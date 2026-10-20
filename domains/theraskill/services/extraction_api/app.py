"""Governed async transport for user-defined TheraSkill extraction plans.

The service deliberately keeps extraction outputs in a candidate workspace.
It never activates a knowledge release. Promotion remains an expert and
release-manager operation in the existing contribution workflow.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from domains.theraskill.runtime import runtime_root
from domains.theraskill.theraskill_contribution.extraction_import import import_extraction_units
from domains.theraskill.theraskill_contribution.repository import SQLiteContributionRepository
from plugins.theraskill.extraction_plan import ExtractionPlanError, validate_plan
from theraskill_agent.source_adapters import inspect_source, sample_source


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


class TrialReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "skipped"]
    sample_job_id: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
    review_token: str | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=2000)


class StartExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: dict[str, Any]
    limit: int | None = Field(default=None, ge=1, le=100000)
    request_id: str | None = Field(default=None, min_length=1, max_length=160)
    trial_review: TrialReviewRequest | None = None


def _plan_fingerprint(plan: dict[str, Any]) -> str:
    """Bind acceptance to execution settings, excluding IDs and display metadata."""
    canonical = validate_plan(plan)
    keys = ("goal", "unit_definition", "language", "target_schema", "evidence_required",
            "evidence_fields", "quality_rules", "operations", "semantic_review", "model_profile", "prompt_version")
    source = canonical.get("source") or {}
    binding = {key: canonical.get(key) for key in keys}
    binding["source_path"] = str(Path(source.get("path") or source.get("input_uri") or "").expanduser().resolve())
    return hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _model_fingerprint(config: dict[str, Any]) -> str:
    """Bind resolved request settings without persisting credentials or URLs."""
    return hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class InspectSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class SourceUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)


class SuggestPlanRequest(InspectSourceRequest):
    goal: str = Field(min_length=1, max_length=12000)
    language: str = Field(default="auto", pattern="^(auto|zh|en)$")


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
            if job.get("status") == "cancel_requested":
                job.update(status="cancelled", updated_at=datetime.now(timezone.utc).isoformat())
                self._jobs[job_id] = job
                self._save(job)
            elif job.get("status") in {"queued", "running"}:
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
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)

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

    def create(self, plan: dict[str, Any], limit: int | None, actor: str, request_id: str | None = None,
               trial_review: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return self._create(plan, limit, actor, request_id, trial_review)

    def _create(self, plan: dict[str, Any], limit: int | None, actor: str, request_id: str | None = None,
                trial_review: dict[str, Any] | None = None) -> dict[str, Any]:
        if request_id:
            with self._lock:
                for existing in self._jobs.values():
                    if existing.get("request_id") == request_id:
                        if (existing['plan'] != plan or existing['limit'] != limit or existing['actor'] != actor
                                or existing.get('trial_review_request') != trial_review):
                            raise ValueError("request_id was already used for different extraction inputs")
                        return dict(existing)
        job_id = f"extract_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        decision = self._validate_trial_review(plan, limit, trial_review) if trial_review is not None else None
        if decision:
            decision.update(recorded_at=now, confirmed_by=actor)
        job = {
            "job_id": job_id,
            "request_id": request_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "actor": actor,
            "plan": plan,
            "limit": limit,
            "trial_review_request": trial_review,
            "trial_review": decision,
            "expected_source_fingerprint": decision.get("source_fingerprint") if decision else None,
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

    def _review_context(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if not job.get("limit") or job.get("status") != "completed" or not job.get("counts", {}).get("units"):
            return None
        source_fingerprint = (job.get("source_info") or {}).get("fingerprint")
        model_fingerprint = (job.get("execution_binding") or {}).get("model_config_sha256")
        units_path = Path(str((job.get("artifacts") or {}).get("units") or ""))
        if not source_fingerprint or not model_fingerprint or not units_path.is_file():
            return None
        content = units_path.read_bytes()
        units = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
        if not units or any(unit.get("qa", {}).get("status") != "pass" for unit in units):
            return None
        context = {"sample_job_id": job["job_id"], "source_fingerprint": source_fingerprint,
                   "plan_fingerprint": _plan_fingerprint(job["plan"]),
                   "model_config_sha256": model_fingerprint,
                   "artifact_sha256": hashlib.sha256(content).hexdigest(), "sample_units": len(units)}
        context["review_token"] = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        return context

    def _validate_trial_review(self, plan: dict[str, Any], limit: int | None, value: dict[str, Any]) -> dict[str, Any]:
        review = TrialReviewRequest.model_validate(value).model_dump(exclude_none=True)
        if limit is not None:
            raise ValueError("Trial review decisions apply only to full extraction jobs")
        source = plan.get("source") or {}
        current = inspect_source(source.get("path") or source.get("input_uri")).fingerprint
        if review["decision"] == "skipped":
            if not review["note"].strip():
                raise ValueError("Explain why the sample extraction is being skipped")
            if review.get("sample_job_id") or review.get("review_token"):
                raise ValueError("Skipping a trial cannot also claim acceptance of a sample")
            return {**review, "source_fingerprint": current, "plan_fingerprint": _plan_fingerprint(plan)}
        sample = self._load(str(review.get("sample_job_id") or ""))
        context = self._review_context(sample) if sample else None
        if not context or context["review_token"] != review.get("review_token"):
            raise ValueError("Preview a completed, quality-checked sample before accepting it")
        if context["plan_fingerprint"] != _plan_fingerprint(plan):
            raise ValueError("The source, goal, schema or quality settings changed; run and review a new sample")
        if context["source_fingerprint"] != current:
            raise ValueError("Source contents changed after the sample; run and review a new sample")
        if context["model_config_sha256"] != _model_fingerprint(self._model_settings()):
            raise ValueError("Model configuration changed after the sample; run and review a new sample")
        return {**review, **context}

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._load(job_id)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in sorted(self._jobs.values(), key=lambda j: (j["created_at"],j["job_id"]), reverse=True)[offset:offset+limit]]

    def count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def retry(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._load(job_id)
            if job is None:
                return None
            if job["status"] not in {"failed", "partial"}:
                raise ValueError("only failed or partial extraction jobs can be retried")
            job.update(status="queued", error=None, retry_failed_records=True,
                       retry_count=int(job.get("retry_count") or 0) + 1,
                       updated_at=datetime.now(timezone.utc).isoformat())
            self._jobs[job_id] = job
            self._save(job)
            self._executor.submit(self._run, job_id)
            return dict(job)

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
        return {"job_id": job_id, "status": job["status"], "items": items,
                "trial_review_context": self._review_context(job)}

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

    def _model_settings(self) -> dict[str, Any]:
        import yaml
        from domains.theraskill.theraskill_llm import merged_model_config

        path = self.settings.model_config_path
        if not path.is_file():
            raise FileNotFoundError(f"model config does not exist: {path}")
        return merged_model_config({}, yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def _run(self, job_id: str) -> None:
        try:
            with self._lock:
                queued = self._load(job_id) or {}
                if queued.get("status") == "cancel_requested":
                    self._update(job_id, status="cancelled")
                    return
                if queued.get("status") not in {"queued", "running"}:
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
            from domains.theraskill.theraskill_llm import OpenAICompatibleClient
            from theraskill_agent.extraction.contracts import ExtractionPlan
            from theraskill_agent.extraction.executor import _source_id, build_manifest, extract_records, load_source_records

            model_config = self._model_settings()
            client = OpenAICompatibleClient(model_config)
            extraction_plan = ExtractionPlan.from_mapping(plan)
            fingerprint = inspect_source(source_path).fingerprint
            records = load_source_records(source_path)
            if inspect_source(source_path).fingerprint != fingerprint:
                raise ValueError("source changed while loading records; create a new extraction job")
            work_dir = self.settings.work_root / job_id
            work_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_units_path = work_dir / "knowledge_units.checkpoint.jsonl"
            checkpoint_failures_path = work_dir / "failures.checkpoint.jsonl"
            previous_fingerprint = (job.get("source_info") or {}).get("fingerprint") or job.get("expected_source_fingerprint")
            if previous_fingerprint and previous_fingerprint != fingerprint:
                raise ValueError("source changed since this job started; create a new extraction job")
            record_digest = hashlib.sha256()
            for record in records:
                value = record.to_dict() if hasattr(record, "to_dict") else record
                record_digest.update(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode() + b"\n")
            binding = {"model_config_sha256": _model_fingerprint(model_config), "plan_sha256": _plan_fingerprint(plan),
                       "source_sha256": fingerprint, "records_sha256": record_digest.hexdigest(), "limit": job.get("limit")}
            expected_model = (job.get("trial_review") or {}).get("model_config_sha256")
            if expected_model and expected_model != binding["model_config_sha256"]:
                raise ValueError("Model configuration changed after the accepted sample; run and review a new sample")
            if job.get("execution_binding") and job["execution_binding"] != binding:
                raise ValueError("Model configuration, plan or source records changed; create a new extraction job")
            if not job.get("execution_binding") and any(path.exists() and path.stat().st_size for path in (checkpoint_units_path, checkpoint_failures_path)):
                raise ValueError("Existing checkpoints have no verified execution binding; create a new extraction job")
            self._update(job_id, execution_binding=binding)
            if job.get("retry_failed_records"):
                # Keep the previous attempt for audit while retrying only records
                # that have not produced a successful unit.
                archive = work_dir / f"failures.attempt-{job.get('retry_count', 1)}.jsonl"
                if checkpoint_failures_path.exists() and not archive.exists():
                    shutil.copyfile(checkpoint_failures_path, archive)
                checkpoint_failures_path.write_text("", encoding="utf-8")
                self._update(job_id, retry_failed_records=False)
            checkpoint_units: list[dict[str, Any]] = []
            checkpoint_failures: list[dict[str, Any]] = []
            for checkpoint_path, target in ((checkpoint_units_path, checkpoint_units), (checkpoint_failures_path, checkpoint_failures)):
                if checkpoint_path.exists():
                    try:
                        with checkpoint_path.open("r", encoding="utf-8") as handle:
                            target.extend(json.loads(line) for line in handle if line.strip())
                    except (OSError, json.JSONDecodeError) as exc:
                        raise ValueError("Extraction checkpoint is damaged; preserve it and create a new job") from exc
            cohort = list(enumerate(records))
            if job.get("limit"):
                limit = int(job["limit"])
                indices = ({round(i * (len(records) - 1) / (limit - 1)) for i in range(limit)} if limit > 1 and records else {0})
                cohort = [(i, record) for i, record in cohort if i in indices]
            source_ids = {_source_id(record, i) for i, record in cohort}
            if len(source_ids) != len(cohort):
                raise ValueError("Source record IDs are not unique; use an adapter with stable unique record IDs")
            checkpoint_ids = [str(item.get("source_id") or "") for item in (*checkpoint_units, *checkpoint_failures) if isinstance(item, dict)]
            if (len(checkpoint_ids) != len(checkpoint_units) + len(checkpoint_failures)
                    or len(set(checkpoint_ids)) != len(checkpoint_ids) or not set(checkpoint_ids) <= source_ids):
                raise ValueError("Extraction checkpoint contains invalid or duplicate source records; create a new job")
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

            self._update(job_id, source_info={"path": str(source_path), "record_count": len(records), "fingerprint": fingerprint}, progress={"processed": len(completed_source_ids), "units": len(checkpoint_units), "failures": len(checkpoint_failures), "resumed": bool(completed_source_ids)})
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
            if inspect_source(source_path).fingerprint != fingerprint:
                raise ValueError("source changed during extraction; create a new extraction job")
            units_path = work_dir / "knowledge_units.jsonl"
            failures_path = work_dir / "failures.jsonl"
            manifest_path = work_dir / "manifest.json"
            units_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in all_units), encoding="utf-8")
            failures_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in all_failures), encoding="utf-8")
            with self._lock:
                cancelled = (self._load(job_id) or {}).get("status") == "cancel_requested"
                manifest = build_manifest(extraction_plan, source_path, units=all_units, failures=all_failures,
                                          model_name=client.model, expected_records=len(cohort), cancelled=cancelled)
                if manifest["source"]["fingerprint"] != fingerprint:
                    raise ValueError("source changed during extraction; create a new extraction job")
                manifest["execution_binding"] = binding
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                final_status = "cancelled" if cancelled else ("completed" if manifest["status"] == "complete" else "partial")
                self._update(
                    job_id,
                    status=final_status,
                    counts={"units": len(all_units), "failures": len(all_failures)},
                    artifacts={"units": str(units_path), "failures": str(failures_path), "manifest": str(manifest_path)},
                )
        except Exception as exc:  # noqa: BLE001 - preserve failure in job state
            with self._lock:
                cancelled = (self._load(job_id) or {}).get("status") in {"cancel_requested", "cancelled"}
                self._update(job_id, status="cancelled" if cancelled else "failed", error=f"{type(exc).__name__}: {exc}")


def create_app(settings: ExtractionAPISettings | None = None) -> FastAPI:
    settings = settings or ExtractionAPISettings()
    store = ExtractionJobStore(settings)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for job in list(store._jobs.values()):
                if job['status'] in {'queued', 'running'}:
                    store.cancel(job['job_id'])
            store._executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="TheraSkill Dynamic Extraction API", version="1.0", lifespan=lifespan)

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
        try:
            return store.create(plan, request.limit, x_extraction_user.strip() or actor_role, request.request_id,
                                request.trial_review.model_dump(exclude_none=True) if request.trial_review else None)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/extraction/inspect")
    def inspect_input(
        request: InspectSourceRequest,
        x_extraction_role: str = Header(default="", alias="X-Extraction-Role"),
    ) -> dict[str, Any]:
        require_role(x_extraction_role)
        try:
            return {"source": inspect_source(request.path).to_dict(), "samples": [item.to_dict() for item in sample_source(request.path, limit=5)], "sample_limit": 5}
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

    @app.post("/v1/extraction/plans/suggest")
    def suggest_plan(request: SuggestPlanRequest,
                     x_extraction_role: str = Header(default="", alias="X-Extraction-Role")) -> dict[str, Any]:
        require_role(x_extraction_role)
        from plugins.theraskill.extraction_plan import plan_with_active_model
        try:
            samples = [item.to_dict() for item in sample_source(request.path, limit=5)]
            plan = plan_with_active_model(request.goal, samples, source={"path": request.path}, language=request.language)
            return {"plan": plan, "sample_count": len(samples), "requires_confirmation": True}
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"Plan generation failed: {exc}. Your input is preserved; retry after checking the model service.") from exc

    @app.get("/v1/extraction/jobs")
    def list_jobs(x_extraction_role: str = Header(default="", alias="X-Extraction-Role"),
                  limit: int = Query(default=100, ge=1, le=500),
                  offset: int = Query(default=0, ge=0), summary: bool = False) -> dict[str, Any]:
        require_role(x_extraction_role)
        jobs = store.list(limit=limit, offset=offset)
        if summary:
            jobs = [{**{key: job.get(key) for key in ("job_id", "status", "created_at", "updated_at")},
                     "plan": {"goal": (job.get("plan") or {}).get("goal", "")}} for job in jobs]
        return {"jobs": jobs, "total": store.count()}

    @app.post("/v1/extraction/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, x_extraction_role: str = Header(default="", alias="X-Extraction-Role")) -> dict[str, Any]:
        require_role(x_extraction_role)
        try:
            job = store.retry(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=404, detail="Extraction job not found")
        return job

    @app.get("/v1/extraction/jobs/{job_id}")
    def get_job(job_id: str, x_extraction_role: str = Header(default="", alias="X-Extraction-Role")) -> dict[str, Any]:
        require_role(x_extraction_role)
        job = store.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="extraction job not found")
        return job

    @app.get("/v1/extraction/jobs/{job_id}/preview")
    def preview_job(job_id: str, limit: int = Query(default=5, ge=1, le=100), x_extraction_role: str = Header(default="", alias="X-Extraction-Role")) -> dict[str, Any]:
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
