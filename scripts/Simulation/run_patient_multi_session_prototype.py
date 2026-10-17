#!/usr/bin/env python3
"""Run a small multi-session patient simulator prototype using longitudinal carryover.

Historical prototype surface preserved for lineage/debugging; not a current benchmark entry point.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_patient_single_session_loop import (  # noqa: E402
    build_seed_transcript,
    read_json,
    write_json,
)
from patient_longitudinal_runtime import (  # noqa: E402
    build_followup_seed_transcript,
    build_runtime_carryover,
    run_session,
)
from domains.theraskill.theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    context_max_prompt_tokens,
    load_prompt,
    load_yaml,
    merged_model_config,
    parse_retry_attempts,
)
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_PATIENT_CASE_BUNDLE_PATH,
    DEFAULT_PATIENT_MULTI_SESSION_PROTOTYPE_OUTPUT_PATH,
    PATIENT_DISCLOSURE_CONTROLLER_PROMPT_PATH,
    PATIENT_MOVE_INTERPRETATION_PROMPT_PATH,
    PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH,
    PATIENT_STATE_MEMORY_WRITEBACK_FROM_BUNDLE_PROMPT_PATH,
    PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH,
    PATIENT_TURN_REALIZATION_PROMPT_PATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_CASE_BUNDLE = DEFAULT_PATIENT_CASE_BUNDLE_PATH
DEFAULT_OUTPUT = DEFAULT_PATIENT_MULTI_SESSION_PROTOTYPE_OUTPUT_PATH


SESSION_TURN_BANK = [
    [
        "听起来你现在最难受的，不只是事情本身，而是那种一直悬着、不知道该怎么帮的感觉。我们先不急着找办法，能不能先把你心里最沉的那块说清楚？",
        "当你这么讲的时候，我感觉你一边很心疼，一边也在拼命扛着责任。那如果把这份压力拆开来看，哪一部分最让你喘不过气？",
    ],
    [
        "上次我们谈到，你最怕的是一边想帮孩子，一边又担心自己成了那个把他推得更远的人。这一周里，这种两难最明显的一次，大概发生在什么时候？",
        "你已经能更早地看到那股‘想立刻抓成绩’的着急了。那在你看到它冒出来的那一刻，脑子里通常还会跟着出现哪句最扎心的话？",
    ],
    [
        "到这一步，你好像已经不只是被焦虑推着走了，也开始能看见自己一着急就会自动补出最坏结果。最近一次你把自己拉住、没有立刻冲上去的时候，心里后来发生了什么？",
        "如果把这几次变化连起来看，也许我们在慢慢分开两件事: 真正想靠近孩子的部分，和被恐惧推着想立刻控制局面的部分。现在哪一部分更容易在关键时刻占上风？",
    ],
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--case-bundle", default=str(DEFAULT_CASE_BUNDLE))
    parser.add_argument(
        "--carryover-report",
        "--legacy-carryover-input",
        "--carryover-input",
        dest="carryover_report",
        help="Optional carryover evidence input. `--legacy-carryover-input` / `--carryover-input` are kept only for historical prototype wiring.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--session-count", type=int, default=3)
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"output exists: {output_path}")

    bundle = read_json(args.case_bundle)
    reference_carryover = read_json(args.carryover_report) if args.carryover_report else None
    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    client = OpenAICompatibleClient(model_config)

    prompts = {
        "move": load_prompt(PATIENT_MOVE_INTERPRETATION_PROMPT_PATH),
        "disclosure": load_prompt(PATIENT_DISCLOSURE_CONTROLLER_PROMPT_PATH),
        "turn": load_prompt(PATIENT_TURN_REALIZATION_PROMPT_PATH),
        "state": load_prompt(PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH),
        "memory": load_prompt(PATIENT_STATE_MEMORY_WRITEBACK_FROM_BUNDLE_PROMPT_PATH),
        "summary": load_prompt(SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH),
        "reviewer": load_prompt(PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH),
    }

    requested_session_count = int(args.session_count)
    if requested_session_count < 1:
        raise ValueError("session_count must be >= 1")
    if requested_session_count > len(SESSION_TURN_BANK):
        raise ValueError(f"session_count must be <= {len(SESSION_TURN_BANK)} for the current prototype")

    sessions: list[dict[str, Any]] = []
    runtime_carryovers: list[dict[str, Any]] = []

    current_state = copy.deepcopy((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {})
    current_memory = copy.deepcopy((bundle.get("runtime_state_seed") or {}).get("episodic_memory_seed") or {})
    current_seed_transcript = build_seed_transcript()
    scenario_id = (reference_carryover or {}).get("scenario_id")

    for session_index in range(1, requested_session_count + 1):
        (
            final_state,
            final_memory,
            turn_records,
            transcript,
            step_audits,
            summary_payload,
            reviewer_payload,
        ) = run_session(
            client,
            prompts,
            bundle,
            current_state,
            current_memory,
            current_seed_transcript,
            SESSION_TURN_BANK[session_index - 1],
            parse_retries=parse_retries,
            max_prompt_tokens=max_prompt_tokens,
        )

        session_payload = {
            "session_index": session_index,
            "opening_session_state": copy.deepcopy(current_state),
            "turn_count_completed": len(turn_records),
            "transcript": transcript,
            "turn_records": turn_records,
            "final_session_state": final_state,
            "final_memory_seed": final_memory,
            "session_longitudinal_summary": summary_payload,
            "reviewer_result": reviewer_payload,
            "step_prompt_audits": step_audits,
        }

        if session_index < requested_session_count:
            carryover = build_runtime_carryover(
                session_payload,
                case_id=bundle.get("case_id"),
                scenario_id=scenario_id,
                source_session_label=f"session_{session_index}",
            )
            session_payload["next_session_carryover"] = carryover
            runtime_carryovers.append(carryover)
            current_state = copy.deepcopy((carryover.get("next_session_bootstrap") or {}).get("session_opening_state") or final_state)
            current_memory = copy.deepcopy((carryover.get("next_session_bootstrap") or {}).get("rolling_memory_seed") or final_memory)
            current_seed_transcript = build_followup_seed_transcript(carryover, session_index + 1)

        sessions.append(session_payload)

    report = {
        "schema_version": "theraskill_patient_multi_session_prototype_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "case_id": bundle.get("case_id"),
        "reference_carryover_input": args.carryover_report,
        "session_count": requested_session_count,
        "longitudinal_gate_min_3_sessions_met": requested_session_count >= 3,
        "runtime_carryover_count": len(runtime_carryovers),
        "sessions": sessions,
    }
    write_json(output_path, report)
    print(
        json.dumps(
            {
                "status": "complete",
                "output": str(output_path),
                "session_count": requested_session_count,
                "runtime_carryover_count": len(runtime_carryovers),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
