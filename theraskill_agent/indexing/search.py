"""Shared policy scope, Chinese/English lexical recall and relevance ranking."""
from __future__ import annotations

import sqlite3
import time
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
    """Keep a matching primary evidence item, then order the rest by relevance."""
    preferred = (understanding.get('route') or {}).get('preferred_families') or []
    priority = {family: rank for rank, family in enumerate(preferred)}
    for hit in hits:
        family = hit.get('dedup_family')
        rank = priority.get(family, len(priority))
        hit['route_rank'] = rank
        hit['route_reason'] = 'skill_preferred_family' if family in priority else 'general_knowledge_match'
        relevance = hit.get('relevance_score', 0.0)
        hit['ranking_score'] = relevance * (1 + 0.08 / (rank + 1))
    ranked = sorted(hits, key=lambda hit: (-hit['ranking_score'], str(hit.get('retrieval_id'))))
    # Response examples outnumber method/rubric records by several orders of
    # magnitude. One matching item from the skill's primary family keeps those
    # examples from displacing the evidence needed to perform the actual task.
    # This only selects recalled matches; an absent family is never fabricated.
    if preferred:
        anchor = next((hit for hit in ranked if hit.get('dedup_family') == preferred[0]), None)
        if anchor is not None:
            anchor['selection_reason'] = 'skill_primary_evidence'
            ranked = [anchor] + [hit for hit in ranked if hit is not anchor]
    return ranked


def language_scoped_database(db_path, understanding):
    db_path = Path(db_path).expanduser().resolve()
    language_filters = (understanding.get('filters') or {}).get('primary_language') or (understanding.get('automatic_filters') or {}).get('primary_language') or []
    # English is a small partition in the reviewed release. Prefer its compact
    # sidecar so language-scoped queries never open the multi-gigabyte mixed
    # corpus on shared storage.
    if language_filters == ['en'] and (db_path.parent / 'retrieval_en.sqlite').is_file():
        # Confirm the primary index still exposes the filter column before
        # using the sidecar. This keeps schema-mismatch failures actionable
        # (callers must rebuild the index) instead of silently using stale
        # sidecar metadata.
        with closing(sqlite3.connect(f'{db_path.as_uri()}?mode=ro', uri=True)) as primary_db:
            columns = {row[1] for row in primary_db.execute('PRAGMA table_info(retrieval_meta)')}
        if 'primary_language' in columns:
            db_path = db_path.parent / 'retrieval_en.sqlite'
    return db_path


def lexical_candidates(db_path: str | Path, understanding: dict[str, Any], limit=80, *, trace=None):
    """Filter before recall; use a token index for Chinese when available."""
    db_path = language_scoped_database(db_path, understanding)
    with closing(sqlite3.connect(f'{Path(db_path).resolve().as_uri()}?mode=ro', uri=True)) as db:
        db.execute('PRAGMA cache_size=-65536')
        db.row_factory = sqlite3.Row
        fts_deadline = time.monotonic() + 6.0
        if trace is not None:
            trace.update(fts_budget_seconds=6.0, timed_out_queries=0, skipped_queries=0)
        def recall(sql, values=()):
            if ' MATCH ' not in sql.upper():
                return db.execute(sql, values).fetchall()
            now = time.monotonic()
            if now >= fts_deadline:
                if trace is not None:
                    trace['skipped_queries'] += 1
                return []
            deadline = min(fts_deadline, now + 2.0)
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            try:
                return db.execute(sql, values).fetchall()
            except sqlite3.OperationalError as exc:
                if str(exc).lower() != 'interrupted':
                    raise
                if trace is not None:
                    trace['timed_out_queries'] += 1
                return []
            finally:
                db.set_progress_handler(None, 0)
        clauses, params = compile_scope(db, understanding)
        where = ' AND '.join(clauses)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        terms = understanding.get('lexical_terms') or []
        language_filters = (understanding.get('filters') or {}).get('primary_language') or (understanding.get('automatic_filters') or {}).get('primary_language') or []
        english_partition = language_filters == ['en'] and 'retrieval_en_fts' in tables
        text_table = 'retrieval_en_fts' if english_partition else 'retrieval_fts'
        meta_table = 'retrieval_en_meta' if english_partition and 'retrieval_en_meta' in tables else 'retrieval_meta'
        channels = []
        preferred = (understanding.get('route') or {}).get('preferred_families') or []
        primary = preferred[0] if preferred else None
        primary_pool = None
        def recall_primary(match, *, cjk=False):
            nonlocal primary_pool
            if not primary:
                return []
            table = 'retrieval_cjk_fts' if cjk else text_table
            alias = 'c' if cjk else 'f'
            # Small families need a scoped recall path. Querying the global FTS
            # index with a bounded family-ID set keeps the lookup on the
            # inverted index and avoids materializing scattered FTS rows from a
            # multi-gigabyte network-mounted database. The global BM25 score is
            # sufficient here: ranking is recomputed by the channel merge below.
            # Use the bounded-ID path whenever the scoped primary pool is
            # small. A language or route filter can reduce a large family to
            # a few thousand rows (for example English style records), while
            # an unfiltered style pool still correctly uses the normal FTS
            # path.
            if primary_pool is None:
                # Only distinguish a small scoped pool from a large one. Do
                # not materialize millions of example rows to make that choice.
                scoped_primary = recall(
                    f'SELECT m.* FROM {meta_table} m WHERE {where} AND m.dedup_family=? LIMIT 50001',
                    (*params, primary),
                )
                primary_pool = {row['retrieval_id']: dict(row) for row in scoped_primary}
            if len(primary_pool) <= 50000 and 'retrieval_cjk_fts' in tables:
                ids = list(primary_pool)
                if not ids:
                    return []
                matches = []
                # Keep each IN clause below SQLite's conservative variable
                # limit. Each chunk is still answered by the FTS postings list.
                table_name = 'retrieval_cjk_fts' if cjk else 'retrieval_fts'
                if cjk:
                    # Filtering a large CJK postings list with hundreds of
                    # string IDs inside SQLite can be slower than taking a
                    # bounded ranked page and applying the small family set in
                    # Python. Hydrate only the matching rowids afterwards.
                    ranked = recall('''SELECT rowid,retrieval_id,bm25(retrieval_cjk_fts) AS score
                        FROM retrieval_cjk_fts WHERE retrieval_cjk_fts MATCH ? ORDER BY rank LIMIT ?''',
                        (match, max(limit * 64, 512)))
                    allowed = set(ids)
                    ranked = [row for row in ranked if row['retrieval_id'] in allowed][:min(limit, 8)]
                    if not ranked:
                        return []
                    rowids = [row['rowid'] for row in ranked]
                    text_by_id = {row['retrieval_id']: row['retrieval_text'] for row in recall(
                        f'SELECT retrieval_id,retrieval_text FROM retrieval_fts WHERE rowid IN ({",".join("?" for _ in rowids)})', rowids
                    )}
                    return [{**primary_pool[row['retrieval_id']], 'retrieval_text': text_by_id[row['retrieval_id']], 'score': row['score']}
                            for row in ranked if row['retrieval_id'] in text_by_id]
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ','.join('?' for _ in chunk)
                    if cjk:
                        rows = recall(f'''SELECT c.retrieval_id,f.retrieval_text,bm25(retrieval_cjk_fts) AS score
                            FROM retrieval_cjk_fts c JOIN retrieval_fts f
                              ON f.rowid=c.rowid AND f.retrieval_id=c.retrieval_id
                            WHERE retrieval_cjk_fts MATCH ? AND c.retrieval_id IN ({placeholders})
                            ORDER BY c.rank LIMIT ?''', (match, *chunk, min(limit, 8)))
                    elif text_table == 'retrieval_en_fts':
                        rows = recall(f'''SELECT f.retrieval_id,f.retrieval_text,bm25(retrieval_en_fts) AS score
                            FROM retrieval_en_fts f
                            WHERE retrieval_en_fts MATCH ? AND f.retrieval_id IN ({placeholders})
                            ORDER BY f.rank LIMIT ?''', (match, *chunk, min(limit, 8)))
                    else:
                        rows = recall(f'''SELECT f.retrieval_id,f.retrieval_text,bm25(retrieval_fts) AS score
                            FROM retrieval_fts f
                            WHERE retrieval_fts MATCH ? AND f.retrieval_id IN ({placeholders})
                            ORDER BY f.rank LIMIT ?''', (match, *chunk, min(limit, 8)))
                    matches.extend(rows)
                matches.sort(key=lambda row: row['score'])
                return [{**primary_pool[row['retrieval_id']], 'retrieval_text': row['retrieval_text'], 'score': row['score']}
                        for row in matches[:min(limit, 8)]]
            joins = f'{table} {alias} JOIN retrieval_meta m USING(retrieval_id)'
            order = f'{alias}.rank'
            if cjk:
                joins += ' CROSS JOIN retrieval_fts f ON f.rowid=c.rowid AND f.retrieval_id=c.retrieval_id'
            return recall(f'''SELECT m.*,f.retrieval_text,bm25({table}) AS score
                FROM {joins} WHERE {table} MATCH ? AND {where} AND m.dedup_family=?
                ORDER BY {order} LIMIT ?''', (match, *params, primary, min(limit, 8)))
        if terms:
            if english_partition:
                # Let FTS produce a small ranked page first, then hydrate and
                # apply metadata filters by primary-key IDs. Joining the
                # language partition to the multi-gigabyte metadata table
                # before MATCH can make SQLite scan the network-mounted table.
                fts_rows = recall('''SELECT retrieval_id,retrieval_text,bm25(retrieval_en_fts) AS score
                    FROM retrieval_en_fts WHERE retrieval_en_fts MATCH ? ORDER BY rank LIMIT ?''',
                    (_match(terms[:80]), max(limit * 8, 80)))
                metadata = {}
                ids = [row['retrieval_id'] for row in fts_rows]
                if ids:
                    for start in range(0, len(ids), 500):
                        chunk = ids[start:start + 500]
                        metadata.update({row['retrieval_id']: dict(row) for row in recall(
                            f'SELECT * FROM {meta_table} WHERE retrieval_id IN ({",".join("?" for _ in chunk)})', chunk
                        )})
                rows = []
                for row in fts_rows:
                    item = metadata.get(row['retrieval_id'])
                    if item and matches_scope({**item, 'retrieval_text': row['retrieval_text']}, understanding):
                        rows.append({**item, 'retrieval_text': row['retrieval_text'], 'score': row['score']})
            else:
                rows = recall(f'''SELECT m.*,f.retrieval_text,bm25({text_table}) AS score
                    FROM {text_table} f JOIN retrieval_meta m ON m.retrieval_id=f.retrieval_id
                    WHERE {text_table} MATCH ? AND {where} ORDER BY f.rank LIMIT ?''',
                    (_match(terms[:80]), *params, limit))
            channels.append(('bm25', rows))
            if primary and not any(row['dedup_family'] == primary for row in rows):
                # Scope inside recall: otherwise a large style corpus can fill
                # every candidate slot before the much smaller rubric is seen.
                primary_rows = recall_primary(_match(terms[:80]))
                channels.append(('primary_bm25', primary_rows))
        cjk = understanding.get('cjk_terms') or []
        indexed_cjk = [term for term in cjk if len(term) in (2, 3)]
        # Translated CJK concepts help cross-language recall for unfiltered or
        # Chinese searches. When the caller explicitly restricts to English,
        # probing the full CJK corpus would defeat the language partition and
        # can dominate latency on a network-mounted index.
        if indexed_cjk and 'retrieval_cjk_fts' in tables and not english_partition:
            # Our builder inserts both FTS tables in the same source order.
            # retrieval_id is UNINDEXED in FTS: joining by it alone can turn
            # every Chinese match into a scan of the entire text corpus.
            # First rank a bounded rowid page in the compact token index, then
            # hydrate only those rows. Doing the text join before ORDER BY rank
            # makes SQLite touch the multi-gigabyte response FTS table for every
            # posting on a network mount.
            cjk_rows = recall('''SELECT rowid,retrieval_id,bm25(retrieval_cjk_fts) AS score
                FROM retrieval_cjk_fts WHERE retrieval_cjk_fts MATCH ? ORDER BY rank LIMIT ?''',
                (_match(indexed_cjk), limit))
            rows = []
            ids = [row['retrieval_id'] for row in cjk_rows]
            metadata = {}
            text_by_id = {}
            if ids:
                placeholders = ','.join('?' for _ in ids)
                metadata = {row['retrieval_id']: dict(row) for row in recall(
                    f'SELECT * FROM retrieval_meta WHERE retrieval_id IN ({placeholders})', ids
                )}
                rowids = [row['rowid'] for row in cjk_rows]
                text_by_id = {row['retrieval_id']: row['retrieval_text'] for row in recall(
                    f'SELECT retrieval_id,retrieval_text FROM retrieval_fts WHERE rowid IN ({",".join("?" for _ in rowids)})', rowids
                )}
            for row in cjk_rows:
                item = metadata.get(row['retrieval_id'])
                text = text_by_id.get(row['retrieval_id'])
                if item and text is not None and matches_scope({**item, 'retrieval_text': text}, understanding):
                    rows.append({**item, 'retrieval_text': text, 'score': row['score']})
            channels.append(('cjk_bm25', rows))
            if primary and not any(row['dedup_family'] == primary for row in rows):
                primary_rows = recall_primary(_match(indexed_cjk), cjk=True)
                channels.append(('primary_cjk_bm25', primary_rows))
        elif cjk:
            # Historical artifacts lack the segmented token index. This path
            # trades latency for compatibility; rebuilding enables indexed recall.
            like = ' OR '.join('f.retrieval_text LIKE ?' for _ in cjk[:20])
            rows = recall(f'''SELECT m.*,f.retrieval_text,0.0 AS score
                FROM retrieval_fts f JOIN retrieval_meta m USING(retrieval_id)
                WHERE {where} AND ({like}) ORDER BY m.retrieval_id LIMIT ?''',
                (*params, *(f'%{term}%' for term in cjk[:20]), limit))
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
