#!/usr/bin/env python3
"""Build compact label/text FTS sidecar for TheraSkill retrieval v3."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retrieval_surface_registry import (
    COMPACT_LABEL_V1_DIR,
    COMPACT_LABEL_V1_QA_PATH,
    COMPACT_LABEL_V1_REPORT_PATH,
    RETRIEVAL_TEXT_VIEW_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEXT_VIEW = RETRIEVAL_TEXT_VIEW_PATH
DEFAULT_OUTPUT_DIR = COMPACT_LABEL_V1_DIR
DEFAULT_REPORT = COMPACT_LABEL_V1_REPORT_PATH
DEFAULT_QA = COMPACT_LABEL_V1_QA_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=str(DEFAULT_TEXT_VIEW))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--text-prefix-chars", type=int, default=480)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def compact_text(obj: dict[str, Any], prefix_chars: int) -> str:
    parts: list[str] = []
    for key in (
        "canonical_label",
        "canonical_knowledge_type",
        "primary_knowledge_type",
        "primary_evidence_tier",
        "primary_dataset",
        "dedup_family",
        "risk_scope",
        "source_weight",
        "quality_confidence",
    ):
        value = clean(obj.get(key))
        if value:
            parts.append(value.replace("_", " "))
    for key in ("knowledge_type_keys", "evidence_tier_keys", "dataset_keys", "language_keys"):
        for value in obj.get(key) or []:
            text = clean(value)
            if text:
                parts.append(text.replace("_", " "))
    retrieval_text = clean(obj.get("retrieval_text"))
    if retrieval_text:
        parts.append(retrieval_text[:prefix_chars])
    return " ".join(parts)


def connect(path: Path, overwrite: bool) -> sqlite3.Connection:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"compact label DB exists; pass --overwrite: {path}")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.execute("pragma temp_store=memory")
    con.execute(
        """
        create table compact_label_meta (
          retrieval_id text primary key,
          release_unit_id text not null,
          dedup_family text,
          canonical_label text,
          canonical_knowledge_type text,
          primary_dataset text,
          primary_knowledge_type text,
          primary_evidence_tier text,
          primary_language text,
          quality_confidence text,
          source_weight text,
          risk_scope text,
          default_route text,
          retrieval_eligible integer,
          embedding_eligible integer,
          member_count integer,
          retrieval_text_chars integer
        )
        """
    )
    con.execute(
        """
        create virtual table compact_label_fts using fts5(
          retrieval_id unindexed,
          compact_text,
          tokenize='unicode61'
        )
        """
    )
    return con


def create_indexes(con: sqlite3.Connection) -> None:
    for sql in (
        "create index idx_compact_label_meta_family on compact_label_meta(dedup_family)",
        "create index idx_compact_label_meta_route on compact_label_meta(default_route)",
        "create index idx_compact_label_meta_risk on compact_label_meta(risk_scope)",
        "create index idx_compact_label_meta_knowledge_type on compact_label_meta(primary_knowledge_type)",
        "create index idx_compact_label_meta_dataset on compact_label_meta(primary_dataset)",
        "create index idx_compact_label_meta_source_weight on compact_label_meta(source_weight)",
        "create index idx_compact_label_meta_family_route on compact_label_meta(dedup_family, default_route)",
    ):
        con.execute(sql)


def meta_row(obj: dict[str, Any]) -> tuple[Any, ...]:
    return (
        clean(obj.get("retrieval_id")),
        clean(obj.get("release_unit_id")),
        clean(obj.get("dedup_family")),
        clean(obj.get("canonical_label")),
        clean(obj.get("canonical_knowledge_type")),
        clean(obj.get("primary_dataset")),
        clean(obj.get("primary_knowledge_type")),
        clean(obj.get("primary_evidence_tier")),
        clean(obj.get("primary_language")),
        clean(obj.get("quality_confidence")),
        clean(obj.get("source_weight")),
        clean(obj.get("risk_scope")),
        clean(obj.get("default_route")),
        1 if obj.get("retrieval_eligible") else 0,
        1 if obj.get("embedding_eligible") else 0,
        int(obj.get("member_count") or 0),
        int(obj.get("retrieval_text_chars") or 0),
    )


def flush(con: sqlite3.Connection, meta_batch: list[tuple[Any, ...]], fts_batch: list[tuple[str, str]]) -> None:
    if meta_batch:
        con.executemany("insert or replace into compact_label_meta values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", meta_batch)
        meta_batch.clear()
    if fts_batch:
        con.executemany("insert into compact_label_fts(retrieval_id, compact_text) values (?,?)", fts_batch)
        fts_batch.clear()


def build(args: argparse.Namespace) -> dict[str, Any]:
    text_view = Path(args.text_view)
    output_dir = Path(args.output_dir)
    db_path = output_dir / "compact_label_v1.sqlite"
    con = connect(db_path, args.overwrite)
    counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    meta_batch: list[tuple[Any, ...]] = []
    fts_batch: list[tuple[str, str]] = []
    try:
        with text_view.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                obj = json.loads(line)
                counts["input_rows"] += 1
                rid = clean(obj.get("retrieval_id"))
                release_id = clean(obj.get("release_unit_id"))
                if not rid or not release_id:
                    counts["missing_required_id_rows"] += 1
                    continue
                meta_batch.append(meta_row(obj))
                fts_batch.append((rid, compact_text(obj, args.text_prefix_chars)))
                family_counts[clean(obj.get("dedup_family")) or "__missing__"] += 1
                if len(meta_batch) >= args.batch_size:
                    flush(con, meta_batch, fts_batch)
                if args.limit and counts["input_rows"] >= args.limit:
                    break
        flush(con, meta_batch, fts_batch)
        create_indexes(con)
        con.commit()
        meta_rows = int(con.execute("select count(*) from compact_label_meta").fetchone()[0])
        fts_rows = int(con.execute("select count(*) from compact_label_fts").fetchone()[0])
    finally:
        con.close()
    return {
        "schema_version": "theraskill_compact_label_v1_build_report",
        "created_at": utc_now(),
        "text_view": str(text_view),
        "output_db": str(db_path),
        "limit": args.limit,
        "text_prefix_chars": args.text_prefix_chars,
        "counts": {
            "input_rows": counts["input_rows"],
            "meta_rows": meta_rows,
            "fts_rows": fts_rows,
            "missing_required_id_rows": counts["missing_required_id_rows"],
        },
        "family_counts_top": family_counts.most_common(20),
    }


def qa(report: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    counts = report["counts"]
    if counts["input_rows"] != counts["meta_rows"]:
        issues.append(f"input/meta row mismatch: {counts['input_rows']} != {counts['meta_rows']}")
    if counts["meta_rows"] != counts["fts_rows"]:
        issues.append(f"meta/fts row mismatch: {counts['meta_rows']} != {counts['fts_rows']}")
    if counts["missing_required_id_rows"]:
        issues.append(f"missing required id rows: {counts['missing_required_id_rows']}")
    return {
        "schema_version": "theraskill_compact_label_v1_qa",
        "created_at": utc_now(),
        "status": "complete" if not issues else "issues_found",
        "issues_count": len(issues),
        "issues": issues,
        "counts": counts,
        "output_db": report["output_db"],
    }


def main() -> int:
    args = parse_args()
    report_path = Path(args.report)
    qa_path = Path(args.qa_report)
    if (report_path.exists() or qa_path.exists()) and not args.overwrite:
        raise FileExistsError("report exists; pass --overwrite")
    report = build(args)
    qa_report = qa(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    qa_path.write_text(json.dumps(qa_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(report_path),
        "qa_report": str(qa_path),
        "status": qa_report["status"],
        "issues_count": qa_report["issues_count"],
        "output_db": report["output_db"],
        "counts": report["counts"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not qa_report["issues"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
