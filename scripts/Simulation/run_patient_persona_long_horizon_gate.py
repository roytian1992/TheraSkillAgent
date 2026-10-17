#!/usr/bin/env python3
"""Run real-runtime 20-turn and explicit three-session patient continuity gates."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts/_common", PROJECT_ROOT / "scripts/Simulation"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import file_sha256, write_json, write_jsonl  # noqa: E402
from simulation_surface_registry import SIMULATION_PERSONA_V2_DIR, SIMULATION_SCENARIO_REGISTRY_PATH  # noqa: E402
from theraskill_simulation.contracts import ActorRole  # noqa: E402
from theraskill_simulation.engines import DeferredLLMPatientEngine  # noqa: E402
from theraskill_simulation.repository import SQLiteSessionRepository  # noqa: E402
from theraskill_simulation.scenarios import ScenarioRegistry  # noqa: E402
from theraskill_simulation.service import SimulationSessionService  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "outputs/simulation/benchmark/patient_persona_v2_long_horizon_gate"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
SESSION_TURNS = (20, 5, 5)
THERAPIST_TURN_BANK = (
    "我想先按你的节奏了解，最近最让你难受的一个具体时刻是什么？",
    "当时你最先注意到的感受、想法或身体反应是什么？",
    "听起来这件事对你很重要。我刚才理解的部分里，有没有哪里偏了？",
    "如果只往前多说一小步，你现在愿意让我知道哪一部分？",
    "当你想把话收回去或停住时，通常最担心接下来会发生什么？",
    "你不需要马上接受建议。我们可以先看看，什么回应会让你觉得更被理解？",
    "回到刚才那个情境，你做了什么来应对？它短期帮到了什么，又留下了什么困难？",
    "如果把事情本身和脑子里自动冒出的解释分开，两部分分别是什么？",
    "我想确认一下当前安全情况：有没有具体的安全风险需要我们现在优先处理？",
    "刚才这个问题如果太快或太重，你可以直接告诉我，我们可以调整节奏。",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-registry", default=str(SIMULATION_SCENARIO_REGISTRY_PATH))
    parser.add_argument("--persona-dir", default=str(SIMULATION_PERSONA_V2_DIR))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--chains", type=int, default=16)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def select_scenarios(rows: list[dict[str, Any]], count: int, persona_dir: Path) -> list[dict[str, Any]]:
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if not case_id or not (persona_dir / f"{case_id}.json").exists():
            continue
        families.setdefault(str(row.get("scenario_family") or "unknown"), []).append(row)
    selected: list[dict[str, Any]] = []
    family_names = sorted(families)
    cursor = 0
    while len(selected) < count and family_names:
        family = family_names[cursor % len(family_names)]
        candidates = families[family]
        index = cursor // len(family_names)
        if index < len(candidates):
            candidate = candidates[index]
            if candidate.get("case_id") not in {row.get("case_id") for row in selected}:
                selected.append(candidate)
        cursor += 1
        if cursor > count * len(family_names) * 4:
            break
    if len(selected) < count:
        raise ValueError(f"only selected {len(selected)} unique cases, expected {count}")
    return selected


def run_chain(scenario: dict[str, Any], chain_index: int, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    case_id = str(scenario["case_id"])
    chain_id = f"chain_{chain_index:02d}_{case_id}"
    database = output_dir / "work" / f"{chain_id}.sqlite"
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
    parent_session_id: str | None = None
    session_reports = []
    all_patient_utterances: list[str] = []
    identity_checksums: set[str] = set()
    disclosure_counts: list[int] = []
    contract_flags: list[str] = []
    turn_contract_checks: list[bool] = []
    patient_role_checks: list[bool] = []

    for session_number, turn_count in enumerate(SESSION_TURNS, start=1):
        session_id = f"{chain_id}_session_{session_number}"
        created = service.create_session(
            scenario_id=scenario["scenario_id"],
            session_id=session_id,
            parent_session_id=parent_session_id,
        )
        start_disclosed = len(created.private_patient_state.disclosed_fact_ids)
        turn_reports = []
        for turn_index in range(turn_count):
            utterance = THERAPIST_TURN_BANK[(turn_index + session_number + chain_index) % len(THERAPIST_TURN_BANK)]
            response = service.submit_turn(
                session_id,
                text=utterance,
                actor=ActorRole.HUMAN_THERAPIST,
                idempotency_key=f"{session_id}_turn_{turn_index + 1}",
            )
            foreground = service.get_snapshot(session_id)
            trace = foreground.session.metadata.get("latest_runtime_trace") or {}
            contract = trace.get("response_contract") or {}
            compiler = trace.get("persona_compiler") or {}
            allowed = set(compiler.get("allowed_fact_ids") or [])
            used = set(contract.get("used_fact_ids") or [])
            active_principles = set(compiler.get("active_principle_ids") or [])
            applied_principles = set(contract.get("applied_principle_ids") or [])
            identity_checksums.add(str(compiler.get("immutable_identity_checksum") or ""))
            contract_flags.extend(str(item) for item in contract.get("uncertainty_or_hallucination_flags") or [])
            patient_text = str(response["agent_turn"]["text"])
            contract_valid = used.issubset(allowed) and applied_principles.issubset(active_principles)
            patient_role_valid = bool(patient_text.strip()) and not any(
                token in patient_text for token in ("作为治疗师", "我的来访者", "我建议你", "治疗方案如下")
            )
            turn_contract_checks.append(contract_valid)
            patient_role_checks.append(patient_role_valid)
            all_patient_utterances.append(patient_text)
            if response.get("deferred_update_id"):
                service.finalize_patient_turn(
                    session_id, deferred_update_id=str(response["deferred_update_id"])
                )
            final_turn = service.get_snapshot(session_id)
            turn_reports.append(
                {
                    "turn": turn_index + 1,
                    "therapist_utterance": utterance,
                    "patient_utterance": patient_text,
                    "used_fact_ids": contract.get("used_fact_ids") or [],
                    "disclosed_fact_count": len(final_turn.private_patient_state.disclosed_fact_ids),
                    "state_update_pending": bool(final_turn.public_view.progress.get("state_update_pending")),
                    "contract_flags": contract.get("uncertainty_or_hallucination_flags") or [],
                    "used_fact_ids_subset_allowed": used.issubset(allowed),
                    "applied_principle_ids_subset_active": applied_principles.issubset(active_principles),
                    "patient_role_language": patient_role_valid,
                }
            )
        service.complete_session(session_id)
        completed = service.get_snapshot(session_id)
        end_disclosed = len(completed.private_patient_state.disclosed_fact_ids)
        disclosure_counts.append(end_disclosed)
        session_reports.append(
            {
                "session_id": session_id,
                "parent_session_id": parent_session_id,
                "turn_count": turn_count,
                "start_disclosed_fact_count": start_disclosed,
                "end_disclosed_fact_count": end_disclosed,
                "carryover_policy": completed.session.metadata.get("carryover_policy"),
                "turns": turn_reports,
            }
        )
        parent_session_id = session_id

    exact_duplicates = len(all_patient_utterances) - len(set(all_patient_utterances))
    checks = {
        "three_sessions_complete": len(session_reports) == 3,
        "first_session_20_turns": session_reports[0]["turn_count"] == 20,
        "explicit_parent_carryover_sessions_2_3": all(
            item["parent_session_id"] and item["carryover_policy"] == "explicit_same_case_terminal_parent_v1"
            for item in session_reports[1:]
        ),
        "carryover_disclosed_facts_preserved": all(
            session_reports[index]["start_disclosed_fact_count"]
            == session_reports[index - 1]["end_disclosed_fact_count"]
            for index in range(1, len(session_reports))
        ),
        "identity_checksum_stable": len(identity_checksums) == 1 and "" not in identity_checksums,
        "disclosed_fact_count_monotonic": disclosure_counts == sorted(disclosure_counts),
        "all_deferred_updates_complete": all(
            not turn["state_update_pending"] for session in session_reports for turn in session["turns"]
        ),
        "response_contract_flags_zero": not contract_flags,
        "fact_and_principle_contracts_valid_all_turns": all(turn_contract_checks),
        "patient_role_language_all_turns": all(patient_role_checks),
        "exact_response_duplicate_rate_below_20_percent": exact_duplicates / max(1, len(all_patient_utterances)) < 0.2,
    }
    return {
        "schema_version": "theraskill_patient_persona_v2_long_horizon_chain_v1",
        "chain_id": chain_id,
        "case_id": case_id,
        "scenario_id": scenario["scenario_id"],
        "scenario_family": scenario.get("scenario_family"),
        "session_turns": list(SESSION_TURNS),
        "patient_turn_count": len(all_patient_utterances),
        "identity_checksums": sorted(identity_checksums),
        "disclosure_counts": disclosure_counts,
        "exact_duplicate_count": exact_duplicates,
        "contract_flags": contract_flags,
        "sessions": session_reports,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
    }


def main() -> int:
    args = parse_args()
    if args.workers < 16:
        raise SystemExit("--workers must be at least 16 for an active model port")
    if args.chains < 16:
        raise SystemExit("--chains must be at least 16 so all configured workers receive a chain")
    output_dir = Path(args.output_dir)
    reports_dir = output_dir / "chain_reports"
    if output_dir.exists() and args.overwrite:
        shutil.rmtree(output_dir)
    if output_dir.exists() and not args.resume:
        raise FileExistsError(f"output exists; pass --overwrite or --resume: {output_dir}")
    reports_dir.mkdir(parents=True, exist_ok=True)
    scenarios = select_scenarios(
        read_jsonl(Path(args.scenario_registry)), args.chains, Path(args.persona_dir)
    )
    completed_case_ids = set()
    if args.resume:
        for path in reports_dir.glob("*.json"):
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("status") == "pass":
                completed_case_ids.add(str(report.get("case_id")))
    jobs = [
        (index, scenario)
        for index, scenario in enumerate(scenarios, start=1)
        if str(scenario.get("case_id")) not in completed_case_ids
    ]
    lock = threading.Lock()
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_chain, scenario, index, output_dir, args): (index, scenario)
            for index, scenario in jobs
        }
        for future in as_completed(futures):
            index, scenario = futures[future]
            try:
                report = future.result()
                write_json(reports_dir / f"chain_{index:02d}_{scenario['case_id']}.json", report, overwrite=True)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    failures.append(
                        {
                            "chain_index": index,
                            "case_id": scenario.get("case_id"),
                            "error_type": type(exc).__name__,
                            "details": str(exc),
                        }
                    )
            finished = len(list(reports_dir.glob("*.json"))) + len(failures)
            print(json.dumps({"event": "progress", "finished": finished, "total": args.chains, "failures": len(failures)}), flush=True)

    reports = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(reports_dir.glob("*.json"))]
    write_jsonl(output_dir / "chain_summaries.jsonl", [
        {key: row[key] for key in ("chain_id", "case_id", "scenario_id", "scenario_family", "patient_turn_count", "disclosure_counts", "checks", "status")}
        for row in reports
    ], overwrite=True, sort_keys=True)
    write_jsonl(output_dir / "failures.jsonl", failures, overwrite=True, sort_keys=True)
    status_counts = Counter(row["status"] for row in reports)
    status = "complete" if len(reports) == args.chains and not failures and status_counts.get("fail", 0) == 0 else "failed"
    qa = {
        "schema_version": "theraskill_patient_persona_v2_long_horizon_gate_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "workers": args.workers,
        "chain_count_expected": args.chains,
        "chain_count_completed": len(reports),
        "session_count_completed": sum(len(row["sessions"]) for row in reports),
        "patient_turn_count_completed": sum(row["patient_turn_count"] for row in reports),
        "status_distribution": dict(status_counts),
        "model_config": {"path": args.model_config, "sha256": file_sha256(args.model_config)},
        "persona_manifest_sha256": file_sha256(
            PROJECT_ROOT / "outputs/simulation/personas/patient_persona_v2.manifest.json"
        ),
        "failures": failures,
        "issues_count": len(failures) + status_counts.get("fail", 0),
    }
    write_json(output_dir / "long_horizon_qa.json", qa, overwrite=True)
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
