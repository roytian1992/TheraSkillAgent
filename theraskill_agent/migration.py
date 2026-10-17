"""Controlled migration from the historical runtime data layout.

Only an explicit allowlist is copied.  Releases (which can be tens of GB) are
opt-in, symlinks and secret-looking files are skipped, and ``plan`` never
writes anything.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import data_root, project_root


DEFAULT_SOURCE = project_root().parent / "hermes-agent-runtime" / "platform-data" / "theraskill"
ALLOWLIST = ("audit", "contribution", "evaluations", "expert", "runtime", "simulation", "support")


@dataclass(frozen=True)
class MigrationReport:
    source: str
    destination: str
    planned: bool
    copied: tuple[str, ...]
    skipped: tuple[str, ...]
    bytes: int
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_size(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(_safe_size(child) for child in path.iterdir())


def migrate_data(source: str | Path | None = None, destination: str | Path | None = None, *, plan: bool = False, include_releases: bool = False, max_bytes: int | None = None) -> MigrationReport:
    src = Path(source or DEFAULT_SOURCE).expanduser().resolve()
    dest = Path(destination or data_root()).expanduser().resolve()
    if not src.is_dir():
        return MigrationReport(str(src), str(dest), plan, (), (), 0, "source directory does not exist")
    names = list(ALLOWLIST) + (["releases"] if include_releases else [])
    copied: list[str] = []
    skipped: list[str] = []
    total = 0
    for name in names:
        item = src / name
        if not item.exists() or item.is_symlink():
            skipped.append(name)
            continue
        size = _safe_size(item)
        if max_bytes is not None and total + size > max_bytes:
            skipped.append(f"{name} (max_bytes)")
            continue
        total += size
        copied.append(name)
        if not plan:
            target = dest / name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True, symlinks=False)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    if not plan:
        (dest / "migration-manifest.json").write_text(json.dumps({"source": str(src), "copied": copied, "bytes": total}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reason = "releases require --include-releases" if not include_releases and (src / "releases").exists() else None
    return MigrationReport(str(src), str(dest), plan, tuple(copied), tuple(skipped), total, reason)
