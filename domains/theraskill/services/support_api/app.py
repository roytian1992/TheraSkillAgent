"""FastAPI transport over the Support Assistant session service."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from datetime import datetime

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from domains.theraskill.theraskill_support.engine import LLMSupportEngine, SupportEngine
from domains.theraskill.theraskill_support.repository import (
    SQLiteSupportRepository,
    SupportSessionConflictError,
    SupportSessionNotFoundError,
)
from domains.theraskill.theraskill_support.service import SupportOperationError, SupportSessionService
from domains.theraskill.theraskill_contribution.client import (
    ContributionClient,
    ContributionClientError,
    HTTPContributionClient,
)
from domains.theraskill.runtime import runtime_root
from theraskill_agent.paths import active_release_id
from domains.theraskill.observability import RequestMetrics
from domains.theraskill.metrics_middleware import MetricsMiddleware
from domains.theraskill.knowledge_editor import KnowledgeEditor, KnowledgeRevisionConflict, KnowledgeUnitNotFound
from theraskill_agent import resource_jobs


from theraskill_agent.paths import resource_root, resource_path

PROJECT_ROOT = resource_root()
DEFAULT_KNOWLEDGE_RELEASE_ID = active_release_id()


class SupportAPISettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    database_path: Path = Field(default_factory=lambda: runtime_root() / "support/sessions.sqlite")
    extraction_config_path: Path = resource_path("configs/extraction_config.yaml")
    model_config_path: Path = resource_path("configs/theraskill/retrieval.yaml")
    work_root: Path = Field(default_factory=lambda: runtime_root() / "support/runtime")
    knowledge_release_id: str = DEFAULT_KNOWLEDGE_RELEASE_ID
    stale_job_seconds: float = Field(default=900.0, ge=30.0, le=86400.0)
    worker_enabled: bool = True
    worker_poll_seconds: float = Field(default=0.5, ge=0.05, le=30.0)
    safety_resources: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    safety_resources_path: Path | None = Field(
        default_factory=lambda: (
            Path(value) if (value := os.environ.get("THERASKILL_SAFETY_RESOURCES_PATH")) else None
        )
    )
    initialize_llm_engine: bool = True
    api_token: str | None = None
    contribution_api_url: str | None = Field(
        default_factory=lambda: os.environ.get(
            "THERASKILL_CONTRIBUTION_API_URL", "inprocess://contribution"
        )
    )
    contribution_api_token: str = Field(
        default_factory=lambda: os.environ.get("THERASKILL_CONTRIBUTION_API_TOKEN", "")
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5174", "http://localhost:5174"]
    )


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    workflow: Literal["support", "response_review", "synthetic_construction"] = "support"
    output_language: Literal["auto", "zh", "en"] = "auto"


class SubmitMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_language: Literal["auto", "zh", "en"] | None = None
    text: str = Field(min_length=1, max_length=12000)


class RetryResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow: Literal["support", "response_review", "synthetic_construction"] | None = None
    output_language: Literal["auto", "zh", "en"] | None = None


class ContributionConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_review: bool = False
    research_use: bool = False
    knowledge_derivation: bool = False


class SupportFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    helpful: bool
    reasons: list[
        Literal[
            "not_understood",
            "not_relevant",
            "not_practical",
            "uncomfortable_expression",
            "possibly_inaccurate",
            "risk_handling_inappropriate",
            "other",
        ]
    ] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=2000)
    consent: ContributionConsentRequest = Field(default_factory=ContributionConsentRequest)


class SupportMemoryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_type: Literal[
        "communication_preference", "user_confirmed_goal", "user_confirmed_context",
        "coping_preference", "explicit_boundary", "do_not_remember",
    ]
    content: str = Field(min_length=1, max_length=2000)
    source_session_id: str | None = None
    source_turn_ids: list[str] = Field(default_factory=list, max_length=20)
    sensitivity_level: Literal["standard", "sensitive"] = "standard"
    ttl_days: int = Field(default=90, ge=1, le=365)


class SupportMemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    ttl_days: int | None = Field(default=None, ge=1, le=365)
    sensitivity_level: Literal["standard", "sensitive"] | None = None


class SupportPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goals: list[str] | None = Field(default=None, max_length=10)
    action_items: list[str] | None = Field(default=None, max_length=10)
    next_check_in_at: datetime | None = None


class KnowledgeUnitUpdateRequest(BaseModel):
    """A revision-guarded correction; the release itself remains immutable."""
    model_config = ConfigDict(extra="forbid")
    expected_revision: int | None = Field(default=None, ge=0)
    expected_index_id: str | None = Field(default=None, min_length=1, max_length=128)
    canonical_label: str | None = Field(default=None, min_length=1, max_length=100000)
    summary: str | None = Field(default=None, max_length=100000)
    applicable_context: str | list[str] | None = Field(default=None)
    evidence_source: str | list[str] | None = Field(default=None)
    allowed_use: str | list[str] | None = Field(default=None)
    forbidden_use: str | list[str] | None = Field(default=None)
    contraindications: str | list[str] | None = Field(default=None)


class KnowledgeUnitReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int | None = Field(default=None, ge=0)
    expected_index_id: str | None = Field(default=None, min_length=1, max_length=128)


class SafetyActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["trusted_person", "professional", "local_emergency", "continue_checkin", "decline"]


class SafetyCheckInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    safe_now: bool
    note: str | None = Field(default=None, max_length=1000)


def create_app(
    settings: SupportAPISettings | None = None,
    *,
    service_override: SupportSessionService | None = None,
    engine_override: SupportEngine | None = None,
    contribution_client_override: ContributionClient | None = None,
) -> FastAPI:
    resolved = settings or SupportAPISettings()
    if resolved.safety_resources_path and resolved.safety_resources_path.exists():
        try:
            payload = json.loads(resolved.safety_resources_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid TheraSkill safety resources file: {resolved.safety_resources_path}"
            ) from exc
        if not isinstance(payload, dict) or any(
            not isinstance(region, str) or not isinstance(entries, list)
            or any(not isinstance(entry, dict) for entry in entries)
            for region, entries in payload.items()
        ):
            raise ValueError(
                "safety resources file must be a JSON object mapping region names to object lists"
            )
        resolved = resolved.model_copy(
            update={
                "safety_resources": {
                    **resolved.safety_resources,
                    **payload,
                }
            }
        )
    contribution_client = contribution_client_override
    if contribution_client is None and resolved.contribution_api_url:
        contribution_client = HTTPContributionClient(
            resolved.contribution_api_url,
            api_token=resolved.contribution_api_token,
        )
    if service_override is not None:
        service = service_override
    else:
        engine: SupportEngine | None = engine_override
        if engine is None and resolved.initialize_llm_engine:
            engine = LLMSupportEngine(
                project_root=PROJECT_ROOT,
                extraction_config=resolved.extraction_config_path,
                model_config=resolved.model_config_path,
                work_root=resolved.work_root,
                knowledge_release_id=resolved.knowledge_release_id,
                memory_provider=(
                    lambda subject_id: contribution_client.memory_context(
                        subject_id=subject_id,
                        role="user",
                        include_sensitive=False,
                        max_chars=3000,
                    )
                    if contribution_client is not None
                    else {}
                ),
            )
        service = SupportSessionService(
            repository=SQLiteSupportRepository(resolved.database_path),
            engine=engine,
            knowledge_release_id=resolved.knowledge_release_id,
            stale_job_seconds=resolved.stale_job_seconds,
        )
    async def worker_loop() -> None:
        while True:
            try:
                processed = await asyncio.to_thread(service.process_one_pending_job)
            except asyncio.CancelledError:
                raise
            except Exception:
                processed = False
            if not processed:
                await asyncio.sleep(resolved.worker_poll_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if resolved.worker_enabled and service.engine is not None:
            task = asyncio.create_task(worker_loop())
            app.state.support_worker_task = task
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(
        title="TheraSkill Support Assistant API",
        version="1.0.0",
        description="Stateful, retrieval-grounded psychological support product API.",
        lifespan=lifespan,
    )
    metrics = RequestMetrics("theraskill-support")
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def identity(
        x_support_user: str = Header(default="local-user", alias="X-Support-User"),
        x_support_role: str = Header(default="user", alias="X-Support-Role"),
        authorization: str | None = Header(default=None),
    ) -> tuple[str, str]:
        if resolved.api_token:
            expected = f"Bearer {resolved.api_token}"
            if authorization != expected:
                raise HTTPException(status_code=401, detail="invalid API token")
        if x_support_role not in {"user", "researcher", "system"}:
            raise HTTPException(status_code=403, detail="unsupported support role")
        return x_support_user.strip() or "local-user", x_support_role

    @app.exception_handler(SupportSessionNotFoundError)
    def not_found_handler(_request, exc: SupportSessionNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(SupportSessionConflictError)
    def conflict_handler(_request, exc: SupportSessionConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SupportOperationError)
    def operation_handler(_request, exc: SupportOperationError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "theraskill-support", "version": app.version}

    @app.get("/safety/resources")
    def safety_resources(
        region: str = Query(default="default", min_length=1, max_length=100),
        _actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        configured = resolved.safety_resources.get(region) or resolved.safety_resources.get("default") or []
        return {
            "region": region,
            "resources": configured,
            "notice": "请使用当地官方急救、危机热线或专业服务；系统不会根据对话临时编造联系方式。",
        }

    @app.get("/metrics")
    def metrics_endpoint() -> dict[str, Any]:
        return metrics.snapshot()

    @app.get("/runtime-config")
    def runtime_config(
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        _owner, role = actor
        if role not in {"researcher", "system"}:
            raise HTTPException(status_code=403, detail="researcher role is required")
        return {
            "database_path": str(resolved.database_path),
            "model_config_path": str(resolved.model_config_path),
            "knowledge_release_id": resolved.knowledge_release_id,
            "llm_engine_enabled": service.engine is not None,
        }

    @app.get("/knowledge/{unit_id}")
    def get_knowledge_unit(
        unit_id: str,
        index_id: str | None = Query(default=None, min_length=1, max_length=128),
        _actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        """Return source fields and a separately labelled indexed passage."""
        try:
            return KnowledgeEditor().get(unit_id, index_id=index_id)
        except KnowledgeRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KnowledgeUnitNotFound as exc:
            raise HTTPException(status_code=404, detail="knowledge unit not found") from exc
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.patch("/knowledge/{unit_id}")
    def update_knowledge_unit(
        unit_id: str,
        request: KnowledgeUnitUpdateRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        _owner, role = actor
        if role not in {"user", "researcher", "system"}:
            raise HTTPException(status_code=403, detail="support knowledge editing is unavailable for this role")
        if request.expected_revision is None or not request.expected_index_id:
            raise HTTPException(status_code=428, detail="expected_revision and expected_index_id are required")
        try:
            values = request.model_dump(mode="json", exclude_none=True)
            values.pop("expected_revision", None); values.pop("expected_index_id", None)
            return KnowledgeEditor().update(unit_id, values, expected_revision=request.expected_revision, expected_index_id=request.expected_index_id)
        except KnowledgeRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KnowledgeUnitNotFound as exc:
            raise HTTPException(status_code=404, detail="knowledge unit not found") from exc
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/knowledge/{unit_id}/reindex", status_code=202)
    def reindex_knowledge_unit(
        unit_id: str,
        request: KnowledgeUnitReindexRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        _owner, role = actor
        if role not in {"user", "researcher", "system"}:
            raise HTTPException(status_code=403, detail="support knowledge reindexing is unavailable for this role")
        if request.expected_revision is None or not request.expected_index_id:
            raise HTTPException(status_code=428, detail="expected_revision and expected_index_id are required")
        try:
            return KnowledgeEditor().submit_reindex(unit_id, expected_revision=request.expected_revision, expected_index_id=request.expected_index_id)
        except KnowledgeRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KnowledgeUnitNotFound as exc:
            raise HTTPException(status_code=404, detail="knowledge unit not found") from exc
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/sessions", status_code=201)
    def create_session(
        request: CreateSessionRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, _role = actor
        return service.create_session(
            owner_id=owner, session_id=request.session_id,
            workflow=request.workflow, output_language=request.output_language,
        ).model_dump(mode="json")

    @app.get("/sessions")
    def list_sessions(
        actor: tuple[str, str] = Depends(identity),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        owner, _role = actor
        sessions = service.list_public(owner_id=owner, limit=limit)
        return {"count": len(sessions), "sessions": [row.model_dump(mode="json") for row in sessions]}

    @app.get("/session-statuses")
    def session_statuses(
        actor: tuple[str, str] = Depends(identity),
        session_id: list[str] = Query(default=[], max_length=500),
    ) -> dict[str, Any]:
        owner, _role = actor
        return {"sessions": service.repository.statuses(owner, session_id)}

    @app.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, _role = actor
        return service.get_public(session_id, owner_id=owner).model_dump(mode="json")

    @app.post("/sessions/{session_id}/messages", status_code=202)
    def submit_message(
        session_id: str,
        request: SubmitMessageRequest,
        background_tasks: BackgroundTasks,
        actor: tuple[str, str] = Depends(identity),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        result = service.submit_message(
            session_id,
            text=request.text,
            owner_id=owner,
            idempotency_key=idempotency_key,
            output_language=request.output_language,
        )
        if result.response_pending:
            background_tasks.add_task(
                service.finalize_response, session_id, job_id=result.job_id
            )
        return result.model_dump(mode="json")

    def require_contribution_client() -> ContributionClient:
        if contribution_client is None:
            raise HTTPException(status_code=503, detail="Contribution Hub is not configured")
        return contribution_client

    def contribution_error(exc: ContributionClientError) -> HTTPException:
        status = exc.status_code if exc.status_code in {400, 403, 404, 409, 422} else 503
        return HTTPException(status_code=status, detail=f"Contribution Hub: {exc}")

    @app.post("/sessions/{session_id}/messages/{message_id}/feedback", status_code=201)
    def submit_response_feedback(
        session_id: str,
        message_id: str,
        request: SupportFeedbackRequest,
        actor: tuple[str, str] = Depends(identity),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        record = service.repository.get(session_id)
        service._assert_owner(record, owner)
        target = next(
            (message for message in record.public.messages if message.message_id == message_id),
            None,
        )
        if target is None or str(target.role) != "assistant":
            raise HTTPException(status_code=404, detail="assistant response not found")
        prior_user = next(
            (
                message
                for message in reversed(record.public.messages)
                if message.sequence < target.sequence and str(message.role) == "user"
            ),
            None,
        )
        trace = next(
            (item for item in record.turn_traces if item.assistant_message_id == message_id),
            None,
        )
        content_review = request.consent.content_review or request.consent.knowledge_derivation
        if request.consent.knowledge_derivation:
            tier = "knowledge_derivation_allowed"
        elif content_review:
            tier = "review_allowed"
        else:
            tier = "feedback_linkable"
        purposes = {
            "service_delivery": True,
            "product_analytics": True,
            "content_review": content_review,
            "research_use": request.consent.research_use,
            "knowledge_derivation": request.consent.knowledge_derivation,
        }
        context: dict[str, Any] = {
            "operational": {
                "model_id": trace.model_config_id if trace else record.model_config_id,
                "prompt_release_id": trace.prompt_release_id if trace else record.prompt_release_id,
                "knowledge_release_id": record.knowledge_release_id,
                "skill_ids": [trace.routing.primary_skill] if trace and trace.routing else [],
                "retrieved_knowledge_unit_ids": trace.retrieved_unit_ids if trace else [],
                "routing_result": trace.routing.model_dump(mode="json") if trace and trace.routing else {},
                "latency_ms": sum((trace.latency_ms or {}).values()) if trace else None,
                "status": trace.status if trace else "complete",
            }
        }
        if content_review:
            context.update(
                {
                    "user_message": prior_user.text if prior_user else None,
                    "assistant_response": target.text,
                    "bounded_conversation": [
                        {"role": str(message.role), "content": message.text, "message_id": message.message_id}
                        for message in record.public.messages
                        if message.sequence <= target.sequence
                    ][-8:],
                }
            )
        client = require_contribution_client()
        try:
            consent = client.create_consent(
                subject_id=owner,
                role="user",
                capture_tier=tier,
                purposes=purposes,
                idempotency_key=f"support-consent:{owner}:{idempotency_key}",
            )
            captured = client.capture(
                subject_id=owner,
                role="user",
                endpoint="/v1/feedback/response",
                idempotency_key=f"support-feedback:{owner}:{idempotency_key}",
                payload={
                    "contribution_type": "user_feedback",
                    "origin": {
                        "product": "support_assistant",
                        "platform": "web",
                        "session_id": session_id,
                        "turn_id": prior_user.message_id if prior_user else None,
                        "response_trace_id": trace.job_id if trace else None,
                    },
                    "target": {"target_type": "response", "target_id": message_id, "target_version": "v1"},
                    "payload": {
                        "helpful": request.helpful,
                        "reasons": request.reasons,
                        "comment": (request.comment or "").strip() or None,
                    },
                    "consent_snapshot_id": consent["consent_snapshot_id"],
                    "context": context,
                    "provenance": {"source_service": "support_api_v1"},
                },
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc
        return captured

    @app.get("/contributions")
    def list_user_contributions(
        actor: tuple[str, str] = Depends(identity),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            return require_contribution_client().list_contributions(
                subject_id=owner, role="user", limit=limit
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.delete("/contributions/{contribution_id}")
    def withdraw_user_contribution(
        contribution_id: str,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, _role = actor
        try:
            return require_contribution_client().withdraw(
                contribution_id=contribution_id, subject_id=owner, role="user"
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.post("/memory/proposals", status_code=201)
    def propose_domain_memory(
        request: SupportMemoryProposalRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            return require_contribution_client().propose_memory(
                subject_id=owner,
                role="user",
                payload={
                    **request.model_dump(mode="json"),
                    "consent_scope": "cross_session_support_continuity",
                },
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.get("/memory")
    def list_domain_memory(
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            items = require_contribution_client().list_memories(
                subject_id=owner, role="user"
            )
            return {"count": len(items), "memories": items}
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.post("/memory/{memory_id}/approve")
    def approve_domain_memory(
        memory_id: str,
        ttl_days: int | None = Query(default=None, ge=1, le=365),
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            return require_contribution_client().approve_memory(
                memory_id=memory_id,
                subject_id=owner,
                role="user",
                payload={"ttl_days": ttl_days},
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.patch("/memory/{memory_id}")
    def update_domain_memory(
        memory_id: str,
        request: SupportMemoryUpdateRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        if not request.model_dump(exclude_none=True):
            raise HTTPException(status_code=422, detail="at least one memory field must change")
        try:
            return require_contribution_client().update_memory(
                memory_id=memory_id,
                subject_id=owner,
                role="user",
                payload=request.model_dump(mode="json", exclude_none=True),
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.delete("/memory/{memory_id}")
    def delete_domain_memory(
        memory_id: str,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, actor_role = actor
        if actor_role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        try:
            return require_contribution_client().delete_memory(
                memory_id=memory_id, subject_id=owner, role="user"
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.post("/sessions/{session_id}/retry", status_code=202)
    def retry_response(
        session_id: str,
        background_tasks: BackgroundTasks,
        request: RetryResponseRequest | None = None,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, _role = actor
        public, job_id = service.retry_response(session_id, owner_id=owner,
            workflow=request.workflow if request else None,
            output_language=request.output_language if request else None)
        background_tasks.add_task(service.finalize_response, session_id, job_id=job_id)
        return {"session": public.model_dump(mode="json"), "job_id": job_id, "response_pending": True}

    @app.patch("/sessions/{session_id}/plan")
    def update_plan(
        session_id: str,
        request: SupportPlanRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, role = actor
        if role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        return service.update_plan(
            session_id,
            owner_id=owner,
            goals=request.goals,
            action_items=request.action_items,
            next_check_in_at=request.next_check_in_at,
        ).model_dump(mode="json")

    @app.post("/sessions/{session_id}/safety/action")
    def safety_action(
        session_id: str,
        request: SafetyActionRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, role = actor
        if role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        return service.record_safety_action(
            session_id, owner_id=owner, action=request.action
        ).model_dump(mode="json")

    @app.post("/sessions/{session_id}/safety/check-in")
    def safety_check_in(
        session_id: str,
        request: SafetyCheckInRequest,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, role = actor
        if role != "user":
            raise HTTPException(status_code=403, detail="user role is required")
        return service.record_check_in(
            session_id,
            owner_id=owner,
            safe_now=request.safe_now,
            note=request.note,
        ).model_dump(mode="json")

    @app.post("/sessions/{session_id}/complete")
    def complete_session(
        session_id: str,
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, _role = actor
        return service.complete_session(session_id, owner_id=owner).model_dump(mode="json")

    @app.delete("/sessions/{session_id}")
    def delete_session(
        session_id: str,
        actor: tuple[str, str] = Depends(identity),
    ) -> Response:
        owner, _role = actor
        service.delete_session(session_id, owner_id=owner)
        return Response(status_code=204)

    @app.get("/sessions/{session_id}/export")
    def export_session(
        session_id: str,
        scope: str = Query(default="public", pattern="^(public|researcher)$"),
        actor: tuple[str, str] = Depends(identity),
    ) -> dict[str, Any]:
        owner, role = actor
        if scope == "researcher" and role not in {"researcher", "system"}:
            raise HTTPException(status_code=403, detail="researcher role is required")
        return service.export(session_id, owner_id=owner, scope=scope).model_dump(mode="json")

    return app
