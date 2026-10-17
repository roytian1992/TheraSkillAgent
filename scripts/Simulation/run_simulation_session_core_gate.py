#!/usr/bin/env python3
"""Run the five-family real-LLM gate through the stateful session core."""

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
SRC_ROOT = PROJECT_ROOT / "src"
SIMULATION_SCRIPTS = PROJECT_ROOT / "scripts/Simulation"
for path in (SRC_ROOT, SIMULATION_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_patient_scenario_execution import build_family_grounded_turns  # noqa: E402
from theraskill_simulation.contracts import ActorRole, SessionStatus  # noqa: E402
from theraskill_simulation.engines import ExistingLLMPatientEngine  # noqa: E402
from theraskill_simulation.repository import SQLiteSessionRepository  # noqa: E402
from theraskill_simulation.scenarios import ScenarioRegistry  # noqa: E402
from theraskill_simulation.service import SimulationSessionService  # noqa: E402
from theraskill_simulation.visibility import ExportScope, find_forbidden_public_fields  # noqa: E402

DEFAULT_SCENARIO_REGISTRY = PROJECT_ROOT / "outputs/simulation/scenarios/scenario_registry.jsonl"
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "outputs/simulation/platform/session_core_gate_5fam_v2_qwen8002_20260711"
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
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    args = parser.parse_args()
    if args.workers < 16:
        parser.error("--workers must be at least 16 for an available model port")
    return args


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists; pass --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_selected_scenarios(registry_path: Path) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    with registry_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            family = row.get("scenario_family")
            if family not in FAMILIES or family in selected:
                continue
            if (row.get("split_labels") or {}).get("formal_split") != "test":
                continue
            selected[family] = row
    missing = sorted(set(FAMILIES) - set(selected))
    if missing:
        raise RuntimeError(f"missing formal_split=test scenarios for families: {missing}")
    return [selected[family] for family in FAMILIES]


def run_case(
    scenario: dict[str, Any],
    *,
    args: argparse.Namespace,
    database_path: Path,
    case_output_dir: Path,
) -> dict[str, Any]:
    family = str(scenario["scenario_family"])
    scenario_id = str(scenario["scenario_id"])
    session_id = f"sim_core_gate_{family}"
    engine = ExistingLLMPatientEngine(
        project_root=PROJECT_ROOT,
        extraction_config=args.extraction_config,
        model_config=args.model_config,
        parse_retries=args.parse_retries,
        max_prompt_tokens=args.max_prompt_tokens,
        max_output_tokens=args.max_output_tokens,
    )
    registry = ScenarioRegistry(args.scenario_registry)
    service = SimulationSessionService(
        repository=SQLiteSessionRepository(database_path, initialize=False),
        scenario_registry=registry,
        patient_engine=engine,
    )
    service.create_session(scenario_id=scenario_id, session_id=session_id)
    bundle = read_json(scenario["case_bundle_path"])
    therapist_turns = build_family_grounded_turns(scenario, bundle)
    turn_budget = int(scenario.get("recommended_turn_budget") or 3)
    therapist_turns = therapist_turns[:turn_budget]
    submit_results = []
    restart_verified = False
    idempotency_verified = False
    for index, therapist_turn in enumerate(therapist_turns, start=1):
        result = service.submit_turn(
            session_id,
            text=therapist_turn,
            actor=ActorRole.HUMAN_THERAPIST,
            idempotency_key=f"{session_id}:human-turn:{index}",
        )
        submit_results.append(result)
        if index == 1:
            duplicate = service.submit_turn(
                session_id,
                text="this duplicate payload must not execute",
                actor=ActorRole.HUMAN_THERAPIST,
                idempotency_key=f"{session_id}:human-turn:{index}",
            )
            idempotency_verified = duplicate == result
            service.pause_session(session_id)
            service = SimulationSessionService(
                repository=SQLiteSessionRepository(database_path, initialize=False),
                scenario_registry=registry,
                patient_engine=engine,
            )
            paused = service.get_snapshot(session_id)
            service.resume_session(session_id)
            restart_verified = (
                paused.session.status == SessionStatus.PAUSED
                and service.get_snapshot(session_id).session.status == SessionStatus.ACTIVE
            )
    completed = service.complete_session(session_id)
    public_export = service.export_session(session_id, scope=ExportScope.PUBLIC)
    researcher_export = service.export_session(session_id, scope=ExportScope.RESEARCHER)
    leakage = find_forbidden_public_fields(public_export)
    events = researcher_export.get("events") or []
    event_types = Counter(str(row.get("event_type")) for row in events)
    checks = {
        "session_completed": completed.session.status == SessionStatus.COMPLETED,
        "turn_budget_completed": len(submit_results) == turn_budget,
        "patient_response_count_matches": completed.public_view.progress.get("agent_response_count")
        == turn_budget,
        "idempotency_verified": idempotency_verified,
        "restart_pause_resume_verified": restart_verified,
        "review_generated": completed.public_view.review is not None,
        "review_gate_pass": bool(completed.public_view.review)
        and completed.public_view.review.gate_status == "pass",
        "public_private_separation": not leakage,
        "public_export_omits_event_trace": "events" not in public_export,
        "researcher_export_has_private_state": "private_patient_state" in researcher_export,
        "researcher_export_has_events": bool(events),
        "llm_model_recorded": bool(completed.session.model_config_id),
        "prompt_release_recorded": bool(completed.session.prompt_release_id),
    }
    result = {
        "schema_version": "theraskill_simulation_session_core_case_gate_v1",
        "status": "pass" if all(checks.values()) else "fail",
        "scenario_id": scenario_id,
        "scenario_family": family,
        "session_id": session_id,
        "turn_budget": turn_budget,
        "completed_turns": len(submit_results),
        "checks": checks,
        "public_leakage_findings": leakage,
        "event_type_distribution": dict(sorted(event_types.items())),
        "model_config_id": completed.session.model_config_id,
        "prompt_release_id": completed.session.prompt_release_id,
        "final_snapshot_id": completed.snapshot_id,
        "final_snapshot_checksum": completed.checksum,
        "public_export": public_export,
        "researcher_export_path": str((case_output_dir / "researcher_export.json").resolve()),
    }
    write_json(case_output_dir / "public_export.json", public_export, overwrite=args.overwrite)
    write_json(case_output_dir / "researcher_export.json", researcher_export, overwrite=args.overwrite)
    write_json(case_output_dir / "case_gate.json", result, overwrite=args.overwrite)
    return result


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"output directory is not empty; pass --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = load_selected_scenarios(Path(args.scenario_registry))
    database_path = output_dir / "sessions.sqlite"
    SQLiteSessionRepository(database_path)
    effective_workers = min(args.workers, len(selected))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {}
        for scenario in selected:
            family = str(scenario["scenario_family"])
            case_output_dir = output_dir / "cases" / family
            future = executor.submit(
                run_case,
                scenario,
                args=args,
                database_path=database_path,
                case_output_dir=case_output_dir,
            )
            futures[future] = family
        for future in as_completed(futures):
            family = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append(
                    {
                        "scenario_family": family,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    }
                )
    results.sort(key=lambda row: FAMILIES.index(row["scenario_family"]))
    passed = sum(row["status"] == "pass" for row in results)
    summary = {
        "schema_version": "theraskill_simulation_session_core_gate_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if passed == len(FAMILIES) and not errors else "failed",
        "environment": {
            "conda_env": "theraskill",
            "python_executable": sys.executable,
            "model_config": str(Path(args.model_config).resolve()),
        },
        "inputs": {
            "scenario_registry": str(Path(args.scenario_registry).resolve()),
            "split_key": "formal_split",
            "split_value": "test",
        },
        "concurrency": {
            "requested_workers": args.workers,
            "effective_workers": effective_workers,
            "model_port": 8002,
        },
        "counts": {
            "selected_cases": len(selected),
            "completed_cases": len(results),
            "passed_cases": passed,
            "failed_cases": len(results) - passed + len(errors),
            "completed_agent_turns": sum(row["completed_turns"] for row in results),
        },
        "scenario_family_distribution": dict(
            sorted(Counter(row["scenario_family"] for row in results).items())
        ),
        "cases": results,
        "errors": errors,
        "database_path": str(database_path),
    }
    write_json(output_dir / "session_core_gate_summary.json", summary, overwrite=args.overwrite)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
