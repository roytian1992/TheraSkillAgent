#!/usr/bin/env python3
"""Run a patient state memory update pass for simulation runtime."""

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
from io_utils import ensure_output_path, write_json  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_PATIENT_STATE_MEMORY_UPDATE_PATH,
    PATIENT_STATE_MEMORY_UPDATE_PROMPT_PATH,
    SIMULATION_ARCHETYPE_BUNDLE_PATH,
    SIMULATION_MEMORY_BUNDLE_PATH,
    SIMULATION_PERSONA_BUNDLE_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = PATIENT_STATE_MEMORY_UPDATE_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_PERSONA = SIMULATION_PERSONA_BUNDLE_PATH
DEFAULT_MEMORY = SIMULATION_MEMORY_BUNDLE_PATH
DEFAULT_ARCHETYPE = SIMULATION_ARCHETYPE_BUNDLE_PATH
DEFAULT_OUTPUT = DEFAULT_PATIENT_STATE_MEMORY_UPDATE_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--persona-jsonl", default=str(DEFAULT_PERSONA))
    parser.add_argument("--memory-jsonl", default=str(DEFAULT_MEMORY))
    parser.add_argument("--archetype-jsonl", default=str(DEFAULT_ARCHETYPE))
    parser.add_argument("--persona-tag", default="情绪")
    parser.add_argument("--memory-group-id", default="target::01_1_to_5_history_pairs")
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


def trim_unit_for_prompt(row: dict[str, Any], *, keep_retrieval_text: bool = False) -> dict[str, Any]:
    trimmed = {
        "unit_id": row.get("unit_id"),
        "knowledge_type": row.get("knowledge_type"),
        "source": row.get("source"),
        "content": row.get("content"),
        "quality": row.get("quality"),
        "normalization": row.get("normalization"),
    }
    if keep_retrieval_text:
        trimmed["retrieval"] = row.get("retrieval")
    return trimmed


def build_runtime_payload(
    persona_row: dict[str, Any],
    memory_row: dict[str, Any],
    archetype_row: dict[str, Any],
) -> dict[str, Any]:
    previous_memory = {
        "demographics_if_known": ["student"],
        "presenting_problems": ["最近持续焦虑", "学业压力下的自我怀疑"],
        "current_emotion": ["焦虑", "无助"],
        "current_cognition": ["担心自己做不好", "容易把压力解释为自己不够好"],
        "current_interpersonal_concerns": ["怕给别人添麻烦"],
        "alliance_state": "forming",
        "disclosure_depth": "surface",
        "resistance_signals": ["担心麻烦别人"],
        "readiness_level": "ready_for_exploration",
        "risk_state": {
            "level": "low",
            "signals": ["情绪负荷较高", "睡眠受影响"],
            "change": "unchanged",
        },
        "explicit_goals": ["想缓解当前压力", "想知道怎么让自己稳一点"],
        "hidden_concerns": ["担心自己是不是太脆弱"],
        "salient_prior_events": ["连续几天睡不好", "原本有效的自我调节方式开始失效"],
        "unresolved_threads": ["如何恢复稳定感", "是否应该向朋友进一步求助"],
        "last_therapist_move_impact": ["来访者感到被理解，但还没有形成明确行动方向"],
        "memory_confidence": "medium",
    }
    recent_turns = [
        {
            "role": "patient",
            "text": "我这几天又开始睡不好了，脑子里一直在转，感觉自己什么都做不好。",
        },
        {
            "role": "therapist",
            "text": "听起来你最近不仅很累，而且那种“怎么做都不够好”的想法也在反复压着你。",
        },
        {
            "role": "patient",
            "text": "对，而且我本来会去跑步，可这几天连跑步都没办法让我安静下来，我就更怀疑自己是不是越来越差了。",
        },
    ]
    therapist_utterance = "当你发现连以前能帮到自己的方式都暂时失效时，那种失控感确实会更强。我们可以先一起看看，这几天最压着你的，是哪一类担心？"
    return {
        "task": "update_patient_state_memory",
        "persona_bundle": trim_unit_for_prompt(persona_row),
        "memory_pattern_bundle": trim_unit_for_prompt(memory_row),
        "archetype_bundle": trim_unit_for_prompt(archetype_row),
        "previous_patient_state_memory": previous_memory,
        "recent_turn_window": recent_turns,
        "current_therapist_utterance": therapist_utterance,
        "update_goal": "preserve continuity while capturing new emotion/cognition/risk/readiness signals after the latest therapist move",
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    persona_rows = read_jsonl(args.persona_jsonl)
    memory_rows = read_jsonl(args.memory_jsonl)
    archetype_rows = read_jsonl(args.archetype_jsonl)
    persona_row = select_unit(persona_rows, "tag", args.persona_tag)
    memory_row = select_unit(memory_rows, "group_id", args.memory_group_id)
    archetype_row = select_unit(archetype_rows, "source_id", args.archetype_source_id)

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    payload = build_runtime_payload(persona_row, memory_row, archetype_row)
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
        "schema_version": "theraskill_patient_state_memory_update_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "model_config": {
            "path": args.model_config,
            "model": model_config.get("name"),
            "base_url": model_config.get("base_url"),
        },
        "selectors": {
            "persona_tag": args.persona_tag,
            "memory_group_id": args.memory_group_id,
            "archetype_source_id": args.archetype_source_id,
        },
        "prompt_path": args.prompt,
        "prompt_audit": prompt_audit,
        "input_summary": {
            "recent_turn_count": len(payload["recent_turn_window"]),
            "has_previous_memory": True,
            "persona_unit_id": persona_row.get("unit_id"),
            "memory_unit_id": memory_row.get("unit_id"),
            "archetype_unit_id": archetype_row.get("unit_id"),
        },
        "result": result,
        "error": error,
    }
    write_json(output_path, report, overwrite=args.overwrite)
    print(json.dumps({"status": status, "output": str(output_path), "estimated_prompt_tokens": prompt_audit["estimated_prompt_tokens"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
