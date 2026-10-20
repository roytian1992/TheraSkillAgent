"""Immutable short exercises and durable, explicitly formative judge jobs.

The exercise shown at submission time is retained with the response. Model
feedback and teacher reviews have separate histories; neither changes a case's
completion state. Interrupted evaluations require an explicit bounded retry.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .batch_jobs import ScenarioBatchJobStore
from .contracts import ActorRole
from .learning import session_fingerprint, stamp


MAX_JUDGE_EXECUTIONS = 3


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def learning_evaluation_signature(model_config_path: str | Path | None = None,
                                  extraction_config_path: str | Path | None = None) -> dict[str, str]:
    """Describe the configured grading environment using hashes only.

    Resolve controls with the same precedence as LearningJudge, without creating
    a client or making a network call. Missing configuration remains listable;
    the worker reports it as an unavailable evaluation environment.
    """
    from domains.theraskill.provider_config import resolve_model_config
    from .learning_judge import JUDGE_PROMPT, PROMPT_ID
    from theraskill_agent.resources import active_index

    result = {"status": "available", "prompt_sha256": _hash({"id": PROMPT_ID, "prompt": JUDGE_PROMPT})}
    try:
        if model_config_path is not None or extraction_config_path is not None:
            from domains.theraskill.theraskill_llm import load_yaml, merged_model_config
            config = merged_model_config(load_yaml(extraction_config_path) if extraction_config_path else {},
                                         load_yaml(model_config_path) if model_config_path else {})
        else:
            config = resolve_model_config()
        # Credentials and arbitrary provider extensions are deliberately absent.
        # Even connection URLs/request controls are only kept inside this digest.
        keys = ("provider", "model", "name", "base_url", "temperature", "max_output_tokens",
                "timeout_seconds", "retry_attempts", "response_format_json", "thinking", "reasoning_effort", "ssl_verify")
        result["model_sha256"] = _hash({key: config.get(key) for key in keys})
    except Exception:
        result.update(status="unavailable", model_sha256=_hash({"status": "unavailable"}))
    try:
        selected = active_index()
        identity = None if selected is None else {"index_id": selected[1]["index_id"],
                                                  "knowledge_sha256": selected[1]["input"]["knowledge_sha256"]}
        result["index_sha256"] = _hash(identity)
    except Exception:
        result.update(status="unavailable", index_sha256=_hash({"status": "unavailable"}))
    return result


class _EvaluationEnvironmentError(ValueError):
    """A safe, user-facing explanation; never include provider error text."""


def _text(value: Any, name: str, limit: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} must be {'nonempty ' if required else ''}text of at most {limit} characters")
    return value.strip()


class PracticeSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    practice_id: str = Field(min_length=1, max_length=160)
    response_text: str = Field(min_length=1, max_length=12000)
    self_reflection: str = Field(default="", max_length=2000)
    hint_used: StrictBool = False
    request_id: str = Field(min_length=1, max_length=160)
    language: Literal["zh", "en"] = "zh"

    @field_validator("practice_id", "response_text", "request_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text cannot be blank")
        return value.strip()

    @field_validator("self_reflection")
    @classmethod
    def reflection(cls, value: str) -> str:
        return value.strip()


class SubmissionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "needs_practice", "rejected"]
    comment: str = Field(min_length=1, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=160)
    score: float | None = Field(default=None, ge=1, le=5)

    @field_validator("score", mode="before")
    @classmethod
    def numeric_score(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Score must be numeric, not boolean")
        return value


class AttemptJudgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["zh", "en"] = "zh"


class _JudgeJobs(ScenarioBatchJobStore):
    """Keep the shared job format, but never replay a remote call on restart."""

    def _recover(self) -> None:
        for path in self.root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(job, dict) or job.get("job_id") != path.parent.name:
                    continue
                if not str(job["job_id"]).startswith("learning_judge_"):
                    continue
                request = job.get("request")
                if not isinstance(request, dict) or not isinstance(job.get("created_at"), str):
                    continue
                if job.get("status") not in {"queued", "running", "failed", "partial", "completed"}:
                    continue
                if not isinstance(job.get("execution_count", 0), int) or isinstance(job.get("execution_count", 0), bool):
                    continue
                if job.get("status") in {"queued", "running"}:
                    job.update(status="failed", recovered_at=stamp(), updated_at=stamp(),
                               error="Evaluation was interrupted. Retry explicitly; the previous remote call may have completed.")
                    self._save(job)
                self._jobs[job["job_id"]] = job
            except (OSError, ValueError, TypeError):
                continue

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            executions = int(job.get("execution_count") or 0)
            if executions >= MAX_JUDGE_EXECUTIONS:
                self._update(job_id, status="failed", error="Judge retry limit reached")
                return
            self._update(job_id, execution_count=executions + 1)
        super()._run(job_id)

    def retry(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job and int(job.get("execution_count") or 0) >= MAX_JUDGE_EXECUTIONS:
                raise ValueError("Judge retry limit reached (three executions per input)")
            return super().retry(job_id)


class LearningPracticeService:
    def __init__(self, learning, judge_factory: Callable[[], Any], job_root: str | Path,
                 *, evaluation_signature: Callable[[], Any] | None = None):
        self.learning = learning
        self.repo = learning.repo
        self.judge_factory = judge_factory
        self.evaluation_signature = evaluation_signature
        self._lock = threading.RLock()
        with self.repo._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS learning_micro_submissions (
                    submission_id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(assignment_id, request_id));
                CREATE TABLE IF NOT EXISTS learning_micro_reviews (
                    review_id TEXT PRIMARY KEY, submission_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_micro_submission_assignment
                    ON learning_micro_submissions(assignment_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_micro_review_submission
                    ON learning_micro_reviews(submission_id, created_at);
            """)
        self.jobs = _JudgeJobs(job_root, self._run_judge, job_prefix="learning_judge", max_workers=1)
        # Job files are the registry, so a crash between create() and a database
        # insert cannot lose idempotency or cause a second model call.
        self._job_bindings: dict[tuple[str, str, str, str], str] = {}
        self._target_jobs: dict[tuple[str, str], list[str]] = {}
        for job in sorted(self.jobs.list(limit=10**9), key=lambda item: item["created_at"]):
            request = job.get("request") or {}
            key = (request.get("source_kind"), request.get("source_id"), request.get("source_sha256"),
                   request.get("environment_sha256", ""))
            if all(isinstance(value, str) for value in key):
                self._job_bindings.setdefault(key, job["job_id"])
                self._target_jobs.setdefault(key[:2], []).append(job["job_id"])

    def shutdown(self) -> None:
        self.jobs.shutdown()

    def _course(self, assignment_id: str):
        assignment = self.learning.assignment(assignment_id)
        course = self.repo.get_course(assignment.course_id) if assignment.course_id else None
        return assignment, course

    def _exercises(self, course) -> list[dict[str, Any]]:
        rows = (course.learning_units.get("micro_practices") or []) if course else []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Course micro-practices must be objects")
        ids = [row.get("practice_id") for row in rows]
        if any(not isinstance(key, str) or not key.strip() for key in ids) or len(set(ids)) != len(ids):
            raise ValueError("Course micro-practice IDs must be nonempty and unique")
        return deepcopy(rows)

    def _submission(self, submission_id: str) -> dict[str, Any]:
        with self.repo._connect() as db:
            row = db.execute("SELECT payload FROM learning_micro_submissions WHERE submission_id=?", (submission_id,)).fetchone()
        if not row:
            raise KeyError(submission_id)
        return json.loads(row[0])

    def _reviews(self, submission_id: str) -> list[dict[str, Any]]:
        with self.repo._connect() as db:
            rows = db.execute("SELECT payload FROM learning_micro_reviews WHERE submission_id=? ORDER BY created_at, review_id", (submission_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_submission(self, submission_id: str) -> dict[str, Any]:
        item = self._submission(submission_id)
        item["human_reviews"] = self._reviews(submission_id)
        with self._lock:
            ids = list(self._target_jobs.get(("submission", submission_id), []))
        item["judge_jobs"] = [self.get_job(key) for key in ids]
        item["latest_judge_job"] = item["judge_jobs"][-1] if item["judge_jobs"] else None
        return item

    def micro_practices(self, assignment_id: str) -> dict[str, Any]:
        assignment, course = self._course(assignment_id)
        with self.repo._connect() as db:
            rows = db.execute("SELECT submission_id FROM learning_micro_submissions WHERE assignment_id=? ORDER BY created_at, submission_id", (assignment_id,)).fetchall()
        exercises = self._exercises(course)
        current = {item["practice_id"]: _hash(item) for item in exercises}
        submissions = [self.get_submission(row[0]) for row in rows]
        for item in submissions:
            item["exercise_current"] = current.get(item["practice_id"]) == _hash(item["exercise_snapshot"])
        attempt_judge_jobs = {}
        for attempt in self.learning.attempts(assignment_id):
            with self._lock:
                ids = list(self._target_jobs.get(("attempt", attempt["id"]), []))
            attempt_judge_jobs[attempt["id"]] = [self.get_job(key) for key in ids]
        return {"assignment_id": assignment_id, "course_id": assignment.course_id,
                "exercises": exercises, "submissions": submissions, "attempt_judge_jobs": attempt_judge_jobs}

    def submit(self, assignment_id: str, body: dict[str, Any] | PracticeSubmissionRequest) -> dict[str, Any]:
        data = (body if isinstance(body, PracticeSubmissionRequest) else PracticeSubmissionRequest.model_validate(body)).model_dump()
        input_sha = _hash(data)
        assignment, course = self._course(assignment_id)
        with self.repo._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT submission_id,input_sha256 FROM learning_micro_submissions WHERE assignment_id=? AND request_id=?", (assignment_id, data["request_id"])).fetchone()
            if old:
                if old["input_sha256"] != input_sha:
                    raise ValueError("Request ID reused with different submission content")
                submission_id = old["submission_id"]
            else:
                exercise = next((row for row in self._exercises(course) if row["practice_id"] == data["practice_id"]), None)
                if exercise is None:
                    raise ValueError("Micro-practice is not part of this assignment's course")
                competency_id = _text(exercise.get("competency_id"), "competency_id", 160)
                target = _text(exercise.get("target_behavior"), "target_behavior", 6000)
                objective = next((deepcopy(row) for row in course.learning_objectives
                                  if (row.get("id") or row.get("objective_id")) == competency_id), None)
                if objective is None:
                    objective = {"id": competency_id, "description": target}
                # A short exercise tests its target, not every behaviour in the
                # wider competency. The original competency remains in context.
                objective["observable_behaviors"] = [target]
                references = [deepcopy(row) for row in course.knowledge_references
                              if row.get("unit_id") in (exercise.get("knowledge_unit_ids") or [])]
                submission_id = "submission_" + uuid.uuid4().hex
                item = {"schema_version": "theraskill_micro_submission_v1", "submission_id": submission_id,
                        "assignment_id": assignment_id, "course_id": assignment.course_id, **data,
                        "created_at": stamp(), "exercise_snapshot": exercise,
                        "objectives_snapshot": [objective], "references_snapshot": references}
                db.execute("INSERT INTO learning_micro_submissions VALUES (?,?,?,?,?,?)",
                           (submission_id, assignment_id, data["request_id"], input_sha,
                            item["created_at"], json.dumps(item, ensure_ascii=False)))
        return self.get_submission(submission_id)

    def review_submission(self, submission_id: str, *, decision: str, comment: str, reviewer: str,
                          score: float | None = None) -> dict[str, Any]:
        self._submission(submission_id)
        if decision not in {"approved", "needs_practice", "rejected"}:
            raise ValueError("Provide an approved, needs_practice, or rejected decision")
        comment = _text(comment, "comment", 4000)
        reviewer = _text(reviewer, "reviewer", 160)
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))
                                  or not math.isfinite(score) or not 1 <= score <= 5):
            raise ValueError("Score must be a finite number from 1 to 5")
        item = {"review_id": "micro_review_" + uuid.uuid4().hex, "submission_id": submission_id,
                "review_kind": "human", "decision": decision, "comment": comment,
                "reviewer": reviewer, "score": score, "created_at": stamp()}
        with self.repo._connect() as db:
            db.execute("INSERT INTO learning_micro_reviews VALUES (?,?,?,?)",
                       (item["review_id"], submission_id, item["created_at"], json.dumps(item, ensure_ascii=False)))
        return item

    def _snapshot(self, source_kind: str, source_id: str, language: str | None = None) -> dict[str, Any]:
        if source_kind == "submission":
            item = self._submission(source_id)
            exercise = {key: value for key, value in item["exercise_snapshot"].items()
                        if key not in {"example_response", "example_discussion"}}
            transcript = []
            stimulus = exercise.get("scenario_text")
            if isinstance(stimulus, str) and stimulus.strip():
                transcript.append({"turn_id": "stimulus:" + source_id, "role": "exercise_context", "text": stimulus})
            transcript.append({"turn_id": "submission:" + source_id, "role": "trainee", "text": item["response_text"]})
            return {"objectives": item["objectives_snapshot"],
                    "transcript": transcript,
                    "references": item["references_snapshot"], "language": item["language"],
                    "context": {"exercise": exercise, "source_kind": "micro_practice",
                                "assignment_id": item["assignment_id"], "submission_id": source_id,
                                "hint_used": item["hint_used"], "self_reflection": item["self_reflection"],
                                "evidence_scope": "Only the trainee response is learner evidence. The exercise_context turn is the supplied stimulus and can support context citations, never learner performance. Hints and self-reflection are context."}}
        if source_kind != "attempt":
            raise ValueError("Unknown judge source kind")
        if language not in {"zh", "en"}:
            raise ValueError("Judge language must be zh or en")
        with self.repo._connect() as db:
            row = db.execute("SELECT payload FROM learning_attempts WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise KeyError(source_id)
        attempt = json.loads(row[0])
        assignment, course = self._course(attempt["assignment_id"])
        view = self.learning.sessions.get_snapshot(attempt["session_id"]).public_view.model_dump(mode="json")
        if view.get("mode") != "human_therapist_vs_patient":
            raise ValueError("Formative therapist feedback requires a human therapist practice session")
        if (view.get("scenario") or {}).get("scenario_id") not in assignment.scenario_ids:
            raise ValueError("Session scenario is no longer part of the assignment")
        transcript = []
        for index, turn in enumerate(view.get("transcript") or []):
            role = str(turn.get("role") or "").casefold()
            if role not in {"therapist", "human_therapist", "patient", "human_patient", "patient_agent"}:
                continue
            if not isinstance(turn.get("text"), str) or not turn["text"].strip():
                continue
            transcript.append({"turn_id": turn.get("turn_id") or f"{attempt['session_id']}:turn:{index + 1}",
                               "role": "trainee" if role in {"therapist", "human_therapist"} else "patient",
                               "text": turn["text"]})
        if not any(turn["role"] == "trainee" for turn in transcript):
            raise ValueError("Session has no actual learner response to evaluate")
        objectives = course.learning_objectives if course else [{"id": tag, "description": tag} for tag in assignment.skill_tags]
        if not objectives:
            raise ValueError("Configure course objectives before requesting feedback")
        return {"objectives": deepcopy(objectives), "transcript": transcript,
                "references": deepcopy(course.knowledge_references) if course else [], "language": language,
                "context": {"source_kind": "full_session", "assignment_id": assignment.assignment_id,
                            "attempt_id": source_id, "session_id": attempt["session_id"],
                            "session_sha256": session_fingerprint(view), "session_status": view.get("status"),
                            "scenario": view.get("scenario"), "hint_used": None,
                            "support_conditions": "Hint use was not recorded for this session; do not infer independent competence.",
                            "self_reflection": attempt.get("self_reflection") or "",
                            "evidence_scope": "Only actual transcript turns are learner evidence; self-reflection and scenario descriptions are context."}}

    def _start_judge(self, source_kind: str, source_id: str, language: str | None, actor: str) -> dict[str, Any]:
        snapshot = self._snapshot(source_kind, source_id, language)
        source_sha = _hash(snapshot)
        environment_sha, available = self._environment()
        key = (source_kind, source_id, source_sha, environment_sha)
        with self._lock:
            existing = self._job_bindings.get(key)
            if existing:
                return self.get_job(existing)
            job = self.jobs.create({"source_kind": source_kind, "source_id": source_id,
                                    "source_sha256": source_sha, "snapshot": snapshot,
                                    "environment_sha256": environment_sha,
                                    "environment_status": "available" if available else "unavailable", "count": 1}, actor)
            self._job_bindings[key] = job["job_id"]
            self._target_jobs.setdefault(key[:2], []).append(job["job_id"])
        return self.get_job(job["job_id"])

    def judge_submission(self, submission_id: str, actor: str = "human_therapist") -> dict[str, Any]:
        return self._start_judge("submission", submission_id, None, actor)

    def judge_attempt(self, attempt_id: str, language: str = "zh", actor: str = "human_therapist") -> dict[str, Any]:
        return self._start_judge("attempt", attempt_id, language, actor)

    def _environment(self) -> tuple[str, bool]:
        try:
            signature = self.evaluation_signature() if self.evaluation_signature else {}
            available = not (isinstance(signature, dict) and signature.get("status") == "unavailable")
            return _hash(signature), available
        except Exception:
            # A configuration exception may contain private URLs or credentials.
            return _hash({"status": "unavailable"}), False

    def _check_environment(self, request: dict[str, Any]) -> None:
        current_sha, available = self._environment()
        if not available:
            raise _EvaluationEnvironmentError("Evaluation environment unavailable; check model and knowledge-index settings.")
        if request.get("environment_sha256") != current_sha:
            raise _EvaluationEnvironmentError("Evaluation environment changed: model settings, judge prompt, or knowledge index differ; request new feedback.")

    def _check_source(self, request: dict[str, Any]) -> None:
        snapshot = request["snapshot"]
        if _hash(snapshot) != request["source_sha256"]:
            raise ValueError("Saved judge input failed its integrity check")
        current = self._snapshot(request["source_kind"], request["source_id"], snapshot["language"])
        if _hash(current) != request["source_sha256"]:
            raise ValueError("Judge input is stale: the session, reflection, or course changed; request new feedback")
        self._check_environment(request)

    def _run_judge(self, _job_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self._check_source(request)
        result = self.judge_factory().evaluate(**deepcopy(request["snapshot"]))
        if not isinstance(result, dict) or result.get("status") != "complete" or result.get("assessment_kind") != "formative":
            raise ValueError("Judge did not return a complete formative assessment")
        self._check_source(request)
        # Retain source and support conditions even if a custom judge omits them.
        result = deepcopy(result)
        result["source"] = {key: request[key] for key in ("source_kind", "source_id", "source_sha256")}
        result["source"]["environment_sha256"] = request["environment_sha256"]
        result["hint_used"] = request["snapshot"]["context"].get("hint_used")
        result["human_approval"] = False
        result["counts_toward_completion"] = False
        _hash(result)  # Ensure JSON-safe finite data before the durable store writes it.
        return result

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job = deepcopy(job)
        request = job.pop("request", {})
        job["source"] = {key: request.get(key) for key in ("source_kind", "source_id", "source_sha256")}
        job["source"]["environment_sha256"] = request.get("environment_sha256")
        job["environment_status"] = request.get("environment_status", "unbound")
        job["language"] = (request.get("snapshot") or {}).get("language")
        try:
            self._check_source(request)
            job["source_current"], job["stale_reason"] = True, None
        except _EvaluationEnvironmentError as exc:
            job["source_current"], job["stale_reason"] = False, str(exc)
        except (KeyError, ValueError, TypeError):
            job["source_current"], job["stale_reason"] = False, "Input changed or is unavailable; request new feedback for the current source."
        job["retry_allowed"] = job["source_current"] and job["status"] in {"failed", "partial"} and int(job.get("execution_count") or 0) < MAX_JUDGE_EXECUTIONS
        return job

    def retry_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        self._check_source(job["request"])
        self.jobs.retry(job_id)
        return self.get_job(job_id)


def create_practice_router(practice: LearningPracticeService, caller_role) -> APIRouter:
    router = APIRouter()

    def execute(action):
        try:
            return action()
        except KeyError:
            raise HTTPException(404, "Learning record not found") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.get("/assignments/{assignment_id}/micro-practices")
    def exercises(assignment_id: str, _role: ActorRole = Depends(caller_role)):
        return execute(lambda: practice.micro_practices(assignment_id))

    @router.post("/assignments/{assignment_id}/micro-practices", status_code=201)
    def submit(assignment_id: str, body: PracticeSubmissionRequest, _role: ActorRole = Depends(caller_role)):
        return execute(lambda: practice.submit(assignment_id, body))

    @router.post("/learning/submissions/{submission_id}/judge", status_code=202)
    def judge_submission(submission_id: str, role: ActorRole = Depends(caller_role)):
        return execute(lambda: practice.judge_submission(submission_id, str(role)))

    @router.post("/learning/attempts/{attempt_id}/judge", status_code=202)
    def judge_attempt(attempt_id: str, body: AttemptJudgeRequest | None = None, role: ActorRole = Depends(caller_role)):
        return execute(lambda: practice.judge_attempt(attempt_id, body.language if body else "zh", str(role)))

    @router.get("/learning/judge-jobs/{job_id}")
    def get_job(job_id: str, _role: ActorRole = Depends(caller_role)):
        return execute(lambda: practice.get_job(job_id))

    @router.post("/learning/judge-jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, _role: ActorRole = Depends(caller_role)):
        return execute(lambda: practice.retry_job(job_id))

    @router.post("/learning/submissions/{submission_id}/review", status_code=201)
    def review(submission_id: str, body: SubmissionReviewRequest, role: ActorRole = Depends(caller_role)):
        if role != ActorRole.INSTRUCTOR:
            raise HTTPException(403, "Instructor role is required")
        return execute(lambda: practice.review_submission(submission_id, **body.model_dump()))

    return router
