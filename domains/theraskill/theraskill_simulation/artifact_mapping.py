"""Map released simulation artifacts into the unified session contract."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    ActorRole,
    ArtifactRef,
    PrivatePatientState,
    PrivateTherapistState,
    PublicSessionView,
    ReviewPublicView,
    RoleAssignment,
    ScenarioPublicView,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
    SimulationSession,
    TranscriptTurn,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_artifact_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def artifact_ref(project_root: Path, value: str | Path) -> ArtifactRef:
    path = resolve_artifact_path(project_root, value)
    payload = read_json(path)
    return ArtifactRef(
        path=str(path),
        sha256=file_sha256(path),
        schema_version=payload.get("schema_version"),
    )


def _scenario_public(scenario: dict[str, Any]) -> ScenarioPublicView:
    return ScenarioPublicView(
        scenario_id=str(scenario.get("scenario_id") or "legacy::unknown"),
        scenario_family=str(scenario.get("scenario_family") or "unknown"),
        case_id=scenario.get("case_id"),
        case_family=scenario.get("case_family"),
        difficulty_band=scenario.get("difficulty_band"),
        risk_band=scenario.get("risk_band"),
        scenario_brief=scenario.get("scenario_brief"),
        scenario_tags=[str(item) for item in (scenario.get("scenario_tags") or [])],
        recommended_turn_budget=scenario.get("recommended_turn_budget"),
        split_labels={
            str(key): str(value)
            for key, value in (scenario.get("split_labels") or {}).items()
        },
    )


def _transcript_turns(transcript: list[dict[str, Any]]) -> list[TranscriptTurn]:
    return [
        TranscriptTurn(
            turn_id=f"turn_{index:04d}",
            turn_index=index,
            role=str(row.get("role") or "unknown"),
            text=str(row.get("text") or ""),
            metadata={
                key: value for key, value in row.items() if key not in {"role", "text"}
            },
        )
        for index, row in enumerate(transcript, start=1)
    ]


def map_validation_report(
    report_path: str | Path,
    *,
    project_root: str | Path,
) -> SessionSnapshot:
    root = Path(project_root).resolve()
    report_file = resolve_artifact_path(root, report_path)
    report = read_json(report_file)
    artifacts = report.get("artifacts") or {}
    patient_artifact = artifacts.get("patient") or {}
    runtime_path = resolve_artifact_path(root, patient_artifact["runtime_report_path"])
    runtime = read_json(runtime_path)
    scenario_payload = runtime.get("scenario") or patient_artifact.get("scenario") or {}
    scenario = _scenario_public(scenario_payload)
    transcript = _transcript_turns(runtime.get("transcript") or [])

    source_artifacts: dict[str, ArtifactRef] = {
        "validation_report": artifact_ref(root, report_file),
        "patient_runtime": artifact_ref(root, runtime_path),
    }
    artifact_paths = {
        "therapist_bundle": (artifacts.get("therapist") or {}).get("bundle_path"),
        "dual_bundle": (artifacts.get("dual") or {}).get("bundle_path"),
        "reviewer_bundle": (artifacts.get("reviewer") or {}).get("bundle_path"),
    }
    for key, value in artifact_paths.items():
        if value:
            source_artifacts[key] = artifact_ref(root, value)

    report_time = report.get("created_at") or runtime.get("created_at")
    created_at = (
        datetime.fromisoformat(str(report_time).replace("Z", "+00:00"))
        if report_time
        else datetime.now(timezone.utc)
    )
    session_id = f"sim_import_{hashlib.sha1(str(report_file).encode()).hexdigest()[:16]}"
    model_meta = runtime.get("model_config") or {}
    model_config_id = model_meta.get("model") or model_meta.get("name")
    reviewer = artifacts.get("reviewer") or {}
    review_digest = reviewer.get("review_digest") or {}
    review_gate = review_digest.get("review_gate") or {}

    session = SimulationSession(
        session_id=session_id,
        mode=SessionMode.DUAL_AGENT_RESEARCH,
        status=SessionStatus.COMPLETED,
        scenario=scenario,
        role_assignment=RoleAssignment(
            agent_roles=[
                ActorRole.PATIENT_AGENT,
                ActorRole.THERAPIST_AGENT,
                ActorRole.REVIEWER_AGENT,
            ]
        ),
        created_at=created_at,
        updated_at=created_at,
        current_turn_index=len(transcript),
        runtime_version=str(runtime.get("schema_version") or "legacy_unknown"),
        knowledge_release_id=str(
            (report.get("provenance") or {}).get("knowledge_release_id")
            or "legacy_artifact_unrecorded"
        ),
        prompt_release_id=(report.get("provenance") or {}).get("prompt_release_id"),
        model_config_id=model_config_id,
        source_artifacts=source_artifacts,
        metadata={
            "imported_from_legacy_validation": True,
            "source_validation_status": report.get("status"),
            "source_integrity_gate": report.get("integrity_gate"),
            "provenance_limitations": [
                "knowledge_release_id was not recorded by the legacy validation artifact"
            ]
            if not (report.get("provenance") or {}).get("knowledge_release_id")
            else [],
        },
    )

    public = PublicSessionView(
        session_id=session_id,
        mode=session.mode,
        status=session.status,
        scenario=scenario,
        current_turn_index=len(transcript),
        transcript=transcript,
        progress={
            "recommended_turn_budget": scenario.recommended_turn_budget,
            "transcript_turn_count": len(transcript),
            "source_status": report.get("status"),
        },
        review=ReviewPublicView(
            status=str(reviewer.get("status") or "unknown"),
            gate_status=review_gate.get("gate_status"),
            summary={
                key: review_digest.get(key)
                for key in (
                    "case_progress_summary",
                    "key_turning_points",
                    "active_unresolved_threads",
                    "next_turn_setup",
                )
                if review_digest.get(key) is not None
            },
            findings=list(review_digest.get("finding_digest") or []),
            artifact_ref=source_artifacts.get("reviewer_bundle"),
        ),
        created_at=created_at,
        updated_at=created_at,
    )

    patient_state = PrivatePatientState(
        latent_concerns=list(
            (runtime.get("final_memory_seed") or {}).get("hidden_concerns") or []
        ),
        withheld_information=list(
            (runtime.get("final_memory_seed") or {}).get("unresolved_threads") or []
        ),
        trust_alliance_readiness={
            key: (runtime.get("final_session_state") or {}).get(key)
            for key in (
                "alliance_state",
                "disclosure_state",
                "readiness_state",
                "resistance_state",
                "felt_understanding",
            )
        },
        runtime_state=runtime.get("final_session_state") or {},
        working_memory=runtime.get("final_memory_seed") or {},
    )

    therapist_payload: dict[str, Any] = {}
    if "therapist_bundle" in source_artifacts:
        therapist_payload = read_json(Path(source_artifacts["therapist_bundle"].path))
    therapist_digest = therapist_payload.get("review_digest") or {}
    therapist_state = PrivateTherapistState(
        selected_strategy={
            "response_intent": therapist_digest.get("therapist_response_intent") or [],
            "stage_target": therapist_digest.get("therapist_stage_target") or [],
        },
        internal_planning={
            "therapist_utterance": therapist_digest.get("therapist_utterance")
        },
    )

    snapshot = SessionSnapshot(
        snapshot_id=f"snapshot_{session_id}_legacy_import",
        session=session,
        through_event_sequence=0,
        public_view=public,
        private_patient_state=patient_state,
        private_therapist_state=therapist_state,
        created_at=created_at,
    )
    checksum = canonical_json_sha256(snapshot.model_dump(mode="json", exclude={"checksum"}))
    return snapshot.model_copy(update={"checksum": checksum})
