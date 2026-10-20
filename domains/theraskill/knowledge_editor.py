"""Immutable-source knowledge details, optimistic local edits and rebuild jobs."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from theraskill_agent import resources, resource_jobs
from theraskill_agent.indexing.common import identity, read_json, safe_id, write_json
from theraskill_agent.indexing.knowledge import _field
from theraskill_agent.paths import data_root

EDITABLE_FIELDS = ('canonical_label', 'summary', 'applicable_context', 'evidence_source',
                   'allowed_use', 'forbidden_use', 'contraindications')
MAX_FIELD_CHARS = 100_000
SMALL_SOURCE_BYTES = 2 * 1024 * 1024
_LABELS = {'canonical_label': 'Title', 'summary': 'Summary', 'applicable_context': 'Applicable context',
           'evidence_source': 'Evidence source', 'allowed_use': 'Allowed use',
           'forbidden_use': 'Boundaries', 'contraindications': 'Precautions',
           'source_faithfulness_notes': 'Source faithfulness', 'representative_text_ref': 'Representative source',
           'registry_fields': 'Knowledge details', 'dedup_text_preview': 'Source excerpt',
           'provenance': 'Provenance', 'quality': 'Review information', 'retrieval_policy': 'Retrieval policy'}


class KnowledgeUnitNotFound(KeyError):
    pass


class KnowledgeRevisionConflict(ValueError):
    pass


def _valid_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}', value):
        raise KnowledgeUnitNotFound('invalid knowledge unit id')
    return value


def source_stat(path: Path) -> dict[str, int]:
    st = path.stat()
    return {'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns, 'inode': st.st_ino}


def _sections(record: dict[str, Any] | None, values: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = [{'key': k, 'label': _LABELS[k], 'value': values[k]}
              for k in EDITABLE_FIELDS if values.get(k) not in (None, '', [])]
    if record:
        content = record.get('content') if isinstance(record.get('content'), dict) else record
        registry = content.get('registry_fields') if isinstance(content.get('registry_fields'), dict) else {}
        for group, source in [('content', content), ('registry', registry)]:
            for key, value in source.items():
                if key in EDITABLE_FIELDS or key in {'registry_fields', '_theraskill_editor'} or value in (None, '', []):
                    continue
                result.append({'key': f'{group}.{key}', 'label': _LABELS.get(key, key.replace('_', ' ').capitalize()), 'value': value})
        if content is not record:
            for key, value in record.items():
                if key in {'content', '_theraskill_editor'} or value in (None, '', []): continue
                result.append({'key': f'source.{key}', 'label': _LABELS.get(key, key.replace('_', ' ').capitalize()), 'value': value})
    return result


def source_values(record: dict[str, Any] | None, mapping: Mapping[str, str], row: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {k: '' for k in EDITABLE_FIELDS}
    values['canonical_label'] = row.get('canonical_label') or ''
    if not record:
        passage = str(row.get('retrieval_text') or '')
        label = str(values.get('canonical_label') or '')
        values['summary'] = passage[len(label):].strip() if label and passage.startswith(label) else passage
        return values
    content = record.get('content') if 'release_unit_id' in record else {
        'canonical_label': _field(record, mapping.get('title', 'title'), ''),
        'summary': _field(record, mapping.get('text', 'text'), ''),
        'evidence_source': _field(record, mapping.get('source', 'source'), ''),
        'registry_fields': record.get('registry_fields', {}),
    }
    content = content if isinstance(content, dict) else {}
    registry = content.get('registry_fields') if isinstance(content.get('registry_fields'), dict) else {}
    for key in EDITABLE_FIELDS:
        for source in (content, registry, record):
            if key in source and source[key] is not None:
                values[key] = copy.deepcopy(source[key]); break
    editor = record.get('_theraskill_editor') or {}
    for key, value in (editor.get('fields') or {}).items():
        if key in EDITABLE_FIELDS: values[key] = copy.deepcopy(value)
    return values


class KnowledgeEditor:
    def __init__(self, *, root: Path | None = None):
        self.root = Path(root or data_root()).expanduser().resolve()
        self.overlay_path = self.root / 'knowledge' / 'edits.json'

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _read_edits(self):
        if not self.overlay_path.exists(): return {'schema_version': 2, 'entries': {}}
        payload = read_json(self.overlay_path)
        # Never attach an old unscoped draft to an arbitrary active release.
        if payload.get('schema_version') != 2 or not isinstance(payload.get('entries'), dict):
            raise ValueError('unscoped legacy knowledge edits require explicit migration')
        return payload

    @contextmanager
    def _edit_lock(self):
        import fcntl
        self.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        with self.overlay_path.with_suffix('.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try: yield self._read_edits()
            finally: fcntl.flock(lock, fcntl.LOCK_UN)

    def _save_edits(self, payload):
        write_json(self.overlay_path, payload)
        os.chmod(self.overlay_path, 0o600)
        fd = os.open(self.overlay_path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _selected(self, index_id=None):
        active = resources.active_index()
        if active and (not index_id or active[1].get('index_id') == index_id):
            return Path(active[0]).resolve(), active[1], active[1].get('index_id')
        if index_id:
            try: safe_id(index_id)
            except ValueError as exc: raise KnowledgeUnitNotFound('invalid knowledge index id') from exc
            for path in sorted((self.root / 'indexes').glob('*/manifest.json')):
                try: manifest = read_json(path)
                except (OSError, ValueError): continue
                if manifest.get('index_id') == index_id and manifest.get('status') == 'complete':
                    return path.parent.resolve(), manifest, active[1].get('index_id') if active else None
        raise KnowledgeUnitNotFound('the evidence index is not installed')

    def _row(self, identifier, index_id=None):
        identifier = _valid_id(identifier)
        directory, manifest, active_id = self._selected(index_id)
        name = (manifest.get('artifacts') or {}).get('sqlite_fts') or 'phase6_retrieval_fts.sqlite'
        path = (directory / name).resolve()
        if not path.is_file() or not path.is_relative_to(directory):
            raise KnowledgeUnitNotFound('knowledge index metadata is unavailable')
        with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT rowid AS source_rowid,* FROM retrieval_meta WHERE release_unit_id=?', (identifier,)).fetchone()
            if row is None: row = db.execute('SELECT rowid AS source_rowid,* FROM retrieval_meta WHERE retrieval_id=?', (identifier,)).fetchone()
            if row is None: raise KnowledgeUnitNotFound(identifier)
            row = dict(row)
            hit = db.execute('SELECT retrieval_id,retrieval_text FROM retrieval_fts WHERE rowid=?', (row['source_rowid'],)).fetchone()
            if not hit or hit['retrieval_id'] != row['retrieval_id']:
                # Legacy stores may not align FTS rowids. Cap that compatibility
                # search instead of scanning gigabytes on a drawer request.
                calls = 0
                def stop():
                    nonlocal calls
                    calls += 1
                    return int(calls > 50)
                db.set_progress_handler(stop, 1000)
                try: hit = db.execute('SELECT retrieval_id,retrieval_text FROM retrieval_fts WHERE retrieval_id=? LIMIT 1', (row['retrieval_id'],)).fetchone()
                except sqlite3.OperationalError: hit = None
                finally: db.set_progress_handler(None, 0)
            row['retrieval_text'] = hit['retrieval_text'] if hit else ''
        return row, manifest, directory, active_id

    @staticmethod
    def _scope(manifest):
        return str(manifest['index_id']) + ':' + str((manifest.get('input') or {}).get('knowledge_sha256') or '')

    def _edit(self, manifest, unit_id, payload=None):
        payload = payload if payload is not None else self._read_edits()
        return copy.deepcopy((payload['entries'].get(self._scope(manifest)) or {}).get(unit_id) or {})

    def _source_path(self, manifest, directory):
        expected = str((manifest.get('input') or {}).get('knowledge_sha256') or '')
        if not re.fullmatch('[a-f0-9]{64}', expected): return None
        for root in dict.fromkeys([self.root, directory.parent.parent]):
            paths = [root / 'current' / 'manifest.json', *sorted((root / 'releases').glob('*/manifest.json')),
                     *sorted((root / 'knowledge').glob('*/manifest.json')), *sorted((root / 'imports').glob('*.manifest.json'))]
            for path in paths:
                if not path.is_file(): continue
                try: record = read_json(path)
                except (OSError, ValueError): continue
                checksum = record.get('knowledge_sha256') or (record.get('manifest') or {}).get('knowledge_sha256') or ((record.get('checksums') or {}).get('knowledge_release') or {}).get('sha256')
                if checksum != expected: continue
                relative = (record.get('artifacts') or {}).get('knowledge_release')
                candidate = Path(record['path']) if record.get('path') else path.parent / str(relative or 'knowledge.jsonl')
                if candidate.is_file(): return candidate.resolve()
        return None

    def _source(self, manifest, directory, unit_id, *, prepare=True):
        expected = str((manifest.get('input') or {}).get('knowledge_sha256') or '')
        source = self._source_path(manifest, directory)
        info = {'kind': 'indexed_passage', 'available': bool(source), 'status': 'missing', 'knowledge_sha256': expected}
        if source is None: return info, None
        mapping = (manifest.get('input') or {}).get('mapping') or {}
        fingerprint = identity({'sha256': expected, 'mapping': mapping})
        catalog = self.root / 'knowledge' / 'source-catalog' / (fingerprint + '.sqlite')
        valid = False
        if catalog.is_file():
            try:
                with closing(sqlite3.connect(f'{catalog.as_uri()}?mode=ro', uri=True)) as db:
                    meta = {r[0]: json.loads(r[1]) for r in db.execute('SELECT key,value FROM catalog_meta')}
                    valid = meta.get('sha256') == expected and meta.get('stat') == source_stat(source) and meta.get('path') == str(source) and meta.get('mapping') == mapping
                    if valid:
                        hit = db.execute('SELECT byte_offset,byte_length,line_no FROM source_records WHERE release_unit_id=?', (unit_id,)).fetchone()
                        if not hit:
                            return {**info, 'status': 'record_missing'}, None
                if valid:
                    with source.open('rb') as handle:
                        handle.seek(hit[0]); record = json.loads(handle.read(hit[1]).decode('utf-8-sig'))
                    actual_id = record.get('release_unit_id') or _field(record, mapping.get('id', 'id'))
                    if actual_id != unit_id: raise ValueError('source record changed')
                    return {**info, 'kind': 'original', 'status': 'ready', 'line_no': hit[2]}, record
            except (OSError, ValueError, sqlite3.Error): pass
        if not prepare:
            return {**info, 'status': 'failed', 'error': 'The prepared source catalog is unavailable or changed.'}, None
        parameters = {'source': str(source), 'output': str(catalog), 'expected_sha256': expected, 'mapping': mapping, 'source_stat': source_stat(source)}
        # Tiny fixture/upload packages are bounded; large files are catalogued
        # once in the durable worker. A request never streams a large release.
        if source.stat().st_size <= SMALL_SOURCE_BYTES:
            try: resource_jobs.run_operation('catalog', parameters)
            except (OSError, ValueError, sqlite3.Error) as exc:
                return {**info, 'status': 'failed', 'error': str(exc)}, None
            return self._source(manifest, directory, unit_id, prepare=False)
        job = resource_jobs.submit_job('catalog', parameters, request_id='knowledge-catalog-' + identity(parameters))
        status = job.get('status')
        if status == 'succeeded':
            # The worker may have completed between our catalog read and job
            # lookup; check once, then repair an actually lost/corrupt catalog.
            loaded = self._source(manifest, directory, unit_id, prepare=False)
            if loaded[1] is not None or loaded[0].get('status') == 'record_missing': return loaded
            job = resource_jobs.resume_job(job['id'], rebuild_catalog=True)
            status = job.get('status')
        elif status == 'interrupted':
            job = resource_jobs.resume_job(job['id'])
            status = job.get('status')
        info.update(status='failed' if status in {'failed', 'cancelled', 'interrupted'} else 'preparing', catalog_job_id=job['id'])
        if job.get('error'): info['error'] = job['error']
        return info, None

    @staticmethod
    def _job(edit):
        if not edit.get('job_id'): return None
        try: job = resource_jobs.get_job(edit['job_id'])
        except (OSError, ValueError):
            return {'id': edit['job_id'], 'status': 'interrupted', 'error': 'The rebuild job record is unavailable.'}
        return {k: job[k] for k in ('id', 'status', 'progress', 'error', 'result') if k in job} | {
            'revision': edit.get('job_revision'), 'resources_url': '/?workspace=knowledge&' + urlencode({'job_id': job['id']})}

    def get(self, identifier, *, index_id=None):
        row, manifest, directory, active_id = self._row(identifier, index_id)
        source, record = self._source(manifest, directory, row['release_unit_id'])
        indexed = source_values(record, (manifest.get('input') or {}).get('mapping') or {}, row)
        edit = self._edit(manifest, row['release_unit_id']); values = {**indexed, **(edit.get('fields') or {})}
        job = self._job(edit); revision = int(edit.get('revision') or 0)
        index_status = 'active' if manifest.get('index_id') == active_id else 'inactive'
        if edit.get('fields'): index_status = 'stale'
        if job and edit.get('job_revision') == revision:
            index_status = {'queued': 'building', 'running': 'building', 'succeeded': 'built',
                            'failed': 'failed', 'cancelled': 'failed', 'interrupted': 'interrupted'}.get(job.get('status'), 'pending')
            if (job.get('result') or {}).get('index_id') == active_id: index_status = 'active'
        mode = (manifest.get('input') or {}).get('mode')
        can_edit = manifest.get('index_id') == active_id and source.get('status') == 'ready'
        return {**values, 'release_unit_id': row['release_unit_id'], 'retrieval_id': row['retrieval_id'],
                'knowledge_type': row.get('primary_knowledge_type') or row.get('canonical_knowledge_type'),
                'dedup_family': row.get('dedup_family'), 'risk_level': row.get('risk_scope'),
                'primary_language': row.get('primary_language'), 'primary_dataset': row.get('primary_dataset'),
                'source_type': row.get('dedup_family'), 'source': source,
                'indexed_passage': row['retrieval_text'], 'evidence_spans': [row['retrieval_text']] if row['retrieval_text'] else [],
                'indexed_values': indexed, 'indexed_sections': _sections(record, indexed), 'sections': _sections(record, values),
                'edited_fields': sorted((edit.get('fields') or {}).keys()), 'editable_fields': list(EDITABLE_FIELDS),
                'revision': revision, 'updated_at': edit.get('updated_at'), 'index_id': manifest['index_id'],
                'active_index_id': active_id, 'mode': mode, 'index_mode': mode, 'index_status': index_status,
                'can_edit': can_edit, 'can_reindex': can_edit and mode in {'lexical', 'hybrid'},
                'activation_required': index_status in {'stale', 'pending', 'building', 'built'}, 'reindex_job': job,
                'editor_url': '/?workspace=knowledge&' + urlencode({'knowledge_id': row['release_unit_id'], 'index_id': manifest['index_id']})}

    def _guard(self, row, manifest, active_id, expected_revision, expected_index_id, payload):
        selected = resources.active_index()
        active_id = selected[1].get('index_id') if selected else None
        if expected_index_id != manifest.get('index_id') or active_id != expected_index_id:
            raise KnowledgeRevisionConflict('The active knowledge index changed. Reload before editing.')
        edit = self._edit(manifest, row['release_unit_id'], payload)
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or int(edit.get('revision') or 0) != expected_revision:
            raise KnowledgeRevisionConflict('This knowledge entry changed. Reload before saving or rebuilding.')
        return edit

    def update(self, identifier, values, *, expected_revision, expected_index_id):
        row, manifest, directory, active_id = self._row(identifier, expected_index_id)
        unknown = set(values) - set(EDITABLE_FIELDS)
        if unknown: raise ValueError('unsupported knowledge fields: ' + ', '.join(sorted(unknown)))
        cleaned = {}
        for key, value in values.items():
            if not isinstance(value, (str, list)) or isinstance(value, list) and any(not isinstance(x, str) for x in value):
                raise ValueError(f'{key} must be text or a list of text values')
            if key in {'canonical_label', 'summary'} and not isinstance(value, str): raise ValueError(f'{key} must be text')
            value = value.strip() if isinstance(value, str) else [x.strip() for x in value if x.strip()]
            if key == 'canonical_label' and not value: raise ValueError('title cannot be empty')
            size = len(value) if isinstance(value, str) else sum(len(x) for x in value)
            if size > MAX_FIELD_CHARS: raise ValueError(f'{key} exceeds {MAX_FIELD_CHARS} characters')
            cleaned[key] = value
        if not cleaned: raise ValueError('at least one knowledge field must change')
        info, record = self._source(manifest, directory, row['release_unit_id'])
        if record is None: raise ValueError('Original source content must be available before editing.')
        with self._edit_lock() as payload:
            prior = self._guard(row, manifest, active_id, expected_revision, expected_index_id, payload)
            fields = {**(prior.get('fields') or {}), **cleaned}
            original = source_values(record, (manifest.get('input') or {}).get('mapping') or {}, row)
            fields = {k: v for k, v in fields.items() if v != original.get(k)}
            if fields != (prior.get('fields') or {}):
                entry = {**prior, 'fields': fields, 'revision': expected_revision + 1, 'updated_at': self._now(),
                         'source_record_sha256': identity(record)}
                payload['entries'].setdefault(self._scope(manifest), {})[row['release_unit_id']] = entry
                self._save_edits(payload)
        return self.get(identifier, index_id=expected_index_id)

    def submit_reindex(self, identifier, *, expected_revision, expected_index_id):
        row, manifest, directory, active_id = self._row(identifier, expected_index_id)
        source = self._source_path(manifest, directory)
        if source is None: raise ValueError('The matching original knowledge source is unavailable.')
        mode = (manifest.get('input') or {}).get('mode')
        if mode not in {'lexical', 'hybrid'}: raise ValueError('The source index has no supported retrieval mode.')
        with self._edit_lock() as payload:
            edit = self._guard(row, manifest, active_id, expected_revision, expected_index_id, payload)
            # Repeated clicks and retries reuse the persisted exact snapshot,
            # output directory and embedding identity. New edits get a new job.
            if edit.get('job_id') and edit.get('job_revision') == expected_revision:
                previous = resource_jobs.get_job(edit['job_id'])
                if previous.get('status') in {'failed', 'interrupted', 'cancelled'}:
                    previous = resource_jobs.resume_job(previous['id'])
                return self._job_response(previous)
            info, record = self._source(manifest, directory, row['release_unit_id'])
            if record is None: raise ValueError('Wait for the original knowledge source to finish loading.')
            snapshot = {'schema_version': 1, 'base_index_id': manifest['index_id'],
                        'source_sha256': manifest['input']['knowledge_sha256'], 'unit_id': row['release_unit_id'],
                        'revision': expected_revision, 'source_record_sha256': identity(record), 'fields': edit.get('fields') or {}}
            snapshot_sha = identity(snapshot)
            snapshot_path = self.root / 'knowledge' / 'edit-snapshots' / (snapshot_sha + '.json')
            if not snapshot_path.exists(): write_json(snapshot_path, snapshot)
            output_id = safe_id('edit-' + snapshot_sha[:24] + '-' + uuid.uuid4().hex[:8])
            parameters = {'source': str(source), 'snapshot': str(snapshot_path), 'snapshot_sha256': snapshot_sha,
                          'index_id': output_id, 'base_index_id': manifest['index_id'], 'mode': mode,
                          'unit_id': row['release_unit_id'], 'revision': expected_revision,
                          'expected_source_sha256': manifest['input']['knowledge_sha256'],
                          'mapping': (manifest.get('input') or {}).get('mapping') or {}, 'resume': True}
            if mode == 'hybrid':
                from theraskill_agent.indexing.embedding import resolve, fingerprint
                parameters['embedding_identity'] = fingerprint(resolve())
            job = resource_jobs.submit_job('reindex', parameters, request_id='knowledge-edit-' + output_id)
            payload['entries'].setdefault(self._scope(manifest), {})[row['release_unit_id']] = {
                **edit, 'revision': expected_revision, 'job_id': job['id'], 'job_revision': expected_revision,
                'updated_at': edit.get('updated_at') or self._now()}
            self._save_edits(payload)
        return self._job_response(job)

    @staticmethod
    def _job_response(job):
        params = job.get('parameters') or {}; result = job.get('result') or {}
        return {'job_id': job['id'], 'status': job['status'], 'index_id': result.get('index_id'),
                'index_directory_id': params.get('index_id'), 'base_index_id': params.get('base_index_id'),
                'revision': params.get('revision'), 'mode': params.get('mode'), 'activation_required': True,
                'resources_url': '/?workspace=knowledge&' + urlencode({'job_id': job['id']})}
