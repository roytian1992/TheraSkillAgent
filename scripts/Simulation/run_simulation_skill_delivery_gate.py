#!/usr/bin/env python3
"""Run a balanced multi-family delivery gate over all four simulation skills."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import ensure_output_dir, file_sha256, read_json, read_jsonl, write_json, write_text  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_SIMULATION_SKILL_DELIVERY_GATE_DIR,
    RUN_SIMULATION_SKILL_VALIDATION_RELPATH,
    SIMULATION_SCENARIO_REGISTRY_PATH,
    project_script_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs" / "extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs" / "theraskill" / "retrieval.yaml"
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH
DEFAULT_VALIDATION_SCRIPT = project_script_path(RUN_SIMULATION_SKILL_VALIDATION_RELPATH)
DEFAULT_OUTPUT_DIR = DEFAULT_SIMULATION_SKILL_DELIVERY_GATE_DIR

TARGET_FAMILIES = (
    "supportive_exploration",
    "cognitive_reframing_probe",
    "rupture_and_repair",
    "risk_escalation_and_handoff",
    "rebt_belief_work",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--validation-script", default=str(DEFAULT_VALIDATION_SCRIPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--split-key", default="formal_split")
    parser.add_argument("--split-value", default="test")
    parser.add_argument("--scenario-family", action="append")
    parser.add_argument("--max-scenarios-per-family", type=int, default=1)
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--case-source-id")
    parser.add_argument("--persona-tag", default="情绪")
    parser.add_argument("--memory-group-id", default="target::01_1_to_5_history_pairs")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def sanitize_stem(value: str) -> str:
    return value.replace("::", "__").replace("/", "_")


def select_scenarios(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    families = tuple(args.scenario_family or TARGET_FAMILIES)
    family_set = set(families)
    candidates = [
        row
        for row in rows
        if row.get("simulation_mode") == "patient_simulator"
        and row.get("scenario_family") in family_set
        and (row.get("split_labels") or {}).get(args.split_key) == args.split_value
    ]
    candidates.sort(key=lambda row: (str(row.get("scenario_family")), str(row.get("case_id")), str(row.get("scenario_id"))))
    selected: list[dict[str, Any]] = []
    family_counts: Counter[str] = Counter()
    for row in candidates:
        family = str(row.get("scenario_family"))
        if family_counts[family] >= args.max_scenarios_per_family:
            continue
        selected.append(row)
        family_counts[family] += 1
    missing = [family for family in families if family_counts[family] < args.max_scenarios_per_family]
    if missing:
        raise ValueError(f"insufficient {args.split_key}={args.split_value} scenarios for families: {missing}")
    return selected


def add_optional_llm_args(command: list[str], args: argparse.Namespace) -> None:
    if args.parse_retries is not None:
        command.extend(["--parse-retries", str(args.parse_retries)])
    if args.max_prompt_tokens is not None:
        command.extend(["--max-prompt-tokens", str(args.max_prompt_tokens)])
    if args.max_output_tokens is not None:
        command.extend(["--max-output-tokens", str(args.max_output_tokens)])


def run_case(scenario: dict[str, Any], args: argparse.Namespace, cases_dir: Path) -> dict[str, Any]:
    scenario_id = str(scenario["scenario_id"])
    case_output_dir = cases_dir / sanitize_stem(scenario_id)
    command = [
        sys.executable,
        str(args.validation_script),
        "--scenario-id",
        scenario_id,
        "--extraction-config",
        args.extraction_config,
        "--model-config",
        args.model_config,
        "--output-dir",
        str(case_output_dir),
        "--persona-tag",
        args.persona_tag,
        "--memory-group-id",
        args.memory_group_id,
    ]
    if args.case_source_id:
        command.extend(["--case-source-id", args.case_source_id])
    if args.overwrite:
        command.append("--overwrite")
    add_optional_llm_args(command, args)
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    report_path = case_output_dir / "simulation_skill_validation_report.json"
    report = read_json(report_path) if report_path.exists() else {}
    reviewer_gate = (((report.get("artifacts") or {}).get("reviewer") or {}).get("review_digest") or {}).get("review_gate") or {}
    stage_statuses = {
        stage: (((report.get("artifacts") or {}).get(stage) or {}).get("status"))
        for stage in ("patient", "therapist", "dual", "reviewer")
    }
    integrity_status = (report.get("integrity_gate") or {}).get("status")
    passes = all(
        [
            completed.returncode == 0,
            all(status == "complete" for status in stage_statuses.values()),
            integrity_status == "pass",
            reviewer_gate.get("gate_status") == "pass",
        ]
    )
    return {
        "scenario_id": scenario_id,
        "scenario_family": scenario.get("scenario_family"),
        "case_id": scenario.get("case_id"),
        "risk_band": scenario.get("risk_band"),
        "difficulty_band": scenario.get("difficulty_band"),
        "status": "pass" if passes else "fail",
        "returncode": completed.returncode,
        "stage_statuses": stage_statuses,
        "integrity_status": integrity_status,
        "reviewer_gate": reviewer_gate,
        "report_path": str(report_path) if report_path.exists() else None,
        "report_sha256": file_sha256(report_path) if report_path.exists() else None,
        "command": command,
        "stdout_tail": completed.stdout[-3000:],
        "stderr_tail": completed.stderr[-3000:],
    }


def build_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Simulation Skill Delivery Gate",
        "",
        f"- created_at: {summary.get('created_at')}",
        f"- status: {summary.get('status')}",
        f"- passes_gate: {(summary.get('gate') or {}).get('passes_gate')}",
        f"- selected_cases: {(summary.get('counts') or {}).get('selected_cases')}",
        f"- passed_cases: {(summary.get('counts') or {}).get('passed_cases')}",
        f"- failed_cases: {(summary.get('counts') or {}).get('failed_cases')}",
        "",
        "## Cases",
        "",
        "| family | scenario | risk | stages | integrity | reviewer | result |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary.get("cases") or []:
        stages = ", ".join(f"{name}:{status}" for name, status in row.get("stage_statuses", {}).items())
        lines.append(
            f"| {row.get('scenario_family')} | {row.get('scenario_id')} | {row.get('risk_band')} | "
            f"{stages} | {row.get('integrity_status')} | {(row.get('reviewer_gate') or {}).get('gate_status')} | "
            f"{row.get('status')} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    if args.max_scenarios_per_family < 1:
        raise ValueError("--max-scenarios-per-family must be >= 1")
    if args.max_workers < 1:
        raise ValueError("--max-workers must be >= 1")
    output_dir = ensure_output_dir(args.output_dir, overwrite=args.overwrite, allow_existing_empty=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    selected = select_scenarios(read_jsonl(args.scenario_registry), args)
    results: list[dict[str, Any]] = []
    worker_count = min(args.max_workers, len(selected))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {executor.submit(run_case, row, args, cases_dir): row for row in selected}
        for future in as_completed(future_map):
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "progress": f"{len(results)}/{len(selected)}",
                        "scenario_id": result["scenario_id"],
                        "status": result["status"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    family_order = {family: index for index, family in enumerate(args.scenario_family or TARGET_FAMILIES)}
    results.sort(key=lambda row: (family_order.get(str(row.get("scenario_family")), 999), str(row.get("scenario_id"))))

    failed = [row for row in results if row["status"] != "pass"]
    family_counts = Counter(str(row.get("scenario_family")) for row in results)
    reviewer_gate_counts = Counter(str((row.get("reviewer_gate") or {}).get("gate_status")) for row in results)
    integrity_counts = Counter(str(row.get("integrity_status")) for row in results)
    expected_family_count = len(args.scenario_family or TARGET_FAMILIES)
    passes_gate = all(
        [
            not failed,
            len(results) == len(selected),
            len(family_counts) == expected_family_count,
            all(count == args.max_scenarios_per_family for count in family_counts.values()),
        ]
    )
    summary = {
        "schema_version": "theraskill_simulation_skill_delivery_gate_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if passes_gate else "failed",
        "selection": {
            "scenario_registry": args.scenario_registry,
            "scenario_registry_sha256": file_sha256(args.scenario_registry),
            "split_key": args.split_key,
            "split_value": args.split_value,
            "target_families": list(args.scenario_family or TARGET_FAMILIES),
            "max_scenarios_per_family": args.max_scenarios_per_family,
            "scenario_ids": [row.get("scenario_id") for row in selected],
            "selection_policy": "first_case_id_per_family_after_stable_sort",
        },
        "runtime": {
            "conda_environment": "theraskill",
            "python_executable": sys.executable,
            "model_config": args.model_config,
            "extraction_config": args.extraction_config,
            "max_workers_requested": args.max_workers,
            "max_workers_effective": worker_count,
        },
        "counts": {
            "selected_cases": len(selected),
            "completed_cases": len(results),
            "passed_cases": len(results) - len(failed),
            "failed_cases": len(failed),
            "covered_families": len(family_counts),
        },
        "distributions": {
            "scenario_family": dict(sorted(family_counts.items())),
            "reviewer_gate_status": dict(sorted(reviewer_gate_counts.items())),
            "integrity_status": dict(sorted(integrity_counts.items())),
        },
        "gate": {
            "passes_gate": passes_gate,
            "criteria": {
                "all_cases_passed": not failed,
                "all_selected_cases_completed": len(results) == len(selected),
                "all_target_families_covered": len(family_counts) == expected_family_count,
                "balanced_family_count": all(
                    count == args.max_scenarios_per_family for count in family_counts.values()
                ),
            },
        },
        "cases": results,
        "issues": [
            {
                "scenario_id": row["scenario_id"],
                "scenario_family": row["scenario_family"],
                "returncode": row["returncode"],
                "stage_statuses": row["stage_statuses"],
                "integrity_status": row["integrity_status"],
                "reviewer_gate": row["reviewer_gate"],
                "stderr_tail": row["stderr_tail"],
            }
            for row in failed
        ],
    }
    summary_path = output_dir / "delivery_gate_summary.json"
    markdown_path = output_dir / "delivery_gate_summary.md"
    write_json(summary_path, summary, overwrite=args.overwrite)
    write_text(markdown_path, build_summary_markdown(summary), overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "passes_gate": passes_gate,
                "summary": str(summary_path),
                "summary_markdown": str(markdown_path),
                "counts": summary["counts"],
                "distributions": summary["distributions"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passes_gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
