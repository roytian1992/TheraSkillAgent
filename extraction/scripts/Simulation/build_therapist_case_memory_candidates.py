#!/usr/bin/env python3
"""Build deterministic candidate rows for Simulation.therapist_case_memory_pattern."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, file_sha256, read_jsonl, write_json, write_jsonl
from simulation_surface_registry import (
    SIMULATION_CASE_FORMULATION_PATH,
    SIMULATION_CASE_TRAJECTORY_PATH,
    SIMULATION_MEMORY_BUNDLE_PATH,
    SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_MANIFEST_PATH,
    SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_PATH,
    SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_QA_PATH,
)


DEFAULT_FORMULATION = SIMULATION_CASE_FORMULATION_PATH
DEFAULT_TRAJECTORY = SIMULATION_CASE_TRAJECTORY_PATH
DEFAULT_MEMORY_BUNDLE = SIMULATION_MEMORY_BUNDLE_PATH
DEFAULT_OUTPUT = SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_PATH
DEFAULT_MANIFEST = SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_MANIFEST_PATH
DEFAULT_QA = SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_QA_PATH

PHASE_TO_MEMORY_STAGE = {
    "early": "target",
    "middle": "working",
    "late": "ending",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-formulation", default=str(DEFAULT_FORMULATION))
    parser.add_argument("--case-trajectory", default=str(DEFAULT_TRAJECTORY))
    parser.add_argument("--memory-bundle", default=str(DEFAULT_MEMORY_BUNDLE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit-cases", type=int)
    return parser.parse_args()

def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def uniq_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def clean_text(text: Any, limit: int | None = None) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] if limit is not None else value


def flatten_dict_items(value: Any) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            flat.append(node)
            return
        if isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return flat


def formulation_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = row.get("source") or {}
        case_id = str(source.get("case_id") or "")
        if case_id:
            index[case_id] = row
    return index


def memory_rows_by_stage(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        stage = str((row.get("source") or {}).get("primary_stage") or "unspecified")
        grouped.setdefault(stage, []).append(row)
    return grouped


def history_bucket_from_count(transition_count: int) -> str:
    if transition_count <= 0:
        return "00_no_history"
    if transition_count <= 5:
        return "01_1_to_5_history_pairs"
    if transition_count <= 10:
        return "02_6_to_10_history_pairs"
    if transition_count <= 15:
        return "03_11_to_15_history_pairs"
    if transition_count <= 20:
        return "04_16_to_20_history_pairs"
    if transition_count <= 25:
        return "05_21_to_25_history_pairs"
    return "06_26_plus_history_pairs"


def phases_present(trajectory: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("alliance_timeline", "disclosure_timeline", "readiness_timeline", "risk_timeline"):
        for item in flatten_dict_items(trajectory.get(key) or []):
            phase = str(item.get("phase") or "").strip()
            if phase:
                found.append(phase)
    return uniq_keep_order(found)


def choose_memory_rows(memory_rows: list[dict[str, Any]], trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    transition_count = int((((trajectory.get("session_or_case_length") or {}).get("transition_count")) or 0))
    history_bucket = history_bucket_from_count(transition_count)
    chosen: list[dict[str, Any]] = []
    stages = [PHASE_TO_MEMORY_STAGE.get(phase, "unspecified") for phase in phases_present(trajectory)]
    stages = uniq_keep_order(stages or ["working"])

    for stage in stages:
        exact = [
            row
            for row in memory_rows
            if str((row.get("source") or {}).get("primary_stage") or "") == stage
            and str((row.get("source") or {}).get("history_bucket") or "") == history_bucket
        ]
        if exact:
            chosen.append(exact[0])
            continue
        same_stage = [
            row for row in memory_rows if str((row.get("source") or {}).get("primary_stage") or "") == stage
        ]
        if same_stage:
            chosen.append(same_stage[0])

    if not chosen:
        fallback = [
            row
            for row in memory_rows
            if str((row.get("source") or {}).get("history_bucket") or "") == history_bucket
        ]
        chosen.extend(fallback[:2] if fallback else memory_rows[:2])

    return chosen[:2]


def slot_values(items: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for item in flatten_dict_items(items):
        value = clean_text(item.get(key), 220)
        if value:
            values.append(value)
    return uniq_keep_order(values)


def evidence_ids_from_items(items: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in flatten_dict_items(items):
        for value in item.get("evidence_transition_ids") or []:
            ids.append(str(value))
    return uniq_keep_order(ids)


def trajectory_thread_hints(trajectory: dict[str, Any]) -> list[str]:
    threads: list[str] = []
    for item in flatten_dict_items(trajectory.get("turning_points") or []):
        summary = clean_text(item.get("summary"), 220)
        if summary:
            threads.append(summary)
    for item in flatten_dict_items(trajectory.get("client_response_sequence") or []):
        response_type = str(item.get("response_type") or "")
        if response_type in {"ambivalence", "agreement_without_commitment", "no_clear_response"}:
            summary = clean_text(item.get("summary"), 220)
            if summary:
                threads.append(summary)
    return uniq_keep_order(threads)


def stage_tracking_hints(trajectory: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("alliance_timeline", "disclosure_timeline", "readiness_timeline", "risk_timeline"):
        for item in flatten_dict_items(trajectory.get(key) or []):
            phase = str(item.get("phase") or "unknown")
            label = (
                item.get("alliance_state")
                or item.get("disclosure_depth")
                or item.get("readiness")
                or item.get("risk_level")
                or "unknown"
            )
            hints.append(f"{key}:{phase}:{label}")
    return uniq_keep_order(hints)


def memory_rule_hints(memory_rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    carry_slots: list[str] = []
    update_rules: list[str] = []
    compression_rules: list[str] = []
    forgetting_risks: list[str] = []
    handoff_rules: list[str] = []
    for row in memory_rows:
        bundle = ((row.get("content") or {}).get("memory_pattern_bundle")) or {}
        summary_pattern = bundle.get("patient_state_summary_pattern") or {}
        update = bundle.get("memory_update_rule") or {}
        stage = bundle.get("stage_transition_memory_pattern") or {}
        carry_slots.extend(clean_text(x, 120) for x in (update.get("carry_forward_slots") or []))
        update_rules.extend(clean_text(x, 220) for x in (update.get("new_information_capture_rules") or []))
        update_rules.extend(clean_text(x, 220) for x in (update.get("turn_level_update_triggers") or []))
        compression_rules.extend(clean_text(x, 220) for x in (update.get("summary_compression_rules") or []))
        compression_rules.extend(clean_text(x, 220) for x in (update.get("discard_or_downgrade_rules") or []))
        carry_slots.extend(clean_text(x, 120) for x in (summary_pattern.get("core_client_state_slots") or []))
        carry_slots.extend(clean_text(x, 120) for x in (summary_pattern.get("goal_progress_slots") or []))
        forgetting_risks.extend(clean_text(x, 220) for x in (stage.get("history_sensitivity_notes") or []))
        handoff_rules.extend(clean_text(x, 220) for x in (stage.get("advance_stage_conditions") or []))
        handoff_rules.extend(clean_text(x, 220) for x in (stage.get("hold_stage_conditions") or []))
    return {
        "carry_slots": uniq_keep_order(carry_slots),
        "update_rules": uniq_keep_order(update_rules),
        "compression_rules": uniq_keep_order(compression_rules),
        "forgetting_risks": uniq_keep_order(forgetting_risks),
        "handoff_rules": uniq_keep_order(handoff_rules),
    }


def make_candidate(
    *,
    formulation_row: dict[str, Any],
    trajectory_row: dict[str, Any],
    memory_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    formulation = ((formulation_row.get("content") or {}).get("case_formulation_pattern")) or {}
    trajectory = ((trajectory_row.get("content") or {}).get("case_trajectory")) or {}
    source = trajectory_row.get("source") or {}
    case_id = str(source.get("case_id") or "")
    rules = memory_rule_hints(memory_rows)

    presenting_problem = slot_values(formulation.get("presenting_problem") or [], "summary")
    triggers = slot_values(formulation.get("trigger_or_context") or [], "summary")
    maintaining = slot_values(formulation.get("maintaining_factors") or [], "factor")
    protective = slot_values(formulation.get("protective_factors") or [], "factor")
    goals = slot_values(formulation.get("client_goals") or [], "goal")
    risk_summaries = slot_values(formulation.get("risk_factors") or [], "summary")
    suggested_focus = slot_values(formulation.get("suggested_focus") or [], "focus")
    trajectory_problems = slot_values(trajectory.get("dominant_presenting_problems") or [], "label")
    alliance_hints = slot_values(trajectory.get("alliance_timeline") or [], "summary")
    readiness_hints = slot_values(trajectory.get("readiness_timeline") or [], "summary")

    evidence_unit_ids = uniq_keep_order(
        [
            formulation_row.get("unit_id"),
            trajectory_row.get("unit_id"),
            *[row.get("unit_id") for row in memory_rows if row.get("unit_id")],
        ]
    )

    return {
        "schema_version": "theraskill_transition_candidate_v1",
        "candidate_id": f"tcmc_{stable_hash(case_id)}",
        "candidate_type": "therapist_case_memory_candidate",
        "source_dataset": "Simulation.multisource",
        "source_case_id": case_id,
        "source_source_id": source.get("source_id"),
        "trajectory_unit_id": trajectory_row.get("unit_id"),
        "formulation_unit_id": formulation_row.get("unit_id"),
        "transition_count": int((((trajectory.get("session_or_case_length") or {}).get("transition_count")) or 0)),
        "phase_sequence": phases_present(trajectory),
        "problem_summary_slots_hint": uniq_keep_order(presenting_problem + trajectory_problems + triggers + maintaining)[:12],
        "resource_slots_hint": uniq_keep_order(protective + readiness_hints)[:10],
        "goal_slots_hint": uniq_keep_order(goals + suggested_focus)[:10],
        "alliance_slots_hint": uniq_keep_order(alliance_hints)[:8],
        "risk_slots_hint": uniq_keep_order(
            risk_summaries
            + [
                f"{item.get('phase') or 'unknown'}::{item.get('risk_level') or 'unknown'}::{','.join(item.get('risk_domains') or [])}"
                for item in (trajectory.get("risk_timeline") or [])
            ]
        )[:8],
        "unfinished_thread_slots_hint": trajectory_thread_hints(trajectory)[:10],
        "stage_tracking_slots_hint": stage_tracking_hints(trajectory)[:12],
        "required_memory_slots_hint": uniq_keep_order(
            rules["carry_slots"]
            + ["当前主诉总结", "本阶段目标", "联盟状态", "风险监测点", "未完成主题", "下一步工作焦点"]
        )[:16],
        "memory_update_rules_hint": rules["update_rules"][:12],
        "compression_rules_hint": rules["compression_rules"][:12],
        "forgetting_risks_hint": uniq_keep_order(rules["forgetting_risks"] + trajectory_thread_hints(trajectory))[:10],
        "handoff_or_resume_rules_hint": rules["handoff_rules"][:10],
        "evidence_transition_ids": uniq_keep_order(
            evidence_ids_from_items(formulation.get("presenting_problem") or [])
            + evidence_ids_from_items(formulation.get("maintaining_factors") or [])
            + evidence_ids_from_items(formulation.get("protective_factors") or [])
            + evidence_ids_from_items(formulation.get("client_goals") or [])
            + evidence_ids_from_items(formulation.get("risk_factors") or [])
            + evidence_ids_from_items(formulation.get("suggested_focus") or [])
            + list(trajectory.get("transition_unit_ids") or [])
        )[:40],
        "memory_pattern_refs": [
            {
                "unit_id": row.get("unit_id"),
                "group_id": (row.get("source") or {}).get("group_id"),
                "primary_stage": (row.get("source") or {}).get("primary_stage"),
                "history_bucket": (row.get("source") or {}).get("history_bucket"),
            }
            for row in memory_rows
        ],
        "case_overview": {
            "overall_process_summary": clean_text(trajectory.get("overall_process_summary"), 400),
            "turning_points": [
                {
                    "type": item.get("turning_point_type"),
                    "summary": clean_text(item.get("summary"), 220),
                }
                for item in (trajectory.get("turning_points") or [])[:4]
            ],
        },
        "evidence_unit_ids": evidence_unit_ids,
        "provenance": {
            "case_formulation_unit_id": formulation_row.get("unit_id"),
            "case_trajectory_unit_id": trajectory_row.get("unit_id"),
            "memory_group_ids": [
                (row.get("source") or {}).get("group_id") for row in memory_rows if (row.get("source") or {}).get("group_id")
            ],
        },
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)

    for path in (output_path, manifest_path, qa_path):
        ensure_output_path(path, overwrite=args.overwrite)

    formulation_rows = read_jsonl(args.case_formulation)
    trajectory_rows = read_jsonl(args.case_trajectory)
    memory_rows = read_jsonl(args.memory_bundle)

    if args.limit_cases is not None:
        trajectory_rows = trajectory_rows[: args.limit_cases]

    formulation_by_case = formulation_index(formulation_rows)
    candidates: list[dict[str, Any]] = []
    missing_formulation_cases = 0
    stage_distribution = Counter()
    history_distribution = Counter()

    for trajectory_row in trajectory_rows:
        case_id = str((trajectory_row.get("source") or {}).get("case_id") or "")
        formulation_row = formulation_by_case.get(case_id)
        if formulation_row is None:
            missing_formulation_cases += 1
            continue
        chosen_memory = choose_memory_rows(memory_rows, ((trajectory_row.get("content") or {}).get("case_trajectory")) or {})
        for ref in chosen_memory:
            stage_distribution[str((ref.get("source") or {}).get("primary_stage") or "unknown")] += 1
            history_distribution[str((ref.get("source") or {}).get("history_bucket") or "unknown")] += 1
        candidates.append(
            make_candidate(
                formulation_row=formulation_row,
                trajectory_row=trajectory_row,
                memory_rows=chosen_memory,
            )
        )

    write_jsonl(output_path, candidates, overwrite=args.overwrite, sort_keys=True, separators=(",", ":"))

    manifest = {
        "schema_version": "theraskill_therapist_case_memory_candidates_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "input_files": {
            "case_formulation": {"path": str(args.case_formulation), "sha256": file_sha256(args.case_formulation)},
            "case_trajectory": {"path": str(args.case_trajectory), "sha256": file_sha256(args.case_trajectory)},
            "memory_bundle": {"path": str(args.memory_bundle), "sha256": file_sha256(args.memory_bundle)},
        },
        "output_file": {"path": str(args.output), "sha256": file_sha256(args.output)},
        "candidate_count": len(candidates),
        "missing_formulation_cases": missing_formulation_cases,
        "qa_report": str(args.qa_report),
    }
    write_json(manifest_path, manifest, overwrite=args.overwrite)

    qa = {
        "schema_version": "theraskill_therapist_case_memory_candidates_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "candidate_count": len(candidates),
        "source_case_count": len(trajectory_rows),
        "missing_formulation_cases": missing_formulation_cases,
        "candidate_type_distribution": dict(Counter(row.get("candidate_type") for row in candidates).most_common()),
        "memory_stage_distribution": dict(stage_distribution.most_common()),
        "memory_history_bucket_distribution": dict(history_distribution.most_common()),
        "duplicate_candidate_id_count": len(candidates) - len({str(row.get("candidate_id") or "") for row in candidates}),
        "issues_count": 0,
        "issues": [],
    }
    write_json(qa_path, qa, overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "missing_formulation_cases": missing_formulation_cases,
                "issues_count": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
