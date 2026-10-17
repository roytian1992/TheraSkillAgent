#!/usr/bin/env python3
"""Hermes-facing wrapper for TheraSkill LLM-structured hybrid retrieval v3."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ADVANCED_RETRIEVAL_DIR = SCRIPT_DIR.parent / "AdvancedRetrieval"
if str(ADVANCED_RETRIEVAL_DIR) not in sys.path:
    sys.path.insert(0, str(ADVANCED_RETRIEVAL_DIR))

from retrieval_surface_registry import FAISS_BGE_M3_SHARDS_DIR, V3_RUNTIME_CACHE_DIR, V3_RUNTIME_REPORT_DIR
from retrieval_wrapper_common import HYBRID_V3_RELPATH, load_project_module, project_script_path


PROJECT_ROOT = project_script_path(Path("."))
V3_RETRIEVER = project_script_path(HYBRID_V3_RELPATH)
RUNTIME_CACHE_DIR = V3_RUNTIME_CACHE_DIR
RUNTIME_REPORT_DIR = V3_RUNTIME_REPORT_DIR


def load_retriever():
    return load_project_module("theraskill_hybrid_retrieval_v3", V3_RETRIEVER)


def has_flag(argv: list[str], flag: str) -> bool:
    return flag in argv


def flag_value(argv: list[str], flag: str) -> str | None:
    if flag not in argv:
        return None
    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        return None
    return argv[idx + 1]


def runtime_output_path(argv: list[str]) -> Path:
    skill = flag_value(argv, "--skill") or "unknown-skill"
    query = flag_value(argv, "--query") or ""
    digest = hashlib.sha256(f"{skill}\n{query}".encode("utf-8")).hexdigest()[:16]
    return RUNTIME_REPORT_DIR / f"{skill}.{digest}.bundle.json"


def with_runtime_defaults(argv: list[str]) -> list[str]:
    defaults = {
        "--dense-backend": "faiss_shards",
        "--model-config": str(PROJECT_ROOT / "configs/theraskill/retrieval.yaml"),
        "--embedding-cache-db": str(RUNTIME_CACHE_DIR / "embedding_cache.sqlite"),
        "--scope-count-cache-db": str(RUNTIME_CACHE_DIR / "scope_count_cache.sqlite"),
        "--faiss-shards-dir": str(FAISS_BGE_M3_SHARDS_DIR),
        "--dense-pool-n": "120",
        "--dense-top-n": "30",
        "--bm25-top-n": "80",
        "--label-top-n": "40",
        "--required-family-top-n": "80",
        "--max-wide-scope-channel-scan": "250000",
        "--max-compact-label-scope": "250000",
        "--max-scoped-dense-ids": "250000",
    }
    patched = list(argv)
    for flag, value in defaults.items():
        if not has_flag(patched, flag):
            patched.extend([flag, value])
    if not has_flag(patched, "--output"):
        patched.extend(["--output", str(runtime_output_path(patched)), "--overwrite"])
    return patched


if __name__ == "__main__":
    retriever = load_retriever()
    sys.argv = with_runtime_defaults(sys.argv)
    raise SystemExit(retriever.main())
