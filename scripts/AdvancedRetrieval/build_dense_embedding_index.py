#!/usr/bin/env python3
"""Build a resumable dense embedding memmap index for TheraSkill retrieval."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_resumable_output_path, iter_jsonl, read_yaml, write_json
from retrieval_surface_registry import (
    DENSE_BGE_M3_INDEX_DIR,
    DENSE_BGE_M3_QA_PATH,
    DENSE_BGE_M3_REPORT_PATH,
    RETRIEVAL_TEXT_VIEW_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEXT_VIEW = RETRIEVAL_TEXT_VIEW_PATH
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT_DIR = DENSE_BGE_M3_INDEX_DIR
DEFAULT_REPORT = DENSE_BGE_M3_REPORT_PATH
DEFAULT_QA = DENSE_BGE_M3_QA_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=str(DEFAULT_TEXT_VIEW))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--limit", type=int, default=0, help="Build only the first N eligible rows for debug/probe runs. 0 means all eligible rows.")
    parser.add_argument("--batch-size", type=int, default=0, help="Override embedding.batch_size from config.")
    parser.add_argument("--dtype", choices=["float16", "float32"], default="float16")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--count-progress-every", type=int, default=250000)
    parser.add_argument(
        "--probe-query",
        "--smoke-query",
        dest="probe_query",
        default="我不想活了 手边有药 危机回应",
        help="Validation probe query. `--smoke-query` is kept as a legacy alias.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def eligible_row(row: dict[str, Any]) -> bool:
    return bool(row.get("embedding_eligible")) and row.get("dedup_family") != "source_inventory" and row.get("release_unit_type") != "deferred_accounting"


def count_eligible(path: Path, limit: int, progress_every: int) -> tuple[int, int]:
    scanned = 0
    eligible = 0
    start = time.time()
    for _, row in iter_jsonl(path, with_line_numbers=True):
        scanned += 1
        if not eligible_row(row):
            if progress_every and scanned % progress_every == 0:
                print(json.dumps({
                    "phase": "count_eligible",
                    "scanned_rows": scanned,
                    "eligible_rows": eligible,
                    "elapsed_seconds": round(time.time() - start, 2),
                }, ensure_ascii=False), flush=True)
            continue
        eligible += 1
        if progress_every and scanned % progress_every == 0:
            print(json.dumps({
                "phase": "count_eligible",
                "scanned_rows": scanned,
                "eligible_rows": eligible,
                "elapsed_seconds": round(time.time() - start, 2),
            }, ensure_ascii=False), flush=True)
        if limit and eligible >= limit:
            break
    return scanned, eligible


def embedding_request(endpoint: str, api_key: str, model: str, texts: list[str], timeout: int, retry_attempts: int) -> list[list[float]]:
    payload = json.dumps({"model": model, "input": texts}, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retry_attempts + 1):
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            data = body.get("data") or []
            embeddings = [item["embedding"] for item in sorted(data, key=lambda item: item.get("index", 0))]
            if len(embeddings) != len(texts):
                raise RuntimeError(f"embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
            return embeddings
        except Exception as exc:
            last_error = exc
            if attempt < retry_attempts:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"embedding request failed after retries: {last_error!r}")


def open_outputs(output_dir: Path, total: int, dim: int, dtype: str, overwrite: bool, resume: bool):
    vectors_path = output_dir / f"vectors.{dtype}.memmap"
    ids_path = output_dir / "ids.jsonl"
    progress_path = output_dir / "progress.json"
    if overwrite and not resume:
        for path in (vectors_path, ids_path, progress_path):
            if path.exists():
                path.unlink()
    ensure_resumable_output_path(vectors_path, overwrite=overwrite, resume=resume)
    mode = "r+" if vectors_path.exists() else "w+"
    vectors = np.memmap(vectors_path, dtype=np.dtype(dtype), mode=mode, shape=(total, dim))
    completed = 0
    if resume and progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        completed = int(progress.get("completed_count") or 0)
    elif ids_path.exists() and resume:
        with ids_path.open("r", encoding="utf-8") as handle:
            completed = sum(1 for line in handle if line.strip())
    ids_handle = ids_path.open("a" if resume and completed else "w", encoding="utf-8")
    return vectors, vectors_path, ids_handle, ids_path, progress_path, completed


def vector_norm_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors.astype(np.float32), axis=1)
    norms[norms == 0] = 1.0
    return vectors.astype(np.float32) / norms[:, None]


def run_validation_probe_search(vectors_path: Path, ids_path: Path, dtype: str, dim: int, total: int, query_embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
    vectors = np.memmap(vectors_path, dtype=np.dtype(dtype), mode="r", shape=(total, dim))
    query = np.asarray(query_embedding, dtype=np.float32)
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    scores: list[tuple[float, int]] = []
    chunk_size = 50000
    for start in range(0, total, chunk_size):
        end = min(total, start + chunk_size)
        chunk = vector_norm_rows(np.asarray(vectors[start:end]))
        sims = chunk @ query
        for offset, score in enumerate(sims):
            scores.append((float(score), start + offset))
    scores.sort(key=lambda pair: pair[0], reverse=True)
    wanted = {idx for _, idx in scores[:top_k]}
    id_rows: dict[int, dict[str, Any]] = {}
    with ids_path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if idx in wanted:
                id_rows[idx] = json.loads(line)
    return [{"rank": rank + 1, "score": round(score, 6), **id_rows.get(idx, {"row_index": idx})} for rank, (score, idx) in enumerate(scores[:top_k])]


def main() -> int:
    args = parse_args()
    text_view = Path(args.text_view)
    output_dir = Path(args.output_dir)
    report_path = Path(args.report)
    qa_path = Path(args.qa_report)
    metadata_path = output_dir / "metadata.json"
    if not text_view.exists():
        raise FileNotFoundError(text_view)
    for path in (report_path, qa_path):
        ensure_resumable_output_path(path, overwrite=args.overwrite, resume=args.resume)

    config = read_yaml(Path(args.model_config))
    emb_cfg = config["embedding"]
    endpoint = emb_cfg["endpoint"]
    api_key_env = str(emb_cfg.get("api_key_env") or "").strip()
    api_key = str(emb_cfg.get("api_key") or (os.environ.get(api_key_env, "") if api_key_env else ""))
    model = emb_cfg["name"]
    batch_size = args.batch_size or int(emb_cfg.get("batch_size") or 32)
    timeout = int(emb_cfg.get("timeout_seconds") or 120)
    retry_attempts = int(emb_cfg.get("retry_attempts") or 2)

    probe = embedding_request(endpoint, api_key, model, ["TheraSkill embedding dimension probe"], timeout, retry_attempts)
    dim = len(probe[0])
    scanned_rows, total = count_eligible(text_view, args.limit, args.count_progress_every)
    if total <= 0:
        raise RuntimeError("no eligible rows selected for embedding")
    output_dir.mkdir(parents=True, exist_ok=True)
    vectors, vectors_path, ids_handle, ids_path, progress_path, completed = open_outputs(output_dir, total, dim, args.dtype, args.overwrite, args.resume)

    created_at = utc_now()
    start_time = time.time()
    batch_texts: list[str] = []
    batch_rows: list[dict[str, Any]] = []
    written = completed
    eligible_seen = 0
    skipped_for_resume = 0
    try:
        for source_line_no, row in iter_jsonl(text_view, with_line_numbers=True):
            if not eligible_row(row):
                continue
            eligible_seen += 1
            if args.limit and eligible_seen > args.limit:
                break
            if eligible_seen <= completed:
                skipped_for_resume += 1
                continue
            batch_texts.append(str(row.get("retrieval_text") or ""))
            batch_rows.append({
                "row_index": written + len(batch_rows),
                "source_line_no": source_line_no,
                "retrieval_id": row.get("retrieval_id"),
                "release_unit_id": row.get("release_unit_id"),
                "dedup_family": row.get("dedup_family"),
                "canonical_label": row.get("canonical_label"),
                "primary_dataset": row.get("primary_dataset"),
                "primary_knowledge_type": row.get("primary_knowledge_type"),
                "risk_scope": row.get("risk_scope"),
                "retrieval_text_sha256": row.get("retrieval_text_sha256"),
            })
            if len(batch_texts) >= batch_size:
                embeddings = embedding_request(endpoint, api_key, model, batch_texts, timeout, retry_attempts)
                arr = np.asarray(embeddings, dtype=np.float32)
                vectors[written:written + len(batch_rows), :] = arr.astype(np.dtype(args.dtype))
                for meta in batch_rows:
                    ids_handle.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
                written += len(batch_rows)
                vectors.flush()
                ids_handle.flush()
                write_json(progress_path, {
                    "schema_version": "theraskill_dense_embedding_index_progress_v1",
                    "updated_at": utc_now(),
                    "completed_count": written,
                    "total_count": total,
                    "dimension": dim,
                    "dtype": args.dtype,
                })
                if args.progress_every and written % args.progress_every < batch_size:
                    print(json.dumps({"completed": written, "total": total, "elapsed_seconds": round(time.time() - start_time, 2)}, ensure_ascii=False))
                batch_texts = []
                batch_rows = []
        if batch_texts:
            embeddings = embedding_request(endpoint, api_key, model, batch_texts, timeout, retry_attempts)
            arr = np.asarray(embeddings, dtype=np.float32)
            vectors[written:written + len(batch_rows), :] = arr.astype(np.dtype(args.dtype))
            for meta in batch_rows:
                ids_handle.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
            written += len(batch_rows)
            vectors.flush()
            ids_handle.flush()
    finally:
        ids_handle.close()
        vectors.flush()

    query_embedding = embedding_request(endpoint, api_key, model, [args.probe_query], timeout, retry_attempts)[0]
    validation_probe_hits = run_validation_probe_search(vectors_path, ids_path, args.dtype, dim, written, query_embedding, top_k=5)
    elapsed = time.time() - start_time
    report = {
        "schema_version": "theraskill_dense_bge_m3_index_report_v1",
        "created_at": created_at,
        "completed_at": utc_now(),
        "status": "complete" if written == total else "partial",
        "text_view": str(text_view),
        "output_dir": str(output_dir),
        "vectors": str(vectors_path),
        "ids": str(ids_path),
        "metadata": str(metadata_path),
        "progress": str(progress_path),
        "model_config": str(Path(args.model_config)),
        "embedding": {
            "model": model,
            "endpoint": endpoint,
            "dimension": dim,
            "dtype": args.dtype,
            "batch_size": batch_size,
        },
        "counts": {
            "scanned_rows_for_selection": scanned_rows,
            "target_eligible_rows": total,
            "completed_count": written,
            "skipped_for_resume": skipped_for_resume,
        },
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rows_per_second": round(written / max(elapsed, 1e-9), 3),
        "validation_probe_query": args.probe_query,
        "validation_probe_hits": validation_probe_hits,
        "scope_note": "Dense embedding memmap index. FAISS/ANN is not built by this script yet.",
    }
    metadata = {
        "schema_version": "theraskill_dense_embedding_index_metadata_v1",
        "created_at": report["created_at"],
        "completed_at": report["completed_at"],
        "status": report["status"],
        "text_view": report["text_view"],
        "model_config": report["model_config"],
        "embedding": report["embedding"],
        "counts": report["counts"],
        "artifacts": {
            "vectors": str(vectors_path),
            "ids": str(ids_path),
            "progress": str(progress_path),
            "report": str(report_path),
            "qa_report": str(qa_path),
        },
        "scope_note": report["scope_note"],
    }
    issues: list[dict[str, Any]] = []
    if written != total:
        issues.append({"issue": "incomplete_embedding_count", "completed": written, "target": total})
    if dim <= 0:
        issues.append({"issue": "invalid_embedding_dimension", "dimension": dim})
    if not validation_probe_hits:
        issues.append({"issue": "empty_validation_probe_hits"})
    if any(hit.get("dedup_family") == "source_inventory" for hit in validation_probe_hits):
        issues.append({"issue": "source_inventory_in_validation_probe_hits"})
    qa = {
        "schema_version": "theraskill_dense_bge_m3_index_qa_v1",
        "created_at": report["created_at"],
        "status": "complete" if not issues else "failed",
        "issues_count": len(issues),
        "issues": issues,
        "counts": report["counts"],
        "embedding_dimension": dim,
        "dtype": args.dtype,
        "validation_probe_hit_count": len(validation_probe_hits),
        "scope_note": report["scope_note"],
    }
    write_json(metadata_path, metadata)
    write_json(report_path, report)
    write_json(qa_path, qa)
    print(json.dumps({
        "status": qa["status"],
        "issues_count": qa["issues_count"],
        "completed_count": written,
        "target_eligible_rows": total,
        "dimension": dim,
        "vectors": str(vectors_path),
        "ids": str(ids_path),
        "metadata": str(metadata_path),
        "report": str(report_path),
        "qa": str(qa_path),
        "elapsed_seconds": report["elapsed_seconds"],
        "throughput_rows_per_second": report["throughput_rows_per_second"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
