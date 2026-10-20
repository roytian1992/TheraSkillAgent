"""SQLite/WAL persistence for Support Assistant sessions and turn jobs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contracts import JobStatus, SupportAuditEvent, SupportSessionRecord, SupportTurnJob, utc_now


class SupportSessionNotFoundError(KeyError):
    pass


class SupportSessionConflictError(RuntimeError):
    pass


class SQLiteSupportRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        bootstrap = sqlite3.connect(str(self.database_path), timeout=30)
        try:
            bootstrap.execute("PRAGMA busy_timeout=30000")
            bootstrap.execute("PRAGMA journal_mode=WAL")
        finally:
            bootstrap.close()
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS support_sessions (
                    session_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS support_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    job_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES support_sessions(session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS support_events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence),
                    FOREIGN KEY (session_id) REFERENCES support_sessions(session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS support_idempotency (
                    session_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    PRIMARY KEY (session_id, idempotency_key),
                    FOREIGN KEY (session_id) REFERENCES support_sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES support_jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_owner_updated
                  ON support_sessions(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_support_jobs_session
                  ON support_jobs(session_id, updated_at DESC);
                """
            )

    @staticmethod
    def _dump(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def create(self, record: SupportSessionRecord, events: list[SupportAuditEvent]) -> None:
        with self.transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO support_sessions VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.public.session_id,
                        record.owner_id,
                        str(record.public.status),
                        record.revision,
                        self._dump(record),
                        record.public.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SupportSessionConflictError(record.public.session_id) from exc
            self._insert_events(connection, events)

    @staticmethod
    def _insert_events(connection: sqlite3.Connection, events: list[SupportAuditEvent]) -> None:
        for event in events:
            connection.execute(
                "INSERT INTO support_events VALUES (?, ?, ?, ?)",
                (
                    event.session_id,
                    event.sequence,
                    event.event_id,
                    SQLiteSupportRepository._dump(event),
                ),
            )

    def get(self, session_id: str) -> SupportSessionRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM support_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise SupportSessionNotFoundError(session_id)
        return SupportSessionRecord.model_validate_json(row["record_json"])

    def list(self, owner_id: str, limit: int = 100) -> list[SupportSessionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record_json FROM support_sessions
                WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [SupportSessionRecord.model_validate_json(row["record_json"]) for row in rows]

    def statuses(self, owner_id: str, session_ids: list[str]) -> list[dict]:
        """Fetch only owned generation states, including sessions outside the recent list."""
        if not session_ids:
            return []
        placeholders = ",".join("?" for _ in session_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT record_json FROM support_sessions WHERE owner_id=? AND session_id IN ({placeholders})",
                (owner_id, *session_ids),
            ).fetchall()
        result = []
        for row in rows:
            public = json.loads(row["record_json"])["public"]
            result.append({"session_id": public["session_id"],
                           "status": public["generation"]["status"], "updated_at": public["updated_at"]})
        return result

    def get_job(self, job_id: str) -> SupportTurnJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_json FROM support_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return SupportTurnJob.model_validate_json(row["job_json"])

    def list_pending_jobs(self) -> list[SupportTurnJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_json FROM support_jobs WHERE status=? ORDER BY updated_at",
                ("pending",),
            ).fetchall()
        return [SupportTurnJob.model_validate_json(row["job_json"]) for row in rows]

    def list_active_jobs(self) -> list[SupportTurnJob]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_json FROM support_jobs WHERE status IN (?, ?) ORDER BY updated_at",
                (str(JobStatus.PENDING), str(JobStatus.RUNNING)),
            ).fetchall()
        return [SupportTurnJob.model_validate_json(row["job_json"]) for row in rows]

    def claim_job(self, job_id: str) -> SupportTurnJob | None:
        """Atomically claim a pending job so background paths cannot duplicate work."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT job_json FROM support_jobs WHERE job_id=? AND status=?",
                (job_id, str(JobStatus.PENDING)),
            ).fetchone()
            if row is None:
                return None
            current = SupportTurnJob.model_validate_json(row["job_json"])
            claimed = current.model_copy(update={"status": JobStatus.RUNNING, "updated_at": utc_now()})
            updated = connection.execute(
                "UPDATE support_jobs SET status=?, job_json=?, updated_at=? WHERE job_id=? AND status=?",
                (
                    str(claimed.status),
                    self._dump(claimed),
                    claimed.updated_at.isoformat(),
                    job_id,
                    str(JobStatus.PENDING),
                ),
            ).rowcount
            return claimed if updated == 1 else None

    def idempotent_job_id(self, session_id: str, idempotency_key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT job_id FROM support_idempotency
                WHERE session_id=? AND idempotency_key=?
                """,
                (session_id, idempotency_key),
            ).fetchone()
        return str(row["job_id"]) if row else None

    def submit_job(
        self,
        record: SupportSessionRecord,
        job: SupportTurnJob,
        event: SupportAuditEvent,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> None:
        with self.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE support_sessions
                SET status=?, revision=?, record_json=?, updated_at=?
                WHERE session_id=? AND revision=?
                """,
                (
                    str(record.public.status),
                    record.revision,
                    self._dump(record),
                    record.public.updated_at.isoformat(),
                    record.public.session_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise SupportSessionConflictError("support session revision changed")
            connection.execute(
                "INSERT INTO support_jobs VALUES (?, ?, ?, ?, ?)",
                (
                    job.job_id,
                    job.session_id,
                    str(job.status),
                    self._dump(job),
                    job.updated_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO support_idempotency VALUES (?, ?, ?)",
                (job.session_id, idempotency_key, job.job_id),
            )
            self._insert_events(connection, [event])

    def commit_job(
        self,
        record: SupportSessionRecord,
        job: SupportTurnJob,
        event: SupportAuditEvent,
        *,
        expected_revision: int,
    ) -> None:
        with self.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE support_sessions
                SET status=?, revision=?, record_json=?, updated_at=?
                WHERE session_id=? AND revision=?
                """,
                (
                    str(record.public.status),
                    record.revision,
                    self._dump(record),
                    record.public.updated_at.isoformat(),
                    record.public.session_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise SupportSessionConflictError("support session revision changed")
            connection.execute(
                "UPDATE support_jobs SET status=?, job_json=?, updated_at=? WHERE job_id=?",
                (str(job.status), self._dump(job), job.updated_at.isoformat(), job.job_id),
            )
            self._insert_events(connection, [event])

    def update(
        self,
        record: SupportSessionRecord,
        event: SupportAuditEvent,
        *,
        expected_revision: int,
    ) -> None:
        with self.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE support_sessions
                SET status=?, revision=?, record_json=?, updated_at=?
                WHERE session_id=? AND revision=?
                """,
                (
                    str(record.public.status),
                    record.revision,
                    self._dump(record),
                    record.public.updated_at.isoformat(),
                    record.public.session_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise SupportSessionConflictError("support session revision changed")
            self._insert_events(connection, [event])

    def list_events(self, session_id: str) -> list[SupportAuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM support_events WHERE session_id=? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return [SupportAuditEvent.model_validate_json(row["event_json"]) for row in rows]

    def delete(self, session_id: str) -> None:
        with self.transaction() as connection:
            deleted = connection.execute(
                "DELETE FROM support_sessions WHERE session_id=?", (session_id,)
            ).rowcount
        if deleted != 1:
            raise SupportSessionNotFoundError(session_id)
