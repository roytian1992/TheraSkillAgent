"""Expert Studio API over the released support and simulation services."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from theraskill_agent.local_services import service_client
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from domains.theraskill.theraskill_contribution.client import (
    ContributionClientError,
    HTTPContributionClient,
)
from domains.theraskill.theraskill_contribution.contracts import (
    CandidateAdjudicationRequest,
    ExpertDecisionRequest,
    ExpertProfileCreate,
    ExpertProfileVerification,
    ReviewAssignmentCreate,
    ReleasePreviewRequest,
    ReleasePromotionRequest,
    ReleaseRollbackRequest,
)
from domains.theraskill.runtime import runtime_root
from domains.theraskill.observability import RequestMetrics
from domains.theraskill.metrics_middleware import MetricsMiddleware


from theraskill_agent.paths import resource_root, resource_path

ROOT = resource_root()


class ExpertAPISettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    database_path: Path = Field(default_factory=lambda: runtime_root() / "expert/expert.sqlite")
    support_api_url: str = Field(default_factory=lambda: os.environ.get("THERASKILL_SUPPORT_API_URL", "inprocess://support"))
    simulation_api_url: str = Field(default_factory=lambda: os.environ.get("THERASKILL_SIMULATION_API_URL", "inprocess://simulation"))
    contribution_api_url: str = Field(default_factory=lambda: os.environ.get("THERASKILL_CONTRIBUTION_API_URL", "inprocess://contribution"))
    contribution_client_override: Any | None = None
    api_token: str | None = Field(
        default_factory=lambda: os.environ.get("THERASKILL_EXPERT_API_TOKEN")
    )
    support_api_token: str | None = Field(
        default_factory=lambda: os.environ.get("THERASKILL_SUPPORT_API_TOKEN")
    )
    simulation_api_token: str | None = Field(
        default_factory=lambda: os.environ.get("THERASKILL_SIMULATION_API_TOKEN")
    )
    contribution_api_token: str | None = Field(
        default_factory=lambda: os.environ.get("THERASKILL_CONTRIBUTION_API_TOKEN")
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5175", "http://localhost:5175"]
    )


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow: Literal["response_review", "content_lab"]
    payload: dict[str, Any]


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: dict[str, Any]
    note: str = ""


class RegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1)


class ImportCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class ExpertIdentity(BaseModel):
    subject_id: str
    product_role: str
    contribution_role: str


GOVERNANCE_ROLES = {"knowledge_curator", "adjudicator", "release_manager", "admin"}


class KnowledgeProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_type: str = Field(min_length=1)
    claim_or_pattern: str = Field(min_length=1, max_length=12000)
    applicable_context: str = Field(min_length=1, max_length=8000)
    contraindications: str = Field(default="", max_length=8000)
    risk_level: Literal["low", "medium", "high"] = "low"
    evidence_source: str = Field(min_length=1)
    evidence_spans: list[dict[str, Any]] = Field(default_factory=list)
    source_evidence_tier: str = Field(min_length=1)
    expert_confidence: int = Field(ge=1, le=5)
    suggested_regression_cases: list[dict[str, Any]] = Field(default_factory=list)
    license_use_restrictions: str = Field(default="", max_length=4000)


class ExpertStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS expert_jobs (
                    job_id TEXT PRIMARY KEY, workflow TEXT NOT NULL,
                    support_session_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS expert_versions (
                    version_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    version_index INTEGER NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS expert_cases (
                    case_draft_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )

    def execute(self, sql: str, values: tuple = ()) -> int:
        with sqlite3.connect(self.path) as db:
            return db.execute(sql, values).rowcount

    def rows(self, sql: str, values: tuple = ()) -> list[sqlite3.Row]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return db.execute(sql, values).fetchall()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_prompt(workflow: str, payload: dict[str, Any]) -> str:
    if workflow == "response_review":
        return (
            "Perform a professional therapy-response review. Distinguish observed dialogue, "
            "generated rationale, and construction notes. Assess stage fit, strategy, boundary, "
            "evidence role, limitations, and give concrete revision suggestions. Input:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
    return (
        "Build a synthetic counseling dialogue teaching artifact. Clearly mark it synthetic; keep "
        "dialogue, rationale, and construction metadata separate. Follow the requested framework, "
        "roles, stage, goal, and turn count without inventing observed outcomes. Specification:\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def create_app(settings: ExpertAPISettings | None = None) -> FastAPI:
    cfg = settings or ExpertAPISettings()
    store = ExpertStore(cfg.database_path)
    contribution_client = cfg.contribution_client_override or HTTPContributionClient(
        cfg.contribution_api_url,
        api_token=cfg.contribution_api_token or "",
        timeout_seconds=660,
    )
    app = FastAPI(title="TheraSkill Expert Studio API", version="1.0.0")
    metrics = RequestMetrics("theraskill-expert")
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_role(role: str) -> None:
        if role not in {
            "expert", "knowledge_curator", "adjudicator", "release_manager",
            "instructor", "researcher", "admin",
        }:
            raise HTTPException(status_code=403, detail="expert role is required")

    def expert_identity(
        x_expert_role: str = Header(default="", alias="X-Expert-Role"),
        authorization: str = Header(default=""),
    ) -> str:
        if cfg.api_token:
            expected = f"Bearer {cfg.api_token}"
            if not secrets.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="invalid API token")
        require_role(x_expert_role)
        return x_expert_role

    def review_identity(
        x_expert_role: str = Header(default="", alias="X-Expert-Role"),
        x_expert_user: str = Header(default="", alias="X-Expert-User"),
        authorization: str = Header(default=""),
    ) -> ExpertIdentity:
        if cfg.api_token:
            expected = f"Bearer {cfg.api_token}"
            if not secrets.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="invalid API token")
        if x_expert_role not in {"expert", *GOVERNANCE_ROLES}:
            raise HTTPException(status_code=403, detail="verified review role is required")
        if not x_expert_user:
            raise HTTPException(status_code=401, detail="stable expert identity is required")
        return ExpertIdentity(
            subject_id=x_expert_user,
            product_role=x_expert_role,
            contribution_role=(
                "expert_reviewer" if x_expert_role == "expert" else x_expert_role
            ),
        )

    def contribution_request(
        identity: ExpertIdentity,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        expert_profile_id: str | None = None,
    ) -> Any:
        try:
            return contribution_client.request(
                path,
                method=method,
                subject_id=identity.subject_id,
                role=identity.contribution_role,
                payload=payload,
                expert_profile_id=expert_profile_id,
            )
        except ContributionClientError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503, detail=str(exc)
            ) from exc

    def upstream(method, base_url, path, **kwargs):
        with service_client(base_url, timeout=kwargs.pop('timeout', 30)) as client:
            return client.request(method, path, **kwargs)

    def support_headers(role: str = "user") -> dict[str, str]:
        headers = {"X-Support-User": "expert-studio", "X-Support-Role": role}
        if cfg.support_api_token:
            headers["Authorization"] = f"Bearer {cfg.support_api_token}"
        return headers

    def simulation_headers(role: str = "instructor") -> dict[str, str]:
        headers = {"X-Simulation-Role": role}
        if cfg.simulation_api_token:
            headers["Authorization"] = f"Bearer {cfg.simulation_api_token}"
        return headers

    def load_job(job_id: str) -> sqlite3.Row:
        rows = store.rows("SELECT * FROM expert_jobs WHERE job_id=?", (job_id,))
        if not rows:
            raise HTTPException(status_code=404, detail="expert job not found")
        return rows[0]

    @app.get("/health")
    def health() -> dict[str, Any]:
        try:
            support = upstream("GET", cfg.support_api_url, f"/health", timeout=5).json()
            simulation = upstream("GET", cfg.simulation_api_url, f"/health", timeout=5).json()
        except Exception:
            support, simulation = {"status": "offline"}, {"status": "offline"}
        return {"status": "ok", "support": support["status"], "simulation": simulation["status"]}

    @app.get("/metrics")
    def metrics_endpoint() -> dict[str, Any]:
        return metrics.snapshot()

    @app.post("/jobs", status_code=202)
    def create_job(
        request: CreateJobRequest,
        x_expert_role: str = Depends(expert_identity),
    ) -> dict[str, Any]:
        require_role(x_expert_role)
        with service_client(cfg.support_api_url, timeout=30) as client:
            session = client.post("/sessions", headers=support_headers(), json={
                "workflow": "response_review" if request.workflow == "response_review" else "synthetic_construction",
                "output_language": request.payload.get("output_language", "auto"),
            })
            session.raise_for_status()
            session_id = session.json()["session_id"]
            submitted = client.post(
                f"/sessions/{session_id}/messages",
                headers={**support_headers(), "Idempotency-Key": f"expert-{uuid.uuid4().hex}"},
                json={"text": job_prompt(request.workflow, request.payload)},
            )
            submitted.raise_for_status()
        job_id = f"expert_job_{uuid.uuid4().hex}"
        store.execute(
            "INSERT INTO expert_jobs VALUES (?, ?, ?, ?, ?)",
            (job_id, request.workflow, session_id, json.dumps(request.payload, ensure_ascii=False), now()),
        )
        return {"job_id": job_id, "workflow": request.workflow, "status": "pending"}

    @app.get("/jobs")
    def list_jobs(
        _role: str = Depends(expert_identity),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        require_role(_role)
        rows = store.rows("SELECT * FROM expert_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        states: dict[str, str] = {}
        try:
            response = upstream("GET", cfg.support_api_url, f"/sessions", params={"limit": 500}, headers=support_headers(), timeout=5)
            response.raise_for_status()
            states = {session["session_id"]: session["generation"]["status"]
                      for session in response.json()["sessions"]}
        except (httpx.HTTPError, KeyError, ValueError):
            pass
        return {"count": len(rows), "jobs": [
            {**dict(row), "status": states.get(row["support_session_id"], "unavailable")}
            for row in rows
        ]}

    @app.get("/jobs/{job_id}")
    def get_job(
        job_id: str,
        x_expert_role: str = Depends(expert_identity),
    ) -> dict[str, Any]:
        require_role(x_expert_role)
        row = load_job(job_id)
        session_id = row["support_session_id"]
        with service_client(cfg.support_api_url, timeout=30) as client:
            public = client.get(f"/sessions/{session_id}", headers=support_headers())
            public.raise_for_status()
            researcher = client.get(
                f"/sessions/{session_id}/export?scope=researcher",
                headers=support_headers("researcher"),
            )
            researcher.raise_for_status()
        session = public.json()
        export = researcher.json()
        assistant = next((m for m in reversed(session["messages"]) if m["role"] == "assistant"), None)
        trace = (export.get("researcher") or {}).get("turn_traces", [])[-1:] or [{}]
        versions = store.rows(
            "SELECT payload_json FROM expert_versions WHERE job_id=? ORDER BY version_index",
            (job_id,),
        )
        return {
            "job_id": job_id,
            "workflow": row["workflow"],
            "status": session["generation"]["status"],
            "input": json.loads(row["payload_json"]),
            "result": assistant,
            "provenance": trace[0],
            "versions": [json.loads(item["payload_json"]) for item in versions],
        }

    @app.post("/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, _role: str = Depends(expert_identity)) -> dict[str, Any]:
        row = load_job(job_id)
        response = upstream("POST", cfg.support_api_url, f"/sessions/{row['support_session_id']}/retry",
                              headers=support_headers(), json={
                                  "workflow": "response_review" if row["workflow"] == "response_review" else "synthetic_construction",
                                  "output_language": json.loads(row["payload_json"]).get("output_language", "auto"),
                              }, timeout=30)
        if not response.is_success:
            raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", "Retry failed"))
        return {"job_id": job_id, "workflow": row["workflow"], "status": "pending"}

    @app.delete("/jobs/{job_id}")
    def delete_job(
        job_id: str,
        _role: str = Depends(expert_identity),
    ) -> Response:
        row = load_job(job_id)
        response = httpx.delete(
            f"{cfg.support_api_url}/sessions/{row['support_session_id']}",
            headers=support_headers(),
            timeout=30,
        )
        if response.status_code not in {204, 404}:
            response.raise_for_status()
        store.execute("DELETE FROM expert_versions WHERE job_id=?", (job_id,))
        store.execute("DELETE FROM expert_jobs WHERE job_id=?", (job_id,))
        return Response(status_code=204)

    @app.post("/jobs/{job_id}/regenerate", status_code=202)
    def regenerate(job_id: str, request: RegenerateRequest, x_expert_role: str = Depends(expert_identity)) -> dict[str, Any]:
        row = load_job(job_id)
        with service_client(cfg.support_api_url, timeout=30) as client:
            response = client.post(
                f"/sessions/{row['support_session_id']}/messages",
                headers={**support_headers(), "Idempotency-Key": f"regen-{uuid.uuid4().hex}"},
                json={"text": "Revise only the requested portion and preserve source labels. " + request.instruction},
            )
            response.raise_for_status()
        return {"job_id": job_id, "status": "pending"}

    @app.post("/jobs/{job_id}/versions", status_code=201)
    def add_version(job_id: str, request: VersionRequest, x_expert_role: str = Depends(expert_identity)) -> dict[str, Any]:
        load_job(job_id)
        index = store.rows("SELECT COUNT(*) AS n FROM expert_versions WHERE job_id=?", (job_id,))[0]["n"] + 1
        value = {"version_index": index, "content": request.content, "note": request.note, "source_type": "expert-authored", "created_at": now()}
        store.execute(
            "INSERT INTO expert_versions VALUES (?, ?, ?, ?, ?)",
            (f"version_{uuid.uuid4().hex}", job_id, index, json.dumps(value, ensure_ascii=False), value["created_at"]),
        )
        return value

    @app.get("/jobs/{job_id}/export")
    def export_job(job_id: str, format: Literal["json", "markdown"] = "json", x_expert_role: str = Depends(expert_identity)) -> Response:
        payload = get_job(job_id, x_expert_role)
        if format == "json":
            return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json")
        result = payload.get("result") or {}
        text = f"# Expert Studio Export\n\n- workflow: `{payload['workflow']}`\n- job: `{job_id}`\n\n{result.get('text') or ''}\n"
        return Response(text, media_type="text/markdown")

    @app.post("/cases/import", status_code=201)
    def import_case(request: ImportCaseRequest, x_expert_role: str = Depends(expert_identity)) -> dict[str, Any]:
        response = httpx.get(
            f"{cfg.simulation_api_url}/sessions/{request.session_id}",
            headers=simulation_headers(),
            timeout=30,
        )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Simulation session not found; choose an existing completed session")
        response.raise_for_status()
        source = response.json()
        if source["public_view"]["status"] not in {"completed", "terminated", "failed"}:
            raise HTTPException(status_code=409, detail="only terminal sessions can become case drafts")
        value = {
            "schema_version": "theraskill_expert_case_draft_v1",
            "case_draft_id": f"case_draft_{uuid.uuid4().hex}",
            "title": request.title,
            "source_session_id": request.session_id,
            "source_type": "observed_simulation_session",
            "public_view": source["public_view"],
            "expert_commentary": [],
            "created_at": now(),
        }
        store.execute(
            "INSERT INTO expert_cases VALUES (?, ?, ?, ?)",
            (value["case_draft_id"], request.session_id, json.dumps(value, ensure_ascii=False), value["created_at"]),
        )
        return value

    @app.get("/cases")
    def list_cases(x_expert_role: str = Depends(expert_identity)) -> dict[str, Any]:
        rows = store.rows("SELECT payload_json FROM expert_cases ORDER BY created_at DESC")
        return {"count": len(rows), "cases": [json.loads(row["payload_json"]) for row in rows]}

    @app.delete("/cases/{case_draft_id}")
    def delete_case(
        case_draft_id: str,
        _role: str = Depends(expert_identity),
    ) -> Response:
        deleted = store.execute(
            "DELETE FROM expert_cases WHERE case_draft_id=?", (case_draft_id,)
        )
        if deleted != 1:
            raise HTTPException(status_code=404, detail="case draft not found")
        return Response(status_code=204)

    @app.get("/reviews/queue")
    def review_queue(
        identity: ExpertIdentity = Depends(review_identity),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        result = contribution_request(
            identity, f"/v1/review-queue?limit={limit}"
        )
        return result if isinstance(result, list) else []

    @app.get("/reviews/profile")
    def review_profile(identity: ExpertIdentity = Depends(review_identity)) -> dict[str, Any]:
        return contribution_request(identity, "/v1/expert-profiles/me")

    @app.get("/reviews/{contribution_id}")
    def review_workbench(
        contribution_id: str,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        return contribution_request(
            identity, f"/v1/contributions/{contribution_id}/workbench"
        )

    @app.post("/reviews/{contribution_id}/decision", status_code=201)
    def review_decision(
        contribution_id: str,
        decision: ExpertDecisionRequest,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role != "expert_reviewer":
            raise HTTPException(status_code=403, detail="expert reviewer role is required")
        return contribution_request(
            identity,
            f"/v1/contributions/{contribution_id}/expert-decision",
            method="POST",
            payload=decision.model_dump(mode="json"),
        )

    @app.post("/reviews/prepare")
    def prepare_review_queue(
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role != "admin":
            raise HTTPException(status_code=403, detail="admin role is required")
        return contribution_request(identity, "/v1/reviews/prepare", method="POST", payload={})

    @app.post("/reviews/profiles", status_code=201)
    def create_review_profile(
        profile: ExpertProfileCreate,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role != "admin":
            raise HTTPException(status_code=403, detail="admin role is required")
        return contribution_request(
            identity,
            "/v1/expert-profiles",
            method="POST",
            payload=profile.model_dump(mode="json"),
        )

    @app.post("/reviews/profiles/{expert_profile_id}/verify")
    def verify_review_profile(
        expert_profile_id: str,
        verification: ExpertProfileVerification,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role != "admin":
            raise HTTPException(status_code=403, detail="admin role is required")
        return contribution_request(
            identity,
            f"/v1/expert-profiles/{expert_profile_id}/verify",
            method="POST",
            payload=verification.model_dump(mode="json"),
        )

    @app.post("/reviews/{contribution_id}/assign", status_code=201)
    def assign_review(
        contribution_id: str,
        assignment: ReviewAssignmentCreate,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role != "admin":
            raise HTTPException(status_code=403, detail="admin role is required")
        return contribution_request(
            identity,
            f"/v1/contributions/{contribution_id}/assign",
            method="POST",
            payload=assignment.model_dump(mode="json"),
        )

    @app.post("/knowledge-proposals", status_code=201)
    def create_knowledge_proposal(
        proposal: KnowledgeProposalRequest,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role != "expert_reviewer":
            raise HTTPException(status_code=403, detail="expert reviewer role is required")
        profile = contribution_request(identity, "/v1/expert-profiles/me")
        if profile.get("status") != "verified":
            raise HTTPException(status_code=403, detail="verified expert profile is required")
        try:
            consent = contribution_client.create_consent(
                subject_id=identity.subject_id,
                role=identity.contribution_role,
                capture_tier="knowledge_derivation_allowed",
                purposes={
                    "service_delivery": True,
                    "product_analytics": False,
                    "content_review": True,
                    "research_use": False,
                    "knowledge_derivation": True,
                },
                idempotency_key=f"knowledge-consent-{uuid.uuid4().hex}",
            )
            return contribution_client.capture(
                subject_id=identity.subject_id,
                role=identity.contribution_role,
                endpoint="/v1/contributions/knowledge",
                idempotency_key=f"knowledge-proposal-{uuid.uuid4().hex}",
                expert_profile_id=profile["expert_profile_id"],
                payload={
                    "contribution_type": "knowledge_proposal",
                    "origin": {"product": "expert_studio", "platform": "web"},
                    "target": {"target_type": "knowledge_candidate", "target_id": "new"},
                    "payload": proposal.model_dump(mode="json"),
                    "consent_snapshot_id": consent["consent_snapshot_id"],
                    "context": {"operational": {"status": "expert_submitted"}},
                    "provenance": {
                        "expert_profile_id": profile["expert_profile_id"],
                        "promotion_blocked_until": "C6",
                    },
                },
            )
        except ContributionClientError as exc:
            raise HTTPException(status_code=exc.status_code or 503, detail=str(exc)) from exc

    @app.get("/governance/me")
    def governance_identity(
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        profile = None
        if identity.contribution_role in {"expert_reviewer", "adjudicator"}:
            try:
                profile = contribution_client.request(
                    "/v1/expert-profiles/me",
                    method="GET",
                    subject_id=identity.subject_id,
                    role=identity.contribution_role,
                )
            except ContributionClientError as exc:
                if exc.status_code != 404:
                    raise HTTPException(status_code=exc.status_code or 503, detail=str(exc)) from exc
        return {
            "subject_id": identity.subject_id,
            "product_role": identity.product_role,
            "contribution_role": identity.contribution_role,
            "expert_profile": profile,
        }

    @app.get("/governance/candidates")
    def governance_candidates(
        status: list[str] | None = Query(default=None),
        identity: ExpertIdentity = Depends(review_identity),
    ) -> list[dict[str, Any]]:
        if identity.contribution_role not in {"knowledge_curator", "adjudicator", "admin"}:
            raise HTTPException(status_code=403, detail="candidate governance role is required")
        suffix = ""
        if status:
            suffix = "?" + "&".join(f"status={value}" for value in status)
        result = contribution_request(identity, f"/v1/candidates{suffix}")
        return result if isinstance(result, list) else []

    @app.get("/governance/candidates/{candidate_id}")
    def governance_candidate(
        candidate_id: str,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role not in {"knowledge_curator", "adjudicator", "admin"}:
            raise HTTPException(status_code=403, detail="candidate governance role is required")
        return contribution_request(identity, f"/v1/candidates/{candidate_id}")

    @app.post("/governance/candidates/{candidate_id}/structure")
    def governance_structure_candidate(
        candidate_id: str,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        return contribution_request(
            identity, f"/v1/candidates/{candidate_id}/structure", method="POST", payload={}
        )

    @app.post("/governance/candidates/{candidate_id}/dedup-check")
    def governance_dedup_candidate(
        candidate_id: str,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        return contribution_request(
            identity, f"/v1/candidates/{candidate_id}/dedup-check", method="POST", payload={}
        )

    @app.post("/governance/candidates/{candidate_id}/adjudicate")
    def governance_adjudicate_candidate(
        candidate_id: str,
        request: CandidateAdjudicationRequest,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role != "adjudicator":
            raise HTTPException(status_code=403, detail="adjudicator role is required")
        profile = contribution_request(identity, "/v1/expert-profiles/me")
        if profile.get("status") != "verified":
            raise HTTPException(status_code=403, detail="verified adjudicator profile is required")
        return contribution_request(
            identity,
            f"/v1/candidates/{candidate_id}/adjudicate",
            method="POST",
            payload=request.model_dump(mode="json"),
            expert_profile_id=profile["expert_profile_id"],
        )

    @app.post("/governance/candidates/{candidate_id}/validate")
    def governance_validate_candidate(
        candidate_id: str,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        return contribution_request(
            identity, f"/v1/candidates/{candidate_id}/validate", method="POST", payload={}
        )

    @app.get("/governance/releases")
    def governance_releases(
        identity: ExpertIdentity = Depends(review_identity),
    ) -> list[dict[str, Any]]:
        if identity.contribution_role not in GOVERNANCE_ROLES:
            raise HTTPException(status_code=403, detail="release governance role is required")
        result = contribution_request(identity, "/v1/releases")
        return result if isinstance(result, list) else []

    @app.post("/governance/releases/preview", status_code=201)
    def governance_preview_release(
        request: ReleasePreviewRequest,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role not in {"knowledge_curator", "admin"}:
            raise HTTPException(status_code=403, detail="knowledge curator role is required")
        return contribution_request(
            identity,
            "/v1/releases/preview",
            method="POST",
            payload=request.model_dump(mode="json"),
        )

    @app.post("/governance/releases/promote")
    def governance_promote_release(
        request: ReleasePromotionRequest,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role not in {"release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release manager role is required")
        return contribution_request(
            identity,
            "/v1/releases/promote",
            method="POST",
            payload=request.model_dump(mode="json"),
        )

    @app.post("/governance/releases/{release_id}/rollback")
    def governance_rollback_release(
        release_id: str,
        request: ReleaseRollbackRequest,
        identity: ExpertIdentity = Depends(review_identity),
    ) -> dict[str, Any]:
        if identity.contribution_role not in {"release_manager", "admin"}:
            raise HTTPException(status_code=403, detail="release manager role is required")
        return contribution_request(
            identity,
            f"/v1/releases/{release_id}/rollback",
            method="POST",
            payload=request.model_dump(mode="json"),
        )

    return app
