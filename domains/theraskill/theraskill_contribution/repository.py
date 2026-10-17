"""SQLite/WAL repository for immutable contribution records and lifecycle events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Any, Iterator, Protocol

from .contracts import (
    CandidateAdjudication,
    CandidateDecision,
    CandidateRegressionValidation,
    ConsentRecord,
    ContextSnapshot,
    Contribution,
    ContributionEvent,
    CorrectionProposal,
    ExpertDecision,
    ExpertProfile,
    KnowledgeCandidate,
    KnowledgeRelease,
    ReleaseFailure,
    ReleaseFailureReplay,
    PrivacyDerivation,
    RegressionCase,
    DomainMemory,
    MemoryEvent,
    ReviewAssignment,
    ReviewTriage,
)


class ContributionNotFoundError(KeyError):
    pass


class ContributionConflictError(RuntimeError):
    pass


class ContributionRepository(Protocol):
    """Storage contract; production deployments may provide a PostgreSQL implementation."""

    def save_consent(self, record: ConsentRecord) -> None: ...
    def idempotent_consent(self, subject_id: str, key: str) -> tuple[str, str] | None: ...
    def save_consent_idempotency(self, subject_id: str, key: str, request_sha256: str, consent_snapshot_id: str) -> None: ...
    def get_consent(self, consent_snapshot_id: str) -> ConsentRecord: ...
    def list_consents(self, subject_id: str) -> list[ConsentRecord]: ...
    def revoke_consent(self, record: ConsentRecord) -> None: ...
    def find_context(self, subject_id: str, checksum: str, tier: str) -> ContextSnapshot | None: ...
    def save_context(self, snapshot: ContextSnapshot) -> None: ...
    def get_context(self, context_snapshot_id: str) -> ContextSnapshot: ...
    def idempotent_result(self, subject_id: str, key: str) -> tuple[str, str] | None: ...
    def create(self, contribution: Contribution, event: ContributionEvent, *, idempotency_key: str, request_sha256: str) -> None: ...
    def get(self, contribution_id: str) -> Contribution: ...
    def list(self, *, subject_id: str | None = None, research_only: bool = False, limit: int | None = 100) -> list[Contribution]: ...
    def list_events(self, contribution_id: str) -> list[ContributionEvent]: ...
    def update_projection(self, contribution: Contribution, event: ContributionEvent) -> None: ...
    def save_privacy_derivation(self, derivation: PrivacyDerivation) -> None: ...
    def get_privacy_derivation(self, contribution_id: str) -> PrivacyDerivation | None: ...
    def save_expert_profile(self, profile: ExpertProfile) -> None: ...
    def get_expert_profile(self, expert_profile_id: str) -> ExpertProfile: ...
    def get_expert_profile_by_subject(self, subject_id: str) -> ExpertProfile | None: ...
    def update_expert_profile(self, profile: ExpertProfile) -> None: ...
    def save_triage(self, triage: ReviewTriage) -> None: ...
    def get_triage(self, contribution_id: str) -> ReviewTriage | None: ...
    def save_assignment(self, assignment: ReviewAssignment) -> None: ...
    def get_assignment(self, assignment_id: str) -> ReviewAssignment: ...
    def get_active_assignment(self, contribution_id: str) -> ReviewAssignment | None: ...
    def update_assignment(self, assignment: ReviewAssignment) -> None: ...
    def save_decision(self, decision: ExpertDecision) -> None: ...
    def list_decisions(self, contribution_id: str) -> list[ExpertDecision]: ...
    def save_correction(self, proposal: CorrectionProposal) -> None: ...
    def save_regression_case(self, case: RegressionCase) -> None: ...
    def save_knowledge_candidate(self, candidate: KnowledgeCandidate) -> None: ...
    def import_extraction_candidate(
        self,
        consent: ConsentRecord,
        contribution: Contribution,
        event: ContributionEvent,
        candidate: KnowledgeCandidate,
        *,
        idempotency_key: str,
        request_sha256: str,
    ) -> None: ...
    def get_knowledge_candidate(self, candidate_id: str) -> KnowledgeCandidate: ...
    def list_knowledge_candidates(self, *, statuses: set[str] | None = None) -> list[KnowledgeCandidate]: ...
    def transition_knowledge_candidate(
        self,
        candidate: KnowledgeCandidate,
        decision: CandidateDecision,
        *,
        adjudication: CandidateAdjudication | None = None,
        validation: CandidateRegressionValidation | None = None,
    ) -> None: ...
    def list_candidate_decisions(self, candidate_id: str) -> list[CandidateDecision]: ...
    def save_knowledge_release(self, release: KnowledgeRelease, event: dict[str, Any]) -> None: ...
    def create_preview_release(
        self,
        release: KnowledgeRelease,
        event: dict[str, Any],
        transitions: list[tuple[KnowledgeCandidate, CandidateDecision]],
    ) -> None: ...
    def get_knowledge_release(self, release_id: str) -> KnowledgeRelease: ...
    def list_knowledge_releases(self) -> list[KnowledgeRelease]: ...
    def update_knowledge_release(self, release: KnowledgeRelease, event: dict[str, Any]) -> None: ...
    def promote_knowledge_release(
        self,
        release: KnowledgeRelease,
        event: dict[str, Any],
        transitions: list[tuple[KnowledgeCandidate, CandidateDecision]],
        *,
        superseded: KnowledgeRelease | None = None,
    ) -> None: ...
    def rollback_knowledge_release(
        self,
        rolled_back: KnowledgeRelease,
        reactivated: KnowledgeRelease | None,
        event: dict[str, Any],
    ) -> None: ...
    def save_release_failure(self, failure: ReleaseFailure) -> None: ...
    def get_release_failure(self, failure_id: str) -> ReleaseFailure: ...
    def list_release_failures(self, release_id: str | None = None) -> list[ReleaseFailure]: ...
    def save_failure_replay(self, failure: ReleaseFailure, replay: ReleaseFailureReplay) -> None: ...
    def create_memory(self, memory: DomainMemory, event: MemoryEvent) -> None: ...
    def get_memory(self, memory_id: str) -> DomainMemory: ...
    def list_memories(self, subject_id: str) -> list[DomainMemory]: ...
    def update_memory(self, memory: DomainMemory, event: MemoryEvent) -> None: ...
    def list_memory_events(self, memory_id: str) -> list[MemoryEvent]: ...


class SQLiteContributionRepository:
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
        with self.transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS consent_records (
                    consent_snapshot_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    capture_tier TEXT NOT NULL,
                    research_use INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    granted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consent_events (
                    consent_snapshot_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(consent_snapshot_id, sequence),
                    FOREIGN KEY(consent_snapshot_id) REFERENCES consent_records(consent_snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS consent_idempotency (
                    subject_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    consent_snapshot_id TEXT NOT NULL,
                    PRIMARY KEY(subject_id, idempotency_key),
                    FOREIGN KEY(consent_snapshot_id) REFERENCES consent_records(consent_snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS context_snapshots (
                    context_snapshot_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    checksum_sha256 TEXT NOT NULL,
                    capture_tier TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(subject_id, checksum_sha256, capture_tier)
                );
                CREATE TABLE IF NOT EXISTS contributions (
                    contribution_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    contribution_type TEXT NOT NULL,
                    consent_snapshot_id TEXT NOT NULL,
                    context_snapshot_id TEXT,
                    privacy_status TEXT NOT NULL,
                    workflow_status TEXT NOT NULL,
                    contribution_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(consent_snapshot_id) REFERENCES consent_records(consent_snapshot_id),
                    FOREIGN KEY(context_snapshot_id) REFERENCES context_snapshots(context_snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS contribution_events (
                    contribution_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(contribution_id, sequence),
                    FOREIGN KEY(contribution_id) REFERENCES contributions(contribution_id)
                );
                CREATE TABLE IF NOT EXISTS contribution_idempotency (
                    subject_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    contribution_id TEXT NOT NULL,
                    PRIMARY KEY(subject_id, idempotency_key),
                    FOREIGN KEY(contribution_id) REFERENCES contributions(contribution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_contribution_owner_created
                    ON contributions(subject_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_contribution_type_created
                    ON contributions(contribution_type, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_consent_owner_granted
                    ON consent_records(subject_id, granted_at DESC);
                CREATE TABLE IF NOT EXISTS privacy_derivations (
                    privacy_derivation_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL UNIQUE,
                    original_context_snapshot_id TEXT,
                    redacted_context_snapshot_id TEXT,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(contribution_id) REFERENCES contributions(contribution_id),
                    FOREIGN KEY(original_context_snapshot_id) REFERENCES context_snapshots(context_snapshot_id),
                    FOREIGN KEY(redacted_context_snapshot_id) REFERENCES context_snapshots(context_snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS expert_profiles (
                    expert_profile_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_triage (
                    triage_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL UNIQUE,
                    priority INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(contribution_id) REFERENCES contributions(contribution_id)
                );
                CREATE TABLE IF NOT EXISTS review_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL,
                    expert_profile_id TEXT NOT NULL,
                    reviewer_subject_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(contribution_id) REFERENCES contributions(contribution_id),
                    FOREIGN KEY(expert_profile_id) REFERENCES expert_profiles(expert_profile_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_review_active_assignment
                    ON review_assignments(contribution_id) WHERE status='assigned';
                CREATE INDEX IF NOT EXISTS idx_review_assignee
                    ON review_assignments(reviewer_subject_id, status, created_at DESC);
                CREATE TABLE IF NOT EXISTS expert_annotations (
                    decision_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL,
                    assignment_id TEXT NOT NULL,
                    expert_profile_id TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(contribution_id) REFERENCES contributions(contribution_id),
                    FOREIGN KEY(assignment_id) REFERENCES review_assignments(assignment_id)
                );
                CREATE TABLE IF NOT EXISTS correction_proposals (
                    correction_proposal_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS regression_cases (
                    regression_case_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_candidates (
                    knowledge_candidate_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status='expert_reviewed_candidate'),
                    production_release_id TEXT CHECK(production_release_id IS NULL),
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS remediation_links (
                    remediation_link_id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(decision_id, lane)
                );
                CREATE TABLE IF NOT EXISTS domain_memories (
                    memory_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    confirmation_status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    deletion_status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_domain_memory_subject_status
                    ON domain_memories(subject_id, confirmation_status, expires_at);
                CREATE TABLE IF NOT EXISTS memory_events (
                    memory_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(memory_id, sequence),
                    FOREIGN KEY(memory_id) REFERENCES domain_memories(memory_id)
                );
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (2);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (3);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (4);
                """
            )
            self._migrate_v5(db)
            self._migrate_v6(db)

    def _migrate_v5(self, db: sqlite3.Connection) -> None:
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=5").fetchone():
            return
        db.executescript(
            """
            ALTER TABLE knowledge_candidates RENAME TO knowledge_candidates_v4;
            CREATE TABLE knowledge_candidates (
                knowledge_candidate_id TEXT PRIMARY KEY,
                contribution_id TEXT NOT NULL,
                decision_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                production_release_id TEXT,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO knowledge_candidates (
                knowledge_candidate_id, contribution_id, decision_id, status,
                production_release_id, record_json, created_at, updated_at
            )
            SELECT knowledge_candidate_id, contribution_id, decision_id, status,
                   production_release_id, record_json, created_at, created_at
            FROM knowledge_candidates_v4;
            DROP TABLE knowledge_candidates_v4;
            CREATE INDEX idx_candidate_status_updated
                ON knowledge_candidates(status, updated_at DESC);

            CREATE TABLE candidate_decisions (
                candidate_decision_id TEXT PRIMARY KEY,
                knowledge_candidate_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                actor_subject_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(knowledge_candidate_id)
                    REFERENCES knowledge_candidates(knowledge_candidate_id)
            );
            CREATE INDEX idx_candidate_decision_lineage
                ON candidate_decisions(knowledge_candidate_id, created_at);

            CREATE TABLE candidate_adjudications (
                adjudication_id TEXT PRIMARY KEY,
                knowledge_candidate_id TEXT NOT NULL,
                expert_profile_id TEXT NOT NULL,
                adjudicator_subject_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(knowledge_candidate_id)
                    REFERENCES knowledge_candidates(knowledge_candidate_id)
            );
            CREATE TABLE candidate_regression_validations (
                validation_id TEXT PRIMARY KEY,
                knowledge_candidate_id TEXT NOT NULL,
                status TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(knowledge_candidate_id)
                    REFERENCES knowledge_candidates(knowledge_candidate_id)
            );

            CREATE TABLE knowledge_releases (
                release_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                manifest_sha256 TEXT,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX idx_knowledge_release_status_created
                ON knowledge_releases(status, created_at DESC);
            CREATE TABLE release_events (
                release_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_subject_id TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(release_id, sequence),
                FOREIGN KEY(release_id) REFERENCES knowledge_releases(release_id)
            );
            CREATE TABLE release_promotion_links (
                release_id TEXT NOT NULL,
                knowledge_candidate_id TEXT NOT NULL,
                promoted_at TEXT NOT NULL,
                PRIMARY KEY(release_id, knowledge_candidate_id),
                FOREIGN KEY(release_id) REFERENCES knowledge_releases(release_id),
                FOREIGN KEY(knowledge_candidate_id)
                    REFERENCES knowledge_candidates(knowledge_candidate_id)
            );
            INSERT INTO schema_migrations(version) VALUES (5);
            """
        )

    def _migrate_v6(self, db: sqlite3.Connection) -> None:
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=6").fetchone():
            return
        db.executescript(
            """
            CREATE TABLE release_failures (
                failure_id TEXT PRIMARY KEY,
                release_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                error_code TEXT NOT NULL,
                status TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(release_id) REFERENCES knowledge_releases(release_id)
            );
            CREATE INDEX idx_release_failure_release_status
                ON release_failures(release_id, status, created_at DESC);
            CREATE TABLE release_failure_replays (
                replay_id TEXT PRIMARY KEY,
                failure_id TEXT NOT NULL,
                release_id TEXT NOT NULL,
                status TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(failure_id) REFERENCES release_failures(failure_id),
                FOREIGN KEY(release_id) REFERENCES knowledge_releases(release_id)
            );
            INSERT INTO schema_migrations(version) VALUES (6);
            """
        )

    @staticmethod
    def _dump(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def save_consent(self, record: ConsentRecord) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO consent_records VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.consent_snapshot_id,
                    record.subject_id,
                    str(record.capture_tier),
                    int(record.purposes.research_use),
                    self._dump(record),
                    record.granted_at.isoformat(),
                ),
            )

    def idempotent_consent(self, subject_id: str, key: str) -> tuple[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT consent_snapshot_id,request_sha256 FROM consent_idempotency
                   WHERE subject_id=? AND idempotency_key=?""",
                (subject_id, key),
            ).fetchone()
        return (str(row["consent_snapshot_id"]), str(row["request_sha256"])) if row else None

    def save_consent_idempotency(
        self,
        subject_id: str,
        key: str,
        request_sha256: str,
        consent_snapshot_id: str,
    ) -> None:
        with self.transaction() as db:
            try:
                db.execute(
                    "INSERT INTO consent_idempotency VALUES (?, ?, ?, ?)",
                    (subject_id, key, request_sha256, consent_snapshot_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ContributionConflictError(str(exc)) from exc

    def get_consent(self, consent_snapshot_id: str) -> ConsentRecord:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM consent_records WHERE consent_snapshot_id=?",
                (consent_snapshot_id,),
            ).fetchone()
            revoked = db.execute(
                """SELECT event_json FROM consent_events
                   WHERE consent_snapshot_id=? AND event_type='revoked'
                   ORDER BY sequence DESC LIMIT 1""",
                (consent_snapshot_id,),
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(consent_snapshot_id)
        record = ConsentRecord.model_validate_json(row["record_json"])
        if revoked:
            event = json.loads(revoked["event_json"])
            record = record.model_copy(update={"revoked_at": datetime.fromisoformat(event["revoked_at"])})
        return record

    def list_consents(self, subject_id: str) -> list[ConsentRecord]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_json FROM consent_records WHERE subject_id=? ORDER BY granted_at DESC",
                (subject_id,),
            ).fetchall()
        return [self.get_consent(ConsentRecord.model_validate_json(row["record_json"]).consent_snapshot_id) for row in rows]

    def revoke_consent(self, record: ConsentRecord) -> None:
        with self.transaction() as db:
            exists = db.execute(
                "SELECT 1 FROM consent_records WHERE consent_snapshot_id=? AND subject_id=?",
                (record.consent_snapshot_id, record.subject_id),
            ).fetchone()
            if not exists:
                raise ContributionNotFoundError(record.consent_snapshot_id)
            prior = db.execute(
                """SELECT 1 FROM consent_events
                   WHERE consent_snapshot_id=? AND event_type='revoked'""",
                (record.consent_snapshot_id,),
            ).fetchone()
            if prior:
                return
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM consent_events WHERE consent_snapshot_id=?",
                (record.consent_snapshot_id,),
            ).fetchone()[0]
            payload = {
                "event_type": "revoked",
                "subject_id": record.subject_id,
                "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
            }
            db.execute(
                "INSERT INTO consent_events VALUES (?, ?, 'revoked', ?, ?)",
                (
                    record.consent_snapshot_id,
                    sequence,
                    self._dump(payload),
                    payload["revoked_at"],
                ),
            )

    def find_context(self, subject_id: str, checksum: str, tier: str) -> ContextSnapshot | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT snapshot_json FROM context_snapshots
                   WHERE subject_id=? AND checksum_sha256=? AND capture_tier=?""",
                (subject_id, checksum, tier),
            ).fetchone()
        return ContextSnapshot.model_validate_json(row["snapshot_json"]) if row else None

    def save_context(self, snapshot: ContextSnapshot) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO context_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (
                    snapshot.context_snapshot_id,
                    snapshot.subject_id,
                    snapshot.checksum_sha256,
                    str(snapshot.capture_tier),
                    self._dump(snapshot),
                    snapshot.created_at.isoformat(),
                ),
            )

    def get_context(self, context_snapshot_id: str) -> ContextSnapshot:
        with self._connect() as db:
            row = db.execute(
                "SELECT snapshot_json FROM context_snapshots WHERE context_snapshot_id=?",
                (context_snapshot_id,),
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(context_snapshot_id)
        return ContextSnapshot.model_validate_json(row["snapshot_json"])

    def idempotent_result(self, subject_id: str, key: str) -> tuple[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT contribution_id, request_sha256 FROM contribution_idempotency
                   WHERE subject_id=? AND idempotency_key=?""",
                (subject_id, key),
            ).fetchone()
        return (str(row["contribution_id"]), str(row["request_sha256"])) if row else None

    def create(
        self,
        contribution: Contribution,
        event: ContributionEvent,
        *,
        idempotency_key: str,
        request_sha256: str,
    ) -> None:
        with self.transaction() as db:
            try:
                db.execute(
                    "INSERT INTO contributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        contribution.contribution_id,
                        contribution.contributor.subject_id,
                        str(contribution.contribution_type),
                        contribution.consent_snapshot_id,
                        contribution.context_snapshot_id,
                        str(contribution.privacy_status),
                        str(contribution.workflow_status),
                        self._dump(contribution),
                        contribution.created_at.isoformat(),
                    ),
                )
                db.execute(
                    "INSERT INTO contribution_events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.contribution_id,
                        event.sequence,
                        event.event_id,
                        event.event_type,
                        self._dump(event),
                        event.created_at.isoformat(),
                    ),
                )
                db.execute(
                    "INSERT INTO contribution_idempotency VALUES (?, ?, ?, ?)",
                    (
                        contribution.contributor.subject_id,
                        idempotency_key,
                        request_sha256,
                        contribution.contribution_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ContributionConflictError(str(exc)) from exc

    def get(self, contribution_id: str) -> Contribution:
        with self._connect() as db:
            row = db.execute(
                "SELECT contribution_json FROM contributions WHERE contribution_id=?",
                (contribution_id,),
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(contribution_id)
        return Contribution.model_validate_json(row["contribution_json"])

    def list(self, *, subject_id: str | None = None, research_only: bool = False, limit: int | None = 100) -> list[Contribution]:
        clauses: list[str] = []
        values: list[Any] = []
        if subject_id is not None:
            clauses.append("c.subject_id=?")
            values.append(subject_id)
        if research_only:
            clauses.append("r.research_use=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            "SELECT c.contribution_json FROM contributions c "
            "JOIN consent_records r ON r.consent_snapshot_id=c.consent_snapshot_id"
            + where
            + " ORDER BY c.created_at DESC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            values.append(limit)
        with self._connect() as db:
            rows = db.execute(sql, tuple(values)).fetchall()
        return [Contribution.model_validate_json(row["contribution_json"]) for row in rows]

    def list_events(self, contribution_id: str) -> list[ContributionEvent]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_json FROM contribution_events
                   WHERE contribution_id=? ORDER BY sequence""",
                (contribution_id,),
            ).fetchall()
        return [ContributionEvent.model_validate_json(row["event_json"]) for row in rows]

    def update_projection(self, contribution: Contribution, event: ContributionEvent) -> None:
        with self.transaction() as db:
            updated = db.execute(
                """UPDATE contributions
                   SET context_snapshot_id=?, privacy_status=?, workflow_status=?, contribution_json=?
                   WHERE contribution_id=?""",
                (
                    contribution.context_snapshot_id,
                    str(contribution.privacy_status),
                    str(contribution.workflow_status),
                    self._dump(contribution),
                    contribution.contribution_id,
                ),
            ).rowcount
            if updated != 1:
                raise ContributionNotFoundError(contribution.contribution_id)
            db.execute(
                "INSERT INTO contribution_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.contribution_id,
                    event.sequence,
                    event.event_id,
                    event.event_type,
                    self._dump(event),
                    event.created_at.isoformat(),
                ),
            )

    def save_privacy_derivation(self, derivation: PrivacyDerivation) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO privacy_derivations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    derivation.privacy_derivation_id,
                    derivation.contribution_id,
                    derivation.original_context_snapshot_id,
                    derivation.redacted_context_snapshot_id,
                    self._dump(derivation),
                    derivation.created_at.isoformat(),
                ),
            )

    def get_privacy_derivation(self, contribution_id: str) -> PrivacyDerivation | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM privacy_derivations WHERE contribution_id=?",
                (contribution_id,),
            ).fetchone()
        return PrivacyDerivation.model_validate_json(row["record_json"]) if row else None

    def save_expert_profile(self, profile: ExpertProfile) -> None:
        with self.transaction() as db:
            try:
                db.execute(
                    "INSERT INTO expert_profiles VALUES (?, ?, ?, ?, ?)",
                    (
                        profile.expert_profile_id,
                        profile.subject_id,
                        str(profile.status),
                        self._dump(profile),
                        profile.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ContributionConflictError(str(exc)) from exc

    def get_expert_profile(self, expert_profile_id: str) -> ExpertProfile:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM expert_profiles WHERE expert_profile_id=?",
                (expert_profile_id,),
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(expert_profile_id)
        return ExpertProfile.model_validate_json(row["record_json"])

    def get_expert_profile_by_subject(self, subject_id: str) -> ExpertProfile | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM expert_profiles WHERE subject_id=?",
                (subject_id,),
            ).fetchone()
        return ExpertProfile.model_validate_json(row["record_json"]) if row else None

    def update_expert_profile(self, profile: ExpertProfile) -> None:
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE expert_profiles SET status=?, record_json=? WHERE expert_profile_id=?",
                (str(profile.status), self._dump(profile), profile.expert_profile_id),
            ).rowcount
            if changed != 1:
                raise ContributionNotFoundError(profile.expert_profile_id)

    def save_triage(self, triage: ReviewTriage) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO review_triage VALUES (?, ?, ?, ?, ?)",
                (
                    triage.triage_id,
                    triage.contribution_id,
                    triage.priority,
                    self._dump(triage),
                    triage.created_at.isoformat(),
                ),
            )

    def get_triage(self, contribution_id: str) -> ReviewTriage | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM review_triage WHERE contribution_id=?",
                (contribution_id,),
            ).fetchone()
        return ReviewTriage.model_validate_json(row["record_json"]) if row else None

    def save_assignment(self, assignment: ReviewAssignment) -> None:
        with self.transaction() as db:
            try:
                db.execute(
                    "INSERT INTO review_assignments VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        assignment.assignment_id,
                        assignment.contribution_id,
                        assignment.expert_profile_id,
                        assignment.reviewer_subject_id,
                        assignment.status,
                        self._dump(assignment),
                        assignment.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ContributionConflictError(str(exc)) from exc

    def get_assignment(self, assignment_id: str) -> ReviewAssignment:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM review_assignments WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(assignment_id)
        return ReviewAssignment.model_validate_json(row["record_json"])

    def get_active_assignment(self, contribution_id: str) -> ReviewAssignment | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT record_json FROM review_assignments
                   WHERE contribution_id=? AND status='assigned' ORDER BY created_at DESC LIMIT 1""",
                (contribution_id,),
            ).fetchone()
        return ReviewAssignment.model_validate_json(row["record_json"]) if row else None

    def update_assignment(self, assignment: ReviewAssignment) -> None:
        with self.transaction() as db:
            changed = db.execute(
                "UPDATE review_assignments SET status=?, record_json=? WHERE assignment_id=?",
                (assignment.status, self._dump(assignment), assignment.assignment_id),
            ).rowcount
            if changed != 1:
                raise ContributionNotFoundError(assignment.assignment_id)

    def save_decision(self, decision: ExpertDecision) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO expert_annotations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.contribution_id,
                    decision.assignment_id,
                    decision.expert_profile_id,
                    self._dump(decision),
                    decision.created_at.isoformat(),
                ),
            )
            for lane in decision.remediation_lanes:
                db.execute(
                    "INSERT INTO remediation_links VALUES (?, ?, ?, ?, ?)",
                    (
                        f"remediation_{decision.decision_id}_{lane}",
                        decision.contribution_id,
                        decision.decision_id,
                        str(lane),
                        decision.created_at.isoformat(),
                    ),
                )

    def list_decisions(self, contribution_id: str) -> list[ExpertDecision]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_json FROM expert_annotations WHERE contribution_id=? ORDER BY created_at",
                (contribution_id,),
            ).fetchall()
        return [ExpertDecision.model_validate_json(row["record_json"]) for row in rows]

    def save_correction(self, proposal: CorrectionProposal) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO correction_proposals VALUES (?, ?, ?, ?, ?)",
                (
                    proposal.correction_proposal_id,
                    proposal.contribution_id,
                    proposal.decision_id,
                    self._dump(proposal),
                    proposal.created_at.isoformat(),
                ),
            )

    def save_regression_case(self, case: RegressionCase) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO regression_cases VALUES (?, ?, ?, ?, ?, ?)",
                (
                    case.regression_case_id,
                    case.contribution_id,
                    case.decision_id,
                    case.status,
                    self._dump(case),
                    case.created_at.isoformat(),
                ),
            )

    def save_knowledge_candidate(self, candidate: KnowledgeCandidate) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO knowledge_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.knowledge_candidate_id,
                    candidate.contribution_id,
                    candidate.decision_id,
                    candidate.status,
                    candidate.production_release_id,
                    self._dump(candidate),
                    candidate.created_at.isoformat(),
                    candidate.updated_at.isoformat(),
                ),
            )

    def import_extraction_candidate(
        self,
        consent: ConsentRecord,
        contribution: Contribution,
        event: ContributionEvent,
        candidate: KnowledgeCandidate,
        *,
        idempotency_key: str,
        request_sha256: str,
    ) -> None:
        """Atomically import an extraction unit into the governed candidate queue.

        Extraction is a system-originated contribution, so it has no expert
        review decision yet.  The candidate intentionally enters at
        ``expert_reviewed_candidate`` only as a review queue item; structuring,
        semantic deduplication, adjudication, regression validation, and
        release promotion remain explicit lifecycle operations.
        """
        with self.transaction() as db:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO consent_records VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        consent.consent_snapshot_id,
                        consent.subject_id,
                        str(consent.capture_tier),
                        int(consent.purposes.research_use),
                        self._dump(consent),
                        consent.granted_at.isoformat(),
                    ),
                )
                existing = db.execute(
                    "SELECT contribution_id FROM contribution_idempotency WHERE subject_id=? AND idempotency_key=?",
                    (contribution.contributor.subject_id, idempotency_key),
                ).fetchone()
                if existing:
                    return
                db.execute(
                    "INSERT INTO contributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        contribution.contribution_id,
                        contribution.contributor.subject_id,
                        str(contribution.contribution_type),
                        contribution.consent_snapshot_id,
                        contribution.context_snapshot_id,
                        str(contribution.privacy_status),
                        str(contribution.workflow_status),
                        self._dump(contribution),
                        contribution.created_at.isoformat(),
                    ),
                )
                db.execute(
                    "INSERT INTO contribution_events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.contribution_id,
                        event.sequence,
                        event.event_id,
                        event.event_type,
                        self._dump(event),
                        event.created_at.isoformat(),
                    ),
                )
                db.execute(
                    "INSERT INTO contribution_idempotency VALUES (?, ?, ?, ?)",
                    (
                        contribution.contributor.subject_id,
                        idempotency_key,
                        request_sha256,
                        contribution.contribution_id,
                    ),
                )
                db.execute(
                    "INSERT INTO knowledge_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        candidate.knowledge_candidate_id,
                        candidate.contribution_id,
                        candidate.decision_id,
                        str(candidate.status),
                        candidate.production_release_id,
                        self._dump(candidate),
                        candidate.created_at.isoformat(),
                        candidate.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ContributionConflictError(str(exc)) from exc

    def get_knowledge_candidate(self, candidate_id: str) -> KnowledgeCandidate:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM knowledge_candidates WHERE knowledge_candidate_id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(candidate_id)
        return KnowledgeCandidate.model_validate_json(row["record_json"])

    def list_knowledge_candidates(
        self, *, statuses: set[str] | None = None
    ) -> list[KnowledgeCandidate]:
        sql = "SELECT record_json FROM knowledge_candidates"
        params: list[Any] = []
        if statuses:
            values = sorted(str(value) for value in statuses)
            sql += f" WHERE status IN ({','.join('?' for _ in values)})"
            params.extend(values)
        sql += " ORDER BY updated_at, knowledge_candidate_id"
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [KnowledgeCandidate.model_validate_json(row["record_json"]) for row in rows]

    def transition_knowledge_candidate(
        self,
        candidate: KnowledgeCandidate,
        decision: CandidateDecision,
        *,
        adjudication: CandidateAdjudication | None = None,
        validation: CandidateRegressionValidation | None = None,
    ) -> None:
        if candidate.knowledge_candidate_id != decision.knowledge_candidate_id:
            raise ContributionConflictError("candidate decision targets another candidate")
        if candidate.status != decision.to_status:
            raise ContributionConflictError("candidate status does not match decision target")
        with self.transaction() as db:
            row = db.execute(
                "SELECT status FROM knowledge_candidates WHERE knowledge_candidate_id=?",
                (candidate.knowledge_candidate_id,),
            ).fetchone()
            if row is None:
                raise ContributionNotFoundError(candidate.knowledge_candidate_id)
            if str(row["status"]) != str(decision.from_status):
                raise ContributionConflictError(
                    f"candidate status changed: expected {decision.from_status}, got {row['status']}"
                )
            db.execute(
                """UPDATE knowledge_candidates
                   SET status=?, production_release_id=?, record_json=?, updated_at=?
                   WHERE knowledge_candidate_id=?""",
                (
                    str(candidate.status),
                    candidate.production_release_id,
                    self._dump(candidate),
                    candidate.updated_at.isoformat(),
                    candidate.knowledge_candidate_id,
                ),
            )
            db.execute(
                "INSERT INTO candidate_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.candidate_decision_id,
                    decision.knowledge_candidate_id,
                    decision.event_type,
                    str(decision.from_status),
                    str(decision.to_status),
                    decision.actor_subject_id,
                    decision.actor_role,
                    self._dump(decision),
                    decision.created_at.isoformat(),
                ),
            )
            if adjudication is not None:
                db.execute(
                    "INSERT INTO candidate_adjudications VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        adjudication.adjudication_id,
                        adjudication.knowledge_candidate_id,
                        adjudication.expert_profile_id,
                        adjudication.adjudicator_subject_id,
                        adjudication.decision,
                        self._dump(adjudication),
                        adjudication.created_at.isoformat(),
                    ),
                )
            if validation is not None:
                db.execute(
                    "INSERT INTO candidate_regression_validations VALUES (?, ?, ?, ?, ?)",
                    (
                        validation.validation_id,
                        validation.knowledge_candidate_id,
                        validation.status,
                        self._dump(validation),
                        validation.created_at.isoformat(),
                    ),
                )

    def list_candidate_decisions(self, candidate_id: str) -> list[CandidateDecision]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT record_json FROM candidate_decisions
                   WHERE knowledge_candidate_id=? ORDER BY created_at, candidate_decision_id""",
                (candidate_id,),
            ).fetchall()
        return [CandidateDecision.model_validate_json(row["record_json"]) for row in rows]

    @staticmethod
    def _release_event_values(
        db: sqlite3.Connection, release: KnowledgeRelease, event: dict[str, Any]
    ) -> tuple[Any, ...]:
        sequence = int(
            db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM release_events WHERE release_id=?",
                (release.release_id,),
            ).fetchone()[0]
        )
        return (
            release.release_id,
            sequence,
            str(event["event_type"]),
            str(event["actor_subject_id"]),
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            str(event["created_at"]),
        )

    def save_knowledge_release(self, release: KnowledgeRelease, event: dict[str, Any]) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO knowledge_releases VALUES (?, ?, ?, ?, ?, ?)",
                (
                    release.release_id,
                    release.status,
                    release.manifest_sha256,
                    self._dump(release),
                    release.created_at.isoformat(),
                    release.created_at.isoformat(),
                ),
            )
            db.execute("INSERT INTO release_events VALUES (?, ?, ?, ?, ?, ?)", self._release_event_values(db, release, event))

    def create_preview_release(
        self,
        release: KnowledgeRelease,
        event: dict[str, Any],
        transitions: list[tuple[KnowledgeCandidate, CandidateDecision]],
    ) -> None:
        with self.transaction() as db:
            expected_ids = {candidate.knowledge_candidate_id for candidate, _ in transitions}
            if expected_ids != set(release.candidate_ids):
                raise ContributionConflictError("preview transitions do not match release candidates")
            for candidate, decision in transitions:
                row = db.execute(
                    "SELECT status FROM knowledge_candidates WHERE knowledge_candidate_id=?",
                    (candidate.knowledge_candidate_id,),
                ).fetchone()
                if row is None or str(row["status"]) != str(decision.from_status):
                    raise ContributionConflictError("candidate changed before preview transaction")
                if candidate.status != decision.to_status:
                    raise ContributionConflictError("preview target status mismatch")
            db.execute(
                "INSERT INTO knowledge_releases VALUES (?, ?, ?, ?, ?, ?)",
                (
                    release.release_id,
                    release.status,
                    release.manifest_sha256,
                    self._dump(release),
                    release.created_at.isoformat(),
                    release.created_at.isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO release_events VALUES (?, ?, ?, ?, ?, ?)",
                self._release_event_values(db, release, event),
            )
            for candidate, decision in transitions:
                db.execute(
                    """UPDATE knowledge_candidates SET status=?,production_release_id=?,record_json=?,updated_at=?
                       WHERE knowledge_candidate_id=?""",
                    (
                        str(candidate.status), candidate.production_release_id, self._dump(candidate),
                        candidate.updated_at.isoformat(), candidate.knowledge_candidate_id,
                    ),
                )
                db.execute(
                    "INSERT INTO candidate_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        decision.candidate_decision_id, decision.knowledge_candidate_id,
                        decision.event_type, str(decision.from_status), str(decision.to_status),
                        decision.actor_subject_id, decision.actor_role, self._dump(decision),
                        decision.created_at.isoformat(),
                    ),
                )

    def get_knowledge_release(self, release_id: str) -> KnowledgeRelease:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM knowledge_releases WHERE release_id=?", (release_id,)
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(release_id)
        return KnowledgeRelease.model_validate_json(row["record_json"])

    def list_knowledge_releases(self) -> list[KnowledgeRelease]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_json FROM knowledge_releases ORDER BY created_at, release_id"
            ).fetchall()
        return [KnowledgeRelease.model_validate_json(row["record_json"]) for row in rows]

    def update_knowledge_release(self, release: KnowledgeRelease, event: dict[str, Any]) -> None:
        with self.transaction() as db:
            changed = db.execute(
                """UPDATE knowledge_releases SET status=?,manifest_sha256=?,record_json=?,updated_at=?
                   WHERE release_id=?""",
                (
                    release.status,
                    release.manifest_sha256,
                    self._dump(release),
                    str(event["created_at"]),
                    release.release_id,
                ),
            ).rowcount
            if changed != 1:
                raise ContributionNotFoundError(release.release_id)
            db.execute("INSERT INTO release_events VALUES (?, ?, ?, ?, ?, ?)", self._release_event_values(db, release, event))
            if event["event_type"] == "promoted":
                for candidate_id in release.candidate_ids:
                    db.execute(
                        "INSERT OR IGNORE INTO release_promotion_links VALUES (?, ?, ?)",
                        (release.release_id, candidate_id, str(event["created_at"])),
                    )

    def promote_knowledge_release(
        self,
        release: KnowledgeRelease,
        event: dict[str, Any],
        transitions: list[tuple[KnowledgeCandidate, CandidateDecision]],
        *,
        superseded: KnowledgeRelease | None = None,
    ) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT status FROM knowledge_releases WHERE release_id=?", (release.release_id,)
            ).fetchone()
            if row is None or str(row["status"]) != "preview":
                raise ContributionConflictError("release is no longer a preview")
            for candidate, decision in transitions:
                current = db.execute(
                    "SELECT status FROM knowledge_candidates WHERE knowledge_candidate_id=?",
                    (candidate.knowledge_candidate_id,),
                ).fetchone()
                if current is None or str(current["status"]) != str(decision.from_status):
                    raise ContributionConflictError("candidate changed before promotion transaction")
            db.execute(
                """UPDATE knowledge_releases SET status=?,manifest_sha256=?,record_json=?,updated_at=?
                   WHERE release_id=?""",
                (
                    release.status, release.manifest_sha256, self._dump(release),
                    str(event["created_at"]), release.release_id,
                ),
            )
            db.execute(
                "INSERT INTO release_events VALUES (?, ?, ?, ?, ?, ?)",
                self._release_event_values(db, release, event),
            )
            if superseded is not None:
                db.execute(
                    """UPDATE knowledge_releases SET status=?,record_json=?,updated_at=?
                       WHERE release_id=?""",
                    (
                        superseded.status, self._dump(superseded), str(event["created_at"]),
                        superseded.release_id,
                    ),
                )
                superseded_event = {
                    "event_type": "superseded",
                    "actor_subject_id": event["actor_subject_id"],
                    "created_at": event["created_at"],
                    "superseded_by": release.release_id,
                }
                db.execute(
                    "INSERT INTO release_events VALUES (?, ?, ?, ?, ?, ?)",
                    self._release_event_values(db, superseded, superseded_event),
                )
            for candidate, decision in transitions:
                db.execute(
                    """UPDATE knowledge_candidates SET status=?,production_release_id=?,record_json=?,updated_at=?
                       WHERE knowledge_candidate_id=?""",
                    (
                        str(candidate.status), candidate.production_release_id, self._dump(candidate),
                        candidate.updated_at.isoformat(), candidate.knowledge_candidate_id,
                    ),
                )
                db.execute(
                    "INSERT INTO candidate_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        decision.candidate_decision_id, decision.knowledge_candidate_id,
                        decision.event_type, str(decision.from_status), str(decision.to_status),
                        decision.actor_subject_id, decision.actor_role, self._dump(decision),
                        decision.created_at.isoformat(),
                    ),
                )
                db.execute(
                    "INSERT INTO release_promotion_links VALUES (?, ?, ?)",
                    (release.release_id, candidate.knowledge_candidate_id, str(event["created_at"])),
                )

    def rollback_knowledge_release(
        self,
        rolled_back: KnowledgeRelease,
        reactivated: KnowledgeRelease | None,
        event: dict[str, Any],
    ) -> None:
        with self.transaction() as db:
            current = db.execute(
                "SELECT status FROM knowledge_releases WHERE release_id=?",
                (rolled_back.release_id,),
            ).fetchone()
            if current is None or str(current["status"]) != "promoted":
                raise ContributionConflictError("release is not the current promoted release")
            db.execute(
                "UPDATE knowledge_releases SET status=?,record_json=?,updated_at=? WHERE release_id=?",
                (
                    rolled_back.status, self._dump(rolled_back), str(event["created_at"]),
                    rolled_back.release_id,
                ),
            )
            db.execute(
                "INSERT INTO release_events VALUES (?, ?, ?, ?, ?, ?)",
                self._release_event_values(db, rolled_back, event),
            )
            if reactivated is not None:
                db.execute(
                    "UPDATE knowledge_releases SET status=?,record_json=?,updated_at=? WHERE release_id=?",
                    (
                        reactivated.status, self._dump(reactivated), str(event["created_at"]),
                        reactivated.release_id,
                    ),
                )
                reactivate_event = {
                    "event_type": "reactivated_by_rollback",
                    "actor_subject_id": event["actor_subject_id"],
                    "created_at": event["created_at"],
                    "rolled_back_release_id": rolled_back.release_id,
                }
                db.execute(
                    "INSERT INTO release_events VALUES (?, ?, ?, ?, ?, ?)",
                    self._release_event_values(db, reactivated, reactivate_event),
                )

    def save_release_failure(self, failure: ReleaseFailure) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO release_failures VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    failure.failure_id,
                    failure.release_id,
                    str(failure.stage),
                    failure.error_code,
                    failure.status,
                    self._dump(failure),
                    failure.created_at.isoformat(),
                    failure.created_at.isoformat(),
                ),
            )

    def get_release_failure(self, failure_id: str) -> ReleaseFailure:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM release_failures WHERE failure_id=?", (failure_id,)
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(failure_id)
        return ReleaseFailure.model_validate_json(row["record_json"])

    def list_release_failures(self, release_id: str | None = None) -> list[ReleaseFailure]:
        sql = "SELECT record_json FROM release_failures"
        params: tuple[Any, ...] = ()
        if release_id is not None:
            sql += " WHERE release_id=?"
            params = (release_id,)
        sql += " ORDER BY created_at, failure_id"
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [ReleaseFailure.model_validate_json(row["record_json"]) for row in rows]

    def save_failure_replay(
        self, failure: ReleaseFailure, replay: ReleaseFailureReplay
    ) -> None:
        with self.transaction() as db:
            current = db.execute(
                "SELECT status FROM release_failures WHERE failure_id=?", (failure.failure_id,)
            ).fetchone()
            if current is None:
                raise ContributionNotFoundError(failure.failure_id)
            db.execute(
                """UPDATE release_failures SET status=?,record_json=?,updated_at=?
                   WHERE failure_id=?""",
                (
                    failure.status,
                    self._dump(failure),
                    replay.created_at.isoformat(),
                    failure.failure_id,
                ),
            )
            db.execute(
                "INSERT INTO release_failure_replays VALUES (?, ?, ?, ?, ?, ?)",
                (
                    replay.replay_id,
                    replay.failure_id,
                    replay.release_id,
                    replay.status,
                    self._dump(replay),
                    replay.created_at.isoformat(),
                ),
            )

    def create_memory(self, memory: DomainMemory, event: MemoryEvent) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO domain_memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory.memory_id,
                    memory.subject_id,
                    str(memory.memory_type),
                    str(memory.confirmation_status),
                    memory.expires_at.isoformat(),
                    memory.deletion_status,
                    self._dump(memory),
                    memory.created_at.isoformat(),
                    memory.updated_at.isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO memory_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.memory_id,
                    event.sequence,
                    event.event_id,
                    event.event_type,
                    self._dump(event),
                    event.created_at.isoformat(),
                ),
            )

    def get_memory(self, memory_id: str) -> DomainMemory:
        with self._connect() as db:
            row = db.execute(
                "SELECT record_json FROM domain_memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
        if row is None:
            raise ContributionNotFoundError(memory_id)
        return DomainMemory.model_validate_json(row["record_json"])

    def list_memories(self, subject_id: str) -> list[DomainMemory]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT record_json FROM domain_memories
                   WHERE subject_id=? ORDER BY updated_at DESC, memory_id""",
                (subject_id,),
            ).fetchall()
        return [DomainMemory.model_validate_json(row["record_json"]) for row in rows]

    def update_memory(self, memory: DomainMemory, event: MemoryEvent) -> None:
        with self.transaction() as db:
            changed = db.execute(
                """UPDATE domain_memories SET confirmation_status=?, expires_at=?,
                   deletion_status=?, record_json=?, updated_at=? WHERE memory_id=?""",
                (
                    str(memory.confirmation_status),
                    memory.expires_at.isoformat(),
                    memory.deletion_status,
                    self._dump(memory),
                    memory.updated_at.isoformat(),
                    memory.memory_id,
                ),
            ).rowcount
            if changed != 1:
                raise ContributionNotFoundError(memory.memory_id)
            db.execute(
                "INSERT INTO memory_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.memory_id,
                    event.sequence,
                    event.event_id,
                    event.event_type,
                    self._dump(event),
                    event.created_at.isoformat(),
                ),
            )

    def list_memory_events(self, memory_id: str) -> list[MemoryEvent]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_json FROM memory_events WHERE memory_id=? ORDER BY sequence",
                (memory_id,),
            ).fetchall()
        return [MemoryEvent.model_validate_json(row["event_json"]) for row in rows]
