#!/usr/bin/env python3
"""Build sidecar metadata_v2 SQLite index for advanced retrieval v3."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retrieval_surface_registry import (
    METADATA_V2_DIR,
    METADATA_V2_QA_PATH,
    METADATA_V2_REPORT_PATH,
    STRUCTURED_INDEX_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRUCTURED = STRUCTURED_INDEX_PATH
DEFAULT_OUTPUT_DIR = METADATA_V2_DIR
DEFAULT_REPORT = METADATA_V2_REPORT_PATH
DEFAULT_QA = METADATA_V2_QA_PATH

LIST_FIELDS = {
    "dataset_keys",
    "evidence_tier_keys",
    "knowledge_type_keys",
    "language_keys",
    "license_constraint_keys",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structured-index", default=str(DEFAULT_STRUCTURED))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_scalar(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_scalar(value)
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def connect_db(path: Path, overwrite: bool) -> sqlite3.Connection:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"metadata_v2 DB exists; pass --overwrite: {path}")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.execute("pragma temp_store=memory")
    con.execute(
        """
        create table metadata_v2 (
          retrieval_id text primary key,
          release_unit_id text not null,
          release_unit_type text,
          dedup_family text,
          canonical_knowledge_type text,
          primary_dataset text,
          primary_knowledge_type text,
          primary_evidence_tier text,
          primary_language text,
          quality_confidence text,
          registry_status text,
          source_weight text,
          risk_scope text,
          default_route text,
          retrieval_eligible integer,
          embedding_eligible integer,
          member_count integer,
          retrieval_text_chars integer,
          retrieval_text_sha256 text
        )
        """
    )
    con.execute(
        """
        create table metadata_v2_tokens (
          retrieval_id text not null,
          field text not null,
          value text not null,
          primary key (retrieval_id, field, value)
        )
        """
    )
    return con


def create_indexes(con: sqlite3.Connection) -> None:
    indexes = [
        "create index idx_metadata_v2_family on metadata_v2(dedup_family)",
        "create index idx_metadata_v2_route on metadata_v2(default_route)",
        "create index idx_metadata_v2_risk on metadata_v2(risk_scope)",
        "create index idx_metadata_v2_language on metadata_v2(primary_language)",
        "create index idx_metadata_v2_quality on metadata_v2(quality_confidence)",
        "create index idx_metadata_v2_source_weight on metadata_v2(source_weight)",
        "create index idx_metadata_v2_knowledge_type on metadata_v2(primary_knowledge_type)",
        "create index idx_metadata_v2_evidence_tier on metadata_v2(primary_evidence_tier)",
        "create index idx_metadata_v2_dataset on metadata_v2(primary_dataset)",
        "create index idx_metadata_v2_eligible on metadata_v2(retrieval_eligible, embedding_eligible)",
        "create index idx_metadata_v2_family_route_risk on metadata_v2(dedup_family, default_route, risk_scope)",
        "create index idx_metadata_v2_tokens_field_value on metadata_v2_tokens(field, value)",
        "create index idx_metadata_v2_tokens_retrieval on metadata_v2_tokens(retrieval_id)",
    ]
    for sql in indexes:
        con.execute(sql)


def metadata_row(obj: dict[str, Any]) -> tuple[Any, ...]:
    return (
        clean_scalar(obj.get("retrieval_id")),
        clean_scalar(obj.get("release_unit_id")),
        clean_scalar(obj.get("release_unit_type")),
        clean_scalar(obj.get("dedup_family")),
        clean_scalar(obj.get("canonical_knowledge_type")),
        clean_scalar(obj.get("primary_dataset")),
        clean_scalar(obj.get("primary_knowledge_type")),
        clean_scalar(obj.get("primary_evidence_tier")),
        clean_scalar(obj.get("primary_language")),
        clean_scalar(obj.get("quality_confidence")),
        clean_scalar(obj.get("registry_status")),
        clean_scalar(obj.get("source_weight")),
        clean_scalar(obj.get("risk_scope")),
        clean_scalar(obj.get("default_route")),
        1 if obj.get("retrieval_eligible") else 0,
        1 if obj.get("embedding_eligible") else 0,
        int(obj.get("member_count") or 0),
        int(obj.get("retrieval_text_chars") or 0),
        clean_scalar(obj.get("retrieval_text_sha256")),
    )


def token_rows(obj: dict[str, Any]) -> list[tuple[str, str, str]]:
    retrieval_id = clean_scalar(obj.get("retrieval_id"))
    if not retrieval_id:
        return []
    rows: list[tuple[str, str, str]] = []
    for field in LIST_FIELDS:
        for value in clean_list(obj.get(field)):
            rows.append((retrieval_id, field, value))
    return rows


def build(args: argparse.Namespace) -> dict[str, Any]:
    structured = Path(args.structured_index)
    output_dir = Path(args.output_dir)
    db_path = output_dir / "metadata_v2.sqlite"
    con = connect_db(db_path, args.overwrite)
    counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    token_field_counts: Counter[str] = Counter()
    row_batch: list[tuple[Any, ...]] = []
    token_batch: list[tuple[str, str, str]] = []
    try:
        with structured.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                obj = json.loads(line)
                counts["input_rows"] += 1
                row = metadata_row(obj)
                if not row[0] or not row[1]:
                    counts["missing_required_id_rows"] += 1
                    continue
                row_batch.append(row)
                toks = token_rows(obj)
                token_batch.extend(toks)
                for _, field, _ in toks:
                    token_field_counts[field] += 1
                family_counts[str(obj.get("dedup_family") or "__missing__")] += 1
                if len(row_batch) >= args.batch_size:
                    flush(con, row_batch, token_batch)
                if args.limit and counts["input_rows"] >= args.limit:
                    break
        flush(con, row_batch, token_batch)
        create_indexes(con)
        con.commit()
        output_rows = int(con.execute("select count(*) from metadata_v2").fetchone()[0])
        token_rows_count = int(con.execute("select count(*) from metadata_v2_tokens").fetchone()[0])
        distinct_retrieval_ids = int(con.execute("select count(distinct retrieval_id) from metadata_v2").fetchone()[0])
    finally:
        con.close()
    return {
        "schema_version": "theraskill_metadata_v2_build_report_v1",
        "created_at": utc_now(),
        "structured_index": str(structured),
        "output_db": str(db_path),
        "limit": args.limit,
        "counts": {
            "input_rows": counts["input_rows"],
            "output_rows": output_rows,
            "distinct_retrieval_ids": distinct_retrieval_ids,
            "token_rows": token_rows_count,
            "missing_required_id_rows": counts["missing_required_id_rows"],
        },
        "family_counts_top": family_counts.most_common(20),
        "token_field_counts": dict(token_field_counts),
    }


def flush(con: sqlite3.Connection, row_batch: list[tuple[Any, ...]], token_batch: list[tuple[str, str, str]]) -> None:
    if row_batch:
        con.executemany(
            """
            insert or replace into metadata_v2 values (
              ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            row_batch,
        )
        row_batch.clear()
    if token_batch:
        con.executemany("insert or ignore into metadata_v2_tokens values (?,?,?)", token_batch)
        token_batch.clear()


def qa_from_report(report: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    counts = report["counts"]
    if counts["input_rows"] != counts["output_rows"]:
        issues.append(f"input/output row mismatch: {counts['input_rows']} != {counts['output_rows']}")
    if counts["output_rows"] != counts["distinct_retrieval_ids"]:
        issues.append("metadata_v2 retrieval_id is not unique")
    if counts["missing_required_id_rows"]:
        issues.append(f"missing required id rows: {counts['missing_required_id_rows']}")
    if counts["token_rows"] <= counts["output_rows"]:
        issues.append("token table unexpectedly small")
    return {
        "schema_version": "theraskill_metadata_v2_qa_v1",
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
    qa = qa_from_report(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(report_path),
        "qa_report": str(qa_path),
        "status": qa["status"],
        "issues_count": qa["issues_count"],
        "output_db": report["output_db"],
        "counts": report["counts"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
