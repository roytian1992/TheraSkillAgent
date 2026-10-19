from __future__ import annotations

from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from domains.theraskill.services.extraction_api.app import ExtractionAPISettings, ExtractionJobStore, create_app
from domains.theraskill.services.extraction_api import app as extraction_api
from plugins.theraskill.extraction_plan import validate_plan
from theraskill_agent.source_adapters import inspect_source


@pytest.fixture
def trial(tmp_path, monkeypatch):
    # These tests exercise API binding/persistence without running a model.
    monkeypatch.setattr(ExtractionJobStore, "_run", lambda *args: None)
    monkeypatch.setattr(ExtractionJobStore, "_model_settings", lambda self: {"model": "synthetic-test-model"})
    source = tmp_path / "source.jsonl"
    source.write_text('{"text":"grounded quote"}\n')
    plan = validate_plan({"goal": "Extract supported observations", "source": {"path": str(source)},
        "target_schema": {"type": "object", "properties": {"evidence": {"type": "string"}}, "required": ["evidence"]}})
    app = create_app(ExtractionAPISettings(work_root=tmp_path / "jobs", contribution_database_path=tmp_path / "contributions.sqlite"))
    store = app.state.extraction_store
    sample = store.create(plan, 3, "workbench")
    units = tmp_path / "units.jsonl"
    units.write_text(json.dumps({"content": {"evidence": "grounded quote"}, "qa": {"status": "pass"}}) + "\n")
    store._update(sample["job_id"], status="completed", counts={"units": 1, "failures": 0},
                  artifacts={"units": str(units)}, source_info={"fingerprint": inspect_source(source).fingerprint},
                  execution_binding={"model_config_sha256": extraction_api._model_fingerprint({"model": "synthetic-test-model"})})
    with TestClient(app) as client:
        headers = {"X-Extraction-Role": "researcher", "X-Extraction-User": "workbench"}
        preview = client.get(f"/v1/extraction/jobs/{sample['job_id']}/preview", headers=headers).json()
        context = preview["trial_review_context"]
        yield client, headers, store, source, plan, sample, context


def accepted(context):
    return {"decision": "accepted", "sample_job_id": context["sample_job_id"], "review_token": context["review_token"], "note": "Reviewed all sample evidence."}


def test_full_run_records_acceptance_bound_to_sample_artifacts(trial):
    client, headers, store, source, plan, sample, context = trial
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": accepted(context)})
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["trial_review"]["sample_job_id"] == sample["job_id"]
    assert job["trial_review"]["source_fingerprint"] == inspect_source(source).fingerprint
    assert job["trial_review"]["confirmed_by"] == "workbench"
    persisted = json.loads(store._path(job["job_id"]).read_text())
    assert persisted["trial_review"] == job["trial_review"]


@pytest.mark.parametrize("field,value", [("goal", "A different extraction goal"), ("semantic_review", "always"), ("quality_rules", ["A different quality policy"]), ("language", "en")])
def test_acceptance_cannot_transfer_to_changed_execution_settings(trial, field, value):
    client, headers, _, _, plan, _, context = trial
    changed = deepcopy(plan)
    changed[field] = value
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": changed, "trial_review": accepted(context)})
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_acceptance_cannot_transfer_to_changed_schema_or_source_contents(trial):
    client, headers, _, source, plan, _, context = trial
    changed = deepcopy(plan)
    changed["target_schema"]["properties"]["evidence"]["minLength"] = 4
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": changed, "trial_review": accepted(context)})
    assert response.status_code == 409
    source.write_text('{"text":"different source content"}\n')
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": accepted(context)})
    assert response.status_code == 409
    assert "Source contents changed" in response.json()["detail"]


def test_unviewed_or_wrong_sample_receipt_is_rejected(trial):
    client, headers, _, _, plan, _, context = trial
    decision = accepted(context)
    decision["review_token"] = "not-the-preview-token"
    assert client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": decision}).status_code == 409


def test_changed_model_configuration_invalidates_sample_acceptance(trial, monkeypatch):
    client, headers, _, _, plan, _, context = trial
    monkeypatch.setattr(ExtractionJobStore, "_model_settings", lambda self: {"model": "different-test-model"})
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": accepted(context)})
    assert response.status_code == 409
    assert "Model configuration changed" in response.json()["detail"]


def test_explicit_skip_requires_reason_and_is_persisted(trial):
    client, headers, _, _, plan, _, _ = trial
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": {"decision": "skipped", "note": "  "}})
    assert response.status_code == 409
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": {"decision": "skipped", "note": "A manually reviewed fixture set."}})
    assert response.status_code == 202
    assert response.json()["trial_review"]["decision"] == "skipped"
    assert response.json()["trial_review"]["note"] == "A manually reviewed fixture set."


def test_partial_sample_or_changed_sample_artifact_cannot_be_accepted(trial):
    client, headers, store, _, plan, sample, context = trial
    store._update(sample["job_id"], status="partial")
    assert client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": accepted(context)}).status_code == 409
    store._update(sample["job_id"], status="completed")
    from pathlib import Path
    Path(store.get(sample["job_id"])["artifacts"]["units"]).write_text(json.dumps({"qa": {"status": "pass"}, "content": {"evidence": "changed quote"}}) + "\n")
    assert client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "trial_review": accepted(context)}).status_code == 409


def test_idempotent_submission_must_preserve_review_decision(trial):
    client, headers, _, _, plan, _, context = trial
    body = {"plan": plan, "trial_review": accepted(context), "request_id": "full-run-once"}
    first = client.post("/v1/extraction/jobs", headers=headers, json=body)
    second = client.post("/v1/extraction/jobs", headers=headers, json=body)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    body["trial_review"] = {"decision": "skipped", "note": "Different decision"}
    assert client.post("/v1/extraction/jobs", headers=headers, json=body).status_code == 409


def test_existing_taskengine_call_without_ui_metadata_stays_supported(trial):
    client, headers, _, _, plan, _, _ = trial
    response = client.post("/v1/extraction/jobs", headers=headers, json={"plan": plan, "request_id": "task-owned-gates"})
    assert response.status_code == 202 and response.json()["trial_review"] is None
