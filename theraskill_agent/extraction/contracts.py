"""Contracts for user-defined extraction plans.

The extraction engine treats a dataset as an input source only.  The target
knowledge type and its JSON schema live in an explicit, versioned plan so a
new source can be used without adding a hard-coded dataset adapter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


PLAN_VERSION = "theraskill_dynamic_extraction_plan_v1"


class PlanError(ValueError):
    """Raised when an extraction plan is unsafe or structurally invalid."""


def _text(value: Any, name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise PlanError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise PlanError(f"{name} is required")
    return value


def validate_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the intentionally small JSON Schema subset used by extraction.

    Supporting a bounded subset keeps plans portable across local runners and
    prevents an LLM-generated schema from smuggling executable behavior into
    the worker.
    """
    if not isinstance(schema, Mapping):
        raise PlanError("target_schema must be an object")
    schema = dict(schema)
    if schema.get("type", "object") != "object":
        raise PlanError("target_schema.type must be object")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        raise PlanError("target_schema.properties must contain at least one field")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise PlanError("target_schema.required must be a list of field names")
    unknown_required = [name for name in required if name not in properties]
    if unknown_required:
        raise PlanError(f"required fields missing from properties: {unknown_required}")
    allowed_types = {"string", "number", "integer", "boolean", "array", "object"}
    for name, definition in properties.items():
        if not isinstance(name, str) or not name.strip():
            raise PlanError("schema field names must be non-empty strings")
        if not isinstance(definition, Mapping):
            raise PlanError(f"schema field {name!r} must be an object")
        field_type = definition.get("type", "string")
        if field_type not in allowed_types:
            raise PlanError(f"unsupported schema type for {name!r}: {field_type}")
        if "enum" in definition and (
            not isinstance(definition["enum"], list) or not definition["enum"]
        ):
            raise PlanError(f"schema enum for {name!r} must be a non-empty list")
    return schema


@dataclass(frozen=True)
class ExtractionPlan:
    """A complete, reviewable description of one dynamic extraction run."""

    goal: str
    target_schema: Mapping[str, Any]
    source: Mapping[str, Any] = field(default_factory=dict)
    unit_definition: str = "one source record"
    evidence_required: bool = True
    evidence_fields: tuple[str, ...] = ("evidence", "evidence_span", "source_quote")
    language: str = "auto"
    quality_rules: tuple[str, ...] = (
        "Only use information supported by the source record.",
        "Use unknown when the source does not support a field.",
        "Keep evidence spans verbatim whenever possible.",
    )
    operations: tuple[str, ...] = ("extract", "normalize", "validate")
    model_profile: str = "default"
    prompt_version: str = "dynamic_extraction_prompt_v1"
    plan_id: str = field(default_factory=lambda: f"plan_{uuid4().hex[:16]}")
    status: str = "draft"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = PLAN_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", _text(self.goal, "goal", required=True))
        object.__setattr__(self, "unit_definition", _text(self.unit_definition, "unit_definition", required=True))
        object.__setattr__(self, "language", _text(self.language, "language") or "auto")
        object.__setattr__(self, "model_profile", _text(self.model_profile, "model_profile") or "default")
        object.__setattr__(self, "prompt_version", _text(self.prompt_version, "prompt_version", required=True))
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id", required=True))
        if not isinstance(self.source, Mapping):
            raise PlanError("source must be an object")
        if not isinstance(self.evidence_required, bool):
            raise PlanError("evidence_required must be boolean")
        if not isinstance(self.evidence_fields, (tuple, list)) or any(not isinstance(v, str) for v in self.evidence_fields):
            raise PlanError("evidence_fields must be a list of strings")
        grounded_fields = tuple(
            field for field in self.evidence_fields
            if any(token in field.lower() for token in ("quote", "span", "source", "utterance", "text"))
            or field.strip().lower() in {"evidence", "evidence_text"}
        )
        if grounded_fields:
            object.__setattr__(self, "evidence_fields", grounded_fields)
        if not isinstance(self.quality_rules, (tuple, list)) or any(not isinstance(v, str) for v in self.quality_rules):
            raise PlanError("quality_rules must be a list of strings")
        if not isinstance(self.operations, (tuple, list)) or any(not isinstance(v, str) for v in self.operations):
            raise PlanError("operations must be a list of strings")
        if self.status not in {"draft", "planned", "approved"}:
            raise PlanError("status must be draft, planned or approved")
        object.__setattr__(self, "target_schema", validate_json_schema(self.target_schema))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExtractionPlan":
        if not isinstance(payload, Mapping):
            raise PlanError("extraction plan must be an object")
        for field_name in ("evidence_fields", "quality_rules", "operations"):
            value = payload.get(field_name)
            if value is not None and (not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value)):
                raise PlanError(f"{field_name} must be a list of strings")
        return cls(
            goal=payload.get("goal", ""),
            target_schema=payload.get("target_schema") or {},
            source=dict(payload.get("source") or {}),
            unit_definition=payload.get("unit_definition", "one source record"),
            evidence_required=payload.get("evidence_required", True),
            evidence_fields=tuple(payload.get("evidence_fields") or ("evidence", "evidence_span", "source_quote")),
            language=payload.get("language", "auto"),
            quality_rules=tuple(payload.get("quality_rules") or ()),
            operations=tuple(payload.get("operations") or ("extract", "normalize", "validate")),
            model_profile=payload.get("model_profile", "default"),
            prompt_version=payload.get("prompt_version", "dynamic_extraction_prompt_v1"),
            plan_id=payload.get("plan_id") or f"plan_{uuid4().hex[:16]}",
            status=payload.get("status", "draft"),
            created_at=payload.get("created_at") or datetime.now(timezone.utc).isoformat(),
            schema_version=payload.get("schema_version", PLAN_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("evidence_fields", "quality_rules", "operations"):
            payload[key] = list(payload[key])
        payload["target_schema"] = dict(self.target_schema)
        payload["source"] = dict(self.source)
        return payload
