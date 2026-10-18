from __future__ import annotations

from pathlib import Path

from domains.theraskill.theraskill_simulation.contracts import (
    ActorRole,
    ReviewPublicView,
    SessionMode,
)
from domains.theraskill.theraskill_simulation.engines import AgentTurnResult, ReviewResult
from domains.theraskill.theraskill_simulation.repository import SQLiteSessionRepository
from domains.theraskill.theraskill_simulation.service import SimulationSessionService


class _Registry:
    def __init__(self, path: Path, scenario_id: str) -> None:
        self.registry_path = path
        self.scenario_id = scenario_id

    def get(self, scenario_id: str, *, language: str = "zh") -> dict[str, object]:
        assert scenario_id == self.scenario_id
        return {
            "scenario_id": self.scenario_id,
            "scenario_family": "supportive_exploration",
            "case_family": "stress",
            "scenario_brief": "持续压力与睡眠困扰",
            "scenario_tags": ["压力", "睡眠"],
            "difficulty_band": "medium",
            "risk_band": "low",
            "recommended_turn_budget": 3,
            "split_labels": {"formal_split": "test"},
        }


class _Therapist:
    def generate_turn(self, **kwargs):
        return AgentTurnResult(text="That sounds difficult. What happened?", therapist_state={})


class _Reviewer:
    def review(self, snapshot):
        return ReviewResult(
            public_review=ReviewPublicView(
                status="complete",
                gate_status="pass",
                summary={"score": 4, "source_session_id": snapshot.session.metadata.get("source_session_id")},
                findings=[],
            ),
            model_config_id="review-test-model",
            prompt_release_id="review-test-prompt",
        )


def _service(tmp_path: Path) -> SimulationSessionService:
    scenario_id = "scenario::review-test"
    return SimulationSessionService(
        repository=SQLiteSessionRepository(tmp_path / "simulation.sqlite"),
        scenario_registry=_Registry(tmp_path / "registry.jsonl", scenario_id),
        reviewer_engine=None,
    )


def test_review_only_copies_terminal_source_and_uses_durable_review_job(tmp_path: Path) -> None:
    service = _service(tmp_path)
    scenario_id = "scenario::review-test"
    source = service.create_session(
        scenario_id=scenario_id,
        mode=SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST,
        session_id="source-session",
    )
    service.therapist_engine = _Therapist()
    service.submit_turn(source.session.session_id, text="I feel stressed at work.",
                        actor=ActorRole.HUMAN_CLIENT_ROLE, idempotency_key="first-turn")
    source = service.complete_session(source.session.session_id)
    service.reviewer_engine = _Reviewer()

    review_only = service.create_session(
        scenario_id=scenario_id,
        mode=SessionMode.REVIEW_ONLY,
        source_session_id=source.session.session_id,
        session_id="review-session",
    )

    assert review_only.session.mode == SessionMode.REVIEW_ONLY
    assert review_only.session.status == "completed"
    assert review_only.public_view.review is not None
    assert review_only.public_view.review.status == "pending"
    assert review_only.private_therapist_state == source.private_therapist_state
    job = service.repository.get_review_job("review-session")
    assert job is not None and job["status"] == "pending"

    assert service.process_one_pending_review() is True
    assert service.process_one_pending_review() is False
    completed = service.get_snapshot("review-session")
    assert completed.public_view.review.status == "complete"
    assert completed.public_view.review.gate_status == "pass"
    assert service.repository.get_review_job("review-session")["status"] == "complete"


def test_review_job_claim_is_single_writer(tmp_path: Path) -> None:
    service = _service(tmp_path)
    scenario_id = "scenario::review-test"
    source = service.create_session(
        scenario_id=scenario_id,
        mode=SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST,
        session_id="source-session",
    )
    service.complete_session(source.session.session_id)
    service.reviewer_engine = _Reviewer()
    service.create_session(
        scenario_id=scenario_id,
        mode=SessionMode.REVIEW_ONLY,
        source_session_id=source.session.session_id,
        session_id="review-session",
    )
    job = service.repository.get_review_job("review-session")
    claimed = service.repository.claim_review_job(job["job_id"])
    assert claimed is not None
    assert service.repository.claim_review_job(job["job_id"]) is None


def test_empty_session_cannot_pass_even_when_reviewer_would_pass(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.reviewer_engine = _Reviewer()
    session = service.create_session(scenario_id="scenario::review-test", mode=SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST)
    completed = service.complete_session(session.session.session_id, defer_review=True)
    assert completed.public_view.review.status == "not_evaluable"
    assert completed.public_view.review.gate_status == "insufficient_evidence"
    assert not service.repository.list_pending_review_jobs()


class _DeferredTherapist(_Therapist):
    fail = True

    def generate_turn(self, **kwargs):
        return AgentTurnResult(text="Tell me more.", therapist_state={}, runtime_trace={
            "deferred_update": {"memory_input": "preserved"}, "deferred_update_id": "update-1"})

    def finalize_turn(self, **kwargs):
        if self.fail:
            raise RuntimeError("synthetic memory failure")
        return AgentTurnResult(text="", therapist_state={})


def test_failed_memory_can_retry_without_duplicate_turns(tmp_path: Path) -> None:
    service = _service(tmp_path)
    engine = _DeferredTherapist(); service.therapist_engine = engine
    session = service.create_session(scenario_id="scenario::review-test", mode=SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST)
    sid = session.session.session_id
    service.submit_turn(sid, text="I feel stressed.", actor=ActorRole.HUMAN_CLIENT_ROLE, idempotency_key="first")
    service.finalize_therapist_turn(sid, deferred_update_id="update-1")
    assert service.get_snapshot(sid).public_view.progress["therapist_memory_update_failed"] == 1
    service.retry_deferred_update(sid); engine.fail = False
    service.finalize_therapist_turn(sid, deferred_update_id="update-1")
    result = service.get_snapshot(sid)
    assert len(result.public_view.transcript) == 2
    assert result.public_view.progress["therapist_memory_update_failed"] == 0
    assert result.session.metadata["therapist_memory_update"]["status"] == "complete"


def test_failed_memory_allows_explicit_incomplete_completion(tmp_path: Path) -> None:
    service = _service(tmp_path); service.therapist_engine = _DeferredTherapist()
    session = service.create_session(scenario_id="scenario::review-test", mode=SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST)
    sid = session.session.session_id
    service.submit_turn(sid, text="I feel stressed.", actor=ActorRole.HUMAN_CLIENT_ROLE, idempotency_key="first")
    service.finalize_therapist_turn(sid, deferred_update_id="update-1")
    completed = service.complete_session(sid, defer_review=True)
    assert completed.public_view.status == "completed"
    assert len(completed.public_view.transcript) == 2
    assert completed.public_view.review.gate_status == "incomplete_state"
    assert completed.public_view.review.status == "not_evaluable"


def test_review_only_cannot_erase_source_memory_failure(tmp_path: Path) -> None:
    service = _service(tmp_path); service.therapist_engine = _DeferredTherapist()
    source = service.create_session(scenario_id='scenario::review-test', mode=SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST)
    sid = source.session.session_id
    service.submit_turn(sid, text='I feel stressed.', actor=ActorRole.HUMAN_CLIENT_ROLE, idempotency_key='first')
    service.finalize_therapist_turn(sid, deferred_update_id='update-1')
    service.complete_session(sid, defer_review=True)
    service.reviewer_engine = _Reviewer()
    review = service.create_session(scenario_id='scenario::review-test', mode=SessionMode.REVIEW_ONLY, source_session_id=sid)
    service.process_one_pending_review()
    assert service.get_snapshot(review.session.session_id).public_view.review.gate_status == 'incomplete_state'


def test_empty_session_cannot_receive_training_ratings_but_accepts_issue_comments(tmp_path: Path) -> None:
    import pytest
    from domains.theraskill.theraskill_simulation.service import SessionOperationError
    service = _service(tmp_path)
    session = service.create_session(scenario_id='scenario::review-test',mode=SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST)
    sid = session.session.session_id; service.complete_session(sid)
    with pytest.raises(SessionOperationError,match='not evaluable'):
        service.submit_feedback(sid,submitted_by_role=ActorRole.HUMAN_CLIENT_ROLE,rating=5)
    result = service.submit_feedback(sid,submitted_by_role=ActorRole.HUMAN_CLIENT_ROLE,comment='Synthetic generation issue')
    assert result.rating is None and result.comment == 'Synthetic generation issue'
