"""Verify each claimed quote against individual, untouched source strings."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import ExtractionPlan


def _strings(value: Any, path: str = "$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for name, child in value.items():
            yield from _strings(child, f"{path}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _strings(child, f"{path}[{index}]")


def _segments(path: str) -> list[str]:
    return [item for item in path.replace("[*]", "").replace("[]", "").split(".") if item and item != "*"]


def _declared(schema: Mapping[str, Any], segments: list[str]) -> bool:
    if not segments:
        return True
    if "array" in ([schema.get("type")] if isinstance(schema.get("type"), str) else schema.get("type", [])):
        return _declared(schema.get("items", {}), segments)
    child = schema.get("properties", {}).get(segments[0])
    return isinstance(child, Mapping) and _declared(child, segments[1:])


def _values(value: Any, segments: list[str], path: str = "$"):
    if isinstance(value, list):
        if not value:
            yield path, None
        for index, child in enumerate(value):
            yield from _values(child, segments, f"{path}[{index}]")
    elif not segments:
        yield path, value
    elif isinstance(value, Mapping) and segments[0] in value:
        yield from _values(value[segments[0]], segments[1:], f"{path}.{segments[0]}")
    else:
        yield path + "." + ".".join(segments), None


def evidence_report(extracted: Mapping[str, Any], source: Any, plan: ExtractionPlan) -> dict[str, Any]:
    source_strings = list(_strings(source))
    fields_found: list[str] = []
    grounded: list[str] = []
    ungrounded: list[str] = []
    missing: list[str] = []
    quotes: list[dict[str, Any]] = []
    for field in plan.evidence_fields:
        segments = _segments(field)
        values = list(_values(extracted, segments))
        present = [(path, value) for path, value in values if value is not None]
        # Default field names are alternative aliases, not three required fields.
        if not present and not _declared(plan.target_schema, segments):
            continue
        if present:
            fields_found.append(field)
        field_bad = False
        field_missing = False
        for path, value in values:
            if value is None or (isinstance(value, str) and not value.strip()):
                if plan.evidence_required:
                    missing.append(path)
                    field_missing = True
                continue
            quote = value.strip() if isinstance(value, str) else None
            matches = []
            if quote:
                for source_path, text in source_strings:
                    start = text.find(quote)
                    if start >= 0:
                        matches.append({"source_path": source_path, "start": start, "end": start + len(quote)})
            quotes.append({"field": field, "output_path": path, "quote": quote,
                           "status": "grounded" if matches else "ungrounded",
                           "source_matches": matches})
            if not matches:
                field_bad = True
        if field_bad:
            ungrounded.append(field)
        elif present and not field_missing:
            grounded.append(field)
    required_missing = plan.evidence_required and (bool(missing) or not fields_found)
    status = "missing" if required_missing else "needs_review" if ungrounded else "pass"
    return {"required": plan.evidence_required, "fields_found": fields_found,
            "grounded_fields": grounded, "ungrounded_fields": ungrounded,
            "missing_paths": missing, "quotes": quotes, "status": status,
            "offset_basis": "unicode_characters_in_source_string",
            "validation_scope": "verbatim_quotes_only"}
