"""Immutable incremental knowledge preview, promotion, and rollback."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

from .candidate import CandidateWorkflowError
from .contracts import (
    CandidateDecision,
    CandidateStatus,
    KnowledgeCandidate,
    KnowledgeRelease,
    ReleasePreviewRequest,
)
from .overlay import EmbeddingProvider, build_overlay_index, query_overlay
from .repository import ContributionConflictError, ContributionRepository
from domains.theraskill.runtime import runtime_root
from theraskill_agent.paths import active_release_id


from theraskill_agent.paths import resource_root, resource_path

ROOT = resource_root()
BASE_RELEASE_ID = active_release_id()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class ConfiguredEmbeddingProvider:
    def __init__(self, config_path: Path) -> None:
        from .candidate import OpenAICompatibleEmbeddingClient
        from domains.theraskill.theraskill_llm import merged_embedding_config

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.client = OpenAICompatibleEmbeddingClient(
            merged_embedding_config({"retrieval": config}, {})
        )
        self.model = self.client.model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed(texts)


class KnowledgeReleaseService:
    def __init__(
        self,
        repository: ContributionRepository,
        *,
        embedding_provider: EmbeddingProvider,
        release_root: Path | None = None,
        slots_dir: Path | None = None,
        base_units_path: Path | None = None,
        base_qa_path: Path | None = None,
        benchmark_cases_path: Path | None = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        data_root = runtime_root()
        self.release_root = release_root or data_root / "contribution/releases"
        self.slots_dir = slots_dir or data_root / "contribution/release_slots"
        current_root = data_root / "current"
        self.base_units_path = base_units_path or current_root / "knowledge/phase6_reviewed_knowledge_units.jsonl"
        self.base_qa_path = base_qa_path or current_root / "qa/phase6_reviewed_knowledge_release_qa.json"
        self.benchmark_cases_path = benchmark_cases_path or data_root / "contribution/benchmark_cases.jsonl"

    @contextmanager
    def _slot_lock(self) -> Iterator[None]:
        self.slots_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.slots_dir / ".release.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _base_release(self) -> dict[str, Any]:
        qa = json.loads(self.base_qa_path.read_text(encoding="utf-8"))
        declared = str(qa.get("outputs", {}).get("release_units", {}).get("sha256") or "")
        if qa.get("status") != "complete" or len(declared) != 64:
            raise CandidateWorkflowError("Phase 6 base release QA is not complete")
        actual = sha256_file(self.base_units_path)
        if actual != declared:
            raise CandidateWorkflowError("Phase 6 base release checksum mismatch")
        return {
            "release_id": BASE_RELEASE_ID,
            "units_path": str(self.base_units_path.resolve()),
            "units_sha256": actual,
            "row_count": int(qa.get("counts", {}).get("release_unit_rows") or 0),
            "qa_path": str(self.base_qa_path.resolve()),
        }

    @staticmethod
    def _all_strings(value: Any) -> Iterator[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from KnowledgeReleaseService._all_strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from KnowledgeReleaseService._all_strings(child)

    def _heldout_overlap(self, candidates: list[KnowledgeCandidate]) -> list[dict[str, str]]:
        if not self.benchmark_cases_path.is_file():
            raise CandidateWorkflowError("frozen benchmark cases are unavailable for contamination audit")
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
            strings = list(self._all_strings(candidate.model_dump(mode="json")))
            for value in strings:
                normalized = value.strip()
                if normalized in case_ids:
                    overlaps.append({"candidate_id": candidate.knowledge_candidate_id, "benchmark_case_id": normalized, "match": "case_id"})
                query_case = query_hashes.get(hashlib.sha256(normalized.encode()).hexdigest())
                if normalized and query_case:
                    overlaps.append({"candidate_id": candidate.knowledge_candidate_id, "benchmark_case_id": query_case, "match": "exact_query"})
            candidate_json = json.dumps(candidate.candidate, ensure_ascii=False).lower()
            if '"benchmark_split": "heldout"' in candidate_json or '"data_split": "heldout"' in candidate_json:
                overlaps.append({"candidate_id": candidate.knowledge_candidate_id, "benchmark_case_id": "declared_heldout", "match": "lineage_flag"})
        unique = {(item["candidate_id"], item["benchmark_case_id"], item["match"]): item for item in overlaps}
        return list(unique.values())

    def _candidate_gate(self, candidate: KnowledgeCandidate) -> dict[str, bool]:
        contribution = self.repository.get(candidate.contribution_id)
        consent = self.repository.get_consent(contribution.consent_snapshot_id)
        structured = candidate.structured_candidate
        expert_decisions = self.repository.list_decisions(candidate.contribution_id)
        source_decision = next(
            (decision for decision in expert_decisions if decision.decision_id == candidate.decision_id),
            None,
        )
        source_expert_verified = False
        if source_decision is not None:
            try:
                source_expert_verified = self.repository.get_expert_profile(
                    source_decision.expert_profile_id
                ).is_verified()
            except KeyError:
                source_expert_verified = False
        adjudicator_verified = True
        if candidate.adjudication is not None:
            try:
                adjudicator_verified = self.repository.get_expert_profile(
                    candidate.adjudication.expert_profile_id
                ).is_verified()
            except KeyError:
                adjudicator_verified = False
        restrictions = structured.license_use_restrictions if structured else []
        clearance = str(candidate.candidate.get("license_clearance_status") or "").lower()
        checks = {
            "validated_status": candidate.status == CandidateStatus.VALIDATED,
            "active_consent": consent.is_active(),
            "knowledge_derivation_authorized": consent.purposes.knowledge_derivation,
            "privacy_processed": str(contribution.privacy_status) in {"redacted", "minimized"},
            "source_expert_decision_present": source_decision is not None,
            "source_expert_currently_verified": source_expert_verified,
            "structured": structured is not None,
            "semantic_dedup_complete": candidate.dedup_result is not None,
            "not_duplicate": bool(candidate.dedup_result and candidate.dedup_result.llm_relation != "duplicate"),
            "regression_pass": bool(candidate.regression_validation and candidate.regression_validation.status == "pass"),
            "high_risk_independently_adjudicated": bool(
                structured
                and (
                    not (structured.risk_level == "high" or structured.dedup_family == "clinical_risk_assessment")
                    or candidate.adjudication is not None
                )
            ),
            "adjudicator_currently_verified": adjudicator_verified,
            "license_compatible": not restrictions or clearance in {"approved", "compatible"},
        }
        return checks

    def _canary(self, overlay_path: Path, candidates: list[KnowledgeCandidate]) -> list[dict[str, Any]]:
        results = []
        for candidate in candidates:
            structured = candidate.structured_candidate
            if structured is None:
                continue
            query = f"{structured.canonical_label} {structured.summary}"
            embedding = self.embedding_provider.embed([query])[0]
            plan = {
                "metadata_filters": {
                    "dedup_family": [structured.dedup_family],
                    "primary_knowledge_type": [],
                    "primary_evidence_tier": [],
                    "risk_scope": [],
                    "default_route": [],
                },
                "target_families": {"required": [structured.dedup_family]},
                "search_queries": {"label_queries": [structured.canonical_label]},
            }
            channels, trace = query_overlay(overlay_path, plan, query, embedding, limit=10)
            ids = {
                item.get("knowledge_candidate_id")
                for values in channels.values()
                for item in values
            }
            results.append(
                {
                    "candidate_id": candidate.knowledge_candidate_id,
                    "passed": candidate.knowledge_candidate_id in ids,
                    "trace": trace,
                }
            )
        return results

    def preview(
        self,
        request: ReleasePreviewRequest,
        *,
        actor_subject_id: str,
        actor_role: str,
    ) -> KnowledgeRelease:
        candidate_ids = list(dict.fromkeys(request.candidate_ids))
        if len(candidate_ids) != len(request.candidate_ids):
            raise CandidateWorkflowError("preview candidate IDs must be unique")
        candidates = [self.repository.get_knowledge_candidate(candidate_id) for candidate_id in candidate_ids]
        gate_results = {candidate.knowledge_candidate_id: self._candidate_gate(candidate) for candidate in candidates}
        failed = {
            candidate_id: [name for name, passed in checks.items() if not passed]
            for candidate_id, checks in gate_results.items()
            if not all(checks.values())
        }
        overlaps = self._heldout_overlap(candidates)
        if failed:
            raise CandidateWorkflowError(f"candidate release gate failed: {failed}")
        if overlaps:
            raise CandidateWorkflowError(f"heldout benchmark lineage overlap: {overlaps}")

        base = self._base_release()
        now = utc_now()
        release_id = f"knowledge_release_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:10]}"
        final_dir = (self.release_root / release_id).resolve()
        if final_dir.exists():
            raise ContributionConflictError(f"release directory already exists: {final_dir}")
        self.release_root.mkdir(parents=True, exist_ok=True)
        build_dir = Path(tempfile.mkdtemp(dir=self.release_root, prefix=f".{release_id}-building-"))
        try:
            delta_path = build_dir / "units.jsonl"
            overlay_path = build_dir / "overlay.sqlite"
            units, index_report = build_overlay_index(
                overlay_path, candidates, release_id, self.embedding_provider
            )
            with delta_path.open("w", encoding="utf-8") as handle:
                for unit in units:
                    handle.write(json.dumps(unit, ensure_ascii=False, sort_keys=True) + "\n")
            canary = self._canary(overlay_path, candidates)
            canary_complete = len(canary) == len(candidates) and all(item["passed"] for item in canary)
            qa = {
                "schema_version": "theraskill_incremental_knowledge_release_qa_v1",
                "release_id": release_id,
                "status": "complete" if canary_complete else "failed",
                "candidate_count": len(candidates),
                "candidate_gates": gate_results,
                "heldout_contamination": {"status": "complete", "overlaps": overlaps},
                "overlay_index": index_report,
                "index_canary": canary,
                "base_release": base,
                "created_at": now.isoformat(),
            }
            qa_path = build_dir / "qa.json"
            qa_path.write_bytes(canonical_bytes(qa))
            if not canary_complete:
                raise CandidateWorkflowError("overlay index canary failed")
            manifest = {
                "schema_version": "theraskill_incremental_knowledge_release_manifest_v1",
                "release_id": release_id,
                "status": "preview",
                "base_release": base,
                "candidate_ids": candidate_ids,
                "release_notes": request.release_notes,
                "artifacts": {
                    "delta_units": {"path": str(final_dir / "units.jsonl"), "sha256": sha256_file(delta_path), "rows": len(units)},
                    "overlay_index": {"path": str(final_dir / "overlay.sqlite"), "sha256": sha256_file(overlay_path), "rows": len(units)},
                    "qa": {"path": str(final_dir / "qa.json"), "sha256": sha256_file(qa_path)},
                },
                "embedding_model": self.embedding_provider.model,
                "created_by": actor_subject_id,
                "created_at": now.isoformat(),
            }
            manifest_path = build_dir / "manifest.json"
            manifest_path.write_bytes(canonical_bytes(manifest))
            manifest_sha = sha256_file(manifest_path)
            os.replace(build_dir, final_dir)
            release = KnowledgeRelease(
                release_id=release_id,
                candidate_ids=candidate_ids,
                status="preview",
                release_dir=str(final_dir),
                delta_units_path=str(final_dir / "units.jsonl"),
                overlay_index_path=str(final_dir / "overlay.sqlite"),
                manifest_path=str(final_dir / "manifest.json"),
                qa_path=str(final_dir / "qa.json"),
                manifest_sha256=manifest_sha,
                created_by=actor_subject_id,
                created_at=now,
            )
            transitions = []
            for candidate in candidates:
                updated = candidate.model_copy(update={"status": CandidateStatus.RELEASE_CANDIDATE, "updated_at": now})
                decision = CandidateDecision(
                    candidate_decision_id=f"candidate_decision_{uuid.uuid4().hex}",
                    knowledge_candidate_id=candidate.knowledge_candidate_id,
                    event_type="included_in_preview_release",
                    from_status=candidate.status,
                    to_status=CandidateStatus.RELEASE_CANDIDATE,
                    actor_subject_id=actor_subject_id,
                    actor_role=actor_role,
                    details={"release_id": release_id, "manifest_sha256": manifest_sha},
                )
                transitions.append((updated, decision))
            event = {
                "event_type": "preview_created",
                "actor_subject_id": actor_subject_id,
                "actor_role": actor_role,
                "created_at": now.isoformat(),
                "manifest_sha256": manifest_sha,
            }
            try:
                self.repository.create_preview_release(release, event, transitions)
            except Exception:
                shutil.rmtree(final_dir)
                raise
            return release
        except Exception:
            if build_dir.exists():
                shutil.rmtree(build_dir)
            raise

    def _verify_preview(self, release: KnowledgeRelease, expected_sha256: str) -> dict[str, Any]:
        manifest_path = Path(release.manifest_path)
        actual = sha256_file(manifest_path)
        if actual != expected_sha256 or actual != release.manifest_sha256:
            raise CandidateWorkflowError("preview manifest checksum changed")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        qa = json.loads(Path(release.qa_path).read_text(encoding="utf-8"))
        if qa.get("status") != "complete" or not all(item.get("passed") for item in qa.get("index_canary") or []):
            raise CandidateWorkflowError("preview QA/canary is not complete")
        for artifact in manifest.get("artifacts", {}).values():
            if sha256_file(Path(artifact["path"])) != artifact["sha256"]:
                raise CandidateWorkflowError(f"release artifact checksum mismatch: {artifact['path']}")
        return manifest

    def promote(
        self,
        release_id: str,
        expected_manifest_sha256: str,
        *,
        actor_subject_id: str,
        actor_role: str,
    ) -> KnowledgeRelease:
        release = self.repository.get_knowledge_release(release_id)
        if release.status != "preview":
            raise CandidateWorkflowError("only a preview release can be promoted")
        self._verify_preview(release, expected_manifest_sha256)
        candidates = [self.repository.get_knowledge_candidate(candidate_id) for candidate_id in release.candidate_ids]
        if any(candidate.status != CandidateStatus.RELEASE_CANDIDATE for candidate in candidates):
            raise CandidateWorkflowError("release candidates changed after preview")
        canary = self._canary(Path(release.overlay_index_path), candidates)
        if not canary or not all(item["passed"] for item in canary):
            raise CandidateWorkflowError("pre-promotion overlay canary failed")
        now = utc_now()
        promoted = release.model_copy(update={"status": "promoted", "promoted_at": now})
        current_path = self.slots_dir / "current.json"
        previous_path = self.slots_dir / "previous.json"
        slot = {
            "schema_version": "theraskill_knowledge_release_slot_v1",
            "release_id": release.release_id,
            "manifest_path": release.manifest_path,
            "manifest_sha256": release.manifest_sha256,
            "overlay_index_path": release.overlay_index_path,
            "base_release_id": release.base_release_id,
            "activated_at": now.isoformat(),
        }
        with self._slot_lock():
            old_current = current_path.read_bytes() if current_path.exists() else None
            old_previous = previous_path.read_bytes() if previous_path.exists() else None
            previous_release = None
            if old_current:
                previous_slot = json.loads(old_current)
                try:
                    prior = self.repository.get_knowledge_release(str(previous_slot["release_id"]))
                    previous_release = prior.model_copy(update={"status": "superseded"})
                except KeyError:
                    previous_release = None
            if old_current:
                atomic_write(previous_path, old_current)
            else:
                base_slot = {
                    "schema_version": "theraskill_knowledge_release_slot_v1",
                    "release_id": BASE_RELEASE_ID,
                    "manifest_path": str(self.base_qa_path.resolve()),
                    "manifest_sha256": sha256_file(self.base_qa_path),
                    "overlay_index_path": None,
                    "base_release_id": BASE_RELEASE_ID,
                    "activated_at": now.isoformat(),
                }
                atomic_write(previous_path, canonical_bytes(base_slot))
            atomic_write(current_path, canonical_bytes(slot))
            transitions = []
            for candidate in candidates:
                updated = candidate.model_copy(
                    update={
                        "status": CandidateStatus.PROMOTED,
                        "production_release_id": release.release_id,
                        "updated_at": now,
                    }
                )
                transitions.append(
                    (
                        updated,
                        CandidateDecision(
                            candidate_decision_id=f"candidate_decision_{uuid.uuid4().hex}",
                            knowledge_candidate_id=candidate.knowledge_candidate_id,
                            event_type="promoted_to_production",
                            from_status=CandidateStatus.RELEASE_CANDIDATE,
                            to_status=CandidateStatus.PROMOTED,
                            actor_subject_id=actor_subject_id,
                            actor_role=actor_role,
                            details={"release_id": release.release_id},
                        ),
                    )
                )
            event = {
                "event_type": "promoted",
                "actor_subject_id": actor_subject_id,
                "actor_role": actor_role,
                "created_at": now.isoformat(),
            }
            try:
                self.repository.promote_knowledge_release(
                    promoted, event, transitions, superseded=previous_release
                )
            except Exception:
                if old_current is None:
                    current_path.unlink(missing_ok=True)
                else:
                    atomic_write(current_path, old_current)
                if old_previous is None:
                    previous_path.unlink(missing_ok=True)
                else:
                    atomic_write(previous_path, old_previous)
                raise
        return promoted

    def rollback(
        self,
        release_id: str,
        reason: str,
        *,
        actor_subject_id: str,
        actor_role: str,
    ) -> KnowledgeRelease:
        current_path = self.slots_dir / "current.json"
        previous_path = self.slots_dir / "previous.json"
        with self._slot_lock():
            if not current_path.is_file():
                raise CandidateWorkflowError("current release slot is empty")
            current_bytes = current_path.read_bytes()
            current_slot = json.loads(current_bytes)
            if current_slot.get("release_id") != release_id:
                raise CandidateWorkflowError("rollback target is not the current release")
            if not previous_path.is_file():
                raise CandidateWorkflowError("previous release slot is unavailable")
            previous_bytes = previous_path.read_bytes()
            previous_slot = json.loads(previous_bytes)
            release = self.repository.get_knowledge_release(release_id)
            now = utc_now()
            rolled_back = release.model_copy(update={"status": "rolled_back", "rolled_back_at": now})
            reactivated = None
            try:
                prior = self.repository.get_knowledge_release(str(previous_slot["release_id"]))
                reactivated = prior.model_copy(update={"status": "promoted", "promoted_at": now})
            except KeyError:
                reactivated = None
            atomic_write(current_path, previous_bytes)
            atomic_write(previous_path, current_bytes)
            event = {
                "event_type": "rolled_back",
                "actor_subject_id": actor_subject_id,
                "actor_role": actor_role,
                "created_at": now.isoformat(),
                "reason": reason,
                "reactivated_release_id": previous_slot.get("release_id"),
            }
            try:
                self.repository.rollback_knowledge_release(rolled_back, reactivated, event)
            except Exception:
                atomic_write(current_path, current_bytes)
                atomic_write(previous_path, previous_bytes)
                raise
        return rolled_back
