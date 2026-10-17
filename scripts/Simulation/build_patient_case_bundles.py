#!/usr/bin/env python3
"""Build per-case patient simulator bundles from released simulation knowledge."""

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
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, file_sha256, read_jsonl, write_json
from simulation_surface_registry import (
    ANNA_HIDDEN_PATIENT_PROFILE_PATTERN_PATH,
    ANNA_HISTORICAL_CASE_MEMORY_SEED_PATH,
    ANNA_PATIENT_PERSONA_PROFILE_PATH,
    ANNA_REPORT_TO_CONVERSATION_BRIDGE_PATTERN_PATH,
    BUILD_PATIENT_CASE_BUNDLES_RELPATH,
    SIMULATION_PATIENT_CASE_BUNDLES_MANIFEST_PATH,
    SIMULATION_PATIENT_CASE_BUNDLES_QA_PATH,
    SIMULATION_PATIENT_CASE_BUNDLES_SUMMARY_PATH,
    SIMULATION_PATIENT_CASE_REGISTRY_PATH,
    SIMULATION_PROTOCOL_PATTERN_PATH,
    SIMULATION_RUNTIME_PATIENT_CASES_DIR,
    SIMULATION_THERAPIST_CASE_MEMORY_PATTERN_PATH,
    SIMULATION_TRANSITION_PATTERN_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_REGISTRY = SIMULATION_PATIENT_CASE_REGISTRY_PATH
DEFAULT_PERSONA = ANNA_PATIENT_PERSONA_PROFILE_PATH
DEFAULT_HIDDEN = ANNA_HIDDEN_PATIENT_PROFILE_PATTERN_PATH
DEFAULT_MEMORY = ANNA_HISTORICAL_CASE_MEMORY_SEED_PATH
DEFAULT_BRIDGE = ANNA_REPORT_TO_CONVERSATION_BRIDGE_PATTERN_PATH
DEFAULT_TRANSITIONS = SIMULATION_TRANSITION_PATTERN_PATH
DEFAULT_THERAPIST_MEMORY = SIMULATION_THERAPIST_CASE_MEMORY_PATTERN_PATH
DEFAULT_PROTOCOL = SIMULATION_PROTOCOL_PATTERN_PATH

DEFAULT_BUNDLE_DIR = SIMULATION_RUNTIME_PATIENT_CASES_DIR
DEFAULT_MANIFEST = SIMULATION_PATIENT_CASE_BUNDLES_MANIFEST_PATH
DEFAULT_QA = SIMULATION_PATIENT_CASE_BUNDLES_QA_PATH
DEFAULT_SUMMARY = SIMULATION_PATIENT_CASE_BUNDLES_SUMMARY_PATH


CASE_KEYWORD_EXPANSIONS = {
    "家庭关系": ["家庭", "亲子", "婚姻", "沟通"],
    "青少年心理": ["青少年", "孩子", "学习", "学业"],
    "婚恋情感": ["伴侣", "婚姻", "亲密关系", "沟通"],
    "焦虑": ["焦虑", "压力", "担忧"],
    "抑郁": ["抑郁", "低落", "退缩"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-jsonl", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--persona-jsonl", default=str(DEFAULT_PERSONA))
    parser.add_argument("--hidden-jsonl", default=str(DEFAULT_HIDDEN))
    parser.add_argument("--memory-jsonl", default=str(DEFAULT_MEMORY))
    parser.add_argument("--bridge-jsonl", default=str(DEFAULT_BRIDGE))
    parser.add_argument("--transition-jsonl", default=str(DEFAULT_TRANSITIONS))
    parser.add_argument("--therapist-memory-jsonl", default=str(DEFAULT_THERAPIST_MEMORY))
    parser.add_argument("--protocol-jsonl", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--bundle-dir", default=str(DEFAULT_BUNDLE_DIR))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit-cases", type=int)
    return parser.parse_args()


def clean_text(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] if limit is not None else text


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


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def index_by_source_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = str((row.get("source") or {}).get("source_id") or "").strip()
        if source_id:
            index[source_id] = row
    return index


def protocol_ids_by_mode(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        pattern = ((row.get("content") or {}).get("simulation_protocol_pattern")) or {}
        mode = clean_text(pattern.get("simulation_mode"), 80)
        unit_id = clean_text(row.get("unit_id"), 120)
        if mode and unit_id:
            grouped.setdefault(mode, []).append(unit_id)
    return grouped


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


def build_case_terms(registry_row: dict[str, Any], persona: dict[str, Any], hidden: dict[str, Any], memory: dict[str, Any], bridge: dict[str, Any]) -> list[str]:
    tags = list(registry_row.get("scenario_tags") or [])
    terms = list(tags)
    terms.extend(flatten_strings(persona.get("presenting_problem_profile") or []))
    terms.extend(flatten_strings(persona.get("symptom_profile") or []))
    terms.extend(flatten_strings(persona.get("emotional_profile") or []))
    terms.extend(flatten_strings(hidden.get("latent_core_conflicts") or []))
    terms.extend(flatten_strings(hidden.get("surface_presenting_story") or []))
    terms.extend(flatten_strings(memory.get("relationship_context") or []))
    terms.extend(flatten_strings(memory.get("unresolved_threads") or []))
    terms.extend(flatten_strings(bridge.get("stable_themes_across_report_and_dialogue") or []))
    for item in registry_row.get("case_categories") or []:
        terms.extend(CASE_KEYWORD_EXPANSIONS.get(str(item), []))
    if registry_row.get("risk_band") == "elevated":
        terms.extend(["风险", "抑郁", "低落", "压力"])
    return uniq_keep_order(terms)


def score_text_match(case_terms: list[str], text: str) -> int:
    score = 0
    haystack = clean_text(text, 4000)
    for term in case_terms:
        if term and term in haystack:
            score += max(1, min(4, len(term) // 4))
    return score


def select_transition_hints(case_terms: list[str], rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, row in enumerate(rows):
        pattern = ((row.get("content") or {}).get("transition_pattern")) or {}
        text_parts = flatten_strings(pattern.get("applicable_context"))
        text_parts.extend(flatten_strings((pattern.get("interaction_context") or {}).get("case_context_summary")))
        text_parts.extend(flatten_strings((pattern.get("starting_state_profile") or {}).get("presenting_problem_state")))
        text_parts.extend(flatten_strings(pattern.get("turning_point_signals")))
        text_parts.extend(flatten_strings((pattern.get("therapist_move_conditions") or {}).get("move_types")))
        score = score_text_match(case_terms, " ".join(text_parts))
        shift_type = clean_text(pattern.get("state_shift_type"), 80)
        if shift_type == "disclosure_increase":
            score += 1
        scored.append((score, -idx, row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen = [row for score, _, row in scored if score > 0][:limit]
    if len(chosen) < limit:
        fallback = [
            row
            for score, _, row in scored
            if ((row.get("content") or {}).get("transition_pattern") or {}).get("state_shift_type") == "disclosure_increase"
        ]
        for row in fallback:
            if row not in chosen:
                chosen.append(row)
            if len(chosen) >= limit:
                break
    return chosen[:limit]


def select_therapist_memory_hints(case_terms: list[str], rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, row in enumerate(rows):
        pattern = ((row.get("content") or {}).get("therapist_case_memory_pattern")) or {}
        text_parts = []
        text_parts.extend(flatten_strings(pattern.get("problem_summary_slots")))
        text_parts.extend(flatten_strings(pattern.get("resource_slots")))
        text_parts.extend(flatten_strings(pattern.get("unfinished_thread_slots")))
        text_parts.extend(flatten_strings(pattern.get("alliance_slots")))
        score = score_text_match(case_terms, " ".join(text_parts))
        scored.append((score, -idx, row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen = [row for score, _, row in scored if score > 0][:limit]
    if len(chosen) < limit:
        for _, _, row in scored:
            if row not in chosen:
                chosen.append(row)
            if len(chosen) >= limit:
                break
    return chosen[:limit]


def derive_patient_formulation(persona: dict[str, Any], hidden: dict[str, Any], memory: dict[str, Any], bridge: dict[str, Any], source_unit_ids: dict[str, Any]) -> dict[str, Any]:
    maintaining_factors = []
    maintaining_factors.extend(hidden.get("latent_core_conflicts") or [])
    maintaining_factors.extend(memory.get("relationship_context") or [])
    maintaining_factors.extend(bridge.get("mismatch_or_tension_signals") or [])
    suggested_focus = []
    suggested_focus.extend(memory.get("next_session_entry_points") or [])
    suggested_focus.extend(bridge.get("simulation_use_notes") or [])
    risk_factors = []
    risk_factors.extend(persona.get("distress_or_risk_flags") or [])
    risk_factors.extend(memory.get("risk_watchpoints") or [])
    return {
        "schema_version": "theraskill_patient_case_formulation_v1",
        "presenting_problems": uniq_keep_order(persona.get("presenting_problem_profile") or []),
        "trigger_or_context": uniq_keep_order(persona.get("interpersonal_context") or [])[:6],
        "maintaining_factors": uniq_keep_order(maintaining_factors)[:8],
        "protective_factors": uniq_keep_order(persona.get("protective_factor_profile") or memory.get("resource_signals") or [])[:8],
        "client_goals": uniq_keep_order(memory.get("next_session_entry_points") or persona.get("motivation_or_readiness_profile") or [])[:6],
        "risk_factors": uniq_keep_order(risk_factors)[:8],
        "suggested_focus": uniq_keep_order(suggested_focus)[:8],
        "evidence_source_unit_ids": list(source_unit_ids.values()),
        "derivation_note": "Deterministically assembled from Anna-CPsyCounD patient-side knowledge for runtime bootstrap; not an LLM-authored clinical formulation.",
    }


def derive_episodic_memory_seed(memory: dict[str, Any], bridge: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_identity_facts": uniq_keep_order(memory.get("stable_identity_facts") or [])[:6],
        "longitudinal_problem_summary": uniq_keep_order(memory.get("longitudinal_problem_summary") or [])[:6],
        "previously_used_techniques": uniq_keep_order(memory.get("previously_used_techniques") or [])[:6],
        "prior_counseling_progress": uniq_keep_order(memory.get("prior_counseling_progress") or [])[:6],
        "resource_signals": uniq_keep_order(memory.get("resource_signals") or [])[:6],
        "unresolved_threads": uniq_keep_order(memory.get("unresolved_threads") or [])[:6],
        "report_dialogue_alignment": uniq_keep_order(bridge.get("report_summary_to_dialogue_alignment") or [])[:6],
    }


def build_bundle(
    registry_row: dict[str, Any],
    persona_row: dict[str, Any],
    hidden_row: dict[str, Any],
    memory_row: dict[str, Any],
    bridge_row: dict[str, Any],
    transition_hints: list[dict[str, Any]],
    therapist_memory_hints: list[dict[str, Any]],
    protocol_by_mode: dict[str, list[str]],
) -> dict[str, Any]:
    case_id = clean_text(registry_row.get("case_id"), 120)
    source_id = clean_text(registry_row.get("source_id"), 160)
    source_unit_ids = registry_row.get("source_unit_ids") or {}

    persona = persona_row.get("content") or {}
    hidden = hidden_row.get("content") or {}
    memory = memory_row.get("content") or {}
    bridge = bridge_row.get("content") or {}

    case_terms = build_case_terms(registry_row, persona, hidden, memory, bridge)
    patient_formulation = derive_patient_formulation(persona, hidden, memory, bridge, source_unit_ids)
    episodic_memory_seed = derive_episodic_memory_seed(memory, bridge)

    return {
        "schema_version": "theraskill_patient_case_bundle_v1",
        "bundle_id": clean_text(registry_row.get("bundle_id"), 120) or f"pcb_{stable_hash(source_id)}",
        "case_id": case_id,
        "source_id": source_id,
        "metadata": {
            "case_index": registry_row.get("case_index"),
            "case_title": registry_row.get("case_title"),
            "case_family": registry_row.get("case_family"),
            "case_categories": registry_row.get("case_categories"),
            "risk_band": registry_row.get("risk_band"),
            "difficulty_band": registry_row.get("difficulty_band"),
            "allowed_session_count": registry_row.get("allowed_session_count"),
            "scenario_tags": registry_row.get("scenario_tags"),
        },
        "authoritative_layer": {
            "source_unit_ids": source_unit_ids,
            "visible_profile": persona,
            "hidden_profile": hidden,
            "continuity_seed": memory,
            "report_dialogue_bridge": bridge,
            "patient_formulation": patient_formulation,
        },
        "runtime_priors": {
            "patient_protocol_unit_ids": protocol_by_mode.get("patient_simulator") or [],
            "reviewer_protocol_unit_ids": protocol_by_mode.get("simulation_reviewer") or [],
            "state_transition_hint_unit_ids": [row.get("unit_id") for row in transition_hints],
            "therapist_case_memory_hint_unit_ids": [row.get("unit_id") for row in therapist_memory_hints],
            "retrieval_seed_terms": case_terms[:24],
            "suggested_therapist_task_focus": uniq_keep_order(memory.get("next_session_entry_points") or bridge.get("simulation_use_notes") or [])[:6],
        },
        "runtime_state_seed": {
            "initial_session_state": registry_row.get("initial_session_state") or {},
            "episodic_memory_seed": episodic_memory_seed,
            "session_bootstrap_notes": uniq_keep_order(bridge.get("simulation_use_notes") or [])[:6],
        },
        "provenance": {
            "generated_by": str(BUILD_PATIENT_CASE_BUNDLES_RELPATH),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "derivation_policy": "deterministic_runtime_bootstrap_from_released_knowledge_v1",
        },
    }


def main() -> int:
    args = parse_args()
    bundle_dir = Path(args.bundle_dir)
    if bundle_dir.exists() and any(bundle_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"bundle directory is not empty: {bundle_dir}")
    for path in (Path(args.summary), Path(args.manifest), Path(args.qa_report)):
        ensure_output_path(path, overwrite=args.overwrite)

    registry_rows = read_jsonl(args.registry_jsonl)
    if args.limit_cases is not None:
        registry_rows = registry_rows[: args.limit_cases]

    persona_map = index_by_source_id(read_jsonl(args.persona_jsonl))
    hidden_map = index_by_source_id(read_jsonl(args.hidden_jsonl))
    memory_map = index_by_source_id(read_jsonl(args.memory_jsonl))
    bridge_map = index_by_source_id(read_jsonl(args.bridge_jsonl))
    transition_rows = read_jsonl(args.transition_jsonl)
    therapist_memory_rows = read_jsonl(args.therapist_memory_jsonl)
    protocol_by_mode = protocol_ids_by_mode(read_jsonl(args.protocol_jsonl))

    issues: list[dict[str, Any]] = []
    bundle_paths: list[str] = []
    risk_counter: Counter[str] = Counter()
    difficulty_counter: Counter[str] = Counter()

    bundle_dir.mkdir(parents=True, exist_ok=True)

    for registry_row in registry_rows:
        source_id = clean_text(registry_row.get("source_id"), 160)
        case_id = clean_text(registry_row.get("case_id"), 120)
        persona_row = persona_map.get(source_id)
        hidden_row = hidden_map.get(source_id)
        memory_row = memory_map.get(source_id)
        bridge_row = bridge_map.get(source_id)
        if not all([persona_row, hidden_row, memory_row, bridge_row]):
            issues.append({"issue": "missing_source_rows_for_bundle", "source_id": source_id, "case_id": case_id})
            continue

        persona = persona_row.get("content") or {}
        hidden = hidden_row.get("content") or {}
        memory = memory_row.get("content") or {}
        bridge = bridge_row.get("content") or {}
        case_terms = build_case_terms(registry_row, persona, hidden, memory, bridge)
        transition_hints = select_transition_hints(case_terms, transition_rows, limit=8)
        therapist_memory_hints = select_therapist_memory_hints(case_terms, therapist_memory_rows, limit=3)

        if not transition_hints:
            issues.append({"issue": "missing_transition_hints", "source_id": source_id, "case_id": case_id})
        if not therapist_memory_hints:
            issues.append({"issue": "missing_therapist_memory_hints", "source_id": source_id, "case_id": case_id})

        bundle = build_bundle(
            registry_row,
            persona_row,
            hidden_row,
            memory_row,
            bridge_row,
            transition_hints,
            therapist_memory_hints,
            protocol_by_mode,
        )
        bundle_path = bundle_dir / f"{case_id}.json"
        write_json(bundle_path, bundle, overwrite=args.overwrite)
        bundle_paths.append(str(bundle_path))
        risk_counter[str((bundle.get("metadata") or {}).get("risk_band") or "unknown")] += 1
        difficulty_counter[str((bundle.get("metadata") or {}).get("difficulty_band") or "unknown")] += 1

    manifest_status = "complete" if not issues else "incomplete"
    summary = {
        "schema_version": "theraskill_patient_case_bundles_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bundle_count": len(bundle_paths),
        "bundle_dir": str(bundle_dir),
        "risk_band_distribution": dict(sorted(risk_counter.items())),
        "difficulty_band_distribution": dict(sorted(difficulty_counter.items())),
        "issues_count": len(issues),
    }
    write_json(args.summary, summary, overwrite=args.overwrite)

    manifest = {
        "schema_version": "theraskill_patient_case_bundles_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest_status,
        "bundle_dir": {"path": str(bundle_dir)},
        "bundle_count": len(bundle_paths),
        "summary": {"path": str(args.summary), "sha256": file_sha256(args.summary)},
        "inputs": {
            "registry_jsonl": {"path": str(args.registry_jsonl), "sha256": file_sha256(args.registry_jsonl)},
            "persona_jsonl": {"path": str(args.persona_jsonl), "sha256": file_sha256(args.persona_jsonl)},
            "hidden_jsonl": {"path": str(args.hidden_jsonl), "sha256": file_sha256(args.hidden_jsonl)},
            "memory_jsonl": {"path": str(args.memory_jsonl), "sha256": file_sha256(args.memory_jsonl)},
            "bridge_jsonl": {"path": str(args.bridge_jsonl), "sha256": file_sha256(args.bridge_jsonl)},
            "transition_jsonl": {"path": str(args.transition_jsonl), "sha256": file_sha256(args.transition_jsonl)},
            "therapist_memory_jsonl": {"path": str(args.therapist_memory_jsonl), "sha256": file_sha256(args.therapist_memory_jsonl)},
            "protocol_jsonl": {"path": str(args.protocol_jsonl), "sha256": file_sha256(args.protocol_jsonl)},
        },
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest, overwrite=args.overwrite)

    qa = {
        "schema_version": "theraskill_patient_case_bundles_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest_status,
        "bundle_count": len(bundle_paths),
        "bundle_dir": str(bundle_dir),
        "issues": issues,
        "issues_count": len(issues),
        "sample_bundle_paths": bundle_paths[:5],
    }
    write_json(args.qa_report, qa, overwrite=args.overwrite)
    print(json.dumps({"status": manifest_status, "bundle_count": len(bundle_paths), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
