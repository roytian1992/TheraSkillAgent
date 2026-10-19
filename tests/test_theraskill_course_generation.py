from __future__ import annotations

import json

import pytest

from domains.theraskill.theraskill_simulation.course_generation import (
    course_requests, plan_course, review_course, validate_course_plan,
)
from domains.theraskill.theraskill_simulation.generation_workflow import generation_checkpoint
from domains.theraskill.theraskill_simulation.scenario_generation import ScenarioGenerationBatchRequest


def request():
    return ScenarioGenerationBatchRequest(course_title="Practising listening", course_description="A two-case course",
        brief="Explore emotional concerns after recent stressful events.", count=2,
        skill_tags=["reflective-listening"], scenario_tags=["training"],
        constraints="Keep the encounter in an outpatient setting.", language="en")


def plan():
    return {"objectives": ["Reflect emotions", "Ask open questions"], "cases": [
        {"index": 1, "learning_objective": "Reflect shame after criticism", "brief": "The client was criticised in a meeting yesterday and avoids colleagues.",
         "scenario_family": "supportive_exploration", "difficulty_band": "medium", "risk_band": "low",
         "scenario_tags": ["workplace"], "constraints": "Recent experience: yesterday's meeting; explore shame without jumping to advice."},
        {"index": 2, "learning_objective": "Explore grief through open questions", "brief": "The client recently lost a pet and has stopped attending a weekly walking group.",
         "scenario_family": "supportive_exploration", "difficulty_band": "medium", "risk_band": "low",
         "scenario_tags": ["grief"], "constraints": "Recent experience: the pet died last week; invite exploration of its meaning."},
    ]}


class Client:
    model = "fixture-model"

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def chat(self, system_prompt, payload, **kwargs):
        self.calls.append((system_prompt, payload))
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)


def actual_rows(tmp_path):
    rows = []
    for index, (concern, event, objective) in enumerate([
        ("I feel ashamed after criticism.", "My manager criticised me yesterday.", "Reflect emotions"),
        ("I miss my pet and feel alone.", "My pet died last week.", "Ask open questions"),
    ], start=1):
        dossier = {"display_name": f"Client {index}", "presenting_concern": concern,
            "recent_events": [{"event_id": f"e{index}", "content": event, "time_label": "recently"}],
            "training_objectives": [objective]}
        path = tmp_path / f"case-{index}.json"
        path.write_text(json.dumps({"patient_dossier": dossier}))
        rows.append({"scenario_id": f"scenario-{index}", "batch_index": index,
            "scenario_brief": concern, "scenario_family": "supportive_exploration", "difficulty_band": "medium", "risk_band": "low",
            "case_bundle_path": str(path), "generation_workflow": {"review_status": "pass"}})
    return rows


def coverage():
    return {"status": "pass", "summary": "The actual patients support distinct training opportunities.", "issues": [], "duplicate_pairs": [],
        "case_reviews": [
            {"index": 1, "covered": True, "evidence": ["My manager criticised me yesterday."], "issues": []},
            {"index": 2, "covered": True, "evidence": ["My pet died last week."], "issues": []},
        ], "objective_coverage": [
            {"objective": "Reflect emotions", "covered": True, "case_indices": [1], "rationale": "A concrete shame trigger."},
            {"objective": "Ask open questions", "covered": True, "case_indices": [2], "rationale": "A recent loss with reduced social participation."},
        ]}


def test_plan_requests_preserve_user_constraints_and_use_different_cases():
    client = Client([plan()])
    result = plan_course(client, request())
    requests = course_requests(request(), result)
    assert len(client.calls) == 1 and len(requests) == 2
    assert requests[0].brief != requests[1].brief
    assert all(item.constraints.startswith(request().constraints) for item in requests)
    assert all(item.risk_band == "low" and item.language == "en" for item in requests)
    assert "reflective-listening" in requests[0].scenario_tags
    assert "workplace" in requests[0].scenario_tags and "grief" in requests[1].scenario_tags


@pytest.mark.parametrize("field,value", [("risk_band", "high"), ("difficulty_band", "high"), ("scenario_family", "another_family"), ("index", 3)])
def test_planner_cannot_escalate_or_reindex_cases(field, value):
    data = plan()
    data["cases"][1][field] = value
    with pytest.raises(ValueError):
        validate_course_plan(data, request())


@pytest.mark.parametrize("field", ["brief", "learning_objective"])
def test_numbering_cannot_disguise_repeated_cases(field):
    data = plan()
    data["cases"][1][field] = "Case 2: " + data["cases"][0][field]
    with pytest.raises(ValueError, match="distinct"):
        validate_course_plan(data, request())


def test_plan_checkpoint_reuses_approved_structure_without_extra_call(tmp_path):
    client = Client([plan()])
    with generation_checkpoint(tmp_path, "course_one", request().model_dump()) as checkpoint:
        first = plan_course(client, request(), checkpoint=checkpoint)
    with generation_checkpoint(tmp_path, "course_one", request().model_dump()) as checkpoint:
        second = plan_course(client, request(), checkpoint=checkpoint)
    assert first == second and len(client.calls) == 1


def test_actual_artifacts_drive_coverage_and_pass_is_evidence_bound(tmp_path):
    rows = actual_rows(tmp_path)
    client = Client([coverage()])
    result = review_course(client, request(), plan(), [{"index": i, "scenario": row} for i, row in enumerate(rows, 1)])
    assert result["status"] == "pass"
    assert result["actual_cases"] == 2
    assert client.calls[0][1]["actual_cases"][1]["patient_dossier"]["recent_events"][0]["content"] == "My pet died last week."
    assert len(result["coverage"]) == 2


def test_missing_dossier_does_not_fake_success_or_call_reviewer(tmp_path):
    rows = actual_rows(tmp_path)
    rows[1]["case_bundle_path"] = str(tmp_path / "missing.json")
    client = Client([])
    result = review_course(client, request(), plan(), rows)
    assert result["status"] == "needs_review" and result["semantic_review"]["status"] == "not_run"
    assert client.calls == []


def test_identical_patient_facts_are_duplicates_even_with_new_ids_names(tmp_path):
    rows = actual_rows(tmp_path)
    first = json.loads((tmp_path / "case-1.json").read_text())
    first["patient_dossier"]["display_name"] = "Different Name"
    first["patient_dossier"]["recent_events"][0]["event_id"] = "new-id"
    (tmp_path / "case-2.json").write_text(json.dumps(first))
    semantic = coverage()
    semantic["case_reviews"][1]["evidence"] = ["My manager criticised me yesterday."]
    result = review_course(Client([semantic]), request(), plan(), rows)
    assert result["status"] == "needs_review"
    assert any(item["check"] == "duplicate_patient_facts" for item in result["deterministic_checks"]["issues"])


def test_actual_risk_drift_blocks_batch_pass_even_if_model_approves(tmp_path):
    rows = actual_rows(tmp_path)
    rows[1]["risk_band"] = "high"
    result = review_course(Client([coverage()]), request(), plan(), rows)
    assert result["status"] == "needs_review"
    assert any(item["check"] == "request_constraint" for item in result["deterministic_checks"]["issues"])


def test_review_unavailability_stays_retryable_and_does_not_cache_pass(tmp_path):
    rows = actual_rows(tmp_path)
    client = Client([RuntimeError("fixture unavailable"), coverage()])
    with generation_checkpoint(tmp_path, "course_retry", request().model_dump()) as checkpoint:
        failed = review_course(client, request(), plan(), rows, checkpoint=checkpoint)
        assert checkpoint.trace[-1]["status"] == "failed"
    with generation_checkpoint(tmp_path, "course_retry", request().model_dump()) as checkpoint:
        retried = review_course(client, request(), plan(), rows, checkpoint=checkpoint)
    assert failed["status"] == "needs_review" and retried["status"] == "pass"
    assert len(client.calls) == 2


@pytest.mark.parametrize("evidence", [["A statement only in the plan"], ["Reflect emotions"]])
def test_fabricated_or_goal_only_evidence_cannot_claim_coverage(tmp_path, monkeypatch, evidence):
    rows = actual_rows(tmp_path)
    semantic = coverage()
    semantic["case_reviews"][0]["evidence"] = evidence
    # Skip JSON formatting retries to test contract validation itself.
    monkeypatch.setattr("domains.theraskill.theraskill_simulation.course_generation.chat_json_with_repair", lambda *args, **kwargs: semantic)
    result = review_course(Client([]), request(), plan(), rows)
    assert result["status"] == "needs_review" and result["semantic_review"]["status"] == "unavailable"


def test_batch_review_cannot_hide_a_missing_objective(tmp_path, monkeypatch):
    rows = actual_rows(tmp_path)
    semantic = coverage()
    semantic["objective_coverage"] = semantic["objective_coverage"][:1]
    monkeypatch.setattr("domains.theraskill.theraskill_simulation.course_generation.chat_json_with_repair", lambda *args, **kwargs: semantic)
    result = review_course(Client([]), request(), plan(), rows)
    assert result["status"] == "needs_review"
    assert "every planned course objective" in result["semantic_review"]["error"]


def test_changed_actual_dossier_invalidates_coverage_checkpoint(tmp_path):
    rows = actual_rows(tmp_path)
    client = Client([coverage(), coverage()])
    with generation_checkpoint(tmp_path, "course_edit", request().model_dump()) as checkpoint:
        first = review_course(client, request(), plan(), rows, checkpoint=checkpoint)
    path = tmp_path / "case-2.json"
    bundle = json.loads(path.read_text())
    bundle["patient_dossier"]["current_emotions"] = ["loneliness"]
    path.write_text(json.dumps(bundle))
    with generation_checkpoint(tmp_path, "course_edit", request().model_dump()) as checkpoint:
        second = review_course(client, request(), plan(), rows, checkpoint=checkpoint)
    assert first["status"] == second["status"] == "pass" and len(client.calls) == 2
    assert first["actual_artifact_sha256"] != second["actual_artifact_sha256"]
