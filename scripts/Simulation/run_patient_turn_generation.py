#!/usr/bin/env python3
"""Run a patient turn generation pass for simulation runtime."""

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
    read_jsonl,
)
from io_utils import ensure_output_path, read_json, write_json  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_PATIENT_TURN_GENERATION_PATH,
    PATIENT_TURN_GENERATION_PROMPT_PATH,
    SIMULATION_ARCHETYPE_BUNDLE_PATH,
    SIMULATION_PERSONA_BUNDLE_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = PATIENT_TURN_GENERATION_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_PERSONA = SIMULATION_PERSONA_BUNDLE_PATH
DEFAULT_ARCHETYPE = SIMULATION_ARCHETYPE_BUNDLE_PATH
DEFAULT_OUTPUT = DEFAULT_PATIENT_TURN_GENERATION_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--persona-jsonl", default=str(DEFAULT_PERSONA))
    parser.add_argument("--archetype-jsonl", default=str(DEFAULT_ARCHETYPE))
    parser.add_argument("--patient-memory-report", required=True, help="Current patient-memory input.")
    parser.add_argument("--therapist-memory-report", required=True, help="Current therapist-memory input.")
    parser.add_argument("--persona-tag", default="情绪")
    parser.add_argument("--archetype-source-id", default="category:uncategorized")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def select_unit(rows: list[dict[str, Any]], selector: str, value: str) -> dict[str, Any]:
    for row in rows:
        source = row.get("source") or {}
        if str(source.get(selector) or "") == value:
            return row
    raise KeyError(f"no row matched {selector}={value}")


def trim_unit_for_prompt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": row.get("unit_id"),
        "knowledge_type": row.get("knowledge_type"),
        "source": row.get("source"),
        "content": row.get("content"),
        "quality": row.get("quality"),
        "normalization": row.get("normalization"),
    }


def build_runtime_payload(
    persona_row: dict[str, Any],
    archetype_row: dict[str, Any],
    patient_memory_report: dict[str, Any],
    therapist_memory_report: dict[str, Any],
) -> dict[str, Any]:
    recent_turns = [
        {
            "role": "patient",
            "text": "我想我可能不会一开始就觉得自己不行了，会比较敢开口，也不会总怕别人觉得我很差。"
        },
        {
            "role": "therapist",
            "text": "你已经开始能描绘出那个更有力量的自己了，这很重要。"
        },
        {
            "role": "patient",
            "text": "嗯，但我还是有点担心，这种状态是不是只是现在想想而已，真到工作里我可能又会退回去。"
        },
    ]
    current_therapist_utterance = "有这样的担心其实很正常，这通常不代表你没有在变化，而是说明你已经开始认真地去想，真正面对那些困难时自己会怎么波动。你觉得如果下一次又有点退回去，最容易先出现的会是什么？"
    return {
        "task": "generate_patient_turn",
        "persona_bundle": trim_unit_for_prompt(persona_row),
        "archetype_bundle": trim_unit_for_prompt(archetype_row),
        "patient_state_memory": (patient_memory_report.get("result") or {}).get("patient_state_memory"),
        "therapist_case_memory": (therapist_memory_report.get("result") or {}).get("therapist_case_memory"),
        "recent_turn_window": recent_turns,
        "current_therapist_utterance": current_therapist_utterance,
        "generation_goal": "produce the next patient reply while preserving ambivalence, continuity, and persona consistency",
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    persona_rows = read_jsonl(args.persona_jsonl)
    archetype_rows = read_jsonl(args.archetype_jsonl)
    patient_memory_report = read_json(args.patient_memory_report)
    therapist_memory_report = read_json(args.therapist_memory_report)
    persona_row = select_unit(persona_rows, "tag", args.persona_tag)
    archetype_row = select_unit(archetype_rows, "source_id", args.archetype_source_id)

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    payload = build_runtime_payload(persona_row, archetype_row, patient_memory_report, therapist_memory_report)
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
        "schema_version": "theraskill_patient_turn_generation_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "model_config": {
            "path": args.model_config,
            "model": model_config.get("name"),
            "base_url": model_config.get("base_url"),
        },
        "selectors": {
            "persona_tag": args.persona_tag,
            "archetype_source_id": args.archetype_source_id,
        },
        "prompt_path": args.prompt,
        "prompt_audit": prompt_audit,
        "input_summary": {
            "recent_turn_count": len(payload["recent_turn_window"]),
            "persona_unit_id": persona_row.get("unit_id"),
            "archetype_unit_id": archetype_row.get("unit_id"),
            "has_patient_memory": payload["patient_state_memory"] is not None,
            "has_therapist_case_memory": payload["therapist_case_memory"] is not None,
        },
        "result": result,
        "error": error,
    }
    write_json(output_path, report, overwrite=args.overwrite)
    print(json.dumps({"status": status, "output": str(output_path), "estimated_prompt_tokens": prompt_audit["estimated_prompt_tokens"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
