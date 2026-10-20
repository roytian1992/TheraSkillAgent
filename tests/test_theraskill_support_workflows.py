from pathlib import Path
import json

from fastapi.testclient import TestClient

from domains.theraskill.services.support_api.app import SupportAPISettings, create_app
from domains.theraskill.theraskill_support.contracts import (
    RoutingDecision,
    SupportEngineResult,
)
from domains.theraskill.theraskill_support.engine import SupportEngine
from domains.theraskill.theraskill_support.repository import SQLiteSupportRepository
from domains.theraskill.theraskill_support.service import SupportSessionService


class _RiskEngine(SupportEngine):
    def generate(self, record, job_id: str) -> SupportEngineResult:
        return SupportEngineResult(
            response_text="先确认你现在是否安全，并联系身边可信任的人。",
            presentation_mode="risk_support",
            routing=RoutingDecision(
                primary_skill="risk-triage",
                task_type="risk_attention",
                detected_language="zh",
                risk_level="high",
                requires_immediate_safety_attention=True,
                safety_signal_codes=["plan_or_means"],
                retrieval_query="crisis safety support",
                user_intent_summary="需要安全支持",
            ),
            retrieval_report_path="/tmp/retrieval.json",
            retrieved_unit_ids=["unit-1"],
            retrieval_summary={
                "retrieval_mode": "hybrid_v3",
                "hit_count": 1,
                "source_index_id": "test-index",
                "knowledge_sha256": "test-knowledge",
            },
            model_config_id="test-model",
            prompt_release_id="test-prompt",
        )


def _service(tmp_path: Path) -> SupportSessionService:
    return SupportSessionService(
        repository=SQLiteSupportRepository(tmp_path / "support.sqlite"),
        engine=_RiskEngine(),
        knowledge_release_id="test-release",
    )


def test_risk_response_creates_safety_state_and_follow_up_plan(tmp_path: Path) -> None:
    service = _service(tmp_path)
    session = service.create_session(owner_id="user-1")
    submitted = service.submit_message(
        session.session_id,
        text="我现在很危险",
        owner_id="user-1",
        idempotency_key="turn-1",
    )
    service.finalize_response(session.session_id, job_id=submitted.job_id)

    generated = service.get_public(session.session_id, owner_id="user-1")
    assert generated.messages[-1].retrieval_summary == {
        "retrieval_mode": "hybrid_v3",
        "hit_count": 1,
        "source_index_id": "test-index",
        "knowledge_sha256": "test-knowledge",
    }

    updated = service.update_plan(
        session.session_id,
        owner_id="user-1",
        goals=["先把今晚安全地度过"],
        action_items=["联系一位可信任的人"],
    )
    updated = service.record_safety_action(
        session.session_id, owner_id="user-1", action="trusted_person"
    )
    updated = service.record_check_in(
        session.session_id, owner_id="user-1", safe_now=True, note="已经有人陪着我"
    )

    assert updated.safety.attention_level == "urgent"
    assert updated.safety.handoff_status == "accepted"
    assert updated.safety.check_in_status == "safe"
    assert updated.plan.goals[0].text == "先把今晚安全地度过"
    assert updated.plan.action_items == ["联系一位可信任的人"]

    needs_support = service.record_check_in(
        session.session_id, owner_id="user-1", safe_now=False, note="仍然需要有人陪同"
    )
    assert needs_support.safety.attention_level == "urgent"
    assert needs_support.safety.check_in_status == "needs_support"
    assert "check_in_not_safe" in needs_support.safety.signal_codes


def test_persisted_worker_claim_is_single_and_processes_pending_job(tmp_path: Path) -> None:
    service = _service(tmp_path)
    session = service.create_session(owner_id="user-1")
    submitted = service.submit_message(
        session.session_id,
        text="请帮我梳理一下现在的情况",
        owner_id="user-1",
        idempotency_key="worker-1",
    )

    assert service.process_one_pending_job() is True
    assert service.process_one_pending_job() is False
    completed = service.get_public(session.session_id, owner_id="user-1")
    assert completed.generation.status == "idle"
    assert completed.messages[-1].role == "assistant"

    # A second execution path cannot claim the same persisted job again.
    assert service.repository.claim_job(submitted.job_id) is None


def test_support_api_exposes_safety_resources_plan_and_check_in(tmp_path: Path) -> None:
    resource_file = tmp_path / "safety_resources.json"
    resource_file.write_text(
        json.dumps({"cn": [{"kind": "official", "label": "配置文件资源"}]}),
        encoding="utf-8",
    )
    app = create_app(
        SupportAPISettings(
            database_path=tmp_path / "support.sqlite",
            initialize_llm_engine=False,
            safety_resources_path=resource_file,
        ),
        service_override=_service(tmp_path),
    )
    client = TestClient(app)
    headers = {"X-Support-User": "user-1", "X-Support-Role": "user"}
    created = client.post("/sessions", headers=headers, json={})
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    resources = client.get("/safety/resources?region=cn", headers=headers)
    assert resources.status_code == 200
    assert resources.json()["resources"][0]["label"] == "配置文件资源"

    plan = client.patch(
        f"/sessions/{session_id}/plan",
        headers=headers,
        json={"goals": ["改善睡眠"], "action_items": ["今晚提前半小时休息"]},
    )
    assert plan.status_code == 200
    assert plan.json()["plan"]["goals"][0]["text"] == "改善睡眠"

    check_in = client.post(
        f"/sessions/{session_id}/safety/check-in",
        headers=headers,
        json={"safe_now": True},
    )
    assert check_in.status_code == 200
    assert check_in.json()["safety"]["check_in_status"] == "safe"


def test_safety_screen_is_visible_before_generation_and_survives_failure(tmp_path: Path) -> None:
    service = _service(tmp_path)
    session = service.create_session(owner_id="user-1")
    submitted = service.submit_message(session.session_id, text="我现在想自杀，身边没人，我现在不安全。", owner_id="user-1", idempotency_key="urgent")
    assert submitted.session.generation.status == "pending"
    assert submitted.session.safety.attention_level == "urgent"
    assert len(submitted.session.messages) == 1
    service.engine = None
    service.finalize_response(session.session_id, job_id=submitted.job_id)
    result = service.get_public(session.session_id, owner_id="user-1")
    assert result.generation.status == "failed"
    assert result.safety.attention_level == "urgent"


def test_check_in_during_generation_is_not_lost(tmp_path: Path) -> None:
    service = _service(tmp_path)
    class ConcurrentEngine(_RiskEngine):
        def generate(self, record, job_id):
            service.record_check_in(record.public.session_id, owner_id="user-1", safe_now=False)
            return super().generate(record, job_id)
    service.engine = ConcurrentEngine()
    session = service.create_session(owner_id="user-1")
    submitted = service.submit_message(session.session_id, text="我现在想自杀。", owner_id="user-1", idempotency_key="urgent")
    service.finalize_response(session.session_id, job_id=submitted.job_id)
    result = service.get_public(session.session_id, owner_id="user-1")
    assert result.generation.status == "idle"
    assert result.safety.check_in_status == "needs_support"
    assert "check_in_not_safe" in result.safety.signal_codes


def test_retry_restores_expert_workflow_without_adding_another_user_turn(tmp_path: Path) -> None:
    service = _service(tmp_path)
    class Broken:
        def generate(self, record, job_id):
            raise RuntimeError('synthetic outage')
    service.engine = Broken()
    session = service.create_session(owner_id='user-1')
    submitted = service.submit_message(session.session_id,text='Review a response.',owner_id='user-1',idempotency_key='first')
    service.finalize_response(session.session_id,job_id=submitted.job_id)
    service.retry_response(session.session_id,owner_id='user-1',workflow='response_review',output_language='en')
    record = service.repository.get(session.session_id)
    assert record.workflow == 'response_review' and record.output_language == 'en'
    assert len(record.public.messages) == 1 and record.public.generation.status == 'pending'


def test_public_message_excludes_private_retrieval_diagnostics(tmp_path):
    class DiagnosticEngine(_RiskEngine):
        def generate(self, record, job_id):
            result = super().generate(record, job_id)
            result.retrieval_summary.update(warnings=["private path /private/knowledge"], structured_filter={"internal": "metadata"}, timings={"internal": 1})
            return result
    service = _service(tmp_path)
    service.engine = DiagnosticEngine()
    session = service.create_session(owner_id="user")
    submitted = service.submit_message(session.session_id, text="Synthetic question", owner_id="user", idempotency_key="private-diagnostics")
    service.finalize_response(session.session_id, job_id=submitted.job_id)
    record = service.repository.get(session.session_id)
    assert "warnings" in record.turn_traces[-1].retrieval_summary
    public = record.public.messages[-1].retrieval_summary
    assert public["source_index_id"] == "test-index"
    assert not ({"warnings", "structured_filter", "timings"} & public.keys())
