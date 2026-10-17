#!/usr/bin/env python3
"""Run reproducible Python-test and frontend-build checks for persona v2 release."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMON_DIR = PROJECT_ROOT / "scripts/_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import write_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    release_dir = PROJECT_ROOT / "outputs/simulation/personas/release_v2"
    parser.add_argument("--test-report", default=str(release_dir / "python_test_report.json"))
    parser.add_argument("--frontend-build-report", default=str(release_dir / "frontend_build_report.json"))
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> dict:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "schema_version": "theraskill_release_command_check_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if completed.returncode == 0 else "failed",
        "command": command,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": completed.stdout[-12000:],
        "stderr_tail": completed.stderr[-12000:],
    }


def main() -> int:
    args = parse_args()
    tests = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], PROJECT_ROOT)
    frontend = run(["npm", "run", "build"], PROJECT_ROOT / "apps/simulation_console")
    write_json(args.test_report, tests, overwrite=True)
    write_json(args.frontend_build_report, frontend, overwrite=True)
    print(json.dumps({"python_tests": tests["status"], "frontend_build": frontend["status"]}, indent=2))
    return 0 if tests["status"] == frontend["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
