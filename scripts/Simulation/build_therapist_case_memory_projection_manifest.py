#!/usr/bin/env python3
"""Build manifest/QA for Simulation.therapist_case_memory_pattern outputs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import file_sha256, read_jsonl, write_json
from simulation_surface_registry import SIMULATION_EXTRACTION_FAMILY_SPECS

DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["therapist_case_memory_pattern"]
DEFAULT_INPUT = DEFAULT_SPEC.canonical_output
DEFAULT_CANDIDATES = DEFAULT_SPEC.candidates_jsonl
DEFAULT_MANIFEST = DEFAULT_SPEC.projection_manifest
DEFAULT_QA = DEFAULT_SPEC.projection_qa
DEFAULT_INDEX = DEFAULT_SPEC.controlled_label_index

TRACKED_FIELDS = [
    "required_memory_slots",
    "problem_summary_slots",
    "resource_slots",
    "goal_slots",
    "alliance_slots",
    "risk_slots",
    "stage_tracking_slots",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    return parser.parse_args()

def pattern_content(row: dict[str, Any]) -> dict[str, Any]:
    return ((row.get("content") or {}).get("therapist_case_memory_pattern")) or {}


def count_list_values(counter: Counter[str], values: Any) -> None:
    if isinstance(values, list):
        for value in values:
            label = str(value or "").strip() or "unknown"
            counter[label] += 1
    else:
        label = str(values or "").strip() or "unknown"
        counter[label] += 1


def has_think_leak(row: dict[str, Any]) -> bool:
    text = json.dumps(pattern_content(row), ensure_ascii=False)
    return "<think>" in text or "</think>" in text


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input) if Path(args.input).exists() else []
    candidates = read_jsonl(args.candidates) if Path(args.candidates).exists() else []

    field_distributions: dict[str, Counter[str]] = {field: Counter() for field in TRACKED_FIELDS}
    missing_counts = Counter()
    issues: list[dict[str, Any]] = []
    candidate_ids_seen = set()
    candidate_ids_expected = {
        str(row.get("candidate_id") or "")
        for row in candidates
        if str(row.get("candidate_id") or "")
    }

    for row in rows:
        source = row.get("source") or {}
        content = pattern_content(row)
        unit_id = row.get("unit_id")
        candidate_id = str(source.get("candidate_id") or "")
        if candidate_id:
            candidate_ids_seen.add(candidate_id)

        if not source.get("source_unit_ids"):
            issues.append({"unit_id": unit_id, "issue": "missing_source_unit_ids"})
        if has_think_leak(row):
            issues.append({"unit_id": unit_id, "issue": "think_leak_detected"})

        for key in ("memory_update_rules", "compression_rules", "required_memory_slots", "evidence_spans"):
            if not content.get(key):
                missing_counts[key] += 1

        for field in TRACKED_FIELDS:
            count_list_values(field_distributions[field], content.get(field))

    missing_candidate_ids = sorted(candidate_ids_expected - candidate_ids_seen)
    is_full_coverage = bool(candidate_ids_expected) and candidate_ids_seen == candidate_ids_expected

    index = {
        "schema_version": "theraskill_controlled_label_index_v1",
        "knowledge_type": "therapist_case_memory_pattern",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "field_distributions": {field: dict(counter.most_common()) for field, counter in field_distributions.items()},
    }
    write_json(args.index, index)

    manifest = {
        "schema_version": "theraskill_therapist_case_memory_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if rows and not issues and is_full_coverage else "partial",
        "input_candidates": {
            "path": str(args.candidates),
            "sha256": file_sha256(args.candidates),
            "row_count": len(candidates),
        },
        "output_units": {
            "path": str(args.input),
            "sha256": file_sha256(args.input),
            "row_count": len(rows),
        },
        "controlled_label_index": {"path": str(args.index), "sha256": file_sha256(args.index)},
        "candidate_coverage": {
            "expected_candidate_count": len(candidate_ids_expected),
            "observed_candidate_count": len(candidate_ids_seen),
            "missing_candidate_count": len(missing_candidate_ids),
            "coverage_ratio": (len(candidate_ids_seen) / len(candidate_ids_expected)) if candidate_ids_expected else 0.0,
            "full_coverage": is_full_coverage,
        },
        "qa_report": str(args.qa_report),
    }
    write_json(args.manifest, manifest)

    qa = {
        "schema_version": "theraskill_therapist_case_memory_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "input_candidate_count": len(candidates),
        "output_unit_count": len(rows),
        "missing_field_counts": dict(missing_counts),
        "candidate_coverage": manifest["candidate_coverage"],
        "tracked_field_distribution": {field: dict(counter.most_common()) for field, counter in field_distributions.items()},
        "think_leak_count": sum(1 for issue in issues if issue["issue"] == "think_leak_detected"),
        "source_unit_ids_missing_count": sum(1 for issue in issues if issue["issue"] == "missing_source_unit_ids"),
        "missing_candidate_ids_top50": missing_candidate_ids[:50],
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(args.qa_report, qa)
    print(json.dumps({"status": qa["status"], "output_unit_count": len(rows), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
