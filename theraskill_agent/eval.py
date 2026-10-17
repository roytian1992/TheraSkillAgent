"""Small, deterministic regression runner shared by TheraSkillAgent skills.

Cases are JSON objects with ``case_id``, ``kind`` and ``input``.  The runner
does not call a model by itself: callers provide the observed output, or a
case may contain ``expected`` for exact/field-level checks.  This makes
provider, prompt and knowledge-release comparisons reproducible and cheap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    kind: str
    input: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvalCase":
        return cls(
            case_id=str(value.get("case_id") or value.get("id") or "case"),
            kind=str(value.get("kind") or "generic"),
            input=dict(value.get("input") or {}),
            expected=dict(value.get("expected") or {}),
            tags=tuple(str(item) for item in (value.get("tags") or [])),
        )


@dataclass(frozen=True)
class EvalItem:
    case_id: str
    kind: str
    status: str
    checks: dict[str, bool]
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "status": self.status,
            "checks": dict(self.checks),
            "details": dict(self.details),
        }


class EvalRunner:
    def evaluate(self, case: EvalCase, output: Any) -> EvalItem:
        checks: dict[str, bool] = {}
        expected = case.expected
        if "status" in expected:
            checks["status"] = isinstance(output, Mapping) and output.get("status") == expected["status"]
        if "required_keys" in expected:
            required = [str(key) for key in expected.get("required_keys") or []]
            checks["required_keys"] = isinstance(output, Mapping) and all(key in output for key in required)
        if "fields" in expected:
            fields = dict(expected.get("fields") or {})
            checks["fields"] = isinstance(output, Mapping) and all(output.get(key) == value for key, value in fields.items())
        if case.kind == "extraction":
            checks["evidence"] = isinstance(output, Mapping) and bool(output.get("evidence") or output.get("source_quote") or output.get("evidence_span"))
            checks["no_error"] = not (isinstance(output, Mapping) and output.get("error"))
        elif case.kind in {"support", "simulation"}:
            checks.setdefault("structured_output", isinstance(output, Mapping))
            if case.kind == "support":
                checks["safety_surface"] = isinstance(output, Mapping) and any(key in output for key in ("safety", "risk", "handoff_status"))
        elif case.kind == "skill":
            checks.setdefault("structured_output", isinstance(output, Mapping))
        else:
            checks.setdefault("structured_output", isinstance(output, Mapping))
        passed = bool(checks) and all(checks.values())
        return EvalItem(case.case_id, case.kind, "pass" if passed else "fail", checks, {"tags": list(case.tags)})

    def run(self, cases: Iterable[EvalCase], outputs: Mapping[str, Any], *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        items = [self.evaluate(case, outputs.get(case.case_id)) for case in cases]
        passed = sum(item.status == "pass" for item in items)
        return {
            "schema_version": "theraskill_eval_report_v1",
            "created_at": _now(),
            "counts": {"total": len(items), "passed": passed, "failed": len(items) - passed},
            "pass_rate": (passed / len(items)) if items else 0.0,
            "metadata": dict(metadata or {}),
            "items": [item.to_dict() for item in items],
        }


def load_cases(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        if Path(path).suffix.lower() == ".jsonl":
            values = [json.loads(line) for line in handle if line.strip()]
        else:
            payload = json.load(handle)
            values = payload if isinstance(payload, list) else payload.get("cases", [])
    return [EvalCase.from_mapping(value) for value in values if isinstance(value, Mapping)]
