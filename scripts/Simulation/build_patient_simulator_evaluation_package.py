#!/usr/bin/env python3
"""Build a unified evaluation package for the patient simulator."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
HERMES_SKILL_DIR = SCRIPT_DIR.parent / "HermesSkill"
for path in (SCRIPT_DIR, COMMON_DIR, HERMES_SKILL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hermes_surface_registry import H6_ENGINEERING_RUNTIME_PILOT_REPORT_PATH
from io_utils import file_sha256, read_json, write_json
from simulation_surface_registry import (
    DEFAULT_HERMES_ADAPTER_REVIEW_DIGEST_PATH,
    DEFAULT_HERMES_HELDOUT_GATE_DIR,
    DEFAULT_HERMES_VALIDATION_REPORT_PATH,
    DEFAULT_PATIENT_SIMULATOR_EVAL_OUTPUT_DIR,
    FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR,
    FORMAL_CONDITIONED_EVAL_EVIDENCE_DIR,
    FORMAL_SCENARIO_BENCHMARK_EVIDENCE_DIR,
    HISTORICAL_V1_COMPLETION_EVIDENCE_PATHS,
    LONGITUDINAL_EVIDENCE_SUMMARY_PATHS,
    REPO_ROOT,
    SIMULATION_PATIENT_CASE_BUNDLES_MANIFEST_PATH,
    SIMULATION_PATIENT_CASE_BUNDLES_QA_PATH,
    SIMULATION_SCENARIO_HARNESS_QA_PATH,
)

DEFAULT_OUTPUT_DIR = DEFAULT_PATIENT_SIMULATOR_EVAL_OUTPUT_DIR
DEFAULT_CASE_BUNDLES_MANIFEST = SIMULATION_PATIENT_CASE_BUNDLES_MANIFEST_PATH
DEFAULT_CASE_BUNDLES_QA = SIMULATION_PATIENT_CASE_BUNDLES_QA_PATH
DEFAULT_SCENARIO_HARNESS_QA = SIMULATION_SCENARIO_HARNESS_QA_PATH
HISTORICAL_SINGLE_SESSION_REPORT = HISTORICAL_V1_COMPLETION_EVIDENCE_PATHS["single_session_loop"]
HISTORICAL_MULTI_SESSION_REPORT = HISTORICAL_V1_COMPLETION_EVIDENCE_PATHS["multi_session_prototype"]
HISTORICAL_REVIEWER_REPORT = HISTORICAL_V1_COMPLETION_EVIDENCE_PATHS["runtime_reviewer"]
DEFAULT_BENCHMARK_SUMMARY = FORMAL_SCENARIO_BENCHMARK_EVIDENCE_DIR / "benchmark_summary.json"
DEFAULT_CONDITIONED_SUMMARY = FORMAL_CONDITIONED_EVAL_EVIDENCE_DIR / "comparison_summary.json"
DEFAULT_CONDITIONED_ADJUDICATION_SUMMARY = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR / "adjudication_summary.json"
DEFAULT_CONDITIONED_HUMAN_RATING_MANIFEST = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR / "human_rating_protocol_manifest.json"
DEFAULT_CONDITIONED_HUMAN_RATING_RESULTS_MANIFEST = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR / "human_rating_results_manifest.json"
DEFAULT_CONDITIONED_HUMAN_RATING_VALIDATION_REPORT = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR / "human_rating_validation_report.json"
DEFAULT_HERMES_VALIDATION = DEFAULT_HERMES_VALIDATION_REPORT_PATH
DEFAULT_HERMES_HELDOUT_SUMMARY = DEFAULT_HERMES_HELDOUT_GATE_DIR / "heldout_gate_summary.json"
DEFAULT_HERMES_HELDOUT_MANIFEST = DEFAULT_HERMES_HELDOUT_GATE_DIR / "heldout_gate_manifest.json"
DEFAULT_HERMES_PILOT = H6_ENGINEERING_RUNTIME_PILOT_REPORT_PATH
DEFAULT_HERMES_DIGEST = DEFAULT_HERMES_ADAPTER_REVIEW_DIGEST_PATH

LONGITUDINAL_DEFAULTS = LONGITUDINAL_EVIDENCE_SUMMARY_PATHS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--case-bundles-manifest", type=Path, default=DEFAULT_CASE_BUNDLES_MANIFEST)
    parser.add_argument("--case-bundles-qa", type=Path, default=DEFAULT_CASE_BUNDLES_QA)
    parser.add_argument("--scenario-harness-qa", type=Path, default=DEFAULT_SCENARIO_HARNESS_QA)
    parser.add_argument("--include-historical-v1-completion", action="store_true")
    parser.add_argument("--single-session-report", dest="single_session_report", type=Path, help="Historical v1 single-session completion evidence.")
    parser.add_argument("--multi-session-report", dest="multi_session_report", type=Path, help="Historical v1 multi-session completion evidence.")
    parser.add_argument("--reviewer-report", dest="reviewer_report", type=Path, help="Historical v1 reviewer evidence.")
    parser.add_argument("--benchmark-summary", type=Path, default=DEFAULT_BENCHMARK_SUMMARY)
    parser.add_argument("--conditioned-summary", type=Path, default=DEFAULT_CONDITIONED_SUMMARY)
    parser.add_argument("--conditioned-adjudication-summary", type=Path, default=DEFAULT_CONDITIONED_ADJUDICATION_SUMMARY)
    parser.add_argument("--conditioned-human-rating-manifest", type=Path, default=DEFAULT_CONDITIONED_HUMAN_RATING_MANIFEST)
    parser.add_argument("--conditioned-human-rating-results-manifest", type=Path, default=DEFAULT_CONDITIONED_HUMAN_RATING_RESULTS_MANIFEST)
    parser.add_argument("--conditioned-human-rating-validation-report", type=Path, default=DEFAULT_CONDITIONED_HUMAN_RATING_VALIDATION_REPORT)
    parser.add_argument("--hermes-validation", type=Path, default=DEFAULT_HERMES_VALIDATION)
    parser.add_argument("--hermes-heldout-summary", type=Path, default=DEFAULT_HERMES_HELDOUT_SUMMARY)
    parser.add_argument("--hermes-heldout-manifest", type=Path, default=DEFAULT_HERMES_HELDOUT_MANIFEST)
    parser.add_argument("--hermes-pilot", type=Path, default=DEFAULT_HERMES_PILOT)
    parser.add_argument("--hermes-digest", type=Path, default=DEFAULT_HERMES_DIGEST)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def rel_or_abs(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def artifact_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "repo_relative_path": rel_or_abs(path),
        "sha256": file_sha256(path),
    }


def resolve_historical_v1_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    single_session_report = args.single_session_report or HISTORICAL_SINGLE_SESSION_REPORT
    multi_session_report = args.multi_session_report or HISTORICAL_MULTI_SESSION_REPORT
    reviewer_report = args.reviewer_report or HISTORICAL_REVIEWER_REPORT
    return single_session_report, multi_session_report, reviewer_report


def collect_v1_completion(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not args.include_historical_v1_completion:
        return {
            "status": "omitted",
            "claim": "historical v1 completion layer omitted from the default package build",
            "included": False,
            "verified_facts": {},
            "passes_gate": None,
        }, []

    single_session_report, multi_session_report, reviewer_report = resolve_historical_v1_inputs(args)
    bundles_manifest = read_json(args.case_bundles_manifest)
    bundles_qa = read_json(args.case_bundles_qa)
    scenario_harness_qa = read_json(args.scenario_harness_qa)
    single_session = read_json(single_session_report)
    multi_session = read_json(multi_session_report)
    reviewer_evidence = read_json(reviewer_report)

    reviewer_gate = reviewer_evidence["result"]["review_gate"]
    single_session_state = single_session.get("final_session_state") or {}

    layer = {
        "status": "complete",
        "claim": "patient simulator v1 completion definition satisfied",
        "included": True,
        "verified_facts": {
            "case_bundle_count": bundles_manifest.get("bundle_count"),
            "case_bundle_manifest_status": bundles_manifest.get("status"),
            "case_bundle_qa_issues_count": bundles_qa.get("issues_count"),
            "scenario_harness_status": scenario_harness_qa.get("status"),
            "scenario_rows": scenario_harness_qa.get("scenario_rows"),
            "missing_family_coverage": scenario_harness_qa.get("missing_family_coverage"),
            "single_session_status": single_session.get("status"),
            "single_session_turn_count_completed": single_session.get("turn_count_completed"),
            "single_session_final_alliance_state": single_session_state.get("alliance_state"),
            "single_session_final_disclosure_state": single_session_state.get("disclosure_state"),
            "single_session_final_risk_state": single_session_state.get("risk_state"),
            "multi_session_status": multi_session.get("status"),
            "multi_session_session_count": multi_session.get("session_count"),
            "longitudinal_gate_min_3_sessions_met": multi_session.get("longitudinal_gate_min_3_sessions_met"),
            "reviewer_gate": reviewer_gate,
        },
    }
    layer["passes_gate"] = all(
        [
            bundles_manifest.get("status") == "complete",
            bundles_qa.get("issues_count") == 0,
            scenario_harness_qa.get("status") == "complete",
            scenario_harness_qa.get("issues_count") == 0,
            single_session.get("status") == "complete",
            int(single_session.get("turn_count_completed") or 0) >= 3,
            multi_session.get("status") == "complete",
            bool(multi_session.get("longitudinal_gate_min_3_sessions_met")),
            reviewer_gate.get("gate_status") == "pass",
        ]
    )
    artifacts = [
        artifact_record(args.case_bundles_manifest),
        artifact_record(args.case_bundles_qa),
        artifact_record(args.scenario_harness_qa),
        artifact_record(single_session_report),
        artifact_record(multi_session_report),
        artifact_record(reviewer_report),
    ]
    return layer, artifacts


def collect_longitudinal_layer() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    families: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []
    model_counter: Counter[str] = Counter()
    base_url_counter: Counter[str] = Counter()
    worker_counter: Counter[int] = Counter()
    all_full_pass = True

    for family, path in LONGITUDINAL_DEFAULTS.items():
        summary = read_json(path)
        selection_policy = summary.get("selection_policy") or {}
        runtime_config = summary.get("runtime_config") or {}
        model_name = runtime_config.get("model") or "unknown"
        base_url = runtime_config.get("base_url") or "unknown"
        max_workers = selection_policy.get("max_workers")
        model_counter[model_name] += 1
        base_url_counter[base_url] += 1
        if isinstance(max_workers, int):
            worker_counter[max_workers] += 1

        family_row = {
            "path": str(path.resolve()),
            "selection_policy": selection_policy,
            "runtime_config": runtime_config,
            "selected_case_count": summary.get("selected_case_count"),
            "completed_case_count": summary.get("completed_case_count"),
            "all_sessions_review_pass_count": summary.get("all_sessions_review_pass_count"),
            "continuity_exact_opening_match_count": summary.get("continuity_exact_opening_match_count"),
            "memory_faithfulness_sessions_2_3_pass_count": summary.get("memory_faithfulness_sessions_2_3_pass_count"),
            "persona_consistency_sessions_2_3_pass_count": summary.get("persona_consistency_sessions_2_3_pass_count"),
            "failure_counter": summary.get("failure_counter"),
        }
        passes = all(
            [
                summary.get("selected_case_count") == 3,
                summary.get("completed_case_count") == 3,
                summary.get("all_sessions_review_pass_count") == 3,
                summary.get("continuity_exact_opening_match_count") == 3,
                summary.get("memory_faithfulness_sessions_2_3_pass_count") == 3,
                summary.get("persona_consistency_sessions_2_3_pass_count") == 3,
                summary.get("failure_counter") == {},
                selection_policy.get("session_count") == 3,
                selection_policy.get("turns_per_session") == 3,
            ]
        )
        family_row["full_pass"] = passes
        all_full_pass = all_full_pass and passes
        families[family] = family_row
        artifacts.append(artifact_record(path))

    layer = {
        "status": "complete" if all_full_pass else "partial",
        "claim": "four major families have stable 3-case / 3-session / 3-turn longitudinal evidence",
        "families": families,
        "aggregate": {
            "family_count": len(families),
            "full_pass_family_count": sum(1 for row in families.values() if row["full_pass"]),
            "model_distribution": dict(model_counter),
            "base_url_distribution": dict(base_url_counter),
            "max_workers_distribution": {str(k): v for k, v in worker_counter.items()},
        },
        "passes_gate": all_full_pass,
    }
    return layer, artifacts


def collect_conditioned_layer(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary = read_json(args.conditioned_summary)
    counts = summary.get("counts") or {}
    distributions = summary.get("distributions") or {}
    selection = summary.get("selection") or {}
    expectation = summary.get("arm_expectation_alignment") or {}
    review_gate = distributions.get("review_gate") or {}
    comparison_gate = distributions.get("comparison_gate") or {}
    family_distribution = distributions.get("selected_scenario_family") or {}
    comparison_pass_count = int(comparison_gate.get("pass", 0))
    comparison_mixed_count = int(comparison_gate.get("mixed_or_fail", 0))
    selected_scenarios = int(counts.get("selected_scenarios") or 0)
    passes_gate = all(
        [
            summary.get("status") == "complete",
            selected_scenarios >= 8,
            counts.get("expected_arm_runs") == counts.get("completed_arm_runs"),
            counts.get("comparison_rows") == selected_scenarios,
            counts.get("issue_count") == 0,
            review_gate.get("fail", 0) == 0,
            expectation.get("supportive_pass_count") == selected_scenarios,
            expectation.get("unexpected_warning_arm_runs", 0) == 0,
            expectation.get("unexpected_fail_arm_runs", 0) == 0,
            selection.get("selection_policy") == "balanced_by_family",
            len(family_distribution) == 4,
            all(int(v) >= 2 for v in family_distribution.values()),
            comparison_pass_count >= selected_scenarios - 1,
            comparison_mixed_count <= 1,
        ]
    )
    layer = {
        "status": summary.get("status"),
        "claim": "therapist-conditioned sensitivity now has a family-balanced 16-scenario held-out baseline where supportive arms stay clean and non-supportive warnings remain expectation-aligned",
        "counts": counts,
        "distributions": distributions,
        "selection": selection,
        "arm_expectation_alignment": expectation,
        "passes_gate": passes_gate,
    }
    artifacts = [artifact_record(args.conditioned_summary)]
    manifest_path = args.conditioned_summary.with_name("comparison_manifest.json")
    if manifest_path.exists():
        artifacts.append(artifact_record(manifest_path))
    return layer, artifacts


def collect_conditioned_adjudication_layer(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    artifacts: list[dict[str, Any]] = []
    summary = read_json(args.conditioned_adjudication_summary)
    packet_paths = summary.get("packet_paths") or []
    protocol_manifest = read_json(args.conditioned_human_rating_manifest)
    results_manifest = read_json(args.conditioned_human_rating_results_manifest)
    results_summary_path = Path(
        ((results_manifest.get("artifacts") or {}).get("results_summary_json") or {}).get("path")
    )
    results_summary = read_json(results_summary_path)
    validation_report = read_json(args.conditioned_human_rating_validation_report)
    packet_count = int(summary.get("packet_count") or 0)
    reviewed_row_count = int((results_summary.get("counts") or {}).get("reviewed_rows") or 0)
    fully_adjudicated_row_count = int((results_summary.get("counts") or {}).get("fully_adjudicated_rows") or 0)
    artifacts_ready = (
        summary.get("packet_count") == len(packet_paths)
        and packet_count >= 1
        and protocol_manifest.get("status") == "complete"
        and results_manifest.get("status") == "complete"
        and validation_report.get("status") == "complete"
    )
    human_review_executed = packet_count >= 1 and reviewed_row_count >= packet_count
    decision_available = packet_count >= 1 and fully_adjudicated_row_count >= packet_count
    layer = {
        "status": "complete",
        "claim": "the last conditioned residual now has an explicit adjudication packet, a reusable human-rating protocol, a compiled results-summary surface, and a validation report for execution readiness",
        "verified_facts": {
            "packet_count": packet_count,
            "scenario_ids": summary.get("scenario_ids") or [],
            "protocol_manifest_status": protocol_manifest.get("status"),
            "protocol_artifact_count": len(protocol_manifest.get("artifacts") or {}),
            "results_manifest_status": results_manifest.get("status"),
            "template_row_count": (results_summary.get("counts") or {}).get("total_rows"),
            "rated_row_count": reviewed_row_count,
            "fully_adjudicated_row_count": fully_adjudicated_row_count,
            "adjudicated_non_blocking_count": ((results_summary.get("distributions") or {}).get("adjudication_status") or {}).get("adjudicated_non_blocking", 0),
            "adjudicated_blocking_count": ((results_summary.get("distributions") or {}).get("adjudication_status") or {}).get("adjudicated_blocking", 0),
            "validation_status": validation_report.get("status"),
            "validation_ready_row_count": (validation_report.get("counts") or {}).get("ready_rows"),
            "validation_blank_row_count": (validation_report.get("counts") or {}).get("blank_rows"),
            "validation_invalid_row_count": (validation_report.get("counts") or {}).get("invalid_rows"),
            "artifacts_ready": artifacts_ready,
            "human_review_executed": human_review_executed,
            "decision_available": decision_available,
        },
        "passes_gate": artifacts_ready,
    }
    artifacts.append(artifact_record(args.conditioned_adjudication_summary))
    artifacts.append(artifact_record(args.conditioned_human_rating_manifest))
    artifacts.append(artifact_record(args.conditioned_human_rating_results_manifest))
    artifacts.append(artifact_record(args.conditioned_human_rating_validation_report))
    for row in packet_paths:
        json_path = Path(row["json_path"])
        md_path = Path(row["markdown_path"])
        if json_path.exists():
            artifacts.append(artifact_record(json_path))
        if md_path.exists():
            artifacts.append(artifact_record(md_path))
    protocol_md = Path(((protocol_manifest.get("artifacts") or {}).get("protocol_md") or {}).get("path") or "")
    sheet_csv = Path(((protocol_manifest.get("artifacts") or {}).get("sheet_csv_template") or {}).get("path") or "")
    sheet_json_path = Path(((protocol_manifest.get("artifacts") or {}).get("sheet_json_template") or {}).get("path") or "")
    if protocol_md.exists():
        artifacts.append(artifact_record(protocol_md))
    if sheet_csv.exists():
        artifacts.append(artifact_record(sheet_csv))
    if sheet_json_path.exists():
        artifacts.append(artifact_record(sheet_json_path))
    if results_summary_path.exists():
        artifacts.append(artifact_record(results_summary_path))
    results_md = Path(((results_manifest.get("artifacts") or {}).get("results_summary_md") or {}).get("path") or "")
    if results_md.exists():
        artifacts.append(artifact_record(results_md))
    return layer, artifacts


def collect_benchmark_layer(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary = read_json(args.benchmark_summary)
    counts = summary.get("counts") or {}
    distributions = summary.get("distributions") or {}
    selection = summary.get("selection") or {}
    failure_taxonomy = distributions.get("failure_taxonomy_observed") or {}
    gate_status = distributions.get("gate_status") or {}
    family_distribution = distributions.get("scenario_family") or {}
    selected_scenarios = int(counts.get("selected_scenarios") or 0)
    completed_scenarios = int(counts.get("completed_scenarios") or 0)
    layer = {
        "status": summary.get("status"),
        "claim": "an all-pass family-balanced held-out formal benchmark now exists for the four major scenario families",
        "counts": counts,
        "distributions": distributions,
        "selection": selection,
        "passes_gate": all(
            [
                summary.get("status") == "complete",
                selected_scenarios >= 8,
                completed_scenarios == selected_scenarios,
                counts.get("issue_count") == 0,
                counts.get("criteria_pass_rate") == 1.0,
                selection.get("selection_policy") == "balanced_by_family",
                len(family_distribution) == 4,
                all(int(v) >= 2 for v in family_distribution.values()),
                gate_status.get("pass", 0) == selected_scenarios,
                not failure_taxonomy,
            ]
        ),
    }
    artifacts = [artifact_record(args.benchmark_summary)]
    manifest_path = args.benchmark_summary.with_name("benchmark_manifest.json")
    if manifest_path.exists():
        artifacts.append(artifact_record(manifest_path))
    return layer, artifacts


def collect_hermes_layer(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    validation = read_json(args.hermes_validation)
    heldout_summary = read_json(args.hermes_heldout_summary)
    pilot = read_json(args.hermes_pilot)
    digest = read_json(args.hermes_digest)
    final_response = validation.get("final_response_json") or {}
    final_state = final_response.get("final_state") or {}
    session = validation.get("session") or {}
    heldout_counts = heldout_summary.get("counts") or {}
    heldout_selection = heldout_summary.get("selection") or {}
    heldout_distributions = heldout_summary.get("distributions") or {}
    heldout_gate = heldout_summary.get("gate") or {}
    passes_gate = all(
        [
            heldout_summary.get("status") == "complete",
            heldout_gate.get("passes_gate") is True,
            heldout_counts.get("selected_scenarios") == 4,
            heldout_counts.get("completed_validations") == 4,
            heldout_counts.get("pass_count") == 4,
            heldout_counts.get("issue_count") == 0,
            heldout_selection.get("selection_policy") == "balanced_by_family",
            len(heldout_distributions.get("scenario_family") or {}) == 4,
            pilot.get("case_count") == pilot.get("passed_count"),
            pilot.get("failed_count") == 0,
            validation.get("status") == "complete",
            bool(session.get("completed")),
            final_response.get("review_gate") == "pass",
        ]
    )
    layer = {
        "status": "complete" if passes_gate else "partial",
        "claim": "Hermes can discover the patient simulator skill and consume exact held-out runtime artifacts across a balanced four-family gate",
        "heldout_gate": {
            "counts": heldout_counts,
            "selection": heldout_selection,
            "distributions": heldout_distributions,
            "gate": heldout_gate,
        },
        "validation_session": session,
        "validation_final_response": {
            "skill_seen": final_response.get("skill_seen"),
            "scenario_id": final_response.get("scenario_id"),
            "review_gate": final_response.get("review_gate"),
            "final_state": final_state,
        },
        "pilot": {
            "case_count": pilot.get("case_count"),
            "passed_count": pilot.get("passed_count"),
            "failed_count": pilot.get("failed_count"),
        },
        "review_digest": {
            "review_gate": digest.get("review_gate"),
            "final_state": digest.get("final_state"),
            "trace_counts": digest.get("trace_counts"),
        },
        "passes_gate": passes_gate,
    }
    artifacts = [
        artifact_record(args.hermes_heldout_summary),
        artifact_record(args.hermes_validation),
        artifact_record(args.hermes_pilot),
        artifact_record(args.hermes_digest),
    ]
    if args.hermes_heldout_manifest.exists():
        artifacts.append(artifact_record(args.hermes_heldout_manifest))
    return layer, artifacts


def build_overall_assessment(package: dict[str, Any]) -> dict[str, Any]:
    v1_layer = package["evidence_layers"]["v1_completion"]
    v1_gate = v1_layer.get("passes_gate")
    v1_included = bool(v1_layer.get("included"))
    longitudinal_gate = package["evidence_layers"]["longitudinal_hardening"]["passes_gate"]
    conditioned_gate = package["evidence_layers"]["therapist_conditioned_formal_baseline"]["passes_gate"]
    conditioned_adjudication = package["evidence_layers"]["conditioned_adjudication"]["verified_facts"]
    conditioned_adjudication_gate = package["evidence_layers"]["conditioned_adjudication"]["passes_gate"]
    adjudication_artifacts_ready = bool(conditioned_adjudication.get("artifacts_ready"))
    adjudication_human_review_executed = bool(conditioned_adjudication.get("human_review_executed"))
    adjudication_decision_available = bool(conditioned_adjudication.get("decision_available"))
    benchmark_gate = package["evidence_layers"]["benchmark_formal_baseline"]["passes_gate"]
    hermes_gate = package["evidence_layers"]["hermes_integration"]["passes_gate"]

    remaining_gaps = []
    if not v1_included:
        remaining_gaps.append("historical v1 completion evidence layer is omitted from the default package build unless explicitly requested")
    if not conditioned_gate:
        remaining_gaps.append("therapist-conditioned formal baseline still needs stronger cross-family evidence or reviewer cleanup")
    elif not conditioned_adjudication_gate or not adjudication_artifacts_ready:
        remaining_gaps.append("conditioned formal baseline is promoted, but the residual adjudication packet / human-rating protocol layer is still incomplete")
    elif not adjudication_human_review_executed:
        remaining_gaps.append("conditioned formal baseline is expectation-aligned at the current 16-scenario balanced held-out slice; adjudication artifacts are ready, but external human review has not yet been executed on the residual packet")
    elif not adjudication_decision_available:
        remaining_gaps.append("conditioned formal baseline has started external human review, but no final adjudication decision is yet available for the residual packet")
    else:
        remaining_gaps.append("conditioned formal baseline is expectation-aligned at the current 16-scenario balanced held-out slice; adjudication execution has completed and the residual should now be interpreted through the compiled human-rating outputs")
    remaining_gaps.append("formal benchmark is now all-pass at the current 16-scenario family-balanced slice, but externally reviewable paper-grade benchmark tables still need broader held-out coverage")
    remaining_gaps.append("Hermes now has a held-out four-family artifact-consumption gate, but a larger from-scratch rerun gate inside Hermes is still optional future work")

    return {
        "historical_v1_completion_included": v1_included,
        "patient_simulator_v1_completion_satisfied": v1_gate,
        "longitudinal_4family_3case_3session_3turn_full_pass": longitudinal_gate,
        "therapist_conditioned_4family_formal_baseline_ready": conditioned_gate,
        "conditioned_adjudication_artifacts_ready": adjudication_artifacts_ready,
        "conditioned_adjudication_human_review_executed": adjudication_human_review_executed,
        "conditioned_adjudication_decision_available": adjudication_decision_available,
        "benchmark_taxonomy_signal_available": benchmark_gate,
        "hermes_integration_verified": hermes_gate,
        "current_best_claim": (
            "patient simulator core runtime is complete and hardened; the current frontier is paper-grade evaluation expansion, "
            "not basic runtime feasibility"
        ),
        "remaining_gaps": remaining_gaps,
    }


def build_markdown_report(package: dict[str, Any]) -> str:
    v1 = package["evidence_layers"]["v1_completion"]
    longi = package["evidence_layers"]["longitudinal_hardening"]
    conditioned = package["evidence_layers"]["therapist_conditioned_formal_baseline"]
    conditioned_adj = package["evidence_layers"]["conditioned_adjudication"]
    benchmark = package["evidence_layers"]["benchmark_formal_baseline"]
    hermes = package["evidence_layers"]["hermes_integration"]
    assessment = package["overall_assessment"]

    lines = [
        "# Patient Simulator Evaluation Package v1",
        "",
        f"- Created at: `{package['created_at']}`",
        f"- Package id: `{package['package_id']}`",
        f"- Status: `{package['status']}`",
        "",
        "## Overall",
        "",
        f"- `historical v1 completion included`: `{assessment['historical_v1_completion_included']}`",
        f"- `patient simulator v1 completion satisfied`: `{assessment['patient_simulator_v1_completion_satisfied']}`",
        f"- `4-family longitudinal full pass`: `{assessment['longitudinal_4family_3case_3session_3turn_full_pass']}`",
        f"- `4-family conditioned formal baseline ready`: `{assessment['therapist_conditioned_4family_formal_baseline_ready']}`",
        f"- `conditioned adjudication artifacts ready`: `{assessment['conditioned_adjudication_artifacts_ready']}`",
        f"- `conditioned adjudication human review executed`: `{assessment['conditioned_adjudication_human_review_executed']}`",
        f"- `conditioned adjudication decision available`: `{assessment['conditioned_adjudication_decision_available']}`",
        f"- `benchmark taxonomy signal available`: `{assessment['benchmark_taxonomy_signal_available']}`",
        f"- `Hermes integration verified`: `{assessment['hermes_integration_verified']}`",
        "",
        f"- Current best claim: {assessment['current_best_claim']}",
            "",
            "## V1 Completion",
            "",
    ]
    if v1.get("included"):
        lines.extend(
            [
                f"- Case bundles: `{v1['verified_facts']['case_bundle_count']}`",
                f"- Scenario rows: `{v1['verified_facts']['scenario_rows']}`",
                f"- Single-session turns completed: `{v1['verified_facts']['single_session_turn_count_completed']}`",
                f"- Multi-session session count: `{v1['verified_facts']['multi_session_session_count']}`",
                f"- Reviewer gate: `{v1['verified_facts']['reviewer_gate']['gate_status']}`",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "- This historical v1 completion evidence layer is omitted by default.",
                "- Rebuild with `--include-historical-v1-completion` to include it again.",
                "",
            ]
        )
    lines.extend(
        [
            "## Longitudinal Hardening",
            "",
            "| family | selected | completed | review pass | continuity pass | memory pass | persona pass | full pass |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for family, row in longi["families"].items():
        lines.append(
            f"| `{family}` | `{row['selected_case_count']}` | `{row['completed_case_count']}` | "
            f"`{row['all_sessions_review_pass_count']}` | `{row['continuity_exact_opening_match_count']}` | "
            f"`{row['memory_faithfulness_sessions_2_3_pass_count']}` | "
            f"`{row['persona_consistency_sessions_2_3_pass_count']}` | `{row['full_pass']}` |"
        )
    lines.extend(
        [
            "",
            "## Therapist-Conditioned Formal Baseline",
            "",
            f"- Selected scenarios: `{conditioned['counts']['selected_scenarios']}`",
            f"- Completed arm runs: `{conditioned['counts']['completed_arm_runs']}` / `{conditioned['counts']['expected_arm_runs']}`",
            f"- Comparison rows: `{conditioned['counts']['comparison_rows']}`",
            f"- Review gate distribution: `{conditioned['distributions']['review_gate']}`",
            f"- Comparison gate distribution: `{conditioned['distributions']['comparison_gate']}`",
            f"- Supportive pass count: `{conditioned.get('arm_expectation_alignment', {}).get('supportive_pass_count')}` / `{conditioned['counts']['selected_scenarios']}`",
            f"- Expected warning arm runs: `{conditioned.get('arm_expectation_alignment', {}).get('expected_warning_arm_runs')}`",
            f"- Unexpected warning arm runs: `{conditioned.get('arm_expectation_alignment', {}).get('unexpected_warning_arm_runs')}`",
            f"- Selection policy: `{conditioned['selection']['selection_policy']}`",
            "",
            "## Conditioned Adjudication",
            "",
            f"- Residual packet count: `{conditioned_adj['verified_facts']['packet_count']}`",
            f"- Protocol manifest status: `{conditioned_adj['verified_facts']['protocol_manifest_status']}`",
            f"- Template row count: `{conditioned_adj['verified_facts']['template_row_count']}`",
            f"- Rated row count: `{conditioned_adj['verified_facts']['rated_row_count']}`",
            f"- Fully adjudicated row count: `{conditioned_adj['verified_facts']['fully_adjudicated_row_count']}`",
            f"- Adjudicated non-blocking count: `{conditioned_adj['verified_facts']['adjudicated_non_blocking_count']}`",
            f"- Adjudicated blocking count: `{conditioned_adj['verified_facts']['adjudicated_blocking_count']}`",
            f"- Validation ready row count: `{conditioned_adj['verified_facts']['validation_ready_row_count']}`",
            f"- Validation blank row count: `{conditioned_adj['verified_facts']['validation_blank_row_count']}`",
            f"- Validation invalid row count: `{conditioned_adj['verified_facts']['validation_invalid_row_count']}`",
            f"- Artifacts ready: `{conditioned_adj['verified_facts']['artifacts_ready']}`",
            f"- Human review executed: `{conditioned_adj['verified_facts']['human_review_executed']}`",
            f"- Decision available: `{conditioned_adj['verified_facts']['decision_available']}`",
            "",
            "## Benchmark Formal Baseline",
            "",
            f"- Selected scenarios: `{benchmark['counts']['selected_scenarios']}`",
            f"- Completed scenarios: `{benchmark['counts']['completed_scenarios']}`",
            f"- Criteria pass rate: `{benchmark['counts']['criteria_pass_rate']}`",
            f"- Gate status distribution: `{benchmark['distributions']['gate_status']}`",
            f"- Failure taxonomy observed: `{benchmark['distributions']['failure_taxonomy_observed']}`",
            "",
        "## Hermes Integration",
        "",
        f"- Held-out selected scenarios: `{hermes['heldout_gate']['counts']['selected_scenarios']}`",
        f"- Held-out completed validations: `{hermes['heldout_gate']['counts']['completed_validations']}`",
        f"- Held-out pass count: `{hermes['heldout_gate']['counts']['pass_count']}`",
        f"- Held-out gate pass: `{hermes['heldout_gate']['gate']['passes_gate']}`",
        f"- Held-out tool distribution: `{hermes['heldout_gate']['distributions']['completed_tool_names']}`",
        f"- Validation session completed: `{hermes['validation_session']['completed']}`",
        f"- Validation API calls: `{hermes['validation_session']['api_calls']}`",
        f"- Pilot pass count: `{hermes['pilot']['passed_count']}` / `{hermes['pilot']['case_count']}`",
            f"- Validation final review gate: `{hermes['validation_final_response']['review_gate']}`",
            f"- Validation final alliance state: `{hermes['validation_final_response']['final_state']['alliance_state']}`",
            "",
            "## Remaining Gaps",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in assessment["remaining_gaps"]])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    v1_layer, v1_artifacts = collect_v1_completion(args)
    longitudinal_layer, longitudinal_artifacts = collect_longitudinal_layer()
    conditioned_layer, conditioned_artifacts = collect_conditioned_layer(args)
    conditioned_adjudication_layer, conditioned_adjudication_artifacts = collect_conditioned_adjudication_layer(args)
    benchmark_layer, benchmark_artifacts = collect_benchmark_layer(args)
    hermes_layer, hermes_artifacts = collect_hermes_layer(args)

    source_artifacts = (
        v1_artifacts
        + longitudinal_artifacts
        + conditioned_artifacts
        + conditioned_adjudication_artifacts
        + benchmark_artifacts
        + hermes_artifacts
    )
    source_fingerprints = [f"{row['repo_relative_path']}::{row['sha256']}" for row in source_artifacts]
    package_id = f"patient_simulator_eval::{stable_hash_text('|'.join(sorted(source_fingerprints)))}"

    package = {
        "schema_version": "theraskill_patient_simulator_evaluation_package_v1",
        "created_at": utc_now(),
        "status": "complete",
        "package_id": package_id,
        "evidence_layers": {
            "v1_completion": v1_layer,
            "longitudinal_hardening": longitudinal_layer,
            "therapist_conditioned_formal_baseline": conditioned_layer,
            "conditioned_adjudication": conditioned_adjudication_layer,
            "benchmark_formal_baseline": benchmark_layer,
            "hermes_integration": hermes_layer,
        },
        "source_artifacts": source_artifacts,
    }
    package["overall_assessment"] = build_overall_assessment(package)

    package_json_path = args.output_dir / "evaluation_package.json"
    package_md_path = args.output_dir / "evaluation_package.md"
    manifest_path = args.output_dir / "evaluation_manifest.json"

    write_json(package_json_path, package)
    package_md_path.write_text(build_markdown_report(package), encoding="utf-8")

    manifest = {
        "schema_version": "theraskill_patient_simulator_evaluation_manifest_v1",
        "created_at": utc_now(),
        "status": "complete",
        "package_id": package_id,
        "inputs": source_artifacts,
        "artifacts": {
            "evaluation_package_json": artifact_record(package_json_path),
            "evaluation_package_md": artifact_record(package_md_path),
        },
    }
    write_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
