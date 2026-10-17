#!/usr/bin/env python3
"""Refine oversized candidate components with deterministic structural keys.

This step is a pre-adjudication graph quality pass. It does not merge content,
does not call an LLM, and does not promote anything to a final release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, write_json  # noqa: E402

DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_COMPONENT_DIR = "outputs/knowledge/dedup/graphs/full_candidate_components"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/graphs/oversized_component_refinement"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/oversized_component_refinement_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/oversized_component_refinement_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/oversized_component_refinement_qa.json"

DEFAULT_THRESHOLDS = {
    "style_response": 200,
    "therapy_process": 200,
    "qa_psychoeducation": 200,
    "risk_assessment": 100,
    "synthetic_construction": 50,
    "method_rubric": 50,
}

FAMILY_ALIASES = {"risk_assessment": "clinical_risk_assessment"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--component-dir", default=DEFAULT_COMPONENT_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def counter_dict(counter: Counter, limit: int | None = None) -> dict[str, int]:
    return {str(k): int(v) for k, v in counter.most_common(limit)}


def display_family(family: str) -> str:
    alias = FAMILY_ALIASES.get(family)
    return f"{family} (alias: {alias})" if alias else family


def stable_hash(payload: Any, length: int = 20) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:length]


def parse_json_prefix(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text.startswith("{"):
        return {}
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def length_bucket(text: str) -> str:
    n = len(text or "")
    if n == 0:
        return "empty"
    if n < 80:
        return "very_short"
    if n < 220:
        return "short"
    if n < 700:
        return "medium"
    if n < 1600:
        return "long"
    return "very_long"


def normalize_label(value: Any, default: str = "unknown") -> str:
    if value in (None, "", [], {}):
        return default
    if isinstance(value, list):
        return "+".join(sorted(str(item) for item in value)) or default
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)[:200]
    return str(value).strip()[:200] or default


def regex_string(text: str, key: str, default: str = "unknown") -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', text or "")
    return match.group(1) if match else default


def regex_array(text: str, key: str) -> list[str]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[(.*?)\]', text or "", flags=re.S)
    if not match:
        return []
    return re.findall(r'"([^"]*)"', match.group(1))


def load_text_rows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    stats = {"rows": 0, "parse_errors": 0, "duplicate_unit_id": 0}
    issues: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:
                stats["parse_errors"] += 1
                issues.append({"issue": "text_view_parse_error", "line": line_no, "error": str(exc)})
                continue
            unit_id = row.get("unit_id")
            if not unit_id:
                continue
            if unit_id in rows:
                stats["duplicate_unit_id"] += 1
                issues.append({"issue": "duplicate_unit_id", "line": line_no, "unit_id": unit_id})
                continue
            rows[unit_id] = row
    return rows, stats, issues


def style_key(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    text = row.get("dedup_text") or ""
    parsed = parse_json_prefix(text)
    annotations = parsed.get("empathy_annotations") if isinstance(parsed, dict) else None
    annotation_levels: list[str] = []
    annotation_span_count = 0
    if isinstance(annotations, dict):
        for dim, payload in sorted(annotations.items()):
            if isinstance(payload, dict):
                annotation_levels.append(f"{dim}:{payload.get('source_level', 'unknown')}")
                annotation_span_count += int(payload.get("rationale_span_count") or 0)
    context = fields.get("context") or ""
    response = fields.get("response") or ""
    response_function = fields.get("response_function") or ""
    empty_context_response = not str(context).strip() and not str(response).strip()
    return {
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "response_function": normalize_label(response_function),
        "length_bucket": length_bucket(text),
        "empty_context_response": empty_context_response,
        "annotation_level_signature": "+".join(annotation_levels[:20]) if annotation_levels else "none",
        "annotation_span_count_bucket": "0" if annotation_span_count == 0 else "1" if annotation_span_count == 1 else "2_3" if annotation_span_count <= 3 else "4_plus",
    }


def therapy_key(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    process_text = fields.get("process_text") or row.get("dedup_text") or ""
    chunks = [chunk for chunk in str(process_text).split("\n") if chunk.strip().startswith("{")]
    parsed_chunks = [parse_json_prefix(chunk) for chunk in chunks]
    state = parsed_chunks[0] if parsed_chunks else parse_json_prefix(str(process_text))
    moves_payload = parsed_chunks[1] if len(parsed_chunks) > 1 else {}
    response_payload = parsed_chunks[2] if len(parsed_chunks) > 2 else {}
    transition_payload = parsed_chunks[3] if len(parsed_chunks) > 3 else {}
    move_functions = sorted(set(as_list(moves_payload.get("move_functions"))))
    move_types = sorted(set(as_list(moves_payload.get("move_types"))))
    qualities = sorted(set(as_list(moves_payload.get("clinical_qualities"))))
    state_dimensions = sorted(set(as_list(state.get("state_dimensions"))))
    risk_domains = sorted(set(as_list(state.get("risk_domains"))))
    response_type = response_payload.get("response_type") or transition_payload.get("response_type")
    no_sequence = "no_sequence_found" in str(process_text)
    text_for_regex = str(process_text)
    if not state:
        state = {
            "risk_level": regex_string(text_for_regex, "risk_level"),
            "risk_domains": regex_array(text_for_regex, "risk_domains"),
            "alliance_state": regex_string(text_for_regex, "alliance_state"),
            "disclosure_depth": regex_string(text_for_regex, "disclosure_depth"),
            "readiness": regex_string(text_for_regex, "readiness"),
            "required_action": regex_string(text_for_regex, "required_action"),
            "state_dimensions": regex_array(text_for_regex, "state_dimensions"),
        }
    if not move_functions:
        move_functions = sorted(set(regex_array(text_for_regex, "move_functions")))
    if not move_types:
        move_types = sorted(set(regex_array(text_for_regex, "move_types")))
    if not qualities:
        qualities = sorted(set(regex_array(text_for_regex, "clinical_qualities")))
    if not response_type or response_type == "unknown":
        response_type = regex_string(text_for_regex, "response_type")
    if not state_dimensions:
        state_dimensions = sorted(set(as_list(state.get("state_dimensions"))))
    if not risk_domains:
        risk_domains = sorted(set(as_list(state.get("risk_domains"))))
    return {
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "risk_level": normalize_label(state.get("risk_level")),
        "risk_domains": "+".join(risk_domains) or "unknown",
        "alliance_state": normalize_label(state.get("alliance_state")),
        "disclosure_depth": normalize_label(state.get("disclosure_depth")),
        "readiness": normalize_label(state.get("readiness")),
        "required_action": normalize_label(state.get("required_action")),
        "move_functions": "+".join(move_functions) or "unknown",
        "move_types": "+".join(move_types) or "unknown",
        "clinical_qualities": "+".join(qualities) or "unknown",
        "response_type": normalize_label(response_type),
        "state_dimension_signature": "+".join(state_dimensions[:12]) or "unknown",
        "no_sequence_found": no_sequence,
    }


def clinical_risk_key(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    text = row.get("dedup_text") or ""
    parsed = parse_json_prefix(text)
    merged = {}
    if isinstance(parsed, dict):
        merged.update(parsed)
    if isinstance(fields, dict):
        merged.update(fields)
    label = merged.get("label") or merged.get("risk_label") or merged.get("severity") or merged.get("distortion_label")
    risk_domain = merged.get("risk_domain") or merged.get("domain") or merged.get("risk_domains")
    severity = merged.get("severity") or merged.get("severity_label") or merged.get("risk_level")
    return {
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "label": normalize_label(label),
        "risk_domain": normalize_label(risk_domain),
        "severity": normalize_label(severity),
        "source_construction_type": normalize_label(row.get("source_construction_type")),
    }


def qa_key(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    question = fields.get("question") or fields.get("input") or ""
    structure = fields.get("structure") or fields.get("answer_structure") or ""
    return {
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "question_length_bucket": length_bucket(str(question)),
        "structure": normalize_label(structure),
    }


def synthetic_key(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    text = row.get("dedup_text") or ""
    return {
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "source_construction_type": normalize_label(row.get("source_construction_type")),
        "pattern_type": normalize_label(fields.get("pattern_type") or fields.get("sequence_name") or fields.get("method_name")),
        "length_bucket": length_bucket(text),
    }


def method_key(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    return {
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "rubric_or_method": normalize_label(fields.get("rubric") or fields.get("method") or fields.get("label")),
    }


def refinement_key(row: dict[str, Any]) -> dict[str, Any]:
    family = row.get("dedup_family")
    if family == "style_response":
        return style_key(row)
    if family == "therapy_process":
        return therapy_key(row)
    if family == "risk_assessment":
        return clinical_risk_key(row)
    if family == "qa_psychoeducation":
        return qa_key(row)
    if family == "synthetic_construction":
        return synthetic_key(row)
    if family == "method_rubric":
        return method_key(row)
    return {"dataset": row.get("dataset"), "knowledge_type": row.get("knowledge_type"), "language": row.get("language")}


def load_oversized_components(component_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    components: list[dict[str, Any]] = []
    stats = {"component_files": [], "component_rows": 0, "oversized_component_count": 0}
    issues: list[dict[str, Any]] = []
    for family, threshold in DEFAULT_THRESHOLDS.items():
        path = component_dir / f"{family}.candidate_components.jsonl"
        file_stats = {"path": str(path), "threshold": threshold, "rows": 0, "oversized_rows": 0, "sha256": file_sha256(path)}
        if not path.exists():
            stats["component_files"].append(file_stats)
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                file_stats["rows"] += 1
                stats["component_rows"] += 1
                try:
                    row = json.loads(line)
                except Exception as exc:
                    issues.append({"issue": "component_parse_error", "path": str(path), "line": line_no, "error": str(exc)})
                    continue
                if int(row.get("member_count") or 0) > threshold:
                    row["_oversized_threshold"] = threshold
                    components.append(row)
                    file_stats["oversized_rows"] += 1
                    stats["oversized_component_count"] += 1
        stats["component_files"].append(file_stats)
    return components, stats, issues


def write_refinement(
    oversized_components: list[dict[str, Any]],
    text_rows: dict[str, dict[str, Any]],
    output_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    handles: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    aggregate = {
        "oversized_component_count": 0,
        "oversized_member_count": 0,
        "refined_subcomponent_count": 0,
        "refined_multi_member_subcomponent_count": 0,
        "refined_singleton_subcomponent_count": 0,
        "largest_refined_subcomponent_size": 0,
        "oversized_component_count_by_family": Counter(),
        "oversized_member_count_by_family": Counter(),
        "refined_subcomponent_count_by_family": Counter(),
        "refined_multi_member_subcomponent_count_by_family": Counter(),
        "largest_refined_subcomponent_size_by_family": Counter(),
        "residual_oversized_subcomponent_count_by_family": Counter(),
    }
    largest_subcomponents: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    try:
        for component in oversized_components:
            family = component.get("dedup_family")
            threshold = int(component.get("_oversized_threshold") or DEFAULT_THRESHOLDS.get(str(family), 100))
            if family not in handles:
                path = output_dir / f"{family}.oversized_refinement.jsonl"
                handles[family] = path.open("w", encoding="utf-8")
            member_ids = [str(unit_id) for unit_id in component.get("member_unit_ids") or []]
            groups: dict[str, dict[str, Any]] = {}
            for unit_id in member_ids:
                row = text_rows.get(unit_id)
                if not row:
                    issues.append({"issue": "missing_text_view_for_component_member", "component_id": component.get("component_id"), "unit_id": unit_id})
                    continue
                key = refinement_key(row)
                key_hash = stable_hash(key, 16)
                if key_hash not in groups:
                    groups[key_hash] = {"refinement_key": key, "member_unit_ids": []}
                groups[key_hash]["member_unit_ids"].append(unit_id)

            subcomponents = []
            for key_hash, payload in groups.items():
                members = sorted(payload["member_unit_ids"])
                sub_id = f"refined_{family}_{stable_hash([component.get('component_id'), key_hash], 20)}"
                sub_record = {
                    "subcomponent_id": sub_id,
                    "parent_component_id": component.get("component_id"),
                    "dedup_family": family,
                    "display_family": display_family(str(family)),
                    "member_count": len(members),
                    "member_unit_ids": members,
                    "representative_unit_id": members[0] if members else None,
                    "refinement_key": payload["refinement_key"],
                    "refinement_key_sha256": hashlib.sha256(json.dumps(payload["refinement_key"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                    "still_oversized_after_refinement": len(members) > threshold,
                }
                subcomponents.append(sub_record)
            subcomponents.sort(key=lambda item: (-item["member_count"], item["subcomponent_id"]))

            residual_count = sum(1 for item in subcomponents if item["still_oversized_after_refinement"])
            record = {
                "schema_version": "theraskill_oversized_component_refinement_v1",
                "parent_component_id": component.get("component_id"),
                "dedup_family": family,
                "display_family": display_family(str(family)),
                "oversized_threshold": threshold,
                "parent_member_count": component.get("member_count"),
                "parent_edge_count": component.get("edge_count"),
                "parent_dataset_distribution": component.get("dataset_distribution"),
                "parent_knowledge_type_distribution": component.get("knowledge_type_distribution"),
                "refinement_method": "deterministic_structural_key",
                "refinement_subcomponent_count": len(subcomponents),
                "refined_multi_member_subcomponent_count": sum(1 for item in subcomponents if item["member_count"] > 1),
                "largest_refined_subcomponent_size": max((item["member_count"] for item in subcomponents), default=0),
                "residual_oversized_subcomponent_count": residual_count,
                "subcomponents": subcomponents,
                "not_final_merged_unit": True,
                "raw_units_unchanged": True,
            }
            handles[family].write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

            parent_count = int(component.get("member_count") or 0)
            aggregate["oversized_component_count"] += 1
            aggregate["oversized_member_count"] += parent_count
            aggregate["refined_subcomponent_count"] += len(subcomponents)
            aggregate["refined_multi_member_subcomponent_count"] += record["refined_multi_member_subcomponent_count"]
            aggregate["refined_singleton_subcomponent_count"] += sum(1 for item in subcomponents if item["member_count"] == 1)
            aggregate["largest_refined_subcomponent_size"] = max(aggregate["largest_refined_subcomponent_size"], record["largest_refined_subcomponent_size"])
            aggregate["oversized_component_count_by_family"][str(family)] += 1
            aggregate["oversized_member_count_by_family"][str(family)] += parent_count
            aggregate["refined_subcomponent_count_by_family"][str(family)] += len(subcomponents)
            aggregate["refined_multi_member_subcomponent_count_by_family"][str(family)] += record["refined_multi_member_subcomponent_count"]
            aggregate["largest_refined_subcomponent_size_by_family"][str(family)] = max(
                aggregate["largest_refined_subcomponent_size_by_family"][str(family)],
                record["largest_refined_subcomponent_size"],
            )
            aggregate["residual_oversized_subcomponent_count_by_family"][str(family)] += residual_count
            for item in subcomponents[:5]:
                largest_subcomponents.append({
                    "subcomponent_id": item["subcomponent_id"],
                    "parent_component_id": item["parent_component_id"],
                    "dedup_family": family,
                    "member_count": item["member_count"],
                    "still_oversized_after_refinement": item["still_oversized_after_refinement"],
                    "refinement_key": item["refinement_key"],
                })
    finally:
        for handle in handles.values():
            handle.close()

    for family in sorted(handles):
        path = output_dir / f"{family}.oversized_refinement.jsonl"
        outputs[family] = {"path": str(path), "sha256": file_sha256(path)}

    aggregate_payload = {
        key: counter_dict(value) if isinstance(value, Counter) else value
        for key, value in aggregate.items()
    }
    largest_subcomponents.sort(key=lambda item: item["member_count"], reverse=True)
    return {"outputs": outputs, "aggregate": aggregate_payload}, largest_subcomponents[:100], issues


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.glob("*.oversized_refinement.jsonl")) and not args.overwrite:
        raise FileExistsError(f"output dir has refinement files: {output_dir}; pass --overwrite")
    if args.overwrite and output_dir.exists():
        for path in output_dir.glob("*.oversized_refinement.jsonl"):
            path.unlink()

    text_rows, text_stats, issues = load_text_rows(Path(args.text_view))
    oversized, component_stats, component_issues = load_oversized_components(Path(args.component_dir))
    issues.extend(component_issues)
    refinement_payload, largest_subcomponents, refinement_issues = write_refinement(oversized, text_rows, output_dir)
    issues.extend(refinement_issues)
    status = "complete" if not issues else "incomplete"

    report = {
        "schema_version": "theraskill_oversized_component_refinement_report_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": {
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view)},
            "component_dir": args.component_dir,
        },
        "thresholds": DEFAULT_THRESHOLDS,
        "text_view_stats": text_stats,
        "component_scan_stats": component_stats,
        **refinement_payload,
        "largest_refined_subcomponents_top100": largest_subcomponents,
        "interpretation": {
            "not_a_merge": True,
            "purpose": "Identify deterministic structural subgroups inside oversized candidate graph components before LLM or human adjudication.",
            "next_gate": "Residual oversized subcomponents require stronger refinement or explicit deferred status before final merge release.",
        },
        "terminology": {
            "risk_assessment": "legacy dedup_family name for clinical risk assessment / symptom severity knowledge; not a data-quality issue",
            "quality_consistency_issue": "use for schema, label, or data-quality problems",
        },
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    manifest = {
        "schema_version": "theraskill_oversized_component_refinement_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": report["inputs"],
        "thresholds": DEFAULT_THRESHOLDS,
        "output_dir": str(output_dir),
        "outputs": refinement_payload["outputs"],
        "aggregate": refinement_payload["aggregate"],
        "report": args.report,
        "qa_report": args.qa_report,
        "issues_count": len(issues),
        "not_final_merge_release": True,
    }
    qa = {
        "schema_version": "theraskill_oversized_component_refinement_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "thresholds": DEFAULT_THRESHOLDS,
        "aggregate": refinement_payload["aggregate"],
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    write_json(args.report, report)
    write_json(args.manifest, manifest)
    write_json(args.qa_report, qa)
    print(json.dumps({
        "status": status,
        "issues_count": len(issues),
        "aggregate": refinement_payload["aggregate"],
        "report": args.report,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
