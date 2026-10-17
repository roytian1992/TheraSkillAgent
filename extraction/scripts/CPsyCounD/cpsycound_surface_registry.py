#!/usr/bin/env python3
"""Shared legacy surface registry for CPsyCounD staged, repair, and case-knowledge scripts."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_WORK_ROOT = PROJECT_ROOT / "data_work"
V2_PILOT_ROOT = DATA_WORK_ROOT / "v2_pilot"

UNIFIED_DIALOGUES_PATH = DATA_WORK_ROOT / "unified" / "counseling_dialogues.jsonl"

SKILL_EXTRACTION_PILOT_ROOT = V2_PILOT_ROOT / "skill_extraction_pilot_20260701"
SKILL_EXTRACTION_PILOT_WINDOWS_PATH = SKILL_EXTRACTION_PILOT_ROOT / "pilot_evidence_windows.jsonl"
SKILL_EXTRACTION_PILOT_CASES_PATH = SKILL_EXTRACTION_PILOT_ROOT / "pilot_cases.jsonl"
SKILL_EXTRACTION_STAGED_SMOKE_ROOT = SKILL_EXTRACTION_PILOT_ROOT / "stage_outputs" / "staged_smoke_8002_20260701"

CPSYCOUND_FULL_ROOT = V2_PILOT_ROOT / "cpsycound_full_20260701"
CPSYCOUND_CASES_PATH = CPSYCOUND_FULL_ROOT / "cpsycound_cases.jsonl"
CPSYCOUND_WINDOWS_PATH = CPSYCOUND_FULL_ROOT / "cpsycound_evidence_windows.jsonl"
CPSYCOUND_SHARDS_DIR = CPSYCOUND_FULL_ROOT / "shards"
CPSYCOUND_STAGE_OUTPUT_ROOT = CPSYCOUND_FULL_ROOT / "stage_outputs" / "current_boundary_full_8002_20260701"
CPSYCOUND_STAGE_OUTPUT_LOGS_DIR = CPSYCOUND_STAGE_OUTPUT_ROOT / "logs"

CPSYCOUND_TIMELINE_INPUT_ROOT = CPSYCOUND_SHARDS_DIR
TIMELINE_REPAIR_RUN_ROOT = PROJECT_ROOT / "work" / "runs" / "cpsycound_staged_extraction"
STATE_REPAIR_RUN_ROOT = PROJECT_ROOT / "work" / "runs" / "cpsycound_state_repair"
STATE_REPAIR_DEDUP_PATH = STATE_REPAIR_RUN_ROOT / "state_before_move_repaired_all_dedup.jsonl"
STATE_REPAIR_PROJECTION_PATH = STATE_REPAIR_RUN_ROOT / "pass_window_prior_projection_all.jsonl"
TIMELINE_REPAIRED_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "current"

CASE_KNOWLEDGE_RUN_ROOT = PROJECT_ROOT / "work" / "runs" / "cpsycound_case_knowledge_full_20260704"
CASE_KNOWLEDGE_SHARDS_DIR = CASE_KNOWLEDGE_RUN_ROOT / "shards"
CASE_KNOWLEDGE_INTERACTION_UNITS_PATH = PROJECT_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.interaction_pattern.jsonl"

CASE_KNOWLEDGE_STAGE_OUTPUTS = {
    "case_trajectory": PROJECT_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.case_trajectory.jsonl",
    "case_formulation_pattern": PROJECT_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.case_formulation_pattern.jsonl",
    "action_sequence_pattern": PROJECT_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.action_sequence_pattern.jsonl",
}

CASE_KNOWLEDGE_STAGE_FAILURE_OUTPUTS = {
    stage: path.with_suffix('.failures.jsonl')
    for stage, path in CASE_KNOWLEDGE_STAGE_OUTPUTS.items()
}

CASE_KNOWLEDGE_PID_FILES = {
    "case_trajectory": CASE_KNOWLEDGE_RUN_ROOT / "case_trajectory.full.pid",
    "pipeline": CASE_KNOWLEDGE_RUN_ROOT / "pipeline.full.pid",
    "sharded_pipeline": CASE_KNOWLEDGE_RUN_ROOT / "sharded_pipeline.full.pid",
}
