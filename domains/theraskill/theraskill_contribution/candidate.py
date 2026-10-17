"""Governed semantic lifecycle for expert-reviewed knowledge candidates."""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

from .contracts import (
    CandidateAdjudication,
    CandidateAdjudicationRequest,
    CandidateDecision,
    CandidateDedupResult,
    CandidateRegressionValidation,
    CandidateStatus,
    KnowledgeCandidate,
    StructuredKnowledgeCandidate,
)
from .repository import ContributionRepository
from domains.theraskill.runtime import runtime_root


from theraskill_agent.paths import resource_root, resource_path

ROOT = resource_root()
COMMON_DIR = ROOT / "scripts" / "_common"
RETRIEVAL_DIR = ROOT / "scripts" / "AdvancedRetrieval"
for _path in (COMMON_DIR, RETRIEVAL_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from domains.theraskill.theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    OpenAICompatibleEmbeddingClient,
    chat_json_with_repair,
    estimate_prompt_tokens,
    merged_embedding_config,
    merged_model_config,
)


class CandidateWorkflowError(ValueError):
    pass


class CandidateStructurer(Protocol):
    model_name: str

    def structure(self, candidate: KnowledgeCandidate) -> tuple[StructuredKnowledgeCandidate, dict[str, Any]]: ...


class CandidateDeduplicator(Protocol):
    embedding_model: str
    llm_model: str

    def check(self, candidate: StructuredKnowledgeCandidate) -> CandidateDedupResult: ...


class CandidateRegressionRunner(Protocol):
    version: str

    def validate(self, candidate: KnowledgeCandidate) -> CandidateRegressionValidation: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def candidate_text(candidate: StructuredKnowledgeCandidate) -> str:
    return "\n".join(
        [
            f"knowledge_type: {candidate.knowledge_type}",
            f"family: {candidate.dedup_family}",
            f"label: {candidate.canonical_label}",
            f"summary: {candidate.summary}",
            f"applicable_context: {candidate.applicable_context}",
            "contraindications: " + "; ".join(candidate.contraindications),
            f"risk_level: {candidate.risk_level}",
            f"evidence_source: {candidate.evidence_source}",
        ]
    )


class QwenCandidateStructurer:
    """Semantic structuring with staged compaction for long inputs.

    The historical class name is kept for import compatibility; the runtime
    client now uses the project's active LLM when the retrieval config has no model.
    """

    def __init__(
        self,
        config_path: Path = resource_path("configs/theraskill/retrieval.yaml"),
        *,
        direct_input_tokens: int = 10000,
        max_prompt_tokens: int = 12000,
    ) -> None:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.client = OpenAICompatibleClient(
            merged_model_config({}, config)
        )
        self.model_name = self.client.model
        self.direct_input_tokens = direct_input_tokens
        self.max_prompt_tokens = max_prompt_tokens
        self.structure_prompt = (ROOT / "prompts/Contribution/candidate_structure.md").read_text(encoding="utf-8")
        self.compaction_prompt = (ROOT / "prompts/Contribution/candidate_compaction.md").read_text(encoding="utf-8")

    def _compact(self, serialized: str) -> tuple[str, dict[str, Any]]:
        chunks: list[str] = []
        current = ""
        for line in serialized.splitlines(keepends=True):
            if current and estimate_prompt_tokens(current + line) > 5000:
                chunks.append(current)
                current = ""
            while estimate_prompt_tokens(line) > 5000:
                low, high = 1, len(line)
                while low < high:
                    middle = (low + high + 1) // 2
                    if estimate_prompt_tokens(line[:middle]) <= 5000:
                        low = middle
                    else:
                        high = middle - 1
                split_at = max(1, low)
                chunks.append(line[:split_at])
                line = line[split_at:]
            current += line
        if current:
            chunks.append(current)
        def compact_parts(parts: list[str], round_index: int) -> list[str]:
            results = []
            for index, chunk in enumerate(parts):
                result = chat_json_with_repair(
                    self.client,
                    self.compaction_prompt,
                    {
                        "compaction_round": round_index,
                        "chunk_index": index,
                        "chunk_count": len(parts),
                        "proposal_chunk": chunk,
                    },
                    parse_retries=1,
                    max_prompt_tokens=self.max_prompt_tokens,
                    force_json_first=True,
                )
                results.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return results

        digests = compact_parts(chunks, 1)
        rounds = 1
        compacted = "[" + ",".join(digests) + "]"
        while estimate_prompt_tokens(compacted) > self.direct_input_tokens and rounds < 4:
            groups: list[str] = []
            current_group = ""
            for digest in digests:
                addition = ("," if current_group else "") + digest
                if current_group and estimate_prompt_tokens(current_group + addition) > 5000:
                    groups.append("[" + current_group + "]")
                    current_group = digest
                else:
                    current_group += addition
            if current_group:
                groups.append("[" + current_group + "]")
            rounds += 1
            digests = compact_parts(groups, rounds)
            compacted = "[" + ",".join(digests) + "]"
        if estimate_prompt_tokens(compacted) > self.direct_input_tokens:
            raise CandidateWorkflowError("staged compaction could not produce a trustworthy sub-10k representation")
        return compacted, {
            "context_policy": "staged_loss_minimizing_compaction",
            "input_estimated_tokens": estimate_prompt_tokens(serialized),
            "chunk_count": len(chunks),
            "compaction_rounds": rounds,
            "compacted_estimated_tokens": estimate_prompt_tokens(compacted),
            "compaction_model": self.model_name,
        }

    def structure(self, candidate: KnowledgeCandidate) -> tuple[StructuredKnowledgeCandidate, dict[str, Any]]:
        serialized = json.dumps(candidate.candidate, ensure_ascii=False, sort_keys=True, indent=2)
        if estimate_prompt_tokens(serialized) > self.direct_input_tokens:
            proposal, provenance = self._compact(serialized)
        else:
            proposal = serialized
            provenance = {
                "context_policy": "full_candidate",
                "input_estimated_tokens": estimate_prompt_tokens(serialized),
                "chunk_count": 1,
            }
        raw = chat_json_with_repair(
            self.client,
            self.structure_prompt,
            {
                "knowledge_candidate_id": candidate.knowledge_candidate_id,
                "expert_reviewed_proposal": proposal,
            },
            parse_retries=1,
            max_prompt_tokens=self.max_prompt_tokens,
            force_json_first=True,
        )
        structured = StructuredKnowledgeCandidate.model_validate(raw)
        provenance.update(
            {
                "structuring_model": self.model_name,
                "prompt": "prompts/Contribution/candidate_structure.md",
                "prompt_sha256": __import__("hashlib").sha256(self.structure_prompt.encode()).hexdigest(),
                "structured_at": utc_now().isoformat(),
            }
        )
        return structured, provenance


class Phase6SemanticDeduplicator:
    """Embedding nearest-neighbor recall followed by an LLM judgment."""

    def __init__(
        self,
        config_path: Path = resource_path("configs/theraskill/retrieval.yaml"),
        *,
        nearest_k: int = 8,
    ) -> None:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.embedding_config = merged_embedding_config({"retrieval": config}, {})
        self.embedding_client = OpenAICompatibleEmbeddingClient(self.embedding_config)
        self.llm_client = OpenAICompatibleClient(merged_model_config({}, config))
        self.embedding_model = self.embedding_client.model
        self.llm_model = self.llm_client.model
        self.nearest_k = nearest_k
        self.prompt = (ROOT / "prompts/Contribution/candidate_relation.md").read_text(encoding="utf-8")
        retrieval_root = runtime_root() / "current" / "retrieval"
        self.shards_dir = retrieval_root / "faiss_bge_m3_shards_v1"
        self.fts_db = retrieval_root / "phase6_retrieval_fts.sqlite"

    def _nearest(self, candidate: StructuredKnowledgeCandidate, vector: list[float]) -> list[dict[str, Any]]:
        from retrieve_hybrid_v3_llm_structured import faiss_shard_search
        from theraskill_agent.resources import active_index
        from theraskill_agent.indexing.embedding import require_compatible
        from theraskill_agent.indexing.common import safe_path
        selected = active_index()
        if selected:
            directory, manifest = selected
            require_compatible(manifest.get("embedding"), self.embedding_config)
            self.shards_dir = safe_path(directory, manifest["artifacts"]["faiss_shards"]).parent
            self.fts_db = safe_path(directory, manifest["artifacts"]["sqlite_fts"])
        elif self.embedding_model != "bge-m3":
            raise ValueError("legacy BGE index is incompatible with this embedding model")

        plan = {
            "metadata_filters": {"dedup_family": [candidate.dedup_family], "default_route": []},
            "target_families": {"required": [candidate.dedup_family], "preferred": []},
            "rerank_preferences": {"must_include_families": [candidate.dedup_family]},
        }
        hits, _ = faiss_shard_search(self.shards_dir, plan, vector, max(self.nearest_k * 3, 24))
        if not hits:
            return []
        ids = [str(hit["retrieval_id"]) for hit in hits]
        placeholders = ",".join("?" for _ in ids)
        con = sqlite3.connect(str(self.fts_db))
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                f"""SELECT m.*, substr(f.retrieval_text,1,1600) AS text_preview
                    FROM retrieval_meta m JOIN retrieval_fts f USING(retrieval_id)
                    WHERE m.retrieval_id IN ({placeholders})""",
                ids,
            ).fetchall()
        finally:
            con.close()
        by_id = {str(row["retrieval_id"]): dict(row) for row in rows}
        nearest = []
        for hit in hits:
            row = by_id.get(str(hit["retrieval_id"]))
            if row is None:
                continue
            row["cosine_similarity"] = float(hit["dense_score"])
            nearest.append(row)
            if len(nearest) >= self.nearest_k:
                break
        return nearest

    def check(self, candidate: StructuredKnowledgeCandidate) -> CandidateDedupResult:
        text = candidate_text(candidate)
        vector = self.embedding_client.embed([text])[0]
        nearest = self._nearest(candidate, vector)
        raw = chat_json_with_repair(
            self.llm_client,
            self.prompt,
            {"candidate": candidate.model_dump(mode="json"), "nearest_release_units": nearest},
            parse_retries=1,
            max_prompt_tokens=12000,
            force_json_first=True,
        )
        relation = str(raw.get("relation") or "")
        if relation == "duplicate":
            disposition = "reject_duplicate"
        elif relation in {"conflict", "insufficient_evidence"}:
            disposition = "needs_adjudication"
        else:
            disposition = "continue"
        if candidate.risk_level == "high" or candidate.dedup_family == "clinical_risk_assessment":
            disposition = "needs_adjudication"
        return CandidateDedupResult(
            nearest_release_units=nearest,
            max_cosine_similarity=max((float(item["cosine_similarity"]) for item in nearest), default=None),
            llm_relation=relation,
            disposition=disposition,
            rationale=str(raw.get("rationale") or ""),
            embedding_model=self.embedding_model,
            llm_model=self.llm_model,
        )


class BuiltinCandidateRegressionRunner:
    """Generate and execute candidate-local deterministic release invariants."""

    version = "theraskill_candidate_regression_v1"

    def validate(self, candidate: KnowledgeCandidate) -> CandidateRegressionValidation:
        structured = candidate.structured_candidate
        if structured is None:
            raise CandidateWorkflowError("structured candidate is required")
        generated_cases = [
            {
                "case_id": f"{candidate.knowledge_candidate_id}:canonical_label",
                "query": structured.canonical_label,
                "expected_family": structured.dedup_family,
            },
            {
                "case_id": f"{candidate.knowledge_candidate_id}:summary",
                "query": structured.summary,
                "expected_knowledge_type": structured.knowledge_type,
            },
        ]
        checks = {
            "state_gate": candidate.status in {CandidateStatus.DEDUP_CHECKED, CandidateStatus.ADJUDICATED},
            "semantic_dedup_complete": candidate.dedup_result is not None,
            "not_duplicate": bool(candidate.dedup_result and candidate.dedup_result.llm_relation != "duplicate"),
            "evidence_complete": bool(structured.evidence_source and structured.evidence_spans),
            "high_risk_adjudicated": not (
                structured.risk_level == "high" or structured.dedup_family == "clinical_risk_assessment"
            ) or candidate.status == CandidateStatus.ADJUDICATED,
            "retrieval_cases_generated": len(generated_cases) == 2,
        }
        return CandidateRegressionValidation(
            validation_id=f"validation_{uuid.uuid4().hex}",
            knowledge_candidate_id=candidate.knowledge_candidate_id,
            status="pass" if all(checks.values()) else "fail",
            checks=checks,
            cases=generated_cases,
            runner_version=self.version,
        )


class CandidateLifecycleService:
    def __init__(
        self,
        repository: ContributionRepository,
        *,
        structurer: CandidateStructurer,
        deduplicator: CandidateDeduplicator,
        regression_runner: CandidateRegressionRunner | None = None,
    ) -> None:
        self.repository = repository
        self.structurer = structurer
        self.deduplicator = deduplicator
        self.regression_runner = regression_runner or BuiltinCandidateRegressionRunner()

    def _transition(
        self,
        current: KnowledgeCandidate,
        to_status: CandidateStatus,
        *,
        event_type: str,
        actor_subject_id: str,
        actor_role: str,
        updates: dict[str, Any],
        adjudication: CandidateAdjudication | None = None,
        validation: CandidateRegressionValidation | None = None,
    ) -> KnowledgeCandidate:
        updated = current.model_copy(update={**updates, "status": to_status, "updated_at": utc_now()})
        decision = CandidateDecision(
            candidate_decision_id=f"candidate_decision_{uuid.uuid4().hex}",
            knowledge_candidate_id=current.knowledge_candidate_id,
            event_type=event_type,
            from_status=current.status,
            to_status=to_status,
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            details={"production_release_id": updated.production_release_id},
        )
        self.repository.transition_knowledge_candidate(
            updated, decision, adjudication=adjudication, validation=validation
        )
        return updated

    def structure(self, candidate_id: str, *, actor_subject_id: str, actor_role: str) -> KnowledgeCandidate:
        current = self.repository.get_knowledge_candidate(candidate_id)
        if current.status != CandidateStatus.EXPERT_REVIEWED:
            raise CandidateWorkflowError("only expert_reviewed_candidate can be structured")
        structured, provenance = self.structurer.structure(current)
        return self._transition(
            current,
            CandidateStatus.STRUCTURED,
            event_type="llm_structured",
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            updates={"structured_candidate": structured, "structuring_provenance": provenance},
        )

    def dedup_check(self, candidate_id: str, *, actor_subject_id: str, actor_role: str) -> KnowledgeCandidate:
        current = self.repository.get_knowledge_candidate(candidate_id)
        if current.status != CandidateStatus.STRUCTURED or current.structured_candidate is None:
            raise CandidateWorkflowError("candidate must be structured before dedup check")
        result = self.deduplicator.check(current.structured_candidate)
        target = CandidateStatus.DEDUP_CHECKED
        if result.disposition == "reject_duplicate":
            target = CandidateStatus.REJECTED
        elif result.disposition in {"needs_adjudication", "quarantine"}:
            target = CandidateStatus.NEEDS_ADJUDICATION
        return self._transition(
            current,
            target,
            event_type="semantic_dedup_checked",
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            updates={"dedup_result": result},
        )

    def adjudicate(
        self,
        candidate_id: str,
        request: CandidateAdjudicationRequest,
        *,
        actor_subject_id: str,
        actor_role: str,
        expert_profile_id: str,
    ) -> KnowledgeCandidate:
        current = self.repository.get_knowledge_candidate(candidate_id)
        if current.status != CandidateStatus.NEEDS_ADJUDICATION:
            raise CandidateWorkflowError("candidate is not awaiting adjudication")
        profile = self.repository.get_expert_profile(expert_profile_id)
        if profile.subject_id != actor_subject_id or not profile.is_verified():
            raise CandidateWorkflowError("verified adjudicator profile does not match actor")
        contribution = self.repository.get(current.contribution_id)
        review_decisions = self.repository.list_decisions(current.contribution_id)
        original_reviewers = {decision.reviewer_subject_id for decision in review_decisions}
        if request.conflict_of_interest:
            raise CandidateWorkflowError("conflicted adjudicator cannot decide candidate")
        if actor_subject_id == contribution.contributor.subject_id or actor_subject_id in original_reviewers:
            raise CandidateWorkflowError("independent adjudicator is required")
        adjudication = CandidateAdjudication(
            adjudication_id=f"adjudication_{uuid.uuid4().hex}",
            knowledge_candidate_id=candidate_id,
            decision=request.decision,
            rationale=request.rationale,
            adjudicator_subject_id=actor_subject_id,
            expert_profile_id=expert_profile_id,
        )
        targets = {
            "approve": CandidateStatus.ADJUDICATED,
            "reject": CandidateStatus.REJECTED,
            "quarantine": CandidateStatus.QUARANTINED,
        }
        return self._transition(
            current,
            targets[request.decision],
            event_type="independent_adjudication",
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            updates={"adjudication": adjudication},
            adjudication=adjudication,
        )

    def validate(self, candidate_id: str, *, actor_subject_id: str, actor_role: str) -> KnowledgeCandidate:
        current = self.repository.get_knowledge_candidate(candidate_id)
        if current.status not in {CandidateStatus.DEDUP_CHECKED, CandidateStatus.ADJUDICATED}:
            raise CandidateWorkflowError("candidate must pass dedup/adjudication before validation")
        validation = self.regression_runner.validate(current)
        target = CandidateStatus.VALIDATED if validation.status == "pass" else CandidateStatus.QUARANTINED
        return self._transition(
            current,
            target,
            event_type="regression_validated",
            actor_subject_id=actor_subject_id,
            actor_role=actor_role,
            updates={"regression_validation": validation},
            validation=validation,
        )
