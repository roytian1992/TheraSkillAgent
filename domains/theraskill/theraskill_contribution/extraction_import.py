"""Import dynamic extraction units into the governed candidate queue."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable
from uuid import uuid4

from .contracts import (
    CaptureTier,
    ConsentPurposes,
    ConsentRecord,
    Contribution,
    ContributionEvent,
    ContributionOrigin,
    ContributionTarget,
    ContributionType,
    Contributor,
    KnowledgeCandidate,
    PrivacyStatus,
    WorkflowStatus,
)
from .repository import ContributionRepository


def import_extraction_units(
    repository: ContributionRepository,
    *,
    job_id: str,
    actor: str,
    units: Iterable[dict[str, Any]],
    source: dict[str, Any] | None = None,
) -> list[str]:
    """Create immutable contribution lineage for every extracted unit.

    The returned IDs can be sent straight to the candidate lifecycle API.  The
    importer is idempotent per ``job_id`` and candidate ID, allowing a handoff
    request to be retried safely after a service restart.
    """
    actor = str(actor or "system:extraction").strip() or "system:extraction"
    consent_snapshot_id = f"consent_extraction_{job_id}"
    consent = ConsentRecord(
        consent_snapshot_id=consent_snapshot_id,
        subject_id=actor,
        capture_tier=CaptureTier.KNOWLEDGE_DERIVATION_ALLOWED,
        purposes=ConsentPurposes(
            service_delivery=False,
            content_review=True,
            research_use=True,
            knowledge_derivation=True,
        ),
        policy_version="theraskill_extraction_governance_v1",
    )
    candidate_ids: list[str] = []
    source = dict(source or {})
    for index, unit in enumerate(units):
        unit = dict(unit)
        candidate_id = str(unit.get("candidate_id") or f"{job_id}_{index:06d}")
        contribution_id = f"extraction_contribution_{job_id}_{index:06d}"
        decision_id = f"extraction_decision_{job_id}_{index:06d}"
        candidate_payload = {
            "candidate_id": candidate_id,
            "source_job_id": job_id,
            "knowledge_type": unit.get("knowledge_type", "dynamic_knowledge"),
            "content": unit.get("content") or {},
            "evidence": unit.get("evidence") or {},
            "qa": unit.get("qa") or {},
            "provenance": unit.get("provenance") or {},
        }
        candidate = KnowledgeCandidate(
            knowledge_candidate_id=candidate_id,
            contribution_id=contribution_id,
            decision_id=decision_id,
            candidate=candidate_payload,
        )
        contribution = Contribution(
            contribution_id=contribution_id,
            contribution_type=ContributionType.KNOWLEDGE_PROPOSAL,
            origin=ContributionOrigin(
                product="TheraSkillAgent",
                platform="dynamic_extraction",
                session_id=job_id,
                turn_id=str(unit.get("source_id") or candidate_id),
            ),
            contributor=Contributor(subject_id=actor, role="extraction_curator"),
            target=ContributionTarget(
                target_type="extraction_job",
                target_id=job_id,
                target_version="dynamic_extraction_v1",
            ),
            payload={"extraction_unit": candidate_payload, "source": source},
            consent_snapshot_id=consent_snapshot_id,
            privacy_status=PrivacyStatus.MINIMIZED,
            workflow_status=WorkflowStatus.CANDIDATE_CREATED,
            provenance={
                "importer": "theraskill_dynamic_extraction",
                "importer_version": "v1",
                "source_job_id": job_id,
            },
        )
        event = ContributionEvent(
            event_id=f"extraction_event_{uuid4().hex}",
            contribution_id=contribution_id,
            sequence=1,
            event_type="extraction_candidate_imported",
            actor_subject_id=actor,
            actor_role="extraction_curator",
            details={"candidate_id": candidate_id, "source_job_id": job_id},
        )
        canonical = json.dumps(unit, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        repository.import_extraction_candidate(
            consent,
            contribution,
            event,
            candidate,
            idempotency_key=f"extraction:{job_id}:{candidate_id}",
            request_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        candidate_ids.append(candidate_id)
    return candidate_ids
