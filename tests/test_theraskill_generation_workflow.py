from copy import deepcopy
import json
from pathlib import Path
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from domains.theraskill import theraskill_llm
from domains.theraskill.theraskill_simulation.scenario_generation import (
    LLMScenarioGenerator, ScenarioGenerationRequest, validate_scenario_review,
)
from domains.theraskill.theraskill_simulation.patient_builder import PatientDossier
from domains.theraskill.services.simulation_api.app import create_app, SimulationAPISettings

AUTHOR = {"X-Simulation-Role": "researcher"}


def generator(tmp_path, monkeypatch, *, reviewer=None, repair=None, fail=None):
    calls = []
    dossier = PatientDossier(presenting_concern="Work stress makes it hard to rest.",
        recent_events=[{"event_id": "model", "content": "Attended a meeting.", "sensitivity": "surface"}],
        training_objectives=["Reflect feelings"]).model_dump(mode="json")
    def model(client, prompt, payload, **kwargs):
        stage = payload["workflow_stage"]
        calls.append(deepcopy(payload))
        if fail and fail(stage):
            raise RuntimeError("Synthetic provider interruption")
        if stage == "plan": result = {"training_goal": "Reflect feelings"}
        elif stage == "patient": result = {"patient": dossier}
        elif stage == "package": result = {"scenario_brief": "Explore recent work stress with reflective listening.", "scenario_tags": ["reflection"]}
        elif stage == "repair": result = repair if repair is not None else {"scenario_tags": ["reflection", "revised"]}
        else: result = reviewer(stage) if reviewer else {"status": "pass", "issues": []}
        return deepcopy(result)
    monkeypatch.setattr(theraskill_llm, "chat_json_with_repair", model)
    obj = LLMScenarioGenerator.__new__(LLMScenarioGenerator)
    obj.client = SimpleNamespace(model="synthetic-test", base_url="http://example.invalid/v1", temperature=0)
    obj.output_root = tmp_path / "generated"
    obj.prompts = {"zh": {s: s for s in ("plan", "patient", "package", "review", "repair")}}
    obj._knowledge_context = lambda req: {"status": "disabled", "items": []}
    return obj, calls


def request(**kwargs):
    return ScenarioGenerationRequest(brief="Explore work stress and recent experiences.", use_knowledge=False, **kwargs)


def app_for(tmp_path, gen):
    registry = tmp_path / "scenarios.jsonl"
    registry.touch()
    return create_app(SimulationAPISettings(database_path=tmp_path / "sessions.sqlite", scenario_registry_path=registry,
        generated_scenario_registry_path=tmp_path / "generated.jsonl", generated_scenario_root=tmp_path / "generated",
        generated_batch_work_root=tmp_path / "jobs", initialize_llm_engine=False), scenario_generator=gen)


def repair_review(stage):
    return {"status": "repair", "issues": [{"code": "alignment", "severity": "error", "detail": "Training objective needs revision"}]}


def test_empty_review_stays_unresolved_and_requires_explicit_human_override(tmp_path, monkeypatch):
    gen, _ = generator(tmp_path, monkeypatch, reviewer=lambda _: {})
    with TestClient(app_for(tmp_path, gen)) as client:
        response = client.post("/scenarios/generate", headers=AUTHOR, json=request().model_dump(mode="json"))
        assert response.status_code == 201, response.text
        row = response.json()["scenario"]
        assert row["generation_workflow"]["review_status"] == "unavailable"
        path = f"/scenarios/generated/{row['scenario_id']}/review"
        assert client.post(path, headers=AUTHOR, json={"action": "approve"}).status_code == 422
        assert client.post(path, headers=AUTHOR, json={"action": "approve", "override_model_review": True}).status_code == 422
        result = client.post(path, headers=AUTHOR, json={"action": "approve", "override_model_review": True, "reason": "Manually checked the full dossier and teaching objective"})
        assert result.status_code == 200, result.text
        assert any(event["action"] == "model_review_override" for event in result.json()["review"]["history"])


def test_empty_repair_does_not_pass(tmp_path, monkeypatch):
    gen, calls = generator(tmp_path, monkeypatch, reviewer=repair_review, repair={})
    result = gen.generate(request())
    workflow = result["scenario"]["generation_workflow"]
    assert workflow["review_status"] == "repair"
    assert workflow["stages"][-1]["status"] == "failed"
    assert not any(c["workflow_stage"] == "re_review" for c in calls)


def test_valid_repair_must_pass_independent_rereview(tmp_path, monkeypatch):
    gen, calls = generator(tmp_path, monkeypatch, reviewer=repair_review)
    result = gen.generate(request())
    assert calls[-1]["workflow_stage"] == "re_review"
    assert result["scenario"]["generation_workflow"]["review_status"] == "repair"
    assert result["case_bundle"]["provenance"]["generation_workflow"]["review"]["repaired"]


def test_supplied_facts_are_pinned_before_package_and_review(tmp_path, monkeypatch):
    gen, calls = generator(tmp_path, monkeypatch)
    seed = PatientDossier(recent_events=[{"event_id": "user", "content": "Walked in a park.", "sensitivity": "surface"}])
    result = gen.generate(request(patient=seed))
    saved = result["case_bundle"]["patient_dossier"]
    for call in calls:
        if "patient_dossier" in call:
            assert call["patient_dossier"] == saved
    review = calls[-1]["scenario_package"]
    assert review["visible_profile"] == result["case_bundle"]["authoritative_layer"]["visible_profile"]
    assert review["learner_role_card"] == result["scenario"]["learner_role_card"]


def test_resume_reuses_completed_stages_and_stable_saved_result(tmp_path, monkeypatch):
    broken = [True]
    gen, calls = generator(tmp_path, monkeypatch, fail=lambda stage: stage == "package" and broken[0])
    with pytest.raises(RuntimeError, match="interruption"):
        gen.generate(request(), workflow_id="resume-test")
    broken[0] = False
    result = gen.generate(request(), workflow_id="resume-test")
    assert [c["workflow_stage"] for c in calls].count("patient") == 1
    assert [c["workflow_stage"] for c in calls].count("package") == 2
    count = len(calls)
    assert gen.generate(request(), workflow_id="resume-test") == result
    assert len(calls) == count
    with pytest.raises(ValueError, match="inputs or model changed"):
        gen.generate(ScenarioGenerationRequest(brief="A different case with new facts"), workflow_id="resume-test")
    gen.client.base_url = "http://different.invalid/v1"
    with pytest.raises(ValueError, match="inputs or model changed"):
        gen.generate(request(), workflow_id="resume-test")


def test_checkpoint_binds_concrete_provider_behind_agent_alias(tmp_path, monkeypatch):
    gen, _ = generator(tmp_path, monkeypatch)
    concrete = gen.client
    concrete.api_key = "fixture-key-one"
    gen.client = SimpleNamespace(model=concrete.model, base_url="agent://active-provider",
                                 _hosted_client=SimpleNamespace(_client=concrete))
    gen.generate(request(), workflow_id="native-provider")
    checkpoint = (gen.output_root / "workflows/native-provider/checkpoint.json").read_text()
    assert "fixture-key-one" not in checkpoint
    concrete.api_key = "fixture-key-two"
    with pytest.raises(ValueError, match="inputs or model changed"):
        gen.generate(request(), workflow_id="native-provider")


def test_unresolved_review_survives_fork_and_details_are_hidden_from_learners(tmp_path, monkeypatch):
    gen, _ = generator(tmp_path, monkeypatch, reviewer=repair_review, repair={})
    with TestClient(app_for(tmp_path, gen)) as client:
        row = client.post("/scenarios/generate", headers=AUTHOR, json=request().model_dump(mode="json")).json()["scenario"]
        copied = client.post(f"/scenarios/{row['scenario_id']}/fork", headers=AUTHOR,
                             json={"brief": "Reflect on a completely different relationship question"}).json()["scenario"]
        assert copied["generation_workflow"]["review_status"] == "stale"
        path = f"/scenarios/generated/{copied['scenario_id']}/review"
        assert client.post(path, headers=AUTHOR, json={"action": "approve"}).status_code == 422
    from domains.theraskill.services.simulation_api.app import scenario_public_digest
    original = {**row, "review_history": [{"reason": "Private event detail"}]}
    digest = scenario_public_digest(original)
    assert "issues" not in digest["generation_workflow"]
    assert "review_history" not in digest
    assert not any("error" in stage for stage in digest["generation_workflow"]["stages"])
    assert scenario_public_digest(original, include_review_details=True)["generation_workflow"]["issues"]


def test_course_api_plans_before_generation_and_resumes_patient_stages(tmp_path, monkeypatch):
    from domains.theraskill.theraskill_simulation import course_generation
    broken = [True]
    gen, calls = generator(tmp_path, monkeypatch, fail=lambda stage: stage == "package" and broken[0])
    plan = {"objectives": ["Reflect feelings"], "cases": [{"index": 1, "learning_objective": "Explore work feedback", "brief": "A distinct planned work feedback scenario"}]}
    monkeypatch.setattr(course_generation, "plan_course", lambda *args, **kwargs: plan)
    monkeypatch.setattr(course_generation, "course_requests", lambda *args: [request()])
    reviewed = []
    def review(client, req, design, rows, **kwargs):
        reviewed.extend(rows)
        assert design == plan
        assert Path(rows[0]["scenario"]["case_bundle_path"]).is_file()
        return {"status": "pass", "coverage": []}
    monkeypatch.setattr(course_generation, "review_course", review)
    with TestClient(app_for(tmp_path, gen)) as client:
        job = client.post("/scenarios/generate-batch/jobs", headers=AUTHOR,
                          json={"count": 1, "brief": "Practice listening to work stress", "course_title": "Reflective listening"}).json()
        path = f"/scenarios/generate-batch/jobs/{job['job_id']}"
        def wait():
            for _ in range(100):
                value = client.get(path, headers=AUTHOR).json()
                if value["status"] not in {"queued", "running"}: return value
                time.sleep(.01)
            raise AssertionError("Job did not settle")
        assert wait()["status"] == "failed"
        broken[0] = False
        assert client.post(path + "/retry", headers=AUTHOR).status_code == 200
        done = wait()
        assert done["status"] == "completed", done
        assert done["result"]["course_plan"] == plan
        # The minimal mock confirms the workflow stage ran, but has no verified
        # artifact/plan binding; it must not unlock course reuse.
        assert done["result"]["course_review"]["status"] == "needs_review"
        assert done["course_handoff"]["coverage_current"] is False
        assert len(reviewed) == 1
        assert [call["workflow_stage"] for call in calls].count("patient") == 1


def test_edit_invalidates_review_and_recheck_uses_new_version(tmp_path, monkeypatch):
    gen, calls = generator(tmp_path, monkeypatch)
    with TestClient(app_for(tmp_path, gen)) as client:
        row = client.post("/scenarios/generate", headers=AUTHOR, json=request().model_dump(mode="json")).json()["scenario"]
        path = f"/scenarios/generated/{row['scenario_id']}"
        updated = client.patch(path, headers=AUTHOR, json={"scenario_brief": "Practice a different reflective listening goal", "expected_revision": 1})
        assert updated.status_code == 200, updated.text
        assert updated.json()["scenario"]["generation_workflow"]["review_status"] == "stale"
        assert client.post(path + "/review", headers=AUTHOR, json={"action": "approve"}).status_code == 422
        checked = client.post(path + "/check", headers=AUTHOR)
        assert checked.status_code == 200, checked.text
        assert calls[-1]["scenario_package"]["scenario_brief"] == "Practice a different reflective listening goal"
        assert client.post(path + "/review", headers=AUTHOR, json={"action": "approve"}).status_code == 200


def test_background_generation_persists_and_lists_completed_job(tmp_path, monkeypatch):
    gen, _ = generator(tmp_path, monkeypatch)
    with TestClient(app_for(tmp_path, gen)) as client:
        job = client.post("/scenarios/generate/jobs", headers=AUTHOR, json=request().model_dump(mode="json")).json()
        for _ in range(100):
            current = client.get(f"/scenarios/generate/jobs/{job['job_id']}", headers=AUTHOR).json()
            if current["status"] not in {"queued", "running"}: break
            time.sleep(.01)
        assert current["status"] == "completed", current
        assert current["progress"]["stage"] == "review"
        assert current["result"]["scenario"]["scenario_id"]
        assert client.get("/scenarios/generate/jobs", headers=AUTHOR).json()["jobs"][0]["job_id"] == job["job_id"]
        assert client.get("/scenarios/generate/jobs", headers={"X-Simulation-Role": "human_therapist"}).status_code == 403


@pytest.mark.parametrize("value", [{}, {"status": "pass"}, {"status": "repair", "issues": []},
    {"status": "pass", "issues": [{"code": "problem", "severity": "error", "detail": "Unresolved"}]}])
def test_review_contract_rejects_incomplete_or_contradictory_pass(value):
    with pytest.raises(ValueError): validate_scenario_review(value)
