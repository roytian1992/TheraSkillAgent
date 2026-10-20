"""Transactional checkpoints and an append-only timeline for interactive tasks."""
from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .paths import data_root


def now():
    return datetime.now(timezone.utc).isoformat()


class TaskConflict(ValueError):
    pass


class TaskStore:
    def __init__(self, path=None):
        self.path = Path(path or data_root() / 'tasks/tasks.sqlite')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, revision INTEGER NOT NULL, updated TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS task_events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task_id,seq);''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA busy_timeout=30000')
        return db

    def create(self, kind, goal, language='zh', inputs=None):
        task = dict(id='task_'+uuid.uuid4().hex, kind=kind, goal=goal, language=language,
                    status='queued', stage='start', revision=0, created_at=now(), updated_at=now(),
                    inputs=inputs or {}, checkpoints={}, interaction=None, error=None, attempt=0)
        with self.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,?,?)', (task['id'],0,task['updated_at'],json.dumps(task,ensure_ascii=False)))
        self.event(task['id'], 'created', {'kind':kind})
        return task

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not row:
            raise KeyError(task_id)
        return json.loads(row[0])

    def save(self, task):
        task = dict(task)
        old = task['revision']
        task.update(revision=old+1,updated_at=now())
        with self.connect() as db:
            changed = db.execute('UPDATE tasks SET revision=?,updated=?,payload=? WHERE id=? AND revision=?',
                                 (task['revision'],task['updated_at'],json.dumps(task,ensure_ascii=False),task['id'],old)).rowcount
            if not changed:
                raise TaskConflict('Task changed. Refresh before continuing.')
        return task

    def list(self, limit=100, offset=0):
        with self.connect() as db:
            rows=db.execute('SELECT payload FROM tasks ORDER BY updated DESC,id LIMIT ? OFFSET ?',
                            (max(1,min(limit,500)),max(0,offset))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def event(self, task_id, kind, data=None):
        event={'at':now(),'kind':kind,'data':data or {}}
        with self.connect() as db:
            db.execute('INSERT INTO task_events(task_id,payload) VALUES (?,?)',(task_id,json.dumps(event,ensure_ascii=False,default=str)))
        return event

    def events(self, task_id):
        with self.connect() as db:
            rows=db.execute('SELECT payload FROM task_events WHERE task_id=? ORDER BY seq',(task_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]
