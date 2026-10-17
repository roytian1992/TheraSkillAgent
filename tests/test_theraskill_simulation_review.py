from __future__ import annotations

from pathlib import Path

from domains.theraskill.theraskill_simulation.contracts import (
    ReviewPublicView,
    SessionMode,
)
from domains.theraskill.theraskill_simulation.engines import ReviewResult
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
