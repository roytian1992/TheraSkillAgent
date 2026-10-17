#!/usr/bin/env python3
"""Run a patient simulator session directly from a scenario registry entry."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    resolve_seed_transcript,
    build_state_payload,
    build_summary_payload,
    build_turn_payload,
    merge_memory_seed,
    read_json,
    recent_turn_window,
    run_json_step,
    trim_case_bundle_for_runtime,
    uniq_keep_order,
    ensure_output_path,
    write_json,
)
from domains.theraskill.theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    context_max_prompt_tokens,
    load_prompt,
    load_yaml,
    merged_model_config,
    parse_retry_attempts,
)
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_PATIENT_SCENARIO_EXECUTION_PATH,
    PATIENT_DISCLOSURE_CONTROLLER_PROMPT_PATH,
    PATIENT_MOVE_INTERPRETATION_PROMPT_PATH,
    PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH,
    PATIENT_STATE_MEMORY_WRITEBACK_FROM_BUNDLE_PROMPT_PATH,
    PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH,
    PATIENT_TURN_REALIZATION_PROMPT_PATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
    SIMULATION_SCENARIO_CURRENT_SPLIT_KEY,
    SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES,
    SIMULATION_SCENARIO_REGISTRY_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH
DEFAULT_SUMMARY_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_REVIEWER_PROMPT = PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH
DEFAULT_OUTPUT = DEFAULT_PATIENT_SCENARIO_EXECUTION_PATH


FAMILY_THERAPIST_TURNS = {
    "review_and_revision": [
        "先请你按你自己的节奏，再说一遍这件事里最让你卡住的那个点。",
        "如果回头看刚才这几轮对话，你觉得我有没有哪里理解偏了、或者问得太快了？",
        "我们可以把刚才说过的内容稍微整理一下，看看最核心、最还没解决的到底是什么。",
    ],
}


def compact_text(text: Any) -> str:
    return " ".join(str(text or "").split())


def extract_case_anchor(scenario: dict[str, Any], bundle: dict[str, Any]) -> str:
    scenario_brief = compact_text(scenario.get("scenario_brief"))
    match = re.search(r"`([^`]+)`", scenario_brief)
    if match:
        return match.group(1)

    entry_state = scenario.get("entry_state") or {}
    topic_focus = list(entry_state.get("topic_focus") or [])
    if topic_focus:
        return compact_text(topic_focus[0])

    runtime_seed = (bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}
    topic_focus = list(runtime_seed.get("topic_focus") or [])
    if topic_focus:
        return compact_text(topic_focus[0])

    return "这件事"


def build_family_grounded_turns(scenario: dict[str, Any], bundle: dict[str, Any]) -> list[str]:
    family = str(scenario.get("scenario_family") or "")
    case_anchor = extract_case_anchor(scenario, bundle)

    if family == "supportive_exploration":
        return [
            f"听起来，`{case_anchor}` 这件事已经让你心里堵了挺久。我们先不急着想办法，最近只要一碰到这件事，最先冒出来的通常是哪种感觉，或者哪一刻最难熬？",
            "当那种感觉上来的时候，你心里一般会怎么想自己、别人，或者接下来会发生什么？我们先只抓住最表面的那一小块，不急着一下子说很深。",
            "如果现在只把这份难受再说具体一点，不用一下子讲到最核心，你最希望我先明白的那部分更像是委屈、紧张、害怕，还是别的什么？",
        ]

    if family == "cognitive_reframing_probe":
        return [
            f"当 `{case_anchor}` 这件事出现的时候，你脑子里最先冒出来的那个念头、判断，或者担心通常是什么？我们先不急着改它，只把它说清楚。",
            "如果顺着刚才那个念头再往下走一步，它最担心会发生什么，或者它最像在说明什么？你可以只说最先跳出来的那一层。",
            "如果把刚才那层想法再往心里贴近一点，它更像是在说“我可能就是不行/学不会”，还是“我只是还没找到方法”？哪一边更贴近你现在其实有点相信、但又还在犹豫的那部分？",
        ]

    if family == "rebt_belief_work":
        return [
            f"当你想到 `{case_anchor}` 这件事时，心里会不会冒出某种很硬的要求，比如‘事情必须怎样’、‘我绝不能怎样’？先不用判断它对不对，只说最贴近你的那一句。",
            "如果事情没有按那个要求发展，你最担心这会意味着什么？是事情本身很难承受，还是它也像在说明你、对方或这段关系‘彻底怎么样了’？",
            "我们先不急着反驳这层想法。我更想和你一起看清楚：当这个‘必须’或‘绝不能’出现时，它怎样影响你的情绪、判断和接下来的行动？",
        ]

    if family == "rupture_and_repair":
        return [
            f"我听见 `{case_anchor}` 让你很难受，但也在想，也许现在最直接的做法是先列一个解决步骤、尽快把局面处理掉。你听到我这么快转向办法，心里是什么感觉？",
            "我注意到刚才可能说快了，把‘解决问题’放在了‘先听懂你’前面。我想停一下：我哪里理解偏了，或者哪部分让你觉得没有被接住？",
            "谢谢你把那份不舒服说出来。我们先把方法放一边，只回到这件事里你最怕失去什么、最希望我先理解什么。这样会不会更贴近你现在的状态？",
        ]

    if family == "risk_escalation_and_handoff":
        return [
            f"听起来，`{case_anchor}` 这件事已经让你开始担心情况会不会往更失控的方向走。我想先更谨慎一点确认：最近有没有出现过让你觉得自己很难稳住情绪、和人的冲突明显升级，或者日常功能开始被拖垮的迹象？",
            "谢谢你把这些风险线索说出来。我们先不急着下很重的结论，但我会把它们认真放在心上。对你来说，现在最让你觉得“不能再按平常方式拖着”的那个信号是什么？",
            "如果接下来这些迹象继续变重，或者你一时稳不住自己、很难找到能联系的人支持你，你会比较能接受怎样的下一步帮助或求助安排？我们可以先把这个预案想清楚一点。",
        ]

    return FAMILY_THERAPIST_TURNS.get(family, FAMILY_THERAPIST_TURNS["review_and_revision"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    parser.add_argument("--scenario-id")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def choose_scenario(rows: list[dict[str, Any]], scenario_id: str | None) -> dict[str, Any]:
    if scenario_id:
        for row in rows:
            if row.get("scenario_id") == scenario_id:
                return row
        raise KeyError(f"scenario not found: {scenario_id}")
    for family in ("supportive_exploration", "cognitive_reframing_probe", "risk_escalation_and_handoff"):
        for row in rows:
            if (
                row.get("scenario_family") == family
                and (row.get("split_labels") or {}).get(SIMULATION_SCENARIO_CURRENT_SPLIT_KEY)
                == SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES["train"]
            ):
                return row
    return rows[0]


def load_runtime_stack(
    extraction_config_path: str | Path,
    model_config_path: str | Path,
    summary_prompt_path: str | Path,
    reviewer_prompt_path: str | Path,
    *,
    language: str = "zh",
    parse_retries_override: int | None = None,
    max_prompt_tokens_override: int | None = None,
    max_output_tokens_override: int | None = None,
) -> dict[str, Any]:
    extraction_config = load_yaml(extraction_config_path)
    local_model_config = load_yaml(model_config_path)
    model_config = merged_model_config(extraction_config, local_model_config)
    if max_output_tokens_override is not None:
        model_config["max_output_tokens"] = int(max_output_tokens_override)
    parse_retries = (
        int(parse_retries_override)
        if parse_retries_override is not None
        else parse_retry_attempts(extraction_config, local_model_config)
    )
    max_prompt_tokens = (
        int(max_prompt_tokens_override)
        if max_prompt_tokens_override is not None
        else context_max_prompt_tokens(extraction_config, local_model_config)
    )
    client = OpenAICompatibleClient(model_config)

    def localized_path(path: str | Path) -> Path:
        source = Path(path)
        if language == "en" and source.name.endswith("_zh.md"):
            candidate = source.with_name(source.name[:-6] + "_en.md")
            if candidate.exists():
                return candidate
        return source

    prompts = {
        "move": load_prompt(localized_path(PATIENT_MOVE_INTERPRETATION_PROMPT_PATH)),
        "disclosure": load_prompt(localized_path(PATIENT_DISCLOSURE_CONTROLLER_PROMPT_PATH)),
        "turn": load_prompt(localized_path(PATIENT_TURN_REALIZATION_PROMPT_PATH)),
        "state": load_prompt(localized_path(PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH)),
        "memory": load_prompt(localized_path(PATIENT_STATE_MEMORY_WRITEBACK_FROM_BUNDLE_PROMPT_PATH)),
        "summary": load_prompt(localized_path(summary_prompt_path)),
        "reviewer": load_prompt(localized_path(reviewer_prompt_path)),
    }
    return {
        "client": client,
        "prompts": prompts,
        "parse_retries": parse_retries,
        "max_prompt_tokens": max_prompt_tokens,
        "model_config_meta": {
            "path": str(model_config_path),
            "model": model_config.get("name"),
            "base_url": model_config.get("base_url"),
            "language": language,
        },
    }


def execute_scenario_row(
    scenario: dict[str, Any],
    bundle: dict[str, Any],
    runtime_stack: dict[str, Any],
    *,
    therapist_turns_override: list[str] | None = None,
    report_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = runtime_stack["client"]
    prompts = runtime_stack["prompts"]
    parse_retries = int(runtime_stack["parse_retries"])
    max_prompt_tokens = int(runtime_stack["max_prompt_tokens"])

    transcript = resolve_seed_transcript(bundle, scenario)
    therapist_turns = therapist_turns_override or build_family_grounded_turns(scenario, bundle)
    therapist_turns = therapist_turns[: int(scenario.get("recommended_turn_budget") or 3)]
    session_state = (bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}
    rolling_memory_seed = (bundle.get("runtime_state_seed") or {}).get("episodic_memory_seed") or {}
    turn_records: list[dict[str, Any]] = []
    step_audits: list[dict[str, Any]] = []

    for turn_index, therapist_utterance in enumerate(therapist_turns, start=1):
        move_payload = build_move_payload(bundle, session_state, transcript, therapist_utterance)
        move_result, move_audit = run_json_step(client, prompts["move"], move_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

        disclosure_payload = build_disclosure_payload(bundle, session_state, rolling_memory_seed, transcript, therapist_utterance, move_result)
        disclosure_result, disclosure_audit = run_json_step(client, prompts["disclosure"], disclosure_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

        turn_payload = build_turn_payload(bundle, session_state, rolling_memory_seed, transcript, therapist_utterance, disclosure_result)
        turn_result, turn_audit = run_json_step(client, prompts["turn"], turn_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

        state_payload = build_state_payload(bundle, session_state, rolling_memory_seed, therapist_utterance, move_result, disclosure_result, turn_result)
        state_result, state_audit = run_json_step(client, prompts["state"], state_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)
        state_result, risk_guardrail_applied = calibrate_state_result_risk(
            session_state,
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
        session_state = state_result.get("updated_session_state") or session_state
        patient_utterance = turn_result.get("patient_utterance") or ""
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
        memory_result, memory_audit = run_json_step(
            client, prompts["memory"], memory_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens
        )
        rolling_memory_seed, memory_fallback_used = apply_memory_update_result(
            rolling_memory_seed, state_result, memory_result
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

    summary_payload = build_summary_payload(transcript, turn_records, session_state, rolling_memory_seed)
    summary_result, summary_audit = run_json_step(client, prompts["summary"], summary_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

    reviewer_payload = build_reviewer_payload(bundle, transcript, turn_records, session_state, summary_result)
    reviewer_result, reviewer_audit = run_json_step(client, prompts["reviewer"], reviewer_payload, parse_retries=parse_retries, max_prompt_tokens=max_prompt_tokens)

    return {
        "schema_version": "theraskill_patient_scenario_execution_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "scenario": scenario,
        "model_config": runtime_stack["model_config_meta"],
        "report_metadata": report_metadata or {},
        "transcript": transcript,
        "turn_records": turn_records,
        "final_session_state": session_state,
        "final_memory_seed": rolling_memory_seed,
        "session_longitudinal_summary": summary_result,
        "reviewer_result": reviewer_result,
        "step_prompt_audits": step_audits,
        "summary_prompt_audit": summary_audit,
        "reviewer_prompt_audit": reviewer_audit,
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    ensure_output_path(output_path, overwrite=args.overwrite)

    scenario_rows = read_jsonl(args.scenario_registry)
    scenario = choose_scenario(scenario_rows, args.scenario_id)
    bundle = read_json(scenario["case_bundle_path"])
    runtime_stack = load_runtime_stack(
        args.extraction_config,
        args.model_config,
        args.summary_prompt,
        args.reviewer_prompt,
        parse_retries_override=args.parse_retries,
        max_prompt_tokens_override=args.max_prompt_tokens,
        max_output_tokens_override=args.max_output_tokens,
    )
    report = execute_scenario_row(scenario, bundle, runtime_stack)
    write_json(output_path, report, overwrite=args.overwrite)
    print(json.dumps({"status": "complete", "output": str(output_path), "scenario_id": scenario["scenario_id"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
