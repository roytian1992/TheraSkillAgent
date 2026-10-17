#!/usr/bin/env python3
"""Run reproducible dual-agent research sessions through the unified platform core."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts/Simulation"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_patient_scenario_execution import build_family_grounded_turns  # noqa: E402
from theraskill_simulation.contracts import SessionMode, SessionStatus  # noqa: E402
from theraskill_simulation.engines import (  # noqa: E402
    CommandBackedLLMSimulationReviewer,
    CommandBackedLLMTherapistEngine,
    ExistingLLMPatientEngine,
)
from theraskill_simulation.repository import SQLiteSessionRepository  # noqa: E402
from theraskill_simulation.scenarios import ScenarioRegistry  # noqa: E402
from theraskill_simulation.service import SimulationSessionService  # noqa: E402
from theraskill_simulation.visibility import ExportScope, find_forbidden_public_fields  # noqa: E402

DEFAULT_REGISTRY = PROJECT_ROOT / "outputs/simulation/scenarios/scenario_registry.jsonl"
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "outputs/simulation/platform/dual_research_5fam_v1_qwen8002_20260711"
)
FAMILIES = (
    "supportive_exploration",
    "cognitive_reframing_probe",
    "rupture_and_repair",
    "risk_escalation_and_handoff",
    "rebt_belief_work",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", action="append")
    parser.add_argument("--scenario-id", action="append")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.workers < 16:
        parser.error("--workers must be at least 16 for each available model port")
    if args.cycles < 1:
        parser.error("--cycles must be positive")
    args.model_config = args.model_config or [str(DEFAULT_MODEL_CONFIG)]
    return args


def write_json(path: Path, value: Any, *, overwrite: bool = True) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def select_scenarios(path: Path, explicit_ids: list[str] | None) -> list[dict[str, Any]]:
    explicit = set(explicit_ids or [])
    selected_by_family: dict[str, dict[str, Any]] = {}
    explicit_rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if explicit:
                if row.get("scenario_id") in explicit:
                    explicit_rows.append(row)
                continue
            family = str(row.get("scenario_family") or "")
            if family not in FAMILIES or family in selected_by_family:
                continue
            if (row.get("split_labels") or {}).get("formal_split") != "test":
                continue
            selected_by_family[family] = row
    if explicit:
        missing = explicit - {str(row.get("scenario_id")) for row in explicit_rows}
        if missing:
            raise RuntimeError(f"scenario ids not found: {sorted(missing)}")
        return sorted(explicit_rows, key=lambda row: str(row["scenario_id"]))
    missing_families = set(FAMILIES) - set(selected_by_family)
    if missing_families:
        raise RuntimeError(f"missing test families: {sorted(missing_families)}")
    return [selected_by_family[family] for family in FAMILIES]


def model_arm(path: str | Path) -> str:
    stem = Path(path).stem
    for suffix in ("_timeout600", "_timeout300"):
        stem = stem.replace(suffix, "")
    return stem.replace("local_model_config_", "")


def run_task(
    scenario: dict[str, Any],
    model_config: str,
    *,
    args: argparse.Namespace,
    database_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    family = str(scenario["scenario_family"])
    arm = model_arm(model_config)
    session_id = f"sim_research_{arm}_{family}"
    work_root = output_dir / "work" / arm / family
    patient_engine = ExistingLLMPatientEngine(
        project_root=PROJECT_ROOT,
        extraction_config=args.extraction_config,
        model_config=model_config,
    )
    therapist_engine = CommandBackedLLMTherapistEngine(
        project_root=PROJECT_ROOT,
        extraction_config=args.extraction_config,
        model_config=model_config,
        work_root=work_root / "therapist",
    )
    reviewer_engine = CommandBackedLLMSimulationReviewer(
        project_root=PROJECT_ROOT,
        extraction_config=args.extraction_config,
        model_config=model_config,
        work_root=work_root / "reviewer",
    )
    service = SimulationSessionService(
        repository=SQLiteSessionRepository(database_path, initialize=False),
        scenario_registry=ScenarioRegistry(args.scenario_registry),
        patient_engine=patient_engine,
        therapist_engine=therapist_engine,
        reviewer_engine=reviewer_engine,
    )
    service.create_session(
        scenario_id=scenario["scenario_id"],
        session_id=session_id,
        mode=SessionMode.DUAL_AGENT_RESEARCH,
    )
    bundle = read_json(scenario["case_bundle_path"])
    opening = build_family_grounded_turns(scenario, bundle)[0]
    cycle_results = []
    for cycle in range(1, args.cycles + 1):
        cycle_results.append(
            service.run_dual_cycle(
                session_id,
                initial_therapist_utterance=opening if cycle == 1 else None,
                idempotency_key=f"{session_id}:cycle:{cycle}",
            )
        )
    completed = service.complete_session(session_id)
    public_export = service.export_session(session_id, scope=ExportScope.PUBLIC)
    research_export = service.export_session(session_id, scope=ExportScope.RESEARCHER)
    roles = [turn.role for turn in completed.public_view.transcript]
    expected_roles = ["therapist"]
    for _ in range(args.cycles):
        expected_roles.extend(["patient", "therapist"])
    leakage = find_forbidden_public_fields(public_export)
    checks = {
        "session_completed": completed.session.status == SessionStatus.COMPLETED,
        "cycle_count_matches": completed.public_view.progress.get("dual_cycle_count")
        == args.cycles,
        "role_alternation_matches": roles == expected_roles,
        "review_generated": completed.public_view.review is not None,
        "review_gate_pass": bool(completed.public_view.review)
        and completed.public_view.review.gate_status == "pass",
        "public_private_separation": not leakage,
        "both_private_states_preserved": "private_patient_state" in research_export
        and "private_therapist_state" in research_export,
        "event_trace_preserved": bool(research_export.get("events")),
        "model_recorded": bool(completed.session.model_config_id),
    }
    case_dir = output_dir / "cases" / arm / family
    write_json(case_dir / "public_export.json", public_export)
    write_json(case_dir / "research_export.json", research_export)
    result = {
        "schema_version": "theraskill_simulation_platform_research_case_v1",
        "status": "pass" if all(checks.values()) else "fail",
        "session_id": session_id,
        "scenario_id": scenario["scenario_id"],
        "scenario_family": family,
        "model_arm": arm,
        "model_config": str(Path(model_config).resolve()),
        "cycles": args.cycles,
        "transcript_turn_count": len(roles),
        "checks": checks,
        "public_leakage_findings": leakage,
        "review_gate_status": (
            completed.public_view.review.gate_status
            if completed.public_view.review
            else None
        ),
        "final_snapshot_id": completed.snapshot_id,
        "final_snapshot_checksum": completed.checksum,
        "public_export_path": str((case_dir / "public_export.json").resolve()),
        "research_export_path": str((case_dir / "research_export.json").resolve()),
    }
    write_json(case_dir / "case_summary.json", result)
    return result


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"output directory is not empty; pass --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = select_scenarios(Path(args.scenario_registry), args.scenario_id)
    tasks = [(scenario, config) for config in args.model_config for scenario in selected]
    database_path = output_dir / "research_sessions.sqlite"
    SQLiteSessionRepository(database_path)
    effective_workers = min(args.workers, len(tasks))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_map = {
            executor.submit(
                run_task,
                scenario,
                config,
                args=args,
                database_path=database_path,
                output_dir=output_dir,
            ): (scenario["scenario_id"], model_arm(config))
            for scenario, config in tasks
        }
        for future in as_completed(future_map):
            scenario_id, arm = future_map[future]
            try:
                row = future.result()
                results.append(row)
                print(json.dumps({"session": row["session_id"], "status": row["status"]}), flush=True)
            except Exception as exc:
                errors.append(
                    {
                        "scenario_id": scenario_id,
                        "model_arm": arm,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    }
                )
    results.sort(key=lambda row: (row["model_arm"], row["scenario_family"]))
    passed = sum(row["status"] == "pass" for row in results)
    failure_replay = [
        {
            "scenario_id": row.get("scenario_id"),
            "model_arm": row.get("model_arm"),
            "command": (
                f"conda run -n theraskill python {Path(__file__).resolve()} "
                f"--scenario-id '{row.get('scenario_id')}' --cycles {args.cycles} "
                f"--workers 16 --output-dir <new_output_dir>"
            ),
        }
        for row in [*results, *errors]
        if row.get("status") != "pass"
    ]
    write_json(
        output_dir / "failure_replay.json",
        {"failed_count": len(failure_replay), "items": failure_replay},
    )
    export_path = output_dir / "paper_benchmark_export.jsonl"
    with export_path.open("w", encoding="utf-8") as handle:
        for row in results:
            public = read_json(row["public_export_path"])["public_view"]
            handle.write(
                json.dumps(
                    {
                        "session_id": row["session_id"],
                        "scenario_id": row["scenario_id"],
                        "scenario_family": row["scenario_family"],
                        "model_arm": row["model_arm"],
                        "transcript": public["transcript"],
                        "review": public.get("review"),
                        "snapshot_checksum": row["final_snapshot_checksum"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    summary = {
        "schema_version": "theraskill_simulation_platform_research_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if passed == len(tasks) and not errors else "failed",
        "environment": {
            "conda_env": "theraskill",
            "python_executable": sys.executable,
            "model_configs": [str(Path(path).resolve()) for path in args.model_config],
        },
        "selection": {
            "split_key": "formal_split",
            "split_value": "test",
            "scenario_ids": [row["scenario_id"] for row in selected],
            "deterministic_first_per_family": not bool(args.scenario_id),
        },
        "concurrency": {
            "requested_workers": args.workers,
            "effective_workers": effective_workers,
            "model_port": 8002,
        },
        "counts": {
            "selected_tasks": len(tasks),
            "completed_tasks": len(results),
            "passed_tasks": passed,
            "failed_tasks": len(tasks) - passed,
            "completed_dual_cycles": sum(row.get("cycles", 0) for row in results),
            "exported_benchmark_rows": len(results),
        },
        "model_arm_distribution": dict(sorted(Counter(row["model_arm"] for row in results).items())),
        "family_distribution": dict(sorted(Counter(row["scenario_family"] for row in results).items())),
        "review_gate_distribution": dict(sorted(Counter(str(row["review_gate_status"]) for row in results).items())),
        "cases": results,
        "errors": errors,
        "artifacts": {
            "database": str(database_path),
            "paper_benchmark_export": str(export_path),
            "failure_replay": str(output_dir / "failure_replay.json"),
        },
    }
    write_json(output_dir / "research_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
