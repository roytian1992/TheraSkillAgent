#!/usr/bin/env python3
"""Audit and materialize the Simulation Platform v1 release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs/simulation/platform/release_v1"
CONTRACT_QA = PROJECT_ROOT / "outputs/simulation/platform/contracts_v1/contract_qa.json"
CORE_GATE = (
    PROJECT_ROOT
    / "outputs/simulation/platform/session_core_gate_5fam_v2_qwen8002_20260711/"
    "session_core_gate_summary.json"
)
UI_QA = PROJECT_ROOT / "outputs/simulation/platform/ui_qa_v1/ui_qa.json"
HERMES_GATE = (
    PROJECT_ROOT
    / "outputs/simulation/platform/hermes_platform_skill_validation_v1_qwen8002_20260711/"
    "hermes_platform_skill_validation.json"
)
RESEARCH_GATE = (
    PROJECT_ROOT
    / "outputs/simulation/platform/dual_research_5fam_v1_qwen8002_20260711/"
    "research_summary.json"
)
FEEDBACK_DATABASE = PROJECT_ROOT / "work/simulation_platform/api_smoke.sqlite"
HERMES_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")) / "skills/research"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--api-url", default="http://127.0.0.1:8876")
    parser.add_argument("--app-url", default="http://127.0.0.1:5173")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists; pass --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-8000:],
        "stderr_tail": result.stderr[-8000:],
    }


def http_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def feedback_count() -> int:
    if not FEEDBACK_DATABASE.exists():
        return 0
    with sqlite3.connect(str(FEEDBACK_DATABASE)) as connection:
        try:
            return int(connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0])
        except sqlite3.OperationalError:
            return 0


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    required_inputs = [CONTRACT_QA, CORE_GATE, UI_QA, HERMES_GATE, RESEARCH_GATE]
    missing_inputs = [str(path) for path in required_inputs if not path.is_file()]
    if missing_inputs:
        raise SystemExit(f"missing release evidence: {missing_inputs}")

    contract = read_json(CONTRACT_QA)
    core = read_json(CORE_GATE)
    ui = read_json(UI_QA)
    hermes = read_json(HERMES_GATE)
    research = read_json(RESEARCH_GATE)
    unit_tests = run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        PROJECT_ROOT,
    )
    frontend_build = run(["npm", "run", "build"], PROJECT_ROOT / "apps/simulation_console")
    skill_validation = run(
        [
            sys.executable,
            str(Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py"),
            str(SKILLS_ROOT / "theraskill-simulation"),
        ],
        PROJECT_ROOT,
    )
    hermes_list = run([str(HERMES_ROOT / "hermes"), "skills", "list"], HERMES_ROOT)
    health = http_json(f"{args.api_url}/health")
    proxied_health = http_json(f"{args.app_url}/api/health")
    runtime_config = http_json(f"{args.api_url}/runtime-config")
    feedback_rows = feedback_count()
    expected_skills = {
        "theraskill-simulation",
        "theraskill-patient-simulator",
        "theraskill-therapist-simulator",
        "theraskill-dual-session-simulator",
        "theraskill-simulation-reviewer",
    }
    installed_skills = {
        name
        for name in expected_skills
        if (SKILLS_ROOT / name / "SKILL.md").is_file()
    }
    hermes_reports_all_enabled = (
        hermes_list["returncode"] == 0
        and "5 hub-installed" not in hermes_list["stdout_tail"]
        and "0 disabled" in hermes_list["stdout_tail"]
    )
    low_level_versions = {}
    for name in sorted(expected_skills - {"theraskill-simulation"}):
        text = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        version_line = next((line for line in text.splitlines() if line.startswith("version:")), "")
        low_level_versions[name] = version_line.split(":", 1)[-1].strip()

    checks = {
        "p0_contract_complete": contract.get("status") == "complete"
        and contract.get("outputs", {}).get("schema_count") == 7,
        "p1_core_gate_complete": core.get("status") == "complete"
        and core.get("counts", {}).get("passed_cases") == 5
        and core.get("counts", {}).get("completed_agent_turns") == 15,
        "p2_api_healthy": health.get("status") == "ok"
        and runtime_config.get("llm_engine_enabled") is True
        and runtime_config.get("therapist_engine_enabled") is True
        and runtime_config.get("reviewer_engine_enabled") is True,
        "python_regression_pass": unit_tests["returncode"] == 0
        and "Ran " in (unit_tests["stderr_tail"] + unit_tests["stdout_tail"])
        and "\nOK\n" in (unit_tests["stderr_tail"] + unit_tests["stdout_tail"]),
        "p3_frontend_build_pass": frontend_build["returncode"] == 0
        and (PROJECT_ROOT / "apps/simulation_console/dist/index.html").is_file(),
        "p3_same_origin_api_proxy": proxied_health.get("status") == "ok",
        "p3_ui_qa_complete": ui.get("status") == "complete"
        and len(ui.get("results") or []) == 2
        and all(row.get("status") == "pass" for row in ui.get("results") or []),
        "p4_high_level_skill_valid": skill_validation["returncode"] == 0
        and hermes.get("status") == "complete",
        "p4_all_skills_enabled": hermes_reports_all_enabled
        and installed_skills == expected_skills,
        "p4_low_level_skills_repositioned": set(low_level_versions.values()) == {"1.2.0"},
        "p5_research_gate_complete": research.get("status") == "complete"
        and research.get("counts", {}).get("passed_tasks") == 5
        and research.get("counts", {}).get("completed_dual_cycles") == 15
        and research.get("review_gate_distribution") == {"pass": 5},
        "p5_failure_replay_clean": read_json(
            RESEARCH_GATE.parent / "failure_replay.json"
        ).get("failed_count")
        == 0,
        "p6_feedback_loop_exercised": feedback_rows >= 2,
        "docs_present": all(
            path.is_file()
            for path in (
                PROJECT_ROOT / "docs/simulation_platform_product_architecture_plan.zh.md",
                PROJECT_ROOT / "docs/simulation_platform_quickstart.zh.md",
                PROJECT_ROOT / "docs/simulation_platform_session_contract.zh.md",
            )
        ),
    }
    evidence_files = {
        "contract_qa": CONTRACT_QA,
        "session_core_gate": CORE_GATE,
        "ui_qa": UI_QA,
        "hermes_skill_gate": HERMES_GATE,
        "dual_research_gate": RESEARCH_GATE,
        "paper_benchmark_export": RESEARCH_GATE.parent / "paper_benchmark_export.jsonl",
        "failure_replay": RESEARCH_GATE.parent / "failure_replay.json",
    }
    manifest = {
        "schema_version": "theraskill_simulation_platform_release_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_id": "theraskill_simulation_platform_v1_20260711",
        "project_root": str(PROJECT_ROOT),
        "files": {
            key: {
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for key, path in evidence_files.items()
        },
        "runtime_urls": {
        "api": args.api_url,
        "training_app": "http://127.0.0.1:5173/",
        },
    }
    qa = {
        "schema_version": "theraskill_simulation_platform_release_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_id": manifest["release_id"],
        "status": "complete" if all(checks.values()) else "failed",
        "checks": checks,
        "counts": {
            "contract_schemas": contract.get("outputs", {}).get("schema_count"),
            "core_gate_cases": core.get("counts", {}).get("passed_cases"),
            "core_gate_patient_turns": core.get("counts", {}).get("completed_agent_turns"),
            "ui_viewports": len(ui.get("results") or []),
            "hermes_skills": len(installed_skills),
            "dual_research_cases": research.get("counts", {}).get("passed_tasks"),
            "dual_research_cycles": research.get("counts", {}).get("completed_dual_cycles"),
            "persisted_feedback_rows": feedback_rows,
        },
        "commands": {
            "unit_tests": unit_tests,
            "frontend_build": frontend_build,
            "skill_validation": skill_validation,
            "hermes_list": hermes_list,
        },
        "api": {
            "health": health,
            "proxied_health": proxied_health,
            "runtime_config": runtime_config,
        },
        "low_level_skill_versions": low_level_versions,
        "manifest_path": str((output_dir / "simulation_platform_release_manifest.json").resolve()),
    }
    write_json(
        output_dir / "simulation_platform_release_manifest.json",
        manifest,
        overwrite=args.overwrite,
    )
    write_json(
        output_dir / "simulation_platform_release_qa.json",
        qa,
        overwrite=args.overwrite,
    )
    summary_lines = [
        "# TheraSkill Simulation Platform v1 Release",
        "",
        f"- status: `{qa['status']}`",
        f"- release_id: `{qa['release_id']}`",
        f"- contract schemas: `{qa['counts']['contract_schemas']}`",
        f"- patient core gate: `{qa['counts']['core_gate_cases']}/5` cases, `{qa['counts']['core_gate_patient_turns']}` turns",
        f"- dual research gate: `{qa['counts']['dual_research_cases']}/5` cases, `{qa['counts']['dual_research_cycles']}` cycles",
        f"- Hermes skills: `{qa['counts']['hermes_skills']}`",
        f"- UI viewports: `{qa['counts']['ui_viewports']}`",
        f"- persisted feedback rows: `{qa['counts']['persisted_feedback_rows']}`",
        "",
        "All checks:" if qa["status"] == "complete" else "Failed checks:",
    ]
    for key, value in checks.items():
        if qa["status"] == "complete" or not value:
            summary_lines.append(f"- `{key}`: `{value}`")
    summary_path = output_dir / "simulation_platform_release_summary.md"
    if summary_path.exists() and not args.overwrite:
        raise FileExistsError(summary_path)
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0 if qa["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
