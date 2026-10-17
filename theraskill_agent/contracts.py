"""Stable task and result contracts for the standalone TheraSkillAgent.

These small dataclasses are intentionally independent of a web framework.  A
CLI command, a service, or a future skill can use the same request/result
shape without importing the historical host runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentTask:
    """A serializable unit of work submitted to the local agent."""

    kind: str
    text: str | None = None
    input_path: str | None = None
    language: str = "auto"
    parameters: dict[str, Any] = field(default_factory=dict)
    allowed_skills: list[str] = field(default_factory=list)
    task_id: str = field(default_factory=lambda: uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentTask":
        raw = dict(value)
        params = raw.get("parameters")
        raw["parameters"] = dict(params) if isinstance(params, Mapping) else {}
        skills = raw.get("allowed_skills")
        raw["allowed_skills"] = list(skills) if isinstance(skills, (list, tuple)) else []
        return cls(**{key: raw[key] for key in cls.__dataclass_fields__ if key in raw})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentResult:
    """Structured, JSON-safe result returned by an agent task."""

    task_id: str
    status: str
    output: Any = None
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillManifest:
    """Minimal manifest used by the local skill manager and runtime."""

    name: str
    description: str
    version: str = "0.1.0"
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillExecutionContext:
    """Audit context shared by every skill invocation."""

    run_id: str
    task_id: str
    skill_name: str | None = None
    provider_kind: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    knowledge_release_id: str | None = None
    input_fingerprint: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
