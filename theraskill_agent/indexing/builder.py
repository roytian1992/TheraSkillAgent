"""Streaming, resumable index construction with immutable completed outputs."""
from __future__ import annotations
import json
import math
import os
import sqlite3
import shutil
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from contextlib import closing
from . import labels, metadata
from .common import digest, identity, read_json, write_json, safe_path, exclusive
from .embedding import fingerprint, resolve, require_compatible
from .knowledge import PROJECTION_VERSION, rows
from ..query_understanding import understand_query, cjk_index_terms
from .search import compile_scope, lexical_candidates, rank_candidates, matches_scope

SCHEMA = 'theraskill_index_v1'
DB = 'phase6_retrieval_fts.sqlite'


class BuildCancelled(RuntimeError):
    pass


def _check(cancel):
    if cancel and cancel():
        raise BuildCancelled('index build cancelled; resume with the same input and direct embedding configuration')


def _counts():
    return dict(total=0, eligible=0, indexed=0, excluded=0, embedding_eligible=0, vectors=0, failed=0)


def plan_index(knowledge, *, mode='lexical', mapping=None, embedding_config=None):
    if mode not in {'lexical', 'hybrid'}:
        raise ValueError('mode must be lexical or hybrid')
    path = Path(knowledge).expanduser().resolve()
    counts = _counts()
    for row in rows(path, mapping):
        counts['total'] += 1
        counts['eligible'] += int(row['retrieval_eligible'])
        counts['embedding_eligible'] += int(row['embedding_eligible'])
    counts['excluded'] = counts['total'] - counts['eligible']
    profile = resolve(embedding_config) if mode == 'hybrid' else None
    return {'mode': mode, 'knowledge_sha256': digest(path), 'source_bytes': path.stat().st_size,
            'counts': counts, 'embedding': fingerprint(profile) if profile else None,
            'embedding_requests_estimate': math.ceil(counts['embedding_eligible'] / max(1, int(profile.get('batch_size', 32)))) if profile else 0,
            'disk_estimate_bytes': path.stat().st_size * 6, 'disk_estimate_note': 'rough lexical estimate; vectors require additional space depending on dimension',
            'network_requests_made': 0}


def _lexical(source, output, mapping, progress, cancel):
    # An interrupted lexical phase is safely rebuilt. Completed embedding batches
    # are retained separately; no half-committed sidecars are ever activated.
    for name in (DB, 'metadata_v2.sqlite', 'compact_label_v1.sqlite'):
        for suffix in ('', '-wal', '-shm'):
            (output / (name + suffix)).unlink(missing_ok=True)
    counts = _counts()
    with closing(sqlite3.connect(output / DB)) as db, closing(metadata.connect_db(output / 'metadata_v2.sqlite', False)) as meta, closing(labels.connect(output / 'compact_label_v1.sqlite', False)) as label:
        db.executescript('''
            CREATE TABLE retrieval_meta (
              retrieval_id TEXT PRIMARY KEY, release_unit_id TEXT NOT NULL UNIQUE,
              release_unit_type TEXT, dedup_family TEXT, canonical_knowledge_type TEXT,
              canonical_label TEXT, primary_dataset TEXT, primary_knowledge_type TEXT,
              primary_evidence_tier TEXT, primary_language TEXT, risk_scope TEXT, default_route TEXT,
              retrieval_eligible INTEGER, embedding_eligible INTEGER, member_count INTEGER,
              retrieval_text_chars INTEGER);
            CREATE VIRTUAL TABLE retrieval_fts USING fts5(retrieval_id UNINDEXED, retrieval_text);
            CREATE VIRTUAL TABLE retrieval_cjk_fts USING fts5(retrieval_id UNINDEXED, retrieval_terms);
        ''')
        fields = ['retrieval_id','release_unit_id','release_unit_type','dedup_family','canonical_knowledge_type','canonical_label','primary_dataset','primary_knowledge_type','primary_evidence_tier','primary_language','risk_scope','default_route','retrieval_eligible','embedding_eligible','member_count','retrieval_text_chars']
        for row in rows(source, mapping):
            _check(cancel)
            try:
                db.execute('INSERT INTO retrieval_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(row.get(k) for k in fields))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f'duplicate knowledge ID: {row["release_unit_id"]}') from exc
            db.execute('INSERT INTO retrieval_fts VALUES (?,?)', (row['retrieval_id'], row['retrieval_text']))
            db.execute('INSERT INTO retrieval_cjk_fts VALUES (?,?)', (row['retrieval_id'], ' '.join(cjk_index_terms(row['retrieval_text']))))
            meta.execute('INSERT INTO metadata_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', metadata.metadata_row(row))
            meta.executemany('INSERT INTO metadata_v2_tokens VALUES (?,?,?)', metadata.token_rows(row))
            label.execute('INSERT INTO compact_label_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', labels.meta_row(row))
            label.execute('INSERT INTO compact_label_fts VALUES (?,?)', (row['retrieval_id'], labels.compact_text(row, 480)))
            counts['total'] += 1
            counts['eligible'] += int(row['retrieval_eligible'])
            counts['embedding_eligible'] += int(row['embedding_eligible'])
            if counts['total'] % 1000 == 0:
                for connection in (db, meta, label):
                    connection.commit()
                progress('lexical', dict(counts))
        if not counts['total']:
            raise ValueError('knowledge input contains no records')
        db.executescript('CREATE INDEX family ON retrieval_meta(dedup_family); CREATE INDEX route ON retrieval_meta(default_route);')
        metadata.create_indexes(meta)
        labels.create_indexes(label)
        for connection in (db, meta, label):
            connection.commit()
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            connection.execute('PRAGMA journal_mode=DELETE')
        counts['indexed'] = counts['eligible']
        counts['excluded'] = counts['total'] - counts['eligible']
    return counts


def _dense(output, config, counts, progress, cancel):
    try:
        import numpy as np
        import faiss
    except ImportError as exc:
        raise ValueError("hybrid build requires pip install 'theraskill-agent[retrieval]'") from exc
    from domains.theraskill.theraskill_llm import OpenAICompatibleEmbeddingClient
    batch_size = max(1, min(int(config.get('batch_size', 32)), 512))
    workers = max(1, min(int(config.get('workers', 1)), 16))
    cache = output / 'embedding-checkpoint.sqlite'
    with closing(sqlite3.connect(cache)) as vectors, closing(sqlite3.connect(output / DB)) as source:
        vectors.execute('PRAGMA cache_size=-65536')
        source.execute('PRAGMA cache_size=-65536')
        vectors.execute('CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, vector BLOB NOT NULL, dimension INTEGER NOT NULL)')
        vectors.execute('CREATE TABLE IF NOT EXISTS checkpoint (key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        vectors.commit()
        cursor_value = vectors.execute("SELECT value FROM checkpoint WHERE key='rowid'").fetchone()
        last_rowid = cursor_value[0] if cursor_value else 0
        source.execute('ATTACH DATABASE ? AS progress', (str(cache),))
        dimension = vectors.execute('SELECT dimension FROM vectors LIMIT 1').fetchone()
        dimension = dimension[0] if dimension else None
        completed = vectors.execute('SELECT count(*) FROM vectors').fetchone()[0]
        if completed and not cursor_value:
            raise ValueError('embedding checkpoint cursor is missing; cannot safely resume')
        # The cursor and vectors commit atomically in source order. Rows after
        # that cursor cannot already be in the checkpoint. Both lexical tables
        # share source rowids, avoiding random string-ID lookups per document.
        def batches():
            cursor = last_rowid
            while True:
                _check(cancel)
                batch = source.execute('''SELECT f.rowid, m.retrieval_id, f.retrieval_text FROM retrieval_fts f
                JOIN retrieval_meta m ON m.rowid=f.rowid AND m.retrieval_id=f.retrieval_id
                WHERE f.rowid > ? AND m.retrieval_eligible=1 AND m.embedding_eligible=1
                ORDER BY f.rowid LIMIT ?''', (cursor, batch_size)).fetchall()
                if not batch:
                    return
                cursor = batch[-1][0]
                yield batch

        def embed(batch):
            # Each request owns its transport; urllib openers are not shared
            # between worker threads. Only the coordinator writes checkpoints.
            return OpenAICompatibleEmbeddingClient(config).embed([r[2] for r in batch])

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='index-embedding') as pool:
            pending = deque()
            iterator = iter(batches())
            # A bounded queue keeps the HTTP workers busy when one earlier
            # batch is slower, while checkpoints still commit in source order.
            for _ in range(workers * 4 if workers > 1 else 1):
                batch = next(iterator, None)
                if batch is not None:
                    pending.append((batch, pool.submit(embed, batch)))
            while pending:
                _check(cancel)
                batch, future = pending.popleft()
                arr = np.asarray(future.result(), dtype=np.float32)
                if arr.ndim != 2 or arr.shape[0] != len(batch) or arr.shape[1] == 0 or not np.isfinite(arr).all():
                    raise ValueError('embedding service returned invalid vectors')
                if dimension is not None and arr.shape[1] != dimension:
                    raise ValueError('embedding dimension changed; cannot resume this index')
                if config.get('dimensions') and arr.shape[1] != int(config['dimensions']):
                    raise ValueError('embedding dimension differs from requested dimensions')
                norms = np.linalg.norm(arr, axis=1)
                if (norms == 0).any():
                    raise ValueError('embedding service returned a zero vector')
                dimension = arr.shape[1]
                arr /= norms[:, None]
                vectors.executemany('INSERT INTO vectors VALUES (?,?,?)', [(row[1], vector.tobytes(), dimension) for row, vector in zip(batch, arr)])
                # Commit in source order even if HTTP requests finish out of
                # order, so the high-water mark cannot skip an unfinished batch.
                last_rowid = batch[-1][0]
                vectors.execute("INSERT OR REPLACE INTO checkpoint VALUES ('rowid', ?)", (last_rowid,))
                vectors.commit()
                completed += len(batch)
                progress('embedding', {**counts, 'vectors': completed})
                _check(cancel)
                batch = next(iterator, None)
                if batch is not None:
                    pending.append((batch, pool.submit(embed, batch)))
        counts['vectors'] = completed
        if completed != counts['embedding_eligible']:
            raise ValueError('embedding coverage differs from eligible count')
        shards_dir = output / 'faiss_shards'
        shards_dir.mkdir(exist_ok=True)
        shards = []
        for family, route in source.execute('SELECT DISTINCT dedup_family,default_route FROM retrieval_meta WHERE retrieval_eligible=1 AND embedding_eligible=1'):
            _check(cancel)
            name = identity([family, route])[:20]
            shard_dir = shards_dir / name
            shard_dir.mkdir(exist_ok=True)
            index = faiss.IndexFlatIP(int(dimension))
            cursor = source.execute('''SELECT m.retrieval_id,m.release_unit_id,v.vector FROM retrieval_meta m
                JOIN progress.vectors v ON v.id=m.retrieval_id WHERE m.dedup_family IS ? AND m.default_route IS ?
                AND m.retrieval_eligible=1 AND m.embedding_eligible=1 ORDER BY m.retrieval_id''', (family, route))
            with (shard_dir / 'ids.jsonl').open('w', encoding='utf-8') as ids:
                while batch := cursor.fetchmany(1000):
                    _check(cancel)
                    index.add(np.asarray([np.frombuffer(r[2], dtype=np.float32) for r in batch]))
                    for rid, uid, _ in batch:
                        ids.write(json.dumps({'retrieval_id': rid, 'release_unit_id': uid}, ensure_ascii=False) + '\n')
            faiss.write_index(index, str(shard_dir / 'index.faiss'))
            shards.append({'shard_name': name, 'dedup_family': family, 'default_route': route,
                           'dimension': dimension, 'counts': {'added_vectors': int(index.ntotal)},
                           'artifacts': {'index': f'{name}/index.faiss', 'ids': f'{name}/ids.jsonl'}})
        embedding = {**fingerprint(config), 'dimension': dimension}
        write_json(shards_dir / 'manifest.json', {'schema_version': 'theraskill_faiss_shards_v1', 'embedding': embedding, 'shards': shards})
    return embedding


def build_index(knowledge, output, *, mode='lexical', embedding_config=None,
                mapping=None, resume=False, progress=None, cancel=None, lexical_index=None):
    if mode not in {'lexical', 'hybrid'}:
        raise ValueError('mode must be lexical or hybrid')
    source, output = Path(knowledge).expanduser().resolve(), Path(output).expanduser().resolve()
    if source.is_relative_to(output):
        raise ValueError('index output must not contain its knowledge input')
    config = resolve(embedding_config) if mode == 'hybrid' else None
    signature = {'knowledge_sha256': digest(source), 'projection': PROJECTION_VERSION, 'mapping': mapping or {},
                 'mode': mode, 'embedding': fingerprint(config) if config else None}
    output.mkdir(parents=True, exist_ok=True)
    with exclusive(output / '.build.lock'):
        if (output / 'manifest.json').exists():
            if resume and read_json(output / 'manifest.json')['input'] == signature:
                return verify_index(output)
            raise ValueError('completed indexes are immutable; choose another output directory')
        state_path = output / 'build-state.json'
        state = read_json(state_path) if state_path.exists() else {}
        if state and (not resume or state['input'] != signature):
            raise ValueError('resume requires the same input, mapping and embedding configuration')
        if not state and any(p.name != '.build.lock' for p in output.iterdir()):
            raise ValueError('output is not an empty index directory')
        state.update(input=signature, status='building')
        write_json(state_path, state)
        def notify(stage, counts):
            state.update(stage=stage, counts=counts)
            write_json(state_path, state)
            if progress:
                progress({'stage': stage, 'counts': counts})
        try:
            _check(cancel)
            if not state.get('lexical_complete'):
                if lexical_index:
                    parent = Path(lexical_index).expanduser().resolve()
                    if parent == output:
                        raise ValueError('reused lexical index must differ from output')
                    previous = verify_index(parent)
                    if any(previous['input'].get(k) != signature[k] for k in ('knowledge_sha256', 'projection', 'mapping')):
                        raise ValueError('reused lexical index has different knowledge, projection or mapping')
                    for name in (DB, 'metadata_v2.sqlite', 'compact_label_v1.sqlite'):
                        _check(cancel)
                        shutil.copy2(parent / name, output / name)
                    counts = {**previous['counts'], 'vectors': 0}
                    state['lexical_source_index_id'] = previous['index_id']
                else:
                    counts = _lexical(source, output, mapping, notify, cancel)
                state['lexical_files'] = {name: digest(output / name) for name in (DB, 'metadata_v2.sqlite', 'compact_label_v1.sqlite')}
                state['lexical_complete'] = True
                notify('lexical_complete', counts)
            else:
                for name, checksum in state.get('lexical_files', {}).items():
                    if digest(safe_path(output, name)) != checksum:
                        raise ValueError('lexical checkpoint changed; start a new index')
                counts = state['counts']
            embedding = _dense(output, config, counts, notify, cancel) if config else None
            _check(cancel)
            if digest(source) != signature['knowledge_sha256']:
                raise ValueError('knowledge input changed during build')
            files = {}
            for path in sorted(output.rglob('*')):
                if path.is_file() and path.name not in {'build-state.json', '.build.lock', 'embedding-checkpoint.sqlite'}:
                    relative = path.relative_to(output).as_posix()
                    files[relative] = {'sha256': digest(path), 'size': path.stat().st_size}
            manifest = {'schema_version': SCHEMA, 'status': 'complete', 'index_id': 'idx_' + identity(signature)[:24],
                        'input': signature, 'counts': counts, 'embedding': embedding, 'files': files,
                        'artifacts': {'sqlite_fts': DB, 'metadata_v2': 'metadata_v2.sqlite', 'compact_label': 'compact_label_v1.sqlite',
                                      **({'faiss_shards': 'faiss_shards/manifest.json'} if embedding else {})}}
            # Validate before publishing the completion marker.
            _verify_files(output, manifest)
            write_json(output / 'manifest.json', manifest)
            (output / 'embedding-checkpoint.sqlite').unlink(missing_ok=True)
            state.update(status='complete', stage='complete', counts=counts)
            write_json(state_path, state)
            return manifest
        except BaseException as exc:
            state['status'] = 'cancelled' if isinstance(exc, BuildCancelled) else 'failed'
            # Error text may contain a provider response; do not persist credentials.
            from theraskill_agent.text import redact_secrets
            state['error'] = redact_secrets(str(exc))
            write_json(state_path, state)
            raise


def _verify_files(root, manifest):
    if manifest.get('schema_version') != SCHEMA or manifest.get('status') != 'complete':
        raise ValueError('not a completed supported index')
    if not manifest.get('files'):
        raise ValueError('index file inventory is empty')
    for name, record in manifest['files'].items():
        path = safe_path(root, name)
        if not path.is_file() or path.stat().st_size != record['size'] or digest(path) != record['sha256']:
            raise ValueError(f'index file integrity check failed: {name}')
    for reference in manifest.get('artifacts', {}).values():
        if reference not in manifest['files']:
            raise ValueError('artifact is not declared in file inventory')
    for name in (DB, 'metadata_v2.sqlite', 'compact_label_v1.sqlite'):
        if name not in manifest['files']:
            raise ValueError(f'required index artifact missing: {name}')
        with closing(sqlite3.connect(f'{(Path(root) / name).as_uri()}?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError(f'SQLite integrity check failed: {name}')
    with closing(sqlite3.connect(f'{(Path(root) / DB).as_uri()}?mode=ro', uri=True)) as db:
        total, eligible = db.execute('SELECT count(*),coalesce(sum(retrieval_eligible),0) FROM retrieval_meta').fetchone()
        if total != manifest['counts']['total'] or eligible != manifest['counts']['indexed']:
            raise ValueError('index row counts differ from manifest')
    if manifest.get('embedding'):
        import faiss
        shard_manifest = read_json(safe_path(root, manifest['artifacts']['faiss_shards']))
        count = 0
        for shard in shard_manifest['shards']:
            for name in shard['artifacts'].values():
                relative = 'faiss_shards/' + name
                safe_path(root, relative)
                if relative not in manifest['files']:
                    raise ValueError('shard artifact missing from inventory')
            index = faiss.read_index(str(safe_path(root / 'faiss_shards', shard['artifacts']['index'])))
            with safe_path(root / 'faiss_shards', shard['artifacts']['ids']).open() as handle:
                id_count = sum(1 for line in handle if line.strip())
            if index.ntotal != shard['counts']['added_vectors'] or id_count != index.ntotal:
                raise ValueError('actual vector or ID count differs from shard manifest')
            if index.d != manifest['embedding']['dimension']:
                raise ValueError('actual vector dimension differs from manifest')
            count += index.ntotal
            del index
        if count != manifest['counts']['vectors'] or count != manifest['counts']['embedding_eligible']:
            raise ValueError('vector coverage differs from manifest')


def verify_index(path):
    root = Path(path).expanduser().resolve()
    manifest = read_json(root / 'manifest.json')
    _verify_files(root, manifest)
    return manifest


@lru_cache(maxsize=16)
def _load_dense_shard(index_path, ids_path):
    """Completed index files are immutable; avoid rereading vectors per query."""
    import faiss
    index = faiss.read_index(index_path)
    with Path(ids_path).open() as handle:
        ids = [json.loads(line)['retrieval_id'] for line in handle if line.strip()]
    if len(ids) != index.ntotal:
        raise ValueError('actual vector and ID counts differ')
    return index, ids


def search_index(path, query, *, limit=8, embedding_config=None, skill=None, filters=None):
    root = Path(path).expanduser().resolve()
    manifest = read_json(root / 'manifest.json')
    if manifest.get('status') != 'complete':
        raise ValueError('index is not complete')
    limit = max(1, min(int(limit), 50))
    understanding = understand_query(query, skill=skill, filters=filters)
    if not understanding['normalized_query']:
        raise ValueError('query is empty')
    if understanding['needs_clarification']:
        return []
    candidate_limit = max(limit * 6, 80)
    hits = lexical_candidates(root / DB, understanding, candidate_limit)
    with closing(sqlite3.connect(f'{(root / DB).as_uri()}?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        clauses, filter_params = compile_scope(db, understanding)
        where = ' AND '.join(clauses)
        if manifest.get('embedding'):
            from domains.theraskill.theraskill_llm import OpenAICompatibleEmbeddingClient
            import numpy as np
            import faiss
            config = resolve(embedding_config)
            require_compatible(manifest.get('embedding'), config)
            vector = np.asarray(OpenAICompatibleEmbeddingClient(config).embed([understanding['normalized_query']]), dtype=np.float32)
            if vector.shape != (1, manifest['embedding']['dimension']) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
                raise ValueError('query embedding has invalid dimension or values')
            vector /= np.linalg.norm(vector)
            scored = {}
            for rank, hit in enumerate(hits[:candidate_limit], 1):
                scored[hit['retrieval_id']] = 1 / (60 + rank)
            dense_hits = []
            # Family and route are represented by shard boundaries. Only
            # finer metadata filters require an ID selector inside a shard.
            scoped = understanding['filters'] or understanding['automatic_filters']
            allowed = {row[0] for row in db.execute(f'SELECT m.retrieval_id FROM retrieval_meta m WHERE {where}', filter_params)} if scoped else None
            for shard in read_json(root / 'faiss_shards/manifest.json')['shards']:
                partition = {**shard, 'retrieval_eligible': 1}
                # Apply only family/route restrictions here; other metadata is
                # checked by the SQL-derived selector and final hydration.
                partition_scope = {**understanding, 'filters': {}, 'automatic_filters': {}}
                if not matches_scope(partition, partition_scope):
                    continue
                index, ids = _load_dense_shard(str(safe_path(root / 'faiss_shards', shard['artifacts']['index'])), str(safe_path(root / 'faiss_shards', shard['artifacts']['ids'])))
                if allowed is None:
                    distances, positions = index.search(vector, min(candidate_limit, index.ntotal))
                else:
                    allowed_positions = np.asarray([i for i, rid in enumerate(ids) if rid in allowed], dtype=np.int64)
                    if not len(allowed_positions):
                        continue
                    selector = faiss.IDSelectorBatch(allowed_positions)
                    distances, positions = index.search(vector, min(candidate_limit, len(allowed_positions)), params=faiss.SearchParameters(sel=selector))
                dense_hits.extend((float(score), ids[int(pos)]) for score, pos in zip(distances[0], positions[0]) if pos >= 0)
            for rank, (_, rid) in enumerate(sorted(dense_hits, reverse=True)[:candidate_limit], 1):
                scored[rid] = scored.get(rid, 0) + 1 / (60 + rank)
            hits = []
            for rid, score in sorted(scored.items(), key=lambda v: -v[1]):
                row = db.execute(f'SELECT m.*,f.retrieval_text FROM retrieval_meta m JOIN retrieval_fts f ON f.rowid=m.rowid AND f.retrieval_id=m.retrieval_id WHERE m.retrieval_id=? AND {where}', (rid, *filter_params)).fetchone()
                if row:
                    item = {**dict(row), 'score': score, 'relevance_score': score}
                    hits.append(item)
            hits = rank_candidates(hits, understanding)
        return hits[:limit]


def retrieve_index(path, query, *, limit=8, embedding_config=None, skill=None, filters=None):
    """Return the common retrieval envelope used by CLI, UI and local jobs."""
    root = Path(path).expanduser().resolve()
    manifest = read_json(root / 'manifest.json')
    understanding = understand_query(query, skill=skill, filters=filters)
    hits = search_index(
        root,
        understanding['normalized_query'],
        limit=limit,
        embedding_config=embedding_config,
        skill=skill,
        filters=filters,
    )
    return {
        'query': str(query or ''),
        'index_id': manifest.get('index_id'),
        'knowledge_sha256': (manifest.get('input') or {}).get('knowledge_sha256'),
        'retrieval_mode': 'hybrid' if manifest.get('embedding') else 'lexical',
        'skill': skill or 'general',
        'query_understanding': understanding,
        'filters': understanding['filters'],
        'matches': hits,
        'count': len(hits),
        'warnings': understanding['warnings'],
    }
