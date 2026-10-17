#!/usr/bin/env python3
"""Canonical output/path registry for AdvancedRetrieval artifact surfaces."""

from __future__ import annotations

from pathlib import Path

from domains.theraskill.runtime import runtime_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]

RETRIEVAL_BENCHMARK_ROOT = PROJECT_ROOT / "outputs" / "retrieval_benchmark"
RETRIEVAL_BENCHMARK_CASES_DIR = RETRIEVAL_BENCHMARK_ROOT / "cases"
RETRIEVAL_BENCHMARK_REPORTS_DIR = RETRIEVAL_BENCHMARK_ROOT / "reports"
RETRIEVAL_BENCHMARK_QA_DIR = RETRIEVAL_BENCHMARK_ROOT / "qa"
RETRIEVAL_BENCHMARK_MANIFESTS_DIR = RETRIEVAL_BENCHMARK_ROOT / "manifests"
RETRIEVAL_BENCHMARK_CACHE_DIR = RETRIEVAL_BENCHMARK_ROOT / "cache"

ADVANCED_RETRIEVAL_V2_RUN_ROOT = PROJECT_ROOT / "work" / "runs" / "advanced_retrieval_v2_20260706"
ADVANCED_RETRIEVAL_V2_LOG_DIR = ADVANCED_RETRIEVAL_V2_RUN_ROOT / "logs"

ADVANCED_RETRIEVAL_UPGRADE_PLAN_DOC = PROJECT_ROOT / "docs" / "advanced_retrieval_upgrade_plan.zh.md"

_CURRENT_RETRIEVAL_ROOT = runtime_root() / "current" / "retrieval"
PHASE6_SQLITE_DB_PATH = _CURRENT_RETRIEVAL_ROOT / "phase6_retrieval_fts.sqlite"
STRUCTURED_INDEX_PATH = _CURRENT_RETRIEVAL_ROOT / "phase6_structured_index.jsonl"
RETRIEVAL_TEXT_VIEW_PATH = _CURRENT_RETRIEVAL_ROOT / "phase6_retrieval_text_view.jsonl"
METADATA_V2_DIR = _CURRENT_RETRIEVAL_ROOT / "metadata_v2"
METADATA_V2_DB_PATH = _CURRENT_RETRIEVAL_ROOT / "metadata_v2.sqlite"
METADATA_V2_REPORT_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "reports" / "metadata_v2_build_report.json"
METADATA_V2_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "metadata_v2_qa.json"
COMPACT_LABEL_V1_DIR = _CURRENT_RETRIEVAL_ROOT / "compact_label_v1"
COMPACT_LABEL_V1_DB_PATH = _CURRENT_RETRIEVAL_ROOT / "compact_label_v1.sqlite"
COMPACT_LABEL_V1_REPORT_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "reports" / "compact_label_v1_build_report.json"
COMPACT_LABEL_V1_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "compact_label_v1_qa.json"
DENSE_BGE_M3_INDEX_DIR = PROJECT_ROOT / "outputs" / "knowledge_engine" / "indexes" / "dense_bge_m3"
DENSE_BGE_M3_REPORT_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "reports" / "dense_bge_m3_index_report.json"
DENSE_BGE_M3_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "dense_bge_m3_index_qa.json"
DENSE_BGE_M3_ROW_MAP_DIR = PROJECT_ROOT / "outputs" / "knowledge_engine" / "indexes" / "dense_bge_m3_row_map"
DENSE_BGE_M3_ROW_MAP_DB_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "indexes" / "dense_bge_m3_row_map" / "dense_row_map.sqlite"
DENSE_BGE_M3_ROW_MAP_REPORT_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "reports" / "dense_bge_m3_row_map_report.json"
DENSE_BGE_M3_ROW_MAP_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "dense_bge_m3_row_map_qa.json"
FAISS_BGE_M3_INDEX_DIR = _CURRENT_RETRIEVAL_ROOT / "faiss_bge_m3"
FAISS_BGE_M3_SHARDS_DIR = _CURRENT_RETRIEVAL_ROOT / "faiss_bge_m3_shards_v1"
FAISS_BGE_M3_REPORT_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "reports" / "faiss_bge_m3_index_report.json"
FAISS_BGE_M3_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "faiss_bge_m3_index_qa.json"
FAISS_BGE_M3_SHARDS_REPORT_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "reports" / "faiss_bge_m3_shards_v1_report.json"
FAISS_BGE_M3_SHARDS_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "faiss_bge_m3_shards_v1_qa.json"

RETRIEVAL_BENCHMARK_CASES_PATH = RETRIEVAL_BENCHMARK_CASES_DIR / "theraskill_retrieval_benchmark_cases.jsonl"
RETRIEVAL_BENCHMARK_MANIFEST_PATH = RETRIEVAL_BENCHMARK_MANIFESTS_DIR / "retrieval_benchmark_manifest.json"
RETRIEVAL_BENCHMARK_CASE_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_case_qa.json"
RETRIEVAL_QUERY_PLAN_V3_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_query_plan_v3.json"

ADVANCED_RETRIEVAL_V2_STATUS_JSON_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "advanced_retrieval_v2_status_report.json"
ADVANCED_RETRIEVAL_V2_STATUS_MD_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "advanced_retrieval_v2_status_report.md"
ADVANCED_RETRIEVAL_V2_FINALIZE_MANIFEST_PATH = RETRIEVAL_BENCHMARK_MANIFESTS_DIR / "advanced_retrieval_v2_finalize_manifest.json"
ADVANCED_RETRIEVAL_V2_FINALIZE_LOG_PATH = ADVANCED_RETRIEVAL_V2_LOG_DIR / "finalize_advanced_retrieval_v2.log"
ADVANCED_RETRIEVAL_V2_RELEASE_SUMMARY_MANIFEST_PATH = RETRIEVAL_BENCHMARK_MANIFESTS_DIR / "advanced_retrieval_v2_release_summary.json"
ADVANCED_RETRIEVAL_V2_RELEASE_SUMMARY_MD_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "advanced_retrieval_v2_release_summary.md"
ADVANCED_RETRIEVAL_V2_BUNDLE_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "advanced_retrieval_v2_bundle.json"

FORMAL_HYBRID_REPORT_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v1_v2_hybrid_formal_report.json"
FORMAL_HYBRID_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v1_v2_hybrid_formal_qa.json"
FORMAL_HYBRID_SUMMARY_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v1_v2_hybrid_formal_summary.md"
FORMAL_HYBRID_AUDIT_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v1_v2_hybrid_formal_gate_audit.json"
FORMAL_HYBRID_AUDIT_SUMMARY_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v1_v2_hybrid_formal_gate_audit.md"
FORMAL_HYBRID_WAIT_AND_AUDIT_LOG_PATH = ADVANCED_RETRIEVAL_V2_LOG_DIR / "wait_and_audit_formal_hybrid_benchmark.log"
RETRIEVAL_BENCHMARK_V1_V2_REPORT_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v1_vs_v2_report.json"
RETRIEVAL_BENCHMARK_V1_V2_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v1_vs_v2_qa.json"
RETRIEVAL_BENCHMARK_V1_V2_SUMMARY_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v1_vs_v2_summary.md"
HYBRID_RETRIEVAL_V2_BUNDLE_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "hybrid_retrieval_v2_bundle.json"
RETRIEVAL_BENCHMARK_V1_V2_HYBRID_REPORT_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v1_v2_hybrid_report.json"
RETRIEVAL_BENCHMARK_V1_V2_HYBRID_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v1_v2_hybrid_qa.json"
RETRIEVAL_BENCHMARK_V1_V2_HYBRID_SUMMARY_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v1_v2_hybrid_summary.md"
RETRIEVAL_BENCHMARK_V3_REPORT_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v3_report.json"
RETRIEVAL_BENCHMARK_V3_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v3_qa.json"
HYBRID_RETRIEVAL_V3_BUNDLE_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "hybrid_retrieval_v3_bundle.json"
RETRIEVAL_BENCHMARK_V3_COMPARISON_REPORT_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v3_comparison_report.json"
RETRIEVAL_BENCHMARK_V3_COMPARISON_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v3_comparison_qa.json"
ADVANCED_RETRIEVAL_V3_COMPLETION_AUDIT_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "advanced_retrieval_v3_completion_audit.json"
ADVANCED_RETRIEVAL_V3_COMPLETION_AUDIT_SUMMARY_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "advanced_retrieval_v3_completion_audit.md"
RETRIEVAL_BENCHMARK_V3_RUNTIME_CANDIDATE_FORMAL_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v3_runtime_candidate_formal_warm_qa.json"
RETRIEVAL_BENCHMARK_V3_RUNTIME_CANDIDATE_FORMAL_REPORT_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v3_runtime_candidate_formal_warm_report.json"
RETRIEVAL_BENCHMARK_V3_MULTILINGUAL18_GATE_QA_PATH = RETRIEVAL_BENCHMARK_QA_DIR / "retrieval_benchmark_v3_multilingual18_gate_qa.json"
RETRIEVAL_BENCHMARK_V3_MULTILINGUAL18_REPORT_PATH = RETRIEVAL_BENCHMARK_REPORTS_DIR / "retrieval_benchmark_v3_multilingual18_report.json"

V3_COMPARISON_CACHE_DIR = RETRIEVAL_BENCHMARK_CACHE_DIR / "v3_comparison"
V3_EMBEDDING_CACHE_DB = RETRIEVAL_BENCHMARK_CACHE_DIR / "v3_embedding_cache.sqlite"
V3_SCOPE_COUNT_CACHE_DB = RETRIEVAL_BENCHMARK_CACHE_DIR / "v3_scope_count_cache.sqlite"
V3_RUNTIME_CACHE_DIR = RETRIEVAL_BENCHMARK_CACHE_DIR / "v3_runtime_candidate_formal"
V3_RUNTIME_REPORT_DIR = PROJECT_ROOT / "work" / "runtime_retrieval" / "v3"
