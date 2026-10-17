#!/usr/bin/env python3
"""Assemble a one-turn patient simulator prototype from runtime module outputs.

Historical prototype surface preserved for lineage/debugging; not a current benchmark entry point.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_json, write_json
from simulation_surface_registry import (
    DEFAULT_PATIENT_CASE_BUNDLE_PATH,
    DEFAULT_PATIENT_SINGLE_SESSION_PROTOTYPE_OUTPUT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASE_BUNDLE = DEFAULT_PATIENT_CASE_BUNDLE_PATH
DEFAULT_OUTPUT = DEFAULT_PATIENT_SINGLE_SESSION_PROTOTYPE_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-bundle", default=str(DEFAULT_CASE_BUNDLE))
    parser.add_argument(
        "--move-report",
        "--legacy-move-input",
        "--move-input",
        dest="move_report",
        required=True,
        help="Current move report input. `--legacy-move-input` / `--move-input` are kept only for historical prototype wiring.",
    )
    parser.add_argument(
        "--disclosure-report",
        "--legacy-disclosure-input",
        "--disclosure-input",
        dest="disclosure_report",
        required=True,
        help="Current disclosure report input. `--legacy-disclosure-input` / `--disclosure-input` are kept only for historical prototype wiring.",
    )
    parser.add_argument(
        "--turn-report",
        "--legacy-turn-input",
        "--turn-input",
        dest="turn_report",
        required=True,
        help="Current turn report input. `--legacy-turn-input` / `--turn-input` are kept only for historical prototype wiring.",
    )
    parser.add_argument(
        "--state-report",
        "--legacy-state-input",
        "--state-input",
        dest="state_report",
        required=True,
        help="Current state report input. `--legacy-state-input` / `--state-input` are kept only for historical prototype wiring.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"output exists: {output_path}")

    bundle = read_json(args.case_bundle)
    move_obj = read_json(args.move_report)
    disclosure_obj = read_json(args.disclosure_report)
    turn_obj = read_json(args.turn_report)
    state_obj = read_json(args.state_report)

    therapist_utterance = (
        "你一边很想保护他，一边又担心自己越着急越会把他推得更远，"
        "这种两难真的很折磨人。我们先不急着判断谁对谁错，"
        "能不能先一起看看，当你最慌的那一刻，你脑子里最先冒出来的担心通常是什么？"
    )
    prior_turns = [
        {
            "role": "patient",
            "text": "我现在最难受的其实不是孩子不说话本身，而是我一想到他是不是已经越来越糟了，我就会很慌。"
        },
        {
            "role": "therapist",
            "text": "所以你现在承受的，不只是和孩子沟通不上，还有一种很强的担心，怕自己已经错过了最该帮到他的时机。"
        },
        {
            "role": "patient",
            "text": "对，我会想是不是我前面说得太多、逼得太紧了，可我又不知道如果我不管，事情会不会更严重。"
        },
    ]
    patient_utterance = ((turn_obj.get("result") or {}).get("patient_utterance") or "")
    transcript = prior_turns + [{"role": "therapist", "text": therapist_utterance}, {"role": "patient", "text": patient_utterance}]

    report = {
        "schema_version": "theraskill_patient_single_session_prototype_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "case_bundle": args.case_bundle,
        "case_id": bundle.get("case_id"),
        "transcript": transcript,
        "turn_record": {
            "therapist_move_interpretation": (move_obj.get("result") or {}).get("therapist_move_interpretation"),
            "disclosure_decision": (disclosure_obj.get("result") or {}).get("disclosure_decision"),
            "patient_turn_result": (turn_obj.get("result") or {}),
            "state_update_result": (state_obj.get("result") or {}),
        },
        "previous_session_state": ((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}),
        "updated_session_state": ((state_obj.get("result") or {}).get("updated_session_state") or {}),
        "memory_write_suggestions": ((state_obj.get("result") or {}).get("memory_write_suggestions") or {}),
    }
    write_json(output_path, report)
    print(json.dumps({"status": "complete", "output": str(output_path), "transcript_turns": len(transcript)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
