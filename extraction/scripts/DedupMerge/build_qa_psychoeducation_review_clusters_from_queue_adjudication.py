#!/usr/bin/env python3
"""Build QA/psychoeducation review clusters from full queue adjudication decisions."""

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

DEFAULT_QUEUE = "outputs/knowledge/dedup/adjudication_queues/qa_psychoeducation/qa_psychoeducation.adjudication_queue.jsonl"
DEFAULT_ADJUDICATION = "outputs/knowledge/dedup/adjudications/qa_psychoeducation_queue/qa_psychoeducation.queue_adjudication.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/clusters/qa_psychoeducation_queue_review"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/qa_psychoeducation_queue_review_clusters_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/qa_psychoeducation_queue_review_clusters_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/qa_psychoeducation_queue_review_clusters_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=DEFAULT_QUEUE)
    parser.add_argument("--adjudication", default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl_by_queue_item(path: str | Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            queue_item_id = row.get("queue_item_id")
            if not queue_item_id:
                raise ValueError(f"missing queue_item_id at {path}:{line_no}")
            if queue_item_id in rows:
                raise ValueError(f"duplicate queue_item_id {queue_item_id} at {path}:{line_no}")
            rows[str(queue_item_id)] = row
    return rows


def stable_cluster_id(queue_item_id: str) -> str:
    return "qa_review_" + hashlib.sha256(queue_item_id.encode("utf-8")).hexdigest()[:20]


def example_unit_roles(queue_row: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    examples = queue_row.get("evidence_examples") or []
    example_unit_ids = {f"E{idx}": example.get("unit_id") for idx, example in enumerate(examples, start=1)}
    roles = []
    for role in decision.get("evidence_example_roles") or []:
        if not isinstance(role, dict):
            continue
        local_id = role.get("local_example_id")
        unit_id = example_unit_ids.get(local_id)
        if not unit_id:
            continue
        roles.append(
            {
                "local_example_id": local_id,
                "unit_id": unit_id,
                "role": role.get("role"),
                "reason": role.get("reason"),
            }
        )
    return roles


def build_review_cluster(queue_row: dict[str, Any], adjudication_row: dict[str, Any]) -> dict[str, Any]:
    decision = adjudication_row.get("llm_decision") or {}
    queue_item_id = str(queue_row["queue_item_id"])
    return {
        "schema_version": "theraskill_qa_psychoeducation_review_cluster_v1",
        "review_cluster_id": stable_cluster_id(queue_item_id),
        "status": "review_cluster_not_final_merge",
        "queue_item_id": queue_item_id,
        "source_queue_schema_version": queue_row.get("schema_version"),
        "adjudication_id": adjudication_row.get("queue_item_id"),
        "component_id": queue_row.get("component_id"),
        "member_unit_ids": queue_row.get("member_unit_ids") or [],
        "member_count": queue_row.get("member_count"),
        "component_metadata": queue_row.get("component_metadata") or {},
        "text_quality_summary": queue_row.get("text_quality_summary") or {},
        "evidence_examples": queue_row.get("evidence_examples") or [],
        "evidence_example_roles": example_unit_roles(queue_row, decision),
        "llm_decision": decision,
        "canonical_topic_label": decision.get("canonical_topic_label"),
        "shared_question_or_need": decision.get("shared_question_or_need"),
        "shared_answer_intent": decision.get("shared_answer_intent"),
        "merge_confidence": decision.get("merge_confidence"),
        "source_or_license_constraints": decision.get("source_or_license_constraints") or [],
        "clinical_boundary_notes": decision.get("clinical_boundary_notes"),
        "source_faithfulness_notes": decision.get("source_faithfulness_notes"),
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
    }


def build_nonmerge_record(queue_row: dict[str, Any], adjudication_row: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_qa_psychoeducation_nonmerge_cluster_decision_v1",
        "status": status,
        "queue_item_id": queue_row.get("queue_item_id"),
        "component_id": queue_row.get("component_id"),
        "member_unit_ids": queue_row.get("member_unit_ids") or [],
        "member_count": queue_row.get("member_count"),
        "component_metadata": queue_row.get("component_metadata") or {},
        "text_quality_summary": queue_row.get("text_quality_summary") or {},
        "evidence_examples": queue_row.get("evidence_examples") or [],
        "llm_decision": adjudication_row.get("llm_decision") or {},
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
    }


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    review_path = output_dir / "qa_psychoeducation.review_clusters.jsonl"
    split_path = output_dir / "qa_psychoeducation.split_needed_queue.jsonl"
    defer_path = output_dir / "qa_psychoeducation.deferred_by_llm.jsonl"
    all_paths = [review_path, split_path, defer_path, Path(args.report), Path(args.manifest), Path(args.qa_report)]
    if any(path.exists() for path in all_paths) and not args.overwrite:
        existing = [str(path) for path in all_paths if path.exists()]
        raise FileExistsError(f"outputs exist; pass --overwrite: {existing}")

    queue_rows = load_jsonl_by_queue_item(args.queue)
    adjudication_rows = load_jsonl_by_queue_item(args.adjudication)
    issues: list[dict[str, Any]] = []
    if set(queue_rows) != set(adjudication_rows):
        missing_adj = sorted(set(queue_rows) - set(adjudication_rows))
        missing_queue = sorted(set(adjudication_rows) - set(queue_rows))
        issues.append(
            {
                "issue": "queue_adjudication_id_mismatch",
                "missing_adjudication": len(missing_adj),
                "missing_queue": len(missing_queue),
                "missing_adjudication_examples": missing_adj[:10],
                "missing_queue_examples": missing_queue[:10],
            }
        )

    review_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    defer_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    member_counters: Counter[str] = Counter()
    dataset_counters: Counter[str] = Counter()
    knowledge_type_counters: Counter[str] = Counter()

    for queue_item_id, queue_row in queue_rows.items():
        adjudication_row = adjudication_rows.get(queue_item_id)
        if not adjudication_row:
            continue
        if adjudication_row.get("status") != "complete":
            issues.append({"issue": "incomplete_adjudication_row", "queue_item_id": queue_item_id})
            continue
        decision = adjudication_row.get("llm_decision") or {}
        cluster_decision = decision.get("cluster_decision")
        action = decision.get("recommended_next_action")
        member_count = int(queue_row.get("member_count") or len(queue_row.get("member_unit_ids") or []))
        metadata = queue_row.get("component_metadata") or {}
        counters[f"decision::{cluster_decision}"] += 1
        counters[f"action::{action}"] += 1
        member_counters[f"decision::{cluster_decision}"] += member_count
        dataset_counters.update(metadata.get("dataset_distribution") or {})
        knowledge_type_counters.update(metadata.get("knowledge_type_distribution") or {})
        if cluster_decision == "merge_equivalent" and action == "build_review_cluster":
            review_rows.append(build_review_cluster(queue_row, adjudication_row))
        elif cluster_decision == "split_needed":
            split_rows.append(build_nonmerge_record(queue_row, adjudication_row, "split_needed_not_review_cluster"))
        elif cluster_decision in {"defer_generic", "defer_quality_issue"}:
            defer_rows.append(build_nonmerge_record(queue_row, adjudication_row, f"{cluster_decision}_not_review_cluster"))
        else:
            issues.append(
                {
                    "issue": "unexpected_adjudication_decision",
                    "queue_item_id": queue_item_id,
                    "cluster_decision": cluster_decision,
                    "recommended_next_action": action,
                }
            )
            defer_rows.append(build_nonmerge_record(queue_row, adjudication_row, "unexpected_decision_deferred"))

    write_jsonl(review_path, review_rows, sort_keys=True)
    write_jsonl(split_path, split_rows, sort_keys=True)
    write_jsonl(defer_path, defer_rows, sort_keys=True)

    status = "complete" if not issues else "incomplete"
    report = {
        "schema_version": "theraskill_qa_psychoeducation_review_clusters_report_v1",
        "created_at": utc_now(),
        "status": status,
        "inputs": {
            "queue": {"path": args.queue, "rows": len(queue_rows), "sha256": file_sha256(args.queue)},
            "adjudication": {"path": args.adjudication, "rows": len(adjudication_rows), "sha256": file_sha256(args.adjudication)},
        },
        "outputs": {
            "review_clusters": {"path": str(review_path), "rows": len(review_rows), "sha256": file_sha256(review_path)},
            "split_needed_queue": {"path": str(split_path), "rows": len(split_rows), "sha256": file_sha256(split_path)},
            "deferred_by_llm": {"path": str(defer_path), "rows": len(defer_rows), "sha256": file_sha256(defer_path)},
        },
        "counts": {
            "queue_rows": len(queue_rows),
            "adjudication_rows": len(adjudication_rows),
            "review_cluster_rows": len(review_rows),
            "review_cluster_members": sum(int(row.get("member_count") or 0) for row in review_rows),
            "split_needed_rows": len(split_rows),
            "split_needed_members": sum(int(row.get("member_count") or 0) for row in split_rows),
            "deferred_rows": len(defer_rows),
            "deferred_members": sum(int(row.get("member_count") or 0) for row in defer_rows),
        },
        "decision_distribution": dict(counters.most_common()),
        "member_distribution": dict(member_counters.most_common()),
        "dataset_distribution": dict(dataset_counters.most_common()),
        "knowledge_type_distribution": dict(knowledge_type_counters.most_common()),
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(args.report, report)
    write_json(args.manifest, report)
    write_json(args.qa_report, report)
    print(json.dumps({"status": status, "counts": report["counts"], "issues_count": len(issues), "report": args.report}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
