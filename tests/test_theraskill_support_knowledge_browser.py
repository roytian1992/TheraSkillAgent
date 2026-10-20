"""Knowledge citation UX against synthetic APIs, without models or user data.

Build both support_assistant and workbench before running. Each test uses a
fresh temporary HTTP server; all /api/ requests are intercepted. Browser
screenshots are retained in pytest's temporary directory.
"""
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlparse

import pytest

FIELDS = ['canonical_label', 'summary', 'applicable_context', 'evidence_source', 'allowed_use', 'forbidden_use', 'contraindications']


class KnowledgeFixture:
    def __init__(self):
        self.calls = []
        self.conflict_next = False
        self.unavailable = False
        self.fail_job = False
        self.detail = {
            'release_unit_id': 'unit-fixture', 'canonical_label': '情绪验证', 'summary': '原始知识摘要：邀请对方修正你的理解。',
            'allowed_use': ['支持性回应', '教学讨论'], 'forbidden_use': ['诊断'], 'contraindications': ['注意当事人情境'],
            'applicable_context': '对方表达困扰时', 'evidence_source': '合成示例来源',
            'revision': 3, 'index_id': 'index-fixture', 'active_index_id': 'index-fixture', 'index_status': 'active',
            'can_edit': True, 'can_reindex': True, 'mode': 'hybrid', 'editable_fields': FIELDS,
            'source': {'kind': 'original', 'available': True, 'status': 'ready', 'knowledge_sha256': 'sha-fixture'},
            'indexed_sections': [
                {'key': 'summary', 'label': 'Summary', 'value': '原始知识摘要：邀请对方修正你的理解。'},
                {'key': 'content.examples', 'label': 'Examples', 'value': [{'speaker': '来访者', 'quote': '我觉得你没有理解我。'}, {'speaker': '咨询师', 'quote': '可以告诉我哪里理解偏了吗？'}]},
                {'key': 'registry.steps', 'label': 'Steps', 'value': {'trigger': '表达失落', 'response': ['确认情绪', '邀请修正']}},
            ],
            'edited_fields': [], 'reindex_job': None,
        }
        self.polls = 0
        self.session = {
            'schema_version': 'fixture', 'session_id': 'session-fixture', 'status': 'active', 'title': '知识引用测试',
            'messages': [{
                'schema_version': 'fixture', 'message_id': 'assistant-1', 'sequence': 1, 'role': 'assistant',
                'text': '可以先从描述当下的感受开始。', 'presentation_mode': 'conversation', 'structured_content': {},
                'retrieval_summary': {'retrieval_skipped': False, 'hit_count': 1, 'retrieval_mode': 'hybrid'},
                'evidence_summary': [{'unit_id': 'unit-fixture', 'source_id': 'unit-fixture', 'source_index_id': 'index-fixture', 'knowledge_sha256': 'sha-fixture', 'label': '情绪验证', 'role': 'supporting_context', 'excerpt': '这是一段固定的历史引用。', 'dataset': '合成示例'}],
                'created_at': '2026-10-20T00:01:00Z',
            }],
            'generation': {'status': 'idle', 'job_id': None, 'error_code': None, 'retryable': False},
            'safety': {'attention_level': 'none', 'source_risk_level': 'none', 'signal_codes': [], 'handoff_status': 'none', 'check_in_status': 'not_requested'},
            'plan': {'goals': [], 'action_items': []}, 'created_at': '2026-10-20T00:00:00Z', 'updated_at': '2026-10-20T00:00:00Z',
        }

    def running(self, status='running'):
        self.detail['index_status'] = 'building'
        self.detail['reindex_job'] = {'id': 'resource-fixture', 'status': status, 'revision': self.detail['revision'], 'progress': {'stage': 'embedding', 'counts': {'indexed': 24, 'vectors': 8}}, 'resources_url': '/?workspace=knowledge&job_id=resource-fixture'}

    def complete(self):
        self.detail['reindex_job'].update(status='succeeded', result={'index_directory_id': 'candidate-directory', 'index_id': 'candidate-manifest', 'activation_required': True}, progress={'stage': 'complete', 'percent': 100})
        self.detail['index_status'] = 'built'

    def handle(self, route):
        parsed = urlparse(route.request.url)
        path = parsed.path
        method = route.request.method
        body = route.request.post_data_json if method in {'POST', 'PATCH'} and route.request.post_data else None
        self.calls.append({'path': path, 'method': method, 'body': body, 'query': parse_qs(parsed.query)})
        data = {}; status = 200
        knowledge_path = '/api/plugins/theraskill-support/knowledge/unit-fixture'
        if path == '/api/plugins/theraskill-support/sessions': data = {'sessions': [self.session]}
        elif path == '/api/plugins/theraskill-support/sessions/session-fixture': data = self.session
        elif path == knowledge_path and method == 'GET':
            self.polls += 1
            if self.unavailable: status, data = 404, {'detail': 'knowledge unit not found'}
            else: data = self.detail
        elif path == knowledge_path and method == 'PATCH':
            if self.conflict_next:
                self.conflict_next = False; self.detail['revision'] += 1
                self.detail['allowed_use'] = ['另一位编辑者新增用途']
                status, data = 409, {'detail': 'This knowledge entry changed.'}
            else:
                assert body['expected_index_id'] == self.detail['index_id']
                assert body['expected_revision'] == self.detail['revision']
                changes = {key: value for key, value in body.items() if key not in {'expected_index_id', 'expected_revision'}}
                self.detail.update(changes)
                self.detail['edited_fields'] = list(changes)
                self.detail['revision'] += 1
                self.detail['index_status'] = 'stale'
                data = self.detail
        elif path == knowledge_path + '/reindex':
            assert body == {'expected_revision': self.detail['revision'], 'expected_index_id': 'index-fixture'}
            self.running()
            if self.fail_job:
                self.detail['reindex_job'].update(status='failed', error='Fixture embedding service unavailable')
                self.detail['index_status'] = 'failed'
            data = {'job_id': 'resource-fixture', 'status': self.detail['reindex_job']['status'], 'activation_required': True}; status = 202
        elif path == '/api/resources/jobs/resource-fixture':
            data = {**(self.detail['reindex_job'] or {}), 'operation': 'reindex', 'parameters': {'index_id': 'candidate-directory'}}
        elif path == '/api/resources':
            data = {'indexes': [{'id': 'candidate-directory', 'manifest': {'input': {'mode': 'hybrid'}, 'counts': {'indexed': 24}}}], 'knowledge': [], 'jobs': [], 'active': {'index': '/indexes/index-fixture', 'mode': 'hybrid', 'index_id': 'index-fixture'}}
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data, ensure_ascii=False))


@pytest.fixture
def knowledge_browser(tmp_path):
    pw = pytest.importorskip('playwright.sync_api')
    root = Path(__file__).parents[1] / 'views/theraskill'
    for name in ('support_assistant', 'workbench'):
        if not (root / name / 'dist/index.html').is_file(): pytest.skip('Build both frontends first')
    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            path = unquote(urlparse(path).path)
            if path.startswith('/theraskill/support/'):
                base = root / 'support_assistant/dist'; path = path[len('/theraskill/support/'):]
            else:
                base = root / 'workbench/dist'; path = path.lstrip('/')
            return str(base / (path or 'index.html'))
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with pw.sync_playwright() as p:
            try: browser = p.chromium.launch(headless=True)
            except pw.Error as exc: pytest.skip(str(exc))
            page = browser.new_page(viewport={'width': 1280, 'height': 1000}); page.set_default_timeout(8000)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            api = KnowledgeFixture(); page.route('**/api/**', api.handle)
            yield page, api, 'http://127.0.0.1:' + str(server.server_port), tmp_path, pw.expect
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def open_drawer(page, base):
    page.goto(base + '/theraskill/support/')
    page.locator('.evidence-details summary').click()
    page.locator('.evidence-row').click()
    page.get_by_role('heading', name='原始知识内容', exact=True).wait_for()


def test_evidence_original_nested_content_guarded_save_and_candidate_handoff(knowledge_browser):
    page, api, base, artifacts, expect = knowledge_browser
    open_drawer(page, base)
    expect(page.locator('.ke-citation').get_by_text('这是一段固定的历史引用。', exact=True)).to_be_visible()
    expect(page.get_by_text('可以告诉我哪里理解偏了吗？', exact=True)).to_be_visible()
    assert '[object Object]' not in page.get_by_role('dialog').inner_text()
    assert page.get_by_role('dialog').locator('pre').count() == 0
    page.get_by_role('button', name='编辑知识', exact=True).click()
    page.get_by_label('知识摘要', exact=True).fill('只修改摘要，不覆盖数组。')
    page.get_by_role('button', name='保存草稿', exact=True).click()
    page.get_by_text('草稿已保存，当前引用保持不变。', exact=False).wait_for()
    patch = next(call['body'] for call in api.calls if call['method'] == 'PATCH')
    assert patch == {'summary': '只修改摘要，不覆盖数组。', 'expected_revision': 3, 'expected_index_id': 'index-fixture'}
    expect(page.locator('.ke-citation').get_by_text('这是一段固定的历史引用。', exact=True)).to_be_visible()
    expect(page.get_by_text('原始知识摘要：邀请对方修正你的理解。', exact=True)).to_be_visible()
    page.get_by_role('button', name='重新生成索引', exact=True).click()
    page.get_by_role('region', name='索引任务', exact=True).wait_for()
    expect(page.get_by_role('button', name='索引任务进行中', exact=True)).to_be_disabled()
    api.complete()
    link = page.get_by_role('link', name='评估这个候选索引', exact=True)
    expect(link).to_have_attribute('href', '/?workspace=evaluation&index=candidate-directory')
    workspace = page.get_by_role('link', name='在知识工作区打开', exact=False)
    assert 'index_id=index-fixture' in workspace.get_attribute('href') and 'knowledge_sha256=sha-fixture' in workspace.get_attribute('href')
    workspace.click()
    page.get_by_role('heading', name='原始知识内容', exact=True).wait_for()
    expect(page.get_by_role('link', name='评估这个候选索引', exact=True)).to_have_attribute('href', '/?workspace=evaluation&index=candidate-directory')
    page.get_by_role('link', name='查看任务、验证与启用', exact=True).click()
    page.get_by_role('button', name='验证', exact=True).wait_for()
    expect(page.locator('.selected-candidate')).to_contain_text('candidate-directory')
    page.screenshot(path=str(artifacts / 'candidate-handoff.png'), full_page=True)


def test_conflict_reload_keeps_edits_and_untouched_list(knowledge_browser):
    page, api, base, _, expect = knowledge_browser
    open_drawer(page, base)
    page.get_by_role('button', name='编辑知识', exact=True).click()
    page.get_by_label('知识摘要', exact=True).fill('保留我的输入')
    api.conflict_next = True
    page.get_by_role('button', name='保存草稿', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('版本或当前索引已变化')
    expect(page.get_by_label('知识摘要', exact=True)).to_have_value('保留我的输入')
    page.get_by_role('button', name='重新读取版本（保留输入）', exact=True).click()
    expect(page.get_by_label('可以用于 · 每行一项', exact=True)).to_have_value('另一位编辑者新增用途')
    expect(page.get_by_label('知识摘要', exact=True)).to_have_value('保留我的输入')
    page.get_by_role('button', name='保存草稿', exact=True).click()
    page.get_by_role('heading', name='已保存的草稿修改', exact=True).wait_for()
    patches = [call['body'] for call in api.calls if call['method'] == 'PATCH']
    assert patches[-1] == {'summary': '保留我的输入', 'expected_revision': 4, 'expected_index_id': 'index-fixture'}


def test_job_reload_unsaved_edits_and_failed_retry(knowledge_browser):
    page, api, base, _, expect = knowledge_browser
    api.running('queued')
    page.goto(base + '/?workspace=knowledge&knowledge_id=unit-fixture&index_id=index-fixture&knowledge_sha256=sha-fixture')
    page.get_by_role('region', name='索引任务', exact=True).wait_for()
    page.reload()
    page.get_by_role('button', name='编辑知识', exact=True).click()
    page.get_by_label('知识摘要', exact=True).fill('轮询期间保留草稿')
    before = api.polls
    page.wait_for_timeout(1700)
    assert api.polls > before
    expect(page.get_by_label('知识摘要', exact=True)).to_have_value('轮询期间保留草稿')
    api.detail['reindex_job'].update(status='failed', error='Fixture embedding service unavailable')
    api.detail['index_status'] = 'failed'
    expect(page.get_by_role('button', name='重试索引任务', exact=True)).to_be_disabled()
    page.get_by_role('button', name='取消编辑', exact=True).click()
    page.get_by_role('button', name='重试索引任务', exact=True).click()
    expect(page.get_by_role('button', name='索引任务进行中', exact=True)).to_be_disabled()
    assert any(call['path'].endswith('/reindex') for call in api.calls)


def test_drawer_keyboard_unsaved_close_and_mobile_english(knowledge_browser):
    page, api, base, artifacts, expect = knowledge_browser
    open_drawer(page, base)
    page.keyboard.press('Tab')
    assert page.get_by_role('dialog').evaluate('(node) => node.contains(document.activeElement)')
    page.keyboard.press('Shift+Tab')
    assert page.get_by_role('dialog').evaluate('(node) => node.contains(document.activeElement)')
    page.get_by_role('button', name='编辑知识', exact=True).click()
    page.get_by_label('可以用于 · 每行一项', exact=True).fill('项目一\n项目二')
    page.keyboard.press('Escape')
    page.get_by_role('button', name='继续编辑', exact=True).click()
    expect(page.get_by_label('可以用于 · 每行一项', exact=True)).to_have_value('项目一\n项目二')
    page.get_by_role('button', name='保存草稿', exact=True).click()
    page.get_by_role('heading', name='已保存的草稿修改', exact=True).wait_for()
    assert next(call['body'] for call in api.calls if call['method'] == 'PATCH')['allowed_use'] == ['项目一', '项目二']
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert page.locator('.evidence-row').evaluate('(node) => node === document.activeElement')
    page.evaluate("localStorage.setItem('theraskill-locale','en')")
    page.set_viewport_size({'width': 390, 'height': 844}); page.reload()
    page.locator('.evidence-details summary').click(); page.locator('.evidence-row').click()
    page.get_by_role('heading', name='Original knowledge content', exact=True).wait_for()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.get_by_role('dialog').evaluate('(node) => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(artifacts / 'evidence-mobile-english.png'), full_page=True)


def test_removed_cited_index_never_falls_back_to_active(knowledge_browser):
    page, api, base, _, expect = knowledge_browser
    api.unavailable = True
    page.goto(base + '/theraskill/support/')
    page.locator('.evidence-details summary').click(); page.locator('.evidence-row').click()
    expect(page.get_by_role('alert')).to_contain_text('本次引用的知识版本未安装或已移除')
    expect(page.locator('.ke-citation').get_by_text('这是一段固定的历史引用。', exact=True)).to_be_visible()
    page.get_by_role('button', name='重新加载', exact=True).click()
    requests = [call for call in api.calls if '/knowledge/' in call['path']]
    assert all(call['query'].get('index_id') == ['index-fixture'] for call in requests)
    assert page.get_by_role('button', name='编辑知识', exact=True).count() == 0


def test_late_drawer_request_cannot_replace_reopened_content(knowledge_browser):
    page, api, base, _, expect = knowledge_browser
    held = []
    old = deepcopy(api.detail)
    def hold_first(route):
        if not held:
            held.append(route)
        else:
            api.handle(route)
    page.route('**/knowledge/unit-fixture?*', hold_first)
    page.goto(base + '/theraskill/support/')
    page.locator('.evidence-details summary').click(); page.locator('.evidence-row').click()
    page.get_by_text('正在载入知识内容…', exact=True).wait_for()
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    api.detail['canonical_label'] = '第二次打开的内容'
    page.locator('.evidence-row').click()
    page.get_by_role('heading', name='第二次打开的内容', exact=True).wait_for()
    try:
        held[0].fulfill(status=200, content_type='application/json', body=json.dumps(old, ensure_ascii=False))
    except Exception as error:
        # AbortController can cancel the first request before the fixture replies.
        assert 'closed' in str(error).lower() or 'handled' in str(error).lower() or 'cancel' in str(error).lower()
    page.wait_for_timeout(200)
    expect(page.get_by_role('heading', name='第二次打开的内容', exact=True)).to_be_visible()


def test_workspace_back_preserves_edits_until_discard_then_cleans_route(knowledge_browser):
    page, _, base, _, expect = knowledge_browser
    page.goto(base + '/?workspace=knowledge&knowledge_id=unit-fixture&index_id=index-fixture&knowledge_sha256=sha-fixture')
    page.get_by_role('button', name='编辑知识', exact=True).click()
    page.get_by_label('知识摘要', exact=True).fill('离开前尚未保存')
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.get_by_role('button', name='← 返回知识与索引', exact=True).click()
    expect(page.get_by_label('知识摘要', exact=True)).to_have_value('离开前尚未保存')
    assert 'knowledge_id=' in page.url
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='← 返回知识与索引', exact=True).click()
    page.get_by_role('heading', name='知识与索引', exact=True).wait_for()
    assert 'knowledge_id=' not in page.url and 'index_id=' not in page.url and 'knowledge_sha256=' not in page.url


def test_legacy_citation_explicitly_identifies_current_index_and_full_rebuild(knowledge_browser):
    page, api, base, _, expect = knowledge_browser
    citation = api.session['messages'][0]['evidence_summary'][0]
    citation.pop('source_index_id'); citation.pop('knowledge_sha256')
    open_drawer(page, base)
    expect(page.get_by_role('dialog')).to_contain_text('这条历史引用未保存索引版本')
    expect(page.get_by_role('dialog')).to_contain_text('无法确认它与当时引用的版本一致')
    expect(page.get_by_role('dialog')).to_contain_text('可能对整个知识库调用配置的 embedding 服务')
    requests = [call for call in api.calls if '/knowledge/' in call['path']]
    assert requests[0]['query'].get('index_id') is None
