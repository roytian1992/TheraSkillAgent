"""Versioned contracts for the Contribution Hub."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from theraskill_agent.paths import active_release_id


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class ContributionType(StrEnum):
    TURN_OBSERVATION = "turn_observation"
    USER_FEEDBACK = "user_feedback"
    SESSION_FEEDBACK = "session_feedback"
    TURN_ANNOTATION = "turn_annotation"
    EXPERT_RESPONSE_REVIEW = "expert_response_review"
    CORRECTION_PROPOSAL = "correction_proposal"
    KNOWLEDGE_PROPOSAL = "knowledge_proposal"
    MEMORY_PROPOSAL = "memory_proposal"
    SYNTHETIC_CONTENT_REVIEW = "synthetic_content_review"
    SYSTEM_FAILURE_REPORT = "system_failure_report"


class CaptureTier(StrEnum):
    OPERATIONAL_ONLY = "operational_only"
    FEEDBACK_LINKABLE = "feedback_linkable"
    REVIEW_ALLOWED = "review_allowed"
    KNOWLEDGE_DERIVATION_ALLOWED = "knowledge_derivation_allowed"


CAPTURE_TIER_RANK = {
    CaptureTier.OPERATIONAL_ONLY: 0,
    CaptureTier.FEEDBACK_LINKABLE: 1,
    CaptureTier.REVIEW_ALLOWED: 2,
    CaptureTier.KNOWLEDGE_DERIVATION_ALLOWED: 3,
}


class PrivacyStatus(StrEnum):
    MINIMIZED = "minimized"
    PENDING = "pending"
    REDACTED = "redacted"
    DELETION_REQUESTED = "deletion_requested"
    DELETED = "deleted"


class WorkflowStatus(StrEnum):
    RECEIVED = "received"
    CONSENT_VALIDATED = "consent_validated"
    PRIVACY_PROCESSED = "privacy_processed"
    TRIAGED = "triaged"
    EXPERT_REVIEW = "expert_review"
    EXPERT_REVIEWED = "expert_reviewed"
    CANDIDATE_CREATED = "candidate_created"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class ConsentPurposes(StrictModel):
    service_delivery: bool = True
    product_analytics: bool = False
    content_review: bool = False
    research_use: bool = False
    knowledge_derivation: bool = False


class ConsentCreate(StrictModel):
    capture_tier: CaptureTier
    purposes: ConsentPurposes = Field(default_factory=ConsentPurposes)
    policy_version: str = "theraskill_consent_v1"
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def purposes_match_tier(self) -> "ConsentCreate":
        if self.capture_tier in {
            CaptureTier.REVIEW_ALLOWED,
            CaptureTier.KNOWLEDGE_DERIVATION_ALLOWED,
        } and not self.purposes.content_review:
            raise ValueError("review-capable capture tiers require content_review consent")
        if (
            self.capture_tier == CaptureTier.KNOWLEDGE_DERIVATION_ALLOWED
            and not self.purposes.knowledge_derivation
        ):
            raise ValueError("knowledge derivation tier requires knowledge_derivation consent")
        return self


class ConsentRecord(ConsentCreate):
    schema_version: str = "theraskill_consent_v1"
    consent_snapshot_id: str
    subject_id: str
    granted_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None

    def is_active(self, at: datetime | None = None) -> bool:
        current = at or utc_now()
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > current)


class ContributionOrigin(StrictModel):
    product: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    session_id: str | None = None
    turn_id: str | None = None
    response_trace_id: str | None = None


class Contributor(StrictModel):
    subject_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    expert_profile_id: str | None = None


class ContributionTarget(StrictModel):
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    target_version: str = "v1"


class OperationalMetadata(StrictModel):
    model_id: str | None = None
    prompt_release_id: str | None = None
    knowledge_release_id: str | None = None
    skill_ids: list[str] = Field(default_factory=list)
    retrieved_knowledge_unit_ids: list[str] = Field(default_factory=list)
    routing_result: dict[str, Any] = Field(default_factory=dict)
    policy_path: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    status: str | None = None
    error_code: str | None = None


class ContextSnapshotInput(StrictModel):
    user_message: str | None = None
    assistant_response: str | None = None
    bounded_conversation: list[dict[str, Any]] = Field(default_factory=list)
    operational: OperationalMetadata = Field(default_factory=OperationalMetadata)
    visibility_scope: str = "restricted_original"

    def contains_text(self) -> bool:
        return bool(self.user_message or self.assistant_response or self.bounded_conversation)


class ContextSnapshot(ContextSnapshotInput):
    schema_version: str = "theraskill_context_snapshot_v1"
    context_snapshot_id: str
    checksum_sha256: str
    capture_tier: CaptureTier
    subject_id: str
    created_at: datetime = Field(default_factory=utc_now)


class ContributionCapture(StrictModel):
    contribution_type: ContributionType
    origin: ContributionOrigin
    target: ContributionTarget
    payload: dict[str, Any] = Field(default_factory=dict)
    consent_snapshot_id: str
    context: ContextSnapshotInput | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class Contribution(StrictModel):
    schema_version: str = "theraskill_contribution_v1"
    contribution_id: str
    contribution_type: ContributionType
    origin: ContributionOrigin
    contributor: Contributor
    target: ContributionTarget
    payload: dict[str, Any]
    consent_snapshot_id: str
    context_snapshot_id: str | None = None
    privacy_status: PrivacyStatus
    workflow_status: WorkflowStatus
    created_at: datetime = Field(default_factory=utc_now)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ContributionEvent(StrictModel):
    schema_version: str = "theraskill_contribution_event_v1"
    event_id: str
    contribution_id: str
    sequence: int = Field(ge=1)
    event_type: str
    actor_subject_id: str
    actor_role: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ContributionView(StrictModel):
    contribution: Contribution
    context: ContextSnapshot | None = None
    events: list[ContributionEvent] = Field(default_factory=list)


class CaptureResult(StrictModel):
    contribution: Contribution
    replayed: bool = False


class ExpertProfileStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


class ExpertProfileCreate(StrictModel):
    subject_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    expertise_domains: list[str] = Field(min_length=1)
    credential_categories: list[str] = Field(default_factory=list)
    affiliation: str | None = None
    expires_at: datetime | None = None


class ExpertProfile(ExpertProfileCreate):
    schema_version: str = "theraskill_expert_profile_v1"
    expert_profile_id: str
    status: ExpertProfileStatus = ExpertProfileStatus.PENDING
    verified_by: str | None = None
    verified_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    def is_verified(self, at: datetime | None = None) -> bool:
        current = at or utc_now()
        return self.status == ExpertProfileStatus.VERIFIED and (
            self.expires_at is None or self.expires_at > current
        )


class ExpertProfileVerification(StrictModel):
    status: ExpertProfileStatus = ExpertProfileStatus.VERIFIED
    expires_at: datetime | None = None


class PrivacyDerivation(StrictModel):
    schema_version: str = "theraskill_privacy_derivation_v1"
    privacy_derivation_id: str
    contribution_id: str
    original_context_snapshot_id: str | None = None
    redacted_context_snapshot_id: str | None = None
    redacted_payload: dict[str, Any] = Field(default_factory=dict)
    processor_id: str = "theraskill_deterministic_redactor_v1"
    replacement_counts: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class IssueType(StrEnum):
    ACCEPTABLE = "acceptable"
    ROUTING_ERROR = "routing_error"
    RETRIEVAL_ERROR = "retrieval_error"
    KNOWLEDGE_ERROR = "knowledge_error"
    MISSING_KNOWLEDGE = "missing_knowledge"
    RESPONSE_GENERATION_ERROR = "response_generation_error"
    REVIEWER_ERROR = "reviewer_error"
    SCENARIO_ERROR = "scenario_error"
    SAFETY_BOUNDARY_ERROR = "safety_boundary_error"
    NOT_ENOUGH_CONTEXT = "not_enough_context"


class ReviewAction(StrEnum):
    RECORD_EVALUATION = "record_evaluation"
    PROPOSE_RESPONSE_REVISION = "propose_response_revision"
    CREATE_REGRESSION_CASE = "create_regression_case"
    PROPOSE_POLICY_CHANGE = "propose_policy_change"
    FLAG_KNOWLEDGE_UNIT = "flag_knowledge_unit"
    CREATE_KNOWLEDGE_CANDIDATE = "create_knowledge_candidate"
    ESCALATE_TO_ADJUDICATION = "escalate_to_adjudication"


class RemediationLane(StrEnum):
    PRODUCT = "product"
    ROUTING = "routing"
    RETRIEVAL = "retrieval"
    POLICY = "policy"
    KNOWLEDGE = "knowledge"
    REVIEWER = "reviewer"
    SCENARIO = "scenario"
    REGRESSION = "regression"


class ReviewTriageRequest(StrictModel):
    priority: int = Field(default=50, ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    remediation_lanes: list[RemediationLane] = Field(default_factory=list)


class ReviewTriage(ReviewTriageRequest):
    schema_version: str = "theraskill_review_triage_v1"
    triage_id: str
    contribution_id: str
    triaged_by: str
    created_at: datetime = Field(default_factory=utc_now)


class ReviewAssignment(StrictModel):
    schema_version: str = "theraskill_review_assignment_v1"
    assignment_id: str
    contribution_id: str
    expert_profile_id: str
    reviewer_subject_id: str
    status: str = "assigned"
    assigned_by: str
    conflict_declared: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class ReviewAssignmentCreate(StrictModel):
    expert_profile_id: str
    conflict_declared: bool = False


class ExpertDecisionRequest(StrictModel):
    assignment_id: str
    issue_types: list[IssueType] = Field(min_length=1)
    actions: list[ReviewAction] = Field(min_length=1)
    remediation_lanes: list[RemediationLane] = Field(default_factory=list)
    confidence: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=8000)
    corrected_response: str | None = Field(default=None, max_length=12000)
    regression_case: dict[str, Any] | None = None
    knowledge_candidate: dict[str, Any] | None = None
    conflict_of_interest: bool = False

    @model_validator(mode="after")
    def required_action_payloads(self) -> "ExpertDecisionRequest":
        actions = set(self.actions)
        if ReviewAction.PROPOSE_RESPONSE_REVISION in actions and not self.corrected_response:
            raise ValueError("propose_response_revision requires corrected_response")
        if ReviewAction.CREATE_REGRESSION_CASE in actions and self.regression_case is None:
            raise ValueError("create_regression_case requires regression_case")
        if ReviewAction.CREATE_KNOWLEDGE_CANDIDATE in actions and self.knowledge_candidate is None:
            raise ValueError("create_knowledge_candidate requires knowledge_candidate")
        if IssueType.ACCEPTABLE in self.issue_types and len(self.issue_types) != 1:
            raise ValueError("acceptable cannot be combined with error issue types")
        return self


class ExpertDecision(ExpertDecisionRequest):
    schema_version: str = "theraskill_expert_decision_v1"
    decision_id: str
    contribution_id: str
    expert_profile_id: str
    reviewer_subject_id: str
    created_at: datetime = Field(default_factory=utc_now)


class ReviewQueueItem(StrictModel):
    contribution_id: str
    contribution_type: ContributionType
    product: str
    target_type: str
    priority: int
    reasons: list[str]
    privacy_status: PrivacyStatus
    workflow_status: WorkflowStatus
    assignment: ReviewAssignment | None = None
    created_at: datetime


class ReviewWorkbench(StrictModel):
    contribution: Contribution
    response: dict[str, Any]
    context: dict[str, Any]
    evidence: dict[str, Any]
    machine_reviewer: dict[str, Any] | None = None
    triage: ReviewTriage | None = None
    assignment: ReviewAssignment | None = None
    decisions: list[ExpertDecision] = Field(default_factory=list)


class CorrectionProposal(StrictModel):
    schema_version: str = "theraskill_correction_proposal_v1"
    correction_proposal_id: str
    contribution_id: str
    decision_id: str
    corrected_response: str
    status: str = "expert_proposed"
    created_at: datetime = Field(default_factory=utc_now)


class RegressionCase(StrictModel):
    schema_version: str = "theraskill_regression_case_v1"
    regression_case_id: str
    contribution_id: str
    decision_id: str
    case: dict[str, Any]
    status: str = "proposed"
    created_at: datetime = Field(default_factory=utc_now)


class CandidateStatus(StrEnum):
    EXPERT_REVIEWED = "expert_reviewed_candidate"
    STRUCTURED = "structured"
    DEDUP_CHECKED = "dedup_checked"
    NEEDS_ADJUDICATION = "needs_adjudication"
    ADJUDICATED = "adjudicated"
    VALIDATED = "validated"
    RELEASE_CANDIDATE = "release_candidate"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class StructuredKnowledgeCandidate(StrictModel):
    schema_version: str = "theraskill_structured_candidate_v1"
    knowledge_type: str = Field(min_length=1)
    dedup_family: str = Field(
        pattern="^(style_response|qa_psychoeducation|clinical_risk_assessment|therapy_process|method_rubric|synthetic_construction)$"
    )
    canonical_label: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=8000)
    applicable_context: str = Field(min_length=1, max_length=4000)
    contraindications: list[str] = Field(default_factory=list, max_length=30)
    risk_level: str = Field(pattern="^(low|medium|high)$")
    evidence_source: str = Field(min_length=1)
    evidence_spans: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    source_evidence_tier: str = Field(min_length=1)
    language: str = Field(min_length=1)
    license_use_restrictions: list[str] = Field(default_factory=list)


class CandidateDedupResult(StrictModel):
    schema_version: str = "theraskill_candidate_dedup_result_v1"
    nearest_release_units: list[dict[str, Any]] = Field(default_factory=list)
    max_cosine_similarity: float | None = Field(default=None, ge=-1, le=1)
    llm_relation: str = Field(
        pattern="^(unique|duplicate|related_non_duplicate|conflict|insufficient_evidence)$"
    )
    disposition: str = Field(
        pattern="^(continue|reject_duplicate|needs_adjudication|quarantine)$"
    )
    rationale: str
    embedding_model: str
    llm_model: str
    checked_at: datetime = Field(default_factory=utc_now)


class CandidateAdjudicationRequest(StrictModel):
    decision: str = Field(pattern="^(approve|reject|quarantine)$")
    rationale: str = Field(min_length=1, max_length=8000)
    conflict_of_interest: bool = False


class CandidateAdjudication(StrictModel):
    schema_version: str = "theraskill_candidate_adjudication_v1"
    adjudication_id: str
    knowledge_candidate_id: str
    decision: str
    rationale: str
    adjudicator_subject_id: str
    expert_profile_id: str
    conflict_of_interest: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class CandidateRegressionValidation(StrictModel):
    schema_version: str = "theraskill_candidate_regression_validation_v1"
    validation_id: str
    knowledge_candidate_id: str
    status: str = Field(pattern="^(pass|fail)$")
    checks: dict[str, bool]
    cases: list[dict[str, Any]] = Field(default_factory=list)
    runner_version: str
    created_at: datetime = Field(default_factory=utc_now)


class KnowledgeCandidate(StrictModel):
    schema_version: str = "theraskill_knowledge_candidate_v1"
    knowledge_candidate_id: str
    contribution_id: str
    decision_id: str
    candidate: dict[str, Any]
    status: CandidateStatus = CandidateStatus.EXPERT_REVIEWED
    structured_candidate: StructuredKnowledgeCandidate | None = None
    structuring_provenance: dict[str, Any] = Field(default_factory=dict)
    dedup_result: CandidateDedupResult | None = None
    adjudication: CandidateAdjudication | None = None
    regression_validation: CandidateRegressionValidation | None = None
    production_release_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExpertDecisionResult(StrictModel):
    decision: ExpertDecision
    correction_proposal: CorrectionProposal | None = None
    regression_case: RegressionCase | None = None
    knowledge_candidate: KnowledgeCandidate | None = None


class ReviewPreparationResult(StrictModel):
    scanned: int
    privacy_processed: int
    triaged: int
    ineligible: int
    already_prepared: int


class MemoryType(StrEnum):
    COMMUNICATION_PREFERENCE = "communication_preference"
    USER_CONFIRMED_GOAL = "user_confirmed_goal"
    USER_CONFIRMED_CONTEXT = "user_confirmed_context"
    COPING_PREFERENCE = "coping_preference"
    EXPLICIT_BOUNDARY = "explicit_boundary"
    DO_NOT_REMEMBER = "do_not_remember"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    DELETED = "deleted"


class MemorySensitivity(StrEnum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"


class MemoryProposalCreate(StrictModel):
    memory_type: MemoryType
    content: str = Field(min_length=1, max_length=2000)
    source_session_id: str | None = None
    source_turn_ids: list[str] = Field(default_factory=list, max_length=20)
    consent_scope: str = "cross_session_support_continuity"
    sensitivity_level: MemorySensitivity = MemorySensitivity.STANDARD
    ttl_days: int = Field(default=90, ge=1, le=365)

    @model_validator(mode="after")
    def no_transcript_payload(self) -> "MemoryProposalCreate":
        if len(self.source_turn_ids) > 20:
            raise ValueError("memory proposal cannot reference more than 20 source turns")
        return self


class MemoryApproval(StrictModel):
    ttl_days: int | None = Field(default=None, ge=1, le=365)


class MemoryUpdate(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    ttl_days: int | None = Field(default=None, ge=1, le=365)
    sensitivity_level: MemorySensitivity | None = None

    @model_validator(mode="after")
    def has_change(self) -> "MemoryUpdate":
        if self.content is None and self.ttl_days is None and self.sensitivity_level is None:
            raise ValueError("at least one memory field must change")
        return self


class DomainMemory(StrictModel):
    schema_version: str = "theraskill_domain_memory_v1"
    memory_id: str
    subject_id: str
    memory_type: MemoryType
    content: str
    confirmation_status: MemoryStatus = MemoryStatus.PROPOSED
    source_session_id: str | None = None
    source_turn_ids: list[str] = Field(default_factory=list)
    consent_scope: str
    sensitivity_level: MemorySensitivity
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    last_confirmed_at: datetime | None = None
    supersedes_memory_id: str | None = None
    deletion_status: str = "retained"

    def is_active(self, at: datetime | None = None) -> bool:
        current = at or utc_now()
        return (
            self.confirmation_status == MemoryStatus.ACTIVE
            and self.deletion_status == "retained"
            and self.expires_at > current
        )


class MemoryEvent(StrictModel):
    schema_version: str = "theraskill_memory_event_v1"
    event_id: str
    memory_id: str
    sequence: int = Field(ge=1)
    event_type: str
    actor_subject_id: str
    actor_role: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class MemoryContextItem(StrictModel):
    memory_id: str
    memory_type: MemoryType
    content: str
    sensitivity_level: MemorySensitivity
    expires_at: datetime


class MemoryContext(StrictModel):
    schema_version: str = "theraskill_domain_memory_context_v1"
    subject_id: str
    items: list[MemoryContextItem] = Field(default_factory=list)
    context_text: str = ""
    truncated: bool = False
    generated_at: datetime = Field(default_factory=utc_now)


class CandidateDecision(StrictModel):
    schema_version: str = "theraskill_candidate_decision_v1"
    candidate_decision_id: str
    knowledge_candidate_id: str
    event_type: str
    from_status: CandidateStatus
    to_status: CandidateStatus
    actor_subject_id: str
    actor_role: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ReleasePreviewRequest(StrictModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=10000)
    release_notes: str = Field(default="", max_length=8000)


class KnowledgeRelease(StrictModel):
    schema_version: str = "theraskill_incremental_knowledge_release_v1"
    release_id: str
    base_release_id: str = Field(default_factory=active_release_id)
    candidate_ids: list[str]
    status: str = Field(pattern="^(preview|promoted|rolled_back|superseded)$")
    release_dir: str
    delta_units_path: str
    overlay_index_path: str
    manifest_path: str
    qa_path: str
    manifest_sha256: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    promoted_at: datetime | None = None
    rolled_back_at: datetime | None = None


class ReleasePromotionRequest(StrictModel):
    release_id: str = Field(min_length=1)
    expected_manifest_sha256: str = Field(min_length=64, max_length=64)


class ReleaseRollbackRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=4000)


class ReleaseFailureStage(StrEnum):
    INDEX_CANARY = "index_canary"
    RUNTIME_RETRIEVAL = "runtime_retrieval"
    RESPONSE_GENERATION = "response_generation"
    POLICY = "policy"
    ROLLOUT = "rollout"


class ReleaseFailureCreate(StrictModel):
    stage: ReleaseFailureStage
    error_code: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=500)
    affected_candidate_ids: list[str] = Field(default_factory=list, max_length=1000)
    affected_release_unit_ids: list[str] = Field(default_factory=list, max_length=1000)
    operational_details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def operational_details_contain_no_content(self) -> "ReleaseFailureCreate":
        forbidden = {
            "query", "message", "user_message", "assistant_response", "text",
            "transcript", "conversation", "prompt", "response_content",
        }

        def inspect(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() in forbidden:
                        raise ValueError(f"release failure details cannot contain content field: {key}")
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        inspect(self.operational_details)
        return self


class ReleaseFailure(ReleaseFailureCreate):
    schema_version: str = "theraskill_release_failure_v1"
    failure_id: str
    release_id: str
    status: str = Field(pattern="^(reported|replayed|closed)$")
    reported_by: str
    reported_role: str
    created_at: datetime = Field(default_factory=utc_now)
    replayed_at: datetime | None = None


class ReleaseFailureReplay(StrictModel):
    schema_version: str = "theraskill_release_failure_replay_v1"
    replay_id: str
    failure_id: str
    release_id: str
    status: str = Field(pattern="^(reproduced|not_reproduced|blocked)$")
    checks: dict[str, bool]
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    production_slot_mutated: bool = False
    replayed_by: str
    created_at: datetime = Field(default_factory=utc_now)


class OperationsSnapshot(StrictModel):
    schema_version: str = "theraskill_continuous_operations_snapshot_v1"
    snapshot_id: str
    scope: str
    reports: dict[str, Any]
    source_counts: dict[str, int]
    contains_conversation_text: bool = False
    artifact_path: str
    artifact_sha256: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
