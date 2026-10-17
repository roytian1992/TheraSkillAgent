#!/usr/bin/env python3
"""Shared surface registry for Hermes authoritative, compatibility, review, and legacy surfaces."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONDA_PYTHON = sys.executable
HERMES_ROOT = Path(__file__).resolve().parents[2]
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
HERMES_CONFIG = HERMES_HOME / "config.yaml"

OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "hermes_skills"
REPORTS_DIR = OUTPUT_ROOT / "reports"
REVIEWS_DIR = OUTPUT_ROOT / "reviews"
PILOT_DIR = OUTPUT_ROOT / "pilot"
EVALUATION_DIR = OUTPUT_ROOT / "evaluation"
HERMES_SKILL_QA_PATH = OUTPUT_ROOT / "hermes_skill_qa.json"
PHASE6_SQLITE_DB_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "indexes" / "sqlite" / "phase6_retrieval_fts.sqlite"
PHASE6_STRUCTURED_INDEX_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "indexes" / "structured" / "phase6_structured_index.jsonl"
PHASE6_RETRIEVAL_TEXT_VIEW_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "indexes" / "retrieval_text_view" / "phase6_retrieval_text_view.jsonl"
PHASE6_REVIEWED_RELEASE_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "phase6_release" / "units" / "phase6_reviewed_knowledge_units.jsonl"
PHASE6_REVIEWED_RELEASE_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "phase6_release" / "manifests" / "phase6_reviewed_knowledge_release_qa.json"
KNOWLEDGE_ENGINE_RETRIEVAL_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "retrieval_index_qa.json"
KNOWLEDGE_ENGINE_SQLITE_QA_PATH = PROJECT_ROOT / "outputs" / "knowledge_engine" / "manifests" / "sqlite_retrieval_index_qa.json"
REGRESSION_DIR = PROJECT_ROOT / "outputs" / "hermes_regression"
PRODUCTION_REGRESSION_MANIFEST_PATH = REGRESSION_DIR / "hermes_production_regression_manifest.json"
PRODUCTION_REGRESSION_QA_PATH = REGRESSION_DIR / "hermes_production_regression_qa.json"
PRODUCTION_REGRESSION_SUMMARY_PATH = REGRESSION_DIR / "hermes_production_regression_summary.md"

SKILLS = (
    "emotional-support",
    "risk-triage",
    "cognitive-reframing-support",
    "synthetic-dialogue-builder",
    "therapy-response-reviewer",
    "rebt-belief-work-support",
)

# Historical H3 runtime evidence kept for lineage/regression only.
H3_HISTORICAL_RUNTIME_SMOKE_REPORT_PATH = REPORTS_DIR / "hermes_runtime_smoke_report.json"
H3_HISTORICAL_RUNTIME_SMOKE_QA_PATH = REPORTS_DIR / "hermes_runtime_smoke_qa.json"

H6_REVIEW_MD_PATH = REVIEWS_DIR / "human_review_round1.md"
H6_DECISIONS_PATH = REVIEWS_DIR / "human_review_round1_decisions.json"
H6_DECISIONS_QA_PATH = REVIEWS_DIR / "human_review_round1_decisions_qa.json"
H6_NEXT_ACTIONS_JSON_PATH = REVIEWS_DIR / "human_review_next_actions.json"
H6_NEXT_ACTIONS_MD_PATH = REVIEWS_DIR / "human_review_next_actions.md"
H6_PACKET_DIR = REVIEWS_DIR / "skill_packets"
H6_PACKET_INDEX_PATH = REVIEWS_DIR / "skill_review_packets_index.json"

# Current H6 engineering runtime evidence surface.
H6_ENGINEERING_RUNTIME_PILOT_CASES_PATH = PILOT_DIR / "hermes_runtime_pilot_cases.jsonl"
H6_ENGINEERING_RUNTIME_PILOT_MANIFEST_PATH = PILOT_DIR / "hermes_runtime_pilot_cases_manifest.json"
H6_ENGINEERING_RUNTIME_PILOT_REPORT_PATH = PILOT_DIR / "hermes_runtime_pilot_report.json"
H6_ENGINEERING_RUNTIME_PILOT_QA_PATH = PILOT_DIR / "hermes_runtime_pilot_qa.json"

H6_COMPLETION_REPORT_PATH = REPORTS_DIR / "h6_completion_report.md"
H6_STATUS_APPLY_REPORT_PATH = REPORTS_DIR / "h6_runtime_status_apply_report.json"

HERMES_E2E_EVAL_SET_PATH = EVALUATION_DIR / "hermes_e2e_eval_set.jsonl"
HERMES_E2E_EVAL_SET_MANIFEST_PATH = EVALUATION_DIR / "hermes_e2e_eval_set_manifest.json"
HERMES_E2E_EVAL_REPORT_PATH = EVALUATION_DIR / "hermes_e2e_eval_report.json"
HERMES_E2E_EVAL_QA_PATH = EVALUATION_DIR / "hermes_e2e_eval_qa.json"
HERMES_LLM_RESPONSE_EVAL_REPORT_PATH = EVALUATION_DIR / "hermes_llm_response_eval_report.json"
HERMES_LLM_RESPONSE_EVAL_QA_PATH = EVALUATION_DIR / "hermes_llm_response_eval_qa.json"
HUMAN_REVIEW_CHECKLIST_PATH = EVALUATION_DIR / "human_review_checklist.md"
RUNTIME_EVIDENCE_BUNDLE_PATH = REPORTS_DIR / "runtime_evidence_bundle.json"
H5_E2E_QA_PATH = EVALUATION_DIR / "hermes_e2e_eval_qa.json"
H5_LLM_QA_PATH = EVALUATION_DIR / "hermes_llm_response_eval_qa.json"

RETRIEVE_V1_WRAPPER_RELPATH = Path("scripts/AgentSkill/retrieve_theraskill_knowledge.py")
RETRIEVE_V2_WRAPPER_RELPATH = Path("scripts/AgentSkill/retrieve_theraskill_knowledge_v2.py")
RETRIEVE_V2_HYBRID_WRAPPER_RELPATH = Path("scripts/AgentSkill/retrieve_theraskill_knowledge_v2_hybrid.py")
RETRIEVE_V3_WRAPPER_RELPATH = Path("scripts/AgentSkill/retrieve_theraskill_knowledge_v3.py")
HERMES_SURFACE_REGISTRY_RELPATH = Path("scripts/AgentSkill/hermes_surface_registry.py")
RETRIEVAL_WRAPPER_COMMON_RELPATH = Path("scripts/AgentSkill/retrieval_wrapper_common.py")
RUNTIME_VALIDATION_PATHS_RELPATH = Path("scripts/AgentSkill/runtime_validation_paths.py")
COMPILE_HERMES_SKILLS_RELPATH = Path("scripts/AgentSkill/compile_hermes_skills.py")
BUILD_HERMES_QWEN_RUNTIME_VALIDATION_CASES_RELPATH = Path("scripts/AgentSkill/build_hermes_qwen_runtime_validation_cases.py")
RUN_HERMES_QWEN_RUNTIME_VALIDATION_RELPATH = Path("scripts/AgentSkill/run_hermes_qwen_runtime_validation.py")
RUN_HERMES_DEEPSEEK_RUNTIME_VALIDATION_RELPATH = Path("scripts/AgentSkill/run_hermes_deepseek_runtime_validation.py")
BUILD_HERMES_PRODUCTION_REGRESSION_MANIFEST_RELPATH = Path("scripts/AgentSkill/build_hermes_production_regression_manifest.py")
BUILD_HERMES_E2E_EVAL_SET_RELPATH = Path("scripts/AgentSkill/build_hermes_e2e_eval_set.py")
RUN_HERMES_E2E_EVAL_RELPATH = Path("scripts/AgentSkill/run_hermes_e2e_eval.py")
RUN_HERMES_LLM_RESPONSE_EVALUATOR_RELPATH = Path("scripts/AgentSkill/run_hermes_llm_response_evaluator.py")
BUILD_HUMAN_REVIEW_CHECKLIST_RELPATH = Path("scripts/AgentSkill/build_human_review_checklist.py")
AUDIT_HERMES_RUNTIME_CASES_RELPATH = Path("scripts/AgentSkill/audit_hermes_runtime_cases.py")
BUILD_H6_REVIEW_AND_PILOT_INPUTS_RELPATH = Path("scripts/AgentSkill/build_h6_review_and_pilot_inputs.py")
LEGACY_RUN_HERMES_RUNTIME_SMOKE_RELPATH = Path("scripts/AgentSkill/run_hermes_runtime_smoke.py")
LEGACY_RUN_H6_RUNTIME_PILOT_RELPATH = Path("scripts/AgentSkill/run_h6_runtime_pilot.py")
BUILD_H6_SKILL_REVIEW_PACKETS_RELPATH = Path("scripts/AgentSkill/build_h6_skill_review_packets.py")
BUILD_H6_COMPLETION_REPORT_RELPATH = Path("scripts/AgentSkill/build_h6_completion_report.py")
APPLY_H6_RUNTIME_STATUS_RELPATH = Path("scripts/AgentSkill/apply_h6_runtime_status.py")
WAIT_AND_RUN_HERMES_V2_HYBRID_RUNTIME_VALIDATION_RELPATH = (
    Path("scripts/AgentSkill/wait_and_run_hermes_v2_hybrid_runtime_validation.py")
)

AUTHORITATIVE_RUNTIME_VALIDATION_RELPATHS = (
    COMPILE_HERMES_SKILLS_RELPATH,
    RETRIEVE_V3_WRAPPER_RELPATH,
    BUILD_HERMES_QWEN_RUNTIME_VALIDATION_CASES_RELPATH,
    RUN_HERMES_QWEN_RUNTIME_VALIDATION_RELPATH,
    RUN_HERMES_DEEPSEEK_RUNTIME_VALIDATION_RELPATH,
)

AUTHORITATIVE_EVALUATION_RELPATHS = (
    BUILD_HERMES_E2E_EVAL_SET_RELPATH,
    RUN_HERMES_E2E_EVAL_RELPATH,
    RUN_HERMES_LLM_RESPONSE_EVALUATOR_RELPATH,
    BUILD_HUMAN_REVIEW_CHECKLIST_RELPATH,
    BUILD_HERMES_PRODUCTION_REGRESSION_MANIFEST_RELPATH,
)

HERMES_SHARED_SUPPORT_RELPATHS = (
    HERMES_SURFACE_REGISTRY_RELPATH,
    RETRIEVAL_WRAPPER_COMMON_RELPATH,
    RUNTIME_VALIDATION_PATHS_RELPATH,
)

COMPATIBILITY_WRAPPER_RELPATHS = (
    RETRIEVE_V1_WRAPPER_RELPATH,
    RETRIEVE_V2_WRAPPER_RELPATH,
    RETRIEVE_V2_HYBRID_WRAPPER_RELPATH,
)

COMPATIBILITY_VALIDATION_SUPPORT_RELPATHS = (
    AUDIT_HERMES_RUNTIME_CASES_RELPATH,
)

H6_REVIEW_WORKFLOW_RELPATHS = (
    BUILD_H6_REVIEW_AND_PILOT_INPUTS_RELPATH,
    BUILD_H6_SKILL_REVIEW_PACKETS_RELPATH,
    BUILD_H6_COMPLETION_REPORT_RELPATH,
    APPLY_H6_RUNTIME_STATUS_RELPATH,
)

LEGACY_HERMES_HELPER_RELPATHS = (
    LEGACY_RUN_HERMES_RUNTIME_SMOKE_RELPATH,
    LEGACY_RUN_H6_RUNTIME_PILOT_RELPATH,
    WAIT_AND_RUN_HERMES_V2_HYBRID_RUNTIME_VALIDATION_RELPATH,
)

CURRENT_AUTHORITATIVE_CODE_SURFACE_RELPATHS = (
    *AUTHORITATIVE_RUNTIME_VALIDATION_RELPATHS,
    *AUTHORITATIVE_EVALUATION_RELPATHS,
    *HERMES_SHARED_SUPPORT_RELPATHS,
)

NON_AUTHORITATIVE_STRUCTURED_SURFACE_RELPATHS = (
    *COMPATIBILITY_WRAPPER_RELPATHS,
    *COMPATIBILITY_VALIDATION_SUPPORT_RELPATHS,
    *H6_REVIEW_WORKFLOW_RELPATHS,
    *LEGACY_HERMES_HELPER_RELPATHS,
)

AUTHORITATIVE_HERMES_SURFACE = AUTHORITATIVE_RUNTIME_VALIDATION_RELPATHS
COMPATIBILITY_HERMES_SURFACE = COMPATIBILITY_WRAPPER_RELPATHS
H6_REVIEW_WORKFLOW_SURFACE = H6_REVIEW_WORKFLOW_RELPATHS
LEGACY_HERMES_HELPER_SURFACE = LEGACY_HERMES_HELPER_RELPATHS

AUTHORITATIVE_RUNTIME_VALIDATION = tuple(path.name for path in AUTHORITATIVE_RUNTIME_VALIDATION_RELPATHS)
AUTHORITATIVE_EVALUATION = tuple(path.name for path in AUTHORITATIVE_EVALUATION_RELPATHS)
HERMES_SHARED_SUPPORT = tuple(path.name for path in HERMES_SHARED_SUPPORT_RELPATHS)
COMPATIBILITY_WRAPPERS = tuple(path.name for path in COMPATIBILITY_WRAPPER_RELPATHS)
COMPATIBILITY_VALIDATION_SUPPORT = tuple(path.name for path in COMPATIBILITY_VALIDATION_SUPPORT_RELPATHS)
H6_REVIEW_WORKFLOW = tuple(path.name for path in H6_REVIEW_WORKFLOW_RELPATHS)
LEGACY_HERMES_HELPERS = tuple(path.name for path in LEGACY_HERMES_HELPER_RELPATHS)
CURRENT_AUTHORITATIVE_CODE_SURFACE = tuple(path.name for path in CURRENT_AUTHORITATIVE_CODE_SURFACE_RELPATHS)
NON_AUTHORITATIVE_STRUCTURED_SURFACE = tuple(path.name for path in NON_AUTHORITATIVE_STRUCTURED_SURFACE_RELPATHS)

# Backward-compatible aliases kept so historical callers and older docs keep resolving.
RUNTIME_SMOKE_REPORT_PATH = H3_HISTORICAL_RUNTIME_SMOKE_REPORT_PATH
RUNTIME_SMOKE_QA_PATH = H3_HISTORICAL_RUNTIME_SMOKE_QA_PATH
H6_PILOT_CASES_PATH = H6_ENGINEERING_RUNTIME_PILOT_CASES_PATH
H6_PILOT_MANIFEST_PATH = H6_ENGINEERING_RUNTIME_PILOT_MANIFEST_PATH
H6_PILOT_REPORT_PATH = H6_ENGINEERING_RUNTIME_PILOT_REPORT_PATH
H6_PILOT_QA_PATH = H6_ENGINEERING_RUNTIME_PILOT_QA_PATH
RUN_HERMES_RUNTIME_SMOKE_RELPATH = LEGACY_RUN_HERMES_RUNTIME_SMOKE_RELPATH
RUN_H6_RUNTIME_PILOT_RELPATH = LEGACY_RUN_H6_RUNTIME_PILOT_RELPATH
