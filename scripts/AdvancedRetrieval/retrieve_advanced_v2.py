#!/usr/bin/env python3
"""Advanced deterministic retrieval v2 for TheraSkill.

This P0 implementation is intentionally not dense retrieval yet. It upgrades the
active SQLite FTS baseline with query understanding, policy-aware reranking,
family quotas, severity/framework features, and evidence-role packing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, write_json
from retrieval_surface_registry import ADVANCED_RETRIEVAL_V2_BUNDLE_PATH, PHASE6_SQLITE_DB_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PHASE6_SQLITE_DB_PATH
DEFAULT_OUTPUT = ADVANCED_RETRIEVAL_V2_BUNDLE_PATH


SKILL_POLICIES: dict[str, dict[str, Any]] = {
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


BASE_EXPANSIONS: dict[str, list[str]] = {
    "emotional-support": ["焦虑", "压力", "支持", "共情", "倾听", "理解", "stress", "anxiety", "support"],
    "risk-triage": ["自杀", "自伤", "轻生", "不想活", "想死", "危机", "安全", "suicide", "self harm", "crisis"],
    "cognitive-reframing-support": ["认知重构", "认知歪曲", "灾难化", "替代想法", "证据", "reframe", "cognitive"],
    "rebt-belief-work-support": ["REBT", "ABC", "信念", "非理性信念", "辩驳", "替代信念", "must", "should", "belief"],
    "synthetic-dialogue-builder": ["合成", "rationale", "dialogue", "construction", "SMILECHAT", "PsyDTCorpus", "SoulChat"],
    "therapy-response-reviewer": ["审核", "回应", "rationale", "dialogue", "stage", "strategy", "诊断", "保证", "review"],
}

FAMILY_BACKFILL_EXPANSIONS: dict[str, dict[str, list[str]]] = {
    "cognitive-reframing-support": {
        "method_rubric": ["thought", "belief", "distortion", "reflection", "alternative", "options", "cognitive", "reframe", "认知", "信念"],
    },
    "rebt-belief-work-support": {
        "method_rubric": ["REBT", "ABC", "belief", "rational", "irrational", "disputation", "alternative", "must", "should", "信念"],
    },
    "therapy-response-reviewer": {
        "method_rubric": ["reflection", "question", "advice", "stage", "strategy", "quality", "review"],
        "therapy_process": ["transition", "state", "move", "response", "alliance", "disclosure"],
    },
}

HIGH_RISK_TERMS = ["不想活", "想死", "自杀", "轻生", "结束一切", "遗书", "跳楼", "跳桥", "跳河", "die", "suicide"]
MEANS_TERMS = ["药", "刀", "绳", "桥", "楼", "煤气", "pills", "knife", "bridge"]
IMMEDIACY_TERMS = ["今晚", "现在", "马上", "今天", "停不下来", "right now", "tonight"]
LOW_RISK_LABEL_TERMS = ["低自杀风险", "low suicide risk", "low_risk", "无自杀", "no suicidal"]
HIGH_RISK_LABEL_TERMS = ["high", "高", "imminent", "危机", "self-harm", "suicide", "自杀", "自伤"]
REBT_TERMS = ["REBT", "ABC", "必须", "应该", "非理性信念", "绝对", "辩驳", "替代信念", "must", "should", "belief", "disputation"]
CBT_TERMS = ["认知重构", "认知歪曲", "灾难化", "替代想法", "证据", "reframe", "cognitive"]
SYNTHETIC_TERMS = ["合成", "伪造", "rationale", "dialogue", "construction", "真实咨询记录"]
REVIEW_TERMS = ["审核", "是否合适", "问题", "改写", "诊断", "保证", "review"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--skill", required=True, choices=sorted(SKILL_POLICIES))
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--candidate-multiplier", type=int, default=30)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def contains_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)


def matched_terms(text: str, terms: list[str]) -> list[str]:
    lower = text.lower()
    return [term for term in terms if term.lower() in lower]


def parse_query(query: str, skill: str) -> dict[str, Any]:
    high_risk = contains_any(query, HIGH_RISK_TERMS)
    means = contains_any(query, MEANS_TERMS)
    immediacy = contains_any(query, IMMEDIACY_TERMS)
    rebt = contains_any(query, REBT_TERMS)
    cbt = contains_any(query, CBT_TERMS)
    synthetic = contains_any(query, SYNTHETIC_TERMS)
    review = contains_any(query, REVIEW_TERMS)
    severity = "ordinary_or_unspecified"
    if high_risk and (means or immediacy):
        severity = "high_or_urgent"
    elif high_risk:
        severity = "ambiguous_or_moderate"
    return {
        "schema_version": "theraskill_retrieval_query_parse_v1",
        "skill_intent": skill,
        "language": "mixed" if re.search(r"[A-Za-z]", query) and re.search(r"[\u4e00-\u9fff]", query) else ("zh" if re.search(r"[\u4e00-\u9fff]", query) else "en"),
        "risk_cues": {
            "high_risk_language": high_risk,
            "means_available": means,
            "immediacy": immediacy,
            "severity_hint": severity,
            "matched_high_risk_terms": matched_terms(query, HIGH_RISK_TERMS),
            "matched_means_terms": matched_terms(query, MEANS_TERMS),
            "matched_immediacy_terms": matched_terms(query, IMMEDIACY_TERMS),
        },
        "framework_cues": {
            "rebt": rebt,
            "cognitive_reframing": cbt,
            "matched_rebt_terms": matched_terms(query, REBT_TERMS),
            "matched_cbt_terms": matched_terms(query, CBT_TERMS),
        },
        "task_cues": {
            "synthetic_construction": synthetic,
            "response_review": review,
            "matched_synthetic_terms": matched_terms(query, SYNTHETIC_TERMS),
            "matched_review_terms": matched_terms(query, REVIEW_TERMS),
        },
    }


def fts_query(query: str, skill: str, parsed: dict[str, Any]) -> str:
    tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_/-]+", query)
    tokens.extend(BASE_EXPANSIONS.get(skill, []))
    tokens.extend(parsed["risk_cues"].get("matched_high_risk_terms") or [])
    tokens.extend(parsed["risk_cues"].get("matched_means_terms") or [])
    tokens.extend(parsed["framework_cues"].get("matched_rebt_terms") or [])
    tokens.extend(parsed["framework_cues"].get("matched_cbt_terms") or [])
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = token.strip().replace('"', "")
        if not token or token in seen:
            continue
        seen.add(token)
        cleaned.append(token)
    return " OR ".join(f'"{token}"' for token in cleaned[:32]) or f'"{query}"'


def placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def fetch_candidates(db: Path, skill: str, query: str, parsed: dict[str, Any], top_k: int, candidate_multiplier: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = SKILL_POLICIES[skill]
    families = list(policy["families"])
    blocked_families = list(policy.get("blocked_families") or [])
    blocked_routes = list(policy.get("blocked_routes") or [])
    risk_scopes = list(policy.get("risk_scopes") or [])
    match = fts_query(query, skill, parsed)
    params: list[Any] = [match]
    where = ["retrieval_fts match ?", "m.retrieval_eligible = 1"]
    if families:
        where.append(f"m.dedup_family in ({placeholders(families)})")
        params.extend(families)
    if blocked_families:
        where.append(f"m.dedup_family not in ({placeholders(blocked_families)})")
        params.extend(blocked_families)
    if blocked_routes:
        where.append(f"m.default_route not in ({placeholders(blocked_routes)})")
        params.extend(blocked_routes)
    if risk_scopes:
        where.append(f"(m.risk_scope in ({placeholders(risk_scopes)}) or m.risk_scope is null)")
        params.extend(risk_scopes)
    limit = max(top_k * candidate_multiplier, top_k)
    sql = f"""
        select
          m.retrieval_id,
          m.release_unit_id,
          m.release_unit_type,
          m.dedup_family,
          m.canonical_knowledge_type,
          m.canonical_label,
          m.primary_dataset,
          m.primary_knowledge_type,
          m.primary_evidence_tier,
          m.risk_scope,
          m.default_route,
          m.member_count,
          m.retrieval_text_chars,
          bm25(retrieval_fts) as bm25_score,
          substr(f.retrieval_text, 1, 1200) as text_preview
        from retrieval_fts f
        join retrieval_meta m on m.retrieval_id = f.retrieval_id
        where {' and '.join(where)}
        order by bm25_score asc
        limit ?
    """
    params.append(limit)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        params[0] = f'"{query.replace(chr(34), "")}"'
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows], {
        "fts_query": match,
        "families": families,
        "blocked_families": blocked_families,
        "blocked_routes": blocked_routes,
        "risk_scopes": risk_scopes,
        "candidate_limit": limit,
    }


def fetch_family_backfill(db: Path, skill: str, family: str, query_terms: list[str], limit: int) -> list[dict[str, Any]]:
    blocked_routes = list(SKILL_POLICIES[skill].get("blocked_routes") or [])
    tokens = []
    seen: set[str] = set()
    for term in query_terms:
        term = term.strip().replace('"', "")
        if term and term not in seen:
            seen.add(term)
            tokens.append(term)
    if not tokens:
        return []
    match = " OR ".join(f'"{token}"' for token in tokens[:24])
    params: list[Any] = [match, family]
    where = ["retrieval_fts match ?", "m.retrieval_eligible = 1", "m.dedup_family = ?"]
    if blocked_routes:
        where.append(f"m.default_route not in ({placeholders(blocked_routes)})")
        params.extend(blocked_routes)
    sql = f"""
        select
          m.retrieval_id,
          m.release_unit_id,
          m.release_unit_type,
          m.dedup_family,
          m.canonical_knowledge_type,
          m.canonical_label,
          m.primary_dataset,
          m.primary_knowledge_type,
          m.primary_evidence_tier,
          m.risk_scope,
          m.default_route,
          m.member_count,
          m.retrieval_text_chars,
          bm25(retrieval_fts) as bm25_score,
          substr(f.retrieval_text, 1, 1200) as text_preview
        from retrieval_fts f
        join retrieval_meta m on m.retrieval_id = f.retrieval_id
        where {' and '.join(where)}
        order by bm25_score asc
        limit ?
    """
    params.append(limit)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]


def label_text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(key) or "") for key in ("canonical_label", "canonical_knowledge_type", "primary_knowledge_type", "text_preview"))


def evidence_role(skill: str, item: dict[str, Any]) -> str:
    family = item.get("dedup_family")
    if family == "clinical_risk_assessment":
        return "risk_boundary_evidence"
    if family == "method_rubric":
        return "method_or_review_rubric"
    if family == "synthetic_construction":
        return "construction_methodology"
    if family == "therapy_process":
        return "therapy_process_pattern"
    if family == "style_response":
        return "response_style_example"
    if family == "qa_psychoeducation":
        return "psychoeducation_or_support_guidance"
    return f"{skill}_supporting_evidence"


def allowed_for_role(role: str) -> list[str]:
    mapping = {
        "risk_boundary_evidence": ["inform risk-boundary response", "support safety-first tone", "identify risk cue patterns"],
        "method_or_review_rubric": ["guide method structure", "guide review criteria", "support framework-specific reasoning"],
        "construction_methodology": ["guide synthetic dialogue construction", "guide construction review only"],
        "therapy_process_pattern": ["inform process sequence", "support response planning"],
        "response_style_example": ["inform tone and wording style", "support non-verbatim response drafting"],
        "psychoeducation_or_support_guidance": ["inform general support guidance", "support bounded psychoeducation"],
    }
    return mapping.get(role, ["support retrieval-grounded response"])


def forbidden_for_role(role: str) -> list[str]:
    base = ["do not copy verbatim", "do not present as clinical diagnosis"]
    if role == "construction_methodology":
        return base + ["do not present as observed clinical evidence", "do not claim it is a real counseling record"]
    if role == "risk_boundary_evidence":
        return base + ["do not assign final risk score", "do not replace local safety policy"]
    return base


def rerank_score(item: dict[str, Any], rank: int, skill: str, parsed: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    policy = SKILL_POLICIES[skill]
    bm25_score = float(item.get("bm25_score") or 0.0)
    # BM25 in FTS5 is lower-is-better and often negative. Use a stable rank term
    # plus the raw inverted score to avoid over-amplifying long-text matches.
    bm25_rank_score = 1.0 / (rank + 1)
    bm25_raw_score = min(max(-bm25_score / 20.0, -2.0), 2.0)
    family = str(item.get("dedup_family") or "")
    family_bonus = float(policy.get("family_priority", {}).get(family, 0.0))
    member_bonus = min(float(item.get("member_count") or 0), 50.0) / 1000.0
    text = label_text(item)

    severity_bonus = 0.0
    severity_penalty = 0.0
    severity_hint = parsed["risk_cues"]["severity_hint"]
    if skill == "risk-triage":
        if severity_hint == "high_or_urgent":
            if contains_any(text, HIGH_RISK_LABEL_TERMS):
                severity_bonus += 1.2
            if contains_any(text, LOW_RISK_LABEL_TERMS):
                severity_penalty -= 2.4
            if item.get("risk_scope") == "clinical_risk_or_assessment":
                severity_bonus += 0.3
        elif severity_hint == "ambiguous_or_moderate":
            if contains_any(text, HIGH_RISK_LABEL_TERMS):
                severity_bonus += 0.45
            if contains_any(text, LOW_RISK_LABEL_TERMS):
                severity_penalty -= 0.35

    framework_bonus = 0.0
    framework_penalty = 0.0
    if skill == "rebt-belief-work-support":
        if parsed["framework_cues"]["rebt"]:
            if family == "method_rubric":
                framework_bonus += 2.0
            if contains_any(text, REBT_TERMS):
                framework_bonus += 1.2
            if family == "style_response" and not contains_any(text, REBT_TERMS):
                framework_penalty -= 1.1
    if skill == "cognitive-reframing-support" and parsed["framework_cues"]["cognitive_reframing"]:
        if family == "method_rubric":
            framework_bonus += 1.7
        if contains_any(text, CBT_TERMS):
            framework_bonus += 0.8
        if family == "clinical_risk_assessment" and not parsed["risk_cues"]["high_risk_language"]:
            framework_penalty -= 0.8
    if skill == "synthetic-dialogue-builder" and parsed["task_cues"]["synthetic_construction"]:
        if family == "synthetic_construction":
            framework_bonus += 1.1
    if skill == "therapy-response-reviewer" and parsed["task_cues"]["response_review"]:
        if family in {"therapy_process", "method_rubric"}:
            framework_bonus += 0.6
        if contains_any(text, REVIEW_TERMS):
            framework_bonus += 0.4

    score = bm25_rank_score + bm25_raw_score + family_bonus + member_bonus + severity_bonus + severity_penalty + framework_bonus + framework_penalty
    return score, {
        "bm25_rank": rank,
        "bm25_score": round(bm25_score, 6),
        "bm25_rank_score": round(bm25_rank_score, 6),
        "bm25_raw_score": round(bm25_raw_score, 6),
        "family_bonus": round(family_bonus, 6),
        "member_bonus": round(member_bonus, 6),
        "severity_bonus": round(severity_bonus, 6),
        "severity_penalty": round(severity_penalty, 6),
        "framework_bonus": round(framework_bonus, 6),
        "framework_penalty": round(framework_penalty, 6),
        "rerank_score": round(score, 6),
    }


def assign_and_select(items: list[dict[str, Any]], top_k: int, skill: str) -> list[dict[str, Any]]:
    quota = dict(SKILL_POLICIES[skill].get("quota") or {})
    selected: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_family[str(item.get("dedup_family") or "")].append(item)
    for family, family_quota in quota.items():
        for item in by_family.get(family, [])[:family_quota]:
            if len(selected) >= top_k:
                break
            rid = str(item.get("release_unit_id"))
            if rid in used_ids:
                continue
            selected.append(item)
            used_ids.add(rid)
    if len(selected) < top_k:
        for item in items:
            if len(selected) >= top_k:
                break
            rid = str(item.get("release_unit_id"))
            if rid in used_ids:
                continue
            selected.append(item)
            used_ids.add(rid)
    return selected[:top_k]


def build_bundle(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    if not db.exists():
        raise FileNotFoundError(f"SQLite retrieval DB not found: {db}")
    parsed = parse_query(args.query, args.skill)
    candidates, resolved_policy = fetch_candidates(db, args.skill, args.query, parsed, args.top_k, args.candidate_multiplier)
    seen_retrieval_ids = {str(item.get("retrieval_id")) for item in candidates}
    backfill_counts: dict[str, int] = {}
    for family, terms in FAMILY_BACKFILL_EXPANSIONS.get(args.skill, {}).items():
        existing = sum(1 for item in candidates if item.get("dedup_family") == family)
        target = int(SKILL_POLICIES[args.skill].get("quota", {}).get(family, 0))
        if existing >= target:
            continue
        backfill = fetch_family_backfill(db, args.skill, family, terms, max(target * 4, 12))
        added = 0
        for item in backfill:
            rid = str(item.get("retrieval_id"))
            if rid in seen_retrieval_ids:
                continue
            candidates.append(item)
            seen_retrieval_ids.add(rid)
            added += 1
        if added:
            backfill_counts[family] = added
    reranked: list[dict[str, Any]] = []
    for rank, item in enumerate(candidates, start=1):
        score, trace = rerank_score(item, rank, args.skill, parsed)
        item = dict(item)
        item.pop("bm25_score", None)
        role = evidence_role(args.skill, item)
        item["evidence_role"] = role
        item["allowed_use"] = allowed_for_role(role)
        item["forbidden_use"] = forbidden_for_role(role)
        item["query_match_cues"] = sorted(set(
            parsed["risk_cues"].get("matched_high_risk_terms", [])
            + parsed["risk_cues"].get("matched_means_terms", [])
            + parsed["risk_cues"].get("matched_immediacy_terms", [])
            + parsed["framework_cues"].get("matched_rebt_terms", [])
            + parsed["framework_cues"].get("matched_cbt_terms", [])
            + parsed["task_cues"].get("matched_synthetic_terms", [])
            + parsed["task_cues"].get("matched_review_terms", [])
        ))
        item["severity_match"] = parsed["risk_cues"]["severity_hint"] if args.skill == "risk-triage" else "not_applicable"
        item["ranking_trace"] = trace
        item["ranking_trace"]["rerank_score"] = round(score, 6)
        reranked.append(item)
    reranked.sort(key=lambda item: item["ranking_trace"]["rerank_score"], reverse=True)
    selected = assign_and_select(reranked, args.top_k, args.skill)
    families = {item.get("dedup_family") for item in selected}
    routes = {item.get("default_route") for item in selected}
    labels = " ".join(str(item.get("canonical_label") or "") for item in selected[:1])
    high_query = parsed["risk_cues"]["severity_hint"] == "high_or_urgent"
    bundle = {
        "schema_version": "theraskill_runtime_evidence_bundle_v2",
        "created_at": utc_now(),
        "skill": args.skill,
        "query": args.query,
        "top_k": args.top_k,
        "source_index": str(db),
        "parsed_query": parsed,
        "retrieval_config": {
            "version": "advanced_retrieval_v2_p0",
            "channels": ["sqlite_fts_bm25"],
            "fusion": "not_enabled_yet",
            "reranker": "policy_aware_deterministic_v1",
            "family_quota": SKILL_POLICIES[args.skill].get("quota"),
            "family_backfill_counts": backfill_counts,
        },
        "retrieval_policy": resolved_policy,
        "candidate_count": len(candidates),
        "items": selected,
        "policy_checks": {
            "has_release_unit_ids": all(bool(item.get("release_unit_id")) for item in selected),
            "source_inventory_excluded": "source_inventory" not in families,
            "deferred_accounting_excluded": "accounting_only_requires_review" not in routes,
            "synthetic_construction_policy_ok": args.skill in {"synthetic-dialogue-builder", "therapy-response-reviewer"} or "synthetic_construction" not in families,
            "high_risk_query_top1_not_low_risk": (not high_query) or (not contains_any(labels, LOW_RISK_LABEL_TERMS)),
        },
        "quality_checks": {
            "method_rubric_in_top5_for_rebt": args.skill != "rebt-belief-work-support" or any(item.get("dedup_family") == "method_rubric" for item in selected[:5]),
            "clinical_risk_in_top3_for_high_risk": args.skill != "risk-triage" or (not high_query) or any(item.get("dedup_family") == "clinical_risk_assessment" for item in selected[:3]),
        },
    }
    return bundle


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    ensure_output_path(output, overwrite=args.overwrite)
    bundle = build_bundle(args)
    write_json(output, bundle, overwrite=args.overwrite)
    if args.json:
        print(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({
            "output": str(output),
            "skill": args.skill,
            "hits": len(bundle["items"]),
            "policy_checks": bundle["policy_checks"],
            "quality_checks": bundle["quality_checks"],
        }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
