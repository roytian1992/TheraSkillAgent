"""Read-only adapters for common datasets used by knowledge extraction.

The adapter registry is deliberately data driven: callers can inspect a source
and sample records before deciding what fields and knowledge to extract.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable


SUPPORTED_SUFFIXES = {".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt", ".md", ".markdown"}


@dataclass(frozen=True)
class SourceInfo:
    path: str
    format: str
    adapter: str
    fingerprint: str
    records: int | None = None
    size_bytes: int = 0
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceRecord:
    record_id: str
    payload: Any
    source_ref: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class SourceAdapterProtocol(Protocol):
    """Contract implemented by every source adapter.

    An adapter only translates a source into stable records.  Extraction goals
    and output schemas stay in :mod:`dynamic_extraction.contracts`, so adding a
    new dataset never requires changing the extraction engine.
    """

    name: str

    def can_handle(self, path: Path) -> bool: ...
    def iter_records(self, path: Path) -> Iterator[SourceRecord]: ...
    def inspect(self, path: Path) -> SourceInfo: ...
    def sample(self, path: Path, limit: int = 5) -> list[SourceRecord]: ...
    def fingerprint(self, path: Path) -> str: ...


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    else:
        # Directory fingerprints are content based.  mtime-only fingerprints
        # made resumable jobs rerun after a harmless file copy and could miss a
        # changed file when timestamps were preserved.
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(str(child.relative_to(path)).encode("utf-8"))
            digest.update(b"\0")
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


class SourceAdapter:
    name = "base"

    def can_handle(self, path: Path) -> bool:
        return False

    def iter_records(self, path: Path) -> Iterator[SourceRecord]:
        raise NotImplementedError

    def fingerprint(self, path: Path) -> str:
        return _fingerprint(path)

    def inspect(self, path: Path) -> SourceInfo:
        count = sum(1 for _ in self.iter_records(path))
        return SourceInfo(
            str(path),
            _format(path),
            self.name,
            self.fingerprint(path),
            count,
            _size(path),
            {"is_directory": path.is_dir()},
        )

    def sample(self, path: Path, limit: int = 5) -> list[SourceRecord]:
        return list(_take(self.iter_records(path), max(0, limit)))


class JsonAdapter(SourceAdapter):
    name = "json"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in {".json", ".jsonl", ".ndjson"}

    def iter_records(self, path: Path) -> Iterator[SourceRecord]:
        suffix = path.suffix.lower()
        if suffix in {".jsonl", ".ndjson"}:
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle, 1):
                    if line.strip():
                        yield SourceRecord(f"{path.name}:{index}", json.loads(line), str(path), {"line": index})
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload if isinstance(payload, list) else [payload]
        for index, value in enumerate(values):
            yield SourceRecord(f"{path.name}:{index}", value, str(path), {"index": index})


class DelimitedAdapter(SourceAdapter):
    name = "delimited"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in {".csv", ".tsv"}

    def iter_records(self, path: Path) -> Iterator[SourceRecord]:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(encoding="utf-8", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle, delimiter=delimiter), 1):
                yield SourceRecord(f"{path.name}:{index}", dict(row), str(path), {"row": index})


class TextAdapter(SourceAdapter):
    name = "text"

    def can_handle(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in {".txt", ".md", ".markdown"}

    def iter_records(self, path: Path) -> Iterator[SourceRecord]:
        yield SourceRecord(path.name, path.read_text(encoding="utf-8"), str(path), {"format": _format(path)})


class DirectoryAdapter(SourceAdapter):
    name = "directory"

    def can_handle(self, path: Path) -> bool:
        return path.is_dir()

    def iter_records(self, path: Path) -> Iterator[SourceRecord]:
        for child in sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES):
            adapter = get_adapter(child)
            relative = str(child.relative_to(path))
            for record in adapter.iter_records(child):
                # Prefix IDs with the relative child path so two files with an
                # identical basename cannot overwrite one another downstream.
                yield SourceRecord(
                    record_id=f"{relative}:{record.record_id}",
                    payload=record.payload,
                    source_ref=record.source_ref,
                    metadata={**record.metadata, "relative_path": relative, "adapter": adapter.name},
                )


ADAPTERS: list[SourceAdapterProtocol] = [JsonAdapter(), DelimitedAdapter(), TextAdapter(), DirectoryAdapter()]


def register_adapter(adapter: SourceAdapterProtocol, *, prepend: bool = False) -> None:
    """Register a custom adapter for the current process.

    Custom adapters are intentionally explicit and in-process.  Deployments
    can register them during startup without modifying this package or the
    extraction executor.
    """
    if not isinstance(adapter, SourceAdapterProtocol):
        raise TypeError("adapter must implement SourceAdapterProtocol")
    if not getattr(adapter, "name", "").strip():
        raise ValueError("adapter.name is required")
    ADAPTERS[:] = [item for item in ADAPTERS if item.name != adapter.name]
    if prepend:
        ADAPTERS.insert(0, adapter)
    else:
        ADAPTERS.append(adapter)


def unregister_adapter(name: str) -> None:
    ADAPTERS[:] = [item for item in ADAPTERS if item.name != str(name).strip()]


def adapter_registry() -> tuple[SourceAdapterProtocol, ...]:
    return tuple(ADAPTERS)


def get_adapter(path: str | Path) -> SourceAdapter:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"source does not exist: {candidate}")
    for adapter in ADAPTERS:
        if adapter.can_handle(candidate):
            return adapter
    raise ValueError(f"no source adapter for: {candidate}")


def inspect_source(path: str | Path) -> SourceInfo:
    candidate = Path(path).expanduser().resolve()
    return get_adapter(candidate).inspect(candidate)


def sample_source(path: str | Path, limit: int = 5) -> list[SourceRecord]:
    candidate = Path(path).expanduser().resolve()
    return get_adapter(candidate).sample(candidate, limit)


def _format(path: Path) -> str:
    return "directory" if path.is_dir() else path.suffix.lower().lstrip(".") or "unknown"


def _size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else path.stat().st_size


def _take(values: Iterable[SourceRecord], limit: int) -> Iterator[SourceRecord]:
    for index, item in enumerate(values):
        if index >= limit:
            break
        yield item
