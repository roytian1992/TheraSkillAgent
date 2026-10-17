#!/usr/bin/env python3
"""Build deterministic candidate rows for Simulation.therapist_response_policy_pattern."""

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

from io_utils import ensure_output_path, file_sha256, read_jsonl, write_json, write_jsonl
from simulation_surface_registry import (
    SIMULATION_ACTION_SEQUENCE_PATH,
    SIMULATION_ANNOMI_CHANGE_TALK_PATH,
    SIMULATION_ANNOMI_METHOD_PATH,
    SIMULATION_ANNOMI_RUBRIC_PATH,
    SIMULATION_ESCONV_SEQUENCE_PATH,
    SIMULATION_ESCONV_STRATEGY_PATH,
    SIMULATION_INTERACTION_PATTERN_PATH,
    SIMULATION_PSYQA_STRUCTURE_PATH,
    SIMULATION_REFRAMING_STEP_PATH,
    SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_MANIFEST_PATH,
    SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_PATH,
    SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_QA_PATH,
    SIMULATION_THINKING_TRAP_PATH,
)


DEFAULT_INTERACTION = SIMULATION_INTERACTION_PATTERN_PATH
DEFAULT_ACTION_SEQUENCE = SIMULATION_ACTION_SEQUENCE_PATH
DEFAULT_ANNOMI_METHOD = SIMULATION_ANNOMI_METHOD_PATH
DEFAULT_ANNOMI_RUBRIC = SIMULATION_ANNOMI_RUBRIC_PATH
DEFAULT_ANNOMI_CHANGE_TALK = SIMULATION_ANNOMI_CHANGE_TALK_PATH
DEFAULT_ESCONV_STRATEGY = SIMULATION_ESCONV_STRATEGY_PATH
DEFAULT_ESCONV_SEQUENCE = SIMULATION_ESCONV_SEQUENCE_PATH
DEFAULT_REFRAME_STEP = SIMULATION_REFRAMING_STEP_PATH
DEFAULT_THINKING_TRAP = SIMULATION_THINKING_TRAP_PATH
DEFAULT_PSYQA_STRUCTURE = SIMULATION_PSYQA_STRUCTURE_PATH
DEFAULT_OUTPUT = SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_PATH
DEFAULT_MANIFEST = SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_MANIFEST_PATH
DEFAULT_QA = SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_QA_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interaction", default=str(DEFAULT_INTERACTION))
    parser.add_argument("--action-sequence", default=str(DEFAULT_ACTION_SEQUENCE))
    parser.add_argument("--annomi-method", default=str(DEFAULT_ANNOMI_METHOD))
    parser.add_argument("--annomi-rubric", default=str(DEFAULT_ANNOMI_RUBRIC))
    parser.add_argument("--annomi-change-talk", default=str(DEFAULT_ANNOMI_CHANGE_TALK))
    parser.add_argument("--esconv-strategy", default=str(DEFAULT_ESCONV_STRATEGY))
    parser.add_argument("--esconv-sequence", default=str(DEFAULT_ESCONV_SEQUENCE))
    parser.add_argument("--reframing-step", default=str(DEFAULT_REFRAME_STEP))
    parser.add_argument("--thinking-trap", default=str(DEFAULT_THINKING_TRAP))
    parser.add_argument("--psyqa-structure", default=str(DEFAULT_PSYQA_STRUCTURE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit-action-sequences", type=int)
    parser.add_argument("--limit-psyqa-structures", type=int)
    return parser.parse_args()

def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def clean_text(text: Any, limit: int | None = None) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] if limit is not None else value


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


def flatten_dict_items(items: Any, *, cap: int | None = None) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if cap is not None and len(flat) >= cap:
            return
        if isinstance(value, dict):
            flat.append(value)
            return
        if isinstance(value, list):
            for child in value:
                visit(child)
                if cap is not None and len(flat) >= cap:
                    return

    visit(items)
    return flat


def interaction_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = row.get("source") or {}
        transition_unit_id = str(source.get("transition_unit_id") or source.get("source_id") or "")
        if transition_unit_id:
            index[transition_unit_id] = row
    return index


def rubric_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rubric = ((row.get("content") or {}).get("mi_quality_rubric")) or {}
        label = str(rubric.get("behavior_label") or "")
        if label:
            index[label].append(row)
    return index


def annomi_method_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        content = ((row.get("content") or {}).get("mi_method_step")) or {}
        label = str(content.get("mi_behavior_label") or "")
        if label:
            index[label].append(row)
    return index


def thinking_trap_step_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        traps = (row.get("source") or {}).get("thinking_traps") or []
        for trap in traps:
            key = str(trap or "").strip()
            if key:
                index[key].append(row)
    return index


def esconv_sequence_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Counter[str]]]:
    index: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: {
            "problem_type": Counter(),
            "emotion_type": Counter(),
            "pattern_summary": Counter(),
            "strategy_position": Counter(),
        }
    )
    for row in rows:
        content = ((row.get("content") or {}).get("support_strategy_sequence")) or {}
        for step in content.get("strategy_sequence") or []:
            label = str(step.get("strategy_label") or "").strip()
            if not label:
                continue
            index[label]["problem_type"][str(content.get("problem_type") or "unknown")] += 1
            index[label]["emotion_type"][str(content.get("emotion_type") or "unknown")] += 1
            index[label]["strategy_position"][f"position_{step.get('order')}"] += 1
        for pattern in flatten_dict_items(content.get("helpful_strategy_patterns") or []):
            summary = clean_text(pattern.get("pattern_summary"), 220)
            if not summary:
                continue
            for step in content.get("strategy_sequence") or []:
                label = str(step.get("strategy_label") or "").strip()
                if label:
                    index[label]["pattern_summary"][summary] += 1
    return index


def map_move_to_orientation(move_type: str, move_function: str) -> list[str]:
    labels: list[str] = []
    move_type = str(move_type or "")
    move_function = str(move_function or "")
    if move_type in {"validation", "reflection", "affirmation"}:
        labels.append("supportive_listening")
    if move_type in {"open_question", "summarization"} or move_function in {"build_alliance", "clarify_problem"}:
        labels.append("process_guidance")
    if move_type in {"psychoeducation", "information"}:
        labels.append("psychoeducation")
    if move_type in {"cognitive_reframe", "reframing"}:
        labels.append("cognitive_reframing")
    if move_type in {"behavioral_activation", "direct_guidance"} or move_function in {"plan_action", "generate_options"}:
        labels.append("direct_support_guidance")
    return labels


def structure_signature(move_sequence: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    for step in move_sequence[:4]:
        move_types = uniq_keep_order([str(x) for x in (step.get("move_types") or [])])
        move_functions = uniq_keep_order([str(x) for x in (step.get("move_functions") or [])])
        if move_types:
            steps.append("+".join(move_types))
        elif move_functions:
            steps.append("+".join(move_functions))
    if not steps:
        return []
    return [" -> ".join(steps)]


def action_sequence_candidates(
    action_rows: list[dict[str, Any]],
    interactions_by_tid: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in action_rows:
        source = row.get("source") or {}
        content = ((row.get("content") or {}).get("action_sequence_pattern")) or {}
        case_id = str(source.get("case_id") or source.get("source_id") or "")
        for order, sequence in enumerate(content.get("action_sequences") or [], start=1):
            transition_ids = sequence.get("evidence_transition_ids") or []
            matched_interactions = [interactions_by_tid[tid] for tid in transition_ids if tid in interactions_by_tid]
            move_sequence = sequence.get("move_sequence") or []
            preferred_move_types = uniq_keep_order(
                [str(move) for step in move_sequence for move in (step.get("move_types") or [])]
            )
            move_functions = uniq_keep_order(
                [str(move) for step in move_sequence for move in (step.get("move_functions") or [])]
            )
            orientations = uniq_keep_order(
                [
                    label
                    for step in move_sequence
                    for move_type in (step.get("move_types") or [])
                    for move_function in (step.get("move_functions") or [""])
                    for label in map_move_to_orientation(str(move_type), str(move_function))
                ]
            )
            response_types = uniq_keep_order(
                [str(change.get("response_type") or "") for change in (sequence.get("observed_client_changes") or []) if isinstance(change, dict)]
            )
            transition_types = uniq_keep_order(
                [str(change.get("transition_type") or "") for change in (sequence.get("observed_client_changes") or []) if isinstance(change, dict)]
            )
            pre_state_labels = uniq_keep_order(
                [
                    str(item.get("state_label") or "")
                    for interaction in matched_interactions
                    for item in (((interaction.get("content") or {}).get("pre_state") or {}).get("state_mentions") or [])
                ]
            )
            emotion_signals = uniq_keep_order(
                [
                    str(item.get("state_label") or "")
                    for interaction in matched_interactions
                    for item in (((interaction.get("content") or {}).get("pre_state") or {}).get("state_mentions") or [])
                    if str(item.get("dimension") or "") == "emotion"
                ]
            )
            readiness_signals = uniq_keep_order(
                [
                    str(item.get("state_label") or "")
                    for interaction in matched_interactions
                    for item in (((interaction.get("content") or {}).get("pre_state") or {}).get("state_mentions") or [])
                    if str(item.get("dimension") or "") == "motivation_readiness"
                ]
            )
            risk_signals = uniq_keep_order(
                [
                    str(risk)
                    for interaction in matched_interactions
                    for risk in ((((interaction.get("content") or {}).get("pre_state") or {}).get("risk_domains")) or [])
                ]
            )
            tone_style_constraints = []
            if "validation" in preferred_move_types:
                tone_style_constraints.append("empathic_validation")
            if "open_question" in preferred_move_types:
                tone_style_constraints.append("exploratory_questioning")
            if "summarization" in preferred_move_types:
                tone_style_constraints.append("structured_reflection")

            unit_ids = [row.get("unit_id")] + [interaction.get("unit_id") for interaction in matched_interactions]
            unit_ids = [unit_id for unit_id in unit_ids if unit_id]
            candidate_id = f"trpc_{stable_hash(case_id + '::' + str(order) + '::' + str(sequence.get('sequence_name') or 'sequence'))}"
            candidates.append(
                {
                    "schema_version": "theraskill_response_policy_candidate_v1",
                    "candidate_id": candidate_id,
                    "candidate_type": "therapist_response_policy_candidate",
                    "source_dataset": "Simulation.multisource",
                    "source_family": "cpsycound_process_fit",
                    "source_case_id": case_id,
                    "source_unit_ids": unit_ids,
                    "therapeutic_orientation_hint": orientations or ["process_guidance"],
                    "triggering_client_state": {
                        "presenting_problem_signals": pre_state_labels[:6],
                        "emotion_signals": emotion_signals[:4],
                        "alliance_signals": [],
                        "risk_signals": risk_signals[:4],
                        "readiness_signals": readiness_signals[:4],
                        "resistance_signals": [],
                    },
                    "stage_constraints": [],
                    "alliance_constraints": [],
                    "risk_constraints": uniq_keep_order([str((sequence.get("starting_state") or {}).get("risk_level") or "")]),
                    "primary_response_goal_hint": uniq_keep_order([clean_text(sequence.get("intended_process_goal"), 220)]),
                    "preferred_move_types_hint": preferred_move_types[:8],
                    "preferred_response_structure_hint": structure_signature(move_sequence),
                    "tone_style_constraints_hint": tone_style_constraints,
                    "things_to_avoid_hint": [clean_text(x, 220) for x in (sequence.get("limitations_from_source") or [])[:4]],
                    "repair_or_escalation_conditions_hint": [],
                    "applicable_context_hint": uniq_keep_order(
                        [clean_text(sequence.get("applicable_context"), 240)] + response_types + transition_types
                    ),
                    "non_applicable_context_hint": [],
                    "evidence_refs": {
                        "transition_ids": transition_ids,
                        "response_types": response_types,
                        "transition_types": transition_types,
                        "sequence_name": sequence.get("sequence_name"),
                    },
                    "provenance": {
                        "action_sequence_unit_id": row.get("unit_id"),
                        "matched_interaction_unit_ids": [interaction.get("unit_id") for interaction in matched_interactions if interaction.get("unit_id")],
                    },
                }
            )
    return candidates


def annomi_candidates(
    method_rows: list[dict[str, Any]],
    rubric_by_behavior: dict[str, list[dict[str, Any]]],
    change_talk_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    change_talk_unit_ids = [row.get("unit_id") for row in change_talk_rows if row.get("unit_id")]
    change_talk_definition = clean_text(
        (((change_talk_rows[0].get("content") or {}).get("change_talk_pattern")) or {}).get("definition") if change_talk_rows else "",
        240,
    )
    candidates: list[dict[str, Any]] = []
    methods_by_behavior = annomi_method_index(method_rows)
    for behavior_label, grouped_rows in methods_by_behavior.items():
        content = ((grouped_rows[0].get("content") or {}).get("mi_method_step")) or {}
        rubrics = rubric_by_behavior.get(behavior_label) or []
        method_unit_ids = [row.get("unit_id") for row in grouped_rows if row.get("unit_id")]
        positive_criteria = [
            clean_text(item, 180)
            for rubric_row in rubrics
            for item in ((((rubric_row.get("content") or {}).get("mi_quality_rubric")) or {}).get("positive_criteria") or [])
        ]
        failure_patterns = [
            clean_text(item, 180)
            for rubric_row in rubrics
            for item in ((((rubric_row.get("content") or {}).get("mi_quality_rubric")) or {}).get("failure_patterns") or [])
        ]
        quality_levels = uniq_keep_order(
            [
                str((((rubric_row.get("content") or {}).get("mi_quality_rubric")) or {}).get("quality_level") or "")
                for rubric_row in rubrics
            ]
        )
        candidate_id = f"trpc_{stable_hash('annomi::' + behavior_label)}"
        candidates.append(
            {
                "schema_version": "theraskill_response_policy_candidate_v1",
                "candidate_id": candidate_id,
                "candidate_type": "therapist_response_policy_candidate",
                "source_dataset": "Simulation.multisource",
                "source_family": "annomi_mi_process_fit",
                "source_case_id": None,
                "source_unit_ids": [unit_id for unit_id in method_unit_ids + [rubric.get("unit_id") for rubric in rubrics] + change_talk_unit_ids if unit_id],
                "therapeutic_orientation_hint": ["motivational_interviewing"],
                "triggering_client_state": {
                    "presenting_problem_signals": [],
                    "emotion_signals": [],
                    "alliance_signals": [],
                    "risk_signals": [],
                    "readiness_signals": [change_talk_definition] if change_talk_definition else [],
                    "resistance_signals": [],
                },
                "stage_constraints": [],
                "alliance_constraints": [],
                "risk_constraints": [],
                "primary_response_goal_hint": uniq_keep_order([clean_text(content.get("definition"), 220)] + positive_criteria[:2]),
                "preferred_move_types_hint": [behavior_label] if behavior_label else [],
                "preferred_response_structure_hint": [behavior_label] if behavior_label else [],
                "tone_style_constraints_hint": ["mi_consistent"] if quality_levels else [],
                "things_to_avoid_hint": failure_patterns[:4],
                "repair_or_escalation_conditions_hint": [],
                "applicable_context_hint": quality_levels[:2],
                "non_applicable_context_hint": [],
                "evidence_refs": {
                    "mi_behavior_label": behavior_label,
                    "quality_levels": quality_levels,
                    "aggregated_method_unit_count": len(grouped_rows),
                },
                "provenance": {
                    "method_unit_ids": method_unit_ids,
                    "rubric_unit_ids": [rubric.get("unit_id") for rubric in rubrics if rubric.get("unit_id")],
                },
            }
        )
    return candidates


def esconv_candidates(
    strategy_rows: list[dict[str, Any]],
    sequence_stats: dict[str, dict[str, Counter[str]]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in strategy_rows:
        source = row.get("source") or {}
        content = ((row.get("content") or {}).get("support_strategy")) or {}
        strategy_label = str(source.get("strategy_label") or source.get("source_id") or "")
        stats = sequence_stats.get(strategy_label) or {
            "problem_type": Counter(),
            "emotion_type": Counter(),
            "pattern_summary": Counter(),
            "strategy_position": Counter(),
        }
        top_problem_states = [problem for problem, _ in stats["problem_type"].most_common(4)]
        top_emotions = [emotion for emotion, _ in stats["emotion_type"].most_common(4)]
        pattern_summaries = [summary for summary, _ in stats["pattern_summary"].most_common(3)]
        strategy_examples = content.get("example_turns") or []
        candidate_id = f"trpc_{stable_hash('esconv::' + strategy_label)}"
        candidates.append(
            {
                "schema_version": "theraskill_response_policy_candidate_v1",
                "candidate_id": candidate_id,
                "candidate_type": "therapist_response_policy_candidate",
                "source_dataset": "Simulation.multisource",
                "source_family": "esconv_support_style",
                "source_case_id": None,
                "source_unit_ids": [row.get("unit_id")] if row.get("unit_id") else [],
                "therapeutic_orientation_hint": ["supportive_listening"],
                "triggering_client_state": {
                    "presenting_problem_signals": top_problem_states,
                    "emotion_signals": top_emotions,
                    "alliance_signals": [],
                    "risk_signals": [],
                    "readiness_signals": [],
                    "resistance_signals": [],
                },
                "stage_constraints": [],
                "alliance_constraints": [],
                "risk_constraints": [],
                "primary_response_goal_hint": [strategy_label] if strategy_label else [],
                "preferred_move_types_hint": [strategy_label] if strategy_label else [],
                "preferred_response_structure_hint": pattern_summaries[:2] or [strategy_label],
                "tone_style_constraints_hint": uniq_keep_order([str(example.get("emotion_type") or "") for example in strategy_examples[:4] if str(example.get("emotion_type") or "")]),
                "things_to_avoid_hint": [],
                "repair_or_escalation_conditions_hint": [],
                "applicable_context_hint": uniq_keep_order(top_problem_states + top_emotions),
                "non_applicable_context_hint": [],
                "evidence_refs": {
                    "strategy_label": strategy_label,
                    "source_strategy_turn_count": source.get("source_strategy_turn_count"),
                    "source_conversation_count": source.get("source_conversation_count"),
                },
                "provenance": {
                    "support_strategy_unit_id": row.get("unit_id"),
                },
            }
        )
    return candidates


def reframing_candidates(
    trap_rows: list[dict[str, Any]],
    steps_by_trap: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in trap_rows:
        source = row.get("source") or {}
        content = ((row.get("content") or {}).get("thinking_trap_unit")) or {}
        trap_label = str(content.get("trap_label") or source.get("trap_label") or "")
        matched_steps = steps_by_trap.get(trap_label) or []
        step_ids = [step.get("unit_id") for step in matched_steps[:3] if step.get("unit_id")]
        candidate_id = f"trpc_{stable_hash('reframe::' + trap_label)}"
        candidates.append(
            {
                "schema_version": "theraskill_response_policy_candidate_v1",
                "candidate_id": candidate_id,
                "candidate_type": "therapist_response_policy_candidate",
                "source_dataset": "Simulation.multisource",
                "source_family": "cognitive_reframing",
                "source_case_id": None,
                "source_unit_ids": [unit_id for unit_id in [row.get("unit_id")] + step_ids if unit_id],
                "therapeutic_orientation_hint": ["cognitive_reframing"],
                "triggering_client_state": {
                    "presenting_problem_signals": [trap_label] if trap_label else [],
                    "emotion_signals": [],
                    "alliance_signals": [],
                    "risk_signals": [],
                    "readiness_signals": [],
                    "resistance_signals": [],
                },
                "stage_constraints": [],
                "alliance_constraints": [],
                "risk_constraints": [],
                "primary_response_goal_hint": uniq_keep_order(
                    [clean_text(content.get("definition"), 220), "identify_distortion_then_offer_balanced_reframe"]
                ),
                "preferred_move_types_hint": ["cognitive_reframe"],
                "preferred_response_structure_hint": ["identify_trap_then_offer_balanced_reframe"],
                "tone_style_constraints_hint": [],
                "things_to_avoid_hint": [],
                "repair_or_escalation_conditions_hint": [],
                "applicable_context_hint": [trap_label] if trap_label else [],
                "non_applicable_context_hint": [],
                "evidence_refs": {
                    "trap_label": trap_label,
                    "step_example_count": len(matched_steps),
                },
                "provenance": {
                    "thinking_trap_unit_id": row.get("unit_id"),
                    "matched_reframing_step_unit_ids": step_ids,
                },
            }
        )
    return candidates


def psyqa_candidates(structure_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in structure_rows:
        content = ((row.get("content") or {}).get("answer_structure_pattern")) or {}
        distribution = content.get("answer_function_metadata_distribution") or {}
        top_functions = [name for name, _ in Counter(distribution).most_common(4)]
        topic = str(content.get("topic") or "")
        pattern_name = str(content.get("pattern_name") or "")
        candidate_id = f"trpc_{stable_hash('psyqa::' + str(row.get('unit_id') or pattern_name))}"
        candidates.append(
            {
                "schema_version": "theraskill_response_policy_candidate_v1",
                "candidate_id": candidate_id,
                "candidate_type": "therapist_response_policy_candidate",
                "source_dataset": "Simulation.multisource",
                "source_family": "psyqa_public_support",
                "source_case_id": None,
                "source_unit_ids": [row.get("unit_id")] if row.get("unit_id") else [],
                "therapeutic_orientation_hint": ["psychoeducation", "supportive_listening"],
                "triggering_client_state": {
                    "presenting_problem_signals": [topic] if topic else [],
                    "emotion_signals": [],
                    "alliance_signals": [],
                    "risk_signals": [],
                    "readiness_signals": [],
                    "resistance_signals": [],
                },
                "stage_constraints": [],
                "alliance_constraints": [],
                "risk_constraints": [],
                "primary_response_goal_hint": top_functions[:4],
                "preferred_move_types_hint": top_functions[:4],
                "preferred_response_structure_hint": [pattern_name] if pattern_name else [],
                "tone_style_constraints_hint": [],
                "things_to_avoid_hint": [],
                "repair_or_escalation_conditions_hint": [],
                "applicable_context_hint": [topic] if topic else [],
                "non_applicable_context_hint": ["not_direct_clinical_fidelity_evidence"],
                "evidence_refs": {
                    "pattern_name": pattern_name,
                    "question_count": content.get("question_count"),
                    "answer_count": content.get("answer_count"),
                },
                "provenance": {
                    "answer_structure_pattern_unit_id": row.get("unit_id"),
                },
            }
        )
    return candidates


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)

    for path in (output_path, manifest_path, qa_path):
        ensure_output_path(path, overwrite=args.overwrite)

    interaction_rows = read_jsonl(args.interaction)
    action_rows = read_jsonl(args.action_sequence)
    annomi_method_rows = read_jsonl(args.annomi_method)
    annomi_rubric_rows = read_jsonl(args.annomi_rubric)
    annomi_change_talk_rows = read_jsonl(args.annomi_change_talk)
    esconv_strategy_rows = read_jsonl(args.esconv_strategy)
    esconv_sequence_rows = read_jsonl(args.esconv_sequence)
    reframing_step_rows = read_jsonl(args.reframing_step)
    thinking_trap_rows = read_jsonl(args.thinking_trap)
    psyqa_structure_rows = read_jsonl(args.psyqa_structure)

    if args.limit_action_sequences is not None:
        action_rows = action_rows[: args.limit_action_sequences]
    if args.limit_psyqa_structures is not None:
        psyqa_structure_rows = psyqa_structure_rows[: args.limit_psyqa_structures]

    interactions_by_tid = interaction_index(interaction_rows)
    rubric_by_behavior = rubric_index(annomi_rubric_rows)
    steps_by_trap = thinking_trap_step_index(reframing_step_rows)
    esconv_stats = esconv_sequence_index(esconv_sequence_rows)

    candidates: list[dict[str, Any]] = []
    candidates.extend(action_sequence_candidates(action_rows, interactions_by_tid))
    candidates.extend(annomi_candidates(annomi_method_rows, rubric_by_behavior, annomi_change_talk_rows))
    candidates.extend(esconv_candidates(esconv_strategy_rows, esconv_stats))
    candidates.extend(reframing_candidates(thinking_trap_rows, steps_by_trap))
    candidates.extend(psyqa_candidates(psyqa_structure_rows))

    write_jsonl(output_path, candidates, overwrite=args.overwrite, sort_keys=True, separators=(",", ":"))

    family_distribution = Counter(str(row.get("source_family") or "unknown") for row in candidates)
    orientation_distribution = Counter(
        label
        for row in candidates
        for label in (row.get("therapeutic_orientation_hint") or [])
    )
    move_type_distribution = Counter(
        label
        for row in candidates
        for label in (row.get("preferred_move_types_hint") or [])
    )

    manifest = {
        "schema_version": "theraskill_therapist_response_policy_candidates_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "input_files": {
            "interaction": {"path": str(args.interaction), "sha256": file_sha256(args.interaction)},
            "action_sequence": {"path": str(args.action_sequence), "sha256": file_sha256(args.action_sequence)},
            "annomi_method": {"path": str(args.annomi_method), "sha256": file_sha256(args.annomi_method)},
            "annomi_rubric": {"path": str(args.annomi_rubric), "sha256": file_sha256(args.annomi_rubric)},
            "annomi_change_talk": {"path": str(args.annomi_change_talk), "sha256": file_sha256(args.annomi_change_talk)},
            "esconv_strategy": {"path": str(args.esconv_strategy), "sha256": file_sha256(args.esconv_strategy)},
            "esconv_sequence": {"path": str(args.esconv_sequence), "sha256": file_sha256(args.esconv_sequence)},
            "reframing_step": {"path": str(args.reframing_step), "sha256": file_sha256(args.reframing_step)},
            "thinking_trap": {"path": str(args.thinking_trap), "sha256": file_sha256(args.thinking_trap)},
            "psyqa_structure": {"path": str(args.psyqa_structure), "sha256": file_sha256(args.psyqa_structure)},
        },
        "candidate_count": len(candidates),
        "source_family_distribution": dict(family_distribution.most_common()),
        "output_file": {"path": str(output_path), "sha256": file_sha256(output_path)},
    }
    write_json(manifest_path, manifest, overwrite=args.overwrite)

    qa = {
        "schema_version": "theraskill_therapist_response_policy_candidates_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "candidate_count": len(candidates),
        "source_family_distribution": dict(family_distribution.most_common()),
        "therapeutic_orientation_hint_distribution": dict(orientation_distribution.most_common()),
        "preferred_move_type_hint_distribution": dict(move_type_distribution.most_common()),
        "missing_source_unit_ids_count": sum(1 for row in candidates if not row.get("source_unit_ids")),
        "issues": [],
        "issues_count": 0,
    }
    write_json(qa_path, qa, overwrite=args.overwrite)
    print(json.dumps({"status": "complete", "candidate_count": len(candidates)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
