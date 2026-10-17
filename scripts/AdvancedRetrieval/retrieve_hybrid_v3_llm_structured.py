#!/usr/bin/env python3
"""LLM-structured hybrid retrieval v3 for TheraSkill.

This retriever uses an LLM query plan to narrow the metadata scope before
running BM25, dense, and controlled-label channels.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR.parents[1], SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import ensure_output_path, read_json, write_json
from domains.theraskill.runtime import runtime_root
from retrieval_module_registry import PROJECT_ROOT, load_advanced_v2, load_hybrid_v2, load_planner
from retrieval_surface_registry import (
    COMPACT_LABEL_V1_DB_PATH,
    DENSE_BGE_M3_INDEX_DIR,
    DENSE_BGE_M3_ROW_MAP_DB_PATH,
    FAISS_BGE_M3_INDEX_DIR,
    FAISS_BGE_M3_SHARDS_DIR,
    HYBRID_RETRIEVAL_V3_BUNDLE_PATH,
    METADATA_V2_DB_PATH,
    PHASE6_SQLITE_DB_PATH,
    V3_EMBEDDING_CACHE_DB,
    V3_SCOPE_COUNT_CACHE_DB,
)
from domains.theraskill.theraskill_contribution.overlay import active_overlay_from_slot, query_overlay


DEFAULT_DB = PHASE6_SQLITE_DB_PATH
DEFAULT_METADATA_V2_DB = METADATA_V2_DB_PATH
DEFAULT_COMPACT_LABEL_DB = COMPACT_LABEL_V1_DB_PATH
DEFAULT_DENSE_INDEX = DENSE_BGE_M3_INDEX_DIR
DEFAULT_DENSE_ROW_MAP_DB = DENSE_BGE_M3_ROW_MAP_DB_PATH
DEFAULT_FAISS_INDEX = FAISS_BGE_M3_INDEX_DIR
DEFAULT_FAISS_SHARDS_DIR = FAISS_BGE_M3_SHARDS_DIR
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_EMBEDDING_CACHE_DB = V3_EMBEDDING_CACHE_DB
DEFAULT_SCOPE_COUNT_CACHE_DB = V3_SCOPE_COUNT_CACHE_DB
DEFAULT_OUTPUT = HYBRID_RETRIEVAL_V3_BUNDLE_PATH
DEFAULT_KNOWLEDGE_RELEASE_SLOT = runtime_root() / "contribution/release_slots/current.json"


V2 = load_advanced_v2()
HYBRID_V2 = load_hybrid_v2()
PLANNER = load_planner()

_FAISS_INDEX_CACHE: dict[str, Any] = {}
_FAISS_SHARD_IDS_CACHE: dict[str, list[dict[str, Any]]] = {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--metadata-v2-db", default=str(DEFAULT_METADATA_V2_DB))
    parser.add_argument("--compact-label-db", default=str(DEFAULT_COMPACT_LABEL_DB))
    parser.add_argument("--dense-index-dir", default=str(DEFAULT_DENSE_INDEX))
    parser.add_argument("--dense-row-map-db", default=str(DEFAULT_DENSE_ROW_MAP_DB))
    parser.add_argument("--faiss-index-dir", default=str(DEFAULT_FAISS_INDEX))
    parser.add_argument("--faiss-shards-dir", default=str(DEFAULT_FAISS_SHARDS_DIR))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--embedding-cache-db")
    parser.add_argument("--scope-count-cache-db")
    parser.add_argument("--skill", required=True, choices=sorted(V2.SKILL_POLICIES))
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--bm25-top-n", type=int, default=120)
    parser.add_argument("--label-top-n", type=int, default=80)
    parser.add_argument("--required-family-top-n", type=int, default=80)
    parser.add_argument("--dense-top-n", type=int, default=80)
    parser.add_argument("--dense-pool-n", type=int, default=400)
    parser.add_argument("--dense-chunk-size", type=int, default=50000)
    parser.add_argument("--dense-backend", choices=["none", "memmap", "faiss", "faiss_shards"], default="memmap")
    parser.add_argument("--rrf-k", type=float, default=60.0)
    parser.add_argument("--bm25-weight", type=float, default=1.0)
    parser.add_argument("--dense-weight", type=float, default=1.0)
    parser.add_argument("--label-weight", type=float, default=1.2)
    parser.add_argument("--min-candidate-scope", type=int, default=100)
    parser.add_argument("--max-candidate-scope", type=int, default=250000)
    parser.add_argument("--max-wide-scope-channel-scan", type=int, default=250000)
    parser.add_argument("--max-compact-label-scope", type=int, default=250000)
    parser.add_argument("--max-scoped-dense-ids", type=int, default=250000)
    parser.add_argument("--max-sql-allowed-ids", type=int, default=50000)
    parser.add_argument("--plan-input")
    parser.add_argument("--plan-output")
    parser.add_argument("--allow-degraded-fallback", action="store_true")
    parser.add_argument("--allow-partial-dense-index", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--knowledge-release-slot",
        default=os.environ.get("THERASKILL_KNOWLEDGE_RELEASE_SLOT", str(DEFAULT_KNOWLEDGE_RELEASE_SLOT)),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_cache_identity(config_path: Path, text: str) -> tuple[str, dict[str, Any]]:
    cfg = HYBRID_V2.read_config(config_path).get("embedding") or {}
    identity = {
        "provider": str(cfg.get("provider") or ""),
        "model": str(cfg.get("name") or ""),
        "endpoint": str(cfg.get("endpoint") or cfg.get("base_url") or ""),
        "text_sha256": sha256_text(text),
    }
    cache_key = sha256_text(json.dumps(identity, ensure_ascii=False, sort_keys=True))
    return cache_key, identity


def ensure_embedding_cache_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        create table if not exists embedding_cache (
          cache_key text primary key,
          provider text,
          model text,
          endpoint text,
          text_sha256 text not null,
          text_chars integer not null,
          dim integer not null,
          embedding_json text not null,
          created_at text not null,
          last_accessed_at text not null,
          access_count integer not null default 0
        )
        """
    )
    con.execute("create index if not exists idx_embedding_cache_text_sha on embedding_cache(text_sha256)")


def embedding_request_cached(config_path: Path, text: str, cache_db: Path | None) -> tuple[list[float], dict[str, Any]]:
    cache_key, identity = embedding_cache_identity(config_path, text)
    trace: dict[str, Any] = {
        "cache_enabled": bool(cache_db),
        "cache_hit": False,
        "cache_key": cache_key,
        "text_sha256": identity["text_sha256"],
        "text_chars": len(text),
        "provider": identity["provider"],
        "model": identity["model"],
        "endpoint": identity["endpoint"],
    }
    if not cache_db:
        embedding = HYBRID_V2.embedding_request(config_path, text)
        trace["dim"] = len(embedding)
        return embedding, trace

    cache_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(cache_db))
    try:
        ensure_embedding_cache_schema(con)
        row = con.execute("select embedding_json, dim from embedding_cache where cache_key = ?", (cache_key,)).fetchone()
        if row:
            con.execute(
                "update embedding_cache set last_accessed_at = ?, access_count = access_count + 1 where cache_key = ?",
                (utc_now(), cache_key),
            )
            con.commit()
            embedding = json.loads(row[0])
            trace.update({"cache_hit": True, "dim": int(row[1]), "cache_db": str(cache_db)})
            return embedding, trace
    finally:
        con.close()

    embedding = HYBRID_V2.embedding_request(config_path, text)
    now = utc_now()
    con = sqlite3.connect(str(cache_db))
    try:
        ensure_embedding_cache_schema(con)
        con.execute(
            """
            insert or replace into embedding_cache
              (cache_key, provider, model, endpoint, text_sha256, text_chars, dim, embedding_json, created_at, last_accessed_at, access_count)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                cache_key,
                identity["provider"],
                identity["model"],
                identity["endpoint"],
                identity["text_sha256"],
                len(text),
                len(embedding),
                json.dumps(embedding, ensure_ascii=False),
                now,
                now,
            ),
        )
        con.commit()
    finally:
        con.close()
    trace.update({"cache_hit": False, "dim": len(embedding), "cache_db": str(cache_db)})
    return embedding, trace


def stable_json_sha256(obj: Any) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def metadata_index_fingerprint(db: Path) -> dict[str, Any]:
    stat = db.stat()
    return {
        "path": str(db.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def scope_filter_signature(db: Path, plan: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    filters = plan.get("metadata_filters") or {}
    normalized_filters: dict[str, list[str]] = {}
    for key in (
        "dedup_family",
        "risk_scope",
        "default_route",
        "primary_knowledge_type",
        "primary_evidence_tier",
        "primary_language",
        "quality_confidence",
        "source_weight",
    ):
        values = [str(value) for value in (filters.get(key) or []) if str(value)]
        normalized_filters[key] = sorted(set(values))
    payload = {
        "schema_version": "theraskill_scope_count_signature_v1",
        "metadata_table": table_name(db),
        "metadata_v2": is_metadata_v2(db),
        "index": metadata_index_fingerprint(db),
        "filters": normalized_filters,
        "retrieval_eligible": 1,
    }
    return stable_json_sha256(payload), payload


def ensure_scope_count_cache_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        create table if not exists scope_count_cache (
          cache_key text primary key,
          metadata_db text not null,
          metadata_size_bytes integer not null,
          metadata_mtime_ns integer not null,
          filter_signature_json text not null,
          count integer not null,
          created_at text not null,
          last_accessed_at text not null,
          access_count integer not null default 0
        )
        """
    )
    con.execute("create index if not exists idx_scope_count_cache_metadata on scope_count_cache(metadata_db, metadata_size_bytes, metadata_mtime_ns)")


def count_scope_uncached(db: Path, plan: dict[str, Any]) -> int:
    where, params, token_filters = compile_filter(plan, metadata_v2=is_metadata_v2(db))
    if is_metadata_v2(db):
        where.extend(token_exists_clauses(token_filters, params))
    sql = f"select count(*) from {table_name(db)} m where {' and '.join(where)}"
    con = sqlite3.connect(str(db))
    try:
        return int(con.execute(sql, params).fetchone()[0])
    finally:
        con.close()


def count_scope_cached(db: Path, plan: dict[str, Any], cache_db: Path | None, trace: dict[str, Any] | None = None) -> int:
    if not cache_db:
        count = count_scope_uncached(db, plan)
        if trace is not None:
            trace["scope_count_cache_enabled"] = False
            trace["scope_count_cache_hits"] = int(trace.get("scope_count_cache_hits") or 0)
            trace["scope_count_cache_misses"] = int(trace.get("scope_count_cache_misses") or 0) + 1
        return count
    cache_key, signature = scope_filter_signature(db, plan)
    cache_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(cache_db))
    try:
        ensure_scope_count_cache_schema(con)
        row = con.execute("select count from scope_count_cache where cache_key = ?", (cache_key,)).fetchone()
        if row:
            con.execute(
                "update scope_count_cache set last_accessed_at = ?, access_count = access_count + 1 where cache_key = ?",
                (utc_now(), cache_key),
            )
            con.commit()
            if trace is not None:
                trace["scope_count_cache_enabled"] = True
                trace["scope_count_cache_db"] = str(cache_db)
                trace["scope_count_cache_hits"] = int(trace.get("scope_count_cache_hits") or 0) + 1
                trace["scope_count_cache_misses"] = int(trace.get("scope_count_cache_misses") or 0)
            return int(row[0])
    finally:
        con.close()

    count = count_scope_uncached(db, plan)
    now = utc_now()
    con = sqlite3.connect(str(cache_db))
    try:
        ensure_scope_count_cache_schema(con)
        con.execute(
            """
            insert or replace into scope_count_cache
              (cache_key, metadata_db, metadata_size_bytes, metadata_mtime_ns, filter_signature_json, count, created_at, last_accessed_at, access_count)
            values (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                cache_key,
                signature["index"]["path"],
                signature["index"]["size_bytes"],
                signature["index"]["mtime_ns"],
                json.dumps(signature, ensure_ascii=False, sort_keys=True),
                count,
                now,
                now,
            ),
        )
        con.commit()
    finally:
        con.close()
    if trace is not None:
        trace["scope_count_cache_enabled"] = True
        trace["scope_count_cache_db"] = str(cache_db)
        trace["scope_count_cache_hits"] = int(trace.get("scope_count_cache_hits") or 0)
        trace["scope_count_cache_misses"] = int(trace.get("scope_count_cache_misses") or 0) + 1
    return count


def placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def plan_args(args: argparse.Namespace) -> argparse.Namespace:
    output = args.plan_output or str(Path(args.output).with_suffix(".plan.json"))
    return argparse.Namespace(
        db=args.db,
        model_config=args.model_config,
        prompt=str(PLANNER.DEFAULT_PROMPT),
        skill=args.skill,
        query=args.query,
        output=output,
        overwrite=True,
        json=False,
        allow_degraded_fallback=args.allow_degraded_fallback,
    )


def load_or_build_plan(args: argparse.Namespace) -> dict[str, Any]:
    if args.plan_input:
        return read_json(Path(args.plan_input))
    artifact = PLANNER.build_plan(plan_args(args))
    if args.plan_output:
        path = Path(args.plan_output)
        write_json(path, artifact, overwrite=True)
    return artifact


def compile_filter(plan: dict[str, Any], *, include_fts: bool = False, fts_alias: str = "retrieval_fts", metadata_v2: bool = False) -> tuple[list[str], list[Any], list[tuple[str, str]]]:
    filters = plan.get("metadata_filters") or {}
    where: list[str] = ["m.retrieval_eligible = 1"]
    params: list[Any] = []
    token_filters: list[tuple[str, str]] = []
    if include_fts:
        where.insert(0, f"{fts_alias} match ?")
    scalar_columns = ["dedup_family", "risk_scope", "default_route", "primary_knowledge_type", "primary_evidence_tier"]
    if metadata_v2:
        scalar_columns.extend(["primary_language", "quality_confidence", "source_weight"])
    for column in scalar_columns:
        values = [str(value) for value in (filters.get(column) or []) if str(value)]
        if values:
            where.append(f"m.{column} in ({placeholders(values)})")
            params.extend(values)
    # Token/list filters are kept in metadata_v2 for later join/temporary-table
    # retrieval, but low-selectivity EXISTS clauses are too slow on the full
    # 2.2M-row index. The current v3 runtime path uses scalar metadata only.
    return where, params, token_filters


def token_exists_clauses(token_filters: list[tuple[str, str]], params: list[Any]) -> list[str]:
    clauses: list[str] = []
    for field, value in token_filters:
        clauses.append(
            "exists (select 1 from metadata_v2_tokens t where t.retrieval_id = m.retrieval_id and t.field = ? and t.value = ?)"
        )
        params.extend([field, value])
    return clauses


def is_metadata_v2(db: Path) -> bool:
    return db.name == "metadata_v2.sqlite"


def table_name(db: Path) -> str:
    return "metadata_v2" if is_metadata_v2(db) else "retrieval_meta"


def count_scope(db: Path, plan: dict[str, Any]) -> int:
    return count_scope_uncached(db, plan)


def count_family_scope(db: Path, plan: dict[str, Any], family: str, cache_db: Path | None = None, trace: dict[str, Any] | None = None) -> int:
    scoped = json.loads(json.dumps(plan, ensure_ascii=False))
    scoped.setdefault("metadata_filters", {})["dedup_family"] = [family]
    return count_scope_cached(db, scoped, cache_db, trace)


def relaxed_plan(plan: dict[str, Any], step: str) -> dict[str, Any]:
    clone = json.loads(json.dumps(plan, ensure_ascii=False))
    filters = clone.setdefault("metadata_filters", {})
    if step == "drop_primary_knowledge_type":
        filters["primary_knowledge_type"] = []
        filters["primary_evidence_tier"] = []
    elif step == "drop_risk_scope":
        filters["risk_scope"] = []
    elif step == "drop_default_route":
        filters["default_route"] = []
    elif step == "broaden_family":
        families = set(filters.get("dedup_family") or [])
        families.update(clone.get("target_families", {}).get("preferred") or [])
        families.update(clone.get("target_families", {}).get("required") or [])
        filters["dedup_family"] = sorted(families)
    elif step == "skill_policy_family":
        filters["dedup_family"] = list(V2.SKILL_POLICIES[clone.get("skill_intent", {}).get("name") or "emotional-support"].get("families") or [])
    return clone


def required_families(plan: dict[str, Any]) -> list[str]:
    families = []
    families.extend(plan.get("target_families", {}).get("required") or [])
    families.extend(plan.get("rerank_preferences", {}).get("must_include_families") or [])
    return sorted({str(family) for family in families if str(family)})


def apply_skill_retrieval_invariants(plan: dict[str, Any], skill: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    scoped = json.loads(json.dumps(plan, ensure_ascii=False))
    trace: list[dict[str, Any]] = []

    def ensure_family(path_name: str, container: dict[str, Any], key: str, family: str, reason: str) -> None:
        values = [str(value) for value in (container.get(key) or []) if str(value)]
        if family in values:
            return
        values.append(family)
        container[key] = values
        trace.append({"field": f"{path_name}.{key}", "values": [family], "reason": reason})

    if skill == "rebt-belief-work-support":
        target = scoped.setdefault("target_families", {})
        rerank = scoped.setdefault("rerank_preferences", {})
        ensure_family("target_families", target, "required", "method_rubric", "rebt_skill_requires_method_rubric_evidence")
        ensure_family("rerank_preferences", rerank, "must_include_families", "method_rubric", "rebt_skill_requires_method_rubric_evidence")
    elif skill == "cognitive-reframing-support":
        rerank = scoped.setdefault("rerank_preferences", {})
        ensure_family("rerank_preferences", rerank, "must_include_families", "method_rubric", "cognitive_reframing_skill_requires_method_rubric_evidence")

    return scoped, trace


def apply_scope_policy(plan: dict[str, Any], skill: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    scoped = json.loads(json.dumps(plan, ensure_ascii=False))
    filters = scoped.setdefault("metadata_filters", {})
    trace: list[dict[str, Any]] = []

    def set_if_empty(key: str, values: list[str], reason: str) -> None:
        if filters.get(key):
            return
        filters[key] = values
        trace.append({"field": key, "values": values, "reason": reason})

    def force_set(key: str, values: list[str], reason: str) -> None:
        old_values = list(filters.get(key) or [])
        if old_values == values:
            return
        filters[key] = values
        trace.append({"field": key, "old_values": old_values, "values": values, "reason": reason})

    if skill == "emotional-support":
        force_set("default_route", ["reviewed_cluster_retrieval"], "emotional_support_prefers_reviewed_clusters_over_raw_style_pool")
        set_if_empty("primary_knowledge_type", ["style_example"], "emotional_support_primary_style_examples")
        if "style_response" in set(filters.get("dedup_family") or []):
            filters["dedup_family"] = ["style_response", "qa_psychoeducation", "therapy_process"]
    elif skill == "cognitive-reframing-support":
        set_if_empty("primary_knowledge_type", ["reframing_step", "thinking_trap_unit", "support_qa_unit", "answer_strategy_sequence"], "cognitive_reframing_prefers_reframe_and_thinking_trap_units")
        set_if_empty("default_route", ["raw_passthrough_retrieval", "reviewed_cluster_retrieval"], "cognitive_reframing_uses_method_and_reviewed_support")
        filters["dedup_family"] = ["method_rubric", "qa_psychoeducation", "style_response"]
        scoped.setdefault("rerank_preferences", {}).setdefault("must_include_families", [])
        if "method_rubric" not in scoped["rerank_preferences"]["must_include_families"]:
            scoped["rerank_preferences"]["must_include_families"].append("method_rubric")
            trace.append({"field": "rerank_preferences.must_include_families", "values": ["method_rubric"], "reason": "cognitive_reframing_requires_method_rubric_channel"})
    elif skill == "therapy-response-reviewer":
        set_if_empty("primary_knowledge_type", ["interaction_pattern", "case_trajectory", "case_formulation_pattern", "action_sequence_pattern", "reframing_step", "quality_check_rubric", "support_qa_unit"], "reviewer_prefers_process_and_method_quality_units")
        filters["dedup_family"] = ["therapy_process", "method_rubric", "qa_psychoeducation", "style_response"]
    elif skill == "rebt-belief-work-support":
        filters["dedup_family"] = ["method_rubric", "qa_psychoeducation", "therapy_process"]
        set_if_empty("primary_knowledge_type", ["reframing_step", "thinking_trap_unit", "support_qa_unit", "answer_strategy_sequence"], "rebt_prefers_belief_reframe_units")
        scoped.setdefault("rerank_preferences", {}).setdefault("must_include_families", [])
        if "method_rubric" not in scoped["rerank_preferences"]["must_include_families"]:
            scoped["rerank_preferences"]["must_include_families"].append("method_rubric")
            trace.append({"field": "rerank_preferences.must_include_families", "values": ["method_rubric"], "reason": "rebt_requires_method_rubric_channel"})

    return scoped, trace


def repair_required_family_filters(db: Path, plan: dict[str, Any], trace: dict[str, Any], cache_db: Path | None = None) -> dict[str, Any]:
    current = plan
    for family in required_families(current):
        if count_family_scope(db, current, family, cache_db, trace) > 0:
            continue
        repaired = json.loads(json.dumps(current, ensure_ascii=False))
        filters = repaired.setdefault("metadata_filters", {})
        before = {
            "family": family,
            "default_route": list(filters.get("default_route") or []),
            "risk_scope": list(filters.get("risk_scope") or []),
            "primary_knowledge_type": list(filters.get("primary_knowledge_type") or []),
            "primary_evidence_tier": list(filters.get("primary_evidence_tier") or []),
        }
        filters["default_route"] = []
        filters["risk_scope"] = []
        filters["primary_knowledge_type"] = []
        filters["primary_evidence_tier"] = []
        after_count = count_family_scope(db, repaired, family, cache_db, trace)
        trace.setdefault("relaxation_trace", []).append({
            "step": "required_family_filter_repair",
            "before": before,
            "after_family_scope_count": after_count,
        })
        # Do not widen the global retrieval scope to satisfy one required
        # family. Required-family backfill has its own channel and can relax
        # family-specific filters without letting broad style/QA pools back in.
    return current


def resolve_plan_scope(db: Path, plan: dict[str, Any], min_scope: int, max_scope: int, scope_count_cache_db: Path | None = None, runtime_skill: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    trace: dict[str, Any] = {
        "initial_candidate_scope_count": None,
        "candidate_scope_count": None,
        "skill_invariant_trace": [],
        "scope_policy_trace": [],
        "relaxation_trace": [],
        "max_candidate_scope": max_scope,
        "min_candidate_scope": min_scope,
        "scope_count_cache_enabled": bool(scope_count_cache_db),
        "scope_count_cache_db": str(scope_count_cache_db) if scope_count_cache_db else None,
        "scope_count_cache_hits": 0,
        "scope_count_cache_misses": 0,
    }
    skill = str(runtime_skill or plan.get("skill_intent", {}).get("name") or "")
    current, invariant_trace = apply_skill_retrieval_invariants(plan, skill)
    trace["skill_invariant_trace"] = invariant_trace
    initial_count = count_scope_cached(db, current, scope_count_cache_db, trace)
    trace["initial_candidate_scope_count"] = initial_count
    current_count = initial_count
    if initial_count > max_scope:
        policy_plan, policy_trace = apply_scope_policy(current, skill)
        if policy_trace:
            policy_count = count_scope_cached(db, policy_plan, scope_count_cache_db, trace)
            trace["scope_policy_trace"].append({
                "before": initial_count,
                "after": policy_count,
                "changes": policy_trace,
            })
            if policy_count > 0:
                current = policy_plan
                current_count = policy_count
    current = repair_required_family_filters(db, current, trace, scope_count_cache_db)
    policy_recount = count_scope_cached(db, current, scope_count_cache_db, trace)
    if policy_recount != current_count:
        trace["relaxation_trace"].append({"step": "recount_after_scope_policy", "before": current_count, "after": policy_recount})
        current_count = policy_recount
    for step in ("drop_primary_knowledge_type", "broaden_family", "drop_default_route", "drop_risk_scope", "skill_policy_family"):
        if current_count >= min_scope:
            break
        next_plan = relaxed_plan(current, step)
        next_count = count_scope_cached(db, next_plan, scope_count_cache_db, trace)
        trace["relaxation_trace"].append({"step": step, "before": current_count, "after": next_count})
        current = next_plan
        current_count = next_count
    trace["candidate_scope_count"] = current_count
    if current_count > max_scope:
        trace["warnings"] = [f"candidate scope remains large: {current_count}"]
    return current, trace


def fts_match_query(queries: list[str], fallback: str) -> str:
    tokens: list[str] = []
    seen: set[str] = set()
    for query in queries or [fallback]:
        for token in str(query).replace('"', " ").split():
            token = token.strip()
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return " OR ".join(f'"{token}"' for token in tokens[:40]) or f'"{fallback.replace(chr(34), chr(32))}"'


def allowed_clause_sql(allowed_ids: set[str] | None, params: list[Any], max_sql_allowed_ids: int) -> str | None:
    if allowed_ids is None:
        return ""
    if not allowed_ids:
        return None
    if len(allowed_ids) > max_sql_allowed_ids:
        return ""
    allowed = list(allowed_ids)
    params.extend(allowed)
    return f" and m.retrieval_id in ({placeholders(allowed)})"


def should_skip_wide_scan(allowed_ids: set[str] | None, max_sql_allowed_ids: int) -> bool:
    return allowed_ids is not None and len(allowed_ids) > max_sql_allowed_ids


def fetch_bm25_candidates(db: Path, plan: dict[str, Any], query: str, limit: int, allowed_ids: set[str] | None = None, max_sql_allowed_ids: int = 50000) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    search_queries = plan.get("search_queries") or {}
    match = fts_match_query(search_queries.get("bm25_queries") or [], query)
    where, params, _ = compile_filter(plan, include_fts=True)
    params.insert(0, match)
    allow_sql = allowed_clause_sql(allowed_ids, params, max_sql_allowed_ids)
    if allow_sql is None:
        return [], {"fts_query": match, "limit": limit, "allowed_ids": 0}
    sql = f"""
        select
          m.retrieval_id,
          m.release_unit_id,
          m.release_unit_type,
          m.dedup_family,
          m.canonical_knowledge_type,
          m.canonical_label,
          m.primary_dataset,
          m.primary_knowledge_type,
          m.primary_evidence_tier,
          m.risk_scope,
          m.default_route,
          m.member_count,
          m.retrieval_text_chars,
          bm25(retrieval_fts) as bm25_score,
          substr(f.retrieval_text, 1, 1200) as text_preview
        from retrieval_fts f
        join retrieval_meta m on m.retrieval_id = f.retrieval_id
        where {' and '.join(where)}{allow_sql}
        order by bm25_score asc
        limit ?
    """
    params.append(limit)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        params[0] = f'"{query.replace(chr(34), chr(32))}"'
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows], {
        "fts_query": match,
        "limit": limit,
        "allowed_ids": len(allowed_ids) if allowed_ids is not None else None,
        "allowed_ids_applied_to_sql": allowed_ids is not None and len(allowed_ids) <= max_sql_allowed_ids,
    }


def fetch_label_candidates(db: Path, plan: dict[str, Any], limit: int, allowed_ids: set[str] | None = None, max_sql_allowed_ids: int = 50000) -> list[dict[str, Any]]:
    label_queries = [str(value).lower() for value in (plan.get("search_queries", {}).get("label_queries") or []) if str(value).strip()]
    if not label_queries:
        return []
    if should_skip_wide_scan(allowed_ids, max_sql_allowed_ids):
        return []
    where, params, _ = compile_filter(plan)
    allow_sql = allowed_clause_sql(allowed_ids, params, max_sql_allowed_ids)
    if allow_sql is None:
        return []
    label_where = []
    for value in label_queries[:12]:
        label_where.append("(lower(coalesce(m.canonical_label,'')) like ? or lower(coalesce(m.canonical_knowledge_type,'')) like ? or lower(coalesce(m.primary_knowledge_type,'')) like ?)")
        pattern = f"%{value}%"
        params.extend([pattern, pattern, pattern])
    sql = f"""
        select
          m.retrieval_id,
          m.release_unit_id,
          m.release_unit_type,
          m.dedup_family,
          m.canonical_knowledge_type,
          m.canonical_label,
          m.primary_dataset,
          m.primary_knowledge_type,
          m.primary_evidence_tier,
          m.risk_scope,
          m.default_route,
          m.member_count,
          m.retrieval_text_chars,
          '' as text_preview
        from retrieval_meta m
        where {' and '.join(where)}{allow_sql} and ({' or '.join(label_where)})
        order by m.member_count desc, m.retrieval_text_chars desc
        limit ?
    """
    params.append(limit)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def fetch_required_family_candidates(db: Path, plan: dict[str, Any], query: str, limit: int, allowed_ids: set[str] | None = None, max_sql_allowed_ids: int = 50000, skip_fts: bool = False) -> list[dict[str, Any]]:
    families = required_families(plan)
    if not families:
        return []
    scoped_allowed_ids = allowed_ids if not should_skip_wide_scan(allowed_ids, max_sql_allowed_ids) else None
    filters = dict(plan.get("metadata_filters") or {})
    routes = [str(value) for value in (filters.get("default_route") or []) if str(value)]
    if not skip_fts:
        search_queries = []
        search_queries.extend(plan.get("search_queries", {}).get("label_queries") or [])
        search_queries.extend(plan.get("search_queries", {}).get("bm25_queries") or [])
        match = fts_match_query(search_queries, query)
        where = ["retrieval_fts match ?", "m.retrieval_eligible = 1", f"m.dedup_family in ({placeholders(families)})"]
        params: list[Any] = [match]
        params.extend(families)
        if routes:
            where.append(f"m.default_route in ({placeholders(routes)})")
            params.extend(routes)
        allow_sql = allowed_clause_sql(scoped_allowed_ids, params, max_sql_allowed_ids)
        if allow_sql is None:
            return []
        sql = f"""
            select
              m.retrieval_id,
              m.release_unit_id,
              m.release_unit_type,
              m.dedup_family,
              m.canonical_knowledge_type,
              m.canonical_label,
              m.primary_dataset,
              m.primary_knowledge_type,
              m.primary_evidence_tier,
              m.risk_scope,
              m.default_route,
              m.member_count,
              m.retrieval_text_chars,
              bm25(retrieval_fts) as bm25_score,
              substr(f.retrieval_text, 1, 1200) as text_preview
            from retrieval_fts f
            join retrieval_meta m on m.retrieval_id = f.retrieval_id
            where {' and '.join(where)}{allow_sql}
            order by bm25_score asc
            limit ?
        """
        params.append(limit)
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in con.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            rows = []
        finally:
            con.close()
        if rows:
            return rows

    # If lexical matching is too narrow, still provide a small required-family
    # fallback so quota selection can include the family mandated by the LLM plan.
    def fallback_query(use_routes: bool, use_allowed_ids: bool) -> list[dict[str, Any]]:
        where = ["m.retrieval_eligible = 1", f"m.dedup_family in ({placeholders(families)})"]
        params = list(families)
        if use_routes and routes:
            where.append(f"m.default_route in ({placeholders(routes)})")
            params.extend(routes)
        allow_sql = allowed_clause_sql(scoped_allowed_ids if use_allowed_ids else None, params, max_sql_allowed_ids)
        if allow_sql is None:
            return []
        sql = f"""
            select
              m.retrieval_id,
              m.release_unit_id,
              m.release_unit_type,
              m.dedup_family,
              m.canonical_knowledge_type,
              m.canonical_label,
              m.primary_dataset,
              m.primary_knowledge_type,
              m.primary_evidence_tier,
              m.risk_scope,
              m.default_route,
              m.member_count,
              m.retrieval_text_chars,
              '' as text_preview
            from retrieval_meta m
            where {' and '.join(where)}{allow_sql}
            order by m.member_count desc, m.retrieval_text_chars desc
            limit ?
        """
        params.append(limit)
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    rows = fallback_query(use_routes=True, use_allowed_ids=True)
    if rows or not routes:
        return rows
    rows = fallback_query(use_routes=False, use_allowed_ids=True)
    if rows:
        return rows
    return fallback_query(use_routes=False, use_allowed_ids=False)


def fetch_policy_family_fallback(db: Path, skill: str, plan: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    families = list(V2.SKILL_POLICIES[skill].get("families") or [])
    filters = dict(plan.get("metadata_filters") or {})
    plan_families = [str(value) for value in (filters.get("dedup_family") or []) if str(value)]
    if plan_families:
        families = [family for family in families if family in set(plan_families)] or families
    blocked = set(V2.SKILL_POLICIES[skill].get("blocked_families") or [])
    families = [family for family in families if family not in blocked]
    if not families:
        return []
    routes = [str(value) for value in (filters.get("default_route") or []) if str(value)]
    where = ["m.retrieval_eligible = 1", f"m.dedup_family in ({placeholders(families)})"]
    params: list[Any] = list(families)
    if routes:
        where.append(f"m.default_route in ({placeholders(routes)})")
        params.extend(routes)
    sql = f"""
        select
          m.retrieval_id,
          m.release_unit_id,
          m.release_unit_type,
          m.dedup_family,
          m.canonical_knowledge_type,
          m.canonical_label,
          m.primary_dataset,
          m.primary_knowledge_type,
          m.primary_evidence_tier,
          m.risk_scope,
          m.default_route,
          m.member_count,
          m.retrieval_text_chars,
          '' as text_preview
        from retrieval_meta m
        where {' and '.join(where)}
        order by
          case m.dedup_family
            {''.join(f" when '{family}' then {idx}" for idx, family in enumerate(families))}
            else 999
          end,
          m.member_count desc,
          m.retrieval_text_chars desc
        limit ?
    """
    params.append(limit)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def fetch_compact_label_candidates(compact_db: Path, plan: dict[str, Any], query: str, limit: int, scope_count: int, max_scope: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not compact_db.exists():
        return [], {"skipped": True, "reason": "compact_label_db_missing", "compact_label_db": str(compact_db)}
    if scope_count > max_scope:
        return [], {
            "skipped": True,
            "reason": "candidate_scope_exceeds_max_compact_label_scope",
            "candidate_scope": scope_count,
            "max_compact_label_scope": max_scope,
            "compact_label_db": str(compact_db),
        }
    search_queries = []
    search_queries.extend(plan.get("search_queries", {}).get("label_queries") or [])
    search_queries.extend(plan.get("search_queries", {}).get("bm25_queries") or [])
    search_queries.extend(plan.get("search_queries", {}).get("dense_queries") or [])
    match = fts_match_query(search_queries, query)
    filters = dict(plan.get("metadata_filters") or {})
    where = ["compact_label_fts match ?", "m.retrieval_eligible = 1"]
    params: list[Any] = [match]
    for column in ("dedup_family", "risk_scope", "default_route", "primary_knowledge_type", "primary_evidence_tier"):
        values = [str(value) for value in (filters.get(column) or []) if str(value)]
        if values:
            where.append(f"m.{column} in ({placeholders(values)})")
            params.extend(values)
    sql = f"""
        select
          m.retrieval_id,
          m.release_unit_id,
          m.dedup_family,
          m.canonical_knowledge_type,
          m.canonical_label,
          m.primary_dataset,
          m.primary_knowledge_type,
          m.primary_evidence_tier,
          m.risk_scope,
          m.default_route,
          m.member_count,
          m.retrieval_text_chars,
          bm25(compact_label_fts) as bm25_score,
          substr(f.compact_text, 1, 1200) as text_preview
        from compact_label_fts f
        join compact_label_meta m on m.retrieval_id = f.retrieval_id
        where {' and '.join(where)}
        order by bm25_score asc
        limit ?
    """
    params.append(limit)
    con = sqlite3.connect(str(compact_db))
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in con.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        params[0] = f'"{query.replace(chr(34), chr(32))}"'
        rows = [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()
    return rows, {"fts_query": match, "limit": limit, "compact_label_db": str(compact_db)}


def allowed_retrieval_ids(db: Path, plan: dict[str, Any], max_materialized_ids: int | None = None, known_count: int | None = None) -> set[str] | None:
    if max_materialized_ids is not None and known_count is not None and known_count > max_materialized_ids:
        return None
    where, params, token_filters = compile_filter(plan, metadata_v2=is_metadata_v2(db))
    if is_metadata_v2(db):
        where.extend(token_exists_clauses(token_filters, params))
    sql = f"select retrieval_id from {table_name(db)} m where {' and '.join(where)}"
    con = sqlite3.connect(str(db))
    try:
        return {str(row[0]) for row in con.execute(sql, params)}
    finally:
        con.close()


def dense_query_text(plan: dict[str, Any], fallback: str) -> str:
    queries = plan.get("search_queries", {}).get("dense_queries") or []
    if queries:
        return "\n".join(str(query) for query in queries[:3])
    return fallback


def fetch_dense_row_indices(row_map_db: Path, allowed_ids: set[str], max_ids: int) -> tuple[list[tuple[str, int]], dict[str, Any]]:
    if not row_map_db.exists():
        return [], {"mode": "scoped_memmap", "status": "skipped", "reason": "dense_row_map_missing", "row_map_db": str(row_map_db)}
    if len(allowed_ids) > max_ids:
        return [], {
            "mode": "scoped_memmap",
            "status": "skipped",
            "reason": "allowed_ids_exceeds_max_scoped_dense_ids",
            "allowed_ids": len(allowed_ids),
            "max_scoped_dense_ids": max_ids,
            "row_map_db": str(row_map_db),
        }
    con = sqlite3.connect(str(row_map_db))
    rows: list[tuple[str, int]] = []
    try:
        ids = list(allowed_ids)
        for start in range(0, len(ids), 800):
            chunk = ids[start:start + 800]
            sql = f"select retrieval_id, row_index from dense_row_map where retrieval_id in ({placeholders(chunk)})"
            rows.extend((str(row[0]), int(row[1])) for row in con.execute(sql, chunk).fetchall())
    finally:
        con.close()
    return rows, {
        "mode": "scoped_memmap",
        "status": "complete",
        "row_map_db": str(row_map_db),
        "allowed_ids": len(allowed_ids),
        "matched_dense_rows": len(rows),
    }


def fetch_retrieval_ids_by_dense_row_indices(row_map_db: Path, row_indices: list[int]) -> dict[int, str]:
    if not row_indices or not row_map_db.exists():
        return {}
    con = sqlite3.connect(str(row_map_db))
    rows: dict[int, str] = {}
    try:
        indices = list(dict.fromkeys(int(idx) for idx in row_indices if int(idx) >= 0))
        for start in range(0, len(indices), 800):
            chunk = indices[start:start + 800]
            sql = f"select row_index, retrieval_id from dense_row_map where row_index in ({placeholders(chunk)})"
            for row_index, retrieval_id in con.execute(sql, chunk).fetchall():
                rows[int(row_index)] = str(retrieval_id)
    finally:
        con.close()
    return rows


def dense_search_row_indices(index_state: dict[str, Any], query_embedding: list[float], row_indices: list[tuple[str, int]], pool_n: int, chunk_size: int) -> list[dict[str, Any]]:
    if not row_indices:
        return []
    dim = int(index_state["dimension"])
    dtype = np.dtype(str(index_state["dtype"]))
    vectors = np.memmap(index_state["vectors"], dtype=dtype, mode="r", shape=(int(index_state["total_count"]), dim))
    query = np.asarray(query_embedding, dtype=np.float32)
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    pairs = sorted(row_indices, key=lambda item: item[1])
    heap: list[tuple[float, str, int]] = []
    for start in range(0, len(pairs), chunk_size):
        chunk = pairs[start:start + chunk_size]
        indices = np.asarray([row_index for _, row_index in chunk], dtype=np.int64)
        arr = vectors[indices].astype(np.float32)
        norms = np.linalg.norm(arr, axis=1)
        norms[norms == 0] = 1.0
        sims = (arr / norms[:, None]) @ query
        local_n = min(pool_n, len(sims))
        if local_n <= 0:
            continue
        local_idx = np.argpartition(sims, -local_n)[-local_n:]
        for offset in local_idx:
            score = float(sims[offset])
            retrieval_id, row_index = chunk[int(offset)]
            if len(heap) < pool_n:
                heapq.heappush(heap, (score, retrieval_id, row_index))
            elif score > heap[0][0]:
                heapq.heapreplace(heap, (score, retrieval_id, row_index))
    top = sorted(heap, key=lambda item: item[0], reverse=True)
    hits: list[dict[str, Any]] = []
    for rank, (score, retrieval_id, row_index) in enumerate(top, start=1):
        hits.append({
            "retrieval_id": retrieval_id,
            "row_index": row_index,
            "dense_rank": rank,
            "dense_score": round(score, 6),
        })
    return hits


def cached_faiss_search(faiss_state: dict[str, Any], dense_state: dict[str, Any], row_map_db: Path, query_embedding: list[float], pool_n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("faiss backend requested but faiss is not available in this Python environment") from exc
    if int(faiss_state["dimension"]) != int(dense_state["dimension"]):
        raise RuntimeError(f"FAISS/dense dimension mismatch: {faiss_state['dimension']} vs {dense_state['dimension']}")
    index_path = str(faiss_state["index"])
    cache_hit = index_path in _FAISS_INDEX_CACHE
    index = _FAISS_INDEX_CACHE.get(index_path)
    if index is None:
        index = faiss.read_index(index_path)
        ann = faiss_state.get("ann_parameters") or {}
        if hasattr(index, "nprobe") and ann.get("nprobe"):
            index.nprobe = int(ann["nprobe"])
        _FAISS_INDEX_CACHE[index_path] = index
    query = np.asarray([query_embedding], dtype=np.float32)
    query = HYBRID_V2.vector_norm_rows(query)
    k = min(pool_n, int(index.ntotal))
    if k <= 0:
        return [], {"faiss_index_cache_hit": cache_hit, "faiss_k": k, "faiss_ntotal": int(index.ntotal)}
    distances, indices = index.search(query, k)
    row_indices = [int(idx) for idx in indices[0].tolist() if int(idx) >= 0]
    retrieval_ids = fetch_retrieval_ids_by_dense_row_indices(row_map_db, row_indices)
    hits: list[dict[str, Any]] = []
    for rank, (idx, score) in enumerate(zip(indices[0].tolist(), distances[0].tolist()), start=1):
        idx = int(idx)
        if idx < 0:
            continue
        retrieval_id = retrieval_ids.get(idx)
        if not retrieval_id:
            continue
        hits.append({
            "retrieval_id": retrieval_id,
            "row_index": idx,
            "dense_rank": rank,
            "dense_score": round(float(score), 6),
        })
    return hits, {
        "faiss_index_cache_hit": cache_hit,
        "faiss_k": k,
        "faiss_ntotal": int(index.ntotal),
        "faiss_row_map_hits": len(retrieval_ids),
    }


def load_shard_ids(ids_path: str) -> list[dict[str, Any]]:
    if ids_path in _FAISS_SHARD_IDS_CACHE:
        return _FAISS_SHARD_IDS_CACHE[ids_path]
    rows: list[dict[str, Any]] = []
    with Path(ids_path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    _FAISS_SHARD_IDS_CACHE[ids_path] = rows
    return rows


def selected_faiss_shards(shards_dir: Path, plan: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = shards_dir / "manifest.json"
    if not manifest_path.exists():
        return [], {"status": "skipped", "reason": "faiss_shards_manifest_missing", "manifest": str(manifest_path)}
    manifest = read_json(manifest_path)
    filters = plan.get("metadata_filters") or {}
    families = {str(value) for value in (filters.get("dedup_family") or []) if str(value)}
    routes = {str(value) for value in (filters.get("default_route") or []) if str(value)}
    required = set(required_families(plan))
    selected = []
    route_relaxed = []
    for shard in manifest.get("shards") or []:
        family = str(shard.get("dedup_family") or "")
        route = str(shard.get("default_route") or "")
        family_ok = not families or family in families
        route_ok = not routes or route in routes
        if family_ok and not route_ok and family in required:
            route_relaxed.append({"dedup_family": family, "default_route": route, "shard_name": shard.get("shard_name")})
            route_ok = True
        if family_ok and route_ok:
            selected.append(shard)
    return selected, {
        "status": "complete",
        "manifest": str(manifest_path),
        "available_shards": len(manifest.get("shards") or []),
        "selected_shards": len(selected),
        "family_filter": sorted(families),
        "route_filter": sorted(routes),
        "required_family_route_relaxed_shards": route_relaxed,
    }


def faiss_shard_search(shards_dir: Path, plan: dict[str, Any], query_embedding: list[float], pool_n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("faiss_shards backend requested but faiss is not available in this Python environment") from exc
    shards, trace = selected_faiss_shards(shards_dir, plan)
    if not shards:
        trace.update({"raw_hits": 0, "reason": trace.get("reason") or "no_matching_shards"})
        return [], trace
    query = np.asarray([query_embedding], dtype=np.float32)
    query = HYBRID_V2.vector_norm_rows(query)
    per_shard_k = max(pool_n, 1)
    merged: list[dict[str, Any]] = []
    shard_traces: list[dict[str, Any]] = []
    for shard in shards:
        artifacts = shard.get("artifacts") or {}
        index_ref = str(artifacts.get("index") or "")
        ids_ref = str(artifacts.get("ids") or "")
        if not index_ref or not ids_ref:
            continue
        index_path = str((shards_dir / index_ref).resolve()) if not Path(index_ref).is_absolute() else index_ref
        ids_path = str((shards_dir / ids_ref).resolve()) if not Path(ids_ref).is_absolute() else ids_ref
        cache_hit = index_path in _FAISS_INDEX_CACHE
        index = _FAISS_INDEX_CACHE.get(index_path)
        if index is None:
            index = faiss.read_index(index_path)
            _FAISS_INDEX_CACHE[index_path] = index
        k = min(per_shard_k, int(index.ntotal))
        if k <= 0:
            continue
        distances, indices = index.search(query, k)
        id_rows = load_shard_ids(ids_path)
        added = 0
        for rank, (local_idx, score) in enumerate(zip(indices[0].tolist(), distances[0].tolist()), start=1):
            local_idx = int(local_idx)
            if local_idx < 0 or local_idx >= len(id_rows):
                continue
            meta = id_rows[local_idx]
            merged.append({
                "retrieval_id": meta.get("retrieval_id"),
                "row_index": meta.get("row_index"),
                "dense_rank": rank,
                "dense_score": round(float(score), 6),
                "_shard_name": shard.get("shard_name"),
            })
            added += 1
        shard_traces.append({
            "shard_name": shard.get("shard_name"),
            "dedup_family": shard.get("dedup_family"),
            "default_route": shard.get("default_route"),
            "cache_hit": cache_hit,
            "ntotal": int(index.ntotal),
            "returned": added,
        })
    merged.sort(key=lambda item: float(item.get("dense_score") or 0.0), reverse=True)
    seen = set()
    hits: list[dict[str, Any]] = []
    for item in merged:
        rid = str(item.get("retrieval_id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        item = dict(item)
        item["dense_rank"] = len(hits) + 1
        item.pop("_shard_name", None)
        hits.append(item)
        if len(hits) >= pool_n:
            break
    trace.update({
        "mode": "faiss_shards",
        "raw_hits": len(merged),
        "deduped_hits": len(hits),
        "shards": shard_traces,
    })
    return hits, trace


def fetch_dense_candidates(args: argparse.Namespace, db: Path, plan: dict[str, Any], allowed_ids: set[str] | None) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    if args.dense_backend == "none":
        return [], {"status": "disabled", "backend": "none"}, None, {"status": "disabled", "backend": "none"}
    if args.dense_backend == "faiss_shards":
        # The published shard bundle contains its own per-shard metadata and
        # ids. No training-time memmap is required for this backend.
        dense_state = {
            "backend": "faiss_shards",
            "index_dir": str(args.faiss_shards_dir),
            "metadata": str(Path(args.faiss_shards_dir) / "manifest.json"),
            "status": "complete",
        }
    else:
        dense_state = HYBRID_V2.load_dense_state(Path(args.dense_index_dir), args.allow_partial_dense_index)
    faiss_state = HYBRID_V2.load_faiss_state(Path(args.faiss_index_dir)) if args.dense_backend == "faiss" else None
    if args.dense_backend == "faiss_shards":
        from theraskill_agent.indexing.embedding import require_compatible
        manifest = read_json(Path(args.faiss_shards_dir) / "manifest.json")
        config = HYBRID_V2.read_config(Path(args.model_config))["embedding"]
        if (manifest.get("embedding") or {}).get("fingerprint"):
            require_compatible(manifest["embedding"], config)
        elif config.get("name") != "bge-m3":
            raise ValueError("legacy BGE index is incompatible with this embedding model; rebuild the index")
    query_text = dense_query_text(plan, args.query)
    query_embedding, embedding_cache_trace = embedding_request_cached(
        Path(args.model_config),
        query_text,
        Path(args.embedding_cache_db) if getattr(args, "embedding_cache_db", None) else None,
    )
    pool_n = max(args.dense_pool_n, args.dense_top_n)
    dense_trace: dict[str, Any] = {
        "backend": args.dense_backend,
        "pool_n": pool_n,
        "dense_top_n": args.dense_top_n,
        "dense_query_text_sha256": sha256_text(query_text),
        "embedding_cache": embedding_cache_trace,
    }
    if args.dense_backend == "memmap" and allowed_ids is not None:
        row_indices, row_trace = fetch_dense_row_indices(Path(args.dense_row_map_db), allowed_ids, args.max_scoped_dense_ids)
        dense_trace.update(row_trace)
        if row_indices:
            raw_hits = dense_search_row_indices(dense_state, query_embedding, row_indices, pool_n, args.dense_chunk_size)
        else:
            raw_hits = []
    elif args.dense_backend == "faiss":
        dense_trace.update({"mode": "faiss_global_then_filter"})
        raw_hits, faiss_trace = cached_faiss_search(faiss_state or {}, dense_state, Path(args.dense_row_map_db), query_embedding, pool_n)
        dense_trace.update(faiss_trace)
    elif args.dense_backend == "faiss_shards":
        raw_hits, shard_trace = faiss_shard_search(Path(args.faiss_shards_dir), plan, query_embedding, pool_n)
        dense_trace.update(shard_trace)
    else:
        dense_trace.update({"mode": "memmap_global_then_filter"})
        raw_hits = HYBRID_V2.dense_search(dense_state, query_embedding, pool_n, args.dense_chunk_size)
    filtered_hits = raw_hits if allowed_ids is None else [hit for hit in raw_hits if str(hit.get("retrieval_id")) in allowed_ids]
    rows = HYBRID_V2.fetch_rows_by_retrieval_id(db, [str(hit.get("retrieval_id")) for hit in filtered_hits])
    candidates: list[dict[str, Any]] = []
    for hit in filtered_hits:
        rid = str(hit.get("retrieval_id"))
        row = rows.get(rid)
        if not row:
            continue
        item = dict(row)
        item.update({"dense_rank": hit["dense_rank"], "dense_score": hit["dense_score"]})
        candidates.append(item)
        if len(candidates) >= args.dense_top_n:
            break
    dense_trace.update({
        "raw_hits": len(raw_hits),
        "filtered_hits": len(filtered_hits),
        "returned_candidates": len(candidates),
    })
    return candidates, dense_state, faiss_state, dense_trace


def rrf_merge_channels(channels: dict[str, list[dict[str, Any]]], weights: dict[str, float], rrf_k: float) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for channel, items in channels.items():
        weight = float(weights.get(channel, 1.0))
        for rank, item in enumerate(items, start=1):
            rid = str(item.get("retrieval_id"))
            record = merged.setdefault(rid, {"item": dict(item), "channel_ranks": {}, "rrf_score": 0.0})
            record["item"].update({key: value for key, value in item.items() if value is not None})
            record["channel_ranks"][channel] = rank
            record["rrf_score"] += weight / (rrf_k + rank)
    fused: list[dict[str, Any]] = []
    for record in merged.values():
        item = dict(record["item"])
        item["_v3_hybrid_trace"] = {
            "channel_ranks": record["channel_ranks"],
            "rrf_score": round(float(record["rrf_score"]), 8),
        }
        fused.append(item)
    fused.sort(key=lambda row: row["_v3_hybrid_trace"]["rrf_score"], reverse=True)
    return fused


def planner_to_v2_parse(plan: dict[str, Any], skill: str) -> dict[str, Any]:
    risk = plan.get("risk") or {}
    risk_level = risk.get("level")
    severity = "ordinary_or_unspecified"
    if risk_level in {"high", "imminent"}:
        severity = "high_or_urgent"
    elif risk_level in {"moderate", "unknown"} and skill == "risk-triage":
        severity = "ambiguous_or_moderate"
    frameworks = {str(value).lower() for value in (plan.get("therapy_frameworks") or [])}
    task_type = str(plan.get("task_type") or "")
    skill_intent_name = str(plan.get("skill_intent", {}).get("name") or skill)
    rebt_skill = skill == "rebt-belief-work-support" or skill_intent_name == "rebt-belief-work-support"
    cognitive_skill = skill == "cognitive-reframing-support" or skill_intent_name == "cognitive-reframing-support"
    return {
        "schema_version": "theraskill_retrieval_query_parse_from_llm_plan_v1",
        "skill_intent": skill_intent_name,
        "language": plan.get("detected_language") or "unknown",
        "risk_cues": {
            "high_risk_language": skill == "risk-triage" and risk_level in {"moderate", "high", "imminent"},
            "means_available": risk_level in {"high", "imminent"},
            "immediacy": risk_level == "imminent",
            "severity_hint": severity,
            "matched_high_risk_terms": [],
            "matched_means_terms": [],
            "matched_immediacy_terms": [],
        },
        "framework_cues": {
            "rebt": rebt_skill or "rebt" in frameworks or "belief" in task_type,
            "cognitive_reframing": cognitive_skill or "cbt" in frameworks or "cognitive" in task_type,
            "matched_rebt_terms": list(plan.get("search_queries", {}).get("label_queries") or []),
            "matched_cbt_terms": list(plan.get("search_queries", {}).get("label_queries") or []),
        },
        "task_cues": {
            "synthetic_construction": task_type == "synthetic_construction",
            "response_review": task_type == "response_review",
            "matched_synthetic_terms": [],
            "matched_review_terms": [],
        },
    }


def build_items(fused: list[dict[str, Any]], plan: dict[str, Any], skill: str, top_k: int) -> list[dict[str, Any]]:
    parsed = planner_to_v2_parse(plan, skill)
    reranked: list[dict[str, Any]] = []
    label_cues = list(plan.get("search_queries", {}).get("label_queries") or [])
    for fused_rank, item in enumerate(fused, start=1):
        hybrid_trace = dict(item.pop("_v3_hybrid_trace", {}))
        score, policy_trace = V2.rerank_score(item, fused_rank, skill, parsed)
        role = V2.evidence_role(skill, item)
        item = dict(item)
        item.pop("bm25_score", None)
        item.pop("dense_rank", None)
        item.pop("dense_score", None)
        item["evidence_role"] = role
        item["allowed_use"] = V2.allowed_for_role(role)
        item["forbidden_use"] = V2.forbidden_for_role(role)
        item["query_match_cues"] = [f"llm_plan_label:{cue}" for cue in label_cues[:8]]
        item["severity_match"] = parsed["risk_cues"]["severity_hint"] if skill == "risk-triage" else "not_applicable"
        item["ranking_trace"] = {
            **hybrid_trace,
            "fused_rank": fused_rank,
            **policy_trace,
            "rerank_score": round(score, 6),
        }
        reranked.append(item)
    reranked.sort(key=lambda row: row["ranking_trace"]["rerank_score"], reverse=True)
    return V2.assign_and_select(reranked, top_k, skill)


def build_bundle(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    if not db.exists():
        raise FileNotFoundError(db)
    metadata_db = Path(args.metadata_v2_db)
    if not metadata_db.exists():
        metadata_db = db
    compact_label_db = Path(args.compact_label_db)
    plan_artifact = load_or_build_plan(args)
    plan = plan_artifact["plan"]
    scoped_plan, filter_trace = resolve_plan_scope(
        metadata_db,
        plan,
        args.min_candidate_scope,
        args.max_candidate_scope,
        Path(args.scope_count_cache_db) if getattr(args, "scope_count_cache_db", None) else None,
        args.skill,
    )
    scope_count = int(filter_trace.get("candidate_scope_count") or 0)
    ids = allowed_retrieval_ids(metadata_db, scoped_plan, args.max_sql_allowed_ids, scope_count)
    dense_ids = allowed_retrieval_ids(metadata_db, scoped_plan, args.max_scoped_dense_ids, scope_count)
    wide_scope = scope_count > args.max_wide_scope_channel_scan
    if wide_scope:
        bm25_candidates, bm25_trace = [], {
            "skipped": True,
            "reason": "candidate_scope_exceeds_max_wide_scope_channel_scan",
            "candidate_scope": scope_count,
            "max_wide_scope_channel_scan": args.max_wide_scope_channel_scan,
        }
        label_candidates, compact_label_trace = fetch_compact_label_candidates(
            compact_label_db,
            scoped_plan,
            args.query,
            args.label_top_n,
            scope_count,
            args.max_compact_label_scope,
        )
        filter_trace.setdefault("warnings", []).append(
            f"wide scope channel scan skipped for bm25/label: {scope_count} > {args.max_wide_scope_channel_scan}"
        )
    else:
        bm25_candidates, bm25_trace = fetch_bm25_candidates(db, scoped_plan, args.query, args.bm25_top_n, ids, args.max_sql_allowed_ids)
        label_candidates = fetch_label_candidates(db, scoped_plan, args.label_top_n, ids, args.max_sql_allowed_ids)
        compact_label_trace = {"skipped": True, "reason": "not_wide_scope"}
    required_family_candidates = fetch_required_family_candidates(
        db,
        scoped_plan,
        args.query,
        args.required_family_top_n,
        ids,
        args.max_sql_allowed_ids,
        skip_fts=wide_scope,
    )
    policy_family_candidates = fetch_policy_family_fallback(db, args.skill, scoped_plan, args.required_family_top_n) if wide_scope and not required_family_candidates else []
    dense_candidates, dense_state, faiss_state, dense_trace = fetch_dense_candidates(args, db, scoped_plan, dense_ids)
    overlay_path, overlay_trace = active_overlay_from_slot(Path(args.knowledge_release_slot))
    overlay_channels: dict[str, list[dict[str, Any]]] = {
        "overlay_sparse": [], "overlay_dense": [], "overlay_label": []
    }
    if overlay_path is not None:
        overlay_query_embedding, overlay_embedding_trace = embedding_request_cached(
            Path(args.model_config),
            dense_query_text(scoped_plan, args.query),
            Path(args.embedding_cache_db) if args.embedding_cache_db else None,
        )
        overlay_channels, overlay_query_trace = query_overlay(
            overlay_path,
            scoped_plan,
            args.query,
            overlay_query_embedding,
            limit=max(args.dense_top_n, args.label_top_n, args.top_k * 4),
        )
        overlay_trace.update(
            {"query": overlay_query_trace, "embedding": overlay_embedding_trace}
        )
    channels = {
        "bm25": bm25_candidates,
        "dense": dense_candidates,
        "label": label_candidates,
        "required_family": required_family_candidates,
        "policy_family_fallback": policy_family_candidates,
        **overlay_channels,
    }
    fused = rrf_merge_channels(
        channels,
        {
            "bm25": args.bm25_weight,
            "dense": args.dense_weight,
            "label": args.label_weight,
            "required_family": args.label_weight,
            "policy_family_fallback": 0.8,
            "overlay_sparse": args.bm25_weight,
            "overlay_dense": args.dense_weight,
            "overlay_label": args.label_weight,
        },
        args.rrf_k,
    )
    selected = build_items(fused, scoped_plan, args.skill, args.top_k)
    families = {item.get("dedup_family") for item in selected}
    routes = {item.get("default_route") for item in selected}
    labels = " ".join(str(item.get("canonical_label") or "") for item in selected[:1])
    risk_level = scoped_plan.get("risk", {}).get("level")
    high_query = args.skill == "risk-triage" and risk_level in {"high", "imminent"}
    return {
        "schema_version": "theraskill_runtime_evidence_bundle_v3_llm_structured_hybrid",
        "created_at": utc_now(),
        "skill": args.skill,
        "query": args.query,
        "top_k": args.top_k,
        "source_index": str(db),
        "metadata_v2_index": str(metadata_db),
        "llm_retrieval_plan": scoped_plan,
        "planner_trace": plan_artifact["planner_trace"],
        "structured_filter": filter_trace,
        "dense_index": dense_state,
        "faiss_index": faiss_state,
        "retrieval_config": {
            "version": "advanced_retrieval_v3_llm_structured_hybrid",
            "channels": ["sqlite_fts_bm25", f"dense_bge_m3_{args.dense_backend}", "controlled_label", "required_family_backfill", "promoted_overlay_sparse", "promoted_overlay_dense", "promoted_overlay_controlled_label"],
            "fusion": "rrf_v1",
            "reranker": "policy_aware_v3_with_llm_plan_features",
            "dense_backend": args.dense_backend,
            "rrf": {
                "k": args.rrf_k,
                "bm25_weight": args.bm25_weight,
                "dense_weight": args.dense_weight,
                "label_weight": args.label_weight,
            },
            "bm25_trace": bm25_trace,
            "compact_label_trace": compact_label_trace,
            "dense_trace": dense_trace,
            "promoted_overlay_trace": overlay_trace,
        },
        "channel_counts": {
            "candidate_scope": scope_count,
            "sql_allowed_ids_materialized": len(ids) if ids is not None else None,
            "dense_allowed_ids_materialized": len(dense_ids) if dense_ids is not None else None,
            "bm25_candidates": len(bm25_candidates),
            "dense_policy_filtered_candidates": len(dense_candidates),
            "label_candidates": len(label_candidates),
            "required_family_candidates": len(required_family_candidates),
            "policy_family_fallback_candidates": len(policy_family_candidates),
            "overlay_sparse_candidates": len(overlay_channels["overlay_sparse"]),
            "overlay_dense_candidates": len(overlay_channels["overlay_dense"]),
            "overlay_label_candidates": len(overlay_channels["overlay_label"]),
            "fused_candidates": len(fused),
        },
        "items": selected,
        "policy_checks": {
            "has_release_unit_ids": all(bool(item.get("release_unit_id")) for item in selected),
            "source_inventory_excluded": "source_inventory" not in families,
            "deferred_accounting_excluded": "accounting_only_requires_review" not in routes,
            "synthetic_construction_policy_ok": args.skill in {"synthetic-dialogue-builder", "therapy-response-reviewer"} or "synthetic_construction" not in families,
            "high_risk_query_top1_not_low_risk": (not high_query) or (not V2.contains_any(labels, V2.LOW_RISK_LABEL_TERMS)),
        },
        "quality_checks": {
            "method_rubric_in_top5_for_rebt": args.skill != "rebt-belief-work-support" or any(item.get("dedup_family") == "method_rubric" for item in selected[:5]),
            "clinical_risk_in_top3_for_high_risk": args.skill != "risk-triage" or (not high_query) or any(item.get("dedup_family") == "clinical_risk_assessment" for item in selected[:3]),
        },
    }


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    ensure_output_path(output, overwrite=args.overwrite)
    bundle = build_bundle(args)
    write_json(output, bundle, overwrite=args.overwrite)
    summary = {
        "output": str(output),
        "planner_status": bundle["planner_trace"]["planner_status"],
        "planner_fallback_used": bundle["planner_trace"]["fallback_used"],
        "skill": args.skill,
        "hits": len(bundle["items"]),
        "channel_counts": bundle["channel_counts"],
        "structured_filter": bundle["structured_filter"],
        "policy_checks": bundle["policy_checks"],
        "quality_checks": bundle["quality_checks"],
        "dense_backend": bundle["retrieval_config"]["dense_backend"],
    }
    print(json.dumps(bundle if args.json else summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
