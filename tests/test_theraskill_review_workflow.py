"""Deterministic fixtures for real review orchestration; no provider requests."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from theraskill_agent import reviews
from theraskill_agent.transcript import parse_transcript, analyze_transcript


@pytest.fixture
def review_env(tmp_path, monkeypatch):
    monkeypatch.setenv('THERASKILL_AGENT_ROOT', str(tmp_path))
    config = {'name': 'fixture', 'base_url': 'https://fixture.invalid/v1', 'api_key': 'test-secret'}
    knowledge = {'release_id': 'fixture-v1'}
    monkeypatch.setattr('domains.theraskill.provider_config.resolve_model_config', lambda: config.copy())
    monkeypatch.setattr(reviews, '_knowledge_snapshot', lambda: knowledge.copy())
    return config, knowledge


def model_reply(system, payload):
    if 'verified_findings' in payload:
        return {'themes': [{'finding_ids': [row['finding_id'] for row in payload['verified_findings']],
                            'summary': 'Combined source-linked findings', 'uncertainty': 'Model interpretation'}]}
    turn = payload['turns'][0]
    knowledge = ['k1'] if any(row['tool'] == 'theraskill_knowledge_search' for row in payload['tool_evidence']) else []
    return {'status': 'completed', 'findings': [{'turn_ids': [turn['turn_id']],
            'evidence': [{'turn_id': turn['turn_id'], 'quote': turn['raw_text'][:80]}],
            'observed': 'Source observation', 'inference': 'Bounded interpretation', 'knowledge_ids': knowledge}]}


def evidence_tool(name, args, **kwargs):
    if name == 'theraskill_knowledge_search':
        return {'status': 'success', 'data': {'matches': [{'retrieval_id': 'k1', 'text_preview': 'A useful review rubric'}]}}
    assert name == 'theraskill_context'
    return {'release_id': 'fixture-v1'}


def test_declared_evidence_tools_execute_and_successful_stages_are_reused(review_env, tmp_path):
    tool_calls, model_calls = [], []
    def tool(name, args, **kwargs):
        tool_calls.append(name)
        return evidence_tool(name, args, **kwargs)
    def model(system, payload):
        model_calls.append(payload)
        return model_reply(system, payload)
    doc = parse_transcript('patient: I worry every night.\ntherapist: I hear you.')
    result = reviews.run_skill_reviews(doc, analyze_transcript(doc), skills=['therapy-response-reviewer'], tool=tool, call_model=model)
    assert tool_calls == ['theraskill_context', 'theraskill_knowledge_search']
    assert result['status'] == 'completed' and result['coverage']['verified_findings'] == 1
    assert result['reviews'][0]['findings'][0]['knowledge_ids'] == ['k1']
    assert result['synthesis']['themes'][0]['finding_ids']
    # A fresh parse produces a different document ID. The content binding is stable.
    fresh = parse_transcript('patient: I worry every night.\ntherapist: I hear you.')
    reused = reviews.run_skill_reviews(fresh, analyze_transcript(fresh), skills=['therapy-response-reviewer'], tool=tool, call_model=model)
    assert fresh.document_id != doc.document_id
    assert reused['run_id'] == result['run_id'] and reused['reused_windows'] == 1
    assert len(model_calls) == 2 and len(tool_calls) == 2
    assert reused['document_id'] == fresh.document_id
    saved = reviews.read_review_run(result['run_id'])
    assert saved['stage'] == 'complete'
    assert 'test-secret' not in json.dumps(saved)


def test_invalid_quotes_ids_and_knowledge_are_quarantined():
    turns = [{'turn_id': 'turn_1', 'raw_text': 'I am tired.'}]
    good = {'turn_ids': ['turn_1'], 'evidence': [{'turn_id': 'turn_1', 'quote': 'I am tired.'}], 'observed': 'Fatigue'}
    bad_quote = {**good, 'evidence': [{'turn_id': 'turn_1', 'quote': 'I am suicidal.'}]}
    bad_id = {**good, 'turn_ids': ['turn_1', 'made_up']}
    bad_knowledge = {**good, 'knowledge_ids': ['invented']}
    result = reviews._validate_result({'status': 'completed', 'findings': [good, bad_quote, bad_id, bad_knowledge]}, 'reviewer', turns)
    assert len(result['findings']) == 1
    assert len(result['quarantined_findings']) == 3
    assert result['status'] == 'needs_review'
    assert result['findings'][0]['evidence_status'] == 'source_quotes_verified'


def test_synthesis_cannot_cite_quarantined_or_missing_findings():
    result = reviews._validate_synthesis({'themes': [{'summary': 'Unsupported conclusion', 'finding_ids': ['made_up']}]},
                                         [{'finding_id': 'verified_1'}])
    assert result['themes'] == [] and result['status'] == 'needs_review'
    assert result['uncovered_finding_ids'] == ['verified_1']


def test_failed_window_retries_without_rerunning_completed_windows(review_env):
    doc = parse_transcript('patient: ' + 'Worry about work. ' * 1600 + '\ntherapist: Tell me more.')
    calls, fail_once = [], [True]
    def model(system, payload):
        if 'turns' in payload:
            key = payload['review_scope']['window_index']
            calls.append(key)
            if key == 1 and fail_once[0]:
                fail_once[0] = False
                raise RuntimeError('fixture interruption')
        return model_reply(system, payload)
    kwargs = {'skills': ['transcript-quality-reviewer'], 'call_model': model, 'tool': evidence_tool}
    first = reviews.run_skill_reviews(doc, analyze_transcript(doc), **kwargs)
    assert first['status'] == 'partial' and first['coverage']['failed_windows'] == 1
    initial_count = len(calls)
    resumed = reviews.run_skill_reviews(doc, analyze_transcript(doc), **kwargs)
    assert resumed['status'] == 'completed' and resumed['coverage']['failed_windows'] == 0
    assert len(calls) == initial_count + 1 and calls[-1] == 1
    assert resumed['reused_windows'] == initial_count - 1


def test_binding_changes_for_roles_language_model_and_knowledge(review_env):
    config, knowledge = review_env
    text = 'A: I worry.\nB: I hear you.'
    doc = parse_transcript(text)
    kwargs = {'skills': ['transcript-quality-reviewer'], 'call_model': model_reply, 'tool': evidence_tool}
    original = reviews.run_skill_reviews(doc, analyze_transcript(doc), **kwargs)
    role_doc = parse_transcript(text, speaker_role_overrides={'A': 'patient'})
    changed_roles = reviews.run_skill_reviews(role_doc, analyze_transcript(role_doc), **kwargs)
    assert changed_roles['run_id'] != original['run_id']
    changed_language = reviews.run_skill_reviews(doc, analyze_transcript(doc), output_language='zh', **kwargs)
    assert changed_language['run_id'] != original['run_id']
    config['name'] = 'another-fixture'
    with pytest.raises(ValueError, match='configuration changed'):
        reviews.run_skill_reviews(doc, analyze_transcript(doc), review_run_id=original['run_id'], **kwargs)
    changed_model = reviews.run_skill_reviews(doc, analyze_transcript(doc), **kwargs)
    knowledge['release_id'] = 'fixture-v2'
    changed_knowledge = reviews.run_skill_reviews(doc, analyze_transcript(doc), **kwargs)
    assert len({original['run_id'], changed_model['run_id'], changed_knowledge['run_id']}) == 3


def test_review_then_package_reuses_windows_and_synthesis(review_env, monkeypatch):
    from domains.theraskill.services.transcript_api import app as api
    calls = []
    def model(system, payload):
        calls.append(payload)
        return model_reply(system, payload)
    real_review = reviews.run_skill_reviews
    monkeypatch.setattr(api, 'run_skill_reviews', lambda *a, **k: real_review(*a, **k, call_model=model, tool=evidence_tool))
    client = TestClient(api.create_app())
    request = {'text': 'patient: I worry.\ntherapist: I hear you.', 'mode': 'model', 'skills': ['therapy-response-reviewer']}
    first = client.post('/v1/transcript/review', json=request)
    assert first.status_code == 200
    run_id = first.json()['reviews']['run_id']
    count = len(calls)
    bundle = client.post('/v1/transcript/package', json={**request, 'review_run_id': run_id})
    assert bundle.status_code == 200 and len(calls) == count
    assert bundle.json()['reviews']['reused_windows'] == 1
    assert client.get('/v1/transcript/reviews/' + run_id).json()['status'] == 'completed'
    assert client.get(bundle.json()['download_url']).status_code == 200
    mismatch = client.post('/v1/transcript/package', json={**request, 'text': 'patient: Changed.', 'review_run_id': run_id})
    assert mismatch.status_code == 409
