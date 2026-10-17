#!/usr/bin/env python3
"""Run Phase 5 LLM subgrouping for split-needed clinical/process clusters."""

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


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_MODEL_CONFIGS = (
    "configs/model_config.yaml,"
    "configs/model_config.yaml"
)

FAMILY_CONFIG = {
    "clinical_risk_assessment": {
        "split_queue": "outputs/knowledge/dedup/clusters/clinical_risk_assessment_queue_review/clinical_risk_assessment.split_needed_queue.jsonl",
        "output_dir": "outputs/knowledge/dedup/adjudications/clinical_risk_assessment_split_subgrouping",
        "output_name": "clinical_risk_assessment.split_subgrouping.jsonl",
        "failure_name": "clinical_risk_assessment.split_subgrouping_failures.jsonl",
        "report": "outputs/knowledge/dedup/reports/clinical_risk_assessment_split_subgrouping_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_split_subgrouping_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/clinical_risk_assessment_split_subgrouping_qa.json",
        "schema": "theraskill_clinical_risk_assessment_split_subgrouping_v1",
        "progress_schema": "theraskill_clinical_risk_assessment_split_subgrouping_progress_v1",
        "prompt_version": "clinical_risk_assessment_split_subgrouping_v1",
    },
    "therapy_process": {
        "split_queue": "outputs/knowledge/dedup/clusters/therapy_process_queue_review/therapy_process.split_needed_queue.jsonl",
        "output_dir": "outputs/knowledge/dedup/adjudications/therapy_process_split_subgrouping",
        "output_name": "therapy_process.split_subgrouping.jsonl",
        "failure_name": "therapy_process.split_subgrouping_failures.jsonl",
        "report": "outputs/knowledge/dedup/reports/therapy_process_split_subgrouping_report.json",
        "manifest": "outputs/knowledge/dedup/manifests/therapy_process_split_subgrouping_manifest.json",
        "qa": "outputs/knowledge/dedup/manifests/therapy_process_split_subgrouping_qa.json",
        "schema": "theraskill_therapy_process_split_subgrouping_v1",
        "progress_schema": "theraskill_therapy_process_split_subgrouping_progress_v1",
        "prompt_version": "therapy_process_split_subgrouping_v1",
    },
}

CLINICAL_PROMPT = """You split one clinical assessment cluster that was marked split_needed into smaller evidence-grounded subgroups.

Return exactly one compact JSON object. Do not write prose outside JSON. Do not reveal chain-of-thought.

Boundaries:
- This is subgrouping only. Do not create final merged knowledge.
- Clinical risk is a content category, not a data-quality problem. Use quality_consistency_issue / label_conflict / schema_inconsistency for data or merge quality.
- Use only provided member text, labels, first-pass decision, and metadata.
- Every member must be assigned exactly once, either to one subgroup or to defer_member_local_ids.
- A valid subgroup requires compatible clinical construct, label/severity/risk-domain boundary, and source evidence.
- Keep conflicting labels, different risk severity, different target behavior, or source/evidence incompatibility separate.

Required JSON schema:
{
  "subgrouping_status": "subgroups_created" | "all_deferred" | "quality_issue",
  "subgroups": [
    {
      "subgroup_label": "short neutral label",
      "member_local_ids": ["M1", "M2"],
      "shared_construct": "one evidence-grounded sentence",
      "label_or_severity_boundary": "one sentence",
      "separation_reason": "why this subgroup is separate",
      "confidence": "high" | "medium" | "low"
    }
  ],
  "defer_member_local_ids": ["M3"],
  "subsplit_cues_used": ["cue labels used"],
  "quality_notes": ["empty/malformed/label-conflict notes, if any"],
  "source_faithfulness_notes": "one sentence about direct support from evidence"
}
"""

THERAPY_PROMPT = """You split one therapy-process cluster that was marked split_needed into smaller evidence-grounded subgroups.

Return exactly one compact JSON object. Do not write prose outside JSON. Do not reveal chain-of-thought.

Boundaries:
- This is subgrouping only. Do not create final merged knowledge.
- Use only provided member process views, first-pass decision, and metadata.
- Every member must be assigned exactly once, either to one subgroup or to defer_member_local_ids.
- A valid subgroup requires compatible client state, therapist move function/type, process goal, and client response/transition.
- Keep different presenting-problem domains, readiness/alliance/disclosure states, move sequences, risk boundaries, or response transitions separate.
- If evidence is too generic, malformed, or no-sequence-like, defer that member rather than forcing a subgroup.

Required JSON schema:
{
  "subgrouping_status": "subgroups_created" | "all_deferred" | "quality_issue",
  "subgroups": [
    {
      "subgroup_label": "short neutral label",
      "member_local_ids": ["M1", "M2"],
      "shared_state_move_response_pattern": "one evidence-grounded sentence",
      "process_boundary": "what makes this subgroup distinct",
      "separation_reason": "why this subgroup is separate",
      "confidence": "high" | "medium" | "low"
    }
  ],
  "defer_member_local_ids": ["M3"],
  "subsplit_cues_used": ["cue labels used"],
  "quality_notes": ["empty/malformed/no-sequence notes, if any"],
  "source_faithfulness_notes": "one sentence about direct support from evidence"
}
"""

PROMPTS = {"clinical_risk_assessment": CLINICAL_PROMPT, "therapy_process": THERAPY_PROMPT}
_THREAD_LOCAL = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=sorted(FAMILY_CONFIG), required=True)
    parser.add_argument("--split-queue")
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--output-dir")
    parser.add_argument("--report")
    parser.add_argument("--manifest")
    parser.add_argument("--qa-report")
    parser.add_argument("--model-configs", default=DEFAULT_MODEL_CONFIGS)
    parser.add_argument("--workers-per-config", type=int, default=32)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-member-chars", type=int, default=520)
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
    return hashlib.sha256(PROMPTS[family].encode("utf-8")).hexdigest()


def load_jsonl(path: str | Path, max_items: int | None = None) -> list[dict[str, Any]]:
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
    return text[: max(0, limit - 3)] + "..."


def adaptive_member_chars(member_count: int, requested: int) -> int:
    if member_count <= 0:
        return requested
    return max(110, min(requested, int(17000 / member_count)))


def evidence_by_unit(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("unit_id")): item for item in row.get("evidence_examples") or [] if item.get("unit_id")}


def member_view(family: str, unit_id: str, view: dict[str, Any] | None, evidence: dict[str, Any] | None, max_chars: int) -> dict[str, Any]:
    fields = (view or {}).get("dedup_key_fields") or {}
    evidence = evidence or {}
    if family == "clinical_risk_assessment":
        body = fields.get("text") or evidence.get("clinical_text_preview") or (view or {}).get("dedup_text") or evidence.get("dedup_text_preview") or ""
        label = fields.get("label") or evidence.get("label_preview") or ""
        return {
            "unit_id": unit_id,
            "dataset": (view or {}).get("dataset") or evidence.get("dataset"),
            "knowledge_type": (view or {}).get("knowledge_type") or evidence.get("knowledge_type"),
            "label": compact_text(label, 120),
            "clinical_text": compact_text(body, max_chars),
            "clinical_content_flags": (view or {}).get("risk_or_safety_flags") or evidence.get("risk_or_safety_flags") or [],
            "source_construction_type": (view or {}).get("source_construction_type") or evidence.get("source_construction_type"),
        }
    process_text = fields.get("process_text") or evidence.get("process_text_preview") or (view or {}).get("dedup_text") or evidence.get("dedup_text_preview") or ""
    return {
        "unit_id": unit_id,
        "dataset": (view or {}).get("dataset") or evidence.get("dataset"),
        "knowledge_type": (view or {}).get("knowledge_type") or evidence.get("knowledge_type"),
        "process_text": compact_text(process_text, max_chars),
        "clinical_content_flags": (view or {}).get("risk_or_safety_flags") or evidence.get("risk_or_safety_flags") or [],
        "source_construction_type": (view or {}).get("source_construction_type") or evidence.get("source_construction_type"),
    }


def build_payload(
    family: str,
    row: dict[str, Any],
    index: int,
    text_views: dict[str, dict[str, Any]],
    max_member_chars: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    member_ids = [str(unit_id) for unit_id in row.get("member_unit_ids") or []]
    local_to_unit: dict[str, str] = {}
    evidence = evidence_by_unit(row)
    member_chars = adaptive_member_chars(len(member_ids), max_member_chars)
    members: list[dict[str, Any]] = []
    for member_index, unit_id in enumerate(member_ids, start=1):
        local_id = f"M{member_index}"
        local_to_unit[local_id] = unit_id
        item = member_view(family, unit_id, text_views.get(unit_id), evidence.get(unit_id), member_chars)
        item["local_member_id"] = local_id
        members.append(item)
    decision = row.get("llm_decision") or {}
    first_pass = {
        "material_differences": decision.get("material_differences") or [],
        "recommended_subsplit_cues": decision.get("recommended_subsplit_cues") or [],
        "source_or_evidence_constraints": decision.get("source_or_evidence_constraints") or [],
    }
    if family == "clinical_risk_assessment":
        first_pass.update(
            {
                "canonical_label_or_construct": decision.get("canonical_label_or_construct"),
                "clinical_label_consistency": decision.get("clinical_label_consistency"),
                "severity_or_risk_domain_consistency": decision.get("severity_or_risk_domain_consistency"),
                "shared_construct": decision.get("shared_construct"),
            }
        )
    else:
        first_pass.update(
            {
                "canonical_process_label": decision.get("canonical_process_label"),
                "process_equivalence": decision.get("process_equivalence"),
                "shared_state_move_response_pattern": decision.get("shared_state_move_response_pattern"),
                "state_consistency": decision.get("state_consistency"),
                "move_sequence_consistency": decision.get("move_sequence_consistency"),
                "client_response_consistency": decision.get("client_response_consistency"),
            }
        )
    return (
        {
            "task": f"{family}_split_needed_subgrouping",
            "local_cluster_id": f"P5S{index}",
            "cluster_metadata": {
                "queue_item_id": row.get("queue_item_id"),
                "member_count": row.get("member_count"),
                "source_kind": row.get("source_kind"),
                "source_component_id": row.get("source_component_id"),
                "refinement_key": row.get("refinement_key"),
                "component_metadata": row.get("component_metadata") or {},
                "text_quality_summary": row.get("text_quality_summary") or {},
                "member_text_char_budget": member_chars,
            },
            "first_pass_decision": first_pass,
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


def normalize_result(family: str, result: dict[str, Any], local_to_unit: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        record = {
            "subgroup_label": compact_text(subgroup.get("subgroup_label"), 140),
            "member_local_ids": member_local_ids,
            "member_unit_ids": [local_to_unit[local_id] for local_id in member_local_ids],
            "separation_reason": compact_text(subgroup.get("separation_reason"), 600),
            "confidence": confidence,
        }
        if family == "clinical_risk_assessment":
            record["shared_construct"] = compact_text(subgroup.get("shared_construct"), 600)
            record["label_or_severity_boundary"] = compact_text(subgroup.get("label_or_severity_boundary"), 500)
        else:
            record["shared_state_move_response_pattern"] = compact_text(subgroup.get("shared_state_move_response_pattern"), 700)
            record["process_boundary"] = compact_text(subgroup.get("process_boundary"), 500)
        normalized_subgroups.append(record)

    defer_local_ids = [str(item) for item in result.get("defer_member_local_ids") or [] if str(item) in all_local_ids]
    for local_id in defer_local_ids:
        assigned[local_id] += 1
    missing = sorted(all_local_ids - set(assigned), key=lambda value: int(value[1:]) if value[1:].isdigit() else value)
    duplicates = sorted((local_id for local_id, count in assigned.items() if count > 1), key=lambda value: int(value[1:]) if value[1:].isdigit() else value)
    if missing:
        issues.append({"issue": "missing_member_assignment", "local_ids": missing[:30]})
        defer_local_ids.extend(missing)
    if duplicates:
        issues.append({"issue": "duplicate_member_assignment", "local_ids": duplicates[:30]})
        duplicate_set = set(duplicates)
        for subgroup in normalized_subgroups:
            subgroup["member_local_ids"] = [local_id for local_id in subgroup["member_local_ids"] if local_id not in duplicate_set]
            subgroup["member_unit_ids"] = [local_to_unit[local_id] for local_id in subgroup["member_local_ids"]]
        normalized_subgroups = [subgroup for subgroup in normalized_subgroups if subgroup["member_local_ids"]]
        defer_local_ids = sorted(set(defer_local_ids).union(duplicate_set), key=lambda value: int(value[1:]) if value[1:].isdigit() else value)

    result["subgroups"] = normalized_subgroups
    result["defer_member_local_ids"] = sorted(set(defer_local_ids), key=lambda value: int(value[1:]) if value[1:].isdigit() else value)
    result["defer_member_unit_ids"] = [local_to_unit[local_id] for local_id in result["defer_member_local_ids"]]
    for key in ["subsplit_cues_used", "quality_notes"]:
        if not isinstance(result.get(key), list):
            result[key] = []
        result[key] = [compact_text(value, 400) for value in result[key][:30]]
    result["source_faithfulness_notes"] = compact_text(result.get("source_faithfulness_notes"), 700)
    if result["subgroups"] and result.get("subgrouping_status") == "all_deferred":
        result["subgrouping_status"] = "subgroups_created"
    if not result["subgroups"] and not result["defer_member_local_ids"]:
        result["subgrouping_status"] = "quality_issue"
        result["defer_member_local_ids"] = sorted(local_to_unit, key=lambda value: int(value[1:]) if value[1:].isdigit() else value)
        result["defer_member_unit_ids"] = [local_to_unit[local_id] for local_id in result["defer_member_local_ids"]]
        issues.append({"issue": "no_members_accounted"})
    return result, issues


def subgroup_one(
    *,
    family: str,
    row: dict[str, Any],
    index: int,
    text_views: dict[str, dict[str, Any]],
    config_index: int,
    configs: list[dict[str, Any]],
    config_paths: list[str],
    parse_retries: int,
    max_prompt_tokens: int,
    max_member_chars: int,
) -> dict[str, Any]:
    started = time.time()
    payload, local_to_unit = build_payload(family, row, index, text_views, max_member_chars)
    system_prompt = PROMPTS[family]
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
        result, normalization_issues = normalize_result(family, result, local_to_unit)
        status = "complete" if not normalization_issues else "complete_with_normalization_issues"
        error = None
    except Exception as exc:  # noqa: BLE001
        result = {}
        normalization_issues = []
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    return {
        "schema_version": FAMILY_CONFIG[family]["schema"],
        "family": family,
        "queue_item_id": row.get("queue_item_id"),
        "status": status,
        "error": error,
        "normalization_issues": normalization_issues,
        "prompt_version": FAMILY_CONFIG[family]["prompt_version"],
        "prompt_sha256": prompt_hash(family),
        "model_config": config_paths[config_index],
        "model": model_name,
        "runtime_seconds": round(time.time() - started, 3),
        "prompt_budget": budget,
        "source_item_id": row.get("source_item_id"),
        "source_component_id": row.get("source_component_id"),
        "source_kind": row.get("source_kind"),
        "member_count": row.get("member_count"),
        "member_unit_ids": row.get("member_unit_ids") or [],
        "component_metadata": row.get("component_metadata") or {},
        "refinement_key": row.get("refinement_key"),
        "text_quality_summary": row.get("text_quality_summary") or {},
        "first_pass_decision": row.get("llm_decision") or {},
        "subgrouping": result,
        "not_final_merged_unit": True,
        "raw_units_unchanged": True,
        "formal_merge_candidates_output": False,
        "final_merge_output": False,
    }


def state_payload(
    *,
    family: str,
    status: str,
    split_queue_path: str,
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
        "schema_version": FAMILY_CONFIG[family]["progress_schema"],
        "family": family,
        "status": status,
        "started_at": started_at,
        "updated_at": utc_now(),
        "split_queue_path": split_queue_path,
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
    cfg = FAMILY_CONFIG[family]
    started_at = utc_now()
    split_queue = args.split_queue or cfg["split_queue"]
    output_dir = Path(args.output_dir or cfg["output_dir"])
    report_path = args.report or cfg["report"]
    manifest_path = args.manifest or cfg["manifest"]
    qa_path = args.qa_report or cfg["qa"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / str(cfg["output_name"])
    failure_path = output_dir / str(cfg["failure_name"])
    progress_path = output_dir / "progress_state.json"

    if args.overwrite:
        for path in [output_path, failure_path, progress_path]:
            if path.exists():
                path.unlink()
    elif output_path.exists() and not args.resume:
        raise FileExistsError(f"output exists: {output_path}; pass --resume or --overwrite")

    config_paths = split_csv(args.model_configs)
    configs = [load_yaml(path) for path in config_paths]
    parse_retries = args.parse_retries
    if parse_retries is None:
        parse_retries = max(parse_retry_attempts({}, config) for config in configs)
    max_prompt_tokens = args.max_prompt_tokens
    if max_prompt_tokens is None:
        max_prompt_tokens = min(context_max_prompt_tokens({}, config) for config in configs)

    rows = load_jsonl(split_queue, args.max_items)
    needed_unit_ids = {str(unit_id) for row in rows for unit_id in (row.get("member_unit_ids") or [])}
    text_views = load_needed_text_views(args.text_view, needed_unit_ids)
    missing_text_views = sorted(needed_unit_ids - set(text_views))
    if missing_text_views:
        raise ValueError(f"missing text views for {len(missing_text_views)} split members, examples={missing_text_views[:10]}")

    statuses = existing_statuses(output_path) if args.resume else {}
    invalid_status_lines = sum(1 for value in statuses.values() if value == "invalid_jsonl")
    complete_statuses = {"complete", "complete_with_normalization_issues"}
    existing_complete = sum(1 for value in statuses.values() if value in complete_statuses)
    existing_failed = sum(1 for value in statuses.values() if value == "failed")
    skip_statuses = set(complete_statuses)
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
        split_queue_path=split_queue,
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
    print(json.dumps({"event": "start", "family": family, "split_queue_rows_loaded": len(rows), "members_loaded": len(needed_unit_ids), "pending_this_run": len(pending), "workers": total_workers, "output": str(output_path)}, ensure_ascii=False), flush=True)

    with ThreadPoolExecutor(max_workers=total_workers) as executor:
        futures = []
        for pending_index, (queue_index, row) in enumerate(pending):
            futures.append(
                executor.submit(
                    subgroup_one,
                    family=family,
                    row=row,
                    index=queue_index,
                    text_views=text_views,
                    config_index=pending_index % len(configs),
                    configs=configs,
                    config_paths=config_paths,
                    parse_retries=parse_retries,
                    max_prompt_tokens=max_prompt_tokens,
                    max_member_chars=args.max_member_chars,
                )
            )
        for future in as_completed(futures):
            record = future.result()
            append_jsonl(output_path, record)
            processed_this_run += 1
            if record["status"] in complete_statuses:
                completed_this_run += 1
                subgrouping = record.get("subgrouping") or {}
                counters[f"subgrouping_status::{subgrouping.get('subgrouping_status')}"] += 1
                counters[f"subgroup_count::{len(subgrouping.get('subgroups') or [])}"] += 1
                if record["status"] == "complete_with_normalization_issues":
                    counters["complete_with_normalization_issues"] += 1
            else:
                failed_this_run += 1
                append_jsonl(failure_path, {"schema_version": "theraskill_phase5_split_subgrouping_failure_v1", "family": family, "queue_item_id": record.get("queue_item_id"), "error": record.get("error"), "model_config": record.get("model_config"), "updated_at": utc_now()})
                counters["failed"] += 1
            if processed_this_run % max(1, args.progress_every) == 0 or processed_this_run == len(pending):
                progress = state_payload(
                    family=family,
                    status="running",
                    split_queue_path=split_queue,
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
    final_complete = sum(1 for value in final_statuses.values() if value in complete_statuses)
    final_failed = sum(1 for value in final_statuses.values() if value == "failed")
    output_rows = 0
    subgroup_count = 0
    subgroup_member_count = 0
    deferred_member_count = 0
    normalization_issue_rows = 0
    subgrouping_status_counter: Counter[str] = Counter()
    with output_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            output_rows += 1
            row = json.loads(line)
            if row.get("status") == "complete_with_normalization_issues":
                normalization_issue_rows += 1
            subgrouping = row.get("subgrouping") or {}
            subgrouping_status_counter[str(subgrouping.get("subgrouping_status"))] += 1
            for subgroup in subgrouping.get("subgroups") or []:
                subgroup_count += 1
                subgroup_member_count += len(subgroup.get("member_unit_ids") or [])
            deferred_member_count += len(subgrouping.get("defer_member_unit_ids") or [])

    status = "complete" if final_complete == len(rows) and final_failed == 0 and not is_limited else "incomplete"
    issues = []
    if final_failed:
        issues.append({"issue": "failed_subgrouping_items", "count": final_failed, "failure_path": str(failure_path)})
    if invalid_status_lines:
        issues.append({"issue": "invalid_existing_output_lines", "count": invalid_status_lines})
    if is_limited:
        issues.append({"issue": "limited_debug_run", "count": len(rows)})
    if subgroup_member_count + deferred_member_count != len(needed_unit_ids):
        issues.append({"issue": "member_assignment_count_mismatch", "input_member_count": len(needed_unit_ids), "accounted_member_assignments": subgroup_member_count + deferred_member_count})
        status = "incomplete"

    report = {
        "schema_version": "theraskill_phase5_split_subgrouping_report_v1",
        "created_at": utc_now(),
        "family": family,
        "status": status,
        "inputs": {
            "split_queue": {"path": split_queue, "sha256": file_sha256(split_queue), "rows_loaded": len(rows)},
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view), "needed_unit_ids": len(needed_unit_ids), "loaded_text_views": len(text_views)},
        },
        "outputs": {
            "output_jsonl": {"path": str(output_path), "rows": output_rows, "sha256": file_sha256(output_path)},
            "failure_jsonl": {"path": str(failure_path), "sha256": file_sha256(failure_path)},
            "progress_state": {"path": str(progress_path), "sha256": file_sha256(progress_path)},
        },
        "model_configs": [{"path": path, "sha256": file_sha256(path), "model": (config.get("model") or {}).get("name"), "base_url": (config.get("model") or {}).get("base_url")} for path, config in zip(config_paths, configs)],
        "runtime": {"started_at": started_at, "finished_at": utc_now(), "workers_per_config": args.workers_per_config, "total_workers": total_workers, "parse_retries": parse_retries, "max_prompt_tokens": max_prompt_tokens, "max_member_chars": args.max_member_chars, "is_limited_debug_run": is_limited},
        "counts": {
            "expected_queue_items": len(rows),
            "existing_complete_before_run": existing_complete,
            "existing_failed_before_run": existing_failed,
            "skipped_this_run": skipped_this_run,
            "pending_this_run": len(pending),
            "completed_this_run": completed_this_run,
            "failed_this_run": failed_this_run,
            "final_complete": final_complete,
            "final_failed": final_failed,
            "final_accounted": final_complete + final_failed,
            "output_rows": output_rows,
            "input_member_count": len(needed_unit_ids),
            "subgroup_count": subgroup_count,
            "subgroup_member_count": subgroup_member_count,
            "deferred_member_count": deferred_member_count,
            "accounted_member_assignments": subgroup_member_count + deferred_member_count,
            "normalization_issue_rows": normalization_issue_rows,
        },
        "subgrouping_status_distribution": dict(subgrouping_status_counter.most_common()),
        "decision_distribution_this_run": dict(counters.most_common()),
        "issues_count": len(issues),
        "issues": issues,
        "raw_units_unchanged": True,
        "final_merge_output": False,
        "formal_merge_candidates_output": False,
    }
    write_json(report_path, report)
    write_json(manifest_path, report)
    write_json(qa_path, report)
    final_progress = state_payload(
        family=family,
        status=status,
        split_queue_path=split_queue,
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
