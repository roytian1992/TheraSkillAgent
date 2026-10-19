from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from domains.theraskill.services.simulation_api.app import create_app, SimulationAPISettings
from domains.theraskill.theraskill_simulation.contracts import ActorRole, PrivatePatientState, SessionMode
from domains.theraskill.theraskill_simulation.engines import AgentTurnResult, DeferredLLMPatientEngine
from domains.theraskill.theraskill_simulation.patient_builder import PatientDossier, build_patient_scenario, dossier_from_bundle
from domains.theraskill.theraskill_simulation.persona_compiler import PersonaCompiler, runtime_bundle_with_context
from domains.theraskill.theraskill_simulation.scenario_generation import PatientBuildRequest, LLMScenarioGenerator, ScenarioGenerationRequest

ROOT = Path(__file__).resolve().parents[1]
AUTHOR = {"X-Simulation-Role": "researcher"}
LEARNER = {"X-Simulation-Role": "human_therapist"}


def patient():
    return PatientDossier(display_name="Alex", age="29", occupation="Designer",
        presenting_concern="Work feedback has made it hard to switch off.",
        background=[{"content": "Started this job six months ago.", "sensitivity": "surface"}],
        recent_events=[
            {"event_id": "public_event", "time_label": "Monday", "content": "Attended a team meeting.", "impact": "Felt tired.", "sensitivity": "surface"},
            {"event_id": "private_event", "time_label": "Last Friday", "content": "Received a confidential project reassignment.", "impact": "Has been avoiding a colleague.", "sensitivity": "moderate"},
        ], hidden_concerns=[{"content": "Fears disappointing a sibling.", "sensitivity": "sensitive"}],
        goals=["Discuss reactions to feedback"], training_objectives=["Practice validation"], current_emotions=["worried"])


def request():
    return PatientBuildRequest(brief="Explore work stress through supportive questions.", language="en", patient=patient())


def app_for(tmp_path, engine=None):
    registry = tmp_path / "scenarios.jsonl"
    registry.touch()
    return create_app(SimulationAPISettings(database_path=tmp_path / "sessions.sqlite", scenario_registry_path=registry,
        generated_scenario_registry_path=tmp_path / "generated.jsonl", generated_scenario_root=tmp_path / "cases",
        generated_batch_work_root=tmp_path / "jobs", initialize_llm_engine=False), patient_engine=engine)


def test_recent_experiences_are_preserved_but_disclosure_is_bounded(tmp_path):
    built = build_patient_scenario(request(), tmp_path)
    bundle = built["case_bundle"]
    persona = bundle["_patient_persona_v2"]
    compiler = PersonaCompiler()
    public = compiler.compile_for_move(persona)
    public_text = json.dumps(runtime_bundle_with_context(bundle, public))
    assert "Attended a team meeting" in public_text
    assert "confidential project reassignment" not in public_text
    assert "avoiding a colleague" not in public_text
    assert "sibling" not in public_text
    assert bundle["patient_dossier"]["recent_events"][1]["event_id"] == "private_event"
    supportive = {"validation_signal": "present", "challenge_level": "low"}
    moderate = compiler.compile_for_turn(persona, {"disclosure_state": "moderate"}, public.allowed_fact_ids, supportive)
    text = json.dumps(moderate.runtime_context)
    assert "Last Friday" in text and "avoiding a colleague" in text
    assert "sibling" not in text
    deep = compiler.compile_for_turn(persona, {"disclosure_state": "deep"}, public.allowed_fact_ids, supportive)
    assert "sibling" in json.dumps(deep.runtime_context)
    pressure = compiler.compile_for_turn(persona, {"disclosure_state": "deep"}, public.allowed_fact_ids,
        {"challenge_level": "high", "validation_signal": "absent"})
    assert "sibling" not in json.dumps(pressure.runtime_context)


def test_event_ids_are_unique_and_empty_concerns_cannot_build(tmp_path):
    assert PatientDossier(age=28).age == "28"
    data = patient().model_dump()
    data["recent_events"][1]["event_id"] = "public_event"
    with pytest.raises(ValidationError, match="unique"):
        PatientDossier.model_validate(data)
    with pytest.raises(ValueError, match="concern"):
        build_patient_scenario(PatientBuildRequest(brief="A patient without a defined concern", patient=PatientDossier()), tmp_path)


def test_copying_resource_persona_retains_private_fact_sensitivity(tmp_path):
    original = build_patient_scenario(request(), tmp_path)["case_bundle"]
    # A released case may carry facts only in its external Persona v2, rather
    # than the new editor's dossier. Converting it must not make them public.
    original.pop("patient_dossier")
    original["authoritative_layer"] = {}
    converted = dossier_from_bundle(original, {"scenario_brief": "Supportive exploration of work stress."})
    assert converted.display_name == "Alex"
    assert any("Fears disappointing" in fact.content and fact.sensitivity == "sensitive" for fact in converted.hidden_concerns)
    assert not any("Fears disappointing" in fact.content for fact in converted.background)


class RecordingPatient:
    def __init__(self):
        self.bundles = []

    def generate_turn(self, **kwargs):
        self.bundles.append(kwargs["case_bundle"])
        return AgentTurnResult(text="I have been thinking about the meeting.", patient_state=kwargs["private_state"])


def test_editing_versions_reuse_and_existing_session_isolation(tmp_path):
    engine = RecordingPatient()
    app = app_for(tmp_path, engine)
    service = app.state.session_service
    with TestClient(app) as client:
        created = client.post("/scenarios/build-patient", headers=AUTHOR, json=request().model_dump(mode="json"))
        assert created.status_code == 201, created.text
        row = created.json()["scenario"]
        sid = row["scenario_id"]
        assert client.get(f"/scenarios/{sid}/patient", headers=LEARNER).status_code == 403
        session = service.create_session(scenario_id=sid, language="en")
        assert session.private_patient_state.persona_id
        assert session.public_view.scenario.learner_role_card["display_name"] == "Alex"
        assert "sibling" not in session.public_view.model_dump_json()
        original_path = Path(session.session.metadata["case_bundle_path"])
        original_bytes = original_path.read_bytes()
        approved = client.post(f"/scenarios/generated/{sid}/review", headers=AUTHOR, json={"action": "approve"})
        assert approved.status_code == 200
        profile = patient().model_dump(mode="json")
        profile["recent_events"][1]["content"] = "A new event in the next case version."
        updated = client.patch(f"/scenarios/generated/{sid}", headers=AUTHOR, json={"patient": profile, "expected_revision": 1})
        assert updated.status_code == 200, updated.text
        assert updated.json()["scenario"]["revision"] == 2
        assert updated.json()["scenario"]["review_status"] == "draft"
        assert original_path.read_bytes() == original_bytes
        assert client.patch(f"/scenarios/generated/{sid}", headers=AUTHOR, json={"patient": profile, "expected_revision": 1}).status_code == 409
        persisted = client.get(f"/scenarios/{sid}/patient", headers=AUTHOR).json()
        assert persisted["revision"] == 2 and persisted["patient"]["recent_events"][1]["event_id"] == "private_event"
        service.submit_turn(session.session.session_id, text="What has the past week been like?", actor=ActorRole.HUMAN_THERAPIST, idempotency_key="turn")
        assert engine.bundles[-1]["patient_dossier"]["recent_events"][1]["content"] == "Received a confidential project reassignment."
        newer = service.create_session(scenario_id=sid, language="en")
        service.submit_turn(newer.session.session_id, text="What happened recently?", actor=ActorRole.HUMAN_THERAPIST, idempotency_key="newturn")
        assert engine.bundles[-1]["patient_dossier"]["recent_events"][1]["content"].startswith("A new event")
        service.complete_session(session.session.session_id)
        review = service.create_session(scenario_id=sid, mode=SessionMode.REVIEW_ONLY, source_session_id=session.session.session_id, language="en")
        assert review.session.metadata["case_bundle_path"] == str(original_path)
        assert review.session.metadata["patient_revision"] == 1
        followup = service.create_session(scenario_id=sid, parent_session_id=session.session.session_id, language="en")
        assert followup.session.metadata["case_bundle_path"] == str(original_path)
        copy = client.post(f"/scenarios/{sid}/fork", headers=AUTHOR, json={})
        assert copy.status_code == 201, copy.text
        copyid = copy.json()["scenario"]["scenario_id"]
        assert copyid != sid and copy.json()["scenario"]["parent_scenario_id"] == sid
        assert client.get(f"/scenarios/{copyid}/patient", headers=AUTHOR).json()["patient"] == profile
        # Saved cases survive a new service instance, without a model or assets.
        restarted = app_for(tmp_path)
        with TestClient(restarted) as other:
            assert other.get(f"/scenarios/{sid}/patient", headers=AUTHOR).json()["revision"] == 2
        reveal = service.export_session(session.session.session_id, scope="researcher")
        assert "Fears disappointing a sibling." in json.dumps(reveal["instructor_persona_reveal"])


def test_fast_runtime_uses_current_move_filters_ids_and_schedules_state_updates(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/Simulation"))
    import run_patient_single_session_loop as runtime_module
    built = build_patient_scenario(request(), tmp_path)
    bundle = built["case_bundle"]
    persona = bundle["_patient_persona_v2"]
    compiler = PersonaCompiler()
    public = compiler.compile_for_move(persona)
    private = PrivatePatientState(runtime_state={"disclosure_state": "moderate"}, disclosed_fact_ids=public.allowed_fact_ids)
    calls = []
    def step(client, prompt, payload, **kwargs):
        calls.append((prompt, payload))
        if prompt == "move":
            return {"therapist_move_interpretation": {"validation_signal": "present", "challenge_level": "low"}}, {}
        return {"patient_utterance": "Last Friday's changes stayed on my mind.", "used_fact_ids": ["invented", public.allowed_fact_ids[0]], "patient_turn_plan": {}}, {}
    monkeypatch.setattr(runtime_module, "run_json_step", step)
    engine = DeferredLLMPatientEngine.__new__(DeferredLLMPatientEngine)
    engine.persona_compiler = compiler
    engine.fast_turn_prompts = {"en": "fast"}
    result = engine._generate_fast_turn(scenario=built["scenario"], case_bundle=bundle, transcript=[],
        private_state=private, therapist_utterance="That sounds difficult. What happened?", language="en",
        runtime={"client": object(), "prompts": {"move": "move"}, "parse_retries": 0, "max_prompt_tokens": 12000})
    assert len(calls) == 2
    assert "confidential project reassignment" not in json.dumps(calls[0])
    assert "confidential project reassignment" in json.dumps(calls[1])
    assert "Fears disappointing" not in json.dumps(calls)
    assert "invented" not in result.patient_state.disclosed_fact_ids
    assert result.runtime_trace["response_contract"]["uncertainty_or_hallucination_flags"]
    assert result.runtime_trace["deferred_update_id"]
    assert result.runtime_trace["deferred_update"]["therapist_move_interpretation"]["therapist_move_interpretation"]["validation_signal"] == "present"


def test_generation_passes_grounding_and_preserves_user_experiences(tmp_path, monkeypatch):
    from domains.theraskill import theraskill_llm
    seen = {}
    def model(client, prompt, payload, **kwargs):
        seen.update(payload)
        return {"scenario_brief": "Supportive exploration of a difficult work week.", "patient": patient().model_dump(mode="json")}
    monkeypatch.setattr(theraskill_llm, "chat_json_with_repair", model)
    generator = LLMScenarioGenerator.__new__(LLMScenarioGenerator)
    generator.client = object(); generator.prompts = {"zh": "prompt", "en": "prompt"}; generator.output_root = tmp_path
    generator._knowledge_context = lambda req: {"status": "grounded", "items": [{"retrieval_id": "unit::actual", "content": "Use gradual disclosure"}]}
    seed = PatientDossier(recent_events=[{"event_id": "supplied", "time_label": "Yesterday", "content": "Walked in a nearby park.", "sensitivity": "surface"}])
    generated = generator.generate(ScenarioGenerationRequest(brief="Explore recent work stress and coping.", language="en", patient=seed))
    assert seen["knowledge_context"]["items"][0]["retrieval_id"] == "unit::actual"
    bundle = generated["case_bundle"]
    assert bundle["patient_dossier"]["recent_events"][0]["event_id"] == "supplied"
    assert len(bundle["patient_dossier"]["recent_events"]) == 1
    assert bundle["authoritative_layer"]["source_unit_ids"] == {"0": "unit::actual"}
    assert bundle["_patient_persona_v2"] and Path(generated["scenario"]["case_bundle_path"]).exists()


def test_patient_skill_is_ready_and_routes_to_same_api(monkeypatch):
    from theraskill_agent.skill_readiness import check_skill
    from theraskill_agent.skill_execution import execute_tool
    from theraskill_agent import task_runtime
    assert check_skill("simulated-patient-builder")["ready"]
    calls = []
    monkeypatch.setattr(task_runtime, "domain_call", lambda *args, **kwargs: calls.append((args, kwargs)) or {"saved": True})
    assert execute_tool("theraskill_patient", {"operation": "build", "parameters": request().model_dump(mode="json")})["saved"]
    assert calls[0][0][:2] == ("simulation", "/scenarios/build-patient")
    with pytest.raises(ValueError, match="expected_revision"):
        execute_tool("theraskill_patient", {"operation": "update", "scenario_id": "test", "parameters": {"patient": {}}})


def test_skill_can_cite_saved_case_without_accepting_fabricated_ids():
    from theraskill_agent.skill_execution import finish_errors
    history = [{"tool": "theraskill_patient", "result": {"scenario": {"scenario_id": "saved-scenario", "case_id": "saved-case"}}}]
    assert not finish_errors({"action": "finish", "result": {"answer": "Saved the synthetic draft.", "evidence_ids": ["saved-scenario"]}}, history)
    assert finish_errors({"action": "finish", "result": {"answer": "Saved.", "evidence_ids": ["invented-scenario"]}}, history)


def test_same_theme_with_changed_experiences_can_be_approved(tmp_path):
    app = app_for(tmp_path)
    with TestClient(app) as client:
        created = client.post("/scenarios/build-patient", headers=AUTHOR, json=request().model_dump(mode="json")).json()["scenario"]
        source = created["scenario_id"]
        copied = client.post(f"/scenarios/{source}/fork", headers=AUTHOR, json={}).json()["scenario"]
        target = copied["scenario_id"]
        assert client.post(f"/scenarios/generated/{target}/review", headers=AUTHOR, json={"action": "approve"}).status_code == 422
        profile = patient().model_dump(mode="json")
        profile["recent_events"][0]["content"] = "Presented a new design to the team."
        client.patch(f"/scenarios/generated/{target}", headers=AUTHOR, json={"patient": profile, "expected_revision": 1}).raise_for_status()
        approved = client.post(f"/scenarios/generated/{target}/review", headers=AUTHOR, json={"action": "approve"})
        assert approved.status_code == 200, approved.text
        listed = client.get("/scenarios?language=en&split_key=pilot_split&split_value=pilot_train", headers=LEARNER).json()["scenarios"]
        assert target in {row["scenario_id"] for row in listed}


def test_dual_cycles_complete_patient_state_and_therapist_memory(tmp_path):
    class Patient(RecordingPatient):
        def generate_turn(self, **kwargs):
            return AgentTurnResult(text="I am still thinking about the feedback.", patient_state=kwargs["private_state"],
                                   runtime_trace={"deferred_update": {"pending": True}})
        def finalize_turn(self, **kwargs):
            return AgentTurnResult(text="", patient_state=kwargs["private_state"].model_copy(update={"runtime_state": {"alliance_state": "working"}}))
    class Therapist:
        def generate_turn(self, **kwargs):
            return AgentTurnResult(text="Would you like to tell me more?", therapist_state={}, runtime_trace={"deferred_update": {"pending": True}})
        def finalize_turn(self, *, private_state, deferred_update, language):
            data = private_state.model_dump(mode="json")
            data["case_formulation_memory"] = {"saved": True}
            return AgentTurnResult(text="", therapist_state=data)
    app = app_for(tmp_path, Patient())
    service = app.state.session_service
    service.therapist_engine = Therapist()
    with TestClient(app) as client:
        sid = client.post("/scenarios/build-patient", headers=AUTHOR, json=request().model_dump(mode="json")).json()["scenario"]["scenario_id"]
        session = service.create_session(scenario_id=sid, mode=SessionMode.DUAL_AGENT_RESEARCH, language="en")
        service.run_dual_cycle(session.session.session_id, initial_therapist_utterance="What has this week been like?", idempotency_key="cycle")
        completed = service.get_snapshot(session.session.session_id)
        assert completed.private_patient_state.runtime_state["alliance_state"] == "working"
        assert completed.private_therapist_state.case_formulation_memory["saved"] is True
