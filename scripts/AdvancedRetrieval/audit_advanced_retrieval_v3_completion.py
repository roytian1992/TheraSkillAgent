#!/usr/bin/env python3
"""Audit Advanced Retrieval v3 completion evidence against the plan."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMON_DIR = PROJECT_ROOT / "scripts" / "_common"
HERMES_SKILL_DIR = PROJECT_ROOT / "scripts" / "AgentSkill"
for path in (COMMON_DIR, HERMES_SKILL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hermes_surface_registry import HERMES_SKILL_QA_PATH  # noqa: E402
from io_utils import read_json, write_json, write_text  # noqa: E402
from runtime_validation_paths import qwen_trusted_v3_gate_runtime_surface  # noqa: E402
from retrieval_surface_registry import (  # noqa: E402
    ADVANCED_RETRIEVAL_V3_COMPLETION_AUDIT_PATH,
    ADVANCED_RETRIEVAL_V3_COMPLETION_AUDIT_SUMMARY_PATH,
    RETRIEVAL_BENCHMARK_V3_MULTILINGUAL18_GATE_QA_PATH,
    RETRIEVAL_BENCHMARK_V3_MULTILINGUAL18_REPORT_PATH,
    RETRIEVAL_BENCHMARK_V3_RUNTIME_CANDIDATE_FORMAL_QA_PATH,
    RETRIEVAL_BENCHMARK_V3_RUNTIME_CANDIDATE_FORMAL_REPORT_PATH,
)

DEFAULT_OUTPUT = ADVANCED_RETRIEVAL_V3_COMPLETION_AUDIT_PATH
DEFAULT_SUMMARY = ADVANCED_RETRIEVAL_V3_COMPLETION_AUDIT_SUMMARY_PATH
DEFAULT_HERMES_TRUSTED_SURFACE = qwen_trusted_v3_gate_runtime_surface()


EVIDENCE_PATHS = {
    "runtime_candidate_formal_qa": RETRIEVAL_BENCHMARK_V3_RUNTIME_CANDIDATE_FORMAL_QA_PATH,
    "runtime_candidate_formal_report": RETRIEVAL_BENCHMARK_V3_RUNTIME_CANDIDATE_FORMAL_REPORT_PATH,
    "hermes_runtime_qa": DEFAULT_HERMES_TRUSTED_SURFACE.qa,
    "hermes_runtime_report": DEFAULT_HERMES_TRUSTED_SURFACE.report,
    "multilingual_gate_qa": RETRIEVAL_BENCHMARK_V3_MULTILINGUAL18_GATE_QA_PATH,
    "multilingual_report": RETRIEVAL_BENCHMARK_V3_MULTILINGUAL18_REPORT_PATH,
    "hermes_skill_qa": HERMES_SKILL_QA_PATH,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ok_item(requirement: str, evidence: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "requirement": requirement,
        "status": "complete",
        "evidence": evidence,
        "details": details or {},
    }


def issue_item(requirement: str, issue: str, evidence: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "requirement": requirement,
        "status": "incomplete",
        "issue": issue,
        "evidence": evidence,
        "details": details or {},
    }


def main() -> int:
    args = parse_args()
    items: list[dict[str, Any]] = []
    missing = [name for name, path in EVIDENCE_PATHS.items() if not path.exists()]
    if missing:
        for name in missing:
            items.append(issue_item(name, "missing_evidence_file", str(EVIDENCE_PATHS[name])))
        audit = {
            "schema_version": "theraskill_advanced_retrieval_v3_completion_audit_v1",
            "created_at": utc_now(),
            "status": "incomplete",
            "issues_count": len(items),
            "items": items,
            "evidence_paths": {name: str(path) for name, path in EVIDENCE_PATHS.items()},
        }
        write_json(Path(args.output), audit)
        write_text(Path(args.summary), "# Advanced Retrieval v3 Completion Audit\n\nStatus: `incomplete`\n")
        print(json.dumps({"status": "incomplete", "issues_count": len(items), "missing": missing}, ensure_ascii=False, indent=2))
        return 1

    formal_qa = read_json(EVIDENCE_PATHS["runtime_candidate_formal_qa"])
    formal_report = read_json(EVIDENCE_PATHS["runtime_candidate_formal_report"])
    hermes_qa = read_json(EVIDENCE_PATHS["hermes_runtime_qa"])
    hermes_report = read_json(EVIDENCE_PATHS["hermes_runtime_report"])
    multi_qa = read_json(EVIDENCE_PATHS["multilingual_gate_qa"])
    multi_report = read_json(EVIDENCE_PATHS["multilingual_report"])
    skill_qa = read_json(EVIDENCE_PATHS["hermes_skill_qa"])

    if formal_qa.get("status") == "complete" and formal_qa.get("issues_count") == 0 and formal_qa.get("case_count") == 120 and formal_qa.get("backend_count") == 2:
        items.append(ok_item(
            "120-case v3 runtime-candidate formal retrieval benchmark passed",
            str(EVIDENCE_PATHS["runtime_candidate_formal_qa"]),
            {
                "case_count": formal_qa.get("case_count"),
                "backend_count": formal_qa.get("backend_count"),
                "planner_fallback_rate": formal_qa.get("planner_fallback_rate"),
                "backend_summary": formal_qa.get("backend_summary"),
            },
        ))
    else:
        items.append(issue_item("120-case v3 runtime-candidate formal retrieval benchmark passed", "formal_qa_not_complete", str(EVIDENCE_PATHS["runtime_candidate_formal_qa"]), formal_qa))

    if formal_qa.get("planner_fallback_rate") == 0.0:
        items.append(ok_item("LLM planner ran without degraded fallback in formal benchmark", str(EVIDENCE_PATHS["runtime_candidate_formal_qa"]), {"planner_fallback_rate": formal_qa.get("planner_fallback_rate")}))
    else:
        items.append(issue_item("LLM planner ran without degraded fallback in formal benchmark", "planner_fallback_detected", str(EVIDENCE_PATHS["runtime_candidate_formal_qa"]), {"planner_fallback_rate": formal_qa.get("planner_fallback_rate")}))

    formal_summary = formal_qa.get("backend_summary") or {}
    faiss = formal_summary.get("faiss_shards") or {}
    if faiss.get("expected_family_hit_at_5_rate", 0) >= 0.99 and faiss.get("planner_fallback_rate") == 0.0:
        items.append(ok_item("FAISS shards runtime backend is validated for v3 retrieval", str(EVIDENCE_PATHS["runtime_candidate_formal_qa"]), faiss))
    else:
        items.append(issue_item("FAISS shards runtime backend is validated for v3 retrieval", "faiss_shards_metric_below_threshold", str(EVIDENCE_PATHS["runtime_candidate_formal_qa"]), faiss))

    if hermes_qa.get("status") == "complete" and hermes_qa.get("retrieval_gate_passed") is True and hermes_qa.get("case_count") == 30 and hermes_qa.get("issues_count") == 0:
        items.append(ok_item(
            "30-case real Hermes + Qwen runtime retrieval gate passed",
            str(EVIDENCE_PATHS["hermes_runtime_qa"]),
            {
                "case_count": hermes_qa.get("case_count"),
                "passed_count": hermes_qa.get("passed_count"),
                "retrieval_observed_count": hermes_qa.get("retrieval_observed_count"),
                "strong_retrieval_observed_count": hermes_qa.get("strong_retrieval_observed_count"),
                "policy_violation_rate": hermes_qa.get("policy_violation_rate"),
                "hermes_runtime_cwd": hermes_report.get("hermes_runtime_cwd"),
            },
        ))
    else:
        items.append(issue_item("30-case real Hermes + Qwen runtime retrieval gate passed", "hermes_runtime_gate_not_complete", str(EVIDENCE_PATHS["hermes_runtime_qa"]), hermes_qa))

    if multi_qa.get("status") == "complete" and multi_qa.get("issues_count") == 0 and multi_qa.get("case_count") == 18 and multi_qa.get("planner_fallback_rate") == 0.0:
        items.append(ok_item(
            "multilingual benchmark covers Chinese, English, and mixed queries",
            str(EVIDENCE_PATHS["multilingual_gate_qa"]),
            {
                "case_count": multi_qa.get("case_count"),
                "language_counts": multi_qa.get("language_counts_inferred_from_case_id"),
                "skill_counts": multi_qa.get("skill_counts_per_backend_reference"),
                "backend_hit_rates": multi_qa.get("backend_hit_rates"),
            },
        ))
    else:
        items.append(issue_item("multilingual benchmark covers Chinese, English, and mixed queries", "multilingual_gate_not_complete", str(EVIDENCE_PATHS["multilingual_gate_qa"]), multi_qa))

    if skill_qa.get("status") == "complete" and skill_qa.get("issues_count") == 0:
        items.append(ok_item("compiled Hermes skills passed v3 retrieval probe QA", str(EVIDENCE_PATHS["hermes_skill_qa"]), {"skills_checked": skill_qa.get("skills_checked")}))
    else:
        items.append(issue_item("compiled Hermes skills passed v3 retrieval probe QA", "hermes_skill_qa_not_complete", str(EVIDENCE_PATHS["hermes_skill_qa"]), skill_qa))

    policy_rows = formal_report.get("rows") or []
    if all(all((row.get("policy_checks") or {}).values()) and all((row.get("quality_checks") or {}).values()) for row in policy_rows):
        items.append(ok_item("formal benchmark policy and quality checks are all true", str(EVIDENCE_PATHS["runtime_candidate_formal_report"]), {"row_count": len(policy_rows)}))
    else:
        bad = [
            {"case_id": row.get("case_id"), "backend": row.get("backend"), "policy_checks": row.get("policy_checks"), "quality_checks": row.get("quality_checks")}
            for row in policy_rows
            if not all((row.get("policy_checks") or {}).values()) or not all((row.get("quality_checks") or {}).values())
        ]
        items.append(issue_item("formal benchmark policy and quality checks are all true", "policy_or_quality_check_failed", str(EVIDENCE_PATHS["runtime_candidate_formal_report"]), {"bad_rows": bad[:20], "bad_count": len(bad)}))

    multi_rows = multi_report.get("rows") or []
    if all(row.get("expected_family_hit_at_5") for row in multi_rows):
        items.append(ok_item("multilingual benchmark expected-family hit@5 is complete for every row", str(EVIDENCE_PATHS["multilingual_report"]), {"row_count": len(multi_rows)}))
    else:
        misses = [
            {"case_id": row.get("case_id"), "backend": row.get("backend"), "top_families": row.get("top_families"), "expected_families": row.get("expected_families")}
            for row in multi_rows
            if not row.get("expected_family_hit_at_5")
        ]
        items.append(issue_item("multilingual benchmark expected-family hit@5 is complete for every row", "multilingual_hit_at_5_miss", str(EVIDENCE_PATHS["multilingual_report"]), {"misses": misses}))

    incomplete = [item for item in items if item["status"] != "complete"]
    audit = {
        "schema_version": "theraskill_advanced_retrieval_v3_completion_audit_v1",
        "created_at": utc_now(),
        "status": "complete" if not incomplete else "incomplete",
        "issues_count": len(incomplete),
        "items": items,
        "evidence_paths": {name: str(path) for name, path in EVIDENCE_PATHS.items()},
        "scope_note": "Audits implemented evidence gates for Advanced Retrieval v3. Optional LLM rerank, HyDE, and late-interaction experiments are future enhancements, not blockers for v3 completion.",
    }
    summary_lines = [
        "# Advanced Retrieval v3 Completion Audit",
        "",
        f"- Status: `{audit['status']}`",
        f"- Issues: `{audit['issues_count']}`",
        "",
        "## Items",
        "",
    ]
    for item in items:
        summary_lines.append(f"- `{item['status']}` {item['requirement']} ({item.get('evidence', '')})")
        if item["status"] != "complete":
            summary_lines.append(f"  - issue: `{item.get('issue')}`")
    write_json(Path(args.output), audit)
    write_text(Path(args.summary), "\n".join(summary_lines) + "\n")
    print(json.dumps({
        "output": args.output,
        "summary": args.summary,
        "status": audit["status"],
        "issues_count": audit["issues_count"],
        "item_count": len(items),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())
