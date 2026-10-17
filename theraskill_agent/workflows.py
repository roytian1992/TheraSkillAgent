"""Resumable interaction contracts shared by Hermes and TheraSkillAgent.

The host runtime owns the conversation and can render questions, forms, and
approval cards.  TheraSkillAgent owns the durable workflow state and domain
side effects.  This module intentionally contains only the small protocol
needed to pass a workflow from one turn to the next.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .paths import data_root


WORKFLOW_STATUSES = {
    "active",
    "needs_input",
    "needs_approval",
    "pending",
    "completed",
    "failed",
    "cancelled",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SkillWorkflow:
    """A durable, host-neutral workflow envelope.

    ``state`` is intentionally opaque to the host. Hermes only needs the
    interaction card and resume payload; domain code may store plan ids, job
    ids, review ids, or release candidates inside it.
    """

    workflow_id: str = field(default_factory=lambda: f"wf_{uuid4().hex}")
    kind: str = "skill"
    status: str = "active"
    session_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    interaction: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    updated_at: str = field(default_factory=_now)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in WORKFLOW_STATUSES:
            raise ValueError(f"unsupported workflow status: {self.status}")
        if self.interaction:
            self.interaction.setdefault("workflow_id", self.workflow_id)

    def wait_for(self, status: str, interaction: Mapping[str, Any]) -> "SkillWorkflow":
        if status not in {"needs_input", "needs_approval", "pending"}:
            raise ValueError(f"workflow wait status must be needs_input, needs_approval or pending: {status}")
        self.status = status
        self.interaction = dict(interaction)
        self.interaction.setdefault("workflow_id", self.workflow_id)
        self.updated_at = _now()
        return self

    def finish(self, *, status: str = "completed", state: Mapping[str, Any] | None = None, error: str | None = None) -> "SkillWorkflow":
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"workflow finish status must be completed, failed or cancelled: {status}")
        self.status = status
        if state is not None:
            self.state = dict(state)
        self.error = error
        self.interaction = {}
        self.updated_at = _now()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SkillWorkflow":
        raw = dict(value)
        raw["state"] = dict(raw.get("state") or {})
        raw["interaction"] = dict(raw.get("interaction") or {})
        return cls(**{key: raw[key] for key in cls.__dataclass_fields__ if key in raw})


def interaction_for_hermes(workflow: SkillWorkflow) -> dict[str, Any]:
    """Return a small, safe rendering envelope for the host runtime."""

    return {
        "workflow_id": workflow.workflow_id,
        "kind": workflow.interaction.get("kind", "progress") if workflow.interaction else "progress",
        "status": workflow.status,
        "title": workflow.interaction.get("title", workflow.kind) if workflow.interaction else workflow.kind,
        "prompt": workflow.interaction.get("prompt", "") if workflow.interaction else "",
        "actions": list(workflow.interaction.get("actions") or ()) if workflow.interaction else [],
        "schema": workflow.interaction.get("schema", {}) if workflow.interaction else {},
        "value": workflow.interaction.get("value", {}) if workflow.interaction else {},
        "resume": workflow.interaction.get("resume", {}) if workflow.interaction else {},
    }


class WorkflowStore:
    """Small atomic JSON store for host-independent workflow checkpoints."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = (data_root() / "workflows" if root is None else Path(root)).expanduser().resolve()

    def _path(self, workflow_id: str):
        clean = str(workflow_id or "").strip()
        if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean:
            raise ValueError("invalid workflow_id")
        return self.root / f"{clean}.json"

    def save(self, workflow: SkillWorkflow) -> SkillWorkflow:
        path = self._path(workflow.workflow_id)
        self.root.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
        return workflow

    def get(self, workflow_id: str) -> SkillWorkflow | None:
        path = self._path(workflow_id)
        if not path.exists():
            return None
        try:
            return SkillWorkflow.from_mapping(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid workflow checkpoint: {path}") from exc

    def delete(self, workflow_id: str) -> None:
        self._path(workflow_id).unlink(missing_ok=True)
