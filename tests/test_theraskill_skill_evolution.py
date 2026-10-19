"""Focused skill evolution runs against local fixtures, never a live model."""
from pathlib import Path
import time

import pytest

from theraskill_agent.task_runtime import TaskEngine
from theraskill_agent.task_store import TaskStore
from theraskill_agent.skill_evolution import _apply_edits


ORIGINAL = '''---
name: fixture-skill
description: A fixture skill.
runtime:
  required_tools: [text.normalize]
---
# Fixture skill
Read the input and summarize it.
Keep source quotes.
'''


def wait(engine, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = engine.store.get(task_id)
        if result['status'] in {'failed', 'completed'}:
            return result
        time.sleep(.01)
    raise AssertionError(engine.store.get(task_id))


def fixture_skill(tmp_path, monkeypatch):
    monkeypatch.setenv('THERASKILL_AGENT_ROOT', str(tmp_path))
    monkeypatch.setattr('domains.theraskill.provider_config.resolve_model_config',
                        lambda: {'name': 'fixture-model', 'base_url': 'http://fixture.invalid/v1', 'api_key': 'fixture-key'})
    source = tmp_path / 'source-skill'
    source.mkdir()
    (source / 'SKILL.md').write_text(ORIGINAL)
    (source / 'notes.md').write_text('A supporting method: distinguish observations from inference.')
    return source


def diagnosis():
    return {'issues': [{'id': 'quote_issue', 'problem': 'Missing turn citations', 'evidence_ids': ['user_input'],
                        'proposed_change': 'Require turn IDs with exact source quotes'}], 'evaluation_focus': ['citation validity']}


def revision():
    return {'edits': [{'issue_id': 'quote_issue', 'old': 'Keep source quotes.',
                       'new': 'Keep exact source quotes and cite the corresponding turn IDs.'}],
            'changes': ['Add source IDs'], 'evaluation_focus': ['citation validity']}


def test_evolution_uses_observations_references_and_resumes_after_failed_revision(tmp_path, monkeypatch):
    source = fixture_skill(tmp_path, monkeypatch)
    calls = []
    fail_once = [True]
    def model(system, payload):
        calls.append(payload)
        assert payload['evidence'][1]['text'] == 'Actual failure: output omitted turn_0001.'
        assert payload['reference_files'][0]['text'].startswith('A supporting method')
        if 'diagnosis' not in payload:
            return diagnosis(), {'model': 'fixture-diagnosis'}
        if fail_once[0]:
            fail_once[0] = False
            raise RuntimeError('fixture interruption')
        return revision(), {'model': 'fixture-revision'}
    monkeypatch.setattr('theraskill_agent.skill_execution.model_json', model)
    engine = TaskEngine(TaskStore(tmp_path / 'tasks.sqlite'))
    try:
        task = engine.create('skill_evolution', 'Improve citations', 'en', {'skill': str(source), 'text': 'Actual failure: output omitted turn_0001.'})
        failed = wait(engine, task['id'])
        assert failed['status'] == 'failed' and 'evolution_diagnosis' in failed['checkpoints']
        engine.action(task['id'], 'retry', revision=failed['revision'])
        result = wait(engine, task['id'])
        assert result['status'] == 'completed', result
        assert len([item for item in calls if 'diagnosis' not in item]) == 1
        draft = Path(result['checkpoints']['result']['draft_path'])
        assert 'corresponding turn IDs' in (draft / 'SKILL.md').read_text()
        assert (source / 'SKILL.md').read_text() == ORIGINAL
        assert (draft / 'notes.md').read_text() == (source / 'notes.md').read_text()
        assert result['checkpoints']['result']['status'] == 'draft_requires_behavior_comparison'
        assert not (tmp_path / 'skills' / 'fixture-skill').exists()
    finally:
        engine.close()


def test_invalid_revision_is_repaired_once_with_validation_feedback(tmp_path, monkeypatch):
    source = fixture_skill(tmp_path, monkeypatch)
    calls = []
    def model(system, payload):
        calls.append(payload)
        if 'diagnosis' not in payload:
            return diagnosis(), {}
        if 'validation_errors' not in payload:
            return {'edits': [{'issue_id': 'quote_issue', 'old': 'nonexistent excerpt', 'new': 'replacement'}]}, {}
        assert 'unique excerpt' in payload['validation_errors'][0]
        return revision(), {}
    monkeypatch.setattr('theraskill_agent.skill_execution.model_json', model)
    engine = TaskEngine(TaskStore(tmp_path / 'tasks.sqlite'))
    try:
        task = engine.create('skill_evolution', 'Improve citations', 'en', {'skill': str(source), 'text': 'Missing citations'})
        result = wait(engine, task['id'])
        assert result['status'] == 'completed', result
        assert len(calls) == 3 and result['checkpoints']['evolution_repair_used']
    finally:
        engine.close()


def test_revision_cannot_remove_declared_tools_or_replace_whole_skill():
    context = {'original_skill': ORIGINAL, 'original': {'name': 'fixture-skill'}}
    change = {'edits': [{'issue_id': 'quote_issue', 'old': 'required_tools: [text.normalize]', 'new': 'required_tools: []'}]}
    with pytest.raises(ValueError, match='preserve declared tools'):
        _apply_edits(change, context, diagnosis())
    with pytest.raises(ValueError, match='entire skill'):
        _apply_edits({'edits': [{'issue_id': 'quote_issue', 'old': ORIGINAL, 'new': ORIGINAL + 'extra'}]}, context, diagnosis())


def test_failure_report_is_supplied_to_diagnosis(tmp_path, monkeypatch):
    from theraskill_agent.skill_evolution import _evidence
    monkeypatch.setattr('theraskill_agent.behavior_eval.get_report', lambda report_id: {'items': [{'id': 'case_1', 'status': 'completed',
        'checks': {'valid_citations': False}, 'scores': {'grounding': 1}, 'response': {'answer': 'Bad answer'}, 'judge': {'reasons': 'Missing sources'}}]})
    evidence = _evidence({'report_ids': ['report_fixture'], 'failure_evidence': {'expected': 'cite a source'}}, 'Improve citations')
    assert evidence[-1]['id'] == 'report:report_fixture:case_1'
    assert evidence[-1]['checks']['valid_citations'] is False


@pytest.mark.parametrize('changed', ['model', 'source_evidence', 'behavior_report'])
def test_retry_cannot_mix_saved_diagnosis_with_changed_model_or_evidence(tmp_path, monkeypatch, changed):
    source = fixture_skill(tmp_path, monkeypatch)
    evidence_file = tmp_path / 'failures.txt'
    evidence_file.write_text('The response omitted turn_0001.')
    report = {'items': [{'id': 'case_1', 'status': 'completed', 'response': {'answer': 'original answer'}}]}
    monkeypatch.setattr('theraskill_agent.behavior_eval.get_report', lambda _: report)
    calls = []
    def model(system, payload):
        calls.append(payload)
        if 'diagnosis' not in payload:
            return diagnosis(), {}
        raise RuntimeError('fixture interruption')
    monkeypatch.setattr('theraskill_agent.skill_execution.model_json', model)
    engine = TaskEngine(TaskStore(tmp_path / 'tasks.sqlite'))
    try:
        task = engine.create('skill_evolution', 'Improve citations', 'en', {'skill': str(source), 'text': 'Missing citations',
                            'source_path': str(evidence_file), 'report_id': 'report-fixture'})
        failed = wait(engine, task['id'])
        assert 'evolution_diagnosis' in failed['checkpoints']
        if changed == 'model':
            monkeypatch.setattr('domains.theraskill.provider_config.resolve_model_config', lambda: {'name': 'new-model', 'api_key': 'private-key'})
        elif changed == 'source_evidence':
            evidence_file.write_text('Changed failure evidence, with a different expected output.')
        else:
            report['items'][0]['response'] = {'answer': 'changed answer'}
        engine.action(task['id'], 'retry', revision=failed['revision'])
        result = wait(engine, task['id'])
        assert result['status'] == 'failed' and 'changed during improvement' in result['error']
        assert len(calls) == 2 and not (tmp_path / 'data' / 'skill-drafts' / task['id']).exists()
        assert 'private-key' not in str(result)
    finally:
        engine.close()


def test_cancelling_during_draft_validation_never_publishes_candidate(tmp_path, monkeypatch):
    source = fixture_skill(tmp_path, monkeypatch)
    monkeypatch.setattr('theraskill_agent.skill_execution.model_json',
                        lambda system, payload: (revision() if 'diagnosis' in payload else diagnosis(), {}))
    from theraskill_agent import skill_readiness
    from theraskill_agent.paths import data_root
    original = skill_readiness.check_skill
    engine = TaskEngine(TaskStore(tmp_path / 'tasks.sqlite'))
    def cancel_before_publish(*args, **kwargs):
        result = original(*args, **kwargs)
        task = engine.store.list()[0]
        engine.action(task['id'], 'cancel', revision=task['revision'])
        return result
    monkeypatch.setattr(skill_readiness, 'check_skill', cancel_before_publish)
    try:
        task = engine.create('skill_evolution', 'Improve citations', 'en', {'skill': str(source), 'text': 'Missing citations'})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not engine.running:
                break
            time.sleep(.01)
        result = engine.store.get(task['id'])
        assert result['status'] == 'cancelled'
        assert not (data_root() / 'skill-drafts' / task['id']).exists()
        assert 'result' not in result['checkpoints']
        assert (source / 'SKILL.md').read_text() == ORIGINAL
    finally:
        engine.close()


def test_closing_worker_mid_stage_leaves_resumable_checkpoint_in_same_process(tmp_path, monkeypatch):
    source = fixture_skill(tmp_path, monkeypatch)
    store = TaskStore(tmp_path / 'tasks.sqlite')
    engine = TaskEngine(store)
    close_once, calls = [True], []
    def model(system, payload):
        calls.append(payload)
        if 'diagnosis' not in payload:
            return diagnosis(), {}
        if close_once[0]:
            close_once[0] = False
            engine.close()
        return revision(), {}
    monkeypatch.setattr('theraskill_agent.skill_execution.model_json', model)
    task = engine.create('skill_evolution', 'Improve citations', 'en', {'skill': str(source), 'text': 'Missing citations'})
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and store.get(task['id'])['status'] != 'interrupted':
            time.sleep(.01)
        interrupted = store.get(task['id'])
        assert interrupted['status'] == 'interrupted' and 'evolution_diagnosis' in interrupted['checkpoints']
        assert 'evolution_proposal' not in interrupted['checkpoints']
    finally:
        engine.close()
    replacement = TaskEngine(store)
    try:
        replacement.action(task['id'], 'retry', revision=interrupted['revision'])
        result = wait(replacement, task['id'])
        assert result['status'] == 'completed', result
        assert len([payload for payload in calls if 'diagnosis' not in payload]) == 1
    finally:
        replacement.close()
