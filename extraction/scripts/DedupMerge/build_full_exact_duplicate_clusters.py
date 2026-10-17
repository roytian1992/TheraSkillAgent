#!/usr/bin/env python3
"""Build full exact duplicate clusters from the dedup text view.

This script is deterministic and full-coverage over comparable raw knowledge
families. It produces clusters only for exact normalized text duplicates; it
does not perform semantic merge, sampling, or LLM adjudication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, write_json  # noqa: E402


DEFAULT_INPUT = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/clusters/exact_duplicates"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/full_exact_duplicate_clusters_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/full_exact_duplicate_clusters_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/full_exact_duplicate_clusters_qa.json"

INDEXED_FAMILIES = {
    "style_response",
    "qa_psychoeducation",
    "risk_assessment",
    "therapy_process",
    "method_rubric",
    "synthetic_construction",
}

FAMILY_ALIASES = {"risk_assessment": "clinical_risk_assessment"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def inc(counter: Counter, value: Any) -> None:
    if value is None or value == "":
        counter["__missing__"] += 1
    else:
        counter[str(value)] += 1


def cluster_policy(family: str) -> dict[str, Any]:
    if family == "risk_assessment":
        return {
            "content_category": "clinical_risk_assessment",
            "exact_duplicate_cluster_allowed": True,
            "final_merge_requires": ["clinical_label_consistency_review"],
            "note": "This is clinical risk assessment knowledge, not a data-quality prohibition. Data-quality concerns must be named quality_consistency_issue.",
        }
    if family == "therapy_process":
        return {
            "content_category": family,
            "exact_duplicate_cluster_allowed": True,
            "final_merge_requires": ["process_structure_consistency_review", "no_generic_no_sequence_filter"],
        }
    if family in {"style_response", "qa_psychoeducation"}:
        return {
            "content_category": family,
            "exact_duplicate_cluster_allowed": True,
            "final_merge_requires": ["license_and_source_constraint_check", "full_candidate_graph_coverage_before_release"],
        }
    return {
        "content_category": family,
        "exact_duplicate_cluster_allowed": True,
        "final_merge_requires": ["source_provenance_preservation"],
    }


def first_pass(input_path: Path) -> tuple[Counter, dict[str, Any], list[dict[str, Any]]]:
    key_counts: Counter[tuple[str, str]] = Counter()
    stats: dict[str, Any] = {
        "input_rows": 0,
        "comparable_rows": 0,
        "skipped_rows": 0,
        "parse_errors": 0,
        "missing_hash": 0,
        "comparable_family_distribution": Counter(),
        "skipped_family_distribution": Counter(),
    }
    issues: list[dict[str, Any]] = []
    with input_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["input_rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:
                stats["parse_errors"] += 1
                issues.append({"issue": "parse_error", "line": line_no, "error": str(exc)})
                continue
            family = str(row.get("dedup_family") or "__missing_family__")
            if family not in INDEXED_FAMILIES:
                stats["skipped_rows"] += 1
                stats["skipped_family_distribution"][family] += 1
                continue
            text_hash = row.get("dedup_text_sha256")
            if not text_hash:
                stats["missing_hash"] += 1
                issues.append({"issue": "missing_hash", "line": line_no, "unit_id": row.get("unit_id"), "dedup_family": family})
                continue
            stats["comparable_rows"] += 1
            stats["comparable_family_distribution"][family] += 1
            key_counts[(family, str(text_hash))] += 1
    return key_counts, stats, issues


def second_pass(input_path: Path, duplicate_keys: set[tuple[str, str]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with input_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            family = str(row.get("dedup_family") or "__missing_family__")
            key = (family, str(row.get("dedup_text_sha256") or ""))
            if key not in duplicate_keys:
                continue
            grouped[key].append(
                {
                    "unit_id": row.get("unit_id"),
                    "dataset": row.get("dataset"),
                    "knowledge_type": row.get("knowledge_type"),
                    "language": row.get("language"),
                    "evidence_tier": row.get("evidence_tier"),
                    "source_construction_type": row.get("source_construction_type"),
                    "raw_file": row.get("raw_file"),
                    "raw_line_no": row.get("raw_line_no"),
                    "text_view_line_no": line_no,
                }
            )
    return grouped


def build_outputs(grouped: dict[tuple[str, str], list[dict[str, Any]]], output_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    handles: dict[str, Any] = {}
    group_counts: Counter[str] = Counter()
    member_counts: Counter[str] = Counter()
    cross_dataset_counts: Counter[str] = Counter()
    largest: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    try:
        for (family, text_hash), members in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
            members = sorted(members, key=lambda item: (str(item.get("dataset")), str(item.get("unit_id")), int(item.get("raw_line_no") or 0)))
            if family not in handles:
                handles[family] = (output_dir / f"{family}.exact_duplicate_clusters.jsonl").open("w", encoding="utf-8")
            dataset_counter: Counter[str] = Counter()
            knowledge_counter: Counter[str] = Counter()
            raw_file_counter: Counter[str] = Counter()
            evidence_counter: Counter[str] = Counter()
            construction_counter: Counter[str] = Counter()
            language_counter: Counter[str] = Counter()
            for member in members:
                inc(dataset_counter, member.get("dataset"))
                inc(knowledge_counter, member.get("knowledge_type"))
                inc(raw_file_counter, member.get("raw_file"))
                inc(evidence_counter, member.get("evidence_tier"))
                inc(construction_counter, member.get("source_construction_type"))
                inc(language_counter, member.get("language"))

            cluster_id = f"exact_{family}_{text_hash[:16]}"
            record = {
                "schema_version": "theraskill_exact_duplicate_cluster_v1",
                "cluster_id": cluster_id,
                "dedup_family": family,
                "display_family": f"{family} (alias: {FAMILY_ALIASES[family]})" if family in FAMILY_ALIASES else family,
                "dedup_text_sha256": text_hash,
                "member_count": len(members),
                "representative_unit_id": members[0].get("unit_id"),
                "member_unit_ids": [member.get("unit_id") for member in members],
                "dataset_distribution": dict(dataset_counter.most_common()),
                "knowledge_type_distribution": dict(knowledge_counter.most_common()),
                "language_distribution": dict(language_counter.most_common()),
                "evidence_tier_distribution": dict(evidence_counter.most_common()),
                "source_construction_type_distribution": dict(construction_counter.most_common()),
                "raw_file_distribution": dict(raw_file_counter.most_common()),
                "raw_refs": members,
                "cluster_policy": cluster_policy(family),
                "raw_units_unchanged": True,
                "not_final_merged_unit": True,
            }
            handles[family].write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            group_counts[family] += 1
            member_counts[family] += len(members)
            if len(dataset_counter) > 1:
                cross_dataset_counts[family] += 1
            largest.append({
                "cluster_id": cluster_id,
                "dedup_family": family,
                "display_family": record["display_family"],
                "member_count": len(members),
                "dataset_distribution": dict(dataset_counter.most_common()),
                "knowledge_type_distribution": dict(knowledge_counter.most_common()),
            })
    finally:
        for handle in handles.values():
            handle.close()

    outputs = {}
    for family in sorted(handles):
        path = output_dir / f"{family}.exact_duplicate_clusters.jsonl"
        outputs[family] = {
            "path": str(path),
            "clusters": group_counts[family],
            "duplicate_members": member_counts[family],
            "cross_dataset_clusters": cross_dataset_counts[family],
            "sha256": file_sha256(path),
        }

    aggregate = {
        "duplicate_cluster_count": sum(group_counts.values()),
        "duplicate_cluster_count_by_family": dict(group_counts.most_common()),
        "duplicate_member_count": sum(member_counts.values()),
        "duplicate_member_count_by_family": dict(member_counts.most_common()),
        "cross_dataset_cluster_count": sum(cross_dataset_counts.values()),
        "cross_dataset_cluster_count_by_family": dict(cross_dataset_counts.most_common()),
    }
    return {"outputs": outputs, "aggregate": aggregate}, sorted(largest, key=lambda item: item["member_count"], reverse=True)[:100], issues


def counters_to_dict(stats: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in stats.items():
        if isinstance(value, Counter):
            out[key] = dict(value.most_common())
        else:
            out[key] = value
    return out


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.glob("*.exact_duplicate_clusters.jsonl")) and not args.overwrite:
        raise FileExistsError(f"output dir has exact cluster files: {output_dir}; pass --overwrite")
    if args.overwrite and output_dir.exists():
        for path in output_dir.glob("*.exact_duplicate_clusters.jsonl"):
            path.unlink()

    key_counts, stats, issues = first_pass(input_path)
    duplicate_keys = {key for key, count in key_counts.items() if count > 1}
    singleton_counts = Counter()
    for (family, _text_hash), count in key_counts.items():
        if count == 1:
            singleton_counts[family] += 1
    grouped = second_pass(input_path, duplicate_keys)
    output_payload, largest, output_issues = build_outputs(grouped, output_dir)
    issues.extend(output_issues)
    stats["singleton_hash_count_by_family"] = singleton_counts
    status = "complete" if not issues else "incomplete"

    report = {
        "schema_version": "theraskill_full_exact_duplicate_clusters_report_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": {"path": str(input_path), "sha256": file_sha256(input_path)},
        "stats": counters_to_dict(stats),
        **output_payload,
        "largest_clusters_top100": largest,
        "terminology": {
            "risk_assessment": "legacy dedup_family name for clinical risk assessment / symptom severity knowledge; not a data-quality issue",
            "quality_consistency_issue": "use for schema, label, or data-quality problems",
        },
        "excluded_families_policy": {
            "source_inventory": "provenance inventory only; not content-merged",
            "source_route_audit": "governance/routing record only; not content-merged",
        },
        "issues_count": len(issues),
        "issues": issues[:500],
        "raw_units_unchanged": True,
        "not_final_merge_release": True,
    }
    manifest = {
        "schema_version": "theraskill_full_exact_duplicate_clusters_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": report["input"],
        "output_dir": str(output_dir),
        "outputs": output_payload["outputs"],
        "aggregate": output_payload["aggregate"],
        "report": args.report,
        "qa_report": args.qa_report,
        "issues_count": len(issues),
        "not_final_merge_release": True,
    }
    qa = {
        "schema_version": "theraskill_full_exact_duplicate_clusters_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "stats": counters_to_dict(stats),
        "aggregate": output_payload["aggregate"],
        "issues_count": len(issues),
        "issues": issues[:500],
        "raw_units_unchanged": True,
        "not_final_merge_release": True,
    }
    write_json(args.report, report, sort_keys=False)
    write_json(args.manifest, manifest, sort_keys=False)
    write_json(args.qa_report, qa, sort_keys=False)
    print(json.dumps({
        "status": status,
        "issues_count": len(issues),
        "stats": counters_to_dict(stats),
        "aggregate": output_payload["aggregate"],
        "report": args.report,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
