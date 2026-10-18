"""Index-versioned metadata inventory, reusable across planner processes."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from .common import identity, write_json

COLUMNS = ('dedup_family', 'risk_scope', 'default_route', 'primary_knowledge_type', 'primary_evidence_tier')
_CACHE = {}
_LOCK = threading.Lock()


def index_fingerprint(db):
    db = Path(db).resolve()
    files = {}
    for suffix in ('', '-wal', '-journal'):
        path = Path(str(db) + suffix)
        try:
            stat = path.stat()
            files[suffix] = [stat.st_size, stat.st_mtime_ns, stat.st_ino]
        except FileNotFoundError:
            pass
    return {'path': str(db), 'files': files}


def save_inventory(db, values, destination):
    write_json(destination, {'schema_version': 1, 'index': index_fingerprint(db),
                             'portable_signature': portable_signature(db),
                             'values': {key: sorted(values[key]) for key in COLUMNS}})


def portable_signature(db):
    """SQLite's header changes on commits; immutable sidecars can travel with it."""
    db = Path(db)
    with db.open('rb') as handle:
        header = hashlib.sha256(handle.read(100)).hexdigest()
    wal = Path(str(db) + '-wal')
    return {'size': db.stat().st_size, 'header_sha256': header,
            'wal_bytes': wal.stat().st_size if wal.exists() else 0}


def load_inventory(db, *, cache_path=None, trace=None):
    db = Path(db).resolve()
    fingerprint = index_fingerprint(db)
    key = identity(fingerprint)
    def report(source):
        if trace is not None:
            trace.update(source=source, index_fingerprint=key)
    with _LOCK:
        if key in _CACHE:
            report('memory')
            return {k: set(v) for k, v in _CACHE[key].items()}
        for path in (db.parent / 'metadata_inventory.json', cache_path):
            if not path:
                continue
            try:
                record = json.loads(Path(path).read_text())
                values = record['values']
                bundled = Path(path) == db.parent / 'metadata_inventory.json'
                valid_index = record.get('index') == fingerprint
                if bundled and record.get('portable_signature'):
                    current = portable_signature(db)
                    valid_index = not current['wal_bytes'] and record['portable_signature'] == current
                if record.get('schema_version') != 1 or not valid_index:
                    continue
                if set(values) != set(COLUMNS) or any(not isinstance(v, list) or any(not isinstance(x, str) for x in v) for v in values.values()):
                    continue
                inventory = {k: set(v) for k, v in values.items()}
                report('index_sidecar' if Path(path).name == 'metadata_inventory.json' else 'disk_cache')
                break
            except (OSError, ValueError, KeyError, TypeError):
                continue
        else:
            inventory = {key: set() for key in COLUMNS}
            # One streaming pass replaces five full scans of the large metadata
            # table. DISTINCT keeps repeated combinations inside SQLite.
            with closing(sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True)) as con:
                for row in con.execute(f"SELECT DISTINCT {','.join(COLUMNS)} FROM retrieval_meta"):
                    for column, value in zip(COLUMNS, row):
                        if value is not None:
                            inventory[column].add(str(value))
            report('database')
            if cache_path and index_fingerprint(db) == fingerprint:
                try:
                    save_inventory(db, inventory, cache_path)
                except OSError:
                    pass  # Read-only cache storage must not prevent retrieval.
        if len(_CACHE) >= 8:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = inventory
        return {k: set(v) for k, v in inventory.items()}
