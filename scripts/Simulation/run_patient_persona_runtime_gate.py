#!/usr/bin/env python3
"""Run a five-family, seven-trajectory live runtime gate for patient persona v2."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts/_common", PROJECT_ROOT / "scripts/Simulation"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import file_sha256, write_json, write_jsonl  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    SIMULATION_PERSONA_V2_DIR,
    SIMULATION_SCENARIO_REGISTRY_PATH,
)
from theraskill_simulation.contracts import ActorRole  # noqa: E402
from theraskill_simulation.engines import DeferredLLMPatientEngine  # noqa: E402
from theraskill_simulation.repository import SQLiteSessionRepository  # noqa: E402
from theraskill_simulation.scenarios import ScenarioRegistry  # noqa: E402
from theraskill_simulation.service import SimulationSessionService  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "outputs/simulation/benchmark/patient_persona_v2_runtime_gate"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
FAMILIES = (
    "supportive_exploration",
    "cognitive_reframing_probe",
    "rupture_and_repair",
    "risk_escalation_and_handoff",
    "rebt_belief_work",
)
TRAJECTORIES = {
    "neutral": "你可以再多说一点最近发生的情况吗？",
    "empathic_exploration": "听起来这件事让你承受了不少压力。我想按你的节奏了解，最近哪一刻最让你难受？",
    "premature_advice": "你先别想那么多，按我说的列个计划每天执行，问题自然就会解决。",
    "repeated_probing": "到底为什么会这样？真正原因是什么？你现在能不能把最深的想法一次说清楚？",
    "inaccurate_reflection": "所以你其实并不在意这件事，只是习惯用担心来吸引别人的注意，对吗？",
    "rupture_and_repair": "我刚才可能定义得太快，让你觉得没有被听懂。我想停下来确认：我哪里理解偏了？",
    "risk_relevant_questioning": "我想认真确认安全情况：最近有没有出现让你担心自己或他人安全的具体念头、冲动或行为？",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-registry", default=str(SIMULATION_SCENARIO_REGISTRY_PATH))
    parser.add_argument("--persona-dir", default=str(SIMULATION_PERSONA_V2_DIR))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def select_scenarios(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for family in FAMILIES:
        candidates = [
            row for row in rows
            if row.get("scenario_family") == family
            and Path(str(row.get("case_bundle_path") or "")).exists()
        ]
        if not candidates:
            raise ValueError(f"missing scenario family: {family}")
        selected.append(candidates[0])
    return selected


def run_arm(
    scenario: dict[str, Any],
    trajectory: str,
    utterance: str,
    output_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    arm_id = f"{scenario['scenario_family']}__{trajectory}"
    database = output_dir / "work" / f"{arm_id}.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()
    engine = DeferredLLMPatientEngine(
        project_root=PROJECT_ROOT,
        extraction_config=args.extraction_config,
        model_config=args.model_config,
        parse_retries=1,
        max_prompt_tokens=12000,
        max_output_tokens=1800,
    )
    service = SimulationSessionService(
        repository=SQLiteSessionRepository(database),
        scenario_registry=ScenarioRegistry(args.scenario_registry),
        patient_engine=engine,
        persona_dir=args.persona_dir,
        knowledge_release_id="patient_persona_v2",
    )
    session_id = f"persona_gate__{arm_id}"
    created = service.create_session(scenario_id=scenario["scenario_id"], session_id=session_id)
    before_count = len(created.private_patient_state.disclosed_fact_ids if created.private_patient_state else [])
    response = service.submit_turn(
        session_id,
        text=utterance,
        actor=ActorRole.HUMAN_THERAPIST,
        idempotency_key=f"{arm_id}__turn1",
    )
    foreground = service.get_snapshot(session_id)
    trace = foreground.session.metadata.get("latest_runtime_trace") or {}
    response_contract = trace.get("response_contract") or {}
    compiler_trace = trace.get("persona_compiler") or {}
    allowed = set(compiler_trace.get("allowed_fact_ids") or [])
    used = set(response_contract.get("used_fact_ids") or [])
    active_principles = set(compiler_trace.get("active_principle_ids") or [])
    applied_principles = set(response_contract.get("applied_principle_ids") or [])
    if response.get("deferred_update_id"):
        service.finalize_patient_turn(
            session_id, deferred_update_id=str(response["deferred_update_id"])
        )
    final = service.get_snapshot(session_id)
    patient_text = str(response["agent_turn"]["text"])
    after_count = len(final.private_patient_state.disclosed_fact_ids if final.private_patient_state else [])
    checks = {
        "patient_text_nonempty": bool(patient_text.strip()),
        "foreground_committed_before_writeback": bool(response.get("state_update_pending")),
        "deferred_writeback_complete": not bool(final.public_view.progress.get("state_update_pending")),
        "used_fact_ids_subset_allowed": used.issubset(allowed),
        "applied_principle_ids_subset_active": applied_principles.issubset(active_principles),
        "response_contract_flags_zero": not (response_contract.get("uncertainty_or_hallucination_flags") or []),
        "disclosed_fact_count_monotonic": after_count >= before_count,
        "patient_role_language": not any(
            token in patient_text for token in ("作为治疗师", "我的来访者", "我建议你", "治疗方案如下")
        ),
    }
    return {
        "schema_version": "theraskill_patient_persona_v2_runtime_gate_arm_v1",
        "arm_id": arm_id,
        "scenario_id": scenario["scenario_id"],
        "case_id": scenario.get("case_id"),
        "scenario_family": scenario["scenario_family"],
        "trajectory": trajectory,
        "therapist_utterance": utterance,
        "patient_utterance": patient_text,
        "before_disclosed_fact_count": before_count,
        "after_disclosed_fact_count": after_count,
        "compiler_trace": compiler_trace,
        "response_contract": response_contract,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
    }


def main() -> int:
    args = parse_args()
    if args.workers < 16:
        raise SystemExit("--workers must be at least 16 for an active model port")
    output_dir = Path(args.output_dir)
    if output_dir.exists():
        if not args.overwrite:
            raise FileExistsError(f"output exists; pass --overwrite: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    scenarios = select_scenarios(read_jsonl(Path(args.scenario_registry)))
    jobs = [
        (scenario, trajectory, utterance)
        for scenario in scenarios
        for trajectory, utterance in TRAJECTORIES.items()
    ]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_arm, scenario, trajectory, utterance, output_dir, args): (
                scenario, trajectory
            )
            for scenario, trajectory, utterance in jobs
        }
        for future in as_completed(futures):
            scenario, trajectory = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    {
                        "scenario_id": scenario["scenario_id"],
                        "scenario_family": scenario["scenario_family"],
                        "trajectory": trajectory,
                        "error_type": type(exc).__name__,
                        "details": str(exc),
                    }
                )
            finished = len(results) + len(failures)
            if finished % 5 == 0 or finished == len(jobs):
                print(json.dumps({"event": "progress", "finished": finished, "total": len(jobs), "failures": len(failures)}), flush=True)

    results.sort(key=lambda row: row["arm_id"])
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in results:
        grouped[row["scenario_family"]][row["trajectory"]] = row
    counterfactual_issues = []
    for family, arms in grouped.items():
        empathic = arms.get("empathic_exploration")
        inaccurate = arms.get("inaccurate_reflection")
        if empathic and inaccurate and inaccurate["after_disclosed_fact_count"] > empathic["after_disclosed_fact_count"]:
            counterfactual_issues.append({"family": family, "issue": "inaccurate_reflection_more_disclosive_than_empathic"})
        checksums = {
            (row.get("compiler_trace") or {}).get("immutable_identity_checksum")
            for row in arms.values()
        }
        if len(checksums) != 1:
            counterfactual_issues.append({"family": family, "issue": "identity_checksum_drift"})

    write_jsonl(output_dir / "arm_results.jsonl", results, overwrite=True, sort_keys=True)
    write_jsonl(output_dir / "failures.jsonl", failures, overwrite=True, sort_keys=True)
    status_counts = Counter(row["status"] for row in results)
    status = "complete" if len(results) == len(jobs) and not failures and status_counts.get("fail", 0) == 0 and not counterfactual_issues else "failed"
    report = {
        "schema_version": "theraskill_patient_persona_v2_runtime_gate_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "scenario_family_count": len(grouped),
        "trajectory_count": len(TRAJECTORIES),
        "expected_arm_count": len(jobs),
        "completed_arm_count": len(results),
        "arm_status_distribution": dict(status_counts),
        "workers": args.workers,
        "model_config": {"path": args.model_config, "sha256": file_sha256(args.model_config)},
        "persona_manifest": {
            "path": str(PROJECT_ROOT / "outputs/simulation/personas/patient_persona_v2.manifest.json"),
            "sha256": file_sha256(PROJECT_ROOT / "outputs/simulation/personas/patient_persona_v2.manifest.json"),
        },
        "counterfactual_issues": counterfactual_issues,
        "failures": failures,
        "issues_count": len(failures) + status_counts.get("fail", 0) + len(counterfactual_issues),
    }
    write_json(output_dir / "runtime_gate_qa.json", report, overwrite=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
