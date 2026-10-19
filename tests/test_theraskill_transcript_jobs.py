"""Background transcript requests, recovery, deduplication and model binding."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from fastapi.testclient import TestClient

from domains.theraskill.services.transcript_api import app as api
from theraskill_agent import reviews
from theraskill_agent.transcript_jobs import TranscriptJobs
from theraskill_agent.task_store import TaskStore


@pytest.fixture
def fixture_env(tmp_path, monkeypatch):
    monkeypatch.setenv('THERASKILL_AGENT_ROOT', str(tmp_path))
    config = {'name': 'fixture', 'base_url': 'https://fixture.invalid/v1'}
    monkeypatch.setattr('domains.theraskill.provider_config.resolve_model_config', lambda: config.copy())
    monkeypatch.setattr(reviews, '_knowledge_snapshot', lambda: {'release_id': 'fixture'})
    return config


def response(system, payload):
    if 'verified_findings' in payload:
        return {'themes': [{'summary': 'Evidence-linked findings', 'finding_ids': [f['finding_id'] for f in payload['verified_findings']]}]}
    turn = payload['turns'][0]
    return {'status': 'completed', 'findings': [{'turn_ids': [turn['turn_id']], 'observed': turn['raw_text'][:80],
            'evidence': [{'turn_id': turn['turn_id'], 'quote': turn['raw_text'][:80]}]}]}


def install_model(monkeypatch, call):
    run = reviews.run_skill_reviews
    monkeypatch.setattr(api, 'run_skill_reviews', lambda *a, **k: run(*a, **k, call_model=call))


def payload(text='patient: I worry every evening.\ntherapist: I hear you.'):
    return {'text': text, 'filename': 'session.txt', 'mode': 'model', 'operation': 'review',
            'skills': ['transcript-quality-reviewer'], 'output_language': 'en'}


def wait(client, job_id, states=('completed', 'partial', 'failed', 'interrupted')):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get('/v1/transcript/jobs/' + job_id).json()
        if job['status'] in states:
            return job
        time.sleep(.01)
    raise AssertionError(job)


def test_submit_returns_before_model_finishes_and_duplicate_posts_share_job(fixture_env, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def model(system, value):
        calls.append(value)
        if 'turns' in value:
            entered.set()
            assert release.wait(5)
        return response(system, value)
    install_model(monkeypatch, model)
    with TestClient(api.create_app()) as client:
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(client.post, '/v1/transcript/jobs', json=payload()) for _ in range(2)]
                results = [future.result(timeout=3) for future in futures]
            assert all(result.status_code == 202 for result in results)
            job_id = results[0].json()['job_id']
            assert results[1].json()['job_id'] == job_id
            assert results[0].json()['run_id'].startswith('review_')
            assert entered.wait(2)
            running = client.get('/v1/transcript/jobs/' + job_id).json()
            assert running['status'] == 'running'
            assert running['progress']['planned_windows'] == 1
            assert client.get('/v1/transcript/jobs').json()['jobs'][0]['job_id'] == job_id
            assert 'request' not in client.get('/v1/transcript/jobs').json()['jobs'][0]
        finally:
            release.set()
        completed = wait(client, job_id)
        assert completed['status'] == 'completed'
        assert len(calls) == 2
        assert completed['request']['text'] == payload()['text']
        assert completed['result']['reviews']['coverage']['completed_windows'] == 1
        archive = client.post('/v1/transcript/jobs', json={**payload(), 'operation': 'package', 'review_run_id': completed['run_id']}).json()
        packed = wait(client, archive['job_id'])
        assert packed['status'] == 'completed' and len(calls) == 2
        assert packed['result']['reviews']['reused_windows'] == 1
        assert client.get(packed['result']['download_url']).status_code == 200


def test_partial_job_refresh_and_concurrent_retry_keep_completed_windows(fixture_env, monkeypatch):
    attempts, fail, gate = [], [True], threading.Event()
    started = threading.Event()
    def model(system, value):
        if 'turns' in value:
            index = value['review_scope']['window_index']
            attempts.append(index)
            if index == 1:
                if fail[0]:
                    fail[0] = False
                    raise RuntimeError('fixture failed window')
                started.set()
                assert gate.wait(5)
        return response(system, value)
    install_model(monkeypatch, model)
    request = payload('patient: ' + 'I worry about work. ' * 1600 + '\ntherapist: Tell me more.')
    with TestClient(api.create_app()) as client:
        created = client.post('/v1/transcript/jobs', json=request).json()
        first = wait(client, created['job_id'])
        assert first['status'] == 'partial'
        before = len(attempts)
    # A fresh API instance reads the same durable job and original request.
    with TestClient(api.create_app()) as restored:
        current = restored.get('/v1/transcript/jobs/' + first['job_id']).json()
        assert current['status'] == 'partial' and current['request']['text'] == request['text']
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(restored.post, '/v1/transcript/jobs/' + current['job_id'] + '/retry', json={'revision': current['revision']}) for _ in range(2)]
                results = [future.result(timeout=3) for future in futures]
            assert sorted(result.status_code for result in results) == [202, 409]
            assert started.wait(2)
        finally:
            gate.set()
        done = wait(restored, current['job_id'])
        assert done['status'] == 'completed'
        assert len(attempts) == before + 1 and attempts[-1] == 1
        assert done['result']['reviews']['reused_windows'] == before - 1


def test_model_change_refuses_resume_and_source_change_creates_bound_new_job(fixture_env, monkeypatch):
    def fail(*_):
        raise RuntimeError('fixture failure')
    install_model(monkeypatch, fail)
    with TestClient(api.create_app()) as client:
        first = client.post('/v1/transcript/jobs', json=payload()).json()
        failed = wait(client, first['job_id'])
        assert failed['status'] == 'failed'
        fixture_env['base_url'] = 'https://different-fixture.invalid/v1'
        denied = client.post('/v1/transcript/jobs/' + first['job_id'] + '/retry', json={'revision': failed['revision']})
        assert denied.status_code == 409
        assert 'configuration changed' in denied.json()['detail']
        changed = client.post('/v1/transcript/jobs', json=payload('patient: A different input.')).json()
        assert changed['job_id'] != first['job_id'] and changed['run_id'] != first['run_id']
        wait(client, changed['job_id'])
        stale = client.post('/v1/transcript/jobs', json={**payload('patient: Changed source.'), 'review_run_id': first['run_id']})
        assert stale.status_code == 409
        assert client.get('/v1/transcript/jobs/' + first['job_id']).json()['request']['text'] == payload()['text']


def test_dead_worker_is_explicitly_resumable(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / 'jobs.sqlite')
    task = store.create('transcript_review', 'session.txt', inputs={'operation': 'review', 'run_id': 'fixture-run', 'request': {'filename': 'session.txt'}})
    task.update(status='running', worker_pid=999999999, stage='synthesis')
    store.save(task)
    def missing(pid, signal):
        raise ProcessLookupError('fixture exited worker')
    monkeypatch.setattr('theraskill_agent.transcript_jobs.os.kill', missing)
    jobs = TranscriptJobs(lambda request: 'fixture-run', lambda *args: {'reviews': {'status': 'completed'}}, store=store)
    try:
        interrupted = jobs.get(task['id'])
        assert interrupted['status'] == 'interrupted'
        jobs.retry(task['id'], revision=interrupted['revision'])
        deadline = time.monotonic() + 3
        while jobs.get(task['id'])['status'] != 'completed' and time.monotonic() < deadline:
            time.sleep(.01)
        assert jobs.get(task['id'])['status'] == 'completed'
    finally:
        jobs.close()


def test_quarantined_window_is_retryable_without_repeating_valid_windows_or_tools(fixture_env, monkeypatch):
    attempts, tool_calls = [], []
    def model(system, value):
        result = response(system, value)
        if 'turns' in value:
            skill = value['output_contract']['skill']
            attempts.append(skill)
            if skill == 'therapy-response-reviewer' and attempts.count(skill) == 1:
                result['findings'][0]['evidence'][0]['quote'] = 'Invented source quotation'
        return result
    def tool(name, args, **kwargs):
        tool_calls.append(name)
        return {'status': 'success', 'data': {'matches': [{'retrieval_id': 'fixture-k1'}]}}
    run = reviews.run_skill_reviews
    monkeypatch.setattr(api, 'run_skill_reviews', lambda *a, **k: run(*a, **k, call_model=model, tool=tool))
    with TestClient(api.create_app()) as client:
        request = {**payload(), 'skills': ['transcript-quality-reviewer', 'therapy-response-reviewer']}
        created = client.post('/v1/transcript/jobs', json=request).json()
        first = wait(client, created['job_id'])
        assert first['status'] == 'partial'
        coverage = first['result']['reviews']['coverage']
        assert coverage['validation_failed_windows'] == coverage['failed_windows'] == 1
        assert coverage['completed_windows'] == 1
        before_tools = list(tool_calls)
        assert before_tools == ['theraskill_context', 'theraskill_knowledge_search']
        # A duplicate submit remains read-only; explicit retry repairs the bad window.
        duplicate = client.post('/v1/transcript/jobs', json=request).json()
        assert duplicate['job_id'] == first['job_id'] and duplicate['status'] == 'partial'
        retried = client.post('/v1/transcript/jobs/' + first['job_id'] + '/retry', json={'revision': first['revision']})
        assert retried.status_code == 202
        done = wait(client, first['job_id'])
        assert done['status'] == 'completed'
        assert attempts.count('transcript-quality-reviewer') == 1
        assert attempts.count('therapy-response-reviewer') == 2
        assert tool_calls == before_tools
        assert done['result']['reviews']['reused_windows'] == 1
        assert done['result']['reviews']['coverage']['quarantined_findings'] == 0


@pytest.mark.parametrize('bad_synthesis', ['unknown_ids', 'missing_coverage'])
def test_invalid_synthesis_can_retry_without_repeating_windows(fixture_env, monkeypatch, bad_synthesis):
    counts = {'window': 0, 'synthesis': 0}
    def model(system, value):
        if 'verified_findings' in value:
            counts['synthesis'] += 1
            if counts['synthesis'] == 1:
                return {'themes': [] if bad_synthesis == 'missing_coverage' else [
                    {'summary': 'Unsupported conclusion', 'finding_ids': ['invented-id']} ]}
        else:
            counts['window'] += 1
        return response(system, value)
    install_model(monkeypatch, model)
    with TestClient(api.create_app()) as client:
        created = client.post('/v1/transcript/jobs', json=payload()).json()
        first = wait(client, created['job_id'])
        assert first['status'] == 'partial'
        assert first['result']['reviews']['synthesis']['status'] == 'partial'
        assert client.post('/v1/transcript/jobs/' + first['job_id'] + '/retry', json={'revision': first['revision']}).status_code == 202
        done = wait(client, first['job_id'])
        assert done['status'] == 'completed'
        assert counts == {'window': 1, 'synthesis': 2}


def test_valid_cautious_review_is_completed_without_retry(fixture_env, monkeypatch):
    calls = []
    def model(system, value):
        calls.append(value)
        result = response(system, value)
        if 'turns' in value:
            result['status'] = 'needs_review'
            result['limitations'] = ['Interpretation needs human review.']
        return result
    install_model(monkeypatch, model)
    with TestClient(api.create_app()) as client:
        created = client.post('/v1/transcript/jobs', json=payload()).json()
        first = wait(client, created['job_id'])
        assert first['status'] == 'completed'
        assert first['result']['reviews']['status'] == 'needs_review'
        assert client.post('/v1/transcript/jobs/' + first['job_id'] + '/retry', json={'revision': first['revision']}).status_code == 409
        assert len(calls) == 2
