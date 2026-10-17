#!/usr/bin/env python3
"""Build deterministic style-response bucket index for large-scale dedup planning."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, write_json  # noqa: E402

DEFAULT_INPUT = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_OUTPUT = "outputs/knowledge/dedup/indexes/style_response_buckets/style_response_bucket_index.jsonl"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/style_response_bucket_index_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/style_response_bucket_index_qa.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--max-member-preview", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


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


def bucket_key(row: dict[str, Any]) -> tuple[str, ...]:
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


def bucket_id_for(key: tuple[str, ...]) -> str:
    raw = "::".join(key)
    return "srb_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def bucket_size_class(size: int) -> str:
    if size < 2:
        return "singleton"
    if size <= 500:
        return "small_embedding_ready"
    if size <= 5000:
        return "medium_embedding_ready"
    if size <= 50000:
        return "large_needs_subbucket_or_ann"
    return "huge_requires_stricter_blocking"


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"output exists: {output}; pass --overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)

    buckets: dict[tuple[str, ...], dict[str, Any]] = {}
    counters = Counter()
    issues: list[dict[str, Any]] = []

    with Path(args.input).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            counters["input_rows_scanned"] += 1
            row = json.loads(line)
            if row.get("dedup_family") != "style_response":
                continue
            counters["style_response_rows"] += 1
            key = bucket_key(row)
            bucket = buckets.setdefault(
                key,
                {
                    "bucket_id": bucket_id_for(key),
                    "bucket_key": {
                        "dataset": key[0],
                        "knowledge_type": key[1],
                        "response_function": key[2],
                        "style_tags": key[3],
                        "length_bucket": key[4],
                        "language": key[5],
                        "hash_prefix": key[6],
                    },
                    "member_count": 0,
                    "dataset_distribution": Counter(),
                    "knowledge_type_distribution": Counter(),
                    "member_preview_unit_ids": [],
                    "raw_refs_preview": [],
                },
            )
            bucket["member_count"] += 1
            bucket["dataset_distribution"][str(row.get("dataset"))] += 1
            bucket["knowledge_type_distribution"][str(row.get("knowledge_type"))] += 1
            if len(bucket["member_preview_unit_ids"]) < args.max_member_preview:
                bucket["member_preview_unit_ids"].append(row.get("unit_id"))
                bucket["raw_refs_preview"].append({"unit_id": row.get("unit_id"), "raw_file": row.get("raw_file"), "raw_line_no": row.get("raw_line_no")})

    digest = hashlib.sha256()
    class_counter = Counter()
    bucket_count = 0
    with output.open("w", encoding="utf-8") as out:
        for key, bucket in sorted(buckets.items(), key=lambda item: (-item[1]["member_count"], item[0])):
            bucket["dataset_distribution"] = dict(bucket["dataset_distribution"].most_common())
            bucket["knowledge_type_distribution"] = dict(bucket["knowledge_type_distribution"].most_common())
            bucket["bucket_size_class"] = bucket_size_class(bucket["member_count"])
            bucket["recommended_next_step"] = {
                "singleton": "skip_embedding_until_needed",
                "small_embedding_ready": "run_exact_then_embedding_within_bucket",
                "medium_embedding_ready": "run_embedding_within_bucket",
                "large_needs_subbucket_or_ann": "subbucket_by_function_tags_or_use_ann",
                "huge_requires_stricter_blocking": "requires_additional_blocking_before_embedding",
            }[bucket["bucket_size_class"]]
            record = {"schema_version": "theraskill_style_response_bucket_v1", **bucket}
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            out.write(encoded)
            digest.update(encoded.encode("utf-8"))
            class_counter[bucket["bucket_size_class"]] += 1
            bucket_count += 1

    if counters["style_response_rows"] == 0:
        issues.append({"issue": "no_style_response_rows"})
    status = "complete" if not issues else "incomplete"
    manifest = {
        "schema_version": "theraskill_style_response_bucket_index_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input": {"path": args.input, "sha256": file_sha256(args.input)},
        "output": {"path": str(output), "rows": bucket_count, "sha256": digest.hexdigest(), "file_sha256": file_sha256(output)},
        "counts": dict(counters),
        "bucket_size_class_distribution": dict(class_counter.most_common()),
        "largest_buckets_preview": [],
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
    }
    with output.open("r", encoding="utf-8") as handle:
        for line in handle:
            if len(manifest["largest_buckets_preview"]) >= 20:
                break
            record = json.loads(line)
            manifest["largest_buckets_preview"].append(
                {
                    "bucket_id": record["bucket_id"],
                    "member_count": record["member_count"],
                    "bucket_size_class": record["bucket_size_class"],
                    "bucket_key": record["bucket_key"],
                }
            )
    write_json(args.manifest, manifest)
    write_json(args.qa_report, manifest)
    print(json.dumps({"status": status, "issues_count": len(issues), "style_rows": counters["style_response_rows"], "buckets": bucket_count, "classes": dict(class_counter)}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
