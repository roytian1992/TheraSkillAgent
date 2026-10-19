"""Regression cases for source coverage, retry checkpoints and evidence grounding."""
import json
from types import SimpleNamespace

import pytest

from domains.theraskill.theraskill_simulation.session_analysis import (
    SUMMARY_FIELDS, GATE_FIELDS, build_session_payload, run_session_analysis, validate_citations,
)
from domains.theraskill.theraskill_simulation.training import LLMResponseComparisonEngine
from domains.theraskill.theraskill_support.engine import LLMSupportEngine
from domains.theraskill.theraskill_support.contracts import SupportSessionRecord, SupportPublicSession, SupportMessage
from domains.theraskill.theraskill_llm import estimate_prompt_tokens, prompt_budget_report


class AnalysisClient:
    model = "test-local-model"
    base_url = "test://offline"

    def __init__(self, mode):
        self.mode, self.calls = mode, []
        self.fail_at = None

    def chat(self, prompt, payload, **kwargs):
        self.calls.append((prompt, payload))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("test interruption")
        row = next(row for row in payload["transcript"] if row["text"])
        evidence = {"turn_id": row["turn_id"], "quote": row["text"][:25]}
        if self.mode == "summary":
            return json.dumps({"session_longitudinal_summary": {key: ([row["text"][:25]] if key == "key_turning_points" else []) for key in SUMMARY_FIELDS}, "source_evidence": [evidence]})
        important = any("EARLY_SIGNAL" in row["text"] for row in payload["transcript"])
        finding = [{"severity": "fail", "category": "early_signal", "message": "Preserved early finding", "evidence": [evidence]}] if important else []
        return json.dumps({"review_gate": {"gate_status": "pass", **{key: "pass" for key in GATE_FIELDS}}, "findings": finding,
                           "recommended_actions": [], "source_evidence": [evidence]})


def long_session():
    return {"transcript": [{"turn_id": f"t{i}", "role": "patient" if i % 2 else "therapist",
        "text": ("EARLY_SIGNAL " if i == 0 else "") + (f"内容 {i} more details. " * 40) + f" END_{i}"} for i in range(18)]}


def test_full_session_segmentation_preserves_all_characters_and_early_findings(tmp_path):
    session = long_session()
    client = AnalysisClient("review")
    result, audit = run_session_analysis(client, "Review", build_session_payload(session, summary={}),
        mode="review", max_prompt_tokens=1100, parse_retries=0, checkpoint_dir=tmp_path)
    assert audit["segment_count"] > 1
    rebuilt = {row["turn_id"]: "" for row in session["transcript"]}
    for prompt, payload in client.calls:
        assert not prompt_budget_report(prompt, payload, 1100)["over_budget"]
        for row in payload["transcript"]:
            rebuilt[row["turn_id"]] += row["text"]
    assert rebuilt == {row["turn_id"]: row["text"] for row in session["transcript"]}
    assert result["review_gate"]["gate_status"] == "fail"
    assert result["findings"][0]["evidence"][0]["turn_id"] == "t0"
    assert result["evidence_coverage"]["source_character_count"] == result["evidence_coverage"]["covered_character_count"]


def test_segment_retry_reuses_only_successful_matching_checkpoints(tmp_path):
    client = AnalysisClient("summary")
    client.fail_at = 2
    payload = build_session_payload(long_session())
    with pytest.raises(RuntimeError, match="interruption"):
        run_session_analysis(client, "Summarize", payload, mode="summary", max_prompt_tokens=1100,
                             parse_retries=0, checkpoint_dir=tmp_path)
    client.fail_at = None
    result, audit = run_session_analysis(client, "Summarize", payload, mode="summary", max_prompt_tokens=1100,
                                         parse_retries=0, checkpoint_dir=tmp_path)
    assert audit["checkpoint_reused_count"] == 1
    assert result["evidence_coverage"]["source_turn_count"] == 18
    _, repeated = run_session_analysis(client, "Summarize", payload, mode="summary", max_prompt_tokens=1100,
                                       parse_retries=0, checkpoint_dir=tmp_path)
    assert repeated["checkpoint_reused_count"] == repeated["segment_count"]


@pytest.mark.parametrize("citation", [{"turn_id": "missing", "quote": "actual"}, {"turn_id": "t1", "quote": "invented"}])
def test_evidence_validation_rejects_fabricated_turns_and_quotes(citation):
    with pytest.raises(ValueError, match="does not match"):
        validate_citations({"source_evidence": [citation]}, [{"turn_id": "t1", "text": "actual"}], "summary")


def support_record(count):
    return SupportSessionRecord(public=SupportPublicSession(session_id="test", messages=[
        SupportMessage(message_id=f"m{i}", sequence=i + 1, role="user" if i % 2 == 0 else "assistant",
                       text="ORIGINAL_GOAL" if i == 0 else f"message {i}") for i in range(count)]),
        knowledge_release_id="test")


def support_engine():
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.max_runtime_prompt_tokens = 12000
    engine.estimate_prompt_tokens = estimate_prompt_tokens
    engine.compaction_prompt = "Keep earlier facts and distinguish new messages."
    calls = []
    def chat(prompt, payload, **kwargs):
        calls.append(payload)
        return "ORIGINAL_GOAL retained; " + "; ".join(row["text"] for row in payload["conversation_to_compact"])[-100:]
    engine.client = SimpleNamespace(chat=chat)
    return engine, calls


def test_short_turns_leave_raw_window_only_after_incremental_summary():
    engine, calls = support_engine()
    record = support_record(15)
    summary, recent, latest, trace, _ = engine._compact_history(record, record.public.messages[-1].text)
    assert "ORIGINAL_GOAL" in summary
    assert trace["summary_through_message"] == 4
    assert [item["text"] for item in calls[0]["conversation_to_compact"]] == [item.text for item in record.public.messages[:4]]
    assert [item["text"] for item in recent] == [item.text for item in record.public.messages[4:-1]]
    assert len(calls) == 1
    next_record = support_record(17)
    next_record.conversation_summary = summary
    next_record.summary_through_message = trace["summary_through_message"]
    _, _, _, next_trace, _ = engine._compact_history(next_record, next_record.public.messages[-1].text)
    assert next_trace["newly_summarized_messages"] == 2
    assert [row["message_position"] for row in calls[1]["conversation_to_compact"]] == [5, 6]
    assert calls[1]["previous_summary"] == summary
    # Restart serialization keeps the raw-message checkpoint with its summary.
    restored = SupportSessionRecord.model_validate_json(next_record.model_dump_json())
    assert restored.summary_through_message == 4


def test_long_latest_message_splits_without_loss_and_bounds_each_prompt():
    engine, calls = support_engine()
    engine.max_runtime_prompt_tokens = 1500
    record = support_record(1)
    record.public.messages[0].text = "long pasted text " * 4000
    _, _, latest, trace, _ = engine._compact_history(record, record.public.messages[0].text)
    assert trace["summary_through_message"] == 1
    assert "conversation_summary" in latest
    fragments = [row["text"] for payload in calls for row in payload["conversation_to_compact"]]
    assert "".join(fragments) == record.public.messages[0].text
    assert all(not prompt_budget_report(engine.compaction_prompt, payload, 1500)["over_budget"] for payload in calls)


def comparison_engine(citations):
    engine = LLMResponseComparisonEngine.__new__(LLMResponseComparisonEngine)
    engine.model_name, engine.prompts = "test", {"en": "Compare", "zh": "Compare"}
    received = []
    def chat(prompt, payload, **kwargs):
        received.append(payload)
        return json.dumps({"candidates": [{"framework": "integrative", "strategy": f"strategy {i}",
            "response": f"response {i}", "rationale": "Uses the supplied reference", "applicable_context": "Here",
            "limitations": "Transcript-based draft", "evidence": citations} for i in range(3)]})
    engine.client = SimpleNamespace(chat=chat)
    engine._retrieve_evidence = lambda query: ([{"unit_id": "real-unit", "text": "Reflect the stated emotion before exploring."}], {"status": "available", "index_id": "actual-index"})
    return engine, received


def test_comparison_supplies_real_source_text_and_drops_old_decorative_ids():
    engine, received = comparison_engine([{"unit_id": "real-unit", "quote": "Reflect the stated emotion"}])
    result = engine.generate(session_id="s", target_turn_id="t", scenario={}, transcript=[{"text": "I am worried"}],
        knowledge_release_id="session-release", evidence_unit_ids=["unseen-old-id"], language="en")
    assert received[0]["retrieved_evidence"][0]["text"].startswith("Reflect")
    assert result.evidence_unit_ids == ["real-unit"]
    assert result.candidates[0].evidence_unit_ids == ["real-unit"]
    assert result.retrieval_summary["index_id"] == "actual-index"
    assert "unseen-old-id" not in result.evidence_unit_ids


def test_comparison_rejects_citations_to_sources_not_supplied():
    engine, received = comparison_engine([{"unit_id": "unseen-old-id", "quote": "Reflect the stated emotion"}])
    with pytest.raises(Exception, match="actual supplied knowledge"):
        engine.generate(session_id="s", target_turn_id="t", scenario={}, transcript=[{"text": "worried"}],
            knowledge_release_id="session-release", evidence_unit_ids=["unseen-old-id"], language="en")
    assert len(received) == 2

from domains.theraskill.theraskill_support.artifact_workflows import ArtifactWorkflow, DIMENSIONS, _source_request


class ArtifactClient:
    model = "local-test"
    base_url = "test://offline"

    def __init__(self, reject=False, persistent=False):
        self.stages, self.reject, self.persistent = [], reject, persistent
        self.fail_stage = None

    def chat(self, prompt, payload, **kwargs):
        stage = prompt.split("stage: ")[1].split(".")[0]
        self.stages.append(stage)
        if self.fail_stage == stage:
            raise RuntimeError("simulated process interruption")
        if stage.startswith("evidence_"):
            value = {"observations": [{"evidence_id": "e1", "quote": payload["source_segment"][:20], "observation": "Observed concern"}]}
        elif stage.startswith("dimensions_"):
            value = {"dimensions": [{"dimension": key, "judgement": "concern", "reason": "Observed wording", "evidence_ids": ["e1"]} for key in DIMENSIONS]}
        elif stage.startswith("recommendations_"):
            value = {"response_text": "Grounded review", "recommendations": [{"dimension": "stage_fit", "action": "Clarify the stated concern", "evidence_ids": ["e1"]}]}
        elif stage == "teaching_plan":
            count = payload["required_turn_count"]
            value = {"turn_count": count, "learning_objectives": ["Reflect"], "framework": "CBT", "outline": ["Explore"] * count}
        elif stage in {"teaching_draft", "teaching_repair"}:
            value = {"dialogue": [{"turn_id": f"turn_{i + 1:04d}", "role": "patient" if i % 2 == 0 else "therapist", "text": "Draft" if stage == "teaching_draft" else "Repaired"} for i in range(payload["plan"]["turn_count"])],
                     "rationale": ["Teaching only"], "construction_metadata": {"source_type": "synthetic"}}
        else:
            reject = self.reject and (self.persistent or stage == "teaching_review")
            value = {"approved": not reject, "issues": ["Make the therapist response specific"] if reject else []}
        return json.dumps(value)


def artifact_runner(tmp_path, client, workflow="synthetic_construction", request=None):
    return ArtifactWorkflow(client, tmp_path, workflow=workflow,
        request=request or {"turn_count": 4, "goal": "Practice reflection", "framework": "CBT"},
        instruction="", context=[], skill={}, language="en", budget=12000)


def test_teaching_draft_requires_independent_review_and_rechecks_repairs(tmp_path):
    client = ArtifactClient(reject=True)
    result = artifact_runner(tmp_path, client).run()
    assert client.stages == ["teaching_plan", "teaching_draft", "teaching_review", "teaching_repair", "teaching_recheck"]
    assert result["structured_content"]["quality_review"]["approved"] is True
    assert all(turn["text"] == "Repaired" for turn in result["structured_content"]["dialogue"])
    assert result["structured_content"]["construction_metadata"]["source_type"] == "synthetic"
    assert "Synthetic teaching dialogue" in result["response_text"]


def test_teaching_rejection_after_repair_fails_job(tmp_path):
    client = ArtifactClient(reject=True, persistent=True)
    with pytest.raises(ValueError, match="still failed review"):
        artifact_runner(tmp_path, client).run()
    progress = json.loads(next(tmp_path.glob("*/workflow.json")).read_text())
    assert progress["status"] == "failed"
    assert progress["failed_stage"] == "teaching_recheck"


def test_expert_retry_reuses_successful_stage_outputs(tmp_path):
    client = ArtifactClient()
    client.fail_stage = "teaching_review"
    with pytest.raises(RuntimeError, match="interruption"):
        artifact_runner(tmp_path, client).run()
    assert client.stages == ["teaching_plan", "teaching_draft", "teaching_review"]
    client.fail_stage = None
    result = artifact_runner(tmp_path, client).run()
    assert client.stages == ["teaching_plan", "teaching_draft", "teaching_review", "teaching_review"]
    assert [stage["checkpoint_reused"] for stage in result["structured_content"]["generation_workflow"]["stages"]] == [True, True, False]


def _cached_workflow_fixture(tmp_path, kind):
    concrete = ArtifactClient() if kind == "artifact" else AnalysisClient("summary")
    # The wrapper's visible endpoint and inference settings stay unchanged.
    # Cache identity must follow the concrete provider nested underneath it.
    client = SimpleNamespace(model="outer-model", base_url="agent://active-provider",
                             _hosted_client=SimpleNamespace(_client=concrete), chat=concrete.chat)
    def run(budget=12000):
        if kind == "artifact":
            return ArtifactWorkflow(client, tmp_path, workflow="synthetic_construction", request={"turn_count": 4},
                                    instruction="", context=[], skill={}, language="en", budget=budget).run()
        return run_session_analysis(client, "Summarize", build_session_payload({"transcript": [{"text": "A short source observation."}]}),
                                    mode="summary", max_prompt_tokens=budget, checkpoint_dir=tmp_path)
    def count():
        return len(concrete.stages if kind == "artifact" else concrete.calls)
    return concrete, client, run, count


@pytest.mark.parametrize("kind", ["artifact", "session"])
@pytest.mark.parametrize("field,value", [
    ("temperature", .7), ("reasoning_effort", "high"), ("thinking", {"type": "disabled"}),
    ("max_output_tokens", 2048), ("response_format_json", True),
    ("api_key", "fixture-private-credential"), ("base_url", "https://different-fixture.invalid/v1"),
])
def test_workflow_cache_tracks_actual_wrapped_provider_settings(tmp_path, kind, field, value):
    concrete, client, run, count = _cached_workflow_fixture(tmp_path, kind)
    run()
    original_count = count()
    run()
    assert count() == original_count
    setattr(concrete, field, value)
    run()
    assert count() == original_count * 2
    run()
    assert count() == original_count * 2
    assert client.base_url == "agent://active-provider"
    assert all("fixture-private-credential" not in path.read_text() for path in tmp_path.rglob("*.json"))


@pytest.mark.parametrize("kind", ["artifact", "session"])
def test_workflow_cache_invalidates_for_budget_and_implementation(tmp_path, monkeypatch, kind):
    concrete, client, run, count = _cached_workflow_fixture(tmp_path, kind)
    run()
    initial = count()
    run(11000)
    assert count() == initial * 2  # This short source has identical segments at both budgets.
    module = ("domains.theraskill.theraskill_support.artifact_workflows" if kind == "artifact"
              else "domains.theraskill.theraskill_simulation.session_analysis")
    monkeypatch.setattr(module + "._implementation_signature", lambda: {"fixture": "updated implementation"})
    run(11000)
    assert count() == initial * 3
    run(11000)
    assert count() == initial * 3


@pytest.mark.parametrize("kind", ["artifact", "session"])
def test_identical_concurrent_workflows_cannot_overwrite_shared_checkpoints(tmp_path, kind):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    concrete, client, run, count = _cached_workflow_fixture(tmp_path, kind)
    entered, release = threading.Event(), threading.Event()
    chat = concrete.chat
    def blocked_chat(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(3), "Test did not release the model fixture"
        return chat(*args, **kwargs)
    client.chat = blocked_chat
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        try:
            assert entered.wait(3)
            with pytest.raises(ValueError, match="another operation"):
                run()
            if kind == "artifact":
                assert json.loads(next(tmp_path.glob("*/workflow.json")).read_text())["status"] == "running"
        finally:
            release.set()
        future.result(timeout=3)
    completed = count()
    run()
    assert count() == completed
    assert list(tmp_path.rglob("*.tmp")) == []


def test_expert_response_review_separates_evidence_dimensions_and_recommendations(tmp_path):
    client = ArtifactClient()
    source = "Patient: I am worried. Therapist: Tell me more about that worry."
    result = artifact_runner(tmp_path, client, "response_review", {"transcript_or_response": source}).run()
    assert client.stages == ["evidence_1", "dimensions_1", "recommendations_1", "review_check_1"]
    report = result["structured_content"]["review_segments"][0]
    assert report["observations"][0]["quote"] in source
    assert {row["dimension"] for row in report["dimensions"]} == set(DIMENSIONS)
    assert result["structured_content"]["source_coverage"]["complete"]


def test_expert_envelope_survives_transport_whitespace_and_keeps_regeneration_source():
    original = "Build teaching content. Specification: " + json.dumps({"goal": "Practice empathy", "turn_count": 8})
    record = SimpleNamespace(public=SimpleNamespace(messages=[SimpleNamespace(role="user", text=original), SimpleNamespace(role="user", text="Make the ending more tentative.")]))
    request, revision = _source_request(record, "Make the ending more tentative.")
    assert request["turn_count"] == 8
    assert revision == "Make the ending more tentative."


def test_existing_support_jobs_run_expert_stages_and_persist_output(tmp_path):
    from domains.theraskill.theraskill_support.repository import SQLiteSupportRepository
    from domains.theraskill.theraskill_support.service import SupportSessionService
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.client = ArtifactClient()
    engine.model_name, engine.work_root = "test", tmp_path / "work"
    engine.max_runtime_prompt_tokens, engine.memory_provider = 12000, None
    engine._retrieve = lambda *_: ({"items": []}, tmp_path / "retrieval.json", 0)
    engine._skill_context = lambda *_: {}
    repository = SQLiteSupportRepository(tmp_path / "support.sqlite")
    service = SupportSessionService(repository=repository, engine=engine, knowledge_release_id="test")
    session = service.create_session(owner_id="user", workflow="synthetic_construction", output_language="en")
    submitted = service.submit_message(session.session_id, owner_id="user", idempotency_key="expert-job",
        text="Build synthetic content. Specification:\n" + json.dumps({"turn_count": 4, "goal": "Reflect", "framework": "CBT"}))
    service.finalize_response(session.session_id, job_id=submitted.job_id)
    restored = SQLiteSupportRepository(tmp_path / "support.sqlite").get(session.session_id)
    assert restored.public.generation.status == "idle"
    artifact = restored.public.messages[-1].structured_content
    assert len(artifact["dialogue"]) == 4
    assert artifact["generation_workflow"]["status"] == "completed"
    assert restored.turn_traces[-1].history_compaction["method"] == "artifact_source_preserved"
    assert engine.client.stages == ["teaching_plan", "teaching_draft", "teaching_review"]


def test_failed_quality_retry_regenerates_repair_instead_of_reusing_rejected_artifact(tmp_path):
    client = ArtifactClient(reject=True, persistent=True)
    with pytest.raises(ValueError):
        artifact_runner(tmp_path, client).run()
    before = len(client.stages)
    client.persistent = False
    result = artifact_runner(tmp_path, client).run()
    assert client.stages[before:] == ["teaching_repair", "teaching_recheck"]
    assert result["structured_content"]["quality_review"]["approved"]


def test_comparison_retrieval_uses_review_policy_and_real_returned_source_text(tmp_path, monkeypatch):
    import domains.theraskill.hybrid_retrieval as retrieval
    import theraskill_agent.resources as resources
    calls = []
    class Retriever:
        def __init__(self, root, paths):
            pass
        def search(self, query, *, skill, top_k):
            calls.append((query, skill, top_k))
            return {"items": [{"release_unit_id": "live-source", "text_preview": "Supportive validation example",
                "canonical_label": "Validation", "allowed_use": "teaching", "forbidden_use": "diagnosis"}],
                "retrieval_mode": "hybrid_v3"}
    paths = SimpleNamespace(sqlite_fts=tmp_path / "unused.sqlite")
    monkeypatch.setattr(resources, "active_index", lambda: (tmp_path, {"index_id": "index-actual"}))
    monkeypatch.setattr(retrieval, "selected_retrieval_paths", lambda *_: paths)
    monkeypatch.setattr(retrieval, "TheraSkillHybridRetriever", Retriever)
    engine = LLMResponseComparisonEngine.__new__(LLMResponseComparisonEngine)
    engine.project_root, engine.model_config_path = tmp_path, tmp_path / "config.yaml"
    engine.retriever, engine.retrieval_paths = None, None
    evidence, audit = engine._retrieve_evidence("client feels worried")
    assert calls == [("client feels worried", "therapy-response-reviewer", 6)]
    assert evidence[0]["unit_id"] == "live-source"
    assert evidence[0]["text"] == "Supportive validation example"
    assert evidence[0]["forbidden_use"] == "diagnosis"
    assert audit["index_id"] == "index-actual"


def test_platform_reviewer_adapter_runs_full_summary_and_gate_scripts(tmp_path, monkeypatch):
    """Exercise engine -> adapter CLI -> both script mains -> saved reports -> public view."""
    import contextlib
    import importlib
    import io
    import subprocess
    import sys
    from pathlib import Path
    import domains.theraskill.theraskill_simulation.engines as engines
    from domains.theraskill.theraskill_simulation.contracts import TranscriptTurn
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts/Simulation"))
    adapter = importlib.import_module("run_simulation_reviewer_hermes_adapter")
    summary_script = importlib.import_module("run_session_longitudinal_summary")
    gate_script = importlib.import_module("run_session_reviewer_gate")
    clients = {"summary": AnalysisClient("summary"), "review": AnalysisClient("review")}
    for module, mode in ((summary_script, "summary"), (gate_script, "review")):
        monkeypatch.setattr(module, "OpenAICompatibleClient", lambda config, mode=mode: clients[mode])
        monkeypatch.setattr(module, "load_yaml", lambda _: {})
        monkeypatch.setattr(module, "merged_model_config", lambda *_: {"name": "offline-test", "base_url": "test://offline"})
        monkeypatch.setattr(module, "context_max_prompt_tokens", lambda *_: 2600)
        monkeypatch.setattr(module, "parse_retry_attempts", lambda *_: 0)
    invoked = []
    def execute(command, **kwargs):
        invoked.append(Path(command[1]).name)
        with monkeypatch.context() as scoped:
            scoped.setattr(sys, "argv", command[1:])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                name = Path(command[1]).name
                if name == "run_simulation_reviewer_hermes_adapter.py":
                    returncode = adapter.command_run_review(adapter.parse_args())
                elif name == "run_session_longitudinal_summary.py":
                    returncode = summary_script.main()
                else:
                    assert name == "run_session_reviewer_gate.py"
                    returncode = gate_script.main()
            return subprocess.CompletedProcess(command, returncode, output.getvalue(), "")
    monkeypatch.setattr(engines.subprocess, "run", execute)
    source = long_session()
    snapshot = SimpleNamespace(
        session=SimpleNamespace(session_id="session-test", language="en", status="completed",
            scenario=SimpleNamespace(model_dump=lambda **_: {"scenario_id": "test"}), runtime_version="test",
            knowledge_release_id="test", model_config_id="test", prompt_release_id="test"),
        public_view=SimpleNamespace(transcript=[TranscriptTurn(turn_id=row["turn_id"], turn_index=i + 1,
            role=row["role"], text=row["text"]) for i, row in enumerate(source["transcript"])]),
        private_patient_state=None, snapshot_id="snapshot-test", checksum="test")
    engine = engines.CommandBackedLLMSimulationReviewer(project_root=root, extraction_config=tmp_path / "extract.yaml",
        model_config=tmp_path / "model.yaml", work_root=tmp_path / "review")
    reviewed = engine.review(snapshot)
    assert invoked == ["run_simulation_reviewer_hermes_adapter.py", "run_session_longitudinal_summary.py", "run_session_reviewer_gate.py"]
    coverage = reviewed.public_review.summary["evidence_coverage"]
    assert coverage["complete"] and coverage["source_turn_count"] == 18
    assert coverage["segment_count"] > 1
    assert reviewed.public_review.findings[0]["evidence"][0]["turn_id"] == "t0"
    assert reviewed.public_review.gate_status == "fail"
    for client in clients.values():
        reconstructed = {row["turn_id"]: "" for row in source["transcript"]}
        for _, payload in client.calls:
            for row in payload["transcript"]:
                reconstructed[row["turn_id"]] += row["text"]
        assert reconstructed == {row["turn_id"]: row["text"] for row in source["transcript"]}


@pytest.mark.parametrize("language,title", [("zh", "合成教学对话"), ("en", "Synthetic teaching dialogue")])
def test_artifacts_have_readable_localized_text_and_main_content(tmp_path, language, title):
    runner = artifact_runner(tmp_path, ArtifactClient())
    runner.language = language
    result = runner.run()
    assert result["response_text"].startswith(title)
    assert "dialogue" in result["structured_content"]
    assert "rationale" in result["structured_content"]
    assert result["structured_content"]["plan"]["learning_objectives"]
    assert result["structured_content"]["quality_review"]["approved"]
