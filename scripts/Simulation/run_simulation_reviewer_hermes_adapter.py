#!/usr/bin/env python3
"""Hermes-facing adapter for TheraSkill simulation reviewer surfaces."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import file_sha256, read_json, write_json  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    RUN_SESSION_LONGITUDINAL_SUMMARY_RELPATH,
    RUN_SESSION_REVIEWER_GATE_RELPATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
    SESSION_REVIEWER_GATE_PROMPT_PATH,
    SIMULATION_REVIEWER_HERMES_ADAPTER_ROOT,
    project_script_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = SIMULATION_REVIEWER_HERMES_ADAPTER_ROOT
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs" / "extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs" / "theraskill" / "retrieval.yaml"
DEFAULT_SUMMARY_SCRIPT = project_script_path(RUN_SESSION_LONGITUDINAL_SUMMARY_RELPATH)
DEFAULT_GATE_SCRIPT = project_script_path(RUN_SESSION_REVIEWER_GATE_RELPATH)
DEFAULT_SUMMARY_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_GATE_PROMPT = SESSION_REVIEWER_GATE_PROMPT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-review", help="Run summary + reviewer gate over a runtime session or a dual bundle.")
    run_parser.add_argument("--session-input")
    run_parser.add_argument("--dual-bundle")
    run_parser.add_argument("--summary-input")
    run_parser.add_argument("--summary-script", default=str(DEFAULT_SUMMARY_SCRIPT))
    run_parser.add_argument("--gate-script", default=str(DEFAULT_GATE_SCRIPT))
    run_parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    run_parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    run_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    run_parser.add_argument("--overwrite", action="store_true")
    run_parser.add_argument("--parse-retries", type=int)
    run_parser.add_argument("--max-prompt-tokens", type=int)
    run_parser.add_argument("--max-output-tokens", type=int)
    run_parser.add_argument("--language", choices=("zh", "en"), default="zh")

    digest_parser = subparsers.add_parser("digest-review", help="Summarize an existing reviewer output for Hermes.")
    digest_parser.add_argument("--review-report", required=True)
    digest_parser.add_argument("--summary-report")

    return parser.parse_args()


def sanitize_stem(text: str) -> str:
    return text.replace("::", "__").replace("/", "_")


def resolve_session_input(session_input: str | None, dual_bundle: str | None) -> tuple[str, dict[str, Any] | None]:
    if session_input:
        return session_input, None
    if dual_bundle:
        bundle = read_json(dual_bundle)
        runtime_path = bundle.get("runtime_report_path")
        if not runtime_path:
            raise SystemExit("dual bundle missing runtime_report_path")
        return str(runtime_path), bundle
    raise SystemExit("run-review requires either --session-input or --dual-bundle")


def load_therapist_continuation(dual_bundle: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not dual_bundle:
        return {}, {}
    therapist_bundle_path = dual_bundle.get("therapist_bundle_path")
    if not therapist_bundle_path:
        return {}, {}
    therapist_bundle = read_json(therapist_bundle_path)
    digest = therapist_bundle.get("review_digest") or {}
    therapist_memory: dict[str, Any] = {}
    therapist_memory_report_path = therapist_bundle.get("therapist_case_memory_report_path")
    if therapist_memory_report_path:
        therapist_memory_report = read_json(therapist_memory_report_path)
        therapist_memory = (therapist_memory_report.get("result") or {}).get("therapist_case_memory") or {}
    continuation = {
        "source_therapist_bundle_path": str(therapist_bundle_path),
        "source_therapist_bundle_sha256": file_sha256(therapist_bundle_path),
        "therapist_utterance": digest.get("therapist_utterance"),
        "stage_target": digest.get("stage_target") or [],
        "response_intent": digest.get("response_intent") or [],
        "primary_move_types": digest.get("primary_move_types") or [],
        "boundary_notes": digest.get("boundary_notes") or [],
        "self_check": digest.get("self_check") or {},
        "awaiting_patient_response": True,
    }
    return continuation, therapist_memory


def append_therapist_continuation(
    normalized_session: dict[str, Any],
    dual_bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    continuation, therapist_memory = load_therapist_continuation(dual_bundle)
    utterance = str(continuation.get("therapist_utterance") or "").strip()
    if not utterance:
        return normalized_session

    transcript = list(normalized_session.get("transcript") or [])
    if not transcript or transcript[-1].get("role") != "therapist" or transcript[-1].get("text") != utterance:
        transcript.append(
            {
                "role": "therapist",
                "text": utterance,
                "surface_role": "generated_continuation",
            }
        )

    turn_records = list(normalized_session.get("turn_records") or [])
    existing_indexes = [row.get("turn_index") for row in turn_records if isinstance(row.get("turn_index"), int)]
    continuation_turn_index = max(existing_indexes, default=0) + 1
    continuation_already_present = any(
        row.get("continuation_only")
        and ((row.get("therapist_turn") or {}).get("therapist_utterance") == utterance)
        for row in turn_records
    )
    if not continuation_already_present:
        turn_records.append(
            {
                "turn_index": continuation_turn_index,
                "continuation_only": True,
                "therapist_turn": {
                    "therapist_utterance": utterance,
                    "stage_target": continuation.get("stage_target") or [],
                    "response_intent": continuation.get("response_intent") or [],
                    "primary_move_types": continuation.get("primary_move_types") or [],
                    "boundary_notes": continuation.get("boundary_notes") or [],
                },
                "patient_turn": {},
                "patient_state_memory": normalized_session.get("final_patient_state_memory") or {},
                "patient_state_delta": {},
                "therapist_case_memory": therapist_memory,
                "state_update_result": {},
                "memory_update_result": {},
            }
        )

    normalized_session["schema_version"] = "theraskill_normalized_patient_runtime_session_v2"
    normalized_session["transcript"] = transcript
    normalized_session["turn_records"] = turn_records
    normalized_session["final_therapist_case_memory"] = therapist_memory
    normalized_session["therapist_continuation"] = continuation
    normalized_session["review_turn_record_count"] = len(turn_records)
    return normalized_session


def normalize_patient_runtime_session(
    session_obj: dict[str, Any],
    *,
    dual_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if session_obj.get("final_patient_state_memory") is not None:
        normalized_session = dict(session_obj)
        normalized_session["transcript"] = list(session_obj.get("transcript") or [])
        normalized_session["turn_records"] = list(session_obj.get("turn_records") or [])
        return append_therapist_continuation(normalized_session, dual_bundle)

    normalized_turns: list[dict[str, Any]] = []
    for row in session_obj.get("turn_records") or []:
        normalized_turns.append(
            {
                "turn_index": row.get("turn_index"),
                "therapist_turn": {
                    "therapist_utterance": row.get("therapist_utterance"),
                    "therapist_move_interpretation": row.get("therapist_move_interpretation"),
                },
                "patient_turn": {
                    "patient_utterance": ((row.get("patient_turn_result") or {}).get("patient_utterance")),
                    "patient_turn_result": row.get("patient_turn_result"),
                },
                "patient_state_memory": ((row.get("memory_update_result") or {}).get("patient_state_memory")) or {},
                "patient_state_delta": ((row.get("memory_update_result") or {}).get("state_delta")) or {},
                "therapist_case_memory": {},
                "state_update_result": row.get("state_update_result") or {},
                "memory_update_result": row.get("memory_update_result") or {},
            }
        )

    normalized_session = {
        "schema_version": "theraskill_normalized_patient_runtime_session_v1",
        "source_schema_version": session_obj.get("schema_version"),
        "scenario": session_obj.get("scenario") or {},
        "transcript": session_obj.get("transcript") or [],
        "turn_records": normalized_turns,
        "turn_count_completed": len(normalized_turns),
        "stop_reason": "completed_runtime_loop",
        "final_patient_state_memory": session_obj.get("final_memory_seed") or {},
        "final_therapist_case_memory": {},
        "final_session_state": session_obj.get("final_session_state") or {},
    }
    return append_therapist_continuation(normalized_session, dual_bundle)


def run_python_command(command: list[str]) -> dict[str, Any]:
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
    }


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


def review_digest(review_report: dict[str, Any], summary_report: dict[str, Any] | None) -> dict[str, Any]:
    result = review_report.get("result") or {}
    summary_obj = (summary_report or {}).get("result") or {}
    session_summary = summary_obj.get("session_longitudinal_summary") or {}
    return {
        "schema_version": "theraskill_simulation_reviewer_review_digest_v1",
        "review_report_path": review_report.get("_report_path"),
        "review_report_sha256": file_sha256(review_report["_report_path"]) if review_report.get("_report_path") else None,
        "status": review_report.get("status"),
        "review_gate": result.get("review_gate") or {},
        "finding_digest": [
            {
                "severity": row.get("severity"),
                "category": row.get("category"),
                "message": row.get("message"),
            }
            for row in (result.get("findings") or [])[:8]
        ],
        "recommended_actions": result.get("recommended_actions") or [],
        "key_turning_points": session_summary.get("key_turning_points") or [],
        "active_unresolved_threads": session_summary.get("active_unresolved_threads") or [],
        "risk_watchpoints": session_summary.get("boundary_or_risk_watchpoints") or [],
    }


def command_run_review(args: argparse.Namespace) -> int:
    session_input, bundle = resolve_session_input(args.session_input, args.dual_bundle)
    output_root = Path(args.output_root)
    stem = sanitize_stem(Path(session_input).stem)
    normalized_session_path = output_root / "normalized_sessions" / f"{stem}.json"
    summary_path = Path(args.summary_input) if args.summary_input else output_root / "summaries" / f"{stem}.json"
    review_path = output_root / "reviews" / f"{stem}.json"
    session_obj = read_json(session_input)
    normalized_session = normalize_patient_runtime_session(session_obj, dual_bundle=bundle)
    write_json(normalized_session_path, normalized_session, overwrite=args.overwrite)

    if args.summary_input is None:
        summary_command = [
            sys.executable,
            str(args.summary_script),
            "--session-input",
            str(normalized_session_path),
            "--extraction-config",
            args.extraction_config,
            "--model-config",
            args.model_config,
            "--prompt",
            str(localized_prompt(DEFAULT_SUMMARY_PROMPT, args.language)),
            "--output",
            str(summary_path),
        ]
        if args.overwrite:
            summary_command.append("--overwrite")
        add_optional_llm_args(summary_command, args)
        summary_result = run_python_command(summary_command)
        if not summary_result["ok"]:
            raise SystemExit(json.dumps({"status": "failed", "stage": "session_summary", **summary_result}, ensure_ascii=False, indent=2))
    else:
        summary_result = {"ok": True}

    gate_command = [
        sys.executable,
        str(args.gate_script),
        "--session-input",
        str(normalized_session_path),
        "--summary-input",
        str(summary_path),
        "--extraction-config",
        args.extraction_config,
        "--model-config",
        args.model_config,
        "--prompt",
        str(localized_prompt(DEFAULT_GATE_PROMPT, args.language)),
        "--output",
        str(review_path),
    ]
    if args.overwrite:
        gate_command.append("--overwrite")
    add_optional_llm_args(gate_command, args)
    gate_result = run_python_command(gate_command)
    if not gate_result["ok"]:
        raise SystemExit(json.dumps({"status": "failed", "stage": "review_gate", **gate_result}, ensure_ascii=False, indent=2))

    summary_report = read_json(summary_path)
    review_report = read_json(review_path)
    review_report["_report_path"] = str(review_path)
    digest = review_digest(review_report, summary_report)
    bundle_path = output_root / "review_bundles" / f"{stem}.json"
    payload = {
        "schema_version": "theraskill_simulation_reviewer_bundle_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_input": str(session_input),
        "normalized_session_input": str(normalized_session_path),
        "summary_report_path": str(summary_path),
        "review_report_path": str(review_path),
        "dual_bundle_path": args.dual_bundle,
        "scenario": (bundle or {}).get("scenario") or {},
        "review_digest": digest,
    }
    write_json(bundle_path, payload, overwrite=args.overwrite)
    print(json.dumps({"status": "complete", "bundle_path": str(bundle_path), "review_digest": digest}, ensure_ascii=False, indent=2))
    return 0


def command_digest_review(args: argparse.Namespace) -> int:
    review_report = read_json(args.review_report)
    review_report["_report_path"] = args.review_report
    summary_report = read_json(args.summary_report) if args.summary_report else None
    print(json.dumps(review_digest(review_report, summary_report), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "run-review":
        return command_run_review(args)
    if args.command == "digest-review":
        return command_digest_review(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
