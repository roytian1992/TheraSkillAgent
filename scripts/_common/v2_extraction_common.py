#!/usr/bin/env python3
"""Shared helpers for TheraSkill v2 extraction runners."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

REGISTRY_FIELDS = [
    "state_dimension",
    "risk_domain",
    "risk_level",
    "required_action",
    "move_type",
    "move_function",
    "skill_layer",
    "certainty",
    "disclosure_depth",
    "clinical_quality",
    "extraction_status",
    "review_dimension_decision",
    "client_readiness",
    "client_response_type",
    "state_delta",
    "transition_type",
    "confidence",
    "promotable_signal",
    "think_handling",
    "source_construction_type",
    "evidence_tier",
    "alliance_state",
]


def registry_options(registry: dict[str, Any]) -> dict[str, list[str]]:
    options: dict[str, list[str]] = {}
    for group_name in ("strongly_controlled", "semi_controlled"):
        group = registry.get(group_name) or {}
        for field in REGISTRY_FIELDS:
            values = group.get(field)
            if isinstance(values, list):
                options[field] = [str(v) for v in values]
    return options


def format_registry_options(options: dict[str, list[str]]) -> str:
    return json.dumps(options, ensure_ascii=False, indent=2)


def audit_value(path: str, value: Any, allowed: set[str], issues: list[dict[str, Any]]) -> None:
    if value is None:
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            audit_value(f"{path}[{idx}]", item, allowed, issues)
        return
    text = str(value)
    if text not in allowed:
        issues.append({"path": path, "value": text, "allowed": sorted(allowed)})


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def remove_generated_rationale(text: str) -> tuple[str, bool]:
    stripped = THINK_RE.sub("", text).strip()
    return stripped, stripped != text.strip()


def compact_dialogue_turns(window: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    turns = []
    turn_id_map: dict[str, Any] = {}
    removed_rationale_turns: list[str] = []
    for idx, turn in enumerate(window.get("turns") or []):
        short_id = f"T{idx}"
        original_text = str(turn.get("text") or "")
        text, removed_rationale = remove_generated_rationale(original_text)
        if removed_rationale:
            removed_rationale_turns.append(short_id)
        turn_id_map[short_id] = turn.get("turn_id")
        turns.append(
            {
                "turn": short_id,
                "role": turn.get("role"),
                "text": text,
            }
        )
    center_original_id = window.get("center_counselor_turn_id")
    center_short_id = None
    for short_id, original_id in turn_id_map.items():
        if original_id == center_original_id:
            center_short_id = short_id
            break
    id_map = {
        "window": "W1",
        "original_window_id": window.get("pilot_window_id"),
        "turn_ids": turn_id_map,
        "center_counselor_turn": center_short_id,
        "generated_rationale_removed_from_turns": removed_rationale_turns,
    }
    return turns, id_map
