#!/usr/bin/env python3
"""Run a reviewer gate over a simulation session output."""

from __future__ import annotations

import argparse
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

from domains.theraskill.theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    PromptBudgetExceededError,
    chat_json_with_repair,
    context_max_prompt_tokens,
    load_prompt,
    load_yaml,
    merged_model_config,
    parse_retry_attempts,
    prompt_budget_report,
)
from io_utils import ensure_output_path, read_json, write_json  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_SESSION_REVIEWER_GATE_OUTPUT_PATH,
    SESSION_REVIEWER_GATE_PROMPT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = SESSION_REVIEWER_GATE_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT = DEFAULT_SESSION_REVIEWER_GATE_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--session-input", required=True)
    parser.add_argument("--summary-input", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def build_payload(session_obj: dict[str, Any], summary_obj: dict[str, Any]) -> dict[str, Any]:
    transcript = session_obj.get("transcript") or []
    compact_transcript = [
        {"role": item.get("role"), "text": str(item.get("text") or "")[:220]}
        for item in transcript[-10:]
    ]
    turn_records = session_obj.get("turn_records") or []
    compact_turn_records = []
    for item in turn_records[-4:]:
        compact_turn_records.append(
            {
                "turn_index": item.get("turn_index"),
                "continuation_only": bool(item.get("continuation_only")),
                "therapist_utterance": ((item.get("therapist_turn") or {}).get("therapist_utterance") or "")[:220],
                "patient_utterance": ((item.get("patient_turn") or {}).get("patient_utterance") or "")[:220],
                "patient_state_focus": {
                    "current_emotion": ((item.get("patient_state_memory") or {}).get("current_emotion") or [])[:4],
                    "unresolved_threads": ((item.get("patient_state_memory") or {}).get("unresolved_threads") or [])[:4],
                },
                "therapist_case_focus": {
                    "working_formulation": ((item.get("therapist_case_memory") or {}).get("working_formulation") or [])[:4],
                    "top_priorities": ((item.get("therapist_case_memory") or {}).get("top_priorities") or [])[:4],
                },
            }
        )
    return {
        "task": "review_simulation_session",
        "transcript": compact_transcript,
        "turn_records": compact_turn_records,
        "session_longitudinal_summary": (summary_obj.get("result") or {}).get("session_longitudinal_summary"),
        "final_patient_state_memory": session_obj.get("final_patient_state_memory") or {},
        "final_therapist_case_memory": session_obj.get("final_therapist_case_memory") or {},
        "therapist_continuation": session_obj.get("therapist_continuation") or {},
        "session_meta": {
            "turn_count_completed": session_obj.get("turn_count_completed"),
            "review_turn_record_count": session_obj.get("review_turn_record_count"),
            "has_therapist_continuation": bool(session_obj.get("therapist_continuation")),
            "stop_reason": session_obj.get("stop_reason"),
        },
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    session_obj = read_json(args.session_input)
    summary_obj = read_json(args.summary_input)
    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    payload = build_payload(session_obj, summary_obj)
    prompt_audit = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
    client = OpenAICompatibleClient(model_config)

    try:
        result = chat_json_with_repair(
            client,
            system_prompt,
            payload,
            parse_retries=parse_retries,
            force_json_first=True,
        )
        status = "complete"
        error = None
    except PromptBudgetExceededError as exc:
        result = None
        status = "prompt_budget_exceeded"
        error = {"type": "PromptBudgetExceededError", "details": exc.report}
    except Exception as exc:  # noqa: BLE001
        result = None
        status = "failed"
        error = {"type": type(exc).__name__, "details": str(exc)}

    report = {
        "schema_version": "theraskill_session_reviewer_gate_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "model_config": {
            "path": args.model_config,
            "model": model_config.get("name"),
            "base_url": model_config.get("base_url"),
        },
        "prompt_path": args.prompt,
        "prompt_audit": prompt_audit,
        "input_summary": {
            "session_input": args.session_input,
            "summary_input": args.summary_input,
            "turn_count_completed": session_obj.get("turn_count_completed"),
            "transcript_len": len(session_obj.get("transcript") or []),
            "compact_transcript_len": len(payload["transcript"]),
            "has_summary": (summary_obj.get("result") or {}).get("session_longitudinal_summary") is not None,
        },
        "result": result,
        "error": error,
    }
    write_json(output_path, report, overwrite=args.overwrite)
    print(json.dumps({"status": status, "output": str(output_path), "estimated_prompt_tokens": prompt_audit["estimated_prompt_tokens"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
