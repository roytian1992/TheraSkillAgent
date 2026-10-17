#!/usr/bin/env python3
"""Refresh Anna-CPsyCounD canonical projection snapshot and print one summary.

This helper is the routine operational entry point during long-running shard
execution:
1. merge current shard outputs into canonical knowledge files
2. rebuild authoritative manifest/QA from canonical outputs
3. print a compact status summary for monitoring or handoff
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anna_cpsycound_surface_registry import (
    CURRENT_MD_OUTPUT_PATH,
    MANIFEST_OUTPUT_PATH,
    QA_OUTPUT_PATH,
    SHARD_DIR,
    STATUS_OUTPUT_PATH,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
PYTHON = sys.executable

MERGE_SCRIPT = SCRIPT_DIR / "merge_anna_patient_profile_shards.py"
FINALIZER_SCRIPT = SCRIPT_DIR / "build_anna_cpsycound_projection_manifest.py"
SYNC_SCRIPT = SCRIPT_DIR / "sync_anna_cpsycound_handoff_to_experiments.py"
MANIFEST_PATH = MANIFEST_OUTPUT_PATH
QA_PATH = QA_OUTPUT_PATH
STATUS_OUTPUT = STATUS_OUTPUT_PATH
CURRENT_MD_OUTPUT = CURRENT_MD_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--sync-experiments", action="store_true")
    parser.add_argument("--status-output", default=str(STATUS_OUTPUT))
    parser.add_argument("--current-md-output", default=str(CURRENT_MD_OUTPUT))
    return parser.parse_args()


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def build_current_md(summary: dict[str, Any]) -> str:
    shard_counts = summary.get("shard_patient_counts") or {}
    complete_gate_satisfied = summary.get("complete_gate_satisfied")
    if complete_gate_satisfied:
        completion_line = "- Completion gate: satisfied"
        next_step_line = "- Next step: optional final archival / release bookkeeping"
    else:
        completion_line = f"- Completion gate: not yet satisfied ({summary.get('completion_gap_summary')})"
        next_step_line = "- Next step: let shards continue, then rerun refresh; use --require-complete only when you expect full coverage"
    lines = [
        "# Current Status",
        "",
        f"- Updated: {summary.get('refreshed_at')}",
        f"- Dataset: {summary.get('dataset')}",
        f"- Status: {summary.get('status')}",
        f"- Covered cases: {summary.get('covered_case_count')} / {summary.get('source_total')}",
        f"- Remaining cases: {summary.get('remaining_case_count')}",
        f"- Coverage ratio: {summary.get('coverage_ratio')}",
        f"- Failure rows: {summary.get('failure_rows')}",
        f"- Shard 00 patient rows: {shard_counts.get('shard_00')}",
        f"- Shard 01 patient rows: {shard_counts.get('shard_01')}",
        f"- Canonical case count attempted: {summary.get('case_count_attempted')}",
        f"- All outputs share same case ids: {summary.get('all_outputs_share_same_case_ids')}",
        f"- Duplicate case-id counts: {summary.get('duplicate_case_id_counts')}",
        completion_line,
        next_step_line,
        "",
        "## Key Paths",
        "",
        f"- JSON status: {summary.get('status_output_path')}",
        f"- Manifest: {summary.get('manifest_path')}",
        f"- QA: {summary.get('qa_path')}",
        "",
        "## Next Commands",
        "",
        "Refresh snapshot:",
        "```bash",
        f"{PYTHON} {SCRIPT_DIR / 'refresh_anna_cpsycound_projection_snapshot.py'}",
        "```",
        "",
        "Strict complete gate:",
        "```bash",
        f"{PYTHON} {SCRIPT_DIR / 'refresh_anna_cpsycound_projection_snapshot.py'} --require-complete",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    merge = run([PYTHON, str(MERGE_SCRIPT), "--include-existing"])
    if merge.returncode != 0:
        sys.stderr.write(merge.stdout)
        sys.stderr.write(merge.stderr)
        return merge.returncode

    finalizer_cmd = [PYTHON, str(FINALIZER_SCRIPT)]
    if args.require_complete:
        finalizer_cmd.append("--require-complete")
    finalizer = run(finalizer_cmd)

    if not MANIFEST_PATH.exists() or not QA_PATH.exists():
        sys.stderr.write("Manifest or QA file missing after refresh.\n")
        if merge.stdout:
            sys.stderr.write(merge.stdout)
        if finalizer.stdout:
            sys.stderr.write(finalizer.stdout)
        if finalizer.stderr:
            sys.stderr.write(finalizer.stderr)
        return 1

    manifest = read_json(MANIFEST_PATH)
    qa = read_json(QA_PATH)
    covered_case_count = (qa.get("coverage_summary") or {}).get("covered_case_count")
    source_total = (qa.get("coverage_summary") or {}).get("source_total")
    remaining_case_count = None
    coverage_ratio = None
    if isinstance(covered_case_count, int) and isinstance(source_total, int) and source_total > 0:
        remaining_case_count = source_total - covered_case_count
        coverage_ratio = round(covered_case_count / source_total, 6)

    shard_patient_counts = {
        "shard_00": jsonl_count(SHARD_DIR / "patient_persona_profile.shard_00.jsonl"),
        "shard_01": jsonl_count(SHARD_DIR / "patient_persona_profile.shard_01.jsonl"),
    }

    summary = {
        "dataset": manifest.get("dataset"),
        "status": manifest.get("status"),
        "case_count_total": manifest.get("case_count_total"),
        "case_count_attempted": manifest.get("case_count_attempted"),
        "failure_rows": manifest.get("failure_rows"),
        "row_counts": qa.get("row_counts"),
        "unique_case_counts": qa.get("unique_case_counts"),
        "duplicate_case_id_counts": qa.get("duplicate_case_id_counts"),
        "all_outputs_share_same_case_ids": ((qa.get("case_id_alignment") or {}).get("all_outputs_share_same_case_ids")),
        "covered_case_count": covered_case_count,
        "source_total": source_total,
        "remaining_case_count": remaining_case_count,
        "coverage_ratio": coverage_ratio,
        "shard_patient_counts": shard_patient_counts,
        "manifest_path": str(MANIFEST_PATH),
        "qa_path": str(QA_PATH),
        "status_output_path": str(Path(args.status_output)),
        "current_md_output_path": str(Path(args.current_md_output)),
        "require_complete": args.require_complete,
        "sync_experiments_requested": args.sync_experiments,
        "finalizer_exit_code": finalizer.returncode,
        "complete_gate_satisfied": finalizer.returncode == 0 and manifest.get("status") == "complete",
        "completion_gap_summary": (
            f"{remaining_case_count} cases remaining"
            if manifest.get("status") != "complete" and remaining_case_count is not None
            else None
        ),
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(Path(args.status_output), summary)
    write_text(Path(args.current_md_output), build_current_md(summary))

    if args.sync_experiments:
        sync = run([PYTHON, str(SYNC_SCRIPT)])
        summary["sync_experiments_exit_code"] = sync.returncode
        if sync.stdout.strip():
            try:
                summary["sync_experiments_result"] = json.loads(sync.stdout)
            except json.JSONDecodeError:
                summary["sync_experiments_stdout"] = sync.stdout
        if sync.stderr.strip():
            summary["sync_experiments_stderr"] = sync.stderr
        write_json(Path(args.status_output), summary)
        write_text(Path(args.current_md_output), build_current_md(summary))
        if sync.returncode != 0:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return sync.returncode

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return finalizer.returncode


if __name__ == "__main__":
    raise SystemExit(main())
