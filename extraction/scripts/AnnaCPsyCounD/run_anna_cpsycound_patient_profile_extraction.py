#!/usr/bin/env python3
"""Run Anna-CPsyCounD patient-side extraction for simulation knowledge."""

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
PROJECT_ROOT = SCRIPT_DIR.parent.parent
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from anna_cpsycound_surface_registry import (  # noqa: E402
    DATASET_ROOT,
    FAILURE_OUTPUT_PATH,
    MANIFEST_OUTPUT_PATH,
    OUTPUT_PATHS,
    PROMPT_PATH,
    QA_OUTPUT_PATH,
    README_FILE,
    SOURCE_FILE,
)
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
)


DATASET = "Anna-CPsyCounD"
SOURCE_ROOT = str(DATASET_ROOT)
PROMPT = str(PROMPT_PATH.relative_to(PROJECT_ROOT))
EXTRACTION_CONFIG = "configs/extraction_config.yaml"
MODEL_CONFIG = "configs/model_config.yaml"
PATIENT_OUTPUT = str(OUTPUT_PATHS["patient_persona_profile"].relative_to(PROJECT_ROOT))
HIDDEN_OUTPUT = str(OUTPUT_PATHS["hidden_patient_profile_pattern"].relative_to(PROJECT_ROOT))
MEMORY_OUTPUT = str(OUTPUT_PATHS["historical_case_memory_seed"].relative_to(PROJECT_ROOT))
BRIDGE_OUTPUT = str(OUTPUT_PATHS["report_to_conversation_bridge_pattern"].relative_to(PROJECT_ROOT))
MANIFEST = str(MANIFEST_OUTPUT_PATH.relative_to(PROJECT_ROOT))
QA = str(QA_OUTPUT_PATH.relative_to(PROJECT_ROOT))
FAILURES = str(FAILURE_OUTPUT_PATH.relative_to(PROJECT_ROOT))

KNOWLEDGE_OUTPUTS = {
    name: str(path.relative_to(PROJECT_ROOT))
    for name, path in OUTPUT_PATHS.items()
}

EXPECTED_FIELDS = {
    "patient_persona_profile": [
        "demographic_profile",
        "presenting_problem_profile",
        "symptom_profile",
        "emotional_profile",
        "cognitive_style",
        "interpersonal_context",
        "help_seeking_style",
        "motivation_or_readiness_profile",
        "protective_factor_profile",
        "distress_or_risk_flags",
        "evidence_spans",
        "quality_status",
    ],
    "hidden_patient_profile_pattern": [
        "surface_presenting_story",
        "latent_core_conflicts",
        "withheld_or_unspoken_concerns",
        "disclosure_conditions",
        "alliance_sensitivity_signals",
        "rupture_triggers",
        "trust_building_signals",
        "response_shift_conditions",
        "memory_update_hooks",
        "evidence_spans",
        "quality_status",
    ],
    "historical_case_memory_seed": [
        "stable_identity_facts",
        "longitudinal_problem_summary",
        "prior_counseling_progress",
        "previously_used_techniques",
        "unresolved_threads",
        "resource_signals",
        "relationship_context",
        "risk_watchpoints",
        "next_session_entry_points",
        "evidence_spans",
        "quality_status",
    ],
    "report_to_conversation_bridge_pattern": [
        "report_summary_to_dialogue_alignment",
        "stable_themes_across_report_and_dialogue",
        "report_only_information",
        "dialogue_only_information",
        "continuity_signals",
        "mismatch_or_tension_signals",
        "simulation_use_notes",
        "evidence_spans",
        "quality_status",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--source-file", default=SOURCE_FILE)
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--extraction-config", default=EXTRACTION_CONFIG)
    parser.add_argument("--model-config", default=MODEL_CONFIG)
    parser.add_argument("--patient-output", default=PATIENT_OUTPUT)
    parser.add_argument("--hidden-output", default=HIDDEN_OUTPUT)
    parser.add_argument("--memory-output", default=MEMORY_OUTPUT)
    parser.add_argument("--bridge-output", default=BRIDGE_OUTPUT)
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--qa-report", default=QA)
    parser.add_argument("--failure-log", default=FAILURES)
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--case-id-file")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--compact-target-prompt-tokens", type=int, default=12000)
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--max-turn-chars", type=int, default=320)
    parser.add_argument("--max-report-item-chars", type=int, default=520)
    parser.add_argument("--max-output-tokens", type=int)
    return parser.parse_args()


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_path(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def file_sha256(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def clean_text(text: Any, limit: int | None = None) -> str:
    text = " ".join(str(text).split())
    if limit is not None:
        return text[:limit]
    return text


def unit_id(case_uuid: str, knowledge_type: str) -> str:
    return f"ku_anna_cpsycound_{knowledge_type}_{stable_hash(case_uuid)}"


def balanced_turns(conversation: list[dict[str, Any]], max_turns: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    total = len(conversation)
    if total <= max_turns:
        return list(conversation), {
            "strategy": "full",
            "kept_turn_count": total,
            "omitted_turn_count": 0,
        }
    head = max_turns // 2
    tail = max_turns - head
    selected = list(conversation[:head]) + list(conversation[-tail:])
    return selected, {
        "strategy": "balanced_head_tail",
        "kept_turn_count": len(selected),
        "omitted_turn_count": total - len(selected),
        "head_kept": head,
        "tail_kept": tail,
    }


def clip_report_list(items: Any, max_chars: int) -> list[str]:
    values = items if isinstance(items, list) else [items]
    result = []
    for item in values:
        text = clean_text(item, max_chars)
        if text:
            result.append(text)
    return result


def conversation_view(conversation: list[dict[str, Any]], *, max_turns: int, max_turn_chars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, policy = balanced_turns(conversation, max_turns)
    compact = []
    for idx, turn in enumerate(selected, start=1):
        compact.append(
            {
                "excerpt_turn_index": idx,
                "role": turn.get("role"),
                "content": clean_text(turn.get("content") or "", max_turn_chars),
            }
        )
    policy["max_turn_chars"] = max_turn_chars
    return compact, policy


def build_case_payload(
    row: dict[str, Any],
    *,
    case_index: int,
    max_turns: int,
    max_turn_chars: int,
    max_report_item_chars: int,
) -> dict[str, Any]:
    convo = row.get("conversation") or []
    convo_excerpt, compaction = conversation_view(
        convo,
        max_turns=max_turns,
        max_turn_chars=max_turn_chars,
    )
    report = row.get("report") or {}
    return {
        "dataset": DATASET,
        "source_id": row.get("id"),
        "case_index": case_index,
        "portrait": {
            "age": (row.get("portrait") or {}).get("age"),
            "gender": (row.get("portrait") or {}).get("gender"),
            "marital_status": (row.get("portrait") or {}).get("marital_status"),
            "occupation": (row.get("portrait") or {}).get("occupation"),
            "symptoms": clean_text((row.get("portrait") or {}).get("symptoms") or "", 400),
        },
        "report": {
            "case_title": clean_text(report.get("案例标题") or "", 180),
            "case_categories": clip_report_list(report.get("案例类别") or [], 120),
            "used_techniques": clip_report_list(report.get("运用的技术") or [], 120),
            "case_summary": clip_report_list(report.get("案例简述") or [], max_report_item_chars),
            "consultation_process": clip_report_list(report.get("咨询经过") or [], max_report_item_chars),
            "reflection_notes": clip_report_list(report.get("经验感想") or [], max_report_item_chars),
        },
        "case_stats": {
            "full_conversation_turn_count": len(convo),
            "excerpt_turn_count": len(convo_excerpt),
        },
        "conversation_excerpt": convo_excerpt,
        "input_compaction": {
            **compaction,
            "max_report_item_chars": max_report_item_chars,
        },
    }


def adaptive_payload(
    row: dict[str, Any],
    *,
    case_index: int,
    system_prompt: str,
    max_prompt_tokens: int,
    compact_target_prompt_tokens: int,
    max_turns: int,
    max_turn_chars: int,
    max_report_item_chars: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempts = [
        (max_turns, max_turn_chars, max_report_item_chars),
        (30, 260, 420),
        (24, 220, 360),
        (18, 180, 280),
        (14, 160, 220),
        (10, 140, 180),
    ]
    seen = set()
    for turn_cap, turn_chars, report_chars in attempts:
        key = (turn_cap, turn_chars, report_chars)
        if key in seen:
            continue
        seen.add(key)
        payload = build_case_payload(
            row,
            case_index=case_index,
            max_turns=turn_cap,
            max_turn_chars=turn_chars,
            max_report_item_chars=report_chars,
        )
        budget = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
        if budget["estimated_prompt_tokens"] <= compact_target_prompt_tokens:
            return payload, {
                "max_turns": turn_cap,
                "max_turn_chars": turn_chars,
                "max_report_item_chars": report_chars,
                **budget,
            }
    payload = build_case_payload(
        row,
        case_index=case_index,
        max_turns=10,
        max_turn_chars=140,
        max_report_item_chars=180,
    )
    budget = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
    return payload, {
        "max_turns": 10,
        "max_turn_chars": 140,
        "max_report_item_chars": 180,
        **budget,
    }


def base_source(row: dict[str, Any], case_index: int, evidence_tier: str) -> dict[str, Any]:
    report = row.get("report") or {}
    return {
        "dataset": DATASET,
        "source_id": f"case:{row.get('id')}",
        "case_uuid": row.get("id"),
        "case_index": case_index,
        "language": "zh",
        "source_files": [SOURCE_FILE, "README.md"],
        "source_construction_type": "synthetic_virtual_client_configuration_extended_from_cpsycound",
        "evidence_tier": evidence_tier,
        "license": "Apache-2.0",
        "citation": "Anna-CPsyCounD local ModelScope dataset card and README; synthetic virtual client data extended from CPsyCounD.",
        "case_title": report.get("案例标题"),
        "case_categories": report.get("案例类别") or [],
    }


def embedding_text(knowledge_type: str, content: dict[str, Any]) -> str:
    parts = [content.get("case_id") or ""]
    if knowledge_type == "patient_persona_profile":
        demo = content.get("demographic_profile") or {}
        parts.extend(
            [
                " ".join(str(v) for v in demo.values() if v),
                " ".join(content.get("presenting_problem_profile") or []),
                " ".join(content.get("symptom_profile") or []),
                " ".join(content.get("emotional_profile") or []),
                " ".join(content.get("cognitive_style") or []),
                " ".join(content.get("interpersonal_context") or []),
            ]
        )
    elif knowledge_type == "hidden_patient_profile_pattern":
        parts.extend(
            [
                " ".join(content.get("surface_presenting_story") or []),
                " ".join(content.get("latent_core_conflicts") or []),
                " ".join(content.get("withheld_or_unspoken_concerns") or []),
                " ".join(content.get("trust_building_signals") or []),
                " ".join(content.get("rupture_triggers") or []),
            ]
        )
    elif knowledge_type == "historical_case_memory_seed":
        parts.extend(
            [
                " ".join(content.get("stable_identity_facts") or []),
                " ".join(content.get("longitudinal_problem_summary") or []),
                " ".join(content.get("prior_counseling_progress") or []),
                " ".join(content.get("previously_used_techniques") or []),
                " ".join(content.get("unresolved_threads") or []),
                " ".join(content.get("next_session_entry_points") or []),
            ]
        )
    elif knowledge_type == "report_to_conversation_bridge_pattern":
        parts.extend(
            [
                " ".join(content.get("stable_themes_across_report_and_dialogue") or []),
                " ".join(content.get("continuity_signals") or []),
                " ".join(content.get("mismatch_or_tension_signals") or []),
                " ".join(content.get("simulation_use_notes") or []),
            ]
        )
    return "\n".join(part for part in parts if part)[:4000]


def add_case_metadata(content: dict[str, Any], row: dict[str, Any], case_index: int) -> dict[str, Any]:
    enriched = dict(content or {})
    enriched["case_id"] = row.get("id")
    enriched["source_id"] = f"case:{row.get('id')}"
    enriched["case_index"] = case_index
    return enriched


def wrap_unit(
    *,
    case_row: dict[str, Any],
    case_index: int,
    knowledge_type: str,
    content: dict[str, Any],
    prompt_audit: dict[str, Any],
    prompt_path: str,
) -> dict[str, Any]:
    evidence_tier_map = {
        "patient_persona_profile": "synthetic_patient_profile_case_pattern",
        "hidden_patient_profile_pattern": "synthetic_hidden_profile_case_pattern",
        "historical_case_memory_seed": "synthetic_case_memory_seed",
        "report_to_conversation_bridge_pattern": "synthetic_report_dialogue_bridge",
    }
    quality_note_map = {
        "patient_persona_profile": "Derived from synthetic portrait/report/dialogue case evidence, not observed clinical evidence.",
        "hidden_patient_profile_pattern": "Latent profile is a conservative inference from synthetic case evidence and may omit unknown factors.",
        "historical_case_memory_seed": "Memory seed reflects report-level continuity hints and dialogue excerpts, not verified longitudinal treatment records.",
        "report_to_conversation_bridge_pattern": "Bridge pattern reflects alignment between synthetic report fields and sampled dialogue evidence.",
    }
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": unit_id(str(case_row.get("id")), knowledge_type),
        "knowledge_type": knowledge_type,
        "source": base_source(case_row, case_index, evidence_tier_map[knowledge_type]),
        "content": add_case_metadata(content, case_row, case_index),
        "quality": {
            "review_status": "llm_extracted_unreviewed",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    quality_note_map[knowledge_type],
                    "Source is Anna-CPsyCounD synthetic simulation data extended from CPsyCounD.",
                    "Fields may be sparse when the case evidence is insufficient.",
                ],
            },
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "free_text_case_level_extraction",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": embedding_text(knowledge_type, add_case_metadata(content, case_row, case_index)),
        },
        "provenance": {
            "projection": "llm_anna_cpsycound_patient_side_case_extraction",
            "prompt_path": prompt_path,
            "prompt_audit": prompt_audit,
            "llm_calls": 1,
            "filtering": "case_level_extraction",
            "deduplication": "none",
            "input_compaction": prompt_audit.get("input_compaction"),
        },
    }


def completed_cases(output_path: Path, failure_log: Path, *, resume: bool) -> set[str]:
    if not resume:
        return set()
    done = set()
    for row in read_jsonl(output_path):
        case_id = (row.get("content") or {}).get("case_id")
        if case_id:
            done.add(str(case_id))
    for row in read_jsonl(failure_log):
        case_id = row.get("case_id")
        if case_id:
            done.add(str(case_id))
    return done


def load_case_ids_from_file(path: str | Path | None) -> list[str]:
    if not path:
        return []
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(line)
    return rows


def missing_field_counts(rows: list[dict[str, Any]], expected_fields: list[str]) -> dict[str, int]:
    counts = {field: 0 for field in expected_fields}
    for row in rows:
        content = row.get("content") or {}
        for field in expected_fields:
            value = content.get(field)
            if value in (None, [], "", "insufficient_evidence"):
                counts[field] += 1
    return counts


def main() -> int:
    args = parse_args()
    source_root = resolve_path(args.source_root)
    patient_output = resolve_path(args.patient_output)
    hidden_output = resolve_path(args.hidden_output)
    memory_output = resolve_path(args.memory_output)
    bridge_output = resolve_path(args.bridge_output)
    output_paths = {
        "patient_persona_profile": patient_output,
        "hidden_patient_profile_pattern": hidden_output,
        "historical_case_memory_seed": memory_output,
        "report_to_conversation_bridge_pattern": bridge_output,
    }
    failure_path = resolve_path(args.failure_log)
    manifest_path = resolve_path(args.manifest)
    qa_path = resolve_path(args.qa_report)

    if args.overwrite:
        for path in [*output_paths.values(), failure_path, manifest_path, qa_path]:
            if path.exists():
                path.unlink()

    rows = read_json(source_root / args.source_file)
    indexed_rows = list(enumerate(rows))
    file_case_ids = load_case_ids_from_file(args.case_id_file)
    requested_case_ids = list(args.case_ids or []) + file_case_ids
    if requested_case_ids:
        allow = set(requested_case_ids)
        indexed_rows = [(i, row) for i, row in indexed_rows if str(row.get("id")) in allow]
    if args.limit_cases is not None:
        indexed_rows = indexed_rows[: args.limit_cases]

    extraction_config = load_yaml(resolve_path(args.extraction_config))
    local_model_config = load_yaml(resolve_path(args.model_config))
    model_config = merged_model_config(extraction_config, local_model_config)
    if args.max_output_tokens is not None:
        model_config["max_output_tokens"] = int(args.max_output_tokens)
    parse_retries = args.parse_retries if args.parse_retries is not None else parse_retry_attempts(extraction_config, local_model_config)
    max_prompt_tokens = args.max_prompt_tokens if args.max_prompt_tokens is not None else context_max_prompt_tokens(extraction_config, local_model_config)
    system_prompt = load_prompt(resolve_path(args.prompt))
    client = OpenAICompatibleClient(model_config)

    processed = completed_cases(patient_output, failure_path, resume=args.resume)
    counters = Counter()
    prompt_audits = []

    for case_index, row in indexed_rows:
        case_uuid = str(row.get("id"))
        if case_uuid in processed:
            counters["skipped_resume"] += 1
            continue
        payload, adaptive_budget = adaptive_payload(
            row,
            case_index=case_index,
            system_prompt=system_prompt,
            max_prompt_tokens=max_prompt_tokens,
            compact_target_prompt_tokens=args.compact_target_prompt_tokens,
            max_turns=args.max_turns,
            max_turn_chars=args.max_turn_chars,
            max_report_item_chars=args.max_report_item_chars,
        )
        prompt_audit = {
            "case_id": case_uuid,
            "case_index": case_index,
            "full_conversation_turn_count": len(row.get("conversation") or []),
            **adaptive_budget,
            "input_compaction": payload.get("input_compaction"),
        }
        prompt_audits.append(prompt_audit)
        if prompt_audit["over_budget"]:
            append_jsonl(
                failure_path,
                {
                    "case_id": case_uuid,
                    "case_index": case_index,
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
            for knowledge_type, output_path in output_paths.items():
                append_jsonl(
                    output_path,
                    wrap_unit(
                        case_row=row,
                        case_index=case_index,
                        knowledge_type=knowledge_type,
                        content=result.get(knowledge_type) or {},
                        prompt_audit=prompt_audit,
                        prompt_path=str(resolve_path(args.prompt)),
                    ),
                )
            counters["written_cases"] += 1
            print(
                json.dumps(
                    {
                        "event": "case_written",
                        "written_cases": counters["written_cases"],
                        "case_index": case_index,
                        "case_id": case_uuid,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except PromptBudgetExceededError as exc:
            append_jsonl(
                failure_path,
                {
                    "case_id": case_uuid,
                    "case_index": case_index,
                    "failure": {"reason": "prompt_token_budget_exceeded", "details": exc.report},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            counters["prompt_token_budget_exceeded"] += 1
            print(
                json.dumps(
                    {
                        "event": "case_failed_budget",
                        "case_index": case_index,
                        "case_id": case_uuid,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            append_jsonl(
                failure_path,
                {
                    "case_id": case_uuid,
                    "case_index": case_index,
                    "failure": {"reason": type(exc).__name__, "details": str(exc)},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            counters["failed"] += 1
            print(
                json.dumps(
                    {
                        "event": "case_failed_exception",
                        "case_index": case_index,
                        "case_id": case_uuid,
                        "error_type": type(exc).__name__,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    output_rows = {name: read_jsonl(path) for name, path in output_paths.items()}
    failures = read_jsonl(failure_path)
    subset_mode = bool(requested_case_ids or args.limit_cases is not None)
    attempted_all_cases = len(indexed_rows) == len(rows) and not subset_mode
    no_failures = counters["failed"] == 0 and counters["prompt_token_budget_exceeded"] == 0
    expected_written = len(rows) if attempted_all_cases else len(indexed_rows)
    complete_output_counts = all(len(items) == expected_written for items in output_rows.values()) if no_failures else False
    status = "complete" if attempted_all_cases and no_failures and complete_output_counts else "partial"

    manifest = {
        "schema_version": "theraskill_anna_cpsycound_persona_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": status,
        "source_root": str(source_root),
        "source_files": {
            args.source_file: {"path": str(source_root / args.source_file), "sha256": file_sha256(source_root / args.source_file)},
            "README.md": {"path": str(source_root / "README.md"), "sha256": file_sha256(source_root / "README.md")},
            str(resolve_path(args.prompt)): {"path": str(resolve_path(args.prompt)), "sha256": file_sha256(resolve_path(args.prompt))},
        },
        "case_count_total": len(rows),
        "case_count_attempted": len(indexed_rows),
        "subset_mode": subset_mode,
        "written_cases": counters["written_cases"],
        "failure_rows": len(failures),
        "projection_policy": "LLM extraction over single Anna-CPsyCounD synthetic cases; portrait/report/dialogue jointly used to build patient profile, hidden profile, historical memory seed and report-dialogue bridge knowledge.",
        "prompt_audit_summary": {
            "count": len(prompt_audits),
            "max_estimated_prompt_tokens": max((item["estimated_prompt_tokens"] for item in prompt_audits), default=0),
            "over_budget_count": sum(1 for item in prompt_audits if item["over_budget"]),
            "balanced_compaction_count": sum(
                1
                for item in prompt_audits
                if ((item.get("input_compaction") or {}).get("strategy") == "balanced_head_tail")
            ),
        },
        "counters": dict(counters),
        "outputs": {
            name: {"path": str(path), "sha256": file_sha256(path), "row_count": len(output_rows[name])}
            for name, path in output_paths.items()
        },
        "failure_log": {"path": str(failure_path), "sha256": file_sha256(failure_path)},
    }
    write_json(manifest_path, manifest)

    qa = {
        "schema_version": "theraskill_anna_cpsycound_persona_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": status,
        "row_counts": {name: len(rows_) for name, rows_ in output_rows.items()},
        "failure_count": len(failures),
        "prompt_audit_summary": manifest["prompt_audit_summary"],
        "missing_field_counts": {
            name: missing_field_counts(output_rows[name], EXPECTED_FIELDS[name])
            for name in output_rows
        },
        "issues": failures,
        "issues_count": len(failures),
    }
    write_json(qa_path, qa)
    print(
        json.dumps(
            {
                "status": qa["status"],
                "row_counts": qa["row_counts"],
                "failure_count": qa["failure_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not no_failures:
        return 1
    return 0 if qa["status"] == "complete" or subset_mode else 1


if __name__ == "__main__":
    raise SystemExit(main())
