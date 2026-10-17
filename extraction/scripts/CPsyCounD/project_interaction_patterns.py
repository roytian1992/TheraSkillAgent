#!/usr/bin/env python3
"""Project final CPsyCounD transition units into knowledge-unit envelopes.

This is a deterministic projection step. It does not call an LLM and does not
rewrite clinical content. The source transition fields are preserved inside the
knowledge-unit envelope, while QA, manifest, and controlled-label indexes are
derived from the existing normalized fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


DEFAULT_INPUT = "outputs/current/transition_units.jsonl"
DEFAULT_OUTPUT = "outputs/knowledge/units/CPsyCounD.interaction_pattern.jsonl"
DEFAULT_MANIFEST = "outputs/knowledge/manifests/CPsyCounD.interaction_pattern.manifest.json"
DEFAULT_QA = "outputs/knowledge/manifests/CPsyCounD.interaction_pattern.qa.json"
DEFAULT_INDEX = "outputs/knowledge/indexes/CPsyCounD.controlled_label_index.json"
DEFAULT_REGISTRY = "configs/normalization_registry.yaml"

KNOWLEDGE_SCHEMA_VERSION = "theraskill_knowledge_unit_v1"
KNOWLEDGE_TYPE = "interaction_pattern"
DATASET = "CPsyCounD"

REVIEW_FINAL_DECISIONS = {"pass", "re_extract", "delete"}
REVIEW_RETENTION_LABELS = {"positive_candidate", "needs_reextract", "delete"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            row["_source_line_no"] = line_no
            rows.append(row)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]], *, overwrite: bool) -> str:
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists: {path}; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            digest.update(line.encode("utf-8"))
            handle.write(line)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def registry_options(registry: dict[str, Any]) -> dict[str, set[str]]:
    options: dict[str, set[str]] = {}
    for group_name in ("strongly_controlled", "semi_controlled"):
        for field, values in (registry.get(group_name) or {}).items():
            if isinstance(values, list):
                options[str(field)] = {str(value) for value in values}
    return options


def add_invalid(
    invalid: list[dict[str, Any]],
    *,
    transition_unit_id: str | None,
    path: str,
    value: Any,
    allowed: set[str],
) -> None:
    invalid.append(
        {
            "transition_unit_id": transition_unit_id,
            "path": path,
            "value": value,
            "allowed": sorted(allowed),
        }
    )


def audit_scalar(
    row: dict[str, Any],
    invalid: list[dict[str, Any]],
    options: dict[str, set[str]],
    field: str,
    path: str,
    value: Any,
) -> None:
    if value is None:
        return
    allowed = options.get(field)
    if allowed is None:
        return
    if str(value) not in allowed:
        add_invalid(
            invalid,
            transition_unit_id=row.get("transition_unit_id"),
            path=path,
            value=value,
            allowed=allowed,
        )


def audit_list(
    row: dict[str, Any],
    invalid: list[dict[str, Any]],
    options: dict[str, set[str]],
    field: str,
    path: str,
    values: Any,
) -> None:
    if values is None:
        return
    if not isinstance(values, list):
        audit_scalar(row, invalid, options, field, path, values)
        return
    for idx, value in enumerate(values):
        audit_scalar(row, invalid, options, field, f"{path}[{idx}]", value)


def audit_controlled_labels(row: dict[str, Any], options: dict[str, set[str]]) -> list[dict[str, Any]]:
    invalid: list[dict[str, Any]] = []
    source = row.get("source_metadata") or {}
    pre_state = row.get("pre_state") or {}
    action = row.get("therapist_action") or {}
    response = row.get("client_response") or {}
    transition = row.get("transition") or {}
    review = row.get("review") or {}

    audit_scalar(row, invalid, options, "source_construction_type", "source.source_construction_type", source.get("source_construction_type"))
    audit_scalar(row, invalid, options, "evidence_tier", "source.evidence_tier", source.get("evidence_tier"))

    audit_list(row, invalid, options, "state_dimension", "content.pre_state.state_dimensions", pre_state.get("state_dimensions"))
    for idx, mention in enumerate(pre_state.get("state_mentions") or []):
        if not isinstance(mention, dict):
            continue
        audit_scalar(row, invalid, options, "state_dimension", f"content.pre_state.state_mentions[{idx}].dimension", mention.get("dimension"))
        audit_scalar(row, invalid, options, "certainty", f"content.pre_state.state_mentions[{idx}].certainty", mention.get("certainty"))
    audit_scalar(row, invalid, options, "client_readiness", "content.pre_state.readiness", pre_state.get("readiness"))
    audit_scalar(row, invalid, options, "alliance_state", "content.pre_state.alliance_state", pre_state.get("alliance_state"))
    audit_scalar(row, invalid, options, "disclosure_depth", "content.pre_state.disclosure_depth", pre_state.get("disclosure_depth"))
    audit_list(row, invalid, options, "risk_domain", "content.pre_state.risk_domains", pre_state.get("risk_domains"))
    audit_scalar(row, invalid, options, "risk_level", "content.pre_state.risk_level", pre_state.get("risk_level"))
    audit_scalar(row, invalid, options, "required_action", "content.pre_state.required_action", pre_state.get("required_action"))

    audit_list(row, invalid, options, "move_type", "content.therapist_action.move_types", action.get("move_types"))
    audit_list(row, invalid, options, "move_function", "content.therapist_action.move_functions", action.get("move_functions"))
    audit_list(row, invalid, options, "clinical_quality", "content.therapist_action.clinical_qualities", action.get("clinical_qualities"))
    for idx, move in enumerate(action.get("moves") or []):
        if not isinstance(move, dict):
            continue
        audit_scalar(row, invalid, options, "move_type", f"content.therapist_action.moves[{idx}].move_type", move.get("move_type"))
        audit_scalar(row, invalid, options, "move_function", f"content.therapist_action.moves[{idx}].move_function", move.get("move_function"))
        audit_scalar(row, invalid, options, "clinical_quality", f"content.therapist_action.moves[{idx}].clinical_quality", move.get("clinical_quality"))

    audit_scalar(row, invalid, options, "client_response_type", "content.client_response.response_type", response.get("response_type"))
    audit_scalar(row, invalid, options, "client_response_type", "content.transition.response_type", transition.get("response_type"))
    audit_scalar(row, invalid, options, "transition_type", "content.transition.transition_type", transition.get("transition_type"))
    audit_scalar(row, invalid, options, "confidence", "content.transition.confidence", transition.get("confidence"))

    if review.get("final_decision") not in REVIEW_FINAL_DECISIONS:
        add_invalid(
            invalid,
            transition_unit_id=row.get("transition_unit_id"),
            path="quality.review.final_decision",
            value=review.get("final_decision"),
            allowed=REVIEW_FINAL_DECISIONS,
        )
    if review.get("retention_label") not in REVIEW_RETENTION_LABELS:
        add_invalid(
            invalid,
            transition_unit_id=row.get("transition_unit_id"),
            path="quality.review.retention_label",
            value=review.get("retention_label"),
            allowed=REVIEW_RETENTION_LABELS,
        )
    return invalid


def knowledge_unit_id(transition_unit_id: str) -> str:
    return f"ku_cpsycound_interaction_{transition_unit_id}"


def build_retrieval_text(row: dict[str, Any]) -> str:
    pre_state = row.get("pre_state") or {}
    action = row.get("therapist_action") or {}
    response = row.get("client_response") or {}
    transition = row.get("transition") or {}

    state_labels = [
        str(mention.get("state_label"))
        for mention in pre_state.get("state_mentions") or []
        if isinstance(mention, dict) and mention.get("state_label")
    ]
    move_surfaces = [
        str(move.get("surface_action"))
        for move in action.get("moves") or []
        if isinstance(move, dict) and move.get("surface_action")
    ]
    parts = [
        f"client_pre_state: {'；'.join(state_labels)}",
        f"risk: {pre_state.get('risk_level')} / {', '.join(pre_state.get('risk_domains') or [])}",
        f"therapist_moves: {'；'.join(move_surfaces)}",
        f"client_response: {response.get('response_type')}；{response.get('notes') or ''}",
        f"transition: {transition.get('transition_type')}；{transition.get('summary') or ''}",
    ]
    return "\n".join(part for part in parts if part.strip())


def project_row(row: dict[str, Any], invalid_labels: list[dict[str, Any]]) -> dict[str, Any]:
    source = row.get("source_metadata") or {}
    review = row.get("review") or {}
    action = row.get("therapist_action") or {}
    transition_unit_id = str(row["transition_unit_id"])

    return {
        "schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "unit_id": knowledge_unit_id(transition_unit_id),
        "knowledge_type": KNOWLEDGE_TYPE,
        "source": {
            "dataset": source.get("dataset") or DATASET,
            "source_id": source.get("source_id"),
            "case_id": row.get("case_id"),
            "source_window_id": row.get("source_window_id"),
            "transition_unit_id": transition_unit_id,
            "language": "zh",
            "source_construction_type": source.get("source_construction_type"),
            "evidence_tier": source.get("evidence_tier"),
            "center_counselor_turn_id": source.get("center_counselor_turn_id"),
            "lexical_risk_hint": source.get("lexical_risk_hint"),
        },
        "content": {
            "pre_state": row.get("pre_state"),
            "therapist_action": row.get("therapist_action"),
            "client_response": row.get("client_response"),
            "transition": row.get("transition"),
            "interaction_summary": (row.get("transition") or {}).get("summary"),
        },
        "quality": {
            "review": review,
            "review_decision": review.get("final_decision"),
            "retention_label": review.get("retention_label"),
            "clinical_qualities": action.get("clinical_qualities") or [],
            "audit_summary": row.get("audit_summary"),
        },
        "normalization": {
            "source_registry": DEFAULT_REGISTRY,
            "controlled_label_audit": {
                "status": "pass" if not invalid_labels else "fail",
                "issue_count": len(invalid_labels),
                "issues": invalid_labels,
            },
            "final_normalization_audit": row.get("final_normalization_audit"),
        },
        "retrieval": {
            "controlled_labels": {
                "state_dimensions": (row.get("pre_state") or {}).get("state_dimensions") or [],
                "risk_domains": (row.get("pre_state") or {}).get("risk_domains") or [],
                "risk_level": (row.get("pre_state") or {}).get("risk_level"),
                "required_action": (row.get("pre_state") or {}).get("required_action"),
                "readiness": (row.get("pre_state") or {}).get("readiness"),
                "alliance_state": (row.get("pre_state") or {}).get("alliance_state"),
                "disclosure_depth": (row.get("pre_state") or {}).get("disclosure_depth"),
                "move_types": action.get("move_types") or [],
                "move_functions": action.get("move_functions") or [],
                "client_response_type": (row.get("client_response") or {}).get("response_type"),
                "transition_type": (row.get("transition") or {}).get("transition_type"),
                "confidence": (row.get("transition") or {}).get("confidence"),
                "review_decision": review.get("final_decision"),
                "retention_label": review.get("retention_label"),
            },
            "embedding_text": build_retrieval_text(row),
            "embedding_status": "not_embedded",
        },
        "provenance": {
            "source_transition_schema_version": row.get("schema_version"),
            "source_transition_unit_id": transition_unit_id,
            "source_line_no": row.get("_source_line_no"),
            "id_mapping": row.get("id_mapping"),
            "canonical_correction": row.get("canonical_correction"),
            "repair_lineage": row.get("repair_lineage"),
            "reextract_trace": row.get("reextract_trace"),
        },
    }


def counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): count for key, count in sorted(counter.items(), key=lambda item: str(item[0]))}


def collect_label_index(units: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, Any] = {
        "schema_version": "theraskill_controlled_label_index_v1",
        "dataset": DATASET,
        "knowledge_type": KNOWLEDGE_TYPE,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fields": {},
    }
    field_to_units: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for unit in units:
        unit_id = unit["unit_id"]
        labels = ((unit.get("retrieval") or {}).get("controlled_labels") or {})
        for field, value in labels.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if item is None:
                    continue
                field_to_units[field][str(item)].add(unit_id)

    for field, value_map in sorted(field_to_units.items()):
        index["fields"][field] = {
            value: {
                "count": len(unit_ids),
                "unit_ids_sample": sorted(unit_ids)[:50],
            }
            for value, unit_ids in sorted(value_map.items())
        }
    return index


def build_qa(rows: list[dict[str, Any]], units: list[dict[str, Any]], invalid_labels: list[dict[str, Any]]) -> dict[str, Any]:
    missing_fields = Counter()
    schema_counter = Counter()
    review_counter = Counter()
    retention_counter = Counter()
    source_type_counter = Counter()
    evidence_tier_counter = Counter()
    state_dimension_counter = Counter()
    risk_domain_counter = Counter()
    risk_level_counter = Counter()
    required_action_counter = Counter()
    move_type_counter = Counter()
    move_function_counter = Counter()
    response_type_counter = Counter()
    transition_type_counter = Counter()

    required_source_fields = [
        "transition_unit_id",
        "schema_version",
        "source_window_id",
        "case_id",
        "source_metadata",
        "pre_state",
        "therapist_action",
        "client_response",
        "transition",
        "review",
    ]
    required_unit_fields = [
        "schema_version",
        "unit_id",
        "knowledge_type",
        "source",
        "content",
        "quality",
        "normalization",
        "provenance",
    ]

    for row in rows:
        for field in required_source_fields:
            if field not in row or row[field] in (None, {}, []):
                missing_fields[f"source.{field}"] += 1
        schema_counter[row.get("schema_version")] += 1
        review = row.get("review") or {}
        source = row.get("source_metadata") or {}
        pre_state = row.get("pre_state") or {}
        action = row.get("therapist_action") or {}
        response = row.get("client_response") or {}
        transition = row.get("transition") or {}
        review_counter[review.get("final_decision")] += 1
        retention_counter[review.get("retention_label")] += 1
        source_type_counter[source.get("source_construction_type")] += 1
        evidence_tier_counter[source.get("evidence_tier")] += 1
        state_dimension_counter.update(pre_state.get("state_dimensions") or [])
        risk_domain_counter.update(pre_state.get("risk_domains") or [])
        risk_level_counter[pre_state.get("risk_level")] += 1
        required_action_counter[pre_state.get("required_action")] += 1
        move_type_counter.update(action.get("move_types") or [])
        move_function_counter.update(action.get("move_functions") or [])
        response_type_counter[response.get("response_type")] += 1
        transition_type_counter[transition.get("transition_type")] += 1

    for unit in units:
        for field in required_unit_fields:
            if field not in unit or unit[field] in (None, {}, []):
                missing_fields[f"unit.{field}"] += 1

    return {
        "schema_version": "theraskill_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_rows": len(rows),
        "output_rows": len(units),
        "case_count": len({row.get("case_id") for row in rows}),
        "source_window_count": len({row.get("source_window_id") for row in rows}),
        "transition_unit_count": len({row.get("transition_unit_id") for row in rows}),
        "knowledge_unit_count": len({unit.get("unit_id") for unit in units}),
        "schema_distribution": counter_dict(schema_counter),
        "review_distribution": counter_dict(review_counter),
        "retention_distribution": counter_dict(retention_counter),
        "source_construction_type_distribution": counter_dict(source_type_counter),
        "evidence_tier_distribution": counter_dict(evidence_tier_counter),
        "controlled_label_distribution": {
            "state_dimensions": counter_dict(state_dimension_counter),
            "risk_domains": counter_dict(risk_domain_counter),
            "risk_level": counter_dict(risk_level_counter),
            "required_action": counter_dict(required_action_counter),
            "move_types": counter_dict(move_type_counter),
            "move_functions": counter_dict(move_function_counter),
            "client_response_type": counter_dict(response_type_counter),
            "transition_type": counter_dict(transition_type_counter),
        },
        "missing_fields": counter_dict(missing_fields),
        "invalid_controlled_label_count": len(invalid_labels),
        "invalid_controlled_labels_sample": invalid_labels[:100],
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)
    index_path = Path(args.index)
    registry_path = Path(args.registry)

    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    options = registry_options(registry)
    rows = read_jsonl(input_path)

    all_invalid_labels: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    for row in rows:
        row_invalid_labels = audit_controlled_labels(row, options)
        all_invalid_labels.extend(row_invalid_labels)
        units.append(project_row(row, row_invalid_labels))

    input_sha = file_sha256(input_path)
    output_sha = write_jsonl(output_path, units, overwrite=args.overwrite)

    qa = build_qa(rows, units, all_invalid_labels)
    qa["input_sha256"] = input_sha
    qa["output_sha256"] = output_sha
    write_json(qa_path, qa)

    index = collect_label_index(units)
    index["input_knowledge_units"] = str(output_path)
    index["input_knowledge_units_sha256"] = output_sha
    write_json(index_path, index)

    manifest = {
        "schema_version": "theraskill_knowledge_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "knowledge_type": KNOWLEDGE_TYPE,
        "projection_policy": "deterministic envelope projection; source transition clinical content preserved; no LLM call; no semantic rewrite",
        "input": str(input_path),
        "input_sha256": input_sha,
        "output": str(output_path),
        "output_sha256": output_sha,
        "qa_report": str(qa_path),
        "controlled_label_index": str(index_path),
        "registry": str(registry_path),
        "counts": {
            "input_rows": len(rows),
            "output_rows": len(units),
            "case_count": qa["case_count"],
            "invalid_controlled_label_count": len(all_invalid_labels),
        },
        "source_snapshot": {
            "source_construction_type_distribution": qa["source_construction_type_distribution"],
            "evidence_tier_distribution": qa["evidence_tier_distribution"],
            "review_distribution": qa["review_distribution"],
            "retention_distribution": qa["retention_distribution"],
        },
        "notes": [
            "Original outputs/current/transition_units.jsonl is retained as the legacy final source snapshot.",
            "retrieval.embedding_text is prepared as a deterministic text view; embedding_status remains not_embedded.",
            "No supports_skill_types or do_not_use_for routing fields are added at this knowledge-unit stage.",
        ],
    }
    write_json(manifest_path, manifest)

    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    print(f"output_sha256: {output_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
