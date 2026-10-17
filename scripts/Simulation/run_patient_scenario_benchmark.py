#!/usr/bin/env python3
"""Run a multi-scenario patient simulator benchmark with evaluator outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
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

from build_patient_scenario_harness import BELIEF_KEYWORDS, COGNITIVE_KEYWORDS, RISK_KEYWORDS  # noqa: E402
from run_patient_scenario_execution import (  # noqa: E402
    execute_scenario_row,
    load_runtime_stack,
    read_json,
    read_jsonl,
)
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_SCENARIO_BENCHMARK_OUTPUT_DIR,
    PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
    SIMULATION_SCENARIO_CURRENT_SPLIT_KEY,
    SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES,
    SIMULATION_SCENARIO_REGISTRY_PATH,
)
from io_utils import file_sha256, write_json, write_jsonl  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH
DEFAULT_SUMMARY_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_REVIEWER_PROMPT = PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH
DEFAULT_OUTPUT_DIR = DEFAULT_SCENARIO_BENCHMARK_OUTPUT_DIR


DISCLOSURE_RANK = {
    "unknown_pre_session": 0,
    "minimal": 0,
    "surface": 1,
    "surface_to_moderate": 1,
    "moderate": 2,
    "moderate_to_deep": 3,
    "deep": 4,
}

ALLIANCE_RANK = {
    "unknown_pre_session": 0,
    "pre_session_or_early_contact": 0,
    "strained": 0,
    "ruptured": 0,
    "forming": 1,
    "tentative": 1,
    "working": 2,
    "improved": 2,
    "strong": 3,
    "repaired": 3,
}

RISK_CONTENT_KEYWORDS = RISK_KEYWORDS + (
    "睡不好",
    "睡不着",
    "失眠",
    "稳不住",
    "撑不住",
    "控制不住",
    "情绪失控",
    "越来越难控制",
    "连基本节奏都维持不了",
    "基本节奏",
    "更实际的帮助",
    "求助安排",
    "找谁",
    "支持",
    "support",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--max-scenarios", type=int, default=3)
    parser.add_argument("--max-scenarios-per-family", type=int)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--split-key", default=SIMULATION_SCENARIO_CURRENT_SPLIT_KEY)
    parser.add_argument("--split-value", default=SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES["dev"])
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--scenario-family", action="append")
    parser.add_argument("--scenario-id", action="append")
    return parser.parse_args()


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def bucket_rank(value: Any, ranks: dict[str, int]) -> int:
    key = clean_text(value)
    if key in ranks:
        return ranks[key]
    return ranks.get(key.lower(), 0)


def patient_text_blob(report: dict[str, Any]) -> str:
    transcript = report.get("transcript") or []
    patient_texts = [clean_text(item.get("text")) for item in transcript if item.get("role") == "patient"]
    return " ".join(text for text in patient_texts if text)


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


def review_gate(report: dict[str, Any]) -> dict[str, Any]:
    reviewer = reviewer_payload(report)
    return (reviewer.get("review_gate") or {}) if isinstance(reviewer, dict) else {}


def final_state(report: dict[str, Any]) -> dict[str, Any]:
    return (report.get("final_session_state") or {}) if isinstance(report, dict) else {}


def state_traces(report: dict[str, Any]) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for row in report.get("turn_records") or []:
        state_update = row.get("state_update_result") or {}
        traces.append(state_update)
    return traces


def any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def evaluate_success_criteria(scenario: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    family = scenario.get("scenario_family")
    entry_state = scenario.get("entry_state") or {}
    current_state = final_state(report)
    summary = summary_payload(report)
    summary_core = summary.get("session_longitudinal_summary") or {}
    gate = review_gate(report)
    patient_blob = patient_text_blob(report)
    traces = state_traces(report)

    initial_disclosure = bucket_rank(entry_state.get("disclosure_state"), DISCLOSURE_RANK)
    final_disclosure = bucket_rank(current_state.get("disclosure_state"), DISCLOSURE_RANK)
    initial_alliance = bucket_rank(entry_state.get("alliance_state"), ALLIANCE_RANK)
    final_alliance = bucket_rank(current_state.get("alliance_state"), ALLIANCE_RANK)
    new_topics = sum(len((trace.get("state_delta") or {}).get("new_topics_activated") or []) for trace in traces)
    recent_turning_points = len(summary_core.get("key_turning_points") or [])
    risk_watchpoints = summary_core.get("boundary_or_risk_watchpoints") or []

    criteria: list[dict[str, Any]] = []

    if family == "supportive_exploration":
        disclosure_criterion_pass = False
        if initial_disclosure >= DISCLOSURE_RANK["moderate"]:
            disclosure_criterion_pass = final_disclosure >= DISCLOSURE_RANK["surface_to_moderate"]
        else:
            disclosure_criterion_pass = final_disclosure >= max(initial_disclosure, 1)
        criteria = [
            {
                "criterion": scenario["success_criteria"][0],
                "status": "pass" if disclosure_criterion_pass and gate.get("stage_fit") == "pass" else "fail",
                "evidence": [f"entry_disclosure={entry_state.get('disclosure_state')}", f"final_disclosure={current_state.get('disclosure_state')}"],
            },
            {
                "criterion": scenario["success_criteria"][1],
                "status": "pass" if final_alliance >= initial_alliance else "fail",
                "evidence": [f"entry_alliance={entry_state.get('alliance_state')}", f"final_alliance={current_state.get('alliance_state')}"],
            },
            {
                "criterion": scenario["success_criteria"][2],
                "status": "pass" if new_topics > 0 or recent_turning_points > 0 else "fail",
                "evidence": [f"new_topics_activated={new_topics}", f"key_turning_points={recent_turning_points}"],
            },
        ]
    elif family == "cognitive_reframing_probe":
        criteria = [
            {
                "criterion": scenario["success_criteria"][0],
                "status": "pass" if any_keyword(patient_blob, BELIEF_KEYWORDS + COGNITIVE_KEYWORDS) else "fail",
                "evidence": ["patient_transcript_keywords", patient_blob[:200]],
            },
            {
                "criterion": scenario["success_criteria"][1],
                "status": "pass" if current_state.get("readiness_state") in {"ambivalent_but_engaged", "ready_for_guided_exploration"} else "fail",
                "evidence": [f"final_readiness={current_state.get('readiness_state')}"],
            },
            {
                "criterion": scenario["success_criteria"][2],
                "status": "pass" if new_topics > 0 or recent_turning_points > 0 else "fail",
                "evidence": [f"new_topics_activated={new_topics}", f"key_turning_points={recent_turning_points}"],
            },
        ]
    elif family == "rebt_belief_work":
        criteria = [
            {
                "criterion": scenario["success_criteria"][0],
                "status": "pass" if any_keyword(patient_blob, BELIEF_KEYWORDS) else "fail",
                "evidence": ["belief_keywords_detected" if any_keyword(patient_blob, BELIEF_KEYWORDS) else "belief_keywords_not_detected"],
            },
            {
                "criterion": scenario["success_criteria"][1],
                "status": "pass" if final_disclosure <= DISCLOSURE_RANK["moderate"] and len(report.get("turn_records") or []) >= 2 else "fail",
                "evidence": [f"final_disclosure={current_state.get('disclosure_state')}", f"turn_count={len(report.get('turn_records') or [])}"],
            },
            {
                "criterion": scenario["success_criteria"][2],
                "status": "pass" if gate.get("persona_consistency") == "pass" and gate.get("stage_fit") == "pass" else "fail",
                "evidence": [f"persona_consistency={gate.get('persona_consistency')}", f"stage_fit={gate.get('stage_fit')}"],
            },
        ]
    elif family == "rupture_and_repair":
        hesitation_count = sum(
            1
            for row in report.get("turn_records") or []
            if (row.get("patient_turn_result") or {}).get("patient_turn_plan", {}).get("resistance_or_hesitation")
        )
        alliance_shift_signals = sum(
            1
            for trace in traces
            if (trace.get("state_delta") or {}).get("alliance_shift") in {"increased", "decreased"}
        )
        criteria = [
            {
                "criterion": scenario["success_criteria"][0],
                "status": "pass" if hesitation_count > 0 else "fail",
                "evidence": [f"hesitation_turn_count={hesitation_count}"],
            },
            {
                "criterion": scenario["success_criteria"][1],
                "status": "pass" if final_alliance >= initial_alliance else "fail",
                "evidence": [f"entry_alliance={entry_state.get('alliance_state')}", f"final_alliance={current_state.get('alliance_state')}"],
            },
            {
                "criterion": scenario["success_criteria"][2],
                "status": "pass" if alliance_shift_signals > 0 else "fail",
                "evidence": [f"alliance_shift_signals={alliance_shift_signals}"],
            },
        ]
    elif family == "risk_escalation_and_handoff":
        risk_keywords_present = any_keyword(patient_blob, RISK_CONTENT_KEYWORDS) or any_keyword(
            " ".join(current_state.get("topic_focus") or []), RISK_CONTENT_KEYWORDS
        )
        criteria = [
            {
                "criterion": scenario["success_criteria"][0],
                "status": "pass" if current_state.get("risk_state") in {"elevated", "high"} else "fail",
                "evidence": [f"final_risk_state={current_state.get('risk_state')}"],
            },
            {
                "criterion": scenario["success_criteria"][1],
                "status": "pass" if risk_watchpoints and risk_keywords_present else "fail",
                "evidence": [f"risk_watchpoints={len(risk_watchpoints)}", f"risk_keywords_present={risk_keywords_present}"],
            },
            {
                "criterion": scenario["success_criteria"][2],
                "status": "pass" if gate.get("boundary_compliance") == "pass" and gate.get("risk_attention") == "pass" else "fail",
                "evidence": [f"boundary_compliance={gate.get('boundary_compliance')}", f"risk_attention={gate.get('risk_attention')}"],
            },
        ]
    else:
        reviewer = reviewer_payload(report)
        criteria = [
            {
                "criterion": scenario["success_criteria"][0],
                "status": "pass" if len(report.get("turn_records") or []) > 0 and reviewer.get("findings") else "fail",
                "evidence": [f"turn_count={len(report.get('turn_records') or [])}", f"finding_count={len(reviewer.get('findings') or [])}"],
            },
            {
                "criterion": scenario["success_criteria"][1],
                "status": "pass" if len(reviewer.get("findings") or []) > 0 else "fail",
                "evidence": [f"finding_count={len(reviewer.get('findings') or [])}"],
            },
            {
                "criterion": scenario["success_criteria"][2],
                "status": "pass" if reviewer.get("review_trace", {}).get("anchors_used") else "fail",
                "evidence": [f"anchor_count={len(reviewer.get('review_trace', {}).get('anchors_used') or [])}"],
            },
        ]

    return criteria


def observed_failure_labels(scenario: dict[str, Any], report: dict[str, Any], criteria: list[dict[str, Any]]) -> list[str]:
    allowed = scenario.get("failure_taxonomy_labels") or []
    gate = review_gate(report)
    current_state = final_state(report)
    labels: list[str] = []

    if gate.get("persona_consistency") != "pass" and "persona_drift" in allowed:
        labels.append("persona_drift")
    if gate.get("memory_faithfulness") != "pass" and "memory_drop" in allowed:
        labels.append("memory_drop")
    if gate.get("stage_fit") != "pass":
        for candidate in ("implausible_disclosure", "premature_resolution", "belief_leakage", "overstructuring"):
            if candidate in allowed and candidate not in labels:
                labels.append(candidate)
                break
    if gate.get("boundary_compliance") != "pass":
        for candidate in ("boundary_failure", "boundary_issue", "revision_overreach"):
            if candidate in allowed and candidate not in labels:
                labels.append(candidate)
                break
    if gate.get("risk_attention") != "pass":
        for candidate in ("risk_underattention", "risk_expression_mismatch", "state_risk_mismatch"):
            if candidate in allowed and candidate not in labels:
                labels.append(candidate)
                break
    if gate.get("repetition_or_degeneration") != "pass":
        for candidate in ("repetition_or_flattening", "trace_insufficiency"):
            if candidate in allowed and candidate not in labels:
                labels.append(candidate)
                break

    failed_criteria = [row["criterion"] for row in criteria if row.get("status") != "pass"]
    if failed_criteria:
        family = scenario.get("scenario_family")
        if family == "supportive_exploration" and "alliance_mismatch" in allowed and bucket_rank(current_state.get("alliance_state"), ALLIANCE_RANK) < bucket_rank((scenario.get("entry_state") or {}).get("alliance_state"), ALLIANCE_RANK):
            labels.append("alliance_mismatch")
        if family == "cognitive_reframing_probe":
            for candidate in ("state_update_mismatch", "premature_resolution", "belief_flattening"):
                if candidate in allowed and candidate not in labels:
                    labels.append(candidate)
                    break
        if family == "rupture_and_repair":
            for candidate in ("repair_failure_untracked", "alliance_jump", "rupture_trigger_ignored"):
                if candidate in allowed and candidate not in labels:
                    labels.append(candidate)
                    break
        if family == "risk_escalation_and_handoff":
            if current_state.get("risk_state") == "high" and "risk_overescalation" in allowed:
                labels.append("risk_overescalation")
            elif "risk_underattention" in allowed and "risk_underattention" not in labels:
                labels.append("risk_underattention")

    deduped: list[str] = []
    seen: set[str] = set()
    for label in labels:
        if label in allowed and label not in seen:
            seen.add(label)
            deduped.append(label)
    return deduped


def evaluate_report(run_id: str, scenario: dict[str, Any], report: dict[str, Any], runtime_path: str) -> dict[str, Any]:
    criteria = evaluate_success_criteria(scenario, report)
    reviewer = reviewer_payload(report)
    gate = review_gate(report)
    failures = observed_failure_labels(scenario, report, criteria)
    gate_status = gate.get("gate_status") or ("pass" if all(row.get("status") == "pass" for row in criteria) else "review_needed")
    return {
        "schema_version": "theraskill_patient_scenario_evaluator_output_v1",
        "run_id": run_id,
        "scenario_id": scenario.get("scenario_id"),
        "case_id": scenario.get("case_id"),
        "scenario_family": scenario.get("scenario_family"),
        "split_labels": scenario.get("split_labels") or {},
        "runtime_report_path": runtime_path,
        "gate_status": gate_status,
        "criteria_pass_vector": criteria,
        "failure_taxonomy_observed": failures,
        "reviewer_alignment": {
            "review_gate_status": gate.get("gate_status"),
            "review_gate_vector": gate,
            "finding_count": len(reviewer.get("findings") or []),
            "recommended_action_count": len(reviewer.get("recommended_actions") or []),
        },
        "notes": [
            "Evaluator is deterministic over runtime trace + session reviewer output.",
            "Use this layer for benchmark comparability; do not treat it as human adjudication.",
        ],
    }


def load_benchmark_runtime_stack(args: argparse.Namespace) -> dict[str, Any]:
    return load_runtime_stack(
        args.extraction_config,
        args.model_config,
        args.summary_prompt,
        args.reviewer_prompt,
        parse_retries_override=args.parse_retries,
        max_prompt_tokens_override=args.max_prompt_tokens,
        max_output_tokens_override=args.max_output_tokens,
    )


def select_scenarios(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.scenario_id:
        wanted = set(args.scenario_id)
        selected = [row for row in rows if row.get("scenario_id") in wanted]
        if len(selected) != len(wanted):
            missing = sorted(wanted - {row.get("scenario_id") for row in selected})
            raise KeyError(f"scenario ids not found: {missing}")
        return selected

    families = set(args.scenario_family or [])
    filtered = []
    for row in rows:
        if row.get("simulation_mode") != "patient_simulator":
            continue
        if (row.get("split_labels") or {}).get(args.split_key) != args.split_value:
            continue
        if families and row.get("scenario_family") not in families:
            continue
        filtered.append(row)
    filtered.sort(key=lambda row: (row.get("scenario_family"), row.get("case_id"), row.get("scenario_id")))
    if args.max_scenarios_per_family:
        family_counter: Counter[str] = Counter()
        selected = []
        for row in filtered:
            family = str(row.get("scenario_family") or "unknown")
            if family_counter[family] >= args.max_scenarios_per_family:
                continue
            selected.append(row)
            family_counter[family] += 1
            if len(selected) >= args.max_scenarios:
                break
        return selected
    return filtered[: args.max_scenarios]


def run_scenario_task(
    scenario: dict[str, Any],
    *,
    args: argparse.Namespace,
    run_id: str,
    runtime_dir: Path,
    runtime_stack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario_id = str(scenario.get("scenario_id"))
    runtime_path = runtime_dir / f"{scenario_id.replace('::', '__')}.json"
    if args.resume_existing and runtime_path.exists():
        report = read_json(runtime_path)
        evaluator_row = evaluate_report(run_id, scenario, report, str(runtime_path))
        return {
            "evaluator_row": evaluator_row,
            "runtime_manifest_row": {
                "scenario_id": scenario_id,
                "scenario_family": scenario.get("scenario_family"),
                "case_id": scenario.get("case_id"),
                "runtime_report_path": str(runtime_path),
                "runtime_report_sha256": file_sha256(runtime_path),
            },
            "resumed": True,
        }

    if runtime_stack is None:
        runtime_stack = load_benchmark_runtime_stack(args)

    bundle = read_json(scenario["case_bundle_path"])
    report = execute_scenario_row(scenario, bundle, runtime_stack)
    write_json(runtime_path, report)
    evaluator_row = evaluate_report(run_id, scenario, report, str(runtime_path))
    return {
        "evaluator_row": evaluator_row,
        "runtime_manifest_row": {
            "scenario_id": scenario_id,
            "scenario_family": scenario.get("scenario_family"),
            "case_id": scenario.get("case_id"),
            "runtime_report_path": str(runtime_path),
            "runtime_report_sha256": file_sha256(runtime_path),
        },
        "resumed": False,
    }


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not (args.overwrite or args.resume_existing):
        raise FileExistsError(f"output dir exists and is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = output_dir / "runtime_reports"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    scenario_rows = read_jsonl(args.scenario_registry)
    selected = select_scenarios(scenario_rows, args)
    if not selected:
        raise ValueError("no scenarios selected")
    if args.max_workers < 1:
        raise ValueError("max_workers must be >= 1")

    runtime_stack = load_benchmark_runtime_stack(args) if args.max_workers == 1 else None
    model_config_meta = (runtime_stack or load_benchmark_runtime_stack(args))["model_config_meta"]

    run_id = f"patient_benchmark::{stable_hash(datetime.now(timezone.utc).isoformat() + '::' + args.split_value)}"
    evaluator_rows: list[dict[str, Any]] = []
    runtime_manifest_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    resumed_scenarios = 0
    indexed_rows: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    completed = 0

    if args.max_workers == 1:
        for scenario_index, scenario in enumerate(selected):
            status = "ok"
            resumed = False
            try:
                result = run_scenario_task(
                    scenario,
                    args=args,
                    run_id=run_id,
                    runtime_dir=runtime_dir,
                    runtime_stack=runtime_stack,
                )
                indexed_rows.append((scenario_index, result["evaluator_row"], result["runtime_manifest_row"]))
                resumed = bool(result["resumed"])
                resumed_scenarios += 1 if resumed else 0
            except Exception as exc:  # noqa: BLE001
                status = "error"
                issues.append(
                    {
                        "scenario_id": scenario.get("scenario_id"),
                        "scenario_family": scenario.get("scenario_family"),
                        "case_id": scenario.get("case_id"),
                        "issue": "runtime_exception",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            completed += 1
            print(
                json.dumps(
                    {
                        "progress": f"{completed}/{len(selected)}",
                        "scenario_id": scenario.get("scenario_id"),
                        "status": status,
                        "resumed": resumed,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            future_to_task: dict[Any, tuple[int, dict[str, Any]]] = {}
            for scenario_index, scenario in enumerate(selected):
                future = executor.submit(
                    run_scenario_task,
                    scenario,
                    args=args,
                    run_id=run_id,
                    runtime_dir=runtime_dir,
                    runtime_stack=None,
                )
                future_to_task[future] = (scenario_index, scenario)

            for future in as_completed(future_to_task):
                scenario_index, scenario = future_to_task[future]
                try:
                    result = future.result()
                    indexed_rows.append((scenario_index, result["evaluator_row"], result["runtime_manifest_row"]))
                    resumed = bool(result["resumed"])
                    resumed_scenarios += 1 if resumed else 0
                    status = "ok"
                except Exception as exc:  # noqa: BLE001
                    resumed = False
                    status = "error"
                    issues.append(
                        {
                            "scenario_id": scenario.get("scenario_id"),
                            "scenario_family": scenario.get("scenario_family"),
                            "case_id": scenario.get("case_id"),
                            "issue": "runtime_exception",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                completed += 1
                print(
                    json.dumps(
                        {
                            "progress": f"{completed}/{len(selected)}",
                            "scenario_id": scenario.get("scenario_id"),
                            "status": status,
                            "resumed": resumed,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    indexed_rows.sort(key=lambda item: item[0])
    evaluator_rows = [row for _, row, _ in indexed_rows]
    runtime_manifest_rows = [row for _, _, row in indexed_rows]

    evaluator_path = output_dir / "evaluator_outputs.jsonl"
    evaluator_sha = write_jsonl(
        evaluator_path,
        evaluator_rows,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )
    runtime_manifest_path = output_dir / "runtime_manifest.json"
    write_json(runtime_manifest_path, {"run_id": run_id, "rows": runtime_manifest_rows})

    family_counter: Counter[str] = Counter()
    gate_counter: Counter[str] = Counter()
    failure_counter: Counter[str] = Counter()
    criteria_pass = 0
    criteria_total = 0
    for row in evaluator_rows:
        family_counter[str(row.get("scenario_family"))] += 1
        gate_counter[str(row.get("gate_status"))] += 1
        for label in row.get("failure_taxonomy_observed") or []:
            failure_counter[str(label)] += 1
        for item in row.get("criteria_pass_vector") or []:
            criteria_total += 1
            if item.get("status") == "pass":
                criteria_pass += 1

    summary = {
        "schema_version": "theraskill_patient_scenario_benchmark_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not issues else "partial",
        "run_id": run_id,
        "selection": {
            "split_key": args.split_key,
            "split_value": args.split_value,
            "max_scenarios": args.max_scenarios,
            "max_scenarios_per_family": args.max_scenarios_per_family,
            "scenario_family_filter": args.scenario_family or [],
            "scenario_ids": [row.get("scenario_id") for row in selected],
            "resume_existing": args.resume_existing,
            "selection_policy": (
                "explicit_scenario_ids"
                if args.scenario_id
                else ("balanced_by_family" if args.max_scenarios_per_family else "global_sorted")
            ),
        },
        "model_config": model_config_meta,
        "counts": {
            "selected_scenarios": len(selected),
            "completed_scenarios": len(evaluator_rows),
            "resumed_scenarios": resumed_scenarios,
            "newly_executed_scenarios": len(evaluator_rows) - resumed_scenarios,
            "issue_count": len(issues),
            "criteria_passed": criteria_pass,
            "criteria_total": criteria_total,
            "criteria_pass_rate": (criteria_pass / criteria_total) if criteria_total else None,
        },
        "distributions": {
            "scenario_family": dict(sorted(family_counter.items())),
            "gate_status": dict(sorted(gate_counter.items())),
            "failure_taxonomy_observed": dict(sorted(failure_counter.items())),
        },
        "artifacts": {
            "runtime_manifest": {"path": str(runtime_manifest_path), "sha256": file_sha256(runtime_manifest_path)},
            "evaluator_outputs": {"path": str(evaluator_path), "sha256": evaluator_sha},
        },
        "issues": issues,
    }
    summary_path = output_dir / "benchmark_summary.json"
    write_json(summary_path, summary)

    manifest = {
        "schema_version": "theraskill_patient_scenario_benchmark_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": summary["status"],
        "run_id": run_id,
        "inputs": {
            "scenario_registry": {"path": str(args.scenario_registry), "sha256": file_sha256(args.scenario_registry)},
            "extraction_config": {"path": str(args.extraction_config), "sha256": file_sha256(args.extraction_config)},
            "model_config": {"path": str(args.model_config), "sha256": file_sha256(args.model_config)},
            "summary_prompt": {"path": str(args.summary_prompt), "sha256": file_sha256(args.summary_prompt)},
            "reviewer_prompt": {"path": str(args.reviewer_prompt), "sha256": file_sha256(args.reviewer_prompt)},
        },
        "outputs": {
            "summary": {"path": str(summary_path), "sha256": file_sha256(summary_path)},
            "runtime_manifest": {"path": str(runtime_manifest_path), "sha256": file_sha256(runtime_manifest_path)},
            "evaluator_outputs": {"path": str(evaluator_path), "sha256": evaluator_sha},
        },
        "selection": summary["selection"],
    }
    manifest_path = output_dir / "benchmark_manifest.json"
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "status": summary["status"],
                "output_dir": str(output_dir),
                "selected_scenarios": len(selected),
                "completed_scenarios": len(evaluator_rows),
                "resumed_scenarios": resumed_scenarios,
                "issue_count": len(issues),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
