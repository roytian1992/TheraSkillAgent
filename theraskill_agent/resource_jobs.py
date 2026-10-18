"""Durable resource job records; interruption is explicit and builds can resume."""
from __future__ import annotations
import os
import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from .paths import data_root, state_root
from .indexing.common import read_json, write_json, safe_id, exclusive
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
    if operation == 'verify':
        return verify_index(resources.index_path(p['index']), scratch_root=p.get('scratch_root'))
    if operation == 'search':
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
        return resources.install_package(archive, expected_sha256=checksum, expected_id=expected_id)
    raise ValueError('unknown resource operation')


def submit_job(operation, parameters, *, request_id=None):
    if operation not in {'plan', 'build', 'verify', 'install', 'activate', 'rollback', 'search'}:
        raise ValueError('unknown resource operation')
    job_id = 'resource_' + (hashlib.sha256(request_id.encode()).hexdigest()[:32] if request_id else uuid.uuid4().hex)
    record = {'id': job_id, 'operation': operation, 'parameters': parameters, 'status': 'queued', 'pid': os.getpid()}
    event = threading.Event()
    with exclusive(state_root() / '.resource-submit.lock'):
        if request_id and _path(job_id).is_file():
            existing = get_job(job_id)
            if existing['operation'] != operation or existing['parameters'] != parameters:
                raise ValueError('Resource request ID was already used with different inputs')
            return existing
        with _LOCK:
            _EVENTS[job_id] = event
        write_json(_path(job_id), record)
    def worker():
        try:
            if event.is_set():
                raise BuildCancelled('cancelled before starting')
            record['status'] = 'running'; write_json(_path(job_id), record)
            def progress(value):
                record['progress'] = value; write_json(_path(job_id), record)
            result = run_operation(operation, parameters, progress=progress, cancel=event.is_set)
            record.update(status='succeeded', result=result)
        except Exception as exc:
            record.update(status='cancelled' if event.is_set() or isinstance(exc, BuildCancelled) else 'failed', error=redact_secrets(str(exc)))
        finally:
            write_json(_path(job_id), record)
            with _LOCK:
                _EVENTS.pop(job_id, None)
    _POOL.submit(worker)
    return dict(record)
