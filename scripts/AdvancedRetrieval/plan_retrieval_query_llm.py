#!/usr/bin/env python3
"""LLM-first retrieval query planner for TheraSkill advanced retrieval v3."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, write_json
from retrieval_surface_registry import PHASE6_SQLITE_DB_PATH, RETRIEVAL_QUERY_PLAN_V3_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_DB = PHASE6_SQLITE_DB_PATH
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_PROMPT = PROJECT_ROOT / "prompts/AdvancedRetrieval/retrieval_query_planner_zh_en.md"
DEFAULT_OUTPUT = RETRIEVAL_QUERY_PLAN_V3_PATH

ALLOWED_SKILLS = {
    "emotional-support",
    "risk-triage",
    "cognitive-reframing-support",
    "rebt-belief-work-support",
    "synthetic-dialogue-builder",
    "therapy-response-reviewer",
    "unknown",
}

ALLOWED_TASK_TYPES = {
    "risk_boundary_response",
    "support_response",
    "framework_guided_intervention",
    "knowledge_lookup",
    "response_review",
    "synthetic_construction",
    "unknown",
}

ALLOWED_LANGUAGES = {"zh", "en", "mixed", "unknown"}
ALLOWED_RISK_LEVELS = {"none_observed", "low", "moderate", "high", "imminent", "unknown"}
ALLOWED_RISK_DOMAINS = {
    "self_harm",
    "suicide",
    "harm_to_others",
    "abuse_or_ipv",
    "sexual_violence",
    "psychosis_like",
    "substance_or_medical",
    "severe_self_neglect",
    "none_observed",
}
ALLOWED_REQUIRED_ACTIONS = {
    "ordinary_support_allowed",
    "clarify_before_ordinary_support",
    "safety_first_response",
    "emergency_or_local_protocol",
    "practitioner_review_required",
}
ALLOWED_DEDUP_FAMILIES = {
    "clinical_risk_assessment",
    "method_rubric",
    "qa_psychoeducation",
    "source_inventory",
    "style_response",
    "synthetic_construction",
    "therapy_process",
}
ALLOWED_RISK_SCOPES = {"clinical_risk_or_assessment", "risk_related_text", "ordinary_or_unspecified"}
ALLOWED_DEFAULT_ROUTES = {
    "raw_passthrough_retrieval",
    "provenance_lookup_only",
    "accounting_only_requires_review",
    "reviewed_cluster_retrieval",
    "clinical_reviewed_cluster_retrieval_with_label_guard",
    "synthetic_construction_method_retrieval",
}
DEFAULT_ROUTE_ALIASES = {
    "clinical_reviewed_cluster_retrieval": "clinical_reviewed_cluster_retrieval_with_label_guard",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--skill", required=True, choices=sorted(ALLOWED_SKILLS - {"unknown"}))
    parser.add_argument("--query", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-degraded-fallback", action="store_true")
    parser.add_argument("--validation-attempts", type=int, default=2, help="bounded real-model attempts for malformed JSON or invalid metadata")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_prompt(path: Path, skill: str, query: str, inventory=None) -> tuple[str, str]:
    template = path.read_text(encoding="utf-8")
    available = json.dumps({key: sorted(values) for key, values in (inventory or {}).items()}, ensure_ascii=False, sort_keys=True)
    prompt = template.replace("{{index_inventory}}", available).replace("{{skill}}", skill).replace("{{query}}", query)
    prompt_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return prompt, prompt_hash


def chat_completion(config_path: Path, prompt: str) -> dict[str, Any]:
    config = read_yaml(config_path)
    if not config.get("model"):
        from domains.theraskill.theraskill_llm import OpenAICompatibleClient

        client = OpenAICompatibleClient({"provider": "hermes"})
        text = client.chat(
            "You are a JSON-only retrieval planning component. Return one valid JSON object and no markdown.",
            {"prompt": prompt},
            force_json_response=True,
            # Qwen3 otherwise spends most of the planner request in hidden
            # reasoning even though this component only needs a small JSON
            # classification.  Keep the planner deterministic and reserve
            # the full reasoning budget for the user-facing agent turn.
            extra_user_instruction="Return the JSON directly. /no_think",
        )
        return {"choices": [{"message": {"content": text}}]}
    cfg = config["model"]
    base_url = str(cfg["base_url"]).rstrip("/")
    payload: dict[str, Any] = {
        "model": cfg["name"],
        "messages": [
            {
                "role": "system",
                "content": "You are a JSON-only retrieval planning component. Return one valid JSON object and no markdown.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": float(cfg.get("temperature") or 0.0),
        "max_tokens": int(cfg.get("max_output_tokens") or 2048),
    }
    if cfg.get("response_format_json"):
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    retry_attempts = int(cfg.get("retry_attempts") or 0)
    timeout = int(cfg.get("timeout_seconds") or 120)
    last_error: Exception | None = None
    for attempt in range(retry_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt < retry_attempts:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"LLM planner request failed: {last_error!r}")


def extract_json_object(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("empty chat completion choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(str(part.get("text") if isinstance(part, dict) else part) for part in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty chat completion content")
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def load_index_value_inventory(db: Path, *, cache_path=None, trace=None) -> dict[str, set[str]]:
    from theraskill_agent.indexing.inventory import load_inventory
    return load_inventory(db, cache_path=cache_path, trace=trace)


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def clean_string_list(values: Any) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in ensure_list(values):
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def filter_allowed(values: Any, allowed: set[str], issues: list[str], path: str, *, allow_empty: bool = True) -> list[str]:
    result: list[str] = []
    for value in clean_string_list(values):
        if value in allowed:
            result.append(value)
        else:
            issues.append(f"invalid enum at {path}: {value}")
    if not result and not allow_empty:
        issues.append(f"missing required enum at {path}")
    return result


def filter_family_list(values: Any, issues: list[str], warnings: list[str], path: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in clean_string_list(values):
        if value in ALLOWED_DEDUP_FAMILIES:
            if value not in seen:
                seen.add(value)
                result.append(value)
        elif value in ALLOWED_DEFAULT_ROUTES or value in DEFAULT_ROUTE_ALIASES:
            warnings.append(f"ignored route value in family field at {path}: {value}")
        else:
            issues.append(f"invalid enum at {path}: {value}")
    return result


def filter_default_routes(values: Any, allowed: set[str], issues: list[str], warnings: list[str], path: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in clean_string_list(values):
        repaired = DEFAULT_ROUTE_ALIASES.get(value, value)
        if repaired != value:
            warnings.append(f"repaired route alias at {path}: {value} -> {repaired}")
        if repaired in allowed and repaired not in seen:
            seen.add(repaired)
            result.append(repaired)
        elif repaired not in allowed:
            issues.append(f"invalid enum at {path}: {value}")
    return result


def normalize_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, confidence))


def validate_plan(plan: dict[str, Any], requested_skill: str, query: str, inventory: dict[str, set[str]]) -> tuple[dict[str, Any], list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    normalized = dict(plan)
    normalized["schema_version"] = "theraskill_llm_retrieval_plan_v1"
    normalized["raw_query"] = str(normalized.get("raw_query") or query)
    normalized["detected_language"] = (normalized.get("detected_language") if normalized.get("detected_language") in ALLOWED_LANGUAGES else "unknown")

    skill_intent = dict(normalized.get("skill_intent") or {})
    skill_name = str(skill_intent.get("name") or requested_skill)
    if skill_name not in ALLOWED_SKILLS:
        issues.append(f"invalid enum at skill_intent.name: {skill_name}")
        skill_name = requested_skill
    normalized["skill_intent"] = {"name": skill_name, "confidence": normalize_confidence(skill_intent.get("confidence"))}

    task_type = str(normalized.get("task_type") or "unknown")
    if task_type not in ALLOWED_TASK_TYPES:
        issues.append(f"invalid enum at task_type: {task_type}")
        task_type = "unknown"
    normalized["task_type"] = task_type

    risk = dict(normalized.get("risk") or {})
    normalized["risk"] = {
        "domains": filter_allowed(risk.get("domains"), ALLOWED_RISK_DOMAINS, issues, "risk.domains"),
        "level": (risk.get("level") if risk.get("level") in ALLOWED_RISK_LEVELS else "unknown"),
        "required_action": (risk.get("required_action") if risk.get("required_action") in ALLOWED_REQUIRED_ACTIONS else "clarify_before_ordinary_support"),
        "evidence_boundary": str(risk.get("evidence_boundary") or "retrieve_relevant_evidence_only"),
    }
    if risk.get("level") and risk.get("level") not in ALLOWED_RISK_LEVELS:
        issues.append(f"invalid enum at risk.level: {risk.get('level')}")
    if risk.get("required_action") and risk.get("required_action") not in ALLOWED_REQUIRED_ACTIONS:
        issues.append(f"invalid enum at risk.required_action: {risk.get('required_action')}")

    normalized["therapy_frameworks"] = clean_string_list(normalized.get("therapy_frameworks"))
    target_families = dict(normalized.get("target_families") or {})
    normalized["target_families"] = {
        "required": filter_family_list(target_families.get("required"), issues, warnings, "target_families.required"),
        "preferred": filter_family_list(target_families.get("preferred"), issues, warnings, "target_families.preferred"),
        "excluded": filter_family_list(target_families.get("excluded"), issues, warnings, "target_families.excluded"),
    }

    filters = dict(normalized.get("metadata_filters") or {})
    normalized_filters = {
        "dedup_family": filter_allowed(filters.get("dedup_family"), ALLOWED_DEDUP_FAMILIES & inventory["dedup_family"], issues, "metadata_filters.dedup_family"),
        "risk_scope": filter_allowed(filters.get("risk_scope"), ALLOWED_RISK_SCOPES & inventory["risk_scope"], issues, "metadata_filters.risk_scope"),
        "default_route": filter_default_routes(filters.get("default_route"), ALLOWED_DEFAULT_ROUTES & inventory["default_route"], issues, warnings, "metadata_filters.default_route"),
        "primary_knowledge_type": filter_allowed(filters.get("primary_knowledge_type"), inventory["primary_knowledge_type"], issues, "metadata_filters.primary_knowledge_type"),
        "primary_evidence_tier": filter_allowed(filters.get("primary_evidence_tier"), inventory["primary_evidence_tier"], issues, "metadata_filters.primary_evidence_tier"),
        "language_keys": filter_allowed(filters.get("language_keys"), ALLOWED_LANGUAGES, issues, "metadata_filters.language_keys"),
        "quality_confidence_min": None,
    }
    normalized["metadata_filters"] = normalized_filters

    queries = dict(normalized.get("search_queries") or {})
    normalized["search_queries"] = {
        "bm25_queries": clean_string_list(queries.get("bm25_queries"))[:6],
        "dense_queries": clean_string_list(queries.get("dense_queries"))[:6],
        "label_queries": clean_string_list(queries.get("label_queries"))[:6],
    }
    if not any(normalized["search_queries"].values()):
        warnings.append("empty search query variants; raw query will be used by retriever")

    rerank = dict(normalized.get("rerank_preferences") or {})
    quota_hint = rerank.get("quota_hint") if isinstance(rerank.get("quota_hint"), dict) else {}
    normalized["rerank_preferences"] = {
        "must_include_families": filter_allowed(rerank.get("must_include_families"), ALLOWED_DEDUP_FAMILIES, issues, "rerank_preferences.must_include_families"),
        "quota_hint": quota_hint,
        "avoid_labels": clean_string_list(rerank.get("avoid_labels"))[:20],
    }
    normalized["safety_constraints"] = clean_string_list(normalized.get("safety_constraints"))[:20]
    uncertainty = dict(normalized.get("uncertainty") or {})
    normalized["uncertainty"] = {
        "ambiguous_intent": bool(uncertainty.get("ambiguous_intent", False)),
        "fallback_policy": str(uncertainty.get("fallback_policy") or "broaden_filters_then_retrieve"),
    }
    return normalized, issues, warnings


def fallback_plan(skill: str, query: str) -> dict[str, Any]:
    family_defaults = {
        "emotional-support": ["style_response", "therapy_process", "qa_psychoeducation"],
        "risk-triage": ["clinical_risk_assessment", "style_response", "qa_psychoeducation"],
        "cognitive-reframing-support": ["method_rubric", "qa_psychoeducation", "style_response"],
        "rebt-belief-work-support": ["method_rubric", "therapy_process", "qa_psychoeducation"],
        "synthetic-dialogue-builder": ["synthetic_construction", "therapy_process", "method_rubric"],
        "therapy-response-reviewer": ["therapy_process", "method_rubric", "synthetic_construction"],
    }
    task_defaults = {
        "emotional-support": "support_response",
        "risk-triage": "risk_boundary_response",
        "cognitive-reframing-support": "framework_guided_intervention",
        "rebt-belief-work-support": "framework_guided_intervention",
        "synthetic-dialogue-builder": "synthetic_construction",
        "therapy-response-reviewer": "response_review",
    }
    framework_defaults = {
        "cognitive-reframing-support": ["CBT"],
        "rebt-belief-work-support": ["REBT"],
    }
    label_defaults = {
        "emotional-support": ["empathy validation support alliance"],
        "risk-triage": ["suicide self_harm high imminent safety risk assessment"],
        "cognitive-reframing-support": ["cognitive reframe reframing_step thinking trap alternative thought"],
        "rebt-belief-work-support": ["REBT ABC belief disputation alternative belief reframing_step"],
        "synthetic-dialogue-builder": ["synthetic construction rationale dialogue methodology"],
        "therapy-response-reviewer": ["therapy process method rubric response review quality"],
    }
    families = family_defaults[skill]
    risk_scopes = ["clinical_risk_or_assessment", "risk_related_text"] if skill == "risk-triage" else []
    return {
        "schema_version": "theraskill_llm_retrieval_plan_v1",
        "raw_query": query,
        "detected_language": "unknown",
        "skill_intent": {"name": skill, "confidence": 0.0},
        "task_type": task_defaults[skill],
        "risk": {
            "domains": [],
            "level": "unknown",
            "required_action": "clarify_before_ordinary_support" if skill == "risk-triage" else "ordinary_support_allowed",
            "evidence_boundary": "degraded_fallback_retrieval_plan",
        },
        "therapy_frameworks": framework_defaults.get(skill, []),
        "target_families": {
            "required": families[:1],
            "preferred": families[1:],
            "excluded": ["source_inventory"],
        },
        "metadata_filters": {
            "dedup_family": families,
            "risk_scope": risk_scopes,
            "default_route": ["raw_passthrough_retrieval", "reviewed_cluster_retrieval", "clinical_reviewed_cluster_retrieval_with_label_guard"],
            "primary_knowledge_type": [],
            "primary_evidence_tier": [],
            "language_keys": [],
            "quality_confidence_min": None,
        },
        "search_queries": {
            "bm25_queries": [query],
            "dense_queries": [query],
            "label_queries": label_defaults.get(skill, []),
        },
        "rerank_preferences": {
            "must_include_families": families[:1],
            "quota_hint": {},
            "avoid_labels": [],
        },
        "safety_constraints": [
            "planner_degraded_fallback",
            "do_not_use_retrieved_items_as_final_diagnosis",
            "do_not_treat_synthetic_construction_as_observed_clinical_evidence",
        ],
        "uncertainty": {"ambiguous_intent": True, "fallback_policy": "broad_skill_policy_retrieval"},
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    prompt_path = Path(args.prompt)
    config_path = Path(args.model_config)
    from theraskill_agent.indexing.diagnostics import RetrievalTimings
    timings = RetrievalTimings()
    inventory_trace = {}
    inventory = timings.call('inventory', load_index_value_inventory, db,
                             cache_path=getattr(args, 'inventory_cache', None), trace=inventory_trace)
    prompt, prompt_hash = read_prompt(prompt_path, args.skill, args.query, inventory)
    trace: dict[str, Any] = {
        "schema_version": "theraskill_retrieval_planner_trace_v1",
        "created_at": utc_now(),
        "planner_model_config": str(config_path),
        "prompt_path": str(prompt_path),
        "prompt_sha256": prompt_hash,
        "planner_status": "unknown",
        "json_valid": False,
        "enum_valid": False,
        "fallback_used": False,
        "issues": [],
        "warnings": [],
        "attempts": [],
        "inventory_cache": inventory_trace,
        "inventory_sha256": hashlib.sha256(json.dumps({key: sorted(values) for key, values in inventory.items()}, sort_keys=True).encode()).hexdigest(),
        "request_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }
    try:
        attempts = max(1, min(int(getattr(args, 'validation_attempts', 2)), 3))
        feedback = ''
        for attempt in range(1, attempts + 1):
            attempt_trace = {'attempt': attempt, 'json_valid': False, 'enum_valid': False}
            try:
                attempt_started = time.perf_counter()
                response = timings.call('model', chat_completion, config_path, prompt + feedback)
                attempt_trace['model_ms'] = round((time.perf_counter() - attempt_started) * 1000, 3)
                raw_plan = extract_json_object(response)
                attempt_trace['json_valid'] = True
                plan, issues, warnings = validate_plan(raw_plan, args.skill, args.query, inventory)
                attempt_trace.update(enum_valid=not issues, issues=issues)
                if issues:
                    raise ValueError('; '.join(issues))
                trace.update(json_valid=True, enum_valid=True, planner_status='complete', issues=[], warnings=warnings)
                trace['attempts'].append(attempt_trace)
                break
            except Exception as exc:
                from theraskill_agent.text import redact_secrets
                error = redact_secrets(str(exc))
                attempt_trace['error'] = error
                trace['attempts'].append(attempt_trace)
                if attempt == attempts or not isinstance(exc, (ValueError, TypeError, KeyError)):
                    raise
                feedback = '\n\nThe previous planning attempt failed validation: ' + error + '\nReturn a fresh, complete, compact JSON object. Choose metadata values only from the current index inventory; use [] when uncertain. Do not repeat a previous answer.'
    except Exception as exc:
        from theraskill_agent.text import redact_secrets
        trace["issues"].append(redact_secrets(str(exc)))
        if not args.allow_degraded_fallback:
            raise
        fallback = fallback_plan(args.skill, args.query)
        plan, fallback_issues, fallback_warnings = validate_plan(fallback, args.skill, args.query, inventory)
        trace["planner_status"] = "degraded_fallback"
        trace["fallback_used"] = True
        trace["fallback_issues"] = fallback_issues
        trace["enum_valid"] = not fallback_issues
        trace["warnings"].extend(fallback_warnings)
    trace['timings'] = timings.snapshot()
    return {
        "schema_version": "theraskill_retrieval_plan_artifact_v1",
        "created_at": utc_now(),
        "skill": args.skill,
        "query": args.query,
        "plan": plan,
        "planner_trace": trace,
    }


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    ensure_output_path(output, overwrite=args.overwrite)
    artifact = build_plan(args)
    write_json(output, artifact, overwrite=args.overwrite)
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({
            "output": str(output),
            "planner_status": artifact["planner_trace"]["planner_status"],
            "json_valid": artifact["planner_trace"]["json_valid"],
            "enum_valid": artifact["planner_trace"]["enum_valid"],
            "fallback_used": artifact["planner_trace"]["fallback_used"],
            "skill_intent": artifact["plan"]["skill_intent"],
            "metadata_filters": artifact["plan"]["metadata_filters"],
        }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
