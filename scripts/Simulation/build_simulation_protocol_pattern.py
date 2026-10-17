#!/usr/bin/env python3
"""Assemble Simulation.simulation_protocol_pattern units from engineered candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_json, read_jsonl, write_jsonl
from simulation_surface_registry import (
    SIMULATION_PROTOCOL_CANDIDATES_MANIFEST_PATH,
    SIMULATION_PROTOCOL_CANDIDATES_PATH,
    SIMULATION_PROTOCOL_PATTERN_PATH,
    SIMULATION_PROTOCOL_PATTERN_ENGINEERING_PROMPT_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CANDIDATES = SIMULATION_PROTOCOL_CANDIDATES_PATH
DEFAULT_CANDIDATE_MANIFEST = SIMULATION_PROTOCOL_CANDIDATES_MANIFEST_PATH
DEFAULT_OUTPUT = SIMULATION_PROTOCOL_PATTERN_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--candidate-manifest", default=str(DEFAULT_CANDIDATE_MANIFEST))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
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


def trim(values: Any, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return uniq_keep_order([clean_text(value, 240) for value in values[:limit] if clean_text(value, 240)])


def make_embedding_text(pattern: dict[str, Any]) -> str:
    chunks: list[str] = [
        pattern.get("simulation_mode", ""),
        pattern.get("protocol_family", ""),
        " ".join(pattern.get("required_input_slots") or []),
        " ".join(pattern.get("patient_visible_context") or []),
        " ".join(pattern.get("therapist_visible_context") or []),
        " ".join(pattern.get("hidden_state_rules") or []),
        " ".join(pattern.get("memory_write_rules") or []),
        " ".join(pattern.get("memory_read_rules") or []),
        " ".join(pattern.get("retrieval_trigger_rules") or []),
        " ".join(pattern.get("turn_termination_rules") or []),
        " ".join(pattern.get("risk_escalation_rules") or []),
        " ".join(pattern.get("non_goals_or_do_not_infer") or []),
    ]
    return "\n".join(chunk for chunk in chunks if chunk)[:4000]


def build_pattern(candidate: dict[str, Any]) -> dict[str, Any]:
    protocol_id = f"sppt_{stable_hash(str(candidate.get('candidate_id') or ''))}"
    return {
        "protocol_id": protocol_id,
        "simulation_mode": candidate.get("simulation_mode"),
        "protocol_family": candidate.get("protocol_family"),
        "required_input_slots": trim(candidate.get("required_input_slots_hints"), 24),
        "patient_visible_context": trim(candidate.get("patient_visible_context_hints"), 12),
        "therapist_visible_context": trim(candidate.get("therapist_visible_context_hints"), 12),
        "hidden_state_rules": trim(candidate.get("hidden_state_rules_hints"), 12),
        "memory_write_rules": trim(candidate.get("memory_write_rules_hints"), 12),
        "memory_read_rules": trim(candidate.get("memory_read_rules_hints"), 12),
        "retrieval_trigger_rules": trim(candidate.get("retrieval_trigger_rules_hints"), 12),
        "turn_termination_rules": trim(candidate.get("turn_termination_rules_hints"), 12),
        "risk_escalation_rules": trim(candidate.get("risk_escalation_rules_hints"), 12),
        "synthetic_labeling_rules": trim(candidate.get("synthetic_labeling_rules_hints"), 12),
        "review_hooks": trim(candidate.get("review_hooks_hints"), 12),
        "non_goals_or_do_not_infer": trim(candidate.get("non_goals_or_do_not_infer_hints"), 12),
        "evidence_unit_ids": trim(candidate.get("source_unit_ids"), 20),
        "quality_status": "raw_source_knowledge",
    }


def wrap_unit(candidate: dict[str, Any], candidate_manifest: dict[str, Any] | None) -> dict[str, Any]:
    pattern = build_pattern(candidate)
    candidate_id = str(candidate.get("candidate_id") or "")
    protocol_id = pattern["protocol_id"]
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": f"ku_simulation_protocol_pattern_{stable_hash(candidate_id)}",
        "knowledge_type": "simulation_protocol_pattern",
        "source": {
            "dataset": "Simulation.multisource",
            "source_id": candidate_id,
            "candidate_id": candidate_id,
            "source_unit_ids": candidate.get("source_unit_ids") or [],
            "language": "zh",
            "source_construction_type": "knowledge_engineered_from_multisource_simulation_constraints",
            "evidence_tier": "engineered_protocol_pattern",
            "citation": "Engineered from finalized simulation knowledge families rather than extracted from raw dialogue.",
        },
        "content": {
            "simulation_protocol_pattern": pattern,
        },
        "quality": {
            "review_status": "deterministically_engineered_unreviewed",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    "This unit is engineered from upstream knowledge releases rather than directly extracted from raw dialogue.",
                    "Protocol rules are designed to support runtime control surfaces and should not be interpreted as clinical fact claims.",
                    "If future runtime needs finer distinctions, protocol families may be split further without altering upstream evidence.",
                ],
            },
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "pending_projection_manifest_builder",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": make_embedding_text(pattern),
        },
        "provenance": {
            "projection": "deterministic_simulation_protocol_engineering",
            "candidate_id": candidate_id,
            "protocol_id": protocol_id,
            "engineering_prompt_path": str(SIMULATION_PROTOCOL_PATTERN_ENGINEERING_PROMPT_PATH),
            "engineering_method": "deterministic_candidate_builder_then_protocol_assembler",
            "evidence_summary": candidate.get("evidence_summary") or {},
            "engineered_from_versions": candidate.get("engineered_from_versions") or {},
            "candidate_manifest_sha256": (candidate_manifest or {}).get("output_candidates", {}).get("sha256"),
            "applicable_runtime_surface": candidate.get("applicable_runtime_surface") or [],
            "constraint_summary": candidate.get("constraint_summary"),
            "notes": candidate.get("notes"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def main() -> int:
    args = parse_args()
    candidate_manifest = read_json(args.candidate_manifest) if Path(args.candidate_manifest).exists() else None
    if candidate_manifest and candidate_manifest.get("status") != "complete":
        print(json.dumps({"status": "blocked_candidate_manifest_not_complete"}, ensure_ascii=False, indent=2))
        return 1

    output_path = Path(args.output)
    if args.overwrite and output_path.exists():
        output_path.unlink()

    candidates = read_jsonl(args.candidates)
    units = [wrap_unit(candidate, candidate_manifest) for candidate in candidates]
    write_jsonl(output_path, units, sort_keys=True, separators=(",", ":"))
    print(json.dumps({"status": "complete" if units else "partial", "output_unit_count": len(units)}, ensure_ascii=False, indent=2))
    return 0 if units else 1


if __name__ == "__main__":
    raise SystemExit(main())
