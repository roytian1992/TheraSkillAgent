"""Local SQLite FTS retrieval against the active TheraSkillAgent knowledge release."""

from __future__ import annotations

import sqlite3
import os
import hashlib
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from domains.theraskill.hybrid_retrieval import (
    HybridRetrievalError,
    HybridRetrievalPaths,
    TheraSkillHybridRetriever,
)

from .artifacts import ArtifactStore, ArtifactStoreError
from theraskill_agent.query_understanding import understand_query


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


def _hydrate_managed_matches(db_path: Path, matches: list[dict[str, Any]], *,
                             index_id: str, knowledge_sha256: str,
                             max_chars: int = 6000) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach bounded source passages from the SAME immutable search index.

    Dense and compact-label recall intentionally return metadata only. Looking
    up their final top-k rows here avoids a full-text join during broad recall.
    Our builder aligns metadata/FTS rowids; verify IDs rather than assuming it.
    Historical unaligned indexes get one bounded same-index fallback query.
    """
    if not matches:
        return [], []
    ids = list(dict.fromkeys(str(item.get('retrieval_id') or '') for item in matches if item.get('retrieval_id')))
    if not ids:
        return [], ['Retrieved matches lacked source IDs; no grounded passages were returned.']
    warnings: list[str] = []
    metadata: dict[str, dict[str, Any]] = {}
    passages: dict[str, str] = {}
    try:
        with closing(sqlite3.connect(f'{db_path.resolve().as_uri()}?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            slots = ','.join('?' for _ in ids)
            metadata = {row['retrieval_id']: dict(row) for row in db.execute(
                f'SELECT rowid AS text_rowid,* FROM retrieval_meta WHERE retrieval_id IN ({slots})', ids)}
            rowids = [row['text_rowid'] for row in metadata.values()]
            if rowids:
                rows = db.execute(f'SELECT retrieval_id,substr(retrieval_text,1,?) AS passage FROM retrieval_fts '
                                  f'WHERE rowid IN ({",".join("?" for _ in rowids)})', [max_chars, *rowids])
                passages = {row['retrieval_id']: row['passage'] for row in rows if row['retrieval_id'] in metadata}
            missing = [key for key in metadata if key not in passages]
            if missing:
                deadline = time.monotonic() + 2.0
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                try:
                    # retrieval_id is UNINDEXED in historical FTS layouts.
                    # This is a bounded fallback only, never a second release.
                    rows = db.execute(f'SELECT retrieval_id,substr(retrieval_text,1,?) AS passage FROM retrieval_fts '
                                      f'WHERE retrieval_id IN ({",".join("?" for _ in missing)})', [max_chars, *missing]).fetchall()
                    passages.update({row['retrieval_id']: row['passage'] for row in rows})
                except sqlite3.OperationalError as exc:
                    if 'interrupted' not in str(exc).lower():
                        raise
                    warnings.append('Source lookup exceeded its 2-second budget; unverified passages were omitted. Rebuild the index to enable direct row lookup.')
                finally:
                    db.set_progress_handler(None, 0)
    except sqlite3.Error as exc:
        raise RetrievalError('Cannot load source passages from the selected knowledge index') from exc
    hydrated = []
    for raw in matches:
        key = str(raw.get('retrieval_id') or '')
        row, passage = metadata.get(key), passages.get(key)
        if not row or not isinstance(passage, str) or not passage.strip() or not row.get('retrieval_eligible'):
            continue
        # A cached/overlay result must not borrow another unit's text merely
        # because its retrieval ID happens to collide in this release.
        if any(raw.get(field) and raw[field] != row.get(field) for field in ('release_unit_id', 'dedup_family', 'default_route')):
            continue
        if raw.get('unit_id') and raw['unit_id'] != row.get('release_unit_id'):
            continue
        item = dict(raw)
        item.update(release_unit_id=row['release_unit_id'], retrieval_text=passage,
                    text_preview=passage[:1200], source_index_id=index_id,
                    knowledge_sha256=knowledge_sha256,
                    text_sha256=hashlib.sha256(passage.encode()).hexdigest(),
                    text_truncated=int(row.get('retrieval_text_chars') or len(passage)) > len(passage))
        # Preserve per-skill allowed_use/forbidden_use/evidence_role fields.
        hydrated.append(item)
    if len(hydrated) != len(matches):
        warnings.append(f'{len(matches) - len(hydrated)} retrieved matches lacked verified source text in the selected index and were omitted.')
    return hydrated, warnings


def search_managed_knowledge(query: str, *, skill: str, limit: int, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
    from theraskill_agent.resources import active_index
    from theraskill_agent.paths import data_root
    from domains.theraskill.hybrid_retrieval import selected_retrieval_paths, search_policy_lexical
    selected = active_index()
    if not selected:
        return None
    _, manifest = selected
    understanding = understand_query(query, skill=skill, filters=filters)
    effective_query = understanding['normalized_query']
    paths = selected_retrieval_paths(resource_path("configs/theraskill/retrieval.yaml"), data_root() / "cache/retrieval", selection=selected)
    warnings = list(understanding['warnings'])
    filter_options = {'filters': understanding['filters']} if understanding['filters'] else {}
    try:
        bundle = TheraSkillHybridRetriever(PROJECT_ROOT, paths).search(effective_query, skill=skill, top_k=limit, **filter_options)
        mode = "hybrid_v3"
    except HybridRetrievalError as exc:
        bundle = search_policy_lexical(PROJECT_ROOT, paths.sqlite_fts, effective_query, skill=skill, top_k=limit, **filter_options)
        mode = "policy_lexical_fallback"
        if manifest["input"]["mode"] != "lexical":
            warnings.append(str(exc))
    if bundle.get('source_index') and Path(bundle['source_index']).resolve() != paths.sqlite_fts.resolve():
        raise RetrievalError('Search results refer to a different knowledge index')
    hydration_started = time.monotonic()
    matches, hydration_warnings = _hydrate_managed_matches(paths.sqlite_fts, list(bundle.get("items") or []),
        index_id=manifest['index_id'], knowledge_sha256=manifest['input']['knowledge_sha256'])
    warnings.extend(hydration_warnings)
    return {"query": query, "release_id": manifest["index_id"], "index_id": manifest["index_id"], "knowledge_sha256": manifest["input"]["knowledge_sha256"],
            "retrieval_mode": mode, "skill": skill, "matches": matches, "count": len(matches),
            "query_understanding": understanding, "filters": understanding['filters'],
            "warnings": list(dict.fromkeys([*warnings, *(bundle.get('warnings') or [])])), "diagnostics": {"planner_status": (bundle.get("planner_trace") or {}).get("planner_status"),
                                                   "timings": {**(bundle.get('timings') or {}), 'source_hydration_seconds': round(time.monotonic() - hydration_started, 6)},
                                                   "planner_timings": (bundle.get('planner_trace') or {}).get('timings') or {},
                                                   "policy_checks": bundle.get("policy_checks") or {},
                                                   "quality_checks": bundle.get("quality_checks") or {},
                                                   "plan":bundle.get('llm_retrieval_plan') or {},
                                                   "scope_trace":bundle.get('structured_filter') or {},
                                                   "channel_counts": bundle.get("channel_counts") or {}}}


def search_knowledge(
    query: str,
    *,
    skill: str = "emotional-support",
    release_id: str = "current",
    limit: int = 8,
    filters: dict[str, Any] | None = None,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Prefer policy-aware hybrid retrieval and report lexical degradation."""
    try:
        from theraskill_agent.config import load_env

        os.environ.update(load_env())
    except Exception:
        pass
    if store is None and release_id == "current":
        managed = search_managed_knowledge(query, skill=skill, limit=limit, filters=filters)
        if managed is not None:
            return managed
    artifact_store = store or ArtifactStore()
    manifest = artifact_store.load_manifest(release_id)
    understanding = understand_query(query, skill=skill, filters=filters)
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
            filter_options = {'filters': understanding['filters']} if understanding['filters'] else {}
            bundle = retriever.search(understanding["normalized_query"], skill=skill, top_k=limit, **filter_options)
            matches = list(bundle.get("items") or [])
            return {
                "query": str(query).strip(),
                "release_id": manifest.release_id,
                "retrieval_mode": "hybrid_v3",
                "skill": skill,
                "matches": matches,
                "count": len(matches),
                "query_understanding": understanding,
                "filters": understanding['filters'],
                "warnings": list(dict.fromkeys([*understanding['warnings'], *(bundle.get('warnings') or [])])),
                "diagnostics": {
                    "timings": bundle.get('timings') or {},
                    "planner_timings": (bundle.get('planner_trace') or {}).get('timings') or {},
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
        skill=skill,
        filters=filters,
        store=artifact_store,
    )
    result["retrieval_mode"] = "lexical_fallback"
    result["skill"] = skill
    result["query_understanding"] = understanding
    result["warnings"] = [degraded_reason, *understanding['warnings']]
    return result


def search_local(
    query: str,
    *,
    release_id: str = "current",
    limit: int = 8,
    skill: str | None = None,
    filters: dict[str, Any] | None = None,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Search the release-declared SQLite FTS artifact without external paths."""
    understanding = understand_query(query, skill=skill, filters=filters)
    clean_query = understanding["normalized_query"]
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
    rows = _query_fts(db_path, understanding, limit)
    return {
        "query": str(query or ""),
        "release_id": manifest.release_id,
        "matches": rows,
        "count": len(rows),
        "query_understanding": understanding,
    }


def _query_fts(db_path: Path, understanding: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    from theraskill_agent.indexing.search import lexical_candidates

    try:
        return lexical_candidates(db_path, understanding, max(limit * 6, 80))[:limit]
    except (sqlite3.Error, ValueError) as exc:
        raise RetrievalError(f"cannot query retrieval index: {exc}") from exc
