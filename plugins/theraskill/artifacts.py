"""Runtime artifact resolution for installed TheraSkill releases."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from theraskill_agent.paths import data_root

from .contracts import ArtifactManifest, ContractError


class ArtifactStoreError(RuntimeError):
    """Raised when a runtime release cannot be resolved safely."""


class ArtifactStore:
    """Resolve immutable TheraSkill releases below one platform-data root."""

    def __init__(self, root: str | Path | None = None) -> None:
        if root is None:
            # Keep a read-only compatibility bridge for callers that still
            # provide the historical host data root.  New deployments use the
            # TheraSkillAgent-owned data root by default.
            root = data_root()
        self.root = Path(root).expanduser()
        self.releases_root = self.root / "releases"

    def _manifest_path(self, release_id: str) -> Path:
        if release_id == "current":
            current = self.root / "current"
            if current.is_dir():
                return current / "manifest.json"
            return self.root / "current.json"
        if not release_id or release_id in {".", ".."} or "/" in release_id or "\\" in release_id:
            raise ArtifactStoreError(f"invalid release_id: {release_id!r}")
        return self.releases_root / release_id / "manifest.json"

    def load_manifest(self, release_id: str = "current") -> ArtifactManifest:
        path = self._manifest_path(release_id)
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ArtifactStoreError(f"release manifest not found: {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(f"cannot read release manifest: {path}") from exc
        try:
            return ArtifactManifest.from_mapping(payload)
        except ContractError as exc:
            raise ArtifactStoreError(f"invalid release manifest: {path}: {exc}") from exc

    def resolve(self, relative_path: str, release_id: str = "current") -> Path:
        manifest = self.load_manifest(release_id)
        artifact_path = manifest.artifacts.get(relative_path, relative_path)
        if artifact_path not in manifest.artifacts.values():
            raise ArtifactStoreError(f"artifact is not declared by release {manifest.release_id}: {relative_path}")
        base = self._manifest_path(release_id).parent.resolve()
        candidate = (base / artifact_path).resolve()
        if base not in candidate.parents and candidate != base:
            raise ArtifactStoreError("artifact path escapes release directory")
        if not candidate.is_file():
            raise ArtifactStoreError(f"artifact file not found: {candidate}")
        return candidate

    def list_releases(self) -> list[str]:
        if not self.releases_root.is_dir():
            return []
        return sorted(
            path.name
            for path in self.releases_root.iterdir()
            if path.is_dir() and (path / "manifest.json").is_file()
        )
