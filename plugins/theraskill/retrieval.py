"""Local SQLite FTS retrieval against the active TheraSkillAgent knowledge release."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from typing import Any

from domains.theraskill.hybrid_retrieval import (
    HybridRetrievalError,
    HybridRetrievalPaths,
    TheraSkillHybridRetriever,
)

from .artifacts import ArtifactStore, ArtifactStoreError


class RetrievalError(RuntimeError):
    """Raised when an installed retrieval artifact cannot be queried."""


from theraskill_agent.paths import resource_root, resource_path

PROJECT_ROOT = resource_root()
HYBRID_ARTIFACTS = {
    "sqlite_fts",
    "metadata_v2",
    "compact_label_v1",
    "faiss_shards_manifest",
}


def search_managed_knowledge(query: str, *, skill: str, limit: int) -> dict[str, Any] | None:
    from theraskill_agent.resources import active_index
    from theraskill_agent.paths import data_root
    from domains.theraskill.hybrid_retrieval import selected_retrieval_paths, search_policy_lexical
    selected = active_index()
    if not selected:
        return None
    directory, manifest = selected
    paths = selected_retrieval_paths(resource_path("configs/theraskill/retrieval.yaml"), data_root() / "cache/retrieval")
    warnings = []
    try:
        bundle = TheraSkillHybridRetriever(PROJECT_ROOT, paths).search(query, skill=skill, top_k=limit)
        mode = "hybrid_v3"
    except HybridRetrievalError as exc:
        bundle = search_policy_lexical(PROJECT_ROOT, paths.sqlite_fts, query, skill=skill, top_k=limit)
        mode = "policy_lexical_fallback"
        if manifest["input"]["mode"] != "lexical":
            warnings.append(str(exc))
    matches = list(bundle.get("items") or [])
    return {"query": query, "release_id": manifest["index_id"], "knowledge_sha256": manifest["input"]["knowledge_sha256"],
            "retrieval_mode": mode, "skill": skill, "matches": matches, "count": len(matches),
            "warnings": warnings, "diagnostics": {"policy_checks": bundle.get("policy_checks") or {}}}


def search_knowledge(
    query: str,
    *,
    skill: str = "emotional-support",
    release_id: str = "current",
    limit: int = 8,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Prefer policy-aware hybrid retrieval and report lexical degradation."""
    try:
        from theraskill_agent.config import load_env

        os.environ.update(load_env())
    except Exception:
        pass
    if store is None and release_id == "current":
        managed = search_managed_knowledge(query, skill=skill, limit=limit)
        if managed is not None:
            return managed
    artifact_store = store or ArtifactStore()
    manifest = artifact_store.load_manifest(release_id)
    if HYBRID_ARTIFACTS.issubset(manifest.artifacts):
        try:
            retriever = TheraSkillHybridRetriever(
                PROJECT_ROOT,
                HybridRetrievalPaths(
                    sqlite_fts=artifact_store.resolve("sqlite_fts", release_id),
                    metadata_v2=artifact_store.resolve("metadata_v2", release_id),
                    compact_label=artifact_store.resolve("compact_label_v1", release_id),
                    faiss_shards=artifact_store.resolve(
                        "faiss_shards_manifest", release_id
                    ).parent,
                    model_config=resource_path("configs/theraskill/retrieval.yaml"),
                    cache_root=artifact_store.root
                    / "cache/retrieval"
                    / manifest.release_id,
                ),
            )
            bundle = retriever.search(query, skill=skill, top_k=limit)
            matches = list(bundle.get("items") or [])
            return {
                "query": str(query).strip(),
                "release_id": manifest.release_id,
                "retrieval_mode": "hybrid_v3",
                "skill": skill,
                "matches": matches,
                "count": len(matches),
                "diagnostics": {
                    "planner_status": (bundle.get("planner_trace") or {}).get(
                        "planner_status"
                    ),
                    "policy_checks": bundle.get("policy_checks") or {},
                    "quality_checks": bundle.get("quality_checks") or {},
                    "channel_counts": bundle.get("channel_counts") or {},
                },
            }
        except HybridRetrievalError as exc:
            degraded_reason = str(exc)
    else:
        missing = sorted(HYBRID_ARTIFACTS - set(manifest.artifacts))
        degraded_reason = f"release lacks hybrid artifacts: {', '.join(missing)}"

    result = search_local(
        query,
        release_id=release_id,
        limit=limit,
        store=artifact_store,
    )
    result["retrieval_mode"] = "lexical_fallback"
    result["skill"] = skill
    result["warnings"] = [degraded_reason]
    return result


def search_local(
    query: str,
    *,
    release_id: str = "current",
    limit: int = 8,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Search the release-declared SQLite FTS artifact without external paths."""
    clean_query = str(query or "").strip()
    if not clean_query:
        raise RetrievalError("query is empty")
    limit = max(1, min(int(limit), 50))
    artifact_store = store or ArtifactStore()
    manifest = artifact_store.load_manifest(release_id)
    try:
        db_path = artifact_store.resolve("sqlite_fts", release_id)
    except ArtifactStoreError:
        try:
            db_path = artifact_store.resolve("retrieval_sqlite", release_id)
        except ArtifactStoreError as exc:
            raise RetrievalError("release does not declare a sqlite_fts artifact") from exc
    rows = _query_fts(db_path, clean_query, limit)
    return {
        "query": clean_query,
        "release_id": manifest.release_id,
        "matches": rows,
        "count": len(rows),
    }


def _query_fts(db_path: Path, query: str, limit: int) -> list[dict[str, Any]]:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT f.retrieval_id, f.retrieval_text,
                           m.release_unit_id, m.release_unit_type,
                           m.dedup_family, m.primary_knowledge_type,
                           m.risk_scope, m.default_route,
                           bm25(f) AS score
                    FROM retrieval_fts AS f
                    JOIN retrieval_meta AS m USING (retrieval_id)
                    WHERE retrieval_fts MATCH ?
                      AND COALESCE(m.retrieval_eligible, 1) = 1
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                escaped = query.replace("%", "\\%").replace("_", "\\_")
                rows = connection.execute(
                    """
                    SELECT f.retrieval_id, f.retrieval_text,
                           m.release_unit_id, m.release_unit_type,
                           m.dedup_family, m.primary_knowledge_type,
                           m.risk_scope, m.default_route,
                           0.0 AS score
                    FROM retrieval_fts AS f
                    JOIN retrieval_meta AS m USING (retrieval_id)
                    WHERE f.retrieval_text LIKE ? ESCAPE '\\'
                      AND COALESCE(m.retrieval_eligible, 1) = 1
                    LIMIT ?
                    """,
                    (f"%{escaped}%", limit),
                ).fetchall()
    except sqlite3.Error as exc:
        raise RetrievalError(f"cannot query retrieval index: {db_path}") from exc
    return [dict(row) for row in rows]
