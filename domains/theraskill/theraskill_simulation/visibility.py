"""Role-based visibility rules for session views and exports."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .contracts import ActorRole, PublicSessionView, SessionSnapshot


class ExportScope(StrEnum):
    PUBLIC = "public"
    INSTRUCTOR = "instructor"
    RESEARCHER = "researcher"


VISIBILITY_MATRIX: dict[ExportScope, frozenset[str]] = {
    ExportScope.PUBLIC: frozenset({"public_view"}),
    ExportScope.INSTRUCTOR: frozenset({"public_view", "private_patient_state"}),
    ExportScope.RESEARCHER: frozenset(
        {"public_view", "private_patient_state", "private_therapist_state", "session"}
    ),
}

ROLE_EXPORT_SCOPE: dict[ActorRole, ExportScope] = {
    ActorRole.HUMAN_THERAPIST: ExportScope.PUBLIC,
    ActorRole.HUMAN_CLIENT_ROLE: ExportScope.PUBLIC,
    ActorRole.PATIENT_AGENT: ExportScope.PUBLIC,
    ActorRole.THERAPIST_AGENT: ExportScope.PUBLIC,
    ActorRole.REVIEWER_AGENT: ExportScope.PUBLIC,
    ActorRole.INSTRUCTOR: ExportScope.INSTRUCTOR,
    ActorRole.RESEARCHER: ExportScope.RESEARCHER,
    ActorRole.SYSTEM: ExportScope.RESEARCHER,
}

FORBIDDEN_PUBLIC_FIELD_NAMES = frozenset(
    {
        "private_patient_state",
        "private_therapist_state",
        "latent_concerns",
        "withheld_information",
        "disclosure_conditions",
        "risk_signals_not_yet_disclosed",
        "internal_planning",
        "candidate_strategies",
        "working_memory",
    }
)


def public_view(snapshot: SessionSnapshot) -> PublicSessionView:
    return snapshot.public_view


def export_snapshot(snapshot: SessionSnapshot, scope: ExportScope | str) -> dict[str, Any]:
    resolved_scope = ExportScope(scope)
    allowed = VISIBILITY_MATRIX[resolved_scope]
    payload: dict[str, Any] = {
        "schema_version": "theraskill_simulation_session_export_v1",
        "scope": resolved_scope.value,
        "snapshot_id": snapshot.snapshot_id,
    }
    if "session" in allowed:
        payload["session"] = snapshot.session.model_dump(mode="json")
    if "public_view" in allowed:
        payload["public_view"] = snapshot.public_view.model_dump(mode="json")
    if "private_patient_state" in allowed and snapshot.private_patient_state is not None:
        payload["private_patient_state"] = snapshot.private_patient_state.model_dump(mode="json")
    if "private_therapist_state" in allowed and snapshot.private_therapist_state is not None:
        payload["private_therapist_state"] = snapshot.private_therapist_state.model_dump(mode="json")
    return payload


def find_forbidden_public_fields(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_PUBLIC_FIELD_NAMES:
                findings.append(child_path)
            findings.extend(find_forbidden_public_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_forbidden_public_fields(child, f"{path}[{index}]"))
    return findings
