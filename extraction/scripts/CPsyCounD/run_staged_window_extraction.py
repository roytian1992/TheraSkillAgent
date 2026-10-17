#!/usr/bin/env python3
"""Run a small formal v2 staged window-extraction smoke test."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v2_extraction_common import (  # noqa: E402
    audit_value,
    compact_dialogue_turns,
    format_registry_options,
    read_jsonl,
    registry_options,
)
from theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    chat_json_with_repair,
    load_yaml,
    merged_model_config,
)
from cpsycound_surface_registry import (  # noqa: E402
    SKILL_EXTRACTION_PILOT_CASES_PATH,
    SKILL_EXTRACTION_PILOT_WINDOWS_PATH,
    SKILL_EXTRACTION_STAGED_SMOKE_ROOT,
)

DEFAULT_INPUT = str(SKILL_EXTRACTION_PILOT_WINDOWS_PATH)
DEFAULT_CASES = str(SKILL_EXTRACTION_PILOT_CASES_PATH)
DEFAULT_OUT_DIR = str(SKILL_EXTRACTION_STAGED_SMOKE_ROOT)
DEFAULT_REGISTRY = "configs/v2/normalization_registry.yaml"

STAGE_PROMPTS = {
    "case_trajectory": "prompts/CPsyCounD/case_trajectory_summary_zh.md",
    "state_risk": "prompts/CPsyCounD/window_state_before_move_zh.md",
    "move": "prompts/CPsyCounD/counselor_move_zh.md",
    "response": "prompts/CPsyCounD/client_response_zh.md",
    "transition": "prompts/CPsyCounD/transition_zh.md",
    "review": "prompts/CPsyCounD/semantic_review_zh.md",
}

STAGE_OUTPUTS = {
    "case_trajectory": "case_trajectory_summaries.jsonl",
    "state_risk": "window_state_risk.jsonl",
    "move": "window_moves.jsonl",
    "response": "window_response_patterns.jsonl",
    "transition": "window_transitions.jsonl",
    "review": "window_review.jsonl",
    "transition_unit": "transition_units.jsonl",
}

STAGE_OPTION_FIELDS = {
    "case_trajectory": ["risk_level"],
    "state_risk": [
        "state_dimension",
        "certainty",
        "client_readiness",
        "alliance_state",
        "disclosure_depth",
        "risk_domain",
        "risk_level",
        "required_action",
        "extraction_status",
    ],
    "move": ["move_type", "move_function", "clinical_quality", "extraction_status"],
    "response": ["client_response_type", "state_delta", "extraction_status"],
    "transition": ["client_response_type", "transition_type", "confidence", "extraction_status"],
    "review": [],
}

REVIEW_DECISIONS = {"pass", "re_extract", "delete"}
REVIEW_PRIORITY_ISSUES = {
    "none",
    "source_faithfulness",
    "clinical_appropriateness",
    "safety_handling",
    "reusable_skill_value",
}
REVIEW_TARGET_STAGES = {"none", "state_risk", "move", "response", "transition", "drop_window"}
PIPELINE_STAGES = ["state_risk", "move", "response", "transition"]
ENUM_SEPARATOR_RE = re.compile(r"[\s\-/.,;:|]+")
ENUM_SAFE_CHARS_RE = re.compile(r"[^a-z0-9_]+")


def canonical_enum_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = ENUM_SEPARATOR_RE.sub("_", text)
    text = ENUM_SAFE_CHARS_RE.sub("", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def enum_key_variants(value: Any) -> set[str]:
    key = canonical_enum_key(value)
    variants = {key}
    parts = key.split("_") if key else []
    if parts:
        last = parts[-1]
        singulars = set()
        if last.endswith("ies") and len(last) > 3:
            singulars.add(last[:-3] + "y")
        if last.endswith("es") and len(last) > 2:
            singulars.add(last[:-2])
        if last.endswith("s") and len(last) > 1:
            singulars.add(last[:-1])
        for singular in singulars:
            variants.add("_".join(parts[:-1] + [singular]))
    return {variant for variant in variants if variant}


def edit_distance_at_most(left: str, right: str, limit: int) -> int | None:
    if abs(len(left) - len(right)) > limit:
        return None
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        row_min = current[0]
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return None
        previous = current
    return previous[-1] if previous[-1] <= limit else None


def max_enum_edit_distance(value_key: str) -> int:
    if len(value_key) <= 6:
        return 0
    if len(value_key) <= 14:
        return 2
    return 3


def resolve_enum_value(value: Any, allowed: set[str]) -> tuple[str | None, dict[str, Any] | None]:
    if value is None:
        return None, None
    text = str(value)
    if text in allowed:
        return text, None

    allowed_by_key: dict[str, set[str]] = defaultdict(set)
    for option in allowed:
        allowed_by_key[canonical_enum_key(option)].add(option)

    for key in enum_key_variants(text):
        matches = allowed_by_key.get(key) or set()
        if len(matches) == 1:
            resolved = next(iter(matches))
            return resolved, {
                "old_value": text,
                "new_value": resolved,
                "method": "canonical_form",
                "canonical_key": key,
            }

    value_key = canonical_enum_key(text)
    if not value_key:
        return None, None
    limit = max_enum_edit_distance(value_key)
    if limit <= 0:
        return None, None
    candidates = []
    for option in allowed:
        option_key = canonical_enum_key(option)
        distance = edit_distance_at_most(value_key, option_key, limit)
        if distance is not None:
            candidates.append((distance, option))
    if not candidates:
        return None, None

    candidates.sort(key=lambda item: (item[0], item[1]))
    best_distance = candidates[0][0]
    best = [option for distance, option in candidates if distance == best_distance]
    if len(best) == 1:
        resolved = best[0]
        return resolved, {
            "old_value": text,
            "new_value": resolved,
            "method": "unique_edit_distance",
            "canonical_key": value_key,
            "distance": best_distance,
            "max_distance": limit,
        }
    return None, None


def normalize_enum_field(container: dict[str, Any], field: str, allowed: set[str], path: str, changes: list[dict[str, Any]]) -> None:
    value = container.get(field)
    if value is None:
        return
    resolved, trace = resolve_enum_value(value, allowed)
    if resolved is None or trace is None:
        return
    container[field] = resolved
    changes.append({"path": path, **trace})


def normalize_enum_list(container: dict[str, Any], field: str, allowed: set[str], path: str, changes: list[dict[str, Any]]) -> None:
    value = container.get(field)
    if not isinstance(value, list):
        normalize_enum_field(container, field, allowed, path, changes)
        return
    normalized = []
    for idx, item in enumerate(value):
        resolved, trace = resolve_enum_value(item, allowed)
        if resolved is not None:
            normalized.append(resolved)
            if trace is not None:
                changes.append({"path": f"{path}[{idx}]", **trace})
        else:
            normalized.append(item)
    container[field] = normalized

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--model-config", default="configs/model_config.yaml")
    parser.add_argument("--extraction-config", default="configs/extraction_config.yaml")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--window-id", action="append", dest="window_ids")
    parser.add_argument("--dataset", action="append", dest="datasets")
    parser.add_argument("--parse-retries", type=int, default=1)
    parser.add_argument("--review-reextract-attempts", type=int, default=1)
    parser.add_argument("--resume", action="store_true", help="Skip cases/windows already completed in the output directory.")
    parser.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty output directory without resume.")
    parser.add_argument(
        "--debug-force-reextract-stage",
        choices=["state_risk", "move", "response", "transition"],
        help="Force one re-extraction target for smoke-testing the review loop.",
    )
    return parser.parse_args()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def output_dir_has_run_artifacts(out_dir: Path) -> bool:
    expected = {Path(filename).name for filename in STAGE_OUTPUTS.values()}
    expected.add("window_failures.jsonl")
    return any((out_dir / name).exists() for name in expected)


def completed_ids(path: Path, id_field: str, *, success_only: bool = False) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for row in read_jsonl(path):
        if success_only and row.get("failure"):
            continue
        value = row.get(id_field)
        if value is not None:
            ids.add(str(value))
    return ids


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    if path.exists():
        history_dir = path.parent / "manifest_history"
        history_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        history_path = history_dir / f"manifest_{timestamp}.json"
        history_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_windows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected = rows
    if args.datasets:
        allowed = set(args.datasets)
        selected = [r for r in selected if r.get("dataset") in allowed]
    if args.window_ids:
        allowed_ids = set(args.window_ids)
        selected = [r for r in selected if r.get("pilot_window_id") in allowed_ids]
    return selected[: args.limit]


def stage_options(options: dict[str, list[str]], stage: str) -> dict[str, list[str]]:
    return {field: options[field] for field in STAGE_OPTION_FIELDS[stage] if field in options}


def prompt_text(path: str, options: dict[str, list[str]], stage: str) -> tuple[str, str]:
    template = Path(path).read_text(encoding="utf-8")
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return template.replace("__NORMALIZATION_OPTIONS__", format_registry_options(stage_options(options, stage))), digest


def source_context(window: dict[str, Any], case: dict[str, Any] | None, id_map: dict[str, Any]) -> dict[str, Any]:
    case = case or {}
    return {
        "dataset": window.get("dataset"),
        "source_construction_type": case.get("source_construction_type"),
        "evidence_tier": case.get("evidence_tier"),
        "topic": case.get("topic"),
        "style_or_framework_context": case.get("therapy_framework"),
        "generated_rationale_hint_present": bool(id_map.get("generated_rationale_removed_from_turns")),
    }


def build_window_payload(
    window: dict[str, Any],
    case: dict[str, Any] | None,
    case_summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    turns, id_map = compact_dialogue_turns(window)
    payload = {
        "source_context": source_context(window, case, id_map),
        "case_context": case_summary,
        "dialogue_window": {
            "window": "W1",
            "center_counselor_turn": id_map["center_counselor_turn"],
            "turns": turns,
        },
    }
    return payload, id_map


def local_turn_index(turn: dict[str, Any]) -> int | None:
    match = re.match(r"^T(\d+)$", str(turn.get("turn") or ""))
    return int(match.group(1)) if match else None


def turn_ref_index(value: Any) -> int | None:
    match = re.match(r"^T(\d+)$", str(value or ""))
    return int(match.group(1)) if match else None


def temporal_safe_case_context(window_payload: dict[str, Any]) -> dict[str, Any]:
    context = window_payload.get("case_context")
    if not isinstance(context, dict):
        return {}
    safe_keys = {
        "prior_context_summary",
        "known_phase_before_window",
        "risk_status_before_window",
        "state_snapshot_before_window",
        "risk_snapshot_before_window",
    }
    return {key: context[key] for key in safe_keys if key in context and context[key]}


def slice_window_payload_for_stage(window_payload: dict[str, Any], stage: str) -> dict[str, Any]:
    payload = dict(window_payload)
    dialogue = dict(payload.get("dialogue_window") or {})
    turns = list(dialogue.get("turns") or [])
    center_turn = dialogue.get("center_counselor_turn")
    center_idx = None
    for turn in turns:
        if turn.get("turn") == center_turn:
            center_idx = local_turn_index(turn)
            break

    if center_idx is None:
        dialogue["evidence_policy"] = "center turn not found; full local window retained"
        payload["dialogue_window"] = dialogue
        return payload

    if stage == "state_risk":
        sliced_turns = [
            turn for turn in turns if local_turn_index(turn) is not None and local_turn_index(turn) < center_idx
        ]
        evidence_policy = "pre_state_only: only turns before center_counselor_turn are provided"
    elif stage == "move":
        sliced_turns = [turn for turn in turns if turn.get("turn") == center_turn]
        evidence_policy = "center_move_only: only center_counselor_turn is provided"
    elif stage == "response":
        sliced_turns = [
            turn
            for turn in turns
            if local_turn_index(turn) is not None
            and local_turn_index(turn) > center_idx
            and turn.get("role") == "client"
        ][:1]
        evidence_policy = "post_response_only: only the first client turn after center_counselor_turn is provided"
    elif stage in {"transition", "review"}:
        sliced_turns = turns
        evidence_policy = (
            "assembled_stage_review: full local window is provided only after isolated state, move, "
            "and response extraction; post-center turns must not be used as pre-state evidence"
        )
    else:
        sliced_turns = turns
        evidence_policy = "unknown stage; full local window retained"

    dialogue["turns"] = sliced_turns
    dialogue["evidence_policy"] = evidence_policy
    dialogue["full_window_turn_count"] = len(turns)
    dialogue["provided_turn_count"] = len(sliced_turns)
    payload["dialogue_window"] = dialogue
    safe_context = temporal_safe_case_context(window_payload)
    if safe_context:
        payload["case_context"] = safe_context
        payload["case_context_policy"] = (
            "only prior-to-window summaries are provided; full-case summaries are withheld from window stages"
        )
    else:
        payload.pop("case_context", None)
        payload["case_context_policy"] = (
            "full-case summaries are withheld from window stages to prevent future-information leakage"
        )
    return payload


def build_case_payload(case_id: str, windows_by_case: dict[str, list[dict[str, Any]]], case: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    turn_by_id: dict[Any, dict[str, Any]] = {}
    removed_rationale_turns: list[str] = []
    for window in windows_by_case[case_id]:
        turns, _ = compact_dialogue_turns(window)
        local_original = {f"T{idx}": turn.get("turn_id") for idx, turn in enumerate(window.get("turns") or [])}
        for turn in turns:
            original_id = local_original.get(turn["turn"])
            if original_id is not None:
                turn_by_id[original_id] = {"turn_id": original_id, "role": turn.get("role"), "text": turn.get("text")}
    compact_turns = []
    turn_id_map = {}
    for idx, original_id in enumerate(sorted(turn_by_id)):
        short_id = f"T{idx}"
        turn = turn_by_id[original_id]
        turn_id_map[short_id] = original_id
        compact_turns.append({"turn": short_id, "role": turn["role"], "text": turn["text"]})
        if not turn["text"]:
            removed_rationale_turns.append(short_id)
    case = case or {}
    payload = {
        "source_context": {
            "dataset": case.get("dataset"),
            "source_construction_type": case.get("source_construction_type"),
            "evidence_tier": case.get("evidence_tier"),
            "topic": case.get("topic"),
            "style_or_framework_context": case.get("therapy_framework"),
            "generated_rationale_hint_present": bool(removed_rationale_turns),
        },
        "case_dialogue": {
            "case": "C1",
            "turns": compact_turns,
        },
    }
    id_map = {
        "case": "C1",
        "original_case_id": case_id,
        "turn_ids": turn_id_map,
        "turn_source": "reconstructed_from_overlapping_pilot_windows",
        "generated_rationale_removed_from_turns": removed_rationale_turns,
    }
    return payload, id_map


def audit_state_risk(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    allowed = {k: set(v) for k, v in options.items()}
    audit_value("extraction_status", row.get("extraction_status"), allowed["extraction_status"], issues)
    for idx, state in enumerate(row.get("state_mentions") or []):
        if isinstance(state, dict):
            audit_value(f"state_mentions[{idx}].dimension", state.get("dimension"), allowed["state_dimension"], issues)
            audit_value(f"state_mentions[{idx}].certainty", state.get("certainty"), allowed["certainty"], issues)
    audit_value("readiness", row.get("readiness"), allowed["client_readiness"], issues)
    audit_value("alliance_state", row.get("alliance_state"), allowed["alliance_state"], issues)
    audit_value("disclosure_depth", row.get("disclosure_depth"), allowed["disclosure_depth"], issues)
    risk = row.get("risk") or {}
    audit_value("risk.risk_domains", risk.get("risk_domains"), allowed["risk_domain"], issues)
    audit_value("risk.risk_level", risk.get("risk_level"), allowed["risk_level"], issues)
    audit_value("risk.required_action", risk.get("required_action"), allowed["required_action"], issues)
    return {"status": "pass" if not issues else "issue", "issue_count": len(issues), "issues": issues}


def audit_state_temporal_scope(row: dict[str, Any], window_payload: dict[str, Any]) -> dict[str, Any]:
    dialogue = window_payload.get("dialogue_window") or {}
    center_idx = turn_ref_index(dialogue.get("center_counselor_turn"))
    issues: list[dict[str, Any]] = []
    if center_idx is None:
        return {
            "status": "issue",
            "issue_count": 1,
            "issues": [{"path": "dialogue_window.center_counselor_turn", "reason": "missing or invalid center turn"}],
        }

    for state_idx, state in enumerate(row.get("state_mentions") or []):
        if not isinstance(state, dict):
            continue
        for turn in state.get("evidence_turns") or []:
            idx = turn_ref_index(turn)
            if idx is not None and idx >= center_idx:
                issues.append(
                    {
                        "path": f"state_mentions[{state_idx}].evidence_turns",
                        "value": turn,
                        "center_counselor_turn": dialogue.get("center_counselor_turn"),
                        "allowed": "turn index must be less than center_counselor_turn",
                    }
                )

    risk = row.get("risk") or {}
    for turn in risk.get("evidence_turns") or []:
        idx = turn_ref_index(turn)
        if idx is not None and idx >= center_idx:
            issues.append(
                {
                    "path": "risk.evidence_turns",
                    "value": turn,
                    "center_counselor_turn": dialogue.get("center_counselor_turn"),
                    "allowed": "turn index must be less than center_counselor_turn",
                }
            )
    return {"status": "pass" if not issues else "issue", "issue_count": len(issues), "issues": issues}


def normalize_state_risk_labels(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    allowed = {k: set(v) for k, v in options.items()}
    changes: list[dict[str, Any]] = []
    normalize_enum_field(row, "extraction_status", allowed["extraction_status"], "extraction_status", changes)
    for idx, state in enumerate(row.get("state_mentions") or []):
        if isinstance(state, dict):
            normalize_enum_field(state, "dimension", allowed["state_dimension"], f"state_mentions[{idx}].dimension", changes)
            normalize_enum_field(state, "certainty", allowed["certainty"], f"state_mentions[{idx}].certainty", changes)
    normalize_enum_field(row, "readiness", allowed["client_readiness"], "readiness", changes)
    normalize_enum_field(row, "alliance_state", allowed["alliance_state"], "alliance_state", changes)
    normalize_enum_field(row, "disclosure_depth", allowed["disclosure_depth"], "disclosure_depth", changes)
    risk = row.get("risk") or {}
    normalize_enum_list(risk, "risk_domains", allowed["risk_domain"], "risk.risk_domains", changes)
    normalize_enum_field(risk, "risk_level", allowed["risk_level"], "risk.risk_level", changes)
    normalize_enum_field(risk, "required_action", allowed["required_action"], "risk.required_action", changes)
    row["risk"] = risk
    row["label_normalization"] = {"status": "normalized" if changes else "pass", "changes": changes}
    return row


def repair_risk_consistency(row: dict[str, Any]) -> dict[str, Any]:
    risk = row.get("risk") or {}
    domains = list(dict.fromkeys(str(v) for v in risk.get("risk_domains") or []))
    corrections: list[dict[str, Any]] = []

    if "none_observed" in domains and len(set(domains)) > 1:
        domains = [d for d in domains if d != "none_observed"]
        corrections.append(
            {
                "field": "risk.risk_domains",
                "removed": "none_observed",
                "reason": "none_observed is mutually exclusive with observed risk domains",
            }
        )
    if corrections and not domains:
        domains = ["none_observed"]
    if corrections and set(domains) == {"none_observed"}:
        old_level = risk.get("risk_level")
        old_action = risk.get("required_action")
        risk["risk_level"] = "none_observed"
        risk["required_action"] = "ordinary_support_allowed"
        corrections.append(
            {
                "field": "risk.risk_level|required_action",
                "old_value": [old_level, old_action],
                "new_value": ["none_observed", "ordinary_support_allowed"],
                "reason": "risk domains were normalized to none_observed by structural consistency repair",
            }
        )
    if set(domains) == {"none_observed"} and risk.get("risk_level") != "none_observed":
        old_level = risk.get("risk_level")
        old_action = risk.get("required_action")
        risk["risk_level"] = "none_observed"
        risk["required_action"] = "ordinary_support_allowed"
        corrections.append(
            {
                "field": "risk.risk_level|required_action",
                "old_value": [old_level, old_action],
                "new_value": ["none_observed", "ordinary_support_allowed"],
                "reason": "risk_level must be none_observed when risk_domains is none_observed",
            }
        )
    risk["risk_domains"] = domains
    row["risk"] = risk
    row["risk_consistency_repair"] = {"status": "repaired" if corrections else "pass", "repairs": corrections}
    return row


def audit_move(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    allowed = {k: set(v) for k, v in options.items()}
    audit_value("extraction_status", row.get("extraction_status"), allowed["extraction_status"], issues)
    for idx, move in enumerate(row.get("micro_moves") or []):
        if isinstance(move, dict):
            audit_value(f"micro_moves[{idx}].move_type", move.get("move_type"), allowed["move_type"], issues)
            audit_value(f"micro_moves[{idx}].move_function", move.get("move_function"), allowed["move_function"], issues)
            audit_value(f"micro_moves[{idx}].clinical_quality", move.get("clinical_quality"), allowed["clinical_quality"], issues)
    return {"status": "pass" if not issues else "issue", "issue_count": len(issues), "issues": issues}


def normalize_move_labels(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    allowed_type = set(options["move_type"])
    allowed_function = set(options["move_function"])
    allowed_quality = set(options["clinical_quality"])
    allowed_status = set(options["extraction_status"])
    changes: list[dict[str, Any]] = []
    normalize_enum_field(row, "extraction_status", allowed_status, "extraction_status", changes)
    for idx, move in enumerate(row.get("micro_moves") or []):
        if isinstance(move, dict):
            normalize_enum_field(move, "move_type", allowed_type, f"micro_moves[{idx}].move_type", changes)
            normalize_enum_field(move, "move_function", allowed_function, f"micro_moves[{idx}].move_function", changes)
            normalize_enum_field(move, "clinical_quality", allowed_quality, f"micro_moves[{idx}].clinical_quality", changes)
    row["label_normalization"] = {"status": "normalized" if changes else "pass", "changes": changes}
    return row


def flag_invalid_move_labels(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    allowed_type = set(options["move_type"])
    allowed_function = set(options["move_function"])
    allowed_quality = set(options["clinical_quality"])
    issues: list[dict[str, Any]] = []
    for idx, move in enumerate(row.get("micro_moves") or []):
        if not isinstance(move, dict):
            continue
        for field, allowed in (
            ("move_type", allowed_type),
            ("move_function", allowed_function),
            ("clinical_quality", allowed_quality),
        ):
            value = move.get(field)
            if value not in allowed:
                issues.append(
                    {
                        "path": f"micro_moves[{idx}].{field}",
                        "value": value,
                        "allowed": sorted(allowed),
                        "reason": "value not in stage normalization options; no deterministic fallback is applied",
                    }
                )
    row["label_issue"] = {"status": "issue" if issues else "pass", "issue_count": len(issues), "issues": issues}
    return row


def normalize_response_labels(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    allowed = {k: set(v) for k, v in options.items()}
    changes: list[dict[str, Any]] = []
    normalize_enum_field(row, "extraction_status", allowed["extraction_status"], "extraction_status", changes)
    response = row.get("client_response_pattern") or {}
    normalize_enum_field(response, "response_type", allowed["client_response_type"], "client_response_pattern.response_type", changes)
    for field in ("affect_delta", "disclosure_depth_delta", "alliance_delta", "readiness_delta", "risk_delta"):
        normalize_enum_field(response, field, allowed["state_delta"], f"client_response_pattern.{field}", changes)
    row["client_response_pattern"] = response
    row["label_normalization"] = {"status": "normalized" if changes else "pass", "changes": changes}
    return row


def normalize_transition_labels(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    allowed = {k: set(v) for k, v in options.items()}
    changes: list[dict[str, Any]] = []
    normalize_enum_field(row, "extraction_status", allowed["extraction_status"], "extraction_status", changes)
    triple = row.get("interaction_triple") or {}
    normalize_enum_field(triple, "response_type", allowed["client_response_type"], "interaction_triple.response_type", changes)
    normalize_enum_field(triple, "transition_type", allowed["transition_type"], "interaction_triple.transition_type", changes)
    normalize_enum_field(triple, "confidence", allowed["confidence"], "interaction_triple.confidence", changes)
    row["interaction_triple"] = triple
    row["label_normalization"] = {"status": "normalized" if changes else "pass", "changes": changes}
    return row


def repair_move_scope(row: dict[str, Any], window_payload: dict[str, Any]) -> dict[str, Any]:
    center_turn = ((window_payload.get("dialogue_window") or {}).get("center_counselor_turn"))
    repairs: list[dict[str, Any]] = []
    kept_moves: list[dict[str, Any]] = []
    for idx, move in enumerate(row.get("micro_moves") or []):
        if not isinstance(move, dict):
            continue
        evidence_turn = move.get("evidence_turn")
        if evidence_turn == center_turn:
            kept_moves.append(move)
            continue
        repairs.append(
            {
                "path": f"micro_moves[{idx}]",
                "removed_evidence_turn": evidence_turn,
                "center_counselor_turn": center_turn,
                "reason": "move extraction must use only the center counselor turn as evidence",
            }
        )
    if repairs:
        row["micro_moves"] = kept_moves
    row["scope_repair"] = {"status": "repaired" if repairs else "pass", "repairs": repairs}
    return row


def audit_response(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    allowed = {k: set(v) for k, v in options.items()}
    audit_value("extraction_status", row.get("extraction_status"), allowed["extraction_status"], issues)
    response = row.get("client_response_pattern") or {}
    audit_value("client_response_pattern.response_type", response.get("response_type"), allowed["client_response_type"], issues)
    for field in ("affect_delta", "disclosure_depth_delta", "alliance_delta", "readiness_delta", "risk_delta"):
        audit_value(f"client_response_pattern.{field}", response.get(field), allowed["state_delta"], issues)
    return {"status": "pass" if not issues else "issue", "issue_count": len(issues), "issues": issues}


def audit_response_temporal_scope(row: dict[str, Any], window_payload: dict[str, Any]) -> dict[str, Any]:
    dialogue = window_payload.get("dialogue_window") or {}
    center_idx = turn_ref_index(dialogue.get("center_counselor_turn"))
    response = row.get("client_response_pattern") or {}
    response_turn = response.get("response_evidence_turn")
    response_idx = turn_ref_index(response_turn)
    issues = []
    if response_turn and center_idx is not None and response_idx is not None and response_idx <= center_idx:
        issues.append(
            {
                "path": "client_response_pattern.response_evidence_turn",
                "value": response_turn,
                "center_counselor_turn": dialogue.get("center_counselor_turn"),
                "allowed": "response evidence must be after center_counselor_turn",
            }
        )
    return {"status": "pass" if not issues else "issue", "issue_count": len(issues), "issues": issues}


def audit_transition(row: dict[str, Any], options: dict[str, list[str]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    allowed = {k: set(v) for k, v in options.items()}
    audit_value("extraction_status", row.get("extraction_status"), allowed["extraction_status"], issues)
    triple = row.get("interaction_triple") or {}
    audit_value("interaction_triple.response_type", triple.get("response_type"), allowed["client_response_type"], issues)
    audit_value("interaction_triple.transition_type", triple.get("transition_type"), allowed["transition_type"], issues)
    audit_value("interaction_triple.confidence", triple.get("confidence"), allowed["confidence"], issues)
    return {"status": "pass" if not issues else "issue", "issue_count": len(issues), "issues": issues}


def audit_review(row: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for dimension in (
        "source_faithfulness",
        "clinical_appropriateness",
        "safety_handling",
        "reusable_skill_value",
    ):
        decision = (row.get(dimension) or {}).get("decision")
        if decision not in REVIEW_DECISIONS:
            issues.append({"path": f"{dimension}.decision", "value": decision, "allowed": sorted(REVIEW_DECISIONS)})
    final_decision = row.get("final_decision")
    if final_decision not in REVIEW_DECISIONS:
        issues.append({"path": "final_decision", "value": final_decision, "allowed": sorted(REVIEW_DECISIONS)})
    priority_issue = row.get("priority_issue")
    if priority_issue not in REVIEW_PRIORITY_ISSUES:
        issues.append({"path": "priority_issue", "value": priority_issue, "allowed": sorted(REVIEW_PRIORITY_ISSUES)})
    if final_decision == "pass" and priority_issue != "none":
        issues.append({"path": "priority_issue", "value": priority_issue, "allowed_when_pass": ["none"]})
    target_stage = row.get("target_stage")
    if target_stage not in REVIEW_TARGET_STAGES:
        issues.append({"path": "target_stage", "value": target_stage, "allowed": sorted(REVIEW_TARGET_STAGES)})
    if final_decision == "pass" and target_stage != "none":
        issues.append({"path": "target_stage", "value": target_stage, "allowed_when_pass": ["none"]})
    if final_decision == "re_extract" and target_stage not in {"state_risk", "move", "response", "transition"}:
        issues.append(
            {
                "path": "target_stage",
                "value": target_stage,
                "allowed_when_re_extract": ["state_risk", "move", "response", "transition"],
            }
        )
    if final_decision == "delete" and target_stage != "drop_window":
        issues.append({"path": "target_stage", "value": target_stage, "allowed_when_delete": ["drop_window"]})
    return {"status": "pass" if not issues else "issue", "issue_count": len(issues), "issues": issues}


def derive_retention_label(review_row: dict[str, Any], state_row: dict[str, Any], move_row: dict[str, Any]) -> str:
    decision = review_row.get("final_decision")
    if decision == "pass":
        return "positive_candidate"
    if decision == "re_extract":
        return "needs_reextract"
    if decision != "delete":
        return "hold_for_human_review"

    risk = state_row.get("risk") or {}
    risk_level = risk.get("risk_level")
    required_action = risk.get("required_action")
    qualities = {
        move.get("clinical_quality")
        for move in (move_row.get("micro_moves") or [])
        if isinstance(move, dict)
    }
    dimension_decisions = [
        (review_row.get(dimension) or {}).get("decision")
        for dimension in (
            "source_faithfulness",
            "clinical_appropriateness",
            "safety_handling",
            "reusable_skill_value",
        )
    ]
    if "delete" in dimension_decisions and (
        review_row.get("priority_issue") in {"clinical_appropriateness", "safety_handling"}
        or qualities.intersection({"questionable", "unsafe"})
    ):
        return "negative_example_candidate"
    if risk_level in {"high", "imminent"} or required_action in {"safety_first_response", "emergency_or_local_protocol"}:
        return "safety_protocol_candidate"
    if review_row.get("priority_issue") == "reusable_skill_value":
        return "drop_not_skill_material"
    return "hold_for_human_review"


def summarize_audits(*rows: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for row in rows:
        for key in (
            "normalization_audit",
            "temporal_scope_audit",
            "semantic_review_audit",
            "label_normalization",
            "risk_consistency_repair",
            "scope_repair",
            "label_issue",
        ):
            if key in row:
                summary.setdefault(key, []).append(row[key])
    return summary


def count_label_normalization(counters: Counter, stage: str, row: dict[str, Any]) -> None:
    trace = row.get("label_normalization") or {}
    changes = trace.get("changes") or []
    if changes:
        counters[f"{stage}_label_normalized"] += len(changes)


def build_transition_unit(
    window: dict[str, Any],
    case: dict[str, Any] | None,
    id_map: dict[str, Any],
    state_row: dict[str, Any],
    move_row: dict[str, Any],
    response_row: dict[str, Any],
    transition_row: dict[str, Any],
    review_row: dict[str, Any],
) -> dict[str, Any]:
    risk = state_row.get("risk") or {}
    response = response_row.get("client_response_pattern") or {}
    triple = transition_row.get("interaction_triple") or {}
    retention_label = derive_retention_label(review_row, state_row, move_row)
    case = case or {}
    return {
        "transition_unit_id": f"tu_{window.get('pilot_window_id')}",
        "schema_version": "theraskill_transition_unit_v1",
        "source_window_id": window.get("pilot_window_id"),
        "case_id": window.get("pilot_case_id"),
        "source_metadata": {
            "dataset": window.get("dataset"),
            "source_id": window.get("source_id"),
            "center_counselor_turn_id": window.get("center_counselor_turn_id"),
            "source_construction_type": case.get("source_construction_type"),
            "evidence_tier": case.get("evidence_tier"),
            "lexical_risk_hint": window.get("lexical_risk_hint"),
        },
        "id_mapping": id_map,
        "pre_state": {
            "state_mentions": state_row.get("state_mentions") or [],
            "state_dimensions": [
                state.get("dimension")
                for state in (state_row.get("state_mentions") or [])
                if isinstance(state, dict)
            ],
            "readiness": state_row.get("readiness"),
            "alliance_state": state_row.get("alliance_state"),
            "disclosure_depth": state_row.get("disclosure_depth"),
            "risk_domains": risk.get("risk_domains") or [],
            "risk_level": risk.get("risk_level"),
            "required_action": risk.get("required_action"),
        },
        "therapist_action": {
            "moves": move_row.get("micro_moves") or [],
            "move_types": [
                move.get("move_type")
                for move in (move_row.get("micro_moves") or [])
                if isinstance(move, dict)
            ],
            "move_functions": [
                move.get("move_function")
                for move in (move_row.get("micro_moves") or [])
                if isinstance(move, dict)
            ],
            "clinical_qualities": [
                move.get("clinical_quality")
                for move in (move_row.get("micro_moves") or [])
                if isinstance(move, dict)
            ],
        },
        "client_response": {
            "response_type": response.get("response_type"),
            "response_evidence_turn": response.get("response_evidence_turn"),
            "affect_delta": response.get("affect_delta"),
            "disclosure_depth_delta": response.get("disclosure_depth_delta"),
            "alliance_delta": response.get("alliance_delta"),
            "readiness_delta": response.get("readiness_delta"),
            "risk_delta": response.get("risk_delta"),
            "notes": response.get("notes"),
        },
        "transition": {
            "pre_state_ids": triple.get("pre_state_ids") or [],
            "move_ids": triple.get("move_ids") or [],
            "response_type": triple.get("response_type"),
            "transition_type": triple.get("transition_type"),
            "summary": triple.get("summary"),
            "confidence": triple.get("confidence"),
        },
        "review": {
            "final_decision": review_row.get("final_decision"),
            "priority_issue": review_row.get("priority_issue"),
            "target_stage": review_row.get("target_stage"),
            "retention_label": retention_label,
            "feedback": review_row.get("feedback"),
        },
        "audit_summary": summarize_audits(state_row, move_row, response_row, transition_row, review_row),
        "reextract_trace": review_row.get("reextract_trace") or [],
    }


def temporal_scope_issue_stage(results: dict[str, dict[str, Any]]) -> str | None:
    for stage in ("state_risk", "response"):
        audit = (results.get(stage) or {}).get("temporal_scope_audit") or {}
        if audit.get("status") == "issue":
            return stage
    return None


def enforce_temporal_review(results: dict[str, dict[str, Any]]) -> None:
    issue_stage = temporal_scope_issue_stage(results)
    if not issue_stage:
        return
    review = results.setdefault("review", {})
    review["final_decision"] = "re_extract"
    review["priority_issue"] = "source_faithfulness"
    review["target_stage"] = issue_stage
    review["feedback"] = (
        "Temporal scope audit failed: stage evidence references turns outside its allowed time range. "
        "Re-extract the target stage using only temporally valid evidence."
    )
    review["semantic_review_audit"] = audit_review(review)


def call_stage(
    client: OpenAICompatibleClient,
    prompts: dict[str, str],
    stage: str,
    payload: dict[str, Any],
    parse_retries: int,
) -> dict[str, Any]:
    return chat_json_with_repair(
        client,
        prompts[stage],
        payload,
        parse_retries=parse_retries,
        force_json_first=True,
    )


def attach_common_window_fields(row: dict[str, Any], window: dict[str, Any], id_map: dict[str, Any]) -> dict[str, Any]:
    row["pilot_window_id"] = window.get("pilot_window_id")
    row["pilot_case_id"] = window.get("pilot_case_id")
    row["dataset"] = window.get("dataset")
    row["source_id"] = window.get("source_id")
    row["center_counselor_turn_id"] = window.get("center_counselor_turn_id")
    row["id_mapping"] = id_map
    return row


def run_window_pipeline_once(
    client: OpenAICompatibleClient,
    prompts: dict[str, str],
    options: dict[str, list[str]],
    window_payload: dict[str, Any],
    parse_retries: int,
    *,
    start_stage: str = "state_risk",
    previous: dict[str, dict[str, Any]] | None = None,
    review_feedback: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = dict(previous or {})
    if start_stage not in PIPELINE_STAGES:
        raise ValueError(f"unsupported re-extract stage: {start_stage}")
    start_idx = PIPELINE_STAGES.index(start_stage)
    feedback_payload = {"review_feedback": review_feedback} if review_feedback else {}

    if start_idx <= PIPELINE_STAGES.index("state_risk"):
        payload = slice_window_payload_for_stage(window_payload, "state_risk")
        payload.update(feedback_payload)
        state_row = call_stage(client, prompts, "state_risk", payload, parse_retries)
        state_row = normalize_state_risk_labels(state_row, options)
        state_row = repair_risk_consistency(state_row)
        state_row["normalization_audit"] = audit_state_risk(state_row, options)
        state_row["temporal_scope_audit"] = audit_state_temporal_scope(state_row, window_payload)
        results["state_risk"] = state_row

    if start_idx <= PIPELINE_STAGES.index("move"):
        payload = slice_window_payload_for_stage(window_payload, "move")
        payload["state_risk_result"] = results["state_risk"]
        payload.update(feedback_payload)
        move_row = call_stage(client, prompts, "move", payload, parse_retries)
        move_row = repair_move_scope(move_row, window_payload)
        move_row = normalize_move_labels(move_row, options)
        move_row = flag_invalid_move_labels(move_row, options)
        move_row["normalization_audit"] = audit_move(move_row, options)
        results["move"] = move_row

    if start_idx <= PIPELINE_STAGES.index("response"):
        payload = slice_window_payload_for_stage(window_payload, "response")
        payload["state_risk_result"] = results["state_risk"]
        payload["move_result"] = results["move"]
        payload.update(feedback_payload)
        response_row = call_stage(client, prompts, "response", payload, parse_retries)
        response_row = normalize_response_labels(response_row, options)
        response_row["normalization_audit"] = audit_response(response_row, options)
        response_row["temporal_scope_audit"] = audit_response_temporal_scope(response_row, window_payload)
        results["response"] = response_row

    if start_idx <= PIPELINE_STAGES.index("transition"):
        payload = slice_window_payload_for_stage(window_payload, "transition")
        payload["state_risk_result"] = results["state_risk"]
        payload["move_result"] = results["move"]
        payload["client_response_result"] = results["response"]
        payload.update(feedback_payload)
        transition_row = call_stage(client, prompts, "transition", payload, parse_retries)
        transition_row = normalize_transition_labels(transition_row, options)
        transition_row["normalization_audit"] = audit_transition(transition_row, options)
        results["transition"] = transition_row

    review_payload = slice_window_payload_for_stage(window_payload, "review")
    review_payload["state_risk_result"] = results["state_risk"]
    review_payload["move_result"] = results["move"]
    review_payload["client_response_result"] = results["response"]
    review_payload["transition_result"] = results["transition"]
    if review_feedback:
        review_payload["review_feedback_applied"] = review_feedback
    review_row = call_stage(client, prompts, "review", review_payload, parse_retries)
    review_row["semantic_review_audit"] = audit_review(review_row)
    results["review"] = review_row
    enforce_temporal_review(results)
    return results


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if output_dir_has_run_artifacts(out_dir) and not args.resume and not args.overwrite:
        raise FileExistsError(
            f"output directory already contains run artifacts: {out_dir}. "
            "Use --resume to continue or choose a new --out-dir."
        )

    model_config = merged_model_config(load_yaml(args.extraction_config), load_yaml(args.model_config))
    client = OpenAICompatibleClient(model_config)
    registry = load_yaml(args.registry)
    options = registry_options(registry)
    prompts = {}
    prompt_hashes = {}
    for stage, path in STAGE_PROMPTS.items():
        prompts[stage], prompt_hashes[stage] = prompt_text(path, options, stage)

    all_windows = read_jsonl(args.input)
    selected_windows = select_windows(all_windows, args)
    cases = {row["pilot_case_id"]: row for row in read_jsonl(args.cases)}
    selected_case_ids = sorted({row["pilot_case_id"] for row in selected_windows})
    windows_by_case = defaultdict(list)
    for window in all_windows:
        if window.get("pilot_case_id") in selected_case_ids:
            windows_by_case[window["pilot_case_id"]].append(window)

    counters = Counter()
    started_at = datetime.now(timezone.utc).isoformat()
    case_summaries: dict[str, dict[str, Any]] = {}
    completed_case_ids = completed_ids(out_dir / STAGE_OUTPUTS["case_trajectory"], "pilot_case_id", success_only=True)
    completed_window_ids = completed_ids(out_dir / STAGE_OUTPUTS["transition_unit"], "source_window_id", success_only=True)

    for case_id in selected_case_ids:
        if args.resume and case_id in completed_case_ids:
            counters["case_trajectory_skipped_existing"] += 1
            for row in read_jsonl(out_dir / STAGE_OUTPUTS["case_trajectory"]):
                if str(row.get("pilot_case_id")) == case_id and not row.get("failure"):
                    case_summaries[case_id] = row
                    break
            print(f"{case_id}: case_trajectory skipped_existing", flush=True)
            continue
        case_payload, case_id_map = build_case_payload(case_id, windows_by_case, cases.get(case_id))
        try:
            row = call_stage(client, prompts, "case_trajectory", case_payload, args.parse_retries)
            row["pilot_case_id"] = case_id
            row["dataset"] = (cases.get(case_id) or {}).get("dataset")
            row["source_id"] = (cases.get(case_id) or {}).get("source_id")
            row["id_mapping"] = case_id_map
            row["turn_source"] = case_id_map["turn_source"]
            case_summaries[case_id] = row
            counters["case_trajectory_success"] += 1
        except Exception as exc:  # noqa: BLE001
            row = {
                "pilot_case_id": case_id,
                "failure": {"stage": "case_trajectory", "error": f"{type(exc).__name__}: {exc}"},
            }
            counters["case_trajectory_failure"] += 1
        append_jsonl(out_dir / STAGE_OUTPUTS["case_trajectory"], row)
        print(f"{case_id}: case_trajectory {'failure' if row.get('failure') else 'success'}", flush=True)

    for window in selected_windows:
        window_payload, id_map = build_window_payload(window, cases.get(window.get("pilot_case_id")), case_summaries.get(window.get("pilot_case_id")))
        window_id = window.get("pilot_window_id")
        if args.resume and str(window_id) in completed_window_ids:
            counters["window_skipped_existing"] += 1
            print(f"{window_id}: staged skipped_existing", flush=True)
            continue
        try:
            results = run_window_pipeline_once(
                client,
                prompts,
                options,
                window_payload,
                args.parse_retries,
            )
            if args.debug_force_reextract_stage:
                results["review"]["final_decision"] = "re_extract"
                results["review"]["priority_issue"] = "source_faithfulness"
                results["review"]["target_stage"] = args.debug_force_reextract_stage
                results["review"]["feedback"] = (
                    "Debug forced re-extraction: rerun the target stage and downstream stages while preserving evidence."
                )
                results["review"]["semantic_review_audit"] = audit_review(results["review"])
            reextract_trace = []
            for attempt in range(args.review_reextract_attempts):
                review_row = results["review"]
                if review_row.get("final_decision") != "re_extract":
                    break
                target_stage = review_row.get("target_stage")
                if target_stage not in {"state_risk", "move", "response", "transition"}:
                    break
                feedback = {
                    "attempt": attempt + 1,
                    "target_stage": target_stage,
                    "priority_issue": review_row.get("priority_issue"),
                    "feedback": review_row.get("feedback"),
                    "previous_review": review_row,
                }
                reextract_trace.append(feedback)
                counters["review_triggered_reextract"] += 1
                results = run_window_pipeline_once(
                    client,
                    prompts,
                    options,
                    window_payload,
                    args.parse_retries,
                    start_stage=target_stage,
                    previous=results,
                    review_feedback=feedback,
                )
            for stage, row in results.items():
                row["reextract_trace"] = reextract_trace
            state_row = attach_common_window_fields(results["state_risk"], window, id_map)
            count_label_normalization(counters, "state_risk", state_row)
            if state_row["risk_consistency_repair"]["status"] == "repaired":
                counters["risk_consistency_repaired"] += 1
            append_jsonl(out_dir / STAGE_OUTPUTS["state_risk"], state_row)
            counters["state_risk_success"] += 1

            move_row = attach_common_window_fields(results["move"], window, id_map)
            count_label_normalization(counters, "move", move_row)
            if move_row["scope_repair"]["status"] == "repaired":
                counters["move_scope_repaired"] += 1
            if move_row["label_issue"]["status"] == "issue":
                counters["move_label_issue"] += 1
            append_jsonl(out_dir / STAGE_OUTPUTS["move"], move_row)
            counters["move_success"] += 1

            response_row = attach_common_window_fields(results["response"], window, id_map)
            count_label_normalization(counters, "response", response_row)
            append_jsonl(out_dir / STAGE_OUTPUTS["response"], response_row)
            counters["response_success"] += 1

            transition_row = attach_common_window_fields(results["transition"], window, id_map)
            count_label_normalization(counters, "transition", transition_row)
            append_jsonl(out_dir / STAGE_OUTPUTS["transition"], transition_row)
            counters["transition_success"] += 1

            review_row = attach_common_window_fields(results["review"], window, id_map)
            append_jsonl(out_dir / STAGE_OUTPUTS["review"], review_row)
            if review_row["semantic_review_audit"]["status"] != "pass":
                counters["review_audit_issue"] += 1
            counters["review_success"] += 1

            transition_unit = build_transition_unit(
                window,
                cases.get(window.get("pilot_case_id")),
                id_map,
                state_row,
                move_row,
                response_row,
                transition_row,
                review_row,
            )
            append_jsonl(out_dir / STAGE_OUTPUTS["transition_unit"], transition_unit)
            counters["transition_unit_success"] += 1
            counters[f"retention::{transition_unit['review']['retention_label']}"] += 1
            print(f"{window_id}: staged success", flush=True)
        except Exception as exc:  # noqa: BLE001
            failure = attach_common_window_fields(
                {"failure": {"stage": "window_pipeline", "error": f"{type(exc).__name__}: {exc}"}},
                window,
                id_map,
            )
            append_jsonl(out_dir / "window_failures.jsonl", failure)
            counters["window_pipeline_failure"] += 1
            print(f"{window_id}: staged failure", flush=True)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "input": args.input,
        "cases": args.cases,
        "output_dir": str(out_dir),
        "selected_window_count": len(selected_windows),
        "selected_case_count": len(selected_case_ids),
        "window_ids": args.window_ids,
        "datasets": args.datasets,
        "resume": args.resume,
        "overwrite": args.overwrite,
        "model": {
            "name": model_config.get("name"),
            "base_url": model_config.get("base_url"),
            "temperature": model_config.get("temperature"),
            "thinking": model_config.get("thinking"),
            "response_format_json": model_config.get("response_format_json"),
        },
        "registry": args.registry,
        "prompts": {
            stage: {
                "path": STAGE_PROMPTS[stage],
                "sha256": prompt_hashes[stage],
                "normalization_option_fields": STAGE_OPTION_FIELDS[stage],
            }
            for stage in STAGE_PROMPTS
        },
        "outputs": {stage: str(out_dir / filename) for stage, filename in STAGE_OUTPUTS.items()},
        "counts": dict(counters),
        "prompting_policy": {
            "direct_llm_prompt": True,
            "staged_extraction": True,
            "id_mapping": "complex source/window/turn IDs are mapped to simple local IDs before prompting and restored in output id_mapping",
            "case_turn_source": "case trajectory reconstructs ordered turns from all evidence windows available for the selected case in the input file",
            "generated_rationale_policy": "text inside <think>...</think> is removed before prompting and exposed only as generated_rationale_hint_present",
            "system_prompt_policy": "source system prompts are not sent as dialogue evidence; therapy framework is exposed only as style_or_framework_context",
            "lexical_risk_hint_policy": "lexical_risk_hint may be retained in runner-managed source metadata, but it is not sent to extraction prompts as risk evidence",
        },
        "method_boundary": {
            "semantic_extraction": "LLM stages only",
            "semantic_review": "LLM semantic review with bounded re-extraction",
            "keyword_semantic_guard": False,
            "deterministic_code_allowed_roles": [
                "JSON repair and parsing",
                "enum and schema audit",
                "simple ID mapping",
                "format-only enum canonicalization",
                "center-turn evidence scope repair",
                "mutually exclusive risk-domain consistency repair",
                "unresolved invalid enum flagging without deterministic fallback",
                "sidecar retention label derivation from review outputs",
                "summary statistics",
            ],
            "enum_canonicalization_policy": {
                "allowed_methods": [
                    "Unicode NFKC normalization",
                    "case folding",
                    "whitespace hyphen slash punctuation to underscore",
                    "simple singular/plural variants",
                    "unique bounded edit-distance match",
                ],
                "max_edit_distance_by_canonical_length": {
                    "<=6": 0,
                    "7-14": 2,
                    ">=15": 3,
                },
                "semantic_fallback_label": False,
                "unresolved_behavior": "leave original value and record label_issue or normalization_audit issue",
            },
            "deterministic_code_disallowed_roles": [
                "keyword-based clinical risk classification",
                "keyword-based promotion or deletion",
                "heuristic replacement of LLM or human semantic decisions",
                "default therapeutic content insertion",
            ],
        },
    }
    write_manifest(out_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
