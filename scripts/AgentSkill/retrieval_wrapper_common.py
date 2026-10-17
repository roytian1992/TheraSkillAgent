#!/usr/bin/env python3
"""Shared constants and loader helpers for Hermes retrieval wrappers."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Iterable


ADVANCED_V2_RELPATH = Path("scripts/AdvancedRetrieval/retrieve_advanced_v2.py")
HYBRID_V2_RELPATH = Path("scripts/AdvancedRetrieval/retrieve_hybrid_v2.py")
HYBRID_V3_RELPATH = Path("scripts/AdvancedRetrieval/retrieve_hybrid_v3_llm_structured.py")

RETRIEVAL_SCRIPT_FILENAMES = {
    "v1": "retrieve_theraskill_knowledge.py",
    "v2_deterministic": "retrieve_theraskill_knowledge_v2.py",
    "v2_hybrid": "retrieve_theraskill_knowledge_v2_hybrid.py",
    "v3_llm_hybrid": "retrieve_theraskill_knowledge_v3.py",
}

SKILL_PACKAGE_RUNTIME_SCRIPT = RETRIEVAL_SCRIPT_FILENAMES["v1"]
CURRENT_RUNTIME_BRIDGE_MODE = "v3_llm_hybrid"


def find_project_root(required_relpaths: Iterable[Path] | None = None, doc_markers: Iterable[Path] | None = None) -> Path:
    env_root = os.environ.get("THERASKILL_PROJECT_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    current = Path(__file__).resolve()
    required = tuple(required_relpaths or (HYBRID_V3_RELPATH,))
    markers = tuple(doc_markers or (Path("docs/advanced_retrieval_upgrade_plan.zh.md"),))
    for parent in current.parents:
        if any((parent / relpath).exists() for relpath in required):
            return parent
        if any((parent / marker).exists() for marker in markers):
            return parent
    return current.parents[2]


PROJECT_ROOT = find_project_root()
HERMES_SKILL_DIR = PROJECT_ROOT / "scripts" / "AgentSkill"
WRAPPER_COMMON_PATH = HERMES_SKILL_DIR / "retrieval_wrapper_common.py"


def project_script_path(relpath: Path) -> Path:
    return PROJECT_ROOT / relpath


def runtime_wrapper_path(mode: str) -> Path:
    filename = RETRIEVAL_SCRIPT_FILENAMES[mode]
    return HERMES_SKILL_DIR / filename


def optional_runtime_support_files(include_current: bool = True) -> list[Path]:
    files = [
        runtime_wrapper_path("v2_deterministic"),
        runtime_wrapper_path("v2_hybrid"),
        WRAPPER_COMMON_PATH,
    ]
    if include_current:
        files.append(runtime_wrapper_path(CURRENT_RUNTIME_BRIDGE_MODE))
    return files


def load_project_module(name: str, script_path: Path):
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
