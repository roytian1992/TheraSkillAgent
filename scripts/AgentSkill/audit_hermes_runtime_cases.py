#!/usr/bin/env python3
"""Audit Hermes runtime validation case files."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, iter_jsonl, read_json, write_json
from retrieval_wrapper_common import RETRIEVAL_SCRIPT_FILENAMES
from runtime_validation_paths import (
    qwen_compatibility_v2_cases_manifest_path,
    qwen_compatibility_v2_cases_path,
    qwen_compatibility_v2_cases_qa_path,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = qwen_compatibility_v2_cases_path()
DEFAULT_MANIFEST = qwen_compatibility_v2_cases_manifest_path()
DEFAULT_QA = qwen_compatibility_v2_cases_qa_path()
SKILL_NAMES = [
    "emotional-support",
    "risk-triage",
    "cognitive-reframing-support",
    "synthetic-dialogue-builder",
    "therapy-response-reviewer",
    "rebt-belief-work-support",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--expected-mode", default="retrieval")
    parser.add_argument("--expected-retrieval-mode", default="v2_hybrid")
    parser.add_argument("--expected-retrieval-script", default=RETRIEVAL_SCRIPT_FILENAMES["v2_hybrid"])
    parser.add_argument("--expected-case-count", type=int, default=30)
    parser.add_argument("--expected-min-per-skill", type=int, default=5)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def main() -> int:
    args = parse_args()
    cases_path = Path(args.cases)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)
    ensure_output_path(qa_path, overwrite=args.overwrite)
    rows = [row for _, row in iter_jsonl(cases_path, with_line_numbers=True)]
    manifest: dict[str, Any] = read_json(manifest_path) if manifest_path.exists() else {}
    ids = [row.get("case_id") for row in rows]
    skill_counts = Counter(str(row.get("skill")) for row in rows)
    type_counts = Counter(str(row.get("case_type")) for row in rows)
    script_counts = Counter(str(row.get("retrieval_script")) for row in rows)
    issues: list[dict[str, Any]] = []
    if len(rows) != args.expected_case_count:
        issues.append({"issue": "case_count_mismatch", "actual": len(rows), "expected": args.expected_case_count})
    duplicate_ids = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        issues.append({"issue": "duplicate_case_ids", "case_ids": duplicate_ids})
    missing_skills = [skill for skill in SKILL_NAMES if skill_counts.get(skill, 0) < args.expected_min_per_skill]
    if missing_skills:
        issues.append({"issue": "skill_count_below_minimum", "minimum": args.expected_min_per_skill, "skill_distribution": dict(skill_counts), "skills": missing_skills})
    if set(skill_counts) - set(SKILL_NAMES):
        issues.append({"issue": "unexpected_skill", "skills": sorted(set(skill_counts) - set(SKILL_NAMES))})
    bad_modes = sorted(row.get("case_id") for row in rows if row.get("validation_mode") != args.expected_mode)
    if bad_modes:
        issues.append({"issue": "unexpected_validation_mode", "case_ids": bad_modes})
    bad_retrieval_modes = sorted(row.get("case_id") for row in rows if row.get("retrieval_mode") != args.expected_retrieval_mode)
    if bad_retrieval_modes:
        issues.append({"issue": "unexpected_retrieval_mode", "case_ids": bad_retrieval_modes})
    bad_scripts = sorted(row.get("case_id") for row in rows if row.get("retrieval_script") != args.expected_retrieval_script)
    if bad_scripts:
        issues.append({"issue": "unexpected_retrieval_script", "case_ids": bad_scripts})
    missing_prompt_script = sorted(row.get("case_id") for row in rows if args.expected_retrieval_script not in str(row.get("prompt") or ""))
    if missing_prompt_script:
        issues.append({"issue": "prompt_missing_expected_retrieval_script", "case_ids": missing_prompt_script})
    if manifest.get("case_count") != len(rows):
        issues.append({"issue": "manifest_case_count_mismatch", "manifest": manifest.get("case_count"), "rows": len(rows)})
    if manifest.get("retrieval_mode") != args.expected_retrieval_mode:
        issues.append({"issue": "manifest_retrieval_mode_mismatch", "actual": manifest.get("retrieval_mode"), "expected": args.expected_retrieval_mode})
    qa = {
        "schema_version": "theraskill_hermes_runtime_cases_qa_v1",
        "created_at": utc_now(),
        "status": "complete" if not issues else "failed",
        "issues_count": len(issues),
        "issues": issues,
        "cases": str(cases_path),
        "manifest": str(manifest_path),
        "case_count": len(rows),
        "unique_case_ids": len(set(ids)),
        "skill_distribution": dict(skill_counts),
        "case_type_distribution": dict(type_counts),
        "script_distribution": dict(script_counts),
        "expected_mode": args.expected_mode,
        "expected_retrieval_mode": args.expected_retrieval_mode,
        "expected_retrieval_script": args.expected_retrieval_script,
        "expected_min_per_skill": args.expected_min_per_skill,
    }
    write_json(qa_path, qa)
    print(json.dumps({
        "status": qa["status"],
        "issues_count": len(issues),
        "case_count": len(rows),
        "qa_report": str(qa_path),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
