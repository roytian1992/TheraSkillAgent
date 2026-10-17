#!/usr/bin/env python3
"""Run the full conditioned human-rating refresh chain."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from simulation_surface_registry import (
    BUILD_PATIENT_SIMULATOR_EVALUATION_PACKAGE_RELPATH,
    COMPILE_CONDITIONED_HUMAN_RATING_RESULTS_RELPATH,
    FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR,
    SYNC_CONDITIONED_HUMAN_RATING_SHEET_RELPATH,
    VALIDATE_CONDITIONED_HUMAN_RATING_SHEET_RELPATH,
    project_script_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADJUDICATION_DIR = FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR
SYNC_SCRIPT = project_script_path(SYNC_CONDITIONED_HUMAN_RATING_SHEET_RELPATH)
COMPILE_SCRIPT = project_script_path(COMPILE_CONDITIONED_HUMAN_RATING_RESULTS_RELPATH)
VALIDATE_SCRIPT = project_script_path(VALIDATE_CONDITIONED_HUMAN_RATING_SHEET_RELPATH)
PACKAGE_SCRIPT = project_script_path(BUILD_PATIENT_SIMULATOR_EVALUATION_PACKAGE_RELPATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adjudication-dir", default=str(DEFAULT_ADJUDICATION_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def run_step(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    args = parse_args()
    adjudication_dir = Path(args.adjudication_dir)
    overwrite_flags = ["--overwrite"] if args.overwrite else []

    run_step(
        [
            args.python,
            str(SYNC_SCRIPT),
            "--adjudication-dir",
            str(adjudication_dir),
        ]
    )
    run_step(
        [
            args.python,
            str(COMPILE_SCRIPT),
            "--adjudication-dir",
            str(adjudication_dir),
            *overwrite_flags,
        ]
    )
    run_step(
        [
            args.python,
            str(VALIDATE_SCRIPT),
            "--adjudication-dir",
            str(adjudication_dir),
            *overwrite_flags,
        ]
    )
    run_step(
        [
            args.python,
            str(PACKAGE_SCRIPT),
        ]
    )


if __name__ == "__main__":
    main()
