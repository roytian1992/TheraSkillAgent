"""Consent-aware Contribution Hub application service."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from .contracts import (
    CaptureResult,
    CaptureTier,
    ConsentCreate,
    ConsentRecord,
    ContextSnapshot,
    ContextSnapshotInput,
    Contributor,
    Contribution,
    ContributionCapture,
    ContributionEvent,
    ContributionView,
    PrivacyStatus,
    WorkflowStatus,
)
from .repository import ContributionConflictError, ContributionRepository


class ConsentViolationError(ValueError):
    pass


class VisibilityError(PermissionError):
    pass


def canonical_sha256(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class ContributionService:
    def __init__(self, repository: ContributionRepository) -> None:
        self.repository = repository

    def create_consent(
        self,
        subject_id: str,
        request: ConsentCreate,
        *,
        idempotency_key: str | None = None,
    ) -> ConsentRecord:
        request_hash = canonical_sha256(request)
        if idempotency_key:
            prior = self.repository.idempotent_consent(subject_id, idempotency_key)
            if prior:
                consent_id, prior_hash = prior
                if prior_hash != request_hash:
                    raise ContributionConflictError(
                        "consent idempotency key reused with a different request"
                    )
                return self.repository.get_consent(consent_id)
        record = ConsentRecord(
            **request.model_dump(),
            consent_snapshot_id=f"consent_{uuid.uuid4().hex}",
            subject_id=subject_id,
        )
        self.repository.save_consent(record)
        if idempotency_key:
            self.repository.save_consent_idempotency(
                subject_id,
                idempotency_key,
                request_hash,
                record.consent_snapshot_id,
            )
        return record

    def revoke_consent(self, subject_id: str, consent_snapshot_id: str) -> ConsentRecord:
        record = self.repository.get_consent(consent_snapshot_id)
        if record.subject_id != subject_id:
            raise VisibilityError("consent belongs to another subject")
        if record.revoked_at is None:
            record = record.model_copy(update={"revoked_at": datetime.now(timezone.utc)})
            self.repository.revoke_consent(record)
        return self.repository.get_consent(consent_snapshot_id)

    @staticmethod
    def _validate_context(tier: CaptureTier, context: ContextSnapshotInput | None) -> ContextSnapshotInput | None:
        if context is None:
            return None
        if tier in {CaptureTier.OPERATIONAL_ONLY, CaptureTier.FEEDBACK_LINKABLE} and context.contains_text():
            raise ConsentViolationError(f"{tier} does not permit conversation text")
        return context

    def _context_snapshot(
        self,
        *,
        subject_id: str,
        tier: CaptureTier,
        context: ContextSnapshotInput | None,
    ) -> ContextSnapshot | None:
        context = self._validate_context(tier, context)
        if context is None:
            return None
        checksum = canonical_sha256(context)
        existing = self.repository.find_context(subject_id, checksum, str(tier))
        if existing:
            return existing
        snapshot = ContextSnapshot(
            **context.model_dump(),
            context_snapshot_id=f"ctx_{uuid.uuid4().hex}",
            checksum_sha256=checksum,
            capture_tier=tier,
            subject_id=subject_id,
        )
        self.repository.save_context(snapshot)
        return snapshot

    def capture(
        self,
        request: ContributionCapture,
        *,
        subject_id: str,
        role: str,
        expert_profile_id: str | None,
        idempotency_key: str,
    ) -> CaptureResult:
        request_hash = canonical_sha256(request)
        prior = self.repository.idempotent_result(subject_id, idempotency_key)
        if prior:
            contribution_id, prior_hash = prior
            if prior_hash != request_hash:
                raise ContributionConflictError("idempotency key reused with a different request")
            return CaptureResult(contribution=self.repository.get(contribution_id), replayed=True)

        consent = self.repository.get_consent(request.consent_snapshot_id)
        if consent.subject_id != subject_id:
            raise ConsentViolationError("consent belongs to another subject")
        if not consent.is_active():
            raise ConsentViolationError("consent is revoked or expired")
        if (
            request.contribution_type.value == "knowledge_proposal"
            and not consent.purposes.knowledge_derivation
        ):
            raise ConsentViolationError("knowledge proposal requires knowledge_derivation consent")

        snapshot = self._context_snapshot(
            subject_id=subject_id,
            tier=consent.capture_tier,
            context=request.context,
        )
        privacy = (
            PrivacyStatus.PENDING
            if snapshot and snapshot.contains_text()
            else PrivacyStatus.MINIMIZED
        )
        contribution = Contribution(
            contribution_id=f"contrib_{uuid.uuid4().hex}",
            contribution_type=request.contribution_type,
            origin=request.origin,
            contributor=Contributor(
                subject_id=subject_id,
                role=role,
                expert_profile_id=expert_profile_id,
            ),
            target=request.target,
            payload=request.payload,
            consent_snapshot_id=request.consent_snapshot_id,
            context_snapshot_id=snapshot.context_snapshot_id if snapshot else None,
            privacy_status=privacy,
            workflow_status=WorkflowStatus.CONSENT_VALIDATED,
            provenance=request.provenance,
        )
        event = ContributionEvent(
            event_id=f"event_{uuid.uuid4().hex}",
            contribution_id=contribution.contribution_id,
            sequence=1,
            event_type="captured",
            actor_subject_id=subject_id,
            actor_role=role,
            details={"capture_tier": str(consent.capture_tier), "privacy_status": str(privacy)},
        )
        self.repository.create(
            contribution,
            event,
            idempotency_key=idempotency_key,
            request_sha256=request_hash,
        )
        return CaptureResult(contribution=contribution)

    def get_visible(self, contribution_id: str, *, subject_id: str, role: str) -> ContributionView:
        contribution = self.repository.get(contribution_id)
        is_owner = contribution.contributor.subject_id == subject_id
        if role != "admin" and not is_owner:
            if role != "researcher":
                raise VisibilityError("contribution is not visible")
            consent = self.repository.get_consent(contribution.consent_snapshot_id)
            if not consent.purposes.research_use:
                raise VisibilityError("contribution is not authorized for research")

        context = None
        if contribution.context_snapshot_id and (is_owner or role == "admin"):
            context = self.repository.get_context(contribution.context_snapshot_id)
        return ContributionView(
            contribution=contribution,
            context=context,
            events=self.repository.list_events(contribution_id),
        )

    def list_visible(self, *, subject_id: str, role: str, limit: int) -> list[Contribution]:
        if role == "admin":
            return self.repository.list(limit=limit)
        if role == "researcher":
            return self.repository.list(research_only=True, limit=limit)
        return self.repository.list(subject_id=subject_id, limit=limit)

    def withdraw(self, contribution_id: str, *, subject_id: str, role: str) -> ContributionView:
        contribution = self.repository.get(contribution_id)
        if role != "admin" and contribution.contributor.subject_id != subject_id:
            raise VisibilityError("contribution belongs to another subject")
        if contribution.privacy_status != PrivacyStatus.DELETION_REQUESTED:
            events = self.repository.list_events(contribution_id)
            updated = contribution.model_copy(
                update={
                    "privacy_status": PrivacyStatus.DELETION_REQUESTED,
                    "workflow_status": WorkflowStatus.QUARANTINED,
                }
            )
            event = ContributionEvent(
                event_id=f"event_{uuid.uuid4().hex}",
                contribution_id=contribution_id,
                sequence=len(events) + 1,
                event_type="deletion_requested",
                actor_subject_id=subject_id,
                actor_role=role,
                details={"content_use_blocked_immediately": True},
            )
            self.repository.update_projection(updated, event)
        return self.get_visible(contribution_id, subject_id=subject_id, role=role)
