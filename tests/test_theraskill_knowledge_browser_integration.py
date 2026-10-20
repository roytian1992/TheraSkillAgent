"""Browser to real domain API/indexer, using isolated synthetic knowledge.

Only the generated response and unrelated workspace APIs are fixtures. Knowledge
reads, draft writes, durable rebuilds and candidate verification use real code.
No production files, model endpoints or embedding services are used.
"""
import json
from pathlib import Path
import socket
import threading
import time

import pytest


def test_citation_to_real_draft_rebuild_and_verification(tmp_path, monkeypatch):
    pw = pytest.importorskip('playwright.sync_api')
    uvicorn = pytest.importorskip('uvicorn')
    repo = Path(__file__).parents[1]
    if any(not (repo / 'views/theraskill' / name / 'dist/index.html').is_file()
           for name in ('workbench', 'support_assistant', 'simulation_console', 'expert_studio')):
        pytest.skip('Build the frontend workspaces first')
    monkeypatch.setenv('THERASKILL_AGENT_ROOT', str(tmp_path))
    from theraskill_agent import local_services, resources, resource_jobs
    from theraskill_agent.indexing import build_index, search_index, verify_index
    from theraskill_agent.indexing.common import digest, write_json
    from theraskill_agent.paths import data_root
    from theraskill_agent.webapp import create_app
    from domains.theraskill.services.support_api.app import create_app as support_app, SupportAPISettings
    from domains.theraskill.theraskill_support.contracts import RoutingDecision, SupportEngineResult
    from domains.theraskill.theraskill_support.repository import SQLiteSupportRepository
    from domains.theraskill.theraskill_support.service import SupportSessionService

    package = data_root() / 'knowledge' / 'synthetic'; package.mkdir(parents=True)
    source = package / 'knowledge.jsonl'
    row = {'release_unit_id': 'browser-entry', 'release_unit_type': 'reviewed_cluster',
        'dedup_family': 'therapy_process', 'canonical_knowledge_type': 'process_rule',
        'content': {'canonical_label': '合成知识示例', 'summary': '原始示例内容，仅用于软件验收。',
                    'registry_fields': {'allowed_use': ['界面验收', '接口验收'],
                                        'examples': [{'speaker': '测试者', 'quote': '这是嵌套的原文内容。'}]}},
        'retrieval_policy': {'retrieval_eligible': True, 'embedding_eligible': True},
        'language_distribution': {'zh': 1}, 'dataset_distribution': {'self_authored_fixture': 1}}
    original = json.dumps(row, ensure_ascii=False) + '\n'
    source.write_text(original, encoding='utf-8')
    checksum = digest(source)
    write_json(package / 'manifest.json', {'knowledge_sha256': checksum})
    manifest = build_index(source, data_root() / 'indexes' / 'browser-base')
    resources.activate_index('browser-base')

    class FixtureResponse:
        def generate(self, record, job_id):
            return SupportEngineResult(response_text='这是软件验收用的合成回复。',
                routing=RoutingDecision(primary_skill='support', task_type='emotional_support',
                    detected_language='zh', risk_level='none_observed', retrieval_query='fixture',
                    user_intent_summary='synthetic software test'),
                retrieval_report_path='', retrieved_unit_ids=['browser-entry'],
                evidence_summary=[{'unit_id': 'browser-entry', 'label': '合成知识示例',
                    'excerpt': '固定的历史引用摘录。', 'source_index_id': manifest['index_id'],
                    'knowledge_sha256': checksum}],
                retrieval_summary={'hit_count': 1, 'retrieval_mode': 'lexical'},
                model_config_id='fixture', prompt_release_id='fixture')

    service = SupportSessionService(repository=SQLiteSupportRepository(tmp_path / 'support.sqlite'),
                                    engine=FixtureResponse(), knowledge_release_id='fixture')
    session = service.create_session(owner_id='local-user')
    submitted = service.submit_message(session.session_id, text='合成界面验收',
                                       owner_id='local-user', idempotency_key='fixture')
    service.finalize_response(session.session_id, job_id=submitted.job_id)
    settings = SupportAPISettings(database_path=tmp_path / 'support.sqlite',
        initialize_llm_engine=False, worker_enabled=False, contribution_api_url=None)
    services = local_services.LocalServices(factories={
        'support': lambda: support_app(settings, service_override=service)})
    monkeypatch.setattr(local_services, '_default', services)
    app = create_app(assets_root=repo / 'views/theraskill')
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    base = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started: break
        if not thread.is_alive(): pytest.fail('Isolated server failed to start')
        time.sleep(.025)
    assert server.started
    try:
        with pw.sync_playwright() as p:
            try: browser = p.chromium.launch(headless=True)
            except pw.Error as exc: pytest.skip(str(exc))
            page = browser.new_page(viewport={'width': 1366, 'height': 1000})
            page.set_default_timeout(10000)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            def unrelated_api(route):
                path = route.request.url.split(base, 1)[-1]
                if path.startswith('/api/plugins/theraskill-support/') or path == '/api/resources' or path.startswith('/api/resources/jobs'):
                    route.continue_()
                else: route.fulfill(status=200, content_type='application/json', body='{}')
            page.route('**/api/**', unrelated_api)
            page.goto(base + '/theraskill/support/')
            page.locator('.evidence-details summary').click()
            page.locator('.evidence-row').click()
            pw.expect(page.get_by_text('这是嵌套的原文内容。', exact=True)).to_be_visible()
            assert '[object Object]' not in page.get_by_role('dialog').inner_text()
            page.get_by_role('button', name='编辑知识', exact=True).click()
            page.get_by_label('知识摘要', exact=True).fill('browsercorrection 修订后的合成知识。')
            page.get_by_role('button', name='保存草稿', exact=True).click()
            page.get_by_role('heading', name='已保存的草稿修改', exact=True).wait_for()
            pw.expect(page.get_by_text('原始示例内容，仅用于软件验收。', exact=True)).to_be_visible()
            page.locator('.knowledge-drawer-body').evaluate('(node) => { node.scrollTop = 0 }')
            page.screenshot(path=str(tmp_path / 'real-evidence-editor.png'), full_page=True)
            page.get_by_role('button', name='重新生成索引', exact=True).click()
            link = page.get_by_role('link', name='评估这个候选索引', exact=True)
            pw.expect(link).to_be_visible(timeout=15000)
            page.get_by_role('link', name='在知识工作区打开', exact=False).click()
            page.get_by_role('heading', name='已保存的草稿修改', exact=True).wait_for()
            pw.expect(page.get_by_text('browsercorrection 修订后的合成知识。', exact=True)).to_be_visible()
            page.get_by_role('link', name='查看任务、验证与启用', exact=True).click()
            page.locator('.selected-candidate').get_by_role('button', name='验证', exact=True).click()
            page.wait_for_function("async () => (await (await fetch('/api/resources')).json()).jobs.some(job => job.operation === 'verify' && job.status === 'succeeded')")
            page.screenshot(path=str(tmp_path / 'real-candidate-verification.png'), full_page=True)
            assert not errors, errors
            browser.close()
        jobs = resource_jobs.list_jobs()
        rebuilt = next(job for job in jobs if job['operation'] == 'reindex')
        assert rebuilt['status'] == 'succeeded', rebuilt.get('error')
        candidate = data_root() / 'indexes' / rebuilt['result']['index_directory_id']
        assert verify_index(candidate)['index_id'] == rebuilt['result']['index_id']
        assert search_index(candidate, 'browsercorrection')[0]['release_unit_id'] == 'browser-entry'
        assert source.read_text(encoding='utf-8') == original
        assert resources.active_index()[1]['index_id'] == manifest['index_id']
        assert any(job['operation'] == 'verify' and job['status'] == 'succeeded' for job in jobs)
    finally:
        server.should_exit = True; thread.join(timeout=10); sock.close()
