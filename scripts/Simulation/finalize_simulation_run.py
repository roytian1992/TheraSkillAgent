#!/usr/bin/env python3
"""Finalize a Simulation extraction run after shard workers stop.

Historical finalize helper preserved for extraction-lineage operations; not a benchmark entry point.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import read_json
from simulation_surface_registry import REPO_ROOT, SIMULATION_FINALIZE_RUN_SPECS, project_script_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", choices=sorted(SIMULATION_FINALIZE_RUN_SPECS), required=True)
    parser.add_argument("--force", action="store_true", help="Allow finalize attempt even if tmux sessions are still running.")
    parser.add_argument("--allow-partial", action="store_true", help="Do not fail the command when projection QA remains partial.")
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


def run_python(project_root: Path, script_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script_path)],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )


def shard_snapshot(project_root: Path, run_dir: str, shard_prefix: str) -> dict[str, Any]:
    shard_dir = project_root / run_dir / "shards"
    manifest_path = shard_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    output_rows = 0
    failure_rows = 0
    for path in sorted(shard_dir.glob(f"{shard_prefix}*.jsonl")):
        output_rows += line_count(path)
    for path in sorted(shard_dir.glob("failures.shard_*.jsonl")):
        failure_rows += line_count(path)
    return {
        "manifest": manifest,
        "output_rows": output_rows,
        "failure_rows": failure_rows,
        "shard_dir": str(shard_dir),
    }


def main() -> int:
    args = parse_args()
    project_root = REPO_ROOT
    spec = SIMULATION_FINALIZE_RUN_SPECS[args.tag]

    running_sessions = []
    for idx in range(32):
        name = f"{spec.tmux_prefix}{idx:02d}"
        if tmux_session_exists(name):
            running_sessions.append(name)

    snapshot_before = shard_snapshot(project_root, spec.run_dir, spec.shard_prefix)
    if running_sessions and not args.force:
        payload = {
            "tag": args.tag,
            "status": "not_ready_running_sessions",
            "running_session_count": len(running_sessions),
            "running_sessions_sample": running_sessions[:5],
            "snapshot_before": snapshot_before,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    merge_result = run_python(project_root, project_script_path(spec.merge_script_relpath))
    projection_result = run_python(project_root, project_script_path(spec.projection_script_relpath))

    projection_manifest_path = project_root / spec.projection_manifest
    projection_qa_path = project_root / spec.projection_qa
    projection_manifest = read_json(projection_manifest_path) if projection_manifest_path.exists() else None
    projection_qa = read_json(projection_qa_path) if projection_qa_path.exists() else None
    canonical_rows = line_count(project_root / spec.canonical_output)

    payload = {
        "tag": args.tag,
        "status": "complete" if (projection_manifest or {}).get("status") == "complete" else "partial",
        "running_session_count": len(running_sessions),
        "snapshot_before": snapshot_before,
        "merge": {
            "returncode": merge_result.returncode,
            "stdout": merge_result.stdout.strip(),
            "stderr": merge_result.stderr.strip(),
        },
        "projection": {
            "returncode": projection_result.returncode,
            "stdout": projection_result.stdout.strip(),
            "stderr": projection_result.stderr.strip(),
        },
        "projection_manifest": projection_manifest,
        "projection_qa": projection_qa,
        "canonical_output_row_count": canonical_rows,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if payload["status"] != "complete" and not args.allow_partial:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
