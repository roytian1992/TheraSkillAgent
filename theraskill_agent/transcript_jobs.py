"""Background transcript jobs using the existing transactional task store."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
from typing import Any, Callable

from .indexing.common import exclusive, identity
from .paths import data_root
from .task_store import TaskStore, TaskConflict
from .text import redact_secrets

_ACTIVE: set[tuple[str, str]] = set()
_ACTIVE_LOCK = threading.RLock()


class TranscriptJobs:
    def __init__(self, prepare: Callable, execute: Callable, *, store: TaskStore | None = None):
        self.store = store or TaskStore(data_root() / 'transcript' / 'jobs.sqlite')
        self.prepare = prepare
        self.execute = execute
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='theraskill-transcript')
        self.lock = threading.RLock()
        self.closed = False
        self.owned: set[str] = set()
        self.key = str(self.store.path.resolve())
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS transcript_requests (signature TEXT PRIMARY KEY, task_id TEXT NOT NULL)')
        for task in self.store.list(500):
            self._recover(task)

    def _recover(self, task):
        if task['status'] not in {'running', 'queued'}:
            return task
        owner = task.get('worker_pid')
        with _ACTIVE_LOCK:
            local = (self.key, task['id']) in _ACTIVE
        live = local
        if owner and owner != os.getpid():
            try:
                os.kill(int(owner), 0)
                live = True
            except (OSError, ValueError):
                pass
        if not live:
            task.update(status='interrupted', error='Worker stopped; resume the saved review stages.')
            try:
                task = self.store.save(task)
                self.store.event(task['id'], 'interrupted')
            except TaskConflict:
                task = self.store.get(task['id'])
        return task

    def _public(self, task, *, summary=False):
        result = {key: task.get(key) for key in ('id', 'status', 'stage', 'revision', 'created_at', 'updated_at', 'error', 'attempt')}
        result.update(job_id=task['id'], operation=task['inputs']['operation'],
                      run_id=task['inputs']['run_id'], filename=task['inputs']['request'].get('filename'),
                      progress=task['checkpoints'].get('progress', {}))
        if not summary:
            result.update(request=task['inputs']['request'], result=task['checkpoints'].get('result'))
        return result

    def get(self, job_id):
        with self.lock:
            return self._public(self._recover(self.store.get(job_id)))

    def list(self):
        with self.lock:
            return [self._public(self._recover(task), summary=True) for task in self.store.list(100)]

    def submit(self, request: dict[str, Any], operation: str):
        if operation not in {'review', 'package'} or request.get('mode') != 'model':
            raise ValueError('Transcript jobs require a model review or package operation')
        run_id = self.prepare(request)
        if request.get('review_run_id') and request['review_run_id'] != run_id:
            raise TaskConflict('Transcript or model configuration changed; create a new review')
        payload = {**request, 'review_run_id': run_id}
        signature = identity({'operation': operation, 'request': payload})
        with self.lock, exclusive(self.store.path.with_suffix('.submit.lock')):
            if self.closed:
                raise TaskConflict('Transcript worker is closing')
            # Exact duplicates return their existing job. An explicit retry endpoint
            # advances failed attempts; posting twice never creates two model runs.
            with self.store.connect() as db:
                prior = db.execute('SELECT task_id FROM transcript_requests WHERE signature=?', (signature,)).fetchone()
            if prior:
                return {**self._public(self._recover(self.store.get(prior[0]))), 'reused_job': True}
            task = self.store.create('transcript_' + operation, request.get('filename', 'Transcript review'),
                                     request.get('output_language', 'auto'),
                                     {'operation': operation, 'request': payload, 'run_id': run_id, 'request_sha256': signature})
            task['worker_pid'] = os.getpid()
            task = self.store.save(task)
            with self.store.connect() as db:
                db.execute('INSERT INTO transcript_requests VALUES (?, ?)', (signature, task['id']))
            self._launch(task['id'])
            return self._public(task)

    def retry(self, job_id: str, *, revision: int | None = None):
        with self.lock:
            if self.closed:
                raise TaskConflict('Transcript worker is closing')
            task = self._recover(self.store.get(job_id))
            if revision is not None and task['revision'] != revision:
                raise TaskConflict('Job changed; refresh before retrying')
            if task['status'] not in {'failed', 'partial', 'interrupted'}:
                raise TaskConflict('Only failed, partial or interrupted jobs can be resumed')
            if self.prepare(task['inputs']['request']) != task['inputs']['run_id']:
                raise TaskConflict('Transcript, skills, knowledge or model configuration changed; create a new review')
            with _ACTIVE_LOCK:
                if (self.key, job_id) in _ACTIVE:
                    raise TaskConflict('Previous attempt is finishing; refresh before retrying')
                task.update(status='queued', error=None, attempt=task['attempt'] + 1, worker_pid=os.getpid())
                task = self.store.save(task)
                self.store.event(job_id, 'retry')
                self._launch(job_id)
            return self._public(task)

    def _launch(self, job_id):
        with _ACTIVE_LOCK:
            key = (self.key, job_id)
            if key in _ACTIVE:
                return
            _ACTIVE.add(key)
            self.owned.add(job_id)
        try:
            self.pool.submit(self._run, job_id)
        except Exception:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(key)
            raise

    def _update(self, job_id, **updates):
        with self.lock:
            task = self.store.get(job_id)
            task.update(updates)
            return self.store.save(task)

    def _run(self, job_id):
        released = False
        try:
            with self.lock:
                if self.closed:
                    self._update(job_id, status='interrupted', error='Worker closed before execution; resume this job.')
                    return
            task = self._update(job_id, status='running', stage='window_reviews')
            self.store.event(job_id, 'running')
            def report(value):
                with self.lock:
                    current = self.store.get(job_id)
                    current['stage'] = value.get('stage', current['stage'])
                    current['checkpoints']['progress'] = value
                    self.store.save(current)
            result = self.execute(task['inputs']['request'], task['inputs']['operation'], report)
            review = result.get('reviews') or {}
            review_status = review.get('status')
            status = review_status if review_status in {'partial', 'failed'} else 'completed'
            with self.lock:
                current = self.store.get(job_id)
                current['checkpoints']['result'] = result
                current.update(status=status, stage='complete', error='Some review stages failed or did not pass evidence verification; resume to retry only those stages.' if status != 'completed' else None)
                self.store.save(current)
                self.store.event(job_id, status)
                with _ACTIVE_LOCK:
                    _ACTIVE.discard((self.key, job_id))
                    self.owned.discard(job_id)
                released = True
        except Exception as exc:
            with self.lock:
                self._update(job_id, status='failed', error=redact_secrets(str(exc))[:1000])
                self.store.event(job_id, 'failed')
                with _ACTIVE_LOCK:
                    _ACTIVE.discard((self.key, job_id))
                    self.owned.discard(job_id)
                released = True
        finally:
            if not released:
                with _ACTIVE_LOCK:
                    _ACTIVE.discard((self.key, job_id))
                    self.owned.discard(job_id)

    def close(self):
        with self.lock:
            self.closed = True
            self.pool.shutdown(wait=False, cancel_futures=True)
            for task in self.store.list(500):
                if task['status'] == 'queued' and task['id'] in self.owned:
                    task.update(status='interrupted', error='Worker closed before execution; resume this job.')
                    self.store.save(task)
                    with _ACTIVE_LOCK:
                        _ACTIVE.discard((self.key, task['id']))
