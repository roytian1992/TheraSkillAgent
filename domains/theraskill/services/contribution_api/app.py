"""FastAPI surface for governed TheraSkill contributions."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from domains.theraskill.theraskill_contribution.contracts import (
    CaptureResult,
    CandidateAdjudicationRequest,
    ConsentCreate,
    ConsentRecord,
    ContributionCapture,
    ContributionType,
    ContributionView,
    ExpertDecisionRequest,
    ExpertDecisionResult,
    ExpertProfile,
    ExpertProfileCreate,
    ExpertProfileVerification,
    DomainMemory,
    MemoryApproval,
    MemoryContext,
    MemoryProposalCreate,
    MemoryUpdate,
    KnowledgeCandidate,
    KnowledgeRelease,
    ReleasePreviewRequest,
    ReleasePromotionRequest,
    ReleaseRollbackRequest,
    ReleaseFailure,
    ReleaseFailureCreate,
    ReleaseFailureReplay,
    OperationsSnapshot,
    ReviewAction,
    ReviewAssignment,
    ReviewAssignmentCreate,
    ReviewPreparationResult,
    ReviewQueueItem,
    ReviewTriage,
    ReviewTriageRequest,
    ReviewWorkbench,
)
from domains.theraskill.theraskill_contribution.repository import (
    ContributionConflictError,
    ContributionNotFoundError,
    SQLiteContributionRepository,
)
from domains.theraskill.theraskill_contribution.service import (
    ConsentViolationError,
    ContributionService,
    VisibilityError,
)
from domains.theraskill.theraskill_contribution.review import ExpertReviewService, ReviewWorkflowError
from domains.theraskill.theraskill_contribution.memory import DomainMemoryService, MemoryWorkflowError
from domains.theraskill.theraskill_contribution.candidate import (
    CandidateLifecycleService,
    CandidateWorkflowError,
    Phase6SemanticDeduplicator,
    QwenCandidateStructurer,
)
from domains.theraskill.theraskill_contribution.release import (
    ConfiguredEmbeddingProvider,
    KnowledgeReleaseService,
)
from domains.theraskill.theraskill_contribution.operations import ContinuousOperationsService
from domains.theraskill.runtime import runtime_root
from domains.theraskill.observability import RequestMetrics
from domains.theraskill.metrics_middleware import MetricsMiddleware


from theraskill_agent.paths import resource_root, resource_path

ROOT = resource_root()


class ContributionAPISettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    database_path: Path = Field(default_factory=lambda: runtime_root() / "contribution/contributions.sqlite")
    candidate_model_config_path: Path = resource_path("configs/theraskill/retrieval.yaml")
    knowledge_release_root: Path = Field(default_factory=lambda: runtime_root() / "contribution/releases")
    knowledge_release_slots_dir: Path = Field(default_factory=lambda: runtime_root() / "contribution/release_slots")
    operations_output_root: Path = Field(default_factory=lambda: runtime_root() / "contribution/operations/snapshots")
    benchmark_cases_path: Path = Field(default_factory=lambda: runtime_root() / "contribution/benchmark_cases.jsonl")
    api_token: str | None = Field(default_factory=lambda: os.environ.get("THERASKILL_CONTRIBUTION_API_TOKEN"))
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5174",
            "http://localhost:5174",
            "http://127.0.0.1:5175",
            "http://localhost:5175",
        ]
    )


class Identity(BaseModel):
    subject_id: str
    role: str
    expert_profile_id: str | None = None


class EscalationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: str
    rationale: str = Field(min_length=1, max_length=8000)
    confidence: int = Field(default=3, ge=1, le=5)
    conflict_of_interest: bool = False


ROLES = {
    "user", "learner", "instructor", "expert_reviewer", "knowledge_curator",
    "adjudicator", "release_manager", "researcher", "admin", "system",
}


ROUTE_TYPES = {
    "turn": ContributionType.TURN_OBSERVATION,
    "session-finalized": ContributionType.TURN_OBSERVATION,
    "response": ContributionType.USER_FEEDBACK,
    "session": ContributionType.SESSION_FEEDBACK,
    "annotation": ContributionType.TURN_ANNOTATION,
    "knowledge": ContributionType.KNOWLEDGE_PROPOSAL,
    "correction": ContributionType.CORRECTION_PROPOSAL,
}

CAPTURE_ROLES = {
    ContributionType.TURN_OBSERVATION: {"system"},
    ContributionType.USER_FEEDBACK: {"user", "learner", "instructor", "expert_reviewer"},
    ContributionType.SESSION_FEEDBACK: {"user", "learner", "instructor", "expert_reviewer"},
    ContributionType.TURN_ANNOTATION: {"learner", "instructor", "expert_reviewer"},
    ContributionType.CORRECTION_PROPOSAL: {"user", "learner", "instructor", "expert_reviewer"},
    ContributionType.KNOWLEDGE_PROPOSAL: {"expert_reviewer", "knowledge_curator", "adjudicator"},
}


def create_app(
    settings: ContributionAPISettings | None = None,
    *,
    candidate_lifecycle: CandidateLifecycleService | None = None,
    knowledge_release_service: KnowledgeReleaseService | None = None,
) -> FastAPI:
    cfg = settings or ContributionAPISettings()
    service = ContributionService(SQLiteContributionRepository(cfg.database_path))
    review_service = ExpertReviewService(service.repository)
    memory_service = DomainMemoryService(service.repository)
    candidate_service = candidate_lifecycle or CandidateLifecycleService(
        service.repository,
        structurer=QwenCandidateStructurer(cfg.candidate_model_config_path),
        deduplicator=Phase6SemanticDeduplicator(cfg.candidate_model_config_path),
    )
    release_service = knowledge_release_service or KnowledgeReleaseService(
        service.repository,
        embedding_provider=ConfiguredEmbeddingProvider(cfg.candidate_model_config_path),
        release_root=cfg.knowledge_release_root,
        slots_dir=cfg.knowledge_release_slots_dir,
    )
    operations_service = ContinuousOperationsService(
        service.repository,
        release_service=release_service,
        output_root=cfg.operations_output_root,
        benchmark_cases_path=cfg.benchmark_cases_path,
    )
    app = FastAPI(title="TheraSkill Contribution Hub API", version="1.0.0")
    metrics = RequestMetrics("theraskill-contribution")
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def identity(
        x_contribution_subject: Annotated[str, Header(alias="X-Contribution-Subject")],
        x_contribution_role: Annotated[str, Header(alias="X-Contribution-Role")],
        authorization: Annotated[str, Header()] = "",
        x_expert_profile: Annotated[str | None, Header(alias="X-Expert-Profile")] = None,
    ) -> Identity:
        if cfg.api_token and not secrets.compare_digest(authorization, f"Bearer {cfg.api_token}"):
            raise HTTPException(status_code=401, detail="invalid API token")
        if x_contribution_role not in ROLES:
            raise HTTPException(status_code=403, detail="unsupported contribution role")
        return Identity(
            subject_id=x_contribution_subject,
            role=x_contribution_role,
            expert_profile_id=x_expert_profile,
        )

    def capture_as(
        expected: ContributionType,
        request: ContributionCapture,
        actor: Identity,
        idempotency_key: str,
    ) -> CaptureResult:
        if request.contribution_type != expected:
            raise HTTPException(status_code=422, detail=f"route requires {expected}")
        if actor.role not in CAPTURE_ROLES[expected] and actor.role != "admin":
            raise HTTPException(status_code=403, detail=f"role cannot capture {expected}")
        if expected == ContributionType.KNOWLEDGE_PROPOSAL:
            if not actor.expert_profile_id:
                raise HTTPException(status_code=403, detail="verified expert profile is required")
            try:
                profile = review_service.repository.get_expert_profile(actor.expert_profile_id)
            except ContributionNotFoundError as exc:
                raise HTTPException(status_code=403, detail="verified expert profile is required") from exc
            if profile.subject_id != actor.subject_id or not profile.is_verified():
                raise HTTPException(status_code=403, detail="verified expert profile is required")
        try:
            return service.capture(
                request,
                subject_id=actor.subject_id,
                role=actor.role,
                expert_profile_id=actor.expert_profile_id,
                idempotency_key=idempotency_key,
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="consent snapshot not found") from exc
        except ConsentViolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ContributionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "schema_version": "theraskill_contribution_v1"}

    @app.get("/metrics")
    def metrics_endpoint() -> dict[str, Any]:
        return metrics.snapshot()

    @app.post("/v1/consents", response_model=ConsentRecord, status_code=201)
    def create_consent(
        request: ConsentCreate,
        actor: Identity = Depends(identity),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ConsentRecord:
        try:
            return service.create_consent(
                actor.subject_id, request, idempotency_key=idempotency_key
            )
        except ContributionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/consents", response_model=list[ConsentRecord])
    def list_consents(actor: Identity = Depends(identity)) -> list[ConsentRecord]:
        return service.repository.list_consents(actor.subject_id)

    @app.delete("/v1/consents/{consent_id}", response_model=ConsentRecord)
    def revoke_consent(consent_id: str, actor: Identity = Depends(identity)) -> ConsentRecord:
        try:
            return service.revoke_consent(actor.subject_id, consent_id)
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="consent snapshot not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def register_capture(path: str, expected: ContributionType) -> None:
        async def endpoint(
            request: ContributionCapture,
            actor: Identity = Depends(identity),
            idempotency_key: Annotated[str, Header(alias="Idempotency-Key")] = "",
        ) -> CaptureResult:
            if not idempotency_key:
                raise HTTPException(status_code=400, detail="Idempotency-Key is required")
            return capture_as(expected, request, actor, idempotency_key)

        app.add_api_route(path, endpoint, methods=["POST"], response_model=CaptureResult, status_code=201)

    register_capture("/v1/events/turn", ContributionType.TURN_OBSERVATION)
    register_capture("/v1/events/session-finalized", ContributionType.TURN_OBSERVATION)
    register_capture("/v1/feedback/response", ContributionType.USER_FEEDBACK)
    register_capture("/v1/feedback/session", ContributionType.SESSION_FEEDBACK)
    register_capture("/v1/annotations/turn", ContributionType.TURN_ANNOTATION)
    register_capture("/v1/contributions/knowledge", ContributionType.KNOWLEDGE_PROPOSAL)
    register_capture("/v1/contributions/correction", ContributionType.CORRECTION_PROPOSAL)

    @app.get("/v1/contributions")
    def list_contributions(
        actor: Identity = Depends(identity),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        items = service.list_visible(subject_id=actor.subject_id, role=actor.role, limit=limit)
        return {"count": len(items), "contributions": items}

    @app.get("/v1/contributions/{contribution_id}", response_model=ContributionView)
    def get_contribution(contribution_id: str, actor: Identity = Depends(identity)) -> ContributionView:
        try:
            return service.get_visible(contribution_id, subject_id=actor.subject_id, role=actor.role)
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="contribution not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.delete("/v1/contributions/{contribution_id}", response_model=ContributionView)
    def withdraw_contribution(
        contribution_id: str, actor: Identity = Depends(identity)
    ) -> ContributionView:
        try:
            return service.withdraw(
                contribution_id, subject_id=actor.subject_id, role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="contribution not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/v1/reviews/prepare", response_model=ReviewPreparationResult)
    def prepare_reviews(actor: Identity = Depends(identity)) -> ReviewPreparationResult:
        if actor.role not in {"admin", "system"}:
            raise HTTPException(status_code=403, detail="admin or system role is required")
        return review_service.prepare_all(
            actor_subject_id=actor.subject_id, actor_role=actor.role
        )

    @app.post("/v1/expert-profiles", response_model=ExpertProfile, status_code=201)
    def create_expert_profile(
        request: ExpertProfileCreate, actor: Identity = Depends(identity)
    ) -> ExpertProfile:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail="admin role is required")
        try:
            return review_service.create_expert_profile(request)
        except ContributionConflictError as exc:
            raise HTTPException(status_code=409, detail="subject already has an expert profile") from exc

    @app.post(
        "/v1/expert-profiles/{expert_profile_id}/verify",
        response_model=ExpertProfile,
    )
    def verify_expert_profile(
        expert_profile_id: str,
        request: ExpertProfileVerification,
        actor: Identity = Depends(identity),
    ) -> ExpertProfile:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail="admin role is required")
        try:
            return review_service.verify_expert_profile(
                expert_profile_id,
                verified_by=actor.subject_id,
                status=request.status,
                expires_at=request.expires_at,
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="expert profile not found") from exc
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/expert-profiles/me", response_model=ExpertProfile)
    def get_my_expert_profile(actor: Identity = Depends(identity)) -> ExpertProfile:
        profile = review_service.repository.get_expert_profile_by_subject(actor.subject_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="expert profile not found")
        return profile

    @app.get("/v1/review-queue", response_model=list[ReviewQueueItem])
    def list_review_queue(
        actor: Identity = Depends(identity),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[ReviewQueueItem]:
        if actor.role not in {
            "expert_reviewer", "knowledge_curator", "adjudicator", "admin"
        }:
            raise HTTPException(status_code=403, detail="review role is required")
        return review_service.list_queue(
            subject_id=actor.subject_id, role=actor.role, limit=limit
        )

    @app.post(
        "/v1/contributions/{contribution_id}/triage",
        response_model=ReviewTriage,
    )
    def triage_contribution(
        contribution_id: str,
        request: ReviewTriageRequest,
        actor: Identity = Depends(identity),
    ) -> ReviewTriage:
        if actor.role not in {"knowledge_curator", "adjudicator", "admin", "system"}:
            raise HTTPException(status_code=403, detail="triage role is required")
        try:
            return review_service.triage(
                contribution_id,
                request,
                actor_subject_id=actor.subject_id,
                actor_role=actor.role,
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="contribution not found") from exc
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/contributions/{contribution_id}/assign",
        response_model=ReviewAssignment,
        status_code=201,
    )
    def assign_contribution(
        contribution_id: str,
        request: ReviewAssignmentCreate,
        actor: Identity = Depends(identity),
    ) -> ReviewAssignment:
        if actor.role not in {"admin", "knowledge_curator"}:
            raise HTTPException(status_code=403, detail="assignment role is required")
        try:
            return review_service.assign(
                contribution_id, request, assigned_by=actor.subject_id
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="contribution or profile not found") from exc
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ContributionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/contributions/{contribution_id}/workbench",
        response_model=ReviewWorkbench,
    )
    def get_review_workbench(
        contribution_id: str, actor: Identity = Depends(identity)
    ) -> ReviewWorkbench:
        if actor.role not in {
            "expert_reviewer", "knowledge_curator", "adjudicator", "admin"
        }:
            raise HTTPException(status_code=403, detail="review role is required")
        try:
            return review_service.workbench(
                contribution_id, subject_id=actor.subject_id, role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="review material not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post(
        "/v1/contributions/{contribution_id}/expert-decision",
        response_model=ExpertDecisionResult,
        status_code=201,
    )
    def submit_expert_decision(
        contribution_id: str,
        request: ExpertDecisionRequest,
        actor: Identity = Depends(identity),
    ) -> ExpertDecisionResult:
        if actor.role != "expert_reviewer":
            raise HTTPException(status_code=403, detail="expert reviewer role is required")
        try:
            return review_service.decide(
                contribution_id, request, subject_id=actor.subject_id
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="assignment or contribution not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/contributions/{contribution_id}/escalate",
        response_model=ExpertDecisionResult,
        status_code=201,
    )
    def escalate_contribution(
        contribution_id: str,
        request: EscalationRequest,
        actor: Identity = Depends(identity),
    ) -> ExpertDecisionResult:
        decision = ExpertDecisionRequest(
            assignment_id=request.assignment_id,
            issue_types=["not_enough_context"],
            actions=[ReviewAction.ESCALATE_TO_ADJUDICATION],
            remediation_lanes=[],
            confidence=request.confidence,
            rationale=request.rationale,
            conflict_of_interest=request.conflict_of_interest,
        )
        return submit_expert_decision(contribution_id, decision, actor)

    @app.get("/v1/candidates", response_model=list[KnowledgeCandidate])
    def list_candidates(
        actor: Identity = Depends(identity),
        status: list[str] | None = Query(default=None),
    ) -> list[KnowledgeCandidate]:
        if actor.role not in {"knowledge_curator", "adjudicator", "admin"}:
            raise HTTPException(status_code=403, detail="candidate governance role is required")
        return service.repository.list_knowledge_candidates(statuses=set(status or []) or None)

    @app.get("/v1/candidates/{candidate_id}", response_model=KnowledgeCandidate)
    def get_candidate(
        candidate_id: str, actor: Identity = Depends(identity)
    ) -> KnowledgeCandidate:
        if actor.role not in {"knowledge_curator", "adjudicator", "admin"}:
            raise HTTPException(status_code=403, detail="candidate governance role is required")
        try:
            return service.repository.get_knowledge_candidate(candidate_id)
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="candidate not found") from exc

    @app.post("/v1/candidates/{candidate_id}/structure", response_model=KnowledgeCandidate)
    def structure_candidate(
        candidate_id: str, actor: Identity = Depends(identity)
    ) -> KnowledgeCandidate:
        if actor.role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        try:
            return candidate_service.structure(
                candidate_id, actor_subject_id=actor.subject_id, actor_role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="candidate not found") from exc
        except CandidateWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/candidates/{candidate_id}/dedup-check", response_model=KnowledgeCandidate)
    def dedup_candidate(
        candidate_id: str, actor: Identity = Depends(identity)
    ) -> KnowledgeCandidate:
        if actor.role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        try:
            return candidate_service.dedup_check(
                candidate_id, actor_subject_id=actor.subject_id, actor_role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="candidate not found") from exc
        except CandidateWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/candidates/{candidate_id}/adjudicate", response_model=KnowledgeCandidate)
    def adjudicate_candidate(
        candidate_id: str,
        request: CandidateAdjudicationRequest,
        actor: Identity = Depends(identity),
    ) -> KnowledgeCandidate:
        if actor.role != "adjudicator" or not actor.expert_profile_id:
            raise HTTPException(status_code=403, detail="verified adjudicator identity is required")
        try:
            return candidate_service.adjudicate(
                candidate_id,
                request,
                actor_subject_id=actor.subject_id,
                actor_role=actor.role,
                expert_profile_id=actor.expert_profile_id,
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="candidate, contribution, or profile not found") from exc
        except CandidateWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/candidates/{candidate_id}/validate", response_model=KnowledgeCandidate)
    def validate_candidate(
        candidate_id: str, actor: Identity = Depends(identity)
    ) -> KnowledgeCandidate:
        if actor.role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        try:
            return candidate_service.validate(
                candidate_id, actor_subject_id=actor.subject_id, actor_role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="candidate not found") from exc
        except CandidateWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/releases", response_model=list[KnowledgeRelease])
    def list_knowledge_releases(
        actor: Identity = Depends(identity),
    ) -> list[KnowledgeRelease]:
        if actor.role not in {"knowledge_curator", "adjudicator", "release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release governance role is required")
        return service.repository.list_knowledge_releases()

    @app.get("/v1/releases/{release_id}", response_model=KnowledgeRelease)
    def get_knowledge_release(
        release_id: str, actor: Identity = Depends(identity)
    ) -> KnowledgeRelease:
        if actor.role not in {"knowledge_curator", "adjudicator", "release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release governance role is required")
        try:
            return service.repository.get_knowledge_release(release_id)
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="release not found") from exc

    @app.post("/v1/releases/preview", response_model=KnowledgeRelease, status_code=201)
    def preview_knowledge_release(
        request: ReleasePreviewRequest,
        actor: Identity = Depends(identity),
    ) -> KnowledgeRelease:
        if actor.role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        try:
            return release_service.preview(
                request, actor_subject_id=actor.subject_id, actor_role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="candidate or lineage record not found") from exc
        except (CandidateWorkflowError, ContributionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/releases/promote", response_model=KnowledgeRelease)
    def promote_knowledge_release(
        request: ReleasePromotionRequest,
        actor: Identity = Depends(identity),
    ) -> KnowledgeRelease:
        if actor.role not in {"release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release manager role is required")
        try:
            return release_service.promote(
                request.release_id,
                request.expected_manifest_sha256,
                behavior_report_id=request.behavior_report_id,
                actor_subject_id=actor.subject_id,
                actor_role=actor.role,
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="release not found") from exc
        except (CandidateWorkflowError, ContributionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/releases/{release_id}/rollback", response_model=KnowledgeRelease)
    def rollback_knowledge_release(
        release_id: str,
        request: ReleaseRollbackRequest,
        actor: Identity = Depends(identity),
    ) -> KnowledgeRelease:
        if actor.role not in {"release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release manager role is required")
        try:
            return release_service.rollback(
                release_id,
                request.reason,
                actor_subject_id=actor.subject_id,
                actor_role=actor.role,
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="release not found") from exc
        except (CandidateWorkflowError, ContributionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/operations/snapshots", response_model=OperationsSnapshot, status_code=201)
    def create_operations_snapshot(
        actor: Identity = Depends(identity),
        scope: str = Query(default="production", pattern="^(staged_pilot|production)$"),
    ) -> OperationsSnapshot:
        if actor.role not in {"researcher", "admin"}:
            raise HTTPException(status_code=403, detail="research operations role is required")
        try:
            return operations_service.snapshot(scope=scope, actor_subject_id=actor.subject_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail="benchmark audit source is unavailable") from exc

    @app.post(
        "/v1/releases/{release_id}/failures",
        response_model=ReleaseFailure,
        status_code=201,
    )
    def report_release_failure(
        release_id: str,
        request: ReleaseFailureCreate,
        actor: Identity = Depends(identity),
    ) -> ReleaseFailure:
        if actor.role not in {"system", "researcher", "release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release operations role is required")
        try:
            return operations_service.report_failure(
                release_id,
                request,
                actor_subject_id=actor.subject_id,
                actor_role=actor.role,
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="release not found") from exc

    @app.get("/v1/release-failures", response_model=list[ReleaseFailure])
    def list_release_failures(
        actor: Identity = Depends(identity),
        release_id: str | None = Query(default=None),
    ) -> list[ReleaseFailure]:
        if actor.role not in {"researcher", "release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release operations role is required")
        return service.repository.list_release_failures(release_id)

    @app.post(
        "/v1/release-failures/{failure_id}/replay",
        response_model=ReleaseFailureReplay,
    )
    def replay_release_failure(
        failure_id: str,
        actor: Identity = Depends(identity),
    ) -> ReleaseFailureReplay:
        if actor.role not in {"release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release manager role is required")
        try:
            return operations_service.replay_failure(
                failure_id, actor_subject_id=actor.subject_id
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="failure or release not found") from exc

    @app.post("/v1/memory/proposals", response_model=DomainMemory, status_code=201)
    def propose_memory(
        request: MemoryProposalCreate, actor: Identity = Depends(identity)
    ) -> DomainMemory:
        if actor.role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        return memory_service.propose(request, subject_id=actor.subject_id)

    @app.get("/v1/memory", response_model=list[DomainMemory])
    def list_memory(actor: Identity = Depends(identity)) -> list[DomainMemory]:
        try:
            return memory_service.list_visible(
                subject_id=actor.subject_id, role=actor.role
            )
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/v1/memory/context", response_model=MemoryContext)
    def memory_context(
        actor: Identity = Depends(identity),
        subject_id: str | None = Query(default=None),
        include_sensitive: bool = Query(default=False),
        max_chars: int = Query(default=3000, ge=0, le=4000),
    ) -> MemoryContext:
        target = subject_id or actor.subject_id
        if target != actor.subject_id and actor.role not in {"system", "admin"}:
            raise HTTPException(status_code=403, detail="memory context belongs to another subject")
        if actor.role not in {"user", "system", "admin"}:
            raise HTTPException(status_code=403, detail="memory context role is not allowed")
        return memory_service.context(
            subject_id=target,
            include_sensitive=include_sensitive,
            max_chars=max_chars,
        )

    @app.post("/v1/memory/{memory_id}/approve", response_model=DomainMemory)
    def approve_memory(
        memory_id: str,
        request: MemoryApproval,
        actor: Identity = Depends(identity),
    ) -> DomainMemory:
        if actor.role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            return memory_service.approve(
                memory_id, request, subject_id=actor.subject_id, role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except MemoryWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/v1/memory/{memory_id}", response_model=DomainMemory)
    def update_memory(
        memory_id: str,
        request: MemoryUpdate,
        actor: Identity = Depends(identity),
    ) -> DomainMemory:
        if actor.role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            return memory_service.update(
                memory_id, request, subject_id=actor.subject_id, role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except MemoryWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/v1/memory/{memory_id}", response_model=DomainMemory)
    def delete_memory(memory_id: str, actor: Identity = Depends(identity)) -> DomainMemory:
        if actor.role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            return memory_service.delete(
                memory_id, subject_id=actor.subject_id, role=actor.role
            )
        except ContributionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        except VisibilityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return app


app = create_app()
