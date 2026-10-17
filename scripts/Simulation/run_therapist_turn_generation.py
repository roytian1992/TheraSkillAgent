#!/usr/bin/env python3
"""Run a therapist turn generation pass for simulation runtime."""

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
    DEFAULT_THERAPIST_TURN_GENERATION_PATH,
    SIMULATION_ACTION_SEQUENCE_PATH,
    SIMULATION_CASE_FORMULATION_PATH,
    SIMULATION_CASE_TRAJECTORY_PATH,
    SIMULATION_PERSONA_BUNDLE_PATH,
    THERAPIST_TURN_GENERATION_PROMPT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = THERAPIST_TURN_GENERATION_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_PERSONA = SIMULATION_PERSONA_BUNDLE_PATH
DEFAULT_TRAJECTORY = SIMULATION_CASE_TRAJECTORY_PATH
DEFAULT_FORMULATION = SIMULATION_CASE_FORMULATION_PATH
DEFAULT_ACTION_SEQUENCE = SIMULATION_ACTION_SEQUENCE_PATH
DEFAULT_OUTPUT = DEFAULT_THERAPIST_TURN_GENERATION_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--persona-jsonl", default=str(DEFAULT_PERSONA))
    parser.add_argument("--trajectory-jsonl", default=str(DEFAULT_TRAJECTORY))
    parser.add_argument("--formulation-jsonl", default=str(DEFAULT_FORMULATION))
    parser.add_argument("--action-sequence-jsonl", default=str(DEFAULT_ACTION_SEQUENCE))
    parser.add_argument("--patient-memory-report", required=True, help="Current patient-memory input.")
    parser.add_argument("--therapist-memory-report", required=True, help="Current therapist-memory input.")
    parser.add_argument("--persona-tag", default="情绪")
    parser.add_argument("--case-source-id", default="case_2517")
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


def load_source_runtime_session(patient_memory_report: dict[str, Any]) -> dict[str, Any] | None:
    source_path = patient_memory_report.get("source_runtime_report")
    if not source_path:
        return None
    path = Path(str(source_path))
    if not path.exists():
        return None
    return read_json(path)


def extract_runtime_dialogue_context(patient_memory_report: dict[str, Any], *, limit: int = 6) -> tuple[list[dict[str, str]], str]:
    runtime_session = load_source_runtime_session(patient_memory_report)
    transcript = runtime_session.get("transcript") if isinstance(runtime_session, dict) else None
    if isinstance(transcript, list):
        cleaned = []
        for item in transcript:
            role = str((item or {}).get("role") or "").strip()
            text = str((item or {}).get("text") or "").strip()
            if role and text:
                cleaned.append({"role": role, "text": text})
        if cleaned:
            if cleaned[-1]["role"] == "patient":
                return cleaned[:-1][-limit:], cleaned[-1]["text"]
            return cleaned[-limit:], ""
    patient_state_memory = (patient_memory_report.get("result") or {}).get("patient_state_memory") or {}
    fallback_turns = [
        {"role": "patient", "text": str(text)}
        for text in (patient_state_memory.get("current_cognition") or [])[:2]
        if str(text).strip()
    ]
    fallback_utterance = ""
    for key in ("current_cognition", "current_interpersonal_concerns", "presenting_problems"):
        values = patient_state_memory.get(key) or []
        if values:
            fallback_utterance = str(values[0])
            break
    return fallback_turns[-limit:], fallback_utterance


def build_risk_attention_hint(patient_memory_report: dict[str, Any], therapist_memory_report: dict[str, Any]) -> dict[str, Any]:
    patient_state_memory = (patient_memory_report.get("result") or {}).get("patient_state_memory") or {}
    therapist_case_memory = (therapist_memory_report.get("result") or {}).get("therapist_case_memory") or {}
    risk_state = patient_state_memory.get("risk_state") or {}
    risk_level = str(risk_state.get("level") or patient_state_memory.get("risk_state") or "unknown")
    signals = [str(item) for item in (risk_state.get("signals") or []) if str(item).strip()]
    watchpoints = [str(item) for item in (therapist_case_memory.get("risk_watchpoints") or []) if str(item).strip()]
    should_check_in = risk_level in {"elevated", "moderate", "high"} or "risk_check_in" in {
        str(item) for item in (therapist_case_memory.get("selected_response_strategy") or [])
    }
    if should_check_in:
        required_behavior = (
            "在当前回应里加入一个温和、非惊吓式的承受状态或风险监测问句；"
            "优先检查来访者此刻是否快撑不住、最近最难熬的时刻、或亲子局势是否有明显恶化；"
            "同时轻量探查一个支持/缓冲杠杆，比如谁或什么能让她稍微撑住一点。"
        )
    else:
        required_behavior = "当前无需单独插入风险监测问句，但仍要避免忽略明显风险信号。"
    return {
        "risk_level": risk_level,
        "signals": signals[:4],
        "watchpoints": watchpoints[:4],
        "should_check_in_now": should_check_in,
        "required_behavior": required_behavior,
    }


def has_support_probe(text: str) -> bool:
    probes = [
        "有没有谁",
        "有没有什么",
        "谁能",
        "什么能",
        "支持",
        "陪你",
        "缓一口气",
        "不是一个人在撑",
        "靠什么",
    ]
    return any(token in text for token in probes)


def append_sentence(text: str, sentence: str) -> str:
    base = text.rstrip()
    if not base:
        return sentence
    if base[-1] not in "。！？!?":
        base += "。"
    return f"{base}{sentence}"


def enforce_risk_attention_surface(result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    risk_hint = payload.get("risk_attention_hint") or {}
    if not risk_hint.get("should_check_in_now"):
        return result
    utterance = str(result.get("therapist_utterance") or "").strip()
    if utterance and not has_support_probe(utterance):
        result["therapist_utterance"] = append_sentence(
            utterance,
            "如果你愿意，我们也可以一起看看，那时候有没有谁、或者什么方式，能让你稍微缓一口气。",
        )
    turn_plan = result.get("therapist_turn_plan") or {}
    response_intent = [str(item) for item in (turn_plan.get("response_intent") or []) if str(item).strip()]
    if "light_support_probe" not in response_intent:
        response_intent.append("light_support_probe")
    turn_plan["response_intent"] = response_intent
    primary_move_types = [str(item) for item in (turn_plan.get("primary_move_types") or []) if str(item).strip()]
    if "support_resource_probe" not in primary_move_types:
        primary_move_types.append("support_resource_probe")
    turn_plan["primary_move_types"] = primary_move_types
    result["therapist_turn_plan"] = turn_plan
    turn_trace = result.get("therapist_turn_trace") or {}
    applied_case_memory = [str(item) for item in (turn_trace.get("applied_therapist_case_memory") or []) if str(item).strip()]
    marker = "runtime_guard: elevated-risk utterance surface augmented with light support/resource probe"
    if marker not in applied_case_memory:
        applied_case_memory.append(marker)
    turn_trace["applied_therapist_case_memory"] = applied_case_memory
    result["therapist_turn_trace"] = turn_trace
    return result


def build_case_evidence_bundle(
    trajectory_row: dict[str, Any],
    formulation_row: dict[str, Any],
    action_sequence_row: dict[str, Any],
) -> dict[str, Any]:
    trajectory = ((trajectory_row.get("content") or {}).get("case_trajectory") or {})
    formulation = ((formulation_row.get("content") or {}).get("case_formulation_pattern") or {})
    action_sequence = ((action_sequence_row.get("content") or {}).get("action_sequence_pattern") or {})
    sequences = action_sequence.get("action_sequences") or []
    compact_sequences = []
    for sequence in sequences[:2]:
        compact_sequences.append(
            {
                "sequence_name": sequence.get("sequence_name"),
                "intended_process_goal": sequence.get("intended_process_goal"),
                "move_sequence": [
                    {
                        "order": step.get("order"),
                        "move_types": step.get("move_types"),
                        "move_functions": step.get("move_functions"),
                        "surface_summary": step.get("surface_summary"),
                    }
                    for step in (sequence.get("move_sequence") or [])[:5]
                ],
                "observed_client_changes": [
                    {
                        "response_type": item.get("response_type"),
                        "transition_type": item.get("transition_type"),
                        "summary": item.get("summary"),
                    }
                    for item in (sequence.get("observed_client_changes") or [])[:3]
                ],
            }
        )
    return {
        "source_case_id": (trajectory_row.get("source") or {}).get("case_id"),
        "trajectory_summary": {
            "overall_process_summary": trajectory.get("overall_process_summary"),
            "main_move_sequence": [
                {
                    "order": item.get("order"),
                    "move_types": item.get("move_types"),
                    "move_functions": item.get("move_functions"),
                    "process_role": item.get("process_role"),
                }
                for item in (trajectory.get("main_move_sequence") or [])[:6]
            ],
            "turning_points": trajectory.get("turning_points") or [],
        },
        "formulation_summary": {
            "presenting_problem": formulation.get("presenting_problem") or [],
            "maintaining_factors": formulation.get("maintaining_factors") or [],
            "protective_factors": formulation.get("protective_factors") or [],
            "client_goals": formulation.get("client_goals") or [],
            "suggested_focus": formulation.get("suggested_focus") or [],
        },
        "action_sequence_summary": compact_sequences,
    }


def build_runtime_payload(
    therapist_persona_row: dict[str, Any],
    patient_memory_report: dict[str, Any],
    therapist_memory_report: dict[str, Any],
    trajectory_row: dict[str, Any],
    formulation_row: dict[str, Any],
    action_sequence_row: dict[str, Any],
) -> dict[str, Any]:
    recent_turns, current_patient_utterance = extract_runtime_dialogue_context(patient_memory_report)
    return {
        "task": "generate_therapist_turn",
        "therapist_persona_bundle": trim_unit_for_prompt(therapist_persona_row),
        "patient_state_memory": (patient_memory_report.get("result") or {}).get("patient_state_memory"),
        "therapist_case_memory": (therapist_memory_report.get("result") or {}).get("therapist_case_memory"),
        "case_evidence_bundle": build_case_evidence_bundle(trajectory_row, formulation_row, action_sequence_row),
        "risk_attention_hint": build_risk_attention_hint(patient_memory_report, therapist_memory_report),
        "recent_turn_window": recent_turns,
        "current_patient_utterance": current_patient_utterance,
        "generation_goal": "produce the next therapist reply while matching stage, strategy, and boundary constraints",
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    persona_rows = read_jsonl(args.persona_jsonl)
    trajectory_rows = read_jsonl(args.trajectory_jsonl)
    formulation_rows = read_jsonl(args.formulation_jsonl)
    action_sequence_rows = read_jsonl(args.action_sequence_jsonl)
    patient_memory_report = read_json(args.patient_memory_report)
    therapist_memory_report = read_json(args.therapist_memory_report)
    therapist_persona_row = select_unit(persona_rows, "tag", args.persona_tag)
    trajectory_row = select_unit(trajectory_rows, "source_id", args.case_source_id)
    formulation_row = select_unit(formulation_rows, "source_id", args.case_source_id)
    action_sequence_row = select_unit(action_sequence_rows, "source_id", args.case_source_id)

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    payload = build_runtime_payload(
        therapist_persona_row,
        patient_memory_report,
        therapist_memory_report,
        trajectory_row,
        formulation_row,
        action_sequence_row,
    )
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
        result = enforce_risk_attention_surface(result, payload)
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
        "schema_version": "theraskill_therapist_turn_generation_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "model_config": {
            "path": args.model_config,
            "model": model_config.get("name"),
            "base_url": model_config.get("base_url"),
        },
        "selectors": {
            "persona_tag": args.persona_tag,
            "case_source_id": args.case_source_id,
        },
        "prompt_path": args.prompt,
        "prompt_audit": prompt_audit,
        "input_summary": {
            "recent_turn_count": len(payload["recent_turn_window"]),
            "therapist_persona_unit_id": therapist_persona_row.get("unit_id"),
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
