"""Framework-neutral local execution kernel for TheraSkillAgent.

The kernel deliberately starts with deterministic text tasks.  Model-backed
skills can build on the same task/result/event contract without forcing every
installation to expose an HTTP port.
"""

from __future__ import annotations

import json
import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import AgentResult, AgentTask, SkillExecutionContext
from .paths import active_release_id, data_root, project_root
from .skills import SkillManager
from .source_adapters import inspect_source, sample_source
from .text import chunk_text, extract_sections, normalize_text


class AgentRuntime:
    """Execute built-in tasks and record a compact local audit trail."""

    def __init__(self, root: str | Path | None = None, *, manager: SkillManager | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.manager = manager or SkillManager()
        self.events_path = data_root(self.root) / "runs" / "events.jsonl"

    def _event(self, payload: Mapping[str, Any]) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(payload), ensure_ascii=False, default=str) + "\n")

    def _read_task_text(self, task: AgentTask) -> str:
        if task.kind.startswith(("index.", "knowledge.")):
            return str(task.input_path or task.text or "")
        if task.text is not None:
            return str(task.text)
        if task.input_path:
            path = Path(task.input_path).expanduser()
            if not path.is_absolute():
                path = self.root / path
            # Transcript loading performs its own encoding detection. Read a
            # replacement-decoded copy here only for the generic execution
            # fingerprint; the transcript branch reloads original bytes.
            if task.kind.startswith("transcript."):
                return path.read_bytes().decode("utf-8", errors="replace")
            return path.read_text(encoding="utf-8")
        return ""

    def _context(self, task: AgentTask, run_id: str, text: str) -> SkillExecutionContext:
        params = dict(task.parameters)
        selected_skill = task.allowed_skills[0] if task.allowed_skills else params.get("skill")
        return SkillExecutionContext(
            run_id=run_id,
            task_id=task.task_id,
            skill_name=str(selected_skill) if selected_skill else None,
            provider_profile=str(params.get("provider_profile") or os.environ.get("THERASKILL_LLM_PROFILE") or "") or None,
            model=str(params.get("model") or "") or None,
            prompt_version=str(params.get("prompt_version") or "") or None,
            knowledge_release_id=str(params.get("knowledge_release_id") or active_release_id()),
            input_fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            parameters=params,
        )

    def run(self, task: AgentTask | Mapping[str, Any]) -> AgentResult:
        if not isinstance(task, AgentTask):
            task = AgentTask.from_mapping(task)
        run_id = str(task.metadata.get("run_id") or f"run_{uuid.uuid4().hex}")
        started = datetime.now(timezone.utc).isoformat()
        read_error: Exception | None = None
        try:
            text = self._read_task_text(task)
        except Exception as exc:  # noqa: BLE001 - report through AgentResult
            text = ""
            read_error = exc
        context = self._context(task, run_id, text)
        self._event({"event": "task_started", "task_id": task.task_id, "run_id": run_id, "kind": task.kind, "context": context.to_dict(), "at": started})
        try:
            if read_error is not None:
                raise read_error
            params = dict(task.parameters)
            if task.kind.startswith("index.") or task.kind in {"knowledge.install", "knowledge.activate", "knowledge.rollback"}:
                from .resource_jobs import run_operation
                if task.input_path:
                    params.setdefault("knowledge", task.input_path)
                output = run_operation(task.kind.split(".", 1)[1], params)
            elif task.kind in {"text.normalize", "normalize"}:
                output: Any = {"text": normalize_text(text, preserve_newlines=not bool(params.get("flat")))}
            elif task.kind in {"text.chunk", "chunk"}:
                chunks = chunk_text(
                    text,
                    max_chars=int(params.get("max_chars", 4000)),
                    overlap=int(params.get("overlap", 200)),
                )
                output = {"chunks": [item.to_dict() for item in chunks]}
            elif task.kind in {"text.sections", "sections"}:
                output = {"sections": extract_sections(text)}
            elif task.kind in {"skill.inspect", "skill.optimize"}:
                target = params.get("target") or task.text
                if not target:
                    raise ValueError("skill target is required")
                report = self.manager.inspect(str(target)) if task.kind == "skill.inspect" else self.manager.optimize(str(target), write=bool(params.get("write")))
                output = report.to_dict()
            elif task.kind in {"source.inspect", "source.sample"}:
                source = task.input_path or task.text
                if not source:
                    raise ValueError("source path is required")
                if task.kind == "source.inspect":
                    output = inspect_source(source).to_dict()
                else:
                    output = {"records": [item.to_dict() for item in sample_source(source, int(params.get("limit", 5)))]}
            elif task.kind in {"extraction.plan", "knowledge.plan"}:
                goal = str(params.get("goal") or task.text or "").strip()
                if not goal:
                    raise ValueError("extraction goal is required")
                from .extraction.planner import build_plan

                output = build_plan(
                    goal,
                    target_schema=params.get("target_schema"),
                    source={"path": task.input_path} if task.input_path else {},
                    language=task.language,
                    status="planned",
                ).to_dict()
            elif task.kind in {"transcript.inspect", "transcript.analyze", "transcript.package", "transcript.review"}:
                from .transcript import analyze_transcript, build_bundle, load_transcript

                source = task.input_path or params.get("source_path")
                if not source:
                    raise ValueError("transcript source path is required")
                document, raw_text = load_transcript(
                    source,
                    language=task.language or str(params.get("language") or "auto"),
                    speaker_role_overrides=params.get("speaker_role_overrides") if isinstance(params.get("speaker_role_overrides"), Mapping) else None,
                )
                if task.kind == "transcript.inspect":
                    output = {"document": document.to_dict(), "sample_turns": [item.to_dict() for item in document.turns[:5]]}
                else:
                    analysis = analyze_transcript(document)
                    output = {"document": document.to_dict(), "analysis": analysis}
                    if task.kind == "transcript.review":
                        from .reviews import build_review_plan, run_skill_reviews

                        skills = params.get("skills") if isinstance(params.get("skills"), list) else None
                        mode = str(params.get("mode") or "plan").lower()
                        max_turns = int(params.get("max_turns")) if params.get("max_turns") else None
                        if mode == "model":
                            output["reviews"] = run_skill_reviews(
                                document,
                                analysis,
                                skills=skills,
                                provider_profile=params.get("provider_profile"),
                                max_turns=max_turns,
                            )
                        elif mode == "plan":
                            output["review_plan"] = build_review_plan(document, analysis, skills=skills, max_turns=max_turns)
                        else:
                            raise ValueError("transcript.review mode must be plan or model")
                    if task.kind == "transcript.package":
                        output_dir = params.get("output_dir")
                        if not output_dir:
                            raise ValueError("transcript.package requires output_dir")
                        output["artifacts"] = build_bundle(document, analysis, output_dir, include_raw=not bool(params.get("exclude_raw")), raw_text=raw_text)
            else:
                raise ValueError(f"unsupported task kind: {task.kind}")
            finished = datetime.now(timezone.utc).isoformat()
            result = AgentResult(task_id=task.task_id, status="succeeded", output=output, started_at=started, finished_at=finished, metadata={"project_root": str(self.root), "network_access": "disabled by default", "run_id": run_id, "skill": context.skill_name}, run_id=run_id, provenance=context.to_dict())
        except Exception as exc:
            finished = datetime.now(timezone.utc).isoformat()
            result = AgentResult(task_id=task.task_id, status="failed", error=str(exc), started_at=started, finished_at=finished, metadata={"project_root": str(self.root), "run_id": run_id}, run_id=run_id, provenance=context.to_dict())
        self._event({"event": "task_finished", "task_id": result.task_id, "run_id": run_id, "status": result.status, "at": result.finished_at, "error": result.error, "provenance": result.provenance})
        return result

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent audit events without requiring a service port."""
        if not self.events_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines()[-max(0, int(limit)):]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows
