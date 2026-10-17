#!/usr/bin/env python3
"""Audit formal hybrid retrieval benchmark gates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_json, write_text
from retrieval_surface_registry import (
    FORMAL_HYBRID_AUDIT_PATH,
    FORMAL_HYBRID_AUDIT_SUMMARY_PATH,
    FORMAL_HYBRID_QA_PATH,
    FORMAL_HYBRID_REPORT_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORMAL_QA = FORMAL_HYBRID_QA_PATH
DEFAULT_FORMAL_REPORT = FORMAL_HYBRID_REPORT_PATH
DEFAULT_AUDIT = FORMAL_HYBRID_AUDIT_PATH
DEFAULT_SUMMARY = FORMAL_HYBRID_AUDIT_SUMMARY_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-qa", default=str(DEFAULT_FORMAL_QA))
    parser.add_argument("--formal-report", default=str(DEFAULT_FORMAL_REPORT))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def nested_get(obj: dict[str, Any], path: list[str], default: Any = None) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def check_equal(name: str, actual: Any, expected: Any) -> dict[str, Any]:
    return {"gate": name, "passed": actual == expected, "actual": actual, "expected": expected}


def check_min(name: str, actual: float | int | None, minimum: float | int) -> dict[str, Any]:
    passed = actual is not None and actual >= minimum
    return {"gate": name, "passed": passed, "actual": actual, "minimum": minimum}


def build_audit(qa_path: Path, report_path: Path) -> dict[str, Any]:
    qa = read_json(qa_path) if qa_path.exists() else None
    report = read_json(report_path) if report_path.exists() else None
    if qa is None or report is None:
        return {
            "schema_version": "theraskill_formal_hybrid_benchmark_gate_audit_v1",
            "created_at": utc_now(),
            "status": "not_ready",
            "issues_count": 1,
            "issues": [{
                "issue": "formal_outputs_missing",
                "formal_qa_exists": qa is not None,
                "formal_report_exists": report is not None,
            }],
            "gates": [],
            "formal_qa": str(qa_path),
            "formal_report": str(report_path),
        }
    aggregate = qa.get("aggregate") or report.get("aggregate") or {}
    hybrid = aggregate.get("hybrid") or {}
    by_skill = hybrid.get("by_skill") or {}
    gates = [
        check_equal("qa_status_complete", qa.get("status"), "complete"),
        check_equal("qa_issues_zero", qa.get("issues_count"), 0),
        check_equal("benchmark_scope_formal", qa.get("benchmark_scope"), "formal"),
        check_min("case_count_at_least_120", qa.get("case_count"), 120),
        check_min("hybrid_expected_family_hit_at_1_at_least_v2_target", hybrid.get("expected_family_hit_at_1"), 1.0),
        check_equal("hybrid_forbidden_family_in_top8_zero", hybrid.get("forbidden_family_in_top8_rate"), 0),
        check_equal("hybrid_high_risk_low_risk_top1_zero", hybrid.get("bad_top1_low_risk_for_high_risk_rate"), 0),
        check_min(
            "hybrid_risk_clinical_top3_high_risk_complete",
            nested_get(by_skill, ["risk-triage", "risk_clinical_family_in_top3_for_high_risk_rate"]),
            1.0,
        ),
        check_min(
            "hybrid_rebt_method_rubric_top5_complete",
            nested_get(by_skill, ["rebt-belief-work-support", "rebt_method_rubric_in_top5_rate"]),
            1.0,
        ),
    ]
    failed = [gate for gate in gates if not gate["passed"]]
    return {
        "schema_version": "theraskill_formal_hybrid_benchmark_gate_audit_v1",
        "created_at": utc_now(),
        "status": "passed" if not failed else "failed",
        "issues_count": len(failed),
        "issues": failed,
        "gates": gates,
        "formal_qa": str(qa_path),
        "formal_report": str(report_path),
        "key_metrics": {
            "case_count": qa.get("case_count"),
            "benchmark_scope": qa.get("benchmark_scope"),
            "hybrid_expected_family_hit_at_1": hybrid.get("expected_family_hit_at_1"),
            "hybrid_forbidden_family_in_top8_rate": hybrid.get("forbidden_family_in_top8_rate"),
            "hybrid_bad_top1_low_risk_for_high_risk_rate": hybrid.get("bad_top1_low_risk_for_high_risk_rate"),
            "hybrid_avg_latency_ms": hybrid.get("avg_latency_ms"),
        },
    }


def build_summary(audit: dict[str, Any]) -> str:
    lines = [
        "# Formal Hybrid Benchmark Gate Audit",
        "",
        f"Created at: `{audit['created_at']}`",
        f"Status: `{audit['status']}`",
        f"Issues: `{audit['issues_count']}`",
        "",
        "## Gates",
        "",
    ]
    for gate in audit.get("gates") or []:
        status = "PASS" if gate.get("passed") else "FAIL"
        lines.append(f"- `{status}` `{gate['gate']}` actual=`{gate.get('actual')}`")
    if audit.get("key_metrics"):
        lines.extend(["", "## Key Metrics", ""])
        for key, value in audit["key_metrics"].items():
            lines.append(f"- {key}: `{value}`")
    if audit.get("issues"):
        lines.extend(["", "## Issues", ""])
        for issue in audit["issues"]:
            lines.append(f"- `{json.dumps(issue, ensure_ascii=False)}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    audit_path = Path(args.audit)
    summary_path = Path(args.summary)
    for path in (audit_path, summary_path):
        ensure_output_path(path, overwrite=args.overwrite)
    audit = build_audit(Path(args.formal_qa), Path(args.formal_report))
    write_json(audit_path, audit, overwrite=args.overwrite)
    write_text(summary_path, build_summary(audit), overwrite=args.overwrite)
    print(json.dumps({
        "status": audit["status"],
        "issues_count": audit["issues_count"],
        "audit": str(audit_path),
        "summary": str(summary_path),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit["status"] in {"passed", "not_ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
