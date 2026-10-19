#!/usr/bin/env python3
"""Build a longitudinal summary over a simulation session output."""

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
    DEFAULT_SESSION_LONGITUDINAL_SUMMARY_OUTPUT_PATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT = DEFAULT_SESSION_LONGITUDINAL_SUMMARY_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--session-input", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def build_payload(session_obj: dict[str, Any]) -> dict[str, Any]:
    from domains.theraskill.theraskill_simulation.session_analysis import build_session_payload
    return build_session_payload(session_obj)


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    session_obj = read_json(args.session_input)
    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    payload = build_payload(session_obj)
    prompt_audit = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
    client = OpenAICompatibleClient(model_config)

    try:
        from domains.theraskill.theraskill_simulation.session_analysis import run_session_analysis
        result, prompt_audit = run_session_analysis(
            client, system_prompt, payload, mode="summary",
            max_prompt_tokens=max_prompt_tokens, parse_retries=parse_retries,
            checkpoint_dir=output_path.parent / (output_path.stem + ".checkpoints"),
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
        "schema_version": "theraskill_session_longitudinal_summary_v1",
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
            "turn_count_completed": session_obj.get("turn_count_completed"),
            "transcript_len": len(session_obj.get("transcript") or []),
            "covered_transcript_len": len(payload["transcript"]),
        },
        "result": result,
        "error": error,
    }
    write_json(output_path, report, overwrite=args.overwrite)
    print(json.dumps({"status": status, "output": str(output_path), "estimated_prompt_tokens": prompt_audit["estimated_prompt_tokens"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
