#!/usr/bin/env python3
"""Shared longitudinal runtime helpers for patient simulator flows."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_patient_single_session_loop import (  # noqa: E402
    apply_memory_update_result,
    calibrate_state_result_risk,
    build_disclosure_payload,
    build_memory_update_payload,
    build_move_payload,
    build_reviewer_payload,
    build_state_payload,
    build_summary_payload,
    build_turn_payload,
    run_json_step,
)
from domains.theraskill.theraskill_llm import OpenAICompatibleClient  # noqa: E402


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def infer_worry_line(carryover: dict[str, Any]) -> str:
    carry_focus = (carryover.get("next_session_bootstrap") or {}).get("carry_forward_focus") or []
    do_not_forget = (carryover.get("next_session_bootstrap") or {}).get("do_not_forget") or []
    last_patient_turn = ((carryover.get("cross_session_trace") or {}).get("last_patient_turn")) or ""

    worry_line = "如果我现在不管，以后会不会更糟。"
    if "更痛苦的人" in last_patient_turn:
        worry_line = "我要是一开口，会不会又变成那个让他更痛苦的人。"
    elif any("错过干预时机" in item for item in do_not_forget + carry_focus):
        worry_line = "如果我现在再退一步，会不会就是我又错过了时机。"
    elif any("成绩" in item or "学业" in item for item in do_not_forget + carry_focus):
        worry_line = "如果我现在不问成绩，会不会真的一步步耽误了他。"
    return worry_line


def build_followup_seed_transcript(carryover: dict[str, Any], session_index: int) -> list[dict[str, str]]:
    worry_line = infer_worry_line(carryover)
    if session_index <= 2:
        return [
            {
                "role": "patient",
                "text": "这周我有几次都差点又去盯他的成绩，后来想到上次说的那些话，我就更怕自己一开口又会把他推远。"
            },
            {
                "role": "therapist",
                "text": "你已经开始更早地察觉到那股着急了，而且也在试着不让它立刻变成行动。"
            },
            {
                "role": "patient",
                "text": f"嗯，可我心里还是会冒出那种声音：{worry_line}"
            },
        ]

    return [
        {
            "role": "patient",
            "text": "这周我有一次先忍住了，没有立刻追着问成绩，可晚上还是会反复想，我这样退一步到底是在帮他，还是只是我不敢碰了。"
        },
        {
            "role": "therapist",
            "text": "你已经不只是卡在要不要管，而是在分辨什么叫靠近、什么又会变成被恐惧推着的控制。"
        },
        {
            "role": "patient",
            "text": f"对，而且我脑子里还是会冒出一句话：{worry_line} 只是现在我会多停一下。"
        },
    ]


def build_runtime_carryover(
    session_payload: dict[str, Any],
    *,
    case_id: str | None,
    scenario_id: str | None,
    source_session_label: str,
) -> dict[str, Any]:
    summary = ((session_payload.get("session_longitudinal_summary") or {}).get("result") or {}).get("session_longitudinal_summary") or {}
    compression = ((session_payload.get("session_longitudinal_summary") or {}).get("result") or {}).get("compression_trace") or {}
    final_state = session_payload.get("final_session_state") or {}
    final_memory_seed = session_payload.get("final_memory_seed") or {}
    transcript = session_payload.get("transcript") or []

    last_patient_turn = ""
    last_therapist_turn = ""
    for item in reversed(transcript):
        if item.get("role") == "patient" and not last_patient_turn:
            last_patient_turn = item.get("text") or ""
        if item.get("role") == "therapist" and not last_therapist_turn:
            last_therapist_turn = item.get("text") or ""
        if last_patient_turn and last_therapist_turn:
            break

    return {
        "schema_version": "theraskill_patient_longitudinal_carryover_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "carryover_id": f"carryover::{stable_hash(f'{case_id or ''}::{source_session_label}')}",
        "source_session_label": source_session_label,
        "case_id": case_id,
        "scenario_id": scenario_id,
        "previous_session_summary": {
            "case_progress_summary": summary.get("case_progress_summary") or [],
            "key_turning_points": summary.get("key_turning_points") or [],
            "active_unresolved_threads": summary.get("active_unresolved_threads") or [],
            "next_turn_setup": summary.get("next_turn_setup") or [],
            "boundary_or_risk_watchpoints": summary.get("boundary_or_risk_watchpoints") or [],
        },
        "next_session_bootstrap": {
            "session_opening_state": {
                "emotion_state": final_state.get("emotion_state") or [],
                "alliance_state": final_state.get("alliance_state"),
                "disclosure_state": final_state.get("disclosure_state"),
                "resistance_state": final_state.get("resistance_state"),
                "readiness_state": final_state.get("readiness_state"),
                "risk_state": final_state.get("risk_state"),
                "topic_focus": final_state.get("topic_focus") or [],
                "felt_understanding": final_state.get("felt_understanding"),
                "unfinished_threads": final_state.get("unfinished_threads") or [],
            },
            "rolling_memory_seed": final_memory_seed,
            "carry_forward_focus": list(summary.get("next_turn_setup") or [])[:10],
            "do_not_forget": list(summary.get("boundary_or_risk_watchpoints") or [])[:10],
            "resume_suggestion": "Reconnect with the most active unresolved fear or responsibility theme before introducing a new focus.",
        },
        "cross_session_trace": {
            "last_patient_turn": last_patient_turn,
            "last_therapist_turn": last_therapist_turn,
            "compression_keep_set": compression.get("kept_from_transcript") or [],
            "compression_uncertainties": compression.get("uncertainties") or [],
        },
    }


def run_session(
    client: OpenAICompatibleClient,
    prompts: dict[str, str],
    bundle: dict[str, Any],
    session_state: dict[str, Any],
    memory_seed: dict[str, Any],
    transcript_seed: list[dict[str, str]],
    therapist_turns: list[str],
    *,
    parse_retries: int,
    max_prompt_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    transcript = copy.deepcopy(transcript_seed)
    current_state = copy.deepcopy(session_state)
    current_memory = copy.deepcopy(memory_seed)
    turn_records: list[dict[str, Any]] = []
    step_audits: list[dict[str, Any]] = []

    for turn_index, therapist_utterance in enumerate(therapist_turns, start=1):
        move_payload = build_move_payload(bundle, current_state, transcript, therapist_utterance)
        move_result, move_audit = run_json_step(client, prompts["move"], move_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

        disclosure_payload = build_disclosure_payload(bundle, current_state, current_memory, transcript, therapist_utterance, move_result)
        disclosure_result, disclosure_audit = run_json_step(client, prompts["disclosure"], disclosure_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

        turn_payload = build_turn_payload(bundle, current_state, current_memory, transcript, therapist_utterance, disclosure_result)
        turn_result, turn_audit = run_json_step(client, prompts["turn"], turn_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

        state_payload = build_state_payload(bundle, current_state, current_memory, therapist_utterance, move_result, disclosure_result, turn_result)
        state_result, state_audit = run_json_step(client, prompts["state"], state_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)
        state_result, risk_guardrail_applied = calibrate_state_result_risk(
            current_state,
            therapist_utterance,
            disclosure_result,
            turn_result,
            state_result,
        )

        transcript.extend(
            [
                {"role": "therapist", "text": therapist_utterance},
                {"role": "patient", "text": turn_result.get("patient_utterance") or ""},
            ]
        )
        current_state = state_result.get("updated_session_state") or current_state
        patient_utterance = turn_result.get("patient_utterance") or ""
        memory_payload = build_memory_update_payload(
            bundle,
            current_memory,
            transcript,
            therapist_utterance,
            patient_utterance,
            move_result,
            disclosure_result,
            state_result,
        )
        memory_result, memory_audit = run_json_step(
            client, prompts["memory"], memory_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
        )
        current_memory, memory_fallback_used = apply_memory_update_result(
            current_memory, state_result, memory_result
        )

        turn_records.append(
            {
                "turn_index": turn_index,
                "therapist_utterance": therapist_utterance,
                "therapist_move_interpretation": move_result.get("therapist_move_interpretation"),
                "disclosure_decision": disclosure_result.get("disclosure_decision"),
                "patient_turn_result": turn_result,
                "state_update_result": state_result,
                "risk_guardrail_applied": risk_guardrail_applied,
                "memory_update_result": memory_result,
                "memory_update_fallback_used": memory_fallback_used,
            }
        )
        step_audits.append(
            {
                "turn_index": turn_index,
                "move_prompt_tokens": move_audit["estimated_prompt_tokens"],
                "disclosure_prompt_tokens": disclosure_audit["estimated_prompt_tokens"],
                "turn_prompt_tokens": turn_audit["estimated_prompt_tokens"],
                "state_prompt_tokens": state_audit["estimated_prompt_tokens"],
                "memory_prompt_tokens": memory_audit["estimated_prompt_tokens"],
            }
        )

    summary_payload = build_summary_payload(transcript, turn_records, current_state, current_memory)
    summary_result, summary_audit = run_json_step(client, prompts["summary"], summary_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

    reviewer_payload = build_reviewer_payload(bundle, transcript, turn_records, current_state, summary_result)
    reviewer_result, reviewer_audit = run_json_step(client, prompts["reviewer"], reviewer_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

    return (
        current_state,
        current_memory,
        turn_records,
        transcript,
        step_audits,
        {"result": summary_result, "audit": summary_audit},
        {"result": reviewer_result, "audit": reviewer_audit},
    )
