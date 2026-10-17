#!/usr/bin/env python3
"""Build canonical Anna-CPsyCounD projection manifest/QA from current outputs.

This script is the authoritative finalization step for Anna-CPsyCounD raw
knowledge projection status. It can be run repeatedly during long-running shard
extraction and will emit `status=partial` until all source cases are covered.
When canonical outputs reach full aligned coverage with no failures, the same
script emits `status=complete`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anna_cpsycound_surface_registry import (
    DATASET_ROOT,
    FAILURE_OUTPUT_PATH,
    MANIFEST_OUTPUT_PATH,
    OUTPUT_PATHS,
    PROMPT_PATH,
    QA_OUTPUT_PATH,
    README_FILE,
    SOURCE_FILE,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

DATASET = "Anna-CPsyCounD"
SOURCE_ROOT = str(DATASET_ROOT)
PROMPT = str(PROMPT_PATH.relative_to(PROJECT_ROOT))

OUTPUT_MAP = {name: str(path.relative_to(PROJECT_ROOT)) for name, path in OUTPUT_PATHS.items()}
FAILURE_OUTPUT = str(FAILURE_OUTPUT_PATH.relative_to(PROJECT_ROOT))
MANIFEST_OUTPUT = str(MANIFEST_OUTPUT_PATH.relative_to(PROJECT_ROOT))
QA_OUTPUT = str(QA_OUTPUT_PATH.relative_to(PROJECT_ROOT))

EXPECTED_FIELDS = {
    "patient_persona_profile": [
        "demographic_profile",
        "presenting_problem_profile",
        "symptom_profile",
        "emotional_profile",
        "cognitive_style",
        "interpersonal_context",
        "help_seeking_style",
        "motivation_or_readiness_profile",
        "protective_factor_profile",
        "distress_or_risk_flags",
        "evidence_spans",
        "quality_status",
    ],
    "hidden_patient_profile_pattern": [
        "surface_presenting_story",
        "latent_core_conflicts",
        "withheld_or_unspoken_concerns",
        "disclosure_conditions",
        "alliance_sensitivity_signals",
        "rupture_triggers",
        "trust_building_signals",
        "response_shift_conditions",
        "memory_update_hooks",
        "evidence_spans",
        "quality_status",
    ],
    "historical_case_memory_seed": [
        "stable_identity_facts",
        "longitudinal_problem_summary",
        "prior_counseling_progress",
        "previously_used_techniques",
        "unresolved_threads",
        "resource_signals",
        "relationship_context",
        "risk_watchpoints",
        "next_session_entry_points",
        "evidence_spans",
        "quality_status",
    ],
    "report_to_conversation_bridge_pattern": [
        "report_summary_to_dialogue_alignment",
        "stable_themes_across_report_and_dialogue",
        "report_only_information",
        "dialogue_only_information",
        "continuity_signals",
        "mismatch_or_tension_signals",
        "simulation_use_notes",
        "evidence_spans",
        "quality_status",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--source-file", default=SOURCE_FILE)
    parser.add_argument("--manifest-output", default=MANIFEST_OUTPUT)
    parser.add_argument("--qa-output", default=QA_OUTPUT)
    parser.add_argument("--failure-output", default=FAILURE_OUTPUT)
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def resolve_path(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


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


def extract_case_ids(rows: list[dict[str, Any]]) -> list[str]:
    case_ids = []
    for row in rows:
        case_id = (row.get("content") or {}).get("case_id")
        if case_id is not None:
            case_ids.append(str(case_id))
    return case_ids


def duplicate_case_id_count(case_ids: list[str]) -> int:
    return len(case_ids) - len(set(case_ids))


def missing_field_counts(rows: list[dict[str, Any]], expected_fields: list[str]) -> dict[str, int]:
    counts = {field: 0 for field in expected_fields}
    for row in rows:
        content = row.get("content") or {}
        for field in expected_fields:
            value = content.get(field)
            if value in (None, "", [], {}):
                counts[field] += 1
    return counts


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root)
    source_rows = read_json(source_root / args.source_file)
    total_case_count = len(source_rows)

    output_rows = {name: read_jsonl(resolve_path(path)) for name, path in OUTPUT_MAP.items()}
    output_case_ids = {name: extract_case_ids(rows) for name, rows in output_rows.items()}
    output_case_sets = {name: set(case_ids) for name, case_ids in output_case_ids.items()}
    duplicate_counts = {name: duplicate_case_id_count(case_ids) for name, case_ids in output_case_ids.items()}

    aligned = len({frozenset(case_ids) for case_ids in output_case_sets.values()}) == 1 if output_case_sets else True
    union_case_ids = set().union(*output_case_sets.values()) if output_case_sets else set()
    intersection_case_ids = set.intersection(*output_case_sets.values()) if output_case_sets else set()
    failures = read_jsonl(resolve_path(args.failure_output))

    row_counts = {name: len(rows) for name, rows in output_rows.items()}
    unique_case_counts = {name: len(output_case_sets[name]) for name in output_case_sets}
    all_outputs_full = all(count == total_case_count for count in row_counts.values()) if row_counts else False
    no_duplicates = all(count == 0 for count in duplicate_counts.values())
    no_failures = len(failures) == 0
    complete = aligned and all_outputs_full and no_duplicates and no_failures and len(union_case_ids) == total_case_count
    status = "complete" if complete else "partial"

    manifest = {
        "schema_version": "theraskill_anna_cpsycound_persona_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": status,
        "source_root": str(source_root),
        "source_files": {
            args.source_file: {"path": str(source_root / args.source_file), "sha256": file_sha256(source_root / args.source_file)},
            README_FILE: {"path": str(source_root / README_FILE), "sha256": file_sha256(source_root / README_FILE)},
            str(resolve_path(PROMPT)): {"path": str(resolve_path(PROMPT)), "sha256": file_sha256(resolve_path(PROMPT))},
        },
        "case_count_total": total_case_count,
        "case_count_attempted": len(union_case_ids),
        "subset_mode": status != "complete",
        "written_cases": len(union_case_ids),
        "failure_rows": len(failures),
        "projection_policy": "Canonical Anna-CPsyCounD projection finalization over patient persona profile, hidden profile, historical memory seed, and report-dialogue bridge knowledge.",
        "prompt_audit_summary": None,
        "counters": {
            "row_counts": row_counts,
            "unique_case_counts": unique_case_counts,
            "duplicate_case_id_counts": duplicate_counts,
            "all_outputs_share_same_case_ids": aligned,
            "union_case_count": len(union_case_ids),
            "intersection_case_count": len(intersection_case_ids),
        },
        "outputs": {
            name: {
                "path": str(resolve_path(path)),
                "sha256": file_sha256(resolve_path(path)),
                "row_count": row_counts[name],
                "unique_case_count": unique_case_counts[name],
                "duplicate_case_id_count": duplicate_counts[name],
            }
            for name, path in OUTPUT_MAP.items()
        },
        "failure_log": {"path": str(resolve_path(args.failure_output)), "sha256": file_sha256(resolve_path(args.failure_output))},
        "completion_policy": "status=complete iff all four canonical outputs each contain exactly one row per source case, share the same case-id set, contain no duplicate case ids, and no failure rows remain.",
    }
    write_json(resolve_path(args.manifest_output), manifest)

    qa = {
        "schema_version": "theraskill_anna_cpsycound_persona_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": status,
        "row_counts": row_counts,
        "unique_case_counts": unique_case_counts,
        "duplicate_case_id_counts": duplicate_counts,
        "failure_count": len(failures),
        "prompt_audit_summary": None,
        "case_id_alignment": {
            "all_outputs_share_same_case_ids": aligned,
            "union_case_count": len(union_case_ids),
            "intersection_case_count": len(intersection_case_ids),
        },
        "coverage_summary": {
            "source_total": total_case_count,
            "covered_case_count": len(union_case_ids),
        },
        "missing_field_counts": {
            name: missing_field_counts(output_rows[name], EXPECTED_FIELDS[name])
            for name in output_rows
        },
        "issues": failures,
        "issues_count": len(failures),
        "qa_scope": "canonical_projection_snapshot",
    }
    write_json(resolve_path(args.qa_output), qa)

    print(
        json.dumps(
            {
                "status": status,
                "row_counts": row_counts,
                "covered_case_count": len(union_case_ids),
                "source_total": total_case_count,
                "failure_count": len(failures),
                "all_outputs_share_same_case_ids": aligned,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.require_complete and status != "complete":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
