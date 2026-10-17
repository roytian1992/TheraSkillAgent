#!/usr/bin/env python3
"""Run full LLM subgrouping for QA/psychoeducation split-needed clusters."""

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


DEFAULT_SPLIT_QUEUE = "outputs/knowledge/dedup/clusters/qa_psychoeducation_queue_review/qa_psychoeducation.split_needed_queue.jsonl"
DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_OUTPUT_DIR = "outputs/knowledge/dedup/adjudications/qa_psychoeducation_split_subgrouping"
DEFAULT_MODEL_CONFIGS = (
    "configs/model_config.yaml,"
    "configs/model_config.yaml"
)
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/qa_psychoeducation_split_subgrouping_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/qa_psychoeducation_split_subgrouping_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/qa_psychoeducation_split_subgrouping_qa.json"
PROMPT_VERSION = "qa_psychoeducation_split_subgrouping_v1"


SYSTEM_PROMPT = """You split one previously rejected mental-health QA / psychoeducation cluster into smaller, evidence-grounded subgroups.

Return exactly one compact JSON object. Do not write prose outside JSON. Do not reveal chain-of-thought.

Important boundaries:
- This is subgrouping only. Do not create final merged knowledge and do not write a new answer.
- Use only the provided QA/psychoeducation member texts, first-pass decision, and cluster metadata.
- Source IDs and hashes are runner metadata, not semantic evidence.
- Every provided member must be assigned exactly once, either to one subgroup or to defer_member_local_ids.
- A subgroup is valid only when its members share materially compatible question/need, answer intent, clinical boundary handling, and source/license constraints.
- Keep different questions, answer focus, professional-referral advice, crisis handling, or source/license constraints in separate subgroups.
- If no useful subgroup can be formed, return no subgroups and put all members in defer_member_local_ids.
- Clinical risk / crisis examples may be grouped only when the risk boundary and suggested action are materially the same.

Required JSON schema:
{
  "subgrouping_status": "subgroups_created" | "all_deferred" | "quality_issue",
  "subgroups": [
    {
      "subgroup_label": "short neutral label",
      "member_local_ids": ["M1", "M2"],
      "shared_question_or_need": "one sentence grounded in members",
      "shared_answer_intent": "one sentence grounded in members",
      "separation_reason": "why this subgroup is separate from other members",
      "license_or_source_notes": ["notes if relevant, otherwise []"],
      "clinical_boundary_notes": "short note if relevant, otherwise null",
      "confidence": "high" | "medium" | "low"
    }
  ],
  "defer_member_local_ids": ["M3"],
  "subsplit_cues_used": ["cue labels used from first-pass cues or evidence"],
  "quality_notes": ["empty/malformed/too-generic evidence notes, if any"],
  "source_faithfulness_notes": "one sentence about whether subgrouping is directly supported by provided member evidence"
}
"""


_THREAD_LOCAL = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split-queue", default=DEFAULT_SPLIT_QUEUE)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--model-configs", default=DEFAULT_MODEL_CONFIGS)
    parser.add_argument("--workers-per-config", type=int, default=32)
    parser.add_argument("--max-items", type=int, help="Debug/development limit. Omit for full split queue.")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--target-prompt-tokens", type=int, default=12000)
    parser.add_argument("--max-question-chars", type=int, default=180)
    parser.add_argument("--max-answer-chars", type=int, default=300)
    parser.add_argument("--max-structure-chars", type=int, default=120)
    parser.add_argument("--min-question-chars", type=int, default=30)
    parser.add_argument("--min-answer-chars", type=int, default=50)
    parser.add_argument("--min-structure-chars", type=int, default=30)
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


def load_split_queue(path: str | Path, max_items: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if max_items is not None and len(rows) >= max_items:
                break
    return rows


def load_needed_text_views(path: str | Path, needed_unit_ids: set[str]) -> dict[str, dict[str, Any]]:
    views: dict[str, dict[str, Any]] = {}
    remaining = set(needed_unit_ids)
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            unit_id = row.get("unit_id")
            if unit_id in remaining:
                views[str(unit_id)] = row
                remaining.remove(str(unit_id))
                if not remaining:
                    break
    return views


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


def compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\u0000", " ").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def member_text(
    unit_id: str,
    view: dict[str, Any] | None,
    split_row: dict[str, Any],
    max_question_chars: int,
    max_answer_chars: int,
    max_structure_chars: int,
) -> dict[str, Any]:
    evidence_by_id = {item.get("unit_id"): item for item in split_row.get("evidence_examples") or []}
    evidence = evidence_by_id.get(unit_id) or {}
    fields = (view or {}).get("dedup_key_fields") or {}
    question = fields.get("question") or fields.get("input") or evidence.get("question_preview") or ""
    answer = fields.get("answer") or fields.get("response") or fields.get("output") or evidence.get("answer_preview") or ""
    structure = fields.get("structure") or fields.get("answer_structure") or fields.get("response_structure") or evidence.get("structure_preview") or ""
    if not answer:
        answer = (view or {}).get("dedup_text") or evidence.get("dedup_text_preview") or ""
    record = {
        "src": "/".join(
            item
            for item in [
                str((view or {}).get("dataset") or evidence.get("dataset") or ""),
                str((view or {}).get("knowledge_type") or evidence.get("knowledge_type") or ""),
            ]
            if item
        ),
        "q": compact_text(question, max_question_chars),
        "a": compact_text(answer, max_answer_chars),
        "s": compact_text(structure, max_structure_chars),
        "lic": (view or {}).get("license_constraints") or evidence.get("license_constraints") or [],
        "flags": (view or {}).get("risk_or_safety_flags") or evidence.get("clinical_content_flags") or [],
    }
    return {key: value for key, value in record.items() if value not in (None, "", [], {})}


def build_payload(
    row: dict[str, Any],
    index: int,
    text_views: dict[str, dict[str, Any]],
    max_question_chars: int,
    max_answer_chars: int,
    max_structure_chars: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    member_ids = [str(unit_id) for unit_id in row.get("member_unit_ids") or []]
    local_to_unit: dict[str, str] = {}
    members: list[dict[str, Any]] = []
    for member_index, unit_id in enumerate(member_ids, start=1):
        local_id = f"M{member_index}"
        local_to_unit[local_id] = unit_id
        member = member_text(unit_id, text_views.get(unit_id), row, max_question_chars, max_answer_chars, max_structure_chars)
        member["id"] = local_id
        members.append(member)
    decision = row.get("llm_decision") or {}
    metadata = row.get("component_metadata") or {}
    return (
        {
            "task": "qa_psychoeducation_split_needed_subgrouping",
            "local_cluster_id": f"QSplit{index}",
            "cluster_metadata": {
                "queue_item_id": row.get("queue_item_id"),
                "member_count": row.get("member_count"),
                "dataset_distribution": metadata.get("dataset_distribution"),
                "knowledge_type_distribution": metadata.get("knowledge_type_distribution"),
                "evidence_tier_distribution": metadata.get("evidence_tier_distribution"),
                "clinical_content_flag_distribution": (row.get("text_quality_summary") or {}).get("clinical_content_flag_distribution"),
                "license_constraint_distribution": (row.get("text_quality_summary") or {}).get("license_constraint_distribution"),
            },
            "first_pass_decision": {
                "canonical_topic_label": decision.get("canonical_topic_label"),
                "shared_question_or_need": decision.get("shared_question_or_need"),
                "shared_answer_intent": decision.get("shared_answer_intent"),
                "material_differences": decision.get("material_differences") or [],
                "recommended_subsplit_cues": decision.get("recommended_subsplit_cues") or [],
                "shared_features": decision.get("shared_features") or [],
                "clinical_boundary_notes": decision.get("clinical_boundary_notes"),
                "source_or_license_constraints": decision.get("source_or_license_constraints") or [],
            },
            "members": members,
        },
        local_to_unit,
    )


def get_client(config_index: int, configs: list[dict[str, Any]]) -> OpenAICompatibleClient:
    clients = getattr(_THREAD_LOCAL, "clients", None)
    if clients is None:
        clients = {}
        _THREAD_LOCAL.clients = clients
    if config_index not in clients:
        clients[config_index] = OpenAICompatibleClient(configs[config_index]["model"])
    return clients[config_index]


def normalize_result(result: dict[str, Any], local_to_unit: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    allowed_statuses = {"subgroups_created", "all_deferred", "quality_issue"}
    allowed_confidence = {"high", "medium", "low"}
    all_local_ids = set(local_to_unit)
    assigned: Counter[str] = Counter()
    normalized_subgroups: list[dict[str, Any]] = []
    if result.get("subgrouping_status") not in allowed_statuses:
        result["subgrouping_status"] = "quality_issue"
        issues.append({"issue": "invalid_subgrouping_status"})

    raw_subgroups = result.get("subgroups")
    if not isinstance(raw_subgroups, list):
        raw_subgroups = []
        issues.append({"issue": "subgroups_not_list"})
    for subgroup in raw_subgroups:
        if not isinstance(subgroup, dict):
            issues.append({"issue": "invalid_subgroup_entry"})
            continue
        member_local_ids = [str(item) for item in subgroup.get("member_local_ids") or [] if str(item) in all_local_ids]
        if not member_local_ids:
            issues.append({"issue": "empty_or_invalid_subgroup"})
            continue
        for local_id in member_local_ids:
            assigned[local_id] += 1
        confidence = subgroup.get("confidence")
        if confidence not in allowed_confidence:
            confidence = "low"
        normalized_subgroups.append(
            {
                "subgroup_label": compact_text(subgroup.get("subgroup_label"), 140),
                "member_local_ids": member_local_ids,
                "member_unit_ids": [local_to_unit[local_id] for local_id in member_local_ids],
                "shared_question_or_need": compact_text(subgroup.get("shared_question_or_need"), 500),
                "shared_answer_intent": compact_text(subgroup.get("shared_answer_intent"), 500),
                "separation_reason": compact_text(subgroup.get("separation_reason"), 500),
                "license_or_source_notes": [compact_text(item, 300) for item in (subgroup.get("license_or_source_notes") or [])[:10]],
                "clinical_boundary_notes": compact_text(subgroup.get("clinical_boundary_notes"), 400) or None,
                "confidence": confidence,
            }
        )

    defer_local_ids = [str(item) for item in result.get("defer_member_local_ids") or [] if str(item) in all_local_ids]
    for local_id in defer_local_ids:
        assigned[local_id] += 1

    missing = sorted(all_local_ids - set(assigned))
    duplicates = sorted(local_id for local_id, count in assigned.items() if count > 1)
    if duplicates:
        issues.append({"issue": "duplicate_member_assignment", "local_ids": duplicates[:20]})
    if missing:
        issues.append({"issue": "missing_member_assignment", "local_ids": missing[:20]})
        defer_local_ids.extend(missing)
    if duplicates:
        duplicate_set = set(duplicates)
        for subgroup in normalized_subgroups:
            subgroup["member_local_ids"] = [local_id for local_id in subgroup["member_local_ids"] if local_id not in duplicate_set]
            subgroup["member_unit_ids"] = [local_to_unit[local_id] for local_id in subgroup["member_local_ids"]]
        defer_local_ids.extend(duplicates)

    defer_local_ids = sorted(set(defer_local_ids), key=lambda item: int(item[1:]) if item[1:].isdigit() else item)
    normalized_subgroups = [subgroup for subgroup in normalized_subgroups if subgroup["member_local_ids"]]
    result["subgroups"] = normalized_subgroups
    result["defer_member_local_ids"] = defer_local_ids
    result["defer_member_unit_ids"] = [local_to_unit[local_id] for local_id in defer_local_ids]
    result["subsplit_cues_used"] = [compact_text(item, 200) for item in (result.get("subsplit_cues_used") or [])[:20]]
    result["quality_notes"] = [compact_text(item, 300) for item in (result.get("quality_notes") or [])[:20]]
    result["source_faithfulness_notes"] = compact_text(result.get("source_faithfulness_notes"), 600)
    if not result["subgroups"] and result["defer_member_local_ids"]:
        result["subgrouping_status"] = "all_deferred"
    return result, issues


def adjudicate_one(
    *,
    row: dict[str, Any],
    index: int,
    config_index: int,
    configs: list[dict[str, Any]],
    config_paths: list[str],
    text_views: dict[str, dict[str, Any]],
    parse_retries: int,
    max_prompt_tokens: int,
    target_prompt_tokens: int,
    max_question_chars: int,
    max_answer_chars: int,
    max_structure_chars: int,
    min_question_chars: int,
    min_answer_chars: int,
    min_structure_chars: int,
) -> dict[str, Any]:
    started = time.time()
    question_chars = max_question_chars
    answer_chars = max_answer_chars
    structure_chars = max_structure_chars
    payload, local_to_unit = build_payload(row, index, text_views, question_chars, answer_chars, structure_chars)
    budget = prompt_budget_report(SYSTEM_PROMPT, payload, max_prompt_tokens)
    compaction_steps = []
    while (
        (budget.get("over_budget") or int(budget.get("estimated_prompt_tokens") or 0) > target_prompt_tokens)
        and (answer_chars > min_answer_chars or question_chars > min_question_chars or structure_chars > min_structure_chars)
    ):
        previous = {
            "question_chars": question_chars,
            "answer_chars": answer_chars,
            "structure_chars": structure_chars,
            "estimated_prompt_tokens": budget.get("estimated_prompt_tokens"),
            "over_budget": budget.get("over_budget"),
        }
        if answer_chars > min_answer_chars:
            answer_chars = max(min_answer_chars, int(answer_chars * 0.6))
        elif question_chars > min_question_chars:
            question_chars = max(min_question_chars, int(question_chars * 0.6))
        elif structure_chars > min_structure_chars:
            structure_chars = max(min_structure_chars, int(structure_chars * 0.6))
        payload, local_to_unit = build_payload(row, index, text_views, question_chars, answer_chars, structure_chars)
        budget = prompt_budget_report(SYSTEM_PROMPT, payload, max_prompt_tokens)
        previous["new_estimated_prompt_tokens"] = budget.get("estimated_prompt_tokens")
        compaction_steps.append(previous)
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
        result, normalization_issues = normalize_result(result, local_to_unit)
        status = "complete" if not normalization_issues else "complete_with_normalization_issues"
        error = None
    except Exception as exc:  # noqa: BLE001
        result = {}
        normalization_issues = []
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "schema_version": "theraskill_qa_psychoeducation_split_subgrouping_v1",
        "queue_item_id": row.get("queue_item_id"),
        "status": status,
        "error": error,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_hash(),
        "model_config": config_paths[config_index],
        "model": model_name,
        "runtime_seconds": round(time.time() - started, 3),
        "prompt_budget": budget,
        "input_compaction": {
            "target_prompt_tokens": target_prompt_tokens,
            "final_question_chars": question_chars,
            "final_answer_chars": answer_chars,
            "final_structure_chars": structure_chars,
            "steps": compaction_steps,
        },
        "component_id": row.get("component_id"),
        "member_count": row.get("member_count"),
        "member_unit_ids": row.get("member_unit_ids") or [],
        "component_metadata": row.get("component_metadata") or {},
        "text_quality_summary": row.get("text_quality_summary") or {},
        "first_pass_decision": row.get("llm_decision") or {},
        "subgrouping": result,
        "normalization_issues": normalization_issues,
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
    }


def state_payload(
    *,
    status: str,
    split_queue_path: str,
    output_path: Path,
    total_items: int,
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
        "schema_version": "theraskill_qa_psychoeducation_split_subgrouping_progress_v1",
        "status": status,
        "started_at": started_at,
        "updated_at": utc_now(),
        "split_queue_path": split_queue_path,
        "output_path": str(output_path),
        "total_items": total_items,
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
    output_path = output_dir / "qa_psychoeducation.split_subgrouping.jsonl"
    failure_path = output_dir / "qa_psychoeducation.split_subgrouping_failures.jsonl"
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

    rows = load_split_queue(args.split_queue, args.max_items)
    needed_ids = {str(unit_id) for row in rows for unit_id in (row.get("member_unit_ids") or [])}
    text_views = load_needed_text_views(args.text_view, needed_ids)
    missing_text_views = sorted(needed_ids - set(text_views))

    statuses = existing_statuses(output_path) if args.resume else {}
    invalid_status_lines = sum(1 for value in statuses.values() if value == "invalid_jsonl")
    existing_complete = sum(1 for value in statuses.values() if value in {"complete", "complete_with_normalization_issues"})
    existing_failed = sum(1 for value in statuses.values() if value == "failed")
    skip_statuses = {"complete", "complete_with_normalization_issues"}
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
        split_queue_path=args.split_queue,
        output_path=output_path,
        total_items=len(rows),
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
    if missing_text_views:
        progress["missing_text_view_rows"] = len(missing_text_views)
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
                "split_items_loaded": len(rows),
                "needed_text_views": len(needed_ids),
                "loaded_text_views": len(text_views),
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
                    text_views=text_views,
                    parse_retries=parse_retries,
                    max_prompt_tokens=max_prompt_tokens,
                    target_prompt_tokens=args.target_prompt_tokens,
                    max_question_chars=args.max_question_chars,
                    max_answer_chars=args.max_answer_chars,
                    max_structure_chars=args.max_structure_chars,
                    min_question_chars=args.min_question_chars,
                    min_answer_chars=args.min_answer_chars,
                    min_structure_chars=args.min_structure_chars,
                )
            )
        for future in as_completed(futures):
            record = future.result()
            append_jsonl(output_path, record)
            processed_this_run += 1
            if record["status"] in {"complete", "complete_with_normalization_issues"}:
                completed_this_run += 1
                subgrouping = record.get("subgrouping") or {}
                counters[f"status::{record['status']}"] += 1
                counters[f"subgrouping::{subgrouping.get('subgrouping_status')}"] += 1
                counters["subgroups"] += len(subgrouping.get("subgroups") or [])
                counters["subgroup_members"] += sum(len(item.get("member_unit_ids") or []) for item in subgrouping.get("subgroups") or [])
                counters["deferred_members"] += len(subgrouping.get("defer_member_unit_ids") or [])
                counters["normalization_issue_rows"] += 1 if record.get("normalization_issues") else 0
            else:
                failed_this_run += 1
                append_jsonl(
                    failure_path,
                    {
                        "schema_version": "theraskill_qa_psychoeducation_split_subgrouping_failure_v1",
                        "queue_item_id": record.get("queue_item_id"),
                        "component_id": record.get("component_id"),
                        "error": record.get("error"),
                        "model_config": record.get("model_config"),
                        "updated_at": utc_now(),
                    },
                )
                counters["failed"] += 1
            if processed_this_run % max(1, args.progress_every) == 0 or processed_this_run == len(pending):
                progress = state_payload(
                    status="running",
                    split_queue_path=args.split_queue,
                    output_path=output_path,
                    total_items=len(rows),
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
    final_complete = sum(1 for value in final_statuses.values() if value in {"complete", "complete_with_normalization_issues"})
    final_failed = sum(1 for value in final_statuses.values() if value == "failed")
    expected_total = len(rows)
    status = "complete" if final_complete == expected_total and final_failed == 0 and not is_limited and not missing_text_views else "incomplete"
    report = {
        "schema_version": "theraskill_qa_psychoeducation_split_subgrouping_report_v1",
        "created_at": utc_now(),
        "status": status,
        "split_queue": {
            "path": args.split_queue,
            "sha256": file_sha256(args.split_queue),
            "rows_loaded": len(rows),
            "is_limited_debug_run": is_limited,
        },
        "text_view": {
            "path": args.text_view,
            "sha256": file_sha256(args.text_view),
            "needed_rows": len(needed_ids),
            "loaded_rows": len(text_views),
            "missing_rows": len(missing_text_views),
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
            "target_prompt_tokens": args.target_prompt_tokens,
            "max_question_chars": args.max_question_chars,
            "max_answer_chars": args.max_answer_chars,
            "max_structure_chars": args.max_structure_chars,
            "min_question_chars": args.min_question_chars,
            "min_answer_chars": args.min_answer_chars,
            "min_structure_chars": args.min_structure_chars,
            "resume": args.resume,
            "retry_failed": args.retry_failed,
        },
        "counts": {
            "expected_items": expected_total,
            "existing_complete_before_run": existing_complete,
            "existing_failed_before_run": existing_failed,
            "skipped_this_run": skipped_this_run,
            "pending_this_run": len(pending),
            "completed_this_run": completed_this_run,
            "failed_this_run": failed_this_run,
            "final_complete": final_complete,
            "final_failed": final_failed,
            "final_accounted": final_complete + final_failed,
            **dict(counters),
        },
        "issues_count": final_failed + invalid_status_lines + len(missing_text_views) + (1 if is_limited else 0),
        "issues": [],
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    if final_failed:
        report["issues"].append({"issue": "failed_subgrouping_items", "count": final_failed, "failure_path": str(failure_path)})
    if invalid_status_lines:
        report["issues"].append({"issue": "invalid_existing_output_lines", "count": invalid_status_lines})
    if missing_text_views:
        report["issues"].append({"issue": "missing_text_view_rows", "count": len(missing_text_views), "examples": missing_text_views[:10]})
    if is_limited:
        report["issues"].append({"issue": "limited_debug_run", "count": expected_total})
    write_json(args.report, report)
    write_json(args.manifest, report)
    write_json(args.qa_report, report)
    final_progress = state_payload(
        status=status,
        split_queue_path=args.split_queue,
        output_path=output_path,
        total_items=len(rows),
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
