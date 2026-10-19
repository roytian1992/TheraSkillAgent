"""Extraction retries cannot silently mix sources/providers or lose cancellation."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from domains.theraskill.services.extraction_api.app import ExtractionAPISettings, ExtractionJobStore
from theraskill_agent.extraction import executor


@pytest.fixture
def fixture_store(tmp_path, monkeypatch):
    source = tmp_path / 'records.jsonl'
    source.write_text('{"id":"a","text":"source A"}\n{"id":"b","text":"source B"}\n')
    settings = ExtractionAPISettings(work_root=tmp_path / 'jobs',
        extraction_project_root=Path(__file__).parents[1] / 'extraction', model_config_path=tmp_path / 'unused.yaml')
    store = ExtractionJobStore(settings)
    monkeypatch.setattr(store._executor, 'submit', lambda *args: None)
    config = {'name': 'fixture', 'base_url': 'http://fixture.invalid/v1', 'api_key': 'fixture-key'}
    monkeypatch.setattr(store, '_model_settings', lambda: dict(config))
    monkeypatch.setattr('domains.theraskill.theraskill_llm.OpenAICompatibleClient', lambda config: SimpleNamespace(model=config['name']))
    calls = []
    def chat(client, prompt, payload, **kwargs):
        calls.append(payload['source_id'])
        return {'evidence': payload['source_record']['text']}
    monkeypatch.setattr(executor, 'chat_json_with_repair', chat)
    plan = {'goal': 'Collect exact excerpts', 'source': {'path': str(source)}, 'unit_definition': 'one record',
            'target_schema': {'type': 'object', 'properties': {'evidence': {'type': 'string'}}, 'required': ['evidence']}}
    yield store, source, config, plan, calls
    store._executor.shutdown(wait=True)


def partial_job(fixture_store, monkeypatch):
    store, source, config, plan, calls = fixture_store
    original = executor.chat_json_with_repair
    def fail_second(client, prompt, payload, **kwargs):
        if payload['source_record']['id'] == 'b':
            calls.append(payload['source_id'])
            raise RuntimeError('fixture interrupted second record')
        return original(client, prompt, payload, **kwargs)
    monkeypatch.setattr(executor, 'chat_json_with_repair', fail_second)
    job = store.create(plan, None, 'fixture')
    store._run(job['job_id'])
    assert store.get(job['job_id'])['status'] == 'partial'
    return job['job_id']


@pytest.mark.parametrize('field,value', [('name', 'another-model'), ('base_url', 'http://changed.invalid/v1'), ('api_key', 'changed-key')])
def test_partial_retry_rejects_provider_change(fixture_store, monkeypatch, field, value):
    store, source, config, plan, calls = fixture_store
    job_id = partial_job(fixture_store, monkeypatch)
    before = list(calls)
    config[field] = value
    store.retry(job_id)
    store._run(job_id)
    result = store.get(job_id)
    assert result['status'] == 'failed' and 'configuration' in result['error']
    assert calls == before
    assert 'fixture-key' not in json.dumps(result) and 'changed-key' not in json.dumps(result)
    assert len((store.settings.work_root / job_id / 'knowledge_units.checkpoint.jsonl').read_text().splitlines()) == 1


def test_retry_rejects_corrupt_checkpoint_instead_of_appending_to_it(fixture_store, monkeypatch):
    store, source, config, plan, calls = fixture_store
    job_id = partial_job(fixture_store, monkeypatch)
    path = store.settings.work_root / job_id / 'knowledge_units.checkpoint.jsonl'
    corrupted = path.read_text() + '{incomplete record'
    path.write_text(corrupted)
    store.retry(job_id)
    store._run(job_id)
    assert store.get(job_id)['status'] == 'failed'
    assert 'damaged' in store.get(job_id)['error']
    assert path.read_text() == corrupted and len(calls) == 2


def test_cancel_after_first_record_preserves_partial_output_without_complete_manifest(fixture_store, monkeypatch):
    store, source, config, plan, calls = fixture_store
    job = store.create(plan, None, 'fixture')
    update = store._update
    def cancel_after_progress(job_id, **values):
        result = update(job_id, **values)
        if values.get('progress', {}).get('processed') == 1:
            store.cancel(job_id)
        return result
    monkeypatch.setattr(store, '_update', cancel_after_progress)
    store._run(job['job_id'])
    result = store.get(job['job_id'])
    manifest = json.loads(Path(result['artifacts']['manifest']).read_text())
    assert result['status'] == manifest['status'] == 'cancelled'
    assert manifest['coverage'] == {'complete': False, 'expected_records': 2, 'processed_records': 1}
    assert result['counts'] == {'units': 1, 'failures': 0} and len(calls) == 1


def test_restart_finalizes_pending_cancellation(tmp_path):
    folder = tmp_path / 'jobs' / 'fixture'
    folder.mkdir(parents=True)
    (folder / 'job.json').write_text(json.dumps({'job_id': 'fixture', 'status': 'cancel_requested'}))
    store = ExtractionJobStore(ExtractionAPISettings(work_root=folder.parent))
    try:
        assert store.get('fixture')['status'] == 'cancelled'
        assert json.loads((folder / 'job.json').read_text())['status'] == 'cancelled'
    finally:
        store._executor.shutdown(wait=True)


def test_source_change_while_loading_is_rejected_before_model_call(fixture_store, monkeypatch):
    store, source, config, plan, calls = fixture_store
    original = executor.load_source_records
    def mutate_source(path):
        records = original(path)
        source.write_text(source.read_text() + '{"id":"c","text":"new"}\n')
        return records
    monkeypatch.setattr(executor, 'load_source_records', mutate_source)
    job = store.create(plan, None, 'fixture')
    store._run(job['job_id'])
    assert store.get(job['job_id'])['status'] == 'failed'
    assert 'while loading' in store.get(job['job_id'])['error'] and calls == []


def test_accepted_sample_provider_is_checked_again_when_worker_starts(fixture_store):
    store, source, config, plan, calls = fixture_store
    job = store.create(plan, None, 'fixture')
    store._update(job['job_id'], trial_review={'model_config_sha256': 'old-provider-hash'})
    store._run(job['job_id'])
    assert store.get(job['job_id'])['status'] == 'failed'
    assert 'accepted sample' in store.get(job['job_id'])['error'] and calls == []


def test_changed_adapter_records_cannot_reuse_checkpoint(fixture_store, monkeypatch):
    from dataclasses import replace
    store, source, config, plan, calls = fixture_store
    job_id = partial_job(fixture_store, monkeypatch)
    original = executor.load_source_records
    def changed_policy(path):
        records = original(path)
        records[0] = replace(records[0], payload={'id': 'a', 'text': 'different adapter segmentation'})
        return records
    monkeypatch.setattr(executor, 'load_source_records', changed_policy)
    store.retry(job_id)
    store._run(job_id)
    assert store.get(job_id)['status'] == 'failed' and len(calls) == 2


def test_complete_manifest_reports_requested_trial_coverage(fixture_store):
    store, source, config, plan, calls = fixture_store
    job = store.create(plan, 1, 'fixture')
    store._run(job['job_id'])
    result = store.get(job['job_id'])
    manifest = json.loads(Path(result['artifacts']['manifest']).read_text())
    assert result['status'] == 'completed' and manifest['status'] == 'complete'
    assert manifest['coverage'] == {'complete': True, 'expected_records': 1, 'processed_records': 1}
