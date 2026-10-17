#!/usr/bin/env python3
"""Run one traceable patient -> therapist -> dual -> reviewer validation chain."""

from __future__ import annotations

import argparse
import json
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

from io_utils import ensure_output_dir, file_sha256, read_json, write_json, write_text  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_HERMES_ADAPTER_RUNTIME_REPORT_PATH,
    DEFAULT_SIMULATION_SKILL_VALIDATION_OUTPUT_DIR,
    RUN_DUAL_SESSION_SIMULATOR_HERMES_ADAPTER_RELPATH,
    RUN_PATIENT_SIMULATOR_HERMES_ADAPTER_RELPATH,
    RUN_SIMULATION_REVIEWER_HERMES_ADAPTER_RELPATH,
    RUN_THERAPIST_SIMULATOR_HERMES_ADAPTER_RELPATH,
    project_script_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs" / "theraskill" / "retrieval.yaml"
DEFAULT_RUNTIME_REPORT = DEFAULT_HERMES_ADAPTER_RUNTIME_REPORT_PATH
DEFAULT_OUTPUT_DIR = DEFAULT_SIMULATION_SKILL_VALIDATION_OUTPUT_DIR
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs" / "extraction_config.yaml"
DEFAULT_PATIENT_ADAPTER = project_script_path(RUN_PATIENT_SIMULATOR_HERMES_ADAPTER_RELPATH)
DEFAULT_THERAPIST_ADAPTER = project_script_path(RUN_THERAPIST_SIMULATOR_HERMES_ADAPTER_RELPATH)
DEFAULT_DUAL_ADAPTER = project_script_path(RUN_DUAL_SESSION_SIMULATOR_HERMES_ADAPTER_RELPATH)
DEFAULT_REVIEWER_ADAPTER = project_script_path(RUN_SIMULATION_REVIEWER_HERMES_ADAPTER_RELPATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--runtime-report",
        help="Reuse an existing patient runtime report. Defaults to the released adapter report.",
    )
    input_group.add_argument(
        "--scenario-id",
        help="Run the patient skill first, then validate the resulting runtime report.",
    )
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--case-source-id")
    parser.add_argument("--persona-tag", default="情绪")
    parser.add_argument("--memory-group-id", default="target::01_1_to_5_history_pairs")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def run_python_command(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
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


def extract_digest(summary: dict[str, Any]) -> dict[str, Any]:
    if isinstance(summary.get("review_digest"), dict):
        return summary["review_digest"]
    return {}


def canonical_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return str(candidate.resolve())


def build_integrity_gate(
    *,
    runtime_report: str,
    patient_artifact: dict[str, Any],
    therapist_artifact: dict[str, Any],
    dual_artifact: dict[str, Any],
    reviewer_artifact: dict[str, Any],
) -> dict[str, Any]:
    therapist_bundle_path = therapist_artifact.get("bundle_path")
    dual_bundle_path = dual_artifact.get("bundle_path")
    reviewer_bundle_path = reviewer_artifact.get("bundle_path")
    therapist_bundle = read_json(therapist_bundle_path)
    dual_bundle = read_json(dual_bundle_path)
    reviewer_bundle = read_json(reviewer_bundle_path)
    normalized_session = read_json(reviewer_bundle["normalized_session_input"])

    generated_utterance = (therapist_bundle.get("review_digest") or {}).get("therapist_utterance")
    reviewed_continuation = (normalized_session.get("therapist_continuation") or {}).get("therapist_utterance")
    turn_records = normalized_session.get("turn_records") or []
    transcript = normalized_session.get("transcript") or []
    checks = {
        "patient_runtime_matches_validation_input": canonical_path(patient_artifact.get("runtime_report_path"))
        == canonical_path(runtime_report),
        "dual_reuses_therapist_bundle": canonical_path(dual_bundle.get("therapist_bundle_path"))
        == canonical_path(therapist_bundle_path),
        "reviewer_uses_dual_bundle": canonical_path(reviewer_bundle.get("dual_bundle_path"))
        == canonical_path(dual_bundle_path),
        "reviewed_continuation_matches_generated_utterance": bool(generated_utterance)
        and reviewed_continuation == generated_utterance,
        "review_transcript_ends_with_generated_continuation": bool(transcript)
        and transcript[-1].get("role") == "therapist"
        and transcript[-1].get("text") == generated_utterance,
        "review_turn_marks_continuation_as_awaiting_patient": bool(turn_records)
        and bool(turn_records[-1].get("continuation_only"))
        and not bool(turn_records[-1].get("patient_turn")),
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "artifact_sha256": {
            "runtime_report": file_sha256(runtime_report),
            "therapist_bundle": file_sha256(therapist_bundle_path),
            "dual_bundle": file_sha256(dual_bundle_path),
            "reviewer_bundle": file_sha256(reviewer_bundle_path),
            "normalized_session": file_sha256(reviewer_bundle["normalized_session_input"]),
        },
    }


def build_markdown_summary(report: dict[str, Any]) -> str:
    patient = (report.get("artifacts") or {}).get("patient") or {}
    therapist = (report.get("artifacts") or {}).get("therapist") or {}
    dual = (report.get("artifacts") or {}).get("dual") or {}
    reviewer = (report.get("artifacts") or {}).get("reviewer") or {}
    reviewer_digest = reviewer.get("review_digest") or {}
    gate = reviewer_digest.get("review_gate") or {}
    integrity_gate = report.get("integrity_gate") or {}
    lines = [
        "# Simulation Skill Validation",
        "",
        f"- created_at: {report.get('created_at')}",
        f"- input_mode: {report.get('input_mode')}",
        f"- scenario_id: {report.get('scenario_id')}",
        f"- runtime_report: {report.get('runtime_report')}",
        f"- model_config: {report.get('model_config')}",
        "",
        "## Stage Status",
        "",
        f"- patient: {patient.get('status')}",
        f"- therapist: {therapist.get('status')}",
        f"- dual: {dual.get('status')}",
        f"- reviewer: {reviewer.get('status')}",
        f"- chain integrity: {integrity_gate.get('status')}",
        "",
        "## Key Outputs",
        "",
        f"- patient runtime report: {patient.get('runtime_report_path')}",
        f"- therapist bundle: {therapist.get('bundle_path')}",
        f"- dual bundle: {dual.get('bundle_path')}",
        f"- reviewer bundle: {reviewer.get('bundle_path')}",
        "",
        "## Reviewer Gate",
        "",
        f"- gate_status: {gate.get('gate_status')}",
        f"- persona_consistency: {gate.get('persona_consistency')}",
        f"- memory_faithfulness: {gate.get('memory_faithfulness')}",
        f"- stage_fit: {gate.get('stage_fit')}",
        f"- boundary_compliance: {gate.get('boundary_compliance')}",
        f"- risk_attention: {gate.get('risk_attention')}",
        f"- repetition_or_degeneration: {gate.get('repetition_or_degeneration')}",
        "",
        "## Chain Integrity",
        "",
    ]
    lines.extend(
        f"- {name}: {'pass' if passed else 'fail'}"
        for name, passed in (integrity_gate.get("checks") or {}).items()
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output_dir = ensure_output_dir(args.output_dir, overwrite=args.overwrite, allow_existing_empty=True)

    patient_output_root = output_dir / "patient"
    therapist_output_root = output_dir / "therapist"
    dual_output_root = output_dir / "dual"
    reviewer_output_root = output_dir / "reviewer"

    runtime_report = args.runtime_report or str(DEFAULT_RUNTIME_REPORT)
    if args.scenario_id:
        patient_command = [
            sys.executable,
            str(DEFAULT_PATIENT_ADAPTER),
            "run-scenario",
            "--scenario-id",
            args.scenario_id,
            "--extraction-config",
            args.extraction_config,
            "--model-config",
            args.model_config,
            "--output-root",
            str(patient_output_root),
        ]
        if args.overwrite:
            patient_command.append("--overwrite")
        add_optional_llm_args(patient_command, args)
        patient_result = run_python_command(patient_command)
        if not patient_result["ok"]:
            raise SystemExit(
                json.dumps(
                    {"status": "failed", "stage": "patient", **patient_result},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        runtime_report = (patient_result["parsed_stdout"] or {}).get("runtime_report_path")
        if not runtime_report:
            raise SystemExit("patient adapter did not return runtime_report_path")
        patient_digest_path = (patient_result["parsed_stdout"] or {}).get("review_digest_path")
        patient_artifact = {
            **(patient_result["parsed_stdout"] or {}),
            "execution_mode": "scenario_generated",
            "review_digest": read_json(patient_digest_path) if patient_digest_path else {},
        }
    else:
        patient_command = [
            sys.executable,
            str(DEFAULT_PATIENT_ADAPTER),
            "review-runtime",
            "--runtime-report",
            runtime_report,
        ]
        patient_result = run_python_command(patient_command)
        if not patient_result["ok"] or patient_result["parsed_stdout"] is None:
            raise SystemExit(
                json.dumps(
                    {"status": "failed", "stage": "patient", **patient_result},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        patient_artifact = {
            "status": "complete",
            "execution_mode": "existing_runtime_reviewed",
            "runtime_report_path": runtime_report,
            "review_digest": patient_result["parsed_stdout"],
        }

    therapist_command = [
        sys.executable,
        str(DEFAULT_THERAPIST_ADAPTER),
        "run-turn",
        "--runtime-report",
        runtime_report,
        "--model-config",
        args.model_config,
        "--output-root",
        str(therapist_output_root),
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
        raise SystemExit(json.dumps({"status": "failed", "stage": "therapist", **therapist_result}, ensure_ascii=False, indent=2))

    therapist_bundle_path = (therapist_result["parsed_stdout"] or {}).get("bundle_path")
    if not therapist_bundle_path:
        raise SystemExit("therapist adapter did not return bundle_path")

    dual_command = [
        sys.executable,
        str(DEFAULT_DUAL_ADAPTER),
        "build-bundle",
        "--runtime-report",
        runtime_report,
        "--therapist-bundle",
        therapist_bundle_path,
        "--model-config",
        args.model_config,
        "--output-root",
        str(dual_output_root),
    ]
    if args.overwrite:
        dual_command.append("--overwrite")
    dual_result = run_python_command(dual_command)
    if not dual_result["ok"]:
        raise SystemExit(json.dumps({"status": "failed", "stage": "dual", **dual_result}, ensure_ascii=False, indent=2))

    dual_bundle_path = (dual_result["parsed_stdout"] or {}).get("bundle_path")
    if not dual_bundle_path:
        raise SystemExit("dual adapter did not return bundle_path")

    reviewer_command = [
        sys.executable,
        str(DEFAULT_REVIEWER_ADAPTER),
        "run-review",
        "--dual-bundle",
        dual_bundle_path,
        "--model-config",
        args.model_config,
        "--output-root",
        str(reviewer_output_root),
    ]
    if args.overwrite:
        reviewer_command.append("--overwrite")
    add_optional_llm_args(reviewer_command, args)
    reviewer_result = run_python_command(reviewer_command)
    if not reviewer_result["ok"]:
        raise SystemExit(json.dumps({"status": "failed", "stage": "reviewer", **reviewer_result}, ensure_ascii=False, indent=2))

    therapist_artifact = therapist_result["parsed_stdout"] or {}
    dual_artifact = dual_result["parsed_stdout"] or {}
    reviewer_artifact = reviewer_result["parsed_stdout"] or {}
    integrity_gate = build_integrity_gate(
        runtime_report=runtime_report,
        patient_artifact=patient_artifact,
        therapist_artifact=therapist_artifact,
        dual_artifact=dual_artifact,
        reviewer_artifact=reviewer_artifact,
    )
    report = {
        "schema_version": "theraskill_simulation_skill_validation_report_v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "input_mode": "scenario_generated" if args.scenario_id else "existing_runtime_report",
        "scenario_id": args.scenario_id,
        "runtime_report": runtime_report,
        "runtime_report_sha256": file_sha256(runtime_report),
        "extraction_config": args.extraction_config,
        "model_config": args.model_config,
        "artifacts": {
            "patient": patient_artifact,
            "therapist": therapist_artifact,
            "dual": dual_artifact,
            "reviewer": reviewer_artifact,
        },
        "integrity_gate": integrity_gate,
        "execution_trace": {
            "patient_command": patient_command,
            "therapist_command": therapist_command,
            "dual_command": dual_command,
            "reviewer_command": reviewer_command,
            "therapist_bundle_reused_by_dual": True,
        },
    }
    report_path = output_dir / "simulation_skill_validation_report.json"
    summary_path = output_dir / "simulation_skill_validation_summary.md"
    write_json(report_path, report, overwrite=args.overwrite)
    write_text(summary_path, build_markdown_summary(report), overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "status": "complete",
                "report": str(report_path),
                "summary": str(summary_path),
                "integrity_gate": integrity_gate,
                "reviewer_gate": (extract_digest((reviewer_result["parsed_stdout"] or {})).get("review_gate") or {}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if integrity_gate["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
