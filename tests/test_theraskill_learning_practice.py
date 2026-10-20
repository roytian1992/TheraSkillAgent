"""Offline contracts for learner submissions and durable formative evaluation."""
from copy import deepcopy
from types import SimpleNamespace
import json
import threading
import time

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
import pytest

from domains.theraskill.theraskill_simulation.contracts import ActorRole
from domains.theraskill.theraskill_simulation.learning import LearningService
from domains.theraskill.theraskill_simulation.learning_practice import (
    LearningPracticeService, create_practice_router, learning_evaluation_signature,
)
from domains.theraskill.theraskill_simulation.training import (
    SQLiteTrainingRepository, TrainingAssignment, TrainingCourse,
)


class Views:
    def __init__(self):
        self.data = {"s": {"session_id": "s", "mode": "human_therapist_vs_patient", "status": "completed",
                           "scenario": {"scenario_id": "case", "scenario_brief": "A fictional learning case"},
                           "transcript": [{"turn_id": "patient-1", "role": "patient", "text": "I feel ignored."},
                                          {"turn_id": "learner-1", "role": "human_therapist", "text": "It sounds lonely. Am I understanding?"}],
                           "review": {"status": "complete", "gate_status": "pass"}, "progress": {}}}

    def get_snapshot(self, sid):
        return SimpleNamespace(public_view=SimpleNamespace(model_dump=lambda **_: deepcopy(self.data[sid])))


class Judge:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.started = threading.Event()
        self.release = None

    def evaluate(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        self.started.set()
        if self.release is not None:
            assert self.release.wait(5), "Test did not release judge"
        if self.fail:
            raise RuntimeError("Synthetic model outage")
        return {"status": "complete", "assessment_kind": "formative", "objectives": [],
                "strengths": ["Synthetic feedback"], "next_practice": []}


@pytest.fixture
def setup(tmp_path):
    repo = SQLiteTrainingRepository(tmp_path / "learning.sqlite")
    exercise = {"practice_id": "micro-empathy", "competency_id": "empathy", "title": "Reflect emotion",
                "prompt": "Write a response", "target_behavior": "Reflect and check emotion",
                "scenario_text": "A fictional patient says: I feel ignored.",
                "example_response": "An example answer is not learner evidence.",
                "example_discussion": "One possible interpretation, not a unique answer.",
                "hint": "Name the emotion before asking", "reflection_prompt": "What did you notice?",
                "knowledge_unit_ids": ["k1"]}
    repo.add_course(TrainingCourse(course_id="course", title="Empathy", scenario_ids=["case"],
                                  learning_objectives=[{"id": "empathy", "description": "Validation",
                                                        "observable_behaviors": ["Reflect and check emotion", "Discuss consent"]}],
                                  learning_units={"micro_practices": [exercise]},
                                  knowledge_references=[{"unit_id": "k1", "quote": "Check your understanding."},
                                                        {"unit_id": "k2", "quote": "Unrelated course material."}]))
    repo.add_assignment(TrainingAssignment(assignment_id="assignment", title="Learning", assignee="learner",
                                          course_id="course", scenario_ids=["case"]))
    views = Views()
    learning = LearningService(repo, views)
    judge = Judge()
    service = LearningPracticeService(learning, lambda: judge, tmp_path / "jobs")
    yield service, learning, judge, views
    service.jobs._executor.shutdown(wait=True, cancel_futures=True)


def submit(service, **updates):
    return service.submit("assignment", {"practice_id": "micro-empathy", "response_text": "It sounds lonely. Is that right?",
                                         "self_reflection": "I wanted to check rather than assume.", "hint_used": True,
                                         "request_id": "request-1", "language": "en", **updates})


def finished(service, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.get_job(job_id)
        if job["status"] in {"completed", "failed", "partial"}:
            return job
        time.sleep(.005)
    pytest.fail("Judge job did not finish")


def test_submission_is_immutable_and_request_id_is_bound_to_all_input(setup):
    service, _, _, _ = setup
    first = submit(service, response_text="  It sounds lonely. Is that right?  ")
    assert submit(service)["submission_id"] == first["submission_id"]
    for changes in ({"hint_used": False}, {"response_text": "Different"}, {"self_reflection": "Different"}, {"language": "zh"}):
        with pytest.raises(ValueError, match="Request ID reused"):
            submit(service, **changes)
    assert len(service.micro_practices("assignment")["submissions"]) == 1
    assert first["response_text"] == "It sounds lonely. Is that right?"


@pytest.mark.parametrize("changes", [{"response_text": "  "}, {"request_id": " "}, {"practice_id": ""},
                                     {"response_text": "x" * 12001}, {"self_reflection": "x" * 2001},
                                     {"hint_used": "false"}, {"language": "fr"}, {"practice_id": "not-in-course"}])
def test_submission_rejects_invalid_input_without_saving(setup, changes):
    service, _, _, _ = setup
    with pytest.raises(ValueError):
        submit(service, **changes)
    assert service.micro_practices("assignment")["submissions"] == []


def test_micro_evidence_excludes_prompt_examples_and_reflection_and_preserves_hint(setup):
    service, learning, judge, _ = setup
    item = submit(service)
    job = finished(service, service.judge_submission(item["submission_id"])["job_id"])
    assert job["status"] == "completed" and job["source_current"]
    call = judge.calls[0]
    assert call["transcript"] == [{"turn_id": "stimulus:" + item["submission_id"], "role": "exercise_context", "text": item["exercise_snapshot"]["scenario_text"]},
                                  {"turn_id": "submission:" + item["submission_id"], "role": "trainee", "text": item["response_text"]}]
    assert "example_response" not in call["context"]["exercise"]
    assert "example_discussion" not in call["context"]["exercise"]
    assert item["exercise_snapshot"]["example_response"].startswith("An example")
    assert call["context"]["hint_used"] is True
    assert call["objectives"][0]["observable_behaviors"] == ["Reflect and check emotion"]
    assert [row["unit_id"] for row in call["references"]] == ["k1"]
    assert job["result"]["hint_used"] is True
    assert job["result"]["human_approval"] is False
    assert job["result"]["counts_toward_completion"] is False
    assert learning.progress("assignment")["completion"]["completed"] == 0


def test_repeated_judge_clicks_and_reload_reuse_one_job(setup, tmp_path):
    service, learning, judge, _ = setup
    item = submit(service)
    first = finished(service, service.judge_submission(item["submission_id"])["job_id"])
    assert service.judge_submission(item["submission_id"])["job_id"] == first["job_id"]
    service.jobs._executor.shutdown(wait=True)
    loaded = LearningPracticeService(learning, lambda: judge, tmp_path / "jobs")
    try:
        assert loaded.judge_submission(item["submission_id"])["job_id"] == first["job_id"]
        assert loaded.micro_practices("assignment")["submissions"][0]["latest_judge_job"]["status"] == "completed"
        assert len(judge.calls) == 1
    finally:
        loaded.jobs._executor.shutdown(wait=True)


def test_failed_judge_keeps_submission_and_requires_explicit_bounded_retry(setup):
    service, _, judge, _ = setup
    item = submit(service)
    judge.fail = True
    job = finished(service, service.judge_submission(item["submission_id"])["job_id"])
    assert job["status"] == "failed" and job["retry_allowed"]
    assert service.get_submission(item["submission_id"])["response_text"] == item["response_text"]
    assert service.judge_submission(item["submission_id"])["job_id"] == job["job_id"]
    assert len(judge.calls) == 1
    for execution in (2, 3):
        job = finished(service, service.retry_job(job["job_id"])["job_id"])
        assert job["execution_count"] == execution
    assert not job["retry_allowed"]
    with pytest.raises(ValueError, match="retry limit"):
        service.retry_job(job["job_id"])
    assert len(judge.calls) == 3


def test_retry_after_outage_can_succeed_without_new_submission(setup):
    service, _, judge, _ = setup
    item = submit(service)
    judge.fail = True
    job = finished(service, service.judge_submission(item["submission_id"])["job_id"])
    judge.fail = False
    retried = finished(service, service.retry_job(job["job_id"])["job_id"])
    assert retried["status"] == "completed" and retried["execution_count"] == 2
    assert len(service.micro_practices("assignment")["submissions"]) == 1


def test_restart_does_not_silently_repeat_interrupted_remote_calls(setup, tmp_path):
    service, learning, judge, _ = setup
    item = submit(service)
    job = finished(service, service.judge_submission(item["submission_id"])["job_id"])
    service.jobs._executor.shutdown(wait=True)
    path = tmp_path / "jobs" / job["job_id"] / "job.json"
    payload = json.loads(path.read_text())
    payload.update(status="running", result=None)
    path.write_text(json.dumps(payload))
    loaded = LearningPracticeService(learning, lambda: judge, tmp_path / "jobs")
    try:
        recovered = loaded.get_job(job["job_id"])
        assert recovered["status"] == "failed" and recovered["retry_allowed"]
        assert "interrupted" in recovered["error"]
        assert len(judge.calls) == 1
        assert finished(loaded, loaded.retry_job(job["job_id"])["job_id"])["status"] == "completed"
        assert len(judge.calls) == 2
    finally:
        loaded.jobs._executor.shutdown(wait=True)


def test_full_attempt_uses_actual_transcript_and_changes_invalidate_completed_feedback(setup):
    service, learning, judge, views = setup
    attempt = learning.link("assignment", "s")
    first = finished(service, service.judge_attempt(attempt["id"], "en")["job_id"])
    assert first["status"] == "completed"
    assert judge.calls[0]["transcript"][1]["role"] == "trainee"
    assert judge.calls[0]["transcript"][1]["turn_id"] == "learner-1"
    assert judge.calls[0]["context"]["hint_used"] is None
    assert service.micro_practices("assignment")["attempt_judge_jobs"][attempt["id"]][0]["job_id"] == first["job_id"]
    views.data["s"]["transcript"][1]["text"] = "A revised learner response"
    assert not service.get_job(first["job_id"])["source_current"]
    with pytest.raises(ValueError, match="stale"):
        service.retry_job(first["job_id"])
    second = finished(service, service.judge_attempt(attempt["id"], "en")["job_id"])
    assert second["job_id"] != first["job_id"] and second["source_current"]
    assert len(judge.calls) == 2
    assert learning.progress("assignment")["completion"]["completed"] == 0
    assert learning.reviews(attempt["id"]) == []


def test_change_during_evaluation_discards_stale_result(setup):
    service, learning, judge, views = setup
    attempt = learning.link("assignment", "s")
    judge.release = threading.Event()
    job = service.judge_attempt(attempt["id"])
    assert judge.started.wait(2)
    views.data["s"]["transcript"].append({"turn_id": "learner-2", "role": "therapist", "text": "Another response"})
    judge.release.set()
    result = finished(service, job["job_id"])
    assert result["status"] == "failed" and "stale" in result["error"]
    assert result["result"] is None and not result["retry_allowed"]


def test_reflection_and_course_rubric_change_invalidate_feedback(setup):
    service, learning, _, _ = setup
    attempt = learning.link("assignment", "s")
    job = finished(service, service.judge_attempt(attempt["id"])["job_id"])
    learning.save_reflection(attempt["id"], "A new reflection")
    assert not service.get_job(job["job_id"])["source_current"]
    job = finished(service, service.judge_attempt(attempt["id"])["job_id"])
    course = learning.repo.get_course("course").model_dump(mode="json")
    course["learning_objectives"][0]["description"] = "Updated objective"
    with learning.repo._connect() as db:
        db.execute("UPDATE training_courses SET payload_json=? WHERE course_id='course'", (json.dumps(course),))
    assert not service.get_job(job["job_id"])["source_current"]


def test_submission_keeps_original_exercise_after_course_edit(setup):
    service, learning, judge, _ = setup
    item = submit(service)
    course = learning.repo.get_course("course").model_dump(mode="json")
    course["learning_units"]["micro_practices"][0]["prompt"] = "A new assignment question"
    with learning.repo._connect() as db:
        db.execute("UPDATE training_courses SET payload_json=? WHERE course_id='course'", (json.dumps(course),))
    view = service.micro_practices("assignment")
    assert view["submissions"][0]["exercise_current"] is False
    assert view["exercises"][0]["prompt"] == "A new assignment question"
    assert submit(service)["submission_id"] == item["submission_id"]
    job = finished(service, service.judge_submission(item["submission_id"])["job_id"])
    assert job["status"] == "completed"
    assert judge.calls[0]["context"]["exercise"]["prompt"] == "Write a response"


def test_corrupt_saved_input_cannot_be_retried(setup, tmp_path):
    service, learning, judge, _ = setup
    item = submit(service)
    judge.fail = True
    job = finished(service, service.judge_submission(item["submission_id"])["job_id"])
    service.jobs._executor.shutdown(wait=True)
    path = tmp_path / "jobs" / job["job_id"] / "job.json"
    saved = json.loads(path.read_text())
    saved["request"]["snapshot"]["transcript"][0]["text"] = "Tampered"
    path.write_text(json.dumps(saved))
    loaded = LearningPracticeService(learning, lambda: judge, tmp_path / "jobs")
    try:
        assert not loaded.get_job(job["job_id"])["source_current"]
        with pytest.raises(ValueError, match="integrity"):
            loaded.retry_job(job["job_id"])
        assert len(judge.calls) == 1
    finally:
        loaded.jobs._executor.shutdown(wait=True)


def test_agent_therapist_and_empty_sessions_are_not_learner_evidence(setup):
    service, learning, _, views = setup
    attempt = learning.link("assignment", "s")
    views.data["s"]["mode"] = "human_client_role_vs_therapist"
    with pytest.raises(ValueError, match="human therapist"):
        service.judge_attempt(attempt["id"])
    views.data["s"]["mode"] = "human_therapist_vs_patient"
    views.data["s"]["transcript"][1]["role"] = "therapist_agent"
    with pytest.raises(ValueError, match="no actual learner"):
        service.judge_attempt(attempt["id"])


def test_human_micro_review_is_separate_and_does_not_complete_case(setup):
    service, learning, _, _ = setup
    item = submit(service)
    for decision in ("needs_practice", "approved"):
        service.review_submission(item["submission_id"], decision=decision, comment="An instructor observation", reviewer="Teacher", score=4)
    saved = service.get_submission(item["submission_id"])
    assert [row["decision"] for row in saved["human_reviews"]] == ["needs_practice", "approved"]
    assert all(row["review_kind"] == "human" for row in saved["human_reviews"])
    assert saved["judge_jobs"] == []
    assert learning.progress("assignment")["completion"]["completed"] == 0
    for score in (True, float("nan"), 0, 6):
        with pytest.raises(ValueError, match="Score"):
            service.review_submission(item["submission_id"], decision="approved", comment="Reviewed", reviewer="Teacher", score=score)


def test_http_routes_round_trip_validate_and_enforce_teacher_review(setup):
    service, learning, _, _ = setup
    app = FastAPI()

    def caller_role(x_role: str = Header(default="human_therapist")):
        return ActorRole(x_role)

    app.include_router(create_practice_router(service, caller_role))
    with TestClient(app) as client:
        assert client.get("/assignments/missing/micro-practices").status_code == 404
        assert client.get("/assignments/assignment/micro-practices").json()["exercises"][0]["practice_id"] == "micro-empathy"
        body = {"practice_id": "micro-empathy", "response_text": "It sounds lonely.", "request_id": "http-1"}
        response = client.post("/assignments/assignment/micro-practices", json=body)
        assert response.status_code == 201
        sid = response.json()["submission_id"]
        invalid = client.post("/assignments/assignment/micro-practices", json={**body, "response_text": "   "})
        assert invalid.status_code == 422
        response = client.post(f"/learning/submissions/{sid}/judge")
        assert response.status_code == 202
        job = finished(service, response.json()["job_id"])
        assert client.get(f"/learning/judge-jobs/{job['job_id']}").json()["status"] == "completed"
        review = {"decision": "approved", "comment": "Observed reflection", "reviewer": "Teacher", "score": 4}
        assert client.post(f"/learning/submissions/{sid}/review", json=review).status_code == 403
        assert client.post(f"/learning/submissions/{sid}/review", json=review, headers={"x-role": "instructor"}).status_code == 201
        assert client.post(f"/learning/submissions/{sid}/review", json={**review, "score": True}, headers={"x-role": "instructor"}).status_code == 422
        attempt = learning.link("assignment", "s")
        assert client.post(f"/learning/attempts/{attempt['id']}/judge").status_code == 202


def test_model_or_index_change_creates_new_job_but_same_environment_reuses(setup):
    service, _, judge, _ = setup
    environment = {"model": "model-a", "index": "index-a", "private_url": "https://private.example/v1", "api_key": "secret-do-not-persist"}
    service.evaluation_signature = lambda: environment
    item = submit(service)
    first = finished(service, service.judge_submission(item['submission_id'])['job_id'])
    assert service.judge_submission(item['submission_id'])['job_id'] == first['job_id']
    environment['model'] = 'model-b'
    assert not service.get_job(first['job_id'])['source_current']
    assert 'environment changed' in service.get_job(first['job_id'])['stale_reason']
    second = finished(service, service.judge_submission(item['submission_id'])['job_id'])
    assert second['job_id'] != first['job_id'] and second['source_current']
    environment['index'] = 'index-b'
    third = finished(service, service.judge_submission(item['submission_id'])['job_id'])
    assert third['job_id'] not in {first['job_id'], second['job_id']}
    assert len(judge.calls) == 3
    assert len(service.get_submission(item['submission_id'])['judge_jobs']) == 3
    with pytest.raises(ValueError, match='environment changed'):
        service.retry_job(first['job_id'])
    for path in service.jobs.root.glob('*/job.json'):
        stored = path.read_text()
        assert 'secret-do-not-persist' not in stored
        assert 'private.example' not in stored


def test_environment_binding_is_restored_after_restart(setup, tmp_path):
    service, learning, judge, _ = setup
    environment = {'model': 'a', 'index': 'one'}
    service.evaluation_signature = lambda: environment
    item = submit(service)
    job = finished(service, service.judge_submission(item['submission_id'])['job_id'])
    service.jobs._executor.shutdown(wait=True)
    loaded = LearningPracticeService(learning, lambda: judge, tmp_path / 'jobs', evaluation_signature=lambda: environment)
    try:
        assert loaded.judge_submission(item['submission_id'])['job_id'] == job['job_id']
        environment['index'] = 'two'
        assert not loaded.get_job(job['job_id'])['source_current']
        latest = finished(loaded, loaded.judge_submission(item['submission_id'])['job_id'])
        assert latest['job_id'] != job['job_id'] and latest['source_current']
        assert len(judge.calls) == 2
    finally:
        loaded.jobs._executor.shutdown(wait=True)


def test_environment_change_during_judging_discards_result(setup):
    service, _, judge, _ = setup
    environment = {'model': 'first'}
    service.evaluation_signature = lambda: environment
    item = submit(service)
    judge.release = threading.Event()
    job = service.judge_submission(item['submission_id'])
    assert judge.started.wait(2)
    environment['model'] = 'changed'
    judge.release.set()
    result = finished(service, job['job_id'])
    assert result['status'] == 'failed' and result['result'] is None
    assert 'environment changed' in result['error']
    assert not result['source_current']


def test_missing_configuration_does_not_break_history_and_fails_in_background(setup):
    service, _, judge, _ = setup
    item = submit(service)
    old = finished(service, service.judge_submission(item['submission_id'])['job_id'])

    def unavailable():
        raise ValueError('private-url.example secret-credential')

    service.evaluation_signature = unavailable
    history = service.micro_practices('assignment')
    assert not history['submissions'][0]['judge_jobs'][0]['source_current']
    assert 'unavailable' in history['submissions'][0]['judge_jobs'][0]['stale_reason']
    job = finished(service, service.judge_submission(item['submission_id'])['job_id'])
    assert job['job_id'] != old['job_id']
    assert job['status'] == 'failed' and 'unavailable' in job['error']
    assert len(judge.calls) == 1
    assert 'secret-credential' not in json.dumps(job)
    assert 'private-url.example' not in json.dumps(history)
    service.evaluation_signature = lambda: {'model': 'fixed'}
    assert finished(service, service.judge_submission(item['submission_id'])['job_id'])['status'] == 'completed'


def test_production_signature_hashes_prompt_provider_controls_and_index_not_credentials(monkeypatch, tmp_path):
    import domains.theraskill.provider_config as providers
    import domains.theraskill.theraskill_llm as llm
    import domains.theraskill.theraskill_simulation.learning_judge as judge_module
    from theraskill_agent import resources
    config = {'model': 'model-a', 'name': 'model-a', 'base_url': 'https://private.example/v1',
              'api_key': 'private-credential', 'temperature': .1, 'max_output_tokens': 2000}
    index = {'index_id': 'index-a', 'input': {'knowledge_sha256': 'a' * 64}}
    monkeypatch.setattr(providers, 'resolve_model_config', lambda: deepcopy(config))
    monkeypatch.setattr(llm, 'resolve_model_config', lambda: deepcopy(config))
    monkeypatch.setattr(resources, 'active_index', lambda: (tmp_path, index))
    first = learning_evaluation_signature()
    assert first['status'] == 'available'
    assert all(len(value) == 64 for key, value in first.items() if key.endswith('_sha256'))
    assert all(text not in json.dumps(first) for text in ('model-a', 'private.example', 'private-credential'))
    config['api_key'] = 'rotated-credential'
    assert learning_evaluation_signature() == first
    config['max_output_tokens'] = 4000
    changed = learning_evaluation_signature()
    assert changed['model_sha256'] != first['model_sha256']
    assert changed['index_sha256'] == first['index_sha256']
    index['index_id'] = 'index-b'
    assert learning_evaluation_signature()['index_sha256'] != changed['index_sha256']
    before_prompt = learning_evaluation_signature()
    monkeypatch.setattr(judge_module, 'JUDGE_PROMPT', judge_module.JUDGE_PROMPT + '\nNew grader version')
    assert learning_evaluation_signature()['prompt_sha256'] != before_prompt['prompt_sha256']
    domain = tmp_path / 'domain.yaml'
    domain.write_text('model:\n  temperature: 0.7\n')
    with_override = learning_evaluation_signature(domain)
    assert with_override['model_sha256'] != learning_evaluation_signature()['model_sha256']
    config['temperature'] = .7
    assert with_override['model_sha256'] == learning_evaluation_signature()['model_sha256']


def test_production_signature_missing_provider_or_invalid_index_is_safe_unavailable(monkeypatch):
    import domains.theraskill.provider_config as providers
    from theraskill_agent import resources

    def broken():
        raise ValueError('secret-credential private-url.example')

    monkeypatch.setattr(providers, 'resolve_model_config', broken)
    monkeypatch.setattr(resources, 'active_index', broken)
    signature = learning_evaluation_signature()
    assert signature['status'] == 'unavailable'
    assert 'secret-credential' not in json.dumps(signature)
    assert 'private-url.example' not in json.dumps(signature)
