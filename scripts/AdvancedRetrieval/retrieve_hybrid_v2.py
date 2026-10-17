#!/usr/bin/env python3
"""Hybrid BM25+dense retrieval v2 for TheraSkill.

This is the first RRF-capable hybrid retriever. It uses SQLite FTS candidates
plus a bge-m3 dense channel, then reuses the deterministic v2 policy reranker
and evidence bundle packing. Dense retrieval supports a memmap brute-force
backend and an optional FAISS backend when faiss is available.
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import ensure_output_path, read_json, write_json
from retrieval_module_registry import PROJECT_ROOT, load_advanced_v2
from retrieval_surface_registry import (
    DENSE_BGE_M3_INDEX_DIR,
    FAISS_BGE_M3_INDEX_DIR,
    HYBRID_RETRIEVAL_V2_BUNDLE_PATH,
    PHASE6_SQLITE_DB_PATH,
)
from domains.theraskill.provider_config import resolve_embedding_config


DEFAULT_DB = PHASE6_SQLITE_DB_PATH
DEFAULT_DENSE_INDEX = DENSE_BGE_M3_INDEX_DIR
DEFAULT_FAISS_INDEX = FAISS_BGE_M3_INDEX_DIR
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT = HYBRID_RETRIEVAL_V2_BUNDLE_PATH


V2 = load_advanced_v2()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--dense-index-dir", default=str(DEFAULT_DENSE_INDEX))
    parser.add_argument("--faiss-index-dir", default=str(DEFAULT_FAISS_INDEX))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--skill", required=True, choices=sorted(V2.SKILL_POLICIES))
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--candidate-multiplier", type=int, default=30)
    parser.add_argument("--dense-top-n", type=int, default=80)
    parser.add_argument("--dense-pool-n", type=int, default=400)
    parser.add_argument("--dense-chunk-size", type=int, default=50000)
    parser.add_argument("--dense-backend", choices=["memmap", "faiss"], default="memmap")
    parser.add_argument("--rrf-k", type=float, default=60.0)
    parser.add_argument("--bm25-weight", type=float, default=1.0)
    parser.add_argument("--dense-weight", type=float, default=1.0)
    parser.add_argument("--allow-partial-dense-index", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    embedding = config.get("embedding") or {}
    config["embedding"] = resolve_embedding_config(embedding)
    return config


def embedding_request(config_path: Path, text: str) -> list[float]:
    from domains.theraskill.theraskill_llm import OpenAICompatibleEmbeddingClient
    return OpenAICompatibleEmbeddingClient(read_config(config_path)["embedding"]).embed([text])[0]


def load_dense_state(index_dir: Path, allow_partial: bool) -> dict[str, Any]:
    metadata_path = index_dir / "metadata.json"
    progress_path = index_dir / "progress.json"
    ids_path = index_dir / "ids.jsonl"
    if metadata_path.exists():
        metadata = read_json(metadata_path)
        counts = metadata.get("counts") or {}
        embedding = metadata.get("embedding") or {}
        completed = int(counts.get("completed_count") or counts.get("target_eligible_rows") or 0)
        total = int(counts.get("target_eligible_rows") or completed)
        dim = int(embedding.get("dimension") or 0)
        dtype = str(embedding.get("dtype") or "float16")
        status = str(metadata.get("status") or "unknown")
    elif progress_path.exists() and allow_partial:
        progress = read_json(progress_path)
        completed = int(progress.get("completed_count") or 0)
        total = int(progress.get("total_count") or completed)
        dim = int(progress.get("dimension") or 0)
        dtype = str(progress.get("dtype") or "float16")
        status = "partial"
    else:
        raise FileNotFoundError(f"dense metadata not found, or partial use not allowed: {metadata_path}")
    vectors_path = index_dir / f"vectors.{dtype}.memmap"
    if not vectors_path.exists():
        raise FileNotFoundError(vectors_path)
    if not ids_path.exists():
        raise FileNotFoundError(ids_path)
    if completed <= 0 or dim <= 0:
        raise RuntimeError(f"invalid dense state: completed={completed}, dim={dim}")
    return {
        "index_dir": str(index_dir),
        "metadata": str(metadata_path) if metadata_path.exists() else None,
        "progress": str(progress_path) if progress_path.exists() else None,
        "vectors": str(vectors_path),
        "ids": str(ids_path),
        "completed_count": completed,
        "total_count": total,
        "dimension": dim,
        "dtype": dtype,
        "status": status,
    }


def load_faiss_state(index_dir: Path) -> dict[str, Any]:
    metadata_path = index_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(metadata_path)
    metadata = read_json(metadata_path)
    artifacts = metadata.get("artifacts") or {}
    index_path = Path(artifacts.get("index") or index_dir / f"index.{metadata.get('index_type', 'flat_ip')}.faiss")
    if not index_path.exists():
        raise FileNotFoundError(index_path)
    counts = metadata.get("counts") or {}
    dense_state = metadata.get("dense_index") or {}
    return {
        "index_dir": str(index_dir),
        "metadata": str(metadata_path),
        "index": str(index_path),
        "index_type": str(metadata.get("index_type") or "unknown"),
        "metric": str(metadata.get("metric") or "unknown"),
        "ann_parameters": metadata.get("ann_parameters") or {},
        "dimension": int(metadata.get("dimension") or dense_state.get("dimension") or 0),
        "count": int(counts.get("added_vectors") or 0),
        "dense_index": dense_state,
        "status": str(metadata.get("status") or "unknown"),
    }


def vector_norm_rows(vectors: np.ndarray) -> np.ndarray:
    arr = vectors.astype(np.float32)
    norms = np.linalg.norm(arr, axis=1)
    norms[norms == 0] = 1.0
    return arr / norms[:, None]


def dense_search(index_state: dict[str, Any], query_embedding: list[float], pool_n: int, chunk_size: int) -> list[dict[str, Any]]:
    total = int(index_state["completed_count"])
    dim = int(index_state["dimension"])
    dtype = np.dtype(str(index_state["dtype"]))
    vectors = np.memmap(index_state["vectors"], dtype=dtype, mode="r", shape=(int(index_state["total_count"]), dim))
    query = np.asarray(query_embedding, dtype=np.float32)
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    heap: list[tuple[float, int]] = []
    for start in range(0, total, chunk_size):
        end = min(total, start + chunk_size)
        sims = vector_norm_rows(np.asarray(vectors[start:end])) @ query
        local_n = min(pool_n, len(sims))
        if local_n <= 0:
            continue
        local_idx = np.argpartition(sims, -local_n)[-local_n:]
        for offset in local_idx:
            score = float(sims[offset])
            row_index = start + int(offset)
            if len(heap) < pool_n:
                heapq.heappush(heap, (score, row_index))
            elif score > heap[0][0]:
                heapq.heapreplace(heap, (score, row_index))
    top = sorted(heap, key=lambda pair: pair[0], reverse=True)
    wanted = {idx for _, idx in top}
    id_rows: dict[int, dict[str, Any]] = {}
    with Path(index_state["ids"]).open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if idx in wanted:
                id_rows[idx] = json.loads(line)
            if idx > max(wanted, default=-1):
                break
    hits: list[dict[str, Any]] = []
    for rank, (score, idx) in enumerate(top, start=1):
        meta = dict(id_rows.get(idx) or {"row_index": idx})
        meta["dense_rank"] = rank
        meta["dense_score"] = round(score, 6)
        hits.append(meta)
    return hits


def faiss_search(faiss_state: dict[str, Any], dense_state: dict[str, Any], query_embedding: list[float], pool_n: int) -> list[dict[str, Any]]:
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("faiss backend requested but faiss is not available in this Python environment") from exc
    if int(faiss_state["dimension"]) != int(dense_state["dimension"]):
        raise RuntimeError(f"FAISS/dense dimension mismatch: {faiss_state['dimension']} vs {dense_state['dimension']}")
    if int(faiss_state["count"]) > int(dense_state["completed_count"]):
        raise RuntimeError(f"FAISS count exceeds dense completed rows: {faiss_state['count']} > {dense_state['completed_count']}")
    index = faiss.read_index(str(faiss_state["index"]))
    ann = faiss_state.get("ann_parameters") or {}
    if hasattr(index, "nprobe") and ann.get("nprobe"):
        index.nprobe = int(ann["nprobe"])
    query = np.asarray([query_embedding], dtype=np.float32)
    query = vector_norm_rows(query)
    k = min(pool_n, int(index.ntotal))
    if k <= 0:
        return []
    distances, indices = index.search(query, k)
    wanted = {int(idx) for idx in indices[0].tolist() if int(idx) >= 0}
    id_rows: dict[int, dict[str, Any]] = {}
    with Path(dense_state["ids"]).open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if idx in wanted:
                id_rows[idx] = json.loads(line)
            if idx > max(wanted, default=-1):
                break
    hits: list[dict[str, Any]] = []
    for rank, (idx, score) in enumerate(zip(indices[0].tolist(), distances[0].tolist()), start=1):
        idx = int(idx)
        if idx < 0:
            continue
        meta = dict(id_rows.get(idx) or {"row_index": idx})
        meta["dense_rank"] = rank
        meta["dense_score"] = round(float(score), 6)
        hits.append(meta)
    return hits


def fetch_rows_by_retrieval_id(db: Path, retrieval_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not retrieval_ids:
        return {}
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows_by_id: dict[str, dict[str, Any]] = {}
    try:
        for start in range(0, len(retrieval_ids), 800):
            chunk = retrieval_ids[start:start + 800]
            sql = f"""
                select
                  retrieval_id,
                  release_unit_id,
                  release_unit_type,
                  dedup_family,
                  canonical_knowledge_type,
                  canonical_label,
                  primary_dataset,
                  primary_knowledge_type,
                  primary_evidence_tier,
                  risk_scope,
                  default_route,
                  member_count,
                  retrieval_text_chars,
                  '' as text_preview
                from retrieval_meta m
                where retrieval_id in ({V2.placeholders(chunk)})
            """
            for row in con.execute(sql, chunk).fetchall():
                rows_by_id[str(row["retrieval_id"])] = dict(row)
    finally:
        con.close()
    return rows_by_id


def policy_allows(item: dict[str, Any], skill: str) -> bool:
    policy = V2.SKILL_POLICIES[skill]
    family = item.get("dedup_family")
    route = item.get("default_route")
    if family not in set(policy.get("families") or []):
        return False
    if family in set(policy.get("blocked_families") or []):
        return False
    if route in set(policy.get("blocked_routes") or []):
        return False
    risk_scopes = set(policy.get("risk_scopes") or [])
    if risk_scopes and item.get("risk_scope") not in risk_scopes and item.get("risk_scope") is not None:
        return False
    return True


def sparse_candidates(db: Path, skill: str, query: str, parsed: dict[str, Any], top_k: int, candidate_multiplier: int) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    candidates, resolved_policy = V2.fetch_candidates(db, skill, query, parsed, top_k, candidate_multiplier)
    seen = {str(item.get("retrieval_id")) for item in candidates}
    backfill_counts: dict[str, int] = {}
    for family, terms in V2.FAMILY_BACKFILL_EXPANSIONS.get(skill, {}).items():
        existing = sum(1 for item in candidates if item.get("dedup_family") == family)
        target = int(V2.SKILL_POLICIES[skill].get("quota", {}).get(family, 0))
        if existing >= target:
            continue
        backfill = V2.fetch_family_backfill(db, skill, family, terms, max(target * 4, 12))
        added = 0
        for item in backfill:
            rid = str(item.get("retrieval_id"))
            if rid in seen:
                continue
            candidates.append(item)
            seen.add(rid)
            added += 1
        if added:
            backfill_counts[family] = added
    return candidates, resolved_policy, backfill_counts


def rrf_merge(sparse: list[dict[str, Any]], dense: list[dict[str, Any]], rrf_k: float, bm25_weight: float, dense_weight: float) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for rank, item in enumerate(sparse, start=1):
        rid = str(item.get("retrieval_id"))
        record = merged.setdefault(rid, {"item": dict(item), "sparse_rank": None, "dense_rank": None, "dense_score": None, "rrf_score": 0.0})
        record["item"].update({key: value for key, value in item.items() if value is not None})
        record["sparse_rank"] = rank
        record["rrf_score"] += bm25_weight / (rrf_k + rank)
    for hit in dense:
        rid = str(hit.get("retrieval_id"))
        record = merged.setdefault(rid, {"item": dict(hit), "sparse_rank": None, "dense_rank": None, "dense_score": None, "rrf_score": 0.0})
        record["item"].update({key: value for key, value in hit.items() if value is not None})
        rank = int(hit.get("dense_rank") or 10**9)
        record["dense_rank"] = rank
        record["dense_score"] = hit.get("dense_score")
        record["rrf_score"] += dense_weight / (rrf_k + rank)
    fused: list[dict[str, Any]] = []
    for record in merged.values():
        item = dict(record["item"])
        item["_hybrid_trace"] = {
            "sparse_rank": record["sparse_rank"],
            "dense_rank": record["dense_rank"],
            "dense_score": record["dense_score"],
            "rrf_score": round(float(record["rrf_score"]), 8),
        }
        fused.append(item)
    fused.sort(key=lambda row: row["_hybrid_trace"]["rrf_score"], reverse=True)
    return fused


def build_bundle(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    dense_index_dir = Path(args.dense_index_dir)
    faiss_index_dir = Path(args.faiss_index_dir)
    if not db.exists():
        raise FileNotFoundError(db)
    parsed = V2.parse_query(args.query, args.skill)
    sparse, resolved_policy, backfill_counts = sparse_candidates(db, args.skill, args.query, parsed, args.top_k, args.candidate_multiplier)
    dense_state = load_dense_state(dense_index_dir, args.allow_partial_dense_index)
    faiss_state = load_faiss_state(faiss_index_dir) if args.dense_backend == "faiss" else None
    query_embedding = embedding_request(Path(args.model_config), args.query)
    if args.dense_backend == "faiss":
        dense_raw_hits = faiss_search(faiss_state or {}, dense_state, query_embedding, max(args.dense_pool_n, args.dense_top_n))
    else:
        dense_raw_hits = dense_search(dense_state, query_embedding, max(args.dense_pool_n, args.dense_top_n), args.dense_chunk_size)
    dense_rows = fetch_rows_by_retrieval_id(db, [str(hit.get("retrieval_id")) for hit in dense_raw_hits if hit.get("retrieval_id")])
    dense_candidates: list[dict[str, Any]] = []
    dense_meta_by_id = {str(hit.get("retrieval_id")): hit for hit in dense_raw_hits}
    for hit in dense_raw_hits:
        rid = str(hit.get("retrieval_id"))
        row = dense_rows.get(rid)
        if not row:
            continue
        item = dict(row)
        item.update({"dense_rank": hit["dense_rank"], "dense_score": hit["dense_score"]})
        if policy_allows(item, args.skill):
            dense_candidates.append(item)
        if len(dense_candidates) >= args.dense_top_n:
            break

    fused = rrf_merge(sparse, dense_candidates, args.rrf_k, args.bm25_weight, args.dense_weight)
    reranked: list[dict[str, Any]] = []
    for fused_rank, item in enumerate(fused, start=1):
        hybrid_trace = dict(item.pop("_hybrid_trace", {}))
        score, policy_trace = V2.rerank_score(item, fused_rank, args.skill, parsed)
        role = V2.evidence_role(args.skill, item)
        item = dict(item)
        item.pop("bm25_score", None)
        item.pop("dense_rank", None)
        item.pop("dense_score", None)
        item["evidence_role"] = role
        item["allowed_use"] = V2.allowed_for_role(role)
        item["forbidden_use"] = V2.forbidden_for_role(role)
        item["query_match_cues"] = sorted(set(
            parsed["risk_cues"].get("matched_high_risk_terms", [])
            + parsed["risk_cues"].get("matched_means_terms", [])
            + parsed["risk_cues"].get("matched_immediacy_terms", [])
            + parsed["framework_cues"].get("matched_rebt_terms", [])
            + parsed["framework_cues"].get("matched_cbt_terms", [])
            + parsed["task_cues"].get("matched_synthetic_terms", [])
            + parsed["task_cues"].get("matched_review_terms", [])
        ))
        item["severity_match"] = parsed["risk_cues"]["severity_hint"] if args.skill == "risk-triage" else "not_applicable"
        item["ranking_trace"] = {
            **hybrid_trace,
            "fused_rank": fused_rank,
            **policy_trace,
            "rerank_score": round(score, 6),
        }
        reranked.append(item)
    reranked.sort(key=lambda row: row["ranking_trace"]["rerank_score"], reverse=True)
    selected = V2.assign_and_select(reranked, args.top_k, args.skill)
    families = {item.get("dedup_family") for item in selected}
    routes = {item.get("default_route") for item in selected}
    top1_labels = " ".join(str(item.get("canonical_label") or "") for item in selected[:1])
    high_query = parsed["risk_cues"]["severity_hint"] == "high_or_urgent"
    return {
        "schema_version": "theraskill_runtime_evidence_bundle_v2_hybrid_rrf",
        "created_at": utc_now(),
        "skill": args.skill,
        "query": args.query,
        "top_k": args.top_k,
        "source_index": str(db),
        "dense_index": dense_state,
        "faiss_index": faiss_state,
        "parsed_query": parsed,
        "retrieval_config": {
            "version": "advanced_retrieval_v2_hybrid_rrf",
            "channels": ["sqlite_fts_bm25", f"dense_bge_m3_{args.dense_backend}"],
            "fusion": "rrf_v1",
            "reranker": "policy_aware_deterministic_v1",
            "rrf": {
                "k": args.rrf_k,
                "bm25_weight": args.bm25_weight,
                "dense_weight": args.dense_weight,
            },
            "dense_backend": args.dense_backend,
            "dense_top_n": args.dense_top_n,
            "dense_pool_n": args.dense_pool_n,
            "family_quota": V2.SKILL_POLICIES[args.skill].get("quota"),
            "family_backfill_counts": backfill_counts,
        },
        "retrieval_policy": resolved_policy,
        "candidate_count": len(fused),
        "channel_counts": {
            "sparse_candidates": len(sparse),
            "dense_raw_hits": len(dense_raw_hits),
            "dense_policy_filtered_candidates": len(dense_candidates),
            "fused_candidates": len(fused),
        },
        "items": selected,
        "policy_checks": {
            "has_release_unit_ids": all(bool(item.get("release_unit_id")) for item in selected),
            "source_inventory_excluded": "source_inventory" not in families,
            "deferred_accounting_excluded": "accounting_only_requires_review" not in routes,
            "synthetic_construction_policy_ok": args.skill in {"synthetic-dialogue-builder", "therapy-response-reviewer"} or "synthetic_construction" not in families,
            "high_risk_query_top1_not_low_risk": (not high_query) or (not V2.contains_any(top1_labels, V2.LOW_RISK_LABEL_TERMS)),
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
        "skill": args.skill,
        "hits": len(bundle["items"]),
        "channel_counts": bundle["channel_counts"],
        "policy_checks": bundle["policy_checks"],
        "quality_checks": bundle["quality_checks"],
        "dense_index_completed_count": bundle["dense_index"]["completed_count"],
        "dense_index_status": bundle["dense_index"]["status"],
        "dense_backend": bundle["retrieval_config"]["dense_backend"],
        "faiss_index_status": (bundle.get("faiss_index") or {}).get("status"),
    }
    print(json.dumps(bundle if args.json else summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
