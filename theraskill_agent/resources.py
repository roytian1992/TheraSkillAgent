"""Independent knowledge/index packages, inventories and atomic local activation."""
from __future__ import annotations
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import httpx
from .paths import data_root
from .indexing.common import digest, identity, read_json, write_json, safe_id, safe_path, exclusive
from .indexing import verify_index

PACKAGE_SCHEMA = 'theraskill_resource_package_v1'
CATALOG_SCHEMA = 'theraskill_resource_catalog_v1'


def knowledge_path(value):
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve()
    return data_root() / 'knowledge' / safe_id(str(value)) / 'knowledge.jsonl'


def index_path(value):
    path = Path(value).expanduser()
    if path.is_dir():
        return path.resolve()
    return data_root() / 'indexes' / safe_id(str(value))


def list_resources():
    root = data_root()
    result = {'knowledge': [], 'indexes': [], 'simulation': [], 'active': None, 'previous': None}
    for folder in ('knowledge', 'indexes', 'simulation'):
        for path in sorted((root / folder).glob('*/manifest.json')):
            try:
                record = read_json(path)
                result[folder].append({'id': path.parent.name, 'manifest': record})
            except (OSError, ValueError):
                continue
    for key, filename in (('active', 'active-index.json'), ('previous', 'previous-index.json')):
        if (root / filename).is_file():
            result[key] = read_json(root / filename)
    return result


def activate_index(index, *, knowledge=None):
    root = data_root().resolve()
    path = index_path(index).resolve()
    if not path.is_relative_to(root / 'indexes'):
        raise ValueError('install or build the index under the project indexes directory before activation')
    manifest = verify_index(path)
    if knowledge:
        source = knowledge_path(knowledge)
        if digest(source) != manifest['input']['knowledge_sha256']:
            raise ValueError('knowledge and index fingerprints do not match')
    selection = {'schema_version': 'theraskill_active_index_v1', 'index': path.relative_to(root).as_posix(),
                 'index_id': manifest['index_id'], 'knowledge_sha256': manifest['input']['knowledge_sha256'],
                 'mode': manifest['input']['mode'], 'embedding': manifest.get('embedding')}
    with exclusive(root / '.activation.lock'):
        current = root / 'active-index.json'
        if current.is_file():
            prior = read_json(current)
            if prior != selection:
                write_json(root / 'previous-index.json', prior)
        write_json(current, selection)
    return selection


def rollback_index():
    root = data_root().resolve()
    with exclusive(root / '.activation.lock'):
        previous = read_json(root / 'previous-index.json')
        verify_index(safe_path(root, previous['index']))
        current = read_json(root / 'active-index.json')
        write_json(root / 'active-index.json', previous)
        write_json(root / 'previous-index.json', current)
    return previous


def active_index():
    root = data_root().resolve()
    path = root / 'active-index.json'
    if not path.is_file():
        return None
    selection = read_json(path)
    directory = safe_path(root, selection['index'])
    manifest = read_json(directory / 'manifest.json')
    if manifest.get('status') != 'complete' or manifest.get('index_id') != selection['index_id']:
        raise ValueError('active index is incomplete or was replaced')
    return directory, manifest


def pack_resource(source, output, *, package_id, kind, license_id=None, part_bytes=None):
    """Create an explicit package, never archive the project/workspace wholesale."""
    package_id = safe_id(package_id)
    source, output = Path(source).expanduser().resolve(), Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError('package output exists')
    if kind == 'knowledge':
        if not source.is_file() or not license_id:
            raise ValueError('knowledge packaging requires a JSONL file and a data license')
        from .indexing import plan_index
        planned = plan_index(source)
        sources = {'knowledge.jsonl': source}
        details = {'knowledge_sha256': planned['knowledge_sha256'], 'counts': planned['counts'], 'license': license_id}
    elif kind == 'index':
        manifest = verify_index(source)
        sources = {name: safe_path(source, name) for name in manifest['files']}
        sources['manifest.json'] = source / 'manifest.json'
        details = {'knowledge_sha256': manifest['input']['knowledge_sha256'], 'embedding': manifest.get('embedding'), 'index_id': manifest['index_id']}
    else:
        raise ValueError('supported package kinds: knowledge, index')
    if any(p.is_symlink() for p in sources.values()):
        raise ValueError('packages cannot include symbolic links')
    files = {name: {'size': p.stat().st_size, 'sha256': digest(p)} for name, p in sources.items()}
    package = {'schema_version': PACKAGE_SCHEMA, 'package_id': package_id, 'kind': kind,
               'files': files, 'unpacked_bytes': sum(x['size'] for x in files.values()), **details}
    if part_bytes is not None and int(part_bytes) <= 0:
        raise ValueError('part_bytes must be positive')
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + '.partial')
    try:
        with tarfile.open(temp, 'w:gz') as archive:
            payload = (json.dumps(package, ensure_ascii=False, indent=2) + '\n').encode()
            entry = tarfile.TarInfo('package.json'); entry.size = len(payload); entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(payload))
            for name, path in sources.items():
                archive.add(path, arcname=name, recursive=False)
        if any(digest(path) != files[name]['sha256'] for name, path in sources.items()):
            raise ValueError('source changed during packaging')
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    record = {'id': package_id, 'kind': kind, 'available': True, 'format': 'tar.gz',
              'size': output.stat().st_size, 'sha256': digest(output), 'unpacked_bytes': package['unpacked_bytes'],
              'url': output.name, 'knowledge_sha256': package['knowledge_sha256']}
    if part_bytes:
        size = int(part_bytes)
        if size <= 0:
            raise ValueError('part_bytes must be positive')
        parts = []
        with output.open('rb') as src:
            number = 1
            while first := src.read(1):
                part = output.with_name(output.name + f'.part{number:03d}')
                count = 1
                with part.open('xb') as f:
                    f.write(first)
                    while count < size:
                        chunk = src.read(min(1024 * 1024, size - count))
                        if not chunk:
                            break
                        f.write(chunk)
                        count += len(chunk)
                parts.append({'url': part.name, 'size': count, 'sha256': digest(part)})
                number += 1
        record['parts'] = parts
    write_json(output.with_name(output.name + '.download.json'), record)
    return record


def _valid_hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(x not in '0123456789abcdef' for x in value):
        raise ValueError('a SHA-256 checksum is required for each download')
    return value


def _download(url, path, record, cancel=None, progress=None):
    expected = _valid_hash(record.get('sha256'))
    size = int(record['size'])
    if size < 0:
        raise ValueError('invalid download size')
    if path.exists() and path.stat().st_size == size and digest(path) == expected:
        return
    if path.exists() and path.stat().st_size >= size:
        path.unlink()
    scheme = urlsplit(url).scheme
    if scheme not in {'https', ''}:
        raise ValueError('downloads require HTTPS or a local file')
    offset = path.stat().st_size if path.exists() else 0
    def copy(chunks, mode):
        reported = time.monotonic()
        with path.open(mode) as out:
            for chunk in chunks:
                if cancel and cancel():
                    raise RuntimeError('download cancelled; partial file retained')
                if out.tell() + len(chunk) > size:
                    raise ValueError('download exceeds declared size')
                out.write(chunk)
                if progress and (time.monotonic() - reported >= .5 or out.tell() == size):
                    progress({'stage': 'download', 'bytes': out.tell(), 'total_bytes': size})
                    reported = time.monotonic()
    if scheme == 'https':
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            with client.stream('GET', url, headers={'Range': f'bytes={offset}-'} if offset else {}) as response:
                response.raise_for_status()
                if response.url.scheme != 'https':
                    raise ValueError('download redirected away from HTTPS')
                if response.status_code == 206:
                    if not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                        raise ValueError('invalid range response')
                    mode = 'ab'
                else:
                    mode = 'wb'
                copy(response.iter_bytes(), mode)
    else:
        with Path(url).open('rb') as src:
            src.seek(offset)
            copy(iter(lambda: src.read(1024 * 1024), b''), 'ab')
    if path.stat().st_size != size or digest(path) != expected:
        path.unlink(missing_ok=True)
        raise ValueError('download checksum or size mismatch')


def download_package(catalog, package_id, *, cancel=None, progress=None):
    """Relative URLs are resolved against the catalog, parts are independently resumable."""
    if urlsplit(str(catalog)).scheme == 'https':
        response = httpx.get(str(catalog), timeout=30, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
        base = str(response.url)
        resolve_url = lambda url: urljoin(base, url)
    else:
        location = Path(catalog).expanduser().resolve()
        payload = read_json(location)
        def resolve_url(url):
            return url if urlsplit(url).scheme else str(safe_path(location.parent, url))
    if payload.get('schema_version') != CATALOG_SCHEMA:
        raise ValueError('unsupported resource catalog')
    record = next((r for r in payload.get('packages', []) if r.get('id') == package_id), None)
    if not record or not record.get('available'):
        raise ValueError('resource is not published in this catalog')
    _valid_hash(record.get('sha256'))
    cache = data_root() / 'downloads' / safe_id(package_id) / record['sha256']
    cache.mkdir(parents=True, exist_ok=True)
    parts = record.get('parts') or [record]
    if sum(int(part['size']) for part in parts) != int(record['size']):
        raise ValueError('download part sizes do not match archive size')
    needed = int(record['size']) * 2 + int(record.get('unpacked_bytes', 0))
    if shutil.disk_usage(cache).free < needed:
        raise ValueError('insufficient free space for download and extraction')
    with exclusive(cache / '.download.lock'):
        archive = cache / 'package.tar.gz'
        if archive.is_file() and digest(archive) == record['sha256']:
            return archive, record
        paths = []
        for number, part in enumerate(parts):
            path = cache / f'part{number:05d}'
            _download(resolve_url(part['url']), path, part, cancel, progress)
            paths.append(path)
        with archive.open('wb') as out:
            for path in paths:
                with path.open('rb') as src:
                    shutil.copyfileobj(src, out)
        if digest(archive) != record['sha256']:
            archive.unlink(missing_ok=True)
            raise ValueError('assembled archive checksum mismatch')
        return archive, record


def install_package(archive, *, expected_sha256=None, expected_id=None):
    archive = Path(archive).expanduser().resolve()
    if expected_sha256 and digest(archive) != _valid_hash(expected_sha256):
        raise ValueError('archive checksum mismatch')
    root = data_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.resource-', dir=root) as temp:
        staging = Path(temp)
        with tarfile.open(archive, 'r:gz') as bundle:
            # Manifest must be first, bounding extraction before any files are written.
            first = bundle.next()
            if first is None or first.name != 'package.json' or not first.isfile() or first.size > 16 * 1024 * 1024:
                raise ValueError('package must start with a bounded package.json manifest')
            package = json.load(bundle.extractfile(first))
            if package.get('schema_version') != PACKAGE_SCHEMA or package.get('kind') not in {'knowledge', 'index', 'simulation'}:
                raise ValueError('unsupported package schema or kind')
            pid = safe_id(package['package_id'])
            if expected_id and pid != expected_id:
                raise ValueError('catalog and archive package IDs differ')
            files = package.get('files') or {}
            if not files:
                raise ValueError('empty package inventory')
            needed = sum(int(item['size']) for item in files.values())
            if needed < 0 or needed > shutil.disk_usage(root).free:
                raise ValueError('insufficient disk space for extraction')
            for name, item in files.items():
                safe_path(staging, name)
                _valid_hash(item.get('sha256'))
                if int(item['size']) < 0:
                    raise ValueError('negative file size')
            seen = set()
            for member in bundle:
                if member.name == 'package.json' and member is first:
                    continue
                if member.name in seen or member.name not in files or not member.isfile():
                    raise ValueError('archive contains duplicate, undeclared or non-regular member')
                seen.add(member.name)
                record = files[member.name]
                if member.size != record['size']:
                    raise ValueError('archive member size mismatch')
                target = safe_path(staging, member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as src, target.open('xb') as out:
                    shutil.copyfileobj(src, out)
                if digest(target) != record['sha256']:
                    raise ValueError('archive member checksum mismatch')
            if seen != set(files):
                raise ValueError('archive is missing declared files')
        if package['kind'] == 'index':
            verify_index(staging)
            folder = 'indexes'
            write_json(staging / 'package.json', package)
        else:
            folder = package['kind']
            if folder == 'knowledge':
                if 'knowledge.jsonl' not in files or digest(staging / 'knowledge.jsonl') != package.get('knowledge_sha256'):
                    raise ValueError('knowledge fingerprint mismatch')
            write_json(staging / 'manifest.json', package)
        destination = root / folder / pid
        destination.parent.mkdir(parents=True, exist_ok=True)
        with exclusive(root / '.install.lock'):
            if destination.exists():
                raise ValueError('resource version already exists; installed versions are immutable')
            os.replace(staging, destination)
    return {'id': pid, 'kind': package['kind'], 'path': str(destination)}
