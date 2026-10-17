#!/usr/bin/env python3
"""Build manifest/QA/index for Simulation.therapist_response_policy_pattern outputs."""

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

DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["therapist_response_policy_pattern"]
DEFAULT_INPUT = DEFAULT_SPEC.canonical_output
DEFAULT_CANDIDATES = DEFAULT_SPEC.candidates_jsonl
DEFAULT_MANIFEST = DEFAULT_SPEC.projection_manifest
DEFAULT_QA = DEFAULT_SPEC.projection_qa
DEFAULT_INDEX = DEFAULT_SPEC.controlled_label_index

CONTROLLED_FIELDS = [
    "therapeutic_orientation",
    "stage_constraints",
    "alliance_constraints",
    "risk_constraints",
    "preferred_move_types",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    return parser.parse_args()

def get_policy_content(row: dict[str, Any]) -> dict[str, Any]:
    return ((row.get("content") or {}).get("response_policy_pattern")) or {}


def count_list_values(counter: Counter[str], values: Any) -> None:
    if isinstance(values, list):
        for value in values:
            label = str(value or "").strip() or "unknown"
            counter[label] += 1
    else:
        label = str(values or "").strip() or "unknown"
        counter[label] += 1


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input) if Path(args.input).exists() else []
    candidates = read_jsonl(args.candidates) if Path(args.candidates).exists() else []

    issues: list[dict[str, Any]] = []
    missing_counts = Counter()
    field_distributions: dict[str, Counter[str]] = {field: Counter() for field in CONTROLLED_FIELDS}
    source_family_distribution = Counter()
    candidate_ids_seen = set()
    candidate_ids_expected = {
        str(row.get("candidate_id") or "")
        for row in candidates
        if str(row.get("candidate_id") or "")
    }

    for row in rows:
        source = row.get("source") or {}
        content = get_policy_content(row)
        unit_id = row.get("unit_id")
        candidate_id = str(source.get("candidate_id") or "")
        if candidate_id:
            candidate_ids_seen.add(candidate_id)
        source_family_distribution[str(source.get("source_family") or "unknown")] += 1

        if not source.get("source_unit_ids"):
            issues.append({"unit_id": unit_id, "issue": "missing_source_unit_ids"})
        if not content.get("primary_response_goal"):
            missing_counts["primary_response_goal"] += 1
        if not content.get("preferred_move_types"):
            missing_counts["preferred_move_types"] += 1
        if not content.get("applicable_context"):
            missing_counts["applicable_context"] += 1
        if not content.get("evidence_spans"):
            missing_counts["evidence_spans"] += 1
        if not content.get("confidence_note"):
            missing_counts["confidence_note"] += 1

        for field in CONTROLLED_FIELDS:
            count_list_values(field_distributions[field], content.get(field))

    missing_candidate_ids = sorted(candidate_ids_expected - candidate_ids_seen)
    is_full_coverage = bool(candidate_ids_expected) and candidate_ids_seen == candidate_ids_expected

    controlled_label_index = {
        "schema_version": "theraskill_controlled_label_index_v1",
        "knowledge_type": "therapist_response_policy_pattern",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "field_distributions": {field: dict(counter.most_common()) for field, counter in field_distributions.items()},
        "source_family_distribution": dict(source_family_distribution.most_common()),
    }
    write_json(args.index, controlled_label_index)

    manifest = {
        "schema_version": "theraskill_therapist_response_policy_projection_manifest_v1",
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
        "schema_version": "theraskill_therapist_response_policy_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "input_candidate_count": len(candidates),
        "output_unit_count": len(rows),
        "missing_field_counts": dict(missing_counts),
        "candidate_coverage": manifest["candidate_coverage"],
        "controlled_label_distribution": {field: dict(counter.most_common()) for field, counter in field_distributions.items()},
        "source_family_distribution": dict(source_family_distribution.most_common()),
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
