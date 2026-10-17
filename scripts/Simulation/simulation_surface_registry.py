#!/usr/bin/env python3
"""Shared surface registry for Simulation authoritative, legacy, and finalize layers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SIMULATION_OUTPUT_ROOT = REPO_ROOT / "outputs" / "simulation"
SIMULATION_RUNTIME_ROOT = SIMULATION_OUTPUT_ROOT / "runtime"
SIMULATION_PROTOTYPE_ROOT = SIMULATION_OUTPUT_ROOT / "prototype"
SIMULATION_HISTORICAL_EVIDENCE_ROOT = SIMULATION_OUTPUT_ROOT / "historical_evidence"
SIMULATION_RUNTIME_INPUT_ROOT = SIMULATION_OUTPUT_ROOT / "runtime_inputs"
SIMULATION_RUNTIME_PATIENT_CASES_DIR = SIMULATION_RUNTIME_INPUT_ROOT / "patient_cases"
SIMULATION_PERSONA_ROOT = SIMULATION_OUTPUT_ROOT / "personas"
SIMULATION_PERSONA_V2_DIR = SIMULATION_PERSONA_ROOT / "v2"
SIMULATION_PERSONA_V2_INDEX_PATH = SIMULATION_PERSONA_ROOT / "patient_persona_v2.index.jsonl"
SIMULATION_PERSONA_V2_MANIFEST_PATH = SIMULATION_PERSONA_ROOT / "patient_persona_v2.manifest.json"
SIMULATION_PERSONA_V2_QA_PATH = SIMULATION_PERSONA_ROOT / "patient_persona_v2.qa.json"
SIMULATION_PERSONA_V2_SUMMARY_PATH = SIMULATION_PERSONA_ROOT / "patient_persona_v2.summary.json"
SIMULATION_PERSONA_ENRICHMENT_ROOT = SIMULATION_PERSONA_ROOT / "enrichment"
SIMULATION_PERSONA_ENRICHMENT_OUTPUT_PATH = SIMULATION_PERSONA_ENRICHMENT_ROOT / "patient_persona_v2.enrichment.verified.jsonl"
SIMULATION_PERSONA_ENRICHMENT_FAILURE_PATH = SIMULATION_PERSONA_ENRICHMENT_ROOT / "patient_persona_v2.enrichment.failures.jsonl"
SIMULATION_PERSONA_ENRICHMENT_MANIFEST_PATH = SIMULATION_PERSONA_ENRICHMENT_ROOT / "patient_persona_v2.enrichment.manifest.json"
SIMULATION_PERSONA_ENRICHMENT_QA_PATH = SIMULATION_PERSONA_ENRICHMENT_ROOT / "patient_persona_v2.enrichment.qa.json"
SIMULATION_BENCHMARK_ROOT = SIMULATION_OUTPUT_ROOT / "benchmark"
SIMULATION_SCENARIO_ROOT = SIMULATION_OUTPUT_ROOT / "scenarios"
SIMULATION_REGISTRY_ROOT = SIMULATION_OUTPUT_ROOT / "registries"
SIMULATION_MANIFEST_ROOT = SIMULATION_OUTPUT_ROOT / "manifests"
SIMULATION_HERMES_VALIDATION_ROOT = SIMULATION_OUTPUT_ROOT / "hermes_validation"
SIMULATION_HERMES_ADAPTER_ROOT = SIMULATION_OUTPUT_ROOT / "hermes_adapter"
SIMULATION_THERAPIST_HERMES_ADAPTER_ROOT = SIMULATION_HERMES_ADAPTER_ROOT / "therapist_simulator"
SIMULATION_DUAL_SESSION_HERMES_ADAPTER_ROOT = SIMULATION_HERMES_ADAPTER_ROOT / "dual_session_simulator"
SIMULATION_REVIEWER_HERMES_ADAPTER_ROOT = SIMULATION_HERMES_ADAPTER_ROOT / "simulation_reviewer"
SIMULATION_SCHEMA_ROOT = SIMULATION_OUTPUT_ROOT / "schemas"
SIMULATION_SOURCE_SCHEMA_ROOT = REPO_ROOT / "schemas" / "simulation"
SIMULATION_PATIENT_PERSONA_V2_SCHEMA_PATH = SIMULATION_SOURCE_SCHEMA_ROOT / "patient_persona_v2.schema.json"
SIMULATION_DISCLOSURE_NODE_V1_SCHEMA_PATH = SIMULATION_SOURCE_SCHEMA_ROOT / "disclosure_node_v1.schema.json"
SIMULATION_PERSONA_V2_SCHEMA_MANIFEST_PATH = SIMULATION_SOURCE_SCHEMA_ROOT / "patient_persona_v2_schemas.manifest.json"
SIMULATION_PERSONA_V2_SCHEMA_QA_PATH = SIMULATION_SOURCE_SCHEMA_ROOT / "patient_persona_v2_schemas.qa.json"

PATIENT_PERSONA_ENRICHMENT_PROMPT_PATH = REPO_ROOT / "prompts" / "Simulation" / "patient_persona_enrichment_zh.md"
PATIENT_PERSONA_ENRICHMENT_VERIFY_PROMPT_PATH = REPO_ROOT / "prompts" / "Simulation" / "patient_persona_enrichment_verify_zh.md"
SIMULATION_PROMPTS_DIR = REPO_ROOT / "prompts" / "Simulation"
SIMULATION_RUNTIME_SCHEMA_OUTPUT_DIR = SIMULATION_SCHEMA_ROOT
SIMULATION_RUNTIME_SCHEMA_MANIFEST_PATH = SIMULATION_SCHEMA_ROOT / "patient_runtime_schemas_manifest.json"
SIMULATION_RUNTIME_SCHEMA_QA_PATH = SIMULATION_SCHEMA_ROOT / "patient_runtime_schemas_qa.json"

SIMULATION_SCENARIO_REGISTRY_PATH = SIMULATION_SCENARIO_ROOT / "scenario_registry.jsonl"
SIMULATION_PATIENT_CASE_REGISTRY_PATH = SIMULATION_SCENARIO_ROOT / "patient_case_registry.jsonl"
SIMULATION_PATIENT_CASE_REGISTRY_SUMMARY_PATH = SIMULATION_SCENARIO_ROOT / "patient_case_registry_summary.json"
SIMULATION_PATIENT_CASE_REGISTRY_MANIFEST_PATH = SIMULATION_SCENARIO_ROOT / "patient_case_registry_manifest.json"
SIMULATION_PATIENT_CASE_REGISTRY_QA_PATH = SIMULATION_SCENARIO_ROOT / "patient_case_registry_qa.json"
SIMULATION_PATIENT_PRESET_REGISTRY_PATH = SIMULATION_SCENARIO_ROOT / "patient_preset_registry.jsonl"
SIMULATION_THERAPIST_TASK_REGISTRY_PATH = SIMULATION_SCENARIO_ROOT / "therapist_task_registry.jsonl"
SIMULATION_HIDDEN_PROFILE_REVEAL_REGISTRY_PATH = SIMULATION_SCENARIO_ROOT / "hidden_profile_reveal_registry.jsonl"
SIMULATION_SCENARIO_SPLIT_MANIFEST_PATH = SIMULATION_SCENARIO_ROOT / "scenario_split_manifest.json"
SIMULATION_EVALUATOR_SCHEMA_PATH = SIMULATION_SCENARIO_ROOT / "evaluator_io_schema.json"
SIMULATION_SCENARIO_HARNESS_MANIFEST_PATH = SIMULATION_SCENARIO_ROOT / "scenario_harness_manifest.json"
SIMULATION_SCENARIO_HARNESS_QA_PATH = SIMULATION_SCENARIO_ROOT / "scenario_harness_qa.json"
SIMULATION_PATIENT_CASE_BUNDLES_MANIFEST_PATH = SIMULATION_RUNTIME_INPUT_ROOT / "patient_case_bundles_manifest.json"
SIMULATION_PATIENT_CASE_BUNDLES_QA_PATH = SIMULATION_RUNTIME_INPUT_ROOT / "patient_case_bundles_qa.json"
SIMULATION_PATIENT_CASE_BUNDLES_SUMMARY_PATH = SIMULATION_RUNTIME_INPUT_ROOT / "patient_case_bundles_summary.json"

DEFAULT_LONGITUDINAL_VALIDATION_OUTPUT_DIR = SIMULATION_BENCHMARK_ROOT / "patient_longitudinal_validation_default"

DEFAULT_SCENARIO_BENCHMARK_OUTPUT_DIR = SIMULATION_BENCHMARK_ROOT / "patient_scenario_benchmark"
DEFAULT_CONDITIONED_EVAL_OUTPUT_DIR = SIMULATION_BENCHMARK_ROOT / "patient_therapist_conditioned_eval"
DEFAULT_PATIENT_SIMULATOR_EVAL_OUTPUT_DIR = SIMULATION_BENCHMARK_ROOT / "patient_simulator_paper_grade_eval_v1"

FORMAL_SCENARIO_BENCHMARK_EVIDENCE_DIR = (
    SIMULATION_BENCHMARK_ROOT / "patient_scenario_benchmark_formal_test_4fam_v5_casegrounded_16scen_allpass_8002"
)
FORMAL_CONDITIONED_EVAL_EVIDENCE_DIR = (
    SIMULATION_BENCHMARK_ROOT / "patient_therapist_conditioned_formal_test_4fam_v8_16scen_calfix3_8002"
)
FORMAL_CONDITIONED_EVAL_ADJUDICATION_DIR = FORMAL_CONDITIONED_EVAL_EVIDENCE_DIR / "adjudication"

DEFAULT_HERMES_VALIDATION_HOME_DIR = SIMULATION_HERMES_VALIDATION_ROOT / "hermes_home"
DEFAULT_HERMES_VALIDATION_REPORT_PATH = SIMULATION_HERMES_VALIDATION_ROOT / "hermes_patient_skill_validation.json"
DEFAULT_HERMES_HELDOUT_GATE_DIR = SIMULATION_HERMES_VALIDATION_ROOT / "hermes_patient_skill_heldout_gate_4fam_v1_8002"
DEFAULT_SIMULATION_SKILL_VALIDATION_OUTPUT_DIR = SIMULATION_HERMES_VALIDATION_ROOT / "simulation_skill_validation"
DEFAULT_SIMULATION_SKILL_DELIVERY_GATE_DIR = SIMULATION_HERMES_VALIDATION_ROOT / "simulation_skill_delivery_gate"
DEFAULT_HERMES_SIMULATION_SKILLS_VALIDATION_DIR = SIMULATION_HERMES_VALIDATION_ROOT / "hermes_simulation_skills_validation"
FORMAL_SIMULATION_SKILL_DELIVERY_GATE_EVIDENCE_DIR = (
    SIMULATION_HERMES_VALIDATION_ROOT / "simulation_skill_delivery_gate_5fam_v2_grounded_8002_20260711"
)
DEFAULT_HERMES_SIMULATION_VALIDATION_INPUT_REPORT = (
    FORMAL_SIMULATION_SKILL_DELIVERY_GATE_EVIDENCE_DIR
    / "cases"
    / "scenario__supportive_exploration__bd0850e0867f7b7e"
    / "simulation_skill_validation_report.json"
)
DEFAULT_HERMES_ADAPTER_RUNTIME_REPORT_PATH = (
    SIMULATION_HERMES_ADAPTER_ROOT / "runtime_reports" / "scenario__supportive_exploration__998a78b598dfc09a.json"
)
DEFAULT_HERMES_ADAPTER_REVIEW_DIGEST_PATH = (
    SIMULATION_HERMES_ADAPTER_ROOT / "review_digests" / "scenario__supportive_exploration__998a78b598dfc09a.json"
)

DEFAULT_PATIENT_STATE_MEMORY_UPDATE_PATH = SIMULATION_RUNTIME_ROOT / "patient_state_memory_update.json"
DEFAULT_THERAPIST_CASE_MEMORY_UPDATE_PATH = SIMULATION_RUNTIME_ROOT / "therapist_case_memory_update.json"
DEFAULT_PATIENT_TURN_GENERATION_PATH = SIMULATION_RUNTIME_ROOT / "patient_turn_generation.json"
DEFAULT_THERAPIST_TURN_GENERATION_PATH = SIMULATION_RUNTIME_ROOT / "therapist_turn_generation.json"
DEFAULT_PATIENT_SCENARIO_EXECUTION_PATH = SIMULATION_RUNTIME_ROOT / "patient_scenario_execution.json"
DEFAULT_PATIENT_RUNTIME_REVIEWER_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "patient_runtime_reviewer_report.json"
DEFAULT_SESSION_LONGITUDINAL_SUMMARY_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "session_longitudinal_summary.json"
DEFAULT_SESSION_REVIEWER_GATE_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "session_reviewer_gate.json"
DEFAULT_PATIENT_LONGITUDINAL_CARRYOVER_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "patient_longitudinal_carryover.json"
DEFAULT_PATIENT_MOVE_INTERPRETER_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "patient_move_interpreter.json"
DEFAULT_PATIENT_DISCLOSURE_CONTROLLER_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "patient_disclosure_controller.json"
DEFAULT_PATIENT_TURN_REALIZER_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "patient_turn_realizer.json"
DEFAULT_PATIENT_STATE_UPDATER_FROM_BUNDLE_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "patient_state_updater_from_bundle.json"
DEFAULT_PATIENT_SINGLE_SESSION_LOOP_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "patient_single_session_loop.json"
DEFAULT_PATIENT_SINGLE_SESSION_PROTOTYPE_OUTPUT_PATH = SIMULATION_PROTOTYPE_ROOT / "patient_single_session_prototype.json"
DEFAULT_PATIENT_MULTI_SESSION_PROTOTYPE_OUTPUT_PATH = SIMULATION_PROTOTYPE_ROOT / "patient_multi_session_prototype.json"
DEFAULT_SIMULATION_SESSION_PROTOTYPE_OUTPUT_PATH = SIMULATION_PROTOTYPE_ROOT / "simulation_session_prototype.json"
DEFAULT_MINIMAL_SIMULATION_TURN_ENGINE_OUTPUT_PATH = SIMULATION_RUNTIME_ROOT / "minimal_simulation_turn_engine.json"

PATIENT_RUNTIME_REVIEWER_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_runtime_reviewer_zh.md"
PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_session_runtime_reviewer_zh.md"
SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "session_longitudinal_summary_zh.md"
SESSION_REVIEWER_GATE_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "session_reviewer_gate_zh.md"
PATIENT_MOVE_INTERPRETATION_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "therapist_move_interpretation_zh.md"
PATIENT_DISCLOSURE_CONTROLLER_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_disclosure_controller_zh.md"
PATIENT_TURN_REALIZATION_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_turn_realization_zh.md"
PATIENT_STATE_UPDATER_FROM_BUNDLE_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_state_updater_from_bundle_zh.md"
PATIENT_STATE_MEMORY_WRITEBACK_FROM_BUNDLE_PROMPT_PATH = (
    SIMULATION_PROMPTS_DIR / "patient_state_memory_writeback_from_bundle_zh.md"
)
PATIENT_STATE_MEMORY_UPDATE_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_state_memory_update_zh.md"
THERAPIST_CASE_MEMORY_UPDATE_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "therapist_case_memory_update_zh.md"
PATIENT_TURN_GENERATION_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_turn_generation_zh.md"
THERAPIST_TURN_GENERATION_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "therapist_turn_generation_zh.md"
PATIENT_STATE_TRANSITION_EXTRACTION_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "patient_state_transition_extraction_zh.md"
THERAPIST_RESPONSE_POLICY_EXTRACTION_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "therapist_response_policy_extraction_zh.md"
THERAPIST_CASE_MEMORY_EXTRACTION_PROMPT_PATH = SIMULATION_PROMPTS_DIR / "therapist_case_memory_extraction_zh.md"
SIMULATION_PROTOCOL_PATTERN_ENGINEERING_PROMPT_PATH = (
    SIMULATION_PROMPTS_DIR / "simulation_protocol_pattern_engineering_zh.md"
)

SIMULATION_KNOWLEDGE_REGISTRY_PATH = SIMULATION_REGISTRY_ROOT / "simulation_knowledge_registry.jsonl"
SIMULATION_KNOWLEDGE_REGISTRY_SUMMARY_PATH = SIMULATION_REGISTRY_ROOT / "simulation_knowledge_registry_summary.json"
SIMULATION_KNOWLEDGE_REGISTRY_MANIFEST_PATH = SIMULATION_MANIFEST_ROOT / "simulation_knowledge_registry_manifest.json"
SIMULATION_KNOWLEDGE_REGISTRY_QA_PATH = SIMULATION_MANIFEST_ROOT / "simulation_knowledge_registry_qa.json"

SIMULATION_PERSONA_BUNDLE_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "PsyDTCorpus.persona_pattern_bundle.jsonl"
SIMULATION_MEMORY_BUNDLE_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "SoulChat-R1.memory_pattern_bundle.jsonl"
SIMULATION_ARCHETYPE_BUNDLE_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "MindEcho-Chat-26.archetype_bundle.jsonl"
SIMULATION_INTERACTION_PATTERN_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.interaction_pattern.jsonl"
SIMULATION_TRANSITION_PATTERN_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Simulation.patient_state_transition_pattern.jsonl"
SIMULATION_PATIENT_STATE_TRANSITION_PATTERN_PATH = SIMULATION_TRANSITION_PATTERN_PATH
SIMULATION_THERAPIST_RESPONSE_POLICY_PATTERN_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "units" / "Simulation.therapist_response_policy_pattern.jsonl"
)
SIMULATION_THERAPIST_CASE_MEMORY_PATTERN_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Simulation.therapist_case_memory_pattern.jsonl"
SIMULATION_PROTOCOL_PATTERN_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Simulation.simulation_protocol_pattern.jsonl"
SIMULATION_CASE_TRAJECTORY_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.case_trajectory.jsonl"
SIMULATION_CASE_FORMULATION_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.case_formulation_pattern.jsonl"
SIMULATION_ACTION_SEQUENCE_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "CPsyCounD.action_sequence_pattern.jsonl"
SIMULATION_ANNOMI_METHOD_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "AnnoMI.mi_method_step.jsonl"
SIMULATION_ANNOMI_RUBRIC_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "AnnoMI.mi_quality_rubric.jsonl"
SIMULATION_ANNOMI_CHANGE_TALK_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "AnnoMI.change_talk_pattern.jsonl"
SIMULATION_ESCONV_STRATEGY_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "ESConv.support_strategy.jsonl"
SIMULATION_ESCONV_SEQUENCE_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "ESConv.support_strategy_sequence.jsonl"
SIMULATION_REFRAMING_STEP_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Cognitive-Reframing.reframing_step.jsonl"
SIMULATION_THINKING_TRAP_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Cognitive-Reframing.thinking_trap_unit.jsonl"
SIMULATION_PSYQA_STRUCTURE_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "PsyQA.answer_structure_pattern.jsonl"

ANNA_PATIENT_PERSONA_PROFILE_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.patient_persona_profile.jsonl"
ANNA_HIDDEN_PATIENT_PROFILE_PATTERN_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.hidden_patient_profile_pattern.jsonl"
ANNA_HISTORICAL_CASE_MEMORY_SEED_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.historical_case_memory_seed.jsonl"
ANNA_REPORT_TO_CONVERSATION_BRIDGE_PATTERN_PATH = REPO_ROOT / "outputs" / "knowledge" / "units" / "Anna-CPsyCounD.report_to_conversation_bridge_pattern.jsonl"
DEFAULT_PATIENT_CASE_BUNDLE_PATH = SIMULATION_RUNTIME_PATIENT_CASES_DIR / "00235848-787b-4f64-a50b-05477a54c42c.json"

SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "candidates" / "Simulation.patient_state_transition_candidates.jsonl"
)
SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.patient_state_transition_candidates_manifest.json"
)
SIMULATION_PATIENT_STATE_TRANSITION_CANDIDATES_QA_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.patient_state_transition_candidates_qa.json"
)
SIMULATION_PATIENT_STATE_TRANSITION_PROJECTION_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.patient_state_transition_projection_manifest.json"
)
SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "candidates" / "Simulation.therapist_case_memory_candidates.jsonl"
)
SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.therapist_case_memory_candidates_manifest.json"
)
SIMULATION_THERAPIST_CASE_MEMORY_CANDIDATES_QA_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.therapist_case_memory_candidates_qa.json"
)
SIMULATION_THERAPIST_CASE_MEMORY_PROJECTION_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.therapist_case_memory_projection_manifest.json"
)
SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "candidates" / "Simulation.therapist_response_policy_candidates.jsonl"
)
SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.therapist_response_policy_candidates_manifest.json"
)
SIMULATION_THERAPIST_RESPONSE_POLICY_CANDIDATES_QA_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.therapist_response_policy_candidates_qa.json"
)
SIMULATION_THERAPIST_RESPONSE_POLICY_PROJECTION_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.therapist_response_policy_projection_manifest.json"
)
SIMULATION_PROTOCOL_CANDIDATES_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "candidates" / "Simulation.simulation_protocol_candidates.jsonl"
)
SIMULATION_PROTOCOL_CANDIDATES_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.simulation_protocol_candidates_manifest.json"
)
SIMULATION_PROTOCOL_CANDIDATES_QA_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.simulation_protocol_candidates_qa.json"
)
SIMULATION_PROTOCOL_PROJECTION_MANIFEST_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.simulation_protocol_projection_manifest.json"
)
SIMULATION_PROTOCOL_PROJECTION_QA_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "manifests" / "Simulation.simulation_protocol_projection_qa.json"
)
SIMULATION_PROTOCOL_CONTROLLED_LABEL_INDEX_PATH = (
    REPO_ROOT / "outputs" / "knowledge" / "indexes" / "Simulation.simulation_protocol_controlled_label_index.json"
)

LONGITUDINAL_EVIDENCE_SUMMARY_PATHS = {
    "supportive_exploration": SIMULATION_BENCHMARK_ROOT
    / "patient_longitudinal_validation_supportive_kin_v22_probe_3case_3turn_8002_fix1"
    / "validation_summary.json",
    "cognitive_reframing_probe": SIMULATION_BENCHMARK_ROOT
    / "patient_longitudinal_validation_cognitive_any_v24_probe_3case_3turn_8002_fix3"
    / "validation_summary.json",
    "rupture_and_repair": SIMULATION_BENCHMARK_ROOT
    / "patient_longitudinal_validation_rupture_any_v21_probe_3case_3turn_8002"
    / "validation_summary.json",
    "risk_escalation_and_handoff": SIMULATION_BENCHMARK_ROOT
    / "patient_longitudinal_validation_risk_any_v21_probe_3case_3turn_8002"
    / "validation_summary.json",
}

SIMULATION_SCENARIO_PROTOCOL_SPLIT_KEY = "protocol_split"
SIMULATION_SCENARIO_PROTOCOL_SPLIT_VALUES = {
    "train": "train",
    "dev": "dev",
    "test": "test",
    "none": "none",
}
SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_KEY = "pilot_split"
SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_VALUES = {
    "train": "pilot_train",
    "dev": "pilot_dev",
    "test": "pilot_test",
    "none": "none",
}
SIMULATION_SCENARIO_FORMAL_SPLIT_KEY = "formal_split"
SIMULATION_SCENARIO_FORMAL_SPLIT_VALUES = {
    "train": "train",
    "dev": "dev",
    "test": "test",
    "none": "none",
}
# The released registry currently contains formal_split and legacy pilot_split.
# protocol_split remains a future harness-build field until that release is rebuilt.
SIMULATION_SCENARIO_CURRENT_SPLIT_KEY = SIMULATION_SCENARIO_FORMAL_SPLIT_KEY
SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES = dict(SIMULATION_SCENARIO_FORMAL_SPLIT_VALUES)
# Backward-compatible aliases for older pilot-named callers.
SIMULATION_SCENARIO_SPLIT_KEY = SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_KEY
SIMULATION_SCENARIO_SPLIT_VALUES = dict(SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_VALUES)


def protocol_split_label(bucket: str) -> str:
    return SIMULATION_SCENARIO_PROTOCOL_SPLIT_VALUES.get(bucket, SIMULATION_SCENARIO_PROTOCOL_SPLIT_VALUES["none"])


def legacy_pilot_split_label(bucket: str) -> str:
    if bucket in SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_VALUES.values():
        return bucket
    return SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_VALUES.get(bucket, SIMULATION_SCENARIO_LEGACY_PILOT_SPLIT_VALUES["none"])

def project_script_path(relpath: Path) -> Path:
    return REPO_ROOT / relpath


RUN_PATIENT_LONGITUDINAL_VALIDATION_RELPATH = Path("scripts/Simulation/run_patient_longitudinal_validation.py")
BUILD_PATIENT_CASE_BUNDLES_RELPATH = Path("scripts/Simulation/build_patient_case_bundles.py")
RUN_PATIENT_SCENARIO_BENCHMARK_RELPATH = Path("scripts/Simulation/run_patient_scenario_benchmark.py")
RUN_PATIENT_THERAPIST_CONDITIONED_EVAL_RELPATH = Path("scripts/Simulation/run_patient_therapist_conditioned_eval.py")
RUN_PATIENT_SIMULATOR_HERMES_ADAPTER_RELPATH = Path("scripts/Simulation/run_patient_simulator_hermes_adapter.py")
RUN_THERAPIST_SIMULATOR_HERMES_ADAPTER_RELPATH = Path("scripts/Simulation/run_therapist_simulator_hermes_adapter.py")
RUN_DUAL_SESSION_SIMULATOR_HERMES_ADAPTER_RELPATH = Path("scripts/Simulation/run_dual_session_simulator_hermes_adapter.py")
RUN_SIMULATION_REVIEWER_HERMES_ADAPTER_RELPATH = Path("scripts/Simulation/run_simulation_reviewer_hermes_adapter.py")
RUN_SIMULATION_SKILL_VALIDATION_RELPATH = Path("scripts/Simulation/run_simulation_skill_validation.py")
RUN_SIMULATION_SKILL_DELIVERY_GATE_RELPATH = Path("scripts/Simulation/run_simulation_skill_delivery_gate.py")
RUN_HERMES_SIMULATION_SKILLS_VALIDATION_RELPATH = Path(
    "scripts/Simulation/run_hermes_simulation_skills_validation.py"
)
RUN_HERMES_PATIENT_SKILL_VALIDATION_RELPATH = Path("scripts/Simulation/run_hermes_patient_skill_validation.py")
RUN_HERMES_PATIENT_SKILL_HELDOUT_GATE_RELPATH = Path("scripts/Simulation/run_hermes_patient_skill_heldout_gate.py")
BUILD_PATIENT_SIMULATOR_EVALUATION_PACKAGE_RELPATH = Path("scripts/Simulation/build_patient_simulator_evaluation_package.py")

SIMULATION_RUNTIME_HELPERS_RELPATH = Path("scripts/Simulation/simulation_runtime_helpers.py")
PATIENT_LONGITUDINAL_RUNTIME_RELPATH = Path("scripts/Simulation/patient_longitudinal_runtime.py")
BUILD_PATIENT_RUNTIME_SCHEMAS_RELPATH = Path("scripts/Simulation/build_patient_runtime_schemas.py")
BUILD_SIMULATION_KNOWLEDGE_REGISTRY_RELPATH = Path("scripts/Simulation/build_simulation_knowledge_registry.py")
RUN_PATIENT_SCENARIO_EXECUTION_RELPATH = Path("scripts/Simulation/run_patient_scenario_execution.py")

RUN_PATIENT_STATE_MEMORY_UPDATE_RELPATH = Path("scripts/Simulation/run_patient_state_memory_update.py")
RUN_THERAPIST_CASE_MEMORY_UPDATE_RELPATH = Path("scripts/Simulation/run_therapist_case_memory_update.py")
RUN_PATIENT_TURN_GENERATION_RELPATH = Path("scripts/Simulation/run_patient_turn_generation.py")
RUN_THERAPIST_TURN_GENERATION_RELPATH = Path("scripts/Simulation/run_therapist_turn_generation.py")
BUILD_PATIENT_LONGITUDINAL_CARRYOVER_RELPATH = Path("scripts/Simulation/build_patient_longitudinal_carryover.py")
RUN_PATIENT_SINGLE_SESSION_LOOP_RELPATH = Path("scripts/Simulation/run_patient_single_session_loop.py")

RUN_PATIENT_MOVE_INTERPRETER_RELPATH = Path("scripts/Simulation/run_patient_move_interpreter.py")
RUN_PATIENT_DISCLOSURE_CONTROLLER_RELPATH = Path("scripts/Simulation/run_patient_disclosure_controller.py")
RUN_PATIENT_TURN_REALIZER_RELPATH = Path("scripts/Simulation/run_patient_turn_realizer.py")
RUN_PATIENT_STATE_UPDATER_FROM_BUNDLE_RELPATH = Path("scripts/Simulation/run_patient_state_updater_from_bundle.py")
RUN_PATIENT_SINGLE_SESSION_PROTOTYPE_RELPATH = Path("scripts/Simulation/run_patient_single_session_prototype.py")
RUN_PATIENT_MULTI_SESSION_PROTOTYPE_RELPATH = Path("scripts/Simulation/run_patient_multi_session_prototype.py")
RUN_SIMULATION_SESSION_PROTOTYPE_RELPATH = Path("scripts/Simulation/run_simulation_session_prototype.py")
RUN_MINIMAL_SIMULATION_TURN_ENGINE_RELPATH = Path("scripts/Simulation/run_minimal_simulation_turn_engine.py")

RUN_PATIENT_RUNTIME_REVIEWER_RELPATH = Path("scripts/Simulation/run_patient_runtime_reviewer.py")
RUN_SESSION_LONGITUDINAL_SUMMARY_RELPATH = Path("scripts/Simulation/run_session_longitudinal_summary.py")
RUN_SESSION_REVIEWER_GATE_RELPATH = Path("scripts/Simulation/run_session_reviewer_gate.py")
BUILD_CONDITIONED_ADJUDICATION_PACKET_RELPATH = Path("scripts/Simulation/build_conditioned_adjudication_packet.py")
BUILD_CONDITIONED_HUMAN_RATING_PROTOCOL_RELPATH = Path("scripts/Simulation/build_conditioned_human_rating_protocol.py")
SYNC_CONDITIONED_HUMAN_RATING_SHEET_RELPATH = Path("scripts/Simulation/sync_conditioned_human_rating_sheet.py")
COMPILE_CONDITIONED_HUMAN_RATING_RESULTS_RELPATH = Path("scripts/Simulation/compile_conditioned_human_rating_results.py")
VALIDATE_CONDITIONED_HUMAN_RATING_SHEET_RELPATH = Path("scripts/Simulation/validate_conditioned_human_rating_sheet.py")
RUN_CONDITIONED_HUMAN_RATING_REFRESH_RELPATH = Path("scripts/Simulation/run_conditioned_human_rating_refresh.py")

FINALIZE_SIMULATION_RUN_RELPATH = Path("scripts/Simulation/finalize_simulation_run.py")
WATCH_AND_FINALIZE_SIMULATION_RUN_RELPATH = Path("scripts/Simulation/watch_and_finalize_simulation_run.py")
REPORT_SIMULATION_MULTIRUN_STATUS_RELPATH = Path("scripts/Simulation/report_simulation_multirun_status.py")
MERGE_THERAPIST_RESPONSE_POLICY_SHARDS_RELPATH = Path("scripts/Simulation/merge_therapist_response_policy_shards.py")
BUILD_THERAPIST_RESPONSE_POLICY_PROJECTION_MANIFEST_RELPATH = Path(
    "scripts/Simulation/build_therapist_response_policy_projection_manifest.py"
)
MERGE_THERAPIST_CASE_MEMORY_SHARDS_RELPATH = Path("scripts/Simulation/merge_therapist_case_memory_shards.py")
BUILD_THERAPIST_CASE_MEMORY_PROJECTION_MANIFEST_RELPATH = Path(
    "scripts/Simulation/build_therapist_case_memory_projection_manifest.py"
)

AUTHORITATIVE_RUNNER_RELPATHS = (
    RUN_PATIENT_LONGITUDINAL_VALIDATION_RELPATH,
    RUN_PATIENT_SCENARIO_BENCHMARK_RELPATH,
    RUN_PATIENT_THERAPIST_CONDITIONED_EVAL_RELPATH,
    RUN_PATIENT_SIMULATOR_HERMES_ADAPTER_RELPATH,
    RUN_THERAPIST_SIMULATOR_HERMES_ADAPTER_RELPATH,
    RUN_DUAL_SESSION_SIMULATOR_HERMES_ADAPTER_RELPATH,
    RUN_SIMULATION_REVIEWER_HERMES_ADAPTER_RELPATH,
    RUN_SIMULATION_SKILL_VALIDATION_RELPATH,
    RUN_SIMULATION_SKILL_DELIVERY_GATE_RELPATH,
    RUN_HERMES_SIMULATION_SKILLS_VALIDATION_RELPATH,
    RUN_HERMES_PATIENT_SKILL_VALIDATION_RELPATH,
    RUN_HERMES_PATIENT_SKILL_HELDOUT_GATE_RELPATH,
    BUILD_PATIENT_SIMULATOR_EVALUATION_PACKAGE_RELPATH,
)

SHARED_RUNTIME_HELPER_RELPATHS = (
    SIMULATION_RUNTIME_HELPERS_RELPATH,
    PATIENT_LONGITUDINAL_RUNTIME_RELPATH,
    BUILD_PATIENT_RUNTIME_SCHEMAS_RELPATH,
    BUILD_SIMULATION_KNOWLEDGE_REGISTRY_RELPATH,
    RUN_PATIENT_SCENARIO_EXECUTION_RELPATH,
)

RUNTIME_HELPER_SURFACE_RELPATHS = (
    RUN_PATIENT_STATE_MEMORY_UPDATE_RELPATH,
    RUN_THERAPIST_CASE_MEMORY_UPDATE_RELPATH,
    RUN_PATIENT_TURN_GENERATION_RELPATH,
    RUN_THERAPIST_TURN_GENERATION_RELPATH,
    BUILD_PATIENT_LONGITUDINAL_CARRYOVER_RELPATH,
    RUN_PATIENT_SINGLE_SESSION_LOOP_RELPATH,
)

LEGACY_RUNTIME_SUPPORT_RELPATHS = (
    RUN_PATIENT_MOVE_INTERPRETER_RELPATH,
    RUN_PATIENT_DISCLOSURE_CONTROLLER_RELPATH,
    RUN_PATIENT_TURN_REALIZER_RELPATH,
    RUN_PATIENT_STATE_UPDATER_FROM_BUNDLE_RELPATH,
)

PROTOTYPE_RUNNER_RELPATHS = (
    RUN_PATIENT_SINGLE_SESSION_PROTOTYPE_RELPATH,
    RUN_PATIENT_MULTI_SESSION_PROTOTYPE_RELPATH,
    RUN_SIMULATION_SESSION_PROTOTYPE_RELPATH,
    RUN_MINIMAL_SIMULATION_TURN_ENGINE_RELPATH,
)

REVIEW_RUNTIME_SUPPORT_RELPATHS = (
    RUN_PATIENT_RUNTIME_REVIEWER_RELPATH,
    RUN_SESSION_LONGITUDINAL_SUMMARY_RELPATH,
    RUN_SESSION_REVIEWER_GATE_RELPATH,
)

HUMAN_RATING_WORKFLOW_RELPATHS = (
    BUILD_CONDITIONED_ADJUDICATION_PACKET_RELPATH,
    BUILD_CONDITIONED_HUMAN_RATING_PROTOCOL_RELPATH,
    SYNC_CONDITIONED_HUMAN_RATING_SHEET_RELPATH,
    COMPILE_CONDITIONED_HUMAN_RATING_RESULTS_RELPATH,
    VALIDATE_CONDITIONED_HUMAN_RATING_SHEET_RELPATH,
    RUN_CONDITIONED_HUMAN_RATING_REFRESH_RELPATH,
)

FINALIZE_WATCH_HELPER_RELPATHS = (
    FINALIZE_SIMULATION_RUN_RELPATH,
    WATCH_AND_FINALIZE_SIMULATION_RUN_RELPATH,
    REPORT_SIMULATION_MULTIRUN_STATUS_RELPATH,
)

CURRENT_AUTHORITATIVE_CODE_SURFACE_RELPATHS = (
    *AUTHORITATIVE_RUNNER_RELPATHS,
    *SHARED_RUNTIME_HELPER_RELPATHS,
    *RUNTIME_HELPER_SURFACE_RELPATHS,
)

NON_AUTHORITATIVE_STRUCTURED_SURFACE_RELPATHS = (
    *LEGACY_RUNTIME_SUPPORT_RELPATHS,
    *REVIEW_RUNTIME_SUPPORT_RELPATHS,
    *FINALIZE_WATCH_HELPER_RELPATHS,
)

PROTOTYPE_LEGACY_SURFACE_RELPATHS = (
    *LEGACY_RUNTIME_SUPPORT_RELPATHS,
    *PROTOTYPE_RUNNER_RELPATHS,
)

REVIEWER_RATING_SURFACE_RELPATHS = (
    *REVIEW_RUNTIME_SUPPORT_RELPATHS,
    *HUMAN_RATING_WORKFLOW_RELPATHS,
)

AUTHORITATIVE_RUNNERS = tuple(path.name for path in AUTHORITATIVE_RUNNER_RELPATHS)
SHARED_RUNTIME_HELPERS = tuple(path.name for path in SHARED_RUNTIME_HELPER_RELPATHS)
RUNTIME_HELPER_SURFACE = tuple(path.name for path in RUNTIME_HELPER_SURFACE_RELPATHS)
CURRENT_AUTHORITATIVE_CODE_SURFACE = tuple(path.name for path in CURRENT_AUTHORITATIVE_CODE_SURFACE_RELPATHS)
NON_AUTHORITATIVE_STRUCTURED_SURFACE = tuple(path.name for path in NON_AUTHORITATIVE_STRUCTURED_SURFACE_RELPATHS)
LEGACY_RUNTIME_SUPPORT = tuple(path.name for path in LEGACY_RUNTIME_SUPPORT_RELPATHS)
PROTOTYPE_RUNNERS = tuple(path.name for path in PROTOTYPE_RUNNER_RELPATHS)
REVIEW_RUNTIME_SUPPORT = tuple(path.name for path in REVIEW_RUNTIME_SUPPORT_RELPATHS)
HUMAN_RATING_WORKFLOW_SURFACE = tuple(path.name for path in HUMAN_RATING_WORKFLOW_RELPATHS)
PROTOTYPE_LEGACY_SURFACE = tuple(path.name for path in PROTOTYPE_LEGACY_SURFACE_RELPATHS)
REVIEWER_RATING_SURFACE = tuple(path.name for path in REVIEWER_RATING_SURFACE_RELPATHS)
FINALIZE_WATCH_HELPERS = tuple(path.name for path in FINALIZE_WATCH_HELPER_RELPATHS)

HISTORICAL_V1_COMPLETION_EVIDENCE_PATHS = {
    "single_session_loop": SIMULATION_HISTORICAL_EVIDENCE_ROOT / "patient_single_session_loop_historical_v1.json",
    "multi_session_prototype": SIMULATION_HISTORICAL_EVIDENCE_ROOT / "patient_multi_session_prototype_historical_v1.json",
    "runtime_reviewer": SIMULATION_HISTORICAL_EVIDENCE_ROOT / "patient_runtime_reviewer_historical_v1.json",
}


@dataclass(frozen=True)
class SimulationFinalizeRunSpec:
    tag: str
    name: str
    run_dir: str
    shard_prefix: str
    tmux_prefix: str
    merge_script_relpath: Path
    projection_script_relpath: Path
    projection_manifest: str
    projection_qa: str
    canonical_output: str


@dataclass(frozen=True)
class SimulationExtractionFamilySpec:
    family: str
    run_dir: str
    shard_dir: str
    shard_prefix: str
    candidates_jsonl: str
    canonical_output: str
    failure_log: str
    projection_manifest: str
    projection_qa: str
    controlled_label_index: str


SIMULATION_EXTRACTION_FAMILY_SPECS = {
    "patient_state_transition_pattern": SimulationExtractionFamilySpec(
        family="patient_state_transition_pattern",
        run_dir="work/runs/patient_state_transition_extraction_20260708",
        shard_dir="work/runs/patient_state_transition_extraction_20260708/shards",
        shard_prefix="patient_state_transition_pattern.shard_",
        candidates_jsonl="outputs/knowledge/candidates/Simulation.patient_state_transition_candidates.jsonl",
        canonical_output="outputs/knowledge/units/Simulation.patient_state_transition_pattern.jsonl",
        failure_log="outputs/knowledge/manifests/Simulation.patient_state_transition_failures.jsonl",
        projection_manifest="outputs/knowledge/manifests/Simulation.patient_state_transition_projection_manifest.json",
        projection_qa="outputs/knowledge/manifests/Simulation.patient_state_transition_projection_qa.json",
        controlled_label_index="outputs/knowledge/indexes/Simulation.patient_state_transition_controlled_label_index.json",
    ),
    "therapist_response_policy_pattern": SimulationExtractionFamilySpec(
        family="therapist_response_policy_pattern",
        run_dir="work/runs/therapist_response_policy_extraction_20260708",
        shard_dir="work/runs/therapist_response_policy_extraction_20260708/shards",
        shard_prefix="therapist_response_policy_pattern.shard_",
        candidates_jsonl="outputs/knowledge/candidates/Simulation.therapist_response_policy_candidates.jsonl",
        canonical_output="outputs/knowledge/units/Simulation.therapist_response_policy_pattern.jsonl",
        failure_log="outputs/knowledge/manifests/Simulation.therapist_response_policy_failures.jsonl",
        projection_manifest="outputs/knowledge/manifests/Simulation.therapist_response_policy_projection_manifest.json",
        projection_qa="outputs/knowledge/manifests/Simulation.therapist_response_policy_projection_qa.json",
        controlled_label_index="outputs/knowledge/indexes/Simulation.therapist_response_policy_controlled_label_index.json",
    ),
    "therapist_case_memory_pattern": SimulationExtractionFamilySpec(
        family="therapist_case_memory_pattern",
        run_dir="work/runs/therapist_case_memory_extraction_20260708",
        shard_dir="work/runs/therapist_case_memory_extraction_20260708/shards",
        shard_prefix="therapist_case_memory_pattern.shard_",
        candidates_jsonl="outputs/knowledge/candidates/Simulation.therapist_case_memory_candidates.jsonl",
        canonical_output="outputs/knowledge/units/Simulation.therapist_case_memory_pattern.jsonl",
        failure_log="outputs/knowledge/manifests/Simulation.therapist_case_memory_failures.jsonl",
        projection_manifest="outputs/knowledge/manifests/Simulation.therapist_case_memory_projection_manifest.json",
        projection_qa="outputs/knowledge/manifests/Simulation.therapist_case_memory_projection_qa.json",
        controlled_label_index="outputs/knowledge/indexes/Simulation.therapist_case_memory_controlled_label_index.json",
    ),
}

SIMULATION_EXTRACTION_RUNNER_MANIFEST_PATHS = {
    "patient_state_transition_pattern": REPO_ROOT
    / "outputs"
    / "knowledge"
    / "manifests"
    / "Simulation.patient_state_transition_projection_manifest.json",
    "therapist_response_policy_pattern": REPO_ROOT
    / "outputs"
    / "knowledge"
    / "manifests"
    / "Simulation.therapist_response_policy_runner_manifest.json",
    "therapist_case_memory_pattern": REPO_ROOT
    / "outputs"
    / "knowledge"
    / "manifests"
    / "Simulation.therapist_case_memory_runner_manifest.json",
}

SIMULATION_EXTRACTION_RUNNER_QA_PATHS = {
    "patient_state_transition_pattern": REPO_ROOT
    / "outputs"
    / "knowledge"
    / "manifests"
    / "Simulation.patient_state_transition_projection_qa.json",
    "therapist_response_policy_pattern": REPO_ROOT
    / "outputs"
    / "knowledge"
    / "manifests"
    / "Simulation.therapist_response_policy_runner_qa.json",
    "therapist_case_memory_pattern": REPO_ROOT
    / "outputs"
    / "knowledge"
    / "manifests"
    / "Simulation.therapist_case_memory_runner_qa.json",
}


SIMULATION_FINALIZE_RUN_SPECS = {
    "S2": SimulationFinalizeRunSpec(
        tag="S2",
        name="therapist_response_policy_pattern",
        run_dir="work/runs/therapist_response_policy_extraction_20260708",
        shard_prefix="therapist_response_policy_pattern.shard_",
        tmux_prefix="therapist_response_policy_shard_",
        merge_script_relpath=MERGE_THERAPIST_RESPONSE_POLICY_SHARDS_RELPATH,
        projection_script_relpath=BUILD_THERAPIST_RESPONSE_POLICY_PROJECTION_MANIFEST_RELPATH,
        projection_manifest="outputs/knowledge/manifests/Simulation.therapist_response_policy_projection_manifest.json",
        projection_qa="outputs/knowledge/manifests/Simulation.therapist_response_policy_projection_qa.json",
        canonical_output="outputs/knowledge/units/Simulation.therapist_response_policy_pattern.jsonl",
    ),
    "S3": SimulationFinalizeRunSpec(
        tag="S3",
        name="therapist_case_memory_pattern",
        run_dir="work/runs/therapist_case_memory_extraction_20260708",
        shard_prefix="therapist_case_memory_pattern.shard_",
        tmux_prefix="therapist_case_memory_shard_",
        merge_script_relpath=MERGE_THERAPIST_CASE_MEMORY_SHARDS_RELPATH,
        projection_script_relpath=BUILD_THERAPIST_CASE_MEMORY_PROJECTION_MANIFEST_RELPATH,
        projection_manifest="outputs/knowledge/manifests/Simulation.therapist_case_memory_projection_manifest.json",
        projection_qa="outputs/knowledge/manifests/Simulation.therapist_case_memory_projection_qa.json",
        canonical_output="outputs/knowledge/units/Simulation.therapist_case_memory_pattern.jsonl",
    ),
}
