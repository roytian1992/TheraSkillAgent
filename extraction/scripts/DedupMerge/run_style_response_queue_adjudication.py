#!/usr/bin/env python3
"""Run full cluster-level LLM adjudication for style-response queue items."""

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


DEFAULT_QUEUE = "outputs/knowledge/dedup/adjudication_queues/style_response/style_response.adjudication_queue.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/adjudications/style_response_queue"
DEFAULT_MODEL_CONFIGS = (
    "configs/model_config.yaml,"
    "configs/model_config.yaml"
)
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/style_response_queue_adjudication_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/style_response_queue_adjudication_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/style_response_queue_adjudication_qa.json"
PROMPT_VERSION = "style_response_cluster_adjudication_v1"


SYSTEM_PROMPT = """You adjudicate whether one cluster of therapeutic support response examples should be treated as one reusable style-response knowledge cluster.

Return exactly one compact JSON object. Do not write prose outside JSON. Do not reveal chain-of-thought.

Important boundaries:
- This is cluster-level adjudication only. Do not create final merged knowledge.
- Use only the provided response examples and cluster metadata. Do not infer facts that are not in the evidence.
- Source IDs and hashes are runner metadata, not semantic evidence.
- These are support-response examples, not instructions to give clinical advice.
- Clinical risk / crisis referral examples may be clustered only when the response intent and boundary handling are materially the same.
- Generic, empty, malformed, or too-broad clusters should be deferred or split, not merged.

Decision labels:
- merge_equivalent: examples express the same reusable response pattern with no material semantic difference.
- split_needed: examples share a broad topic but contain materially different actions, contexts, boundaries, or intended use.
- defer_generic: examples are too generic/template-like to become a useful merged cluster.
- defer_quality_issue: evidence text is empty, malformed, or insufficient for reliable adjudication.

Required JSON schema:
{
  "cluster_decision": "merge_equivalent" | "split_needed" | "defer_generic" | "defer_quality_issue",
  "merge_confidence": "high" | "medium" | "low",
  "canonical_pattern_label": "short neutral label, or null",
  "shared_features": ["features grounded in the examples"],
  "material_differences": ["differences that matter for merge/split decisions"],
  "recommended_subsplit_cues": ["short cues if split_needed, otherwise []"],
  "evidence_example_roles": [
    {"local_example_id": "E1", "role": "core_evidence" | "variant" | "outlier" | "insufficient_text", "reason": "short evidence-grounded reason"}
  ],
  "source_faithfulness_notes": "one sentence about whether the decision is directly supported by the examples",
  "recommended_next_action": "build_review_cluster" | "split_by_cues" | "defer_generic" | "defer_quality_issue"
}
"""


_THREAD_LOCAL = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default=DEFAULT_QUEUE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--model-configs", default=DEFAULT_MODEL_CONFIGS)
    parser.add_argument("--workers-per-config", type=int, default=32)
    parser.add_argument("--max-items", type=int, help="Debug/development limit. Omit for full queue.")
    parser.add_argument("--max-evidence-examples", type=int, default=8)
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


def prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


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


def local_example(example: dict[str, Any], index: int) -> dict[str, Any]:
    response = str(example.get("response_preview") or "").strip()
    context = str(example.get("context_preview") or "").strip()
    dedup_text = str(example.get("dedup_text_preview") or "").strip()
    return {
        "local_example_id": f"E{index}",
        "dataset": example.get("dataset"),
        "knowledge_type": example.get("knowledge_type"),
        "context": context,
        "response": response,
        "dedup_text_hint": dedup_text if not response else "",
    }


def build_payload(row: dict[str, Any], index: int, max_evidence_examples: int) -> dict[str, Any]:
    key = row.get("v2_refinement_key") or {}
    examples = row.get("evidence_examples") or []
    local_examples = [
        local_example(example, example_index)
        for example_index, example in enumerate(examples[:max_evidence_examples], start=1)
    ]
    return {
        "task": "style_response_cluster_level_adjudication",
        "local_cluster_id": f"C{index}",
        "cluster_metadata": {
            "member_count": row.get("member_count"),
            "evidence_example_count": len(local_examples),
            "dataset": key.get("dataset"),
            "language": key.get("language"),
            "knowledge_type": key.get("knowledge_type"),
            "response_function": key.get("response_function"),
            "primary_action_topic": key.get("primary_action_topic"),
            "all_action_topics": key.get("all_action_topics"),
            "style_label_signature": key.get("style_label_signature"),
            "modal_signature": key.get("modal_signature"),
            "response_length_bucket": key.get("response_length_bucket"),
            "sentence_shape": key.get("sentence_shape"),
            "has_context": key.get("has_context"),
            "size_bucket": row.get("size_bucket"),
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


def normalize_decision(result: dict[str, Any], expected_example_ids: set[str]) -> dict[str, Any]:
    allowed_decisions = {"merge_equivalent", "split_needed", "defer_generic", "defer_quality_issue"}
    allowed_confidence = {"high", "medium", "low"}
    allowed_actions = {"build_review_cluster", "split_by_cues", "defer_generic", "defer_quality_issue"}
    allowed_roles = {"core_evidence", "variant", "outlier", "insufficient_text"}

    if result.get("cluster_decision") not in allowed_decisions:
        result["cluster_decision"] = "defer_quality_issue"
    if result.get("merge_confidence") not in allowed_confidence:
        result["merge_confidence"] = "low"
    if result.get("recommended_next_action") not in allowed_actions:
        decision = result.get("cluster_decision")
        result["recommended_next_action"] = {
            "merge_equivalent": "build_review_cluster",
            "split_needed": "split_by_cues",
            "defer_generic": "defer_generic",
            "defer_quality_issue": "defer_quality_issue",
        }.get(decision, "defer_quality_issue")
    if not isinstance(result.get("shared_features"), list):
        result["shared_features"] = []
    if not isinstance(result.get("material_differences"), list):
        result["material_differences"] = []
    if not isinstance(result.get("recommended_subsplit_cues"), list):
        result["recommended_subsplit_cues"] = []

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
                "reason": str(role.get("reason") or "")[:500],
            }
        )
    result["evidence_example_roles"] = normalized_roles
    if result.get("canonical_pattern_label") is not None:
        result["canonical_pattern_label"] = str(result.get("canonical_pattern_label"))[:160]
    result["source_faithfulness_notes"] = str(result.get("source_faithfulness_notes") or "")[:600]
    return result


def adjudicate_one(
    *,
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
    payload = build_payload(row, index, max_evidence_examples)
    example_ids = {example["local_example_id"] for example in payload["evidence_examples"]}
    budget = prompt_budget_report(SYSTEM_PROMPT, payload, max_prompt_tokens)
    model_name = (configs[config_index].get("model") or {}).get("name")
    try:
        client = get_client(config_index, configs)
        result = chat_json_with_repair(
            client,
            SYSTEM_PROMPT,
            payload,
            parse_retries=parse_retries,
            max_prompt_tokens=max_prompt_tokens,
            force_json_first=True,
        )
        result = normalize_decision(result, example_ids)
        status = "complete"
        error = None
    except Exception as exc:  # noqa: BLE001
        result = {}
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "schema_version": "theraskill_style_response_cluster_adjudication_v1",
        "queue_item_id": row.get("queue_item_id"),
        "status": status,
        "error": error,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_hash(),
        "model_config": config_paths[config_index],
        "model": model_name,
        "runtime_seconds": round(time.time() - started, 3),
        "prompt_budget": budget,
        "parent_component_id": row.get("parent_component_id"),
        "v1_subcomponent_id": row.get("v1_subcomponent_id"),
        "v2_subcomponent_id": row.get("v2_subcomponent_id"),
        "member_count": row.get("member_count"),
        "evidence_example_count": len(payload["evidence_examples"]),
        "v2_refinement_key": row.get("v2_refinement_key"),
        "llm_decision": result,
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
    }


def state_payload(
    *,
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
        "schema_version": "theraskill_style_response_queue_adjudication_progress_v1",
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
        "final_merge_output": False,
    }


def main() -> int:
    args = parse_args()
    started_at = utc_now()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "style_response.queue_adjudication.jsonl"
    failure_path = output_dir / "style_response.queue_adjudication_failures.jsonl"
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

    rows = load_queue(args.queue, args.max_items)
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
        status="running",
        queue_path=args.queue,
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
                        "schema_version": "theraskill_style_response_cluster_adjudication_failure_v1",
                        "queue_item_id": record.get("queue_item_id"),
                        "error": record.get("error"),
                        "model_config": record.get("model_config"),
                        "updated_at": utc_now(),
                    },
                )
                counters["failed"] += 1

            if processed_this_run % max(1, args.progress_every) == 0 or processed_this_run == len(pending):
                progress = state_payload(
                    status="running",
                    queue_path=args.queue,
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
        "schema_version": "theraskill_style_response_queue_adjudication_report_v1",
        "created_at": utc_now(),
        "status": status,
        "queue": {
            "path": args.queue,
            "sha256": file_sha256(args.queue),
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

    write_json(args.report, report)
    write_json(args.manifest, report)
    write_json(args.qa_report, report)
    final_progress = state_payload(
        status=status,
        queue_path=args.queue,
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
    print(json.dumps({"event": "finished", "status": status, "counts": report["counts"], "report": args.report}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
