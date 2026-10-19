"""FastAPI transport over the shared simulation session service."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from domains.theraskill.theraskill_simulation.contracts import ActorRole, SessionLanguage, SessionMode, SessionStatus
from domains.theraskill.theraskill_simulation.engines import (
    CommandBackedLLMTherapistEngine,
    CommandBackedLLMSimulationReviewer,
    DeferredLLMPatientEngine,
    PatientEngine,
    TherapistEngine,
    ReviewerEngine,
)
from domains.theraskill.theraskill_simulation.repository import (
    SQLiteSessionRepository,
    SessionConflictError,
    SessionNotFoundError,
)
from domains.theraskill.theraskill_simulation.scenarios import ScenarioNotFoundError, ScenarioRegistry
from domains.theraskill.theraskill_simulation.scenario_generation import (
    ScenarioDraftReviewRequest,
    ScenarioDraftUpdateRequest,
    ScenarioGenerationBatchRequest,
    LLMScenarioGenerator,
    ScenarioGenerationRequest,
    ScenarioGenerator,
    build_quality_report,
    PatientBuildRequest,
    ScenarioForkRequest,
)
from domains.theraskill.theraskill_simulation.patient_builder import (
    PatientDossier, build_patient_scenario, compile_patient, dossier_from_bundle,
    public_role_card, save_bundle_version,
)
from domains.theraskill.theraskill_simulation.batch_jobs import ScenarioBatchJobStore
from domains.theraskill.theraskill_simulation.service import SessionOperationError, SimulationSessionService
from domains.theraskill.theraskill_simulation.visibility import ExportScope
from domains.theraskill.theraskill_simulation.training import (
    LLMResponseComparisonEngine,
    ResponseComparisonEngine,
    SQLiteTrainingRepository,
    TrainingAssignment,
    TrainingCourse,
    TurnAnnotation,
)
from domains.theraskill.theraskill_contribution.client import (
    ContributionClient,
    ContributionClientError,
    HTTPContributionClient,
)
from domains.theraskill.runtime import runtime_root, simulation_root
from domains.theraskill.observability import RequestMetrics
from domains.theraskill.metrics_middleware import MetricsMiddleware


from theraskill_agent.paths import resource_root, resource_path

PROJECT_ROOT = resource_root()


class SimulationAPISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_root: Path = PROJECT_ROOT
    database_path: Path = Field(default_factory=lambda: runtime_root() / "simulation/sessions.sqlite")
    scenario_registry_path: Path = Field(
        default_factory=lambda: simulation_root() / "scenarios/scenario_registry.jsonl"
    )
    extraction_config_path: Path = resource_path("configs/extraction_config.yaml")
    model_config_path: Path = resource_path("configs/theraskill/retrieval.yaml")
    api_token: str | None = None
    contribution_api_url: str | None = Field(
        default_factory=lambda: os.environ.get(
            "THERASKILL_CONTRIBUTION_API_URL", "inprocess://contribution"
        )
    )
    contribution_api_token: str = Field(
        default_factory=lambda: os.environ.get("THERASKILL_CONTRIBUTION_API_TOKEN", "")
    )
    initialize_llm_engine: bool = True
    therapist_work_root: Path = Field(default_factory=lambda: runtime_root() / "simulation/therapist_runtime")
    reviewer_work_root: Path = Field(default_factory=lambda: runtime_root() / "simulation/reviewer_runtime")
    review_worker_enabled: bool = True
    review_worker_poll_seconds: float = Field(default=0.5, ge=0.05, le=30.0)
    review_job_stale_seconds: float = Field(default=900.0, ge=30.0, le=86400.0)
    patient_max_prompt_tokens: int = Field(default=12000, ge=2000, le=24000)
    patient_max_output_tokens: int | None = Field(default=None, ge=256, le=32768)
    patient_parse_retries: int = Field(default=1, ge=0, le=3)
    patient_fast_mode: bool = True
    generated_scenario_registry_path: Path = Field(
        default_factory=lambda: runtime_root() / "simulation/generated_scenarios.jsonl"
    )
    generated_scenario_root: Path = Field(
        default_factory=lambda: runtime_root() / "simulation/generated_cases"
    )
    generated_batch_work_root: Path = Field(
        default_factory=lambda: runtime_root() / "simulation/scenario_batch_jobs"
    )
    initialize_scenario_generator: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    mode: SessionMode = SessionMode.HUMAN_THERAPIST_VS_PATIENT
    language: SessionLanguage = SessionLanguage.ZH
    session_id: str | None = None
    parent_session_id: str | None = None
    source_session_id: str | None = None


class RunDualCycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_therapist_utterance: str | None = Field(default=None, max_length=12000)


class SubmitTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    actor: ActorRole


class TerminateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1)


class ContributionConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_review: bool = False
    research_use: bool = False
    knowledge_derivation: bool = False


class SessionFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: int | None = Field(default=None, ge=1, le=5)
    dimension_ratings: dict[
        Literal[
            "patient_realism",
            "therapist_response_appropriateness",
            "scenario_difficulty_fit",
            "reviewer_usefulness",
        ],
        int,
    ] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=2000)
    consent: ContributionConsentRequest = Field(default_factory=ContributionConsentRequest)
    consent_to_research: bool | None = None


class TurnAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str = Field(min_length=1)
    comment: str = Field(min_length=1)
    alternative_response: str | None = None


class TurnFlagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal[
        "unrealistic",
        "inappropriate_response",
        "state_inconsistent",
        "risk_handling_concern",
        "other",
    ]
    comment: str | None = Field(default=None, max_length=1000)
    consent: ContributionConsentRequest = Field(default_factory=ContributionConsentRequest)


class TrainingAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    description: str = ""
    scenario_ids: list[str] = Field(min_length=1)
    assignee: str = Field(min_length=1)
    course_id: str | None = None
    skill_tags: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    required_review_gate: str | None = None


class TrainingCourseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    description: str = ""
    skill_tags: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    difficulty_path: list[str] = Field(default_factory=list)
    completion_review_gate: str = "pass"
    learning_objectives: list[dict[str, str]] = Field(default_factory=list)
    require_instructor_review: bool = True


class ResponseComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_turn_id: str = Field(min_length=1)


def scenario_public_digest(row: dict[str, Any]) -> dict[str, Any]:
    digest = {
        key: row.get(key)
        for key in (
            "scenario_id",
            "scenario_family",
            "case_id",
            "case_family",
            "difficulty_band",
            "risk_band",
            "language",
            "scenario_brief",
            "scenario_tags",
            "recommended_turn_budget",
            "split_labels",
            "learner_role_card",
            "generated",
            "synthetic",
            "review_status",
            "review_history",
            "quality_report",
            "generated_at",
            "batch_id",
            "batch_index",
            "batch_size",
            "revision",
            "grounding_status",
            "parent_scenario_id",
            "generation_workflow",
        )
    }
    if row.get("generated") is True:
        digest["review_status"] = row.get("review_status") or "draft"
    return digest


def create_app(
    settings: SimulationAPISettings | None = None,
    *,
    patient_engine: PatientEngine | None = None,
    therapist_engine: TherapistEngine | None = None,
    reviewer_engine: ReviewerEngine | None = None,
    comparison_engine: ResponseComparisonEngine | None = None,
    scenario_generator: ScenarioGenerator | None = None,
    contribution_client_override: ContributionClient | None = None,
) -> FastAPI:
    resolved = settings or SimulationAPISettings()
    repository = SQLiteSessionRepository(resolved.database_path)
    registry = ScenarioRegistry(
        resolved.scenario_registry_path,
        generated_registry_path=resolved.generated_scenario_registry_path,
    )
    engine = patient_engine
    if engine is None and resolved.initialize_llm_engine:
        engine = DeferredLLMPatientEngine(
            project_root=resolved.project_root,
            extraction_config=resolved.extraction_config_path,
            model_config=resolved.model_config_path,
            parse_retries=resolved.patient_parse_retries,
            max_prompt_tokens=resolved.patient_max_prompt_tokens,
            max_output_tokens=resolved.patient_max_output_tokens,
            fast_mode=resolved.patient_fast_mode,
        )
    resolved_therapist_engine = therapist_engine
    if resolved_therapist_engine is None and resolved.initialize_llm_engine:
        resolved_therapist_engine = CommandBackedLLMTherapistEngine(
            project_root=resolved.project_root,
            extraction_config=resolved.extraction_config_path,
            model_config=resolved.model_config_path,
            work_root=resolved.therapist_work_root,
        )
    resolved_reviewer_engine = reviewer_engine
    if resolved_reviewer_engine is None and resolved.initialize_llm_engine:
        resolved_reviewer_engine = CommandBackedLLMSimulationReviewer(
            project_root=resolved.project_root,
            extraction_config=resolved.extraction_config_path,
            model_config=resolved.model_config_path,
            work_root=resolved.reviewer_work_root,
        )
    service = SimulationSessionService(
        repository=repository,
        scenario_registry=registry,
        patient_engine=engine,
        therapist_engine=resolved_therapist_engine,
        reviewer_engine=resolved_reviewer_engine,
        review_job_stale_seconds=resolved.review_job_stale_seconds,
    )
    training_repository = SQLiteTrainingRepository(resolved.database_path)
    resolved_comparison_engine = comparison_engine
    if resolved_comparison_engine is None and resolved.initialize_llm_engine:
        resolved_comparison_engine = LLMResponseComparisonEngine(
            project_root=resolved.project_root,
            extraction_config=resolved.extraction_config_path,
            model_config=resolved.model_config_path,
        )
    resolved_scenario_generator = scenario_generator
    scenario_edit_lock = threading.RLock()
    if resolved_scenario_generator is None and resolved.initialize_scenario_generator and resolved.initialize_llm_engine:
        resolved_scenario_generator = LLMScenarioGenerator(
            project_root=resolved.project_root,
            extraction_config=resolved.extraction_config_path,
            model_config=resolved.model_config_path,
            output_root=resolved.generated_scenario_root,
        )
    contribution_client = contribution_client_override
    if contribution_client is None and resolved.contribution_api_url:
        contribution_client = HTTPContributionClient(
            resolved.contribution_api_url,
            api_token=resolved.contribution_api_token,
        )

    def execute_batch_payload(batch_id: str, payload: dict[str, Any], report_progress=None) -> dict[str, Any]:
        request = ScenarioGenerationBatchRequest.model_validate(payload)
        if resolved_scenario_generator is None:
            raise RuntimeError("online scenario generator is unavailable")
        # The registry is the checkpoint: do not regenerate already saved items
        # after a provider failure or a restart between persistence and progress.
        by_index = {int(row["batch_index"]): row for row in generated_rows()
                    if row.get("batch_id") == batch_id and row.get("batch_index")}
        persisted = [by_index[i] for i in sorted(by_index)]

        def result_payload() -> dict[str, Any]:
            quality_counts = {"pass": 0, "warn": 0, "fail": 0}
            for row in persisted:
                status = str((row.get("quality_report") or {}).get("status") or "warn")
                quality_counts[status] = quality_counts.get(status, 0) + 1
            return {
                "schema_version": "theraskill_online_scenario_batch_v1", "batch_id": batch_id,
                "count": len(persisted), "scenarios": [scenario_public_digest(row) for row in persisted],
                "quality_summary": quality_counts,
                "course_draft": {"title": request.course_title, "description": request.course_description,
                                 "skill_tags": request.skill_tags,
                                 "scenario_ids": [str(row["scenario_id"]) for row in persisted],
                                 "batch_id": batch_id, "review_required": True},
            }

        for index in range(1, request.count + 1):
            if index not in by_index:
                variation = (f"Scenario {index}/{request.count} in a course batch; vary the trigger or client goal to avoid duplicates."
                             if str(request.language) == "en" else f"课程批次第 {index}/{request.count} 个场景，请改变触发情境或来访者目标，避免同批重复。")
                generated = resolved_scenario_generator.generate(ScenarioGenerationRequest(
                    brief=request.brief, scenario_family=request.scenario_family,
                    difficulty_band=request.difficulty_band, risk_band=request.risk_band,
                    language=request.language, scenario_tags=request.scenario_tags,
                    constraints=f"{request.constraints.strip()}\n{variation}".strip(),
                ))
                row = dict(generated["scenario"])
                row.update({"language": row.get("language") or request.language,
                            "batch_id": batch_id, "batch_index": index, "batch_size": request.count,
                            "review_status": "draft", "review_history": []})
                row["quality_report"] = build_quality_report(row, generated_rows())
                by_index[index] = registry.register_generated(row)
                persisted = [by_index[i] for i in sorted(by_index)]
            if report_progress:
                report_progress(progress={"processed": len(persisted), "total": request.count}, result=result_payload())
        return result_payload()

    batch_job_store = ScenarioBatchJobStore(resolved.generated_batch_work_root, execute_batch_payload, runner_reports_progress=True)
    async def review_worker() -> None:
        while True:
            try:
                processed = await asyncio.to_thread(service.process_one_pending_review)
            except asyncio.CancelledError:
                raise
            except Exception:
                processed = False
            if not processed:
                await asyncio.sleep(resolved.review_worker_poll_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if resolved.review_worker_enabled and service.reviewer_engine is not None:
            task = asyncio.create_task(review_worker())
            app.state.review_worker_task = task
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            batch_job_store.shutdown()

    app = FastAPI(
        title="TheraSkill Simulation API",
        version="1.0.0",
        description="Stateful session API for patient, therapist, dual, and review simulation modes.",
        lifespan=lifespan,
    )
    metrics = RequestMetrics("theraskill-simulation")
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.state.settings = resolved
    app.state.session_service = service
    app.state.training_repository = training_repository
    app.state.comparison_engine = resolved_comparison_engine
    app.state.scenario_generator = resolved_scenario_generator
    app.state.scenario_batch_jobs = batch_job_store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Simulation-Role", "X-Simulation-User"],
    )

    async def caller_identity(
        x_simulation_role: str = Header(default=ActorRole.HUMAN_THERAPIST.value),
        x_simulation_user: str = Header(default="local-user"),
        authorization: str | None = Header(default=None),
    ) -> tuple[str, ActorRole]:
        try:
            role = ActorRole(x_simulation_role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid X-Simulation-Role") from exc
        if resolved.api_token:
            expected = f"Bearer {resolved.api_token}"
            if not authorization or not secrets.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="invalid bearer token")
        return x_simulation_user.strip() or "local-user", role

    async def caller_role(
        actor: tuple[str, ActorRole] = Depends(caller_identity),
    ) -> ActorRole:
        return actor[1]

    def require_contribution_client() -> ContributionClient:
        if contribution_client is None:
            raise HTTPException(status_code=503, detail="Contribution Hub is not configured")
        return contribution_client

    def contribution_role(role: ActorRole) -> str:
        if role == ActorRole.INSTRUCTOR:
            return "instructor"
        if role in {ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            return str(role)
        return "learner"

    def contribution_error(exc: ContributionClientError) -> HTTPException:
        status = exc.status_code if exc.status_code in {400, 403, 404, 409, 422} else 503
        return HTTPException(status_code=status, detail=f"Contribution Hub: {exc}")

    def stable_id(prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
        return f"{prefix}_{digest}"

    def generated_rows() -> list[dict[str, Any]]:
        return registry.list(split_key="formal_split", split_value="generated", limit=500, language="mixed")

    def review_event(row: dict[str, Any], *, action: str, actor_role: ActorRole, reason: str = "") -> list[dict[str, Any]]:
        history = list(row.get("review_history") or [])
        history.append({
            "action": action,
            "actor_role": str(actor_role),
            "reason": reason.strip(),
            "at": datetime.now(timezone.utc).isoformat(),
        })
        return history[-30:]

    def update_case_bundle(row: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
        bundle_path = str(row.get("case_bundle_path") or "")
        if not bundle_path:
            if "patient" in updates:
                raise ValueError("Patient bundle is missing; create a new patient draft")
            return updates
        path = Path(bundle_path)
        if not path.exists():
            if "patient" in updates:
                raise ValueError("Patient bundle is missing; restore its file before editing")
            return updates
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if "patient" in updates:
                raise ValueError("Patient bundle could not be read")
            return updates
        metadata = bundle.setdefault("metadata", {})
        for key in ("case_family", "difficulty_band", "risk_band", "scenario_tags"):
            if key in updates:
                metadata[key] = updates[key]
        authoritative = bundle.setdefault("authoritative_layer", {})
        if "learner_role_card" in updates:
            visible = authoritative.setdefault("visible_profile", {})
            visible["learner_role_card"] = updates["learner_role_card"]
        if "scenario_brief" in updates:
            visible = authoritative.setdefault("visible_profile", {})
            visible["scenario_brief"] = updates["scenario_brief"]
        revised = {**row, **updates, "revision": int(row.get("revision") or 1) + 1}
        if "patient" in updates or bundle.get("patient_dossier"):
            patient = PatientDossier.model_validate(updates["patient"]) if "patient" in updates else dossier_from_bundle(bundle, row)
            bundle = compile_patient(bundle, revised, patient)
            revised["learner_role_card"] = public_role_card(bundle, revised)
        revised.pop("patient", None)
        revised.pop("expected_revision", None)
        return save_bundle_version(bundle, revised, resolved.generated_scenario_root)

    def simulation_context(snapshot, *, include_text: bool) -> tuple[dict[str, Any], bool]:
        events = service.repository.list_events(snapshot.session.session_id)
        unit_ids = list(
            dict.fromkeys(unit_id for event in events for unit_id in event.retrieved_unit_ids)
        )
        context: dict[str, Any] = {
            "operational": {
                "model_id": snapshot.session.model_config_id,
                "prompt_release_id": snapshot.session.prompt_release_id,
                "knowledge_release_id": snapshot.session.knowledge_release_id,
                "retrieved_knowledge_unit_ids": unit_ids,
                "routing_result": {
                    "mode": str(snapshot.session.mode),
                    "scenario_family": snapshot.public_view.scenario.scenario_family,
                    "risk_band": snapshot.public_view.scenario.risk_band,
                },
                "status": str(snapshot.session.status),
            }
        }
        if not include_text:
            return context, False
        remaining = 12_000
        bounded: list[dict[str, Any]] = []
        truncated = False
        for turn in reversed(snapshot.public_view.transcript):
            if remaining <= 0:
                truncated = True
                break
            text = turn.text
            if len(text) > remaining:
                text = text[-remaining:]
                truncated = True
            bounded.append(
                {"role": turn.role, "content": text, "turn_id": turn.turn_id}
            )
            remaining -= len(text)
        context["bounded_conversation"] = list(reversed(bounded))
        return context, truncated

    def scope_for_role(role: ActorRole, session_id: str | None = None) -> ExportScope:
        if role in {ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            return ExportScope.RESEARCHER
        if role == ActorRole.INSTRUCTOR:
            if session_id:
                status = str(service.get_snapshot(session_id).session.status)
                if status in {"completed", "terminated", "failed"}:
                    return ExportScope.INSTRUCTOR
            return ExportScope.PUBLIC
        return ExportScope.PUBLIC

    @app.exception_handler(SessionNotFoundError)
    @app.exception_handler(ScenarioNotFoundError)
    def not_found_handler(_request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(SessionConflictError)
    def conflict_handler(_request, exc: SessionConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SessionOperationError)
    def operation_handler(_request, exc: SessionOperationError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    from domains.theraskill.theraskill_llm import ModelOutputError
    from domains.theraskill.theraskill_simulation.engines import SimulationEngineError

    @app.exception_handler(ModelOutputError)
    @app.exception_handler(SimulationEngineError)
    def model_output_handler(_request, exc: ModelOutputError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc), "code": "model_output_invalid", "retryable": True})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "theraskill-simulation",
            "version": app.version,
        }

    @app.get("/metrics")
    def metrics_endpoint() -> dict[str, Any]:
        return metrics.snapshot()

    @app.get("/version")
    def version() -> dict[str, Any]:
        return {
            "api_version": app.version,
            "session_schema": "theraskill_simulation_session_v1",
            "runtime_version": service.runtime_version,
        }

    @app.get("/runtime-config")
    def runtime_config(
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="researcher role is required")
        return {
            "database_path": str(resolved.database_path),
            "scenario_registry_path": str(resolved.scenario_registry_path),
            "model_config_path": str(resolved.model_config_path),
            "llm_engine_enabled": service.patient_engine is not None,
            "therapist_engine_enabled": service.therapist_engine is not None,
            "reviewer_engine_enabled": service.reviewer_engine is not None,
            "api_token_configured": bool(resolved.api_token),
        }

    @app.get("/scenarios")
    def list_scenarios(
        _role: ActorRole = Depends(caller_role),
        family: str | None = None,
        risk_band: str | None = None,
        difficulty_band: str | None = None,
        search: str | None = None,
        language: SessionLanguage = SessionLanguage.ZH,
        split_key: str = "formal_split",
        split_value: str = "test",
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        rows = registry.list(
            family=family,
            risk_band=risk_band,
            difficulty_band=difficulty_band,
            search=search,
            split_key=split_key,
            split_value=split_value,
            limit=limit,
            language=str(language),
        )
        return {
            "count": len(rows),
            "scenarios": [
                {
                    **scenario_public_digest(row),
                    "learner_role_card": row.get("learner_role_card") or service.learner_role_card_for_case(
                        str(row.get("case_id") or ""), row
                    ),
                }
                for row in rows
            ],
        }

    @app.get("/scenarios/generated")
    def list_generated_scenarios(
        role: ActorRole = Depends(caller_role),
        limit: int = Query(default=50, ge=1, le=200),
        review_status: str | None = None,
        batch_id: str | None = None,
        language: SessionLanguage = SessionLanguage.ZH,
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="instructor or researcher role is required")
        rows = [
            row for row in registry.list(
                split_key="formal_split",
                split_value="generated",
                limit=500,
                language=str(language),
            )
            if (not review_status or row.get("review_status", "draft") == review_status)
            and (not batch_id or row.get("batch_id") == batch_id)
        ][:limit]
        return {"count": len(rows), "scenarios": [scenario_public_digest(row) for row in rows]}

    @app.post("/scenarios/generate", status_code=201)
    async def generate_scenario(
        request: ScenarioGenerationRequest,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="online scenario generation requires instructor or researcher role")
        if resolved_scenario_generator is None:
            raise HTTPException(status_code=503, detail="online scenario generator is unavailable")
        try:
            generated = await asyncio.to_thread(resolved_scenario_generator.generate, request)
            row = dict(generated["scenario"])
            row.setdefault("language", request.language)
            row.setdefault("review_status", "draft")
            row.setdefault("review_history", [])
            row["quality_report"] = build_quality_report(
                row,
                [item for item in generated_rows() if item.get("scenario_id") != row.get("scenario_id")],
            )
            row = registry.register_generated(row)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"online scenario generation failed: {exc}") from exc
        return {
            "schema_version": "theraskill_online_scenario_draft_v1",
            "synthetic": True,
            "scenario": scenario_public_digest(row),
            "draft": row,
            "quality": row.get("quality_report"),
            "review": {"status": row.get("review_status", "draft"), "history": row.get("review_history", [])},
            "safety": {
                "status": "synthetic_draft",
                "requires_researcher_or_instructor_review": True,
                "not_clinical_diagnosis": True,
            },
        }

    @app.post("/scenarios/build-patient", status_code=201)
    def build_patient(request: PatientBuildRequest, role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="patient building requires instructor or researcher role")
        try:
            generated = build_patient_scenario(request, resolved.generated_scenario_root)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        row = generated["scenario"]
        row["quality_report"] = build_quality_report(row, generated_rows())
        row = registry.register_generated(row)
        return {"scenario": scenario_public_digest(row), "quality": row["quality_report"], "synthetic": True}

    @app.get("/scenarios/{scenario_id}/patient")
    def get_patient(scenario_id: str, role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="patient design contains private case facts")
        row = registry.get(scenario_id)
        bundle, _, _ = service._load_case_bundle(row, language=row.get("language") or "zh")
        return {"scenario": scenario_public_digest(row), "revision": row.get("revision") or 1,
                "patient": dossier_from_bundle(bundle, row).model_dump(mode="json"),
                "grounding": (bundle.get("provenance") or {}).get("knowledge_grounding", {"status": "not_requested"}),
                "bundle_sha256": row.get("bundle_sha256"), "synthetic": bool(row.get("synthetic"))}

    @app.post("/scenarios/{scenario_id}/fork", status_code=201)
    def fork_patient(scenario_id: str, request: ScenarioForkRequest, role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="patient copying requires instructor or researcher role")
        source = registry.get(scenario_id)
        bundle, _, _ = service._load_case_bundle(source, language=source.get("language") or "zh")
        patient = dossier_from_bundle(bundle, source)
        request = PatientBuildRequest(brief=request.brief or source["scenario_brief"], patient=patient,
            scenario_family=source["scenario_family"], difficulty_band=source.get("difficulty_band") or "medium",
            risk_band=source.get("risk_band") or "low", language=source.get("language") or "zh",
            scenario_tags=source.get("scenario_tags") or [])
        generated = build_patient_scenario(request, resolved.generated_scenario_root, bundle=bundle)
        row = {**generated["scenario"], "parent_scenario_id": scenario_id, "parent_revision": source.get("revision") or 1}
        row["quality_report"] = build_quality_report(row, generated_rows())
        row = registry.register_generated(row)
        return {"scenario": scenario_public_digest(row), "quality": row["quality_report"], "synthetic": True}

    @app.post("/scenarios/generate-batch", status_code=201)
    async def generate_scenario_batch(
        request: ScenarioGenerationBatchRequest,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="batch scenario generation requires instructor or researcher role")
        if resolved_scenario_generator is None:
            raise HTTPException(status_code=503, detail="online scenario generator is unavailable")
        try:
            return await asyncio.to_thread(execute_batch_payload, f"batch_{uuid.uuid4().hex}", request.model_dump(mode="json"))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"batch scenario generation failed: {exc}") from exc

    @app.post("/scenarios/generate-batch/jobs", status_code=202)
    def create_scenario_batch_job(
        request: ScenarioGenerationBatchRequest,
        role: ActorRole = Depends(caller_role),
        x_simulation_user: str = Header(default="local-user"),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="batch scenario generation requires instructor or researcher role")
        if resolved_scenario_generator is None:
            raise HTTPException(status_code=503, detail="online scenario generator is unavailable")
        return batch_job_store.create(request.model_dump(mode="json"), x_simulation_user.strip() or "local-user")

    @app.get("/scenarios/generate-batch/jobs")
    def list_scenario_batch_jobs(role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="batch jobs require instructor or researcher role")
        return {"jobs": batch_job_store.list()}

    @app.get("/scenarios/generate-batch/jobs/{job_id}")
    def get_scenario_batch_job(job_id: str, role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="batch scenario generation requires instructor or researcher role")
        job = batch_job_store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="scenario batch job not found")
        return job

    @app.post("/scenarios/generate-batch/jobs/{job_id}/retry")
    def retry_scenario_batch_job(job_id: str, role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="batch scenario generation requires instructor or researcher role")
        try:
            job = batch_job_store.retry(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not job:
            raise HTTPException(status_code=404, detail="scenario batch job not found")
        return job

    @app.patch("/scenarios/generated/{scenario_id}")
    def update_generated_scenario(
        scenario_id: str,
        request: ScenarioDraftUpdateRequest,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="editing generated scenarios requires instructor or researcher role")
        with scenario_edit_lock:
            current = registry.get(scenario_id)
            if current.get("generated") is not True:
                raise HTTPException(status_code=400, detail="only generated drafts can be edited")
            if request.expected_revision is not None and request.expected_revision != int(current.get("revision") or 1):
                raise HTTPException(status_code=409, detail="Patient changed. Reload before saving your edits.")
            updates = request.model_dump(mode="json", exclude_none=True, exclude={"expected_revision"})
            if not updates:
                raise HTTPException(status_code=400, detail="at least one editable field is required")
            try:
                merged = {**current, **update_case_bundle(current, updates)}
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            merged.pop("patient", None)
            merged["revision"] = int(current.get("revision") or 1) + 1
            merged["review_status"] = "draft"
            merged["review_history"] = review_event(current, action="edit", actor_role=role)
            merged["quality_report"] = build_quality_report(
                merged, [item for item in generated_rows() if item.get("scenario_id") != scenario_id],
            )
            saved = registry.update_generated(scenario_id, merged)
        return {
            "schema_version": "theraskill_online_scenario_draft_v1",
            "scenario": scenario_public_digest(saved),
            "draft": saved,
            "quality": saved.get("quality_report"),
            "review": {"status": saved.get("review_status"), "history": saved.get("review_history", [])},
        }

    @app.post("/scenarios/generated/{scenario_id}/review")
    def review_generated_scenario(
        scenario_id: str,
        request: ScenarioDraftReviewRequest,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="reviewing generated scenarios requires instructor or researcher role")
        with scenario_edit_lock:
            current = registry.get(scenario_id)
            if current.get("generated") is not True:
                raise HTTPException(status_code=400, detail="only generated drafts can be reviewed")
            if request.action == "reject" and not request.reason.strip():
                raise HTTPException(status_code=422, detail="rejecting a draft requires a reason")
            quality = build_quality_report(
                current,
                [item for item in generated_rows() if item.get("scenario_id") != scenario_id],
            )
            if request.action == "approve" and quality.get("status") == "fail":
                raise HTTPException(status_code=422, detail="quality checks must pass before approval")
            status = "approved" if request.action == "approve" else "rejected"
            saved = registry.update_generated(scenario_id, {
                "review_status": status,
                "quality_report": quality,
                "review_history": review_event(current, action=request.action, actor_role=role, reason=request.reason),
            })
        return {
            "schema_version": "theraskill_online_scenario_review_v1",
            "scenario": scenario_public_digest(saved),
            "quality": quality,
            "review": {"status": status, "reason": request.reason.strip(), "history": saved.get("review_history", [])},
        }

    @app.post("/sessions", status_code=201)
    def create_session(
        request: CreateSessionRequest,
        actor: tuple[str, ActorRole] = Depends(caller_identity),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        subject_id, role = actor
        if request.mode == SessionMode.REVIEW_ONLY and role not in {
            ActorRole.INSTRUCTOR,
            ActorRole.RESEARCHER,
            ActorRole.SYSTEM,
        }:
            raise HTTPException(status_code=403, detail="review-only sessions require instructor or researcher role")
        resolved_session_id = request.session_id
        if idempotency_key.strip() and not resolved_session_id:
            resolved_session_id = stable_id("session", subject_id, idempotency_key.strip())
        if resolved_session_id:
            try:
                existing = service.get_snapshot(resolved_session_id)
            except SessionNotFoundError:
                existing = None
            if existing is not None:
                return existing.public_view.model_dump(mode="json")
        snapshot = service.create_session(
            scenario_id=request.scenario_id,
            mode=request.mode,
            language=request.language,
            session_id=resolved_session_id,
            parent_session_id=request.parent_session_id,
            source_session_id=request.source_session_id,
        )
        return snapshot.public_view.model_dump(mode="json")

    @app.get("/sessions")
    def list_sessions(
        _role: ActorRole = Depends(caller_role),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        sessions = service.list_public_sessions(limit=limit)
        return {"count": len(sessions), "sessions": sessions}

    @app.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        return service.export_session(session_id, scope=scope_for_role(role, session_id))

    @app.delete("/sessions/{session_id}")
    def delete_session(
        session_id: str,
        _role: ActorRole = Depends(caller_role),
    ) -> Response:
        service.delete_session(session_id)
        return Response(status_code=204)

    @app.get("/sessions/{session_id}/annotations")
    def list_annotations(
        session_id: str,
        _role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        service.get_snapshot(session_id)
        rows = training_repository.list_annotations(session_id)
        return {"count": len(rows), "annotations": [row.model_dump(mode="json") for row in rows]}

    @app.post("/sessions/{session_id}/annotations", status_code=201)
    def add_annotation(
        session_id: str,
        request: TurnAnnotationRequest,
        actor: tuple[str, ActorRole] = Depends(caller_identity),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        subject_id, role = actor
        if role != ActorRole.INSTRUCTOR:
            raise HTTPException(status_code=403, detail="turn annotations require instructor role")
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        snapshot = service.get_snapshot(session_id)
        if SessionStatus(snapshot.session.status) not in {
            SessionStatus.COMPLETED,
            SessionStatus.TERMINATED,
            SessionStatus.FAILED,
        }:
            raise HTTPException(status_code=409, detail="annotations require a terminal session")
        if request.turn_id not in {turn.turn_id for turn in snapshot.public_view.transcript}:
            raise HTTPException(status_code=404, detail="turn not found in session")
        context, truncated = simulation_context(snapshot, include_text=True)
        client = require_contribution_client()
        try:
            consent = client.create_consent(
                subject_id=subject_id,
                role="instructor",
                capture_tier="review_allowed",
                purposes={
                    "service_delivery": True,
                    "product_analytics": True,
                    "content_review": True,
                    "research_use": False,
                    "knowledge_derivation": False,
                },
                idempotency_key=f"simulation-annotation-consent:{subject_id}:{idempotency_key}",
            )
            captured = client.capture(
                subject_id=subject_id,
                role="instructor",
                endpoint="/v1/annotations/turn",
                idempotency_key=f"simulation-annotation:{subject_id}:{idempotency_key}",
                payload={
                    "contribution_type": "turn_annotation",
                    "origin": {
                        "product": "simulation_training",
                        "platform": "web",
                        "session_id": session_id,
                        "turn_id": request.turn_id,
                    },
                    "target": {"target_type": "turn", "target_id": request.turn_id, "target_version": "v1"},
                    "payload": {
                        "comment": request.comment.strip(),
                        "alternative_response": (request.alternative_response or "").strip() or None,
                        "context_truncated": truncated,
                    },
                    "consent_snapshot_id": consent["consent_snapshot_id"],
                    "context": context,
                    "provenance": {"source_service": "simulation_api_v1"},
                },
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc
        contribution_id = captured["contribution"]["contribution_id"]
        annotation_id = stable_id("annotation", subject_id, session_id, idempotency_key)
        existing = training_repository.get_annotation(annotation_id)
        if existing:
            return existing.model_dump(mode="json")
        annotation = TurnAnnotation(
            annotation_id=annotation_id,
            session_id=session_id,
            turn_id=request.turn_id,
            author_role="instructor",
            comment=request.comment.strip(),
            alternative_response=(request.alternative_response or "").strip() or None,
            contribution_id=contribution_id,
        )
        training_repository.add_annotation(annotation)
        return annotation.model_dump(mode="json")

    @app.post("/sessions/{session_id}/turns/{turn_id}/flag", status_code=201)
    def flag_turn(
        session_id: str,
        turn_id: str,
        request: TurnFlagRequest,
        actor: tuple[str, ActorRole] = Depends(caller_identity),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        subject_id, role = actor
        if role not in {
            ActorRole.HUMAN_THERAPIST,
            ActorRole.HUMAN_CLIENT_ROLE,
            ActorRole.INSTRUCTOR,
        }:
            raise HTTPException(status_code=403, detail="role cannot flag simulation turns")
        snapshot = service.get_snapshot(session_id)
        if turn_id not in {turn.turn_id for turn in snapshot.public_view.transcript}:
            raise HTTPException(status_code=404, detail="turn not found in session")
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
        context, truncated = simulation_context(snapshot, include_text=content_review)
        client = require_contribution_client()
        try:
            consent = client.create_consent(
                subject_id=subject_id,
                role=contribution_role(role),
                capture_tier=tier,
                purposes=purposes,
                idempotency_key=f"simulation-turn-flag-consent:{subject_id}:{idempotency_key}",
            )
            return client.capture(
                subject_id=subject_id,
                role=contribution_role(role),
                endpoint="/v1/annotations/turn",
                idempotency_key=f"simulation-turn-flag:{subject_id}:{idempotency_key}",
                payload={
                    "contribution_type": "turn_annotation",
                    "origin": {
                        "product": "simulation_training",
                        "platform": "web",
                        "session_id": session_id,
                        "turn_id": turn_id,
                    },
                    "target": {"target_type": "turn", "target_id": turn_id, "target_version": "v1"},
                    "payload": {
                        "annotation_kind": "learner_flag",
                        "category": request.category,
                        "comment": (request.comment or "").strip() or None,
                        "context_truncated": truncated,
                    },
                    "consent_snapshot_id": consent["consent_snapshot_id"],
                    "context": context,
                    "provenance": {"source_service": "simulation_api_v1"},
                },
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.get("/assignments")
    def list_assignments(
        role: ActorRole = Depends(caller_role),
        assignee: str | None = None,
    ) -> dict[str, Any]:
        if role == ActorRole.INSTRUCTOR:
            rows = training_repository.list_assignments(assignee=assignee)
        else:
            if not assignee:
                raise HTTPException(status_code=400, detail="learner assignment query requires assignee")
            rows = training_repository.list_assignments(assignee=assignee)
        return {"count": len(rows), "assignments": [row.model_dump(mode="json") for row in rows]}

    from domains.theraskill.theraskill_simulation.learning import LearningService
    from domains.theraskill.theraskill_simulation.learning_api import create_learning_router
    learning = LearningService(training_repository, service)
    app.include_router(create_learning_router(learning, service, caller_role))

    @app.get("/courses")
    def list_courses(
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="instructor or researcher role is required")
        rows = training_repository.list_courses()
        return {"count": len(rows), "courses": [row.model_dump(mode="json") for row in rows]}

    @app.post("/courses", status_code=201)
    def add_course(
        request: TrainingCourseRequest,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role != ActorRole.INSTRUCTOR:
            raise HTTPException(status_code=403, detail="course creation requires instructor role")
        missing: list[str] = []
        unapproved: list[str] = []
        for scenario_id in request.scenario_ids:
            try:
                scenario_row = registry.get(scenario_id)
            except ScenarioNotFoundError:
                missing.append(scenario_id)
                continue
            if scenario_row.get("generated") is True and scenario_row.get("review_status") != "approved":
                unapproved.append(scenario_id)
        if missing:
            raise HTTPException(status_code=404, detail=f"unknown scenarios: {missing}")
        if unapproved:
            raise HTTPException(status_code=422, detail=f"generated scenarios require approval before course creation: {unapproved}")
        objective_ids = [str(o.get("id") or "").strip() for o in request.learning_objectives]
        if request.learning_objectives and (not all(objective_ids) or len(set(objective_ids)) != len(objective_ids) or any(not str(o.get("description") or "").strip() for o in request.learning_objectives)):
            raise HTTPException(422, "Learning objectives need unique IDs and descriptions")
        course = TrainingCourse(
            course_id=f"course_{uuid.uuid4().hex}",
            title=request.title.strip(),
            description=request.description.strip(),
            skill_tags=[tag.strip() for tag in request.skill_tags if tag.strip()],
            scenario_ids=list(dict.fromkeys(request.scenario_ids)),
            difficulty_path=[band.strip() for band in request.difficulty_path if band.strip()],
            completion_review_gate=request.completion_review_gate.strip() or "pass",
            learning_objectives=request.learning_objectives,
            require_instructor_review=request.require_instructor_review,
        )
        training_repository.add_course(course)
        return course.model_dump(mode="json")

    @app.delete("/courses/{course_id}")
    def delete_course(
        course_id: str,
        role: ActorRole = Depends(caller_role),
    ) -> Response:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="course deletion requires instructor role")
        if not training_repository.delete_course(course_id):
            raise HTTPException(status_code=404, detail="course not found")
        return Response(status_code=204)

    @app.get("/teaching/curriculum")
    def teaching_curriculum(
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="instructor or researcher role is required")
        courses = training_repository.list_courses()
        assignments = training_repository.list_assignments()
        return {
            "schema_version": "theraskill_curriculum_v1",
            "courses": [row.model_dump(mode="json") for row in courses],
            "assignments": [row.model_dump(mode="json") for row in assignments],
            "counts": {
                "courses": len(courses),
                "assignments": len(assignments),
                "active_assignments": sum(row.status == "assigned" for row in assignments),
            },
        }

    @app.get("/teaching/recommendations")
    def teaching_recommendations(
        assignee: str | None = None,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="instructor or researcher role is required")
        # A learner's recommendations may only use that learner's linked attempts.
        linked = {item['session_id'] for assignment in training_repository.list_assignments(assignee=assignee)
                  for item in learning.attempts(assignment.assignment_id)} if assignee else None
        feedback = [item for item in service.list_feedback(limit=2000)
                    if linked is None or item.get('session_id') in linked]
        totals: dict[str, list[float]] = {}
        for item in feedback:
            for dimension, value in (item.get("dimension_ratings") or {}).items():
                totals.setdefault(str(dimension), []).append(float(value))
        weak_dimensions = sorted(
            (
                {"dimension": key, "average": round(sum(values) / len(values), 3), "count": len(values)}
                for key, values in totals.items()
                if values
            ),
            key=lambda item: (item["average"], item["dimension"]),
        )
        assignments = training_repository.list_assignments(assignee=assignee)
        recommendations = []
        for assignment in assignments:
            if assignment.status not in {"assigned", "in_progress"}:
                continue
            recommendations.append({
                "assignment_id": assignment.assignment_id,
                "course_id": assignment.course_id,
                "scenario_ids": assignment.scenario_ids,
                "skill_tags": assignment.skill_tags,
                "priority": "high" if weak_dimensions and weak_dimensions[0]["average"] < 3.5 else "normal",
                "reason": (
                    f"优先练习 {weak_dimensions[0]['dimension']}（平均 {weak_dimensions[0]['average']}）"
                    if weak_dimensions else "基于当前课程安排继续完成练习"
                ),
            })
        return {
            "schema_version": "theraskill_learning_recommendations_v1",
            "assignee": assignee,
            "weak_dimensions": weak_dimensions,
            "recommendations": recommendations,
        }

    @app.get("/teaching/overview")
    def teaching_overview(
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        """Return one bounded view of the instructor teaching loop."""
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="instructor or researcher role is required")
        assignments = training_repository.list_assignments()
        feedback = service.list_feedback(limit=2000)
        sessions = service.list_public_sessions(limit=2000)
        status_counts: dict[str, int] = {}
        review_gate_counts: dict[str, int] = {}
        for row in sessions:
            status = str(row.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            review = row.get("review") or {}
            gate = str(review.get("gate_status") or "unreviewed")
            review_gate_counts[gate] = review_gate_counts.get(gate, 0) + 1
        dimension_totals: dict[str, dict[str, float]] = {}
        for item in feedback:
            for dimension, value in (item.get("dimension_ratings") or {}).items():
                bucket = dimension_totals.setdefault(str(dimension), {"count": 0, "sum": 0.0})
                bucket["count"] += 1
                bucket["sum"] += float(value)
        dimension_summary = {
            key: {"count": int(value["count"]), "average": round(value["sum"] / value["count"], 3)}
            for key, value in dimension_totals.items()
            if value["count"]
        }
        return {
            "schema_version": "theraskill_teaching_overview_v1",
            "curriculum": {
                "course_count": len(training_repository.list_courses()),
                "assignment_count": len(assignments),
            },
            "assignments": {
                "count": len(assignments),
                "active_count": sum(row.status == "assigned" for row in assignments),
            },
            "sessions": {
                "count": len(sessions),
                "feedback_count": len(feedback),
                "status_counts": status_counts,
                "review_gate_counts": review_gate_counts,
            },
            "learning_signals": {"dimension_summary": dimension_summary},
            "annotations": {"count": training_repository.count_annotations()},
            "response_comparisons": {"count": training_repository.count_comparisons()},
            "next_actions": [
                "review_pending_annotations",
                "compare_therapist_responses",
                "capture_consented_feedback",
                "promote_only_approved_contributions",
            ],
        }

    @app.post("/assignments", status_code=201)
    def add_assignment(
        request: TrainingAssignmentRequest,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role != ActorRole.INSTRUCTOR:
            raise HTTPException(status_code=403, detail="assignment creation requires instructor role")
        missing: list[str] = []
        for scenario_id in request.scenario_ids:
            try:
                registry.get(scenario_id)
            except ScenarioNotFoundError:
                missing.append(scenario_id)
        if missing:
            raise HTTPException(status_code=404, detail=f"unknown scenarios: {missing}")
        if request.course_id:
            course = training_repository.get_course(request.course_id)
            if not course:
                raise HTTPException(404, "Course not found")
            if not set(request.scenario_ids).issubset(set(course.scenario_ids)):
                raise HTTPException(422, "Assignment scenarios must belong to the course")
        for scenario_id in request.scenario_ids:
            row = registry.get(scenario_id)
            if row.get("generated") and row.get("review_status") != "approved":
                raise HTTPException(422, "Generated scenarios need approval before assignment")
        assignment = TrainingAssignment(
            assignment_id=f"assignment_{uuid.uuid4().hex}",
            title=request.title.strip(),
            description=request.description.strip(),
            scenario_ids=request.scenario_ids,
            assignee=request.assignee.strip(),
            course_id=request.course_id,
            skill_tags=[tag.strip() for tag in request.skill_tags if tag.strip()],
            due_at=request.due_at,
            required_review_gate=request.required_review_gate.strip() if request.required_review_gate else None,
        )
        training_repository.add_assignment(assignment)
        return assignment.model_dump(mode="json")

    @app.delete("/assignments/{assignment_id}")
    def delete_assignment(
        assignment_id: str,
        role: ActorRole = Depends(caller_role),
    ) -> Response:
        if role not in {ActorRole.INSTRUCTOR, ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="assignment deletion requires instructor role")
        if not training_repository.delete_assignment(assignment_id):
            raise HTTPException(status_code=404, detail="assignment not found")
        return Response(status_code=204)

    @app.get("/sessions/{session_id}/comparisons")
    def list_comparisons(
        session_id: str,
        _role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        service.get_snapshot(session_id)
        rows = training_repository.list_comparisons(session_id)
        return {"count": len(rows), "comparisons": [row.model_dump(mode="json") for row in rows]}

    @app.post("/sessions/{session_id}/comparisons", status_code=201)
    def create_comparison(
        session_id: str,
        request: ResponseComparisonRequest,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.HUMAN_THERAPIST, ActorRole.INSTRUCTOR, ActorRole.RESEARCHER}:
            raise HTTPException(status_code=403, detail="response comparison requires therapist training role")
        if resolved_comparison_engine is None:
            raise HTTPException(status_code=503, detail="response comparison engine is unavailable")
        snapshot = service.get_snapshot(session_id)
        turns = snapshot.public_view.transcript
        target_index = next(
            (index for index, turn in enumerate(turns) if turn.turn_id == request.target_turn_id),
            None,
        )
        if target_index is None or turns[target_index].role != "patient":
            raise HTTPException(status_code=400, detail="target must be a patient turn")
        events = service.repository.list_events(session_id)
        evidence_ids = [
            unit_id
            for event in events
            for unit_id in event.retrieved_unit_ids
        ]
        comparison_kwargs = {
            "session_id": session_id,
            "target_turn_id": request.target_turn_id,
            "scenario": snapshot.public_view.scenario.model_dump(mode="json"),
            "transcript": [turn.model_dump(mode="json") for turn in turns[: target_index + 1]],
            "knowledge_release_id": snapshot.session.knowledge_release_id,
            "evidence_unit_ids": list(dict.fromkeys(evidence_ids)),
        }
        try:
            comparison = resolved_comparison_engine.generate(
                **comparison_kwargs,
                language=str(snapshot.session.language),
            )
        except TypeError as exc:
            # Preserve compatibility with injected pre-language comparison engines.
            if "language" not in str(exc):
                raise
            comparison = resolved_comparison_engine.generate(**comparison_kwargs)
        training_repository.add_comparison(comparison)
        return comparison.model_dump(mode="json")

    @app.post("/sessions/{session_id}/turns")
    async def submit_turn(
        session_id: str,
        request: SubmitTurnRequest,
        background_tasks: BackgroundTasks,
        role: ActorRole = Depends(caller_role),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        if request.actor != role:
            raise HTTPException(
                status_code=403,
                detail="request actor must match X-Simulation-Role",
            )
        result = await asyncio.to_thread(service.submit_turn,
            session_id,
            text=request.text,
            actor=request.actor,
            idempotency_key=idempotency_key,
        )
        deferred_update_id = result.get("deferred_update_id")
        if result.get("state_update_pending") and deferred_update_id:
            background_tasks.add_task(
                service.finalize_patient_turn,
                session_id,
                deferred_update_id=deferred_update_id,
            )
        if result.get("therapist_memory_update_pending") and deferred_update_id:
            background_tasks.add_task(
                service.finalize_therapist_turn,
                session_id,
                deferred_update_id=deferred_update_id,
            )
        return result

    @app.post("/sessions/{session_id}/dual-cycle")
    async def run_dual_cycle(
        session_id: str,
        request: RunDualCycleRequest,
        role: ActorRole = Depends(caller_role),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if role not in {ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="dual-agent research requires researcher role")
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        return await asyncio.to_thread(service.run_dual_cycle,
            session_id,
            idempotency_key=idempotency_key,
            initial_therapist_utterance=request.initial_therapist_utterance,
        )

    @app.post("/sessions/{session_id}/patient-opening")
    async def generate_patient_opening(
        session_id: str,
        role: ActorRole = Depends(caller_role),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if role != ActorRole.HUMAN_THERAPIST:
            raise HTTPException(status_code=403, detail="patient opening requires trainee role")
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        return await asyncio.to_thread(service.generate_patient_opening,
            session_id, idempotency_key=idempotency_key
        )

    @app.post("/sessions/{session_id}/pause")
    def pause_session(
        session_id: str,
        _role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        return service.pause_session(session_id).public_view.model_dump(mode="json")

    @app.post("/sessions/{session_id}/resume")
    def resume_session(
        session_id: str,
        _role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        return service.resume_session(session_id).public_view.model_dump(mode="json")

    @app.post("/sessions/{session_id}/complete")
    async def complete_session(
        session_id: str,
        _role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        snapshot = await asyncio.to_thread(service.complete_session, session_id, defer_review=True)
        return snapshot.public_view.model_dump(mode="json")

    @app.post("/sessions/{session_id}/updates/retry", status_code=202)
    def retry_deferred_update(session_id: str, background_tasks: BackgroundTasks,
                                    _role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        update = service.retry_deferred_update(session_id)
        finalize = service.finalize_patient_turn if update["kind"] == "patient_state_update" else service.finalize_therapist_turn
        background_tasks.add_task(finalize, session_id, deferred_update_id=update["update_id"])
        return service.get_snapshot(session_id).public_view.model_dump(mode="json")

    @app.post("/sessions/{session_id}/review/retry", status_code=202)
    def retry_review(session_id: str, _role: ActorRole = Depends(caller_role)) -> dict[str, Any]:
        return service.retry_review(session_id).public_view.model_dump(mode="json")

    @app.post("/sessions/{session_id}/review")
    async def review_session(
        session_id: str,
        _role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        snapshot = await asyncio.to_thread(service.review_session, session_id)
        return snapshot.public_view.model_dump(mode="json")

    @app.post("/sessions/{session_id}/terminate")
    def terminate_session(
        session_id: str,
        request: TerminateSessionRequest,
        _role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        return service.terminate_session(
            session_id, reason=request.reason
        ).public_view.model_dump(mode="json")

    @app.get("/sessions/{session_id}/export")
    def export_session(
        session_id: str,
        role: ActorRole = Depends(caller_role),
        scope: ExportScope | None = None,
    ) -> dict[str, Any]:
        allowed_scope = scope_for_role(role, session_id)
        requested_scope = scope or allowed_scope
        rank = {ExportScope.PUBLIC: 0, ExportScope.INSTRUCTOR: 1, ExportScope.RESEARCHER: 2}
        if rank[requested_scope] > rank[allowed_scope]:
            raise HTTPException(status_code=403, detail="requested export scope is not permitted")
        return service.export_session(session_id, scope=requested_scope)

    @app.get("/sessions/{session_id}/reviewer-runtime-report")
    def reviewer_runtime_report(
        session_id: str,
        role: ActorRole = Depends(caller_role),
    ) -> dict[str, Any]:
        if role not in {ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="researcher role is required")
        return service.export_reviewer_runtime_report(session_id)

    @app.post("/sessions/{session_id}/feedback", status_code=201)
    def submit_feedback(
        session_id: str,
        request: SessionFeedbackRequest,
        actor: tuple[str, ActorRole] = Depends(caller_identity),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        subject_id, role = actor
        snapshot = service.get_snapshot(session_id)
        if SessionStatus(snapshot.session.status) not in {
            SessionStatus.COMPLETED,
            SessionStatus.TERMINATED,
            SessionStatus.FAILED,
        }:
            raise HTTPException(status_code=409, detail="feedback requires a terminal session")
        if request.rating is None and not request.dimension_ratings and not (request.comment or "").strip():
            raise HTTPException(status_code=422, detail="feedback requires a rating, dimension rating, or comment")
        content_review = request.consent.content_review or request.consent.knowledge_derivation
        research_use = request.consent.research_use or request.consent_to_research is True
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
            "research_use": research_use,
            "knowledge_derivation": request.consent.knowledge_derivation,
        }
        context, truncated = simulation_context(snapshot, include_text=content_review)
        client = require_contribution_client()
        try:
            consent = client.create_consent(
                subject_id=subject_id,
                role=contribution_role(role),
                capture_tier=tier,
                purposes=purposes,
                idempotency_key=f"simulation-feedback-consent:{subject_id}:{idempotency_key}",
            )
            captured = client.capture(
                subject_id=subject_id,
                role=contribution_role(role),
                endpoint="/v1/feedback/session",
                idempotency_key=f"simulation-feedback:{subject_id}:{idempotency_key}",
                payload={
                    "contribution_type": "session_feedback",
                    "origin": {
                        "product": "simulation_training",
                        "platform": "web",
                        "session_id": session_id,
                    },
                    "target": {"target_type": "session", "target_id": session_id, "target_version": "v1"},
                    "payload": {
                        "rating": request.rating,
                        "dimension_ratings": request.dimension_ratings,
                        "comment": (request.comment or "").strip() or None,
                        "context_truncated": truncated,
                    },
                    "consent_snapshot_id": consent["consent_snapshot_id"],
                    "context": context,
                    "provenance": {"source_service": "simulation_api_v1"},
                },
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc
        contribution_id = captured["contribution"]["contribution_id"]
        feedback = service.submit_feedback(
            session_id,
            submitted_by_role=role,
            rating=request.rating,
            dimension_ratings=request.dimension_ratings,
            comment=request.comment,
            consent_to_research=research_use,
            feedback_id=stable_id("feedback", subject_id, session_id, idempotency_key),
            metadata={
                "contribution_id": contribution_id,
                "consent_snapshot_id": consent["consent_snapshot_id"],
            },
        ).model_dump(mode="json")
        feedback["contribution"] = captured["contribution"]
        return feedback

    @app.get("/feedback")
    def list_feedback(
        role: ActorRole = Depends(caller_role),
        session_id: str | None = None,
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> dict[str, Any]:
        if role not in {ActorRole.RESEARCHER, ActorRole.SYSTEM}:
            raise HTTPException(status_code=403, detail="researcher role is required")
        rows = service.list_feedback(session_id=session_id, limit=limit)
        return {"count": len(rows), "feedback": rows}

    @app.get("/contributions")
    def list_user_contributions(
        actor: tuple[str, ActorRole] = Depends(caller_identity),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        subject_id, role = actor
        try:
            return require_contribution_client().list_contributions(
                subject_id=subject_id, role=contribution_role(role), limit=limit
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    @app.delete("/contributions/{contribution_id}")
    def withdraw_user_contribution(
        contribution_id: str,
        actor: tuple[str, ActorRole] = Depends(caller_identity),
    ) -> dict[str, Any]:
        subject_id, role = actor
        try:
            return require_contribution_client().withdraw(
                contribution_id=contribution_id,
                subject_id=subject_id,
                role=contribution_role(role),
            )
        except ContributionClientError as exc:
            raise contribution_error(exc) from exc

    return app
