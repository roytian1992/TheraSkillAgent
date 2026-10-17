#!/usr/bin/env python3
"""Build exact duplicate groups from TheraSkill dedup text views."""

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
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/indexes/exact_hash_index"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/exact_duplicate_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/exact_duplicate_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/exact_duplicate_qa.json"

INDEXED_FAMILIES = {
    "style_response",
    "qa_psychoeducation",
    "risk_assessment",
    "therapy_process",
    "method_rubric",
    "synthetic_construction",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_counts(input_path: Path) -> tuple[Counter, dict[str, Any], list[dict[str, Any]]]:
    hash_counts: Counter[tuple[str, str]] = Counter()
    stats: dict[str, Any] = {
        "input_rows": 0,
        "indexed_rows": 0,
        "skipped_rows": 0,
        "parse_errors": 0,
        "missing_hash": 0,
        "indexed_family_distribution": Counter(),
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
            family = row.get("dedup_family") or "unknown"
            if family not in INDEXED_FAMILIES:
                stats["skipped_rows"] += 1
                stats["skipped_family_distribution"][family] += 1
                continue
            text_hash = row.get("dedup_text_sha256")
            if not text_hash:
                stats["missing_hash"] += 1
                issues.append({"issue": "missing_hash", "line": line_no, "unit_id": row.get("unit_id")})
                continue
            stats["indexed_rows"] += 1
            stats["indexed_family_distribution"][family] += 1
            hash_counts[(family, text_hash)] += 1
    return hash_counts, stats, issues


def build_groups(input_path: Path, output_dir: Path, duplicate_keys: set[tuple[str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with input_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            family = row.get("dedup_family") or "unknown"
            key = (family, row.get("dedup_text_sha256"))
            if key not in duplicate_keys:
                continue
            grouped[key].append(
                {
                    "unit_id": row.get("unit_id"),
                    "dataset": row.get("dataset"),
                    "knowledge_type": row.get("knowledge_type"),
                    "raw_file": row.get("raw_file"),
                    "raw_line_no": row.get("raw_line_no"),
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    handles = {}
    output_hashes = {}
    group_counts = Counter()
    duplicate_members = Counter()
    cross_dataset_groups = Counter()
    largest_groups: list[dict[str, Any]] = []

    try:
        for (family, text_hash), members in sorted(grouped.items()):
            if family not in handles:
                path = output_dir / f"{family}.exact_hash.jsonl"
                handles[family] = path.open("w", encoding="utf-8")
                output_hashes[family] = hashlib.sha256()
            dataset_distribution = dict(Counter(str(item["dataset"]) for item in members).most_common())
            knowledge_type_distribution = dict(Counter(str(item["knowledge_type"]) for item in members).most_common())
            record = {
                "schema_version": "theraskill_exact_duplicate_group_v1",
                "exact_group_id": f"edg_{family}_{text_hash[:16]}",
                "dedup_family": family,
                "dedup_text_sha256": text_hash,
                "member_count": len(members),
                "member_unit_ids": [item["unit_id"] for item in members],
                "dataset_distribution": dataset_distribution,
                "knowledge_type_distribution": knowledge_type_distribution,
                "raw_refs": members,
            }
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            handles[family].write(encoded)
            output_hashes[family].update(encoded.encode("utf-8"))
            group_counts[family] += 1
            duplicate_members[family] += len(members)
            if len(dataset_distribution) > 1:
                cross_dataset_groups[family] += 1
            largest_groups.append(
                {
                    "exact_group_id": record["exact_group_id"],
                    "dedup_family": family,
                    "dedup_text_sha256": text_hash,
                    "member_count": len(members),
                    "dataset_distribution": dataset_distribution,
                    "knowledge_type_distribution": knowledge_type_distribution,
                }
            )
    finally:
        for handle in handles.values():
            handle.close()

    outputs = {}
    for family in sorted(handles):
        path = output_dir / f"{family}.exact_hash.jsonl"
        outputs[family] = {
            "path": str(path),
            "groups": group_counts[family],
            "duplicate_members": duplicate_members[family],
            "cross_dataset_groups": cross_dataset_groups[family],
            "sha256": output_hashes[family].hexdigest(),
            "file_sha256": file_sha256(path),
        }

    aggregate = {
        "duplicate_group_count": sum(group_counts.values()),
        "duplicate_group_count_by_family": dict(group_counts.most_common()),
        "duplicate_member_count": sum(duplicate_members.values()),
        "duplicate_member_count_by_family": dict(duplicate_members.most_common()),
        "cross_dataset_group_count": sum(cross_dataset_groups.values()),
        "cross_dataset_group_count_by_family": dict(cross_dataset_groups.most_common()),
    }
    largest_groups = sorted(largest_groups, key=lambda item: item["member_count"], reverse=True)[:50]
    return {"outputs": outputs, "aggregate": aggregate}, largest_groups


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"output dir is not empty: {output_dir}; pass --overwrite")
    if args.overwrite and output_dir.exists():
        for path in output_dir.glob("*.exact_hash.jsonl"):
            path.unlink()

    hash_counts, stats, issues = load_counts(input_path)
    duplicate_keys = {key for key, count in hash_counts.items() if count > 1}
    singletons_by_family = Counter()
    for (family, _), count in hash_counts.items():
        if count == 1:
            singletons_by_family[family] += 1

    group_payload, largest_groups = build_groups(input_path, output_dir, duplicate_keys)
    stats["indexed_family_distribution"] = dict(stats["indexed_family_distribution"].most_common())
    stats["skipped_family_distribution"] = dict(stats["skipped_family_distribution"].most_common())
    stats["singleton_hash_count_by_family"] = dict(singletons_by_family.most_common())

    if stats["parse_errors"]:
        issues.append({"issue": "parse_errors_present", "count": stats["parse_errors"]})
    if stats["missing_hash"]:
        issues.append({"issue": "missing_hash_present", "count": stats["missing_hash"]})
    status = "complete" if not issues else "incomplete"

    report = {
        "schema_version": "theraskill_exact_duplicate_report_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": {"path": str(input_path), "sha256": file_sha256(input_path)},
        "stats": stats,
        **group_payload,
        "largest_groups_top50": largest_groups,
        "excluded_families_policy": {
            "source_inventory": "not content-merged in this phase",
            "source_route_audit": "governance record; not content-merged in this phase",
        },
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
    }
    manifest = {
        "schema_version": "theraskill_exact_duplicate_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": report["input"],
        "output_dir": str(output_dir),
        "outputs": group_payload["outputs"],
        "aggregate": group_payload["aggregate"],
        "stats": stats,
        "report": args.report,
        "qa_report": args.qa_report,
        "issues_count": len(issues),
    }
    qa = {
        "schema_version": "theraskill_exact_duplicate_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "stats": stats,
        "aggregate": group_payload["aggregate"],
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
    }
    write_json(args.report, report, sort_keys=False)
    write_json(args.manifest, manifest, sort_keys=False)
    write_json(args.qa_report, qa, sort_keys=False)
    print(
        json.dumps(
            {
                "status": status,
                "issues_count": len(issues),
                "stats": stats,
                "aggregate": group_payload["aggregate"],
                "report": args.report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
