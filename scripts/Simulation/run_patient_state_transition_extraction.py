#!/usr/bin/env python3
"""Run LLM extraction for Simulation.patient_state_transition_pattern."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from domains.theraskill.theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    PromptBudgetExceededError,
    chat_json_with_repair,
    context_max_prompt_tokens,
    load_prompt,
    load_yaml,
    merged_model_config,
    parse_retry_attempts,
    prompt_budget_report,
    read_jsonl,
)
from io_utils import file_sha256, write_json  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    PATIENT_STATE_TRANSITION_EXTRACTION_PROMPT_PATH,
    REPO_ROOT,
    SIMULATION_EXTRACTION_FAMILY_SPECS,
    SIMULATION_EXTRACTION_RUNNER_MANIFEST_PATHS,
    SIMULATION_EXTRACTION_RUNNER_QA_PATHS,
)

PROJECT_ROOT = REPO_ROOT
DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["patient_state_transition_pattern"]
DEFAULT_CANDIDATES = PROJECT_ROOT / DEFAULT_SPEC.candidates_jsonl
DEFAULT_PROMPT = PATIENT_STATE_TRANSITION_EXTRACTION_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT = PROJECT_ROOT / DEFAULT_SPEC.canonical_output
DEFAULT_MANIFEST = SIMULATION_EXTRACTION_RUNNER_MANIFEST_PATHS["patient_state_transition_pattern"]
DEFAULT_QA = SIMULATION_EXTRACTION_RUNNER_QA_PATHS["patient_state_transition_pattern"]
DEFAULT_INDEX = PROJECT_ROOT / DEFAULT_SPEC.controlled_label_index
DEFAULT_FAILURES = PROJECT_ROOT / DEFAULT_SPEC.failure_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-jsonl", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--failure-log", default=str(DEFAULT_FAILURES))
    parser.add_argument("--limit-candidates", type=int)
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids")
    parser.add_argument("--candidate-id-file")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--compact-target-prompt-tokens", type=int, default=12000)
    return parser.parse_args()


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def safe_read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return read_jsonl(path)


def clean_text(text: Any, limit: int | None = None) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] if limit is not None else text


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def flatten_dict_items(items: Any, *, cap: int | None = None) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if cap is not None and len(flat) >= cap:
            return
        if isinstance(value, dict):
            flat.append(value)
            return
        if isinstance(value, list):
            for child in value:
                visit(child)
                if cap is not None and len(flat) >= cap:
                    return

    visit(items)
    return flat


def trim_move_sequence(seq: list[dict[str, Any]], *, step_cap: int = 5) -> list[dict[str, Any]]:
    items = []
    for step in flatten_dict_items(seq, cap=step_cap):
        items.append(
            {
                "order": step.get("order"),
                "move_types": step.get("move_types") or [],
                "move_functions": step.get("move_functions") or [],
                "surface_summary": clean_text(step.get("surface_summary"), 220),
                "evidence_transition_ids": step.get("evidence_transition_ids") or [],
            }
        )
    return items


def trim_changes(changes: list[dict[str, Any]], *, cap: int = 4) -> list[dict[str, Any]]:
    items = []
    for change in flatten_dict_items(changes, cap=cap):
        items.append(
            {
                "response_type": change.get("response_type"),
                "transition_type": change.get("transition_type"),
                "summary": clean_text(change.get("summary"), 240),
                "evidence_transition_ids": change.get("evidence_transition_ids") or [],
            }
        )
    return items


def trim_problem_summary(items: list[dict[str, Any]], *, cap: int = 5) -> list[dict[str, Any]]:
    trimmed = []
    for item in flatten_dict_items(items, cap=cap):
        trimmed.append(
            {
                "label": item.get("label"),
                "evidence_transition_ids": item.get("evidence_transition_ids") or [],
            }
        )
    return trimmed


def trim_turning_points(items: list[dict[str, Any]], *, cap: int = 4) -> list[dict[str, Any]]:
    trimmed = []
    for item in flatten_dict_items(items, cap=cap):
        trimmed.append(
            {
                "summary": clean_text(item.get("summary"), 220),
                "evidence_transition_ids": item.get("evidence_transition_ids") or [],
            }
        )
    return trimmed


def build_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "extract_patient_state_transition_pattern",
        "candidate_id": candidate.get("candidate_id"),
        "starting_state": candidate.get("starting_state") or {},
        "applicable_context": clean_text(candidate.get("applicable_context"), 320),
        "move_sequence": trim_move_sequence(candidate.get("move_sequence") or []),
        "observed_client_changes": trim_changes(candidate.get("observed_client_changes") or []),
        "trajectory_problem_summary": trim_problem_summary(candidate.get("trajectory_problem_summary") or []),
        "trajectory_overall_process_summary": clean_text(candidate.get("trajectory_overall_process_summary"), 520),
        "trajectory_turning_points": trim_turning_points(candidate.get("trajectory_turning_points") or []),
        "transition_type_counts": candidate.get("transition_type_counts") or {},
        "response_type_counts": candidate.get("response_type_counts") or {},
        "memory_pattern_refs": candidate.get("memory_pattern_refs") or [],
        "anna_bridge_refs": candidate.get("anna_bridge_refs") or [],
        "continuity_hint_note": candidate.get("continuity_hint_note") or "",
        "evidence_unit_ids": candidate.get("evidence_unit_ids") or [],
        "provenance": candidate.get("provenance") or {},
        "constraints": {
            "anna_bridge_only_as_continuity_hint": True,
            "no_invented_therapist_details": True,
            "must_preserve_uncertainty_if_evidence_is_weak": True,
        },
    }


def wrap_unit(candidate: dict[str, Any], extracted: dict[str, Any], prompt_audit: dict[str, Any], prompt_path: str) -> dict[str, Any]:
    transition = extracted.get("transition_pattern") or {}
    candidate_id = str(candidate.get("candidate_id"))
    source_unit_ids = candidate.get("evidence_unit_ids") or []
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": f"ku_simulation_patient_state_transition_{stable_hash(candidate_id)}",
        "knowledge_type": "patient_state_transition_pattern",
        "source": {
            "dataset": "Simulation.multisource",
            "source_id": candidate_id,
            "candidate_id": candidate_id,
            "case_id": candidate.get("source_case_id"),
            "source_unit_ids": source_unit_ids,
            "language": "zh",
            "source_construction_type": "llm_multisource_transition_pattern_abstraction",
            "evidence_tier": "multisource_patient_state_transition_pattern",
            "citation": "Derived from CPsyCounD case/process knowledge, SoulChat-R1 synthetic memory bundles, and optional Anna continuity hints.",
        },
        "content": {"transition_pattern": transition},
        "quality": {
            "review_status": "llm_extracted_unreviewed",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    "This is a multisource abstraction over prior knowledge units, not a direct observed dialogue record.",
                    "Anna continuity hints, when present, are synthetic and must not be treated as therapist observed evidence.",
                    "Patterns may express likely process motifs rather than deterministic rules.",
                ],
            },
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "pending_projection_manifest_builder",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": "\n".join(
                [
                    clean_text(transition.get("state_shift_type")),
                    clean_text(transition.get("emotion_shift")),
                    clean_text(transition.get("alliance_shift")),
                    clean_text(transition.get("disclosure_shift")),
                    clean_text(transition.get("resistance_shift")),
                    clean_text(transition.get("readiness_shift")),
                    clean_text(transition.get("risk_shift")),
                    " ".join(transition.get("applicable_context") or []),
                    " ".join(transition.get("non_applicable_context") or []),
                    clean_text((transition.get("interaction_context") or {}).get("case_context_summary")),
                    " ".join((transition.get("starting_state_profile") or {}).get("presenting_problem_state") or []),
                    " ".join((transition.get("next_state_profile") or {}).get("presenting_problem_state") or []),
                ]
            )[:4000],
        },
        "provenance": {
            "projection": "llm_simulation_patient_state_transition_extraction",
            "prompt_path": prompt_path,
            "prompt_audit": prompt_audit,
            "llm_calls": 1,
            "sampling": "one_candidate_per_call",
            "filtering": "deterministic_candidate_builder_then_llm_abstraction",
            "deduplication": "none",
            "candidate_snapshot": {
                "candidate_id": candidate_id,
                "sequence_name": candidate.get("sequence_name"),
                "sequence_order": candidate.get("sequence_order"),
                "source_case_id": candidate.get("source_case_id"),
                "stage_hint": candidate.get("stage_hint"),
            },
        },
    }


def completed_candidate_ids(output_path: Path, failure_path: Path, *, resume: bool) -> set[str]:
    if not resume:
        return set()
    done = set()
    for row in safe_read_jsonl(output_path):
        candidate_id = str((row.get("source") or {}).get("candidate_id") or "")
        if candidate_id:
            done.add(candidate_id)
    for row in safe_read_jsonl(failure_path):
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id:
            done.add(candidate_id)
    return done


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    failure_path = Path(args.failure_log)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)
    index_path = Path(args.index)

    if args.overwrite:
        for path in [output_path, failure_path, manifest_path, qa_path, index_path]:
            if Path(path).exists():
                Path(path).unlink()

    candidates = read_jsonl(args.candidates_jsonl)
    candidate_ids = list(args.candidate_ids or [])
    if args.candidate_id_file:
        with Path(args.candidate_id_file).open("r", encoding="utf-8") as f:
            candidate_ids.extend(line.strip() for line in f if line.strip())
    if candidate_ids:
        allow = set(candidate_ids)
        candidates = [row for row in candidates if str(row.get("candidate_id")) in allow]
    if args.limit_candidates is not None:
        candidates = candidates[: args.limit_candidates]

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    client = OpenAICompatibleClient(model_config)

    done = completed_candidate_ids(output_path, failure_path, resume=args.resume)
    counters = Counter()
    prompt_audits = []

    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id in done:
            counters["skipped_resume"] += 1
            continue

        payload = build_payload(candidate)
        prompt_audit = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
        prompt_audit["candidate_id"] = candidate_id
        prompt_audits.append(prompt_audit)

        if prompt_audit["estimated_prompt_tokens"] > args.compact_target_prompt_tokens:
            # We surface the true audit and still attempt if under hard max_prompt_tokens.
            prompt_audit["compact_target_exceeded"] = True
        else:
            prompt_audit["compact_target_exceeded"] = False

        if prompt_audit["over_budget"]:
            append_jsonl(
                failure_path,
                {
                    "candidate_id": candidate_id,
                    "failure": {"reason": "prompt_token_budget_exceeded", "details": prompt_audit},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            counters["prompt_token_budget_exceeded"] += 1
            continue

        try:
            result = chat_json_with_repair(
                client,
                system_prompt,
                payload,
                parse_retries=parse_retries,
                force_json_first=True,
            )
            append_jsonl(output_path, wrap_unit(candidate, result, prompt_audit, args.prompt))
            counters["written"] += 1
        except PromptBudgetExceededError as exc:
            append_jsonl(
                failure_path,
                {
                    "candidate_id": candidate_id,
                    "failure": {"reason": "prompt_token_budget_exceeded", "details": exc.report},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            counters["prompt_token_budget_exceeded"] += 1
        except Exception as exc:  # noqa: BLE001
            append_jsonl(
                failure_path,
                {
                    "candidate_id": candidate_id,
                    "failure": {"reason": type(exc).__name__, "details": str(exc)},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            counters["failed"] += 1

    output_rows = safe_read_jsonl(output_path)
    failures = safe_read_jsonl(failure_path)

    manifest = {
        "schema_version": "theraskill_simulation_patient_state_transition_runner_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "Simulation.multisource",
        "status": "complete" if counters["failed"] == 0 and counters["prompt_token_budget_exceeded"] == 0 else "partial",
        "input_candidates": {
            "path": args.candidates_jsonl,
            "sha256": file_sha256(args.candidates_jsonl),
            "row_count": len(candidates),
        },
        "source_files": {
            args.prompt: {"path": args.prompt, "sha256": file_sha256(args.prompt)},
            args.model_config: {"path": args.model_config, "sha256": file_sha256(args.model_config)},
        },
        "written_rows": len(output_rows),
        "failure_rows": len(failures),
        "counters": dict(counters),
        "prompt_audit_summary": {
            "count": len(prompt_audits),
            "max_estimated_prompt_tokens": max((item["estimated_prompt_tokens"] for item in prompt_audits), default=0),
            "compact_target_exceeded_count": sum(1 for item in prompt_audits if item.get("compact_target_exceeded")),
            "over_budget_count": sum(1 for item in prompt_audits if item["over_budget"]),
        },
        "output_file": {"path": str(output_path), "sha256": file_sha256(output_path)},
        "failure_log": {"path": str(failure_path), "sha256": file_sha256(failure_path)},
        "notes": [
            "This runner emits raw knowledge units only; projection QA/index should be rebuilt by build_patient_state_transition_projection_manifest.py.",
            "Candidates above the compact target are still allowed when they remain below the hard prompt-token limit.",
        ],
    }
    write_json(manifest_path, manifest)

    qa = {
        "schema_version": "theraskill_simulation_patient_state_transition_runner_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "Simulation.multisource",
        "status": manifest["status"],
        "written_rows": len(output_rows),
        "failure_count": len(failures),
        "issues": failures,
        "issues_count": len(failures),
        "prompt_audit_summary": manifest["prompt_audit_summary"],
    }
    write_json(qa_path, qa)
    print(json.dumps({"status": qa["status"], "written_rows": qa["written_rows"], "failure_count": qa["failure_count"]}, ensure_ascii=False, indent=2))
    return 0 if qa["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
