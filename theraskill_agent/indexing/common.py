"""Small, dependency-free primitives for portable resources."""
from __future__ import annotations
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temp.open('w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('invalid resource ID')
    return value


def safe_path(root, name):
    p = PurePosixPath(str(name))
    if not name or p.is_absolute() or '..' in p.parts or '\\' in str(name) or ':' in str(name):
        raise ValueError('resource path must be relative and contained')
    root = Path(root).resolve()
    target = root.joinpath(*p.parts)
    if target.is_symlink() or not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise ValueError('resource path escapes root')
    return target


@contextmanager
def exclusive(path):
    """OS releases the lock after a crash; an abandoned lock file is harmless."""
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another operation is already using this resource') from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
