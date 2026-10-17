#!/usr/bin/env python3
"""Audit the v3 multilingual retrieval benchmark with stricter gate checks."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import write_json, write_text

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--qa-report", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--min-hit-at-5", type=float, default=1.0)
    parser.add_argument("--max-planner-fallback-rate", type=float, default=0.0)
    parser.add_argument("--require-languages", nargs="+", default=["zh", "en", "mixed"])
    parser.add_argument("--require-skills", nargs="+", default=[
        "risk-triage",
        "emotional-support",
        "cognitive-reframing-support",
        "rebt-belief-work-support",
        "synthetic-dialogue-builder",
        "therapy-response-reviewer",
    ])
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rate(n: int, d: int) -> float:
    return round(n / d, 6) if d else 0.0


def main() -> int:
    args = parse_args()
    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = report.get("rows") or []
    plan_rows = report.get("plan_rows") or []
    issues: list[dict[str, Any]] = []
    case_ids = sorted({str(row["case_id"]) for row in rows})
    backends = sorted({str(row["backend"]) for row in rows})
    language_by_case = {
        str(row["case_id"]): str(row.get("language") or "unknown")
        for row in rows
        if str(row.get("backend")) == (backends[0] if backends else "")
    }
    language_counts = Counter(language_by_case.values())
    skill_counts = Counter(str(row["skill"]) for row in rows if str(row.get("backend")) == (backends[0] if backends else ""))
    backend_hit_rates: dict[str, dict[str, Any]] = {}
    by_backend: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_backend[str(row["backend"])].append(row)
    for backend, backend_rows in sorted(by_backend.items()):
        hit5 = rate(sum(1 for row in backend_rows if row.get("expected_family_hit_at_5")), len(backend_rows))
        hit1 = rate(sum(1 for row in backend_rows if row.get("expected_family_hit_at_1")), len(backend_rows))
        backend_hit_rates[backend] = {
            "row_count": len(backend_rows),
            "expected_family_hit_at_1_rate": hit1,
            "expected_family_hit_at_5_rate": hit5,
        }
        if hit5 < args.min_hit_at_5:
            misses = [
                {
                    "case_id": row.get("case_id"),
                    "skill": row.get("skill"),
                    "backend": backend,
                    "top_families": row.get("top_families"),
                    "expected_families": row.get("expected_families"),
                }
                for row in backend_rows
                if not row.get("expected_family_hit_at_5")
            ]
            issues.append({"issue": "expected_family_hit_at_5_below_threshold", "backend": backend, "rate": hit5, "misses": misses})
    planner_fallback_rate = rate(sum(1 for row in plan_rows if row.get("planner_fallback_used")), len(plan_rows))
    if planner_fallback_rate > args.max_planner_fallback_rate:
        issues.append({"issue": "planner_fallback_rate_above_threshold", "rate": planner_fallback_rate})
    if report.get("case_count") != 18:
        issues.append({"issue": "unexpected_case_count", "case_count": report.get("case_count")})
    if report.get("backend_count") != 2:
        issues.append({"issue": "unexpected_backend_count", "backend_count": report.get("backend_count")})
    expected_backends = {"none", "faiss_shards"}
    if set(backends) != expected_backends:
        issues.append({"issue": "unexpected_backends", "backends": backends})
    for row in rows:
        if not all((row.get("policy_checks") or {}).values()):
            issues.append({"issue": "policy_check_failed", "case_id": row.get("case_id"), "backend": row.get("backend"), "checks": row.get("policy_checks")})
        if not all((row.get("quality_checks") or {}).values()):
            issues.append({"issue": "quality_check_failed", "case_id": row.get("case_id"), "backend": row.get("backend"), "checks": row.get("quality_checks")})
    for language in args.require_languages:
        if language_counts.get(language, 0) < len(args.require_skills):
            issues.append({"issue": "language_undercovered", "language": language, "count": language_counts.get(language, 0)})
    for skill in args.require_skills:
        if skill_counts.get(skill, 0) < len(args.require_languages):
            issues.append({"issue": "skill_undercovered", "skill": skill, "count": skill_counts.get(skill, 0)})
    qa = {
        "schema_version": "theraskill_retrieval_benchmark_v3_multilingual_audit_v1",
        "created_at": utc_now(),
        "status": "complete" if not issues else "issues_found",
        "issues_count": len(issues),
        "issues": issues,
        "source_report": str(report_path),
        "case_count": report.get("case_count"),
        "backend_count": report.get("backend_count"),
        "row_count": len(rows),
        "case_ids": case_ids,
        "backends": backends,
        "planner_fallback_rate": planner_fallback_rate,
        "backend_hit_rates": backend_hit_rates,
        "skill_counts_per_backend_reference": dict(skill_counts),
        "language_counts_inferred_from_case_id": dict(language_counts),
        "gate": {
            "min_hit_at_5": args.min_hit_at_5,
            "max_planner_fallback_rate": args.max_planner_fallback_rate,
            "expected_backends": sorted(expected_backends),
        },
    }
    summary_lines = [
        "# Advanced Retrieval v3 Multilingual Benchmark Audit",
        "",
        f"- Status: `{qa['status']}`",
        f"- Issues: `{qa['issues_count']}`",
        f"- Source report: `{qa['source_report']}`",
        f"- Case count: `{qa['case_count']}`",
        f"- Backends: `{', '.join(backends)}`",
        f"- Planner fallback rate: `{planner_fallback_rate}`",
        "",
        "## Backend Hit Rates",
        "",
    ]
    for backend, metrics in backend_hit_rates.items():
        summary_lines.append(
            f"- `{backend}`: hit@1=`{metrics['expected_family_hit_at_1_rate']}`, "
            f"hit@5=`{metrics['expected_family_hit_at_5_rate']}`, rows=`{metrics['row_count']}`"
        )
    if issues:
        summary_lines.extend(["", "## Issues", ""])
        for issue in issues:
            summary_lines.append(f"- `{json.dumps(issue, ensure_ascii=False)}`")
    write_json(Path(args.qa_report), qa)
    write_text(Path(args.summary), "\n".join(summary_lines) + "\n")
    print(json.dumps({
        "qa_report": args.qa_report,
        "summary": args.summary,
        "status": qa["status"],
        "issues_count": qa["issues_count"],
        "planner_fallback_rate": planner_fallback_rate,
        "backend_hit_rates": backend_hit_rates,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
