"""Project-local settings and secret loading for TheraSkillAgent."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from .paths import project_root


def cfg_get(config: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = config
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            return default
        value = value[key]
    return value


def load_config_readonly(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path).expanduser() if path else project_root() / "config.yaml"
    if not path.is_absolute():
        path = project_root() / path
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("config.yaml must contain a mapping")
    return value


def load_env() -> dict[str, str]:
    """Read only this project's .env; explicit process credentials win."""
    values = dotenv_values(project_root() / ".env", interpolate=False)
    return {str(key): str(os.environ.get(key, value)) for key, value in values.items() if value is not None}
