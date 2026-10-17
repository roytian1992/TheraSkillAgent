#!/usr/bin/env python3
"""Build deterministic patient case registry entries for the patient simulator."""

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
    ANNA_HIDDEN_PATIENT_PROFILE_PATTERN_PATH,
    ANNA_HISTORICAL_CASE_MEMORY_SEED_PATH,
    ANNA_PATIENT_PERSONA_PROFILE_PATH,
    ANNA_REPORT_TO_CONVERSATION_BRIDGE_PATTERN_PATH,
    SIMULATION_PATIENT_CASE_REGISTRY_MANIFEST_PATH,
    SIMULATION_PATIENT_CASE_REGISTRY_PATH,
    SIMULATION_PATIENT_CASE_REGISTRY_QA_PATH,
    SIMULATION_PATIENT_CASE_REGISTRY_SUMMARY_PATH,
    SIMULATION_PROTOCOL_PATTERN_PATH,
    SIMULATION_RUNTIME_PATIENT_CASES_DIR,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PERSONA = ANNA_PATIENT_PERSONA_PROFILE_PATH
DEFAULT_HIDDEN = ANNA_HIDDEN_PATIENT_PROFILE_PATTERN_PATH
DEFAULT_MEMORY = ANNA_HISTORICAL_CASE_MEMORY_SEED_PATH
DEFAULT_BRIDGE = ANNA_REPORT_TO_CONVERSATION_BRIDGE_PATTERN_PATH
DEFAULT_PROTOCOL = SIMULATION_PROTOCOL_PATTERN_PATH

DEFAULT_OUTPUT = SIMULATION_PATIENT_CASE_REGISTRY_PATH
DEFAULT_SUMMARY = SIMULATION_PATIENT_CASE_REGISTRY_SUMMARY_PATH
DEFAULT_MANIFEST = SIMULATION_PATIENT_CASE_REGISTRY_MANIFEST_PATH
DEFAULT_QA = SIMULATION_PATIENT_CASE_REGISTRY_QA_PATH
DEFAULT_BUNDLE_DIR = SIMULATION_RUNTIME_PATIENT_CASES_DIR

HIGH_RISK_KEYWORDS = (
    "自杀",
    "自残",
    "轻生",
    "伤人",
    "急诊",
    "住院",
)
ELEVATED_RISK_KEYWORDS = (
    "抑郁",
    "惊恐",
    "创伤",
    "暴力",
    "家暴",
    "离家",
    "失眠",
    "崩溃",
    "绝望",
    "危机",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona-jsonl", default=str(DEFAULT_PERSONA))
    parser.add_argument("--hidden-jsonl", default=str(DEFAULT_HIDDEN))
    parser.add_argument("--memory-jsonl", default=str(DEFAULT_MEMORY))
    parser.add_argument("--bridge-jsonl", default=str(DEFAULT_BRIDGE))
    parser.add_argument("--protocol-jsonl", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--bundle-dir", default=str(DEFAULT_BUNDLE_DIR))
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--overwrite", action="store_true")
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


def collect_case_blob(*parts: Any) -> str:
    flat: list[str] = []
    for part in parts:
        if isinstance(part, list):
            flat.extend(clean_text(item, 200) for item in part if clean_text(item, 200))
        elif isinstance(part, dict):
            flat.extend(clean_text(value, 200) for value in part.values() if clean_text(value, 200))
        else:
            text = clean_text(part, 200)
            if text:
                flat.append(text)
    return " ".join(flat)


def infer_risk_band(persona: dict[str, Any], memory: dict[str, Any]) -> str:
    blob = collect_case_blob(
        persona.get("distress_or_risk_flags"),
        persona.get("symptom_profile"),
        memory.get("risk_watchpoints"),
        persona.get("presenting_problem_profile"),
    )
    if any(keyword in blob for keyword in HIGH_RISK_KEYWORDS):
        return "high"
    if any(keyword in blob for keyword in ELEVATED_RISK_KEYWORDS):
        return "elevated"
    return "low"


def infer_disclosure_band(persona: dict[str, Any], hidden: dict[str, Any]) -> str:
    helper_blob = collect_case_blob(persona.get("help_seeking_style"))
    if "主动求助" in helper_blob or "表达清晰" in helper_blob:
        if len(hidden.get("withheld_or_unspoken_concerns") or []) >= 3:
            return "surface_to_moderate"
        return "moderate"
    if len(hidden.get("disclosure_conditions") or []) >= 2:
        return "surface"
    return "guarded"


def infer_difficulty_band(persona: dict[str, Any], hidden: dict[str, Any], memory: dict[str, Any], risk_band: str) -> str:
    score = 0
    score += min(len(hidden.get("withheld_or_unspoken_concerns") or []), 3)
    score += min(len(hidden.get("rupture_triggers") or []), 2)
    score += min(len(memory.get("unresolved_threads") or []), 2)
    if risk_band == "elevated":
        score += 2
    elif risk_band == "high":
        score += 4
    if "依赖专业建议" in collect_case_blob(persona.get("help_seeking_style")):
        score -= 1
    if score >= 6:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def infer_allowed_session_count(memory: dict[str, Any], difficulty_band: str) -> int:
    unresolved = len(memory.get("unresolved_threads") or [])
    count = 3
    if difficulty_band == "high":
        count += 1
    if unresolved >= 3:
        count += 1
    return max(3, min(count, 5))


def build_scenario_tags(source: dict[str, Any], persona: dict[str, Any], hidden: dict[str, Any], memory: dict[str, Any], risk_band: str) -> list[str]:
    tags: list[str] = []
    tags.extend(str(item).strip() for item in (source.get("case_categories") or []) if str(item).strip())
    tags.extend(str(item).strip() for item in (persona.get("presenting_problem_profile") or [])[:4] if str(item).strip())
    tags.extend(str(item).strip() for item in (persona.get("emotional_profile") or [])[:3] if str(item).strip())
    tags.extend(str(item).strip() for item in (memory.get("relationship_context") or [])[:2] if str(item).strip())
    tags.extend(str(item).strip() for item in (hidden.get("latent_core_conflicts") or [])[:2] if str(item).strip())
    tags.append(f"risk::{risk_band}")
    return uniq_keep_order(tags)[:16]


def build_initial_session_state(persona: dict[str, Any], hidden: dict[str, Any], memory: dict[str, Any], risk_band: str) -> dict[str, Any]:
    return {
        "emotion_state": uniq_keep_order((persona.get("emotional_profile") or [])[:3]),
        "alliance_state": "pre_session_or_early_contact",
        "disclosure_state": infer_disclosure_band(persona, hidden),
        "resistance_state": "guarded" if (hidden.get("rupture_triggers") or hidden.get("withheld_or_unspoken_concerns")) else "low_to_moderate",
        "readiness_state": "ready_for_guided_exploration"
        if "开放" in collect_case_blob(persona.get("motivation_or_readiness_profile"))
        or "愿意" in collect_case_blob(persona.get("motivation_or_readiness_profile"))
        else "ambivalent_but_engaged",
        "risk_state": risk_band,
        "topic_focus": uniq_keep_order((persona.get("presenting_problem_profile") or [])[:4]),
        "felt_understanding": "unknown_pre_session",
        "unfinished_threads": uniq_keep_order((memory.get("unresolved_threads") or [])[:5]),
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    for path in (output_path, Path(args.summary), Path(args.manifest), Path(args.qa_report)):
        ensure_output_path(path, overwrite=args.overwrite)

    persona_rows = read_jsonl(args.persona_jsonl)
    hidden_rows = read_jsonl(args.hidden_jsonl)
    memory_rows = read_jsonl(args.memory_jsonl)
    bridge_rows = read_jsonl(args.bridge_jsonl)
    protocol_rows = read_jsonl(args.protocol_jsonl)

    persona_map = index_by_source_id(persona_rows)
    hidden_map = index_by_source_id(hidden_rows)
    memory_map = index_by_source_id(memory_rows)
    bridge_map = index_by_source_id(bridge_rows)
    protocol_by_mode = protocol_ids_by_mode(protocol_rows)

    shared_source_ids = sorted(set(persona_map) & set(hidden_map) & set(memory_map) & set(bridge_map))
    if args.limit_cases is not None:
        shared_source_ids = shared_source_ids[: args.limit_cases]

    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    risk_counter: Counter[str] = Counter()
    difficulty_counter: Counter[str] = Counter()

    patient_protocol_ids = protocol_by_mode.get("patient_simulator") or []
    reviewer_protocol_ids = protocol_by_mode.get("simulation_reviewer") or []
    if not patient_protocol_ids:
        issues.append({"issue": "missing_patient_protocol_units", "simulation_mode": "patient_simulator"})
    if not reviewer_protocol_ids:
        issues.append({"issue": "missing_reviewer_protocol_units", "simulation_mode": "simulation_reviewer"})

    for source_id in shared_source_ids:
        persona_row = persona_map[source_id]
        hidden_row = hidden_map[source_id]
        memory_row = memory_map[source_id]
        bridge_row = bridge_map[source_id]

        source = persona_row.get("source") or {}
        persona = persona_row.get("content") or {}
        hidden = hidden_row.get("content") or {}
        memory = memory_row.get("content") or {}

        case_id = clean_text(persona.get("case_id"), 120)
        case_index = persona.get("case_index")
        case_title = clean_text(source.get("case_title"), 220)
        case_categories = [clean_text(item, 80) for item in (source.get("case_categories") or []) if clean_text(item, 80)]
        case_family = case_categories[0] if case_categories else "uncategorized"
        risk_band = infer_risk_band(persona, memory)
        difficulty_band = infer_difficulty_band(persona, hidden, memory, risk_band)
        allowed_session_count = infer_allowed_session_count(memory, difficulty_band)
        scenario_tags = build_scenario_tags(source, persona, hidden, memory, risk_band)
        bundle_path = Path(args.bundle_dir) / f"{case_id}.json"
        entry_id = f"patient_case::{case_id}"
        initial_state = build_initial_session_state(persona, hidden, memory, risk_band)

        risk_counter[risk_band] += 1
        difficulty_counter[difficulty_band] += 1

        if not case_id:
            issues.append({"issue": "missing_case_id", "source_id": source_id})
        if not scenario_tags:
            issues.append({"issue": "missing_scenario_tags", "source_id": source_id})

        rows.append(
            {
                "schema_version": "theraskill_patient_case_registry_entry_v1",
                "registry_id": entry_id,
                "case_id": case_id,
                "source_id": source_id,
                "case_index": case_index,
                "case_title": case_title,
                "case_family": case_family,
                "case_categories": case_categories,
                "source_unit_ids": {
                    "patient_persona_profile": persona_row.get("unit_id"),
                    "hidden_patient_profile_pattern": hidden_row.get("unit_id"),
                    "historical_case_memory_seed": memory_row.get("unit_id"),
                    "report_to_conversation_bridge_pattern": bridge_row.get("unit_id"),
                },
                "profile_bundle_ref": {"path": str(bundle_path), "section": "authoritative_layer.visible_profile"},
                "formulation_bundle_ref": {"path": str(bundle_path), "section": "authoritative_layer.patient_formulation"},
                "initial_session_state": initial_state,
                "allowed_session_count": allowed_session_count,
                "scenario_tags": scenario_tags,
                "risk_band": risk_band,
                "difficulty_band": difficulty_band,
                "protocol_refs": {
                    "patient_simulator": patient_protocol_ids,
                    "simulation_reviewer": reviewer_protocol_ids,
                },
                "bundle_id": f"pcb_{stable_hash(source_id)}",
            }
        )

    registry_sha = write_jsonl(
        output_path,
        rows,
        overwrite=args.overwrite,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )

    summary = {
        "schema_version": "theraskill_patient_case_registry_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "registry_rows": len(rows),
        "risk_band_distribution": dict(sorted(risk_counter.items())),
        "difficulty_band_distribution": dict(sorted(difficulty_counter.items())),
        "patient_protocol_count": len(patient_protocol_ids),
        "reviewer_protocol_count": len(reviewer_protocol_ids),
        "issues_count": len(issues),
    }
    write_json(args.summary, summary, overwrite=args.overwrite)

    manifest_status = "complete" if not issues else "incomplete"
    manifest = {
        "schema_version": "theraskill_patient_case_registry_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest_status,
        "registry": {
            "path": str(output_path),
            "rows": len(rows),
            "sha256": registry_sha,
        },
        "summary": {"path": str(args.summary), "sha256": file_sha256(args.summary)},
        "inputs": {
            "persona_jsonl": {"path": str(args.persona_jsonl), "sha256": file_sha256(args.persona_jsonl)},
            "hidden_jsonl": {"path": str(args.hidden_jsonl), "sha256": file_sha256(args.hidden_jsonl)},
            "memory_jsonl": {"path": str(args.memory_jsonl), "sha256": file_sha256(args.memory_jsonl)},
            "bridge_jsonl": {"path": str(args.bridge_jsonl), "sha256": file_sha256(args.bridge_jsonl)},
            "protocol_jsonl": {"path": str(args.protocol_jsonl), "sha256": file_sha256(args.protocol_jsonl)},
        },
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest, overwrite=args.overwrite)

    qa = {
        "schema_version": "theraskill_patient_case_registry_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest_status,
        "registry_rows": len(rows),
        "shared_source_ids": len(shared_source_ids),
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(args.qa_report, qa, overwrite=args.overwrite)
    print(json.dumps({"status": manifest_status, "registry_rows": len(rows), "issues_count": len(issues)}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
