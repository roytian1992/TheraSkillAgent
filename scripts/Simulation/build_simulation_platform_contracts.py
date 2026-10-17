#!/usr/bin/env python3
"""Build P0 simulation platform contracts and validate released artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from theraskill_simulation.artifact_mapping import map_validation_report  # noqa: E402
from theraskill_simulation.contracts import (  # noqa: E402
    ActorRole,
    PrivatePatientState,
    PrivateTherapistState,
    PublicSessionView,
    RoleAssignment,
    ScenarioPublicView,
    SessionEvent,
    SessionFeedback,
    SessionMode,
    SessionSnapshot,
    SimulationSession,
)
from theraskill_simulation.lifecycle import ALLOWED_TRANSITIONS  # noqa: E402
from theraskill_simulation.visibility import (  # noqa: E402
    ExportScope,
    ROLE_EXPORT_SCOPE,
    VISIBILITY_MATRIX,
    export_snapshot,
    find_forbidden_public_fields,
)

DEFAULT_EVIDENCE_ROOT = (
    PROJECT_ROOT
    / "outputs/simulation/hermes_validation/"
    "simulation_skill_delivery_gate_5fam_v2_grounded_8002_20260711"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs/simulation/platform/contracts_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def write_json(path: Path, value: Any, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists; pass --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    evidence_root = Path(args.evidence_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    report_paths = sorted((evidence_root / "cases").glob("*/simulation_skill_validation_report.json"))
    if not report_paths:
        raise SystemExit(f"no validation reports found under {evidence_root}")

    schema_models = {
        "simulation_session.schema.json": SimulationSession,
        "session_event.schema.json": SessionEvent,
        "session_snapshot.schema.json": SessionSnapshot,
        "public_session_view.schema.json": PublicSessionView,
        "private_patient_state.schema.json": PrivatePatientState,
        "private_therapist_state.schema.json": PrivateTherapistState,
        "session_feedback.schema.json": SessionFeedback,
    }
    for filename, model in schema_models.items():
        write_json(output_dir / "schemas" / filename, model.model_json_schema(), overwrite=args.overwrite)

    role_visibility = {
        "schema_version": "theraskill_simulation_role_visibility_matrix_v1",
        "role_default_scope": {
            role.value: scope.value for role, scope in ROLE_EXPORT_SCOPE.items()
        },
        "scope_fields": {
            scope.value: sorted(fields) for scope, fields in VISIBILITY_MATRIX.items()
        },
    }
    lifecycle = {
        "schema_version": "theraskill_simulation_lifecycle_v1",
        "allowed_transitions": {
            status.value: sorted(target.value for target in targets)
            for status, targets in ALLOWED_TRANSITIONS.items()
        },
    }
    write_json(output_dir / "role_visibility_matrix.json", role_visibility, overwrite=args.overwrite)
    write_json(output_dir / "lifecycle_state_machine.json", lifecycle, overwrite=args.overwrite)

    shared_scenario = ScenarioPublicView(
        scenario_id="scenario::contract_example",
        scenario_family="supportive_exploration",
    )
    mode_roles = {
        SessionMode.HUMAN_THERAPIST_VS_PATIENT: RoleAssignment(
            human_roles=[ActorRole.HUMAN_THERAPIST],
            agent_roles=[ActorRole.PATIENT_AGENT],
        ),
        SessionMode.HUMAN_CLIENT_ROLE_VS_THERAPIST: RoleAssignment(
            human_roles=[ActorRole.HUMAN_CLIENT_ROLE],
            agent_roles=[ActorRole.THERAPIST_AGENT],
        ),
        SessionMode.DUAL_AGENT_RESEARCH: RoleAssignment(
            agent_roles=[ActorRole.PATIENT_AGENT, ActorRole.THERAPIST_AGENT]
        ),
        SessionMode.REVIEW_ONLY: RoleAssignment(
            agent_roles=[ActorRole.REVIEWER_AGENT]
        ),
    }
    mode_examples = {
        mode.value: SimulationSession(
            session_id=f"sim_contract_{mode.value}",
            mode=mode,
            scenario=shared_scenario,
            role_assignment=roles,
            runtime_version="contract_v1",
            knowledge_release_id="contract_example",
        ).model_dump(mode="json")
        for mode, roles in mode_roles.items()
    }
    write_json(
        output_dir / "session_mode_contract_examples.json",
        {
            "schema_version": "theraskill_simulation_session_mode_examples_v1",
            "examples": mode_examples,
        },
        overwrite=args.overwrite,
    )

    mapping_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    family_counts: Counter[str] = Counter()
    for report_path in report_paths:
        try:
            snapshot = map_validation_report(report_path, project_root=PROJECT_ROOT)
            family = snapshot.session.scenario.scenario_family
            family_counts[family] += 1
            public_payload = export_snapshot(snapshot, ExportScope.PUBLIC)
            leakage = find_forbidden_public_fields(public_payload)
            missing_artifacts = [
                key
                for key, ref in snapshot.session.source_artifacts.items()
                if not Path(ref.path).is_file()
            ]
            if leakage:
                issues.append({"report": str(report_path), "type": "public_field_leakage", "paths": leakage})
            if missing_artifacts:
                issues.append({"report": str(report_path), "type": "missing_artifacts", "keys": missing_artifacts})
            mapping_rows.append(
                {
                    "source_report": str(report_path.resolve()),
                    "session_id": snapshot.session.session_id,
                    "scenario_id": snapshot.session.scenario.scenario_id,
                    "scenario_family": family,
                    "status": snapshot.session.status,
                    "transcript_turn_count": len(snapshot.public_view.transcript),
                    "source_artifact_count": len(snapshot.session.source_artifacts),
                    "source_artifacts": {
                        key: ref.model_dump(mode="json")
                        for key, ref in snapshot.session.source_artifacts.items()
                    },
                    "snapshot_checksum": snapshot.checksum,
                    "public_leakage_findings": leakage,
                    "mapped_snapshot": snapshot.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            issues.append(
                {
                    "report": str(report_path),
                    "type": "mapping_error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    expected_families = {
        "supportive_exploration",
        "cognitive_reframing_probe",
        "rupture_and_repair",
        "risk_escalation_and_handoff",
        "rebt_belief_work",
    }
    missing_families = sorted(expected_families - set(family_counts))
    if missing_families:
        issues.append({"type": "missing_scenario_families", "families": missing_families})

    qa = {
        "schema_version": "theraskill_simulation_platform_contract_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not issues else "failed",
        "project_root": str(PROJECT_ROOT),
        "environment": {
            "conda_env": "theraskill",
            "python_executable": sys.executable,
        },
        "inputs": {
            "evidence_root": str(evidence_root),
            "validation_report_count": len(report_paths),
        },
        "outputs": {
            "schema_count": len(schema_models),
            "mapped_session_count": len(mapping_rows),
        },
        "checks": {
            "all_reports_mapped": len(mapping_rows) == len(report_paths),
            "five_families_covered": not missing_families,
            "all_session_modes_instantiated": set(mode_examples)
            == {mode.value for mode in SessionMode},
            "public_private_separation": not any(
                issue.get("type") == "public_field_leakage" for issue in issues
            ),
            "all_source_artifacts_exist": not any(
                issue.get("type") == "missing_artifacts" for issue in issues
            ),
            "legacy_provenance_uncertainty_explicit": all(
                row["mapped_snapshot"]["session"]["knowledge_release_id"]
                != ""
                for row in mapping_rows
            ),
        },
        "scenario_family_distribution": dict(sorted(family_counts.items())),
        "issues": issues,
    }
    write_json(output_dir / "artifact_mapping_report.json", {"rows": mapping_rows}, overwrite=args.overwrite)
    write_json(output_dir / "contract_qa.json", qa, overwrite=args.overwrite)
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0 if qa["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
