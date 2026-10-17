#!/usr/bin/env python3
"""Shared helpers for simulation runtime scaffolds."""

from __future__ import annotations

from typing import Any


def trim_unit_for_prompt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": row.get("unit_id"),
        "knowledge_type": row.get("knowledge_type"),
        "source": row.get("source"),
        "content": row.get("content"),
        "quality": row.get("quality"),
        "normalization": row.get("normalization"),
    }


def build_case_evidence_bundle(
    trajectory_row: dict[str, Any],
    formulation_row: dict[str, Any],
    action_sequence_row: dict[str, Any],
) -> dict[str, Any]:
    trajectory = ((trajectory_row.get("content") or {}).get("case_trajectory") or {})
    formulation = ((formulation_row.get("content") or {}).get("case_formulation_pattern") or {})
    action_sequence = ((action_sequence_row.get("content") or {}).get("action_sequence_pattern") or {})
    sequences = action_sequence.get("action_sequences") or []
    compact_sequences = []
    for sequence in sequences[:2]:
        compact_sequences.append(
            {
                "sequence_name": sequence.get("sequence_name"),
                "intended_process_goal": sequence.get("intended_process_goal"),
                "move_sequence": [
                    {
                        "order": step.get("order"),
                        "move_types": step.get("move_types"),
                        "move_functions": step.get("move_functions"),
                        "surface_summary": step.get("surface_summary"),
                    }
                    for step in (sequence.get("move_sequence") or [])[:5]
                ],
                "observed_client_changes": [
                    {
                        "response_type": item.get("response_type"),
                        "transition_type": item.get("transition_type"),
                        "summary": item.get("summary"),
                    }
                    for item in (sequence.get("observed_client_changes") or [])[:3]
                ],
            }
        )
    return {
        "source_case_id": (trajectory_row.get("source") or {}).get("case_id"),
        "trajectory_summary": {
            "overall_process_summary": trajectory.get("overall_process_summary"),
            "main_move_sequence": [
                {
                    "order": item.get("order"),
                    "move_types": item.get("move_types"),
                    "move_functions": item.get("move_functions"),
                    "process_role": item.get("process_role"),
                }
                for item in (trajectory.get("main_move_sequence") or [])[:6]
            ],
            "turning_points": trajectory.get("turning_points") or [],
        },
        "formulation_summary": {
            "presenting_problem": formulation.get("presenting_problem") or [],
            "maintaining_factors": formulation.get("maintaining_factors") or [],
            "protective_factors": formulation.get("protective_factors") or [],
            "client_goals": formulation.get("client_goals") or [],
            "suggested_focus": formulation.get("suggested_focus") or [],
        },
        "action_sequence_summary": compact_sequences,
    }
