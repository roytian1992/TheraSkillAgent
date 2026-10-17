#!/usr/bin/env python3
"""List the clean extraction surfaces and their expected inputs/outputs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACES = [
    ("CPsyCounD", "scripts/CPsyCounD/run_case_knowledge_extraction.py", "outputs/knowledge/units/CPsyCounD.*.jsonl"),
    ("CPsyCounE", "scripts/CPsyCounE/run_cpsycoune_knowledge_extraction.py", "outputs/knowledge/units/CPsyCounE.*.jsonl"),
    ("ESConv", "scripts/ESConv/run_esconv_knowledge_extraction.py", "outputs/knowledge/units/ESConv.*.jsonl"),
    ("Simulation.patient_state_transition", "scripts/Simulation/run_patient_state_transition_extraction.py", "outputs/knowledge/units/Simulation.patient_state_transition_pattern.jsonl"),
    ("Simulation.therapist_response_policy", "scripts/Simulation/run_therapist_response_policy_extraction.py", "outputs/knowledge/units/Simulation.therapist_response_policy_pattern.jsonl"),
    ("Simulation.therapist_case_memory", "scripts/Simulation/run_therapist_case_memory_extraction.py", "outputs/knowledge/units/Simulation.therapist_case_memory_pattern.jsonl"),
]
for name, runner, output in SURFACES:
    print(f"{name}\n  runner: {runner}\n  output: {output}")
