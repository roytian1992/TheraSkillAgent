"""SQLite/WAL persistence for simulation sessions, events, and snapshots."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import SessionEvent, SessionFeedback, SessionSnapshot, SimulationSession


class SessionNotFoundError(KeyError):
    pass


class SessionConflictError(RuntimeError):
    pass


class SQLiteSessionRepository:
    def __init__(self, database_path: str | Path, *, initialize: bool = True) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if initialize:
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
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    session_json TEXT NOT NULL,
                    latest_event_sequence INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    session_id TEXT NOT NULL,
                    through_event_sequence INTEGER NOT NULL,
                    snapshot_id TEXT NOT NULL UNIQUE,
                    snapshot_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, through_event_sequence),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS idempotency_results (
                    session_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, idempotency_key),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    feedback_json TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS review_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_review_jobs_status_updated
                  ON review_jobs(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id, created_at);
                """
            )

    @staticmethod
    def _dump(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def create(self, snapshot: SessionSnapshot, events: list[SessionEvent]) -> None:
        if not events:
            raise ValueError("session creation requires at least one event")
        expected_sequences = list(range(len(events)))
        if [event.sequence for event in events] != expected_sequences:
            raise ValueError("initial event sequence must start at zero and be contiguous")
        session = snapshot.session
        with self.transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        str(session.status),
                        self._dump(session),
                        events[-1].sequence,
                        session.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SessionConflictError(f"session already exists: {session.session_id}") from exc
            for event in events:
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    (
                        event.session_id,
                        event.sequence,
                        event.event_id,
                        str(event.event_type),
                        self._dump(event),
                    ),
                )
            connection.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                (
                    session.session_id,
                    snapshot.through_event_sequence,
                    snapshot.snapshot_id,
                    self._dump(snapshot),
                ),
            )

    def get_session(self, session_id: str) -> SimulationSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_json FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return SimulationSession.model_validate_json(row["session_json"])

    def get_latest_snapshot(self, session_id: str) -> SessionSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM snapshots
                WHERE session_id=? ORDER BY through_event_sequence DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        return SessionSnapshot.model_validate_json(row["snapshot_json"])

    def list_events(self, session_id: str) -> list[SessionEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM events WHERE session_id=? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        if not rows and not self.exists(session_id):
            raise SessionNotFoundError(session_id)
        return [SessionEvent.model_validate_json(row["event_json"]) for row in rows]

    def exists(self, session_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return row is not None

    def list_latest_snapshots(self, *, limit: int = 100) -> list[SessionSnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sn.snapshot_json
                FROM snapshots sn
                JOIN (
                    SELECT session_id, MAX(through_event_sequence) AS max_sequence
                    FROM snapshots GROUP BY session_id
                ) latest
                  ON latest.session_id = sn.session_id
                 AND latest.max_sequence = sn.through_event_sequence
                ORDER BY json_extract(sn.snapshot_json, '$.session.updated_at') DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [SessionSnapshot.model_validate_json(row["snapshot_json"]) for row in rows]

    def enqueue_review_job(self, session_id: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        job_id = f"review_{uuid.uuid4().hex}"
        with self.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone() is None:
                raise SessionNotFoundError(session_id)
            existing = connection.execute(
                "SELECT job_id FROM review_jobs WHERE session_id=?", (session_id,)
            ).fetchone()
            if existing:
                return str(existing["job_id"])
            connection.execute(
                "INSERT INTO review_jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, session_id, "pending", 0, None, now, now),
            )
        return job_id

    def get_review_job(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_jobs WHERE session_id=?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_pending_review_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_jobs WHERE status='pending' ORDER BY updated_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_review_job(self, job_id: str) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM review_jobs WHERE job_id=? AND status='pending'",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """UPDATE review_jobs
                   SET status='running', attempts=attempts+1, updated_at=?, error=NULL
                   WHERE job_id=? AND status='pending'""",
                (now, job_id),
            ).rowcount
            if updated != 1:
                return None
            claimed = dict(row)
            claimed.update(status="running", attempts=int(row["attempts"]) + 1, updated_at=now, error=None)
            return claimed

    def finish_review_job(self, job_id: str, *, status: str, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE review_jobs SET status=?, error=?, updated_at=? WHERE job_id=?",
                (status, error[:2000] if error else None, now, job_id),
            )

    def recover_stale_review_jobs(self, stale_seconds: float = 900.0) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(30.0, stale_seconds))).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            return connection.execute(
                """UPDATE review_jobs
                   SET status='pending', error='review_interrupted_by_restart', updated_at=?
                   WHERE status='running' AND updated_at < ?""",
                (now, cutoff),
            ).rowcount

    def get_idempotency_result(self, session_id: str, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM idempotency_results WHERE session_id=? AND idempotency_key=?",
                (session_id, key),
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def add_feedback(self, feedback: SessionFeedback) -> SessionFeedback:
        with self.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (feedback.session_id,)
            ).fetchone() is None:
                raise SessionNotFoundError(feedback.session_id)
            existing = connection.execute(
                "SELECT feedback_json FROM feedback WHERE feedback_id=?",
                (feedback.feedback_id,),
            ).fetchone()
            if existing:
                stored = SessionFeedback.model_validate_json(existing["feedback_json"])
                if self._dump(stored) != self._dump(feedback):
                    raise SessionConflictError("feedback id reused with different content")
                return stored
            connection.execute(
                "INSERT INTO feedback VALUES (?, ?, ?, ?)",
                (
                    feedback.feedback_id,
                    feedback.session_id,
                    feedback.created_at.isoformat(),
                    self._dump(feedback),
                ),
            )
        return feedback

    def get_feedback(self, feedback_id: str) -> SessionFeedback | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT feedback_json FROM feedback WHERE feedback_id=?",
                (feedback_id,),
            ).fetchone()
        return SessionFeedback.model_validate_json(row["feedback_json"]) if row else None

    def delete(self, session_id: str) -> None:
        with self.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone() is None:
                raise SessionNotFoundError(session_id)
            for table in (
                "feedback",
                "idempotency_results",
                "snapshots",
                "events",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE session_id=?", (session_id,)
                )
            connection.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))

    def list_feedback(self, *, session_id: str | None = None, limit: int = 500) -> list[SessionFeedback]:
        with self._connect() as connection:
            if session_id:
                rows = connection.execute(
                    "SELECT feedback_json FROM feedback WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT feedback_json FROM feedback ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [SessionFeedback.model_validate_json(row["feedback_json"]) for row in rows]

    def commit(
        self,
        snapshot: SessionSnapshot,
        events: list[SessionEvent],
        *,
        expected_prior_sequence: int,
        idempotency_key: str | None = None,
        idempotency_result: dict[str, Any] | None = None,
    ) -> None:
        if not events:
            raise ValueError("commit requires at least one event")
        if events[0].sequence != expected_prior_sequence + 1:
            raise ValueError("event sequence does not follow expected prior sequence")
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT latest_event_sequence FROM sessions WHERE session_id=?",
                (snapshot.session.session_id,),
            ).fetchone()
            if current is None:
                raise SessionNotFoundError(snapshot.session.session_id)
            if current["latest_event_sequence"] != expected_prior_sequence:
                raise SessionConflictError(
                    f"stale session write: expected sequence {expected_prior_sequence}, "
                    f"found {current['latest_event_sequence']}"
                )
            for event in events:
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    (
                        event.session_id,
                        event.sequence,
                        event.event_id,
                        str(event.event_type),
                        self._dump(event),
                    ),
                )
            session = snapshot.session
            connection.execute(
                """
                UPDATE sessions
                SET status=?, session_json=?, latest_event_sequence=?, updated_at=?
                WHERE session_id=?
                """,
                (
                    str(session.status),
                    self._dump(session),
                    events[-1].sequence,
                    session.updated_at.isoformat(),
                    session.session_id,
                ),
            )
            connection.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                (
                    session.session_id,
                    snapshot.through_event_sequence,
                    snapshot.snapshot_id,
                    self._dump(snapshot),
                ),
            )
            if idempotency_key and idempotency_result is not None:
                connection.execute(
                    "INSERT INTO idempotency_results VALUES (?, ?, ?)",
                    (session.session_id, idempotency_key, self._dump(idempotency_result)),
                )
