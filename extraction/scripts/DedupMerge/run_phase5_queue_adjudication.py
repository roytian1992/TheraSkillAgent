#!/usr/bin/env python3
"""Run Phase 5 full cluster-level LLM adjudication queues.

This writes dedup intermediate decisions only. It does not create merge
candidates or final merged knowledge units.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    chat_json_with_repair,
    context_max_prompt_tokens,
    load_yaml,
    parse_retry_attempts,
    prompt_budget_report,
)
from io_utils import file_sha256, write_json  # noqa: E402


DEFAULT_MODEL_CONFIGS = (
    "configs/model_config.yaml,"
    "configs/model_config.yaml"
)

FAMILY_CONFIG = {
    "clinical_risk_assessment": {
        "queue": "outputs/knowledge/dedup/adjudication_queues/clinical_risk_assessment/clinical_risk_assessment.adjudication_queue.jsonl",
        "output_dir": "outputs/knowledge/dedup/adjudications/clinical_risk_assessment_queue",
        "output_name": "clinical_risk_assessment.queue_adjudication.jsonl",
        "failure_name": "clinical_risk_assessment.queue_adjudication_failures.jsonl",
        "report": "outputs/knowledge/dedup/reports/clinical_risk_assessment_queue_adjudication_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_queue_adjudication_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_queue_adjudication_qa.json",
        "prompt_version": "clinical_risk_assessment_cluster_adjudication_v1",
        "schema_version": "theraskill_clinical_risk_assessment_cluster_adjudication_v1",
    },
    "therapy_process": {
        "queue": "outputs/knowledge/dedup/adjudication_queues/therapy_process/therapy_process.adjudication_queue.jsonl",
        "output_dir": "outputs/knowledge/dedup/adjudications/therapy_process_queue",
        "output_name": "therapy_process.queue_adjudication.jsonl",
        "failure_name": "therapy_process.queue_adjudication_failures.jsonl",
        "report": "outputs/knowledge/dedup/reports/therapy_process_queue_adjudication_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/therapy_process_queue_adjudication_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/therapy_process_queue_adjudication_qa.json",
        "prompt_version": "therapy_process_cluster_adjudication_v1",
        "schema_version": "theraskill_therapy_process_cluster_adjudication_v1",
    },
}

CLINICAL_SYSTEM_PROMPT = """You adjudicate whether one cluster of clinical risk, symptom-severity, or assessment knowledge units should become one reviewed deduplication cluster.

Return exactly one compact JSON object. Do not write prose outside JSON. Do not reveal chain-of-thought.

Important boundaries:
- This is cluster-level deduplication adjudication only. Do not create final merged knowledge and do not change clinical labels.
- Clinical risk content is allowed research evidence here. "risk" means clinical content such as suicide/self-harm risk, symptom severity, crisis boundary, or risk handling.
- Data or merge quality problems must be described as quality_consistency_issue, label_conflict, schema_inconsistency, merge_hazard, source_governance_caveat, or clinical_label_consistency_review_required. Do not use generic "risk" for data quality.
- Use only the provided clinical text, labels, explanation previews, and metadata. Generated explanations are rationale hints, not observed client evidence unless the source says otherwise.
- Merge only when the label/construct, severity/risk domain, evidence basis, and source constraints are materially compatible.
- Split when examples share a broad label but differ in clinical construct, risk/severity boundary, or evidence basis.
- Defer empty, label-only, malformed, or source-incompatible clusters.

Decision labels:
- merge_equivalent: examples are near-duplicate or semantically equivalent clinical assessment knowledge.
- split_needed: examples are related but have materially different labels, risk/severity boundaries, constructs, or source evidence.
- defer_generic: examples are label-only, generic, or too broad for a useful reviewed cluster.
- defer_quality_issue: evidence text is empty, malformed, or insufficient for reliable adjudication.

Required JSON schema:
{
  "cluster_decision": "merge_equivalent" | "split_needed" | "defer_generic" | "defer_quality_issue",
  "merge_confidence": "high" | "medium" | "low",
  "canonical_label_or_construct": "short neutral evidence-grounded label, or null",
  "clinical_label_consistency": "consistent" | "mixed_or_conflicting" | "unclear",
  "severity_or_risk_domain_consistency": "consistent" | "mixed_or_conflicting" | "not_applicable" | "unclear",
  "shared_construct": "short description of the shared construct, or null",
  "shared_evidence_basis": ["evidence basis shared by examples"],
  "material_differences": ["differences that matter for merge/split decisions"],
  "recommended_subsplit_cues": ["short cues if split_needed, otherwise []"],
  "source_or_evidence_constraints": ["constraints that must remain attached, or []"],
  "evidence_example_roles": [
    {"local_example_id": "E1", "role": "core_evidence" | "variant" | "outlier" | "insufficient_text", "reason": "short evidence-grounded reason"}
  ],
  "source_faithfulness_notes": "one sentence about whether the decision is directly supported by the examples",
  "recommended_next_action": "build_review_cluster" | "split_by_cues" | "defer_generic" | "defer_quality_issue"
}
"""

THERAPY_SYSTEM_PROMPT = """You adjudicate whether one cluster of therapy-process knowledge units should become one reviewed deduplication cluster.

Return exactly one compact JSON object. Do not write prose outside JSON. Do not reveal chain-of-thought.

Important boundaries:
- This is cluster-level deduplication adjudication only. Do not create final merged knowledge and do not rewrite therapeutic process semantics.
- Use only the provided process examples and metadata. Source IDs and hashes are runner metadata, not semantic evidence.
- Merge only when the client state signature, therapist move function/type, process goal, and observed client response/transition are materially the same.
- Split when examples share a broad move label but differ in state, move sequence, client response, risk boundary, or source evidence.
- Defer generic/no-sequence/malformed clusters or clusters whose examples are too incomplete for reliable process-pattern adjudication.
- Clinical risk in examples means clinical content; data/merge quality issues must be named as quality_consistency_issue, schema_inconsistency, merge_hazard, or similar explicit terms.

Decision labels:
- merge_equivalent: examples describe the same reusable therapy-process pattern.
- split_needed: examples are related but contain materially different states, moves, response transitions, or boundaries.
- defer_generic: examples are too generic/no-sequence/template-like to become a useful reviewed cluster.
- defer_quality_issue: evidence text is empty, malformed, or insufficient for reliable adjudication.

Required JSON schema:
{
  "cluster_decision": "merge_equivalent" | "split_needed" | "defer_generic" | "defer_quality_issue",
  "merge_confidence": "high" | "medium" | "low",
  "canonical_process_label": "short neutral label, or null",
  "process_equivalence": "same_process_pattern" | "related_but_distinct" | "unclear",
  "shared_state_move_response_pattern": "short evidence-grounded description, or null",
  "state_consistency": "consistent" | "mixed_or_conflicting" | "unclear",
  "move_sequence_consistency": "consistent" | "mixed_or_conflicting" | "unclear",
  "client_response_consistency": "consistent" | "mixed_or_conflicting" | "unclear",
  "shared_features": ["features grounded in the examples"],
  "material_differences": ["differences that matter for merge/split decisions"],
  "recommended_subsplit_cues": ["short cues if split_needed, otherwise []"],
  "source_or_evidence_constraints": ["constraints that must remain attached, or []"],
  "evidence_example_roles": [
    {"local_example_id": "E1", "role": "core_evidence" | "variant" | "outlier" | "insufficient_text", "reason": "short evidence-grounded reason"}
  ],
  "source_faithfulness_notes": "one sentence about whether the decision is directly supported by the examples",
  "recommended_next_action": "build_review_cluster" | "split_by_cues" | "defer_generic" | "defer_quality_issue"
}
"""

SYSTEM_PROMPTS = {
    "clinical_risk_assessment": CLINICAL_SYSTEM_PROMPT,
    "therapy_process": THERAPY_SYSTEM_PROMPT,
}

_THREAD_LOCAL = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=sorted(FAMILY_CONFIG), required=True)
    parser.add_argument("--queue")
    parser.add_argument("--output-dir")
    parser.add_argument("--report")
    parser.add_argument("--manifest")
    parser.add_argument("--qa-report")
    parser.add_argument("--model-configs", default=DEFAULT_MODEL_CONFIGS)
    parser.add_argument("--workers-per-config", type=int, default=32)
    parser.add_argument("--max-items", type=int, help="Debug/development limit. Omit for full queue.")
    parser.add_argument("--max-evidence-examples", type=int, default=12)
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--resume", dest="resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def prompt_hash(family: str) -> str:
    return hashlib.sha256(SYSTEM_PROMPTS[family].encode("utf-8")).hexdigest()


def load_queue(path: str | Path, max_items: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if max_items is not None and len(rows) >= max_items:
                break
    return rows


def existing_statuses(output: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    if not output.exists():
        return statuses
    with output.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                statuses[f"__invalid_line_{line_no}"] = "invalid_jsonl"
                continue
            queue_item_id = row.get("queue_item_id")
            if queue_item_id:
                statuses[str(queue_item_id)] = str(row.get("status") or "unknown")
    return statuses


def trim_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\u0000", " ").split())[:limit]


def trim_list(values: Any, limit: int = 12, item_chars: int = 260) -> list[str]:
    if not isinstance(values, list):
        return []
    return [trim_text(value, item_chars) for value in values[:limit]]


def local_example(family: str, example: dict[str, Any], index: int) -> dict[str, Any]:
    base = {
        "local_example_id": f"E{index}",
        "dataset": example.get("dataset"),
        "knowledge_type": example.get("knowledge_type"),
        "evidence_tier": example.get("evidence_tier"),
        "source_construction_type": example.get("source_construction_type"),
        "clinical_content_flags": trim_list(example.get("risk_or_safety_flags")),
        "license_constraints": trim_list(example.get("license_constraints")),
        "dedup_text_hint": trim_text(example.get("dedup_text_preview"), 700),
    }
    if family == "clinical_risk_assessment":
        base.update(
            {
                "clinical_text": trim_text(example.get("clinical_text_preview"), 900),
                "label": trim_text(example.get("label_preview"), 260),
            }
        )
    else:
        base.update(
            {
                "process_text": trim_text(example.get("process_text_preview"), 1000),
            }
        )
    return base


def build_payload(family: str, row: dict[str, Any], index: int, max_evidence_examples: int) -> dict[str, Any]:
    metadata = row.get("component_metadata") or {}
    examples = row.get("evidence_examples") or []
    local_examples = [
        local_example(family, example, example_index)
        for example_index, example in enumerate(examples[:max_evidence_examples], start=1)
    ]
    return {
        "task": f"{family}_cluster_level_adjudication",
        "local_cluster_id": f"P5_{index}",
        "cluster_metadata": {
            "member_count": row.get("member_count"),
            "evidence_example_count": len(local_examples),
            "source_kind": row.get("source_kind"),
            "source_component_id": row.get("source_component_id"),
            "refinement_key": row.get("refinement_key"),
            "dataset_distribution": metadata.get("dataset_distribution"),
            "knowledge_type_distribution": metadata.get("knowledge_type_distribution"),
            "evidence_tier_distribution": metadata.get("evidence_tier_distribution"),
            "source_construction_type_distribution": metadata.get("source_construction_type_distribution"),
            "min_edge_cosine_similarity": metadata.get("min_edge_cosine_similarity"),
            "max_edge_cosine_similarity": metadata.get("max_edge_cosine_similarity"),
            "edge_count": metadata.get("edge_count"),
            "cross_dataset_component": metadata.get("cross_dataset_component"),
            "text_quality_summary": row.get("text_quality_summary") or {},
        },
        "evidence_examples": local_examples,
    }


def get_client(config_index: int, configs: list[dict[str, Any]]) -> OpenAICompatibleClient:
    clients = getattr(_THREAD_LOCAL, "clients", None)
    if clients is None:
        clients = {}
        _THREAD_LOCAL.clients = clients
    if config_index not in clients:
        clients[config_index] = OpenAICompatibleClient(configs[config_index]["model"])
    return clients[config_index]


def normalize_common(result: dict[str, Any], expected_example_ids: set[str]) -> dict[str, Any]:
    allowed_decisions = {"merge_equivalent", "split_needed", "defer_generic", "defer_quality_issue"}
    allowed_confidence = {"high", "medium", "low"}
    allowed_actions = {"build_review_cluster", "split_by_cues", "defer_generic", "defer_quality_issue"}
    allowed_roles = {"core_evidence", "variant", "outlier", "insufficient_text"}

    if result.get("cluster_decision") not in allowed_decisions:
        result["cluster_decision"] = "defer_quality_issue"
    if result.get("merge_confidence") not in allowed_confidence:
        result["merge_confidence"] = "low"
    if result.get("recommended_next_action") not in allowed_actions:
        result["recommended_next_action"] = {
            "merge_equivalent": "build_review_cluster",
            "split_needed": "split_by_cues",
            "defer_generic": "defer_generic",
            "defer_quality_issue": "defer_quality_issue",
        }.get(result.get("cluster_decision"), "defer_quality_issue")

    for key in [
        "shared_evidence_basis",
        "shared_features",
        "material_differences",
        "recommended_subsplit_cues",
        "source_or_evidence_constraints",
    ]:
        if not isinstance(result.get(key), list):
            result[key] = []
        result[key] = [trim_text(value, 500) for value in result[key][:20]]

    roles = result.get("evidence_example_roles")
    if not isinstance(roles, list):
        roles = []
    normalized_roles = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        local_id = role.get("local_example_id")
        if local_id not in expected_example_ids:
            continue
        role_name = role.get("role")
        if role_name not in allowed_roles:
            role_name = "variant"
        normalized_roles.append(
            {
                "local_example_id": local_id,
                "role": role_name,
                "reason": trim_text(role.get("reason"), 500),
            }
        )
    result["evidence_example_roles"] = normalized_roles
    result["source_faithfulness_notes"] = trim_text(result.get("source_faithfulness_notes"), 700)
    return result


def normalize_decision(family: str, result: dict[str, Any], expected_example_ids: set[str]) -> dict[str, Any]:
    result = normalize_common(result, expected_example_ids)
    if family == "clinical_risk_assessment":
        for key in ["canonical_label_or_construct", "shared_construct"]:
            if result.get(key) is not None:
                result[key] = trim_text(result.get(key), 260) or None
        allowed_label = {"consistent", "mixed_or_conflicting", "unclear"}
        allowed_severity = {"consistent", "mixed_or_conflicting", "not_applicable", "unclear"}
        if result.get("clinical_label_consistency") not in allowed_label:
            result["clinical_label_consistency"] = "unclear"
        if result.get("severity_or_risk_domain_consistency") not in allowed_severity:
            result["severity_or_risk_domain_consistency"] = "unclear"
    else:
        if result.get("canonical_process_label") is not None:
            result["canonical_process_label"] = trim_text(result.get("canonical_process_label"), 260) or None
        if result.get("shared_state_move_response_pattern") is not None:
            result["shared_state_move_response_pattern"] = trim_text(result.get("shared_state_move_response_pattern"), 600) or None
        allowed_equivalence = {"same_process_pattern", "related_but_distinct", "unclear"}
        allowed_consistency = {"consistent", "mixed_or_conflicting", "unclear"}
        if result.get("process_equivalence") not in allowed_equivalence:
            result["process_equivalence"] = "unclear"
        for key in ["state_consistency", "move_sequence_consistency", "client_response_consistency"]:
            if result.get(key) not in allowed_consistency:
                result[key] = "unclear"
    return result


def adjudicate_one(
    *,
    family: str,
    row: dict[str, Any],
    index: int,
    config_index: int,
    configs: list[dict[str, Any]],
    config_paths: list[str],
    parse_retries: int,
    max_prompt_tokens: int,
    max_evidence_examples: int,
) -> dict[str, Any]:
    started = time.time()
    payload = build_payload(family, row, index, max_evidence_examples)
    example_ids = {example["local_example_id"] for example in payload["evidence_examples"]}
    system_prompt = SYSTEM_PROMPTS[family]
    budget = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
    model_name = (configs[config_index].get("model") or {}).get("name")
    try:
        client = get_client(config_index, configs)
        result = chat_json_with_repair(
            client,
            system_prompt,
            payload,
            parse_retries=parse_retries,
            max_prompt_tokens=max_prompt_tokens,
            force_json_first=True,
        )
        result = normalize_decision(family, result, example_ids)
        status = "complete"
        error = None
    except Exception as exc:  # noqa: BLE001
        result = {}
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "schema_version": FAMILY_CONFIG[family]["schema_version"],
        "family": family,
        "queue_item_id": row.get("queue_item_id"),
        "status": status,
        "error": error,
        "prompt_version": FAMILY_CONFIG[family]["prompt_version"],
        "prompt_sha256": prompt_hash(family),
        "model_config": config_paths[config_index],
        "model": model_name,
        "runtime_seconds": round(time.time() - started, 3),
        "prompt_budget": budget,
        "source_item_id": row.get("source_item_id"),
        "source_component_id": row.get("source_component_id"),
        "source_kind": row.get("source_kind"),
        "member_unit_ids": row.get("member_unit_ids") or [],
        "member_count": row.get("member_count"),
        "evidence_example_count": len(payload["evidence_examples"]),
        "component_metadata": row.get("component_metadata"),
        "refinement_key": row.get("refinement_key"),
        "text_quality_summary": row.get("text_quality_summary"),
        "llm_decision": result,
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "formal_merge_candidates_output": False,
        "final_merge_output": False,
    }


def state_payload(
    *,
    family: str,
    status: str,
    queue_path: str,
    output_path: Path,
    total_queue_items: int,
    pending_this_run: int,
    existing_complete: int,
    existing_failed: int,
    completed_this_run: int,
    failed_this_run: int,
    skipped_this_run: int,
    started_at: str,
    model_config_paths: list[str],
    workers: int,
    is_limited_debug_run: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_phase5_queue_adjudication_progress_v1",
        "family": family,
        "status": status,
        "started_at": started_at,
        "updated_at": utc_now(),
        "queue_path": queue_path,
        "output_path": str(output_path),
        "total_queue_items": total_queue_items,
        "pending_this_run": pending_this_run,
        "existing_complete": existing_complete,
        "existing_failed": existing_failed,
        "completed_this_run": completed_this_run,
        "failed_this_run": failed_this_run,
        "skipped_this_run": skipped_this_run,
        "model_configs": model_config_paths,
        "workers": workers,
        "is_limited_debug_run": is_limited_debug_run,
        "raw_units_unchanged": True,
        "formal_merge_candidates_output": False,
        "final_merge_output": False,
    }


def main() -> int:
    args = parse_args()
    family = args.family
    defaults = FAMILY_CONFIG[family]
    started_at = utc_now()
    queue_path = args.queue or defaults["queue"]
    output_dir = Path(args.output_dir or defaults["output_dir"])
    report_path = args.report or defaults["report"]
    manifest_path = args.manifest or defaults["manifest"]
    qa_path = args.qa_report or defaults["qa"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / str(defaults["output_name"])
    failure_path = output_dir / str(defaults["failure_name"])
    progress_path = output_dir / "progress_state.json"

    if args.overwrite:
        for path in [output_path, failure_path, progress_path]:
            if path.exists():
                path.unlink()
    elif output_path.exists() and not args.resume:
        raise FileExistsError(f"output exists: {output_path}; pass --resume or --overwrite")

    config_paths = split_csv(args.model_configs)
    if not config_paths:
        raise ValueError("--model-configs must contain at least one config path")
    configs = [load_yaml(path) for path in config_paths]
    parse_retries = args.parse_retries
    if parse_retries is None:
        parse_retries = max(parse_retry_attempts({}, config) for config in configs)
    max_prompt_tokens = args.max_prompt_tokens
    if max_prompt_tokens is None:
        max_prompt_tokens = min(context_max_prompt_tokens({}, config) for config in configs)

    rows = load_queue(queue_path, args.max_items)
    statuses = existing_statuses(output_path) if args.resume else {}
    invalid_status_lines = sum(1 for value in statuses.values() if value == "invalid_jsonl")
    existing_complete = sum(1 for value in statuses.values() if value == "complete")
    existing_failed = sum(1 for value in statuses.values() if value == "failed")
    skip_statuses = {"complete"}
    if not args.retry_failed:
        skip_statuses.add("failed")
    pending: list[tuple[int, dict[str, Any]]] = []
    skipped_this_run = 0
    for index, row in enumerate(rows, start=1):
        status = statuses.get(str(row.get("queue_item_id")))
        if status in skip_statuses:
            skipped_this_run += 1
            continue
        pending.append((index, row))

    total_workers = max(1, int(args.workers_per_config) * len(configs))
    is_limited = args.max_items is not None
    progress = state_payload(
        family=family,
        status="running",
        queue_path=queue_path,
        output_path=output_path,
        total_queue_items=len(rows),
        pending_this_run=len(pending),
        existing_complete=existing_complete,
        existing_failed=existing_failed,
        completed_this_run=0,
        failed_this_run=0,
        skipped_this_run=skipped_this_run,
        started_at=started_at,
        model_config_paths=config_paths,
        workers=total_workers,
        is_limited_debug_run=is_limited,
    )
    if invalid_status_lines:
        progress["invalid_existing_output_lines"] = invalid_status_lines
    write_json(progress_path, progress)

    counters: Counter[str] = Counter()
    completed_this_run = 0
    failed_this_run = 0
    processed_this_run = 0
    print(
        json.dumps(
            {
                "event": "start",
                "family": family,
                "queue_items_loaded": len(rows),
                "pending_this_run": len(pending),
                "skipped_this_run": skipped_this_run,
                "workers": total_workers,
                "model_configs": config_paths,
                "output": str(output_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=total_workers) as executor:
        futures = []
        for pending_index, (queue_index, row) in enumerate(pending):
            config_index = pending_index % len(configs)
            futures.append(
                executor.submit(
                    adjudicate_one,
                    family=family,
                    row=row,
                    index=queue_index,
                    config_index=config_index,
                    configs=configs,
                    config_paths=config_paths,
                    parse_retries=parse_retries,
                    max_prompt_tokens=max_prompt_tokens,
                    max_evidence_examples=args.max_evidence_examples,
                )
            )
        for future in as_completed(futures):
            record = future.result()
            append_jsonl(output_path, record)
            processed_this_run += 1
            if record["status"] == "complete":
                completed_this_run += 1
                decision = (record.get("llm_decision") or {}).get("cluster_decision")
                action = (record.get("llm_decision") or {}).get("recommended_next_action")
                counters[f"decision::{decision}"] += 1
                counters[f"next::{action}"] += 1
            else:
                failed_this_run += 1
                append_jsonl(
                    failure_path,
                    {
                        "schema_version": "theraskill_phase5_cluster_adjudication_failure_v1",
                        "family": family,
                        "queue_item_id": record.get("queue_item_id"),
                        "source_component_id": record.get("source_component_id"),
                        "error": record.get("error"),
                        "model_config": record.get("model_config"),
                        "updated_at": utc_now(),
                    },
                )
                counters["failed"] += 1

            if processed_this_run % max(1, args.progress_every) == 0 or processed_this_run == len(pending):
                progress = state_payload(
                    family=family,
                    status="running",
                    queue_path=queue_path,
                    output_path=output_path,
                    total_queue_items=len(rows),
                    pending_this_run=len(pending),
                    existing_complete=existing_complete,
                    existing_failed=existing_failed,
                    completed_this_run=completed_this_run,
                    failed_this_run=failed_this_run,
                    skipped_this_run=skipped_this_run,
                    started_at=started_at,
                    model_config_paths=config_paths,
                    workers=total_workers,
                    is_limited_debug_run=is_limited,
                )
                write_json(progress_path, progress)
                print(json.dumps({"event": "progress", **progress}, ensure_ascii=False), flush=True)

    final_statuses = existing_statuses(output_path)
    final_complete = sum(1 for value in final_statuses.values() if value == "complete")
    final_failed = sum(1 for value in final_statuses.values() if value == "failed")
    expected_total = len(rows)
    status = "complete" if final_complete == expected_total and final_failed == 0 and not is_limited else "incomplete"
    report = {
        "schema_version": "theraskill_phase5_queue_adjudication_report_v1",
        "created_at": utc_now(),
        "family": family,
        "status": status,
        "queue": {
            "path": queue_path,
            "sha256": file_sha256(queue_path),
            "rows_loaded": len(rows),
            "is_limited_debug_run": is_limited,
        },
        "outputs": {
            "output_jsonl": {"path": str(output_path), "sha256": file_sha256(output_path)},
            "failure_jsonl": {"path": str(failure_path), "sha256": file_sha256(failure_path)},
            "progress_state": {"path": str(progress_path), "sha256": file_sha256(progress_path)},
        },
        "model_configs": [
            {
                "path": path,
                "sha256": file_sha256(path),
                "model": (config.get("model") or {}).get("name"),
                "base_url": (config.get("model") or {}).get("base_url"),
            }
            for path, config in zip(config_paths, configs)
        ],
        "runtime": {
            "started_at": started_at,
            "finished_at": utc_now(),
            "workers_per_config": args.workers_per_config,
            "total_workers": total_workers,
            "parse_retries": parse_retries,
            "max_prompt_tokens": max_prompt_tokens,
            "max_evidence_examples": args.max_evidence_examples,
            "resume": args.resume,
            "retry_failed": args.retry_failed,
        },
        "counts": {
            "expected_queue_items": expected_total,
            "existing_complete_before_run": existing_complete,
            "existing_failed_before_run": existing_failed,
            "skipped_this_run": skipped_this_run,
            "pending_this_run": len(pending),
            "completed_this_run": completed_this_run,
            "failed_this_run": failed_this_run,
            "final_complete": final_complete,
            "final_failed": final_failed,
            "final_accounted": final_complete + final_failed,
        },
        "decision_distribution_this_run": dict(counters.most_common()),
        "issues_count": final_failed + invalid_status_lines + (1 if is_limited else 0),
        "issues": [],
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    if final_failed:
        report["issues"].append({"issue": "failed_adjudication_items", "count": final_failed, "failure_path": str(failure_path)})
    if invalid_status_lines:
        report["issues"].append({"issue": "invalid_existing_output_lines", "count": invalid_status_lines})
    if is_limited:
        report["issues"].append({"issue": "limited_debug_run", "count": expected_total})

    write_json(report_path, report)
    write_json(manifest_path, report)
    write_json(qa_path, report)
    final_progress = state_payload(
        family=family,
        status=status,
        queue_path=queue_path,
        output_path=output_path,
        total_queue_items=len(rows),
        pending_this_run=len(pending),
        existing_complete=existing_complete,
        existing_failed=existing_failed,
        completed_this_run=completed_this_run,
        failed_this_run=failed_this_run,
        skipped_this_run=skipped_this_run,
        started_at=started_at,
        model_config_paths=config_paths,
        workers=total_workers,
        is_limited_debug_run=is_limited,
    )
    final_progress["final_complete"] = final_complete
    final_progress["final_failed"] = final_failed
    write_json(progress_path, final_progress)
    print(json.dumps({"event": "finished", "family": family, "status": status, "counts": report["counts"], "report": report_path}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
