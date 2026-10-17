#!/usr/bin/env python3
"""Historical comparison runner for the v1 lexical baseline and v2 advanced retrieval."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import ensure_output_path, iter_jsonl, write_json, write_text
from retrieval_module_registry import PROJECT_ROOT, load_advanced_v2, load_v1_wrapper
from retrieval_surface_registry import (
    RETRIEVAL_BENCHMARK_CASES_PATH,
    RETRIEVAL_BENCHMARK_V1_V2_QA_PATH,
    RETRIEVAL_BENCHMARK_V1_V2_REPORT_PATH,
    RETRIEVAL_BENCHMARK_V1_V2_SUMMARY_PATH,
)


DEFAULT_CASES = RETRIEVAL_BENCHMARK_CASES_PATH
DEFAULT_REPORT = RETRIEVAL_BENCHMARK_V1_V2_REPORT_PATH
DEFAULT_QA = RETRIEVAL_BENCHMARK_V1_V2_QA_PATH
DEFAULT_SUMMARY = RETRIEVAL_BENCHMARK_V1_V2_SUMMARY_PATH


V1 = load_v1_wrapper()
V2 = load_advanced_v2()


LOW_RISK_LABEL_TERMS = ["低自杀风险", "low suicide risk", "low_risk", "无自杀", "no suicidal"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def contains_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)


class ArgsObj:
    def __init__(self, **kwargs: Any):
        self.__dict__.update(kwargs)


def run_v1(case: dict[str, Any]) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    bundle = V1.build_bundle(ArgsObj(
        db=str(V1.DEFAULT_DB),
        skill=case["skill"],
        query=case["query"],
        top_k=int(case.get("top_k") or 8),
    ))
    elapsed_ms = (time.perf_counter() - start) * 1000
    return bundle, elapsed_ms


def run_v2(case: dict[str, Any]) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    bundle = V2.build_bundle(ArgsObj(
        db=str(V2.DEFAULT_DB),
        skill=case["skill"],
        query=case["query"],
        top_k=int(case.get("top_k") or 8),
        candidate_multiplier=30,
    ))
    elapsed_ms = (time.perf_counter() - start) * 1000
    return bundle, elapsed_ms


def evaluate_bundle(case: dict[str, Any], bundle: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    items = bundle.get("items") or []
    families = [item.get("dedup_family") for item in items]
    labels = [str(item.get("canonical_label") or "") for item in items]
    expected_families = set(case.get("expected_families") or [])
    forbidden_families = set(case.get("forbidden_families") or [])
    top1_family = families[0] if families else None
    top1_label = labels[0] if labels else ""
    severity = case.get("severity_label")
    high_risk_case = severity == "high_or_urgent"
    result = {
        "hit_count": len(items),
        "elapsed_ms": round(elapsed_ms, 3),
        "top1_family": top1_family,
        "top1_label": top1_label,
        "top5_families": families[:5],
        "top8_families": families[:8],
        "expected_family_hit_at_1": top1_family in expected_families,
        "expected_family_hit_at_3": bool(expected_families.intersection(families[:3])),
        "expected_family_hit_at_8": bool(expected_families.intersection(families[:8])),
        "forbidden_family_in_top8": bool(forbidden_families.intersection(families[:8])),
        "bad_top1_low_risk_for_high_risk": bool(high_risk_case and contains_any(top1_label, LOW_RISK_LABEL_TERMS)),
        "risk_clinical_family_in_top3_for_high_risk": bool((not high_risk_case) or ("clinical_risk_assessment" in families[:3])),
        "rebt_method_rubric_in_top5": bool(case["skill"] != "rebt-belief-work-support" or ("method_rubric" in families[:5])),
        "policy_checks": bundle.get("policy_checks") or {},
        "quality_checks": bundle.get("quality_checks") or {},
        "top_ids": [item.get("release_unit_id") for item in items[:8]],
    }
    return result


def aggregate(results: list[dict[str, Any]], version: str) -> dict[str, Any]:
    selected = [row[version] for row in results]
    n = len(selected)
    if n == 0:
        return {}
    skill_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        skill_groups[row["skill"]].append(row[version])
    def rate(key: str, rows: list[dict[str, Any]] = selected) -> float:
        return round(sum(1 for item in rows if item.get(key)) / max(1, len(rows)), 6)
    agg = {
        "case_count": n,
        "avg_latency_ms": round(sum(item["elapsed_ms"] for item in selected) / n, 3),
        "p95_latency_ms": percentile([item["elapsed_ms"] for item in selected], 0.95),
        "expected_family_hit_at_1": rate("expected_family_hit_at_1"),
        "expected_family_hit_at_3": rate("expected_family_hit_at_3"),
        "expected_family_hit_at_8": rate("expected_family_hit_at_8"),
        "forbidden_family_in_top8_rate": rate("forbidden_family_in_top8"),
        "bad_top1_low_risk_for_high_risk_rate": rate("bad_top1_low_risk_for_high_risk"),
        "risk_clinical_family_in_top3_for_high_risk_rate": rate("risk_clinical_family_in_top3_for_high_risk"),
        "rebt_method_rubric_in_top5_rate": rate("rebt_method_rubric_in_top5"),
        "top1_family_distribution": dict(Counter(item["top1_family"] for item in selected).most_common()),
        "by_skill": {},
    }
    for skill, rows in sorted(skill_groups.items()):
        agg["by_skill"][skill] = {
            "case_count": len(rows),
            "expected_family_hit_at_1": rate("expected_family_hit_at_1", rows),
            "expected_family_hit_at_3": rate("expected_family_hit_at_3", rows),
            "forbidden_family_in_top8_rate": rate("forbidden_family_in_top8", rows),
            "bad_top1_low_risk_for_high_risk_rate": rate("bad_top1_low_risk_for_high_risk", rows),
            "risk_clinical_family_in_top3_for_high_risk_rate": rate("risk_clinical_family_in_top3_for_high_risk", rows),
            "rebt_method_rubric_in_top5_rate": rate("rebt_method_rubric_in_top5", rows),
            "avg_latency_ms": round(sum(item["elapsed_ms"] for item in rows) / max(1, len(rows)), 3),
            "top1_family_distribution": dict(Counter(item["top1_family"] for item in rows).most_common()),
        }
    return agg


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
    return round(values[idx], 3)


def build_summary(report: dict[str, Any], qa: dict[str, Any]) -> str:
    lines = [
        "# TheraSkill Retrieval Benchmark v1 vs v2",
        "",
        f"Created at: `{report['created_at']}`",
        f"Cases: `{report['case_count']}`",
        f"QA status: `{qa['status']}`",
        "",
        "## Aggregate",
        "",
    ]
    for version in ("v1", "v2"):
        agg = report["aggregate"][version]
        lines.extend([
            f"### {version}",
            "",
            f"- expected_family_hit_at_1: `{agg['expected_family_hit_at_1']}`",
            f"- expected_family_hit_at_3: `{agg['expected_family_hit_at_3']}`",
            f"- forbidden_family_in_top8_rate: `{agg['forbidden_family_in_top8_rate']}`",
            f"- bad_top1_low_risk_for_high_risk_rate: `{agg['bad_top1_low_risk_for_high_risk_rate']}`",
            f"- risk_clinical_family_in_top3_for_high_risk_rate: `{agg['risk_clinical_family_in_top3_for_high_risk_rate']}`",
            f"- rebt_method_rubric_in_top5_rate: `{agg['rebt_method_rubric_in_top5_rate']}`",
            f"- avg_latency_ms: `{agg['avg_latency_ms']}`",
            "",
        ])
    if qa["issues"]:
        lines.extend(["## Issues", ""])
        for issue in qa["issues"]:
            lines.append(f"- `{json.dumps(issue, ensure_ascii=False)}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    cases_path = Path(args.cases)
    report_path = Path(args.report)
    qa_path = Path(args.qa_report)
    summary_path = Path(args.summary)
    for path in (report_path, qa_path, summary_path):
        ensure_output_path(path, overwrite=args.overwrite)
    cases = [case for _, case in iter_jsonl(cases_path, with_line_numbers=True)]
    if args.limit:
        cases = cases[: args.limit]

    results: list[dict[str, Any]] = []
    for case in cases:
        v1_bundle, v1_ms = run_v1(case)
        v2_bundle, v2_ms = run_v2(case)
        results.append({
            "benchmark_case_id": case["benchmark_case_id"],
            "skill": case["skill"],
            "case_type": case["case_type"],
            "query": case["query"],
            "severity_label": case.get("severity_label"),
            "v1": evaluate_bundle(case, v1_bundle, v1_ms),
            "v2": evaluate_bundle(case, v2_bundle, v2_ms),
        })

    aggregate_report = {"v1": aggregate(results, "v1"), "v2": aggregate(results, "v2")}
    created_at = utc_now()
    report = {
        "schema_version": "theraskill_retrieval_benchmark_v1_vs_v2_report_v1",
        "created_at": created_at,
        "cases": str(cases_path),
        "case_count": len(results),
        "aggregate": aggregate_report,
        "results": results,
        "scope_note": "Compares current v1 SQLite lexical runtime retrieval against deterministic advanced retrieval v2 P0. Dense retrieval is not enabled yet.",
    }
    issues: list[dict[str, Any]] = []
    v2 = aggregate_report["v2"]
    if v2["forbidden_family_in_top8_rate"] != 0:
        issues.append({"issue": "v2_forbidden_family_detected", "rate": v2["forbidden_family_in_top8_rate"]})
    if v2["bad_top1_low_risk_for_high_risk_rate"] != 0:
        issues.append({"issue": "v2_high_risk_low_risk_top1_detected", "rate": v2["bad_top1_low_risk_for_high_risk_rate"]})
    if v2["by_skill"]["risk-triage"]["risk_clinical_family_in_top3_for_high_risk_rate"] < 1:
        issues.append({"issue": "v2_risk_clinical_family_top3_incomplete", "rate": v2["by_skill"]["risk-triage"]["risk_clinical_family_in_top3_for_high_risk_rate"]})
    if v2["by_skill"]["rebt-belief-work-support"]["rebt_method_rubric_in_top5_rate"] < 1:
        issues.append({"issue": "v2_rebt_method_rubric_top5_incomplete", "rate": v2["by_skill"]["rebt-belief-work-support"]["rebt_method_rubric_in_top5_rate"]})
    qa = {
        "schema_version": "theraskill_retrieval_benchmark_v1_vs_v2_qa_v1",
        "created_at": created_at,
        "status": "complete" if not issues else "failed",
        "case_count": len(results),
        "issues_count": len(issues),
        "issues": issues,
        "key_metrics": {
            "v1": aggregate_report["v1"],
            "v2": aggregate_report["v2"],
        },
    }
    write_json(report_path, report)
    write_json(qa_path, qa)
    write_text(summary_path, build_summary(report, qa))
    print(json.dumps({
        "report": str(report_path),
        "qa": str(qa_path),
        "summary": str(summary_path),
        "status": qa["status"],
        "case_count": len(results),
        "issues_count": len(issues),
        "v1_bad_top1_low_risk_for_high_risk_rate": aggregate_report["v1"]["bad_top1_low_risk_for_high_risk_rate"],
        "v2_bad_top1_low_risk_for_high_risk_rate": aggregate_report["v2"]["bad_top1_low_risk_for_high_risk_rate"],
        "v1_rebt_method_rubric_in_top5_rate": aggregate_report["v1"]["by_skill"]["rebt-belief-work-support"]["rebt_method_rubric_in_top5_rate"],
        "v2_rebt_method_rubric_in_top5_rate": aggregate_report["v2"]["by_skill"]["rebt-belief-work-support"]["rebt_method_rubric_in_top5_rate"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
