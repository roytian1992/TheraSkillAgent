"""Compatibility projections for released simulation reviewer tooling."""

from __future__ import annotations

from typing import Any

from .contracts import SessionSnapshot


def build_reviewer_runtime_report(snapshot: SessionSnapshot) -> dict[str, Any]:
    """Project a platform snapshot into the runtime shape accepted by current reviewer tools."""
    transcript = [
        {
            "role": turn.role,
            "text": turn.text,
            **turn.metadata,
            "turn_id": turn.turn_id,
            "turn_index": turn.turn_index,
        }
        for turn in snapshot.public_view.transcript
    ]
    turn_records: list[dict[str, Any]] = []
    pending_therapist: dict[str, Any] | None = None
    for turn in snapshot.public_view.transcript:
        if turn.role == "therapist":
            pending_therapist = {
                "turn_index": len(turn_records) + 1,
                "therapist_utterance": turn.text,
                "therapist_move_interpretation": None,
            }
        elif turn.role == "patient" and pending_therapist is not None:
            turn_records.append(
                {
                    **pending_therapist,
                    "patient_turn_result": {"patient_utterance": turn.text},
                    "state_update_result": {},
                    "memory_update_result": {},
                }
            )
            pending_therapist = None
    if pending_therapist is not None:
        turn_records.append(
            {
                **pending_therapist,
                "continuation_only": True,
                "patient_turn_result": {},
                "state_update_result": {},
                "memory_update_result": {},
            }
        )
    patient_state = snapshot.private_patient_state
    return {
        "schema_version": "theraskill_simulation_platform_runtime_export_v1",
        "status": str(snapshot.session.status),
        "session_id": snapshot.session.session_id,
        "scenario": snapshot.session.scenario.model_dump(mode="json"),
        "transcript": transcript,
        "turn_records": turn_records,
        "final_session_state": patient_state.runtime_state if patient_state else {},
        "final_memory_seed": patient_state.working_memory if patient_state else {},
        "report_metadata": {
            "runtime_version": snapshot.session.runtime_version,
            "knowledge_release_id": snapshot.session.knowledge_release_id,
            "model_config_id": snapshot.session.model_config_id,
            "prompt_release_id": snapshot.session.prompt_release_id,
            "source_snapshot_id": snapshot.snapshot_id,
            "source_snapshot_checksum": snapshot.checksum,
            "compatibility_target": "run_simulation_reviewer_hermes_adapter.normalize_patient_runtime_session",
        },
    }
