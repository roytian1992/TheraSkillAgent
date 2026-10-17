#!/usr/bin/env python3
"""Report current Advanced Retrieval v2 build/finalize status."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, iter_jsonl, read_json, write_json, write_text
from retrieval_surface_registry import (
    ADVANCED_RETRIEVAL_UPGRADE_PLAN_DOC,
    ADVANCED_RETRIEVAL_V2_FINALIZE_LOG_PATH,
    ADVANCED_RETRIEVAL_V2_FINALIZE_MANIFEST_PATH,
    ADVANCED_RETRIEVAL_V2_STATUS_JSON_PATH,
    ADVANCED_RETRIEVAL_V2_STATUS_MD_PATH,
    DENSE_BGE_M3_INDEX_DIR,
    DENSE_BGE_M3_QA_PATH,
    FORMAL_HYBRID_QA_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ADVANCED_RETRIEVAL_V2_STATUS_JSON_PATH
DEFAULT_MD = ADVANCED_RETRIEVAL_V2_STATUS_MD_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-report", default=str(DEFAULT_JSON))
    parser.add_argument("--markdown-report", default=str(DEFAULT_MD))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def dense_wait_events(log_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not log_path.exists():
        return events
    for row in iter_jsonl(log_path):
        try:
            if row.get("event") == "dense_wait":
                dense_status = row.get("dense_status") or {}
                events.append({
                    "created_at": row.get("created_at"),
                    "completed_count": int(dense_status.get("completed_count") or 0),
                    "total_count": int(dense_status.get("total_count") or 0),
                })
        except Exception:
            continue
    return events


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.stdout.strip()


def tmux_sessions() -> list[str]:
    out = run(["tmux", "list-sessions"])
    return [line.split(":", 1)[0] for line in out.splitlines() if line.strip()]


def pgrep(pattern: str) -> list[str]:
    out = run(["pgrep", "-af", pattern])
    return [line for line in out.splitlines() if line.strip()]


def file_size(path: Path) -> int | None:
    return path.stat().st_size if path.exists() else None


def build_report() -> dict[str, Any]:
    dense_dir = DENSE_BGE_M3_INDEX_DIR
    progress_path = dense_dir / "progress.json"
    metadata_path = dense_dir / "metadata.json"
    dense_progress = read_json(progress_path) if progress_path.exists() else {}
    dense_metadata = read_json(metadata_path) if metadata_path.exists() else {}
    dense_qa = read_json(DENSE_BGE_M3_QA_PATH) if DENSE_BGE_M3_QA_PATH.exists() else {}
    formal_qa = read_json(FORMAL_HYBRID_QA_PATH) if FORMAL_HYBRID_QA_PATH.exists() else {}
    finalize_manifest = read_json(ADVANCED_RETRIEVAL_V2_FINALIZE_MANIFEST_PATH) if ADVANCED_RETRIEVAL_V2_FINALIZE_MANIFEST_PATH.exists() else {}
    finalize_log = ADVANCED_RETRIEVAL_V2_FINALIZE_LOG_PATH
    sessions = tmux_sessions()
    metadata_counts = dense_metadata.get("counts") or {}
    qa_counts = dense_qa.get("counts") or {}
    metadata_complete = dense_metadata.get("status") == "complete"
    qa_complete = dense_qa.get("status") == "complete" and dense_qa.get("issues_count") == 0
    if metadata_complete and qa_complete:
        completed = int(metadata_counts.get("completed_count") or qa_counts.get("completed_count") or dense_progress.get("completed_count") or 0)
        total = int(metadata_counts.get("target_eligible_rows") or qa_counts.get("target_eligible_rows") or dense_progress.get("total_count") or completed)
    else:
        completed = int(dense_progress.get("completed_count") or metadata_counts.get("completed_count") or qa_counts.get("completed_count") or 0)
        total = int(dense_progress.get("total_count") or metadata_counts.get("target_eligible_rows") or qa_counts.get("target_eligible_rows") or 0)
    dense_percent = round(completed / total * 100, 3) if total else 0.0
    dense_complete = bool(
        completed > 0
        and total > 0
        and completed == total
        and metadata_complete
        and qa_complete
    )
    wait_events = dense_wait_events(finalize_log)
    speed_rows_per_second = None
    eta_seconds = None
    eta_hours = None
    if len(wait_events) >= 2:
        first = wait_events[0]
        last = wait_events[-1]
        first_time = parse_dt(first.get("created_at"))
        last_time = parse_dt(last.get("created_at"))
        if first_time and last_time and last_time > first_time and last["completed_count"] > first["completed_count"]:
            elapsed = (last_time - first_time).total_seconds()
            speed_rows_per_second = round((last["completed_count"] - first["completed_count"]) / elapsed, 3)
    if speed_rows_per_second and speed_rows_per_second > 0 and total and completed < total:
        eta_seconds = round((total - completed) / speed_rows_per_second, 1)
        eta_hours = round(eta_seconds / 3600, 3)
    formal_complete = bool(formal_qa.get("status") == "complete" and formal_qa.get("issues_count") == 0)
    status = "formal_complete" if formal_complete else ("dense_complete_waiting_formal" if dense_complete else "dense_running")
    return {
        "schema_version": "theraskill_advanced_retrieval_v2_status_report_v1",
        "created_at": utc_now(),
        "status": status,
        "dense": {
            "completed_count": completed,
            "total_count": total,
            "percent": dense_percent,
            "progress": dense_progress,
            "metadata_exists": bool(dense_metadata),
            "metadata_status": dense_metadata.get("status"),
            "qa_exists": bool(dense_qa),
            "qa_status": dense_qa.get("status"),
            "qa_issues_count": dense_qa.get("issues_count"),
            "complete": dense_complete,
            "vectors_bytes": file_size(dense_dir / "vectors.float16.memmap"),
            "ids_bytes": file_size(dense_dir / "ids.jsonl"),
            "speed_rows_per_second_from_wait_log": speed_rows_per_second,
            "eta_seconds": eta_seconds,
            "eta_hours": eta_hours,
            "wait_event_count": len(wait_events),
        },
        "finalize": {
            "tmux_session_present": "theraskill_advanced_retrieval_finalize" in sessions,
            "processes": pgrep("finalize_advanced_retrieval_v2.py"),
            "manifest_exists": bool(finalize_manifest),
            "manifest_status": finalize_manifest.get("status"),
            "manifest_dry_run": finalize_manifest.get("dry_run"),
            "readiness_issues_count": finalize_manifest.get("readiness_issues_count"),
        },
        "formal_benchmark": {
            "qa_exists": bool(formal_qa),
            "qa_status": formal_qa.get("status"),
            "issues_count": formal_qa.get("issues_count"),
            "case_count": formal_qa.get("case_count"),
            "complete": formal_complete,
        },
        "tmux_sessions": sessions,
        "dense_processes": pgrep("build_dense_embedding_index.py --output-dir outputs/knowledge_engine/indexes/dense_bge_m3"),
        "docs_status_line": next(
            (line.strip() for line in ADVANCED_RETRIEVAL_UPGRADE_PLAN_DOC.read_text(encoding="utf-8").splitlines() if line.startswith("当前状态：")),
            "",
        ),
    }


def build_markdown(report: dict[str, Any]) -> str:
    dense = report["dense"]
    finalize = report["finalize"]
    formal = report["formal_benchmark"]
    lines = [
        "# Advanced Retrieval v2 Status",
        "",
        f"Created at: `{report['created_at']}`",
        f"Status: `{report['status']}`",
        f"Docs: `{report['docs_status_line']}`",
        "",
        "## Dense",
        "",
        f"- completed: `{dense['completed_count']}` / `{dense['total_count']}` (`{dense['percent']}%`)",
        f"- complete: `{dense['complete']}`",
        f"- metadata_status: `{dense['metadata_status']}`",
        f"- qa_status: `{dense['qa_status']}`",
        f"- qa_issues_count: `{dense['qa_issues_count']}`",
        f"- speed_rows_per_second_from_wait_log: `{dense['speed_rows_per_second_from_wait_log']}`",
        f"- eta_hours: `{dense['eta_hours']}`",
        "",
        "## Finalize",
        "",
        f"- tmux_session_present: `{finalize['tmux_session_present']}`",
        f"- process_count: `{len(finalize['processes'])}`",
        f"- manifest_status: `{finalize['manifest_status']}`",
        f"- readiness_issues_count: `{finalize['readiness_issues_count']}`",
        "",
        "## Formal Benchmark",
        "",
        f"- qa_exists: `{formal['qa_exists']}`",
        f"- qa_status: `{formal['qa_status']}`",
        f"- issues_count: `{formal['issues_count']}`",
        f"- case_count: `{formal['case_count']}`",
        f"- complete: `{formal['complete']}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    json_path = Path(args.json_report)
    md_path = Path(args.markdown_report)
    for path in (json_path, md_path):
        ensure_output_path(path, overwrite=args.overwrite)
    report = build_report()
    write_json(json_path, report, overwrite=args.overwrite)
    write_text(md_path, build_markdown(report) + "\n", overwrite=args.overwrite)
    print(json.dumps({
        "status": report["status"],
        "dense_completed_count": report["dense"]["completed_count"],
        "dense_total_count": report["dense"]["total_count"],
        "dense_percent": report["dense"]["percent"],
        "finalize_tmux_session_present": report["finalize"]["tmux_session_present"],
        "formal_complete": report["formal_benchmark"]["complete"],
        "json_report": str(json_path),
        "markdown_report": str(md_path),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
