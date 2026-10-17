#!/usr/bin/env python3
"""Watch a Simulation shard run and finalize it automatically after workers stop.

Historical finalize/watch helper preserved for extraction-lineage operations; not a benchmark entry point.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from simulation_surface_registry import (
    FINALIZE_SIMULATION_RUN_RELPATH,
    FINALIZE_WATCH_HELPERS,
    REPORT_SIMULATION_MULTIRUN_STATUS_RELPATH,
    REPO_ROOT,
    SIMULATION_FINALIZE_RUN_SPECS,
    WATCH_AND_FINALIZE_SIMULATION_RUN_RELPATH,
    project_script_path,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = REPO_ROOT
STATUS_SCRIPT = project_script_path(REPORT_SIMULATION_MULTIRUN_STATUS_RELPATH)
FINALIZE_SCRIPT = project_script_path(FINALIZE_SIMULATION_RUN_RELPATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", choices=sorted(SIMULATION_FINALIZE_RUN_SPECS), required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--max-polls", type=int, help="Optional safety bound for polling attempts.")
    return parser.parse_args()


def run_json(cmd: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def run_finalize(tag: str, allow_partial: bool) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(FINALIZE_SCRIPT), "--tag", tag]
    if allow_partial:
        cmd.append("--allow-partial")
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def select_run(payload: dict[str, Any], tag: str) -> dict[str, Any]:
    for row in payload.get("runs") or []:
        if row.get("tag") == tag:
            return row
    raise KeyError(f"tag not found in status payload: {tag}")


def main() -> int:
    args = parse_args()
    polls = 0

    # Keep an explicit code-level link between this watcher and the finalize/watch
    # layer defined in the shared Simulation surface registry.
    if WATCH_AND_FINALIZE_SIMULATION_RUN_RELPATH.name not in FINALIZE_WATCH_HELPERS:
        raise RuntimeError("watch helper is missing from FINALIZE_WATCH_HELPERS registry")

    while True:
        polls += 1
        snapshot = select_run(
            run_json([sys.executable, str(STATUS_SCRIPT)]),
            args.tag,
        )
        status_line = {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "tag": args.tag,
            "running_sessions": snapshot.get("running_sessions"),
            "output_rows": snapshot.get("output_rows"),
            "failure_rows": snapshot.get("failure_rows"),
            "avg_rows_per_min": snapshot.get("avg_rows_per_min"),
            "eta_min": snapshot.get("eta_min"),
        }
        print(json.dumps(status_line, ensure_ascii=False), flush=True)

        if int(snapshot.get("running_sessions") or 0) == 0:
            result = run_finalize(args.tag, args.allow_partial)
            if result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
            return result.returncode

        if args.max_polls is not None and polls >= args.max_polls:
            print(
                json.dumps(
                    {
                        "tag": args.tag,
                        "status": "max_polls_reached_without_finalize",
                        "max_polls": args.max_polls,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        time.sleep(max(1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
