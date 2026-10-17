"""Stable data contracts shared by TheraSkill platform adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
from uuid import uuid4

CONTRACT_VERSION = "theraskill_platform_contract_v1"


INTERACTION_KINDS = {
    "question",
    "form",
    "approval",
    "progress",
    "review",
}


class ContractError(ValueError):
    """Raised when a platform request or release manifest is malformed."""


def _clean_text(value: Any, field_name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ContractError(f"{field_name} must be a string")
    value = value.strip()
    if required and not value:
        raise ContractError(f"{field_name} is required")
    return value


@dataclass(frozen=True)
class TheraSkillRequest:
    """Normalized request entering any TheraSkill platform capability."""

    action: str
    query: str = ""
    actor: str = "agent"
    domain: str = "theraskill"
    scope: str = "default"
    session_id: str = ""
    profile: str = "default"
    release_id: str = "current"
    idempotency_key: str = ""
    approval_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _clean_text(self.action, "action", required=True))
        object.__setattr__(self, "query", _clean_text(self.query, "query"))
        object.__setattr__(self, "actor", _clean_text(self.actor, "actor") or "agent")
        object.__setattr__(self, "domain", _clean_text(self.domain, "domain") or "theraskill")
        object.__setattr__(self, "scope", _clean_text(self.scope, "scope") or "default")
        object.__setattr__(self, "session_id", _clean_text(self.session_id, "session_id"))
        object.__setattr__(self, "profile", _clean_text(self.profile, "profile") or "default")
        object.__setattr__(self, "release_id", _clean_text(self.release_id, "release_id") or "current")
        object.__setattr__(self, "idempotency_key", _clean_text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "approval_id", _clean_text(self.approval_id, "approval_id"))
        object.__setattr__(self, "run_id", _clean_text(self.run_id, "run_id"))
        object.__setattr__(self, "workflow_id", _clean_text(self.workflow_id, "workflow_id"))
        if not isinstance(self.parameters, Mapping):
            raise ContractError("parameters must be an object")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TheraSkillRequest":
        if not isinstance(payload, Mapping):
            raise ContractError("request payload must be an object")
        return cls(
            action=payload.get("action", ""),
            query=payload.get("query", ""),
            actor=payload.get("actor", "agent"),
            domain=payload.get("domain", "theraskill"),
            scope=payload.get("scope", "default"),
            session_id=payload.get("session_id", ""),
            profile=payload.get("profile", "default"),
            release_id=payload.get("release_id", "current"),
            idempotency_key=payload.get("idempotency_key", ""),
            approval_id=payload.get("approval_id", ""),
            run_id=payload.get("run_id", ""),
            workflow_id=payload.get("workflow_id", ""),
            parameters=dict(payload.get("parameters") or {}),
            request_id=payload.get("request_id") or uuid4().hex,
            schema_version=payload.get("schema_version", CONTRACT_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TheraSkillResult:
    """Normalized result returned by every TheraSkill platform capability."""

    ok: bool
    request_id: str
    action: str
    status: str
    data: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    next_action: str | None = None
    view: Mapping[str, Any] = field(default_factory=dict)
    interaction: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None
    schema_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "pending"}:
            raise ContractError("status must be 'completed', 'failed' or 'pending'")
        if self.ok != (self.status == "completed"):
            raise ContractError("ok must agree with status")
        object.__setattr__(self, "request_id", _clean_text(self.request_id, "request_id", required=True))
        object.__setattr__(self, "action", _clean_text(self.action, "action", required=True))
        if not isinstance(self.data, Mapping):
            raise ContractError("data must be an object")
        if not isinstance(self.provenance, Mapping):
            raise ContractError("provenance must be an object")
        if not isinstance(self.view, Mapping):
            raise ContractError("view must be an object")
        if not isinstance(self.interaction, Mapping):
            raise ContractError("interaction must be an object")
        if self.interaction:
            kind = str(self.interaction.get("kind") or "").strip()
            if kind not in INTERACTION_KINDS:
                raise ContractError(f"interaction.kind must be one of: {', '.join(sorted(INTERACTION_KINDS))}")
            if not str(self.interaction.get("workflow_id") or "").strip():
                raise ContractError("interaction.workflow_id is required")
        if self.error is not None and not isinstance(self.error, Mapping):
            raise ContractError("error must be an object")

    @classmethod
    def success(
        cls,
        action: str,
        data: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
        warnings: tuple[str, ...] = (),
        provenance: Mapping[str, Any] | None = None,
        next_action: str | None = None,
        view: Mapping[str, Any] | None = None,
        interaction: Mapping[str, Any] | None = None,
    ) -> "TheraSkillResult":
        return cls(
            True,
            request_id or uuid4().hex,
            action,
            "completed",
            data or {},
            warnings,
            provenance or {},
            next_action,
            view or {},
            interaction or {},
        )

    @classmethod
    def pending(
        cls,
        action: str,
        data: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
        warnings: tuple[str, ...] = (),
        provenance: Mapping[str, Any] | None = None,
        next_action: str | None = None,
        view: Mapping[str, Any] | None = None,
        interaction: Mapping[str, Any] | None = None,
    ) -> "TheraSkillResult":
        """Return a resumable result while waiting for a user or worker."""

        return cls(
            False,
            request_id or uuid4().hex,
            action,
            "pending",
            data or {},
            warnings,
            provenance or {},
            next_action,
            view or {},
            interaction or {},
        )

    @classmethod
    def failure(
        cls,
        action: str,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> "TheraSkillResult":
        error = {"code": code, "message": message}
        if details:
            error["details"] = dict(details)
        return cls(False, request_id or uuid4().hex, action, "failed", error=error)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        return payload


@dataclass(frozen=True)
class ArtifactManifest:
    """Minimal release metadata required by the TheraSkillAgent runtime."""

    release_id: str
    status: str
    artifacts: Mapping[str, str]
    schema_version: str = CONTRACT_VERSION
    created_at: str = ""
    counts: Mapping[str, int] = field(default_factory=dict)
    model_version: str = ""
    prompt_version: str = ""
    retrieval_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_id", _clean_text(self.release_id, "release_id", required=True))
        object.__setattr__(self, "status", _clean_text(self.status, "status", required=True))
        if not isinstance(self.artifacts, Mapping):
            raise ContractError("artifacts must be an object")
        if self.status != "complete":
            raise ContractError("runtime release manifest must have status='complete'")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ArtifactManifest":
        if not isinstance(payload, Mapping):
            raise ContractError("release manifest must be an object")
        return cls(
            release_id=payload.get("release_id", ""),
            status=payload.get("status", ""),
            artifacts=dict(payload.get("artifacts") or payload.get("outputs") or {}),
            schema_version=payload.get("schema_version", CONTRACT_VERSION),
            created_at=payload.get("created_at", ""),
            counts=dict(payload.get("counts") or {}),
            model_version=str(payload.get("model_version") or (payload.get("runtime") or {}).get("model_version") or ""),
            prompt_version=str(payload.get("prompt_version") or (payload.get("runtime") or {}).get("prompt_version") or ""),
            retrieval_version=str(payload.get("retrieval_version") or (payload.get("runtime") or {}).get("retrieval_version") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
