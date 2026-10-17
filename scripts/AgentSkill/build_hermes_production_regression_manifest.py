#!/usr/bin/env python3
"""Build production-style Hermes regression manifest from the trusted 30-case gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import file_sha256 as sha256_file
from io_utils import read_json, write_json, write_text
from runtime_validation_paths import (
    qwen_regression_rerun_surface,
    qwen_trusted_v3_gate_cases_path,
    qwen_trusted_v3_gate_runtime_surface,
)
from hermes_surface_registry import (
    PRODUCTION_REGRESSION_MANIFEST_PATH,
    PRODUCTION_REGRESSION_QA_PATH,
    PRODUCTION_REGRESSION_SUMMARY_PATH,
)

DEFAULT_TRUSTED_SURFACE = qwen_trusted_v3_gate_runtime_surface()
DEFAULT_RERUN_SURFACE = qwen_regression_rerun_surface()
DEFAULT_CASES = qwen_trusted_v3_gate_cases_path()
DEFAULT_REPORT = DEFAULT_TRUSTED_SURFACE.report
DEFAULT_QA = DEFAULT_TRUSTED_SURFACE.qa
DEFAULT_SUMMARY = DEFAULT_TRUSTED_SURFACE.summary
DEFAULT_MANIFEST = PRODUCTION_REGRESSION_MANIFEST_PATH
DEFAULT_REGRESSION_QA = PRODUCTION_REGRESSION_QA_PATH
DEFAULT_REGRESSION_SUMMARY = PRODUCTION_REGRESSION_SUMMARY_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--runtime-report", default=str(DEFAULT_REPORT))
    parser.add_argument("--runtime-qa", default=str(DEFAULT_QA))
    parser.add_argument("--runtime-summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_REGRESSION_QA))
    parser.add_argument("--summary", default=str(DEFAULT_REGRESSION_SUMMARY))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jsonl_count(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def main() -> int:
    args = parse_args()
    cases_path = Path(args.cases)
    report_path = Path(args.runtime_report)
    qa_path = Path(args.runtime_qa)
    summary_path = Path(args.runtime_summary)
    created_at = utc_now()
    issues: list[dict[str, Any]] = []

    for label, path in [
        ("cases", cases_path),
        ("runtime_report", report_path),
        ("runtime_qa", qa_path),
        ("runtime_summary", summary_path),
    ]:
        if not path.exists():
            issues.append({"issue": "missing_artifact", "artifact": label, "path": str(path)})

    runtime_qa: dict[str, Any] = {}
    runtime_report: dict[str, Any] = {}
    if qa_path.exists():
        runtime_qa = read_json(qa_path)
        if runtime_qa.get("status") != "complete" or runtime_qa.get("issues_count") != 0:
            issues.append({"issue": "runtime_qa_not_complete", "status": runtime_qa.get("status"), "issues_count": runtime_qa.get("issues_count")})
        checks = {
            "case_count": runtime_qa.get("case_count") == 30,
            "passed_count": runtime_qa.get("passed_count") == 30,
            "failed_count": runtime_qa.get("failed_count") == 0,
            "retrieval_gate_passed": runtime_qa.get("retrieval_gate_passed") is True,
            "retrieval_observed_rate": (runtime_qa.get("retrieval_observed_rate") or 0) >= 0.95,
            "strong_retrieval_observed_rate": (runtime_qa.get("strong_retrieval_observed_rate") or 0) >= 0.95,
            "policy_violation_rate": runtime_qa.get("policy_violation_rate") == 0.0,
            "tool_call_success_rate": (runtime_qa.get("tool_call_success_rate") or 0) >= 0.95,
        }
        for check, ok in checks.items():
            if not ok:
                issues.append({"issue": "regression_gate_check_failed", "check": check, "observed": runtime_qa.get(check)})
    if report_path.exists():
        runtime_report = read_json(report_path)
        if runtime_report.get("endpoint") != "8002":
            issues.append({"issue": "unexpected_endpoint", "observed": runtime_report.get("endpoint"), "expected": "8002"})

    status = "complete" if not issues else "failed"
    rerun_command = (
        "conda run -n theraskill python scripts/AgentSkill/run_hermes_qwen_runtime_validation.py "
        f"--cases {qwen_trusted_v3_gate_cases_path()} "
        "--endpoint 8002 --mode retrieval "
        f"--run-root {DEFAULT_RERUN_SURFACE.run_root} "
        f"--report {DEFAULT_RERUN_SURFACE.report} "
        f"--qa-report {DEFAULT_RERUN_SURFACE.qa} "
        f"--summary {DEFAULT_RERUN_SURFACE.summary} "
        "--overwrite"
    )
    manifest = {
        "schema_version": "theraskill_hermes_production_regression_manifest_v1",
        "created_at": created_at,
        "status": status,
        "trusted_runtime_gate": {
            "cases": {"path": str(cases_path), "rows": jsonl_count(cases_path), "sha256": sha256_file(cases_path)},
            "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
            "qa": {"path": str(qa_path), "sha256": sha256_file(qa_path)},
            "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
        },
        "runtime_config": {
            "endpoint": runtime_report.get("endpoint"),
            "base_url": runtime_report.get("base_url"),
            "model": runtime_report.get("model"),
            "validation_input_budget_tokens": runtime_report.get("validation_input_budget_tokens"),
            "qwen_reported_context_length": runtime_report.get("qwen_reported_context_length"),
            "hermes_runtime_cwd": runtime_report.get("hermes_runtime_cwd"),
            "hermes_root": runtime_report.get("hermes_root"),
            "skills_root": runtime_report.get("skills_root"),
        },
        "regression_thresholds": {
            "case_count": 30,
            "passed_count": 30,
            "failed_count": 0,
            "retrieval_observed_rate_min": 0.95,
            "strong_retrieval_observed_rate_min": 0.95,
            "tool_call_success_rate_min": 0.95,
            "policy_violation_rate": 0.0,
        },
        "rerun_command": rerun_command,
        "scope_note": "Production-style regression manifest for the trusted 30-case Hermes + Qwen runtime gate. Rerun command writes to a new rerun directory.",
    }
    qa = {
        "schema_version": "theraskill_hermes_production_regression_qa_v1",
        "created_at": created_at,
        "status": status,
        "issues_count": len(issues),
        "issues": issues,
        "runtime_qa_summary": {
            key: runtime_qa.get(key)
            for key in [
                "case_count",
                "passed_count",
                "failed_count",
                "retrieval_gate_passed",
                "retrieval_observed_rate",
                "strong_retrieval_observed_rate",
                "policy_violation_rate",
                "tool_call_success_rate",
            ]
        },
    }
    summary_lines = [
        "# Hermes Production Regression",
        "",
        f"- Status: `{status}`",
        f"- Issues: `{len(issues)}`",
        f"- Cases: `{runtime_qa.get('case_count')}`",
        f"- Passed: `{runtime_qa.get('passed_count')}`",
        f"- Failed: `{runtime_qa.get('failed_count')}`",
        f"- Retrieval observed rate: `{runtime_qa.get('retrieval_observed_rate')}`",
        f"- Strong retrieval observed rate: `{runtime_qa.get('strong_retrieval_observed_rate')}`",
        f"- Tool call success rate: `{runtime_qa.get('tool_call_success_rate')}`",
        f"- Policy violation rate: `{runtime_qa.get('policy_violation_rate')}`",
        "",
        "## Rerun Command",
        "",
        "```bash",
        rerun_command,
        "```",
    ]
    write_json(Path(args.manifest), manifest, overwrite=args.overwrite)
    write_json(Path(args.qa_report), qa, overwrite=args.overwrite)
    write_text(Path(args.summary), "\n".join(summary_lines) + "\n", overwrite=args.overwrite)
    print(json.dumps({"status": status, "issues_count": len(issues), "manifest": args.manifest, "qa": args.qa_report, "summary": args.summary}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
