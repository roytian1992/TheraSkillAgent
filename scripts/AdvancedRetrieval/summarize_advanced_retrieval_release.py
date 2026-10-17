#!/usr/bin/env python3
"""Summarize Advanced Retrieval v2 release readiness across all gates."""

from __future__ import annotations

import argparse
import json
import subprocess
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
    ADVANCED_RETRIEVAL_V2_RELEASE_SUMMARY_MANIFEST_PATH,
    ADVANCED_RETRIEVAL_V2_RELEASE_SUMMARY_MD_PATH,
    ADVANCED_RETRIEVAL_V2_STATUS_JSON_PATH,
    FORMAL_HYBRID_AUDIT_PATH,
    FORMAL_HYBRID_QA_PATH,
)
from retrieval_module_registry import REPORT_ADVANCED_RETRIEVAL_STATUS_RELPATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HERMES_SKILL_DIR = PROJECT_ROOT / "scripts" / "AgentSkill"
if str(HERMES_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(HERMES_SKILL_DIR))

from runtime_validation_paths import qwen_compatibility_v2_cases_qa_path, qwen_compatibility_v2_runtime_surface  # noqa: E402

DEFAULT_STATUS = ADVANCED_RETRIEVAL_V2_STATUS_JSON_PATH
DEFAULT_FORMAL_AUDIT = FORMAL_HYBRID_AUDIT_PATH
DEFAULT_FORMAL_QA = FORMAL_HYBRID_QA_PATH
DEFAULT_RUNTIME_QA = qwen_compatibility_v2_runtime_surface().qa
DEFAULT_CASE_QA = qwen_compatibility_v2_cases_qa_path()
DEFAULT_MANIFEST = ADVANCED_RETRIEVAL_V2_RELEASE_SUMMARY_MANIFEST_PATH
DEFAULT_SUMMARY = ADVANCED_RETRIEVAL_V2_RELEASE_SUMMARY_MD_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-report", default=str(DEFAULT_STATUS))
    parser.add_argument("--formal-audit", default=str(DEFAULT_FORMAL_AUDIT))
    parser.add_argument("--formal-qa", default=str(DEFAULT_FORMAL_QA))
    parser.add_argument("--runtime-qa", default=str(DEFAULT_RUNTIME_QA))
    parser.add_argument("--case-qa", default=str(DEFAULT_CASE_QA))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--refresh-status", action="store_true", default=True)
    parser.add_argument("--no-refresh-status", dest="refresh_status", action="store_false")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def refresh_status_report(status_report: Path) -> None:
    cmd = [
        sys.executable,
        str(REPORT_ADVANCED_RETRIEVAL_STATUS_RELPATH),
        "--json-report", str(status_report),
        "--overwrite",
    ]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"status refresh failed: {' '.join(cmd)}\n{proc.stdout}")


def gate(name: str, passed: bool, detail: dict[str, Any]) -> dict[str, Any]:
    return {"gate": name, "passed": bool(passed), **detail}


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    if args.refresh_status:
        refresh_status_report(Path(args.status_report))
    status_report_path = Path(args.status_report)
    formal_audit_path = Path(args.formal_audit)
    formal_qa_path = Path(args.formal_qa)
    runtime_qa_path = Path(args.runtime_qa)
    case_qa_path = Path(args.case_qa)
    status_report = read_json(status_report_path) if status_report_path.exists() else {}
    formal_audit = read_json(formal_audit_path) if formal_audit_path.exists() else {}
    formal_qa = read_json(formal_qa_path) if formal_qa_path.exists() else {}
    runtime_qa = read_json(runtime_qa_path) if runtime_qa_path.exists() else {}
    case_qa = read_json(case_qa_path) if case_qa_path.exists() else {}
    dense = status_report.get("dense") or {}
    formal = status_report.get("formal_benchmark") or {}
    gates = [
        gate("dense_index_complete", dense.get("complete") is True, {
            "completed_count": dense.get("completed_count"),
            "total_count": dense.get("total_count"),
            "metadata_status": dense.get("metadata_status"),
            "qa_status": dense.get("qa_status"),
            "qa_issues_count": dense.get("qa_issues_count"),
        }),
        gate("formal_benchmark_complete", formal.get("complete") is True, {
            "qa_exists": formal.get("qa_exists"),
            "qa_status": formal.get("qa_status"),
            "issues_count": formal.get("issues_count"),
            "case_count": formal.get("case_count"),
        }),
        gate("formal_gate_audit_passed", formal_audit.get("status") == "passed", {
            "status": formal_audit.get("status"),
            "issues_count": formal_audit.get("issues_count"),
        }),
        gate("runtime_case_qa_complete", case_qa.get("status") == "complete" and case_qa.get("issues_count") == 0 and case_qa.get("case_count") == 30, {
            "status": case_qa.get("status"),
            "issues_count": case_qa.get("issues_count"),
            "case_count": case_qa.get("case_count"),
        }),
        gate("hermes_runtime_gate_passed", runtime_qa.get("status") == "complete" and runtime_qa.get("retrieval_gate_passed") is True, {
            "qa_exists": bool(runtime_qa),
            "status": runtime_qa.get("status"),
            "issues_count": runtime_qa.get("issues_count"),
            "case_count": runtime_qa.get("case_count"),
            "passed_count": runtime_qa.get("passed_count"),
            "failed_count": runtime_qa.get("failed_count"),
            "retrieval_gate_passed": runtime_qa.get("retrieval_gate_passed"),
            "tool_call_success_rate": runtime_qa.get("tool_call_success_rate"),
            "retrieval_observed_rate": runtime_qa.get("retrieval_observed_rate"),
            "strong_retrieval_observed_rate": runtime_qa.get("strong_retrieval_observed_rate"),
            "policy_violation_rate": runtime_qa.get("policy_violation_rate"),
        }),
    ]
    failed = [item for item in gates if not item["passed"]]
    hybrid_metrics = {}
    if formal_qa:
        aggregate = formal_qa.get("aggregate") or {}
        hybrid_metrics = aggregate.get("hybrid") or {}
    return {
        "schema_version": "theraskill_advanced_retrieval_v2_release_summary_v1",
        "created_at": utc_now(),
        "status": "complete" if not failed else "not_ready",
        "issues_count": len(failed),
        "issues": failed,
        "gates": gates,
        "inputs": {
            "status_report": args.status_report,
            "formal_audit": args.formal_audit,
            "formal_qa": args.formal_qa,
            "runtime_qa": args.runtime_qa,
            "case_qa": args.case_qa,
        },
        "key_metrics": {
            "dense_completed_count": dense.get("completed_count"),
            "dense_total_count": dense.get("total_count"),
            "dense_percent": dense.get("percent"),
            "formal_case_count": formal.get("case_count") or formal_qa.get("case_count"),
            "hybrid_expected_family_hit_at_1": hybrid_metrics.get("expected_family_hit_at_1"),
            "hybrid_forbidden_family_in_top8_rate": hybrid_metrics.get("forbidden_family_in_top8_rate"),
            "hybrid_bad_top1_low_risk_for_high_risk_rate": hybrid_metrics.get("bad_top1_low_risk_for_high_risk_rate"),
            "hybrid_avg_latency_ms": hybrid_metrics.get("avg_latency_ms"),
            "runtime_case_count": runtime_qa.get("case_count"),
            "runtime_retrieval_observed_rate": runtime_qa.get("retrieval_observed_rate"),
            "runtime_strong_retrieval_observed_rate": runtime_qa.get("strong_retrieval_observed_rate"),
            "runtime_policy_violation_rate": runtime_qa.get("policy_violation_rate"),
        },
    }


def build_summary(manifest: dict[str, Any]) -> str:
    lines = [
        "# Advanced Retrieval v2 Release Summary",
        "",
        f"Created at: `{manifest['created_at']}`",
        f"Status: `{manifest['status']}`",
        f"Issues: `{manifest['issues_count']}`",
        "",
        "## Gates",
        "",
    ]
    for item in manifest["gates"]:
        status = "PASS" if item["passed"] else "WAIT"
        detail = {key: value for key, value in item.items() if key not in {"gate", "passed"}}
        lines.append(f"- `{status}` `{item['gate']}` `{json.dumps(detail, ensure_ascii=False, sort_keys=True)}`")
    lines.extend(["", "## Key Metrics", ""])
    for key, value in manifest["key_metrics"].items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    summary_path = Path(args.summary)
    for path in (manifest_path, summary_path):
        ensure_output_path(path, overwrite=args.overwrite)
    manifest = build_manifest(args)
    write_json(manifest_path, manifest, overwrite=args.overwrite)
    write_text(summary_path, build_summary(manifest), overwrite=args.overwrite)
    print(json.dumps({
        "status": manifest["status"],
        "issues_count": manifest["issues_count"],
        "manifest": str(manifest_path),
        "summary": str(summary_path),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if manifest["status"] in {"complete", "not_ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
