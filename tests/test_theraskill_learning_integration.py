"""Exercise the real teaching API and verified judge with a deterministic model."""
import json
import time
from fastapi.testclient import TestClient

from domains.theraskill.services.simulation_api.app import SimulationAPISettings, create_app
from domains.theraskill.theraskill_simulation.learning_judge import LearningJudge


class EvidenceClient:
    model = 'integration-fixture'

    def chat(self, prompt, payload, **kwargs):
        objective = payload['objective']
        trainee = next(t for t in payload['transcript'] if t['role'] == 'trainee')
        source = payload['references'][0]
        finding = {'observation': 'observed', 'rating': 4, 'rationale': 'The learner invites correction.',
                   'transcript_evidence': [{'turn_id': trainee['turn_id'], 'quote': trainee['text']}],
                   'knowledge_evidence': [{'unit_id': source['unit_id'], 'quote': source['text']}],
                   'knowledge_note': ''}
        return json.dumps({'objective_id': objective['objective_id'], **finding,
            'behaviors': [{'behavior_id': b['behavior_id'], **finding} for b in objective['behaviors']],
            'strengths': ['Checks understanding'], 'next_practice': ['Practise receiving a correction']})


def test_course_to_submission_judge_and_teacher_review_survives_restart(tmp_path, monkeypatch):
    reference = {'unit_id': 'fixture-reference', 'text': 'Invite the client to correct your understanding.'}
    retrieve = lambda *a, **kw: {'matches': [reference], 'index_id': 'fixture-index', 'retrieval_mode': 'lexical'}
    monkeypatch.setattr('plugins.theraskill.retrieval.search_managed_knowledge', retrieve)
    registry = tmp_path / 'scenarios.jsonl'
    registry.write_text(json.dumps({'scenario_id': 'fixture-case', 'scenario_family': 'supportive_exploration',
                                   'case_id': 'fixture-case', 'case_family': 'training', 'difficulty_band': 'low',
                                   'risk_band': 'low', 'scenario_brief': 'A fictional training case',
                                   'language': 'en', 'generated': False})+'\n')
    settings = SimulationAPISettings(database_path=tmp_path / 'sessions.sqlite', scenario_registry_path=registry,
        generated_scenario_registry_path=tmp_path / 'generated.jsonl', generated_batch_work_root=tmp_path / 'batches',
        generated_scenario_root=tmp_path / 'generated', initialize_llm_engine=False, initialize_scenario_generator=False)
    factory = lambda: LearningJudge(tmp_path, tmp_path / 'unused.yaml', client=EvidenceClient(), retrieve=retrieve)
    teacher = {'X-Simulation-Role': 'instructor'}
    learner = {'X-Simulation-Role': 'human_therapist'}
    with TestClient(create_app(settings, learning_judge_factory=factory)) as api:
        seed = api.post('/courses/blueprint', json={'language': 'en'}, headers=teacher).json()
        response = api.post('/courses', headers=teacher, json={
            'title': seed['title'], 'scenario_ids': ['fixture-case'], 'learning_objectives': seed['objectives'],
            'learning_units': seed['generation_payload']['learning_units'], 'knowledge_references': seed['knowledge_references']})
        assert response.status_code == 201, response.text
        course = response.json()
        response = api.post('/assignments', headers=teacher, json={'title': 'First practice', 'assignee': 'fixture-learner',
            'course_id': course['course_id'], 'scenario_ids': course['scenario_ids']})
        assert response.status_code == 201, response.text
        assignment = response.json()['assignment_id']
        exercise = api.get(f'/assignments/{assignment}/micro-practices', headers=learner).json()['exercises'][0]
        response = api.post(f'/assignments/{assignment}/micro-practices', headers=learner, json={
            'practice_id': exercise['practice_id'], 'response_text': 'That sounds painful. Am I understanding you?',
            'self_reflection': 'I left room for correction.', 'hint_used': True, 'request_id': 'synthetic-1', 'language': 'en'})
        assert response.status_code == 201, response.text
        sid = response.json()['submission_id']
        response = api.post(f'/learning/submissions/{sid}/judge', headers=learner)
        assert response.status_code == 202, response.text
        job_id = response.json()['job_id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = api.get('/learning/judge-jobs/'+job_id, headers=learner).json()
            if job['status'] in {'completed', 'failed'}:
                break
            time.sleep(.01)
        assert job['status'] == 'completed', job
        assert job['result']['evidence_mode'] == 'knowledge_grounded'
        assert not job['result']['counts_toward_completion']
        assert job['result']['hint_used']
        assert api.post(f'/learning/submissions/{sid}/review', headers=learner,
            json={'decision':'approved','comment':'Clear reflection','reviewer':'Instructor'}).status_code == 403
        assert api.post(f'/learning/submissions/{sid}/review', headers=teacher,
            json={'decision':'approved','comment':'Clear reflection','reviewer':'Instructor','score':4}).status_code == 201
        progress = api.get(f'/assignments/{assignment}/progress', headers=learner).json()
        assert progress['completion']['completed'] == 0
    with TestClient(create_app(settings, learning_judge_factory=factory)) as api:
        history = api.get(f'/assignments/{assignment}/micro-practices', headers=learner).json()
        assert history['submissions'][0]['submission_id'] == sid
        assert history['submissions'][0]['latest_judge_job']['status'] == 'completed'
        assert history['submissions'][0]['human_reviews'][0]['score'] == 4
        assert api.post(f'/learning/submissions/{sid}/judge', headers=learner).json()['job_id'] == job_id
