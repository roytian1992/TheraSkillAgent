#!/usr/bin/env python3
"""Shared module/path registry for AdvancedRetrieval scripts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADVANCED_RETRIEVAL_DIR = PROJECT_ROOT / "scripts" / "AdvancedRetrieval"
HERMES_SKILL_DIR = PROJECT_ROOT / "scripts" / "AgentSkill"

RETRIEVAL_MODULE_REGISTRY_RELPATH = Path("scripts/AdvancedRetrieval/retrieval_module_registry.py")
RETRIEVAL_SURFACE_REGISTRY_RELPATH = Path("scripts/AdvancedRetrieval/retrieval_surface_registry.py")
RETRIEVE_ADVANCED_V2_RELPATH = Path("scripts/AdvancedRetrieval/retrieve_advanced_v2.py")
RETRIEVE_HYBRID_V2_RELPATH = Path("scripts/AdvancedRetrieval/retrieve_hybrid_v2.py")
RETRIEVE_HYBRID_V3_RELPATH = Path("scripts/AdvancedRetrieval/retrieve_hybrid_v3_llm_structured.py")
PLAN_RETRIEVAL_QUERY_RELPATH = Path("scripts/AdvancedRetrieval/plan_retrieval_query_llm.py")
BUILD_RETRIEVAL_BENCHMARK_CASES_RELPATH = Path("scripts/AdvancedRetrieval/build_retrieval_benchmark_cases.py")
BUILD_RETRIEVAL_METADATA_V2_RELPATH = Path("scripts/AdvancedRetrieval/build_retrieval_metadata_v2.py")
BUILD_COMPACT_LABEL_INDEX_RELPATH = Path("scripts/AdvancedRetrieval/build_compact_label_index.py")
BUILD_DENSE_ROW_MAP_RELPATH = Path("scripts/AdvancedRetrieval/build_dense_row_map.py")
BUILD_DENSE_EMBEDDING_INDEX_RELPATH = Path("scripts/AdvancedRetrieval/build_dense_embedding_index.py")
BUILD_FAISS_INDEX_RELPATH = Path("scripts/AdvancedRetrieval/build_faiss_index.py")
BUILD_FAISS_SHARDS_RELPATH = Path("scripts/AdvancedRetrieval/build_faiss_shards.py")
REPORT_ADVANCED_RETRIEVAL_STATUS_RELPATH = Path("scripts/AdvancedRetrieval/report_advanced_retrieval_status.py")
RUN_RETRIEVAL_BENCHMARK_V1_V2_RELPATH = Path("scripts/AdvancedRetrieval/run_retrieval_benchmark.py")
RUN_RETRIEVAL_BENCHMARK_V1_V2_HYBRID_RELPATH = Path("scripts/AdvancedRetrieval/run_hybrid_retrieval_benchmark.py")
RUN_RETRIEVAL_BENCHMARK_V3_RELPATH = Path("scripts/AdvancedRetrieval/run_retrieval_benchmark_v3.py")
RUN_RETRIEVAL_BENCHMARK_V3_COMPARISON_RELPATH = Path("scripts/AdvancedRetrieval/run_retrieval_benchmark_v3_comparison.py")
AUDIT_ADVANCED_RETRIEVAL_V3_COMPLETION_RELPATH = Path("scripts/AdvancedRetrieval/audit_advanced_retrieval_v3_completion.py")
AUDIT_MULTILINGUAL_V3_BENCHMARK_RELPATH = Path("scripts/AdvancedRetrieval/audit_multilingual_v3_benchmark.py")
HERMES_RETRIEVE_V1_RELPATH = Path("scripts/AgentSkill/retrieve_theraskill_knowledge.py")
FINALIZE_ADVANCED_RETRIEVAL_V2_RELPATH = Path("scripts/AdvancedRetrieval/finalize_advanced_retrieval_v2.py")
AUDIT_FORMAL_HYBRID_BENCHMARK_RELPATH = Path("scripts/AdvancedRetrieval/audit_formal_hybrid_benchmark.py")
SUMMARIZE_ADVANCED_RETRIEVAL_RELEASE_RELPATH = Path("scripts/AdvancedRetrieval/summarize_advanced_retrieval_release.py")
WAIT_AND_AUDIT_FORMAL_HYBRID_BENCHMARK_RELPATH = Path("scripts/AdvancedRetrieval/wait_and_audit_formal_hybrid_benchmark.py")

FAISS_IVF_FLAT_INDEX_DIR = PROJECT_ROOT / "outputs/knowledge_engine/indexes/faiss_bge_m3_ivf_flat_n4096_np64"

AUTHORITATIVE_RUNTIME_RETRIEVAL_RELPATHS = (
    PLAN_RETRIEVAL_QUERY_RELPATH,
    RETRIEVE_HYBRID_V3_RELPATH,
    RUN_RETRIEVAL_BENCHMARK_V3_RELPATH,
    RUN_RETRIEVAL_BENCHMARK_V3_COMPARISON_RELPATH,
)

AUTHORITATIVE_INDEX_BUILD_RELPATHS = (
    BUILD_RETRIEVAL_BENCHMARK_CASES_RELPATH,
    BUILD_RETRIEVAL_METADATA_V2_RELPATH,
    BUILD_COMPACT_LABEL_INDEX_RELPATH,
    BUILD_DENSE_ROW_MAP_RELPATH,
    BUILD_DENSE_EMBEDDING_INDEX_RELPATH,
    BUILD_FAISS_INDEX_RELPATH,
    BUILD_FAISS_SHARDS_RELPATH,
)

AUTHORITATIVE_AUDIT_RELPATHS = (
    AUDIT_ADVANCED_RETRIEVAL_V3_COMPLETION_RELPATH,
    AUDIT_MULTILINGUAL_V3_BENCHMARK_RELPATH,
)

ADVANCED_RETRIEVAL_SHARED_SUPPORT_RELPATHS = (
    RETRIEVAL_MODULE_REGISTRY_RELPATH,
    RETRIEVAL_SURFACE_REGISTRY_RELPATH,
)

COMPATIBILITY_WRAPPER_RELPATHS = (
    HERMES_RETRIEVE_V1_RELPATH,
    RETRIEVE_ADVANCED_V2_RELPATH,
    RETRIEVE_HYBRID_V2_RELPATH,
)

HISTORICAL_COMPARISON_RELPATHS = (
    RUN_RETRIEVAL_BENCHMARK_V1_V2_RELPATH,
    RUN_RETRIEVAL_BENCHMARK_V1_V2_HYBRID_RELPATH,
)

LEGACY_RETRIEVAL_HELPER_RELPATHS = (
    REPORT_ADVANCED_RETRIEVAL_STATUS_RELPATH,
    FINALIZE_ADVANCED_RETRIEVAL_V2_RELPATH,
    AUDIT_FORMAL_HYBRID_BENCHMARK_RELPATH,
    SUMMARIZE_ADVANCED_RETRIEVAL_RELEASE_RELPATH,
    WAIT_AND_AUDIT_FORMAL_HYBRID_BENCHMARK_RELPATH,
)

CURRENT_AUTHORITATIVE_CODE_SURFACE_RELPATHS = (
    *AUTHORITATIVE_RUNTIME_RETRIEVAL_RELPATHS,
    *AUTHORITATIVE_INDEX_BUILD_RELPATHS,
    *AUTHORITATIVE_AUDIT_RELPATHS,
    *ADVANCED_RETRIEVAL_SHARED_SUPPORT_RELPATHS,
)

NON_AUTHORITATIVE_STRUCTURED_SURFACE_RELPATHS = (
    *COMPATIBILITY_WRAPPER_RELPATHS,
    *HISTORICAL_COMPARISON_RELPATHS,
    *LEGACY_RETRIEVAL_HELPER_RELPATHS,
)

AUTHORITATIVE_PUBLIC_RETRIEVAL_SURFACE = AUTHORITATIVE_RUNTIME_RETRIEVAL_RELPATHS
COMPATIBILITY_RETRIEVAL_SURFACE = COMPATIBILITY_WRAPPER_RELPATHS
HISTORICAL_COMPARISON_RETRIEVAL_SURFACE = HISTORICAL_COMPARISON_RELPATHS
LEGACY_RETRIEVAL_HELPER_SURFACE = LEGACY_RETRIEVAL_HELPER_RELPATHS

AUTHORITATIVE_RUNTIME_RETRIEVAL = tuple(path.name for path in AUTHORITATIVE_RUNTIME_RETRIEVAL_RELPATHS)
AUTHORITATIVE_INDEX_BUILD = tuple(path.name for path in AUTHORITATIVE_INDEX_BUILD_RELPATHS)
AUTHORITATIVE_AUDIT = tuple(path.name for path in AUTHORITATIVE_AUDIT_RELPATHS)
ADVANCED_RETRIEVAL_SHARED_SUPPORT = tuple(path.name for path in ADVANCED_RETRIEVAL_SHARED_SUPPORT_RELPATHS)
COMPATIBILITY_WRAPPERS = tuple(path.name for path in COMPATIBILITY_WRAPPER_RELPATHS)
HISTORICAL_COMPARISON_RETRIEVAL = tuple(path.name for path in HISTORICAL_COMPARISON_RELPATHS)
LEGACY_RETRIEVAL_HELPERS = tuple(path.name for path in LEGACY_RETRIEVAL_HELPER_RELPATHS)
CURRENT_AUTHORITATIVE_CODE_SURFACE = tuple(path.name for path in CURRENT_AUTHORITATIVE_CODE_SURFACE_RELPATHS)
NON_AUTHORITATIVE_STRUCTURED_SURFACE = tuple(path.name for path in NON_AUTHORITATIVE_STRUCTURED_SURFACE_RELPATHS)

_MODULE_CACHE: dict[tuple[str, str], Any] = {}


def project_script_path(relpath: Path) -> Path:
    return PROJECT_ROOT / relpath


def load_project_module(name: str, relpath: Path):
    cache_key = (name, str(relpath))
    if cache_key in _MODULE_CACHE:
        return _MODULE_CACHE[cache_key]
    script_path = project_script_path(relpath)
    script_dir = str(script_path.parent)
    inserted = False
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
        inserted = True
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {script_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        _MODULE_CACHE[cache_key] = module
        return module
    finally:
        if inserted:
            try:
                sys.path.remove(script_dir)
            except ValueError:
                pass


def load_v1_wrapper():
    return load_project_module("retrieve_v1", HERMES_RETRIEVE_V1_RELPATH)


def load_advanced_v2():
    return load_project_module("retrieve_advanced_v2", RETRIEVE_ADVANCED_V2_RELPATH)


def load_hybrid_v2():
    return load_project_module("retrieve_hybrid_v2", RETRIEVE_HYBRID_V2_RELPATH)


def load_hybrid_v3():
    return load_project_module("retrieve_hybrid_v3_llm_structured", RETRIEVE_HYBRID_V3_RELPATH)


def load_planner():
    return load_project_module("plan_retrieval_query_llm", PLAN_RETRIEVAL_QUERY_RELPATH)
