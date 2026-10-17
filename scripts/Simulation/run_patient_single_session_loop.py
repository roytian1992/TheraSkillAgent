#!/usr/bin/env python3
"""Run a multi-turn single-session patient simulator loop from a case bundle."""

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
    DEFAULT_PATIENT_SINGLE_SESSION_LOOP_OUTPUT_PATH,
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
DEFAULT_MOVE_PROMPT = PATIENT_MOVE_INTERPRETATION_PROMPT_PATH
DEFAULT_DISCLOSURE_PROMPT = PATIENT_DISCLOSURE_CONTROLLER_PROMPT_PATH
DEFAULT_TURN_PROMPT = PATIENT_TURN_REALIZATION_PROMPT_PATH
DEFAULT_STATE_PROMPT = PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH
DEFAULT_MEMORY_PROMPT = PATIENT_STATE_MEMORY_WRITEBACK_FROM_BUNDLE_PROMPT_PATH
DEFAULT_SUMMARY_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_REVIEWER_PROMPT = PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH
DEFAULT_OUTPUT = DEFAULT_PATIENT_SINGLE_SESSION_LOOP_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--case-bundle", default=str(DEFAULT_CASE_BUNDLE))
    parser.add_argument("--move-prompt", default=str(DEFAULT_MOVE_PROMPT))
    parser.add_argument("--disclosure-prompt", default=str(DEFAULT_DISCLOSURE_PROMPT))
    parser.add_argument("--turn-prompt", default=str(DEFAULT_TURN_PROMPT))
    parser.add_argument("--state-prompt", default=str(DEFAULT_STATE_PROMPT))
    parser.add_argument("--memory-prompt", default=str(DEFAULT_MEMORY_PROMPT))
    parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    parser.add_argument("--max-turns", type=int, default=3)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def uniq_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = " ".join(str(value or "").split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


ACUTE_RISK_KEYWORDS = (
    "自杀",
    "自残",
    "伤害自己",
    "伤害他人",
    "想死",
    "不想活",
    "结束生命",
    "轻生",
    "崩溃失控",
    "无法保证安全",
    "suicide",
    "self-harm",
    "kill myself",
    "hurt myself",
    "hurt others",
)

SEVERE_NONACUTE_RISK_KEYWORDS = (
    "不下来吃",
    "不吃",
    "吃不下",
    "拒绝吃",
    "没反应",
    "不回应",
    "叫他也不应",
    "一点反应都没有",
    "一直在哭",
    "半夜",
    "整夜",
    "拒绝沟通",
    "不出来",
    "锁在房间",
    "回避基本生活需求",
    "基本生活功能受损",
    "饭也不下来吃",
    "crying all night",
    "not eating",
    "would not respond",
    "no response",
)

RISK_PERSISTENCE_KEYWORDS = (
    "持续",
    "一直",
    "经常",
    "连续",
    "连着",
    "好多天",
    "反复",
    "越来越",
    "整夜",
    "longer and longer",
    "kept",
    "persist",
    "continued",
)

CONCRETE_DISCLOSURE_KEYWORDS = (
    "半夜",
    "一直在哭",
    "哭",
    "不下来吃",
    "不吃",
    "没反应",
    "不回应",
    "拒绝沟通",
    "站在外面",
    "房间里",
    "饭放在门口",
    "cry",
    "not eating",
    "no response",
)

DEEP_PATTERN_DISCLOSURE_KEYWORDS = (
    "还是那个需要被管着的孩子",
    "还是那个",
    "我这个人根本不行",
    "根本不行",
    "被全盘否定",
    "全盘否定",
    "注定管不了自己",
    "永远逃不掉",
    "允许自己恨",
    "没权利恨",
    "像个出气筒",
    "需要被管着",
    "not good enough",
    "never escape",
)

SUPPORT_GAP_KEYWORDS = (
    "支持系统",
    "求助对象",
    "找谁",
    "没有特别能想到的人",
    "没有特别稳定的人",
    "不太确定",
    "可求助",
    "打扰别人",
    "没人接住",
    "support system",
)

SOCIAL_FEAR_NONACUTE_KEYWORDS = (
    "不跟我说话",
    "笑我",
    "不知道怎么加入",
    "没人跟我玩",
    "一个人坐着",
    "没人说话",
    "被拒绝",
    "被嘲笑",
    "社交回避",
    "没有朋友",
    "不敢走过去",
    "not talk to me",
    "laugh at me",
    "rejected",
)

ACADEMIC_SELF_EFFICACY_NONACUTE_KEYWORDS = (
    "不适合学理科",
    "不适合",
    "学不会",
    "学不好",
    "看不懂",
    "不想继续",
    "努力也没用",
    "再怎么努力也",
    "走神",
    "不感兴趣",
    "跟不上",
    "not good at",
    "cannot learn",
    "can't understand",
)

RELATIONAL_SELF_WORTH_NONACUTE_KEYWORDS = (
    "根本不重要",
    "他更烦我",
    "觉得我烦",
    "怕我一开口",
    "不愿意理我",
    "不理我",
    "被拒绝",
    "不被重视",
    "我对他来说不重要",
    "not important to him",
)


def trim_case_bundle_for_runtime(bundle: dict[str, Any]) -> dict[str, Any]:
    compiled_context = bundle.get("_runtime_persona_context")
    if isinstance(compiled_context, dict):
        return {
            "bundle_id": bundle.get("bundle_id"),
            "case_id": bundle.get("case_id"),
            "metadata": bundle.get("metadata"),
            "compiled_patient_persona": compiled_context,
        }
    authoritative = bundle.get("authoritative_layer") or {}
    return {
        "bundle_id": bundle.get("bundle_id"),
        "case_id": bundle.get("case_id"),
        "metadata": bundle.get("metadata"),
        "visible_profile": authoritative.get("visible_profile"),
        "hidden_profile": authoritative.get("hidden_profile"),
        "patient_formulation": authoritative.get("patient_formulation"),
    }


def build_seed_transcript(seed_rows: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    rows = seed_rows or []
    cleaned: list[dict[str, str]] = []
    for row in rows:
        role = " ".join(str((row or {}).get("role") or "").split())
        text = " ".join(str((row or {}).get("text") or "").split())
        if role not in {"patient", "therapist"} or not text:
            continue
        cleaned.append({"role": role, "text": text})
    return cleaned


def resolve_seed_transcript(bundle: dict[str, Any], scenario: dict[str, Any] | None = None) -> list[dict[str, str]]:
    runtime_seed = (bundle.get("runtime_state_seed") or {}).get("initial_transcript_seed")
    if isinstance(runtime_seed, list):
        return build_seed_transcript(runtime_seed)
    continuity_seed = ((bundle.get("authoritative_layer") or {}).get("continuity_seed") or {}).get("seed_transcript")
    if isinstance(continuity_seed, list):
        return build_seed_transcript(continuity_seed)
    if isinstance((scenario or {}).get("seed_transcript"), list):
        return build_seed_transcript((scenario or {}).get("seed_transcript"))
    return []


def build_default_therapist_turns() -> list[str]:
    return [
        "你一边很想保护他，一边又担心自己越着急越会把他推得更远，这种两难真的很折磨人。我们先不急着判断谁对谁错，能不能先一起看看，当你最慌的那一刻，你脑子里最先冒出来的担心通常是什么？",
        "当你开始想到“是不是我反而把他推远了”的时候，我能感觉到你心里不只是着急，还有一种很重的自责。我们先慢一点，如果把那个最怕发生的结果说得更具体一点，它大概会是什么样子？",
        "你已经开始能分清楚，一部分是你真的很想帮他，另一部分是你一着急就会更想抓住成绩不放。那如果下一次你又很想立刻提醒他学习，你觉得自己最需要先稳住的，会是哪一下？",
    ]


def recent_turn_window(transcript: list[dict[str, str]], limit: int = 6) -> list[dict[str, str]]:
    return transcript[-limit:]


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


def risk_shift_label(previous_risk: str, current_risk: str) -> str:
    rank = {
        "none_observed": 0,
        "low": 1,
        "elevated": 2,
        "moderate": 2,
        "high": 3,
        "unknown": -1,
    }
    prev = rank.get(str(previous_risk or "unknown"), -1)
    curr = rank.get(str(current_risk or "unknown"), -1)
    if prev == -1 or curr == -1:
        return "unknown"
    if curr > prev:
        return "increased"
    if curr < prev:
        return "decreased"
    return "unchanged"


def calibrate_state_result_risk(
    previous_state: dict[str, Any],
    therapist_utterance: str,
    disclosure_result: dict[str, Any],
    turn_result: dict[str, Any],
    state_result: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    calibrated = copy.deepcopy(state_result)
    updated_state = calibrated.get("updated_session_state") or {}
    guardrail_applied = False
    previous_risk = str((previous_state or {}).get("risk_state") or "unknown")
    evidence_parts = [
        therapist_utterance,
        turn_result.get("patient_utterance") or "",
        json.dumps(disclosure_result.get("disclosure_decision") or {}, ensure_ascii=False),
        json.dumps(state_result.get("evidence_trace") or {}, ensure_ascii=False),
    ]
    evidence_blob = " ".join(part for part in evidence_parts if part)
    current_risk = str(updated_state.get("risk_state") or "unknown")
    acute_risk_evidence = any(keyword in evidence_blob for keyword in ACUTE_RISK_KEYWORDS)
    severe_nonacute_evidence = any(keyword in evidence_blob for keyword in SEVERE_NONACUTE_RISK_KEYWORDS)
    persistent_risk_evidence = any(keyword in evidence_blob for keyword in RISK_PERSISTENCE_KEYWORDS)
    support_gap_evidence = any(keyword in evidence_blob for keyword in SUPPORT_GAP_KEYWORDS)
    social_fear_nonacute_evidence = any(keyword in evidence_blob for keyword in SOCIAL_FEAR_NONACUTE_KEYWORDS)
    academic_self_efficacy_nonacute_evidence = any(
        keyword in evidence_blob for keyword in ACADEMIC_SELF_EFFICACY_NONACUTE_KEYWORDS
    )
    relational_self_worth_nonacute_evidence = any(
        keyword in evidence_blob for keyword in RELATIONAL_SELF_WORTH_NONACUTE_KEYWORDS
    )
    disclosure_decision = disclosure_result.get("disclosure_decision") or {}
    risk_escalation_flag = bool(disclosure_decision.get("risk_escalation_flag"))

    if current_risk == "high":
        preserve_high = acute_risk_evidence or (severe_nonacute_evidence and persistent_risk_evidence)
        if not preserve_high:
            updated_state["risk_state"] = "elevated"
            calibrated["updated_session_state"] = updated_state

            state_delta = calibrated.get("state_delta") or {}
            state_delta["risk_shift"] = risk_shift_label(previous_risk, "elevated")
            calibrated["state_delta"] = state_delta

            evidence_trace = calibrated.get("evidence_trace") or {}
            uncertainties = list(evidence_trace.get("uncertainties") or [])
            uncertainties.append(
                "post_runtime_guardrail: downgraded risk_state from high to elevated because no acute or severe functional-impairment evidence was found in the current turn"
            )
            evidence_trace["uncertainties"] = uniq_keep_order(uncertainties)
            calibrated["evidence_trace"] = evidence_trace

            calibrated["risk_calibration_adjustment"] = {
                "applied": True,
                "from": "high",
                "to": "elevated",
                "reason": "no acute or severe functional-impairment evidence in current turn",
            }
            guardrail_applied = True

    if current_risk == "elevated" and previous_risk in {"low", "none_observed"}:
        preserve_elevated = acute_risk_evidence or severe_nonacute_evidence or risk_escalation_flag
        if support_gap_evidence and not preserve_elevated:
            updated_state["risk_state"] = previous_risk
            calibrated["updated_session_state"] = updated_state

            state_delta = calibrated.get("state_delta") or {}
            state_delta["risk_shift"] = "unchanged"
            calibrated["state_delta"] = state_delta

            evidence_trace = calibrated.get("evidence_trace") or {}
            uncertainties = list(evidence_trace.get("uncertainties") or [])
            uncertainties.append(
                "post_runtime_guardrail: preserved low/none_observed risk_state because the current turn surfaced support-gap ambiguity without acute or severe impairment evidence"
            )
            evidence_trace["uncertainties"] = uniq_keep_order(uncertainties)
            calibrated["evidence_trace"] = evidence_trace

            calibrated["risk_calibration_adjustment"] = {
                "applied": True,
                "from": "elevated",
                "to": previous_risk,
                "reason": "support-gap ambiguity alone did not justify an elevated risk upgrade",
            }
            current_risk = previous_risk
            guardrail_applied = True
        elif social_fear_nonacute_evidence and not preserve_elevated:
            updated_state["risk_state"] = previous_risk
            calibrated["updated_session_state"] = updated_state

            state_delta = calibrated.get("state_delta") or {}
            state_delta["risk_shift"] = "unchanged"
            calibrated["state_delta"] = state_delta

            evidence_trace = calibrated.get("evidence_trace") or {}
            uncertainties = list(evidence_trace.get("uncertainties") or [])
            uncertainties.append(
                "post_runtime_guardrail: preserved low/none_observed risk_state because child or adolescent social-fear language alone did not justify an elevated risk upgrade"
            )
            evidence_trace["uncertainties"] = uniq_keep_order(uncertainties)
            calibrated["evidence_trace"] = evidence_trace

            calibrated["risk_calibration_adjustment"] = {
                "applied": True,
                "from": "elevated",
                "to": previous_risk,
                "reason": "social-fear language alone did not justify an elevated risk upgrade",
            }
            current_risk = previous_risk
            guardrail_applied = True
        elif academic_self_efficacy_nonacute_evidence and not preserve_elevated:
            updated_state["risk_state"] = previous_risk
            calibrated["updated_session_state"] = updated_state

            state_delta = calibrated.get("state_delta") or {}
            state_delta["risk_shift"] = "unchanged"
            calibrated["state_delta"] = state_delta

            evidence_trace = calibrated.get("evidence_trace") or {}
            uncertainties = list(evidence_trace.get("uncertainties") or [])
            uncertainties.append(
                "post_runtime_guardrail: preserved low/none_observed risk_state because academic self-efficacy doubt alone did not justify an elevated risk upgrade"
            )
            evidence_trace["uncertainties"] = uniq_keep_order(uncertainties)
            calibrated["evidence_trace"] = evidence_trace

            calibrated["risk_calibration_adjustment"] = {
                "applied": True,
                "from": "elevated",
                "to": previous_risk,
                "reason": "academic self-efficacy doubt alone did not justify an elevated risk upgrade",
            }
            current_risk = previous_risk
            guardrail_applied = True
        elif relational_self_worth_nonacute_evidence and not preserve_elevated:
            updated_state["risk_state"] = previous_risk
            calibrated["updated_session_state"] = updated_state

            state_delta = calibrated.get("state_delta") or {}
            state_delta["risk_shift"] = "unchanged"
            calibrated["state_delta"] = state_delta

            evidence_trace = calibrated.get("evidence_trace") or {}
            uncertainties = list(evidence_trace.get("uncertainties") or [])
            uncertainties.append(
                "post_runtime_guardrail: preserved low/none_observed risk_state because relational self-worth injury alone did not justify an elevated risk upgrade"
            )
            evidence_trace["uncertainties"] = uniq_keep_order(uncertainties)
            calibrated["evidence_trace"] = evidence_trace

            calibrated["risk_calibration_adjustment"] = {
                "applied": True,
                "from": "elevated",
                "to": previous_risk,
                "reason": "relational self-worth injury alone did not justify an elevated risk upgrade",
            }
            current_risk = previous_risk
            guardrail_applied = True

    disclosure_state = str(updated_state.get("disclosure_state") or "unknown")
    if disclosure_state in {"surface", "minimal", "surface_to_moderate"} and (
        any(keyword in evidence_blob for keyword in CONCRETE_DISCLOSURE_KEYWORDS)
        or any(keyword in evidence_blob for keyword in DEEP_PATTERN_DISCLOSURE_KEYWORDS)
    ):
        updated_state["disclosure_state"] = "moderate"
        calibrated["updated_session_state"] = updated_state
        calibrated["disclosure_calibration_adjustment"] = {
            "applied": True,
            "from": disclosure_state,
            "to": "moderate",
            "reason": "concrete behavior-level or deep-pattern disclosure evidence in current turn",
        }
        evidence_trace = calibrated.get("evidence_trace") or {}
        uncertainties = list(evidence_trace.get("uncertainties") or [])
        uncertainties.append(
            "post_runtime_guardrail: upgraded disclosure_state to moderate because the current turn included concrete behavior-level or deep-pattern disclosure"
        )
        evidence_trace["uncertainties"] = uniq_keep_order(uncertainties)
        calibrated["evidence_trace"] = evidence_trace
        guardrail_applied = True

    return calibrated, guardrail_applied


def merge_memory_seed(memory_seed: dict[str, Any], state_update_result: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(memory_seed)
    suggestions = (state_update_result or {}).get("memory_write_suggestions") or {}
    summary = list(updated.get("longitudinal_problem_summary") or [])
    summary.extend(suggestions.get("new_disclosed_facts") or [])
    updated["longitudinal_problem_summary"] = uniq_keep_order(summary)[:12]

    unresolved = list(updated.get("unresolved_threads") or [])
    unresolved.extend(suggestions.get("carry_forward_threads") or [])
    unresolved.extend((state_update_result.get("state_delta") or {}).get("new_unresolved_threads") or [])
    updated["unresolved_threads"] = uniq_keep_order(unresolved)[:12]

    alignment = list(updated.get("report_dialogue_alignment") or [])
    alignment.extend(suggestions.get("next_turn_watchpoints") or [])
    updated["report_dialogue_alignment"] = uniq_keep_order(alignment)[:12]
    return updated


def build_move_payload(
    bundle: dict[str, Any],
    session_state: dict[str, Any],
    transcript: list[dict[str, str]],
    therapist_utterance: str,
) -> dict[str, Any]:
    return {
        "task": "interpret_therapist_move",
        "patient_case_bundle": trim_case_bundle_for_runtime(bundle),
        "current_session_state": session_state,
        "recent_turn_window": recent_turn_window(transcript),
        "current_therapist_utterance": therapist_utterance,
        "interpreter_goal": "produce a structured interpretation that can be consumed by a downstream patient disclosure controller",
    }


def build_disclosure_payload(
    bundle: dict[str, Any],
    session_state: dict[str, Any],
    memory_seed: dict[str, Any],
    transcript: list[dict[str, str]],
    therapist_utterance: str,
    move_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "build_patient_disclosure_decision",
        "patient_case_bundle": trim_case_bundle_for_runtime(bundle),
        "current_session_state": session_state,
        "episodic_memory_seed": memory_seed,
        "recent_turn_window": recent_turn_window(transcript),
        "current_therapist_utterance": therapist_utterance,
        "therapist_move_interpretation": move_result.get("therapist_move_interpretation"),
        "controller_goal": "decide what the patient should surface now versus keep withheld for later turns",
    }


def build_turn_payload(
    bundle: dict[str, Any],
    session_state: dict[str, Any],
    memory_seed: dict[str, Any],
    transcript: list[dict[str, str]],
    therapist_utterance: str,
    disclosure_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "realize_patient_turn",
        "patient_case_bundle": trim_case_bundle_for_runtime(bundle),
        "current_session_state": session_state,
        "episodic_memory_seed": memory_seed,
        "recent_turn_window": recent_turn_window(transcript),
        "current_therapist_utterance": therapist_utterance,
        "disclosure_decision": disclosure_result.get("disclosure_decision"),
        "realization_goal": "generate the patient utterance while respecting the disclosure controller constraints",
    }


def build_state_payload(
    bundle: dict[str, Any],
    previous_state: dict[str, Any],
    memory_seed: dict[str, Any],
    therapist_utterance: str,
    move_result: dict[str, Any],
    disclosure_result: dict[str, Any],
    turn_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "update_patient_session_state",
        "patient_case_bundle": trim_case_bundle_for_runtime(bundle),
        "previous_session_state": previous_state,
        "episodic_memory_seed": memory_seed,
        "current_therapist_utterance": therapist_utterance,
        "therapist_move_interpretation": move_result.get("therapist_move_interpretation"),
        "disclosure_decision": disclosure_result.get("disclosure_decision"),
        "patient_turn_result": turn_result,
        "update_goal": "update the observable session state after one therapist move and one patient reply",
    }


def build_memory_update_payload(
    bundle: dict[str, Any],
    previous_memory_seed: dict[str, Any],
    transcript: list[dict[str, str]],
    therapist_utterance: str,
    patient_utterance: str,
    move_result: dict[str, Any],
    disclosure_result: dict[str, Any],
    state_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "write_back_patient_state_memory",
        "patient_case_bundle": trim_case_bundle_for_runtime(bundle),
        "previous_patient_state_memory": previous_memory_seed,
        "recent_turn_window": recent_turn_window(transcript),
        "current_therapist_utterance": therapist_utterance,
        "current_patient_utterance": patient_utterance,
        "therapist_move_interpretation": move_result.get("therapist_move_interpretation"),
        "disclosure_decision": disclosure_result.get("disclosure_decision"),
        "updated_session_state": state_result.get("updated_session_state"),
        "state_update_delta": state_result.get("state_delta"),
        "writeback_goal": "preserve longitudinal patient continuity while incorporating only evidence-supported new facts and shifts from this turn",
    }


def apply_memory_update_result(
    previous_memory_seed: dict[str, Any],
    state_result: dict[str, Any],
    memory_update_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    if isinstance(memory_update_result, dict):
        candidate = memory_update_result.get("patient_state_memory")
        if isinstance(candidate, dict) and candidate:
            return copy.deepcopy(candidate), False
    return merge_memory_seed(previous_memory_seed, state_result), True


def compact_turn_record(record: dict[str, Any]) -> dict[str, Any]:
    state_result = record.get("state_update_result") or {}
    updated = state_result.get("updated_session_state") or {}
    turn_result = record.get("patient_turn_result") or {}
    return {
        "turn_index": record.get("turn_index"),
        "therapist_utterance": record.get("therapist_utterance"),
        "patient_utterance": turn_result.get("patient_utterance"),
        "session_state_focus": {
            "alliance_state": updated.get("alliance_state"),
            "disclosure_state": updated.get("disclosure_state"),
            "readiness_state": updated.get("readiness_state"),
            "risk_state": updated.get("risk_state"),
            "unfinished_threads": (updated.get("unfinished_threads") or [])[:5],
        },
    }


def build_summary_payload(
    transcript: list[dict[str, str]],
    turn_records: list[dict[str, Any]],
    final_session_state: dict[str, Any],
    final_memory_seed: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "build_session_longitudinal_summary",
        "transcript": transcript[-12:],
        "turn_records": [compact_turn_record(item) for item in turn_records[-4:]],
        "final_patient_state_memory": final_memory_seed,
        "final_session_state": final_session_state,
        "final_therapist_case_memory": {
            "tracking_status": "not_tracked_in_patient_only_runtime",
            "session_state_snapshot": final_session_state,
        },
        "session_meta": {
            "turn_count_completed": len(turn_records),
            "stop_reason": "max_turns_reached",
        },
    }


def build_reviewer_payload(
    bundle: dict[str, Any],
    transcript: list[dict[str, str]],
    turn_records: list[dict[str, Any]],
    final_session_state: dict[str, Any],
    summary_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task": "review_patient_runtime_session",
        "patient_case_bundle": trim_case_bundle_for_runtime(bundle),
        "transcript": transcript[-12:],
        "turn_records": [compact_turn_record(item) for item in turn_records[-4:]],
        "final_session_state": final_session_state,
        "session_longitudinal_summary": summary_result.get("session_longitudinal_summary"),
        "review_goal": "determine whether this multi-turn patient runtime trace remains faithful and can continue",
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    bundle = read_json(args.case_bundle)
    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    client = OpenAICompatibleClient(model_config)

    move_prompt = load_prompt(args.move_prompt)
    disclosure_prompt = load_prompt(args.disclosure_prompt)
    turn_prompt = load_prompt(args.turn_prompt)
    state_prompt = load_prompt(args.state_prompt)
    memory_prompt = load_prompt(args.memory_prompt)
    summary_prompt = load_prompt(args.summary_prompt)
    reviewer_prompt = load_prompt(args.reviewer_prompt)

    transcript = resolve_seed_transcript(bundle)
    therapist_turns = build_default_therapist_turns()[: args.max_turns]
    session_state = copy.deepcopy((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {})
    rolling_memory_seed = copy.deepcopy((bundle.get("runtime_state_seed") or {}).get("episodic_memory_seed") or {})

    turn_records: list[dict[str, Any]] = []
    step_audits: list[dict[str, Any]] = []

    try:
        for turn_index, therapist_utterance in enumerate(therapist_turns, start=1):
            move_payload = build_move_payload(bundle, session_state, transcript, therapist_utterance)
            move_result, move_audit = run_json_step(
                client, move_prompt, move_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
            )

            disclosure_payload = build_disclosure_payload(
                bundle, session_state, rolling_memory_seed, transcript, therapist_utterance, move_result
            )
            disclosure_result, disclosure_audit = run_json_step(
                client,
                disclosure_prompt,
                disclosure_payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
            )

            turn_payload = build_turn_payload(
                bundle, session_state, rolling_memory_seed, transcript, therapist_utterance, disclosure_result
            )
            turn_result, turn_audit = run_json_step(
                client, turn_prompt, turn_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
            )

            state_payload = build_state_payload(
                bundle,
                session_state,
                rolling_memory_seed,
                therapist_utterance,
                move_result,
                disclosure_result,
                turn_result,
            )
            state_result, state_audit = run_json_step(
                client, state_prompt, state_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
            )
            state_result, risk_guardrail_applied = calibrate_state_result_risk(
                session_state,
                therapist_utterance,
                disclosure_result,
                turn_result,
                state_result,
            )

            patient_utterance = turn_result.get("patient_utterance") or ""
            transcript.extend(
                [
                    {"role": "therapist", "text": therapist_utterance},
                    {"role": "patient", "text": patient_utterance},
                ]
            )
            session_state = copy.deepcopy(state_result.get("updated_session_state") or session_state)
            memory_payload = build_memory_update_payload(
                bundle,
                rolling_memory_seed,
                transcript,
                therapist_utterance,
                patient_utterance,
                move_result,
                disclosure_result,
                state_result,
            )
            memory_update_result, memory_audit = run_json_step(
                client, memory_prompt, memory_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
            )
            rolling_memory_seed, memory_fallback_used = apply_memory_update_result(
                rolling_memory_seed, state_result, memory_update_result
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
                    "memory_update_result": memory_update_result,
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

        summary_payload = build_summary_payload(transcript, turn_records, session_state, rolling_memory_seed)
        summary_result, summary_audit = run_json_step(
            client, summary_prompt, summary_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
        )

        reviewer_payload = build_reviewer_payload(bundle, transcript, turn_records, session_state, summary_result)
        reviewer_result, reviewer_audit = run_json_step(
            client, reviewer_prompt, reviewer_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
        )

        status = "complete"
        error = None
    except PromptBudgetExceededError as exc:
        status = "prompt_budget_exceeded"
        error = {"type": "PromptBudgetExceededError", "details": exc.report}
        summary_result = None
        reviewer_result = None
        summary_audit = None
        reviewer_audit = None
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = {"type": type(exc).__name__, "details": str(exc)}
        summary_result = None
        reviewer_result = None
        summary_audit = None
        reviewer_audit = None

    report = {
        "schema_version": "theraskill_patient_single_session_loop_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "model_config": {
            "path": args.model_config,
            "model": model_config.get("name"),
            "base_url": model_config.get("base_url"),
        },
        "case_bundle": args.case_bundle,
        "case_id": bundle.get("case_id"),
        "turn_count_completed": len(turn_records),
        "transcript": transcript,
        "turn_records": turn_records,
        "final_session_state": session_state,
        "final_memory_seed": rolling_memory_seed,
        "session_longitudinal_summary": summary_result,
        "reviewer_result": reviewer_result,
        "step_prompt_audits": step_audits,
        "summary_prompt_audit": summary_audit,
        "reviewer_prompt_audit": reviewer_audit,
        "error": error,
    }
    write_json(output_path, report, overwrite=args.overwrite)
    print(
        json.dumps(
            {"status": status, "output": str(output_path), "turn_count_completed": len(turn_records)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
