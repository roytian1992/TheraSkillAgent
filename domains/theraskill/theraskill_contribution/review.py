"""Privacy-gated expert review workflow for Contribution Hub."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    ContextSnapshot,
    ContextSnapshotInput,
    Contribution,
    ContributionEvent,
    CorrectionProposal,
    ExpertDecision,
    ExpertDecisionRequest,
    ExpertDecisionResult,
    ExpertProfile,
    ExpertProfileCreate,
    ExpertProfileStatus,
    KnowledgeCandidate,
    PrivacyDerivation,
    PrivacyStatus,
    RegressionCase,
    ReviewAction,
    ReviewAssignment,
    ReviewAssignmentCreate,
    ReviewPreparationResult,
    ReviewQueueItem,
    ReviewTriage,
    ReviewTriageRequest,
    ReviewWorkbench,
    WorkflowStatus,
)
from .repository import ContributionConflictError, ContributionNotFoundError, ContributionRepository
from .service import VisibilityError, canonical_sha256


class ReviewWorkflowError(ValueError):
    pass


_REDACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")),
    ("cn_id", re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")),
    ("phone", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")),
    ("landline", re.compile(r"(?<!\d)0\d{2,3}[- ]?\d{7,8}(?!\d)")),
    ("ipv4", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")),
    (
        "labeled_name",
        re.compile(r"(?P<label>(?:姓名|名字|联系人|name)\s*[:：]\s*)[\u4e00-\u9fffA-Za-z· ]{2,30}", re.I),
    ),
)


def redact_value(value: Any, counts: Counter[str] | None = None) -> Any:
    """Recursively redact direct identifiers while preserving review-relevant structure."""
    replacements = counts if counts is not None else Counter()
    if isinstance(value, str):
        redacted = value
        for label, pattern in _REDACTION_PATTERNS:
            def replace(match: re.Match[str], kind: str = label) -> str:
                replacements[kind] += 1
                prefix = match.groupdict().get("label", "")
                return f"{prefix}[REDACTED_{kind.upper()}]"

            redacted = pattern.sub(replace, redacted)
        return redacted
    if isinstance(value, list):
        return [redact_value(item, replacements) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in {
                "email", "phone", "phone_number", "mobile", "id_number", "identity_number",
                "full_name", "real_name", "address", "contact",
            } and item is not None and item != "" and item != [] and item != {}:
                replacements[f"field_{key.lower()}"] += 1
                result[key] = f"[REDACTED_{key.upper()}]"
            else:
                result[key] = redact_value(item, replacements)
        return result
    return value


class ExpertReviewService:
    def __init__(self, repository: ContributionRepository) -> None:
        self.repository = repository

    def _append_projection_event(
        self,
        contribution: Contribution,
        *,
        actor_subject_id: str,
        actor_role: str,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        sequence = len(self.repository.list_events(contribution.contribution_id)) + 1
        event = ContributionEvent(
            event_id=f"event_{uuid.uuid4().hex}",
            contribution_id=contribution.contribution_id,
            sequence=sequence,
            event_type=event_type,
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            details=details,
        )
        self.repository.update_projection(contribution, event)

    def create_expert_profile(self, request: ExpertProfileCreate) -> ExpertProfile:
        profile = ExpertProfile(
            **request.model_dump(),
            expert_profile_id=f"expert_{uuid.uuid4().hex}",
        )
        self.repository.save_expert_profile(profile)
        return profile

    def verify_expert_profile(
        self,
        expert_profile_id: str,
        *,
        verified_by: str,
        status: ExpertProfileStatus,
        expires_at: datetime | None,
    ) -> ExpertProfile:
        profile = self.repository.get_expert_profile(expert_profile_id)
        now = datetime.now(timezone.utc)
        if status == ExpertProfileStatus.VERIFIED and expires_at is not None and expires_at <= now:
            raise ReviewWorkflowError("verified profile expiry must be in the future")
        updated = profile.model_copy(
            update={
                "status": status,
                "expires_at": expires_at if expires_at is not None else profile.expires_at,
                "verified_by": verified_by if status == ExpertProfileStatus.VERIFIED else None,
                "verified_at": now if status == ExpertProfileStatus.VERIFIED else None,
            }
        )
        self.repository.update_expert_profile(updated)
        return updated

    def _eligible(self, contribution: Contribution) -> bool:
        if contribution.privacy_status in {
            PrivacyStatus.DELETION_REQUESTED,
            PrivacyStatus.DELETED,
        } or contribution.workflow_status == WorkflowStatus.QUARANTINED:
            return False
        consent = self.repository.get_consent(contribution.consent_snapshot_id)
        return consent.is_active() and consent.purposes.content_review

    def privacy_process(
        self,
        contribution_id: str,
        *,
        actor_subject_id: str,
        actor_role: str,
    ) -> bool:
        contribution = self.repository.get(contribution_id)
        if not self._eligible(contribution):
            raise ReviewWorkflowError("contribution is not authorized for content review")
        if self.repository.get_privacy_derivation(contribution_id):
            return False

        counts: Counter[str] = Counter()
        redacted_payload = redact_value(contribution.payload, counts)
        original_context_id = contribution.context_snapshot_id
        redacted_context: ContextSnapshot | None = None
        if original_context_id:
            original = self.repository.get_context(original_context_id)
            redacted_input = ContextSnapshotInput(
                user_message=redact_value(original.user_message, counts),
                assistant_response=redact_value(original.assistant_response, counts),
                bounded_conversation=redact_value(original.bounded_conversation, counts),
                operational=original.operational,
                visibility_scope="redacted_review_snapshot",
            )
            checksum = canonical_sha256(redacted_input)
            redacted_context = self.repository.find_context(
                original.subject_id, checksum, str(original.capture_tier)
            )
            if redacted_context is None:
                redacted_context = ContextSnapshot(
                    **redacted_input.model_dump(),
                    context_snapshot_id=f"ctx_{uuid.uuid4().hex}",
                    checksum_sha256=checksum,
                    capture_tier=original.capture_tier,
                    subject_id=original.subject_id,
                )
                self.repository.save_context(redacted_context)

        derivation = PrivacyDerivation(
            privacy_derivation_id=f"privacy_{uuid.uuid4().hex}",
            contribution_id=contribution_id,
            original_context_snapshot_id=original_context_id,
            redacted_context_snapshot_id=(
                redacted_context.context_snapshot_id if redacted_context else None
            ),
            redacted_payload=redacted_payload,
            replacement_counts=dict(counts),
        )
        self.repository.save_privacy_derivation(derivation)
        updated = contribution.model_copy(
            update={
                "context_snapshot_id": derivation.redacted_context_snapshot_id,
                "privacy_status": PrivacyStatus.REDACTED,
                "workflow_status": WorkflowStatus.PRIVACY_PROCESSED,
            }
        )
        self._append_projection_event(
            updated,
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            event_type="privacy_processed",
            details={
                "privacy_derivation_id": derivation.privacy_derivation_id,
                "processor_id": derivation.processor_id,
                "replacement_counts": derivation.replacement_counts,
                "restricted_original_retained": True,
            },
        )
        return True

    @staticmethod
    def deterministic_triage(contribution: Contribution) -> ReviewTriageRequest:
        serialized = str(contribution.payload).lower()
        operational = contribution.provenance
        priority = 50
        reasons: list[str] = ["authorized_content_review"]
        lanes = []
        if any(term in serialized for term in ("unsafe", "risk", "危机", "自杀", "自伤")):
            priority = 100
            reasons.append("risk_or_safety_signal")
            lanes.extend(["policy", "regression"])
        if contribution.payload.get("helpful") is False or contribution.payload.get("rating") in {0, 1, 2}:
            priority = max(priority, 85)
            reasons.append("explicit_negative_feedback")
            lanes.append("product")
        if operational.get("error_code") or operational.get("status") == "failed":
            priority = max(priority, 90)
            reasons.append("system_failure")
            lanes.append("product")
        if contribution.payload.get("category") in {"unrealistic", "state_inconsistent", "scenario_error"}:
            priority = max(priority, 75)
            reasons.append("simulation_quality_flag")
            lanes.append("scenario")
        if not lanes:
            lanes.append("product")
        return ReviewTriageRequest(
            priority=priority,
            reasons=reasons,
            remediation_lanes=list(dict.fromkeys(lanes)),
        )

    def triage(
        self,
        contribution_id: str,
        request: ReviewTriageRequest,
        *,
        actor_subject_id: str,
        actor_role: str,
    ) -> ReviewTriage:
        existing = self.repository.get_triage(contribution_id)
        if existing:
            return existing
        contribution = self.repository.get(contribution_id)
        if contribution.privacy_status != PrivacyStatus.REDACTED:
            raise ReviewWorkflowError("privacy processing must complete before triage")
        triage = ReviewTriage(
            **request.model_dump(),
            triage_id=f"triage_{uuid.uuid4().hex}",
            contribution_id=contribution_id,
            triaged_by=actor_subject_id,
        )
        self.repository.save_triage(triage)
        updated = contribution.model_copy(update={"workflow_status": WorkflowStatus.TRIAGED})
        self._append_projection_event(
            updated,
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            event_type="triaged",
            details={"triage_id": triage.triage_id, "priority": triage.priority},
        )
        return triage

    def prepare_all(self, *, actor_subject_id: str, actor_role: str) -> ReviewPreparationResult:
        contributions = self.repository.list(limit=None)
        privacy_processed = triaged = ineligible = already_prepared = 0
        for contribution in reversed(contributions):
            if not self._eligible(contribution):
                ineligible += 1
                continue
            changed = self.privacy_process(
                contribution.contribution_id,
                actor_subject_id=actor_subject_id,
                actor_role=actor_role,
            )
            privacy_processed += int(changed)
            already_prepared += int(not changed)
            current = self.repository.get(contribution.contribution_id)
            if self.repository.get_triage(contribution.contribution_id) is None:
                self.triage(
                    contribution.contribution_id,
                    self.deterministic_triage(current),
                    actor_subject_id=actor_subject_id,
                    actor_role=actor_role,
                )
                triaged += 1
        return ReviewPreparationResult(
            scanned=len(contributions),
            privacy_processed=privacy_processed,
            triaged=triaged,
            ineligible=ineligible,
            already_prepared=already_prepared,
        )

    def list_queue(
        self,
        *,
        subject_id: str,
        role: str,
        limit: int = 100,
    ) -> list[ReviewQueueItem]:
        items: list[ReviewQueueItem] = []
        for contribution in self.repository.list(limit=None):
            triage = self.repository.get_triage(contribution.contribution_id)
            if triage is None:
                continue
            assignment = self.repository.get_active_assignment(contribution.contribution_id)
            if role == "expert_reviewer" and (
                assignment is None or assignment.reviewer_subject_id != subject_id
            ):
                continue
            items.append(
                ReviewQueueItem(
                    contribution_id=contribution.contribution_id,
                    contribution_type=contribution.contribution_type,
                    product=contribution.origin.product,
                    target_type=contribution.target.target_type,
                    priority=triage.priority,
                    reasons=triage.reasons,
                    privacy_status=contribution.privacy_status,
                    workflow_status=contribution.workflow_status,
                    assignment=assignment,
                    created_at=contribution.created_at,
                )
            )
        items.sort(key=lambda item: (-item.priority, item.created_at))
        return items[:limit]

    def assign(
        self,
        contribution_id: str,
        request: ReviewAssignmentCreate,
        *,
        assigned_by: str,
    ) -> ReviewAssignment:
        contribution = self.repository.get(contribution_id)
        if self.repository.get_triage(contribution_id) is None:
            raise ReviewWorkflowError("contribution is not in the review queue")
        if self.repository.get_active_assignment(contribution_id):
            raise ContributionConflictError("contribution already has an active assignment")
        profile = self.repository.get_expert_profile(request.expert_profile_id)
        if not profile.is_verified():
            raise ReviewWorkflowError("expert profile is not currently verified")
        conflict = request.conflict_declared or profile.subject_id == contribution.contributor.subject_id
        assignment = ReviewAssignment(
            assignment_id=f"assignment_{uuid.uuid4().hex}",
            contribution_id=contribution_id,
            expert_profile_id=profile.expert_profile_id,
            reviewer_subject_id=profile.subject_id,
            status="conflicted" if conflict else "assigned",
            assigned_by=assigned_by,
            conflict_declared=conflict,
        )
        self.repository.save_assignment(assignment)
        updated = contribution.model_copy(
            update={
                "workflow_status": (
                    WorkflowStatus.TRIAGED if conflict else WorkflowStatus.EXPERT_REVIEW
                )
            }
        )
        self._append_projection_event(
            updated,
            actor_subject_id=assigned_by,
            actor_role="admin",
            event_type="review_assignment_conflict" if conflict else "review_assigned",
            details={
                "assignment_id": assignment.assignment_id,
                "expert_profile_id": profile.expert_profile_id,
                "conflict_declared": conflict,
            },
        )
        return assignment

    def _assert_review_access(
        self, contribution_id: str, *, subject_id: str, role: str
    ) -> ReviewAssignment | None:
        assignment = self.repository.get_active_assignment(contribution_id)
        if role == "expert_reviewer":
            if assignment is None or assignment.reviewer_subject_id != subject_id:
                raise VisibilityError("review is not assigned to this expert")
            profile = self.repository.get_expert_profile(assignment.expert_profile_id)
            if not profile.is_verified():
                raise VisibilityError("expert profile is not currently verified")
        return assignment

    def workbench(
        self, contribution_id: str, *, subject_id: str, role: str
    ) -> ReviewWorkbench:
        assignment = self._assert_review_access(
            contribution_id, subject_id=subject_id, role=role
        )
        contribution = self.repository.get(contribution_id)
        derivation = self.repository.get_privacy_derivation(contribution_id)
        if derivation is None or contribution.privacy_status != PrivacyStatus.REDACTED:
            raise VisibilityError("privacy-processed review material is unavailable")
        context_snapshot = (
            self.repository.get_context(derivation.redacted_context_snapshot_id)
            if derivation.redacted_context_snapshot_id
            else None
        )
        if context_snapshot and context_snapshot.visibility_scope != "redacted_review_snapshot":
            raise VisibilityError("restricted original cannot be shown in expert workbench")
        operational = context_snapshot.operational.model_dump(mode="json") if context_snapshot else {}
        return ReviewWorkbench(
            contribution=contribution.model_copy(update={"payload": derivation.redacted_payload}),
            response={
                "assistant_response": context_snapshot.assistant_response if context_snapshot else None,
                "target": contribution.target.model_dump(mode="json"),
            },
            context={
                "user_message": context_snapshot.user_message if context_snapshot else None,
                "bounded_conversation": context_snapshot.bounded_conversation if context_snapshot else [],
                "feedback": derivation.redacted_payload,
                "visibility_scope": "redacted_review_snapshot",
            },
            evidence={
                "routing_result": operational.get("routing_result", {}),
                "retrieved_knowledge_unit_ids": operational.get(
                    "retrieved_knowledge_unit_ids", []
                ),
                "skill_ids": operational.get("skill_ids", []),
                "knowledge_release_id": operational.get("knowledge_release_id"),
                "provenance": contribution.provenance,
            },
            machine_reviewer=derivation.redacted_payload.get("machine_reviewer"),
            triage=self.repository.get_triage(contribution_id),
            assignment=assignment,
            decisions=self.repository.list_decisions(contribution_id),
        )

    def decide(
        self,
        contribution_id: str,
        request: ExpertDecisionRequest,
        *,
        subject_id: str,
    ) -> ExpertDecisionResult:
        assignment = self.repository.get_assignment(request.assignment_id)
        if assignment.contribution_id != contribution_id:
            raise ReviewWorkflowError("assignment targets a different contribution")
        if assignment.status != "assigned" or assignment.reviewer_subject_id != subject_id:
            raise VisibilityError("active assignment belongs to another expert")
        profile = self.repository.get_expert_profile(assignment.expert_profile_id)
        if not profile.is_verified():
            raise VisibilityError("expert profile is not currently verified")
        if request.conflict_of_interest:
            if set(request.actions) != {ReviewAction.ESCALATE_TO_ADJUDICATION}:
                raise ReviewWorkflowError("conflict declaration permits escalation only")

        decision = ExpertDecision(
            **request.model_dump(),
            decision_id=f"decision_{uuid.uuid4().hex}",
            contribution_id=contribution_id,
            expert_profile_id=profile.expert_profile_id,
            reviewer_subject_id=subject_id,
        )
        self.repository.save_decision(decision)
        correction = regression = candidate = None
        if request.corrected_response:
            correction = CorrectionProposal(
                correction_proposal_id=f"correction_{uuid.uuid4().hex}",
                contribution_id=contribution_id,
                decision_id=decision.decision_id,
                corrected_response=request.corrected_response,
            )
            self.repository.save_correction(correction)
        if request.regression_case is not None:
            regression = RegressionCase(
                regression_case_id=f"regression_{uuid.uuid4().hex}",
                contribution_id=contribution_id,
                decision_id=decision.decision_id,
                case=request.regression_case,
            )
            self.repository.save_regression_case(regression)
        if request.knowledge_candidate is not None:
            candidate = KnowledgeCandidate(
                knowledge_candidate_id=f"candidate_{uuid.uuid4().hex}",
                contribution_id=contribution_id,
                decision_id=decision.decision_id,
                candidate=request.knowledge_candidate,
            )
            self.repository.save_knowledge_candidate(candidate)

        now = datetime.now(timezone.utc)
        completed = assignment.model_copy(
            update={
                "status": "conflicted" if request.conflict_of_interest else "completed",
                "completed_at": now,
                "conflict_declared": request.conflict_of_interest,
            }
        )
        self.repository.update_assignment(completed)
        contribution = self.repository.get(contribution_id)
        updated = contribution.model_copy(
            update={
                "workflow_status": (
                    WorkflowStatus.TRIAGED
                    if request.conflict_of_interest
                    else WorkflowStatus.EXPERT_REVIEWED
                )
            }
        )
        self._append_projection_event(
            updated,
            actor_subject_id=subject_id,
            actor_role="expert_reviewer",
            event_type=(
                "review_conflict_declared"
                if request.conflict_of_interest
                else "expert_decision_recorded"
            ),
            details={
                "decision_id": decision.decision_id,
                "assignment_id": assignment.assignment_id,
                "actions": [str(action) for action in request.actions],
                "candidate_status": candidate.status if candidate else None,
                "production_knowledge_unchanged": True,
            },
        )
        return ExpertDecisionResult(
            decision=decision,
            correction_proposal=correction,
            regression_case=regression,
            knowledge_candidate=candidate,
        )
