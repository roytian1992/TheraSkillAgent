"""Durable file-backed jobs for course scenario generation."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScenarioBatchJobStore:
    def __init__(self, root: str | Path, runner: Callable[[str, dict[str, Any]], dict[str, Any]], *, max_workers: int = 1, runner_reports_progress: bool = False, job_prefix: str = "scenario_batch") -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.runner_reports_progress = runner_reports_progress
        self.job_prefix = job_prefix
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="theraskill-scenario-batch")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._recover()

    def _path(self, job_id: str) -> Path:
        return self.root / job_id / "job.json"

    def _save(self, job: dict[str, Any]) -> None:
        path = self._path(str(job["job_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _recover(self) -> None:
        for path in self.root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job_id = str(job.get("job_id") or path.parent.name)
            if job.get("status") in {"queued", "running"}:
                job["status"] = "queued"
                job["recovered_at"] = _now()
                self._jobs[job_id] = job
                self._save(job)
                self._executor.submit(self._run, job_id)
            else:
                self._jobs[job_id] = job

    def _update(self, job_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            job.update(updates)
            job["updated_at"] = _now()
            self._save(job)
            return dict(job)

    def create(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        job_id = f"{self.job_prefix}_{uuid.uuid4().hex[:16]}"
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "actor": actor,
            "request": dict(payload),
            "progress": {"processed": 0, "total": int(payload.get("count") or 1)},
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        self._executor.submit(self._run, job_id)
        return dict(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._jobs[job_id]) if job_id in self._jobs else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)[:limit]]

    def retry(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            review_pending = job.get("status") == "completed" and ((job.get("result") or {}).get("course_review") or {}).get("status") == "needs_review"
            if job.get("status") not in {"failed", "partial"} and not review_pending:
                raise ValueError("only failed or partial batch jobs can be retried")
            job["status"] = "queued"
            job["error"] = None
            job["retry_count"] = int(job.get("retry_count") or 0) + 1
            job["updated_at"] = _now()
            self._save(job)
            self._executor.submit(self._run, job_id)
            return dict(job)

    def _run(self, job_id: str) -> None:
        try:
            self._update(job_id, status="running", started_at=_now())
            payload = dict(self._jobs[job_id].get("request") or {})
            result = (self.runner(job_id, payload, lambda **updates: self._update(job_id, **updates))
                      if self.runner_reports_progress else self.runner(job_id, payload))
            progress = {**self._jobs[job_id].get("progress", {}), "processed": int(result.get("count") or 1), "total": int(payload.get("count") or 1)}
            self._update(job_id, status="completed", progress=progress, result=result, finished_at=_now())
        except Exception as exc:  # noqa: BLE001
            status = "partial" if int((self._jobs[job_id].get("progress") or {}).get("processed") or 0) else "failed"
            from theraskill_agent.text import redact_secrets
            self._update(job_id, status=status, error=redact_secrets(f"{type(exc).__name__}: {exc}"), finished_at=_now())

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
