#!/usr/bin/env python3
"""Run LLM extraction for Simulation.therapist_case_memory_pattern."""

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

from theraskill_llm import (  # noqa: E402
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
    REPO_ROOT,
    SIMULATION_EXTRACTION_FAMILY_SPECS,
    SIMULATION_EXTRACTION_RUNNER_MANIFEST_PATHS,
    SIMULATION_EXTRACTION_RUNNER_QA_PATHS,
    THERAPIST_CASE_MEMORY_EXTRACTION_PROMPT_PATH,
)

PROJECT_ROOT = REPO_ROOT
DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["therapist_case_memory_pattern"]
DEFAULT_CANDIDATES = PROJECT_ROOT / DEFAULT_SPEC.candidates_jsonl
DEFAULT_PROMPT = THERAPIST_CASE_MEMORY_EXTRACTION_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/model_config.yaml"
DEFAULT_OUTPUT = PROJECT_ROOT / DEFAULT_SPEC.canonical_output
DEFAULT_RUNNER_MANIFEST = SIMULATION_EXTRACTION_RUNNER_MANIFEST_PATHS["therapist_case_memory_pattern"]
DEFAULT_RUNNER_QA = SIMULATION_EXTRACTION_RUNNER_QA_PATHS["therapist_case_memory_pattern"]
DEFAULT_FAILURES = PROJECT_ROOT / DEFAULT_SPEC.failure_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-jsonl", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_RUNNER_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_RUNNER_QA))
    parser.add_argument("--failure-log", default=str(DEFAULT_FAILURES))
    parser.add_argument("--limit-candidates", type=int)
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids")
    parser.add_argument("--candidate-id-file")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
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
    value = " ".join(str(text or "").split())
    return value[:limit] if limit is not None else value


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def trim_list(values: list[Any], limit: int, item_limit: int = 220) -> list[str]:
    return [clean_text(value, item_limit) for value in values[:limit] if clean_text(value, item_limit)]


def build_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "extract_therapist_case_memory_pattern",
        "candidate_id": candidate.get("candidate_id"),
        "source_case_id": candidate.get("source_case_id"),
        "source_source_id": candidate.get("source_source_id"),
        "transition_count": candidate.get("transition_count"),
        "phase_sequence": candidate.get("phase_sequence") or [],
        "required_memory_slots_hint": candidate.get("required_memory_slots_hint") or [],
        "problem_summary_slots_hint": candidate.get("problem_summary_slots_hint") or [],
        "resource_slots_hint": candidate.get("resource_slots_hint") or [],
        "goal_slots_hint": candidate.get("goal_slots_hint") or [],
        "alliance_slots_hint": candidate.get("alliance_slots_hint") or [],
        "risk_slots_hint": candidate.get("risk_slots_hint") or [],
        "unfinished_thread_slots_hint": candidate.get("unfinished_thread_slots_hint") or [],
        "stage_tracking_slots_hint": candidate.get("stage_tracking_slots_hint") or [],
        "memory_update_rules_hint": candidate.get("memory_update_rules_hint") or [],
        "compression_rules_hint": candidate.get("compression_rules_hint") or [],
        "forgetting_risks_hint": candidate.get("forgetting_risks_hint") or [],
        "handoff_or_resume_rules_hint": candidate.get("handoff_or_resume_rules_hint") or [],
        "evidence_transition_ids": candidate.get("evidence_transition_ids") or [],
        "memory_pattern_refs": candidate.get("memory_pattern_refs") or [],
        "case_overview": candidate.get("case_overview") or {},
        "evidence_unit_ids": candidate.get("evidence_unit_ids") or [],
        "provenance": candidate.get("provenance") or {},
        "constraints": {
            "must_keep_generic_memory_rules_separate_from_case_facts": True,
            "must_output_structured_slots": True,
            "must_include_memory_update_rules": True,
            "must_include_compression_rules": True,
            "must_not_copy_think_or_system_text": True,
        },
    }


def wrap_unit(candidate: dict[str, Any], extracted: dict[str, Any], prompt_audit: dict[str, Any], prompt_path: str) -> dict[str, Any]:
    pattern = extracted.get("therapist_case_memory_pattern") or {}
    candidate_id = str(candidate.get("candidate_id") or "")
    source_unit_ids = candidate.get("evidence_unit_ids") or []
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": f"ku_simulation_therapist_case_memory_{stable_hash(candidate_id)}",
        "knowledge_type": "therapist_case_memory_pattern",
        "source": {
            "dataset": "Simulation.multisource",
            "source_id": candidate_id,
            "candidate_id": candidate_id,
            "case_id": candidate.get("source_case_id"),
            "source_unit_ids": source_unit_ids,
            "language": "zh",
            "source_construction_type": "llm_multisource_case_memory_abstraction",
            "evidence_tier": "multisource_therapist_case_memory_pattern",
            "citation": "Derived from SoulChat-R1 memory pattern bundles and CPsyCounD case-level process knowledge.",
        },
        "content": {"therapist_case_memory_pattern": pattern},
        "quality": {
            "review_status": "llm_extracted_unreviewed",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    "This unit is a multisource abstraction over prior knowledge units, not a verbatim dialogue artifact.",
                    "SoulChat-R1 contributes generic memory design evidence and should not be read as case-level observed fact.",
                    "Memory slots and rules express likely therapist working-memory patterns rather than deterministic protocol.",
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
                    " ".join(pattern.get("required_memory_slots") or []),
                    " ".join(pattern.get("problem_summary_slots") or []),
                    " ".join(pattern.get("resource_slots") or []),
                    " ".join(pattern.get("goal_slots") or []),
                    " ".join(pattern.get("alliance_slots") or []),
                    " ".join(pattern.get("risk_slots") or []),
                    " ".join(pattern.get("unfinished_thread_slots") or []),
                    " ".join(pattern.get("stage_tracking_slots") or []),
                    " ".join(pattern.get("memory_update_rules") or []),
                    " ".join(pattern.get("compression_rules") or []),
                ]
            )[:4000],
        },
        "provenance": {
            "projection": "llm_simulation_therapist_case_memory_extraction",
            "prompt_path": prompt_path,
            "prompt_audit": prompt_audit,
            "llm_calls": 1,
            "sampling": "one_candidate_per_call",
            "filtering": "deterministic_slot_harvesting_then_llm_abstraction",
            "deduplication": "none",
            "candidate_snapshot": {
                "candidate_id": candidate_id,
                "source_case_id": candidate.get("source_case_id"),
                "phase_sequence": candidate.get("phase_sequence") or [],
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

    if args.overwrite:
        for path in [output_path, failure_path, manifest_path, qa_path]:
            if Path(path).exists():
                Path(path).unlink()

    candidates = read_jsonl(args.candidates_jsonl)
    candidate_ids = list(args.candidate_ids or [])
    if args.candidate_id_file:
        with Path(args.candidate_id_file).open("r", encoding="utf-8") as f:
            candidate_ids.extend(line.strip() for line in f if line.strip())
    if candidate_ids:
        allow = set(candidate_ids)
        candidates = [row for row in candidates if str(row.get("candidate_id") or "") in allow]
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

    completed = completed_candidate_ids(output_path, failure_path, resume=args.resume)
    selected = [row for row in candidates if str(row.get("candidate_id") or "") not in completed]

    written_rows = 0
    failure_rows = 0
    status_counter = Counter()

    for candidate in selected:
        payload = build_payload(candidate)
        prompt_audit = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
        candidate_id = str(candidate.get("candidate_id") or "")

        try:
            result = chat_json_with_repair(
                client,
                system_prompt,
                payload,
                parse_retries=parse_retries,
                force_json_first=True,
            )
            unit = wrap_unit(candidate, result, prompt_audit, args.prompt)
            append_jsonl(output_path, unit)
            written_rows += 1
            status_counter["complete"] += 1
        except PromptBudgetExceededError as exc:
            append_jsonl(
                failure_path,
                {
                    "candidate_id": candidate_id,
                    "error_type": "PromptBudgetExceededError",
                    "details": exc.report,
                },
            )
            failure_rows += 1
            status_counter["prompt_budget_exceeded"] += 1
        except Exception as exc:  # noqa: BLE001
            append_jsonl(
                failure_path,
                {
                    "candidate_id": candidate_id,
                    "error_type": type(exc).__name__,
                    "details": str(exc),
                },
            )
            failure_rows += 1
            status_counter["failed"] += 1

    manifest = {
        "schema_version": "theraskill_therapist_case_memory_runner_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if failure_rows == 0 else "partial",
        "input_candidates": {
            "path": str(args.candidates_jsonl),
            "sha256": file_sha256(args.candidates_jsonl),
            "row_count": len(candidates),
            "selected_count": len(selected),
        },
        "output_units": {
            "path": str(args.output),
            "sha256": file_sha256(args.output),
            "row_count": len(safe_read_jsonl(args.output)),
        },
        "failure_log": {
            "path": str(args.failure_log),
            "sha256": file_sha256(args.failure_log),
            "row_count": len(safe_read_jsonl(args.failure_log)),
        },
        "model_config": {
            "path": args.model_config,
            "name": model_config.get("name"),
            "base_url": model_config.get("base_url"),
        },
        "prompt_path": args.prompt,
        "qa_report": str(args.qa_report),
    }
    write_json(manifest_path, manifest)

    qa = {
        "schema_version": "theraskill_therapist_case_memory_runner_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": manifest["status"],
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "written_rows": written_rows,
        "failure_count": failure_rows,
        "status_distribution": dict(status_counter.most_common()),
        "output_row_count": len(safe_read_jsonl(args.output)),
        "failure_row_count": len(safe_read_jsonl(args.failure_log)),
    }
    write_json(qa_path, qa)
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0 if failure_rows == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
