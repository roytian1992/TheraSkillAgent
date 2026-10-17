#!/usr/bin/env python3
"""Run a small multi-turn patient/doctor simulation session prototype.

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
from io_utils import write_json  # noqa: E402
from simulation_runtime_helpers import build_case_evidence_bundle, trim_unit_for_prompt  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_SIMULATION_SESSION_PROTOTYPE_OUTPUT_PATH,
    PATIENT_STATE_MEMORY_UPDATE_PROMPT_PATH,
    PATIENT_TURN_GENERATION_PROMPT_PATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
    SESSION_REVIEWER_GATE_PROMPT_PATH,
    SIMULATION_ACTION_SEQUENCE_PATH,
    SIMULATION_ARCHETYPE_BUNDLE_PATH,
    SIMULATION_CASE_FORMULATION_PATH,
    SIMULATION_CASE_TRAJECTORY_PATH,
    SIMULATION_MEMORY_BUNDLE_PATH,
    SIMULATION_PERSONA_BUNDLE_PATH,
    THERAPIST_CASE_MEMORY_UPDATE_PROMPT_PATH,
    THERAPIST_TURN_GENERATION_PROMPT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_PERSONA = SIMULATION_PERSONA_BUNDLE_PATH
DEFAULT_MEMORY = SIMULATION_MEMORY_BUNDLE_PATH
DEFAULT_ARCHETYPE = SIMULATION_ARCHETYPE_BUNDLE_PATH
DEFAULT_TRAJECTORY = SIMULATION_CASE_TRAJECTORY_PATH
DEFAULT_FORMULATION = SIMULATION_CASE_FORMULATION_PATH
DEFAULT_ACTION_SEQUENCE = SIMULATION_ACTION_SEQUENCE_PATH
DEFAULT_PATIENT_MEMORY_PROMPT = PATIENT_STATE_MEMORY_UPDATE_PROMPT_PATH
DEFAULT_THERAPIST_MEMORY_PROMPT = THERAPIST_CASE_MEMORY_UPDATE_PROMPT_PATH
DEFAULT_THERAPIST_TURN_PROMPT = THERAPIST_TURN_GENERATION_PROMPT_PATH
DEFAULT_PATIENT_TURN_PROMPT = PATIENT_TURN_GENERATION_PROMPT_PATH
DEFAULT_SUMMARY_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_REVIEWER_PROMPT = SESSION_REVIEWER_GATE_PROMPT_PATH
DEFAULT_OUTPUT = DEFAULT_SIMULATION_SESSION_PROTOTYPE_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--persona-jsonl", default=str(DEFAULT_PERSONA), help="Persona bundle input used by this historical prototype surface.")
    parser.add_argument("--memory-jsonl", default=str(DEFAULT_MEMORY), help="Memory bundle input used by this historical prototype surface.")
    parser.add_argument("--archetype-jsonl", default=str(DEFAULT_ARCHETYPE), help="Archetype bundle input used by this historical prototype surface.")
    parser.add_argument("--trajectory-jsonl", default=str(DEFAULT_TRAJECTORY), help="Case trajectory input used by this historical prototype surface.")
    parser.add_argument("--formulation-jsonl", default=str(DEFAULT_FORMULATION), help="Case formulation input used by this historical prototype surface.")
    parser.add_argument("--action-sequence-jsonl", default=str(DEFAULT_ACTION_SEQUENCE), help="Action-sequence input used by this historical prototype surface.")
    parser.add_argument("--patient-memory-prompt", default=str(DEFAULT_PATIENT_MEMORY_PROMPT))
    parser.add_argument("--therapist-memory-prompt", default=str(DEFAULT_THERAPIST_MEMORY_PROMPT))
    parser.add_argument("--therapist-turn-prompt", default=str(DEFAULT_THERAPIST_TURN_PROMPT))
    parser.add_argument("--patient-turn-prompt", default=str(DEFAULT_PATIENT_TURN_PROMPT))
    parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    parser.add_argument("--persona-tag", default="情绪", help="Fixed lineage selector default for the prototype surface.")
    parser.add_argument("--memory-group-id", default="target::01_1_to_5_history_pairs", help="Fixed lineage selector default for the prototype surface.")
    parser.add_argument("--archetype-source-id", default="category:uncategorized", help="Fixed lineage selector default for the prototype surface.")
    parser.add_argument("--case-source-id", default="case_2517", help="Fixed lineage selector default for the prototype surface.")
    parser.add_argument("--max-turns", type=int, default=2)
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


def run_json_step(
    client: OpenAICompatibleClient,
    system_prompt: str,
    payload: dict[str, Any],
    *,
    parse_retries: int,
    max_prompt_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
    result = chat_json_with_repair(
        client,
        system_prompt,
        payload,
        parse_retries=parse_retries,
        force_json_first=True,
    )
    return result, audit


def build_initial_patient_memory() -> dict[str, Any]:
    return {
        "demographics_if_known": ["working_adult"],
        "presenting_problems": ["工作中的自我怀疑", "害怕他人评价"],
        "current_emotion": ["焦虑"],
        "current_cognition": ["担心自己不够好"],
        "current_interpersonal_concerns": ["害怕别人觉得自己不行"],
        "alliance_state": "forming",
        "disclosure_depth": "surface",
        "resistance_signals": ["先退一下，不太敢说话"],
        "readiness_level": "ready_for_exploration",
        "risk_state": {"level": "low", "signals": ["情绪负荷"], "change": "unchanged"},
        "explicit_goals": ["想更稳一点面对工作挑战"],
        "hidden_concerns": ["怕自己其实真的不够好"],
        "salient_prior_events": ["工作中一遇到难题就自我怀疑"],
        "unresolved_threads": ["最怕发生什么", "如何不那么快退缩"],
        "last_therapist_move_impact": ["来访者感到被理解"],
        "memory_confidence": "medium",
    }


def build_initial_therapist_memory() -> dict[str, Any]:
    return {
        "working_formulation": [
            "来访者在工作场景中存在明显自我怀疑。",
            "回避和预期性羞耻可能在维持问题。"
        ],
        "stage_hypothesis": "working",
        "top_priorities": ["继续澄清核心担忧"],
        "ruled_out_or_deferred_paths": ["暂不急于给行为任务"],
        "selected_response_strategy": ["validation", "clarifying_question"],
        "rationale_summary": ["联盟尚可，可继续探索触发点。"],
        "resource_map": {
            "client_strengths": ["能观察到自己的退缩"],
            "support_systems": [],
            "available_process_levers": ["共情", "澄清", "资源建构"]
        },
        "risk_watchpoints": [],
        "progress_markers": ["开始描述自动想法"],
        "repair_needs": [],
        "memory_confidence": "medium",
    }


def build_seed_transcript() -> list[dict[str, str]]:
    return [
        {
            "role": "patient",
            "text": "我最近工作里一遇到难一点的情况就会很快怀疑自己，感觉别人会不会也觉得我不行。"
        },
        {
            "role": "therapist",
            "text": "听起来那种‘我是不是不行’的念头一冒出来，就会很快把你整个人都压住。"
        },
        {
            "role": "patient",
            "text": "是，而且我有时候明明知道不一定真是这样，但还是会先退一下，不太敢说话。"
        },
    ]


def should_stop(patient_memory: dict[str, Any], patient_utterance: str, turn_index: int, max_turns: int) -> tuple[bool, str]:
    if turn_index >= max_turns:
        return True, "max_turns_reached"
    risk_level = str(((patient_memory or {}).get("risk_state") or {}).get("level") or "unknown")
    if risk_level in {"moderate", "high"}:
        return True, f"risk_level_{risk_level}"
    if "不知道" not in patient_utterance and "一点点" not in patient_utterance and "也许" not in patient_utterance:
        if "可以" in patient_utterance or "试" in patient_utterance:
            return True, "patient_action_readiness_signal"
    return False, "continue"


def build_summary_payload(
    transcript: list[dict[str, str]],
    turn_records: list[dict[str, Any]],
    final_patient_state_memory: dict[str, Any],
    final_therapist_case_memory: dict[str, Any],
    stop_reason: str,
) -> dict[str, Any]:
    return {
        "task": "build_session_longitudinal_summary",
        "transcript": transcript,
        "turn_records": turn_records,
        "final_patient_state_memory": final_patient_state_memory,
        "final_therapist_case_memory": final_therapist_case_memory,
        "session_meta": {
            "turn_count_completed": len(turn_records),
            "stop_reason": stop_reason,
        },
    }


def build_reviewer_payload(
    transcript: list[dict[str, str]],
    turn_records: list[dict[str, Any]],
    session_longitudinal_summary: dict[str, Any],
    final_patient_state_memory: dict[str, Any],
    final_therapist_case_memory: dict[str, Any],
    stop_reason: str,
) -> dict[str, Any]:
    return {
        "task": "review_simulation_session",
        "transcript": transcript,
        "turn_records": turn_records,
        "session_longitudinal_summary": session_longitudinal_summary,
        "final_patient_state_memory": final_patient_state_memory,
        "final_therapist_case_memory": final_therapist_case_memory,
        "session_meta": {
            "turn_count_completed": len(turn_records),
            "stop_reason": stop_reason,
        },
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"output exists: {output_path}")

    persona_rows = read_jsonl(args.persona_jsonl)
    memory_rows = read_jsonl(args.memory_jsonl)
    archetype_rows = read_jsonl(args.archetype_jsonl)
    trajectory_rows = read_jsonl(args.trajectory_jsonl)
    formulation_rows = read_jsonl(args.formulation_jsonl)
    action_sequence_rows = read_jsonl(args.action_sequence_jsonl)

    persona_row = select_unit(persona_rows, "tag", args.persona_tag)
    memory_row = select_unit(memory_rows, "group_id", args.memory_group_id)
    archetype_row = select_unit(archetype_rows, "source_id", args.archetype_source_id)
    trajectory_row = select_unit(trajectory_rows, "source_id", args.case_source_id)
    formulation_row = select_unit(formulation_rows, "source_id", args.case_source_id)
    action_sequence_row = select_unit(action_sequence_rows, "source_id", args.case_source_id)
    case_evidence_bundle = build_case_evidence_bundle(trajectory_row, formulation_row, action_sequence_row)

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    client = OpenAICompatibleClient(model_config)

    patient_memory_prompt = load_prompt(args.patient_memory_prompt)
    therapist_memory_prompt = load_prompt(args.therapist_memory_prompt)
    therapist_turn_prompt = load_prompt(args.therapist_turn_prompt)
    patient_turn_prompt = load_prompt(args.patient_turn_prompt)
    summary_prompt = load_prompt(args.summary_prompt)
    reviewer_prompt = load_prompt(args.reviewer_prompt)

    transcript = build_seed_transcript()
    patient_memory = build_initial_patient_memory()
    therapist_memory = build_initial_therapist_memory()
    latest_patient_utterance = transcript[-1]["text"]
    latest_therapist_utterance = "当你说自己会先退一下的时候，我有点在想，那一刻你最怕发生的事情通常是什么？"

    turn_records: list[dict[str, Any]] = []
    prompt_audit_records: list[dict[str, Any]] = []
    stop_reason = "max_turns_reached"

    try:
        for turn_index in range(1, args.max_turns + 1):
            therapist_memory_payload = {
                "task": "update_therapist_case_memory",
                "therapist_persona_bundle": trim_unit_for_prompt(persona_row),
                "memory_pattern_bundle": trim_unit_for_prompt(memory_row),
                "patient_state_memory": patient_memory,
                "case_trajectory": trim_unit_for_prompt(trajectory_row),
                "case_formulation_pattern": trim_unit_for_prompt(formulation_row),
                "action_sequence_pattern": trim_unit_for_prompt(action_sequence_row),
                "previous_therapist_case_memory": therapist_memory,
                "recent_turn_window": transcript[-6:],
                "current_patient_utterance": latest_patient_utterance,
                "update_goal": "update therapist case memory for the next turn in an ongoing simulation session",
            }
            therapist_memory_result, therapist_memory_audit = run_json_step(
                client,
                therapist_memory_prompt,
                therapist_memory_payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
            )
            therapist_memory = therapist_memory_result.get("therapist_case_memory") or therapist_memory

            therapist_turn_payload = {
                "task": "generate_therapist_turn",
                "therapist_persona_bundle": trim_unit_for_prompt(persona_row),
                "patient_state_memory": patient_memory,
                "therapist_case_memory": therapist_memory,
                "case_evidence_bundle": case_evidence_bundle,
                "recent_turn_window": transcript[-6:],
                "current_patient_utterance": latest_patient_utterance,
                "generation_goal": "generate the next therapist reply in an ongoing simulation session",
            }
            therapist_turn_result, therapist_turn_audit = run_json_step(
                client,
                therapist_turn_prompt,
                therapist_turn_payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
            )
            latest_therapist_utterance = therapist_turn_result.get("therapist_utterance") or ""
            transcript.append({"role": "therapist", "text": latest_therapist_utterance})

            patient_memory_payload = {
                "task": "update_patient_state_memory",
                "persona_bundle": trim_unit_for_prompt(persona_row),
                "memory_pattern_bundle": trim_unit_for_prompt(memory_row),
                "archetype_bundle": trim_unit_for_prompt(archetype_row),
                "previous_patient_state_memory": patient_memory,
                "recent_turn_window": transcript[-6:],
                "current_therapist_utterance": latest_therapist_utterance,
                "update_goal": "update patient state after the newest therapist reply in an ongoing simulation session",
            }
            patient_memory_result, patient_memory_audit = run_json_step(
                client,
                patient_memory_prompt,
                patient_memory_payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
            )
            patient_memory = patient_memory_result.get("patient_state_memory") or patient_memory

            patient_turn_payload = {
                "task": "generate_patient_turn",
                "persona_bundle": trim_unit_for_prompt(persona_row),
                "archetype_bundle": trim_unit_for_prompt(archetype_row),
                "patient_state_memory": patient_memory,
                "therapist_case_memory": therapist_memory,
                "recent_turn_window": transcript[-6:],
                "current_therapist_utterance": latest_therapist_utterance,
                "generation_goal": "generate the next patient reply in an ongoing simulation session",
            }
            patient_turn_result, patient_turn_audit = run_json_step(
                client,
                patient_turn_prompt,
                patient_turn_payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
            )
            latest_patient_utterance = patient_turn_result.get("patient_utterance") or ""
            transcript.append({"role": "patient", "text": latest_patient_utterance})

            therapist_memory_after_patient_payload = {
                "task": "update_therapist_case_memory",
                "therapist_persona_bundle": trim_unit_for_prompt(persona_row),
                "memory_pattern_bundle": trim_unit_for_prompt(memory_row),
                "patient_state_memory": patient_memory,
                "case_trajectory": trim_unit_for_prompt(trajectory_row),
                "case_formulation_pattern": trim_unit_for_prompt(formulation_row),
                "action_sequence_pattern": trim_unit_for_prompt(action_sequence_row),
                "previous_therapist_case_memory": therapist_memory,
                "recent_turn_window": transcript[-6:],
                "current_patient_utterance": latest_patient_utterance,
                "update_goal": "write back therapist case memory after the newly generated patient reply",
            }
            therapist_memory_after_patient_result, therapist_memory_after_patient_audit = run_json_step(
                client,
                therapist_memory_prompt,
                therapist_memory_after_patient_payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
            )
            therapist_memory = therapist_memory_after_patient_result.get("therapist_case_memory") or therapist_memory

            turn_records.append(
                {
                    "turn_index": turn_index,
                    "therapist_turn": therapist_turn_result,
                    "patient_turn": patient_turn_result,
                    "patient_state_memory": patient_memory,
                    "therapist_case_memory": therapist_memory,
                }
            )
            prompt_audit_records.append(
                {
                    "turn_index": turn_index,
                    "therapist_case_memory_before_turn": therapist_memory_audit,
                    "therapist_turn_generation": therapist_turn_audit,
                    "patient_state_memory_after_therapist_turn": patient_memory_audit,
                    "patient_turn_generation": patient_turn_audit,
                    "therapist_case_memory_after_patient_turn": therapist_memory_after_patient_audit,
                }
            )

            stop, stop_reason = should_stop(patient_memory, latest_patient_utterance, turn_index, args.max_turns)
            if stop:
                break

        summary_payload = build_summary_payload(
            transcript,
            turn_records,
            patient_memory,
            therapist_memory,
            stop_reason,
        )
        summary_result, summary_audit = run_json_step(
            client,
            summary_prompt,
            summary_payload,
            parse_retries=parse_retries,
            max_prompt_tokens=max_prompt_tokens,
        )
        reviewer_payload = build_reviewer_payload(
            transcript,
            turn_records,
            summary_result.get("session_longitudinal_summary") or {},
            patient_memory,
            therapist_memory,
            stop_reason,
        )
        reviewer_result, reviewer_audit = run_json_step(
            client,
            reviewer_prompt,
            reviewer_payload,
            parse_retries=parse_retries,
            max_prompt_tokens=max_prompt_tokens,
        )

        status = "complete"
        error = None
    except PromptBudgetExceededError as exc:
        status = "prompt_budget_exceeded"
        error = {"type": "PromptBudgetExceededError", "details": exc.report}
        summary_result = None
        summary_audit = None
        reviewer_result = None
        reviewer_audit = None
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = {"type": type(exc).__name__, "details": str(exc)}
        summary_result = None
        summary_audit = None
        reviewer_result = None
        reviewer_audit = None

    report = {
        "schema_version": "theraskill_simulation_session_prototype_v1",
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
            "case_source_id": args.case_source_id,
            "max_turns": args.max_turns,
        },
        "stop_reason": stop_reason,
        "turn_count_completed": len(turn_records),
        "transcript": transcript,
        "turn_records": turn_records,
        "prompt_audit_records": prompt_audit_records,
        "final_patient_state_memory": patient_memory,
        "final_therapist_case_memory": therapist_memory,
        "session_longitudinal_summary": summary_result,
        "session_reviewer_gate": reviewer_result,
        "session_level_prompt_audits": {
            "summary": summary_audit,
            "reviewer_gate": reviewer_audit,
        },
        "error": error,
    }
    write_json(output_path, report)
    summary = {
        "status": status,
        "output": str(output_path),
        "turn_count_completed": len(turn_records),
        "stop_reason": stop_reason,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
