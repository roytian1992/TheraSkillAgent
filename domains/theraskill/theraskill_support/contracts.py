"""Versioned contracts for user-facing support sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class SupportRole(StrEnum):
    USER = "user"
    RESEARCHER = "researcher"
    SYSTEM = "system"


class SupportSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class GenerationStatus(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    FAILED = "failed"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class SafetyAttentionLevel(StrEnum):
    NONE = "none"
    ATTENTION = "attention"
    URGENT = "urgent"


class SafetyHandoffStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    DECLINED = "declined"


class SafetyState(ContractModel):
    schema_version: str = "theraskill_support_safety_state_v1"
    attention_level: SafetyAttentionLevel = SafetyAttentionLevel.NONE
    source_risk_level: str = "none_observed"
    signal_codes: list[str] = Field(default_factory=list)
    handoff_status: SafetyHandoffStatus = SafetyHandoffStatus.NOT_NEEDED
    last_action: str | None = None
    last_action_at: datetime | None = None
    check_in_status: str = "not_requested"
    check_in_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class SupportGoal(ContractModel):
    goal_id: str
    text: str = Field(min_length=1, max_length=500)
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SupportPlan(ContractModel):
    schema_version: str = "theraskill_support_plan_v1"
    goals: list[SupportGoal] = Field(default_factory=list, max_length=10)
    action_items: list[str] = Field(default_factory=list, max_length=10)
    next_check_in_at: datetime | None = None
    last_check_in_at: datetime | None = None
    last_check_in_note: str | None = None


class SupportMessage(ContractModel):
    schema_version: str = "theraskill_support_message_v1"
    message_id: str
    sequence: int = Field(ge=1)
    role: MessageRole
    text: str = Field(min_length=1)
    presentation_mode: str = "conversation"
    structured_content: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class GenerationState(ContractModel):
    status: GenerationStatus = GenerationStatus.IDLE
    job_id: str | None = None
    error_code: str | None = None
    retryable: bool = False


class SupportPublicSession(ContractModel):
    schema_version: str = "theraskill_support_public_session_v1"
    session_id: str
    status: SupportSessionStatus = SupportSessionStatus.ACTIVE
    title: str = "新对话"
    messages: list[SupportMessage] = Field(default_factory=list)
    generation: GenerationState = Field(default_factory=GenerationState)
    safety: SafetyState = Field(default_factory=SafetyState)
    plan: SupportPlan = Field(default_factory=SupportPlan)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RoutingDecision(ContractModel):
    schema_version: str = "theraskill_support_routing_decision_v1"
    primary_skill: str
    secondary_skills: list[str] = Field(default_factory=list)
    task_type: str
    detected_language: str
    risk_level: str
    requires_immediate_safety_attention: bool = False
    safety_signal_codes: list[str] = Field(default_factory=list)
    routing_source: str = "llm"
    retrieval_query: str
    user_intent_summary: str


class SupportTurnTrace(ContractModel):
    schema_version: str = "theraskill_support_turn_trace_v1"
    job_id: str
    user_message_id: str
    assistant_message_id: str | None = None
    routing: RoutingDecision | None = None
    retrieval_report_path: str | None = None
    retrieved_unit_ids: list[str] = Field(default_factory=list)
    retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    model_config_id: str | None = None
    prompt_release_id: str | None = None
    knowledge_release_id: str
    latency_ms: dict[str, int] = Field(default_factory=dict)
    history_compaction: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    error: str | None = None


class SupportSessionRecord(ContractModel):
    schema_version: str = "theraskill_support_session_record_v1"
    public: SupportPublicSession
    owner_id: str = "local-user"
    workflow: str = "support"
    output_language: str = "auto"
    revision: int = Field(default=0, ge=0)
    knowledge_release_id: str
    model_config_id: str | None = None
    prompt_release_id: str | None = None
    conversation_summary: str | None = None
    summary_through_message: int = Field(default=0, ge=0)
    turn_traces: list[SupportTurnTrace] = Field(default_factory=list)


class SupportTurnJob(ContractModel):
    schema_version: str = "theraskill_support_turn_job_v1"
    job_id: str
    session_id: str
    user_message_id: str
    status: JobStatus = JobStatus.PENDING
    attempts: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    error: str | None = None


class SupportAuditEvent(ContractModel):
    schema_version: str = "theraskill_support_audit_event_v1"
    event_id: str
    session_id: str
    sequence: int = Field(ge=0)
    event_type: str
    actor: SupportRole
    created_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class SupportEngineResult(ContractModel):
    response_text: str = Field(min_length=1)
    presentation_mode: str = "conversation"
    structured_content: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: list[dict[str, str]] = Field(default_factory=list)
    routing: RoutingDecision
    retrieval_report_path: str
    retrieved_unit_ids: list[str] = Field(default_factory=list)
    retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    model_config_id: str
    prompt_release_id: str
    conversation_summary: str | None = None
    summary_through_message: int = Field(default=0, ge=0)
    latency_ms: dict[str, int] = Field(default_factory=dict)
    history_compaction: dict[str, Any] = Field(default_factory=dict)


class SubmitMessageResult(ContractModel):
    schema_version: str = "theraskill_support_submit_message_result_v1"
    session: SupportPublicSession
    job_id: str
    response_pending: bool = True


class SupportExport(ContractModel):
    schema_version: str = "theraskill_support_export_v1"
    scope: str
    public: SupportPublicSession
    researcher: dict[str, Any] | None = None

    @model_validator(mode="after")
    def researcher_scope_matches_payload(self) -> "SupportExport":
        if self.scope == "public" and self.researcher is not None:
            raise ValueError("public export cannot include researcher data")
        return self
