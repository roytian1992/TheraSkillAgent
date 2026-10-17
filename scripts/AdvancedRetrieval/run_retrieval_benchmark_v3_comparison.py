#!/usr/bin/env python3
"""Compare LLM-structured retrieval v3 backends with shared plan and embedding cache."""

from __future__ import annotations

import argparse
import json
import re
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
from retrieval_module_registry import FAISS_IVF_FLAT_INDEX_DIR, PROJECT_ROOT, load_hybrid_v3
from retrieval_surface_registry import (
    RETRIEVAL_BENCHMARK_CASES_PATH,
    RETRIEVAL_BENCHMARK_V3_COMPARISON_QA_PATH,
    RETRIEVAL_BENCHMARK_V3_COMPARISON_REPORT_PATH,
    V3_COMPARISON_CACHE_DIR,
)


DEFAULT_CASES = RETRIEVAL_BENCHMARK_CASES_PATH
DEFAULT_REPORT = RETRIEVAL_BENCHMARK_V3_COMPARISON_REPORT_PATH
DEFAULT_QA = RETRIEVAL_BENCHMARK_V3_COMPARISON_QA_PATH
DEFAULT_CACHE_DIR = V3_COMPARISON_CACHE_DIR


RETRIEVER = load_hybrid_v3()

BACKEND_SPECS = {
    "none": {"dense_backend": "none", "faiss_index_dir": str(RETRIEVER.DEFAULT_FAISS_INDEX)},
    "scoped_memmap": {"dense_backend": "memmap", "faiss_index_dir": str(RETRIEVER.DEFAULT_FAISS_INDEX)},
    "faiss_flat": {"dense_backend": "faiss", "faiss_index_dir": str(RETRIEVER.DEFAULT_FAISS_INDEX)},
    "faiss_ivf": {
        "dense_backend": "faiss",
        "faiss_index_dir": str(FAISS_IVF_FLAT_INDEX_DIR),
    },
    "faiss_shards": {"dense_backend": "faiss_shards", "faiss_index_dir": str(RETRIEVER.DEFAULT_FAISS_INDEX)},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--embedding-cache-db", default=str(DEFAULT_CACHE_DIR / "embedding_cache.sqlite"))
    parser.add_argument("--scope-count-cache-db", default=str(DEFAULT_CACHE_DIR / "scope_count_cache.sqlite"))
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--skills", nargs="*", default=[])
    parser.add_argument("--backends", nargs="+", choices=sorted(BACKEND_SPECS), default=["none", "scoped_memmap", "faiss_flat", "faiss_ivf", "faiss_shards"])
    parser.add_argument("--dense-pool-n", type=int, default=120)
    parser.add_argument("--dense-top-n", type=int, default=30)
    parser.add_argument("--bm25-top-n", type=int, default=80)
    parser.add_argument("--label-top-n", type=int, default=40)
    parser.add_argument("--required-family-top-n", type=int, default=80)
    parser.add_argument("--max-sql-allowed-ids", type=int, default=50000)
    parser.add_argument("--max-wide-scope-channel-scan", type=int, default=250000)
    parser.add_argument("--max-compact-label-scope", type=int, default=250000)
    parser.add_argument("--max-scoped-dense-ids", type=int, default=250000)
    parser.add_argument("--allow-degraded-fallback", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_case_id(case: dict[str, Any], idx: int) -> str:
    source_line = case.get("_benchmark_source_line")
    raw = str(case.get("case_id") or case.get("id") or (f"case_{source_line}" if source_line else f"case_{idx}"))
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe or f"case_{idx}"


def iter_cases(path: Path, skills: set[str], limit: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for source_line, obj in iter_jsonl(path, with_line_numbers=True):
        skill = obj.get("skill") or obj.get("expected_primary_skill")
        query = obj.get("query") or obj.get("user_query")
        if skills and skill not in skills:
            continue
        if not skill or not query:
            continue
        obj["_benchmark_source_line"] = source_line
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


def build_args(args: argparse.Namespace, case: dict[str, Any], case_id: str, backend_name: str, plan_path: Path, bundle_path: Path) -> argparse.Namespace:
    spec = BACKEND_SPECS[backend_name]
    skill = case.get("skill") or case.get("expected_primary_skill")
    query = case.get("query") or case.get("user_query")
    return argparse.Namespace(
        db=str(RETRIEVER.DEFAULT_DB),
        metadata_v2_db=str(RETRIEVER.DEFAULT_METADATA_V2_DB),
        compact_label_db=str(RETRIEVER.DEFAULT_COMPACT_LABEL_DB),
        dense_index_dir=str(RETRIEVER.DEFAULT_DENSE_INDEX),
        dense_row_map_db=str(RETRIEVER.DEFAULT_DENSE_ROW_MAP_DB),
        faiss_index_dir=spec["faiss_index_dir"],
        faiss_shards_dir=str(RETRIEVER.DEFAULT_FAISS_SHARDS_DIR),
        model_config=str(RETRIEVER.DEFAULT_MODEL_CONFIG),
        embedding_cache_db=args.embedding_cache_db if spec["dense_backend"] != "none" else None,
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
        dense_backend=spec["dense_backend"],
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
        plan_input=str(plan_path),
        plan_output=None,
        allow_degraded_fallback=args.allow_degraded_fallback,
        allow_partial_dense_index=False,
        output=str(bundle_path),
        overwrite=True,
        json=False,
    )


def ensure_plan(args: argparse.Namespace, case: dict[str, Any], case_id: str, plan_path: Path) -> tuple[dict[str, Any], bool, float]:
    if plan_path.exists() and not args.overwrite:
        return json.loads(plan_path.read_text(encoding="utf-8")), True, 0.0
    skill = case.get("skill") or case.get("expected_primary_skill")
    query = case.get("query") or case.get("user_query")
    plan_args = argparse.Namespace(
        db=str(RETRIEVER.DEFAULT_DB),
        model_config=str(RETRIEVER.DEFAULT_MODEL_CONFIG),
        prompt=str(RETRIEVER.PLANNER.DEFAULT_PROMPT),
        skill=skill,
        query=query,
        output=str(plan_path),
        overwrite=True,
        json=False,
        allow_degraded_fallback=args.allow_degraded_fallback,
    )
    started = time.time()
    artifact = RETRIEVER.PLANNER.build_plan(plan_args)
    elapsed_ms = round((time.time() - started) * 1000, 3)
    write_json(plan_path, artifact)
    return artifact, False, elapsed_ms


def row_from_bundle(case: dict[str, Any], case_id: str, backend_name: str, bundle: dict[str, Any], elapsed_ms: float, plan_cache_hit: bool, plan_elapsed_ms: float, bundle_path: Path, plan_path: Path) -> dict[str, Any]:
    expected = case_expected_families(case)
    top_families = [item.get("dedup_family") for item in bundle["items"]]
    dense_trace = bundle["retrieval_config"].get("dense_trace") or {}
    structured_filter = bundle.get("structured_filter") or {}
    embedding_cache = dense_trace.get("embedding_cache") if isinstance(dense_trace, dict) else None
    selected_shards = dense_trace.get("selected_shards")
    if isinstance(selected_shards, list):
        selected_shard_count = len(selected_shards)
    elif selected_shards is None:
        selected_shard_count = None
    else:
        selected_shard_count = int(selected_shards)
    return {
        "case_id": case_id,
        "benchmark_case_id": case.get("benchmark_case_id") or case.get("id") or case_id,
        "case_type": case.get("case_type"),
        "language": case.get("language"),
        "skill": bundle["skill"],
        "query": bundle["query"],
        "backend": backend_name,
        "elapsed_ms": elapsed_ms,
        "plan_cache_hit": plan_cache_hit,
        "plan_elapsed_ms": plan_elapsed_ms,
        "planner_status": bundle["planner_trace"]["planner_status"],
        "planner_fallback_used": bundle["planner_trace"]["fallback_used"],
        "candidate_scope": bundle["channel_counts"]["candidate_scope"],
        "scope_count_cache": {
            "enabled": structured_filter.get("scope_count_cache_enabled"),
            "hits": structured_filter.get("scope_count_cache_hits"),
            "misses": structured_filter.get("scope_count_cache_misses"),
        },
        "channel_counts": bundle["channel_counts"],
        "dense_trace_summary": {
            "backend": dense_trace.get("backend"),
            "mode": dense_trace.get("mode"),
            "raw_hits": dense_trace.get("raw_hits"),
            "filtered_hits": dense_trace.get("filtered_hits"),
            "returned_candidates": dense_trace.get("returned_candidates"),
            "selected_shards": selected_shard_count,
            "embedding_cache_hit": embedding_cache.get("cache_hit") if isinstance(embedding_cache, dict) else None,
        },
        "top_families": top_families,
        "expected_families": expected,
        "expected_family_hit_at_1": (not expected) or (top_families[:1] and top_families[0] in expected),
        "expected_family_hit_at_5": (not expected) or any(family in expected for family in top_families[:5]),
        "policy_checks": bundle["policy_checks"],
        "quality_checks": bundle["quality_checks"],
        "bundle_output": str(bundle_path),
        "plan_output": str(plan_path),
    }


def summarize_backend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_backend: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_backend.setdefault(row["backend"], []).append(row)
    summary: dict[str, Any] = {}
    for backend, backend_rows in by_backend.items():
        dense_rows = [row for row in backend_rows if row["dense_trace_summary"]["embedding_cache_hit"] is not None]
        scope_hits = sum(int((row.get("scope_count_cache") or {}).get("hits") or 0) for row in backend_rows)
        scope_misses = sum(int((row.get("scope_count_cache") or {}).get("misses") or 0) for row in backend_rows)
        summary[backend] = {
            "case_count": len(backend_rows),
            "avg_latency_ms": sum(float(row["elapsed_ms"]) for row in backend_rows) / len(backend_rows),
            "planner_fallback_rate": sum(1 for row in backend_rows if row["planner_fallback_used"]) / len(backend_rows),
            "expected_family_hit_at_1_rate": sum(1 for row in backend_rows if row["expected_family_hit_at_1"]) / len(backend_rows),
            "expected_family_hit_at_5_rate": sum(1 for row in backend_rows if row["expected_family_hit_at_5"]) / len(backend_rows),
            "embedding_cache_hit_rate": (sum(1 for row in dense_rows if row["dense_trace_summary"]["embedding_cache_hit"]) / len(dense_rows)) if dense_rows else None,
            "scope_count_cache_hits": scope_hits,
            "scope_count_cache_misses": scope_misses,
            "scope_count_cache_hit_rate": (scope_hits / (scope_hits + scope_misses)) if (scope_hits + scope_misses) else None,
            "avg_dense_filtered_hits": (
                sum(float(row["dense_trace_summary"]["filtered_hits"] or 0) for row in dense_rows) / len(dense_rows)
            ) if dense_rows else None,
        }
    return summary


def main() -> int:
    args = parse_args()
    report = Path(args.report)
    qa_report = Path(args.qa_report)
    for path in (report, qa_report):
        ensure_output_path(path, overwrite=args.overwrite)
    cases = iter_cases(Path(args.cases), set(args.skills), args.limit)
    cache_dir = Path(args.cache_dir)
    plan_dir = cache_dir / "plans"
    bundle_dir = cache_dir / "bundles"
    rows: list[dict[str, Any]] = []
    plan_rows: list[dict[str, Any]] = []

    for idx, case in enumerate(cases, start=1):
        case_id = safe_case_id(case, idx)
        plan_path = plan_dir / f"{case_id}.plan.json"
        plan_artifact, plan_cache_hit, plan_elapsed_ms = ensure_plan(args, case, case_id, plan_path)
        plan_rows.append({
            "case_id": case_id,
            "plan_path": str(plan_path),
            "plan_cache_hit": plan_cache_hit,
            "plan_elapsed_ms": plan_elapsed_ms,
            "planner_status": plan_artifact["planner_trace"]["planner_status"],
            "planner_fallback_used": plan_artifact["planner_trace"]["fallback_used"],
            "metadata_filters": plan_artifact["plan"].get("metadata_filters"),
            "target_families": plan_artifact["plan"].get("target_families"),
        })
        for backend_name in args.backends:
            bundle_path = bundle_dir / backend_name / f"{case_id}.bundle.json"
            bargs = build_args(args, case, case_id, backend_name, plan_path, bundle_path)
            started = time.time()
            bundle = RETRIEVER.build_bundle(bargs)
            elapsed_ms = round((time.time() - started) * 1000, 3)
            write_json(bundle_path, bundle)
            rows.append(row_from_bundle(case, case_id, backend_name, bundle, elapsed_ms, plan_cache_hit, plan_elapsed_ms, bundle_path, plan_path))

    issues: list[str] = []
    for row in rows:
        if not all(row["policy_checks"].values()):
            issues.append(f"policy check failed: {row['case_id']} {row['backend']}")
        if not all(row["quality_checks"].values()):
            issues.append(f"quality check failed: {row['case_id']} {row['backend']}")
    backend_summary = summarize_backend(rows)
    report_obj = {
        "schema_version": "theraskill_retrieval_benchmark_v3_backend_comparison_report_v1",
        "created_at": utc_now(),
        "cases_path": str(args.cases),
        "case_count": len(cases),
        "backend_count": len(args.backends),
        "backends": args.backends,
        "cache_dir": str(cache_dir),
        "embedding_cache_db": args.embedding_cache_db,
        "scope_count_cache_db": args.scope_count_cache_db,
        "plan_rows": plan_rows,
        "rows": rows,
        "backend_summary": backend_summary,
    }
    qa_obj = {
        "schema_version": "theraskill_retrieval_benchmark_v3_backend_comparison_qa_v1",
        "created_at": utc_now(),
        "status": "complete" if not issues else "issues_found",
        "issues_count": len(issues),
        "issues": issues,
        "case_count": len(cases),
        "backend_count": len(args.backends),
        "row_count": len(rows),
        "planner_fallback_rate": (sum(1 for row in plan_rows if row["planner_fallback_used"]) / len(plan_rows)) if plan_rows else None,
        "plan_cache_hit_rate": (sum(1 for row in plan_rows if row["plan_cache_hit"]) / len(plan_rows)) if plan_rows else None,
        "backend_summary": backend_summary,
    }
    write_json(report, report_obj)
    write_json(qa_report, qa_obj)
    print(json.dumps({
        "report": str(report),
        "qa_report": str(qa_report),
        "status": qa_obj["status"],
        "issues_count": qa_obj["issues_count"],
        "case_count": qa_obj["case_count"],
        "backend_count": qa_obj["backend_count"],
        "backend_summary": qa_obj["backend_summary"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
