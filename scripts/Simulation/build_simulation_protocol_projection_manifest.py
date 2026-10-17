#!/usr/bin/env python3
"""Build projection manifest/QA/index for Simulation.simulation_protocol_pattern."""

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
from simulation_surface_registry import (
    SIMULATION_PROTOCOL_CANDIDATES_PATH,
    SIMULATION_PROTOCOL_CONTROLLED_LABEL_INDEX_PATH,
    SIMULATION_PROTOCOL_PATTERN_PATH,
    SIMULATION_PROTOCOL_PROJECTION_MANIFEST_PATH,
    SIMULATION_PROTOCOL_PROJECTION_QA_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_INPUT = SIMULATION_PROTOCOL_PATTERN_PATH
DEFAULT_CANDIDATES = SIMULATION_PROTOCOL_CANDIDATES_PATH
DEFAULT_MANIFEST = SIMULATION_PROTOCOL_PROJECTION_MANIFEST_PATH
DEFAULT_QA = SIMULATION_PROTOCOL_PROJECTION_QA_PATH
DEFAULT_INDEX = SIMULATION_PROTOCOL_CONTROLLED_LABEL_INDEX_PATH

VALID_MODES = {
    "patient_simulator",
    "therapist_simulator",
    "dual_session_simulator",
    "simulation_reviewer",
}
VALID_FAMILIES = {
    "context_loading",
    "patient_state_visibility",
    "therapist_planning_visibility",
    "hidden_state_update",
    "memory_write",
    "memory_read",
    "retrieval_routing",
    "turn_management",
    "escalation_and_handoff",
    "review_and_labeling",
}
CORE_FAMILIES = {
    "context_loading",
    "hidden_state_update",
    "retrieval_routing",
    "turn_management",
    "escalation_and_handoff",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    return parser.parse_args()

def protocol_content(row: dict[str, Any]) -> dict[str, Any]:
    return ((row.get("content") or {}).get("simulation_protocol_pattern")) or {}


def count_list_values(counter: Counter[str], values: Any) -> None:
    if isinstance(values, list):
        for value in values:
            label = str(value or "").strip() or "unknown"
            counter[label] += 1
        return
    label = str(values or "").strip() or "unknown"
    counter[label] += 1


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input) if Path(args.input).exists() else []
    candidates = read_jsonl(args.candidates) if Path(args.candidates).exists() else []

    candidate_ids_expected = {
        str(row.get("candidate_id") or "")
        for row in candidates
        if str(row.get("candidate_id") or "")
    }
    candidate_ids_seen = set()

    mode_distribution = Counter()
    family_distribution = Counter()
    runtime_surface_distribution = Counter()
    missing_counts = Counter()
    issues: list[dict[str, Any]] = []

    for row in rows:
        source = row.get("source") or {}
        provenance = row.get("provenance") or {}
        content = protocol_content(row)
        unit_id = row.get("unit_id")
        candidate_id = str(source.get("candidate_id") or provenance.get("candidate_id") or "")
        if candidate_id:
            candidate_ids_seen.add(candidate_id)

        simulation_mode = str(content.get("simulation_mode") or "")
        protocol_family = str(content.get("protocol_family") or "")
        mode_distribution[simulation_mode or "unknown"] += 1
        family_distribution[protocol_family or "unknown"] += 1
        for value in provenance.get("applicable_runtime_surface") or []:
            runtime_surface_distribution[str(value or "").strip() or "unknown"] += 1

        if simulation_mode not in VALID_MODES:
            issues.append({"unit_id": unit_id, "issue": "invalid_simulation_mode", "value": simulation_mode})
        if protocol_family not in VALID_FAMILIES:
            issues.append({"unit_id": unit_id, "issue": "invalid_protocol_family", "value": protocol_family})
        if not content.get("required_input_slots"):
            missing_counts["required_input_slots"] += 1
        if not content.get("non_goals_or_do_not_infer"):
            missing_counts["non_goals_or_do_not_infer"] += 1
        if not content.get("evidence_unit_ids"):
            missing_counts["evidence_unit_ids"] += 1
            issues.append({"unit_id": unit_id, "issue": "missing_evidence_unit_ids"})
        if not provenance.get("engineered_from_versions"):
            missing_counts["engineered_from_versions"] += 1
        if not content.get("retrieval_trigger_rules"):
            missing_counts["retrieval_trigger_rules"] += 1
        if not content.get("turn_termination_rules"):
            missing_counts["turn_termination_rules"] += 1
        if not content.get("risk_escalation_rules"):
            missing_counts["risk_escalation_rules"] += 1

    missing_candidate_ids = sorted(candidate_ids_expected - candidate_ids_seen)
    full_coverage = bool(candidate_ids_expected) and candidate_ids_seen == candidate_ids_expected
    missing_core_families = sorted(CORE_FAMILIES - set(family_distribution))

    index = {
        "schema_version": "theraskill_controlled_label_index_v1",
        "knowledge_type": "simulation_protocol_pattern",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "simulation_mode_distribution": dict(mode_distribution.most_common()),
        "protocol_family_distribution": dict(family_distribution.most_common()),
        "runtime_surface_distribution": dict(runtime_surface_distribution.most_common()),
    }
    write_json(args.index, index)

    manifest = {
        "schema_version": "theraskill_simulation_protocol_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if rows and not issues and full_coverage and not missing_core_families else "partial",
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
            "full_coverage": full_coverage,
        },
        "qa_report": str(args.qa_report),
    }
    write_json(args.manifest, manifest)

    qa = {
        "schema_version": "theraskill_simulation_protocol_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "input_candidate_count": len(candidates),
        "output_unit_count": len(rows),
        "missing_field_counts": dict(missing_counts),
        "candidate_coverage": manifest["candidate_coverage"],
        "simulation_mode_distribution": dict(mode_distribution.most_common()),
        "protocol_family_distribution": dict(family_distribution.most_common()),
        "runtime_surface_distribution": dict(runtime_surface_distribution.most_common()),
        "missing_core_families": missing_core_families,
        "missing_candidate_ids_top50": missing_candidate_ids[:50],
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(args.qa_report, qa)
    print(json.dumps({"status": qa["status"], "output_unit_count": len(rows), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
