#!/usr/bin/env python3
"""Build a cluster-level adjudication queue for refined style-response components."""

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

from io_utils import file_sha256, write_json  # noqa: E402

DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_V2_INPUT = "outputs/knowledge/dedup/graphs/style_response_v2_refinement/style_response.v2_refinement.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/adjudication_queues/style_response"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/style_response_adjudication_queue_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/style_response_adjudication_queue_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/style_response_adjudication_queue_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--v2-input", default=DEFAULT_V2_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--max-adjudication-size", type=int, default=200)
    parser.add_argument("--evidence-per-cluster", type=int, default=8)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def stable_hash(payload: Any, length: int = 20) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:length]


def counter_dict(counter: Counter, limit: int | None = None) -> dict[str, int]:
    return {str(k): int(v) for k, v in counter.most_common(limit)}


def load_needed_ids(v2_path: Path) -> tuple[set[str], dict[str, Any], list[dict[str, Any]]]:
    ids: set[str] = set()
    stats = {"v2_parent_rows": 0, "v2_subcomponents": 0, "member_refs": 0, "parse_errors": 0}
    issues: list[dict[str, Any]] = []
    with v2_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["v2_parent_rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:
                stats["parse_errors"] += 1
                issues.append({"issue": "v2_parse_error", "line": line_no, "error": str(exc)})
                continue
            for sub in row.get("v2_subcomponents") or []:
                stats["v2_subcomponents"] += 1
                for unit_id in sub.get("member_unit_ids") or []:
                    ids.add(str(unit_id))
                    stats["member_refs"] += 1
    return ids, stats, issues


def load_text_rows(path: Path, needed_ids: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    stats = {"scanned_rows": 0, "loaded_rows": 0, "parse_errors": 0}
    issues: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["scanned_rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:
                stats["parse_errors"] += 1
                issues.append({"issue": "text_view_parse_error", "line": line_no, "error": str(exc)})
                continue
            unit_id = row.get("unit_id")
            if unit_id in needed_ids:
                rows[str(unit_id)] = row
                stats["loaded_rows"] += 1
    missing = len(needed_ids - set(rows))
    if missing:
        issues.append({"issue": "missing_text_view_rows", "count": missing})
    return rows, stats, issues


def text_quality(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    context = str(fields.get("context") or "")
    response = str(fields.get("response") or "")
    dedup_text = str(row.get("dedup_text") or "")
    return {
        "has_context": bool(context.strip()),
        "has_response": bool(response.strip()),
        "response_char_length": len(response),
        "dedup_text_char_length": len(dedup_text),
        "response": response,
        "context": context,
        "dedup_text": dedup_text,
    }


def select_evidence(member_ids: list[str], text_rows: dict[str, dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored = []
    for unit_id in member_ids:
        row = text_rows.get(unit_id) or {}
        quality = text_quality(row)
        score = (
            1 if quality["has_response"] else 0,
            min(quality["response_char_length"], 600),
            unit_id,
        )
        scored.append((score, unit_id, row, quality))
    scored.sort(key=lambda item: (-item[0][0], -item[0][1], item[0][2]))
    evidence = []
    for _score, unit_id, row, quality in scored[:limit]:
        evidence.append({
            "unit_id": unit_id,
            "dataset": row.get("dataset"),
            "knowledge_type": row.get("knowledge_type"),
            "context_preview": quality["context"][:240],
            "response_preview": quality["response"][:700],
            "dedup_text_preview": quality["dedup_text"][:900],
            "dedup_text_sha256": row.get("dedup_text_sha256"),
        })
    return evidence


def defer_reason(sub: dict[str, Any], text_rows: dict[str, dict[str, Any]], max_size: int) -> str | None:
    member_count = int(sub.get("member_count") or 0)
    if member_count <= 1:
        return "singleton_no_adjudication_needed"
    if member_count > max_size:
        return "oversized_after_v2_defer"
    member_ids = [str(unit_id) for unit_id in sub.get("member_unit_ids") or []]
    qualities = [text_quality(text_rows.get(unit_id) or {}) for unit_id in member_ids]
    response_count = sum(1 for item in qualities if item["has_response"])
    if response_count == 0:
        return "empty_response_text_view_issue"
    key = sub.get("v2_refinement_key") or {}
    if key.get("primary_action_topic") == "general_support" and key.get("response_length_bucket") == "very_short" and response_count < max(2, member_count // 4):
        return "generic_support_insufficient_text"
    return None


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    queue_path = output_dir / "style_response.adjudication_queue.jsonl"
    deferred_path = output_dir / "style_response.deferred_components.jsonl"
    singleton_path = output_dir / "style_response.singleton_components.jsonl"
    if output_dir.exists() and any(output_dir.glob("*.jsonl")) and not args.overwrite:
        raise FileExistsError(f"output dir has jsonl files: {output_dir}; pass --overwrite")
    if args.overwrite and output_dir.exists():
        for path in output_dir.glob("*.jsonl"):
            path.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)

    needed_ids, v2_scan_stats, issues = load_needed_ids(Path(args.v2_input))
    text_rows, text_stats, text_issues = load_text_rows(Path(args.text_view), needed_ids)
    issues.extend(text_issues)

    counters = {
        "v2_parent_rows": 0,
        "v2_subcomponents": 0,
        "member_refs": 0,
        "queue_rows": 0,
        "queue_member_count": 0,
        "deferred_rows": 0,
        "deferred_member_count": 0,
        "singleton_rows": 0,
        "singleton_member_count": 0,
    }
    queue_by_topic = Counter()
    deferred_by_reason = Counter()
    deferred_by_topic = Counter()
    queue_by_size_bucket = Counter()

    with Path(args.v2_input).open("r", encoding="utf-8") as source, \
        queue_path.open("w", encoding="utf-8") as queue_out, \
        deferred_path.open("w", encoding="utf-8") as deferred_out, \
        singleton_path.open("w", encoding="utf-8") as singleton_out:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            counters["v2_parent_rows"] += 1
            row = json.loads(line)
            for sub in row.get("v2_subcomponents") or []:
                counters["v2_subcomponents"] += 1
                member_ids = [str(unit_id) for unit_id in sub.get("member_unit_ids") or []]
                member_count = len(member_ids)
                counters["member_refs"] += member_count
                reason = defer_reason(sub, text_rows, args.max_adjudication_size)
                topic = (sub.get("v2_refinement_key") or {}).get("primary_action_topic") or "unknown"
                if reason == "singleton_no_adjudication_needed":
                    singleton_record = {
                        "schema_version": "theraskill_style_singleton_component_v1",
                        "v2_subcomponent_id": sub.get("v2_subcomponent_id"),
                        "member_count": member_count,
                        "member_unit_ids": member_ids,
                        "v2_refinement_key": sub.get("v2_refinement_key"),
                        "not_final_merged_unit": True,
                    }
                    singleton_out.write(json.dumps(singleton_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                    counters["singleton_rows"] += 1
                    counters["singleton_member_count"] += member_count
                    continue
                if reason is not None:
                    deferred_record = {
                        "schema_version": "theraskill_style_deferred_component_v1",
                        "v2_subcomponent_id": sub.get("v2_subcomponent_id"),
                        "v1_subcomponent_id": sub.get("v1_subcomponent_id"),
                        "parent_component_id": sub.get("parent_component_id"),
                        "member_count": member_count,
                        "member_unit_ids": member_ids,
                        "v2_refinement_key": sub.get("v2_refinement_key"),
                        "defer_reason": reason,
                        "evidence_examples": select_evidence(member_ids, text_rows, args.evidence_per_cluster),
                        "not_final_merged_unit": True,
                        "raw_units_unchanged": True,
                    }
                    deferred_out.write(json.dumps(deferred_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                    counters["deferred_rows"] += 1
                    counters["deferred_member_count"] += member_count
                    deferred_by_reason[reason] += 1
                    deferred_by_topic[topic] += 1
                    continue

                if member_count <= 5:
                    size_bucket = "2_5"
                elif member_count <= 20:
                    size_bucket = "6_20"
                elif member_count <= 50:
                    size_bucket = "21_50"
                else:
                    size_bucket = "51_200"
                queue_id = "style_adj_" + stable_hash([sub.get("v2_subcomponent_id"), member_ids], 20)
                queue_record = {
                    "schema_version": "theraskill_style_adjudication_queue_item_v1",
                    "queue_item_id": queue_id,
                    "candidate_status": "queued_for_cluster_level_llm_adjudication",
                    "v2_subcomponent_id": sub.get("v2_subcomponent_id"),
                    "v1_subcomponent_id": sub.get("v1_subcomponent_id"),
                    "parent_component_id": sub.get("parent_component_id"),
                    "member_count": member_count,
                    "member_unit_ids": member_ids,
                    "v2_refinement_key": sub.get("v2_refinement_key"),
                    "size_bucket": size_bucket,
                    "evidence_examples": select_evidence(member_ids, text_rows, args.evidence_per_cluster),
                    "adjudication_instruction": {
                        "task": "Decide whether this refined style-response component is a true near-duplicate/style-equivalent cluster, a generic pattern that should be deferred, or a split-needed group.",
                        "not_allowed": "Do not create final merged knowledge here; this queue item is only for cluster-level adjudication.",
                    },
                    "not_final_merged_unit": True,
                    "raw_units_unchanged": True,
                }
                queue_out.write(json.dumps(queue_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                counters["queue_rows"] += 1
                counters["queue_member_count"] += member_count
                queue_by_topic[topic] += 1
                queue_by_size_bucket[size_bucket] += 1

    status = "complete" if not issues else "incomplete"
    aggregate = {
        **counters,
        "queue_by_primary_topic": counter_dict(queue_by_topic),
        "queue_by_size_bucket": counter_dict(queue_by_size_bucket),
        "deferred_by_reason": counter_dict(deferred_by_reason),
        "deferred_by_primary_topic": counter_dict(deferred_by_topic),
    }
    outputs = {
        "queue": {"path": str(queue_path), "sha256": file_sha256(queue_path)},
        "deferred": {"path": str(deferred_path), "sha256": file_sha256(deferred_path)},
        "singleton": {"path": str(singleton_path), "sha256": file_sha256(singleton_path)},
    }
    report = {
        "schema_version": "theraskill_style_adjudication_queue_report_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": {
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view)},
            "v2_input": {"path": args.v2_input, "sha256": file_sha256(args.v2_input)},
        },
        "outputs": outputs,
        "parameters": {
            "max_adjudication_size": args.max_adjudication_size,
            "evidence_per_cluster": args.evidence_per_cluster,
        },
        "v2_scan_stats": v2_scan_stats,
        "text_view_stats": text_stats,
        "aggregate": aggregate,
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    manifest = {
        "schema_version": "theraskill_style_adjudication_queue_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": report["inputs"],
        "outputs": outputs,
        "aggregate": aggregate,
        "report": args.report,
        "qa_report": args.qa_report,
        "issues_count": len(issues),
        "not_final_merge_release": True,
    }
    qa = {
        "schema_version": "theraskill_style_adjudication_queue_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "aggregate": aggregate,
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    write_json(args.report, report, sort_keys=False)
    write_json(args.manifest, manifest, sort_keys=False)
    write_json(args.qa_report, qa, sort_keys=False)
    print(json.dumps({"status": status, "issues_count": len(issues), "aggregate": aggregate, "outputs": outputs}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
