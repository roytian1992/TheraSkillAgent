#!/usr/bin/env python3
"""Build per-family/per-route FAISS shards for filtered dense retrieval."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    import faiss
except ImportError as exc:  # pragma: no cover
    raise SystemExit("faiss is not available in this Python environment") from exc

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_dir, read_json, write_json
from retrieval_surface_registry import (
    DENSE_BGE_M3_INDEX_DIR,
    DENSE_BGE_M3_ROW_MAP_DB_PATH,
    FAISS_BGE_M3_SHARDS_DIR,
    FAISS_BGE_M3_SHARDS_QA_PATH,
    FAISS_BGE_M3_SHARDS_REPORT_PATH,
    METADATA_V2_DB_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DENSE_INDEX = DENSE_BGE_M3_INDEX_DIR
DEFAULT_METADATA_DB = METADATA_V2_DB_PATH
DEFAULT_ROW_MAP_DB = DENSE_BGE_M3_ROW_MAP_DB_PATH
DEFAULT_OUTPUT_DIR = FAISS_BGE_M3_SHARDS_DIR
DEFAULT_REPORT = FAISS_BGE_M3_SHARDS_REPORT_PATH
DEFAULT_QA = FAISS_BGE_M3_SHARDS_QA_PATH


DEFAULT_SHARDS = [
    ("clinical_risk_assessment", "clinical_reviewed_cluster_retrieval_with_label_guard"),
    ("style_response", "reviewed_cluster_retrieval"),
    ("qa_psychoeducation", "raw_passthrough_retrieval"),
    ("qa_psychoeducation", "reviewed_cluster_retrieval"),
    ("therapy_process", "raw_passthrough_retrieval"),
    ("therapy_process", "reviewed_cluster_retrieval"),
    ("method_rubric", "raw_passthrough_retrieval"),
    ("synthetic_construction", "synthetic_construction_method_retrieval"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-index-dir", default=str(DEFAULT_DENSE_INDEX))
    parser.add_argument("--metadata-db", default=str(DEFAULT_METADATA_DB))
    parser.add_argument("--row-map-db", default=str(DEFAULT_ROW_MAP_DB))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--batch-size", type=int, default=50000)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def load_dense_state(index_dir: Path) -> dict[str, Any]:
    metadata_path = index_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(metadata_path)
    metadata = read_json(metadata_path)
    counts = metadata.get("counts") or {}
    embedding = metadata.get("embedding") or {}
    dtype = str(embedding.get("dtype") or "float16")
    return {
        "index_dir": str(index_dir),
        "metadata": str(metadata_path),
        "vectors": str(index_dir / f"vectors.{dtype}.memmap"),
        "completed_count": int(counts.get("completed_count") or counts.get("target_eligible_rows") or 0),
        "total_count": int(counts.get("target_eligible_rows") or counts.get("completed_count") or 0),
        "dimension": int(embedding.get("dimension") or 0),
        "dtype": dtype,
        "status": str(metadata.get("status") or "unknown"),
    }


def normalize_rows(rows: np.ndarray) -> np.ndarray:
    arr = rows.astype(np.float32)
    norms = np.linalg.norm(arr, axis=1)
    norms[norms == 0] = 1.0
    return arr / norms[:, None]


def fetch_shard_rows(metadata_db: Path, row_map_db: Path, family: str, route: str) -> list[dict[str, Any]]:
    con = sqlite3.connect(str(metadata_db))
    row_con = sqlite3.connect(str(row_map_db))
    con.row_factory = sqlite3.Row
    row_con.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in con.execute(
                """
                select retrieval_id, release_unit_id, dedup_family, default_route,
                       primary_dataset, primary_knowledge_type, risk_scope
                from metadata_v2
                where retrieval_eligible=1 and embedding_eligible=1
                  and dedup_family=? and default_route=?
                order by retrieval_id
                """,
                (family, route),
            ).fetchall()
        ]
        if not rows:
            return []
        row_indices: dict[str, int] = {}
        ids = [str(row["retrieval_id"]) for row in rows]
        for start in range(0, len(ids), 800):
            chunk = ids[start:start + 800]
            sql = f"select retrieval_id, row_index from dense_row_map where retrieval_id in ({','.join('?' for _ in chunk)})"
            for item in row_con.execute(sql, chunk).fetchall():
                row_indices[str(item["retrieval_id"])] = int(item["row_index"])
        out: list[dict[str, Any]] = []
        for row in rows:
            rid = str(row["retrieval_id"])
            if rid not in row_indices:
                continue
            row["row_index"] = row_indices[rid]
            out.append(row)
        out.sort(key=lambda item: int(item["row_index"]))
        return out
    finally:
        con.close()
        row_con.close()


def write_id_map(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for local_index, row in enumerate(rows):
            handle.write(json.dumps({
                "local_index": local_index,
                "row_index": int(row["row_index"]),
                "retrieval_id": row["retrieval_id"],
                "release_unit_id": row.get("release_unit_id"),
                "dedup_family": row.get("dedup_family"),
                "default_route": row.get("default_route"),
                "primary_dataset": row.get("primary_dataset"),
                "primary_knowledge_type": row.get("primary_knowledge_type"),
                "risk_scope": row.get("risk_scope"),
            }, ensure_ascii=False, sort_keys=True) + "\n")


def build_shard(args: argparse.Namespace, vectors: np.memmap, dense_state: dict[str, Any], family: str, route: str) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    shard_name = f"{slug(family)}__{slug(route)}"
    shard_dir = output_dir / shard_name
    if shard_dir.exists():
        if not args.overwrite:
            raise FileExistsError(f"shard exists; pass --overwrite: {shard_dir}")
        for child in shard_dir.iterdir():
            child.unlink()
    shard_dir.mkdir(parents=True, exist_ok=True)
    rows = fetch_shard_rows(Path(args.metadata_db), Path(args.row_map_db), family, route)
    dim = int(dense_state["dimension"])
    index = faiss.IndexFlatIP(dim)
    started = time.time()
    for start in range(0, len(rows), args.batch_size):
        batch_rows = rows[start:start + args.batch_size]
        indices = np.asarray([int(row["row_index"]) for row in batch_rows], dtype=np.int64)
        index.add(normalize_rows(np.asarray(vectors[indices])))
    index_path = shard_dir / "index.flat_ip.faiss"
    ids_path = shard_dir / "ids.jsonl"
    metadata_path = shard_dir / "metadata.json"
    faiss.write_index(index, str(index_path))
    write_id_map(ids_path, rows)
    metadata = {
        "schema_version": "theraskill_faiss_dense_shard_metadata_v1",
        "created_at": utc_now(),
        "shard_name": shard_name,
        "dedup_family": family,
        "default_route": route,
        "index_type": "flat_ip",
        "metric": "inner_product_on_l2_normalized_vectors",
        "dimension": dim,
        "counts": {
            "metadata_rows": len(rows),
            "added_vectors": int(index.ntotal),
        },
        "artifacts": {
            "index": str(index_path),
            "ids": str(ids_path),
            "metadata": str(metadata_path),
        },
        "elapsed_seconds": round(time.time() - started, 3),
    }
    write_json(metadata_path, metadata)
    return metadata


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    ensure_output_dir(output_dir, overwrite=args.overwrite, allow_existing_empty=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    dense_state = load_dense_state(Path(args.dense_index_dir))
    vectors = np.memmap(dense_state["vectors"], dtype=np.dtype(dense_state["dtype"]), mode="r", shape=(dense_state["total_count"], dense_state["dimension"]))
    started = time.time()
    shards = []
    issues = []
    for family, route in DEFAULT_SHARDS:
        shard = build_shard(args, vectors, dense_state, family, route)
        shards.append(shard)
        if shard["counts"]["metadata_rows"] != shard["counts"]["added_vectors"]:
            issues.append({"issue": "shard_count_mismatch", "shard_name": shard["shard_name"], "counts": shard["counts"]})
        print(json.dumps({
            "phase": "shard_complete",
            "shard_name": shard["shard_name"],
            "rows": shard["counts"]["added_vectors"],
            "elapsed_seconds": shard["elapsed_seconds"],
        }, ensure_ascii=False), flush=True)
    manifest = {
        "schema_version": "theraskill_faiss_dense_shards_manifest_v1",
        "created_at": utc_now(),
        "output_dir": str(output_dir),
        "dense_index": dense_state,
        "metadata_db": str(Path(args.metadata_db)),
        "row_map_db": str(Path(args.row_map_db)),
        "shard_count": len(shards),
        "shards": shards,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    total_vectors = sum(int(shard["counts"]["added_vectors"]) for shard in shards)
    qa = {
        "schema_version": "theraskill_faiss_dense_shards_qa_v1",
        "created_at": utc_now(),
        "status": "complete" if not issues else "issues_found",
        "issues_count": len(issues),
        "issues": issues,
        "shard_count": len(shards),
        "total_vectors_across_shards": total_vectors,
        "manifest": str(manifest_path),
    }
    report = {
        "schema_version": "theraskill_faiss_dense_shards_report_v1",
        "created_at": manifest["created_at"],
        "status": qa["status"],
        "manifest": str(manifest_path),
        "shard_count": len(shards),
        "total_vectors_across_shards": total_vectors,
        "elapsed_seconds": manifest["elapsed_seconds"],
        "shards": [
            {
                "shard_name": shard["shard_name"],
                "dedup_family": shard["dedup_family"],
                "default_route": shard["default_route"],
                "vectors": shard["counts"]["added_vectors"],
                "elapsed_seconds": shard["elapsed_seconds"],
            }
            for shard in shards
        ],
    }
    write_json(Path(args.report), report)
    write_json(Path(args.qa_report), qa)
    print(json.dumps({
        "status": qa["status"],
        "issues_count": qa["issues_count"],
        "manifest": str(manifest_path),
        "report": str(Path(args.report)),
        "qa_report": str(Path(args.qa_report)),
        "shard_count": len(shards),
        "total_vectors_across_shards": total_vectors,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
