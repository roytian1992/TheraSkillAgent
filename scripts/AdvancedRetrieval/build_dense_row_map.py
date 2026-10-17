#!/usr/bin/env python3
"""Build retrieval_id -> dense row_index sidecar for scoped dense retrieval."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, read_json, write_json
from retrieval_surface_registry import (
    DENSE_BGE_M3_INDEX_DIR,
    DENSE_BGE_M3_ROW_MAP_DIR,
    DENSE_BGE_M3_ROW_MAP_QA_PATH,
    DENSE_BGE_M3_ROW_MAP_REPORT_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DENSE_INDEX = DENSE_BGE_M3_INDEX_DIR
DEFAULT_OUTPUT_DIR = DENSE_BGE_M3_ROW_MAP_DIR
DEFAULT_REPORT = DENSE_BGE_M3_ROW_MAP_REPORT_PATH
DEFAULT_QA = DENSE_BGE_M3_ROW_MAP_QA_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-index-dir", default=str(DEFAULT_DENSE_INDEX))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--batch-size", type=int, default=50000)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db(path: Path, overwrite: bool) -> sqlite3.Connection:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"row map DB exists; pass --overwrite: {path}")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.execute("pragma temp_store=memory")
    con.execute(
        """
        create table dense_row_map (
          retrieval_id text primary key,
          row_index integer not null,
          release_unit_id text,
          dedup_family text,
          risk_scope text,
          primary_dataset text,
          primary_knowledge_type text
        )
        """
    )
    return con


def flush(con: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    con.executemany("insert or replace into dense_row_map values (?,?,?,?,?,?,?)", rows)
    rows.clear()


def build(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    dense_dir = Path(args.dense_index_dir)
    metadata_path = dense_dir / "metadata.json"
    ids_path = dense_dir / "ids.jsonl"
    if not metadata_path.exists():
        raise FileNotFoundError(metadata_path)
    if not ids_path.exists():
        raise FileNotFoundError(ids_path)
    metadata = read_json(metadata_path)
    counts = metadata.get("counts") or {}
    expected = int(counts.get("completed_count") or counts.get("target_eligible_rows") or 0)
    db_path = Path(args.output_dir) / "dense_row_map.sqlite"
    con = connect_db(db_path, args.overwrite)
    rows: list[tuple[Any, ...]] = []
    scanned = 0
    missing_retrieval_id = 0
    try:
        with ids_path.open("r", encoding="utf-8") as handle:
            for scanned, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                obj = json.loads(line)
                retrieval_id = str(obj.get("retrieval_id") or "").strip()
                if not retrieval_id:
                    missing_retrieval_id += 1
                    continue
                row_index = int(obj.get("row_index") if obj.get("row_index") is not None else scanned - 1)
                rows.append((
                    retrieval_id,
                    row_index,
                    obj.get("release_unit_id"),
                    obj.get("dedup_family"),
                    obj.get("risk_scope"),
                    obj.get("primary_dataset"),
                    obj.get("primary_knowledge_type"),
                ))
                if len(rows) >= args.batch_size:
                    flush(con, rows)
        flush(con, rows)
        con.execute("create index idx_dense_row_map_row_index on dense_row_map(row_index)")
        con.execute("create index idx_dense_row_map_family on dense_row_map(dedup_family)")
        con.execute("create index idx_dense_row_map_risk on dense_row_map(risk_scope)")
        con.commit()
        output_rows = int(con.execute("select count(*) from dense_row_map").fetchone()[0])
        min_row, max_row = con.execute("select min(row_index), max(row_index) from dense_row_map").fetchone()
    finally:
        con.close()
    issues = []
    if expected and output_rows != expected:
        issues.append({"issue": "row_count_mismatch", "expected": expected, "output_rows": output_rows})
    if missing_retrieval_id:
        issues.append({"issue": "missing_retrieval_id_rows", "count": missing_retrieval_id})
    if output_rows and (min_row != 0 or max_row != output_rows - 1):
        issues.append({"issue": "row_index_range_unexpected", "min_row_index": min_row, "max_row_index": max_row, "output_rows": output_rows})
    report = {
        "schema_version": "theraskill_dense_row_map_report_v1",
        "created_at": utc_now(),
        "dense_index_dir": str(dense_dir),
        "dense_metadata": str(metadata_path),
        "dense_ids": str(ids_path),
        "output_db": str(db_path),
        "dense_index_status": metadata.get("status"),
        "counts": {
            "expected_dense_rows": expected,
            "ids_rows_scanned": scanned,
            "output_rows": output_rows,
            "missing_retrieval_id_rows": missing_retrieval_id,
            "min_row_index": min_row,
            "max_row_index": max_row,
        },
    }
    qa = {
        "schema_version": "theraskill_dense_row_map_qa_v1",
        "created_at": utc_now(),
        "status": "complete" if not issues else "issues_found",
        "issues_count": len(issues),
        "issues": issues,
        "output_db": str(db_path),
        "expected_dense_rows": expected,
        "output_rows": output_rows,
    }
    return report, qa


def main() -> int:
    args = parse_args()
    report, qa = build(args)
    report_path = Path(args.report)
    qa_path = Path(args.qa_report)
    for path in (report_path, qa_path):
        ensure_output_path(path, overwrite=args.overwrite)
    write_json(report_path, report)
    write_json(qa_path, qa)
    print(json.dumps({
        "report": str(report_path),
        "qa_report": str(qa_path),
        "status": qa["status"],
        "issues_count": qa["issues_count"],
        "output_rows": qa["output_rows"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
