#!/usr/bin/env python3
"""Build Phase 5 reviewed-cluster registries for clinical/process families."""

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

from io_utils import file_sha256, iter_jsonl, write_json, write_jsonl  # noqa: E402


FAMILY_CONFIG = {
    "clinical_risk_assessment": {
        "first_pass": "outputs/knowledge/dedup/clusters/clinical_risk_assessment_queue_review/clinical_risk_assessment.review_clusters.jsonl",
        "second_pass": "outputs/knowledge/dedup/clusters/clinical_risk_assessment_split_subreview/clinical_risk_assessment.subreview_clusters.jsonl",
        "first_deferred": "outputs/knowledge/dedup/clusters/clinical_risk_assessment_queue_review/clinical_risk_assessment.deferred_by_llm.jsonl",
        "split_singletons": "outputs/knowledge/dedup/clusters/clinical_risk_assessment_split_subreview/clinical_risk_assessment.split_singletons.jsonl",
        "split_deferred": "outputs/knowledge/dedup/clusters/clinical_risk_assessment_split_subreview/clinical_risk_assessment.split_deferred_members.jsonl",
        "queue_deferred": "outputs/knowledge/dedup/adjudication_queues/clinical_risk_assessment/clinical_risk_assessment.deferred_components.jsonl",
        "queue_singletons": "outputs/knowledge/dedup/adjudication_queues/clinical_risk_assessment/clinical_risk_assessment.singleton_components.jsonl",
        "output_dir": "outputs/knowledge/dedup/registries/clinical_risk_assessment_reviewed_clusters",
        "report": "outputs/knowledge/dedup/reports/clinical_risk_assessment_reviewed_cluster_registry_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_reviewed_cluster_registry_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_reviewed_cluster_registry_qa.json",
        "output_prefix": "clinical_risk_assessment",
        "id_prefix": "clinical_reg_",
        "registry_schema": "theraskill_clinical_risk_assessment_reviewed_cluster_registry_v1",
        "excluded_schema": "theraskill_clinical_risk_assessment_excluded_from_registry_v1",
        "summary_schema": "theraskill_clinical_risk_assessment_reviewed_cluster_registry_summary_v1",
        "report_schema": "theraskill_clinical_risk_assessment_reviewed_cluster_registry_report_v1",
    },
    "therapy_process": {
        "first_pass": "outputs/knowledge/dedup/clusters/therapy_process_queue_review/therapy_process.review_clusters.jsonl",
        "second_pass": "outputs/knowledge/dedup/clusters/therapy_process_split_subreview/therapy_process.subreview_clusters.jsonl",
        "first_deferred": "outputs/knowledge/dedup/clusters/therapy_process_queue_review/therapy_process.deferred_by_llm.jsonl",
        "split_singletons": "outputs/knowledge/dedup/clusters/therapy_process_split_subreview/therapy_process.split_singletons.jsonl",
        "split_deferred": "outputs/knowledge/dedup/clusters/therapy_process_split_subreview/therapy_process.split_deferred_members.jsonl",
        "queue_deferred": "outputs/knowledge/dedup/adjudication_queues/therapy_process/therapy_process.deferred_components.jsonl",
        "queue_singletons": "outputs/knowledge/dedup/adjudication_queues/therapy_process/therapy_process.singleton_components.jsonl",
        "output_dir": "outputs/knowledge/dedup/registries/therapy_process_reviewed_clusters",
        "report": "outputs/knowledge/dedup/reports/therapy_process_reviewed_cluster_registry_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/therapy_process_reviewed_cluster_registry_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/therapy_process_reviewed_cluster_registry_qa.json",
        "output_prefix": "therapy_process",
        "id_prefix": "therapy_reg_",
        "registry_schema": "theraskill_therapy_process_reviewed_cluster_registry_v1",
        "excluded_schema": "theraskill_therapy_process_excluded_from_registry_v1",
        "summary_schema": "theraskill_therapy_process_reviewed_cluster_registry_summary_v1",
        "report_schema": "theraskill_therapy_process_reviewed_cluster_registry_report_v1",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=sorted(FAMILY_CONFIG), required=True)
    parser.add_argument("--first-pass")
    parser.add_argument("--second-pass")
    parser.add_argument("--first-deferred")
    parser.add_argument("--split-singletons")
    parser.add_argument("--split-deferred")
    parser.add_argument("--queue-deferred")
    parser.add_argument("--queue-singletons")
    parser.add_argument("--output-dir")
    parser.add_argument("--report")
    parser.add_argument("--manifest")
    parser.add_argument("--qa-report")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
def stable_id(prefix: str, stage: str, source_id: str, member_unit_ids: list[str]) -> str:
    raw = stage + "::" + source_id + "::" + "::".join(sorted(member_unit_ids))
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def representative_unit_id(member_unit_ids: list[str]) -> str | None:
    return sorted(member_unit_ids)[0] if member_unit_ids else None


def clinical_label(row: dict[str, Any]) -> str | None:
    return row.get("canonical_label_or_construct") or row.get("subgroup_label") or (row.get("llm_decision") or {}).get("canonical_label_or_construct")


def therapy_label(row: dict[str, Any]) -> str | None:
    return row.get("canonical_process_label") or row.get("subgroup_label") or (row.get("llm_decision") or {}).get("canonical_process_label")


def shared_pattern(family: str, row: dict[str, Any]) -> str | None:
    decision = row.get("llm_decision") or {}
    if family == "clinical_risk_assessment":
        return row.get("shared_construct") or decision.get("shared_construct")
    return row.get("shared_state_move_response_pattern") or decision.get("shared_state_move_response_pattern")


def first_pass_registry_record(family: str, cfg: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    decision = row.get("llm_decision") or {}
    source_id = str(row.get("review_cluster_id") or row.get("queue_item_id"))
    return {
        "schema_version": cfg["registry_schema"],
        "registry_cluster_id": stable_id(str(cfg["id_prefix"]), "first_pass_merge_equivalent", source_id, member_unit_ids),
        "registry_status": "reviewed_cluster_not_final_merge",
        "dedup_family": family,
        "registry_source_stage": "first_pass_merge_equivalent",
        "source_cluster_id": row.get("review_cluster_id"),
        "source_queue_item_id": row.get("queue_item_id"),
        "source_component_id": row.get("source_component_id"),
        "source_item_id": row.get("source_item_id"),
        "source_kind": row.get("source_kind"),
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "representative_unit_id": representative_unit_id(member_unit_ids),
        "canonical_label": clinical_label(row) if family == "clinical_risk_assessment" else therapy_label(row),
        "shared_pattern": shared_pattern(family, row),
        "confidence": row.get("merge_confidence") or decision.get("merge_confidence"),
        "refinement_key": row.get("refinement_key"),
        "source_faithfulness_notes": row.get("source_faithfulness_notes") or decision.get("source_faithfulness_notes"),
        "provenance": {
            "source_file_role": "first_pass_review_clusters",
            "source_schema_version": row.get("schema_version"),
            "llm_decision": "merge_equivalent",
            "recommended_next_action": "build_review_cluster",
        },
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }


def second_pass_registry_record(family: str, cfg: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    source_id = str(row.get("subreview_cluster_id"))
    return {
        "schema_version": cfg["registry_schema"],
        "registry_cluster_id": stable_id(str(cfg["id_prefix"]), "second_pass_split_subreview", source_id, member_unit_ids),
        "registry_status": "reviewed_cluster_not_final_merge",
        "dedup_family": family,
        "registry_source_stage": "second_pass_split_subreview",
        "source_cluster_id": row.get("subreview_cluster_id"),
        "source_queue_item_id": row.get("source_split_queue_item_id"),
        "source_component_id": row.get("source_component_id"),
        "source_item_id": row.get("source_item_id"),
        "source_kind": row.get("source_kind"),
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "representative_unit_id": representative_unit_id(member_unit_ids),
        "canonical_label": row.get("subgroup_label"),
        "shared_pattern": shared_pattern(family, row),
        "confidence": row.get("confidence"),
        "refinement_key": row.get("refinement_key"),
        "source_faithfulness_notes": row.get("source_faithfulness_notes"),
        "provenance": {
            "source_file_role": "second_pass_subreview_clusters",
            "source_schema_version": row.get("schema_version"),
            "llm_decision": "split_needed_then_subgrouped",
            "recommended_next_action": "subreview_cluster_only",
        },
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }


def excluded_record(family: str, cfg: dict[str, Any], row: dict[str, Any], *, source_stage: str, excluded_status: str, excluded_reason: str) -> dict[str, Any]:
    if "unit_id" in row:
        member_unit_ids = [row.get("unit_id")] if row.get("unit_id") else []
    else:
        member_unit_ids = list(row.get("member_unit_ids") or [])
    return {
        "schema_version": cfg["excluded_schema"],
        "dedup_family": family,
        "excluded_status": excluded_status,
        "excluded_reason": excluded_reason,
        "source_stage": source_stage,
        "source_queue_item_id": row.get("queue_item_id") or row.get("source_split_queue_item_id"),
        "source_component_id": row.get("source_component_id"),
        "source_item_id": row.get("source_item_id"),
        "source_kind": row.get("source_kind"),
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "refinement_key": row.get("refinement_key"),
        "text_quality_summary": row.get("text_quality_summary") or {},
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "formal_merge_candidate": False,
        "final_merge_output": False,
    }


def main() -> int:
    args = parse_args()
    cfg = FAMILY_CONFIG[args.family]
    paths = {
        "first_pass": args.first_pass or cfg["first_pass"],
        "second_pass": args.second_pass or cfg["second_pass"],
        "first_deferred": args.first_deferred or cfg["first_deferred"],
        "split_singletons": args.split_singletons or cfg["split_singletons"],
        "split_deferred": args.split_deferred or cfg["split_deferred"],
        "queue_deferred": args.queue_deferred or cfg["queue_deferred"],
        "queue_singletons": args.queue_singletons or cfg["queue_singletons"],
    }
    output_dir = Path(args.output_dir or str(cfg["output_dir"]))
    output_prefix = str(cfg["output_prefix"])
    registry_path = output_dir / f"{output_prefix}.reviewed_cluster_registry.jsonl"
    excluded_path = output_dir / f"{output_prefix}.excluded_from_reviewed_cluster_registry.jsonl"
    summary_path = output_dir / f"{output_prefix}.reviewed_cluster_registry_summary.json"
    report_path = Path(args.report or str(cfg["report"]))
    manifest_path = Path(args.manifest or str(cfg["manifest"]))
    qa_path = Path(args.qa_report or str(cfg["qa"]))
    all_paths = [registry_path, excluded_path, summary_path, report_path, manifest_path, qa_path]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    registry_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    member_counters: Counter[str] = Counter()
    stage_counters: Counter[str] = Counter()
    confidence_counters: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []

    for _, row in iter_jsonl(paths["first_pass"], with_line_numbers=True):
        record = first_pass_registry_record(args.family, cfg, row)
        registry_rows.append(record)
        counters["first_pass_review_clusters"] += 1
        member_counters["first_pass_review_cluster_members"] += record["member_count"]
    for _, row in iter_jsonl(paths["second_pass"], with_line_numbers=True):
        record = second_pass_registry_record(args.family, cfg, row)
        registry_rows.append(record)
        counters["second_pass_subreview_clusters"] += 1
        member_counters["second_pass_subreview_cluster_members"] += record["member_count"]
    for _, row in iter_jsonl(paths["first_deferred"], with_line_numbers=True):
        decision = row.get("llm_decision") or {}
        reason = decision.get("cluster_decision") or row.get("status") or "first_pass_deferred"
        record = excluded_record(args.family, cfg, row, source_stage="first_pass_deferred", excluded_status=row.get("status") or "first_pass_deferred", excluded_reason=reason)
        excluded_rows.append(record)
        counters["first_pass_deferred_rows"] += 1
        member_counters["first_pass_deferred_members"] += record["member_count"]
    for _, row in iter_jsonl(paths["split_singletons"], with_line_numbers=True):
        record = excluded_record(args.family, cfg, row, source_stage="second_pass_split_singleton", excluded_status="split_singleton_not_cluster", excluded_reason="singleton_after_second_pass_split")
        excluded_rows.append(record)
        counters["split_singletons"] += 1
        member_counters["split_singleton_members"] += record["member_count"]
    for _, row in iter_jsonl(paths["split_deferred"], with_line_numbers=True):
        record = excluded_record(args.family, cfg, row, source_stage="second_pass_split_deferred", excluded_status="split_deferred_not_cluster", excluded_reason=row.get("subgrouping_status") or "deferred_after_second_pass_split")
        excluded_rows.append(record)
        counters["split_deferred_rows"] += 1
        member_counters["split_deferred_members"] += record["member_count"]
    for _, row in iter_jsonl(paths["queue_deferred"], with_line_numbers=True):
        record = excluded_record(args.family, cfg, row, source_stage="queue_build_deferred", excluded_status=row.get("status") or "queue_build_deferred", excluded_reason=row.get("defer_reason") or "queue_build_deferred")
        excluded_rows.append(record)
        counters["queue_build_deferred_rows"] += 1
        member_counters["queue_build_deferred_members"] += record["member_count"]
    for _, row in iter_jsonl(paths["queue_singletons"], with_line_numbers=True):
        record = excluded_record(args.family, cfg, row, source_stage="queue_build_singleton", excluded_status=row.get("status") or "singleton_no_adjudication_needed", excluded_reason="singleton_no_adjudication_needed")
        excluded_rows.append(record)
        counters["queue_build_singletons"] += 1
        member_counters["queue_build_singleton_members"] += record["member_count"]

    seen_registry_ids: set[str] = set()
    registry_member_assignments: Counter[str] = Counter()
    excluded_member_assignments: Counter[str] = Counter()
    for row in registry_rows:
        registry_id = str(row["registry_cluster_id"])
        if registry_id in seen_registry_ids:
            issues.append({"issue": "duplicate_registry_cluster_id", "registry_cluster_id": registry_id})
        seen_registry_ids.add(registry_id)
        if int(row.get("member_count") or 0) < 2:
            issues.append({"issue": "registry_cluster_with_less_than_two_members", "registry_cluster_id": registry_id})
        if row.get("final_merge_output") is not False or row.get("formal_merge_candidate") is not False:
            issues.append({"issue": "registry_row_has_merge_output_flag", "registry_cluster_id": registry_id})
        stage_counters[str(row.get("registry_source_stage") or "unknown")] += 1
        confidence_counters[str(row.get("confidence") or "unknown")] += 1
        for unit_id in row.get("member_unit_ids") or []:
            registry_member_assignments[unit_id] += 1
    for row in excluded_rows:
        for unit_id in row.get("member_unit_ids") or []:
            excluded_member_assignments[unit_id] += 1

    duplicate_registry_members = [unit_id for unit_id, count in registry_member_assignments.items() if count > 1]
    duplicate_excluded_members = [unit_id for unit_id, count in excluded_member_assignments.items() if count > 1]
    overlap = sorted(set(registry_member_assignments) & set(excluded_member_assignments))
    if duplicate_registry_members:
        issues.append({"issue": "member_in_multiple_reviewed_registry_clusters", "count": len(duplicate_registry_members), "examples": duplicate_registry_members[:20]})
    if duplicate_excluded_members:
        issues.append({"issue": "member_in_multiple_excluded_records", "count": len(duplicate_excluded_members), "examples": duplicate_excluded_members[:20]})
    if overlap:
        issues.append({"issue": "member_in_registry_and_excluded_outputs", "count": len(overlap), "examples": overlap[:20]})

    write_jsonl(registry_path, registry_rows, sort_keys=True)
    write_jsonl(excluded_path, excluded_rows, sort_keys=True)

    status = "complete" if not issues else "incomplete"
    registry_members = sum(int(row.get("member_count") or 0) for row in registry_rows)
    excluded_members = sum(int(row.get("member_count") or 0) for row in excluded_rows)
    summary = {
        "schema_version": cfg["summary_schema"],
        "created_at": utc_now(),
        "family": args.family,
        "status": status,
        "registry_rows": len(registry_rows),
        "registry_members": registry_members,
        "registry_unique_members": len(registry_member_assignments),
        "excluded_rows": len(excluded_rows),
        "excluded_members": excluded_members,
        "excluded_unique_members": len(excluded_member_assignments),
        "total_unique_members_accounted": len(set(registry_member_assignments) | set(excluded_member_assignments)),
        "stage_distribution": dict(stage_counters.most_common()),
        "confidence_distribution": dict(confidence_counters.most_common()),
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(summary_path, summary)

    report = {
        "schema_version": cfg["report_schema"],
        "created_at": utc_now(),
        "family": args.family,
        "status": status,
        "inputs": {name: {"path": path, "sha256": file_sha256(path)} for name, path in paths.items()},
        "outputs": {
            "registry": {"path": str(registry_path), "rows": len(registry_rows), "members": registry_members, "sha256": file_sha256(registry_path)},
            "excluded": {"path": str(excluded_path), "rows": len(excluded_rows), "members": excluded_members, "sha256": file_sha256(excluded_path)},
            "summary": {"path": str(summary_path), "sha256": file_sha256(summary_path)},
        },
        "counts": {
            **dict(counters),
            **dict(member_counters),
            "registry_rows": len(registry_rows),
            "registry_members": registry_members,
            "registry_unique_members": len(registry_member_assignments),
            "excluded_rows": len(excluded_rows),
            "excluded_members": excluded_members,
            "excluded_unique_members": len(excluded_member_assignments),
            "total_unique_members_accounted": summary["total_unique_members_accounted"],
        },
        "stage_distribution": dict(stage_counters.most_common()),
        "confidence_distribution": dict(confidence_counters.most_common()),
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(report_path, report)
    write_json(manifest_path, report)
    write_json(qa_path, report)
    print(json.dumps({"status": status, "family": args.family, "issues_count": len(issues), "counts": report["counts"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
