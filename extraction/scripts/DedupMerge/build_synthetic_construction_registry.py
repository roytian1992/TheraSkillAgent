#!/usr/bin/env python3
"""Build a full synthetic-construction registry under dedup/.

This registry preserves construction-method provenance. Multi-member exact
components are recorded as registry clusters, while all remaining singleton
units are retained as singleton registry rows. This is not a merge release.
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

from io_utils import file_sha256, iter_jsonl, read_json, write_json, write_jsonl  # noqa: E402


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_COMPONENTS = "outputs/knowledge/dedup/graphs/full_candidate_components/synthetic_construction.candidate_components.jsonl"
DEFAULT_COMPONENT_REPORT = "outputs/knowledge/dedup/reports/full_candidate_components_report.json"
DEFAULT_OVERSIZED_REFINEMENT = "outputs/knowledge/dedup/graphs/oversized_component_refinement/synthetic_construction.oversized_refinement.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/registries/synthetic_construction_registry"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/synthetic_construction_registry_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/synthetic_construction_registry_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/synthetic_construction_registry_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--components", default=DEFAULT_COMPONENTS)
    parser.add_argument("--component-report", default=DEFAULT_COMPONENT_REPORT)
    parser.add_argument("--oversized-refinement", default=DEFAULT_OVERSIZED_REFINEMENT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_cluster_id(source_id: str, member_unit_ids: list[str]) -> str:
    raw = source_id + "::" + "::".join(sorted(member_unit_ids))
    return "synth_reg_cluster_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def stable_singleton_id(unit_id: str) -> str:
    return "synth_reg_singleton_" + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:20]


def add_distribution(counter: Counter[str], value: Any) -> None:
    if isinstance(value, dict):
        for key, count in value.items():
            counter[str(key)] += int(count or 0)
    elif isinstance(value, list):
        if not value:
            counter["__empty_list__"] += 1
        for item in value:
            counter[str(item)] += 1
    elif value is None or value == "":
        counter["__missing__"] += 1
    else:
        counter[str(value)] += 1


def load_synthetic_text_view(path: str | Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for _, row in iter_jsonl(path, with_line_numbers=True):
        if row.get("dedup_family") != "synthetic_construction":
            continue
        unit_id = str(row.get("unit_id") or "")
        if unit_id:
            rows[unit_id] = row
    return rows


def load_residual_oversized_component_ids(path: str | Path) -> set[str]:
    if not Path(path).exists():
        return set()
    ids: set[str] = set()
    for _, row in iter_jsonl(path, with_line_numbers=True):
        if int(row.get("residual_oversized_subcomponent_count") or 0) > 0:
            parent = row.get("parent_component_id")
            if parent:
                ids.add(str(parent))
    return ids


def component_record(row: dict[str, Any], residual_oversized_ids: set[str]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    component_id = str(row.get("component_id") or "")
    return {
        "schema_version": "theraskill_synthetic_construction_registry_v1",
        "registry_id": stable_cluster_id(component_id, member_unit_ids),
        "registry_status": "construction_component_registry_not_final_merge",
        "dedup_family": "synthetic_construction",
        "registry_source_stage": "full_candidate_component_exact_or_candidate_cluster",
        "source_component_id": component_id,
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "representative_unit_id": row.get("representative_unit_id") or (sorted(member_unit_ids)[0] if member_unit_ids else None),
        "dataset_distribution": row.get("dataset_distribution") or {},
        "knowledge_type_distribution": row.get("knowledge_type_distribution") or {},
        "evidence_tier_distribution": row.get("evidence_tier_distribution") or {},
        "source_construction_type_distribution": row.get("source_construction_type_distribution") or {},
        "language_distribution": row.get("language_distribution") or {},
        "edge_type_distribution": row.get("edge_type_distribution") or {},
        "edge_count": row.get("edge_count"),
        "max_edge_cosine_similarity": row.get("max_edge_cosine_similarity"),
        "min_edge_cosine_similarity": row.get("min_edge_cosine_similarity"),
        "residual_oversized_after_refinement": component_id in residual_oversized_ids,
        "before_final_merge_requires": [
            "source_provenance_preservation_required",
            "construction_method_stage_review_required",
        ],
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }


def singleton_record(unit_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_synthetic_construction_registry_v1",
        "registry_id": stable_singleton_id(unit_id),
        "registry_status": "construction_singleton_registry_not_final_merge",
        "dedup_family": "synthetic_construction",
        "registry_source_stage": "full_candidate_graph_singleton",
        "source_component_id": None,
        "member_unit_ids": [unit_id],
        "member_count": 1,
        "representative_unit_id": unit_id,
        "dataset_distribution": {str(row.get("dataset") or "unknown"): 1},
        "knowledge_type_distribution": {str(row.get("knowledge_type") or "unknown"): 1},
        "evidence_tier_distribution": {str(row.get("evidence_tier") or "unknown"): 1},
        "source_construction_type_distribution": {str(row.get("source_construction_type") or "unknown"): 1},
        "language_distribution": {str(row.get("language") or "unknown"): 1},
        "license_constraints": row.get("license_constraints") or [],
        "dedup_text_sha256": row.get("dedup_text_sha256"),
        "dedup_text_preview": str(row.get("dedup_text") or "")[:1000],
        "dedup_key_fields": row.get("dedup_key_fields") or {},
        "raw_file": row.get("raw_file"),
        "raw_line_no": row.get("raw_line_no"),
        "before_final_merge_requires": ["source_provenance_preservation_required"],
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    registry_path = output_dir / "synthetic_construction.registry.jsonl"
    summary_path = output_dir / "synthetic_construction.registry_summary.json"
    all_paths = [registry_path, summary_path, Path(args.report), Path(args.manifest), Path(args.qa_report)]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    text_rows = load_synthetic_text_view(args.text_view)
    component_report = read_json(args.component_report)
    synthetic_stats = ((component_report.get("outputs") or {}).get("synthetic_construction") or {})
    expected_covered_units = int(synthetic_stats.get("covered_unit_count") or 0)
    expected_multi_member_components = int(synthetic_stats.get("multi_member_component_count") or 0)
    expected_multi_member_units = int(synthetic_stats.get("multi_member_unit_count") or 0)
    expected_singleton_units = int(synthetic_stats.get("singleton_unit_count") or 0)
    residual_oversized_ids = load_residual_oversized_component_ids(args.oversized_refinement)

    registry_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    component_member_ids: set[str] = set()
    component_rows = 0
    component_member_count = 0
    residual_component_rows = 0

    for _, row in iter_jsonl(args.components, with_line_numbers=True):
        component_rows += 1
        record = component_record(row, residual_oversized_ids)
        registry_rows.append(record)
        member_ids = set(record["member_unit_ids"])
        if len(member_ids) != record["member_count"]:
            issues.append({"issue": "duplicate_member_inside_component", "component_id": row.get("component_id")})
        overlap = component_member_ids & member_ids
        if overlap:
            issues.append({"issue": "member_in_multiple_components", "count": len(overlap), "examples": sorted(overlap)[:20]})
        component_member_ids.update(member_ids)
        component_member_count += record["member_count"]
        if record["residual_oversized_after_refinement"]:
            residual_component_rows += 1

    singleton_ids = sorted(set(text_rows) - component_member_ids)
    for unit_id in singleton_ids:
        registry_rows.append(singleton_record(unit_id, text_rows[unit_id]))

    all_member_assignments: Counter[str] = Counter()
    registry_ids: set[str] = set()
    dataset_counter: Counter[str] = Counter()
    knowledge_type_counter: Counter[str] = Counter()
    evidence_tier_counter: Counter[str] = Counter()
    source_type_counter: Counter[str] = Counter()
    language_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    stage_counter: Counter[str] = Counter()
    license_counter: Counter[str] = Counter()
    for row in registry_rows:
        registry_id = row["registry_id"]
        if registry_id in registry_ids:
            issues.append({"issue": "duplicate_registry_id", "registry_id": registry_id})
        registry_ids.add(registry_id)
        if row.get("final_merge_output") is not False or row.get("formal_merge_candidate") is not False:
            issues.append({"issue": "registry_row_has_merge_output_flag", "registry_id": registry_id})
        status_counter[str(row.get("registry_status") or "unknown")] += 1
        stage_counter[str(row.get("registry_source_stage") or "unknown")] += 1
        for unit_id in row.get("member_unit_ids") or []:
            all_member_assignments[str(unit_id)] += 1
        add_distribution(dataset_counter, row.get("dataset_distribution") or {})
        add_distribution(knowledge_type_counter, row.get("knowledge_type_distribution") or {})
        add_distribution(evidence_tier_counter, row.get("evidence_tier_distribution") or {})
        add_distribution(source_type_counter, row.get("source_construction_type_distribution") or {})
        add_distribution(language_counter, row.get("language_distribution") or {})
        for item in row.get("license_constraints") or []:
            license_counter[str(item)] += 1

    duplicate_members = [unit_id for unit_id, count in all_member_assignments.items() if count > 1]
    missing_members = sorted(set(text_rows) - set(all_member_assignments))
    extra_members = sorted(set(all_member_assignments) - set(text_rows))
    if duplicate_members:
        issues.append({"issue": "member_assigned_multiple_times", "count": len(duplicate_members), "examples": duplicate_members[:20]})
    if missing_members:
        issues.append({"issue": "synthetic_text_view_members_missing_from_registry", "count": len(missing_members), "examples": missing_members[:20]})
    if extra_members:
        issues.append({"issue": "registry_members_not_in_text_view", "count": len(extra_members), "examples": extra_members[:20]})
    if expected_covered_units and len(all_member_assignments) != expected_covered_units:
        issues.append(
            {
                "issue": "registry_member_count_does_not_match_component_report",
                "registry_unique_members": len(all_member_assignments),
                "component_report_covered_unit_count": expected_covered_units,
            }
        )
    if expected_multi_member_components and component_rows != expected_multi_member_components:
        issues.append(
            {
                "issue": "component_row_count_does_not_match_report",
                "component_rows": component_rows,
                "component_report_multi_member_components": expected_multi_member_components,
            }
        )
    if expected_multi_member_units and component_member_count != expected_multi_member_units:
        issues.append(
            {
                "issue": "component_member_count_does_not_match_report",
                "component_member_count": component_member_count,
                "component_report_multi_member_units": expected_multi_member_units,
            }
        )
    if expected_singleton_units and len(singleton_ids) != expected_singleton_units:
        issues.append(
            {
                "issue": "singleton_count_does_not_match_report",
                "singleton_rows": len(singleton_ids),
                "component_report_singleton_units": expected_singleton_units,
            }
        )

    write_jsonl(registry_path, registry_rows, sort_keys=True)
    status = "complete" if not issues else "incomplete"
    summary = {
        "schema_version": "theraskill_synthetic_construction_registry_summary_v1",
        "created_at": utc_now(),
        "status": status,
        "registry_rows": len(registry_rows),
        "registry_member_count": sum(int(row.get("member_count") or 0) for row in registry_rows),
        "unique_member_count": len(all_member_assignments),
        "component_registry_rows": component_rows,
        "component_registry_members": component_member_count,
        "singleton_registry_rows": len(singleton_ids),
        "residual_oversized_component_rows": residual_component_rows,
        "dataset_distribution": dict(dataset_counter.most_common()),
        "knowledge_type_distribution": dict(knowledge_type_counter.most_common()),
        "registry_status_distribution": dict(status_counter.most_common()),
        "registry_source_stage_distribution": dict(stage_counter.most_common()),
        "evidence_tier_distribution": dict(evidence_tier_counter.most_common()),
        "source_construction_type_distribution": dict(source_type_counter.most_common()),
        "language_distribution": dict(language_counter.most_common()),
        "license_constraints_distribution": dict(license_counter.most_common()),
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(summary_path, summary)

    report = {
        "schema_version": "theraskill_synthetic_construction_registry_report_v1",
        "created_at": utc_now(),
        "status": status,
        "inputs": {
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view)},
            "components": {"path": args.components, "sha256": file_sha256(args.components)},
            "component_report": {"path": args.component_report, "sha256": file_sha256(args.component_report)},
            "oversized_refinement": {"path": args.oversized_refinement, "sha256": file_sha256(args.oversized_refinement)},
        },
        "outputs": {
            "registry": {"path": str(registry_path), "rows": len(registry_rows), "members": summary["registry_member_count"], "sha256": file_sha256(registry_path)},
            "summary": {"path": str(summary_path), "sha256": file_sha256(summary_path)},
        },
        "counts": {
            "text_view_synthetic_units": len(text_rows),
            "component_registry_rows": component_rows,
            "component_registry_members": component_member_count,
            "singleton_registry_rows": len(singleton_ids),
            "registry_rows": len(registry_rows),
            "registry_member_count": summary["registry_member_count"],
            "unique_member_count": len(all_member_assignments),
            "residual_oversized_component_rows": residual_component_rows,
            "component_report_covered_unit_count": expected_covered_units,
            "component_report_multi_member_components": expected_multi_member_components,
            "component_report_multi_member_units": expected_multi_member_units,
            "component_report_singleton_units": expected_singleton_units,
        },
        "dataset_distribution": dict(dataset_counter.most_common()),
        "knowledge_type_distribution": dict(knowledge_type_counter.most_common()),
        "registry_status_distribution": dict(status_counter.most_common()),
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
