import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from domains.theraskill import knowledge_editor
from domains.theraskill.knowledge_editor import KnowledgeEditor, KnowledgeRevisionConflict, KnowledgeUnitNotFound
from theraskill_agent import resource_jobs, resources
from theraskill_agent.indexing import build_index, search_index, verify_index
from theraskill_agent.indexing.common import digest, identity, read_json, write_json


def _row(identifier='unit_a', summary='obsoletephrase old semantic description'):
    return {'release_unit_id': identifier, 'release_unit_type': 'reviewed_cluster',
        'dedup_family': 'therapy_process', 'canonical_knowledge_type': 'process_rule',
        'content': {'canonical_label': 'oldlabel', 'summary': summary,
            'source_faithfulness_notes': 'Source faithfully retained.',
            'registry_fields': {'shared_intent': summary, 'allowed_use': ['reflection', 'exploration'],
                                'forbidden_use': ['diagnosis'], 'custom_nested': {'steps': ['first', 'then']}},
            'representative_text_ref': {'dedup_text_preview': summary, 'raw_file': 'fixture.jsonl', 'raw_line_no': 17}},
        'retrieval_policy': {'retrieval_eligible': True, 'embedding_eligible': True, 'default_route': 'reviewed_cluster_retrieval'},
        'language_distribution': {'en': 1}, 'dataset_distribution': {'self_authored_fixture': 1},
        'knowledge_type_distribution': {'process_rule': 1}, 'evidence_tier_distribution': {'synthetic': 1},
        'quality': {'registry_status': 'approved'}, 'untouched_field': {'provenance': ['original']}}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('THERASKILL_AGENT_ROOT', str(tmp_path))
    from theraskill_agent.paths import data_root
    root = data_root(); package = root / 'knowledge' / 'fixture'; package.mkdir(parents=True)
    source = package / 'knowledge.jsonl'
    source.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in [_row(), _row('unit_b', 'second original text')]), encoding='utf-8')
    write_json(package / 'manifest.json', {'knowledge_sha256': digest(source)})
    output = root / 'indexes' / 'base'
    manifest = build_index(source, output)
    resources.activate_index('base')
    return {'root': root, 'source': source, 'output': output, 'manifest': manifest, 'editor': KnowledgeEditor(root=root)}


def _save(w, fields, revision=0, unit='unit_a'):
    return w['editor'].update(unit, fields, expected_revision=revision, expected_index_id=w['manifest']['index_id'])


def _wait(job_id):
    for _ in range(200):
        job = resource_jobs.get_job(job_id)
        if job['status'] not in {'queued', 'running'}: return job
        time.sleep(.01)
    pytest.fail('bounded resource job did not complete')


def _submit(w, revision=1, unit='unit_a'):
    return w['editor'].submit_reindex(unit, expected_revision=revision, expected_index_id=w['manifest']['index_id'])


def test_original_fields_and_complete_source_are_readable_without_invention(workspace):
    w = workspace; detail = w['editor'].get('unit_a')
    assert detail['source']['kind'] == 'original' and detail['source']['status'] == 'ready'
    assert detail['summary'] == _row()['content']['summary']
    assert detail['allowed_use'] == ['reflection', 'exploration']
    assert detail['contraindications'] == '' and detail['evidence_source'] == ''
    sections = {s['key']: s['value'] for s in detail['indexed_sections']}
    assert sections['registry.custom_nested'] == {'steps': ['first', 'then']}
    assert sections['source.untouched_field'] == {'provenance': ['original']}
    assert sections['content.representative_text_ref']['raw_line_no'] == 17
    assert 'source_record' not in detail
    assert detail['can_edit'] and detail['can_reindex']


def test_long_content_save_reload_and_optimistic_conflict(workspace):
    w = workspace; long = 'new full text. ' * 1500
    changed = _save(w, {'summary': long, 'allowed_use': ['new typed item', 'second']})
    assert changed['revision'] == 1 and changed['summary'] == long.strip()
    assert changed['indexed_values']['summary'] == _row()['content']['summary']
    reloaded = KnowledgeEditor(root=w['root']).get('unit_a')
    assert reloaded['allowed_use'] == ['new typed item', 'second']
    assert len(reloaded['summary']) > 12000
    assert reloaded['index_status'] == 'stale' and reloaded['activation_required']
    with pytest.raises(KnowledgeRevisionConflict): _save(w, {'summary': 'lost update'}, revision=0)
    payload = read_json(w['editor'].overlay_path)
    assert set(payload) == {'schema_version', 'entries'}
    assert _save(w, {'summary': long}, revision=1)['revision'] == 1


def test_concurrent_writers_do_not_lose_other_units_or_overwrite_stale_revision(workspace):
    w = workspace; barrier = threading.Barrier(2)
    def update(summary):
        barrier.wait()
        try: return _save(w, {'summary': summary})['revision']
        except KnowledgeRevisionConflict: return 'conflict'
    with ThreadPoolExecutor(2) as pool: outcomes = list(pool.map(update, ['one', 'two']))
    assert sorted(map(str, outcomes)) == ['1', 'conflict']
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(_save, w, {'allowed_use': ['updated']}, 1, 'unit_a')
        b = pool.submit(_save, w, {'summary': 'another unit'}, 0, 'unit_b')
        assert a.result()['revision'] == 2 and b.result()['revision'] == 1
    assert w['editor'].get('unit_a')['allowed_use'] == ['updated']
    assert w['editor'].get('unit_b')['summary'] == 'another unit'


def test_missing_source_still_shows_verified_index_passage_and_disables_writes(workspace):
    w = workspace; w['source'].unlink()
    detail = w['editor'].get('unit_a')
    assert detail['source']['kind'] == 'indexed_passage' and detail['source']['status'] == 'missing'
    assert 'obsoletephrase' in detail['indexed_passage']
    assert not detail['can_edit'] and not detail['can_reindex']
    assert not detail['allowed_use'] and not detail['contraindications']
    with pytest.raises(ValueError, match='Original source'): _save(w, {'summary': 'not allowed'})
    with pytest.raises(ValueError, match='unavailable'): _submit(w, 0)


@pytest.mark.parametrize('identifier', ['', '../secret', 'a/b', 'x' * 300, 'unknown'])
def test_invalid_or_missing_id_never_reads_arbitrary_files(workspace, identifier):
    with pytest.raises(KnowledgeUnitNotFound): workspace['editor'].get(identifier)


def test_real_lexical_rebuild_applies_exact_snapshot_and_keeps_active_immutable(workspace, monkeypatch):
    w = workspace; original_bytes = w['source'].read_bytes(); active_before = read_json(w['root'] / 'active-index.json')
    _save(w, {'summary': 'replacementsemantic corrected content', 'canonical_label': 'newlabel', 'allowed_use': ['one', 'two']})
    _save(w, {'summary': 'unrelated unsaved-index revision'}, unit='unit_b')
    queued = []
    monkeypatch.setattr(resource_jobs._POOL, 'submit', lambda worker: queued.append(worker))
    response = _submit(w); job_id = response['job_id']; params = resource_jobs.get_job(job_id)['parameters']
    assert _submit(w)['job_id'] == job_id
    assert read_json(params['snapshot'])['fields']['allowed_use'] == ['one', 'two']
    _save(w, {'summary': 'later draft must not leak'}, revision=1)
    queued.pop(0)()
    job = resource_jobs.get_job(job_id)
    assert job['status'] == 'succeeded', job.get('error')
    result = job['result']; built = w['root'] / 'indexes' / result['index_directory_id']
    assert verify_index(built)['index_id'] == result['index_id']
    assert result['index_id'] != result['index_directory_id']
    assert search_index(built, 'replacementsemantic')[0]['release_unit_id'] == 'unit_a'
    assert not search_index(built, 'obsoletephrase')
    assert all(item['release_unit_id'] != 'unit_a' for item in search_index(built, 'oldlabel'))
    materialized_rows = [json.loads(line) for line in Path(result['materialized_knowledge']).read_text().splitlines()]
    assert materialized_rows[0]['content']['registry_fields']['shared_intent'] == _row()['content']['summary']
    assert materialized_rows[0]['content']['registry_fields']['allowed_use'] == ['one', 'two']
    assert materialized_rows[0]['untouched_field'] == _row()['untouched_field']
    assert materialized_rows[1] == _row('unit_b', 'second original text')
    assert w['source'].read_bytes() == original_bytes
    assert read_json(w['root'] / 'active-index.json') == active_before
    assert w['editor'].get('unit_a')['index_status'] == 'stale'
    # Source registration lets an installed result render original content even
    # before activation; normal activation still requires the existing gate.
    detail = w['editor'].get('unit_a', index_id=result['index_id'])
    assert detail['summary'] == 'replacementsemantic corrected content'
    assert detail['source']['kind'] == 'original' and not detail['can_edit']
    with pytest.raises(ValueError, match='comparison report'): resources.activate_index(result['index_directory_id'])


def test_failed_rebuild_retries_exact_snapshot_and_completed_click_is_idempotent(workspace, monkeypatch):
    w = workspace; _save(w, {'summary': 'retrycontent'})
    original_build = resource_jobs.build_index; calls = []
    def flaky(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1: raise ValueError('synthetic transient failure')
        return original_build(*args, **kwargs)
    monkeypatch.setattr(resource_jobs, 'build_index', flaky)
    first = _submit(w); failed = _wait(first['job_id'])
    assert failed['status'] == 'failed' and w['editor'].get('unit_a')['index_status'] == 'failed'
    params = copy.deepcopy(failed['parameters'])
    retried = _submit(w); done = _wait(retried['job_id'])
    assert retried['job_id'] == first['job_id'] and done['parameters'] == params
    assert done['status'] == 'succeeded', done.get('error')
    assert w['editor'].get('unit_a')['index_status'] == 'built'
    assert _submit(w)['job_id'] == first['job_id'] and len(calls) == 2
    assert all(call['resume'] for call in calls)


def test_finished_index_is_reused_after_source_registration_failure(workspace, monkeypatch):
    w = workspace; _save(w, {'summary': 'alreadybuiltcontent'})
    original_write = resource_jobs.write_json; failures = []
    def fail_registration_once(path, value):
        if Path(path).parent == w['root'] / 'imports' and not failures:
            failures.append(str(path)); raise OSError('synthetic registration interruption')
        return original_write(path, value)
    monkeypatch.setattr(resource_jobs, 'write_json', fail_registration_once)
    submitted = _submit(w); failed = _wait(submitted['job_id'])
    assert failed['status'] == 'failed' and 'registration interruption' in failed['error']
    built = w['root'] / 'indexes' / failed['parameters']['index_id']
    metadata = built / 'manifest.json'; completed = read_json(metadata); before = metadata.stat().st_mtime_ns
    assert completed['status'] == 'complete'
    resumed = _submit(w); succeeded = _wait(resumed['job_id'])
    assert succeeded['status'] == 'succeeded', succeeded.get('error')
    assert metadata.stat().st_mtime_ns == before
    assert succeeded['result']['index_id'] == completed['index_id']
    assert w['editor'].get('unit_a', index_id=completed['index_id'])['source']['kind'] == 'original'


def test_activation_scopes_later_edits_to_new_source_and_blocks_old_drafts(workspace, monkeypatch):
    from theraskill_agent import behavior_eval
    w = workspace; _save(w, {'summary': 'firstgeneration'})
    job = _wait(_submit(w)['job_id']); assert job['status'] == 'succeeded'
    result = job['result']; checked = []
    monkeypatch.setattr(behavior_eval, 'verify_gate', lambda report, target: checked.append((report, target)))
    resources.activate_index(result['index_directory_id'], report_id='reviewed-test-fixture')
    assert checked == [('reviewed-test-fixture', {'index_id': result['index_id'], 'knowledge_sha256': result['input']['knowledge_sha256']})]
    historical = w['editor'].get('unit_a', index_id=w['manifest']['index_id'])
    assert historical['index_status'] == 'active' and not historical['can_edit']
    with pytest.raises(KnowledgeRevisionConflict): _save(w, {'summary': 'stale'}, revision=1)
    with pytest.raises(KnowledgeRevisionConflict): _submit(w, revision=1)
    current = w['editor'].get('unit_a')
    assert current['summary'] == 'firstgeneration' and current['revision'] == 0 and current['can_edit']
    edited = w['editor'].update('unit_a', {'allowed_use': ['next generation']}, expected_revision=0, expected_index_id=result['index_id'])
    assert edited['revision'] == 1 and edited['summary'] == 'firstgeneration'
    assert w['editor'].get('unit_a', index_id=w['manifest']['index_id'])['allowed_use'] == ['reflection', 'exploration']


def test_corrupt_snapshot_fails_without_modifying_source_or_active_index(workspace, monkeypatch):
    w = workspace; _save(w, {'summary': 'frozen'})
    queued = []; monkeypatch.setattr(resource_jobs._POOL, 'submit', lambda fn: queued.append(fn))
    response = _submit(w); params = resource_jobs.get_job(response['job_id'])['parameters']
    snap = read_json(params['snapshot']); snap['fields']['summary'] = 'tampered'; write_json(params['snapshot'], snap)
    queued.pop(0)(); job = resource_jobs.get_job(response['job_id'])
    assert job['status'] == 'failed' and 'snapshot changed' in job['error']
    assert resources.active_index()[1]['index_id'] == w['manifest']['index_id']
    assert not (w['root'] / 'indexes' / params['index_id'] / 'manifest.json').exists()


def test_source_changed_after_snapshot_is_rejected_in_background(workspace, monkeypatch):
    w = workspace; _save(w, {'summary': 'frozen'})
    queued = []; monkeypatch.setattr(resource_jobs._POOL, 'submit', lambda fn: queued.append(fn))
    response = _submit(w)
    w['source'].write_bytes(w['source'].read_bytes() + b' ')
    queued.pop(0)(); job = resource_jobs.get_job(response['job_id'])
    assert job['status'] == 'failed' and 'source changed' in job['error']
    assert resources.active_index()[1]['index_id'] == w['manifest']['index_id']


def test_catalog_repairs_corruption_and_never_publishes_wrong_checksum(workspace):
    w = workspace; w['editor'].get('unit_a')
    catalog = next((w['root'] / 'knowledge' / 'source-catalog').glob('*.sqlite'))
    catalog.write_bytes(b'bad sqlite')
    assert w['editor'].get('unit_a')['source']['kind'] == 'original'
    w['source'].write_text(w['source'].read_text() + ' ')
    detail = w['editor'].get('unit_a')
    assert detail['source']['status'] == 'failed' and not detail['can_edit']
    assert not list(catalog.parent.glob('*.tmp'))


def test_large_catalog_runs_once_in_background_and_repairs_lost_output(workspace, monkeypatch):
    w = workspace; monkeypatch.setattr(knowledge_editor, 'SMALL_SOURCE_BYTES', 0)
    first = w['editor'].get('unit_a'); job_id = first['source']['catalog_job_id']
    assert first['source']['status'] == 'preparing'
    assert _wait(job_id)['status'] == 'succeeded'
    assert w['editor'].get('unit_a')['source']['kind'] == 'original'
    catalog = next((w['root'] / 'knowledge' / 'source-catalog').glob('*.sqlite')); catalog.unlink()
    reloading = w['editor'].get('unit_a')
    assert reloading['source']['status'] == 'preparing'
    assert _wait(reloading['source']['catalog_job_id'])['status'] == 'succeeded'
    assert w['editor'].get('unit_a')['source']['kind'] == 'original'


def test_custom_mapped_source_edits_survive_projection_and_rebuild(workspace):
    w = workspace; root = w['root']; source = root / 'knowledge' / 'mapped' / 'knowledge.jsonl'; source.parent.mkdir()
    row = {'key': 'mapped_entry', 'body': {'title': 'Before', 'text': 'beforetext'}, 'review_status': 'approved',
           'retrieval_policy': {'retrieval_eligible': True, 'embedding_eligible': True}}
    source.write_text(json.dumps(row) + '\n'); write_json(source.parent / 'manifest.json', {'knowledge_sha256': digest(source)})
    mapping = {'id': 'key', 'title': 'body.title', 'text': 'body.text'}
    output = root / 'indexes' / 'mapped'; manifest = build_index(source, output, mapping=mapping)
    # Isolated fixture activation only: this test is about source mapping.
    write_json(root / 'active-index.json', {'index': 'indexes/mapped', 'index_id': manifest['index_id']})
    editor = w['editor']; assert editor.get('mapped_entry')['summary'] == 'beforetext'
    editor.update('mapped_entry', {'summary': 'aftertext', 'canonical_label': 'After', 'allowed_use': ['typed item']},
                  expected_revision=0, expected_index_id=manifest['index_id'])
    response = editor.submit_reindex('mapped_entry', expected_revision=1, expected_index_id=manifest['index_id'])
    job = _wait(response['job_id']); assert job['status'] == 'succeeded', job.get('error')
    built = root / 'indexes' / job['result']['index_directory_id']
    assert search_index(built, 'aftertext')[0]['canonical_label'] == 'After'
    assert not search_index(built, 'beforetext')
    detail = editor.get('mapped_entry', index_id=job['result']['index_id'])
    assert detail['summary'] == 'aftertext' and detail['allowed_use'] == ['typed item']


def test_hybrid_rebuild_uses_configured_embedding_and_never_falls_back(workspace, monkeypatch):
    from theraskill_agent.indexing import embedding
    w = workspace; manifest = copy.deepcopy(w['manifest']); manifest['input']['mode'] = 'hybrid'
    write_json(w['output'] / 'manifest.json', manifest)
    config = {'provider': 'openai-compatible', 'name': 'configured-model', 'endpoint': 'http://embedding.test/v1/embeddings', 'api_key': 'fixture-secret'}
    monkeypatch.setattr(embedding, 'resolve', lambda *args: dict(config))
    _save(w, {'summary': 'newhybrid'})
    seen = []
    def fake_build(source, output, **options):
        seen.append(options)
        return {'index_id': 'idx_mock_hybrid', 'input': {'knowledge_sha256': digest(source), 'mode': options['mode']}, 'counts': {'total': 2}}
    monkeypatch.setattr(resource_jobs, 'build_index', fake_build)
    response = _submit(w); job = _wait(response['job_id'])
    assert job['status'] == 'succeeded', job.get('error')
    assert seen[0]['mode'] == 'hybrid' and seen[0]['embedding_config']['name'] == 'configured-model'
    assert 'fixture-secret' not in json.dumps(job['parameters'])
    assert job['result']['index_id'] == 'idx_mock_hybrid'


def test_hybrid_retry_keeps_embedding_identity_and_requires_compatible_config(workspace, monkeypatch):
    from theraskill_agent.indexing import embedding
    w = workspace; manifest = copy.deepcopy(w['manifest']); manifest['input']['mode'] = 'hybrid'
    write_json(w['output'] / 'manifest.json', manifest)
    config = {'provider': 'openai-compatible', 'name': 'initial-model', 'endpoint': 'http://embedding.test/v1/embeddings'}
    monkeypatch.setattr(embedding, 'resolve', lambda *args: dict(config))
    queued = []; built = []
    monkeypatch.setattr(resource_jobs._POOL, 'submit', lambda worker: queued.append(worker))
    def fake_build(source, output, **options):
        built.append(options)
        return {'index_id': 'idx_retry_hybrid', 'input': {'knowledge_sha256': digest(source), 'mode': options['mode']}, 'counts': {'total': 2}}
    monkeypatch.setattr(resource_jobs, 'build_index', fake_build)
    _save(w, {'summary': 'configdrift'}); submitted = _submit(w)
    original_params = copy.deepcopy(resource_jobs.get_job(submitted['job_id'])['parameters'])
    config['name'] = 'changed-model'; queued.pop(0)()
    failed = resource_jobs.get_job(submitted['job_id'])
    assert failed['status'] == 'failed' and 'embedding model differs' in failed['error']
    assert built == []
    config['name'] = 'initial-model'; resumed = _submit(w); queued.pop(0)()
    done = resource_jobs.get_job(resumed['job_id'])
    assert done['status'] == 'succeeded' and done['parameters'] == original_params
    assert done['id'] == submitted['job_id'] and built[0]['mode'] == 'hybrid'


def test_api_requires_revision_and_reports_conflicts(workspace):
    from fastapi.testclient import TestClient
    from domains.theraskill.services.support_api.app import SupportAPISettings, create_app
    w = workspace; app = create_app(SupportAPISettings(database_path=w['root'] / 'support.sqlite', initialize_llm_engine=False, worker_enabled=False))
    headers = {'X-Support-User': 'fixture', 'X-Support-Role': 'user'}
    with TestClient(app) as client:
        detail = client.get('/knowledge/unit_a', headers=headers); assert detail.status_code == 200
        assert client.patch('/knowledge/unit_a', headers=headers, json={'summary': 'missing guard'}).status_code == 428
        body = {'summary': 'saved via API', 'expected_revision': 0, 'expected_index_id': w['manifest']['index_id']}
        assert client.patch('/knowledge/unit_a', headers=headers, json=body).status_code == 200
        assert client.patch('/knowledge/unit_a', headers=headers, json=body).status_code == 409
        typed = client.patch('/knowledge/unit_a', headers=headers, json={
            'expected_revision': 1, 'expected_index_id': w['manifest']['index_id'],
            'applicable_context': ['typed context'], 'evidence_source': ['first source', 'second source']})
        assert typed.status_code == 200 and typed.json()['evidence_source'] == ['first source', 'second source']
        assert client.get('/knowledge/unknown', headers=headers).status_code == 404
        assert client.get('/knowledge/unit_a', headers=headers, params={'index_id': '../untrusted'}).status_code == 404
