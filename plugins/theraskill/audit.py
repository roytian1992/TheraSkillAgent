"""Append-only metadata audit trail for TheraSkill Native Tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from theraskill_agent.paths import data_root

from .contracts import TheraSkillRequest


def record(request: TheraSkillRequest, status: str, *, error: str = "") -> None:
    path = data_root() / "audit" / "tool-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "request_id": request.request_id,
        "action": request.action,
        "actor": request.actor,
        "domain": request.domain,
        "scope": request.scope,
        "profile": request.profile,
        "release_id": request.release_id,
        "session_id": request.session_id,
        "run_id": request.run_id,
        "idempotency_key": request.idempotency_key,
        "approval_id": request.approval_id,
        "status": status,
    }
    if error:
        event["error"] = error[:500]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
