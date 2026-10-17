#!/usr/bin/env python3
"""Merge Anna-CPsyCounD shard outputs into canonical raw knowledge files.

Also writes a canonical partial manifest/QA snapshot so long-running shard
progress can be tracked from authoritative files under outputs/knowledge/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    SHARD_DIR as SHARD_DIR_PATH,
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
SHARD_DIR = str(SHARD_DIR_PATH.relative_to(PROJECT_ROOT))

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
    parser.add_argument("--shard-dir", default=SHARD_DIR)
    parser.add_argument("--failure-output", default=FAILURE_OUTPUT)
    parser.add_argument("--manifest-output", default=MANIFEST_OUTPUT)
    parser.add_argument("--qa-output", default=QA_OUTPUT)
    parser.add_argument("--include-existing", action="store_true")
    return parser.parse_args()


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


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_path(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def file_sha256(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merge_success_rows(paths: list[Path], existing_paths: list[Path] | None = None) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, Any]] = {}
    for path in existing_paths or []:
        for row in read_jsonl(path):
            case_id = (row.get("content") or {}).get("case_id")
            if case_id:
                by_case[str(case_id)] = row
    for path in paths:
        for row in read_jsonl(path):
            case_id = (row.get("content") or {}).get("case_id")
            if case_id:
                by_case[str(case_id)] = row
    return [by_case[key] for key in sorted(by_case)]


def merge_failure_rows(paths: list[Path], success_case_ids: set[str]) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in read_jsonl(path):
            case_id = row.get("case_id")
            if case_id and str(case_id) not in success_case_ids:
                by_case[str(case_id)] = row
    return [by_case[key] for key in sorted(by_case)]


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
    shard_dir = resolve_path(args.shard_dir)

    merged_counts = {}
    success_case_ids: set[str] = set()
    merged_rows_by_type: dict[str, list[dict[str, Any]]] = {}
    for knowledge_type, output_path in OUTPUT_MAP.items():
        output_path = resolve_path(output_path)
        shard_paths = sorted(shard_dir.glob(f"{knowledge_type}.shard_*.jsonl"))
        existing_paths = [output_path] if args.include_existing else []
        merged = merge_success_rows(shard_paths, existing_paths=existing_paths)
        write_jsonl(output_path, merged)
        merged_rows_by_type[knowledge_type] = merged
        merged_counts[knowledge_type] = len(merged)
        success_case_ids.update(str((row.get("content") or {}).get("case_id")) for row in merged if (row.get("content") or {}).get("case_id"))

    failure_paths = sorted(shard_dir.glob("persona_projection_failures.shard_*.jsonl"))
    merged_failures = merge_failure_rows(failure_paths, success_case_ids)
    if merged_failures:
        write_jsonl(resolve_path(args.failure_output), merged_failures)
    else:
        failure_output = resolve_path(args.failure_output)
        if failure_output.exists():
            failure_output.unlink()

    source_root = Path(SOURCE_ROOT)
    manifest = {
        "schema_version": "theraskill_anna_cpsycound_persona_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "partial",
        "source_root": str(source_root),
        "source_files": {
            SOURCE_FILE: {"path": str(source_root / SOURCE_FILE), "sha256": file_sha256(source_root / SOURCE_FILE)},
            README_FILE: {"path": str(source_root / README_FILE), "sha256": file_sha256(source_root / README_FILE)},
            str(resolve_path(PROMPT)): {"path": str(resolve_path(PROMPT)), "sha256": file_sha256(resolve_path(PROMPT))},
        },
        "case_count_total": 3134,
        "case_count_attempted": len(success_case_ids),
        "subset_mode": True,
        "written_cases": len(success_case_ids),
        "failure_rows": len(merged_failures),
        "projection_policy": "Canonical partial snapshot merged from active Anna-CPsyCounD shard outputs; portrait/report/dialogue jointly support patient profile, hidden profile, historical memory seed, and report-dialogue bridge knowledge.",
        "prompt_audit_summary": None,
        "counters": {
            "merged_case_count": len(success_case_ids),
            "failure_case_count": len(merged_failures),
            "shard_file_count": len(list(shard_dir.glob('*.jsonl'))),
        },
        "outputs": {
            name: {
                "path": str(resolve_path(path)),
                "sha256": file_sha256(resolve_path(path)),
                "row_count": len(merged_rows_by_type[name]),
            }
            for name, path in OUTPUT_MAP.items()
        },
        "failure_log": {"path": str(resolve_path(args.failure_output)), "sha256": file_sha256(resolve_path(args.failure_output))},
        "merge_inputs": {
            "shard_dir": str(shard_dir),
            "include_existing": args.include_existing,
        },
    }
    write_json(resolve_path(args.manifest_output), manifest)

    qa = {
        "schema_version": "theraskill_anna_cpsycound_persona_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "partial",
        "row_counts": {name: len(rows) for name, rows in merged_rows_by_type.items()},
        "failure_count": len(merged_failures),
        "prompt_audit_summary": None,
        "missing_field_counts": {
            name: missing_field_counts(merged_rows_by_type[name], EXPECTED_FIELDS[name])
            for name in merged_rows_by_type
        },
        "issues": merged_failures,
        "issues_count": len(merged_failures),
        "qa_scope": "canonical_partial_snapshot",
    }
    write_json(resolve_path(args.qa_output), qa)

    summary = {
        "dataset": DATASET,
        "shard_dir": str(shard_dir),
        "merged_counts": merged_counts,
        "failure_count": len(merged_failures),
        "manifest_output": str(resolve_path(args.manifest_output)),
        "qa_output": str(resolve_path(args.qa_output)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
