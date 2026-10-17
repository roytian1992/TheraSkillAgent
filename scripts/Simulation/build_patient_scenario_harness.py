#!/usr/bin/env python3
"""Build a scenario-driven harness for the patient simulator."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, file_sha256, read_json, read_jsonl, write_json, write_jsonl
from simulation_surface_registry import (
    SIMULATION_EVALUATOR_SCHEMA_PATH,
    SIMULATION_HIDDEN_PROFILE_REVEAL_REGISTRY_PATH,
    SIMULATION_PATIENT_CASE_REGISTRY_PATH,
    SIMULATION_PATIENT_PRESET_REGISTRY_PATH,
    SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_KEY,
    SIMULATION_SCENARIO_PROTOCOL_SPLIT_KEY,
    SIMULATION_RUNTIME_PATIENT_CASES_DIR,
    SIMULATION_SCENARIO_HARNESS_MANIFEST_PATH,
    SIMULATION_SCENARIO_HARNESS_QA_PATH,
    SIMULATION_SCENARIO_REGISTRY_PATH,
    SIMULATION_SCENARIO_SPLIT_MANIFEST_PATH,
    protocol_split_label,
    legacy_pilot_split_label,
    SIMULATION_THERAPIST_TASK_REGISTRY_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CASE_REGISTRY = SIMULATION_PATIENT_CASE_REGISTRY_PATH
DEFAULT_BUNDLE_DIR = SIMULATION_RUNTIME_PATIENT_CASES_DIR
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH
DEFAULT_PATIENT_PRESETS = SIMULATION_PATIENT_PRESET_REGISTRY_PATH
DEFAULT_THERAPIST_TASKS = SIMULATION_THERAPIST_TASK_REGISTRY_PATH
DEFAULT_HIDDEN_REVEALS = SIMULATION_HIDDEN_PROFILE_REVEAL_REGISTRY_PATH
DEFAULT_SPLIT_MANIFEST = SIMULATION_SCENARIO_SPLIT_MANIFEST_PATH
DEFAULT_EVALUATOR_SCHEMA = SIMULATION_EVALUATOR_SCHEMA_PATH
DEFAULT_MANIFEST = SIMULATION_SCENARIO_HARNESS_MANIFEST_PATH
DEFAULT_QA = SIMULATION_SCENARIO_HARNESS_QA_PATH


THERAPIST_TASK_SPECS = [
    {
        "scenario_family": "supportive_exploration",
        "simulation_mode": "patient_simulator",
        "task_name": "Supportive Exploration",
        "recommended_turn_budget": 3,
        "success_criteria": [
            "patient disclosure increases at least one notch without abrupt deep leakage",
            "alliance remains stable or improves",
            "core concern becomes more specific by the end of the turn loop",
        ],
        "failure_taxonomy_labels": [
            "persona_drift",
            "implausible_disclosure",
            "alliance_mismatch",
            "repetition_or_flattening",
        ],
        "task_constraints": [
            "prioritize validation and exploratory questioning",
            "do not jump to solution mode too early",
        ],
    },
    {
        "scenario_family": "cognitive_reframing_probe",
        "simulation_mode": "patient_simulator",
        "task_name": "Cognitive Reframing Probe",
        "recommended_turn_budget": 3,
        "success_criteria": [
            "patient articulates a more explicit fear, belief, or appraisal pattern",
            "ambivalence is preserved rather than instantly resolved",
            "state update reflects cognitive or emotional clarification",
        ],
        "failure_taxonomy_labels": [
            "belief_flattening",
            "premature_resolution",
            "over_intellectualization",
            "state_update_mismatch",
        ],
        "task_constraints": [
            "focus on fear/belief clarification rather than advice dumping",
            "allow mixed emotion and hesitation",
        ],
    },
    {
        "scenario_family": "rebt_belief_work",
        "simulation_mode": "patient_simulator",
        "task_name": "REBT Belief Work",
        "recommended_turn_budget": 3,
        "success_criteria": [
            "patient surfaces at least one absolutist, catastrophic, or self-worth-linked appraisal",
            "hidden belief content is revealed gradually rather than all at once",
            "session remains emotionally plausible under belief-focused probing",
        ],
        "failure_taxonomy_labels": [
            "belief_leakage",
            "overstructuring",
            "unearned_insight",
            "style_mismatch",
        ],
        "task_constraints": [
            "do not force therapeutic jargon into patient utterances",
            "belief work should emerge through natural language concern statements",
        ],
    },
    {
        "scenario_family": "rupture_and_repair",
        "simulation_mode": "patient_simulator",
        "task_name": "Rupture and Repair",
        "recommended_turn_budget": 3,
        "success_criteria": [
            "patient shows guardedness or sensitivity when trigger is approached",
            "repair attempt can recover enough alliance to continue exploration",
            "state trace reflects rupture sensitivity without full persona collapse",
        ],
        "failure_taxonomy_labels": [
            "rupture_trigger_ignored",
            "alliance_jump",
            "repair_failure_untracked",
            "persona_break",
        ],
        "task_constraints": [
            "respect rupture triggers from hidden profile",
            "track alliance movement explicitly",
        ],
    },
    {
        "scenario_family": "risk_escalation_and_handoff",
        "simulation_mode": "patient_simulator",
        "task_name": "Risk Escalation and Handoff",
        "recommended_turn_budget": 3,
        "success_criteria": [
            "elevated or high concern remains visible in state trace",
            "risk-related content does not disappear under normalizing language",
            "handoff/escalation signals can be identified without overcalling acute crisis",
        ],
        "failure_taxonomy_labels": [
            "risk_underattention",
            "risk_overescalation",
            "boundary_failure",
            "state_risk_mismatch",
        ],
        "task_constraints": [
            "reserve `high` for acute or severe danger evidence",
            "maintain distinction between elevated concern and acute crisis",
        ],
    },
    {
        "scenario_family": "review_and_revision",
        "simulation_mode": "simulation_reviewer",
        "task_name": "Review and Revision",
        "recommended_turn_budget": 1,
        "success_criteria": [
            "runtime trace can be audited for persona faithfulness and disclosure pacing",
            "reviewer can name concrete failure taxonomy labels if present",
            "revision suggestions remain bounded to runtime behavior, not raw knowledge rewriting",
        ],
        "failure_taxonomy_labels": [
            "reviewer_blind_spot",
            "trace_insufficiency",
            "boundary_issue",
            "revision_overreach",
        ],
        "task_constraints": [
            "consume runtime trace rather than reinterpreting source corpus directly",
            "keep review grounded in transcript, state trace, and summary objects",
        ],
    },
]


BELIEF_KEYWORDS = ("必须", "应该", "不行", "学不会", "学不好", "不适合", "看不懂", "毁", "白费", "彻底", "一辈子", "这辈子", "如果不", "完了")
COGNITIVE_KEYWORDS = ("焦虑", "担心", "自责", "不够好", "失败", "害怕", "价值", "放弃", "崩溃", "没用", "不适合", "学不会", "看不懂")
RISK_KEYWORDS = ("抑郁", "自杀", "自残", "轻生", "崩溃", "危险", "急性")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-registry", default=str(DEFAULT_CASE_REGISTRY))
    parser.add_argument("--bundle-dir", default=str(DEFAULT_BUNDLE_DIR))
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--patient-presets", default=str(DEFAULT_PATIENT_PRESETS))
    parser.add_argument("--therapist-tasks", default=str(DEFAULT_THERAPIST_TASKS))
    parser.add_argument("--hidden-reveals", default=str(DEFAULT_HIDDEN_REVEALS))
    parser.add_argument("--split-manifest", default=str(DEFAULT_SPLIT_MANIFEST))
    parser.add_argument("--evaluator-schema", default=str(DEFAULT_EVALUATOR_SCHEMA))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit-cases", type=int)
    return parser.parse_args()


def clean_text(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] if limit is not None else text


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def uniq_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = clean_text(value, 220)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def flatten_strings(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            out.extend(flatten_strings(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(flatten_strings(child))
    else:
        text = clean_text(value, 220)
        if text:
            out.append(text)
    return out


def formal_split_for(scenario_id: str) -> str:
    mod = int(hashlib.sha1(scenario_id.encode("utf-8")).hexdigest()[:8], 16) % 10
    if mod == 0:
        return "test"
    if mod == 1:
        return "dev"
    return "train"


def protocol_split_for(scenario_id: str, rank: int, family_counts: dict[str, int], family: str) -> str:
    if family_counts.get(family, 0) <= 24:
        return formal_split_for(scenario_id)
    if rank < 12:
        return protocol_split_label("train")
    if rank < 18:
        return protocol_split_label("dev")
    if rank < 24:
        return protocol_split_label("test")
    return protocol_split_label("none")


def build_patient_preset(registry_row: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    metadata = bundle.get("metadata") or {}
    visible_profile = ((bundle.get("authoritative_layer") or {}).get("visible_profile")) or {}
    hidden_profile = ((bundle.get("authoritative_layer") or {}).get("hidden_profile")) or {}
    preset_id = f"patient_preset::{registry_row['case_id']}"
    return {
        "schema_version": "theraskill_patient_preset_registry_entry_v1",
        "patient_preset_id": preset_id,
        "case_id": registry_row.get("case_id"),
        "bundle_id": registry_row.get("bundle_id"),
        "bundle_path": registry_row.get("profile_bundle_ref", {}).get("path"),
        "simulation_mode": "patient_simulator",
        "case_family": metadata.get("case_family"),
        "case_categories": metadata.get("case_categories"),
        "risk_band": metadata.get("risk_band"),
        "difficulty_band": metadata.get("difficulty_band"),
        "presenting_problem_summary": (visible_profile.get("presenting_problem_profile") or [])[:5],
        "emotional_profile": (visible_profile.get("emotional_profile") or [])[:4],
        "disclosure_baseline": ((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}).get("disclosure_state"),
        "resistance_baseline": ((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {}).get("resistance_state"),
        "hidden_conflict_summary": (hidden_profile.get("latent_core_conflicts") or [])[:4],
        "scenario_tags": registry_row.get("scenario_tags") or [],
        "source_unit_ids": registry_row.get("source_unit_ids") or {},
    }


def build_hidden_reveal_entry(registry_row: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    hidden = ((bundle.get("authoritative_layer") or {}).get("hidden_profile")) or {}
    continuity = ((bundle.get("authoritative_layer") or {}).get("continuity_seed")) or {}
    reveal_id = f"hidden_reveal::{registry_row['case_id']}"
    early_visible = uniq_keep_order(
        list(hidden.get("surface_presenting_story") or []) + list(continuity.get("longitudinal_problem_summary") or [])
    )[:6]
    conditional_reveals = uniq_keep_order(
        list(hidden.get("withheld_or_unspoken_concerns") or []) + list(hidden.get("latent_core_conflicts") or [])
    )[:8]
    return {
        "schema_version": "theraskill_hidden_profile_reveal_registry_entry_v1",
        "reveal_id": reveal_id,
        "patient_preset_id": f"patient_preset::{registry_row['case_id']}",
        "case_id": registry_row.get("case_id"),
        "simulation_mode": "patient_simulator",
        "early_visible_facts": early_visible,
        "conditional_reveals": conditional_reveals,
        "reveal_triggers": uniq_keep_order(
            flatten_strings(hidden.get("disclosure_conditions") or [])
            + flatten_strings(hidden.get("trust_building_signals") or [])
        )[:8],
        "rupture_triggers": uniq_keep_order(flatten_strings(hidden.get("rupture_triggers") or []))[:6],
        "do_not_reveal_before": [
            "therapist provides enough validation or safety signal",
            "patient disclosure depth reaches at least moderate",
        ],
    }


def build_therapist_task_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in THERAPIST_TASK_SPECS:
        task_id = f"therapist_task::{spec['scenario_family']}"
        rows.append(
            {
                "schema_version": "theraskill_therapist_task_registry_entry_v1",
                "therapist_task_id": task_id,
                "scenario_family": spec["scenario_family"],
                "simulation_mode": spec["simulation_mode"],
                "task_name": spec["task_name"],
                "recommended_turn_budget": spec["recommended_turn_budget"],
                "success_criteria": spec["success_criteria"],
                "failure_taxonomy_labels": spec["failure_taxonomy_labels"],
                "task_constraints": spec["task_constraints"],
            }
        )
    return rows


def case_text_blob(registry_row: dict[str, Any], bundle: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.extend(registry_row.get("scenario_tags") or [])
    parts.extend(flatten_strings(((bundle.get("authoritative_layer") or {}).get("visible_profile")) or {}))
    parts.extend(flatten_strings(((bundle.get("authoritative_layer") or {}).get("hidden_profile")) or {}))
    parts.extend(flatten_strings(((bundle.get("authoritative_layer") or {}).get("patient_formulation")) or {}))
    return " ".join(parts)


def applicable_families(registry_row: dict[str, Any], bundle: dict[str, Any]) -> list[str]:
    hidden = ((bundle.get("authoritative_layer") or {}).get("hidden_profile")) or {}
    text_blob = case_text_blob(registry_row, bundle)
    families = ["supportive_exploration"]

    if any(keyword in text_blob for keyword in COGNITIVE_KEYWORDS):
        families.append("cognitive_reframing_probe")
    if any(keyword in text_blob for keyword in BELIEF_KEYWORDS):
        families.append("rebt_belief_work")
    if flatten_strings(hidden.get("rupture_triggers") or []) or registry_row.get("difficulty_band") == "high":
        families.append("rupture_and_repair")
    if registry_row.get("risk_band") in {"elevated", "high"} or any(keyword in text_blob for keyword in RISK_KEYWORDS):
        families.append("risk_escalation_and_handoff")
    if registry_row.get("difficulty_band") == "high" or registry_row.get("risk_band") in {"elevated", "high"}:
        families.append("review_and_revision")

    return uniq_keep_order(families)


def scenario_brief(family: str, registry_row: dict[str, Any]) -> str:
    title = clean_text(registry_row.get("case_title"), 120)
    if family == "supportive_exploration":
        return f"围绕 `{title}` 的支持性探索场景，目标是安全提升披露和联盟。"
    if family == "cognitive_reframing_probe":
        return f"围绕 `{title}` 的认知重评探针场景，目标是表露更明确的担忧/解释模式。"
    if family == "rebt_belief_work":
        return f"围绕 `{title}` 的信念工作场景，目标是渐进暴露灾难化或绝对化信念。"
    if family == "rupture_and_repair":
        return f"围绕 `{title}` 的破裂-修复场景，目标是测试触发点与修复能力。"
    if family == "risk_escalation_and_handoff":
        return f"围绕 `{title}` 的风险升级/转介场景，目标是保持对风险与边界的持续注意。"
    return f"围绕 `{title}` 的 review/revision 场景，用于审查 runtime 轨迹。"


def build_scenario_rows(case_rows: list[dict[str, Any]], bundle_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    family_counter: Counter[str] = Counter()
    pending_by_family: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for registry_row in case_rows:
        case_id = str(registry_row.get("case_id"))
        bundle = bundle_map[case_id]
        for family in applicable_families(registry_row, bundle):
            simulation_mode = "simulation_reviewer" if family == "review_and_revision" else "patient_simulator"
            task_id = f"therapist_task::{family}"
            scenario_id = f"scenario::{family}::{stable_hash(case_id + '::' + family)}"
            pending_by_family[family].append(
                {
                    "schema_version": "theraskill_patient_scenario_registry_entry_v1",
                    "scenario_id": scenario_id,
                    "simulation_mode": simulation_mode,
                    "scenario_family": family,
                    "patient_preset_id": f"patient_preset::{case_id}",
                    "therapist_task_id": task_id,
                    "hidden_profile_reveal_id": f"hidden_reveal::{case_id}",
                    "case_id": case_id,
                    "case_bundle_path": registry_row.get("profile_bundle_ref", {}).get("path"),
                    "case_family": registry_row.get("case_family"),
                    "risk_band": registry_row.get("risk_band"),
                    "difficulty_band": registry_row.get("difficulty_band"),
                    "scenario_tags": registry_row.get("scenario_tags") or [],
                    "entry_state": registry_row.get("initial_session_state") or {},
                    "recommended_turn_budget": next(
                        spec["recommended_turn_budget"]
                        for spec in THERAPIST_TASK_SPECS
                        if spec["scenario_family"] == family
                    ),
                    "success_criteria": next(
                        spec["success_criteria"] for spec in THERAPIST_TASK_SPECS if spec["scenario_family"] == family
                    ),
                    "failure_taxonomy_labels": next(
                        spec["failure_taxonomy_labels"]
                        for spec in THERAPIST_TASK_SPECS
                        if spec["scenario_family"] == family
                    ),
                    "scenario_brief": scenario_brief(family, registry_row),
                    "applicable_runtime_surface": "patient_single_session_loop_v1" if simulation_mode == "patient_simulator" else "patient_runtime_reviewer_v1",
                }
            )

    for family, family_rows in pending_by_family.items():
        family_rows.sort(key=lambda row: row["case_id"])
        total = len(family_rows)
        for rank, row in enumerate(family_rows):
            protocol_split = protocol_split_for(row["scenario_id"], rank, {family: total}, family)
            row["split_labels"] = {
                "formal_split": formal_split_for(row["scenario_id"]),
                SIMULATION_SCENARIO_PROTOCOL_SPLIT_KEY: protocol_split,
                SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_KEY: legacy_pilot_split_label(protocol_split),
            }
            rows.append(row)
            family_counter[family] += 1
    rows.sort(key=lambda row: (row["scenario_family"], row["case_id"]))
    return rows


def build_evaluator_schema() -> dict[str, Any]:
    return {
        "schema_version": "theraskill_patient_scenario_evaluator_io_schema_v1",
        "evaluator_input": {
            "required_fields": [
                "scenario_id",
                "simulation_mode",
                "scenario_family",
                "patient_preset_id",
                "therapist_task_id",
                "transcript",
                "turn_records",
                "final_session_state",
                "session_longitudinal_summary",
                "reviewer_result",
                "success_criteria",
                "failure_taxonomy_labels",
            ],
            "notes": [
                "Runtime evaluator should consume scenario metadata plus the patient session loop outputs.",
                "Evaluator input is task surface, not raw knowledge unit text.",
            ],
        },
        "evaluator_output": {
            "required_fields": [
                "scenario_id",
                "run_id",
                "gate_status",
                "criteria_pass_vector",
                "failure_taxonomy_observed",
                "reviewer_alignment",
                "notes",
            ],
            "notes": [
                "gate_status should align with session reviewer but may add evaluator-side disagreement analysis.",
                "failure taxonomy labels should come from scenario-specific allowed label set.",
            ],
        },
    }


def main() -> int:
    args = parse_args()
    output_paths = (
        Path(args.patient_presets),
        Path(args.hidden_reveals),
        Path(args.therapist_tasks),
        Path(args.scenario_registry),
        Path(args.evaluator_schema),
        Path(args.split_manifest),
        Path(args.manifest),
        Path(args.qa_report),
    )
    for path in output_paths:
        ensure_output_path(path, overwrite=args.overwrite)

    case_rows = read_jsonl(args.case_registry)
    if args.limit_cases is not None:
        case_rows = case_rows[: args.limit_cases]

    bundle_map: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for row in case_rows:
        case_id = str(row.get("case_id"))
        bundle_path = Path(row.get("profile_bundle_ref", {}).get("path") or "")
        if not bundle_path.exists():
            issues.append({"issue": "missing_case_bundle", "case_id": case_id, "path": str(bundle_path)})
            continue
        bundle_map[case_id] = read_json(bundle_path)

    case_rows = [row for row in case_rows if str(row.get("case_id")) in bundle_map]

    patient_presets = [build_patient_preset(row, bundle_map[str(row.get("case_id"))]) for row in case_rows]
    hidden_reveals = [build_hidden_reveal_entry(row, bundle_map[str(row.get("case_id"))]) for row in case_rows]
    therapist_tasks = build_therapist_task_rows()
    scenarios = build_scenario_rows(case_rows, bundle_map)
    evaluator_schema = build_evaluator_schema()

    presets_sha = write_jsonl(
        args.patient_presets,
        patient_presets,
        overwrite=args.overwrite,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )
    reveals_sha = write_jsonl(
        args.hidden_reveals,
        hidden_reveals,
        overwrite=args.overwrite,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )
    tasks_sha = write_jsonl(
        args.therapist_tasks,
        therapist_tasks,
        overwrite=args.overwrite,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )
    scenarios_sha = write_jsonl(
        args.scenario_registry,
        scenarios,
        overwrite=args.overwrite,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )
    write_json(args.evaluator_schema, evaluator_schema, overwrite=args.overwrite)

    family_counter: Counter[str] = Counter()
    formal_split_counter: Counter[str] = Counter()
    protocol_split_counter: Counter[str] = Counter()
    legacy_pilot_split_counter: Counter[str] = Counter()
    mode_counter: Counter[str] = Counter()
    for row in scenarios:
        family_counter[row["scenario_family"]] += 1
        mode_counter[row["simulation_mode"]] += 1
        splits = row.get("split_labels") or {}
        formal_split_counter[splits.get("formal_split") or "unknown"] += 1
        protocol_split_counter[splits.get(SIMULATION_SCENARIO_PROTOCOL_SPLIT_KEY) or "unknown"] += 1
        legacy_pilot_split_counter[splits.get(SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_KEY) or "unknown"] += 1

    split_manifest = {
        "schema_version": "theraskill_patient_scenario_split_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not issues else "incomplete",
        "patient_preset_registry": {
            "path": str(args.patient_presets),
            "rows": len(patient_presets),
            "sha256": presets_sha,
        },
        "therapist_task_registry": {
            "path": str(args.therapist_tasks),
            "rows": len(therapist_tasks),
            "sha256": tasks_sha,
        },
        "hidden_profile_reveal_registry": {
            "path": str(args.hidden_reveals),
            "rows": len(hidden_reveals),
            "sha256": reveals_sha,
        },
        "scenario_registry": {
            "path": str(args.scenario_registry),
            "rows": len(scenarios),
            "sha256": scenarios_sha,
        },
        "formal_split_distribution": dict(sorted(formal_split_counter.items())),
        "protocol_split_distribution": dict(sorted(protocol_split_counter.items())),
        "pilot_split_distribution": dict(sorted(legacy_pilot_split_counter.items())),
        "protocol_split_field_name": SIMULATION_SCENARIO_PROTOCOL_SPLIT_KEY,
        "legacy_pilot_split_field_name": SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_KEY,
        "scenario_family_distribution": dict(sorted(family_counter.items())),
        "simulation_mode_distribution": dict(sorted(mode_counter.items())),
        "issues_count": len(issues),
    }
    write_json(args.split_manifest, split_manifest, overwrite=args.overwrite)

    manifest = {
        "schema_version": "theraskill_patient_scenario_harness_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": split_manifest["status"],
        "inputs": {
            "case_registry": {"path": str(args.case_registry), "sha256": file_sha256(args.case_registry)},
            "bundle_dir": {"path": str(args.bundle_dir), "sha256": None},
        },
        "outputs": {
            "patient_presets": {"path": str(args.patient_presets), "sha256": presets_sha},
            "therapist_tasks": {"path": str(args.therapist_tasks), "sha256": tasks_sha},
            "hidden_reveals": {"path": str(args.hidden_reveals), "sha256": reveals_sha},
            "scenario_registry": {"path": str(args.scenario_registry), "sha256": scenarios_sha},
            "split_manifest": {"path": str(args.split_manifest), "sha256": file_sha256(args.split_manifest)},
            "evaluator_schema": {"path": str(args.evaluator_schema), "sha256": file_sha256(args.evaluator_schema)},
        },
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest, overwrite=args.overwrite)

    qa = {
        "schema_version": "theraskill_patient_scenario_harness_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": split_manifest["status"],
        "patient_preset_rows": len(patient_presets),
        "therapist_task_rows": len(therapist_tasks),
        "hidden_reveal_rows": len(hidden_reveals),
        "scenario_rows": len(scenarios),
        "issues": issues,
        "issues_count": len(issues),
        "missing_family_coverage": [
            family for family in [spec["scenario_family"] for spec in THERAPIST_TASK_SPECS] if family_counter[family] <= 0
        ],
    }
    if qa["missing_family_coverage"]:
        qa["status"] = "incomplete"
        qa["issues"].append({"issue": "missing_family_coverage", "families": qa["missing_family_coverage"]})
        qa["issues_count"] = len(qa["issues"])
    write_json(args.qa_report, qa, overwrite=args.overwrite)

    print(
        json.dumps(
            {
                "status": qa["status"],
                "patient_preset_rows": len(patient_presets),
                "scenario_rows": len(scenarios),
                "issues_count": qa["issues_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if qa["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
