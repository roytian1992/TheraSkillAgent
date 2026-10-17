#!/usr/bin/env python3
"""Run targeted, independently verified LLM enrichment for patient persona v2."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from io_utils import file_sha256, read_json, write_json, write_jsonl  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    PATIENT_PERSONA_ENRICHMENT_PROMPT_PATH,
    PATIENT_PERSONA_ENRICHMENT_VERIFY_PROMPT_PATH,
    SIMULATION_PERSONA_ENRICHMENT_FAILURE_PATH,
    SIMULATION_PERSONA_ENRICHMENT_MANIFEST_PATH,
    SIMULATION_PERSONA_ENRICHMENT_OUTPUT_PATH,
    SIMULATION_PERSONA_ENRICHMENT_QA_PATH,
    SIMULATION_PERSONA_V2_DIR,
    SIMULATION_RUNTIME_PATIENT_CASES_DIR,
)
from domains.theraskill.theraskill_llm import (  # noqa: E402
    OpenAICompatibleClient,
    chat_json_with_repair,
    load_prompt,
    load_yaml,
    merged_model_config,
    parse_retry_attempts,
    prompt_budget_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
ALLOWED_ORIGINS = {"evidence_derived", "bounded_inference", "synthetic_fill"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_SENSITIVITY = {"surface", "moderate", "sensitive"}
ALLOWED_PRINCIPLE_CATEGORIES = {
    "disclosure_behavior",
    "alliance_and_rupture",
    "resistance_and_readiness",
    "communication_style",
}
FORBIDDEN_CLINICAL_TERMS = (
    "诊断", "共病", "自杀", "自伤", "暴力", "虐待", "创伤", "住院", "药物", "违法"
)
CONFLICT_ENTITY_TERMS = (
    "丈夫", "妻子", "男友", "女友", "配偶", "孩子", "儿子", "女儿", "母亲", "父亲",
    "舅父", "婚姻", "出轨", "阴茎", "火灾", "车祸",
)
PATCH_ARRAY_FIELDS = (
    "life_history_events",
    "relationship_additions",
    "rupture_triggers",
    "disclosure_adjustments",
    "behavioral_principles",
)
COMM_ARRAY_FIELDS = (
    "emotional_expression",
    "hesitation_and_avoidance",
    "advice_response_style",
    "natural_variation_examples",
)
COMM_STRING_FIELDS = (
    "response_length",
    "vocabulary_and_abstraction",
    "narrative_organization",
    "initiative_level",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", default=str(SIMULATION_RUNTIME_PATIENT_CASES_DIR))
    parser.add_argument("--persona-dir", default=str(SIMULATION_PERSONA_V2_DIR))
    parser.add_argument("--prompt", default=str(PATIENT_PERSONA_ENRICHMENT_PROMPT_PATH))
    parser.add_argument("--verify-prompt", default=str(PATIENT_PERSONA_ENRICHMENT_VERIFY_PROMPT_PATH))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--output", default=str(SIMULATION_PERSONA_ENRICHMENT_OUTPUT_PATH))
    parser.add_argument("--failure-log", default=str(SIMULATION_PERSONA_ENRICHMENT_FAILURE_PATH))
    parser.add_argument("--manifest", default=str(SIMULATION_PERSONA_ENRICHMENT_MANIFEST_PATH))
    parser.add_argument("--qa-report", default=str(SIMULATION_PERSONA_ENRICHMENT_QA_PATH))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-prompt-tokens", type=int, default=12000)
    parser.add_argument("--compact-trigger-chars", type=int, default=10000)
    parser.add_argument("--limit-cases", type=int)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--quality-retries", type=int, default=1)
    parser.add_argument("--conflict-aware-conservative", action="store_true")
    return parser.parse_args()


def clean_text(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] if limit is not None else text


def clean_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return list(dict.fromkeys(text for item in values if (text := clean_text(item))))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any], lock: threading.Lock) -> None:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()


def balanced_list(values: list[Any], limit: int) -> list[Any]:
    if len(values) <= limit:
        return values
    head = (limit + 1) // 2
    tail = limit - head
    return [*values[:head], *values[-tail:]] if tail else values[:head]


def compact_value(value: Any, *, list_limit: int, text_limit: int) -> Any:
    if isinstance(value, dict):
        return {
            str(key): compact_value(child, list_limit=list_limit, text_limit=text_limit)
            for key, child in value.items()
            if key not in {"quality_status", "case_id", "case_index", "source_id"}
        }
    if isinstance(value, list):
        selected = balanced_list(value, list_limit)
        return [compact_value(item, list_limit=list_limit, text_limit=text_limit) for item in selected]
    if isinstance(value, str):
        return clean_text(value, text_limit)
    return value


def target_slots(persona: dict[str, Any]) -> list[str]:
    warnings = set((persona.get("quality") or {}).get("warnings") or [])
    slots = [
        "communication_profile",
        "life_history_events",
        "disclosure_adjustments",
        "behavioral_principles",
    ]
    if "interpersonal_context_requires_R2_enrichment" in warnings:
        slots.append("relationship_additions.interpersonal_context")
    if "relationship_context_requires_R2_enrichment" in warnings:
        slots.append("relationship_additions.relationship_context")
    if "rupture_triggers_require_R2_enrichment" in warnings:
        slots.append("rupture_triggers")
    return slots


def evidence_source(bundle: dict[str, Any]) -> dict[str, Any]:
    layer = bundle.get("authoritative_layer") or {}
    return {
        "demographic_profile": (layer.get("visible_profile") or {}).get("demographic_profile") or {},
        "visible_profile": layer.get("visible_profile") or {},
        "hidden_profile": layer.get("hidden_profile") or {},
        "continuity_seed": layer.get("continuity_seed") or {},
        "patient_formulation": layer.get("patient_formulation") or {},
        "report_dialogue_bridge": layer.get("report_dialogue_bridge") or {},
        "runtime_state_seed": bundle.get("runtime_state_seed") or {},
    }


def build_payload(
    bundle: dict[str, Any],
    persona: dict[str, Any],
    system_prompt: str,
    max_prompt_tokens: int,
    compact_trigger_chars: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = evidence_source(bundle)
    source_chars = len(json.dumps(source, ensure_ascii=False))
    attempts = [(10, 360), (8, 300), (6, 240), (4, 180), (3, 140)]
    if source_chars <= compact_trigger_chars:
        attempts.insert(0, (1000, 2000))
    last_payload: dict[str, Any] = {}
    last_report: dict[str, Any] = {}
    for list_limit, text_limit in attempts:
        compacted = compact_value(source, list_limit=list_limit, text_limit=text_limit)
        payload = {
            "task": "targeted_patient_persona_v2_enrichment",
            "case_id": bundle.get("case_id"),
            "target_slots": target_slots(persona),
            "source_evidence": compacted,
            "existing_disclosure_fact_contents": balanced_list(
                [node.get("content") for node in (persona.get("disclosure_graph") or {}).get("nodes") or []],
                80,
            ),
            "existing_communication_counts": {
                field: len((persona.get("communication_profile") or {}).get(field) or [])
                for field in COMM_ARRAY_FIELDS
            },
            "forbidden_new_clinical_content": list(FORBIDDEN_CLINICAL_TERMS),
            "input_compaction": {
                "strategy": "balanced_head_tail" if source_chars > compact_trigger_chars else "none_required",
                "source_chars": source_chars,
                "list_item_cap": list_limit,
                "text_char_cap": text_limit,
            },
        }
        report = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
        last_payload, last_report = payload, report
        if not report["over_budget"]:
            return payload, report
    return last_payload, last_report


def normalize_item(item: Any) -> dict[str, Any]:
    row = item if isinstance(item, dict) else {}
    return {
        "content": clean_text(row.get("content"), 500),
        "origin": clean_text(row.get("origin")),
        "confidence": clean_text(row.get("confidence")),
        "evidence_paths": clean_list(row.get("evidence_paths")),
    }


def normalize_patch(raw: dict[str, Any], case_id: str) -> dict[str, Any]:
    comm_raw = raw.get("communication_profile") or {}
    communication = {field: clean_text(comm_raw.get(field), 300) for field in COMM_STRING_FIELDS}
    for field in COMM_ARRAY_FIELDS:
        communication[field] = [normalize_item(item) for item in (comm_raw.get(field) or [])]
    events = []
    for item in raw.get("life_history_events") or []:
        item = item if isinstance(item, dict) else {}
        events.append(
            {
                "relative_time": clean_text(item.get("relative_time"), 120),
                "event_type": clean_text(item.get("event_type"), 120),
                **normalize_item(item),
            }
        )
    disclosure = []
    for item in raw.get("disclosure_adjustments") or []:
        item = item if isinstance(item, dict) else {}
        disclosure.append(
            {
                "fact_content": clean_text(item.get("fact_content"), 500),
                "sensitivity": clean_text(item.get("sensitivity")),
                "facilitating_conditions": clean_list(item.get("facilitating_conditions")),
                "blocking_conditions": clean_list(item.get("blocking_conditions")),
            }
        )
    principles = []
    for item in raw.get("behavioral_principles") or []:
        item = item if isinstance(item, dict) else {}
        principles.append(
            {
                "category": clean_text(item.get("category")),
                "when": clean_text(item.get("when"), 500),
                "then": clean_text(item.get("then"), 500),
                "unless": clean_text(item.get("unless"), 500),
                "origin": clean_text(item.get("origin")),
                "evidence_paths": clean_list(item.get("evidence_paths")),
            }
        )
    return {
        "case_id": case_id,
        "communication_profile": communication,
        "life_history_events": events,
        "relationship_additions": [normalize_item(item) for item in raw.get("relationship_additions") or []],
        "rupture_triggers": [normalize_item(item) for item in raw.get("rupture_triggers") or []],
        "disclosure_adjustments": disclosure,
        "behavioral_principles": principles,
    }


def sanitize_patch(
    patch: dict[str, Any],
    payload: dict[str, Any],
    *,
    conflict_aware_conservative: bool = False,
) -> dict[str, Any]:
    """Apply conservative, non-clinical fallbacks before independent verification."""
    target_set = set(payload.get("target_slots") or [])
    if not any(slot.startswith("relationship_additions") for slot in target_set):
        patch["relationship_additions"] = []
    if "rupture_triggers" not in target_set:
        patch["rupture_triggers"] = []

    allowed_contents = set(payload.get("existing_disclosure_fact_contents") or [])
    patch["disclosure_adjustments"] = [
        item
        for item in patch.get("disclosure_adjustments") or []
        if item.get("fact_content") in allowed_contents
        and item.get("sensitivity") in ALLOWED_SENSITIVITY
    ]

    evidence_text = json.dumps(payload.get("source_evidence") or {}, ensure_ascii=False)
    for field in ("life_history_events", "relationship_additions", "rupture_triggers"):
        patch[field] = [
            item
            for item in patch.get(field) or []
            if not any(
                term in (item.get("content") or "") and term not in evidence_text
                for term in FORBIDDEN_CLINICAL_TERMS
            )
        ]

    communication = patch["communication_profile"]
    if conflict_aware_conservative:
        conservative_strings = {
            "response_length": "简短到中等，随问题开放程度变化",
            "vocabulary_and_abstraction": "以具体、日常表达为主",
            "narrative_organization": "围绕当前问题逐步展开",
            "initiative_level": "根据问题清晰度适度回应",
        }
        for field, fallback in conservative_strings.items():
            if any(term in communication.get(field, "") for term in CONFLICT_ENTITY_TERMS):
                communication[field] = fallback
        for field in ("emotional_expression", "hesitation_and_avoidance", "advice_response_style"):
            communication[field] = [
                item
                for item in communication.get(field) or []
                if not any(term in (item.get("content") or "") for term in CONFLICT_ENTITY_TERMS)
            ]
        response_length = communication.get("response_length") or "随问题开放度自然变化"
        narrative = communication.get("narrative_organization") or "保持当前叙述倾向"
        communication["natural_variation_examples"] = [
            {
                "content": f"回答长度可在“{response_length}”范围内随问题开放度变化，不固定使用同一句式",
                "origin": "synthetic_fill",
                "confidence": "low",
                "evidence_paths": [],
            },
            {
                "content": f"叙述保持“{narrative}”倾向，可在不改变事实内容的前提下调整措辞",
                "origin": "synthetic_fill",
                "confidence": "low",
                "evidence_paths": [],
            },
        ]
        patch["life_history_events"] = []
        patch["relationship_additions"] = [
            item
            for item in patch.get("relationship_additions") or []
            if any(slot.startswith("relationship_additions") for slot in target_set)
            and not any(term in (item.get("content") or "") for term in CONFLICT_ENTITY_TERMS)
        ]
        patch["disclosure_adjustments"] = []
        patch["rupture_triggers"] = [
            item
            for item in patch.get("rupture_triggers") or []
            if "rupture_triggers" in target_set
            and not any(term in (item.get("content") or "") for term in CONFLICT_ENTITY_TERMS)
        ]
        patch["behavioral_principles"] = [
            item
            for item in patch.get("behavioral_principles") or []
            if not any(
                term in " ".join(
                    str(item.get(field) or "") for field in ("when", "then", "unless")
                )
                for term in (*CONFLICT_ENTITY_TERMS, *FORBIDDEN_CLINICAL_TERMS)
            )
        ]
        return patch
    if not communication.get("natural_variation_examples"):
        response_length = communication.get("response_length") or "随问题开放度自然变化"
        narrative = communication.get("narrative_organization") or "保持当前叙述倾向"
        communication["natural_variation_examples"] = [
            {
                "content": f"回答长度可在“{response_length}”范围内随问题的开放程度变化，不逐轮使用相同句式",
                "origin": "synthetic_fill",
                "confidence": "low",
                "evidence_paths": [],
            },
            {
                "content": f"叙述保持“{narrative}”倾向；触及敏感主题时可自然地变得更简短或犹豫",
                "origin": "synthetic_fill",
                "confidence": "low",
                "evidence_paths": [],
            },
        ]
    return patch


def validate_evidence_item(item: dict[str, Any], path: str, evidence_text: str, issues: list[str]) -> None:
    if not item.get("content"):
        issues.append(f"{path}.content_empty")
    if item.get("origin") not in ALLOWED_ORIGINS:
        issues.append(f"{path}.origin_invalid")
    if item.get("confidence") not in ALLOWED_CONFIDENCE:
        issues.append(f"{path}.confidence_invalid")
    content = item.get("content") or ""
    if any(term in content and term not in evidence_text for term in FORBIDDEN_CLINICAL_TERMS):
        issues.append(f"{path}.unsupported_clinical_content")
    if item.get("origin") == "synthetic_fill" and not path.startswith(
        "communication_profile.natural_variation_examples["
    ):
        issues.append(f"{path}.synthetic_fill_not_allowed")


def validate_patch(patch: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    targets = set(payload.get("target_slots") or [])
    if patch.get("case_id") != payload.get("case_id"):
        issues.append("case_id_mismatch")
    evidence_text = json.dumps(payload.get("source_evidence") or {}, ensure_ascii=False)
    communication = patch.get("communication_profile") or {}
    for field in COMM_STRING_FIELDS:
        if not communication.get(field):
            issues.append(f"communication_profile.{field}.empty")
    for field in COMM_ARRAY_FIELDS:
        rows = communication.get(field) or []
        existing_count = int((payload.get("existing_communication_counts") or {}).get(field) or 0)
        if not rows and existing_count == 0:
            issues.append(f"communication_profile.{field}.empty")
        for index, item in enumerate(rows):
            validate_evidence_item(item, f"communication_profile.{field}[{index}]", evidence_text, issues)
    for field in ("relationship_additions", "rupture_triggers"):
        for index, item in enumerate(patch.get(field) or []):
            validate_evidence_item(item, f"{field}[{index}]", evidence_text, issues)
    if any(slot.startswith("relationship_additions") for slot in targets) and not patch.get("relationship_additions"):
        issues.append("relationship_additions.required_target_empty")
    if "rupture_triggers" in targets and not patch.get("rupture_triggers"):
        issues.append("rupture_triggers.required_target_empty")
    for index, item in enumerate(patch.get("life_history_events") or []):
        validate_evidence_item(item, f"life_history_events[{index}]", evidence_text, issues)
        if not item.get("relative_time") or not item.get("event_type"):
            issues.append(f"life_history_events[{index}].time_or_type_empty")
    allowed_contents = set(payload.get("existing_disclosure_fact_contents") or [])
    for index, item in enumerate(patch.get("disclosure_adjustments") or []):
        if item.get("fact_content") not in allowed_contents:
            issues.append(f"disclosure_adjustments[{index}].unknown_fact_content")
        if item.get("sensitivity") not in ALLOWED_SENSITIVITY:
            issues.append(f"disclosure_adjustments[{index}].invalid_sensitivity")
    for index, item in enumerate(patch.get("behavioral_principles") or []):
        if item.get("category") not in ALLOWED_PRINCIPLE_CATEGORIES:
            issues.append(f"behavioral_principles[{index}].invalid_category")
        if not item.get("when") or not item.get("then"):
            issues.append(f"behavioral_principles[{index}].condition_empty")
        if item.get("origin") not in {"evidence_derived", "bounded_inference"}:
            issues.append(f"behavioral_principles[{index}].origin_invalid")
    if len(patch.get("behavioral_principles") or []) < 2:
        issues.append("behavioral_principles.too_few")
    return issues


def worker(
    bundle_path: Path,
    persona_dir: Path,
    model_config: dict[str, Any],
    system_prompt: str,
    verify_prompt: str,
    parse_retries: int,
    max_prompt_tokens: int,
    compact_trigger_chars: int,
    quality_retries: int,
    conflict_aware_conservative: bool,
) -> dict[str, Any]:
    bundle = read_json(bundle_path)
    case_id = clean_text(bundle.get("case_id"))
    persona = read_json(persona_dir / f"{case_id}.json")
    payload, enrichment_audit = build_payload(
        bundle, persona, system_prompt, max_prompt_tokens, compact_trigger_chars
    )
    if conflict_aware_conservative:
        required_rupture = "rupture_triggers" in payload["target_slots"]
        required_relationship_slots = [
            slot
            for slot in payload["target_slots"]
            if slot.startswith("relationship_additions")
        ]
        payload["target_slots"] = ["communication_profile", "behavioral_principles"]
        payload["target_slots"].extend(required_relationship_slots)
        if required_rupture:
            payload["target_slots"].append("rupture_triggers")
        payload["conflict_aware_conservative"] = {
            "enabled": True,
            "instruction": (
                "源证据存在身份、关系、主体或时间线冲突。只生成不依赖冲突事实的沟通核心字段；"
                "不得尝试调和或补全冲突。可疑的具体事件、人物、关系、诊断、风险或治疗内容必须省略。"
            ),
        }
        enrichment_audit = prompt_budget_report(system_prompt, payload, max_prompt_tokens)
    if enrichment_audit["over_budget"]:
        raise ValueError(f"prompt_budget_exceeded:{enrichment_audit}")
    client = OpenAICompatibleClient(model_config)
    retry_feedback: list[dict[str, Any]] = []
    for quality_attempt in range(quality_retries + 1):
        call_payload = dict(payload)
        if retry_feedback:
            call_payload["verifier_retry_feedback"] = retry_feedback[-1]
        raw_patch = chat_json_with_repair(
            client,
            system_prompt,
            call_payload,
            parse_retries=parse_retries,
            max_prompt_tokens=max_prompt_tokens,
            force_json_first=True,
        )
        patch = sanitize_patch(
            normalize_patch(raw_patch, case_id),
            payload,
            conflict_aware_conservative=conflict_aware_conservative,
        )
        deterministic_issues = validate_patch(patch, payload)
        verify_payload = {
            "task": "verify_patient_persona_v2_enrichment",
            "case_id": case_id,
            "target_slots": payload["target_slots"],
            "source_evidence": payload["source_evidence"],
            "forbidden_new_clinical_content": payload["forbidden_new_clinical_content"],
            "candidate_patch": patch,
            "deterministic_issues": deterministic_issues,
        }
        if conflict_aware_conservative:
            verify_payload["conflict_aware_conservative"] = {
                "enabled": True,
                "contract": (
                    "只检查 target_slots 中的字段。未列为 target 的数组允许为空且不得据此判错。"
                    "合法 origin 只有 evidence_derived、bounded_inference、synthetic_fill；"
                    "不得要求 informed_inference。natural_variation_examples 中不新增事实的"
                    "纯表达形式描述允许 synthetic_fill。"
                ),
            }
        verify_audit = prompt_budget_report(verify_prompt, verify_payload, max_prompt_tokens)
        if verify_audit["over_budget"]:
            raise ValueError(f"verifier_prompt_budget_exceeded:{verify_audit}")
        verdict = chat_json_with_repair(
            client,
            verify_prompt,
            verify_payload,
            parse_retries=parse_retries,
            max_prompt_tokens=max_prompt_tokens,
            force_json_first=True,
        )
        verifier_pass = (
            clean_text(verdict.get("case_id")) == case_id
            and clean_text(verdict.get("verdict")) == "pass"
            and not (verdict.get("issues") or [])
        )
        if not deterministic_issues and verifier_pass:
            return {
                "schema_version": "theraskill_patient_persona_v2_enrichment_v1",
                "case_id": case_id,
                "patch": patch,
                "verification": verdict,
                "provenance": {
                    "model": model_config.get("name"),
                    "model_base_url": model_config.get("base_url"),
                    "enrichment_prompt_sha256": file_sha256(PATIENT_PERSONA_ENRICHMENT_PROMPT_PATH),
                    "verify_prompt_sha256": file_sha256(PATIENT_PERSONA_ENRICHMENT_VERIFY_PROMPT_PATH),
                    "quality_attempts": quality_attempt + 1,
                    "prompt_audit": enrichment_audit,
                    "verify_prompt_audit": verify_audit,
                    "input_compaction": payload["input_compaction"],
                    "enrichment_strategy": (
                        "conflict_aware_conservative"
                        if conflict_aware_conservative
                        else "standard"
                    ),
                },
            }
        retry_feedback.append(
            {
                "deterministic_issues": deterministic_issues,
                "verifier_issues": verdict.get("issues") or [],
                "retry_fields": verdict.get("retry_fields") or [],
            }
        )
    raise ValueError(f"quality_verification_failed:{retry_feedback[-1] if retry_feedback else {}}")


def main() -> int:
    args = parse_args()
    if args.workers < 16:
        raise SystemExit("--workers must be at least 16 for an active model port")
    output_path = Path(args.output)
    failure_path = Path(args.failure_log)
    for path in (output_path, failure_path, Path(args.manifest), Path(args.qa_report)):
        path.parent.mkdir(parents=True, exist_ok=True)
    if args.overwrite:
        for path in (output_path, failure_path, Path(args.manifest), Path(args.qa_report)):
            if path.exists():
                path.unlink()

    extraction_config = load_yaml(args.extraction_config)
    local_model_config = load_yaml(args.model_config)
    model_config = merged_model_config(extraction_config, local_model_config)
    parse_retries = parse_retry_attempts(extraction_config, local_model_config)
    system_prompt = load_prompt(args.prompt)
    verify_prompt = load_prompt(args.verify_prompt)
    bundle_paths = sorted(Path(args.bundle_dir).glob("*.json"))
    if args.case_ids:
        allowed = set(args.case_ids)
        bundle_paths = [path for path in bundle_paths if path.stem in allowed]
    if args.limit_cases is not None:
        bundle_paths = bundle_paths[: args.limit_cases]

    completed = {row.get("case_id") for row in read_jsonl(output_path)} if args.resume else set()
    selected = [path for path in bundle_paths if path.stem not in completed]
    lock = threading.Lock()
    counter: Counter[str] = Counter()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                worker,
                path,
                Path(args.persona_dir),
                model_config,
                system_prompt,
                verify_prompt,
                parse_retries,
                args.max_prompt_tokens,
                args.compact_trigger_chars,
                args.quality_retries,
                args.conflict_aware_conservative,
            ): path
            for path in selected
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                row = future.result()
                append_jsonl(output_path, row, lock)
                counter["complete"] += 1
            except Exception as exc:  # noqa: BLE001
                append_jsonl(
                    failure_path,
                    {
                        "case_id": path.stem,
                        "error_type": type(exc).__name__,
                        "details": str(exc),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    lock,
                )
                counter["failed"] += 1
            finished = counter["complete"] + counter["failed"]
            if finished % 25 == 0 or finished == len(selected):
                print(
                    json.dumps(
                        {
                            "event": "progress",
                            "finished": finished,
                            "selected": len(selected),
                            "complete": counter["complete"],
                            "failed": counter["failed"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    output_rows = read_jsonl(output_path)
    unique_cases = {row.get("case_id") for row in output_rows}
    duplicate_case_count = len(output_rows) - len(unique_cases)
    invalid_verification_count = sum(
        (row.get("verification") or {}).get("verdict") != "pass" for row in output_rows
    )
    all_prompt_tokens = [
        int(((row.get("provenance") or {}).get("prompt_audit") or {}).get("estimated_prompt_tokens") or 0)
        for row in output_rows
    ]
    all_compaction_distribution = Counter(
        str(((row.get("provenance") or {}).get("input_compaction") or {}).get("strategy") or "missing")
        for row in output_rows
    )
    all_verify_prompt_tokens = [
        int(((row.get("provenance") or {}).get("verify_prompt_audit") or {}).get("estimated_prompt_tokens") or 0)
        for row in output_rows
    ]
    enrichment_prompt_sha_distribution = Counter(
        str((row.get("provenance") or {}).get("enrichment_prompt_sha256") or "missing")
        for row in output_rows
    )
    verify_prompt_sha_distribution = Counter(
        str((row.get("provenance") or {}).get("verify_prompt_sha256") or "missing")
        for row in output_rows
    )
    enrichment_strategy_distribution = Counter(
        str((row.get("provenance") or {}).get("enrichment_strategy") or "standard")
        for row in output_rows
    )
    latest_failure_by_case: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(failure_path):
        case_id = str(row.get("case_id") or "")
        if case_id and case_id not in unique_cases:
            latest_failure_by_case[case_id] = row
    failures = [latest_failure_by_case[case_id] for case_id in sorted(latest_failure_by_case)]
    write_jsonl(failure_path, failures, overwrite=True, sort_keys=True)
    subset_mode = bool(args.case_ids or args.limit_cases is not None)
    expected = len(bundle_paths)
    status = "complete" if (
        not subset_mode
        and len(unique_cases) == expected
        and len(output_rows) == expected
        and duplicate_case_count == 0
        and invalid_verification_count == 0
        and all(value <= 12000 for value in all_prompt_tokens)
        and all(value <= 12000 for value in all_verify_prompt_tokens)
        and not failures
    ) else "partial"
    created_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "theraskill_patient_persona_v2_enrichment_manifest_v1",
        "created_at": created_at,
        "status": status,
        "subset_mode": subset_mode,
        "input_case_count": expected,
        "selected_this_run": len(selected),
        "workers": args.workers,
        "model": {"name": model_config.get("name"), "base_url": model_config.get("base_url")},
        "prompts": {
            "enrichment": {
                "path": args.prompt,
                "current_sha256": file_sha256(args.prompt),
                "observed_sha256_distribution": dict(enrichment_prompt_sha_distribution),
            },
            "verification": {
                "path": args.verify_prompt,
                "current_sha256": file_sha256(args.verify_prompt),
                "observed_sha256_distribution": dict(verify_prompt_sha_distribution),
            },
        },
        "context_policy": {
            "compact_trigger_chars": args.compact_trigger_chars,
            "max_prompt_tokens": args.max_prompt_tokens,
            "strategy": "balanced_head_tail",
        },
        "output": {"path": str(output_path), "sha256": file_sha256(output_path), "row_count": len(output_rows)},
        "failures": {"path": str(failure_path), "sha256": file_sha256(failure_path), "row_count": len(failures)},
    }
    write_json(args.manifest, manifest, overwrite=True)
    qa = {
        "schema_version": "theraskill_patient_persona_v2_enrichment_qa_v1",
        "created_at": created_at,
        "status": status,
        "input_case_count": expected,
        "output_row_count": len(output_rows),
        "unique_case_count": len(unique_cases),
        "duplicate_case_count": duplicate_case_count,
        "invalid_verification_count": invalid_verification_count,
        "failure_count": len(failures),
        "run_counters": dict(counter),
        "compaction_distribution": dict(all_compaction_distribution),
        "enrichment_strategy_distribution": dict(enrichment_strategy_distribution),
        "enrichment_prompt_sha256_distribution": dict(enrichment_prompt_sha_distribution),
        "verify_prompt_sha256_distribution": dict(verify_prompt_sha_distribution),
        "max_estimated_prompt_tokens": max(all_prompt_tokens, default=0),
        "max_estimated_verify_prompt_tokens": max(all_verify_prompt_tokens, default=0),
        "prompt_over_12000_count": sum(value > 12000 for value in all_prompt_tokens),
        "verify_prompt_over_12000_count": sum(value > 12000 for value in all_verify_prompt_tokens),
        "issues": failures[:1000],
        "issues_count": len(failures),
    }
    write_json(args.qa_report, qa, overwrite=True)
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    return 0 if (subset_mode or status == "complete") and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
