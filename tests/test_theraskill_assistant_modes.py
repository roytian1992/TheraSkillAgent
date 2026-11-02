from pathlib import Path

from fastapi.testclient import TestClient

from domains.theraskill.services.support_api.app import SupportAPISettings, create_app
from domains.theraskill.theraskill_support.contracts import RoutingDecision, SupportEngineResult
from domains.theraskill.theraskill_support.engine import LLMSupportEngine
from domains.theraskill.theraskill_support.repository import SQLiteSupportRepository
from domains.theraskill.theraskill_support.service import SupportSessionService


def _client(tmp_path: Path, engine) -> TestClient:
    service = SupportSessionService(
        repository=SQLiteSupportRepository(tmp_path / "sessions.sqlite"),
        engine=engine,
        knowledge_release_id="test",
    )
    return TestClient(create_app(
        SupportAPISettings(worker_enabled=False, contribution_api_url=None),
        service_override=service,
    ))


def test_model_only_turn_succeeds_without_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.work_root = tmp_path / "runtime"
    engine.memory_provider = None
    engine.model_name = "test-llm"
    engine._compact_history = lambda record, text: (None, [], text, {"summary_through_message": 0}, 0)
    engine._route = lambda *_args: (RoutingDecision(
        primary_skill="emotional-support", task_type="psychoeducation",
        detected_language="en", risk_level="none_observed",
        retrieval_query="What is CBT?", user_intent_summary="CBT definition",
    ), 0)

    def retrieval_must_not_run(*_args):
        raise AssertionError("model-only mode reached the knowledge retriever")

    engine._retrieve = retrieval_must_not_run
    engine._respond = lambda *_args: (
        {"response_text": "CBT is a structured therapy.", "presentation_mode": "plain_text", "structured_content": {}},
        None, {"summary_through_message": 0}, {"response_generation": 1},
    )
    with _client(tmp_path, engine) as client:
        session_id = client.post("/sessions", json={}).json()["session_id"]
        sent = client.post(f"/sessions/{session_id}/messages", json={
            "text": "What is CBT?", "knowledge_mode": "model_only",
        }, headers={"Idempotency-Key": "model-only"})
        assert sent.status_code == 202
        session = client.get(f"/sessions/{session_id}").json()
        assert session["generation"]["status"] == "idle"
        assert session["messages"][-1]["retrieval_summary"]["retrieval_mode"] == "skipped_by_user"
        diagnostics = client.get(f"/sessions/{session_id}/diagnostics").json()
        assert diagnostics["index"]["status"] == "missing"
        assert diagnostics["knowledge_mode"] == "model_only"


def test_diagnostics_classifies_failure_without_exposing_provider_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))

    class FailedEngine:
        def generate(self, *_args):
            raise ValueError("finish_reason: length; private-provider-detail")

    with _client(tmp_path, FailedEngine()) as client:
        alice = {"X-Support-User": "alice", "X-Support-Role": "user"}
        session_id = client.post("/sessions", json={}, headers=alice).json()["session_id"]
        sent = client.post(f"/sessions/{session_id}/messages", json={"text": "What is CBT?"},
                           headers={**alice, "Idempotency-Key": "failure"})
        assert sent.status_code == 202
        response = client.get(f"/sessions/{session_id}/diagnostics", headers=alice)
        assert response.status_code == 200
        assert response.json()["error_code"] == "output_limit"
        assert "private-provider-detail" not in response.text
        assert client.get(f"/sessions/{session_id}/diagnostics", headers={
            "X-Support-User": "bob", "X-Support-Role": "user",
        }).status_code == 404


def test_retry_can_switch_to_model_only_and_clears_old_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("THERASKILL_AGENT_ROOT", str(tmp_path))

    class ConditionalEngine:
        def generate(self, record, job_id):
            trace = next(item for item in record.turn_traces if item.job_id == job_id)
            if trace.knowledge_mode == "auto":
                raise FileNotFoundError("knowledge index is missing")
            return SupportEngineResult(
                response_text="CBT is a structured therapy.",
                routing=RoutingDecision(primary_skill="emotional-support", task_type="psychoeducation",
                                        detected_language="en", risk_level="none_observed",
                                        retrieval_query="CBT", user_intent_summary="definition"),
                retrieval_report_path="/tmp/none", retrieval_summary={"retrieval_mode": "skipped_by_user"},
                model_config_id="test-llm", prompt_release_id="test",
            )

    with _client(tmp_path, ConditionalEngine()) as client:
        session_id = client.post("/sessions", json={}).json()["session_id"]
        client.post(f"/sessions/{session_id}/messages", json={"text": "What is CBT?"},
                    headers={"Idempotency-Key": "first"})
        failed = client.get(f"/sessions/{session_id}/diagnostics").json()
        assert failed["error_code"] == "index_unavailable"
        retry = client.post(f"/sessions/{session_id}/retry", json={"knowledge_mode": "model_only"})
        assert retry.status_code == 202
        current = client.get(f"/sessions/{session_id}").json()
        assert current["generation"]["status"] == "idle"
        assert len(current["messages"]) == 2
        diagnostics = client.get(f"/sessions/{session_id}/diagnostics").json()
        assert diagnostics["knowledge_mode"] == "model_only"
        assert diagnostics["error_code"] == "none"
        assert diagnostics["failure_stage"] is None
