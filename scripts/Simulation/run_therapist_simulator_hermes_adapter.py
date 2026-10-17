#!/usr/bin/env python3
"""Hermes-facing adapter for TheraSkill therapist simulator surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import file_sha256, read_json, read_jsonl, write_json  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    RUN_THERAPIST_CASE_MEMORY_UPDATE_RELPATH,
    RUN_THERAPIST_TURN_GENERATION_RELPATH,
    SIMULATION_ACTION_SEQUENCE_PATH,
    SIMULATION_CASE_TRAJECTORY_PATH,
    SIMULATION_CASE_FORMULATION_PATH,
    SIMULATION_MEMORY_BUNDLE_PATH,
    SIMULATION_PERSONA_BUNDLE_PATH,
    SIMULATION_THERAPIST_HERMES_ADAPTER_ROOT,
    THERAPIST_TURN_GENERATION_PROMPT_PATH,
    THERAPIST_CASE_MEMORY_UPDATE_PROMPT_PATH,
    project_script_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs" / "extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs" / "theraskill" / "retrieval.yaml"
DEFAULT_OUTPUT_ROOT = SIMULATION_THERAPIST_HERMES_ADAPTER_ROOT
DEFAULT_THERAPIST_MEMORY_SCRIPT = project_script_path(RUN_THERAPIST_CASE_MEMORY_UPDATE_RELPATH)
DEFAULT_THERAPIST_TURN_SCRIPT = project_script_path(RUN_THERAPIST_TURN_GENERATION_RELPATH)
DEFAULT_TRAJECTORY_JSONL = SIMULATION_CASE_TRAJECTORY_PATH


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def compact_text(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-case-sources", help="List available therapist-side exemplar case sources.")
    list_parser.add_argument("--trajectory-jsonl", default=str(DEFAULT_TRAJECTORY_JSONL))
    list_parser.add_argument("--limit", type=int, default=10)

    prepare_parser = subparsers.add_parser("prepare-patient-memory", help="Materialize a patient-memory report from an existing patient runtime report.")
    prepare_parser.add_argument("--runtime-report", required=True)
    prepare_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    prepare_parser.add_argument("--overwrite", action="store_true")

    run_parser = subparsers.add_parser("run-turn", help="Generate one therapist-side continuation from an existing patient runtime report.")
    run_parser.add_argument("--runtime-report")
    run_parser.add_argument("--patient-memory-report")
    run_parser.add_argument(
        "--therapist-memory-seed-report",
        help="Use an existing therapist-memory report for foreground generation and defer the LLM memory refresh.",
    )
    run_parser.add_argument("--therapist-memory-script", default=str(DEFAULT_THERAPIST_MEMORY_SCRIPT))
    run_parser.add_argument("--therapist-turn-script", default=str(DEFAULT_THERAPIST_TURN_SCRIPT))
    run_parser.add_argument("--persona-jsonl", default=str(SIMULATION_PERSONA_BUNDLE_PATH))
    run_parser.add_argument("--memory-jsonl", default=str(SIMULATION_MEMORY_BUNDLE_PATH))
    run_parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    run_parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    run_parser.add_argument("--trajectory-jsonl", default=str(DEFAULT_TRAJECTORY_JSONL))
    run_parser.add_argument("--formulation-jsonl", default=str(SIMULATION_CASE_FORMULATION_PATH))
    run_parser.add_argument("--action-sequence-jsonl", default=str(SIMULATION_ACTION_SEQUENCE_PATH))
    run_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    run_parser.add_argument("--persona-tag", default="情绪")
    run_parser.add_argument("--memory-group-id", default="target::01_1_to_5_history_pairs")
    run_parser.add_argument("--case-source-id")
    run_parser.add_argument("--overwrite", action="store_true")
    run_parser.add_argument("--parse-retries", type=int)
    run_parser.add_argument("--max-prompt-tokens", type=int)
    run_parser.add_argument("--max-output-tokens", type=int)
    run_parser.add_argument("--language", choices=("zh", "en"), default="zh")

    review_parser = subparsers.add_parser("review-turn", help="Summarize an existing therapist-turn output for Hermes.")
    review_parser.add_argument("--turn-report", required=True)
    review_parser.add_argument("--memory-report")

    return parser.parse_args()


def ensure_runtime_or_memory(args: argparse.Namespace) -> None:
    if getattr(args, "runtime_report", None) or getattr(args, "patient_memory_report", None):
        return
    raise SystemExit("run-turn requires either --runtime-report or --patient-memory-report")


def run_python_command(command: list[str]) -> dict[str, Any]:
    started = time.monotonic()
    environment = os.environ.copy()
    project_path = str(PROJECT_ROOT)
    environment["PYTHONPATH"] = project_path + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    parsed_stdout = None
    if stdout:
        try:
            parsed_stdout = json.loads(stdout)
        except json.JSONDecodeError:
            parsed_stdout = None
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "parsed_stdout": parsed_stdout,
        "command": command,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def sanitize_stem(text: str) -> str:
    return text.replace("::", "__").replace("/", "_")


def therapist_case_source_digest(row: dict[str, Any]) -> dict[str, Any]:
    content = ((row.get("content") or {}).get("case_trajectory") or {})
    source = row.get("source") or {}
    return {
        "source_id": source.get("source_id"),
        "case_id": source.get("case_id"),
        "dominant_presenting_problems": content.get("dominant_presenting_problems") or [],
        "overall_process_summary": compact_text(content.get("overall_process_summary")),
    }


def content_labels(row: dict[str, Any]) -> list[str]:
    content = ((row.get("content") or {}).get("case_trajectory") or {})
    labels: list[str] = []
    for item in content.get("dominant_presenting_problems") or []:
        label = str(item.get("label") or "").strip()
        if label:
            labels.append(label)
    return labels


def extract_case_source_text(row: dict[str, Any]) -> str:
    content = ((row.get("content") or {}).get("case_trajectory") or {})
    return " ".join(content_labels(row) + [str(content.get("overall_process_summary") or "")])


def scenario_keywords(scenario: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    case_family = str(scenario.get("case_family") or "")
    scenario_family = str(scenario.get("scenario_family") or "")
    keywords.extend([case_family, scenario_family])
    keywords.extend(list((scenario.get("entry_state") or {}).get("topic_focus") or []))
    keywords.extend(list(scenario.get("scenario_tags") or []))
    if "家庭" in case_family:
        keywords.extend(["家庭", "亲子", "婚姻", "父母", "孩子", "母亲"])
    if "情绪" in case_family:
        keywords.extend(["情绪", "焦虑", "抑郁"])
    if scenario_family == "supportive_exploration":
        keywords.extend(["支持", "联盟", "共情", "探索", "早期"])
    uniq: list[str] = []
    seen: set[str] = set()
    for item in keywords:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            uniq.append(text)
    return uniq


def score_case_source(row: dict[str, Any], scenario: dict[str, Any]) -> int:
    text = extract_case_source_text(row)
    labels = content_labels(row)
    score = 0
    for keyword in scenario_keywords(scenario):
        if keyword in text:
            score += 2
        for label in labels:
            if keyword in label or label in keyword:
                score += 3
    if str(scenario.get("scenario_family") or "") == "supportive_exploration":
        if "支持" in text or "联盟" in text or "共情" in text:
            score += 4
        if "早期" in text:
            score += 2
    return score


def auto_select_case_source_id(trajectory_jsonl: str | Path, scenario: dict[str, Any]) -> str:
    rows = read_jsonl(trajectory_jsonl)
    best_row = None
    best_score = -1
    for row in rows:
        score = score_case_source(row, scenario)
        if score > best_score:
            best_score = score
            best_row = row
    if best_row is None:
        raise SystemExit("no trajectory rows available for therapist case-source selection")
    return str((best_row.get("source") or {}).get("source_id") or "")


def derive_patient_memory(session_obj: dict[str, Any]) -> dict[str, Any]:
    memory_seed = session_obj.get("final_memory_seed")
    if isinstance(memory_seed, dict) and memory_seed:
        return memory_seed
    final_state = session_obj.get("final_session_state") or {}
    scenario = session_obj.get("scenario") or {}
    return {
        "demographics_if_known": [],
        "presenting_problems": (scenario.get("entry_state") or {}).get("topic_focus") or [],
        "current_emotion": final_state.get("emotion_state") or [],
        "current_cognition": [],
        "current_interpersonal_concerns": [],
        "alliance_state": final_state.get("alliance_state"),
        "disclosure_depth": final_state.get("disclosure_state"),
        "resistance_signals": [],
        "readiness_level": final_state.get("readiness_state"),
        "risk_state": {
            "level": final_state.get("risk_state"),
            "signals": [],
            "change": "unknown",
        },
        "explicit_goals": [],
        "hidden_concerns": [],
        "salient_prior_events": [],
        "unresolved_threads": final_state.get("unfinished_threads") or [],
        "last_therapist_move_impact": [],
        "memory_confidence": "low",
    }


def write_runtime_derived_patient_memory(runtime_report: str | Path, output_root: str | Path, *, overwrite: bool) -> Path:
    runtime_path = Path(runtime_report)
    session_obj = read_json(runtime_path)
    scenario = session_obj.get("scenario") or {}
    patient_memory = derive_patient_memory(session_obj)
    payload = {
        "schema_version": "theraskill_patient_memory_report_from_runtime_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "source_runtime_report": str(runtime_path),
        "source_runtime_sha256": file_sha256(runtime_path),
        "scenario": {
            "scenario_id": scenario.get("scenario_id"),
            "scenario_family": scenario.get("scenario_family"),
            "case_id": scenario.get("case_id"),
        },
        "result": {
            "patient_state_memory": patient_memory,
        },
    }
    scenario_id = scenario.get("scenario_id") or runtime_path.stem
    output_path = Path(output_root) / "patient_memory_reports" / f"{sanitize_stem(str(scenario_id))}.json"
    write_json(output_path, payload, overwrite=overwrite)
    return output_path


def therapist_turn_digest(turn_report: dict[str, Any], memory_report: dict[str, Any] | None) -> dict[str, Any]:
    result = turn_report.get("result") or {}
    plan = result.get("therapist_turn_plan") or {}
    memory = ((memory_report or {}).get("result") or {}).get("therapist_case_memory") or {}
    return {
        "schema_version": "theraskill_therapist_simulator_review_digest_v1",
        "turn_report_path": turn_report.get("_report_path"),
        "turn_report_sha256": file_sha256(turn_report["_report_path"]) if turn_report.get("_report_path") else None,
        "status": turn_report.get("status"),
        "selectors": turn_report.get("selectors") or {},
        "therapist_utterance": result.get("therapist_utterance"),
        "stage_target": plan.get("stage_target") or [],
        "response_intent": plan.get("response_intent") or [],
        "primary_move_types": plan.get("primary_move_types") or [],
        "boundary_notes": plan.get("boundary_notes") or [],
        "top_priorities": memory.get("top_priorities") or [],
        "selected_response_strategy": memory.get("selected_response_strategy") or [],
        "self_check": result.get("self_check") or {},
    }


def command_list_case_sources(args: argparse.Namespace) -> int:
    rows = read_jsonl(args.trajectory_jsonl)
    digests = [therapist_case_source_digest(row) for row in rows[: args.limit]]
    print(json.dumps({"status": "complete", "case_source_count": len(digests), "case_sources": digests}, ensure_ascii=False, indent=2))
    return 0


def command_prepare_patient_memory(args: argparse.Namespace) -> int:
    output_path = write_runtime_derived_patient_memory(args.runtime_report, args.output_root, overwrite=args.overwrite)
    print(json.dumps({"status": "complete", "patient_memory_report_path": str(output_path)}, ensure_ascii=False, indent=2))
    return 0


def add_optional_llm_args(command: list[str], args: argparse.Namespace) -> None:
    if args.parse_retries is not None:
        command.extend(["--parse-retries", str(args.parse_retries)])
    if args.max_prompt_tokens is not None:
        command.extend(["--max-prompt-tokens", str(args.max_prompt_tokens)])
    if args.max_output_tokens is not None:
        command.extend(["--max-output-tokens", str(args.max_output_tokens)])


def localized_prompt(path: Path, language: str) -> Path:
    if language == "en" and path.name.endswith("_zh.md"):
        candidate = path.with_name(path.name[:-6] + "_en.md")
        if candidate.exists():
            return candidate
    return path


def command_run_turn(args: argparse.Namespace) -> int:
    ensure_runtime_or_memory(args)
    runtime_path = Path(args.runtime_report) if args.runtime_report else None
    output_root = Path(args.output_root)
    patient_memory_path = Path(args.patient_memory_report) if args.patient_memory_report else None
    if patient_memory_path is None:
        patient_memory_path = write_runtime_derived_patient_memory(runtime_path, output_root, overwrite=args.overwrite)

    runtime_obj = read_json(runtime_path) if runtime_path else {}
    scenario = runtime_obj.get("scenario") or {}
    selected_case_source_id = args.case_source_id or auto_select_case_source_id(args.trajectory_jsonl, scenario)
    scenario_stem = sanitize_stem(str(scenario.get("scenario_id") or patient_memory_path.stem))
    file_stem = f"{scenario_stem}__{sanitize_stem(selected_case_source_id)}"
    therapist_memory_path = (
        Path(args.therapist_memory_seed_report)
        if args.therapist_memory_seed_report
        else output_root / "therapist_case_memory" / f"{file_stem}.json"
    )
    therapist_turn_path = output_root / "therapist_turns" / f"{file_stem}.json"

    memory_command = [
        sys.executable,
        str(args.therapist_memory_script),
        "--extraction-config",
        args.extraction_config,
        "--model-config",
        args.model_config,
        "--persona-jsonl",
        args.persona_jsonl,
        "--memory-jsonl",
        args.memory_jsonl,
        "--trajectory-jsonl",
        args.trajectory_jsonl,
        "--formulation-jsonl",
        args.formulation_jsonl,
        "--action-sequence-jsonl",
        args.action_sequence_jsonl,
        "--patient-memory-report",
        str(patient_memory_path),
        "--persona-tag",
        args.persona_tag,
        "--memory-group-id",
        args.memory_group_id,
        "--case-source-id",
        selected_case_source_id,
        "--prompt",
        str(localized_prompt(THERAPIST_CASE_MEMORY_UPDATE_PROMPT_PATH, args.language)),
        "--output",
        str(therapist_memory_path),
    ]
    turn_command = [
        sys.executable,
        str(args.therapist_turn_script),
        "--extraction-config",
        args.extraction_config,
        "--model-config",
        args.model_config,
        "--persona-jsonl",
        args.persona_jsonl,
        "--trajectory-jsonl",
        args.trajectory_jsonl,
        "--formulation-jsonl",
        args.formulation_jsonl,
        "--action-sequence-jsonl",
        args.action_sequence_jsonl,
        "--patient-memory-report",
        str(patient_memory_path),
        "--therapist-memory-report",
        str(therapist_memory_path),
        "--persona-tag",
        args.persona_tag,
        "--case-source-id",
        selected_case_source_id,
        "--prompt",
        str(localized_prompt(THERAPIST_TURN_GENERATION_PROMPT_PATH, args.language)),
        "--output",
        str(therapist_turn_path),
    ]
    if args.overwrite:
        memory_command.append("--overwrite")
        turn_command.append("--overwrite")
    add_optional_llm_args(memory_command, args)
    add_optional_llm_args(turn_command, args)

    memory_result = None
    if not args.therapist_memory_seed_report:
        memory_result = run_python_command(memory_command)
        if not memory_result["ok"]:
            raise SystemExit(json.dumps({"status": "failed", "stage": "therapist_case_memory_update", **memory_result}, ensure_ascii=False, indent=2))

    turn_result = run_python_command(turn_command)
    if not turn_result["ok"]:
        raise SystemExit(json.dumps({"status": "failed", "stage": "therapist_turn_generation", **turn_result}, ensure_ascii=False, indent=2))

    therapist_memory_report = read_json(therapist_memory_path)
    therapist_turn_report = read_json(therapist_turn_path)
    therapist_turn_report["_report_path"] = str(therapist_turn_path)
    digest = therapist_turn_digest(therapist_turn_report, therapist_memory_report)
    bundle_path = output_root / "turn_bundles" / f"{file_stem}.json"
    bundle = {
        "schema_version": "theraskill_therapist_simulator_turn_bundle_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime_report_path": str(runtime_path) if runtime_path else None,
        "patient_memory_report_path": str(patient_memory_path),
        "therapist_case_memory_report_path": str(therapist_memory_path),
        "therapist_turn_report_path": str(therapist_turn_path),
        "scenario": {
            "scenario_id": scenario.get("scenario_id"),
            "scenario_family": scenario.get("scenario_family"),
            "case_id": scenario.get("case_id"),
        },
        "case_source_id": selected_case_source_id,
        "case_source_selection_mode": "user_supplied" if args.case_source_id else "auto_selected_from_scenario",
        "execution_timing": {
            "therapist_memory_seconds": (memory_result or {}).get("elapsed_seconds", 0.0),
            "therapist_turn_seconds": turn_result.get("elapsed_seconds"),
            "memory_refresh_deferred": bool(args.therapist_memory_seed_report),
        },
        "review_digest": digest,
        "language": args.language,
    }
    write_json(bundle_path, bundle, overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "status": "complete",
                "bundle_path": str(bundle_path),
                "patient_memory_report_path": str(patient_memory_path),
                "therapist_case_memory_report_path": str(therapist_memory_path),
                "therapist_turn_report_path": str(therapist_turn_path),
                "selected_case_source_id": selected_case_source_id,
                "execution_timing": bundle["execution_timing"],
                "review_digest": digest,
                "language": args.language,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_review_turn(args: argparse.Namespace) -> int:
    turn_report = read_json(args.turn_report)
    turn_report["_report_path"] = args.turn_report
    memory_report = read_json(args.memory_report) if args.memory_report else None
    print(json.dumps(therapist_turn_digest(turn_report, memory_report), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "list-case-sources":
        return command_list_case_sources(args)
    if args.command == "prepare-patient-memory":
        return command_prepare_patient_memory(args)
    if args.command == "run-turn":
        return command_run_turn(args)
    if args.command == "review-turn":
        return command_review_turn(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
