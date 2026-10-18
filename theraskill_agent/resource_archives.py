"""Stream ZIP64 and tar resource members through the same strict installer."""
from __future__ import annotations
import stat
import tarfile
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace


class ZipBundle:
    def __init__(self, archive):
        self.archive = archive
        self.members = []
        for entry in archive.infolist():
            mode = entry.external_attr >> 16
            regular = not entry.is_dir() and not stat.S_ISLNK(mode) and (not stat.S_IFMT(mode) or stat.S_ISREG(mode))
            self.members.append(SimpleNamespace(name=entry.filename, size=entry.file_size,
                isfile=lambda regular=regular: regular, entry=entry))

    def next(self):
        return self.members[0] if self.members else None

    def __iter__(self):
        return iter(self.members)

    def extractfile(self, member):
        return self.archive.open(member.entry)


@contextmanager
def open_bundle(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            yield ZipBundle(archive)
    else:
        with tarfile.open(path, 'r:gz') as archive:
            yield archive
