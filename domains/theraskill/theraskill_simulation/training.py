"""Instructor collaboration and controlled response-comparison services."""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TrainingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnAnnotation(TrainingModel):
    schema_version: str = "theraskill_turn_annotation_v1"
    annotation_id: str
    session_id: str
    turn_id: str
    author_role: str = "instructor"
    comment: str
    alternative_response: str | None = None
    contribution_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class TrainingCourse(TrainingModel):
    schema_version: str = "theraskill_training_course_v1"
    course_id: str
    title: str
    description: str = ""
    skill_tags: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    difficulty_path: list[str] = Field(default_factory=list)
    completion_review_gate: str = "pass"
    # Keep objectives as JSON objects for compatibility with existing course
    # rows, while allowing the richer observable-behaviour/rubric fields.
    learning_objectives: list[dict[str, Any]] = Field(default_factory=list)
    template_id: str | None = None
    competency_ids: list[str] = Field(default_factory=list)
    scaffolding: list[str] = Field(default_factory=list)
    learning_units: dict[str, Any] = Field(default_factory=dict)
    knowledge_references: list[dict[str, Any]] = Field(default_factory=list)
    design_retrieval: dict[str, Any] = Field(default_factory=dict)
    require_instructor_review: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class TrainingAssignment(TrainingModel):
    schema_version: str = "theraskill_training_assignment_v1"
    assignment_id: str
    title: str
    description: str = ""
    scenario_ids: list[str]
    assignee: str
    created_by: str = "instructor"
    status: str = "assigned"
    course_id: str | None = None
    skill_tags: list[str] = Field(default_factory=list)
    due_at: datetime | None = None
    required_review_gate: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ResponseCandidate(TrainingModel):
    candidate_id: str
    framework: str
    strategy: str
    response: str
    rationale: str
    applicable_context: str
    limitations: str
    evidence_unit_ids: list[str] = Field(default_factory=list)
    evidence: list[dict[str, str]] = Field(default_factory=list)


class ResponseComparison(TrainingModel):
    schema_version: str = "theraskill_response_comparison_v1"
    comparison_id: str
    session_id: str
    target_turn_id: str
    status: str = "complete"
    candidates: list[ResponseCandidate]
    model_config_id: str
    prompt_release_id: str
    knowledge_release_id: str
    evidence_unit_ids: list[str] = Field(default_factory=list)
    evidence_context: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ResponseComparisonEngine(Protocol):
    def generate(
        self,
        *,
        session_id: str,
        target_turn_id: str,
        scenario: dict[str, Any],
        transcript: list[dict[str, Any]],
        knowledge_release_id: str,
        evidence_unit_ids: list[str],
        language: str = "zh",
    ) -> ResponseComparison: ...


class SQLiteTrainingRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS turn_annotations (
                    annotation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_annotations_session
                    ON turn_annotations(session_id, created_at);
                CREATE TABLE IF NOT EXISTS training_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    assignee TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assignments_assignee
                    ON training_assignments(assignee, created_at);
                CREATE TABLE IF NOT EXISTS training_courses (
                    course_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_courses_created
                    ON training_courses(created_at);
                CREATE TABLE IF NOT EXISTS response_comparisons (
                    comparison_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    target_turn_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_comparisons_session
                    ON response_comparisons(session_id, created_at);
                """
            )

    @staticmethod
    def _json(model: BaseModel) -> str:
        return json.dumps(
            model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )

    def add_annotation(self, value: TurnAnnotation) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM turn_annotations WHERE annotation_id=?",
                (value.annotation_id,),
            ).fetchone()
            if existing:
                stored = TurnAnnotation.model_validate_json(existing[0])
                if self._json(stored) != self._json(value):
                    raise ValueError("annotation id reused with different content")
                return
            connection.execute(
                "INSERT INTO turn_annotations VALUES (?, ?, ?, ?, ?)",
                (
                    value.annotation_id,
                    value.session_id,
                    value.turn_id,
                    value.created_at.isoformat(),
                    self._json(value),
                ),
            )

    def list_annotations(self, session_id: str) -> list[TurnAnnotation]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM turn_annotations WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [TurnAnnotation.model_validate_json(row[0]) for row in rows]

    def get_annotation(self, annotation_id: str) -> TurnAnnotation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM turn_annotations WHERE annotation_id=?",
                (annotation_id,),
            ).fetchone()
        return TurnAnnotation.model_validate_json(row[0]) if row else None

    def count_annotations(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM turn_annotations").fetchone()[0])

    def add_assignment(self, value: TrainingAssignment) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO training_assignments VALUES (?, ?, ?, ?)",
                (
                    value.assignment_id,
                    value.assignee,
                    value.created_at.isoformat(),
                    self._json(value),
                ),
            )

    def list_assignments(self, assignee: str | None = None) -> list[TrainingAssignment]:
        with self._connect() as connection:
            if assignee:
                rows = connection.execute(
                    "SELECT payload_json FROM training_assignments WHERE assignee=? ORDER BY created_at DESC",
                    (assignee,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM training_assignments ORDER BY created_at DESC"
                ).fetchall()
        return [TrainingAssignment.model_validate_json(row[0]) for row in rows]

    def delete_assignment(self, assignment_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM training_assignments WHERE assignment_id=?",
                (assignment_id,),
            ).rowcount == 1

    def add_course(self, value: TrainingCourse) -> None:
        with self._connect() as connection:
            # Serialize the existence check with the insert so repeated handoff
            # requests return the same course instead of a UNIQUE-key error.
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM training_courses WHERE course_id=?",
                (value.course_id,),
            ).fetchone()
            if existing:
                stored = TrainingCourse.model_validate_json(existing[0])
                if self._json(stored) != self._json(value):
                    raise ValueError("course id reused with different content")
                return
            connection.execute(
                "INSERT INTO training_courses VALUES (?, ?, ?)",
                (value.course_id, value.created_at.isoformat(), self._json(value)),
            )

    def get_course(self, course_id: str) -> TrainingCourse | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM training_courses WHERE course_id=?",
                (course_id,),
            ).fetchone()
        return TrainingCourse.model_validate_json(row[0]) if row else None

    def list_courses(self) -> list[TrainingCourse]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM training_courses ORDER BY created_at DESC"
            ).fetchall()
        return [TrainingCourse.model_validate_json(row[0]) for row in rows]

    def delete_course(self, course_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "DELETE FROM training_courses WHERE course_id=?", (course_id,)
            ).rowcount == 1

    def count_comparisons(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM response_comparisons").fetchone()[0])

    def add_comparison(self, value: ResponseComparison) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO response_comparisons VALUES (?, ?, ?, ?, ?)",
                (
                    value.comparison_id,
                    value.session_id,
                    value.target_turn_id,
                    value.created_at.isoformat(),
                    self._json(value),
                ),
            )

    def list_comparisons(self, session_id: str) -> list[ResponseComparison]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM response_comparisons WHERE session_id=? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [ResponseComparison.model_validate_json(row[0]) for row in rows]


class LLMResponseComparisonEngine:
    def __init__(
        self,
        *,
        project_root: str | Path,
        extraction_config: str | Path,
        model_config: str | Path,
    ) -> None:
        root = Path(project_root).resolve()
        self.project_root = root
        self.model_config_path = Path(model_config).resolve()
        self.retriever = None
        self.retrieval_paths = None
        common = root / "scripts/_common"
        if str(common) not in sys.path:
            sys.path.insert(0, str(common))
        from domains.theraskill.theraskill_llm import OpenAICompatibleClient, load_prompt, load_yaml, merged_model_config

        resolved = merged_model_config(load_yaml(extraction_config), load_yaml(model_config))
        self.client = OpenAICompatibleClient(resolved)
        self.model_name = self.client.model
        self.prompts = {
            "zh": load_prompt(root / "prompts/Simulation/response_comparison_zh.md"),
            "en": load_prompt(root / "prompts/Simulation/response_comparison_en.md"),
        }

    def _retrieve_evidence(self, query: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from domains.theraskill.hybrid_retrieval import (HybridRetrievalError, TheraSkillHybridRetriever,
            selected_retrieval_paths, search_policy_lexical)
        from domains.theraskill.runtime import runtime_root
        from theraskill_agent.resources import active_index

        selected = active_index()
        index_id = selected[1]["index_id"] if selected else None
        paths = selected_retrieval_paths(self.model_config_path, runtime_root() / "cache/comparison_retrieval")
        try:
            if self.retriever is None or self.retrieval_paths != paths:
                self.retriever = TheraSkillHybridRetriever(self.project_root, paths)
                self.retrieval_paths = paths
            bundle = self.retriever.search(query, skill="therapy-response-reviewer", top_k=6)
        except HybridRetrievalError:
            try:
                bundle = search_policy_lexical(self.project_root, paths.sqlite_fts, query,
                                               skill="therapy-response-reviewer", top_k=6)
            except HybridRetrievalError:
                bundle = {"items": [], "retrieval_mode": "unavailable"}
        context = []
        for item in bundle.get("items") or []:
            unit_id = str(item.get("release_unit_id") or item.get("retrieval_id") or "")
            text = str(item.get("text_preview") or "")
            if unit_id and text:
                context.append({"unit_id": unit_id, "text": text, "label": item.get("canonical_label"),
                                "allowed_use": item.get("allowed_use"), "forbidden_use": item.get("forbidden_use"),
                                "evidence_role": item.get("evidence_role")})
        return context, {"index_id": index_id, "retrieval_mode": bundle.get("retrieval_mode"),
                         "source_count": len(context), "status": "available" if context else "no_evidence"}

    def generate(
        self,
        *,
        session_id: str,
        target_turn_id: str,
        scenario: dict[str, Any],
        transcript: list[dict[str, Any]],
        knowledge_release_id: str,
        evidence_unit_ids: list[str],
        language: str = "zh",
    ) -> ResponseComparison:
        from domains.theraskill.theraskill_llm import chat_json_with_repair

        query = " ".join(str(row.get("text") or "") for row in transcript[-6:])
        context, retrieval = self._retrieve_evidence(query)
        by_id = {row["unit_id"]: row for row in context}
        prompt = self.prompts.get(str(language).lower(), self.prompts["zh"]) + """
Use retrieved_evidence as reference material, respecting allowed_use and forbidden_use.
For each candidate also return evidence: [{"unit_id": "supplied ID", "quote": "exact excerpt"}].
Cite at least one applicable source per candidate when evidence is supplied. Quotes must be
verbatim from retrieved_evidence.text. Explain the connection in rationale. Do not cite past
session evidence or invent sources. If no evidence is supplied, return evidence: [], explicitly
state in limitations that this is a transcript-based draft without knowledge-library grounding.
"""
        def validate(value: dict[str, Any]) -> None:
            rows = value.get("candidates")
            if not isinstance(rows, list) or len(rows) != 3:
                raise ValueError("Response comparison requires exactly three candidates")
            for row in rows:
                if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                    for key in ("framework", "strategy", "response", "rationale", "applicable_context", "limitations")):
                    raise ValueError("Every comparison candidate requires complete text fields")
                citations = row.get("evidence")
                if not isinstance(citations, list) or (context and not citations):
                    raise ValueError("Every candidate must cite the supplied knowledge when available")
                for citation in citations:
                    source = by_id.get(citation.get("unit_id")) if isinstance(citation, dict) else None
                    quote = citation.get("quote") if isinstance(citation, dict) else None
                    if not source or not isinstance(quote, str) or not quote.strip() or quote not in source["text"]:
                        raise ValueError("Comparison evidence must quote an actual supplied knowledge unit")
            if len({row["response"].strip().casefold() for row in rows}) != 3:
                raise ValueError("Comparison candidates must not be identical responses")

        result = chat_json_with_repair(self.client, prompt, {
            "scenario": scenario, "transcript_through_target": transcript, "target_turn_id": target_turn_id,
            "required_candidate_count": 3, "retrieved_evidence": context,
        }, parse_retries=1, max_prompt_tokens=12000, force_json_first=True, validate=validate)
        candidates = [ResponseCandidate(candidate_id=f"candidate_{index + 1}",
            **{key: row[key].strip() for key in ("framework", "strategy", "response", "rationale", "applicable_context", "limitations")},
            evidence=row["evidence"], evidence_unit_ids=list(dict.fromkeys(item["unit_id"] for item in row["evidence"])))
            for index, row in enumerate(result["candidates"])]
        return ResponseComparison(
            comparison_id=f"comparison_{uuid.uuid4().hex}", session_id=session_id, target_turn_id=target_turn_id,
            status="complete" if context else "limited_evidence", candidates=candidates,
            model_config_id=self.model_name, prompt_release_id=f"response_comparison_{str(language).lower()}_v2",
            knowledge_release_id=knowledge_release_id,
            evidence_unit_ids=list(dict.fromkeys(item for candidate in candidates for item in candidate.evidence_unit_ids)),
            evidence_context=context, retrieval_summary={**retrieval, "session_knowledge_release_id": knowledge_release_id,
                "prior_session_evidence_count": len(set(evidence_unit_ids)), "citation_validation": "exact_source_and_quote"},
        )
