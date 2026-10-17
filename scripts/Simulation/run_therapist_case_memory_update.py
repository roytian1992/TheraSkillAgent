#!/usr/bin/env python3
"""Run a therapist case memory update pass for simulation runtime."""

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
    DEFAULT_THERAPIST_CASE_MEMORY_UPDATE_PATH,
    SIMULATION_ACTION_SEQUENCE_PATH,
    SIMULATION_CASE_FORMULATION_PATH,
    SIMULATION_CASE_TRAJECTORY_PATH,
    SIMULATION_MEMORY_BUNDLE_PATH,
    SIMULATION_PERSONA_BUNDLE_PATH,
    THERAPIST_CASE_MEMORY_UPDATE_PROMPT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT = THERAPIST_CASE_MEMORY_UPDATE_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_PERSONA = SIMULATION_PERSONA_BUNDLE_PATH
DEFAULT_MEMORY = SIMULATION_MEMORY_BUNDLE_PATH
DEFAULT_TRAJECTORY = SIMULATION_CASE_TRAJECTORY_PATH
DEFAULT_FORMULATION = SIMULATION_CASE_FORMULATION_PATH
DEFAULT_ACTION_SEQUENCE = SIMULATION_ACTION_SEQUENCE_PATH
DEFAULT_OUTPUT = DEFAULT_THERAPIST_CASE_MEMORY_UPDATE_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--persona-jsonl", default=str(DEFAULT_PERSONA))
    parser.add_argument("--memory-jsonl", default=str(DEFAULT_MEMORY))
    parser.add_argument("--trajectory-jsonl", default=str(DEFAULT_TRAJECTORY))
    parser.add_argument("--formulation-jsonl", default=str(DEFAULT_FORMULATION))
    parser.add_argument("--action-sequence-jsonl", default=str(DEFAULT_ACTION_SEQUENCE))
    parser.add_argument("--patient-memory-report", required=True, help="Current patient-memory input.")
    parser.add_argument("--persona-tag", default="情绪")
    parser.add_argument("--memory-group-id", default="target::01_1_to_5_history_pairs")
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


def trim_unit_for_prompt(row: dict[str, Any], *, include_retrieval: bool = False) -> dict[str, Any]:
    trimmed = {
        "unit_id": row.get("unit_id"),
        "knowledge_type": row.get("knowledge_type"),
        "source": row.get("source"),
        "content": row.get("content"),
        "quality": row.get("quality"),
        "normalization": row.get("normalization"),
    }
    if include_retrieval:
        trimmed["retrieval"] = row.get("retrieval")
    return trimmed


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


def stage_hypothesis_from_patient_memory(patient_state_memory: dict[str, Any]) -> str:
    alliance = str(patient_state_memory.get("alliance_state") or "")
    disclosure = str(patient_state_memory.get("disclosure_depth") or "")
    readiness = str(patient_state_memory.get("readiness_level") or "")
    if alliance in {"pre_session_or_early_contact", "forming", "initiating"}:
        return "engagement"
    if disclosure in {"surface", "surface_to_moderate"}:
        return "assessment_or_problem_clarification"
    if alliance in {"working", "building"} or readiness in {"ambivalent", "ready_for_guided_exploration", "ready_to_reflect"}:
        return "working"
    return "unknown"


def build_grounded_previous_case_memory(patient_memory_report: dict[str, Any]) -> dict[str, Any]:
    patient_state_memory = (patient_memory_report.get("result") or {}).get("patient_state_memory") or {}
    current_emotion = [str(item) for item in (patient_state_memory.get("current_emotion") or []) if str(item).strip()]
    presenting = [str(item) for item in (patient_state_memory.get("presenting_problems") or []) if str(item).strip()]
    concerns = [str(item) for item in (patient_state_memory.get("current_interpersonal_concerns") or []) if str(item).strip()]
    cognition = [str(item) for item in (patient_state_memory.get("current_cognition") or []) if str(item).strip()]
    unresolved = [str(item) for item in (patient_state_memory.get("unresolved_threads") or []) if str(item).strip()]
    risk_state = patient_state_memory.get("risk_state") or {}
    risk_level = str(risk_state.get("level") or patient_state_memory.get("risk_state") or "unknown")
    risk_signals = [str(item) for item in (risk_state.get("signals") or []) if str(item).strip()]

    working_formulation: list[str] = []
    if presenting:
        working_formulation.append(f"当前主诉集中在：{'；'.join(presenting[:3])}。")
    if concerns:
        working_formulation.append(f"当前关系/互动困扰包括：{'；'.join(concerns[:2])}。")
    if cognition:
        working_formulation.append(f"来访者当前核心认知拉扯是：{cognition[0]}。")
    if risk_level in {"elevated", "high", "moderate"}:
        working_formulation.append("当前情绪负担与家庭情境提示需要持续关注风险升级与承受能力。")

    selected_response_strategy = ["validation", "emotion_clarification", "process_focusing"]
    readiness = str(patient_state_memory.get("readiness_level") or "")
    if "ambivalent" in readiness:
        selected_response_strategy.append("normalization_of_ambivalence")
    if risk_level in {"elevated", "high", "moderate"}:
        selected_response_strategy.append("risk_check_in")

    top_priorities = []
    if current_emotion:
        top_priorities.append(f"先承接来访者当前的{'、'.join(current_emotion[:3])}。")
    if unresolved:
        top_priorities.append(f"继续澄清未解决主题：{unresolved[0]}。")
    if risk_level in {"elevated", "high", "moderate"}:
        top_priorities.append("在不打断联盟的前提下补足风险关注与承受能力检查。")

    progress_markers = [str(item) for item in (patient_state_memory.get("last_therapist_move_impact") or [])[:3] if str(item).strip()]
    if patient_state_memory.get("disclosure_depth") in {"moderate", "high"}:
        progress_markers.append("来访者已进入中等以上披露深度。")

    return {
        "working_formulation": working_formulation[:4],
        "stage_hypothesis": stage_hypothesis_from_patient_memory(patient_state_memory),
        "top_priorities": top_priorities[:4],
        "ruled_out_or_deferred_paths": [
            "暂不做过度诊断",
            "暂不推动过快行动承诺",
        ],
        "selected_response_strategy": selected_response_strategy,
        "rationale_summary": [
            "优先以当前患者状态和最近对话为锚点维护工作记忆。",
            "在联盟与探索稳定前，不应让泛化 case 证据覆盖当前会话事实。",
            "若存在 elevated 风险或明显两难，应先做承接、澄清与风险关注。",
        ],
        "resource_map": {
            "client_strengths": [
                "能够表达当前困扰",
                "已出现一定程度的反思与自我观察",
            ],
            "support_systems": [],
            "available_process_levers": [
                "共情与验证",
                "开放式澄清",
                "过程聚焦",
            ],
        },
        "risk_watchpoints": risk_signals[:4],
        "progress_markers": progress_markers[:4],
        "repair_needs": [],
        "memory_confidence": "medium",
    }


def build_runtime_payload(
    therapist_persona_row: dict[str, Any],
    memory_row: dict[str, Any],
    trajectory_row: dict[str, Any],
    formulation_row: dict[str, Any],
    action_sequence_row: dict[str, Any],
    patient_memory_report: dict[str, Any],
) -> dict[str, Any]:
    previous_case_memory = build_grounded_previous_case_memory(patient_memory_report)
    recent_turns, current_patient_utterance = extract_runtime_dialogue_context(patient_memory_report)
    return {
        "task": "update_therapist_case_memory",
        "therapist_persona_bundle": trim_unit_for_prompt(therapist_persona_row),
        "memory_pattern_bundle": trim_unit_for_prompt(memory_row),
        "patient_state_memory": (patient_memory_report.get("result") or {}).get("patient_state_memory"),
        "case_trajectory": trim_unit_for_prompt(trajectory_row),
        "case_formulation_pattern": trim_unit_for_prompt(formulation_row),
        "action_sequence_pattern": trim_unit_for_prompt(action_sequence_row),
        "previous_therapist_case_memory": previous_case_memory,
        "recent_turn_window": recent_turns,
        "current_patient_utterance": current_patient_utterance,
        "update_goal": "update therapist working memory to reflect ambivalence after resource-focused progress and prepare the next response strategy",
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    persona_rows = read_jsonl(args.persona_jsonl)
    memory_rows = read_jsonl(args.memory_jsonl)
    trajectory_rows = read_jsonl(args.trajectory_jsonl)
    formulation_rows = read_jsonl(args.formulation_jsonl)
    action_sequence_rows = read_jsonl(args.action_sequence_jsonl)
    patient_memory_report = read_json(args.patient_memory_report)

    therapist_persona_row = select_unit(persona_rows, "tag", args.persona_tag)
    memory_row = select_unit(memory_rows, "group_id", args.memory_group_id)
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
        memory_row,
        trajectory_row,
        formulation_row,
        action_sequence_row,
        patient_memory_report,
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
        "schema_version": "theraskill_therapist_case_memory_update_v1",
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
            "case_source_id": args.case_source_id,
        },
        "prompt_path": args.prompt,
        "prompt_audit": prompt_audit,
        "input_summary": {
            "recent_turn_count": len(payload["recent_turn_window"]),
            "has_previous_case_memory": True,
            "therapist_persona_unit_id": therapist_persona_row.get("unit_id"),
            "memory_unit_id": memory_row.get("unit_id"),
            "trajectory_unit_id": trajectory_row.get("unit_id"),
            "formulation_unit_id": formulation_row.get("unit_id"),
            "action_sequence_unit_id": action_sequence_row.get("unit_id"),
        },
        "result": result,
        "error": error,
    }
    write_json(output_path, report, overwrite=args.overwrite)
    print(json.dumps({"status": status, "output": str(output_path), "estimated_prompt_tokens": prompt_audit["estimated_prompt_tokens"]}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
