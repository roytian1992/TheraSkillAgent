#!/usr/bin/env python3
"""Shared JSON/JSONL/text IO helpers for active TheraSkill scripts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml


def ensure_output_path(path: str | Path, *, overwrite: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not overwrite:
        raise FileExistsError(f"output exists; pass --overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def ensure_output_dir(path: str | Path, *, overwrite: bool = False, allow_existing_empty: bool = False) -> Path:
    output_dir = Path(path)
    if output_dir.exists() and not overwrite:
        if allow_existing_empty and output_dir.is_dir() and not any(output_dir.iterdir()):
            output_dir.parent.mkdir(parents=True, exist_ok=True)
            return output_dir
        raise FileExistsError(f"output dir exists; pass --overwrite: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    return output_dir


def ensure_resumable_output_path(path: str | Path, *, overwrite: bool = False, resume: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not (overwrite or resume):
        raise FileExistsError(f"output exists; pass --overwrite or --resume: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_json_if_exists(path: str | Path, *, default: Any = None) -> Any:
    input_path = Path(path)
    if not input_path.exists():
        return default
    return read_json(input_path)


def read_yaml(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_json(
    path: str | Path,
    payload: Any,
    *,
    overwrite: bool = True,
    ensure_ascii: bool = False,
    indent: int = 2,
    sort_keys: bool = True,
) -> Path:
    output = ensure_output_path(path, overwrite=overwrite)
    output.write_text(
        json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys) + "\n",
        encoding="utf-8",
    )
    return output


def write_yaml(path: str | Path, payload: Any, *, overwrite: bool = True, sort_keys: bool = False) -> Path:
    output = ensure_output_path(path, overwrite=overwrite)
    with output.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=sort_keys)
    return output


def write_text(path: str | Path, text: str, *, overwrite: bool = True) -> Path:
    output = ensure_output_path(path, overwrite=overwrite)
    output.write_text(text, encoding="utf-8")
    return output


def iter_jsonl(path: str | Path, *, with_line_numbers: bool = False) -> Iterator[Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if with_line_numbers:
                yield line_no, row
            else:
                yield row


def read_jsonl(path: str | Path) -> list[Any]:
    return list(iter_jsonl(path))


def write_jsonl(
    path: str | Path,
    rows: Iterable[Any],
    *,
    overwrite: bool = True,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
    return_sha256: bool = False,
) -> str | None:
    output = ensure_output_path(path, overwrite=overwrite)
    digest = hashlib.sha256() if return_sha256 else None
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(
                row,
                ensure_ascii=ensure_ascii,
                sort_keys=sort_keys,
                separators=separators,
            ) + "\n"
            if digest is not None:
                digest.update(line.encode("utf-8"))
            handle.write(line)
    if digest is None:
        return None
    return digest.hexdigest()


def file_sha256(path: str | Path) -> str | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
