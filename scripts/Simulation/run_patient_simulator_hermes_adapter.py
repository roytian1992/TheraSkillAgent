#!/usr/bin/env python3
"""Hermes-facing adapter for TheraSkill patient simulator runtime surfaces."""

from __future__ import annotations

import argparse
import hashlib
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

from run_patient_scenario_execution import (  # noqa: E402
    execute_scenario_row,
    load_runtime_stack,
    read_json,
    read_jsonl,
)
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_CONDITIONED_EVAL_OUTPUT_DIR,
    DEFAULT_SCENARIO_BENCHMARK_OUTPUT_DIR,
    PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH,
    RUN_PATIENT_SCENARIO_BENCHMARK_RELPATH,
    RUN_PATIENT_THERAPIST_CONDITIONED_EVAL_RELPATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
    SIMULATION_HERMES_ADAPTER_ROOT,
    SIMULATION_SCENARIO_CURRENT_SPLIT_KEY,
    SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES,
    SIMULATION_SCENARIO_REGISTRY_PATH,
    project_script_path,
)
from io_utils import file_sha256, write_json  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH
DEFAULT_SUMMARY_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_REVIEWER_PROMPT = PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH
DEFAULT_OUTPUT_ROOT = SIMULATION_HERMES_ADAPTER_ROOT
DEFAULT_BENCHMARK_SCRIPT = project_script_path(RUN_PATIENT_SCENARIO_BENCHMARK_RELPATH)
DEFAULT_CONDITIONED_SCRIPT = project_script_path(RUN_PATIENT_THERAPIST_CONDITIONED_EVAL_RELPATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-scenarios", help="List available patient simulator scenarios.")
    list_parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    list_parser.add_argument("--split-key", default=SIMULATION_SCENARIO_CURRENT_SPLIT_KEY)
    list_parser.add_argument("--split-value")
    list_parser.add_argument("--scenario-family", action="append")
    list_parser.add_argument("--limit", type=int, default=10)

    run_parser = subparsers.add_parser("run-scenario", help="Run one scenario through the patient simulator.")
    run_parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    run_parser.add_argument("--scenario-id", required=True)
    run_parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    run_parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    run_parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    run_parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    run_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    run_parser.add_argument("--overwrite", action="store_true")
    run_parser.add_argument("--parse-retries", type=int)
    run_parser.add_argument("--max-prompt-tokens", type=int)
    run_parser.add_argument("--max-output-tokens", type=int)

    review_parser = subparsers.add_parser("review-runtime", help="Summarize an existing runtime report for Hermes.")
    review_parser.add_argument("--runtime-report", required=True)

    benchmark_parser = subparsers.add_parser("run-benchmark", help="Run a benchmark batch and summarize it for Hermes.")
    benchmark_parser.add_argument("--benchmark-script", default=str(DEFAULT_BENCHMARK_SCRIPT))
    benchmark_parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    benchmark_parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    benchmark_parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    benchmark_parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    benchmark_parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    benchmark_parser.add_argument("--output-dir", default=str(DEFAULT_SCENARIO_BENCHMARK_OUTPUT_DIR))
    benchmark_parser.add_argument("--overwrite", action="store_true")
    benchmark_parser.add_argument("--max-scenarios", type=int, default=2)
    benchmark_parser.add_argument("--split-key", default=SIMULATION_SCENARIO_CURRENT_SPLIT_KEY)
    benchmark_parser.add_argument("--split-value", default=SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES["dev"])
    benchmark_parser.add_argument("--scenario-family", action="append")
    benchmark_parser.add_argument("--scenario-id", action="append")
    benchmark_parser.add_argument("--parse-retries", type=int)
    benchmark_parser.add_argument("--max-prompt-tokens", type=int)
    benchmark_parser.add_argument("--max-output-tokens", type=int)

    compare_parser = subparsers.add_parser("run-conditioned-eval", help="Run therapist-conditioned comparison and summarize it for Hermes.")
    compare_parser.add_argument("--conditioned-script", default=str(DEFAULT_CONDITIONED_SCRIPT))
    compare_parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    compare_parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    compare_parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    compare_parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    compare_parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    compare_parser.add_argument("--output-dir", default=str(DEFAULT_CONDITIONED_EVAL_OUTPUT_DIR))
    compare_parser.add_argument("--overwrite", action="store_true")
    compare_parser.add_argument("--scenario-id", action="append")
    compare_parser.add_argument("--max-scenarios", type=int, default=1)
    compare_parser.add_argument("--split-key", default=SIMULATION_SCENARIO_CURRENT_SPLIT_KEY)
    compare_parser.add_argument("--split-value", default=SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES["train"])
    compare_parser.add_argument("--scenario-family", action="append")
    compare_parser.add_argument("--parse-retries", type=int)
    compare_parser.add_argument("--max-prompt-tokens", type=int)
    compare_parser.add_argument("--max-output-tokens", type=int)

    return parser.parse_args()


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_scenarios(path: str | Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def select_scenario(rows: list[dict[str, Any]], scenario_id: str) -> dict[str, Any]:
    for row in rows:
        if row.get("scenario_id") == scenario_id:
            return row
    raise KeyError(f"scenario not found: {scenario_id}")


def scenario_digest(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": row.get("scenario_id"),
        "scenario_family": row.get("scenario_family"),
        "case_id": row.get("case_id"),
        "case_family": row.get("case_family"),
        "risk_band": row.get("risk_band"),
        "difficulty_band": row.get("difficulty_band"),
        "split_labels": row.get("split_labels") or {},
        "recommended_turn_budget": row.get("recommended_turn_budget"),
        "scenario_brief": row.get("scenario_brief"),
        "scenario_tags": (row.get("scenario_tags") or [])[:8],
    }


def patient_texts(report: dict[str, Any]) -> list[str]:
    return [
        clean_text(item.get("text"))
        for item in (report.get("transcript") or [])
        if item.get("role") == "patient" and clean_text(item.get("text"))
    ]


def reviewer_payload(report: dict[str, Any]) -> dict[str, Any]:
    reviewer = report.get("reviewer_result") or {}
    if isinstance(reviewer.get("result"), dict):
        return reviewer["result"]
    return reviewer


def summary_payload(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("session_longitudinal_summary") or {}
    if isinstance(summary.get("result"), dict):
        return summary["result"]
    return summary


def runtime_review_digest(report: dict[str, Any], runtime_report_path: str | Path) -> dict[str, Any]:
    final_state = report.get("final_session_state") or {}
    reviewer = reviewer_payload(report)
    summary = summary_payload(report)
    summary_core = summary.get("session_longitudinal_summary") or {}
    findings = reviewer.get("findings") or []
    return {
        "schema_version": "theraskill_patient_simulator_hermes_review_digest_v1",
        "runtime_report_path": str(runtime_report_path),
        "runtime_report_sha256": file_sha256(runtime_report_path),
        "scenario": scenario_digest(report.get("scenario") or {}),
        "review_gate": reviewer.get("review_gate") or {},
        "final_state": {
            "alliance_state": final_state.get("alliance_state"),
            "disclosure_state": final_state.get("disclosure_state"),
            "resistance_state": final_state.get("resistance_state"),
            "readiness_state": final_state.get("readiness_state"),
            "risk_state": final_state.get("risk_state"),
            "felt_understanding": final_state.get("felt_understanding"),
            "topic_focus": final_state.get("topic_focus") or [],
            "unfinished_threads": final_state.get("unfinished_threads") or [],
        },
        "session_summary": {
            "case_progress_summary": summary_core.get("case_progress_summary") or [],
            "key_turning_points": summary_core.get("key_turning_points") or [],
            "active_unresolved_threads": summary_core.get("active_unresolved_threads") or [],
            "boundary_or_risk_watchpoints": summary_core.get("boundary_or_risk_watchpoints") or [],
            "next_turn_setup": summary_core.get("next_turn_setup") or [],
        },
        "finding_digest": [
            {
                "severity": row.get("severity"),
                "category": row.get("category"),
                "message": row.get("message"),
            }
            for row in findings[:8]
        ],
        "patient_text_sample": patient_texts(report)[:4],
        "trace_counts": {
            "transcript_turns": len(report.get("transcript") or []),
            "patient_turn_records": len(report.get("turn_records") or []),
            "reviewer_finding_count": len(findings),
        },
    }


def command_list_scenarios(args: argparse.Namespace) -> int:
    rows = load_scenarios(args.scenario_registry)
    families = set(args.scenario_family or [])
    selected: list[dict[str, Any]] = []
    for row in rows:
        if row.get("simulation_mode") != "patient_simulator":
            continue
        if args.split_value is not None and (row.get("split_labels") or {}).get(args.split_key) != args.split_value:
            continue
        if families and row.get("scenario_family") not in families:
            continue
        selected.append(scenario_digest(row))
        if len(selected) >= args.limit:
            break
    print(
        json.dumps(
            {
                "status": "complete",
                "scenario_count": len(selected),
                "scenarios": selected,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_run_scenario(args: argparse.Namespace) -> int:
    rows = load_scenarios(args.scenario_registry)
    scenario = select_scenario(rows, args.scenario_id)
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
    report = execute_scenario_row(
        scenario,
        bundle,
        runtime_stack,
        report_metadata={
            "hermes_adapter": True,
            "adapter_command": "run-scenario",
        },
    )
    output_root = Path(args.output_root)
    runtime_dir = output_root / "runtime_reports"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / f"{args.scenario_id.replace('::', '__')}.json"
    if runtime_path.exists() and not args.overwrite:
        raise FileExistsError(f"runtime report exists: {runtime_path}")
    write_json(runtime_path, report)
    digest = runtime_review_digest(report, runtime_path)
    digest_path = output_root / "review_digests" / f"{args.scenario_id.replace('::', '__')}.json"
    write_json(digest_path, digest)
    print(
        json.dumps(
            {
                "status": "complete",
                "runtime_report_path": str(runtime_path),
                "review_digest_path": str(digest_path),
                "scenario": scenario_digest(scenario),
                "review_gate": digest["review_gate"],
                "final_state": digest["final_state"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_review_runtime(args: argparse.Namespace) -> int:
    report_path = Path(args.runtime_report)
    report = read_json(report_path)
    digest = runtime_review_digest(report, report_path)
    print(json.dumps(digest, ensure_ascii=False, indent=2))
    return 0


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
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "parsed_stdout": parsed_stdout,
    }


def build_optional_flag(args: argparse.Namespace, name: str) -> list[str]:
    value = getattr(args, name)
    if value is None:
        return []
    return [f"--{name.replace('_', '-')}", str(value)]


def command_run_benchmark(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(args.benchmark_script),
        "--scenario-registry",
        str(args.scenario_registry),
        "--extraction-config",
        str(args.extraction_config),
        "--model-config",
        str(args.model_config),
        "--summary-prompt",
        str(args.summary_prompt),
        "--reviewer-prompt",
        str(args.reviewer_prompt),
        "--output-dir",
        str(args.output_dir),
        "--max-scenarios",
        str(args.max_scenarios),
        "--split-key",
        str(args.split_key),
        "--split-value",
        str(args.split_value),
    ]
    if args.overwrite:
        command.append("--overwrite")
    for family in args.scenario_family or []:
        command.extend(["--scenario-family", str(family)])
    for scenario_id in args.scenario_id or []:
        command.extend(["--scenario-id", str(scenario_id)])
    command.extend(build_optional_flag(args, "parse_retries"))
    command.extend(build_optional_flag(args, "max_prompt_tokens"))
    command.extend(build_optional_flag(args, "max_output_tokens"))

    run_info = run_python_command(command)
    if run_info["returncode"] != 0:
        raise RuntimeError(
            f"benchmark command failed: returncode={run_info['returncode']} stderr={run_info['stderr'] or run_info['stdout']}"
        )
    summary_path = Path(args.output_dir) / "benchmark_summary.json"
    summary = read_json(summary_path)
    print(
        json.dumps(
            {
                "status": "complete",
                "output_dir": str(args.output_dir),
                "benchmark_summary_path": str(summary_path),
                "counts": summary.get("counts") or {},
                "distributions": summary.get("distributions") or {},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_run_conditioned_eval(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(args.conditioned_script),
        "--scenario-registry",
        str(args.scenario_registry),
        "--extraction-config",
        str(args.extraction_config),
        "--model-config",
        str(args.model_config),
        "--summary-prompt",
        str(args.summary_prompt),
        "--reviewer-prompt",
        str(args.reviewer_prompt),
        "--output-dir",
        str(args.output_dir),
        "--max-scenarios",
        str(args.max_scenarios),
        "--split-key",
        str(args.split_key),
        "--split-value",
        str(args.split_value),
    ]
    if args.overwrite:
        command.append("--overwrite")
    for family in args.scenario_family or []:
        command.extend(["--scenario-family", str(family)])
    for scenario_id in args.scenario_id or []:
        command.extend(["--scenario-id", str(scenario_id)])
    command.extend(build_optional_flag(args, "parse_retries"))
    command.extend(build_optional_flag(args, "max_prompt_tokens"))
    command.extend(build_optional_flag(args, "max_output_tokens"))

    run_info = run_python_command(command)
    if run_info["returncode"] != 0:
        raise RuntimeError(
            f"conditioned eval command failed: returncode={run_info['returncode']} stderr={run_info['stderr'] or run_info['stdout']}"
        )
    summary_path = Path(args.output_dir) / "comparison_summary.json"
    summary = read_json(summary_path)
    print(
        json.dumps(
            {
                "status": "complete",
                "output_dir": str(args.output_dir),
                "comparison_summary_path": str(summary_path),
                "counts": summary.get("counts") or {},
                "distributions": summary.get("distributions") or {},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "list-scenarios":
        return command_list_scenarios(args)
    if args.command == "run-scenario":
        return command_run_scenario(args)
    if args.command == "review-runtime":
        return command_review_runtime(args)
    if args.command == "run-benchmark":
        return command_run_benchmark(args)
    if args.command == "run-conditioned-eval":
        return command_run_conditioned_eval(args)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
