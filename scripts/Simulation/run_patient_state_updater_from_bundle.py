#!/usr/bin/env python3
"""Run a patient session-state update pass from bundle-native runtime inputs."""

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
    DEFAULT_PATIENT_CASE_BUNDLE_PATH,
    DEFAULT_PATIENT_STATE_UPDATER_FROM_BUNDLE_OUTPUT_PATH,
    PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_CASE_BUNDLE = DEFAULT_PATIENT_CASE_BUNDLE_PATH
DEFAULT_OUTPUT = DEFAULT_PATIENT_STATE_UPDATER_FROM_BUNDLE_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--case-bundle", default=str(DEFAULT_CASE_BUNDLE))
    parser.add_argument("--move-report", "--move-input", dest="move_report", required=True)
    parser.add_argument("--disclosure-report", "--disclosure-input", dest="disclosure_report", required=True)
    parser.add_argument("--turn-report", "--turn-input", dest="turn_report", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def trim_case_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    authoritative = bundle.get("authoritative_layer") or {}
    runtime_seed = bundle.get("runtime_state_seed") or {}
    return {
        "bundle_id": bundle.get("bundle_id"),
        "case_id": bundle.get("case_id"),
        "metadata": bundle.get("metadata"),
        "patient_formulation": authoritative.get("patient_formulation"),
        "current_session_state": runtime_seed.get("initial_session_state"),
        "episodic_memory_seed": runtime_seed.get("episodic_memory_seed"),
    }


def build_payload(bundle: dict[str, Any], move_obj: dict[str, Any], disclosure_obj: dict[str, Any], turn_obj: dict[str, Any]) -> dict[str, Any]:
    current_therapist_utterance = (
        "你一边很想保护他，一边又担心自己越着急越会把他推得更远，"
        "这种两难真的很折磨人。我们先不急着判断谁对谁错，"
        "能不能先一起看看，当你最慌的那一刻，你脑子里最先冒出来的担心通常是什么？"
    )
    return {
        "task": "update_patient_session_state",
        "patient_case_bundle": trim_case_bundle(bundle),
        "previous_session_state": ((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}),
        "episodic_memory_seed": ((bundle.get("runtime_state_seed") or {}).get("episodic_memory_seed") or {}),
        "current_therapist_utterance": current_therapist_utterance,
        "therapist_move_interpretation": (move_obj.get("result") or {}).get("therapist_move_interpretation"),
        "disclosure_decision": (disclosure_obj.get("result") or {}).get("disclosure_decision"),
        "patient_turn_result": (turn_obj.get("result") or {}),
        "update_goal": "update the observable session state after one therapist move and one patient reply",
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    bundle = read_json(args.case_bundle)
    move_obj = read_json(args.move_report)
    disclosure_obj = read_json(args.disclosure_report)
    turn_obj = read_json(args.turn_report)
    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    payload = build_payload(bundle, move_obj, disclosure_obj, turn_obj)
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
        "schema_version": "theraskill_patient_state_updater_from_bundle_v1",
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
            "case_bundle": args.case_bundle,
            "case_id": bundle.get("case_id"),
            "has_move_interpretation": payload["therapist_move_interpretation"] is not None,
            "has_disclosure_decision": payload["disclosure_decision"] is not None,
            "has_patient_turn_result": payload["patient_turn_result"] is not None,
        },
        "result": result,
        "error": error,
    }
    write_json(output_path, report, overwrite=args.overwrite)
    print(json.dumps({"status": status, "output": str(output_path), "estimated_prompt_tokens": prompt_audit["estimated_prompt_tokens"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
