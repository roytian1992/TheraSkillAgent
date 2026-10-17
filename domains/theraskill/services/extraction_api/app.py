"""Governed async transport for user-defined TheraSkill extraction plans.

The service deliberately keeps extraction outputs in a candidate workspace.
It never activates a knowledge release. Promotion remains an expert and
release-manager operation in the existing contribution workflow.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from domains.theraskill.runtime import runtime_root
from domains.theraskill.theraskill_contribution.extraction_import import import_extraction_units
from domains.theraskill.theraskill_contribution.repository import SQLiteContributionRepository
from plugins.theraskill.extraction_plan import ExtractionPlanError, validate_plan
from theraskill_agent.source_adapters import inspect_source


from theraskill_agent.paths import resource_root, resource_path

ROOT = resource_root()
DEFAULT_EXTRACTION_ROOT = resource_path("extraction")
ALLOWED_ROLES = {"researcher", "knowledge_curator", "admin"}


class ExtractionAPISettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, protected_namespaces=())
    extraction_project_root: Path = Field(
        default_factory=lambda: Path(os.environ.get("THERASKILL_EXTRACTION_PROJECT_ROOT", str(DEFAULT_EXTRACTION_ROOT)))
    )
    model_config_path: Path = Field(
        default_factory=lambda: Path(
            os.environ.get(
                "THERASKILL_EXTRACTION_MODEL_CONFIG",
                str(resource_path("configs/theraskill/retrieval.yaml")),
            )
        )
    )
    work_root: Path = Field(default_factory=lambda: runtime_root() / "extraction/jobs")
    contribution_database_path: Path = Field(
        default_factory=lambda: Path(
            os.environ.get(
                "THERASKILL_CONTRIBUTION_DATABASE",
                str(runtime_root() / "contribution/contributions.sqlite"),
            )
        )
    )
    max_workers: int = Field(default=2, ge=1, le=8)


class StartExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: dict[str, Any]
    limit: int | None = Field(default=None, ge=1, le=100000)


class InspectSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class SourceUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)


class ExtractionJobStore:
    def __init__(self, settings: ExtractionAPISettings) -> None:
        self.settings = settings
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="theraskill-extract")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._recover_jobs()

    def _recover_jobs(self) -> None:
        """Load persisted jobs and resume work interrupted by a restart."""
        for path in sorted(self.settings.work_root.glob("*/job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job_id = str(job.get("job_id") or path.parent.name)
            if not job_id:
                continue
            if job.get("status") in {"queued", "running"}:
                job["status"] = "queued"
                job["recovered_at"] = datetime.now(timezone.utc).isoformat()
                job["updated_at"] = job["recovered_at"]
                path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self._jobs[job_id] = job
                self._executor.submit(self._run, job_id)
            else:
                self._jobs[job_id] = job

    def _path(self, job_id: str) -> Path:
        return self.settings.work_root / job_id / "job.json"

    def _save(self, job: dict[str, Any]) -> None:
        path = self._path(str(job["job_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            if job_id in self._jobs:
                return dict(self._jobs[job_id])
        path = self._path(job_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def create(self, plan: dict[str, Any], limit: int | None, actor: str) -> dict[str, Any]:
        job_id = f"extract_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "actor": actor,
            "plan": plan,
            "limit": limit,
            "counts": {"units": 0, "failures": 0},
            "artifacts": {},
            "error": None,
            "release_eligibility": "candidate_only",
            "progress": {"processed": 0, "units": 0, "failures": 0},
        }
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        self._executor.submit(self._run, job_id)
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._load(job_id)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id) or self._load(job_id)
            if not job:
                return None
            if job["status"] in {"queued", "running"}:
                job["status"] = "cancel_requested"
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._jobs[job_id] = job
                self._save(job)
            return dict(job)

    def preview(self, job_id: str, limit: int = 5) -> dict[str, Any] | None:
        job = self._load(job_id)
        if not job:
            return None
        artifact = job.get("artifacts", {}).get("units")
        if not artifact:
            return {"job_id": job_id, "status": job["status"], "items": []}
        path = Path(artifact)
        items: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        items.append(json.loads(line))
                    if len(items) >= limit:
                        break
        except (OSError, json.JSONDecodeError) as exc:
            return {"job_id": job_id, "status": job["status"], "items": [], "error": str(exc)}
        return {"job_id": job_id, "status": job["status"], "items": items}

    def handoff(self, job_id: str, actor: str) -> dict[str, Any] | None:
        """Create a governed candidate pack without activating a release."""
        with self._lock:
            job = self._jobs.get(job_id) or self._load(job_id)
            if not job:
                return None
            if job.get("status") not in {"completed", "partial"}:
                raise ValueError("only completed or partial extraction jobs can be handed off")
            if job.get("governance_status") == "imported":
                return dict(job)
            units_path = Path(str((job.get("artifacts") or {}).get("units") or ""))
            if not units_path.exists():
                raise FileNotFoundError("knowledge_units.jsonl is unavailable")
            candidate_path = self.settings.work_root / job_id / "candidate_pack.jsonl"
            candidate_count = 0
            with units_path.open("r", encoding="utf-8") as source, candidate_path.open("w", encoding="utf-8") as target:
                for line in source:
                    if not line.strip():
                        continue
                    unit = json.loads(line)
                    candidate = {
                        "candidate_id": f"{job_id}_{candidate_count:06d}",
                        "status": "EXTRACTION_READY",
                        "source_job_id": job_id,
                        "knowledge_type": unit.get("knowledge_type", "dynamic_knowledge"),
                        "content": unit.get("content") or {},
                        "evidence": unit.get("evidence") or {},
                        "qa": unit.get("qa") or {},
                        "provenance": unit.get("provenance") or {},
                    }
                    target.write(json.dumps(candidate, ensure_ascii=False) + "\n")
                    candidate_count += 1
            units_for_governance: list[dict[str, Any]] = []
            with candidate_path.open("r", encoding="utf-8") as candidate_file:
                for line in candidate_file:
                    if line.strip():
                        units_for_governance.append(json.loads(line))
            manifest = {}
            manifest_path = (job.get("artifacts") or {}).get("manifest")
            if manifest_path and Path(manifest_path).exists():
                try:
                    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    manifest = {}
            repository = SQLiteContributionRepository(self.settings.contribution_database_path)
            candidate_ids = import_extraction_units(
                repository,
                job_id=job_id,
                actor=actor,
                units=units_for_governance,
                source=dict(manifest.get("source") or {}),
            )
            artifacts = dict(job.get("artifacts") or {})
            artifacts["candidate_pack"] = str(candidate_path)
            job.update({
                "handoff_status": "ready_for_candidate_review",
                "governance_status": "imported",
                "handoff_actor": actor,
                "handoff_at": datetime.now(timezone.utc).isoformat(),
                "candidate_count": candidate_count,
                "candidate_ids": candidate_ids,
                "governance_database": str(self.settings.contribution_database_path),
                "artifacts": artifacts,
            })
            self._jobs[job_id] = job
            self._save(job)
            return dict(job)

    def _update(self, job_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            job.update(updates)
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save(job)
            return dict(job)

    def _run(self, job_id: str) -> None:
        try:
            queued = self._load(job_id) or {}
            if queued.get("status") == "cancel_requested":
                self._update(job_id, status="cancelled")
                return
            job = self._update(job_id, status="running")
            plan = validate_plan(job["plan"])
            source_path = Path(str((plan.get("source") or {}).get("path") or (plan.get("source") or {}).get("input_uri") or ""))
            if not source_path.exists():
                raise FileNotFoundError(f"source path does not exist: {source_path}")
            project_root = self.settings.extraction_project_root.resolve()
            if not project_root.exists():
                raise FileNotFoundError(f"extraction project does not exist: {project_root}")
            if str(project_root / "scripts") not in sys.path:
                sys.path.insert(0, str(project_root / "scripts"))
            # The extraction project supplies the generic executor/contracts,
            # while TheraSkillAgent owns provider routing for the model client.
            from _common.theraskill_llm import load_yaml
            from domains.theraskill.theraskill_llm import OpenAICompatibleClient, merged_model_config
            from theraskill_agent.extraction.contracts import ExtractionPlan
            from theraskill_agent.extraction.executor import build_manifest, extract_records, load_source_records

            model_config_path = self.settings.model_config_path
            if not model_config_path.exists():
                raise FileNotFoundError(f"model config does not exist: {model_config_path}")
            model_file = load_yaml(model_config_path)
            model_config = merged_model_config({}, model_file)
            client = OpenAICompatibleClient(model_config)
            extraction_plan = ExtractionPlan.from_mapping(plan)
            records = load_source_records(source_path)
            work_dir = self.settings.work_root / job_id
            work_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_units_path = work_dir / "knowledge_units.checkpoint.jsonl"
            checkpoint_failures_path = work_dir / "failures.checkpoint.jsonl"
            checkpoint_units: list[dict[str, Any]] = []
            checkpoint_failures: list[dict[str, Any]] = []
            for checkpoint_path, target in ((checkpoint_units_path, checkpoint_units), (checkpoint_failures_path, checkpoint_failures)):
                if checkpoint_path.exists():
                    try:
                        with checkpoint_path.open("r", encoding="utf-8") as handle:
                            target.extend(json.loads(line) for line in handle if line.strip())
                    except (OSError, json.JSONDecodeError):
                        target.clear()
            completed_source_ids = {str(item.get("source_id")) for item in (*checkpoint_units, *checkpoint_failures) if item.get("source_id")}
            checkpoint_lock = threading.Lock()

            def on_progress(progress: dict[str, Any]) -> None:
                if progress.get("result") and progress.get("item"):
                    path = checkpoint_units_path if progress["result"] == "unit" else checkpoint_failures_path
                    with checkpoint_lock, path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(progress["item"], ensure_ascii=False) + "\n")
                self._update(job_id, progress={
                    "processed": progress.get("processed", 0),
                    "units": len(checkpoint_units) + int(progress.get("units", 0)),
                    "failures": len(checkpoint_failures) + int(progress.get("failures", 0)),
                    "source_id": progress.get("source_id"),
                    "resumed": bool(completed_source_ids),
                })

            self._update(job_id, source_info={"path": str(source_path), "record_count": len(records), "fingerprint": build_manifest(extraction_plan, source_path, units=[], failures=[], model_name=client.model)["source"]["fingerprint"]}, progress={"processed": len(completed_source_ids), "units": len(checkpoint_units), "failures": len(checkpoint_failures), "resumed": bool(completed_source_ids)})
            units, failures = extract_records(
                records,
                extraction_plan,
                client,
                limit=job.get("limit"),
                max_prompt_tokens=int(model_config.get("max_prompt_tokens") or 24000),
                should_cancel=lambda: str((self._load(job_id) or {}).get("status")) == "cancel_requested",
                on_progress=on_progress,
                skip_source_ids=completed_source_ids,
            )
            all_units = checkpoint_units + units
            all_failures = checkpoint_failures + failures
            units_path = work_dir / "knowledge_units.jsonl"
            failures_path = work_dir / "failures.jsonl"
            manifest_path = work_dir / "manifest.json"
            units_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in all_units), encoding="utf-8")
            failures_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in all_failures), encoding="utf-8")
            manifest = build_manifest(extraction_plan, source_path, units=all_units, failures=all_failures, model_name=client.model)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            final_status = "cancelled" if str((self._load(job_id) or {}).get("status")) == "cancel_requested" else ("completed" if not all_failures else "partial")
            self._update(
                job_id,
                status=final_status,
                counts={"units": len(all_units), "failures": len(all_failures)},
                artifacts={"units": str(units_path), "failures": str(failures_path), "manifest": str(manifest_path)},
            )
        except Exception as exc:  # noqa: BLE001 - preserve failure in job state
            self._update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")


def create_app(settings: ExtractionAPISettings | None = None) -> FastAPI:
    settings = settings or ExtractionAPISettings()
    store = ExtractionJobStore(settings)
    app = FastAPI(title="TheraSkill Dynamic Extraction API", version="1.0")

    def require_role(role: str) -> str:
        role = role.strip().lower()
        if role not in ALLOWED_ROLES:
            raise HTTPException(status_code=403, detail="researcher, knowledge_curator or admin role is required")
        return role

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "theraskill-extraction", "project_root": str(settings.extraction_project_root)}

    @app.post("/v1/extraction/jobs", status_code=202)
    def start_job(
        request: StartExtractionRequest,
        x_extraction_role: str = Header(default="", alias="X-Extraction-Role"),
        x_extraction_user: str = Header(default="", alias="X-Extraction-User"),
    ) -> dict[str, Any]:
        actor_role = require_role(x_extraction_role)
        try:
            plan = validate_plan(request.plan)
        except ExtractionPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        plan_source = plan.get("source") or {}
        if not plan_source.get("path") and not plan_source.get("input_uri"):
            raise HTTPException(status_code=422, detail="plan.source.path or plan.source.input_uri is required")
        return store.create(plan, request.limit, x_extraction_user.strip() or actor_role)

    @app.post("/v1/extraction/inspect")
    def inspect_input(
        request: InspectSourceRequest,
        x_extraction_role: str = Header(default="", alias="X-Extraction-Role"),
    ) -> dict[str, Any]:
        require_role(x_extraction_role)
        try:
            return {"source": inspect_source(request.path).to_dict()}
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/extraction/sources")
    def upload_source(
        request: SourceUploadRequest,
        x_extraction_role: str = Header(default="", alias="X-Extraction-Role"),
    ) -> dict[str, Any]:
        require_role(x_extraction_role)
        safe_name = Path(request.filename).name or "uploaded_source.txt"
        upload_root = settings.work_root / "uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        path = upload_root / f"{uuid.uuid4().hex[:12]}_{safe_name}"
        path.write_text(request.text, encoding="utf-8")
        try:
            info = inspect_source(path).to_dict()
        except (OSError, ValueError, TypeError) as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"path": str(path), "filename": safe_name, "source": info}

    @app.get("/v1/extraction/jobs/{job_id}")
    def get_job(job_id: str, x_extraction_role: str = Header(default="", alias="X-Extraction-Role")) -> dict[str, Any]:
        require_role(x_extraction_role)
        job = store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="extraction job not found")
        return job

    @app.get("/v1/extraction/jobs/{job_id}/preview")
    def preview_job(job_id: str, limit: int = Query(default=5, ge=1, le=50), x_extraction_role: str = Header(default="", alias="X-Extraction-Role")) -> dict[str, Any]:
        require_role(x_extraction_role)
        preview = store.preview(job_id, limit)
        if not preview:
            raise HTTPException(status_code=404, detail="extraction job not found")
        return preview

    @app.post("/v1/extraction/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, x_extraction_role: str = Header(default="", alias="X-Extraction-Role")) -> dict[str, Any]:
        require_role(x_extraction_role)
        job = store.cancel(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="extraction job not found")
        return job

    @app.post("/v1/extraction/jobs/{job_id}/handoff")
    def handoff_job(
        job_id: str,
        x_extraction_role: str = Header(default="", alias="X-Extraction-Role"),
        x_extraction_user: str = Header(default="", alias="X-Extraction-User"),
    ) -> dict[str, Any]:
        actor_role = require_role(x_extraction_role)
        try:
            job = store.handoff(job_id, x_extraction_user.strip() or actor_role)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not job:
            raise HTTPException(status_code=404, detail="extraction job not found")
        return job

    app.state.extraction_store = store
    app.state.extraction_settings = settings
    return app
