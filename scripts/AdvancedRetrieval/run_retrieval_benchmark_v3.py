#!/usr/bin/env python3
"""Small benchmark runner for LLM-structured hybrid retrieval v3."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import ensure_output_path, iter_jsonl, write_json
from retrieval_module_registry import PROJECT_ROOT, load_hybrid_v3
from retrieval_surface_registry import (
    RETRIEVAL_BENCHMARK_CASES_PATH,
    RETRIEVAL_BENCHMARK_V3_QA_PATH,
    RETRIEVAL_BENCHMARK_V3_REPORT_PATH,
)


DEFAULT_CASES = RETRIEVAL_BENCHMARK_CASES_PATH
DEFAULT_REPORT = RETRIEVAL_BENCHMARK_V3_REPORT_PATH
DEFAULT_QA = RETRIEVAL_BENCHMARK_V3_QA_PATH


RETRIEVER = load_hybrid_v3()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--skills", nargs="*", default=[])
    parser.add_argument("--dense-pool-n", type=int, default=120)
    parser.add_argument("--dense-top-n", type=int, default=30)
    parser.add_argument("--dense-row-map-db", default=str(RETRIEVER.DEFAULT_DENSE_ROW_MAP_DB))
    parser.add_argument("--faiss-index-dir", default=str(RETRIEVER.DEFAULT_FAISS_INDEX))
    parser.add_argument("--faiss-shards-dir", default=str(RETRIEVER.DEFAULT_FAISS_SHARDS_DIR))
    parser.add_argument("--embedding-cache-db")
    parser.add_argument("--scope-count-cache-db")
    parser.add_argument("--bm25-top-n", type=int, default=80)
    parser.add_argument("--label-top-n", type=int, default=40)
    parser.add_argument("--required-family-top-n", type=int, default=80)
    parser.add_argument("--max-sql-allowed-ids", type=int, default=50000)
    parser.add_argument("--max-wide-scope-channel-scan", type=int, default=250000)
    parser.add_argument("--max-compact-label-scope", type=int, default=250000)
    parser.add_argument("--max-scoped-dense-ids", type=int, default=250000)
    parser.add_argument("--dense-backend", choices=["none", "memmap", "faiss", "faiss_shards"], default="memmap")
    parser.add_argument("--allow-degraded-fallback", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_cases(path: Path, skills: set[str], limit: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for obj in iter_jsonl(path):
        skill = obj.get("skill") or obj.get("expected_primary_skill")
        query = obj.get("query") or obj.get("user_query")
        if skills and skill not in skills:
            continue
        if not skill or not query:
            continue
        cases.append(obj)
        if len(cases) >= limit:
            break
    return cases


def case_expected_families(case: dict[str, Any]) -> list[str]:
    for key in ("expected_evidence_families", "expected_families", "expected_family"):
        value = case.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
    return []


def bundle_args(args: argparse.Namespace, case: dict[str, Any], idx: int) -> argparse.Namespace:
    skill = case.get("skill") or case.get("expected_primary_skill")
    query = case.get("query") or case.get("user_query")
    return argparse.Namespace(
        db=str(RETRIEVER.DEFAULT_DB),
        metadata_v2_db=str(RETRIEVER.DEFAULT_METADATA_V2_DB),
        compact_label_db=str(RETRIEVER.DEFAULT_COMPACT_LABEL_DB),
        dense_index_dir=str(RETRIEVER.DEFAULT_DENSE_INDEX),
        dense_row_map_db=str(Path(args.dense_row_map_db)),
        faiss_index_dir=str(Path(args.faiss_index_dir)),
        faiss_shards_dir=str(Path(args.faiss_shards_dir)),
        model_config=str(RETRIEVER.DEFAULT_MODEL_CONFIG),
        embedding_cache_db=args.embedding_cache_db,
        scope_count_cache_db=args.scope_count_cache_db,
        skill=skill,
        query=query,
        top_k=8,
        bm25_top_n=args.bm25_top_n,
        label_top_n=args.label_top_n,
        required_family_top_n=args.required_family_top_n,
        dense_top_n=args.dense_top_n,
        dense_pool_n=args.dense_pool_n,
        dense_chunk_size=50000,
        dense_backend=args.dense_backend,
        rrf_k=60.0,
        bm25_weight=1.0,
        dense_weight=1.0,
        label_weight=1.2,
        min_candidate_scope=100,
        max_candidate_scope=250000,
        max_sql_allowed_ids=args.max_sql_allowed_ids,
        max_wide_scope_channel_scan=args.max_wide_scope_channel_scan,
        max_compact_label_scope=args.max_compact_label_scope,
        max_scoped_dense_ids=args.max_scoped_dense_ids,
        plan_input=None,
        plan_output=str(Path(args.report).with_suffix(f".case{idx}.plan.json")),
        allow_degraded_fallback=args.allow_degraded_fallback,
        allow_partial_dense_index=False,
        output=str(Path(args.report).with_suffix(f".case{idx}.bundle.json")),
        overwrite=True,
        json=False,
    )


def main() -> int:
    args = parse_args()
    report = Path(args.report)
    qa_report = Path(args.qa_report)
    for path in (report, qa_report):
        ensure_output_path(path, overwrite=args.overwrite)
    cases = iter_cases(Path(args.cases), set(args.skills), args.limit)
    rows: list[dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        started = time.time()
        bargs = bundle_args(args, case, idx)
        bundle = RETRIEVER.build_bundle(bargs)
        elapsed_ms = round((time.time() - started) * 1000, 3)
        write_json(bargs.output, bundle)
        expected = case_expected_families(case)
        top_families = [item.get("dedup_family") for item in bundle["items"]]
        rows.append({
            "case_id": case.get("case_id") or case.get("id") or f"case_{idx}",
            "skill": bargs.skill,
            "query": bargs.query,
            "elapsed_ms": elapsed_ms,
            "planner_status": bundle["planner_trace"]["planner_status"],
            "planner_fallback_used": bundle["planner_trace"]["fallback_used"],
            "candidate_scope": bundle["channel_counts"]["candidate_scope"],
            "top_families": top_families,
            "expected_families": expected,
            "expected_family_hit_at_1": (not expected) or (top_families[:1] and top_families[0] in expected),
            "expected_family_hit_at_5": (not expected) or any(family in expected for family in top_families[:5]),
            "policy_checks": bundle["policy_checks"],
            "quality_checks": bundle["quality_checks"],
            "bundle_output": bargs.output,
            "plan_output": bargs.plan_output,
        })
    issues: list[str] = []
    for row in rows:
        if not all(row["policy_checks"].values()):
            issues.append(f"policy check failed: {row['case_id']}")
        if not all(row["quality_checks"].values()):
            issues.append(f"quality check failed: {row['case_id']}")
    report_obj = {
        "schema_version": "theraskill_retrieval_benchmark_v3_report_v1",
        "created_at": utc_now(),
        "cases_path": str(args.cases),
        "case_count": len(rows),
        "rows": rows,
    }
    qa_obj = {
        "schema_version": "theraskill_retrieval_benchmark_v3_qa_v1",
        "created_at": utc_now(),
        "status": "complete" if not issues else "issues_found",
        "issues_count": len(issues),
        "issues": issues,
        "case_count": len(rows),
        "planner_fallback_rate": (sum(1 for row in rows if row["planner_fallback_used"]) / len(rows)) if rows else None,
        "expected_family_hit_at_1_rate": (sum(1 for row in rows if row["expected_family_hit_at_1"]) / len(rows)) if rows else None,
        "expected_family_hit_at_5_rate": (sum(1 for row in rows if row["expected_family_hit_at_5"]) / len(rows)) if rows else None,
        "avg_latency_ms": (sum(float(row["elapsed_ms"]) for row in rows) / len(rows)) if rows else None,
    }
    write_json(report, report_obj)
    write_json(qa_report, qa_obj)
    print(json.dumps({
        "report": str(report),
        "qa_report": str(qa_report),
        "status": qa_obj["status"],
        "issues_count": qa_obj["issues_count"],
        "case_count": qa_obj["case_count"],
        "planner_fallback_rate": qa_obj["planner_fallback_rate"],
        "expected_family_hit_at_1_rate": qa_obj["expected_family_hit_at_1_rate"],
        "expected_family_hit_at_5_rate": qa_obj["expected_family_hit_at_5_rate"],
        "avg_latency_ms": qa_obj["avg_latency_ms"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
