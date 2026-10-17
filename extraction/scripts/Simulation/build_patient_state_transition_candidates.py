#!/usr/bin/env python3
"""Build deterministic candidate rows for Simulation.patient_state_transition_pattern."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, file_sha256, read_jsonl, write_json, write_jsonl
from simulation_surface_registry import (
    ANNA_REPORT_TO_CONVERSATION_BRIDGE_PATTERN_PATH,
    SIMULATION_ACTION_SEQUENCE_PATH,
    SIMULATION_CASE_TRAJECTORY_PATH,
    SIMULATION_MEMORY_BUNDLE_PATH,
    SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_MANIFEST_PATH,
    SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_PATH,
    SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_QA_PATH,
)


DEFAULT_CASE_TRAJECTORY = SIMULATION_CASE_TRAJECTORY_PATH
DEFAULT_ACTION_SEQUENCE = SIMULATION_ACTION_SEQUENCE_PATH
DEFAULT_MEMORY_BUNDLE = SIMULATION_MEMORY_BUNDLE_PATH
DEFAULT_ANNA_BRIDGE = ANNA_REPORT_TO_CONVERSATION_BRIDGE_PATTERN_PATH
DEFAULT_OUTPUT = SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_PATH
DEFAULT_MANIFEST = SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_MANIFEST_PATH
DEFAULT_QA = SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_QA_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-trajectory", default=str(DEFAULT_CASE_TRAJECTORY))
    parser.add_argument("--action-sequence", default=str(DEFAULT_ACTION_SEQUENCE))
    parser.add_argument("--memory-bundle", default=str(DEFAULT_MEMORY_BUNDLE))
    parser.add_argument("--anna-bridge", default=str(DEFAULT_ANNA_BRIDGE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit-cases", type=int)
    return parser.parse_args()

def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def sequence_index(action_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in action_rows:
        case_id = str((row.get("source") or {}).get("case_id") or "")
        sequences = (((row.get("content") or {}).get("action_sequence_pattern") or {}).get("action_sequences") or [])
        if case_id:
            index[case_id] = sequences
    return index


def anna_bridge_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        source = row.get("source") or {}
        case_id = str(source.get("case_id") or source.get("source_id") or "")
        if case_id:
            index.setdefault(case_id, []).append(row)
    return index


def map_stage_hint(item: dict[str, Any]) -> str:
    if item.get("phase"):
        return str(item.get("phase"))
    return "unspecified"


def choose_memory_groups(memory_rows: list[dict[str, Any]], *, stage_hint: str) -> list[dict[str, Any]]:
    matched = []
    for row in memory_rows:
        source = row.get("source") or {}
        primary_stage = str(source.get("primary_stage") or "")
        if stage_hint == "early" and primary_stage == "target":
            matched.append(row)
        elif stage_hint == "middle" and primary_stage == "working":
            matched.append(row)
        elif stage_hint == "late" and primary_stage in {"ending", "working"}:
            matched.append(row)
    if matched:
        return matched[:2]
    return memory_rows[:2]


def make_candidate(
    *,
    case_row: dict[str, Any],
    sequence: dict[str, Any],
    memory_rows: list[dict[str, Any]],
    anna_rows: list[dict[str, Any]],
    order: int,
) -> dict[str, Any]:
    source = case_row.get("source") or {}
    content = (case_row.get("content") or {}).get("case_trajectory") or {}
    applicable_context = sequence.get("applicable_context") or ""
    observed = sequence.get("observed_client_changes") or []
    start = sequence.get("starting_state") or {}
    stage_hint = "unspecified"
    if observed and isinstance(observed[0], dict):
        ev = observed[0].get("evidence_transition_ids") or []
        for timeline_name in ("alliance_timeline", "disclosure_timeline", "readiness_timeline", "risk_timeline"):
            for item in content.get(timeline_name) or []:
                if set(ev) & set(item.get("evidence_transition_ids") or []):
                    stage_hint = map_stage_hint(item)
                    break
            if stage_hint != "unspecified":
                break

    transition_type_counts = Counter(
        str(change.get("transition_type") or "unknown") for change in observed if isinstance(change, dict)
    )
    response_type_counts = Counter(
        str(change.get("response_type") or "unknown") for change in observed if isinstance(change, dict)
    )
    memory_refs = [
        {
            "unit_id": row.get("unit_id"),
            "group_id": (row.get("source") or {}).get("group_id"),
            "primary_stage": (row.get("source") or {}).get("primary_stage"),
        }
        for row in memory_rows
    ]
    anna_refs = [
        {
            "unit_id": row.get("unit_id"),
            "source_id": (row.get("source") or {}).get("source_id"),
        }
        for row in anna_rows[:2]
    ]
    evidence_unit_ids = [case_row.get("unit_id")]
    if memory_rows:
        evidence_unit_ids.extend(row.get("unit_id") for row in memory_rows if row.get("unit_id"))
    if anna_rows:
        evidence_unit_ids.extend(row.get("unit_id") for row in anna_rows[:2] if row.get("unit_id"))

    return {
        "schema_version": "theraskill_transition_candidate_v1",
        "candidate_id": f"pstc_{stable_hash(str(source.get('case_id')) + '::' + str(order) + '::' + str(sequence.get('sequence_name') or 'sequence'))}",
        "candidate_type": "patient_state_transition_candidate",
        "source_dataset": "Simulation.multisource",
        "source_case_id": source.get("case_id"),
        "case_source_unit_id": case_row.get("unit_id"),
        "sequence_order": order,
        "sequence_name": sequence.get("sequence_name"),
        "stage_hint": stage_hint,
        "starting_state": {
            "summary": start.get("summary"),
            "risk_level": start.get("risk_level"),
            "state_dimensions": start.get("state_dimensions") or [],
        },
        "move_sequence": sequence.get("move_sequence") or [],
        "observed_client_changes": observed,
        "applicable_context": applicable_context,
        "trajectory_turning_points": content.get("turning_points") or [],
        "trajectory_problem_summary": content.get("dominant_presenting_problems") or [],
        "trajectory_overall_process_summary": content.get("overall_process_summary") or "",
        "transition_type_counts": dict(transition_type_counts),
        "response_type_counts": dict(response_type_counts),
        "memory_pattern_refs": memory_refs,
        "anna_bridge_refs": anna_refs,
        "continuity_hint_note": "Anna bridge refs are synthetic continuity hints only, not therapist observed evidence.",
        "evidence_unit_ids": [unit_id for unit_id in evidence_unit_ids if unit_id],
        "provenance": {
            "case_trajectory_unit_id": case_row.get("unit_id"),
            "action_sequence_name": sequence.get("sequence_name"),
            "memory_group_ids": [ref["group_id"] for ref in memory_refs if ref.get("group_id")],
            "anna_source_ids": [ref["source_id"] for ref in anna_refs if ref.get("source_id")],
        },
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)

    for path in (output_path, manifest_path, qa_path):
        ensure_output_path(path, overwrite=args.overwrite)

    case_rows = read_jsonl(args.case_trajectory)
    action_rows = read_jsonl(args.action_sequence)
    memory_rows = read_jsonl(args.memory_bundle)
    anna_rows = read_jsonl(args.anna_bridge) if Path(args.anna_bridge).exists() else []

    if args.limit_cases is not None:
        case_rows = case_rows[: args.limit_cases]

    action_by_case = sequence_index(action_rows)
    anna_by_case = anna_bridge_index(anna_rows)

    candidates: list[dict[str, Any]] = []
    missing_action_cases = 0

    for case_row in case_rows:
        case_id = str((case_row.get("source") or {}).get("case_id") or "")
        sequences = action_by_case.get(case_id) or []
        if not sequences:
            missing_action_cases += 1
            continue
        for order, sequence in enumerate(sequences, start=1):
            stage_hint = "middle"
            observed = sequence.get("observed_client_changes") or []
            if observed and isinstance(observed[0], dict):
                transition_label = str(observed[0].get("transition_type") or "")
                if "engagement" in transition_label:
                    stage_hint = "early"
                elif "no_clear_change" in transition_label:
                    stage_hint = "late"
            memory_refs = choose_memory_groups(memory_rows, stage_hint=stage_hint)
            candidates.append(
                make_candidate(
                    case_row=case_row,
                    sequence=sequence,
                    memory_rows=memory_refs,
                    anna_rows=anna_by_case.get(case_id) or [],
                    order=order,
                )
            )

    write_jsonl(output_path, candidates, overwrite=args.overwrite, sort_keys=True, separators=(",", ":"))

    manifest = {
        "schema_version": "theraskill_patient_state_transition_candidates_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "input_files": {
            "case_trajectory": {"path": str(args.case_trajectory), "sha256": file_sha256(args.case_trajectory)},
            "action_sequence": {"path": str(args.action_sequence), "sha256": file_sha256(args.action_sequence)},
            "memory_bundle": {"path": str(args.memory_bundle), "sha256": file_sha256(args.memory_bundle)},
            "anna_bridge": {"path": str(args.anna_bridge), "sha256": file_sha256(args.anna_bridge) if Path(args.anna_bridge).exists() else None},
        },
        "candidate_count": len(candidates),
        "case_count_input": len(case_rows),
        "cases_with_missing_action_sequence": missing_action_cases,
        "output_file": {"path": str(output_path), "sha256": file_sha256(output_path)},
    }
    write_json(manifest_path, manifest, overwrite=args.overwrite)

    qa = {
        "schema_version": "theraskill_patient_state_transition_candidates_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "candidate_count": len(candidates),
        "case_count_input": len(case_rows),
        "cases_with_missing_action_sequence": missing_action_cases,
        "stage_hint_distribution": dict(Counter(row.get("stage_hint") or "unknown" for row in candidates).most_common()),
        "transition_type_distribution": dict(
            Counter(
                key
                for row in candidates
                for key, value in (row.get("transition_type_counts") or {}).items()
                for _ in range(int(value))
            ).most_common()
        ),
        "issues": [],
        "issues_count": 0,
    }
    write_json(qa_path, qa, overwrite=args.overwrite)
    print(json.dumps({"status": "complete", "candidate_count": len(candidates)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
