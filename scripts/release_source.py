#!/usr/bin/env python3
"""Explicit source inventory and clean export. Does not stage or push Git."""
from __future__ import annotations
import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r'(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
EXCLUDED_PARTS = {'__pycache__', 'node_modules', 'dist', '.pytest_cache', '.venv', 'artifacts', 'data', 'state', 'sessions', 'memories', 'logs'}


def inventory(root=ROOT):
    config = json.loads((root / 'releases/source-allowlist.json').read_text())
    selected = set()
    for pattern in config['include']:
        selected.update(p for p in root.glob(pattern) if p.is_file())
    results = []
    for path in sorted(selected):
        relative = path.relative_to(root).as_posix()
        if any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts) or any(fnmatch.fnmatch(relative, pat) for pat in config['exclude']):
            continue
        if path.is_symlink():
            raise ValueError(f'symlink not permitted in source inventory: {relative}')
        data = path.read_bytes()
        if len(data) > 5 * 1024 * 1024:
            raise ValueError(f'large file in source inventory: {relative}')
        if SECRET.search(data.decode('utf-8', errors='replace')):
            raise ValueError(f'credential-shaped content in source inventory: {relative}')
        results.append({'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    return {'schema_version': 'theraskill_source_inventory_v1', 'files': results, 'file_count': len(results), 'total_bytes': sum(x['size'] for x in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-git', action='store_true'); parser.add_argument('--export'); parser.add_argument('--manifest'); parser.add_argument('--paths', action='store_true')
    args = parser.parse_args(); report = inventory()
    if args.check_git:
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\0')
        outside = set(filter(None, tracked)) - {item['path'] for item in report['files']}
        if outside:
            raise ValueError('tracked files outside source allowlist: ' + ', '.join(sorted(outside)))
    if args.export:
        destination = Path(args.export).resolve()
        if destination.exists() and any(destination.iterdir()):
            raise ValueError('export destination must be empty')
        for item in report['files']:
            path = destination / item['path']; path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / item['path'], path)
    if args.manifest:
        path = Path(args.manifest); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    if args.paths:
        print('\n'.join(item['path'] for item in report['files']))
    else:
        print(json.dumps({k: v for k, v in report.items() if k != 'files'}, indent=2))


if __name__ == '__main__': main()
