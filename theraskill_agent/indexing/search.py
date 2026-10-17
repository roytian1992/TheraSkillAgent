"""Shared policy scope, Chinese/English lexical recall and relevance ranking."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


FILTER_FIELDS = {
    'dedup_family', 'risk_scope', 'default_route', 'primary_knowledge_type',
    'primary_evidence_tier', 'primary_dataset', 'primary_language',
}


def compile_scope(connection, understanding, *, table='retrieval_meta', alias='m'):
    if table not in {'retrieval_meta', 'metadata_v2', 'compact_label_meta'} or alias != 'm':
        raise ValueError('unsupported knowledge metadata table')
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
    clauses, params = [f'{alias}.retrieval_eligible=1'], []
    explicit = understanding.get('filters') or {}
    for key in explicit:
        if key not in FILTER_FIELDS or key not in columns:
            raise ValueError(f'index cannot enforce knowledge filter {key}; rebuild the index')
    filters = {**(understanding.get('automatic_filters') or {}), **explicit}
    for key, values in filters.items():
        if key in FILTER_FIELDS and key in columns and values:
            clauses.append(f"{alias}.{key} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    route = understanding.get('route') or {}
    families = route.get('allowed_families') or []
    if families:
        clauses.append(f"{alias}.dedup_family IN ({','.join('?' for _ in families)})")
        params.extend(families)
    for key, values in [('dedup_family', route.get('excluded_families')), ('default_route', route.get('excluded_routes'))]:
        if values and key in columns:
            clauses.append(f"COALESCE({alias}.{key}, '') NOT IN ({','.join('?' for _ in values)})")
            params.extend(values)
    return clauses, params


def _match(terms):
    return ' OR '.join('"' + str(term).replace('"', ' ') + '"' for term in terms if str(term).strip())


def rank_candidates(hits, understanding):
    """Fuse channel ranks; a small route prior cannot drown out relevance."""
    preferred = (understanding.get('route') or {}).get('preferred_families') or []
    priority = {family: rank for rank, family in enumerate(preferred)}
    for hit in hits:
        family = hit.get('dedup_family')
        rank = priority.get(family, len(priority))
        hit['route_rank'] = rank
        hit['route_reason'] = 'skill_preferred_family' if family in priority else 'general_knowledge_match'
        relevance = hit.get('relevance_score', 0.0)
        hit['ranking_score'] = relevance * (1 + 0.08 / (rank + 1))
    return sorted(hits, key=lambda hit: (-hit['ranking_score'], str(hit.get('retrieval_id'))))


def lexical_candidates(db_path: str | Path, understanding: dict[str, Any], limit=80):
    """Filter before recall; use a token index for Chinese when available."""
    with closing(sqlite3.connect(f'{Path(db_path).resolve().as_uri()}?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        clauses, params = compile_scope(db, understanding)
        where = ' AND '.join(clauses)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        terms = understanding.get('lexical_terms') or []
        channels = []
        if terms:
            rows = db.execute(f'''SELECT m.*,f.retrieval_text,bm25(retrieval_fts) AS score
                FROM retrieval_fts f JOIN retrieval_meta m USING(retrieval_id)
                WHERE retrieval_fts MATCH ? AND {where} ORDER BY score, m.retrieval_id LIMIT ?''',
                (_match(terms[:80]), *params, limit)).fetchall()
            channels.append(('bm25', rows))
        cjk = understanding.get('cjk_terms') or []
        indexed_cjk = [term for term in cjk if len(term) in (2, 3)]
        if indexed_cjk and 'retrieval_cjk_fts' in tables:
            rows = db.execute(f'''SELECT m.*,f.retrieval_text,bm25(retrieval_cjk_fts) AS score
                FROM retrieval_cjk_fts c JOIN retrieval_meta m USING(retrieval_id)
                JOIN retrieval_fts f USING(retrieval_id)
                WHERE retrieval_cjk_fts MATCH ? AND {where} ORDER BY score, m.retrieval_id LIMIT ?''',
                (_match(indexed_cjk), *params, limit)).fetchall()
            channels.append(('cjk_bm25', rows))
        elif cjk:
            # Historical artifacts lack the segmented token index. This path
            # trades latency for compatibility; rebuilding enables indexed recall.
            like = ' OR '.join('f.retrieval_text LIKE ?' for _ in cjk[:20])
            rows = db.execute(f'''SELECT m.*,f.retrieval_text,0.0 AS score
                FROM retrieval_fts f JOIN retrieval_meta m USING(retrieval_id)
                WHERE {where} AND ({like}) ORDER BY m.retrieval_id LIMIT ?''',
                (*params, *(f'%{term}%' for term in cjk[:20]), limit)).fetchall()
            channels.append(('legacy_cjk_substring', rows))
        merged = {}
        for name, rows in channels:
            for rank, row in enumerate(rows, 1):
                item = merged.setdefault(row['retrieval_id'], {**dict(row), 'relevance_score': 0.0, 'match_channels': []})
                item['relevance_score'] += 1 / (60 + rank)
                item['match_channels'].append(name)
        return rank_candidates(list(merged.values()), understanding)[:limit]


def matches_scope(item, understanding):
    """Also check overlay rows, which are not in the base metadata table."""
    for key, values in {**understanding.get('automatic_filters', {}), **understanding.get('filters', {})}.items():
        if values and str(item.get(key) or '') not in values:
            return False
    route = understanding.get('route') or {}
    family = item.get('dedup_family')
    if route.get('allowed_families') and family not in route['allowed_families']:
        return False
    return family not in (route.get('excluded_families') or []) and item.get('default_route') not in (route.get('excluded_routes') or [])


def filter_candidates(db_path, candidates, understanding):
    """Hydrate metadata and enforce scope before fusion/quotas truncate hits."""
    if not candidates:
        return []
    metadata = {}
    with closing(sqlite3.connect(f'{Path(db_path).resolve().as_uri()}?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        ids = list({item['retrieval_id'] for item in candidates})
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            for row in db.execute(f"SELECT * FROM retrieval_meta WHERE retrieval_id IN ({','.join('?' for _ in batch)})", batch):
                metadata[row['retrieval_id']] = dict(row)
    result = []
    for item in candidates:
        merged = {**item, **metadata.get(item['retrieval_id'], {})}
        if matches_scope(merged, understanding):
            result.append(merged)
    return result
