"""Durable resource job records; interruption is explicit and builds can resume."""
from __future__ import annotations
import os
import hashlib
import json
import threading
import uuid
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from .paths import data_root, state_root
from .indexing.common import read_json, write_json, safe_id, exclusive, digest
from .indexing import build_index, plan_index, retrieve_index, verify_index
from .indexing.builder import BuildCancelled
from . import resources
from .text import redact_secrets

_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='resource-job')
_EVENTS = {}
_LOCK = threading.Lock()


def cancel_running_jobs():
    with _LOCK:
        for event in _EVENTS.values():
            event.set()


def _path(job_id):
    return state_root() / 'resource-jobs' / (safe_id(job_id) + '.json')


def get_job(job_id):
    record = read_json(_path(job_id))
    if record['status'] in {'queued', 'running'}:
        with _LOCK:
            local = job_id in _EVENTS
        if not local:
            try:
                os.kill(int(record.get('pid', 0)), 0)
            except (ProcessLookupError, ValueError):
                record.update(status='interrupted', error='worker stopped; resume using the same index ID')
                write_json(_path(job_id), record)
    return record


def list_jobs():
    paths = sorted((state_root() / 'resource-jobs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    return [get_job(p.stem) for p in paths[:30]]


def cancel_job(job_id):
    with _LOCK:
        event = _EVENTS.get(job_id)
    if event:
        event.set()
    return get_job(job_id)


def _source_stat(path):
    st = path.stat()
    return {'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns, 'inode': st.st_ino}


def _set_field(record, path, value):
    parts = path.split('.')
    current = record
    for part in parts[:-1]:
        if part not in current: current[part] = {}
        if not isinstance(current[part], dict):
            raise ValueError('mapped knowledge field is not an object')
        current = current[part]
    current[parts[-1]] = value


def _apply_knowledge_snapshot(row, snapshot, mapping):
    import copy
    from .indexing.common import identity
    from .indexing.knowledge import _field
    fields = snapshot['fields']
    unit_id = row.get('release_unit_id') or _field(row, mapping.get('id', 'id'))
    if unit_id != snapshot['unit_id']:
        return None
    if identity(row) != snapshot['source_record_sha256']:
        raise ValueError('knowledge source record differs from the saved revision')
    result = copy.deepcopy(row)
    if 'release_unit_id' in result:
        content = result['content']
        for key, value in fields.items():
            if key in {'canonical_label', 'summary', 'evidence_source'}:
                content[key] = value
            else:
                content.setdefault('registry_fields', {})[key] = value
    else:
        for key, source_key in (('canonical_label', 'title'), ('summary', 'text')):
            if key in fields:
                _set_field(result, mapping.get(source_key, source_key), fields[key])
    # Preserve old source structure for audit, while the projection explicitly
    # treats corrected summary/label as authoritative retrieval semantics.
    previous = result.get('_theraskill_editor') or {}
    result['_theraskill_editor'] = {'schema_version': 1,
        'fields': {**(previous.get('fields') or {}), **fields},
        'source_record_sha256': snapshot['source_record_sha256'],
        'source_index_id': snapshot['base_index_id'], 'revision': snapshot['revision']}
    return result


def _materialize_knowledge_overrides(source, snapshot_path, output, *, expected_sha256,
                                      snapshot_sha256, mapping=None, progress=None, cancel=None):
    """Copy the exact release and apply one frozen revision, never live edits."""
    from pathlib import Path
    from .indexing.common import identity
    source, output = Path(source).resolve(), Path(output).resolve()
    mapping = mapping or {}
    snapshot = read_json(snapshot_path)
    if identity(snapshot) != snapshot_sha256:
        raise ValueError('knowledge edit snapshot changed')
    if snapshot.get('schema_version') != 1 or snapshot.get('source_sha256') != expected_sha256:
        raise ValueError('knowledge edit snapshot does not match its source')
    required = ('unit_id', 'source_record_sha256', 'base_index_id', 'revision', 'fields')
    if any(k not in snapshot for k in required) or not isinstance(snapshot['fields'], dict):
        raise ValueError('knowledge edit snapshot is incomplete')
    allowed = {'canonical_label', 'summary', 'applicable_context', 'evidence_source', 'allowed_use', 'forbidden_use', 'contraindications'}
    if set(snapshot['fields']) - allowed:
        raise ValueError('knowledge edit snapshot contains unsupported fields')
    output.parent.mkdir(parents=True, exist_ok=True)
    before = _source_stat(source); total = before['size'] or 1
    temporary = output.with_name('.' + output.name + '.' + uuid.uuid4().hex + '.tmp')
    hasher = hashlib.sha256(); materialized = hashlib.sha256(); matches = 0; completed = 0
    marker = json.dumps(snapshot['unit_id'], ensure_ascii=True).encode()
    marker_utf8 = json.dumps(snapshot['unit_id'], ensure_ascii=False).encode()
    try:
        with source.open('rb') as src, temporary.open('wb') as dst:
            for line_no, raw in enumerate(src, 1):
                if cancel and cancel(): raise BuildCancelled('knowledge materialization cancelled')
                hasher.update(raw); completed += len(raw); out = raw
                # Only candidate lines are parsed; all other records retain
                # their exact bytes, order and fields in the new release.
                if marker in raw or marker_utf8 in raw:
                    row = json.loads(raw.decode('utf-8-sig'))
                    if isinstance(row, dict):
                        patched = _apply_knowledge_snapshot(row, snapshot, mapping)
                        if patched is not None:
                            matches += 1
                            out = json.dumps(patched, ensure_ascii=False).encode() + bytes([10])
                dst.write(out); materialized.update(out)
                if progress and line_no % 10000 == 0:
                    progress({'stage': 'materialize_overrides', 'bytes': completed, 'total_bytes': total, 'line': line_no})
            dst.flush(); os.fsync(dst.fileno())
        if hasher.hexdigest() != expected_sha256 or _source_stat(source) != before:
            raise ValueError('knowledge source changed or does not match the active index')
        if matches != 1:
            raise ValueError(f'knowledge edit must match exactly one source record; matched {matches}')
        if identity(read_json(snapshot_path)) != snapshot_sha256:
            raise ValueError('knowledge edit snapshot changed during build')
        if output.exists():
            if digest(output) != materialized.hexdigest():
                raise ValueError('materialized knowledge differs from the previous attempt')
        else:
            os.replace(temporary, output)
        return {'path': str(output), 'sha256': materialized.hexdigest(), 'source_sha256': expected_sha256, 'matched': matches}
    finally:
        temporary.unlink(missing_ok=True)


def _build_source_catalog(source, output, *, expected_sha256, mapping=None, source_stat=None, progress=None, cancel=None):
    """Publish byte offsets only after a single streaming checksum pass."""
    from pathlib import Path
    from contextlib import closing
    from .indexing.knowledge import _field
    source, output = Path(source).resolve(), Path(output).resolve()
    mapping = mapping or {}; before = _source_stat(source)
    if source_stat and before != source_stat:
        raise ValueError('knowledge source changed before catalog preparation')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f'.{output.name}.{uuid.uuid4().hex}.tmp')
    total = before['size'] or 1; completed = 0; records = 0; checksum = hashlib.sha256()
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE source_records (release_unit_id TEXT PRIMARY KEY, byte_offset INTEGER NOT NULL, byte_length INTEGER NOT NULL, line_no INTEGER NOT NULL)')
            with source.open('rb') as stream:
                for line_no, raw in enumerate(stream, 1):
                    if cancel and cancel(): raise BuildCancelled('source catalog cancelled')
                    offset = completed; completed += len(raw); checksum.update(raw)
                    if not raw.strip(): continue
                    row = json.loads(raw.decode('utf-8-sig'))
                    if not isinstance(row, dict): raise ValueError('knowledge source record must be an object')
                    unit_id = row.get('release_unit_id') or _field(row, mapping.get('id', 'id'))
                    if not isinstance(unit_id, str) or not unit_id:
                        raise ValueError('knowledge source record has no stable ID')
                    try: db.execute('INSERT INTO source_records VALUES (?,?,?,?)', (unit_id, offset, len(raw), line_no))
                    except sqlite3.IntegrityError as exc: raise ValueError('duplicate knowledge ID in source') from exc
                    records += 1
                    if line_no % 10000 == 0:
                        db.commit()
                        if progress: progress({'stage': 'source_catalog', 'bytes': completed, 'total_bytes': total, 'line': line_no})
            if checksum.hexdigest() != expected_sha256 or _source_stat(source) != before:
                raise ValueError('knowledge source checksum does not match the selected index')
            db.executemany('INSERT INTO catalog_meta VALUES (?,?)', [
                ('sha256', json.dumps(expected_sha256)), ('stat', json.dumps(before)),
                ('path', json.dumps(str(source))), ('mapping', json.dumps(mapping))])
            db.commit()
        os.replace(temporary, output)
        return {'catalog': str(output), 'source_sha256': expected_sha256, 'records': records}
    finally:
        temporary.unlink(missing_ok=True)


def run_operation(operation, parameters, *, progress=None, cancel=None):
    p = dict(parameters)
    if operation in {'plan', 'build'}:
        source = resources.knowledge_path(p['knowledge'])
        options = {'mode': p.get('mode', 'lexical'), 'mapping': p.get('mapping')}
        if operation == 'plan':
            return plan_index(source, **options)
        output = data_root() / 'indexes' / safe_id(p['index_id'])
        lexical = resources.index_path(p['reuse_lexical']) if p.get('reuse_lexical') else None
        return build_index(source, output, resume=bool(p.get('resume')), lexical_index=lexical, progress=progress, cancel=cancel, **options)
    if operation == 'reindex':
        from pathlib import Path
        from .indexing.common import identity
        source = Path(p['source']).expanduser().resolve()
        snapshot_path = Path(p['snapshot']).expanduser().resolve()
        snapshot = read_json(snapshot_path)
        if identity(snapshot) != p.get('snapshot_sha256'):
            raise ValueError('knowledge edit snapshot changed')
        for field, expected in [('base_index_id', p.get('base_index_id')), ('unit_id', p.get('unit_id')),
                                ('revision', p.get('revision')), ('source_sha256', p.get('expected_source_sha256'))]:
            if snapshot.get(field) != expected:
                raise ValueError('knowledge edit snapshot inputs differ from the job')
        mode = p.get('mode')
        if mode not in {'lexical', 'hybrid'}:
            raise ValueError('reindex must preserve an explicit lexical or hybrid mode')
        embedding_config = None
        if mode == 'hybrid':
            from .indexing.embedding import resolve, fingerprint
            embedding_config = resolve()
            if not p.get('embedding_identity') or fingerprint(embedding_config) != p['embedding_identity']:
                raise ValueError('configured embedding model differs from the snapshot; restore it before retrying')
        job_key = safe_id(p['index_id'])
        materialized = data_root() / 'knowledge' / 'reindex-work' / f'{job_key}.jsonl'
        materialized_info = _materialize_knowledge_overrides(source, snapshot_path, materialized,
            expected_sha256=p['expected_source_sha256'], snapshot_sha256=p['snapshot_sha256'],
            mapping=p.get('mapping'), progress=progress, cancel=cancel)
        output = data_root() / 'indexes' / job_key
        result = build_index(materialized, output, mode=mode, mapping=p.get('mapping'),
            embedding_config=embedding_config, resume=bool(p.get('resume')), progress=progress, cancel=cancel)
        if result.get('input', {}).get('knowledge_sha256') != materialized_info['sha256']:
            raise ValueError('built index source differs from the materialized knowledge')
        write_json(data_root() / 'imports' / (job_key + '.manifest.json'), {
            'id': job_key, 'path': str(materialized), 'manifest': {
                'knowledge_sha256': materialized_info['sha256'], 'counts': result.get('counts') or {}},
            'provenance': {'base_index_id': p['base_index_id'], 'unit_id': p['unit_id'],
                           'revision': p['revision'], 'snapshot_sha256': p['snapshot_sha256']}})
        return {**result, 'index_directory_id': job_key, 'base_index_id': p['base_index_id'],
                'unit_id': p['unit_id'], 'revision': p['revision'], 'mode': mode,
                'activation_required': True, 'materialized_knowledge': str(materialized)}
    if operation == 'catalog':
        return _build_source_catalog(p.get('source'), p.get('output'), expected_sha256=p.get('expected_sha256'), mapping=p.get('mapping'), source_stat=p.get('source_stat'), progress=progress, cancel=cancel)
    if operation == 'verify':
        return verify_index(resources.index_path(p['index']), scratch_root=p.get('scratch_root'))
    if operation == 'search':
        if p.get('production'):
            from .resources import using_index
            from plugins.theraskill.retrieval import search_managed_knowledge
            with using_index(resources.index_path(p['index'])):
                return search_managed_knowledge(p['query'],skill=p.get('skill') or 'emotional-support',
                    limit=p.get('limit',8),filters=p.get('filters'))
        return retrieve_index(
            resources.index_path(p['index']),
            p['query'],
            limit=p.get('limit', 8),
            skill=p.get('skill'),
            filters=p.get('filters'),
        )
    if operation == 'activate':
        return resources.activate_index(p['index'], knowledge=p.get('knowledge'), scratch_root=p.get('scratch_root'), report_id=p.get('report_id'))
    if operation == 'rollback':
        return resources.rollback_index()
    if operation == 'install':
        archive, checksum, expected_id = p.get('archive'), p.get('sha256'), None
        if p.get('catalog'):
            archive, record = resources.download_package(p['catalog'], p['package'], cancel=cancel, progress=progress)
            checksum, expected_id = record['sha256'], record['id']
        if not archive:
            raise ValueError('archive or catalog and package are required')
        if cancel and cancel():
            raise BuildCancelled('installation cancelled before extraction')
        return resources.install_package(archive, expected_sha256=checksum, expected_id=expected_id,cancel=cancel,progress=progress)
    raise ValueError('unknown resource operation')


def _enqueue(record, event):
    job_id = record['id']
    with _LOCK:
        _EVENTS[job_id] = event
    def worker():
        try:
            if event.is_set():
                raise BuildCancelled('cancelled before starting')
            record['status'] = 'running'; record.pop('error', None); write_json(_path(job_id), record)
            def progress(value):
                record['progress'] = value; write_json(_path(job_id), record)
            result = run_operation(record['operation'], record.get('parameters') or {}, progress=progress, cancel=event.is_set)
            record.update(status='succeeded', result=result)
        except Exception as exc:
            record.update(status='cancelled' if event.is_set() or isinstance(exc, BuildCancelled) else 'failed', error=redact_secrets(str(exc)))
        finally:
            write_json(_path(job_id), record)
            with _LOCK:
                _EVENTS.pop(job_id, None)
    _POOL.submit(worker)


def submit_job(operation, parameters, *, request_id=None):
    if operation not in {'plan', 'build', 'reindex', 'catalog', 'verify', 'install', 'activate', 'rollback', 'search'}:
        raise ValueError('unknown resource operation')
    job_id = 'resource_' + (hashlib.sha256(request_id.encode()).hexdigest()[:32] if request_id else uuid.uuid4().hex)
    record = {'id': job_id, 'operation': operation, 'parameters': parameters, 'status': 'queued', 'pid': os.getpid()}
    with exclusive(state_root() / '.resource-submit.lock'):
        if request_id and _path(job_id).is_file():
            existing = get_job(job_id)
            if existing['operation'] != operation or existing['parameters'] != parameters:
                raise ValueError('Resource request ID was already used with different inputs')
            return existing
        write_json(_path(job_id), record)
    _enqueue(record, threading.Event())
    return dict(record)


def resume_job(job_id, *, rebuild_catalog=False):
    """Resume an interrupted/failed resource job with the exact same inputs."""
    with exclusive(state_root() / '.resource-submit.lock'):
        record = get_job(job_id)
        permitted = {'failed', 'interrupted', 'cancelled'}
        if rebuild_catalog and record.get('operation') == 'catalog': permitted.add('succeeded')
        if record.get('status') not in permitted:
            return record
        record = dict(record)
        record.update(status='queued', pid=os.getpid())
        record.pop('error', None)
        write_json(_path(job_id), record)
    _enqueue(record, threading.Event())
    return dict(record)
