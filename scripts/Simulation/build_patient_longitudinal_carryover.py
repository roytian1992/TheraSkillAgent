#!/usr/bin/env python3
"""Build a cross-session carryover bundle from a completed patient session runtime trace."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_json
from simulation_surface_registry import DEFAULT_PATIENT_LONGITUDINAL_CARRYOVER_OUTPUT_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = DEFAULT_PATIENT_LONGITUDINAL_CARRYOVER_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-input", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def uniq_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = " ".join(str(value or "").split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    session_obj = read_json(args.session_input)
    scenario = session_obj.get("scenario") or {}
    summary = ((session_obj.get("session_longitudinal_summary") or {}).get("session_longitudinal_summary")) or {}
    compression = ((session_obj.get("session_longitudinal_summary") or {}).get("compression_trace")) or {}
    final_state = session_obj.get("final_session_state") or {}
    final_memory_seed = session_obj.get("final_memory_seed") or {}
    transcript = session_obj.get("transcript") or []
    last_patient_turn = ""
    last_therapist_turn = ""
    for item in reversed(transcript):
        if item.get("role") == "patient" and not last_patient_turn:
            last_patient_turn = item.get("text") or ""
        if item.get("role") == "therapist" and not last_therapist_turn:
            last_therapist_turn = item.get("text") or ""
        if last_patient_turn and last_therapist_turn:
            break

    carryover = {
        "schema_version": "theraskill_patient_longitudinal_carryover_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "carryover_id": f"carryover::{stable_hash(str(scenario.get('scenario_id') or args.session_input))}",
        "source_session_path": args.session_input,
        "case_id": session_obj.get("case_id") or scenario.get("case_id"),
        "scenario_id": scenario.get("scenario_id"),
        "previous_session_summary": {
            "case_progress_summary": summary.get("case_progress_summary") or [],
            "key_turning_points": summary.get("key_turning_points") or [],
            "active_unresolved_threads": summary.get("active_unresolved_threads") or [],
            "next_turn_setup": summary.get("next_turn_setup") or [],
            "boundary_or_risk_watchpoints": summary.get("boundary_or_risk_watchpoints") or [],
        },
        "next_session_bootstrap": {
            "session_opening_state": {
                "emotion_state": final_state.get("emotion_state") or [],
                "alliance_state": final_state.get("alliance_state"),
                "disclosure_state": final_state.get("disclosure_state"),
                "resistance_state": final_state.get("resistance_state"),
                "readiness_state": final_state.get("readiness_state"),
                "risk_state": final_state.get("risk_state"),
                "topic_focus": final_state.get("topic_focus") or [],
                "felt_understanding": final_state.get("felt_understanding"),
                "unfinished_threads": final_state.get("unfinished_threads") or [],
            },
            "rolling_memory_seed": final_memory_seed,
            "carry_forward_focus": uniq_keep_order(
                list(summary.get("next_turn_setup") or [])
                + list(summary.get("active_unresolved_threads") or [])
            )[:10],
            "do_not_forget": uniq_keep_order(
                list(summary.get("stable_patient_state_variables") or [])
                + list(summary.get("boundary_or_risk_watchpoints") or [])
            )[:10],
            "resume_suggestion": "Begin by reconnecting to the most recent unresolved fear or responsibility theme rather than restarting intake.",
        },
        "cross_session_trace": {
            "last_patient_turn": last_patient_turn,
            "last_therapist_turn": last_therapist_turn,
            "compression_keep_set": compression.get("kept_from_transcript") or [],
            "compression_uncertainties": compression.get("uncertainties") or [],
        },
    }

    write_json(output_path, carryover, overwrite=args.overwrite)
    print(json.dumps({"status": "complete", "output": str(output_path), "case_id": carryover["case_id"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
