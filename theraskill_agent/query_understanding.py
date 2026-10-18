"""Deterministic query understanding shared by every TheraSkill retrieval entry.

The layer deliberately does not call a model. It normalizes noisy user input,
detects the broad language mix, derives a bounded set of lexical variants, and
routes the request to the knowledge families that belong to the selected skill.
An optional LLM planner can enrich this result later, but retrieval remains
usable and auditable when that planner is unavailable.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping


SUPPORTED_RETRIEVAL_SKILLS = (
    "emotional-support",
    "risk-triage",
    "cognitive-reframing-support",
    "rebt-belief-work-support",
    "synthetic-dialogue-builder",
    "therapy-response-reviewer",
)


# One policy registry for local retrieval, runtime evidence packing and hybrid search.
SKILL_KNOWLEDGE_ROUTES: dict[str, dict[str, Any]] = {
    "emotional-support": {
        "families": ["style_response", "therapy_process", "qa_psychoeducation"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "family_priority": {"style_response": 0.18, "therapy_process": 0.18, "qa_psychoeducation": 0.10},
        "quota": {"style_response": 3, "therapy_process": 2, "qa_psychoeducation": 2},
    },
    "risk-triage": {
        "families": ["clinical_risk_assessment", "style_response", "qa_psychoeducation"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "risk_scopes": ["clinical_risk_or_assessment", "risk_related_text"],
        "family_priority": {"clinical_risk_assessment": 0.55, "qa_psychoeducation": 0.12, "style_response": 0.06},
        "quota": {"clinical_risk_assessment": 4, "style_response": 2, "qa_psychoeducation": 2},
    },
    "cognitive-reframing-support": {
        "families": ["method_rubric", "qa_psychoeducation", "style_response", "clinical_risk_assessment"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "family_priority": {"method_rubric": 0.65, "qa_psychoeducation": 0.18, "style_response": 0.06, "clinical_risk_assessment": 0.02},
        "quota": {"method_rubric": 3, "qa_psychoeducation": 2, "style_response": 2, "clinical_risk_assessment": 1},
    },
    "rebt-belief-work-support": {
        "families": ["method_rubric", "qa_psychoeducation", "therapy_process", "style_response"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "family_priority": {"method_rubric": 0.95, "qa_psychoeducation": 0.20, "therapy_process": 0.12, "style_response": 0.02},
        "quota": {"method_rubric": 4, "qa_psychoeducation": 1, "therapy_process": 2, "style_response": 1},
    },
    "synthetic-dialogue-builder": {
        "families": ["synthetic_construction", "therapy_process", "method_rubric", "style_response"],
        "blocked_families": ["source_inventory"],
        "blocked_routes": ["accounting_only_requires_review"],
        "family_priority": {"synthetic_construction": 0.85, "therapy_process": 0.15, "method_rubric": 0.08, "style_response": 0.03},
        "quota": {"synthetic_construction": 4, "therapy_process": 2, "method_rubric": 1, "style_response": 1},
    },
    "therapy-response-reviewer": {
        "families": ["therapy_process", "method_rubric", "synthetic_construction", "style_response", "qa_psychoeducation"],
        "blocked_families": ["source_inventory"],
        "blocked_routes": ["accounting_only_requires_review"],
        "family_priority": {"therapy_process": 0.35, "method_rubric": 0.30, "synthetic_construction": 0.18, "style_response": 0.06, "qa_psychoeducation": 0.06},
        "quota": {"therapy_process": 2, "method_rubric": 2, "synthetic_construction": 2, "style_response": 1, "qa_psychoeducation": 1},
    },
}

# Minimum evidence coverage for each business purpose. Explicit user filters
# remain hard constraints; unavailable families are reported, never invented.
MINIMUM_EVIDENCE_FAMILIES = {
    'emotional-support': ['therapy_process', 'style_response'],
    'risk-triage': ['clinical_risk_assessment'],
    'cognitive-reframing-support': ['method_rubric', 'qa_psychoeducation'],
    'rebt-belief-work-support': ['method_rubric'],
    'synthetic-dialogue-builder': ['synthetic_construction', 'therapy_process'],
    'therapy-response-reviewer': ['therapy_process', 'method_rubric'],
}

_FILTER_ALIASES = {
    "family": "dedup_family",
    "families": "dedup_family",
    "evidence_tier": "primary_evidence_tier",
    "evidence_tiers": "primary_evidence_tier",
    "knowledge_type": "primary_knowledge_type",
    "knowledge_types": "primary_knowledge_type",
    "dataset": "primary_dataset",
    "datasets": "primary_dataset",
    "route": "default_route",
    "routes": "default_route",
    "risk": "risk_scope",
    "language": "primary_language",
}
_FILTER_FIELDS = {
    "dedup_family",
    "risk_scope",
    "default_route",
    "primary_knowledge_type",
    "primary_evidence_tier",
    "primary_dataset",
    "primary_language",
}
_RISK_TERMS = re.compile(
    r"(?:自杀|自伤|伤害自己|伤人|危机|轻生|不想活|想死|结束生命|suicid|self[- ]?harm|kill myself|harm myself|want to die|violence|abuse)",
    re.IGNORECASE,
)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_LATIN = re.compile(r"[a-z0-9]+(?:['_-][a-z0-9]+)*", re.IGNORECASE)



# Expand only concepts present in the query, never a fixed bag of all skill terms.
# The LLM planner can add contextual rewrites on top of these auditable variants.
_CONCEPTS = {
    "empathy": (re.compile(r"共情|倾听|情绪支持|\bempath\w*|\bvalidation\b", re.I), ["共情", "empathy", "validation"]),
    "anxiety": (re.compile(r"焦虑|紧张|\banxiet\w*|\banxious\b", re.I), ["焦虑", "anxiety"]),
    "cognitive_reframing": (re.compile(r"认知重构|认知歪曲|灾难化|替代想法|\bCBT\b|\brefram\w*|cognitive distortion", re.I), ["认知重构", "cognitive reframing", "alternative thought"]),
    "rebt": (re.compile(r"非理性信念|辩驳|\bREBT\b|irrational belief|disputation", re.I), ["REBT", "非理性信念", "irrational belief", "disputation"]),
    "suicide": (re.compile(r"自杀|轻生|不想活|想死|结束生命|\bsuicid\w*|kill myself|want to die", re.I), ["自杀", "suicide", "safety assessment"]),
    "self_harm": (re.compile(r"自伤|伤害自己|self[- ]harm|harm myself", re.I), ["自伤", "self harm", "safety assessment"]),
}
_STOP_WORDS = set("a an the i me my you your he she it we they is am are was were be to of and or for in on this that please help can could would how what do does with".split())
_TASK_TYPES = {
    "emotional-support": "support_response",
    "risk-triage": "risk_boundary_response",
    "cognitive-reframing-support": "framework_guided_intervention",
    "rebt-belief-work-support": "framework_guided_intervention",
    "synthetic-dialogue-builder": "synthetic_construction",
    "therapy-response-reviewer": "response_review",
}
_INTENT_SIGNALS = [
    ("therapy-response-reviewer", re.compile(r"审核|审查|评估.*(?:回复|回应)|review.*(?:response|reply)", re.I)),
    ("synthetic-dialogue-builder", re.compile(r"(?:生成|模拟).*(?:对话|情景|场景)|(?:generate|simulate).*(?:dialogue|scenario)", re.I)),
    ("risk-triage", _RISK_TERMS),
    ("rebt-belief-work-support", _CONCEPTS["rebt"][0]),
    ("cognitive-reframing-support", _CONCEPTS["cognitive_reframing"][0]),
    ("emotional-support", _CONCEPTS["empathy"][0]),
]


def cjk_index_terms(text: str) -> list[str]:
    """All 2/3-character terms for the indexed text; query variants stay bounded."""
    return list(dict.fromkeys(
        span[index:index + size]
        for span in _CJK.findall(unicodedata.normalize("NFKC", text))
        for size in (2, 3)
        for index in range(len(span) - size + 1)
    ))


def _unique(values: list[str], limit: int = 32) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return _unique([str(item) for item in value])
    return _unique([str(value)])


def detect_language(text: str) -> str:
    has_cjk = bool(_CJK.search(text))
    has_latin = bool(re.search(r'[A-Za-z]', text))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"


def normalize_query(query: str) -> str:
    """Normalize Unicode and common transcript punctuation without rewriting meaning."""
    text = unicodedata.normalize("NFKC", str(query or ""))
    text = text.replace("\u200b", "").replace("\u00a0", " ")
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", text)
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"([:;!?])(?=[A-Za-z0-9])", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _cjk_variants(text: str) -> list[str]:
    variants: list[str] = []
    for span in _CJK.findall(text):
        if len(span) <= 8:
            variants.append(span)
        # SQLite unicode61 is not a Chinese segmenter. Bounded character
        # n-grams provide a deterministic substring fallback for short terms.
        for size in (2, 3):
            if len(span) >= size:
                variants.extend(span[index:index + size] for index in range(len(span) - size + 1))
    return _unique(variants, 40)


def _normalize_filters(filters: Mapping[str, Any] | None) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    if filters is not None and not isinstance(filters, Mapping):
        raise ValueError("filters must be an object")
    for raw_key, value in (filters or {}).items():
        key = _FILTER_ALIASES.get(str(raw_key), str(raw_key))
        if key not in _FILTER_FIELDS:
            raise ValueError(f"unsupported knowledge filter: {raw_key}")
        if value is not None and not isinstance(value, (str, list, tuple, set)):
            raise ValueError(f"knowledge filter {raw_key} must be a string or list of strings")
        if isinstance(value, (list, tuple, set)) and not all(isinstance(item, str) for item in value):
            raise ValueError(f"knowledge filter {raw_key} must contain only strings")
        values = _values(value)
        if values:
            normalized[key] = _unique(normalized.get(key, []) + values)
    return normalized


def understand_query(
    query: str,
    *,
    skill: str | None = None,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded, JSON-serializable understanding for retrieval.

    ``skill`` is intentionally explicit. The caller may pass ``None`` for a
    general knowledge search, while user-facing functions should choose the
    skill that owns their knowledge route.
    """
    raw = str(query or "")
    normalized = normalize_query(raw)
    selected_skill = str(skill or "").strip() or None
    if selected_skill is not None and selected_skill not in SKILL_KNOWLEDGE_ROUTES:
        raise ValueError(f"unsupported retrieval skill: {selected_skill}")
    route = SKILL_KNOWLEDGE_ROUTES.get(selected_skill or "", {})
    cjk_terms = _cjk_variants(normalized)
    latin_terms = [value.lower() for value in _LATIN.findall(normalized)]
    concepts = [name for name, (pattern, _) in _CONCEPTS.items() if pattern.search(normalized)]
    expansions = _unique([term for name in concepts for term in _CONCEPTS[name][1]], 24)
    cjk_terms = _unique(_cjk_variants(' '.join(expansions)) + cjk_terms, 64)
    expanded_latin = [term.lower() for term in _LATIN.findall(' '.join(expansions))]
    lexical_terms = _unique([term for term in latin_terms + expanded_latin if term not in _STOP_WORDS] + cjk_terms + expansions, 80)
    hard_filters = _normalize_filters(filters)
    risk_detected = bool(_RISK_TERMS.search(normalized))
    automatic_filters: dict[str, list[str]] = {}
    if selected_skill == "risk-triage":
        automatic_filters["risk_scope"] = ["clinical_risk_or_assessment", "risk_related_text"]
    task_type = _TASK_TYPES.get(selected_skill or "", "knowledge_lookup")
    suggested_skill = selected_skill
    if not suggested_skill:
        for name, pattern in _INTENT_SIGNALS:
            if pattern.search(normalized):
                suggested_skill = name
                break
    warnings = []
    if risk_detected and selected_skill != "risk-triage":
        warnings.append("risk-related language detected; consider the risk-triage workflow before ordinary support")
    return {
        "schema_version": "theraskill_query_understanding_v1",
        "raw_query": raw,
        "normalized_query": normalized,
        "language": detect_language(normalized),
        "lexical_terms": lexical_terms,
        "cjk_terms": cjk_terms,
        "latin_terms": _unique(latin_terms),
        "skill": selected_skill or "general",
        "route": {
            "preferred_families": sorted(route.get("families") or [], key=lambda family: -route.get("family_priority", {}).get(family, 0)),
            "allowed_families": list(route.get("families") or []),
            "excluded_families": list(route.get("blocked_families") or ["source_inventory"]),
            "excluded_routes": list(route.get("blocked_routes") or ["accounting_only_requires_review"]),
            "risk_families": ["clinical_risk_assessment"] if selected_skill == "risk-triage" else [],
        },
        "filters": hard_filters,
        "automatic_filters": automatic_filters,
        "risk_signal": {"detected": risk_detected, "matched": _unique([match.group(0) for match in _RISK_TERMS.finditer(normalized)], 12)},
        "task_type": task_type,
        "suggested_skill": suggested_skill,
        "concepts": concepts,
        "query_variants": _unique([normalized] + expansions, 25),
        "needs_clarification": not lexical_terms,
        "warnings": warnings,
        "understanding_source": "deterministic",
    }


__all__ = [
    "SKILL_KNOWLEDGE_ROUTES",
    "SUPPORTED_RETRIEVAL_SKILLS",
    "detect_language",
    "normalize_query",
    "understand_query",
]
