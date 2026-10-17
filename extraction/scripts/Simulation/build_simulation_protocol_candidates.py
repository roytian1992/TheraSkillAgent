#!/usr/bin/env python3
"""Build deterministic candidates for Simulation.simulation_protocol_pattern."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import file_sha256, read_json, read_jsonl, write_json, write_jsonl
from simulation_surface_registry import (
    SIMULATION_PATIENT_STATE_TRANSITION_PATTERN_PATH,
    SIMULATION_PATIENT_STATE_TRANSITION_PROJECTION_MANIFEST_PATH,
    SIMULATION_PROTOCOL_CANDIDATES_MANIFEST_PATH,
    SIMULATION_PROTOCOL_CANDIDATES_PATH,
    SIMULATION_PROTOCOL_CANDIDATES_QA_PATH,
    SIMULATION_THERAPIST_CASE_MEMORY_PATTERN_PATH,
    SIMULATION_THERAPIST_CASE_MEMORY_PROJECTION_MANIFEST_PATH,
    SIMULATION_THERAPIST_RESPONSE_POLICY_PATTERN_PATH,
    SIMULATION_THERAPIST_RESPONSE_POLICY_PROJECTION_MANIFEST_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PATIENT_UNITS = SIMULATION_PATIENT_STATE_TRANSITION_PATTERN_PATH
DEFAULT_PATIENT_MANIFEST = SIMULATION_PATIENT_STATE_TRANSITION_PROJECTION_MANIFEST_PATH
DEFAULT_RESPONSE_UNITS = SIMULATION_THERAPIST_RESPONSE_POLICY_PATTERN_PATH
DEFAULT_RESPONSE_MANIFEST = SIMULATION_THERAPIST_RESPONSE_POLICY_PROJECTION_MANIFEST_PATH
DEFAULT_MEMORY_UNITS = SIMULATION_THERAPIST_CASE_MEMORY_PATTERN_PATH
DEFAULT_MEMORY_MANIFEST = SIMULATION_THERAPIST_CASE_MEMORY_PROJECTION_MANIFEST_PATH
DEFAULT_OUTPUT = SIMULATION_PROTOCOL_CANDIDATES_PATH
DEFAULT_MANIFEST = SIMULATION_PROTOCOL_CANDIDATES_MANIFEST_PATH
DEFAULT_QA = SIMULATION_PROTOCOL_CANDIDATES_QA_PATH

PROTOCOL_SPECS = [
    {
        "simulation_mode": "patient_simulator",
        "protocol_family": "context_loading",
        "priority": "high",
        "runtime_surface": ["session_bootstrap", "pre_turn_context"],
        "sources": ["patient", "memory"],
    },
    {
        "simulation_mode": "patient_simulator",
        "protocol_family": "patient_state_visibility",
        "priority": "high",
        "runtime_surface": ["turn_state_rendering", "patient_response_generation"],
        "sources": ["patient", "memory"],
    },
    {
        "simulation_mode": "patient_simulator",
        "protocol_family": "hidden_state_update",
        "priority": "high",
        "runtime_surface": ["post_turn_state_update"],
        "sources": ["patient", "memory"],
    },
    {
        "simulation_mode": "patient_simulator",
        "protocol_family": "turn_management",
        "priority": "medium",
        "runtime_surface": ["turn_loop_control"],
        "sources": ["patient", "memory"],
    },
    {
        "simulation_mode": "therapist_simulator",
        "protocol_family": "context_loading",
        "priority": "high",
        "runtime_surface": ["session_bootstrap", "pre_turn_context"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "therapist_simulator",
        "protocol_family": "therapist_planning_visibility",
        "priority": "high",
        "runtime_surface": ["response_planning"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "therapist_simulator",
        "protocol_family": "memory_read",
        "priority": "medium",
        "runtime_surface": ["retrieval_augmented_planning"],
        "sources": ["response", "memory"],
    },
    {
        "simulation_mode": "therapist_simulator",
        "protocol_family": "retrieval_routing",
        "priority": "high",
        "runtime_surface": ["retrieval_router"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "therapist_simulator",
        "protocol_family": "turn_management",
        "priority": "high",
        "runtime_surface": ["turn_loop_control", "response_stop_gate"],
        "sources": ["patient", "response"],
    },
    {
        "simulation_mode": "therapist_simulator",
        "protocol_family": "escalation_and_handoff",
        "priority": "high",
        "runtime_surface": ["risk_monitoring", "handoff_gate"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "dual_session_simulator",
        "protocol_family": "context_loading",
        "priority": "high",
        "runtime_surface": ["session_bootstrap", "dual_context_sync"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "dual_session_simulator",
        "protocol_family": "hidden_state_update",
        "priority": "high",
        "runtime_surface": ["post_turn_state_update", "cross_role_state_sync"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "dual_session_simulator",
        "protocol_family": "memory_write",
        "priority": "high",
        "runtime_surface": ["memory_writeback"],
        "sources": ["patient", "memory"],
    },
    {
        "simulation_mode": "dual_session_simulator",
        "protocol_family": "memory_read",
        "priority": "medium",
        "runtime_surface": ["retrieval_augmented_generation"],
        "sources": ["response", "memory"],
    },
    {
        "simulation_mode": "dual_session_simulator",
        "protocol_family": "retrieval_routing",
        "priority": "high",
        "runtime_surface": ["retrieval_router", "knowledge_router"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "dual_session_simulator",
        "protocol_family": "turn_management",
        "priority": "high",
        "runtime_surface": ["turn_loop_control", "stage_transition_gate"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "dual_session_simulator",
        "protocol_family": "escalation_and_handoff",
        "priority": "high",
        "runtime_surface": ["risk_monitoring", "pause_or_handoff_gate"],
        "sources": ["patient", "response", "memory"],
    },
    {
        "simulation_mode": "simulation_reviewer",
        "protocol_family": "review_and_labeling",
        "priority": "high",
        "runtime_surface": ["review_pass", "labeling_pass"],
        "sources": ["patient", "response", "memory"],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patient-units", default=str(DEFAULT_PATIENT_UNITS))
    parser.add_argument("--patient-manifest", default=str(DEFAULT_PATIENT_MANIFEST))
    parser.add_argument("--response-units", default=str(DEFAULT_RESPONSE_UNITS))
    parser.add_argument("--response-manifest", default=str(DEFAULT_RESPONSE_MANIFEST))
    parser.add_argument("--memory-units", default=str(DEFAULT_MEMORY_UNITS))
    parser.add_argument("--memory-manifest", default=str(DEFAULT_MEMORY_MANIFEST))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--allow-partial-upstream", action="store_true")
    return parser.parse_args()

def clean_text(text: Any, limit: int | None = None) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] if limit is not None else value


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def uniq_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = clean_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def top_labels(counter: Counter[str], limit: int) -> list[str]:
    return [label for label, _ in counter.most_common(limit) if label and label != "unknown"]


def add_values(counter: Counter[str], values: Any) -> None:
    if isinstance(values, list):
        for value in values:
            label = clean_text(value, 220)
            if label:
                counter[label] += 1
        return
    label = clean_text(values, 220)
    if label:
        counter[label] += 1


def sample_unit_ids(rows: list[dict[str, Any]], limit: int) -> list[str]:
    out: list[str] = []
    for row in rows:
        unit_id = clean_text(row.get("unit_id"), 120)
        if unit_id:
            out.append(unit_id)
        if len(out) >= limit:
            break
    return out


def patient_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    context_counter = Counter()
    phase_counter = Counter()
    shift_counter = Counter()
    move_type_counter = Counter()
    move_function_counter = Counter()
    turning_counter = Counter()
    alliance_counter = Counter()
    readiness_counter = Counter()
    resistance_counter = Counter()
    risk_counter = Counter()

    for row in rows:
        content = ((row.get("content") or {}).get("transition_pattern")) or {}
        interaction_context = content.get("interaction_context") or {}
        starting = content.get("starting_state_profile") or {}
        next_state = content.get("next_state_profile") or {}
        move_conditions = content.get("therapist_move_conditions") or {}
        add_values(context_counter, content.get("applicable_context"))
        add_values(phase_counter, interaction_context.get("phase_hint"))
        add_values(shift_counter, content.get("state_shift_type"))
        add_values(move_type_counter, move_conditions.get("move_types"))
        add_values(move_function_counter, move_conditions.get("move_functions"))
        add_values(turning_counter, content.get("turning_point_signals"))
        add_values(alliance_counter, [starting.get("alliance_state"), next_state.get("alliance_state")])
        add_values(readiness_counter, [starting.get("readiness_state"), next_state.get("readiness_state")])
        add_values(resistance_counter, [starting.get("resistance_state"), next_state.get("resistance_state")])
        add_values(risk_counter, [starting.get("risk_state"), next_state.get("risk_state")])

    return {
        "row_count": len(rows),
        "sample_unit_ids": sample_unit_ids(rows, 8),
        "applicable_context": top_labels(context_counter, 12),
        "phase_hints": top_labels(phase_counter, 6),
        "state_shift_types": top_labels(shift_counter, 8),
        "move_types": top_labels(move_type_counter, 8),
        "move_functions": top_labels(move_function_counter, 8),
        "turning_signals": top_labels(turning_counter, 10),
        "alliance_states": top_labels(alliance_counter, 8),
        "readiness_states": top_labels(readiness_counter, 8),
        "resistance_states": top_labels(resistance_counter, 8),
        "risk_states": top_labels(risk_counter, 8),
    }


def response_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    move_type_counter = Counter()
    orientation_counter = Counter()
    tone_counter = Counter()
    risk_counter = Counter()
    stage_counter = Counter()
    goal_counter = Counter()
    avoid_counter = Counter()
    structure_counter = Counter()

    for row in rows:
        content = ((row.get("content") or {}).get("response_policy_pattern")) or {}
        add_values(move_type_counter, content.get("preferred_move_types"))
        add_values(orientation_counter, content.get("therapeutic_orientation"))
        add_values(tone_counter, content.get("tone_style_constraints"))
        add_values(risk_counter, content.get("risk_constraints"))
        add_values(stage_counter, content.get("stage_constraints"))
        add_values(goal_counter, content.get("primary_response_goal"))
        add_values(avoid_counter, content.get("things_to_avoid"))
        add_values(structure_counter, content.get("preferred_response_structure"))

    return {
        "row_count": len(rows),
        "sample_unit_ids": sample_unit_ids(rows, 8),
        "preferred_move_types": top_labels(move_type_counter, 8),
        "therapeutic_orientation": top_labels(orientation_counter, 8),
        "tone_styles": top_labels(tone_counter, 8),
        "risk_constraints": top_labels(risk_counter, 8),
        "stage_constraints": top_labels(stage_counter, 8),
        "response_goals": top_labels(goal_counter, 10),
        "things_to_avoid": top_labels(avoid_counter, 10),
        "response_structures": top_labels(structure_counter, 6),
    }


def memory_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    required_slots = Counter()
    problem_slots = Counter()
    resource_slots = Counter()
    goal_slots = Counter()
    alliance_slots = Counter()
    risk_slots = Counter()
    thread_slots = Counter()
    stage_slots = Counter()
    update_rules = Counter()
    compression_rules = Counter()
    forgetting_risks = Counter()
    handoff_rules = Counter()

    for row in rows:
        content = ((row.get("content") or {}).get("therapist_case_memory_pattern")) or {}
        add_values(required_slots, content.get("required_memory_slots"))
        add_values(problem_slots, content.get("problem_summary_slots"))
        add_values(resource_slots, content.get("resource_slots"))
        add_values(goal_slots, content.get("goal_slots"))
        add_values(alliance_slots, content.get("alliance_slots"))
        add_values(risk_slots, content.get("risk_slots"))
        add_values(thread_slots, content.get("unfinished_thread_slots"))
        add_values(stage_slots, content.get("stage_tracking_slots"))
        add_values(update_rules, content.get("memory_update_rules"))
        add_values(compression_rules, content.get("compression_rules"))
        add_values(forgetting_risks, content.get("forgetting_risks"))
        add_values(handoff_rules, content.get("handoff_or_resume_rules"))

    return {
        "row_count": len(rows),
        "sample_unit_ids": sample_unit_ids(rows, 8),
        "required_memory_slots": top_labels(required_slots, 16),
        "problem_summary_slots": top_labels(problem_slots, 10),
        "resource_slots": top_labels(resource_slots, 10),
        "goal_slots": top_labels(goal_slots, 10),
        "alliance_slots": top_labels(alliance_slots, 10),
        "risk_slots": top_labels(risk_slots, 8),
        "unfinished_thread_slots": top_labels(thread_slots, 10),
        "stage_tracking_slots": top_labels(stage_slots, 8),
        "memory_update_rules": top_labels(update_rules, 12),
        "compression_rules": top_labels(compression_rules, 12),
        "forgetting_risks": top_labels(forgetting_risks, 10),
        "handoff_or_resume_rules": top_labels(handoff_rules, 10),
    }


def upstream_info(name: str, units_path: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    rows = read_jsonl(units_path)
    output_units = manifest.get("output_units") or {}
    candidate_coverage = manifest.get("candidate_coverage") or {}
    return {
        "name": name,
        "units_path": str(units_path),
        "units_sha256": file_sha256(units_path),
        "units_row_count": len(rows),
        "manifest_path": str(manifest_path),
        "manifest_sha256": file_sha256(manifest_path),
        "manifest_status": manifest.get("status"),
        "manifest_output_row_count": output_units.get("row_count"),
        "candidate_coverage_full": candidate_coverage.get("full_coverage"),
        "rows": rows,
    }


def check_upstream_ready(info: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if info.get("manifest_status") != "complete":
        issues.append(f"{info['name']}:manifest_not_complete")
    if not info.get("candidate_coverage_full"):
        issues.append(f"{info['name']}:candidate_coverage_not_full")
    if int(info.get("units_row_count") or 0) <= 3:
        issues.append(f"{info['name']}:canonical_output_too_small")
    return issues


def fixed_required_slots(spec: dict[str, Any]) -> list[str]:
    family = spec["protocol_family"]
    mode = spec["simulation_mode"]
    slots = ["simulation_mode", "protocol_family", "source_evidence_ids"]
    family_slots = {
        "context_loading": [
            "presenting_problem_state",
            "phase_hint",
            "alliance_state",
            "readiness_state",
            "risk_state",
            "active_goals",
            "known_resources",
            "unfinished_threads",
        ],
        "patient_state_visibility": [
            "patient_visible_state",
            "disclosure_depth",
            "emotion_surface",
            "readiness_signal",
            "resistance_signal",
        ],
        "therapist_planning_visibility": [
            "current_client_state",
            "stage_constraint",
            "risk_constraint",
            "preferred_move_types",
            "response_goal",
        ],
        "hidden_state_update": [
            "hidden_state_snapshot",
            "alliance_timeline",
            "disclosure_timeline",
            "readiness_timeline",
            "risk_timeline",
        ],
        "memory_write": ["new_information_span", "memory_slot_name", "write_trigger", "write_priority"],
        "memory_read": ["query_state", "memory_slot_name", "retrieval_reason"],
        "retrieval_routing": ["query_state", "risk_state", "stage_hint", "knowledge_family_selector"],
        "turn_management": ["current_turn_goal", "stop_signal", "continue_signal", "stage_transition_signal"],
        "escalation_and_handoff": ["risk_state", "pause_signal", "handoff_signal", "resume_condition"],
        "review_and_labeling": ["review_target", "evidence_chain", "label_namespace", "rejection_rule"],
    }
    slots.extend(family_slots.get(family, []))
    if mode == "dual_session_simulator":
        slots.extend(["patient_visible_context", "therapist_visible_context", "shared_memory_handle"])
    return uniq_keep_order(slots)


def build_constraint_summary(spec: dict[str, Any], patient: dict[str, Any], response: dict[str, Any], memory: dict[str, Any]) -> str:
    family = spec["protocol_family"]
    pieces = {
        "context_loading": "Load compact structured context before a turn, keep patient-visible context separate from therapist-only memory, and prefer phase/risk/readiness summaries over long narrative replay.",
        "patient_state_visibility": "Expose only the patient-surface state needed for generation, while hiding therapist-only memory assessments and engineered protocol internals.",
        "therapist_planning_visibility": "Give the therapist simulator the minimum planning context needed to choose a response policy, including move options, stage constraints, and current client readiness/risk.",
        "hidden_state_update": "Update alliance, disclosure, readiness, and risk as hidden tracked state after each turn instead of flattening them into free-text summaries.",
        "memory_write": "Write new facts to memory only when they change goals, resources, stage, risk, or unresolved threads in a durable way.",
        "memory_read": "Read memory selectively when past goals, resources, unresolved threads, or stage/risk history materially change the current response choice.",
        "retrieval_routing": "Trigger retrieval only when current state, risk, or planning ambiguity requires outside knowledge support instead of retrieving by default every turn.",
        "turn_management": "Use explicit continue, stop, and stage-transition signals so the simulator can decide whether to probe, summarize, pause, or close the turn.",
        "escalation_and_handoff": "Keep risk escalation, pause, and handoff rules explicit and separate from normal supportive progression rules.",
        "review_and_labeling": "Review and label generated turns with traceable evidence, while rejecting unsupported inferences and any leakage from hidden reasoning fields.",
    }
    evidence_note = (
        f" Evidence is grounded in patient transitions ({patient['row_count']} units), "
        f"response policies ({response['row_count']} units), and case memory patterns ({memory['row_count']} units)."
    )
    return pieces[family] + evidence_note


def build_candidate(spec: dict[str, Any], patient: dict[str, Any], response: dict[str, Any], memory: dict[str, Any], versions: dict[str, Any]) -> dict[str, Any]:
    family = spec["protocol_family"]
    mode = spec["simulation_mode"]
    candidate_id = f"sppc_{stable_hash(mode + '::' + family)}"

    patient_visible = uniq_keep_order(
        patient["applicable_context"][:6]
        + patient["alliance_states"][:3]
        + patient["readiness_states"][:3]
        + patient["risk_states"][:2]
    )
    therapist_visible = uniq_keep_order(
        response["response_goals"][:5]
        + response["preferred_move_types"][:5]
        + response["therapeutic_orientation"][:5]
        + response["risk_constraints"][:3]
    )
    hidden_rules = uniq_keep_order(
        [
            "Track alliance, disclosure, readiness, and risk as hidden structured state rather than exposing them directly in patient-facing text.",
            "Preserve unresolved threads across turns even when they are not repeated verbatim.",
        ]
        + memory["memory_update_rules"][:6]
        + memory["compression_rules"][:4]
        + patient["state_shift_types"][:4]
    )
    memory_write_rules = uniq_keep_order(
        [
            "Write memory when new goals, resources, risk cues, or stage transitions are observed.",
            "Do not write single-turn decorative details unless they affect later planning.",
        ]
        + memory["memory_update_rules"][:8]
        + memory["compression_rules"][:4]
    )
    memory_read_rules = uniq_keep_order(
        [
            "Read memory before planning when unresolved threads, prior commitments, or resource changes matter to the next response.",
            "Prefer targeted slot reads over full-history replay.",
        ]
        + memory["required_memory_slots"][:8]
        + memory["unfinished_thread_slots"][:4]
    )
    retrieval_triggers = uniq_keep_order(
        [
            "Trigger retrieval when current client state is ambiguous relative to stage, risk, or intervention choice.",
            "Trigger retrieval when prior goals/resources are needed to avoid generic responses.",
        ]
        + patient["turning_signals"][:4]
        + response["risk_constraints"][:3]
        + memory["forgetting_risks"][:4]
    )
    turn_rules = uniq_keep_order(
        [
            "Continue the turn when disclosure deepens, new problem detail appears, or readiness is still forming.",
            "End or summarize the turn when a concrete next step, stage handoff, or explicit pause condition is reached.",
        ]
        + patient["turning_signals"][:6]
        + response["things_to_avoid"][:4]
    )
    escalation_rules = uniq_keep_order(
        [
            "Escalate or pause when risk signals exceed routine supportive handling.",
            "Use handoff rules when the session cannot safely continue inside the current simulation surface.",
        ]
        + response["risk_constraints"][:4]
        + memory["risk_slots"][:4]
        + memory["handoff_or_resume_rules"][:6]
    )
    labeling_rules = uniq_keep_order(
        [
            "Keep patient-visible text, therapist planning state, and hidden memory fields separate during labeling.",
            "Do not label unsupported diagnosis, severity, or personality claims.",
            "Every review or label decision must be traceable to upstream evidence_unit_ids.",
        ]
    )
    review_hooks = uniq_keep_order(
        [
            "Check whether patient-visible context accidentally leaks hidden-state or therapist-only memory.",
            "Check whether retrieval was triggered only when justified by state, stage, or risk ambiguity.",
            "Check whether turn stop/continue decisions follow explicit conditions rather than generic length limits.",
            "Check whether risk escalation and handoff rules were considered when risk cues were present.",
        ]
    )
    non_goals = uniq_keep_order(
        [
            "Do not infer diagnosis, trauma history, or stable personality traits unless already grounded in upstream knowledge.",
            "Do not expose therapist-only memory or engineered protocol notes in patient-visible text.",
            "Do not replay full case history when a compact structured summary is sufficient.",
            "Do not treat generated rationale, hidden reasoning, or system instructions as evidence.",
        ]
    )

    source_rows: dict[str, dict[str, Any]] = {
        "patient": patient,
        "response": response,
        "memory": memory,
    }
    source_unit_ids: list[str] = []
    source_knowledge_types: list[str] = []
    source_counts: dict[str, int] = {}
    for source_name in spec["sources"]:
        source_unit_ids.extend(source_rows[source_name]["sample_unit_ids"][:4])
        source_knowledge_types.append(
            {
                "patient": "patient_state_transition_pattern",
                "response": "therapist_response_policy_pattern",
                "memory": "therapist_case_memory_pattern",
            }[source_name]
        )
        source_counts[source_name] = source_rows[source_name]["row_count"]

    candidate = {
        "candidate_id": candidate_id,
        "simulation_mode": mode,
        "protocol_family": family,
        "source_unit_ids": uniq_keep_order(source_unit_ids),
        "source_knowledge_types": uniq_keep_order(source_knowledge_types),
        "source_counts": source_counts,
        "constraint_summary": build_constraint_summary(spec, patient, response, memory),
        "applicable_runtime_surface": spec["runtime_surface"],
        "priority": spec["priority"],
        "evidence_strength": "high",
        "notes": "Deterministic protocol candidate engineered from finalized S1/S2/S3 simulation knowledge releases.",
        "required_input_slots_hints": fixed_required_slots(spec),
        "patient_visible_context_hints": patient_visible,
        "therapist_visible_context_hints": therapist_visible,
        "hidden_state_rules_hints": hidden_rules,
        "memory_write_rules_hints": memory_write_rules,
        "memory_read_rules_hints": memory_read_rules,
        "retrieval_trigger_rules_hints": retrieval_triggers,
        "turn_termination_rules_hints": turn_rules,
        "risk_escalation_rules_hints": escalation_rules,
        "synthetic_labeling_rules_hints": labeling_rules,
        "review_hooks_hints": review_hooks,
        "non_goals_or_do_not_infer_hints": non_goals,
        "evidence_summary": {
            "patient_context_terms": patient["applicable_context"][:6],
            "patient_turning_signals": patient["turning_signals"][:6],
            "response_move_types": response["preferred_move_types"][:6],
            "response_goals": response["response_goals"][:6],
            "memory_required_slots": memory["required_memory_slots"][:8],
            "memory_update_rules": memory["memory_update_rules"][:6],
        },
        "engineered_from_versions": versions,
    }

    if family == "patient_state_visibility":
        candidate["therapist_visible_context_hints"] = []
        candidate["memory_read_rules_hints"] = []
    if family == "review_and_labeling":
        candidate["patient_visible_context_hints"] = []
        candidate["therapist_visible_context_hints"] = []
        candidate["hidden_state_rules_hints"] = []

    return candidate


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)

    if args.overwrite:
        for path in (output_path, manifest_path, qa_path):
            if path.exists():
                path.unlink()

    upstream = {
        "patient": upstream_info("patient_state_transition", Path(args.patient_units), Path(args.patient_manifest)),
        "response": upstream_info("therapist_response_policy", Path(args.response_units), Path(args.response_manifest)),
        "memory": upstream_info("therapist_case_memory", Path(args.memory_units), Path(args.memory_manifest)),
    }

    upstream_issues: list[str] = []
    for info in upstream.values():
        upstream_issues.extend(check_upstream_ready(info))
    if upstream_issues and not args.allow_partial_upstream:
        qa = {
            "schema_version": "theraskill_simulation_protocol_candidates_qa_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "blocked_upstream_not_ready",
            "issues": [{"issue": issue} for issue in upstream_issues],
            "issues_count": len(upstream_issues),
        }
        write_json(qa_path, qa)
        manifest = {
            "schema_version": "theraskill_simulation_protocol_candidates_manifest_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "blocked_upstream_not_ready",
            "output_candidates": {"path": str(output_path), "sha256": file_sha256(output_path), "row_count": 0},
            "qa_report": str(qa_path),
            "upstream_inputs": {
                name: {
                    "units_path": info["units_path"],
                    "units_sha256": info["units_sha256"],
                    "units_row_count": info["units_row_count"],
                    "manifest_path": info["manifest_path"],
                    "manifest_sha256": info["manifest_sha256"],
                    "manifest_status": info["manifest_status"],
                    "candidate_coverage_full": info["candidate_coverage_full"],
                }
                for name, info in upstream.items()
            },
        }
        write_json(manifest_path, manifest)
        print(json.dumps({"status": manifest["status"], "issues_count": len(upstream_issues)}, ensure_ascii=False, indent=2))
        return 1

    patient = patient_summary(upstream["patient"]["rows"])
    response = response_summary(upstream["response"]["rows"])
    memory = memory_summary(upstream["memory"]["rows"])
    versions = {
        name: {
            "units_sha256": info["units_sha256"],
            "units_row_count": info["units_row_count"],
            "manifest_sha256": info["manifest_sha256"],
            "manifest_status": info["manifest_status"],
        }
        for name, info in upstream.items()
    }

    candidates = [build_candidate(spec, patient, response, memory, versions) for spec in PROTOCOL_SPECS]
    duplicate_counts = Counter(row["candidate_id"] for row in candidates)
    duplicates = [candidate_id for candidate_id, count in duplicate_counts.items() if count > 1]
    missing_source = [row["candidate_id"] for row in candidates if not row.get("source_unit_ids")]
    family_distribution = Counter(row["protocol_family"] for row in candidates)
    mode_distribution = Counter(row["simulation_mode"] for row in candidates)
    runtime_distribution = Counter()
    for row in candidates:
        for value in row.get("applicable_runtime_surface") or []:
            runtime_distribution[value] += 1

    issues: list[dict[str, Any]] = []
    issues.extend({"candidate_id": candidate_id, "issue": "duplicate_candidate_id"} for candidate_id in duplicates)
    issues.extend({"candidate_id": candidate_id, "issue": "missing_source_unit_ids"} for candidate_id in missing_source)
    issues.extend({"issue": issue} for issue in upstream_issues)

    write_jsonl(output_path, candidates, sort_keys=True, separators=(",", ":"))

    manifest = {
        "schema_version": "theraskill_simulation_protocol_candidates_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if candidates and not issues else "partial",
        "upstream_inputs": {
            name: {
                "units_path": info["units_path"],
                "units_sha256": info["units_sha256"],
                "units_row_count": info["units_row_count"],
                "manifest_path": info["manifest_path"],
                "manifest_sha256": info["manifest_sha256"],
                "manifest_status": info["manifest_status"],
                "candidate_coverage_full": info["candidate_coverage_full"],
            }
            for name, info in upstream.items()
        },
        "output_candidates": {
            "path": str(output_path),
            "sha256": file_sha256(output_path),
            "row_count": len(candidates),
        },
        "family_distribution": dict(family_distribution.most_common()),
        "simulation_mode_distribution": dict(mode_distribution.most_common()),
        "runtime_surface_distribution": dict(runtime_distribution.most_common()),
        "qa_report": str(qa_path),
    }
    write_json(manifest_path, manifest)

    qa = {
        "schema_version": "theraskill_simulation_protocol_candidates_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "candidate_count": len(candidates),
        "family_distribution": dict(family_distribution.most_common()),
        "simulation_mode_distribution": dict(mode_distribution.most_common()),
        "runtime_surface_distribution": dict(runtime_distribution.most_common()),
        "duplicate_candidate_ids": duplicates,
        "missing_source_unit_ids": missing_source,
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(qa_path, qa)
    print(json.dumps({"status": qa["status"], "candidate_count": len(candidates), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
