#!/usr/bin/env python3
"""Build the canonical release manifest and QA for patient persona v2."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMMON_DIR = PROJECT_ROOT / "scripts/_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import file_sha256, read_json, write_json  # noqa: E402


DEFAULT_RELEASE_DIR = PROJECT_ROOT / "outputs/simulation/personas/release_v2"
REQUIRED_ARTIFACTS = {
    "persona_manifest": PROJECT_ROOT / "outputs/simulation/personas/patient_persona_v2.manifest.json",
    "persona_qa": PROJECT_ROOT / "outputs/simulation/personas/patient_persona_v2.qa.json",
    "persona_summary": PROJECT_ROOT / "outputs/simulation/personas/patient_persona_v2.summary.json",
    "enrichment_manifest": PROJECT_ROOT / "outputs/simulation/personas/enrichment/patient_persona_v2.enrichment.manifest.json",
    "enrichment_qa": PROJECT_ROOT / "outputs/simulation/personas/enrichment/patient_persona_v2.enrichment.qa.json",
    "compiler_qa": PROJECT_ROOT / "outputs/simulation/personas/patient_persona_compiler.qa.json",
    "runtime_gate_qa": PROJECT_ROOT / "outputs/simulation/benchmark/patient_persona_v2_runtime_gate/runtime_gate_qa.json",
    "long_horizon_qa": PROJECT_ROOT / "outputs/simulation/benchmark/patient_persona_v2_long_horizon_gate/long_horizon_qa.json",
    "ui_qa": PROJECT_ROOT / "outputs/simulation/platform/ui_qa_v1/ui_qa.json",
    "persona_role_card_ui_qa": PROJECT_ROOT
    / "outputs/simulation/platform/persona_role_card_ui_qa_v2/ui_qa.json",
    "schema_manifest": PROJECT_ROOT / "schemas/simulation/patient_persona_v2_schemas.manifest.json",
    "schema_qa": PROJECT_ROOT / "schemas/simulation/patient_persona_v2_schemas.qa.json",
    "runtime_schema_qa": PROJECT_ROOT / "outputs/simulation/schemas/patient_runtime_schemas_qa.json",
}
CODE_ARTIFACTS = {
    "persona_compiler": PROJECT_ROOT / "src/theraskill_simulation/persona_compiler.py",
    "simulation_service": PROJECT_ROOT / "src/theraskill_simulation/service.py",
    "patient_engines": PROJECT_ROOT / "src/theraskill_simulation/engines.py",
    "enrichment_runner": PROJECT_ROOT / "scripts/Simulation/run_patient_persona_v2_enrichment.py",
    "materializer": PROJECT_ROOT / "scripts/Simulation/materialize_patient_persona_v2_enrichment.py",
    "runtime_gate_runner": PROJECT_ROOT / "scripts/Simulation/run_patient_persona_runtime_gate.py",
    "long_horizon_runner": PROJECT_ROOT / "scripts/Simulation/run_patient_persona_long_horizon_gate.py",
    "enrichment_prompt": PROJECT_ROOT / "prompts/Simulation/patient_persona_enrichment_zh.md",
    "verification_prompt": PROJECT_ROOT / "prompts/Simulation/patient_persona_enrichment_verify_zh.md",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", default=str(DEFAULT_RELEASE_DIR))
    parser.add_argument("--test-report", required=True)
    parser.add_argument("--frontend-build-report", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def status_of(path: Path) -> str | None:
    if not path.exists():
        return None
    payload = read_json(path)
    return str(payload.get("status")) if isinstance(payload, dict) else None


def main() -> int:
    args = parse_args()
    release_dir = Path(args.release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = release_dir / "patient_persona_v2_release_manifest.json"
    qa_path = release_dir / "patient_persona_v2_release_qa.json"
    if not args.overwrite and (manifest_path.exists() or qa_path.exists()):
        raise FileExistsError("release artifacts exist; pass --overwrite")

    missing = [name for name, path in REQUIRED_ARTIFACTS.items() if not path.exists()]
    statuses = {name: status_of(path) for name, path in REQUIRED_ARTIFACTS.items()}
    payloads = {
        name: read_json(path) if path.exists() else {}
        for name, path in REQUIRED_ARTIFACTS.items()
    }
    persona_manifest = payloads["persona_manifest"]
    persona_qa = read_json(REQUIRED_ARTIFACTS["persona_qa"]) if REQUIRED_ARTIFACTS["persona_qa"].exists() else {}
    enrichment_qa = read_json(REQUIRED_ARTIFACTS["enrichment_qa"]) if REQUIRED_ARTIFACTS["enrichment_qa"].exists() else {}
    compiler_qa = payloads["compiler_qa"]
    runtime_gate_qa = payloads["runtime_gate_qa"]
    long_horizon_qa = payloads["long_horizon_qa"]
    test_report_path = Path(args.test_report)
    frontend_report_path = Path(args.frontend_build_report)
    test_report = read_json(test_report_path) if test_report_path.exists() else {}
    frontend_report = read_json(frontend_report_path) if frontend_report_path.exists() else {}
    checks = {
        "required_artifacts_present": not missing,
        "persona_r2_complete": statuses.get("persona_qa") == "complete"
        and persona_qa.get("migration_stage") == "R2_verified_llm_enrichment",
        "persona_artifacts_published": statuses.get("persona_manifest") == "complete"
        and persona_manifest.get("migration_stage") == "R2_verified_llm_enrichment"
        and (persona_manifest.get("output") or {}).get("published") is True,
        "persona_count_3134": (persona_qa.get("counts") or {}).get("personas") == 3134,
        "persona_schema_valid_3134": (persona_qa.get("counts") or {}).get("schema_valid") == 3134,
        "enrichment_complete_3134": statuses.get("enrichment_qa") == "complete"
        and enrichment_qa.get("unique_case_count") == 3134
        and enrichment_qa.get("failure_count") == 0,
        "compiler_gate_complete": statuses.get("compiler_qa") == "complete",
        "compiler_gate_full_coverage": compiler_qa.get("persona_count") == 3134
        and compiler_qa.get("expected_persona_count") == 3134
        and compiler_qa.get("issues_count") == 0,
        "runtime_gate_complete": statuses.get("runtime_gate_qa") == "complete",
        "runtime_gate_full_coverage": runtime_gate_qa.get("expected_arm_count") == 35
        and runtime_gate_qa.get("completed_arm_count") == 35
        and runtime_gate_qa.get("issues_count") == 0,
        "long_horizon_gate_complete": statuses.get("long_horizon_qa") == "complete",
        "long_horizon_gate_full_coverage": long_horizon_qa.get("chain_count_expected") == 16
        and long_horizon_qa.get("chain_count_completed") == 16
        and long_horizon_qa.get("patient_turn_count_completed") == 480
        and long_horizon_qa.get("issues_count") == 0,
        "ui_qa_complete": statuses.get("ui_qa") == "complete",
        "persona_role_card_ui_qa_complete": statuses.get("persona_role_card_ui_qa") == "complete",
        "schema_qa_complete": statuses.get("schema_qa") == "complete",
        "runtime_schema_qa_complete": statuses.get("runtime_schema_qa") == "complete",
        "python_tests_complete": test_report.get("status") == "complete",
        "frontend_build_complete": frontend_report.get("status") == "complete",
    }
    status = "complete" if all(checks.values()) else "incomplete"
    created_at = datetime.now(timezone.utc).isoformat()
    artifacts: dict[str, dict[str, Any]] = {}
    for name, path in {**REQUIRED_ARTIFACTS, **CODE_ARTIFACTS}.items():
        artifacts[name] = {
            "path": str(path),
            "exists": path.exists(),
            "sha256": file_sha256(path),
        }
    artifacts["test_report"] = {
        "path": str(test_report_path), "exists": test_report_path.exists(), "sha256": file_sha256(test_report_path)
    }
    artifacts["frontend_build_report"] = {
        "path": str(frontend_report_path), "exists": frontend_report_path.exists(), "sha256": file_sha256(frontend_report_path)
    }
    manifest = {
        "schema_version": "theraskill_patient_persona_v2_release_manifest_v1",
        "created_at": created_at,
        "status": status,
        "release_id": "patient_persona_v2",
        "persona_count": (persona_qa.get("counts") or {}).get("personas"),
        "model": "Qwen3-235B-FP8",
        "model_endpoint": "http://127.0.0.1:8002/v1",
        "artifacts": artifacts,
    }
    qa = {
        "schema_version": "theraskill_patient_persona_v2_release_qa_v1",
        "created_at": created_at,
        "status": status,
        "checks": checks,
        "missing_artifacts": missing,
        "artifact_statuses": statuses,
        "issues": [name for name, passed in checks.items() if not passed],
        "issues_count": sum(not passed for passed in checks.values()),
    }
    write_json(manifest_path, manifest, overwrite=True)
    write_json(qa_path, qa, overwrite=True)
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
