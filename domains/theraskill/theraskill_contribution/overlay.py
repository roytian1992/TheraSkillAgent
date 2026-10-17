"""Small immutable hybrid indexes layered over the Phase 6 retrieval release."""

from __future__ import annotations

import json
import hashlib
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from .candidate import candidate_text
from .contracts import KnowledgeCandidate
from theraskill_agent.paths import active_release_id


_ACTIVE_SLOT_CACHE: dict[str, tuple[tuple[int, int, int, int, int, int], Path | None, dict[str, Any]]] = {}


class EmbeddingProvider(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_overlay_from_slot(slot_path: Path) -> tuple[Path | None, dict[str, Any]]:
    if not slot_path.is_file():
        return None, {"status": "inactive", "reason": "release_slot_missing", "slot_path": str(slot_path)}
    try:
        slot_stat = slot_path.stat()
        slot = json.loads(slot_path.read_text(encoding="utf-8"))
        if slot.get("schema_version") != "theraskill_knowledge_release_slot_v1":
            raise ValueError("invalid release slot schema")
        overlay_value = slot.get("overlay_index_path")
        if not overlay_value:
            return None, {
                "status": "base_only",
                "release_id": slot.get("release_id"),
                "slot_path": str(slot_path),
            }
        manifest_path = Path(str(slot["manifest_path"]))
        overlay_path = Path(str(overlay_value))
        manifest_stat = manifest_path.stat()
        overlay_stat = overlay_path.stat()
        fingerprint = (
            slot_stat.st_mtime_ns, slot_stat.st_size,
            manifest_stat.st_mtime_ns, manifest_stat.st_size,
            overlay_stat.st_mtime_ns, overlay_stat.st_size,
        )
        cache_key = str(slot_path.resolve())
        cached = _ACTIVE_SLOT_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            trace = dict(cached[2])
            trace["integrity_cache_hit"] = True
            return cached[1], trace
        if _sha256_file(manifest_path) != str(slot.get("manifest_sha256") or ""):
            raise ValueError("active release manifest checksum mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = manifest.get("artifacts", {}).get("overlay_index") or {}
        if str(artifact.get("path")) != str(overlay_path):
            raise ValueError("active overlay path differs from release manifest")
        if not overlay_path.is_file() or _sha256_file(overlay_path) != str(artifact.get("sha256") or ""):
            raise ValueError("active overlay checksum mismatch")
        trace = {
            "status": "active",
            "release_id": slot.get("release_id"),
            "manifest_path": str(manifest_path),
            "manifest_sha256": slot.get("manifest_sha256"),
            "overlay_index_path": str(overlay_path),
            "slot_path": str(slot_path),
            "integrity_cache_hit": False,
        }
        _ACTIVE_SLOT_CACHE[cache_key] = (fingerprint, overlay_path, trace)
        return overlay_path, trace
    except Exception as exc:
        return None, {
            "status": "invalid_fail_closed",
            "reason": f"{type(exc).__name__}: {exc}",
            "slot_path": str(slot_path),
        }


def _default_route(family: str) -> str:
    return {
        "style_response": "reviewed_cluster_retrieval",
        "qa_psychoeducation": "reviewed_cluster_retrieval",
        "clinical_risk_assessment": "clinical_reviewed_cluster_retrieval_with_label_guard",
        "therapy_process": "raw_passthrough_retrieval",
        "method_rubric": "raw_passthrough_retrieval",
        "synthetic_construction": "synthetic_construction_method_retrieval",
    }[family]


def _risk_scope(level: str) -> str:
    return {
        "high": "high_or_urgent",
        "medium": "ambiguous_or_moderate",
        "low": "ordinary_or_unspecified",
    }[level]


def candidate_release_unit(candidate: KnowledgeCandidate, release_id: str) -> dict[str, Any]:
    structured = candidate.structured_candidate
    if structured is None:
        raise ValueError("structured candidate is required")
    release_unit_id = f"rku_incremental_{candidate.knowledge_candidate_id.removeprefix('candidate_')}"
    return {
        "schema_version": "theraskill_incremental_reviewed_knowledge_unit_v1",
        "release_unit_id": release_unit_id,
        "release_unit_type": "incremental_expert_candidate",
        "canonical_knowledge_type": structured.knowledge_type,
        "dedup_family": structured.dedup_family,
        "content": {
            "canonical_label": structured.canonical_label,
            "summary": structured.summary,
            "applicable_context": structured.applicable_context,
            "contraindications": structured.contraindications,
            "risk_level": structured.risk_level,
            "evidence_source": structured.evidence_source,
            "evidence_spans": structured.evidence_spans,
        },
        "retrieval_policy": {
            "default_route": _default_route(structured.dedup_family),
            "retrieval_eligible": True,
            "embedding_eligible": True,
        },
        "quality": {
            "candidate_status": str(candidate.status),
            "regression_status": candidate.regression_validation.status if candidate.regression_validation else None,
            "semantic_dedup_relation": candidate.dedup_result.llm_relation if candidate.dedup_result else None,
        },
        "provenance": {
            "incremental_release_id": release_id,
            "knowledge_candidate_id": candidate.knowledge_candidate_id,
            "contribution_id": candidate.contribution_id,
            "expert_decision_id": candidate.decision_id,
            "adjudication_id": candidate.adjudication.adjudication_id if candidate.adjudication else None,
            "base_release_id": active_release_id(),
        },
        "final_release_unit": True,
    }


def _row_from_unit(unit: dict[str, Any], vector: list[float]) -> tuple[Any, ...]:
    content = unit["content"]
    policy = unit["retrieval_policy"]
    text = "\n".join(
        [
            str(content["canonical_label"]),
            str(content["summary"]),
            str(content["applicable_context"]),
            " ".join(str(item) for item in content.get("contraindications") or []),
        ]
    )
    risk_level = str(content["risk_level"])
    return (
        unit["release_unit_id"],
        unit["release_unit_id"],
        unit["release_unit_type"],
        unit["dedup_family"],
        unit["canonical_knowledge_type"],
        content["canonical_label"],
        "expert_contribution",
        unit["canonical_knowledge_type"],
        "verified_expert_candidate",
        _risk_scope(risk_level),
        policy["default_route"],
        text,
        json.dumps(vector, separators=(",", ":")),
        json.dumps(unit, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def build_overlay_index(
    path: Path,
    candidates: list[KnowledgeCandidate],
    release_id: str,
    embedding_provider: EmbeddingProvider,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    units = [candidate_release_unit(candidate, release_id) for candidate in candidates]
    texts = [candidate_text(candidate.structured_candidate) for candidate in candidates if candidate.structured_candidate]
    vectors = embedding_provider.embed(texts)
    if len(vectors) != len(units) or any(not vector for vector in vectors):
        raise ValueError("overlay embedding count/dimension mismatch")
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1:
        raise ValueError("overlay embeddings have inconsistent dimensions")
    con = sqlite3.connect(str(path))
    try:
        con.executescript(
            """
            CREATE TABLE overlay_meta (
              retrieval_id TEXT PRIMARY KEY,
              release_unit_id TEXT NOT NULL,
              release_unit_type TEXT NOT NULL,
              dedup_family TEXT NOT NULL,
              canonical_knowledge_type TEXT NOT NULL,
              canonical_label TEXT NOT NULL,
              primary_dataset TEXT NOT NULL,
              primary_knowledge_type TEXT NOT NULL,
              primary_evidence_tier TEXT NOT NULL,
              risk_scope TEXT NOT NULL,
              default_route TEXT NOT NULL,
              retrieval_text TEXT NOT NULL,
              embedding_json TEXT NOT NULL,
              unit_json TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE overlay_fts USING fts5(retrieval_id UNINDEXED, retrieval_text);
            CREATE TABLE overlay_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        for unit, vector in zip(units, vectors):
            row = _row_from_unit(unit, vector)
            con.execute("INSERT INTO overlay_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            con.execute("INSERT INTO overlay_fts VALUES (?,?)", (row[0], row[11]))
        con.executemany(
            "INSERT INTO overlay_info VALUES (?,?)",
            [
                ("release_id", release_id),
                ("embedding_model", embedding_provider.model),
                ("embedding_dimension", str(next(iter(dimensions)))),
                ("row_count", str(len(units))),
            ],
        )
        con.commit()
    finally:
        con.close()
    return units, {
        "status": "complete",
        "row_count": len(units),
        "embedding_model": embedding_provider.model,
        "embedding_dimension": next(iter(dimensions)),
    }


def _matches_filters(row: dict[str, Any], plan: dict[str, Any], *, relax_route: bool = False) -> bool:
    filters = plan.get("metadata_filters") or {}
    mapping = {
        "dedup_family": "dedup_family",
        "primary_knowledge_type": "primary_knowledge_type",
        "primary_evidence_tier": "primary_evidence_tier",
        "risk_scope": "risk_scope",
        "default_route": "default_route",
    }
    for plan_key, row_key in mapping.items():
        allowed = {str(value) for value in filters.get(plan_key) or []}
        if plan_key == "default_route" and relax_route:
            continue
        if allowed and str(row.get(row_key) or "") not in allowed:
            return False
    return True


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return dot / norm if norm else -1.0


def _item(row: dict[str, Any]) -> dict[str, Any]:
    unit = json.loads(row["unit_json"])
    content = unit["content"]
    return {
        "retrieval_id": row["retrieval_id"],
        "release_unit_id": row["release_unit_id"],
        "release_unit_type": row["release_unit_type"],
        "dedup_family": row["dedup_family"],
        "canonical_knowledge_type": row["canonical_knowledge_type"],
        "canonical_label": row["canonical_label"],
        "primary_dataset": row["primary_dataset"],
        "primary_knowledge_type": row["primary_knowledge_type"],
        "primary_evidence_tier": row["primary_evidence_tier"],
        "risk_scope": row["risk_scope"],
        "default_route": row["default_route"],
        "member_count": 1,
        "retrieval_text_chars": len(row["retrieval_text"]),
        "text_preview": row["retrieval_text"][:1200],
        "incremental_release_id": unit["provenance"]["incremental_release_id"],
        "knowledge_candidate_id": unit["provenance"]["knowledge_candidate_id"],
    }


def query_overlay(
    path: Path,
    plan: dict[str, Any],
    query: str,
    query_embedding: list[float],
    *,
    limit: int = 40,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if not path.is_file():
        return {"overlay_sparse": [], "overlay_dense": [], "overlay_label": []}, {"status": "inactive"}
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in con.execute("SELECT * FROM overlay_meta")]
        info = dict(con.execute("SELECT key,value FROM overlay_info").fetchall())
    finally:
        con.close()
    filtered = [row for row in rows if _matches_filters(row, plan)]
    required = set(plan.get("target_families", {}).get("required") or [])
    present = {row["dedup_family"] for row in filtered}
    for row in rows:
        if row["dedup_family"] in required - present and _matches_filters(row, plan, relax_route=True):
            filtered.append(row)
    terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1]
    label_terms = [str(value).lower() for value in plan.get("search_queries", {}).get("label_queries") or []]
    sparse_ranked = sorted(
        filtered,
        key=lambda row: sum(row["retrieval_text"].lower().count(term) for term in terms),
        reverse=True,
    )
    sparse = [_item(row) for row in sparse_ranked if any(term in row["retrieval_text"].lower() for term in terms)][:limit]
    dense_ranked = sorted(
        ((_cosine(query_embedding, json.loads(row["embedding_json"])), row) for row in filtered),
        key=lambda pair: pair[0],
        reverse=True,
    )
    dense = []
    for score, row in dense_ranked[:limit]:
        item = _item(row)
        item["dense_score"] = round(score, 6)
        dense.append(item)
    label = [
        _item(row)
        for row in filtered
        if any(
            term in f"{row['canonical_label']} {row['canonical_knowledge_type']}".lower()
            for term in label_terms
        )
    ][:limit]
    return {
        "overlay_sparse": sparse,
        "overlay_dense": dense,
        "overlay_label": label,
    }, {
        "status": "complete",
        "release_id": info.get("release_id"),
        "embedding_model": info.get("embedding_model"),
        "rows": len(rows),
        "filtered_rows": len(filtered),
        "channel_counts": {"sparse": len(sparse), "dense": len(dense), "label": len(label)},
    }
