#!/usr/bin/env python3
"""Shared lineage surface registry for Anna-CPsyCounD patient extraction scripts."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = Path("datasets/Anna-CPsyCounD")
SOURCE_FILE = "CPsyCounS-3134.json"
README_FILE = "README.md"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "AnnaCPsyCounD" / "patient_profile_extraction_zh.md"

RUN_ROOT = PROJECT_ROOT / "work" / "runs" / "anna_cpsycound_patient_extraction_20260708"
SHARD_DIR = RUN_ROOT / "shards"
STATUS_OUTPUT_PATH = RUN_ROOT / "current_projection_status.json"
CURRENT_MD_OUTPUT_PATH = RUN_ROOT / "CURRENT.md"
RUNBOOK_PATH = RUN_ROOT / "RUNBOOK.md"

PATIENT_PERSONA_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.patient_persona_profile.jsonl"
HIDDEN_PROFILE_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.hidden_patient_profile_pattern.jsonl"
HISTORICAL_MEMORY_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.historical_case_memory_seed.jsonl"
REPORT_BRIDGE_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.report_to_conversation_bridge_pattern.jsonl"
FAILURE_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "manifests" / "Anna-CPsyCounD.persona_projection_failures.jsonl"
MANIFEST_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "manifests" / "Anna-CPsyCounD.persona_projection_manifest.json"
QA_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "manifests" / "Anna-CPsyCounD.persona_projection_qa.json"

OUTPUT_PATHS = {
    "patient_persona_profile": PATIENT_PERSONA_OUTPUT_PATH,
    "hidden_patient_profile_pattern": HIDDEN_PROFILE_OUTPUT_PATH,
    "historical_case_memory_seed": HISTORICAL_MEMORY_OUTPUT_PATH,
    "report_to_conversation_bridge_pattern": REPORT_BRIDGE_OUTPUT_PATH,
}
