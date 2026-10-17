#!/usr/bin/env python3
"""Run LLM extraction for Simulation.therapist_response_policy_pattern."""

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
    REPO_ROOT,
    SIMULATION_EXTRACTION_FAMILY_SPECS,
    SIMULATION_EXTRACTION_RUNNER_MANIFEST_PATHS,
    SIMULATION_EXTRACTION_RUNNER_QA_PATHS,
    THERAPIST_RESPONSE_POLICY_EXTRACTION_PROMPT_PATH,
)

PROJECT_ROOT = REPO_ROOT
DEFAULT_SPEC = SIMULATION_EXTRACTION_FAMILY_SPECS["therapist_response_policy_pattern"]
DEFAULT_CANDIDATES = PROJECT_ROOT / DEFAULT_SPEC.candidates_jsonl
DEFAULT_PROMPT = THERAPIST_RESPONSE_POLICY_EXTRACTION_PROMPT_PATH
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_OUTPUT = PROJECT_ROOT / DEFAULT_SPEC.canonical_output
DEFAULT_RUNNER_MANIFEST = SIMULATION_EXTRACTION_RUNNER_MANIFEST_PATHS["therapist_response_policy_pattern"]
DEFAULT_RUNNER_QA = SIMULATION_EXTRACTION_RUNNER_QA_PATHS["therapist_response_policy_pattern"]
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
    value = " ".join(str(text or "").split())
    return value[:limit] if limit is not None else value


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def trim_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "presenting_problem_signals": [clean_text(x, 120) for x in (state.get("presenting_problem_signals") or [])[:6]],
        "emotion_signals": [clean_text(x, 120) for x in (state.get("emotion_signals") or [])[:5]],
        "alliance_signals": [clean_text(x, 120) for x in (state.get("alliance_signals") or [])[:5]],
        "risk_signals": [clean_text(x, 120) for x in (state.get("risk_signals") or [])[:5]],
        "readiness_signals": [clean_text(x, 120) for x in (state.get("readiness_signals") or [])[:5]],
        "resistance_signals": [clean_text(x, 120) for x in (state.get("resistance_signals") or [])[:5]],
    }


def build_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "extract_therapist_response_policy_pattern",
        "candidate_id": candidate.get("candidate_id"),
        "source_family": candidate.get("source_family"),
        "therapeutic_orientation_hint": candidate.get("therapeutic_orientation_hint") or [],
        "triggering_client_state": trim_state(candidate.get("triggering_client_state") or {}),
        "stage_constraints_hint": candidate.get("stage_constraints") or [],
        "alliance_constraints_hint": candidate.get("alliance_constraints") or [],
        "risk_constraints_hint": candidate.get("risk_constraints") or [],
        "primary_response_goal_hint": candidate.get("primary_response_goal_hint") or [],
        "preferred_move_types_hint": candidate.get("preferred_move_types_hint") or [],
        "preferred_response_structure_hint": candidate.get("preferred_response_structure_hint") or [],
        "tone_style_constraints_hint": candidate.get("tone_style_constraints_hint") or [],
        "things_to_avoid_hint": candidate.get("things_to_avoid_hint") or [],
        "repair_or_escalation_conditions_hint": candidate.get("repair_or_escalation_conditions_hint") or [],
        "applicable_context_hint": candidate.get("applicable_context_hint") or [],
        "non_applicable_context_hint": candidate.get("non_applicable_context_hint") or [],
        "evidence_refs": candidate.get("evidence_refs") or {},
        "source_unit_ids": candidate.get("source_unit_ids") or [],
        "provenance": candidate.get("provenance") or {},
        "constraints": {
            "no_invented_clinical_indications": True,
            "must_keep_style_and_indication_separate": True,
            "public_support_sources_not_equal_clinical_fidelity": True,
            "must_preserve_uncertainty_if_evidence_is_weak": True,
        },
    }


def wrap_unit(candidate: dict[str, Any], extracted: dict[str, Any], prompt_audit: dict[str, Any], prompt_path: str) -> dict[str, Any]:
    policy = extracted.get("response_policy_pattern") or {}
    candidate_id = str(candidate.get("candidate_id") or "")
    source_unit_ids = candidate.get("source_unit_ids") or []
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": f"ku_simulation_therapist_response_policy_{stable_hash(candidate_id)}",
        "knowledge_type": "therapist_response_policy_pattern",
        "source": {
            "dataset": "Simulation.multisource",
            "source_id": candidate_id,
            "candidate_id": candidate_id,
            "case_id": candidate.get("source_case_id"),
            "source_family": candidate.get("source_family"),
            "source_unit_ids": source_unit_ids,
            "language": "zh",
            "source_construction_type": "llm_multisource_response_policy_abstraction",
            "evidence_tier": "multisource_therapist_response_policy_pattern",
            "citation": "Derived from CPsyCounD, AnnoMI, ESConv, Cognitive-Reframing, and PsyQA raw knowledge units.",
        },
        "content": {"response_policy_pattern": policy},
        "quality": {
            "review_status": "llm_extracted_unreviewed",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    "This is a multisource abstraction over prior knowledge units, not a direct observed dialogue record.",
                    "Public support or QA sources should not be read as direct clinical fidelity evidence.",
                    "Patterns may express likely response policies rather than deterministic rules.",
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
                    " ".join(policy.get("therapeutic_orientation") or []),
                    " ".join((policy.get("triggering_client_state") or {}).get("presenting_problem_signals") or []),
                    " ".join((policy.get("triggering_client_state") or {}).get("emotion_signals") or []),
                    " ".join(policy.get("stage_constraints") or []),
                    " ".join(policy.get("risk_constraints") or []),
                    " ".join(policy.get("primary_response_goal") or []),
                    " ".join(policy.get("preferred_move_types") or []),
                    " ".join(policy.get("preferred_response_structure") or []),
                    " ".join(policy.get("things_to_avoid") or []),
                    " ".join(policy.get("applicable_context") or []),
                ]
            )[:4000],
        },
        "provenance": {
            "projection": "llm_simulation_therapist_response_policy_extraction",
            "prompt_path": prompt_path,
            "prompt_audit": prompt_audit,
            "llm_calls": 1,
            "sampling": "one_candidate_per_call",
            "filtering": "deterministic_candidate_builder_then_llm_abstraction",
            "deduplication": "none",
            "candidate_snapshot": {
                "candidate_id": candidate_id,
                "source_family": candidate.get("source_family"),
                "source_case_id": candidate.get("source_case_id"),
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
        prompt_audit["compact_target_exceeded"] = prompt_audit["estimated_prompt_tokens"] > args.compact_target_prompt_tokens

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
        "schema_version": "theraskill_simulation_therapist_response_policy_runner_manifest_v1",
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
            "This runner emits raw knowledge units only; projection QA/index should be rebuilt by build_therapist_response_policy_projection_manifest.py.",
            "Public support or QA source evidence should not be promoted into direct clinical fidelity claims.",
        ],
    }
    write_json(manifest_path, manifest)

    qa = {
        "schema_version": "theraskill_simulation_therapist_response_policy_runner_qa_v1",
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
