"""Privacy-minimized continuous-operations reports and release failure replay."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    OperationsSnapshot,
    ReleaseFailure,
    ReleaseFailureCreate,
    ReleaseFailureReplay,
)
from .release import KnowledgeReleaseService
from .repository import ContributionRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    result = ordered[lower] * (upper - position) + ordered[upper] * (position - lower)
    return round(result, 3)


def latency_report(values: list[float], threshold_hours: float) -> dict[str, Any]:
    return {
        "status": "complete" if values else "insufficient_data",
        "samples": len(values),
        "p50_hours": percentile(values, 0.5),
        "p95_hours": percentile(values, 0.95),
        "max_hours": round(max(values), 3) if values else None,
        "threshold_hours": threshold_hours,
        "threshold_passed": bool(values) and (percentile(values, 0.95) or 0) <= threshold_hours,
    }


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


class ContinuousOperationsService:
    def __init__(
        self,
        repository: ContributionRepository,
        *,
        release_service: KnowledgeReleaseService,
        output_root: Path,
        benchmark_cases_path: Path,
    ) -> None:
        self.repository = repository
        self.release_service = release_service
        self.output_root = output_root
        self.benchmark_cases_path = benchmark_cases_path

    def _sla_report(self, contributions: list[Any]) -> dict[str, Any]:
        capture_complete = 0
        privacy_hours = []
        review_hours = []
        now = utc_now()
        open_queue_hours = []
        for contribution in contributions:
            events = self.repository.list_events(contribution.contribution_id)
            if events and events[0].sequence == 1:
                capture_complete += 1
            derivation = self.repository.get_privacy_derivation(contribution.contribution_id)
            if derivation is not None:
                privacy_hours.append(
                    (derivation.created_at - contribution.created_at).total_seconds() / 3600
                )
            decisions = self.repository.list_decisions(contribution.contribution_id)
            if decisions:
                review_hours.append(
                    (decisions[0].created_at - contribution.created_at).total_seconds() / 3600
                )
            elif self.repository.get_triage(contribution.contribution_id) is not None:
                open_queue_hours.append((now - contribution.created_at).total_seconds() / 3600)
        total = len(contributions)
        return {
            "capture": {
                "status": "complete" if total else "insufficient_data",
                "received_contributions": total,
                "append_only_event_initialized": capture_complete,
                "receipt_integrity_rate": round(capture_complete / total, 6) if total else None,
                "scope_note": "Hub receipt integrity; sender-side delivery denominator is reported by each producer",
            },
            "privacy_processing": latency_report(privacy_hours, 24),
            "expert_review": latency_report(review_hours, 72),
            "open_queue": {
                "status": "complete" if open_queue_hours else "insufficient_data",
                "count": len(open_queue_hours),
                "oldest_hours": round(max(open_queue_hours), 3) if open_queue_hours else None,
                "threshold_hours": 168,
                "threshold_passed": bool(open_queue_hours) and max(open_queue_hours) <= 168,
            },
        }

    def _agreement_report(self, contributions: list[Any]) -> dict[str, Any]:
        pairs = []
        for contribution in contributions:
            decisions = self.repository.list_decisions(contribution.contribution_id)
            for left, right in combinations(decisions, 2):
                if left.reviewer_subject_id == right.reviewer_subject_id:
                    continue
                pairs.append(
                    {
                        "issue_jaccard": jaccard(map(str, left.issue_types), map(str, right.issue_types)),
                        "action_jaccard": jaccard(map(str, left.actions), map(str, right.actions)),
                        "confidence_delta": abs(left.confidence - right.confidence),
                    }
                )
        return {
            "status": "complete" if pairs else "insufficient_data",
            "independent_pairs": len(pairs),
            "mean_issue_jaccard": round(sum(row["issue_jaccard"] for row in pairs) / len(pairs), 6) if pairs else None,
            "mean_action_jaccard": round(sum(row["action_jaccard"] for row in pairs) / len(pairs), 6) if pairs else None,
            "mean_confidence_delta": round(sum(row["confidence_delta"] for row in pairs) / len(pairs), 6) if pairs else None,
            "pair_metrics": pairs,
        }

    def _candidate_quality(self, candidates: list[Any]) -> dict[str, Any]:
        statuses = Counter(str(candidate.status) for candidate in candidates)
        relations = Counter(
            candidate.dedup_result.llm_relation
            for candidate in candidates
            if candidate.dedup_result is not None
        )
        evidence_complete = sum(
            bool(
                candidate.structured_candidate
                and candidate.structured_candidate.evidence_source
                and candidate.structured_candidate.evidence_spans
            )
            for candidate in candidates
        )
        regression_pass = sum(
            bool(candidate.regression_validation and candidate.regression_validation.status == "pass")
            for candidate in candidates
        )
        return {
            "status": "complete" if candidates else "insufficient_data",
            "candidate_count": len(candidates),
            "status_distribution": dict(sorted(statuses.items())),
            "semantic_relation_distribution": dict(sorted(relations.items())),
            "evidence_completeness_rate": round(evidence_complete / len(candidates), 6) if candidates else None,
            "regression_pass_rate": round(regression_pass / len(candidates), 6) if candidates else None,
            "promoted_count": statuses.get("promoted", 0),
            "rejected_count": statuses.get("rejected", 0),
            "quarantined_count": statuses.get("quarantined", 0),
        }

    def _contamination_report(self, candidates: list[Any]) -> dict[str, Any]:
        case_ids: set[str] = set()
        query_hashes: dict[str, str] = {}
        with self.benchmark_cases_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                case_id = str(row.get("benchmark_case_id") or "")
                query = str(row.get("query") or "").strip()
                if case_id:
                    case_ids.add(case_id)
                if query:
                    query_hashes[hashlib.sha256(query.encode()).hexdigest()] = case_id
        overlaps = []
        for candidate in candidates:
            serialized = json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False)
            values = [value for value in self._strings(candidate.model_dump(mode="json")) if value]
            for value in values:
                if value in case_ids:
                    overlaps.append({"candidate_id": candidate.knowledge_candidate_id, "benchmark_case_id": value, "match": "case_id"})
                case_id = query_hashes.get(hashlib.sha256(value.encode()).hexdigest())
                if case_id:
                    overlaps.append({"candidate_id": candidate.knowledge_candidate_id, "benchmark_case_id": case_id, "match": "exact_query"})
            if '"benchmark_split": "heldout"' in serialized.lower() or '"data_split": "heldout"' in serialized.lower():
                overlaps.append({"candidate_id": candidate.knowledge_candidate_id, "benchmark_case_id": "declared_heldout", "match": "lineage_flag"})
        unique = list({(row["candidate_id"], row["benchmark_case_id"], row["match"]): row for row in overlaps}.values())
        return {
            "status": "complete",
            "candidate_count": len(candidates),
            "benchmark_case_count": len(case_ids),
            "overlap_count": len(unique),
            "overlaps": unique,
            "gate_passed": not unique,
        }

    @staticmethod
    def _strings(value: Any) -> Iterable[str]:
        if isinstance(value, str):
            yield value.strip()
        elif isinstance(value, dict):
            for child in value.values():
                yield from ContinuousOperationsService._strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from ContinuousOperationsService._strings(child)

    def snapshot(self, *, scope: str, actor_subject_id: str) -> OperationsSnapshot:
        contributions = self.repository.list(limit=None)
        candidates = self.repository.list_knowledge_candidates()
        releases = self.repository.list_knowledge_releases()
        failures = self.repository.list_release_failures()
        reports = {
            "sla": self._sla_report(contributions),
            "reviewer_agreement": self._agreement_report(contributions),
            "candidate_quality": self._candidate_quality(candidates),
            "heldout_contamination": self._contamination_report(candidates),
            "release_failures": {
                "reported": len(failures),
                "status_distribution": dict(sorted(Counter(failure.status for failure in failures).items())),
            },
        }
        snapshot_id = f"operations_{utc_now().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:10]}"
        output = (self.output_root / f"{snapshot_id}.json").resolve()
        artifact = {
            "schema_version": "theraskill_continuous_operations_artifact_v1",
            "snapshot_id": snapshot_id,
            "scope": scope,
            "reports": reports,
            "source_counts": {
                "contributions": len(contributions),
                "knowledge_candidates": len(candidates),
                "knowledge_releases": len(releases),
                "release_failures": len(failures),
            },
            "contains_conversation_text": False,
            "created_at": utc_now().isoformat(),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(output)
        output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        return OperationsSnapshot(
            snapshot_id=snapshot_id,
            scope=scope,
            reports=reports,
            source_counts=artifact["source_counts"],
            contains_conversation_text=False,
            artifact_path=str(output),
            artifact_sha256=digest,
            created_by=actor_subject_id,
        )

    def report_failure(
        self,
        release_id: str,
        request: ReleaseFailureCreate,
        *,
        actor_subject_id: str,
        actor_role: str,
    ) -> ReleaseFailure:
        self.repository.get_knowledge_release(release_id)
        failure = ReleaseFailure(
            **request.model_dump(),
            failure_id=f"release_failure_{uuid.uuid4().hex}",
            release_id=release_id,
            status="reported",
            reported_by=actor_subject_id,
            reported_role=actor_role,
        )
        self.repository.save_release_failure(failure)
        return failure

    def replay_failure(
        self,
        failure_id: str,
        *,
        actor_subject_id: str,
    ) -> ReleaseFailureReplay:
        failure = self.repository.get_release_failure(failure_id)
        release = self.repository.get_knowledge_release(failure.release_id)
        slot = self.release_service.slots_dir / "current.json"
        before = hashlib.sha256(slot.read_bytes()).hexdigest() if slot.is_file() else None
        checks = {"manifest_and_artifacts": False, "overlay_canary": False}
        diagnostics: dict[str, Any] = {"failure_stage": str(failure.stage), "error_code": failure.error_code}
        try:
            self.release_service._verify_preview(release, str(release.manifest_sha256))
            checks["manifest_and_artifacts"] = True
            candidates = [
                self.repository.get_knowledge_candidate(candidate_id)
                for candidate_id in release.candidate_ids
            ]
            canary = self.release_service._canary(Path(release.overlay_index_path), candidates)
            checks["overlay_canary"] = bool(canary) and all(row["passed"] for row in canary)
            diagnostics["canary_count"] = len(canary)
        except Exception as exc:
            diagnostics["exception_type"] = type(exc).__name__
            diagnostics["exception_code"] = hashlib.sha256(str(exc).encode()).hexdigest()[:16]
        after = hashlib.sha256(slot.read_bytes()).hexdigest() if slot.is_file() else None
        slot_unchanged = before == after
        checks["production_slot_unchanged"] = slot_unchanged
        reproduced = not all(checks.values())
        replay = ReleaseFailureReplay(
            replay_id=f"failure_replay_{uuid.uuid4().hex}",
            failure_id=failure.failure_id,
            release_id=failure.release_id,
            status="reproduced" if reproduced else "not_reproduced",
            checks=checks,
            diagnostics=diagnostics,
            production_slot_mutated=not slot_unchanged,
            replayed_by=actor_subject_id,
        )
        updated = failure.model_copy(update={"status": "replayed", "replayed_at": replay.created_at})
        self.repository.save_failure_replay(updated, replay)
        return replay
