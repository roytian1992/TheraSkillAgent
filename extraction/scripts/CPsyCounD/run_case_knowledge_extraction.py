#!/usr/bin/env python3
"""Run CPsyCounD case-level LLM knowledge extraction.

Stages:
- case_trajectory
- case_formulation_pattern
- action_sequence_pattern

The runner uses already reviewed interaction_pattern knowledge units as input.
It does not read raw dialogue by default and does not silently truncate inputs:
over-budget cases are written to the failure log.
"""

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
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    PromptBudgetExceededError,
    chat_json_with_repair,
    context_max_prompt_tokens,
    load_yaml,
    merged_model_config,
    prompt_budget_report,
    parse_retry_attempts,
)


INPUT_UNITS = "outputs/knowledge/units/CPsyCounD.interaction_pattern.jsonl"
EXTRACTION_CONFIG = "configs/extraction_config.yaml"
MODEL_CONFIG = "configs/model_config.yaml"

STAGE_CONFIGS = {
    "case_trajectory": {
        "knowledge_type": "case_trajectory",
        "prompt": "prompts/CPsyCounD/case_trajectory_knowledge_zh.md",
        "output": "outputs/knowledge/units/CPsyCounD.case_trajectory.jsonl",
        "manifest": "outputs/knowledge/manifests/CPsyCounD.case_trajectory.manifest.json",
    },
    "case_formulation_pattern": {
        "knowledge_type": "case_formulation_pattern",
        "prompt": "prompts/CPsyCounD/case_formulation_pattern_zh.md",
        "output": "outputs/knowledge/units/CPsyCounD.case_formulation_pattern.jsonl",
        "manifest": "outputs/knowledge/manifests/CPsyCounD.case_formulation_pattern.manifest.json",
    },
    "action_sequence_pattern": {
        "knowledge_type": "action_sequence_pattern",
        "prompt": "prompts/CPsyCounD/action_sequence_pattern_zh.md",
        "output": "outputs/knowledge/units/CPsyCounD.action_sequence_pattern.jsonl",
        "manifest": "outputs/knowledge/manifests/CPsyCounD.action_sequence_pattern.manifest.json",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=sorted(STAGE_CONFIGS), required=True)
    parser.add_argument("--input-units", default=INPUT_UNITS)
    parser.add_argument("--trajectory-units", default=STAGE_CONFIGS["case_trajectory"]["output"])
    parser.add_argument("--formulation-units", default=STAGE_CONFIGS["case_formulation_pattern"]["output"])
    parser.add_argument("--extraction-config", default=EXTRACTION_CONFIG)
    parser.add_argument("--model-config", default=MODEL_CONFIG)
    parser.add_argument("--output")
    parser.add_argument("--manifest")
    parser.add_argument("--failure-log")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--case-id-file", help="Text file with one case_id per line.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument(
        "--compact-long-input",
        action="store_true",
        help="Use an explicit audited compact view for over-budget long cases.",
    )
    parser.add_argument(
        "--compact-long-input-mode",
        choices=["text_clip", "minimal", "balanced"],
        default="text_clip",
        help="Compact evidence view to use with --compact-long-input.",
    )
    parser.add_argument("--compact-text-max-chars", type=int, default=180)
    parser.add_argument(
        "--auto-compact-over-tokens",
        type=int,
        help="Automatically compact only cases whose full prompt estimate exceeds this threshold.",
    )
    parser.add_argument(
        "--auto-compact-target-tokens",
        type=int,
        default=12000,
        help="Preferred prompt-token target for adaptive compaction.",
    )
    parser.add_argument(
        "--auto-compact-allow-minimal",
        action="store_true",
        help="Allow adaptive compaction to fall back to minimal rows if balanced cannot hit target.",
    )
    parser.add_argument("--max-output-tokens", type=int, help="Override model max_output_tokens for this run.")
    return parser.parse_args()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = Path(path)
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def completed_case_ids(output_path: Path, failure_log: Path, *, resume: bool) -> set[str]:
    if not resume:
        return set()
    ids = set()
    for row in read_jsonl(output_path):
        case_id = (row.get("source") or {}).get("case_id")
        if case_id:
            ids.add(str(case_id))
    for row in read_jsonl(failure_log):
        case_id = row.get("case_id")
        if case_id:
            ids.add(str(case_id))
    return ids


def compact_interaction_unit(unit: dict[str, Any]) -> dict[str, Any]:
    labels = ((unit.get("retrieval") or {}).get("controlled_labels") or {})
    content = unit.get("content") or {}
    pre_state = content.get("pre_state") or {}
    action = content.get("therapist_action") or {}
    response = content.get("client_response") or {}
    transition = content.get("transition") or {}
    source = unit.get("source") or {}
    return {
        "unit_id": unit.get("unit_id"),
        "transition_unit_id": source.get("transition_unit_id"),
        "source_window_id": source.get("source_window_id"),
        "center_counselor_turn_id": source.get("center_counselor_turn_id"),
        "pre_state": {
            "state_mentions": pre_state.get("state_mentions") or [],
            "state_dimensions": labels.get("state_dimensions") or [],
            "readiness": labels.get("readiness"),
            "alliance_state": labels.get("alliance_state"),
            "disclosure_depth": labels.get("disclosure_depth"),
            "risk_domains": labels.get("risk_domains") or [],
            "risk_level": labels.get("risk_level"),
            "required_action": labels.get("required_action"),
        },
        "therapist_action": {
            "move_types": labels.get("move_types") or [],
            "move_functions": labels.get("move_functions") or [],
            "moves": action.get("moves") or [],
        },
        "client_response": {
            "response_type": labels.get("client_response_type"),
            "notes": response.get("notes"),
            "affect_delta": response.get("affect_delta"),
            "disclosure_depth_delta": response.get("disclosure_depth_delta"),
            "alliance_delta": response.get("alliance_delta"),
            "readiness_delta": response.get("readiness_delta"),
            "risk_delta": response.get("risk_delta"),
        },
        "transition": {
            "transition_type": labels.get("transition_type"),
            "confidence": labels.get("confidence"),
            "summary": transition.get("summary"),
        },
    }


def group_by_case(units: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        case_id = (unit.get("source") or {}).get("case_id")
        if case_id:
            grouped[str(case_id)].append(unit)
    for case_units in grouped.values():
        case_units.sort(
            key=lambda unit: (
                ((unit.get("source") or {}).get("center_counselor_turn_id") is None),
                (unit.get("source") or {}).get("center_counselor_turn_id") or 0,
                (unit.get("source") or {}).get("source_window_id") or "",
            )
        )
    return grouped


def knowledge_unit_id(stage: str, case_id: str) -> str:
    short_hash = hashlib.sha1(case_id.encode("utf-8")).hexdigest()[:16]
    return f"ku_cpsycound_{stage}_{short_hash}"


def base_case_source(case_id: str, case_units: list[dict[str, Any]]) -> dict[str, Any]:
    first_source = case_units[0].get("source") or {}
    return {
        "dataset": "CPsyCounD",
        "source_id": first_source.get("source_id"),
        "case_id": case_id,
        "language": "zh",
        "source_construction_type": first_source.get("source_construction_type"),
        "evidence_tier": first_source.get("evidence_tier"),
        "source_transition_unit_count": len(case_units),
        "source_transition_unit_ids": [
            (unit.get("source") or {}).get("transition_unit_id")
            for unit in case_units
            if (unit.get("source") or {}).get("transition_unit_id")
        ],
    }


def build_payload(
    stage: str,
    case_id: str,
    case_units: list[dict[str, Any]],
    trajectory_by_case: dict[str, dict[str, Any]],
    formulation_by_case: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source = base_case_source(case_id, case_units)
    payload: dict[str, Any] = {
        "case_id": case_id,
        "source_id": source.get("source_id"),
        "source_context": {
            "dataset": source["dataset"],
            "language": source["language"],
            "source_construction_type": source["source_construction_type"],
            "evidence_tier": source["evidence_tier"],
        },
        "interaction_patterns": [compact_interaction_unit(unit) for unit in case_units],
    }
    if stage in {"case_formulation_pattern", "action_sequence_pattern"}:
        trajectory = trajectory_by_case.get(case_id)
        if trajectory:
            payload["case_trajectory"] = (trajectory.get("content") or {}).get("case_trajectory")
    if stage == "action_sequence_pattern":
        formulation = formulation_by_case.get(case_id)
        if formulation:
            payload["case_formulation_pattern"] = (formulation.get("content") or {}).get("case_formulation_pattern")
    return payload


def _clip_text(value: Any, max_chars: int) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + f"...[truncated_for_explicit_compact_view chars={len(value)}]"


def minimal_interaction_pattern(item: dict[str, Any]) -> dict[str, Any]:
    pre_state = item.get("pre_state") or {}
    therapist_action = item.get("therapist_action") or {}
    client_response = item.get("client_response") or {}
    transition = item.get("transition") or {}
    return {
        "unit_id": item.get("unit_id"),
        "transition_unit_id": item.get("transition_unit_id"),
        "source_window_id": item.get("source_window_id"),
        "center_counselor_turn_id": item.get("center_counselor_turn_id"),
        "state_dimensions": pre_state.get("state_dimensions") or [],
        "readiness": pre_state.get("readiness"),
        "alliance_state": pre_state.get("alliance_state"),
        "disclosure_depth": pre_state.get("disclosure_depth"),
        "risk_domains": pre_state.get("risk_domains") or [],
        "risk_level": pre_state.get("risk_level"),
        "required_action": pre_state.get("required_action"),
        "move_types": therapist_action.get("move_types") or [],
        "move_functions": therapist_action.get("move_functions") or [],
        "client_response_type": client_response.get("response_type"),
        "affect_delta": client_response.get("affect_delta"),
        "disclosure_depth_delta": client_response.get("disclosure_depth_delta"),
        "alliance_delta": client_response.get("alliance_delta"),
        "readiness_delta": client_response.get("readiness_delta"),
        "risk_delta": client_response.get("risk_delta"),
        "transition_type": transition.get("transition_type"),
        "confidence": transition.get("confidence"),
    }


def clipped_list(values: Any, max_chars: int, *, max_items: int | None = None) -> list[Any]:
    if not isinstance(values, list):
        return []
    selected = values if max_items is None else values[:max_items]
    return [_clip_text(value, max_chars) for value in selected]


def clipped_moves(values: Any, max_chars: int, *, max_items: int | None = None) -> list[Any]:
    if not isinstance(values, list):
        return []
    selected = values if max_items is None else values[:max_items]
    clipped = []
    for move in selected:
        if isinstance(move, dict):
            clipped.append({key: _clip_text(value, max_chars) for key, value in move.items()})
        else:
            clipped.append(_clip_text(move, max_chars))
    return clipped


def collect_evidence_transition_ids(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"evidence_transition_ids", "transition_unit_ids", "input_transition_unit_ids"}:
                if isinstance(item, list):
                    ids.update(str(v) for v in item if v)
                elif item:
                    ids.add(str(item))
            else:
                ids.update(collect_evidence_transition_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.update(collect_evidence_transition_ids(item))
    return ids


def balanced_interaction_pattern(item: dict[str, Any], max_chars: int, important_ids: set[str]) -> dict[str, Any]:
    row = minimal_interaction_pattern(item)
    transition_id = str(row.get("transition_unit_id") or "")
    pre_state = item.get("pre_state") or {}
    therapist_action = item.get("therapist_action") or {}
    client_response = item.get("client_response") or {}
    transition = item.get("transition") or {}
    is_important = transition_id in important_ids
    brief_chars = max(40, max_chars // 2)
    detail_chars = max_chars if is_important else brief_chars
    row["evidence_brief"] = {
        "state_mentions": clipped_list(pre_state.get("state_mentions"), detail_chars, max_items=2 if is_important else 1),
        "moves": clipped_moves(therapist_action.get("moves"), detail_chars, max_items=2 if is_important else 1),
        "client_notes": _clip_text(client_response.get("notes"), detail_chars),
        "transition_summary": _clip_text(transition.get("summary"), detail_chars),
        "detail_level": "selected_evidence_detail" if is_important else "brief_all_transition_context",
    }
    return row


def compact_dependency(value: Any, max_chars: int) -> Any:
    if isinstance(value, dict):
        compacted = {}
        for key, item in value.items():
            if key in {"overall_process_summary", "summary", "rationale_from_case", "focus", "factor", "goal"}:
                compacted[key] = _clip_text(item, max_chars)
            elif key in {
                "risk_timeline",
                "alliance_timeline",
                "disclosure_timeline",
                "readiness_timeline",
                "main_move_sequence",
                "client_response_sequence",
                "turning_points",
                "presenting_problem",
                "trigger_or_context",
                "maintaining_factors",
                "protective_factors",
                "client_goals",
                "risk_factors",
                "suggested_focus",
                "evidence_spans",
                "quality_status",
            }:
                compacted[key] = compact_dependency(item, max_chars)
            elif key in {"case_id", "source_id", "transition_unit_ids", "evidence_transition_ids", "evidence_tier"}:
                compacted[key] = item
        return compacted
    if isinstance(value, list):
        return [compact_dependency(item, max_chars) for item in value]
    if isinstance(value, str):
        return _clip_text(value, max_chars)
    return value


def compact_for_long_input(payload: dict[str, Any], max_chars: int, *, mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create an explicit compact evidence view for long cases.

    This is intentionally not a hidden truncation path: callers must pass
    --compact-long-input, and the resulting knowledge unit provenance records
    the compaction policy. Identifiers, controlled labels, order, and source
    coverage are retained.
    """
    compact_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    if mode == "balanced":
        important_ids = collect_evidence_transition_ids(compact_payload.get("case_trajectory"))
        important_ids.update(collect_evidence_transition_ids(compact_payload.get("case_formulation_pattern")))
        compact_payload["interaction_patterns"] = [
            balanced_interaction_pattern(item, max_chars, important_ids)
            for item in compact_payload.get("interaction_patterns") or []
        ]
        if "case_trajectory" in compact_payload:
            compact_payload["case_trajectory"] = compact_dependency(compact_payload["case_trajectory"], max_chars)
        if "case_formulation_pattern" in compact_payload:
            compact_payload["case_formulation_pattern"] = compact_dependency(
                compact_payload["case_formulation_pattern"],
                max_chars,
            )
        report = {
            "enabled": True,
            "policy": "explicit_compact_long_input_balanced_evidence_pack_v1",
            "mode": mode,
            "text_max_chars": max_chars,
            "changed_field_groups": len(compact_payload.get("interaction_patterns") or []),
            "important_transition_id_count": len(important_ids),
            "retained_fields": [
                "all_transition_unit_ids",
                "all_transition_order",
                "all_controlled_labels",
                "brief_text_for_all_transitions",
                "richer_text_for_dependency_evidence_transitions",
                "compact_case_trajectory",
                "compact_case_formulation_pattern",
            ],
            "dropped_or_compacted_fields": [
                "full state_mentions",
                "full therapist_action.moves",
                "full client_response.notes",
                "full transition.summary",
                "non-evidence dependency prose beyond compact fields",
            ],
        }
        return compact_payload, report

    if mode == "minimal":
        compact_payload["interaction_patterns"] = [
            minimal_interaction_pattern(item) for item in compact_payload.get("interaction_patterns") or []
        ]
        report = {
            "enabled": True,
            "policy": "explicit_compact_long_input_minimal_transition_rows_v1",
            "mode": mode,
            "text_max_chars": None,
            "changed_field_groups": len(compact_payload.get("interaction_patterns") or []),
            "retained_fields": [
                "case_id",
                "source_id",
                "source_context",
                "unit_id",
                "transition_unit_id",
                "source_window_id",
                "center_counselor_turn_id",
                "controlled_labels",
                "transition_order",
            ],
            "dropped_fields": [
                "state_mentions",
                "therapist_action.moves",
                "client_response.notes",
                "transition.summary",
            ],
        }
        return compact_payload, report

    changed_fields = 0
    for item in compact_payload.get("interaction_patterns") or []:
        pre_state = item.get("pre_state") or {}
        if isinstance(pre_state.get("state_mentions"), list):
            clipped = []
            for mention in pre_state["state_mentions"]:
                clipped.append(_clip_text(mention, max_chars))
            if clipped != pre_state["state_mentions"]:
                changed_fields += 1
            pre_state["state_mentions"] = clipped

        therapist_action = item.get("therapist_action") or {}
        if isinstance(therapist_action.get("moves"), list):
            clipped_moves = []
            for move in therapist_action["moves"]:
                if isinstance(move, dict):
                    clipped_move = {key: _clip_text(value, max_chars) for key, value in move.items()}
                    clipped_moves.append(clipped_move)
                    if clipped_move != move:
                        changed_fields += 1
                else:
                    clipped_moves.append(_clip_text(move, max_chars))
            therapist_action["moves"] = clipped_moves

        client_response = item.get("client_response") or {}
        if "notes" in client_response:
            clipped_notes = _clip_text(client_response.get("notes"), max_chars)
            if clipped_notes != client_response.get("notes"):
                changed_fields += 1
            client_response["notes"] = clipped_notes

        transition = item.get("transition") or {}
        if "summary" in transition:
            clipped_summary = _clip_text(transition.get("summary"), max_chars)
            if clipped_summary != transition.get("summary"):
                changed_fields += 1
            transition["summary"] = clipped_summary

    report = {
        "enabled": True,
        "policy": "explicit_compact_long_input_text_fields_v1",
        "mode": mode,
        "text_max_chars": max_chars,
        "changed_field_groups": changed_fields,
        "retained_fields": [
            "case_id",
            "source_id",
            "source_context",
            "unit_id",
            "transition_unit_id",
            "source_window_id",
            "center_counselor_turn_id",
            "controlled_labels",
            "interaction_order",
        ],
    }
    return compact_payload, report


def adaptive_compact_payload(
    system_prompt: str,
    payload: dict[str, Any],
    max_prompt_tokens: int,
    *,
    compact_over_tokens: int,
    target_tokens: int,
    allow_minimal: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    full_report = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
    if full_report["estimated_prompt_tokens"] <= compact_over_tokens:
        return payload, None

    candidates = [
        ("balanced", 160),
        ("balanced", 120),
        ("balanced", 80),
        ("balanced", 60),
    ]
    if allow_minimal:
        candidates.append(("minimal", 0))
    reports = []
    best_payload = payload
    best_report = None
    for mode, max_chars in candidates:
        compact_payload, compact_report = compact_for_long_input(payload, max_chars, mode=mode)
        after_report = prompt_budget_report(system_prompt, compact_payload, max_prompt_tokens)
        compact_report = {
            **compact_report,
            "trigger": "auto_compact_over_tokens",
            "compact_over_tokens": compact_over_tokens,
            "target_tokens": target_tokens,
            "before_prompt_budget": full_report,
            "after_prompt_budget": after_report,
        }
        reports.append(
            {
                "mode": mode,
                "text_max_chars": max_chars if mode != "minimal" else None,
                "estimated_prompt_tokens": after_report["estimated_prompt_tokens"],
                "over_target": after_report["estimated_prompt_tokens"] > target_tokens,
                "over_budget": after_report["over_budget"],
            }
        )
        if not after_report["over_budget"]:
            best_payload = compact_payload
            best_report = compact_report
            if after_report["estimated_prompt_tokens"] <= target_tokens:
                break

    if best_report is None and allow_minimal:
        compact_payload, compact_report = compact_for_long_input(payload, 0, mode="minimal")
        after_report = prompt_budget_report(system_prompt, compact_payload, max_prompt_tokens)
        best_payload = compact_payload
        best_report = {
            **compact_report,
            "trigger": "auto_compact_over_tokens",
            "compact_over_tokens": compact_over_tokens,
            "target_tokens": target_tokens,
            "before_prompt_budget": full_report,
            "after_prompt_budget": after_report,
        }
    if best_report is None:
        # Balanced-only policy: keep the smallest balanced payload even if it
        # remains above the preferred target, as long as it fits the hard model
        # context. This avoids dropping all evidence text.
        mode, max_chars = "balanced", 60
        compact_payload, compact_report = compact_for_long_input(payload, max_chars, mode=mode)
        after_report = prompt_budget_report(system_prompt, compact_payload, max_prompt_tokens)
        best_payload = compact_payload
        best_report = {
            **compact_report,
            "trigger": "auto_compact_over_tokens",
            "compact_over_tokens": compact_over_tokens,
            "target_tokens": target_tokens,
            "before_prompt_budget": full_report,
            "after_prompt_budget": after_report,
            "balanced_only_above_target": after_report["estimated_prompt_tokens"] > target_tokens,
        }
    best_report["candidate_reports"] = reports
    return best_payload, best_report


def wrap_stage_output(
    stage: str,
    case_id: str,
    case_units: list[dict[str, Any]],
    extracted: dict[str, Any],
    *,
    input_compaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    knowledge_type = STAGE_CONFIGS[stage]["knowledge_type"]
    content_field = knowledge_type
    source = base_case_source(case_id, case_units)
    row = {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": knowledge_unit_id(stage, case_id),
        "knowledge_type": knowledge_type,
        "source": source,
        "content": {
            content_field: extracted,
        },
        "quality": {
            "quality_status": extracted.get("quality_status"),
            "review_status": "llm_extracted_unreviewed",
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "not_applicable_case_level_llm_output",
                "issue_count": 0,
                "issues": [],
            },
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": build_embedding_text(stage, extracted),
        },
        "provenance": {
            "input_interaction_knowledge_units": [unit.get("unit_id") for unit in case_units],
            "input_transition_unit_ids": source["source_transition_unit_ids"],
            "llm_stage": stage,
        },
    }
    if input_compaction:
        row["provenance"]["input_compaction"] = input_compaction
    return row


def build_embedding_text(stage: str, extracted: dict[str, Any]) -> str:
    if stage == "case_trajectory":
        return "\n".join(
            [
                str(extracted.get("overall_process_summary") or ""),
                json.dumps(extracted.get("dominant_presenting_problems") or [], ensure_ascii=False),
                json.dumps(extracted.get("main_move_sequence") or [], ensure_ascii=False),
                json.dumps(extracted.get("turning_points") or [], ensure_ascii=False),
            ]
        ).strip()
    if stage == "case_formulation_pattern":
        return "\n".join(
            [
                json.dumps(extracted.get("presenting_problem") or [], ensure_ascii=False),
                json.dumps(extracted.get("trigger_or_context") or [], ensure_ascii=False),
                json.dumps(extracted.get("maintaining_factors") or [], ensure_ascii=False),
                json.dumps(extracted.get("protective_factors") or [], ensure_ascii=False),
                json.dumps(extracted.get("client_goals") or [], ensure_ascii=False),
                json.dumps(extracted.get("suggested_focus") or [], ensure_ascii=False),
            ]
        ).strip()
    return json.dumps(extracted.get("action_sequences") or [], ensure_ascii=False)


def load_case_units(path: str | Path) -> dict[str, dict[str, Any]]:
    by_case: dict[str, dict[str, Any]] = {}
    for unit in read_jsonl(path):
        case_id = (unit.get("source") or {}).get("case_id")
        if case_id:
            by_case[str(case_id)] = unit
    return by_case


def main() -> int:
    args = parse_args()
    stage_cfg = STAGE_CONFIGS[args.stage]
    output_path = Path(args.output or stage_cfg["output"])
    manifest_path = Path(args.manifest or stage_cfg["manifest"])
    failure_log = Path(args.failure_log or output_path.with_suffix(".failures.jsonl"))

    if output_path.exists() and not args.resume and not args.overwrite:
        raise FileExistsError(f"output exists: {output_path}; pass --resume or --overwrite")
    if args.overwrite:
        output_path.unlink(missing_ok=True)
        failure_log.unlink(missing_ok=True)

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = args.max_output_tokens
    parse_retries = args.parse_retries
    if parse_retries is None:
        parse_retries = parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens
    if max_prompt_tokens is None:
        max_prompt_tokens = context_max_prompt_tokens(extraction_config, local_model_config)

    prompt_path = Path(stage_cfg["prompt"])
    system_prompt = prompt_path.read_text(encoding="utf-8")
    client = OpenAICompatibleClient(model_config)

    units = read_jsonl(args.input_units)
    grouped = group_by_case(units)
    trajectory_by_case = load_case_units(args.trajectory_units) if args.stage in {"case_formulation_pattern", "action_sequence_pattern"} else {}
    formulation_by_case = load_case_units(args.formulation_units) if args.stage == "action_sequence_pattern" else {}

    selected_case_ids = sorted(grouped)
    requested_case_ids = set(args.case_ids or [])
    if args.case_id_file:
        with Path(args.case_id_file).open("r", encoding="utf-8") as handle:
            requested_case_ids.update(line.strip() for line in handle if line.strip())
    if requested_case_ids:
        requested = requested_case_ids
        selected_case_ids = [case_id for case_id in selected_case_ids if case_id in requested]
    if args.limit is not None:
        selected_case_ids = selected_case_ids[: args.limit]

    done = completed_case_ids(output_path, failure_log, resume=args.resume)
    counters = Counter()
    compaction_reports: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    for case_id in selected_case_ids:
        if case_id in done:
            counters["skipped_existing"] += 1
            continue
        case_units = grouped[case_id]
        payload = build_payload(args.stage, case_id, case_units, trajectory_by_case, formulation_by_case)
        input_compaction = None
        if args.auto_compact_over_tokens is not None:
            payload, input_compaction = adaptive_compact_payload(
                system_prompt,
                payload,
                max_prompt_tokens,
                compact_over_tokens=args.auto_compact_over_tokens,
                target_tokens=args.auto_compact_target_tokens,
                allow_minimal=args.auto_compact_allow_minimal,
            )
            if input_compaction:
                compaction_reports.append(input_compaction)
                counters["auto_compact"] += 1
                counters[f"auto_compact_{input_compaction.get('mode')}"] += 1
        elif args.compact_long_input:
            before_report = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
            compact_payload, compact_report = compact_for_long_input(
                payload,
                args.compact_text_max_chars,
                mode=args.compact_long_input_mode,
            )
            after_report = prompt_budget_report(system_prompt, compact_payload, max_prompt_tokens)
            compact_report = {
                **compact_report,
                "case_id": case_id,
                "before_prompt_budget": before_report,
                "after_prompt_budget": after_report,
            }
            payload = compact_payload
            input_compaction = compact_report
            compaction_reports.append(compact_report)
            counters["compact_long_input"] += 1
        try:
            extracted = chat_json_with_repair(
                client,
                system_prompt,
                payload,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
                force_json_first=True,
            )
            wrapped = wrap_stage_output(args.stage, case_id, case_units, extracted, input_compaction=input_compaction)
            append_jsonl(output_path, wrapped)
            counters["success"] += 1
            print(f"{case_id}: success", flush=True)
        except PromptBudgetExceededError as exc:
            failure = {
                "case_id": case_id,
                "stage": args.stage,
                "failure": {
                    "reason": "prompt_token_budget_exceeded",
                    "details": exc.report,
                },
            }
            append_jsonl(failure_log, failure)
            counters["prompt_token_budget_exceeded"] += 1
            print(f"{case_id}: prompt_token_budget_exceeded", flush=True)
        except Exception as exc:  # noqa: BLE001
            failure = {
                "case_id": case_id,
                "stage": args.stage,
                "failure": {
                    "reason": "runner_or_llm_failure",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            }
            append_jsonl(failure_log, failure)
            counters["failure"] += 1
            print(f"{case_id}: failure {type(exc).__name__}: {exc}", flush=True)

    output_rows = read_jsonl(output_path)
    failures = read_jsonl(failure_log)
    manifest = {
        "schema_version": "theraskill_case_knowledge_extraction_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "stage": args.stage,
        "knowledge_type": stage_cfg["knowledge_type"],
        "input_interaction_units": args.input_units,
        "input_interaction_units_sha256": file_sha256(args.input_units),
        "output": str(output_path),
        "output_sha256": file_sha256(output_path),
        "failure_log": str(failure_log),
        "failure_log_sha256": file_sha256(failure_log),
        "prompt": str(prompt_path),
        "prompt_sha256": prompt_hash(system_prompt),
        "model_config": args.model_config,
        "extraction_config": args.extraction_config,
        "model": {
            "provider": model_config.get("provider"),
            "name": model_config.get("name"),
            "base_url": model_config.get("base_url"),
            "temperature": model_config.get("temperature"),
            "max_output_tokens": model_config.get("max_output_tokens"),
            "response_format_json": model_config.get("response_format_json"),
            "thinking": model_config.get("thinking"),
        },
        "context_policy": {
            "max_prompt_tokens": max_prompt_tokens,
            "too_long_behavior": "write_failure_no_truncation",
            "compact_long_input": bool(args.compact_long_input),
            "compact_long_input_mode": args.compact_long_input_mode if args.compact_long_input else None,
            "compact_text_max_chars": args.compact_text_max_chars if args.compact_long_input else None,
            "auto_compact_over_tokens": args.auto_compact_over_tokens,
            "auto_compact_target_tokens": args.auto_compact_target_tokens if args.auto_compact_over_tokens else None,
            "auto_compact_allow_minimal": bool(args.auto_compact_allow_minimal),
        },
        "input_compaction_reports": compaction_reports,
        "counts": {
            "input_interaction_units": len(units),
            "available_cases": len(grouped),
            "selected_cases": len(selected_case_ids),
            "output_rows": len(output_rows),
            "failure_rows": len(failures),
            **dict(counters),
        },
    }
    if args.stage in {"case_formulation_pattern", "action_sequence_pattern"}:
        manifest["dependency_counts"] = {"trajectory_units_loaded": len(trajectory_by_case)}
    if args.stage == "action_sequence_pattern":
        manifest.setdefault("dependency_counts", {})["formulation_units_loaded"] = len(formulation_by_case)
    write_json(manifest_path, manifest)
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
