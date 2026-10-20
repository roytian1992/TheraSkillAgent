"""Task summaries and reversible presentation metadata, separate from execution records."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .paths import data_root
from .text import redact_secrets


KINDS = ("workflow", "extraction", "expert", "scenario_batch")
TERMINAL = {"completed", "cancelled"}
LIVE = {"running", "queued", "pending", "cancel_requested"}
ARCHIVE_BLOCKED = LIVE | {"unavailable", "unknown"}
PRIORITY = {"needs_input": 0, "needs_approval": 1, "failed": 2, "partial": 3,
            "interrupted": 4, "unavailable": 5, "unknown": 6, "running": 7,
            "cancel_requested": 8, "pending": 9, "queued": 10}
SOURCE_SPECS = {
    "extraction": ("extraction", "/v1/extraction/jobs", {"X-Extraction-Role": "researcher"}),
    "expert": ("expert", "/jobs", {"X-Expert-Role": "expert", "X-Expert-User": "expert-local"}),
    "scenario_batch": ("simulation", "/scenarios/generate-batch/jobs", {"X-Simulation-Role": "researcher"}),
}


class HistoryMetadataStore:
    """Explicit origin and archive decisions; never rewrite or delete source jobs.

    ``set_records`` also supports a one-time, evidence-verified QA migration. It
    deliberately has no keyword classifier: a goal containing "test" is user data.
    """
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or data_root() / "tasks/history.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS task_history_metadata (
                key TEXT PRIMARY KEY, origin TEXT NOT NULL DEFAULT 'user',
                archived INTEGER NOT NULL DEFAULT 0, archive_reason TEXT,
                archived_status TEXT, archived_updated_at TEXT, changed_at TEXT NOT NULL)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def all(self) -> dict[str, dict]:
        with self.connect() as db:
            return {row["key"]: dict(row) for row in db.execute("SELECT * FROM task_history_metadata")}

    def set_records(self, records, *, origin=None, archived=None, reason=None):
        if origin is not None and origin not in {"user", "test"}:
            raise ValueError("origin must be user or test")
        records = list(records)
        for record in records:
            if record["key"].split(":", 1)[0] not in KINDS or ":" not in record["key"]:
                raise ValueError("Unknown task key")
            if archived and record.get("status") in ARCHIVE_BLOCKED:
                raise ValueError("A running task or a task with unknown state cannot be archived")
        changed_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            for record in records:
                db.execute("INSERT OR IGNORE INTO task_history_metadata (key,changed_at) VALUES (?,?)",
                           (record["key"], changed_at))
                if origin is not None:
                    db.execute("UPDATE task_history_metadata SET origin=?,changed_at=? WHERE key=?",
                               (origin, changed_at, record["key"]))
                if archived is not None:
                    db.execute("""UPDATE task_history_metadata SET archived=?,archive_reason=?,
                        archived_status=?,archived_updated_at=?,changed_at=? WHERE key=?""",
                        (int(archived), reason if archived else None, record.get("status"),
                         record.get("updated_at", ""), changed_at, record["key"]))
        return [record["key"] for record in records]


def _normalize(kind: str, row: dict) -> dict:
    task_id = str((row.get("id") if kind == "workflow" else row.get("job_id")) or "")
    if not task_id:
        raise ValueError("A task record has no identifier")
    encoded = quote(task_id, safe="")
    title_en = None
    if kind == "workflow":
        title = row.get("goal") or "Skill workflow"
        href = "/?workspace=workflow&task_id=" + encoded
    elif kind == "extraction":
        title = (row.get("plan") or {}).get("goal") or "Knowledge extraction"
        href = "/?workspace=extraction&job_id=" + encoded
    elif kind == "expert":
        title = row.get("title")
        if not title:
            title, title_en = (("回应审核", "Response review") if row.get("workflow") == "response_review"
                               else ("内容构建", "Content development"))
        href = "/theraskill/expert/?job_id=" + encoded
    else:
        title = (row.get("request") or {}).get("course_title") or "Course scenario generation"
        href = "/theraskill/simulation/?batch_job=" + encoded
    status = str(row.get("status") or "unknown")
    status = {"idle": "completed", "succeeded": "completed", "complete": "completed"}.get(status, status)
    return {"key": f"{kind}:{task_id}", "id": task_id, "kind": kind,
            "title": redact_secrets(str(title))[:300], "title_en": title_en,
            "status": status, "created_at": row.get("created_at") or "",
            "updated_at": row.get("updated_at") or row.get("created_at") or "", "href": href,
            "children": [], "parent_key": None, "archived": False, "origin": "user", "archive_reason": None}


def _extraction_children(task: dict) -> set[str]:
    """Only recorded creation checkpoints establish ownership, never matching titles."""
    checkpoints = task.get("checkpoints") or {}
    children = set()
    for plan in [checkpoints, *(checkpoints.get("previous_plans") or [])]:
        for slot in ("trial_job", "full_job"):
            value = plan.get(slot) or {}
            if value.get("job_id"):
                children.add("extraction:" + str(value["job_id"]))
    for entry in checkpoints.get("skill_history") or []:
        if (entry.get("tool") == "theraskill_extraction" and
                (entry.get("arguments") or {}).get("operation") == "start"):
            result = entry.get("result") or {}
            if isinstance(result, dict) and result.get("job_id"):
                children.add("extraction:" + str(result["job_id"]))
    return children


class TaskHistory:
    def __init__(self, task_store, services, metadata=None):
        self.task_store = task_store
        self.services = services
        self.metadata = metadata or HistoryMetadataStore()

    def _source(self, kind):
        service, path, headers = SOURCE_SPECS[kind]
        result, seen, errors = [], set(), []
        offset, limit = 0, 200
        try:
            while True:
                response = self.services.request(service, "GET", path, headers=headers,
                                                 params={"limit": limit, "offset": offset, "summary": "true"})
                if not response.is_success:
                    raise RuntimeError(f"{service} returned HTTP {response.status_code}")
                payload = response.json()
                rows = payload["jobs"]
                if not isinstance(rows, list):
                    raise ValueError("Invalid task list response")
                for row in rows:
                    item = _normalize(kind, row)
                    if item["key"] not in seen:
                        seen.add(item["key"])
                        result.append(item)
                if not rows or offset + len(rows) >= int(payload["total"]):
                    break
                if len(result) <= offset:
                    raise ValueError("Task source did not advance its page")
                offset += len(rows)
            if any(row["status"] == "unavailable" for row in result):
                errors.append({"kind": kind, "message": "Some execution states are unavailable; refresh to check their status."})
        except Exception as exc:
            errors.append({"kind": kind, "message": redact_secrets(str(exc))[:600] or "Task source unavailable"})
        return result, errors

    def collect(self):
        """Return flat, sanitized rows plus explicit source failures, without a top-N cap."""
        rows, errors, parents, seen = [], [], {}, set()
        try:
            offset = 0
            while True:
                page = self.task_store.list(limit=200, offset=offset)
                for task in page:
                    item = _normalize("workflow", task)
                    if item["key"] not in seen:
                        seen.add(item["key"])
                        rows.append(item)
                    for child in _extraction_children(task):
                        parents.setdefault(child, set()).add(item["key"])
                if len(page) < 200:
                    break
                offset += len(page)
        except Exception as exc:
            errors.append({"kind": "workflow", "message": redact_secrets(str(exc))[:600]})
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="task-history") as pool:
            futures = [pool.submit(self._source, kind) for kind in SOURCE_SPECS]
            for future in futures:
                source_rows, source_errors = future.result()
                rows.extend(source_rows)
                errors.extend(source_errors)
        metadata = self.metadata.all()
        for row in rows:
            record = metadata.get(row["key"], {})
            requested = bool(record.get("archived"))
            stale = requested and (row["status"] in LIVE or
                                   record.get("archived_status") != row["status"] or
                                   record.get("archived_updated_at") != row["updated_at"])
            row.update(origin=record.get("origin", "user"), archived=requested and not stale,
                       archive_reason=record.get("archive_reason"), archive_stale=stale)
            owners = parents.get(row["key"], set())
            if len(owners) == 1:
                row["parent_key"] = next(iter(owners))
        return rows, errors

    @staticmethod
    def _groups(rows, *, include_test):
        by_key = {row["key"]: {**row, "children": []} for row in rows}
        roots = []
        for row in by_key.values():
            parent = by_key.get(row["parent_key"])
            if parent is not None:
                parent["children"].append(row)
            else:
                roots.append(row)
        result = []
        for root in roots:
            members = [root, *root["children"]]
            # Changed archived test runs must not hide newly running work.
            visible = [row for row in members if include_test or row["origin"] != "test" or row["archive_stale"]]
            if not visible:
                continue
            root["children"] = [row for row in root["children"] if row in visible]
            live_members = [row for row in visible if not row["archived"]]
            root["own_archived"] = root["archived"]
            root["archived"] = not live_members
            relevant = live_members or visible
            active = [row["status"] for row in relevant if row["status"] not in TERMINAL]
            root["group_status"] = min(active, key=lambda value: PRIORITY.get(value, 6)) if active else root["status"]
            root["view"] = "archived" if root["archived"] else "active" if active else "history"
            root["sort_at"] = max(row["updated_at"] for row in relevant)
            for row in members:
                row["can_archive"] = row["status"] not in ARCHIVE_BLOCKED
                row["archive_blocked_reason"] = None if row["can_archive"] else "running_or_unavailable"
            root["can_archive"] = all(row["status"] not in ARCHIVE_BLOCKED for row in members)
            root["archive_blocked_reason"] = None if root["can_archive"] else "running_or_unavailable"
            result.append(root)
        return result

    def list(self, *, view="active", kind="", status="", q="", page=1, page_size=20, include_test=False):
        rows, errors = self.collect()
        groups = self._groups(rows, include_test=include_test)
        query = q.strip().casefold()

        def matches(group):
            # Match one member against all filters, retaining its parent context.
            return any((not kind or row["kind"] == kind) and
                       (not status or row["status"] == status) and
                       (not query or query in (row["title"] + " " + (row.get("title_en") or "") + " " + row["id"]).casefold())
                       for row in [group, *group["children"]])

        groups = [group for group in groups if matches(group)]
        counts = {name: sum(group["view"] == name for group in groups) for name in ("active", "history", "archived")}
        # The archived tab includes QA history on entry. Expose its exact count
        # without changing the current view's visibility or double-counting
        # mixed user/test groups whose child state can change their group view.
        all_groups = groups if include_test else [group for group in self._groups(rows, include_test=True) if matches(group)]
        counts_including_test = {name: sum(group["view"] == name for group in all_groups)
                                 for name in ("active", "history", "archived")}
        selected = sorted((group for group in groups if group["view"] == view),
                          key=lambda group: (group["sort_at"], group["key"]), reverse=True)
        start = (page - 1) * page_size
        return {"items": selected[start:start+page_size], "total": len(selected), "page": page, "page_size": page_size,
                "counts": counts, "counts_including_test": counts_including_test,
                "errors": errors, "count_unit": "task_groups", "supported_kinds": list(KINDS)}

    def archive(self, keys, *, archived):
        rows, errors = self.collect()
        by_key = {row["key"]: row for row in rows}
        selected = set(keys)
        if any(key not in by_key for key in selected):
            if errors:
                raise HTTPException(503, "Some task sources are unavailable; refresh before archiving")
            raise HTTPException(404, "Task record not found")
        # Do not archive a parent while its extraction source cannot confirm children.
        if archived and errors:
            raise HTTPException(503, "Task states could not be verified; refresh before archiving")
        selected.update(row["key"] for row in rows if row["parent_key"] in selected)
        records = [by_key[key] for key in sorted(selected)]
        if archived and any(row["status"] in ARCHIVE_BLOCKED for row in records):
            raise HTTPException(409, "Wait for running tasks and their child tasks to finish before archiving")
        return {"updated": self.metadata.set_records(records, archived=archived,
                                                      reason="user_archived" if archived else None)}


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: list[str] = Field(min_length=1, max_length=500)
    archived: bool


def create_router(task_store, services):
    history = TaskHistory(task_store, services)
    router = APIRouter()

    @router.get("/api/task-history")
    def task_history(view: Literal["active", "history", "archived"] = "active",
                     kind: Literal["", "workflow", "extraction", "expert", "scenario_batch"] = "",
                     status: str = Query(default="", max_length=40), q: str = Query(default="", max_length=500),
                     page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                     include_test: bool = False):
        return history.list(view=view, kind=kind, status=status, q=q, page=page,
                            page_size=page_size, include_test=include_test)

    @router.post("/api/task-history/archive")
    def archive(body: ArchiveRequest):
        return history.archive(body.keys, archived=body.archived)

    return router
