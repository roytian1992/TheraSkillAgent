#!/usr/bin/env python3
"""Hermes-facing adapter for TheraSkill dual-session simulator surfaces."""

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
    RUN_DUAL_SESSION_SIMULATOR_HERMES_ADAPTER_RELPATH,
    RUN_PATIENT_SIMULATOR_HERMES_ADAPTER_RELPATH,
    RUN_THERAPIST_SIMULATOR_HERMES_ADAPTER_RELPATH,
    SIMULATION_DUAL_SESSION_HERMES_ADAPTER_ROOT,
    project_script_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = SIMULATION_DUAL_SESSION_HERMES_ADAPTER_ROOT
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs" / "extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs" / "theraskill" / "retrieval.yaml"
DEFAULT_PATIENT_ADAPTER_SCRIPT = project_script_path(RUN_PATIENT_SIMULATOR_HERMES_ADAPTER_RELPATH)
DEFAULT_THERAPIST_ADAPTER_SCRIPT = project_script_path(RUN_THERAPIST_SIMULATOR_HERMES_ADAPTER_RELPATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build-bundle", help="Build a paired patient-runtime + therapist-continuation bundle.")
    build_parser.add_argument("--scenario-id")
    build_parser.add_argument("--runtime-report")
    build_parser.add_argument("--therapist-bundle")
    build_parser.add_argument("--therapist-memory-report")
    build_parser.add_argument("--therapist-turn-report")
    build_parser.add_argument("--patient-adapter-script", default=str(DEFAULT_PATIENT_ADAPTER_SCRIPT))
    build_parser.add_argument("--therapist-adapter-script", default=str(DEFAULT_THERAPIST_ADAPTER_SCRIPT))
    build_parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    build_parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    build_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    build_parser.add_argument("--persona-tag", default="情绪")
    build_parser.add_argument("--memory-group-id", default="target::01_1_to_5_history_pairs")
    build_parser.add_argument("--case-source-id")
    build_parser.add_argument("--overwrite", action="store_true")
    build_parser.add_argument("--parse-retries", type=int)
    build_parser.add_argument("--max-prompt-tokens", type=int)
    build_parser.add_argument("--max-output-tokens", type=int)

    review_parser = subparsers.add_parser("review-bundle", help="Summarize an existing dual-session simulator bundle.")
    review_parser.add_argument("--bundle", required=True)

    return parser.parse_args()


def sanitize_stem(text: str) -> str:
    return text.replace("::", "__").replace("/", "_")


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


def bundle_digest(bundle: dict[str, Any]) -> dict[str, Any]:
    patient_digest = bundle.get("patient_review_digest") or {}
    therapist_digest = bundle.get("therapist_review_digest") or {}
    return {
        "schema_version": "theraskill_dual_session_simulator_review_digest_v1",
        "bundle_path": bundle.get("_bundle_path"),
        "bundle_sha256": file_sha256(bundle["_bundle_path"]) if bundle.get("_bundle_path") else None,
        "scenario": bundle.get("scenario") or {},
        "case_source_id": bundle.get("case_source_id"),
        "patient_review_gate": patient_digest.get("review_gate") or {},
        "patient_final_state": patient_digest.get("final_state") or {},
        "therapist_stage_target": therapist_digest.get("stage_target") or [],
        "therapist_response_intent": therapist_digest.get("response_intent") or [],
        "therapist_utterance": therapist_digest.get("therapist_utterance"),
    }


def build_therapist_bundle_from_reports(
    therapist_memory_report_path: str,
    therapist_turn_report_path: str,
    case_source_id: str | None,
) -> dict[str, Any]:
    therapist_memory_report = read_json(therapist_memory_report_path)
    therapist_turn_report = read_json(therapist_turn_report_path)
    result = therapist_turn_report.get("result") or {}
    plan = result.get("therapist_turn_plan") or {}
    memory = (therapist_memory_report.get("result") or {}).get("therapist_case_memory") or {}
    return {
        "schema_version": "theraskill_therapist_simulator_turn_bundle_v1",
        "therapist_case_memory_report_path": therapist_memory_report_path,
        "therapist_turn_report_path": therapist_turn_report_path,
        "case_source_id": case_source_id,
        "review_digest": {
            "schema_version": "theraskill_therapist_simulator_review_digest_v1",
            "turn_report_path": therapist_turn_report_path,
            "turn_report_sha256": file_sha256(therapist_turn_report_path),
            "status": therapist_turn_report.get("status"),
            "selectors": therapist_turn_report.get("selectors") or {},
            "therapist_utterance": result.get("therapist_utterance"),
            "stage_target": plan.get("stage_target") or [],
            "response_intent": plan.get("response_intent") or [],
            "primary_move_types": plan.get("primary_move_types") or [],
            "boundary_notes": plan.get("boundary_notes") or [],
            "top_priorities": memory.get("top_priorities") or [],
            "selected_response_strategy": memory.get("selected_response_strategy") or [],
            "self_check": result.get("self_check") or {},
        },
    }


def command_build_bundle(args: argparse.Namespace) -> int:
    if not args.runtime_report and not args.scenario_id:
        raise SystemExit("build-bundle requires either --runtime-report or --scenario-id")

    output_root = Path(args.output_root)
    runtime_report_path = args.runtime_report

    if runtime_report_path is None:
        patient_command = [
            sys.executable,
            str(args.patient_adapter_script),
            "run-scenario",
            "--scenario-id",
            args.scenario_id,
            "--extraction-config",
            args.extraction_config,
            "--model-config",
            args.model_config,
            "--output-root",
            str(output_root / "patient_runtime"),
        ]
        if args.overwrite:
            patient_command.append("--overwrite")
        add_optional_llm_args(patient_command, args)
        patient_result = run_python_command(patient_command)
        if not patient_result["ok"]:
            raise SystemExit(json.dumps({"status": "failed", "stage": "patient_runtime", **patient_result}, ensure_ascii=False, indent=2))
        runtime_report_path = (patient_result["parsed_stdout"] or {}).get("runtime_report_path")
        if not runtime_report_path:
            raise SystemExit("patient adapter did not return runtime_report_path")

    patient_review_command = [
        sys.executable,
        str(args.patient_adapter_script),
        "review-runtime",
        "--runtime-report",
        str(runtime_report_path),
    ]
    patient_review_result = run_python_command(patient_review_command)
    if not patient_review_result["ok"] or patient_review_result["parsed_stdout"] is None:
        raise SystemExit(json.dumps({"status": "failed", "stage": "patient_review_digest", **patient_review_result}, ensure_ascii=False, indent=2))

    therapist_bundle_path = args.therapist_bundle
    therapist_bundle = None
    if therapist_bundle_path is not None:
        therapist_bundle = read_json(therapist_bundle_path)
    elif args.therapist_memory_report and args.therapist_turn_report:
        therapist_bundle = build_therapist_bundle_from_reports(
            args.therapist_memory_report,
            args.therapist_turn_report,
            args.case_source_id,
        )
        therapist_bundle_path = args.therapist_turn_report
    else:
        therapist_command = [
            sys.executable,
            str(args.therapist_adapter_script),
            "run-turn",
            "--runtime-report",
            str(runtime_report_path),
            "--extraction-config",
            args.extraction_config,
            "--model-config",
            args.model_config,
            "--output-root",
            str(output_root / "therapist_runtime"),
            "--persona-tag",
            args.persona_tag,
            "--memory-group-id",
            args.memory_group_id,
        ]
        if args.case_source_id:
            therapist_command.extend(["--case-source-id", args.case_source_id])
        if args.overwrite:
            therapist_command.append("--overwrite")
        add_optional_llm_args(therapist_command, args)
        therapist_result = run_python_command(therapist_command)
        if not therapist_result["ok"]:
            raise SystemExit(json.dumps({"status": "failed", "stage": "therapist_runtime", **therapist_result}, ensure_ascii=False, indent=2))

        therapist_bundle_path = (therapist_result["parsed_stdout"] or {}).get("bundle_path")
        if not therapist_bundle_path:
            raise SystemExit("therapist adapter did not return bundle_path")
        therapist_bundle = read_json(therapist_bundle_path)

    runtime_obj = read_json(runtime_report_path)
    if therapist_bundle is None:
        therapist_bundle = read_json(therapist_bundle_path)
    scenario = runtime_obj.get("scenario") or {}
    selected_case_source_id = therapist_bundle.get("case_source_id") or args.case_source_id or "auto"
    bundle_stem = f"{sanitize_stem(str(scenario.get('scenario_id') or Path(runtime_report_path).stem))}__{sanitize_stem(selected_case_source_id)}"
    bundle_path = output_root / "bundles" / f"{bundle_stem}.json"
    bundle = {
        "schema_version": "theraskill_dual_session_simulator_bundle_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime_report_path": str(runtime_report_path),
        "therapist_bundle_path": str(therapist_bundle_path),
        "scenario": {
            "scenario_id": scenario.get("scenario_id"),
            "scenario_family": scenario.get("scenario_family"),
            "case_id": scenario.get("case_id"),
        },
        "case_source_id": selected_case_source_id,
        "patient_review_digest": patient_review_result["parsed_stdout"],
        "therapist_review_digest": therapist_bundle.get("review_digest") or {},
    }
    write_json(bundle_path, bundle, overwrite=args.overwrite)
    digest = bundle_digest({**bundle, "_bundle_path": str(bundle_path)})
    print(json.dumps({"status": "complete", "bundle_path": str(bundle_path), "review_digest": digest}, ensure_ascii=False, indent=2))
    return 0


def command_review_bundle(args: argparse.Namespace) -> int:
    bundle = read_json(args.bundle)
    bundle["_bundle_path"] = args.bundle
    print(json.dumps(bundle_digest(bundle), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "build-bundle":
        return command_build_bundle(args)
    if args.command == "review-bundle":
        return command_review_bundle(args)
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
