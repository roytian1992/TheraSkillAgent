#!/usr/bin/env python3
"""Generate style-response dedup candidate pairs within deterministic buckets."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.neighbors import NearestNeighbors

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from theraskill_llm import OpenAICompatibleEmbeddingClient, load_yaml  # noqa: E402
from io_utils import file_sha256, write_json  # noqa: E402


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_BUCKET_INDEX = "outputs/knowledge/dedup/indexes/style_response_buckets/style_response_bucket_index.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/indexes/embedding_candidates/style_response_bucketed"
DEFAULT_MODEL_CONFIG = "configs/model_config.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--bucket-index", default=DEFAULT_BUCKET_INDEX)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model-config", default=DEFAULT_MODEL_CONFIG)
    parser.add_argument("--datasets", default="", help="Comma-separated datasets to include.")
    parser.add_argument("--bucket-size-classes", default="small_embedding_ready,medium_embedding_ready")
    parser.add_argument("--threshold", type=float, default=0.88)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--embedding-chunk-size", type=int, default=512)
    parser.add_argument("--text-char-limit", type=int, default=4000)
    parser.add_argument("--max-buckets", type=int, default=None, help="Debug only; final runs should leave unset.")
    parser.add_argument("--max-records", type=int, default=None, help="Debug only; final runs should leave unset.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def split_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def length_bucket(text: str) -> str:
    n = len(text or "")
    if n < 60:
        return "very_short"
    if n < 180:
        return "short"
    if n < 500:
        return "medium"
    if n < 1200:
        return "long"
    return "very_long"


def get_key_field(row: dict[str, Any], key: str, default: str = "unknown") -> str:
    fields = row.get("dedup_key_fields") or {}
    value = fields.get(key)
    if value in (None, "", [], {}):
        return default
    if isinstance(value, str):
        value = value.strip()
        return value[:120] if value else default
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:120]


def style_tags(row: dict[str, Any]) -> str:
    text = " ".join([row.get("dedup_text") or "", json.dumps(row.get("dedup_key_fields") or {}, ensure_ascii=False)])
    tags = []
    for tag in [
        "explicit_empathy",
        "validation",
        "reassurance",
        "suggestion",
        "psychoeducation",
        "reflection",
        "question",
        "risk",
    ]:
        if tag in text:
            tags.append(tag)
    return "+".join(tags) if tags else "untagged"


def bucket_key_tuple(row: dict[str, Any]) -> tuple[str, ...]:
    text = row.get("dedup_text") or ""
    return (
        str(row.get("dataset") or "unknown"),
        str(row.get("knowledge_type") or "unknown"),
        get_key_field(row, "response_function"),
        style_tags(row),
        length_bucket(text),
        str(row.get("language") or "unknown"),
        str(row.get("dedup_text_sha256") or "")[:2],
    )


def bucket_id_for_key(key: tuple[str, ...]) -> str:
    return "srb_" + hashlib.sha256("::".join(key).encode("utf-8")).hexdigest()[:20]


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def embed_texts(client: OpenAICompatibleEmbeddingClient, texts: list[str], chunk_size: int) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), chunk_size):
        end = min(start + chunk_size, len(texts))
        embeddings.extend(client.embed(texts[start:end]))
    return embeddings


def make_pair_record(records: list[dict[str, Any]], left_idx: int, right_idx: int, similarity: float, bucket_id: str) -> dict[str, Any]:
    left = records[left_idx]
    right = records[right_idx]
    pair_id_source = f"{bucket_id}::{left['unit_id']}::{right['unit_id']}::{similarity:.6f}"
    return {
        "schema_version": "theraskill_embedding_candidate_pair_v1",
        "candidate_pair_id": "ecp_" + hashlib.sha256(pair_id_source.encode("utf-8")).hexdigest()[:20],
        "method": "embedding_cosine_bge_m3_brute_topk_within_style_bucket",
        "bucket_id": bucket_id,
        "cosine_similarity": round(similarity, 6),
        "left_unit_id": left["unit_id"],
        "right_unit_id": right["unit_id"],
        "left_dataset": left["dataset"],
        "right_dataset": right["dataset"],
        "left_knowledge_type": left["knowledge_type"],
        "right_knowledge_type": right["knowledge_type"],
        "dedup_family": "style_response",
        "same_dataset": left["dataset"] == right["dataset"],
        "same_knowledge_type": left["knowledge_type"] == right["knowledge_type"],
        "left_text_preview": left["text_preview"],
        "right_text_preview": right["text_preview"],
    }


def build_pairs(records: list[dict[str, Any]], embeddings: np.ndarray, threshold: float, top_k: int, bucket_id: str) -> list[dict[str, Any]]:
    if len(records) < 2:
        return []
    normalized = l2_normalize(embeddings.astype(np.float32, copy=False))
    k = min(max(2, top_k + 1), len(records))
    neighbors = NearestNeighbors(n_neighbors=k, metric="cosine", algorithm="brute")
    neighbors.fit(normalized)
    distances, indices = neighbors.kneighbors(normalized, return_distance=True)
    seen: set[tuple[int, int]] = set()
    pairs: list[dict[str, Any]] = []
    for left_idx, (row_distances, row_indices) in enumerate(zip(distances, indices)):
        for distance, right_idx in zip(row_distances, row_indices):
            right_idx = int(right_idx)
            if right_idx == left_idx:
                continue
            a, b = sorted((left_idx, right_idx))
            if (a, b) in seen:
                continue
            similarity = 1.0 - float(distance)
            if similarity < threshold:
                continue
            seen.add((a, b))
            pairs.append(make_pair_record(records, left_idx, right_idx, similarity, bucket_id))
    pairs.sort(key=lambda item: item["cosine_similarity"], reverse=True)
    return pairs


def load_target_buckets(args: argparse.Namespace) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    datasets = split_csv(args.datasets)
    classes = split_csv(args.bucket_size_classes)
    selected: dict[str, dict[str, Any]] = {}
    counters = Counter()
    issues: list[dict[str, Any]] = []
    with Path(args.bucket_index).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            counters["bucket_index_rows_scanned"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                counters["bucket_index_parse_errors"] += 1
                issues.append({"issue": "bucket_index_parse_error", "line": line_no, "error": str(exc)})
                continue
            key = row.get("bucket_key") or {}
            dataset = key.get("dataset")
            bucket_class = row.get("bucket_size_class")
            if datasets and dataset not in datasets:
                continue
            if classes and bucket_class not in classes:
                continue
            if int(row.get("member_count") or 0) < 2:
                continue
            selected[row["bucket_id"]] = row
            counters["selected_bucket_rows_expected"] += int(row.get("member_count") or 0)
            counters[f"selected_dataset::{dataset}"] += int(row.get("member_count") or 0)
            counters[f"selected_bucket_class::{bucket_class}"] += 1
            if args.max_buckets is not None and len(selected) >= args.max_buckets:
                break
    if not selected:
        issues.append({"issue": "no_target_buckets"})
    return selected, dict(counters), issues


def load_records_by_bucket(args: argparse.Namespace, target_bucket_ids: set[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], list[dict[str, Any]]]:
    records_by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counters = Counter()
    issues: list[dict[str, Any]] = []
    with Path(args.input).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            counters["text_view_rows_scanned"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                counters["text_view_parse_errors"] += 1
                issues.append({"issue": "text_view_parse_error", "line": line_no, "error": str(exc)})
                continue
            if row.get("dedup_family") != "style_response":
                continue
            bucket_id = bucket_id_for_key(bucket_key_tuple(row))
            if bucket_id not in target_bucket_ids:
                continue
            text = (row.get("dedup_text") or "")[: args.text_char_limit]
            if not text:
                counters["empty_text_rows"] += 1
                issues.append({"issue": "empty_text", "line": line_no, "unit_id": row.get("unit_id"), "bucket_id": bucket_id})
                continue
            records_by_bucket[bucket_id].append(
                {
                    "unit_id": row.get("unit_id"),
                    "dataset": row.get("dataset"),
                    "knowledge_type": row.get("knowledge_type"),
                    "dedup_text_sha256": row.get("dedup_text_sha256"),
                    "text": text,
                    "text_preview": text[:300],
                    "raw_file": row.get("raw_file"),
                    "raw_line_no": row.get("raw_line_no"),
                }
            )
            counters["selected_records"] += 1
            counters[f"selected_records_dataset::{row.get('dataset')}"] += 1
            if args.max_records is not None and counters["selected_records"] >= args.max_records:
                break
    return records_by_bucket, dict(counters), issues


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"output dir is not empty: {output_dir}; pass --overwrite")
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_records_path = output_dir / "selected_records.jsonl"
    pairs_path = output_dir / "candidate_pairs.jsonl"
    bucket_summary_path = output_dir / "bucket_candidate_summary.jsonl"
    manifest_path = output_dir / "embedding_candidate_manifest.json"
    qa_path = output_dir / "embedding_candidate_qa.json"
    if args.overwrite:
        for path in [selected_records_path, pairs_path, bucket_summary_path, manifest_path, qa_path]:
            if path.exists():
                path.unlink()

    target_buckets, bucket_stats, issues = load_target_buckets(args)
    records_by_bucket, record_stats, record_issues = load_records_by_bucket(args, set(target_buckets))
    issues.extend(record_issues)

    model_config = load_yaml(args.model_config)
    embedding_config = model_config.get("embedding") or {}
    client = OpenAICompatibleEmbeddingClient(embedding_config)

    counters = Counter()
    bucket_mismatch_count = 0
    started = time.time()
    pair_digest = hashlib.sha256()
    selected_digest = hashlib.sha256()
    summary_digest = hashlib.sha256()

    with selected_records_path.open("w", encoding="utf-8") as selected_out, pairs_path.open("w", encoding="utf-8") as pairs_out, bucket_summary_path.open("w", encoding="utf-8") as summary_out:
        for bucket_no, (bucket_id, bucket_meta) in enumerate(
            sorted(target_buckets.items(), key=lambda item: (-int(item[1].get("member_count") or 0), item[0])),
            start=1,
        ):
            records = records_by_bucket.get(bucket_id, [])
            expected = int(bucket_meta.get("member_count") or 0)
            if len(records) != expected:
                bucket_mismatch_count += 1
                issues.append({"issue": "bucket_member_count_mismatch", "bucket_id": bucket_id, "expected": expected, "loaded": len(records)})
            if len(records) < 2:
                counters["skipped_buckets_too_few_records"] += 1
                continue

            texts = [record["text"] for record in records]
            embeddings = np.asarray(embed_texts(client, texts, args.embedding_chunk_size), dtype=np.float32)
            if embeddings.ndim != 2 or embeddings.shape[0] != len(records):
                issues.append({"issue": "embedding_shape_mismatch", "bucket_id": bucket_id, "shape": list(embeddings.shape), "records": len(records)})
                continue
            pairs = build_pairs(records, embeddings, args.threshold, args.top_k, bucket_id)

            for local_index, record in enumerate(records):
                payload = {key: value for key, value in record.items() if key != "text"}
                payload["bucket_id"] = bucket_id
                payload["bucket_local_index"] = local_index
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                selected_out.write(encoded)
                selected_digest.update(encoded.encode("utf-8"))
            for pair in pairs:
                encoded = json.dumps(pair, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                pairs_out.write(encoded)
                pair_digest.update(encoded.encode("utf-8"))

            bucket_summary = {
                "schema_version": "theraskill_style_bucket_candidate_summary_v1",
                "bucket_id": bucket_id,
                "bucket_key": bucket_meta.get("bucket_key"),
                "bucket_size_class": bucket_meta.get("bucket_size_class"),
                "expected_member_count": expected,
                "loaded_member_count": len(records),
                "embedding_shape": list(embeddings.shape),
                "candidate_pair_count": len(pairs),
                "max_similarity": max((pair["cosine_similarity"] for pair in pairs), default=None),
            }
            encoded = json.dumps(bucket_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            summary_out.write(encoded)
            summary_digest.update(encoded.encode("utf-8"))

            counters["processed_buckets"] += 1
            counters["processed_records"] += len(records)
            counters["candidate_pairs"] += len(pairs)
            counters[f"dataset::{(bucket_meta.get('bucket_key') or {}).get('dataset')}"] += len(records)
            counters[f"bucket_class::{bucket_meta.get('bucket_size_class')}"] += 1
            if bucket_no % 100 == 0 or bucket_no == len(target_buckets):
                print(
                    json.dumps(
                        {
                            "event": "style_bucket_progress",
                            "processed_buckets": bucket_no,
                            "total_target_buckets": len(target_buckets),
                            "processed_records": counters["processed_records"],
                            "candidate_pairs": counters["candidate_pairs"],
                            "elapsed_seconds": round(time.time() - started, 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    if bucket_mismatch_count:
        issues.append({"issue": "bucket_member_count_mismatches_detected", "count": bucket_mismatch_count})
    status = "complete" if not issues else "incomplete"
    common = {
        "schema_version": "theraskill_style_bucket_embedding_candidate_generation_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": {"path": args.input, "sha256": file_sha256(args.input)},
        "bucket_index": {"path": args.bucket_index, "sha256": file_sha256(args.bucket_index)},
        "model_config": args.model_config,
        "embedding_model": embedding_config.get("name"),
        "embedding_endpoint": embedding_config.get("endpoint") or embedding_config.get("base_url"),
        "filters": {
            "datasets": sorted(split_csv(args.datasets)),
            "bucket_size_classes": sorted(split_csv(args.bucket_size_classes)),
            "max_buckets": args.max_buckets,
            "max_records": args.max_records,
            "debug_limits_used": args.max_buckets is not None or args.max_records is not None,
            "text_char_limit": args.text_char_limit,
        },
        "parameters": {
            "threshold": args.threshold,
            "top_k": args.top_k,
            "embedding_chunk_size": args.embedding_chunk_size,
            "candidate_method": "brute_topk_within_style_bucket",
        },
        "bucket_selection_stats": bucket_stats,
        "record_load_stats": record_stats,
        "counts": dict(counters),
        "outputs": {
            "selected_records": str(selected_records_path),
            "candidate_pairs": str(pairs_path),
            "bucket_candidate_summary": str(bucket_summary_path),
        },
        "output_sha256": {
            "selected_records": selected_digest.hexdigest(),
            "candidate_pairs": pair_digest.hexdigest(),
            "bucket_candidate_summary": summary_digest.hexdigest(),
            "selected_records_file": file_sha256(selected_records_path),
            "candidate_pairs_file": file_sha256(pairs_path),
            "bucket_candidate_summary_file": file_sha256(bucket_summary_path),
        },
        "elapsed_seconds": round(time.time() - started, 1),
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
    }
    write_json(manifest_path, common)
    write_json(qa_path, common)
    print(json.dumps({"status": status, "issues_count": len(issues), "counts": dict(counters), "output_dir": str(output_dir)}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
