"""Versioned domain contracts for the simulation platform."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class SessionMode(StrEnum):
    HUMAN_THERAPIST_VS_PATIENT = "human_therapist_vs_patient"
    HUMAN_CLIENT_ROLE_VS_THERAPIST = "human_client_role_vs_therapist"
    DUAL_AGENT_RESEARCH = "dual_agent_research"
    REVIEW_ONLY = "review_only"


class SessionLanguage(StrEnum):
    ZH = "zh"
    EN = "en"
    MIXED = "mixed"


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"


class ActorRole(StrEnum):
    HUMAN_THERAPIST = "human_therapist"
    HUMAN_CLIENT_ROLE = "human_client_role"
    PATIENT_AGENT = "patient_agent"
    THERAPIST_AGENT = "therapist_agent"
    REVIEWER_AGENT = "reviewer_agent"
    INSTRUCTOR = "instructor"
    RESEARCHER = "researcher"
    SYSTEM = "system"


class EventType(StrEnum):
    SESSION_CREATED = "session_created"
    SCENARIO_INITIALIZED = "scenario_initialized"
    HUMAN_TURN_SUBMITTED = "human_turn_submitted"
    AGENT_TURN_GENERATED = "agent_turn_generated"
    PATIENT_STATE_UPDATED = "patient_state_updated"
    THERAPIST_MEMORY_UPDATED = "therapist_memory_updated"
    SESSION_PAUSED = "session_paused"
    SESSION_RESUMED = "session_resumed"
    SESSION_COMPLETED = "session_completed"
    SESSION_TERMINATED = "session_terminated"
    SESSION_FAILED = "session_failed"
    REVIEW_REQUESTED = "review_requested"
    REVIEW_GENERATED = "review_generated"
    LEGACY_ARTIFACT_IMPORTED = "legacy_artifact_imported"


class RoleAssignment(ContractModel):
    human_roles: list[ActorRole] = Field(default_factory=list)
    agent_roles: list[ActorRole] = Field(default_factory=list)
    reviewer_enabled: bool = True

    @model_validator(mode="after")
    def roles_do_not_overlap(self) -> "RoleAssignment":
        overlap = set(self.human_roles) & set(self.agent_roles)
        if overlap:
            raise ValueError(f"roles cannot be both human and agent controlled: {sorted(overlap)}")
        return self


class ArtifactRef(ContractModel):
    path: str
    sha256: str | None = None
    schema_version: str | None = None
    media_type: str = "application/json"


class ScenarioPublicView(ContractModel):
    scenario_id: str
    scenario_family: str
    case_id: str | None = None
    case_family: str | None = None
    difficulty_band: str | None = None
    risk_band: str | None = None
    scenario_brief: str | None = None
    scenario_tags: list[str] = Field(default_factory=list)
    recommended_turn_budget: int | None = None
    split_labels: dict[str, str] = Field(default_factory=dict)
    learner_role_card: dict[str, Any] = Field(default_factory=dict)


class TranscriptTurn(ContractModel):
    turn_id: str
    turn_index: int = Field(ge=0)
    role: str
    text: str
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewPublicView(ContractModel):
    status: str
    gate_status: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    artifact_ref: ArtifactRef | None = None


class PublicSessionView(ContractModel):
    schema_version: str = "theraskill_simulation_public_session_view_v1"
    session_id: str
    mode: SessionMode
    language: SessionLanguage = SessionLanguage.ZH
    status: SessionStatus
    scenario: ScenarioPublicView
    current_turn_index: int = Field(ge=0)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    progress: dict[str, Any] = Field(default_factory=dict)
    review: ReviewPublicView | None = None
    created_at: datetime
    updated_at: datetime


class PrivatePatientState(ContractModel):
    schema_version: str = "theraskill_simulation_private_patient_state_v1"
    latent_concerns: list[str] = Field(default_factory=list)
    withheld_information: list[str] = Field(default_factory=list)
    disclosure_conditions: list[str] = Field(default_factory=list)
    trust_alliance_readiness: dict[str, Any] = Field(default_factory=dict)
    rupture_sensitivity: list[str] = Field(default_factory=list)
    risk_signals_not_yet_disclosed: list[str] = Field(default_factory=list)
    runtime_state: dict[str, Any] = Field(default_factory=dict)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    evidence_unit_ids: list[str] = Field(default_factory=list)
    persona_id: str | None = None
    persona_schema_version: str | None = None
    disclosed_fact_ids: list[str] = Field(default_factory=list)


class PrivateTherapistState(ContractModel):
    schema_version: str = "theraskill_simulation_private_therapist_state_v1"
    case_formulation_memory: dict[str, Any] = Field(default_factory=dict)
    candidate_strategies: list[dict[str, Any]] = Field(default_factory=list)
    selected_strategy: dict[str, Any] = Field(default_factory=dict)
    retrieved_policy_evidence: list[str] = Field(default_factory=list)
    internal_planning: dict[str, Any] = Field(default_factory=dict)


class SimulationSession(ContractModel):
    schema_version: str = "theraskill_simulation_session_v1"
    session_id: str
    mode: SessionMode
    language: SessionLanguage = SessionLanguage.ZH
    status: SessionStatus = SessionStatus.CREATED
    scenario: ScenarioPublicView
    role_assignment: RoleAssignment
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    current_turn_index: int = Field(default=0, ge=0)
    public_state_ref: str | None = None
    private_state_ref: str | None = None
    event_log_ref: str | None = None
    review_ref: str | None = None
    runtime_version: str
    knowledge_release_id: str
    prompt_release_id: str | None = None
    model_config_id: str | None = None
    source_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def mode_matches_roles(self) -> "SimulationSession":
        human = set(self.role_assignment.human_roles)
        agents = set(self.role_assignment.agent_roles)
        requirements = {
            SessionMode.HUMAN_THERAPIST_VS_PATIENT: (
                {ActorRole.HUMAN_THERAPIST},
                {ActorRole.PATIENT_AGENT},
            ),
            SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST: (
                {ActorRole.HUMAN_CLIENT_ROLE},
                {ActorRole.THERAPIST_AGENT},
            ),
            SessionMode.DUAL_AGENT_RESEARCH: (
                set(),
                {ActorRole.PATIENT_AGENT, ActorRole.THERAPIST_AGENT},
            ),
            SessionMode.REVIEW_ONLY: (set(), {ActorRole.REVIEWER_AGENT}),
        }
        needed_human, needed_agents = requirements[SessionMode(self.mode)]
        if not needed_human.issubset(human) or not needed_agents.issubset(agents):
            raise ValueError(f"role assignment does not satisfy mode={self.mode}")
        return self


class SessionEvent(ContractModel):
    schema_version: str = "theraskill_simulation_session_event_v1"
    event_id: str
    session_id: str
    sequence: int = Field(ge=0)
    event_type: EventType
    actor: ActorRole
    created_at: datetime = Field(default_factory=utc_now)
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    prior_state_checksum: str | None = None
    resulting_state_checksum: str | None = None
    model_config_id: str | None = None
    prompt_release_id: str | None = None
    retrieved_unit_ids: list[str] = Field(default_factory=list)


class SessionSnapshot(ContractModel):
    schema_version: str = "theraskill_simulation_session_snapshot_v1"
    snapshot_id: str
    session: SimulationSession
    through_event_sequence: int = Field(ge=0)
    public_view: PublicSessionView
    private_patient_state: PrivatePatientState | None = None
    private_therapist_state: PrivateTherapistState | None = None
    created_at: datetime = Field(default_factory=utc_now)
    checksum: str | None = None


class SessionFeedback(ContractModel):
    schema_version: str = "theraskill_simulation_session_feedback_v1"
    feedback_id: str
    session_id: str
    submitted_by_role: ActorRole
    rating: int | None = Field(default=None, ge=1, le=5)
    dimension_ratings: dict[str, int] = Field(default_factory=dict)
    comment: str | None = None
    consent_to_research: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dimension_ratings(self) -> "SessionFeedback":
        invalid = {
            key: value
            for key, value in self.dimension_ratings.items()
            if value < 1 or value > 5
        }
        if invalid:
            raise ValueError(f"dimension ratings must be between 1 and 5: {invalid}")
        if self.rating is None and not self.dimension_ratings and not (self.comment or "").strip():
            raise ValueError("feedback requires a rating, dimension rating, or comment")
        return self
