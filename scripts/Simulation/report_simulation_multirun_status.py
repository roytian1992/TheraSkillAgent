#!/usr/bin/env python3
"""Report live status for active Simulation extraction runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_json
from simulation_surface_registry import REPO_ROOT, SIMULATION_FINALIZE_RUN_SPECS

DEFAULT_RUNS = tuple(SIMULATION_FINALIZE_RUN_SPECS.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def tmux_session_exists(name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def tmux_session_created_epoch(name: str) -> int | None:
    result = subprocess.run(
        ["tmux", "display-message", "-p", "-t", name, "#{session_created}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text.isdigit():
        return None
    return int(text)


def run_status(spec) -> dict[str, Any]:
    run_dir = REPO_ROOT / spec.run_dir
    shard_dir = run_dir / "shards"
    manifest_path = shard_dir / "manifest.json"
    shard_manifest = read_json(manifest_path) if manifest_path.exists() else {}

    output_rows = 0
    output_files = 0
    for path in sorted(shard_dir.glob(f"{spec.shard_prefix}*.jsonl")):
        output_rows += line_count(path)
        output_files += 1

    failure_rows = 0
    failure_files = 0
    for path in sorted(shard_dir.glob("failures.shard_*.jsonl")):
        failure_rows += line_count(path)
        failure_files += 1

    qa_status_counts: dict[str, int] = {}
    qa_count = 0
    for path in sorted(shard_dir.glob("qa.shard_*.json")):
        qa = read_json(path)
        status = str(qa.get("status") or "unknown")
        qa_status_counts[status] = qa_status_counts.get(status, 0) + 1
        qa_count += 1

    running = 0
    stopped = 0
    session_created_epochs: list[int] = []
    for idx in range(32):
        name = f"{spec.tmux_prefix}{idx:02d}"
        if tmux_session_exists(name):
            running += 1
            created_epoch = tmux_session_created_epoch(name)
            if created_epoch is not None:
                session_created_epochs.append(created_epoch)
        else:
            stopped += 1

    remaining = shard_manifest.get("remaining_candidate_count")
    progress_ratio = (output_rows / remaining) if isinstance(remaining, int) and remaining > 0 else None
    launch_epoch = min(session_created_epochs) if session_created_epochs else None
    latest_output_mtime = None
    output_mtimes = [path.stat().st_mtime for path in sorted(shard_dir.glob(f"{spec.shard_prefix}*.jsonl"))]
    if output_mtimes:
        latest_output_mtime = max(output_mtimes)
    now_ts = datetime.now().timestamp()
    elapsed_min_since_first_output = (
        (now_ts - launch_epoch) / 60.0 if launch_epoch is not None and now_ts >= launch_epoch else None
    )
    avg_rows_per_min = (
        (output_rows / elapsed_min_since_first_output)
        if elapsed_min_since_first_output is not None and elapsed_min_since_first_output > 0
        else None
    )
    eta_min = (
        ((remaining - output_rows) / avg_rows_per_min)
        if isinstance(remaining, int) and avg_rows_per_min is not None and avg_rows_per_min > 0 and remaining >= output_rows
        else None
    )

    return {
        "tag": spec.tag,
        "name": spec.name,
        "run_dir": str(run_dir),
        "remaining_candidate_count": remaining,
        "output_rows": output_rows,
        "failure_rows": failure_rows,
        "output_files": output_files,
        "failure_files": failure_files,
        "qa_count": qa_count,
        "qa_status_counts": qa_status_counts,
        "running_sessions": running,
        "stopped_sessions": stopped,
        "progress_ratio": progress_ratio,
        "launch_time": datetime.fromtimestamp(launch_epoch).isoformat() if launch_epoch is not None else None,
        "last_output_mtime": datetime.fromtimestamp(latest_output_mtime).isoformat() if latest_output_mtime is not None else None,
        "elapsed_min_since_launch": elapsed_min_since_first_output,
        "avg_rows_per_min": avg_rows_per_min,
        "eta_min": eta_min,
    }


def main() -> int:
    args = parse_args()
    payload = {"runs": [run_status(spec) for spec in DEFAULT_RUNS]}
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
