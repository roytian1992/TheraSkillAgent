"""Portable Phase 6 projection; migrated from KnowledgeEngine. No research paths."""
from __future__ import annotations
import hashlib
from typing import Any

def stable_retrieval_id(release_unit_id: str) -> str:
    return "ret_" + hashlib.sha256(release_unit_id.encode("utf-8")).hexdigest()[:24]


def first_key(mapping: dict[str, Any] | None) -> str | None:
    if not mapping:
        return None
    return next(iter(mapping))


def list_keys(mapping: dict[str, Any] | None, limit: int = 20) -> list[str]:
    if not mapping:
        return []
    return [str(key) for key in list(mapping)[:limit]]


def normalize_text(value: Any) -> str:
    text = str(value or "")
    return " ".join(text.split())


def effective_content(row: dict[str, Any]) -> dict[str, Any]:
    content = dict(row.get('content') or {})
    registry = dict(content.get('registry_fields') or {})
    edits = (row.get('_theraskill_editor') or {}).get('fields') or {}
    for key, value in edits.items():
        if key in {'canonical_label', 'summary', 'evidence_source'}:
            content[key] = value
        elif key in {'applicable_context', 'allowed_use', 'forbidden_use', 'contraindications'}:
            registry[key] = value
    content['registry_fields'] = registry
    return content


def _text(value: Any) -> str:
    if isinstance(value, list): return ' '.join(_text(item) for item in value)
    if isinstance(value, dict): return ' '.join(_text(item) for item in value.values())
    return normalize_text(value)


def build_retrieval_text(row: dict[str, Any]) -> str:
    content = effective_content(row)
    rep = content.get('representative_text_ref') or {}
    edits = (row.get('_theraskill_editor') or {}).get('fields') or {}
    parts = [content.get('canonical_label'), content.get('summary'), content.get('evidence_source')]
    # Preserve source exemplars for audit, but don't reintroduce a corrected
    # semantic description through its old shared_intent or text preview.
    keys = []
    if 'summary' not in edits:
        parts.extend([content.get('source_faithfulness_notes'), rep.get('dedup_text_preview')])
        keys.extend(['shared_question_or_need', 'shared_answer_intent', 'shared_intent', 'shared_pattern'])
    keys.extend(['clinical_boundary_notes', 'next_review_gate', 'applicable_context',
                 'evidence_source', 'allowed_use', 'forbidden_use', 'contraindications'])
    registry = content.get('registry_fields') or {}
    parts.extend(registry.get(key) for key in keys)
    return normalize_text(' '.join(_text(part) for part in parts if part))


def infer_risk_scope(row: dict[str, Any], retrieval_text: str) -> str:
    family = row.get("dedup_family")
    if family == "clinical_risk_assessment":
        return "clinical_risk_or_assessment"
    lowered = retrieval_text.lower()
    if any(token in lowered for token in ["suicide", "self-harm", "自杀", "自伤", "危机"]):
        return "risk_related_text"
    return "ordinary_or_unspecified"


def infer_source_weight(row: dict[str, Any]) -> str:
    evidence = row.get("evidence_tier_distribution") or {}
    joined = " ".join(evidence)
    if "expert" in joined or "human" in joined:
        return "human_or_expert_annotated"
    if "clinical" in joined or "therapeutic_process" in joined:
        return "clinical_or_therapeutic_process"
    if "synthetic" in joined or row.get("dedup_family") == "synthetic_construction":
        return "synthetic_or_construction"
    if row.get("release_unit_type") == "provenance_only":
        return "provenance_only"
    return "general_reviewed_or_raw"


def retrieval_row(row: dict[str, Any], line_no: int) -> dict[str, Any]:
    policy = row.get("retrieval_policy") or {}
    retrieval_text = build_retrieval_text(row)
    release_unit_id = str(row.get("release_unit_id") or "")
    content = effective_content(row)
    rep = content.get("representative_text_ref") or {}
    family = str(row.get("dedup_family") or "unknown")
    return {
        "schema_version": "theraskill_retrieval_text_view_v1",
        "retrieval_id": stable_retrieval_id(release_unit_id),
        "release_unit_id": release_unit_id,
        "release_unit_type": row.get("release_unit_type"),
        "dedup_family": family,
        "canonical_knowledge_type": row.get("canonical_knowledge_type"),
        "representative_unit_id": row.get("representative_unit_id"),
        "member_count": row.get("member_count"),
        "dataset_keys": list_keys(row.get("dataset_distribution")),
        "knowledge_type_keys": list_keys(row.get("knowledge_type_distribution")),
        "evidence_tier_keys": list_keys(row.get("evidence_tier_distribution")),
        "language_keys": list_keys(row.get("language_distribution")),
        "license_constraint_keys": list_keys(row.get("license_constraints_distribution")),
        "primary_dataset": first_key(row.get("dataset_distribution")),
        "primary_knowledge_type": first_key(row.get("knowledge_type_distribution")),
        "primary_evidence_tier": first_key(row.get("evidence_tier_distribution")),
        "primary_language": first_key(row.get("language_distribution")),
        "canonical_label": content.get("canonical_label"),
        "quality_confidence": (row.get("quality") or {}).get("confidence"),
        "registry_status": (row.get("quality") or {}).get("registry_status"),
        "retrieval_eligible": bool(policy.get("retrieval_eligible")),
        "embedding_eligible": bool(policy.get("embedding_eligible")),
        "default_route": policy.get("default_route"),
        "risk_scope": infer_risk_scope(row, retrieval_text),
        "source_weight": infer_source_weight(row),
        "retrieval_text": retrieval_text,
        "retrieval_text_sha256": hashlib.sha256(retrieval_text.encode("utf-8")).hexdigest(),
        "retrieval_text_chars": len(retrieval_text),
        "representative_text_ref": {
            "unit_id": rep.get("unit_id"),
            "raw_file": rep.get("raw_file"),
            "raw_line_no": rep.get("raw_line_no"),
            "dedup_text_sha256": rep.get("dedup_text_sha256"),
        },
        "source_release_line_no": line_no,
    }


def structured_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_structured_retrieval_index_v1",
        "retrieval_id": row["retrieval_id"],
        "release_unit_id": row["release_unit_id"],
        "release_unit_type": row["release_unit_type"],
        "dedup_family": row["dedup_family"],
        "canonical_knowledge_type": row["canonical_knowledge_type"],
        "retrieval_eligible": row["retrieval_eligible"],
        "embedding_eligible": row["embedding_eligible"],
        "default_route": row["default_route"],
        "primary_language": row["primary_language"],
        "language_keys": row["language_keys"],
        "primary_dataset": row["primary_dataset"],
        "dataset_keys": row["dataset_keys"],
        "primary_knowledge_type": row["primary_knowledge_type"],
        "knowledge_type_keys": row["knowledge_type_keys"],
        "primary_evidence_tier": row["primary_evidence_tier"],
        "evidence_tier_keys": row["evidence_tier_keys"],
        "license_constraint_keys": row["license_constraint_keys"],
        "risk_scope": row["risk_scope"],
        "source_weight": row["source_weight"],
        "member_count": row["member_count"],
        "quality_confidence": row["quality_confidence"],
        "registry_status": row["registry_status"],
        "retrieval_text_sha256": row["retrieval_text_sha256"],
        "retrieval_text_chars": row["retrieval_text_chars"],
    }
