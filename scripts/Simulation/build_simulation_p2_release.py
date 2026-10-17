#!/usr/bin/env python3
"""Build the Simulation Training P2 product release QA."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/simulation/platform/p2_release_v1"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    qa_paths = {
        "patient_mode_ui": ROOT / "outputs/simulation/platform/ui_qa_p2_regression/ui_qa.json",
        "therapist_mode_ui": ROOT / "outputs/simulation/platform/p2_mode_qa_v1/p2_mode_qa.json",
        "five_family_gate": ROOT / "outputs/simulation/platform/session_core_gate_5fam_v2_qwen8002_20260711/session_core_gate_summary.json",
    }
    missing = [str(path) for path in qa_paths.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"missing P2 evidence: {missing}")
    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    build = subprocess.run(
        ["npm", "run", "build"],
        cwd=ROOT / "apps/simulation_console",
        text=True,
        capture_output=True,
        check=False,
    )
    patient = load(qa_paths["patient_mode_ui"])
    therapist = load(qa_paths["therapist_mode_ui"])
    families = load(qa_paths["five_family_gate"])
    checks = {
        "patient_mode_desktop_mobile": patient.get("status") == "complete",
        "therapist_mode_real_qwen": therapist.get("status") == "complete",
        "foreground_message_under_500ms": therapist.get("checks", {}).get("user_message_visible_under_500ms") is True,
        "therapist_memory_deferred": therapist.get("checks", {}).get("deferred_memory_settled") is True,
        "instructor_annotation": therapist.get("checks", {}).get("instructor_annotation_saved") is True,
        "assignment_api_contract": "TrainingAssignmentRequest" in (ROOT / "services/simulation_api/app.py").read_text(encoding="utf-8"),
        "controlled_comparison_contract": "theraskill_response_comparison_v1" in (ROOT / "src/theraskill_simulation/training.py").read_text(encoding="utf-8"),
        "five_scenario_families": families.get("status") == "complete",
        "python_regression": tests.returncode == 0 and "Ran 19 tests" in (tests.stdout + tests.stderr),
        "frontend_build": build.returncode == 0,
    }
    report = {
        "schema_version": "theraskill_simulation_training_p2_release_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if all(checks.values()) else "failed",
        "checks": checks,
        "metrics": {
            "user_message_visible_ms": therapist.get("metrics", {}).get("user_message_visible_ms"),
            "python_tests": 19,
            "ui_viewports": len(patient.get("results") or []),
            "interactive_modes": 2,
        },
        "evidence": {key: str(path.resolve()) for key, path in qa_paths.items()},
    }
    output.mkdir(parents=True, exist_ok=True)
    path = output / "simulation_training_p2_release_qa.json"
    if path.exists() and not args.overwrite:
        raise SystemExit(f"output exists; pass --overwrite: {path}")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
