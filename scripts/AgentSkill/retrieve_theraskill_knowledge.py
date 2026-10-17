#!/usr/bin/env python3
"""Runtime SQLite retrieval bridge for TheraSkill Hermes skills."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, write_json
from hermes_surface_registry import PHASE6_SQLITE_DB_PATH, RUNTIME_EVIDENCE_BUNDLE_PATH
from retrieval_wrapper_common import find_project_root

PROJECT_ROOT = find_project_root(
    required_relpaths=(Path("outputs/knowledge_engine/indexes/sqlite/phase6_retrieval_fts.sqlite"),),
    doc_markers=(Path("docs/hermes_skill_development_plan.zh.md"),),
)
DEFAULT_DB = PHASE6_SQLITE_DB_PATH
DEFAULT_OUTPUT = RUNTIME_EVIDENCE_BUNDLE_PATH


SKILL_POLICIES: dict[str, dict[str, Any]] = {
    "emotional-support": {
        "families": ["style_response", "therapy_process", "qa_psychoeducation"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "allow_synthetic_construction": False,
        "family_priority": {"therapy_process": 0.20, "qa_psychoeducation": 0.12, "style_response": 0.08},
    },
    "risk-triage": {
        "families": ["clinical_risk_assessment", "qa_psychoeducation", "style_response"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "allow_synthetic_construction": False,
        "risk_scopes": ["clinical_risk_or_assessment", "risk_related_text"],
        "family_priority": {"clinical_risk_assessment": 0.35, "qa_psychoeducation": 0.10, "style_response": 0.04},
    },
    "cognitive-reframing-support": {
        "families": ["method_rubric", "qa_psychoeducation", "clinical_risk_assessment", "style_response"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "allow_synthetic_construction": False,
        "family_priority": {"method_rubric": 0.35, "qa_psychoeducation": 0.10, "clinical_risk_assessment": 0.04},
    },
    "synthetic-dialogue-builder": {
        "families": ["synthetic_construction", "therapy_process", "method_rubric", "style_response"],
        "blocked_families": ["source_inventory"],
        "blocked_routes": ["accounting_only_requires_review"],
        "allow_synthetic_construction": True,
        "family_priority": {"synthetic_construction": 0.45, "therapy_process": 0.15, "method_rubric": 0.08},
    },
    "therapy-response-reviewer": {
        "families": ["therapy_process", "method_rubric", "synthetic_construction", "style_response", "qa_psychoeducation"],
        "blocked_families": ["source_inventory"],
        "blocked_routes": ["accounting_only_requires_review"],
        "allow_synthetic_construction": True,
        "family_priority": {"therapy_process": 0.30, "method_rubric": 0.18, "synthetic_construction": 0.08},
    },
    "rebt-belief-work-support": {
        "families": ["method_rubric", "qa_psychoeducation", "therapy_process", "style_response"],
        "blocked_families": ["source_inventory", "synthetic_construction"],
        "blocked_routes": ["accounting_only_requires_review", "synthetic_construction_method_retrieval"],
        "allow_synthetic_construction": False,
        "family_priority": {"method_rubric": 0.30, "qa_psychoeducation": 0.14, "therapy_process": 0.08},
    },
}


QUERY_EXPANSIONS: dict[str, list[str]] = {
    "emotional-support": ["焦虑", "压力", "支持", "共情", "倾听", "理解", "stress", "anxiety", "support"],
    "risk-triage": ["自杀", "自伤", "轻生", "不想活", "危机", "风险", "suicide", "self harm", "crisis"],
    "cognitive-reframing-support": ["认知重构", "认知歪曲", "灾难化", "替代想法", "信念", "reframe", "cognitive"],
    "synthetic-dialogue-builder": ["rationale", "dialogue", "Generated", "Final response", "construction", "SMILECHAT", "PsyDTCorpus", "SoulChat"],
    "therapy-response-reviewer": ["rationale", "dialogue", "stage", "strategy", "reflection", "support_strategy_sequence", "interaction_pattern"],
    "rebt-belief-work-support": ["ABC", "信念", "情绪", "行为", "认知", "艾利斯", "rational", "belief"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--skill", required=True, choices=sorted(SKILL_POLICIES))
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the evidence bundle JSON to stdout.")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fts_query(query: str, skill: str) -> str:
    tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_/-]+", query)
    tokens.extend(QUERY_EXPANSIONS.get(skill, []))
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = token.strip().replace('"', "")
        if not token or token in seen:
            continue
        seen.add(token)
        cleaned.append(token)
    return " OR ".join(f'"{token}"' for token in cleaned[:24]) or f'"{query}"'


def placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def fetch_candidates(db: Path, skill: str, query: str, top_k: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = SKILL_POLICIES[skill]
    families = list(policy["families"])
    blocked_families = list(policy.get("blocked_families") or [])
    blocked_routes = list(policy.get("blocked_routes") or [])
    risk_scopes = list(policy.get("risk_scopes") or [])
    match = fts_query(query, skill)
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
          substr(f.retrieval_text, 1, 900) as text_preview
        from retrieval_fts f
        join retrieval_meta m on m.retrieval_id = f.retrieval_id
        where {' and '.join(where)}
        order by bm25_score asc
        limit ?
    """
    params.append(max(top_k * 5, top_k))
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Fallback to the raw query if expansion creates an unsupported FTS expression.
        params[0] = f'"{query.replace(chr(34), "")}"'
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    family_priority = policy.get("family_priority") or {}
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        bm25_score = float(item.pop("bm25_score"))
        priority = float(family_priority.get(item.get("dedup_family"), 0.0))
        member_bonus = min(float(item.get("member_count") or 0), 50.0) / 1000.0
        # bm25 is lower-is-better and usually negative for FTS5; convert to a descending score.
        score = -bm25_score + priority + member_bonus
        item["score_detail"] = {
            "bm25_score": round(bm25_score, 6),
            "family_priority_bonus": round(priority, 6),
            "member_bonus": round(member_bonus, 6),
            "runtime_score": round(score, 6),
        }
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:top_k]], {
        "fts_query": match,
        "families": families,
        "blocked_families": blocked_families,
        "blocked_routes": blocked_routes,
        "risk_scopes": risk_scopes,
        "allow_synthetic_construction": bool(policy.get("allow_synthetic_construction")),
    }


def build_bundle(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    if not db.exists():
        raise FileNotFoundError(f"SQLite retrieval DB not found: {db}")
    items, resolved_policy = fetch_candidates(db, args.skill, args.query, args.top_k)
    families = {item.get("dedup_family") for item in items}
    routes = {item.get("default_route") for item in items}
    bundle = {
        "schema_version": "theraskill_runtime_evidence_bundle_v1",
        "created_at": utc_now(),
        "skill": args.skill,
        "query": args.query,
        "top_k": args.top_k,
        "source_index": str(db),
        "retrieval_policy": resolved_policy,
        "items": items,
        "policy_checks": {
            "has_release_unit_ids": all(bool(item.get("release_unit_id")) for item in items),
            "source_inventory_excluded": "source_inventory" not in families,
            "deferred_accounting_excluded": "accounting_only_requires_review" not in routes,
            "synthetic_construction_excluded": "synthetic_construction" not in families,
            "synthetic_construction_allowed_by_policy": bool(resolved_policy.get("allow_synthetic_construction")),
            "synthetic_construction_policy_ok": bool(resolved_policy.get("allow_synthetic_construction")) or "synthetic_construction" not in families,
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
        }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
