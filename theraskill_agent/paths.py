"""Paths owned by a single TheraSkillAgent project checkout."""

from __future__ import annotations

import os
import json
from pathlib import Path


def project_root() -> Path:
    """Keep mutable files in the checkout or a user-owned installed workspace."""
    override = os.environ.get("THERASKILL_AGENT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    code_root = Path(__file__).resolve().parents[1]
    if (code_root / "pyproject.toml").is_file() and (code_root / "theraskill-agent").is_file():
        return code_root
    return Path.home() / "TheraSkillAgent"


def resource_root() -> Path:
    """Read-only business assets, available in both source and wheel installs."""
    bundled = Path(__file__).resolve().parent / "_resources"
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[1]


def resource_path(relative: str | Path) -> Path:
    """Use project overrides when present, otherwise the packaged default."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("resource path must be relative to the project")
    local = project_root() / relative
    return local if local.exists() else resource_root() / relative


def data_root(root: str | Path | None = None) -> Path:
    if root is None and not os.environ.get("THERASKILL_AGENT_ROOT"):
        legacy_home = os.environ.get("HERMES_HOME", "").strip()
        if legacy_home:
            return Path(legacy_home).expanduser().resolve() / "platform-data" / "theraskill"
    return (Path(root).expanduser().resolve() if root else project_root()) / "data" / "theraskill"


def state_root() -> Path:
    return project_root() / "state"


def logs_root() -> Path:
    return project_root() / "logs"


def active_release_id(default: str = "phase6_v1") -> str:
    """Return the active knowledge release id from project-owned data."""
    configured = os.environ.get("THERASKILL_KNOWLEDGE_RELEASE_ID", "").strip()
    if configured:
        return configured
    try:
        selected = json.loads((data_root() / "active-index.json").read_text(encoding="utf-8"))
        if selected.get("knowledge_id"):
            return str(selected["knowledge_id"])
        if selected.get("knowledge_sha256"):
            return "knowledge_" + str(selected["knowledge_sha256"])[:16]
    except (OSError, json.JSONDecodeError):
        pass
    manifest = data_root() / "current" / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    value = str(payload.get("release_id") or "").strip()
    return value or default
