#!/usr/bin/env python3
"""Canonical path helpers for Hermes runtime validation artifacts.

The naming here distinguishes:
- authoritative current runtime surfaces
- compatibility/historical validation surfaces
- trusted legacy gates kept as evidence
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "hermes_runtime_validation"
WORK_RUNS_ROOT = PROJECT_ROOT / "work" / "runs"

QWEN_AUTHORITATIVE_ROOT = OUTPUT_ROOT / "qwen"
QWEN_AUTHORITATIVE_CASES_DIR = QWEN_AUTHORITATIVE_ROOT / "cases"
QWEN_AUTHORITATIVE_RUNTIME_ROOT = QWEN_AUTHORITATIVE_ROOT / "runtime_validation"

QWEN_COMPATIBILITY_ROOT = WORK_RUNS_ROOT / "hermes_qwen_runtime_validation_20260706"
QWEN_COMPATIBILITY_CASES_DIR = QWEN_COMPATIBILITY_ROOT / "cases"
QWEN_COMPATIBILITY_LOGS_DIR = QWEN_COMPATIBILITY_ROOT / "logs"

DEEPSEEK_AUTHORITATIVE_ROOT = OUTPUT_ROOT / "deepseek"
DEEPSEEK_AUTHORITATIVE_RUNTIME_ROOT = DEEPSEEK_AUTHORITATIVE_ROOT / "runtime_validation"
DEEPSEEK_COMPATIBILITY_ROOT = WORK_RUNS_ROOT / "hermes_deepseek_runtime_validation_20260706"


@dataclass(frozen=True)
class RuntimeSurface:
    run_root: Path
    report: Path
    qa: Path
    summary: Path


def qwen_authoritative_cases_path(filename: str = "hermes_qwen_runtime_cases.jsonl") -> Path:
    return QWEN_AUTHORITATIVE_CASES_DIR / filename


def qwen_authoritative_cases_manifest_path(filename: str = "hermes_qwen_runtime_cases_manifest.json") -> Path:
    return QWEN_AUTHORITATIVE_CASES_DIR / filename


def qwen_authoritative_runtime_surface() -> RuntimeSurface:
    run_root = QWEN_AUTHORITATIVE_RUNTIME_ROOT
    return RuntimeSurface(
        run_root=run_root,
        report=run_root / "reports" / "hermes_qwen_runtime_validation_report.json",
        qa=run_root / "reports" / "hermes_qwen_runtime_validation_qa.json",
        summary=run_root / "reports" / "hermes_qwen_runtime_validation_summary.md",
    )


def qwen_compatibility_v2_cases_path() -> Path:
    return QWEN_COMPATIBILITY_CASES_DIR / "hermes_qwen_runtime_cases_v2_hybrid.jsonl"


def qwen_compatibility_v2_cases_manifest_path() -> Path:
    return QWEN_COMPATIBILITY_CASES_DIR / "hermes_qwen_runtime_cases_v2_hybrid_manifest.json"


def qwen_compatibility_v2_cases_qa_path() -> Path:
    return QWEN_COMPATIBILITY_CASES_DIR / "hermes_qwen_runtime_cases_v2_hybrid_qa.json"


def qwen_trusted_v3_gate_cases_path() -> Path:
    return QWEN_COMPATIBILITY_CASES_DIR / "hermes_qwen_runtime_cases_v3_llm_hybrid.jsonl"


def qwen_trusted_v3_gate_cases_manifest_path() -> Path:
    return QWEN_COMPATIBILITY_CASES_DIR / "hermes_qwen_runtime_cases_v3_llm_hybrid_manifest.json"


def qwen_compatibility_log_path(filename: str) -> Path:
    return QWEN_COMPATIBILITY_LOGS_DIR / filename


def qwen_compatibility_v2_runtime_surface() -> RuntimeSurface:
    run_root = QWEN_COMPATIBILITY_ROOT / "v2_hybrid_30case"
    return RuntimeSurface(
        run_root=run_root,
        report=run_root / "reports" / "hermes_qwen_v2_hybrid_runtime_validation_report.json",
        qa=run_root / "reports" / "hermes_qwen_v2_hybrid_runtime_validation_qa.json",
        summary=run_root / "reports" / "hermes_qwen_v2_hybrid_runtime_validation_summary.md",
    )


def qwen_trusted_v3_gate_runtime_surface() -> RuntimeSurface:
    run_root = QWEN_COMPATIBILITY_ROOT / "v3_llm_hybrid_30case_8002_clean_cwd"
    return RuntimeSurface(
        run_root=run_root,
        report=run_root / "reports" / "hermes_qwen_v3_llm_hybrid_runtime_validation_report.json",
        qa=run_root / "reports" / "hermes_qwen_v3_llm_hybrid_runtime_validation_qa.json",
        summary=run_root / "reports" / "hermes_qwen_v3_llm_hybrid_runtime_validation_summary.md",
    )


def qwen_regression_rerun_surface() -> RuntimeSurface:
    run_root = QWEN_AUTHORITATIVE_ROOT / "regression_reruns" / "v3_llm_hybrid_30case_8002_clean_cwd_rerun"
    return RuntimeSurface(
        run_root=run_root,
        report=run_root / "reports" / "hermes_qwen_v3_llm_hybrid_runtime_validation_report.json",
        qa=run_root / "reports" / "hermes_qwen_v3_llm_hybrid_runtime_validation_qa.json",
        summary=run_root / "reports" / "hermes_qwen_v3_llm_hybrid_runtime_validation_summary.md",
    )


def deepseek_authoritative_runtime_surface() -> RuntimeSurface:
    run_root = DEEPSEEK_AUTHORITATIVE_RUNTIME_ROOT
    return RuntimeSurface(
        run_root=run_root,
        report=run_root / "reports" / "hermes_deepseek_runtime_validation_report.json",
        qa=run_root / "reports" / "hermes_deepseek_runtime_validation_qa.json",
        summary=run_root / "reports" / "hermes_deepseek_runtime_validation_summary.md",
    )


# Backward-compatible aliases for older callers.
def qwen_current_cases_path(filename: str = "hermes_qwen_runtime_cases.jsonl") -> Path:
    return qwen_authoritative_cases_path(filename)


def qwen_current_cases_manifest_path(filename: str = "hermes_qwen_runtime_cases_manifest.json") -> Path:
    return qwen_authoritative_cases_manifest_path(filename)


def qwen_current_runtime_surface() -> RuntimeSurface:
    return qwen_authoritative_runtime_surface()


def qwen_legacy_v2_cases_path() -> Path:
    return qwen_compatibility_v2_cases_path()


def qwen_legacy_v2_cases_manifest_path() -> Path:
    return qwen_compatibility_v2_cases_manifest_path()


def qwen_legacy_v2_cases_qa_path() -> Path:
    return qwen_compatibility_v2_cases_qa_path()


def qwen_legacy_v3_cases_path() -> Path:
    return qwen_trusted_v3_gate_cases_path()


def qwen_legacy_v3_cases_manifest_path() -> Path:
    return qwen_trusted_v3_gate_cases_manifest_path()


def qwen_legacy_log_path(filename: str) -> Path:
    return qwen_compatibility_log_path(filename)


def qwen_legacy_v2_runtime_surface() -> RuntimeSurface:
    return qwen_compatibility_v2_runtime_surface()


def qwen_legacy_trusted_v3_runtime_surface() -> RuntimeSurface:
    return qwen_trusted_v3_gate_runtime_surface()


def qwen_current_regression_rerun_surface() -> RuntimeSurface:
    return qwen_regression_rerun_surface()


def deepseek_current_runtime_surface() -> RuntimeSurface:
    return deepseek_authoritative_runtime_surface()
