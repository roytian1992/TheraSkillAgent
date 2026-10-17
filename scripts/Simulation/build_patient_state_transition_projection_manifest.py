#!/usr/bin/env python3
"""Build manifest/QA/index for Simulation.patient_state_transition_pattern outputs."""

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

DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["patient_state_transition_pattern"]
DEFAULT_INPUT = DEFAULT_SPEC.canonical_output
DEFAULT_CANDIDATES = DEFAULT_SPEC.candidates_jsonl
DEFAULT_MANIFEST = DEFAULT_SPEC.projection_manifest
DEFAULT_QA = DEFAULT_SPEC.projection_qa
DEFAULT_INDEX = DEFAULT_SPEC.controlled_label_index

SHIFT_FIELDS = [
    "state_shift_type",
    "emotion_shift",
    "alliance_shift",
    "disclosure_shift",
    "resistance_shift",
    "readiness_shift",
    "risk_shift",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    return parser.parse_args()

def get_transition_content(row: dict[str, Any]) -> dict[str, Any]:
    return ((row.get("content") or {}).get("transition_pattern")) or {}


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input) if Path(args.input).exists() else []
    candidates = read_jsonl(args.candidates) if Path(args.candidates).exists() else []

    issues: list[dict[str, Any]] = []
    distributions: dict[str, Counter[str]] = {field: Counter() for field in SHIFT_FIELDS}
    missing_counts = Counter()
    candidate_ids_seen = set()
    candidate_ids_expected = {
        str(row.get("candidate_id") or "")
        for row in candidates
        if str(row.get("candidate_id") or "")
    }

    for row in rows:
        content = get_transition_content(row)
        source = row.get("source") or {}
        unit_id = row.get("unit_id")
        candidate_ids_seen.add(str(source.get("candidate_id") or ""))

        if not source.get("source_unit_ids"):
            issues.append({"unit_id": unit_id, "issue": "missing_source_unit_ids"})
        if not content.get("applicable_context"):
            missing_counts["applicable_context"] += 1
        if not content.get("non_applicable_context"):
            missing_counts["non_applicable_context"] += 1
        if not content.get("evidence_spans"):
            missing_counts["evidence_spans"] += 1
        if not content.get("confidence_note"):
            missing_counts["confidence_note"] += 1

        for field in SHIFT_FIELDS:
            value = str(content.get(field) or "unknown")
            distributions[field][value] += 1

    missing_candidate_ids = sorted(candidate_ids_expected - candidate_ids_seen)

    controlled_label_index = {
        "schema_version": "theraskill_controlled_label_index_v1",
        "knowledge_type": "patient_state_transition_pattern",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "field_distributions": {field: dict(counter.most_common()) for field, counter in distributions.items()},
    }
    write_json(args.index, controlled_label_index)

    is_full_coverage = bool(candidate_ids_expected) and candidate_ids_seen == candidate_ids_expected

    manifest = {
        "schema_version": "theraskill_patient_state_transition_projection_manifest_v1",
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
        "schema_version": "theraskill_patient_state_transition_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "input_candidate_count": len(candidates),
        "output_unit_count": len(rows),
        "missing_field_counts": dict(missing_counts),
        "candidate_coverage": manifest["candidate_coverage"],
        "controlled_label_distribution": {field: dict(counter.most_common()) for field, counter in distributions.items()},
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
