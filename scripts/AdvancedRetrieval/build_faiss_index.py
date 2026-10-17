#!/usr/bin/env python3
"""Build a FAISS ANN/search index from a TheraSkill dense memmap index."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    import faiss
except ImportError as exc:  # pragma: no cover - environment dependent
    raise SystemExit("faiss is not available in this Python environment") from exc

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_json, write_json
from retrieval_surface_registry import (
    DENSE_BGE_M3_INDEX_DIR,
    FAISS_BGE_M3_INDEX_DIR,
    FAISS_BGE_M3_QA_PATH,
    FAISS_BGE_M3_REPORT_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DENSE_INDEX = DENSE_BGE_M3_INDEX_DIR
DEFAULT_OUTPUT_DIR = FAISS_BGE_M3_INDEX_DIR
DEFAULT_REPORT = FAISS_BGE_M3_REPORT_PATH
DEFAULT_QA = FAISS_BGE_M3_QA_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-index-dir", default=str(DEFAULT_DENSE_INDEX))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--index-type", choices=["flat_ip", "ivf_flat_ip"], default="flat_ip")
    parser.add_argument("--nlist", type=int, default=4096)
    parser.add_argument("--nprobe", type=int, default=32)
    parser.add_argument("--train-size", type=int, default=200000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=50000)
    parser.add_argument("--allow-partial-dense-index", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        raise FileNotFoundError(f"dense metadata missing, or partial dense use not allowed: {metadata_path}")
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


def normalize_rows(rows: np.ndarray) -> np.ndarray:
    arr = rows.astype(np.float32)
    norms = np.linalg.norm(arr, axis=1)
    norms[norms == 0] = 1.0
    return arr / norms[:, None]


def count_id_rows(ids_path: Path, limit: int) -> int:
    count = 0
    with ids_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
                if limit and count >= limit:
                    break
    return count


def main() -> int:
    args = parse_args()
    dense_dir = Path(args.dense_index_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / f"index.{args.index_type}.faiss"
    metadata_path = output_dir / "metadata.json"
    if (index_path.exists() or metadata_path.exists() or Path(args.report).exists() or Path(args.qa_report).exists()) and not args.overwrite:
        raise FileExistsError("FAISS outputs exist; pass --overwrite")

    dense_state = load_dense_state(dense_dir, args.allow_partial_dense_index)
    dim = int(dense_state["dimension"])
    completed = int(dense_state["completed_count"])
    target = min(completed, args.limit) if args.limit else completed
    if target <= 0:
        raise RuntimeError("no vectors selected for FAISS build")

    vectors = np.memmap(dense_state["vectors"], dtype=np.dtype(str(dense_state["dtype"])), mode="r", shape=(int(dense_state["total_count"]), dim))
    if args.index_type == "flat_ip":
        index = faiss.IndexFlatIP(dim)
        trained_vectors = 0
    else:
        quantizer = faiss.IndexFlatIP(dim)
        index = faiss.IndexIVFFlat(quantizer, dim, int(args.nlist), faiss.METRIC_INNER_PRODUCT)
        train_size = min(target, int(args.train_size))
        train_rows = normalize_rows(np.asarray(vectors[:train_size]))
        train_start = time.time()
        index.train(train_rows)
        trained_vectors = train_size
        print(json.dumps({
            "phase": "faiss_train",
            "index_type": args.index_type,
            "train_vectors": train_size,
            "nlist": int(args.nlist),
            "elapsed_seconds": round(time.time() - train_start, 2),
        }, ensure_ascii=False), flush=True)
        index.nprobe = int(args.nprobe)
    started_at = utc_now()
    start_time = time.time()
    added = 0
    for start in range(0, target, args.batch_size):
        end = min(target, start + args.batch_size)
        batch = normalize_rows(np.asarray(vectors[start:end]))
        index.add(batch)
        added += end - start
        print(json.dumps({"phase": "faiss_add", "added": added, "target": target, "elapsed_seconds": round(time.time() - start_time, 2)}, ensure_ascii=False), flush=True)
    faiss.write_index(index, str(index_path))
    elapsed = time.time() - start_time
    id_rows_checked = count_id_rows(Path(dense_state["ids"]), target)
    probe_k = min(5, target)
    distances, indices = index.search(normalize_rows(np.asarray(vectors[:1])), probe_k)
    probe_hits = [
        {"rank": rank + 1, "row_index": int(idx), "score": round(float(score), 6)}
        for rank, (idx, score) in enumerate(zip(indices[0].tolist(), distances[0].tolist()))
    ]
    report = {
        "schema_version": "theraskill_faiss_bge_m3_index_report_v1",
        "created_at": started_at,
        "completed_at": utc_now(),
        "status": "complete",
        "dense_index": dense_state,
        "output_dir": str(output_dir),
        "index_path": str(index_path),
        "metadata_path": str(metadata_path),
        "index_type": args.index_type,
        "metric": "inner_product_on_l2_normalized_vectors",
        "ann_parameters": {
            "nlist": int(args.nlist) if args.index_type == "ivf_flat_ip" else None,
            "nprobe": int(args.nprobe) if args.index_type == "ivf_flat_ip" else None,
            "train_size": int(args.train_size) if args.index_type == "ivf_flat_ip" else None,
            "trained_vectors": trained_vectors,
        },
        "dimension": dim,
        "counts": {
            "target_vectors": target,
            "added_vectors": int(index.ntotal),
            "ids_rows_checked": id_rows_checked,
        },
        "elapsed_seconds": round(elapsed, 3),
        "throughput_vectors_per_second": round(target / max(elapsed, 1e-9), 3),
        "probe_hits": probe_hits,
        "scope_note": "FAISS validation/full builder. flat_ip is exact search; ivf_flat_ip is approximate ANN over normalized vectors and must be evaluated for recall/latency.",
    }
    issues: list[dict[str, Any]] = []
    if int(index.ntotal) != target:
        issues.append({"issue": "faiss_ntotal_mismatch", "ntotal": int(index.ntotal), "target": target})
    if args.index_type == "ivf_flat_ip" and not index.is_trained:
        issues.append({"issue": "faiss_ivf_not_trained"})
    if id_rows_checked != target:
        issues.append({"issue": "ids_count_mismatch", "ids_rows_checked": id_rows_checked, "target": target})
    if dim <= 0:
        issues.append({"issue": "invalid_dimension", "dimension": dim})
    if not probe_hits:
        issues.append({"issue": "empty_probe_hits"})
    qa = {
        "schema_version": "theraskill_faiss_bge_m3_index_qa_v1",
        "created_at": report["created_at"],
        "status": "complete" if not issues else "failed",
        "issues_count": len(issues),
        "issues": issues,
        "dense_index_status": dense_state["status"],
        "index_type": args.index_type,
        "ann_parameters": report["ann_parameters"],
        "counts": report["counts"],
        "dimension": dim,
        "scope_note": report["scope_note"],
    }
    metadata = {
        "schema_version": "theraskill_faiss_bge_m3_index_metadata_v1",
        "created_at": report["created_at"],
        "completed_at": report["completed_at"],
        "status": report["status"],
        "dense_index": dense_state,
        "index_type": args.index_type,
        "metric": report["metric"],
        "ann_parameters": report["ann_parameters"],
        "dimension": dim,
        "artifacts": {
            "index": str(index_path),
            "report": str(Path(args.report)),
            "qa_report": str(Path(args.qa_report)),
        },
        "counts": report["counts"],
    }
    write_json(metadata_path, metadata)
    write_json(Path(args.report), report)
    write_json(Path(args.qa_report), qa)
    print(json.dumps({
        "status": qa["status"],
        "issues_count": qa["issues_count"],
        "index": str(index_path),
        "metadata": str(metadata_path),
        "report": str(Path(args.report)),
        "qa": str(Path(args.qa_report)),
        "added_vectors": int(index.ntotal),
        "elapsed_seconds": report["elapsed_seconds"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
