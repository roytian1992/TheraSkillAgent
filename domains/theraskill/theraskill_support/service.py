"""Application service for asynchronous Support Assistant conversations."""

from __future__ import annotations

import uuid
from typing import Any

from .contracts import (
    GenerationState,
    GenerationStatus,
    JobStatus,
    MessageRole,
    SafetyAttentionLevel,
    SafetyHandoffStatus,
    SafetyState,
    SubmitMessageResult,
    SupportAuditEvent,
    SupportExport,
    SupportGoal,
    SupportMessage,
    SupportPublicSession,
    SupportRole,
    SupportSessionRecord,
    SupportSessionStatus,
    SupportTurnJob,
    SupportTurnTrace,
    utc_now,
)
from .engine import SupportEngine
from .safety import screen_safety_risk
from .repository import SQLiteSupportRepository, SupportSessionNotFoundError


class SupportOperationError(RuntimeError):
    pass


class SupportSessionService:
    def __init__(
        self,
        *,
        repository: SQLiteSupportRepository,
        engine: SupportEngine | None,
        knowledge_release_id: str,
        stale_job_seconds: float = 900.0,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self.knowledge_release_id = knowledge_release_id
        self.stale_job_seconds = max(30.0, float(stale_job_seconds))
        self.recover_pending_jobs()

    @staticmethod
    def _event(
        session_id: str,
        sequence: int,
        event_type: str,
        actor: SupportRole,
        payload: dict[str, Any] | None = None,
    ) -> SupportAuditEvent:
        return SupportAuditEvent(
            event_id=f"support_event_{uuid.uuid4().hex}",
            session_id=session_id,
            sequence=sequence,
            event_type=event_type,
            actor=actor,
            payload=payload or {},
        )

    @staticmethod
    def _attention_for_risk(risk_level: str, immediate: bool) -> SafetyAttentionLevel:
        if immediate or risk_level in {"high", "imminent"}:
            return SafetyAttentionLevel.URGENT
        if risk_level in {"ambiguous", "moderate"}:
            return SafetyAttentionLevel.ATTENTION
        return SafetyAttentionLevel.NONE

    def _safety_from_routing(self, routing) -> SafetyState:
        level = self._attention_for_risk(
            str(routing.risk_level), bool(routing.requires_immediate_safety_attention)
        )
        return SafetyState(
            attention_level=level,
            source_risk_level=str(routing.risk_level),
            signal_codes=[str(item) for item in (routing.safety_signal_codes or [])],
            handoff_status=(
                SafetyHandoffStatus.NOT_NEEDED
                if level == SafetyAttentionLevel.NONE
                else SafetyHandoffStatus.OFFERED
            ),
            updated_at=utc_now(),
        )

    def create_session(
        self, *, owner_id: str = "local-user", session_id: str | None = None,
        workflow: str = "support", output_language: str = "auto",
    ) -> SupportPublicSession:
        now = utc_now()
        public = SupportPublicSession(
            session_id=session_id or f"support_{uuid.uuid4().hex}",
            created_at=now,
            updated_at=now,
        )
        record = SupportSessionRecord(
            public=public,
            owner_id=owner_id,
            workflow=workflow,
            output_language=output_language,
            knowledge_release_id=self.knowledge_release_id,
        )
        self.repository.create(
            record,
            [self._event(public.session_id, 0, "session_created", SupportRole.USER)],
        )
        return public

    def get_public(self, session_id: str, *, owner_id: str) -> SupportPublicSession:
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        return record.public

    def list_public(
        self, *, owner_id: str = "local-user", limit: int = 100
    ) -> list[SupportPublicSession]:
        return [record.public for record in self.repository.list(owner_id, limit)]

    @staticmethod
    def _assert_owner(record: SupportSessionRecord, owner_id: str) -> None:
        if record.owner_id != owner_id:
            raise SupportSessionNotFoundError(record.public.session_id)

    def submit_message(
        self,
        session_id: str,
        *,
        text: str,
        owner_id: str,
        idempotency_key: str,
        output_language: str | None = None,
    ) -> SubmitMessageResult:
        existing_job_id = self.repository.idempotent_job_id(
            session_id, idempotency_key
        )
        if existing_job_id:
            current = self.repository.get(session_id)
            self._assert_owner(current, owner_id)
            return SubmitMessageResult(
                session=current.public,
                job_id=existing_job_id,
                response_pending=current.public.generation.status == GenerationStatus.PENDING,
            )
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        if record.public.status != SupportSessionStatus.ACTIVE:
            raise SupportOperationError("support session is not active")
        if record.public.generation.status == GenerationStatus.PENDING:
            raise SupportOperationError("an assistant response is still being generated")
        clean_text = " ".join(text.split())
        if not clean_text:
            raise ValueError("message text cannot be empty")
        if output_language is not None and output_language not in {"auto", "zh", "en"}:
            raise ValueError("Unsupported output language")
        now = utc_now()
        message = SupportMessage(
            message_id=f"support_message_{uuid.uuid4().hex}",
            sequence=len(record.public.messages) + 1,
            role=MessageRole.USER,
            text=clean_text,
            created_at=now,
        )
        job = SupportTurnJob(
            job_id=f"support_job_{uuid.uuid4().hex}",
            session_id=session_id,
            user_message_id=message.message_id,
            created_at=now,
            updated_at=now,
        )
        # Publish the existing fast screen before slow routing/retrieval starts.
        # This is a safety UI signal, not a substitute for model generation.
        screen = screen_safety_risk(clean_text if record.workflow == "support" else "")
        safety = record.public.safety
        level = self._attention_for_risk(screen.risk_level, screen.requires_immediate_attention)
        if level != SafetyAttentionLevel.NONE:
            attention_rank = {SafetyAttentionLevel.NONE: 0, SafetyAttentionLevel.ATTENTION: 1,
                              SafetyAttentionLevel.URGENT: 2}
            if attention_rank[level] < attention_rank[safety.attention_level]:
                level = safety.attention_level
                risk_level = safety.source_risk_level
            else:
                risk_level = screen.risk_level
            safety = safety.model_copy(update={
                "attention_level": level,
                "source_risk_level": risk_level,
                "signal_codes": list(dict.fromkeys([*safety.signal_codes, *screen.signal_codes])),
                "handoff_status": SafetyHandoffStatus.OFFERED,
                "updated_at": now,
            })
        public = record.public.model_copy(
            update={
                "messages": record.public.messages + [message],
                "generation": GenerationState(
                    status=GenerationStatus.PENDING,
                    job_id=job.job_id,
                ),
                "safety": safety,
                "updated_at": now,
            }
        )
        trace = SupportTurnTrace(
            job_id=job.job_id,
            user_message_id=message.message_id,
            knowledge_release_id=record.knowledge_release_id,
        )
        next_record = record.model_copy(
            update={
                "public": public,
                "output_language": output_language or record.output_language,
                "revision": record.revision + 1,
                "turn_traces": record.turn_traces + [trace],
            }
        )
        event = self._event(
            session_id,
            len(self.repository.list_events(session_id)),
            "user_message_submitted",
            SupportRole.USER,
            {"message_id": message.message_id, "job_id": job.job_id},
        )
        self.repository.submit_job(
            next_record,
            job,
            event,
            expected_revision=record.revision,
            idempotency_key=idempotency_key,
        )
        return SubmitMessageResult(session=public, job_id=job.job_id)

    def finalize_response(self, session_id: str, *, job_id: str) -> None:
        record = self.repository.get(session_id)
        job = self.repository.get_job(job_id)
        if job.session_id != session_id or job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
            return
        if job.status == JobStatus.PENDING:
            claimed = self.repository.claim_job(job_id)
            if claimed is None:
                return
            job = claimed
        if (
            record.public.generation.status != GenerationStatus.PENDING
            or record.public.generation.job_id != job_id
        ):
            return
        if self.engine is None:
            self._mark_failed(record, job, "engine_not_configured")
            return
        try:
            result = self.engine.generate(record, job_id)
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(record, job, f"{type(exc).__name__}: {exc}")
            return

        # Check-ins and safety actions may be saved while generation is running.
        record = self.repository.get(session_id)
        if record.public.generation.job_id != job_id or record.public.generation.status != GenerationStatus.PENDING:
            return
        routed_safety = self._safety_from_routing(result.routing)
        current_safety = record.public.safety
        attention_rank = {"none": 0, "attention": 1, "urgent": 2}
        if attention_rank[str(current_safety.attention_level)] >= attention_rank[str(routed_safety.attention_level)]:
            routed_safety = current_safety
        elif current_safety.check_in_at or current_safety.last_action_at:
            routed_safety = current_safety.model_copy(update={
                "attention_level": routed_safety.attention_level,
                "source_risk_level": routed_safety.source_risk_level,
                "signal_codes": list(dict.fromkeys([*current_safety.signal_codes, *routed_safety.signal_codes])),
            })
        now = utc_now()
        assistant = SupportMessage(
            message_id=f"support_message_{uuid.uuid4().hex}",
            sequence=len(record.public.messages) + 1,
            role=MessageRole.ASSISTANT,
            text=result.response_text,
            presentation_mode=result.presentation_mode,
            structured_content=result.structured_content,
            evidence_summary=result.evidence_summary,
            created_at=now,
        )
        first_user_text = next(
            (
                message.text
                for message in record.public.messages
                if message.role == MessageRole.USER
            ),
            "新对话",
        )
        title = record.public.title
        if title == "新对话":
            title = first_user_text[:28] + ("..." if len(first_user_text) > 28 else "")
        public = record.public.model_copy(
            update={
                "title": title,
                "messages": record.public.messages + [assistant],
                "generation": GenerationState(status=GenerationStatus.IDLE),
                "safety": routed_safety,
                "updated_at": now,
            }
        )
        traces = list(record.turn_traces)
        trace_index = next(
            index for index, trace in enumerate(traces) if trace.job_id == job_id
        )
        traces[trace_index] = traces[trace_index].model_copy(
            update={
                "assistant_message_id": assistant.message_id,
                "routing": result.routing,
                "retrieval_report_path": result.retrieval_report_path,
                "retrieved_unit_ids": result.retrieved_unit_ids,
                "retrieval_summary": result.retrieval_summary,
                "model_config_id": result.model_config_id,
                "prompt_release_id": result.prompt_release_id,
                "latency_ms": result.latency_ms,
                "history_compaction": result.history_compaction,
                "status": "complete",
            }
        )
        next_record = record.model_copy(
            update={
                "public": public,
                "revision": record.revision + 1,
                "model_config_id": result.model_config_id,
                "prompt_release_id": result.prompt_release_id,
                "conversation_summary": result.conversation_summary,
                "turn_traces": traces,
            }
        )
        next_job = job.model_copy(
            update={"status": JobStatus.COMPLETE, "updated_at": now, "error": None}
        )
        event = self._event(
            session_id,
            len(self.repository.list_events(session_id)),
            "assistant_message_generated",
            SupportRole.SYSTEM,
            {
                "job_id": job_id,
                "assistant_message_id": assistant.message_id,
                "presentation_mode": assistant.presentation_mode,
            },
        )
        self.repository.commit_job(
            next_record,
            next_job,
            event,
            expected_revision=record.revision,
        )

    def process_one_pending_job(self) -> bool:
        """Run one persisted job; safe for both startup recovery and polling workers."""
        jobs = self.repository.list_pending_jobs()
        if not jobs:
            return False
        job = jobs[0]
        self.finalize_response(job.session_id, job_id=job.job_id)
        return True

    def update_plan(
        self,
        session_id: str,
        *,
        owner_id: str,
        goals: list[str] | None = None,
        action_items: list[str] | None = None,
        next_check_in_at=None,
    ) -> SupportPublicSession:
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        current = record.public.plan
        now = utc_now()
        resolved_goals = current.goals
        if goals is not None:
            resolved_goals = [
                SupportGoal(
                    goal_id=f"goal_{uuid.uuid4().hex}",
                    text=" ".join(value.split()),
                    created_at=now,
                    updated_at=now,
                )
                for value in goals
                if " ".join(value.split())
            ][:10]
        resolved_actions = current.action_items if action_items is None else [
            " ".join(value.split()) for value in action_items if " ".join(value.split())
        ][:10]
        plan = current.model_copy(
            update={
                "goals": resolved_goals,
                "action_items": resolved_actions,
                "next_check_in_at": next_check_in_at,
            }
        )
        public = record.public.model_copy(update={"plan": plan, "updated_at": now})
        next_record = record.model_copy(update={"public": public, "revision": record.revision + 1})
        event = self._event(
            session_id,
            len(self.repository.list_events(session_id)),
            "support_plan_updated",
            SupportRole.USER,
            {"goal_count": len(plan.goals), "action_item_count": len(plan.action_items)},
        )
        self.repository.update(next_record, event, expected_revision=record.revision)
        return public

    def record_check_in(
        self,
        session_id: str,
        *,
        owner_id: str,
        safe_now: bool,
        note: str | None = None,
    ) -> SupportPublicSession:
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        now = utc_now()
        plan = record.public.plan.model_copy(
            update={"last_check_in_at": now, "last_check_in_note": (note or "").strip() or None}
        )
        safety = record.public.safety.model_copy(
            update={
                "check_in_status": "safe" if safe_now else "needs_support",
                "check_in_at": now,
                "updated_at": now,
            }
        )
        if not safe_now:
            safety = safety.model_copy(
                update={
                    "attention_level": SafetyAttentionLevel.URGENT,
                    "handoff_status": SafetyHandoffStatus.OFFERED,
                    "signal_codes": list(dict.fromkeys(
                        [*safety.signal_codes, "check_in_not_safe"]
                    )),
                    "source_risk_level": safety.source_risk_level
                    if safety.source_risk_level != "none_observed"
                    else "check_in_not_safe",
                }
            )
        public = record.public.model_copy(update={"plan": plan, "safety": safety, "updated_at": now})
        next_record = record.model_copy(update={"public": public, "revision": record.revision + 1})
        event = self._event(
            session_id,
            len(self.repository.list_events(session_id)),
            "safety_check_in_recorded",
            SupportRole.USER,
            {"safe_now": safe_now},
        )
        self.repository.update(next_record, event, expected_revision=record.revision)
        return public

    def record_safety_action(
        self,
        session_id: str,
        *,
        owner_id: str,
        action: str,
    ) -> SupportPublicSession:
        allowed = {"trusted_person", "professional", "local_emergency", "continue_checkin", "decline"}
        clean_action = action.strip()
        if clean_action not in allowed:
            raise ValueError(f"unsupported safety action: {clean_action}")
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        now = utc_now()
        status = SafetyHandoffStatus.DECLINED if clean_action == "decline" else SafetyHandoffStatus.ACCEPTED
        safety = record.public.safety.model_copy(
            update={
                "handoff_status": status,
                "last_action": clean_action,
                "last_action_at": now,
                "updated_at": now,
            }
        )
        public = record.public.model_copy(update={"safety": safety, "updated_at": now})
        next_record = record.model_copy(update={"public": public, "revision": record.revision + 1})
        event = self._event(
            session_id,
            len(self.repository.list_events(session_id)),
            "safety_handoff_action_recorded",
            SupportRole.USER,
            {"action": clean_action},
        )
        self.repository.update(next_record, event, expected_revision=record.revision)
        return public

    def _mark_failed(
        self, record: SupportSessionRecord, job: SupportTurnJob, error: str
    ) -> None:
        record = self.repository.get(record.public.session_id)
        if record.public.generation.job_id != job.job_id:
            return
        now = utc_now()
        public = record.public.model_copy(
            update={
                "generation": GenerationState(
                    status=GenerationStatus.FAILED,
                    job_id=job.job_id,
                    error_code="generation_failed",
                    retryable=True,
                ),
                "updated_at": now,
            }
        )
        traces = list(record.turn_traces)
        trace_index = next(
            index for index, trace in enumerate(traces) if trace.job_id == job.job_id
        )
        traces[trace_index] = traces[trace_index].model_copy(
            update={"status": "failed", "error": error[:2000]}
        )
        next_record = record.model_copy(
            update={
                "public": public,
                "revision": record.revision + 1,
                "turn_traces": traces,
            }
        )
        next_job = job.model_copy(
            update={
                "status": JobStatus.FAILED,
                "updated_at": now,
                "error": error[:2000],
            }
        )
        event = self._event(
            record.public.session_id,
            len(self.repository.list_events(record.public.session_id)),
            "assistant_generation_failed",
            SupportRole.SYSTEM,
            {"job_id": job.job_id, "error_code": "generation_failed"},
        )
        self.repository.commit_job(
            next_record,
            next_job,
            event,
            expected_revision=record.revision,
        )

    def recover_pending_jobs(self) -> int:
        """Mark jobs abandoned by a crashed process as retryable failures."""
        now = utc_now()
        recovered = 0
        for job in self.repository.list_active_jobs():
            if (now - job.updated_at).total_seconds() < self.stale_job_seconds:
                continue
            try:
                record = self.repository.get(job.session_id)
                if record.public.generation.job_id == job.job_id:
                    self._mark_failed(record, job, "generation_interrupted_by_restart")
                    recovered += 1
            except (KeyError, SupportSessionNotFoundError, SupportOperationError):
                continue
        return recovered

    def retry_response(
        self, session_id: str, *, owner_id: str,
        workflow: str | None = None, output_language: str | None = None,
    ) -> tuple[SupportPublicSession, str]:
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        generation = record.public.generation
        if generation.status != GenerationStatus.FAILED or not generation.job_id:
            raise SupportOperationError("there is no failed response to retry")
        job = self.repository.get_job(generation.job_id)
        now = utc_now()
        public = record.public.model_copy(
            update={
                "generation": GenerationState(
                    status=GenerationStatus.PENDING, job_id=job.job_id
                ),
                "updated_at": now,
            }
        )
        next_record = record.model_copy(
            update={"public": public, "revision": record.revision + 1,
                    "workflow": workflow or record.workflow,
                    "output_language": output_language or record.output_language}
        )
        next_job = job.model_copy(
            update={
                "status": JobStatus.PENDING,
                "attempts": job.attempts + 1,
                "updated_at": now,
                "error": None,
            }
        )
        event = self._event(
            session_id,
            len(self.repository.list_events(session_id)),
            "assistant_generation_retried",
            SupportRole.USER,
            {"job_id": job.job_id, "attempt": next_job.attempts},
        )
        self.repository.commit_job(
            next_record,
            next_job,
            event,
            expected_revision=record.revision,
        )
        return public, job.job_id

    def complete_session(
        self, session_id: str, *, owner_id: str
    ) -> SupportPublicSession:
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        if record.public.generation.status == GenerationStatus.PENDING:
            raise SupportOperationError("cannot complete while a response is pending")
        if record.public.status == SupportSessionStatus.COMPLETED:
            return record.public
        now = utc_now()
        public = record.public.model_copy(
            update={"status": SupportSessionStatus.COMPLETED, "updated_at": now}
        )
        next_record = record.model_copy(
            update={"public": public, "revision": record.revision + 1}
        )
        event = self._event(
            session_id,
            len(self.repository.list_events(session_id)),
            "session_completed",
            SupportRole.USER,
        )
        self.repository.update(next_record, event, expected_revision=record.revision)
        return public

    def delete_session(self, session_id: str, *, owner_id: str) -> None:
        record = self.repository.get(session_id)
        self._assert_owner(record, owner_id)
        if record.public.generation.status == GenerationStatus.PENDING:
            raise SupportOperationError("cannot delete while a response is pending")
        self.repository.delete(session_id)

    def export(
        self, session_id: str, *, owner_id: str, scope: str
    ) -> SupportExport:
        record = self.repository.get(session_id)
        if scope == "public":
            self._assert_owner(record, owner_id)
            return SupportExport(scope="public", public=record.public)
        if scope != "researcher":
            raise ValueError(f"unsupported export scope: {scope}")
        return SupportExport(
            scope="researcher",
            public=record.public,
            researcher={
                "owner_id": record.owner_id,
                "revision": record.revision,
                "knowledge_release_id": record.knowledge_release_id,
                "model_config_id": record.model_config_id,
                "prompt_release_id": record.prompt_release_id,
                "conversation_summary": record.conversation_summary,
                "turn_traces": [trace.model_dump(mode="json") for trace in record.turn_traces],
                "events": [
                    event.model_dump(mode="json")
                    for event in self.repository.list_events(session_id)
                ],
            },
        )
