#!/usr/bin/env python3
"""Build a full-coverage method/rubric registry under dedup/.

This is a registry/index step, not a merge step. The method_rubric family has
no multi-member candidate components in the current candidate graph, so every
unit is preserved as an individual registry entry for later LLM/manual synonym
or dimension review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, read_json, write_json, write_jsonl  # noqa: E402


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_COMPONENT_REPORT = "outputs/knowledge/dedup/reports/full_candidate_components_report.json"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/registries/method_rubric_registry"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/method_rubric_registry_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/method_rubric_registry_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/method_rubric_registry_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--component-report", default=DEFAULT_COMPONENT_REPORT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(unit_id: str) -> str:
    return "method_reg_" + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:20]


def registry_kind(row: dict[str, Any]) -> str:
    knowledge_type = str(row.get("knowledge_type") or "")
    if "rubric" in knowledge_type or "lexicon" in knowledge_type:
        return "quality_or_label_rubric"
    if knowledge_type in {"mi_method_step", "reframing_step"}:
        return "method_step"
    if knowledge_type in {"thinking_trap_unit", "change_talk_pattern"}:
        return "method_label_or_pattern"
    return "method_or_rubric_unit"


def iter_method_rows(text_view: str | Path):
    with Path(text_view).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("dedup_family") == "method_rubric":
                yield line_no, row


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    registry_path = output_dir / "method_rubric.registry.jsonl"
    summary_path = output_dir / "method_rubric.registry_summary.json"
    all_paths = [registry_path, summary_path, Path(args.report), Path(args.manifest), Path(args.qa_report)]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    component_report = read_json(args.component_report)
    method_component_stats = ((component_report.get("outputs") or {}).get("method_rubric") or {})
    expected_covered_units = int(method_component_stats.get("covered_unit_count") or 0)
    expected_multi_member_components = int(method_component_stats.get("multi_member_component_count") or 0)
    expected_singletons = int(method_component_stats.get("singleton_unit_count") or 0)

    rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    dataset_counters: Counter[str] = Counter()
    knowledge_type_counters: Counter[str] = Counter()
    evidence_tier_counters: Counter[str] = Counter()
    source_type_counters: Counter[str] = Counter()
    language_counters: Counter[str] = Counter()
    license_counters: Counter[str] = Counter()
    kind_counters: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []
    seen_unit_ids: set[str] = set()
    seen_registry_ids: set[str] = set()

    for line_no, row in iter_method_rows(args.text_view):
        unit_id = str(row.get("unit_id") or "")
        if not unit_id:
            issues.append({"issue": "missing_unit_id", "line_no": line_no})
            continue
        if unit_id in seen_unit_ids:
            issues.append({"issue": "duplicate_unit_id", "unit_id": unit_id, "line_no": line_no})
        seen_unit_ids.add(unit_id)
        reg_id = stable_id(unit_id)
        if reg_id in seen_registry_ids:
            issues.append({"issue": "duplicate_registry_id", "registry_id": reg_id, "unit_id": unit_id})
        seen_registry_ids.add(reg_id)

        kind = registry_kind(row)
        dataset = str(row.get("dataset") or "unknown")
        knowledge_type = str(row.get("knowledge_type") or "unknown")
        record = {
            "schema_version": "theraskill_method_rubric_registry_v1",
            "registry_id": reg_id,
            "registry_status": "singleton_method_rubric_not_merged",
            "dedup_family": "method_rubric",
            "registry_kind": kind,
            "unit_id": unit_id,
            "dataset": dataset,
            "knowledge_type": knowledge_type,
            "language": row.get("language"),
            "evidence_tier": row.get("evidence_tier"),
            "source_construction_type": row.get("source_construction_type"),
            "license_constraints": row.get("license_constraints") or [],
            "risk_or_safety_flags": row.get("risk_or_safety_flags") or [],
            "dedup_text_sha256": row.get("dedup_text_sha256"),
            "dedup_text_preview": str(row.get("dedup_text") or "")[:1000],
            "dedup_key_fields": row.get("dedup_key_fields") or {},
            "raw_file": row.get("raw_file"),
            "raw_line_no": row.get("raw_line_no"),
            "candidate_graph_status": {
                "multi_member_component_id": None,
                "component_member_count": 1,
                "singleton_in_full_candidate_graph": True,
            },
            "next_review_gate": "optional_llm_or_manual_synonym_and_dimension_review",
            "not_final_merged_unit": True,
            "raw_units_unchanged": True,
            "final_merge_output": False,
            "formal_merge_candidate": False,
        }
        rows.append(record)
        counters["registry_rows"] += 1
        dataset_counters[dataset] += 1
        knowledge_type_counters[knowledge_type] += 1
        evidence_tier_counters[str(row.get("evidence_tier") or "unknown")] += 1
        source_type_counters[str(row.get("source_construction_type") or "unknown")] += 1
        language_counters[str(row.get("language") or "unknown")] += 1
        kind_counters[kind] += 1
        licenses = row.get("license_constraints") or []
        if licenses:
            for item in licenses:
                license_counters[str(item)] += 1
        else:
            license_counters["__empty_list__"] += 1

    if expected_covered_units and len(rows) != expected_covered_units:
        issues.append(
            {
                "issue": "text_view_count_does_not_match_component_report",
                "registry_rows": len(rows),
                "component_report_covered_unit_count": expected_covered_units,
            }
        )
    if expected_multi_member_components != 0:
        issues.append(
            {
                "issue": "method_rubric_has_unexpected_multi_member_components",
                "multi_member_component_count": expected_multi_member_components,
            }
        )
    if expected_singletons and len(rows) != expected_singletons:
        issues.append(
            {
                "issue": "registry_rows_do_not_match_singleton_unit_count",
                "registry_rows": len(rows),
                "singleton_unit_count": expected_singletons,
            }
        )

    write_jsonl(registry_path, rows, sort_keys=True)
    status = "complete" if not issues else "incomplete"
    summary = {
        "schema_version": "theraskill_method_rubric_registry_summary_v1",
        "created_at": utc_now(),
        "status": status,
        "registry_rows": len(rows),
        "dataset_distribution": dict(dataset_counters.most_common()),
        "knowledge_type_distribution": dict(knowledge_type_counters.most_common()),
        "registry_kind_distribution": dict(kind_counters.most_common()),
        "evidence_tier_distribution": dict(evidence_tier_counters.most_common()),
        "source_construction_type_distribution": dict(source_type_counters.most_common()),
        "language_distribution": dict(language_counters.most_common()),
        "license_constraints_distribution": dict(license_counters.most_common()),
        "candidate_graph": {
            "covered_unit_count": expected_covered_units,
            "multi_member_component_count": expected_multi_member_components,
            "singleton_unit_count": expected_singletons,
            "source_report": args.component_report,
            "source_report_sha256": file_sha256(args.component_report),
        },
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(summary_path, summary)

    report = {
        "schema_version": "theraskill_method_rubric_registry_report_v1",
        "created_at": utc_now(),
        "status": status,
        "inputs": {
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view)},
            "component_report": {"path": args.component_report, "sha256": file_sha256(args.component_report)},
        },
        "outputs": {
            "registry": {"path": str(registry_path), "rows": len(rows), "sha256": file_sha256(registry_path)},
            "summary": {"path": str(summary_path), "sha256": file_sha256(summary_path)},
        },
        "counts": {
            **dict(counters),
            "unique_unit_ids": len(seen_unit_ids),
            "unique_registry_ids": len(seen_registry_ids),
            "component_report_covered_unit_count": expected_covered_units,
            "component_report_multi_member_components": expected_multi_member_components,
            "component_report_singleton_units": expected_singletons,
        },
        "dataset_distribution": dict(dataset_counters.most_common()),
        "knowledge_type_distribution": dict(knowledge_type_counters.most_common()),
        "registry_kind_distribution": dict(kind_counters.most_common()),
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(args.report, report)
    write_json(args.manifest, report)
    write_json(args.qa_report, report)
    print(json.dumps({"status": status, "issues_count": len(issues), "counts": report["counts"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
