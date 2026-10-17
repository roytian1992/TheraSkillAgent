#!/usr/bin/env python3
"""Build a QA/psychoeducation reviewed cluster registry under dedup/."""

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

DEFAULT_FIRST_PASS = "outputs/knowledge/dedup/clusters/qa_psychoeducation_queue_review/qa_psychoeducation.review_clusters.jsonl"
DEFAULT_SECOND_PASS = "outputs/knowledge/dedup/clusters/qa_psychoeducation_split_subreview/qa_psychoeducation.subreview_clusters.jsonl"
DEFAULT_FIRST_DEFERRED = "outputs/knowledge/dedup/clusters/qa_psychoeducation_queue_review/qa_psychoeducation.deferred_by_llm.jsonl"
DEFAULT_SPLIT_SINGLETONS = "outputs/knowledge/dedup/clusters/qa_psychoeducation_split_subreview/qa_psychoeducation.split_singletons.jsonl"
DEFAULT_SPLIT_DEFERRED = "outputs/knowledge/dedup/clusters/qa_psychoeducation_split_subreview/qa_psychoeducation.split_deferred_members.jsonl"
DEFAULT_QUEUE_DEFERRED = "outputs/knowledge/dedup/adjudication_queues/qa_psychoeducation/qa_psychoeducation.deferred_components.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/registries/qa_psychoeducation_reviewed_clusters"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/qa_psychoeducation_reviewed_cluster_registry_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/qa_psychoeducation_reviewed_cluster_registry_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/qa_psychoeducation_reviewed_cluster_registry_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-pass", default=DEFAULT_FIRST_PASS)
    parser.add_argument("--second-pass", default=DEFAULT_SECOND_PASS)
    parser.add_argument("--first-deferred", default=DEFAULT_FIRST_DEFERRED)
    parser.add_argument("--split-singletons", default=DEFAULT_SPLIT_SINGLETONS)
    parser.add_argument("--split-deferred", default=DEFAULT_SPLIT_DEFERRED)
    parser.add_argument("--queue-deferred", default=DEFAULT_QUEUE_DEFERRED)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
def stable_registry_id(stage: str, source_id: str, member_unit_ids: list[str]) -> str:
    raw = stage + "::" + source_id + "::" + "::".join(sorted(member_unit_ids))
    return "qa_reg_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def representative_unit_id(member_unit_ids: list[str]) -> str | None:
    return sorted(member_unit_ids)[0] if member_unit_ids else None


def list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def first_pass_registry_record(row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    decision = row.get("llm_decision") or {}
    source_id = str(row.get("review_cluster_id") or row.get("queue_item_id"))
    return {
        "schema_version": "theraskill_qa_psychoeducation_reviewed_cluster_registry_v1",
        "registry_cluster_id": stable_registry_id("first_pass_merge_equivalent", source_id, member_unit_ids),
        "registry_status": "reviewed_cluster_not_final_merge",
        "dedup_family": "qa_psychoeducation",
        "registry_source_stage": "first_pass_merge_equivalent",
        "source_cluster_id": row.get("review_cluster_id"),
        "source_queue_item_id": row.get("queue_item_id"),
        "source_component_id": row.get("component_id"),
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "representative_unit_id": representative_unit_id(member_unit_ids),
        "canonical_topic_label": row.get("canonical_topic_label") or decision.get("canonical_topic_label"),
        "shared_question_or_need": row.get("shared_question_or_need") or decision.get("shared_question_or_need"),
        "shared_answer_intent": row.get("shared_answer_intent") or decision.get("shared_answer_intent"),
        "confidence": row.get("merge_confidence") or decision.get("merge_confidence"),
        "clinical_boundary_notes": row.get("clinical_boundary_notes") or decision.get("clinical_boundary_notes"),
        "source_or_license_constraints": row.get("source_or_license_constraints")
        or decision.get("source_or_license_constraints")
        or [],
        "component_metadata": row.get("component_metadata") or {},
        "text_quality_summary": row.get("text_quality_summary") or {},
        "source_faithfulness_notes": row.get("source_faithfulness_notes") or decision.get("source_faithfulness_notes"),
        "provenance": {
            "source_file": DEFAULT_FIRST_PASS,
            "source_schema_version": row.get("schema_version"),
            "llm_decision": "merge_equivalent",
            "recommended_next_action": "build_review_cluster",
        },
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }


def second_pass_registry_record(row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    source_id = str(row.get("subreview_cluster_id"))
    return {
        "schema_version": "theraskill_qa_psychoeducation_reviewed_cluster_registry_v1",
        "registry_cluster_id": stable_registry_id("second_pass_split_subreview", source_id, member_unit_ids),
        "registry_status": "reviewed_cluster_not_final_merge",
        "dedup_family": "qa_psychoeducation",
        "registry_source_stage": "second_pass_split_subreview",
        "source_cluster_id": row.get("subreview_cluster_id"),
        "source_queue_item_id": row.get("source_split_queue_item_id"),
        "source_component_id": row.get("source_component_id"),
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "representative_unit_id": representative_unit_id(member_unit_ids),
        "canonical_topic_label": row.get("subgroup_label"),
        "shared_question_or_need": row.get("shared_question_or_need"),
        "shared_answer_intent": row.get("shared_answer_intent"),
        "confidence": row.get("confidence"),
        "clinical_boundary_notes": row.get("clinical_boundary_notes"),
        "source_or_license_constraints": row.get("license_or_source_notes") or [],
        "component_metadata": row.get("component_metadata") or {},
        "text_quality_summary": row.get("text_quality_summary") or {},
        "source_faithfulness_notes": row.get("source_faithfulness_notes"),
        "provenance": {
            "source_file": DEFAULT_SECOND_PASS,
            "source_schema_version": row.get("schema_version"),
            "llm_decision": "split_needed_then_subgrouped",
            "recommended_next_action": "subreview_cluster_only",
        },
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }


def excluded_record(
    *,
    schema_version: str = "theraskill_qa_psychoeducation_excluded_from_registry_v1",
    excluded_status: str,
    excluded_reason: str,
    source_stage: str,
    source_queue_item_id: str | None,
    source_component_id: str | None,
    member_unit_ids: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": schema_version,
        "excluded_status": excluded_status,
        "excluded_reason": excluded_reason,
        "source_stage": source_stage,
        "source_queue_item_id": source_queue_item_id,
        "source_component_id": source_component_id,
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidate": False,
    }
    if extra:
        payload.update(extra)
    return payload


def first_deferred_record(row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    decision = row.get("llm_decision") or {}
    return excluded_record(
        excluded_status=row.get("status") or "first_pass_deferred",
        excluded_reason=decision.get("cluster_decision") or "deferred_by_first_pass_llm",
        source_stage="first_pass_deferred",
        source_queue_item_id=row.get("queue_item_id"),
        source_component_id=row.get("component_id"),
        member_unit_ids=member_unit_ids,
        extra={
            "canonical_topic_label": row.get("canonical_topic_label") or decision.get("canonical_topic_label"),
            "text_quality_summary": row.get("text_quality_summary") or {},
            "source_faithfulness_notes": decision.get("source_faithfulness_notes"),
        },
    )


def singleton_record(row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = [row.get("unit_id")] if row.get("unit_id") else []
    return excluded_record(
        excluded_status="split_singleton_not_cluster",
        excluded_reason="singleton_after_second_pass_split",
        source_stage="second_pass_split_singleton",
        source_queue_item_id=row.get("source_split_queue_item_id"),
        source_component_id=row.get("source_component_id"),
        member_unit_ids=member_unit_ids,
        extra={
            "subgroup_label": row.get("subgroup_label"),
            "shared_question_or_need": row.get("shared_question_or_need"),
            "shared_answer_intent": row.get("shared_answer_intent"),
            "confidence": row.get("confidence"),
        },
    )


def split_deferred_record(row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = [row.get("unit_id")] if row.get("unit_id") else []
    return excluded_record(
        excluded_status="split_deferred_not_cluster",
        excluded_reason=row.get("subgrouping_status") or "deferred_after_second_pass_split",
        source_stage="second_pass_split_deferred",
        source_queue_item_id=row.get("source_split_queue_item_id"),
        source_component_id=row.get("source_component_id"),
        member_unit_ids=member_unit_ids,
        extra={
            "quality_notes": row.get("quality_notes") or [],
            "source_faithfulness_notes": row.get("source_faithfulness_notes"),
        },
    )


def queue_deferred_record(row: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(row.get("member_unit_ids") or [])
    return excluded_record(
        excluded_status=row.get("status") or "queue_build_deferred",
        excluded_reason=row.get("deferred_reason") or row.get("reason") or "deferred_during_queue_build",
        source_stage="queue_build_deferred",
        source_queue_item_id=row.get("queue_item_id"),
        source_component_id=row.get("component_id"),
        member_unit_ids=member_unit_ids,
        extra={
            "component_metadata": row.get("component_metadata") or row.get("component") or {},
            "deferred_notes": row.get("deferred_notes") or row.get("notes") or [],
        },
    )


def add_member_assignments(rows: list[dict[str, Any]]) -> Counter[str]:
    assignments: Counter[str] = Counter()
    for row in rows:
        for unit_id in row.get("member_unit_ids") or []:
            assignments[str(unit_id)] += 1
    return assignments


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    registry_path = output_dir / "qa_psychoeducation.reviewed_cluster_registry.jsonl"
    excluded_path = output_dir / "qa_psychoeducation.excluded_from_reviewed_cluster_registry.jsonl"
    summary_path = output_dir / "qa_psychoeducation.reviewed_cluster_registry_summary.json"
    all_paths = [registry_path, excluded_path, summary_path, Path(args.report), Path(args.manifest), Path(args.qa_report)]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    registry_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    member_counters: Counter[str] = Counter()
    stage_counters: Counter[str] = Counter()
    confidence_counters: Counter[str] = Counter()
    topic_counters: Counter[str] = Counter()
    knowledge_type_counters: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []

    for _, row in iter_jsonl(args.first_pass, with_line_numbers=True):
        record = first_pass_registry_record(row)
        registry_rows.append(record)
        counters["first_pass_review_clusters"] += 1
        member_counters["first_pass_review_cluster_members"] += record["member_count"]
    for _, row in iter_jsonl(args.second_pass, with_line_numbers=True):
        record = second_pass_registry_record(row)
        registry_rows.append(record)
        counters["second_pass_subreview_clusters"] += 1
        member_counters["second_pass_subreview_cluster_members"] += record["member_count"]
    for _, row in iter_jsonl(args.first_deferred, with_line_numbers=True):
        record = first_deferred_record(row)
        excluded_rows.append(record)
        counters["first_pass_deferred_rows"] += 1
        member_counters["first_pass_deferred_members"] += record["member_count"]
    for _, row in iter_jsonl(args.split_singletons, with_line_numbers=True):
        record = singleton_record(row)
        excluded_rows.append(record)
        counters["split_singletons"] += 1
        member_counters["split_singleton_members"] += record["member_count"]
    for _, row in iter_jsonl(args.split_deferred, with_line_numbers=True):
        record = split_deferred_record(row)
        excluded_rows.append(record)
        counters["split_deferred_rows"] += 1
        member_counters["split_deferred_members"] += record["member_count"]
    for _, row in iter_jsonl(args.queue_deferred, with_line_numbers=True):
        record = queue_deferred_record(row)
        excluded_rows.append(record)
        counters["queue_build_deferred_rows"] += 1
        member_counters["queue_build_deferred_members"] += record["member_count"]

    seen_registry_ids: set[str] = set()
    for row in registry_rows:
        registry_id = row["registry_cluster_id"]
        if registry_id in seen_registry_ids:
            issues.append({"issue": "duplicate_registry_cluster_id", "registry_cluster_id": registry_id})
        seen_registry_ids.add(registry_id)
        if row.get("member_count", 0) < 2:
            issues.append({"issue": "registry_cluster_with_less_than_two_members", "registry_cluster_id": registry_id})
        if row.get("final_merge_output") is not False or row.get("formal_merge_candidate") is not False:
            issues.append({"issue": "registry_row_has_merge_output_flag", "registry_cluster_id": registry_id})
        stage_counters[str(row.get("registry_source_stage") or "unknown")] += 1
        confidence_counters[str(row.get("confidence") or "unknown")] += 1
        topic_counters[str(row.get("canonical_topic_label") or "unknown")] += 1
        kt_dist = (row.get("component_metadata") or {}).get("knowledge_type_distribution") or {}
        for key, value in kt_dist.items():
            knowledge_type_counters[str(key)] += int(value or 0)

    for row in excluded_rows:
        if row.get("final_merge_output") is not False or row.get("formal_merge_candidate") is not False:
            issues.append({"issue": "excluded_row_has_merge_output_flag", "source_stage": row.get("source_stage")})

    registry_member_assignments = add_member_assignments(registry_rows)
    excluded_member_assignments = add_member_assignments(excluded_rows)
    duplicate_registry_members = [unit_id for unit_id, count in registry_member_assignments.items() if count > 1]
    duplicate_excluded_members = [unit_id for unit_id, count in excluded_member_assignments.items() if count > 1]
    if duplicate_registry_members:
        issues.append(
            {
                "issue": "member_in_multiple_reviewed_registry_clusters",
                "count": len(duplicate_registry_members),
                "examples": duplicate_registry_members[:20],
            }
        )
    if duplicate_excluded_members:
        issues.append(
            {
                "issue": "member_in_multiple_excluded_rows",
                "count": len(duplicate_excluded_members),
                "examples": duplicate_excluded_members[:20],
            }
        )
    overlap_excluded = sorted(set(registry_member_assignments) & set(excluded_member_assignments))
    if overlap_excluded:
        issues.append(
            {
                "issue": "member_in_registry_and_excluded_outputs",
                "count": len(overlap_excluded),
                "examples": overlap_excluded[:20],
            }
        )

    write_jsonl(registry_path, registry_rows, sort_keys=True)
    write_jsonl(excluded_path, excluded_rows, sort_keys=True)

    status = "complete" if not issues else "incomplete"
    summary = {
        "schema_version": "theraskill_qa_psychoeducation_reviewed_cluster_registry_summary_v1",
        "created_at": utc_now(),
        "status": status,
        "registry_rows": len(registry_rows),
        "registry_members": sum(int(row.get("member_count") or 0) for row in registry_rows),
        "excluded_rows": len(excluded_rows),
        "excluded_members": sum(int(row.get("member_count") or 0) for row in excluded_rows),
        "stage_distribution": dict(stage_counters.most_common()),
        "confidence_distribution": dict(confidence_counters.most_common()),
        "topic_distribution_top50": dict(topic_counters.most_common(50)),
        "knowledge_type_member_distribution": dict(knowledge_type_counters.most_common()),
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(summary_path, summary)

    report = {
        "schema_version": "theraskill_qa_psychoeducation_reviewed_cluster_registry_report_v1",
        "created_at": utc_now(),
        "status": status,
        "inputs": {
            "first_pass_review_clusters": {"path": args.first_pass, "sha256": file_sha256(args.first_pass)},
            "second_pass_subreview_clusters": {"path": args.second_pass, "sha256": file_sha256(args.second_pass)},
            "first_pass_deferred": {"path": args.first_deferred, "sha256": file_sha256(args.first_deferred)},
            "split_singletons": {"path": args.split_singletons, "sha256": file_sha256(args.split_singletons)},
            "split_deferred": {"path": args.split_deferred, "sha256": file_sha256(args.split_deferred)},
            "queue_build_deferred": {"path": args.queue_deferred, "sha256": file_sha256(args.queue_deferred)},
        },
        "outputs": {
            "registry": {
                "path": str(registry_path),
                "rows": len(registry_rows),
                "members": summary["registry_members"],
                "sha256": file_sha256(registry_path),
            },
            "excluded": {
                "path": str(excluded_path),
                "rows": len(excluded_rows),
                "members": summary["excluded_members"],
                "sha256": file_sha256(excluded_path),
            },
            "summary": {"path": str(summary_path), "sha256": file_sha256(summary_path)},
        },
        "counts": {
            **dict(counters),
            **dict(member_counters),
            "registry_rows": len(registry_rows),
            "registry_members": summary["registry_members"],
            "registry_unique_members": len(registry_member_assignments),
            "excluded_rows": len(excluded_rows),
            "excluded_members": summary["excluded_members"],
            "excluded_unique_members": len(excluded_member_assignments),
            "total_unique_members_accounted": len(set(registry_member_assignments) | set(excluded_member_assignments)),
        },
        "stage_distribution": dict(stage_counters.most_common()),
        "confidence_distribution": dict(confidence_counters.most_common()),
        "topic_distribution_top50": dict(topic_counters.most_common(50)),
        "knowledge_type_member_distribution": dict(knowledge_type_counters.most_common()),
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
