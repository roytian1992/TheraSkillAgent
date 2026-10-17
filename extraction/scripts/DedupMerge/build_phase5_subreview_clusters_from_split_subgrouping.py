#!/usr/bin/env python3
"""Build Phase 5 subreview clusters from split-needed subgrouping results."""

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

from io_utils import file_sha256, write_json, write_jsonl  # noqa: E402

FAMILY_CONFIG = {
    "clinical_risk_assessment": {
        "subgrouping": "outputs/knowledge/dedup/adjudications/clinical_risk_assessment_split_subgrouping/clinical_risk_assessment.split_subgrouping.jsonl",
        "output_dir": "outputs/knowledge/dedup/clusters/clinical_risk_assessment_split_subreview",
        "report": "outputs/knowledge/dedup/reports/clinical_risk_assessment_split_subreview_clusters_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_split_subreview_clusters_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_split_subreview_clusters_qa.json",
        "prefix": "clinical_split_subreview_",
        "output_prefix": "clinical_risk_assessment",
        "cluster_schema": "theraskill_clinical_risk_assessment_subreview_cluster_v1",
        "singleton_schema": "theraskill_clinical_risk_assessment_split_singleton_v1",
        "deferred_schema": "theraskill_clinical_risk_assessment_split_deferred_member_v1",
        "report_schema": "theraskill_clinical_risk_assessment_split_subreview_clusters_report_v1",
    },
    "therapy_process": {
        "subgrouping": "outputs/knowledge/dedup/adjudications/therapy_process_split_subgrouping/therapy_process.split_subgrouping.jsonl",
        "output_dir": "outputs/knowledge/dedup/clusters/therapy_process_split_subreview",
        "report": "outputs/knowledge/dedup/reports/therapy_process_split_subreview_clusters_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/therapy_process_split_subreview_clusters_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/therapy_process_split_subreview_clusters_qa.json",
        "prefix": "therapy_split_subreview_",
        "output_prefix": "therapy_process",
        "cluster_schema": "theraskill_therapy_process_subreview_cluster_v1",
        "singleton_schema": "theraskill_therapy_process_split_singleton_v1",
        "deferred_schema": "theraskill_therapy_process_split_deferred_member_v1",
        "report_schema": "theraskill_therapy_process_split_subreview_clusters_report_v1",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=sorted(FAMILY_CONFIG), required=True)
    parser.add_argument("--subgrouping")
    parser.add_argument("--output-dir")
    parser.add_argument("--report")
    parser.add_argument("--manifest")
    parser.add_argument("--qa-report")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, queue_item_id: str, member_unit_ids: list[str]) -> str:
    raw = queue_item_id + "::" + "::".join(sorted(member_unit_ids))
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def subgroup_content_fields(family: str, subgroup: dict[str, Any]) -> dict[str, Any]:
    if family == "clinical_risk_assessment":
        return {
            "shared_construct": subgroup.get("shared_construct"),
            "label_or_severity_boundary": subgroup.get("label_or_severity_boundary"),
        }
    return {
        "shared_state_move_response_pattern": subgroup.get("shared_state_move_response_pattern"),
        "process_boundary": subgroup.get("process_boundary"),
    }


def common_source_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_split_queue_item_id": row.get("queue_item_id"),
        "source_item_id": row.get("source_item_id"),
        "source_component_id": row.get("source_component_id"),
        "source_kind": row.get("source_kind"),
        "source_subgrouping_schema_version": row.get("schema_version"),
        "component_metadata": row.get("component_metadata") or {},
        "refinement_key": row.get("refinement_key"),
        "text_quality_summary": row.get("text_quality_summary") or {},
        "first_pass_decision": row.get("first_pass_decision") or {},
        "subsplit_cues_used": (row.get("subgrouping") or {}).get("subsplit_cues_used") or [],
        "source_faithfulness_notes": (row.get("subgrouping") or {}).get("source_faithfulness_notes"),
    }


def build_subreview_cluster(family: str, cfg: dict[str, Any], row: dict[str, Any], subgroup: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(subgroup.get("member_unit_ids") or [])
    queue_item_id = str(row.get("queue_item_id"))
    return {
        "schema_version": cfg["cluster_schema"],
        "subreview_cluster_id": stable_id(str(cfg["prefix"]), queue_item_id, member_unit_ids),
        "dedup_family": family,
        "status": "subreview_cluster_not_final_merge",
        "member_unit_ids": member_unit_ids,
        "member_count": len(member_unit_ids),
        "subgroup_label": subgroup.get("subgroup_label"),
        "separation_reason": subgroup.get("separation_reason"),
        "confidence": subgroup.get("confidence"),
        **subgroup_content_fields(family, subgroup),
        **common_source_fields(row),
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "formal_merge_candidate": False,
        "final_merge_output": False,
    }


def build_singleton(family: str, cfg: dict[str, Any], row: dict[str, Any], subgroup: dict[str, Any]) -> dict[str, Any]:
    member_unit_ids = list(subgroup.get("member_unit_ids") or [])
    return {
        "schema_version": cfg["singleton_schema"],
        "dedup_family": family,
        "status": "singleton_after_split_not_merge_cluster",
        "unit_id": member_unit_ids[0] if member_unit_ids else None,
        "subgroup_label": subgroup.get("subgroup_label"),
        "separation_reason": subgroup.get("separation_reason"),
        "confidence": subgroup.get("confidence"),
        **subgroup_content_fields(family, subgroup),
        **common_source_fields(row),
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "formal_merge_candidate": False,
        "final_merge_output": False,
    }


def build_deferred(family: str, cfg: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    subgrouping = row.get("subgrouping") or {}
    deferred = []
    for unit_id in subgrouping.get("defer_member_unit_ids") or []:
        deferred.append(
            {
                "schema_version": cfg["deferred_schema"],
                "dedup_family": family,
                "status": "deferred_after_split_not_merge_cluster",
                "unit_id": unit_id,
                "subgrouping_status": subgrouping.get("subgrouping_status"),
                "quality_notes": subgrouping.get("quality_notes") or [],
                **common_source_fields(row),
                "not_final_merged_unit": True,
                "raw_units_unchanged": True,
                "formal_merge_candidate": False,
                "final_merge_output": False,
            }
        )
    return deferred


def main() -> int:
    args = parse_args()
    cfg = FAMILY_CONFIG[args.family]
    subgrouping_path = args.subgrouping or cfg["subgrouping"]
    output_dir = Path(args.output_dir or str(cfg["output_dir"]))
    output_prefix = str(cfg["output_prefix"])
    subreview_path = output_dir / f"{output_prefix}.subreview_clusters.jsonl"
    singleton_path = output_dir / f"{output_prefix}.split_singletons.jsonl"
    deferred_path = output_dir / f"{output_prefix}.split_deferred_members.jsonl"
    report_path = Path(args.report or str(cfg["report"]))
    manifest_path = Path(args.manifest or str(cfg["manifest"]))
    qa_path = Path(args.qa_report or str(cfg["qa"]))
    all_paths = [subreview_path, singleton_path, deferred_path, report_path, manifest_path, qa_path]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    subreview_rows: list[dict[str, Any]] = []
    singleton_rows: list[dict[str, Any]] = []
    deferred_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    member_counters: Counter[str] = Counter()
    confidence_counters: Counter[str] = Counter()
    subgrouping_status_counter: Counter[str] = Counter()
    seen_subreview_ids: set[str] = set()
    seen_member_assignments: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []
    complete_statuses = {"complete", "complete_with_normalization_issues"}

    with Path(subgrouping_path).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            counters["input_rows"] += 1
            row = json.loads(line)
            if row.get("status") not in complete_statuses:
                issues.append({"issue": "non_complete_subgrouping_row", "line": line_no, "queue_item_id": row.get("queue_item_id"), "status": row.get("status")})
                continue
            source_member_ids = set(row.get("member_unit_ids") or [])
            accounted: list[str] = []
            subgrouping = row.get("subgrouping") or {}
            subgrouping_status_counter[str(subgrouping.get("subgrouping_status"))] += 1
            for subgroup in subgrouping.get("subgroups") or []:
                member_unit_ids = list(subgroup.get("member_unit_ids") or [])
                accounted.extend(member_unit_ids)
                confidence_counters[str(subgroup.get("confidence"))] += 1
                if len(member_unit_ids) >= 2:
                    record = build_subreview_cluster(args.family, cfg, row, subgroup)
                    if record["subreview_cluster_id"] in seen_subreview_ids:
                        issues.append({"issue": "duplicate_subreview_cluster_id", "subreview_cluster_id": record["subreview_cluster_id"]})
                    seen_subreview_ids.add(record["subreview_cluster_id"])
                    subreview_rows.append(record)
                    counters["subreview_clusters"] += 1
                    member_counters["subreview_cluster_members"] += len(member_unit_ids)
                elif len(member_unit_ids) == 1:
                    singleton_rows.append(build_singleton(args.family, cfg, row, subgroup))
                    counters["split_singletons"] += 1
                    member_counters["split_singleton_members"] += 1
                else:
                    issues.append({"issue": "empty_subgroup", "line": line_no, "queue_item_id": row.get("queue_item_id")})
            row_deferred = build_deferred(args.family, cfg, row)
            for item in row_deferred:
                accounted.append(item["unit_id"])
            deferred_rows.extend(row_deferred)
            counters["deferred_rows"] += len(row_deferred)
            member_counters["deferred_members"] += len(row_deferred)
            if set(accounted) != source_member_ids:
                issues.append(
                    {
                        "issue": "source_member_accounting_mismatch",
                        "line": line_no,
                        "queue_item_id": row.get("queue_item_id"),
                        "source_member_count": len(source_member_ids),
                        "accounted_unique_count": len(set(accounted)),
                    }
                )
            if len(accounted) != len(set(accounted)):
                issues.append({"issue": "duplicate_member_within_source_row", "line": line_no, "queue_item_id": row.get("queue_item_id")})
            for unit_id in accounted:
                seen_member_assignments[unit_id] += 1

    duplicate_member_ids = [unit_id for unit_id, count in seen_member_assignments.items() if count > 1]
    if duplicate_member_ids:
        issues.append({"issue": "member_assigned_from_multiple_split_rows", "count": len(duplicate_member_ids), "examples": duplicate_member_ids[:20]})

    write_jsonl(subreview_path, subreview_rows)
    write_jsonl(singleton_path, singleton_rows)
    write_jsonl(deferred_path, deferred_rows)

    status = "complete" if not issues else "incomplete"
    report = {
        "schema_version": cfg["report_schema"],
        "created_at": utc_now(),
        "family": args.family,
        "status": status,
        "inputs": {"subgrouping": {"path": subgrouping_path, "rows": counters["input_rows"], "sha256": file_sha256(subgrouping_path)}},
        "outputs": {
            "subreview_clusters": {
                "path": str(subreview_path),
                "rows": len(subreview_rows),
                "members": sum(int(row.get("member_count") or 0) for row in subreview_rows),
                "sha256": file_sha256(subreview_path),
            },
            "split_singletons": {"path": str(singleton_path), "rows": len(singleton_rows), "members": len(singleton_rows), "sha256": file_sha256(singleton_path)},
            "split_deferred_members": {"path": str(deferred_path), "rows": len(deferred_rows), "members": len(deferred_rows), "sha256": file_sha256(deferred_path)},
        },
        "counts": {
            **dict(counters),
            **dict(member_counters),
            "accounted_members": sum(member_counters.values()),
            "unique_accounted_members": len(seen_member_assignments),
        },
        "subgrouping_status_distribution": dict(subgrouping_status_counter.most_common()),
        "subgroup_confidence_distribution": dict(confidence_counters.most_common()),
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
