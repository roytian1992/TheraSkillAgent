"""Metadata schema compatible with the policy-aware retriever."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
LIST_FIELDS={"dataset_keys","evidence_tier_keys","knowledge_type_keys","language_keys","license_constraint_keys"}

def clean_scalar(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_scalar(value)
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def connect_db(path: Path, overwrite: bool) -> sqlite3.Connection:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"metadata_v2 DB exists; pass --overwrite: {path}")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.execute("pragma temp_store=memory")
    con.execute(
        """
        create table metadata_v2 (
          retrieval_id text primary key,
          release_unit_id text not null,
          release_unit_type text,
          dedup_family text,
          canonical_knowledge_type text,
          primary_dataset text,
          primary_knowledge_type text,
          primary_evidence_tier text,
          primary_language text,
          quality_confidence text,
          registry_status text,
          source_weight text,
          risk_scope text,
          default_route text,
          retrieval_eligible integer,
          embedding_eligible integer,
          member_count integer,
          retrieval_text_chars integer,
          retrieval_text_sha256 text
        )
        """
    )
    con.execute(
        """
        create table metadata_v2_tokens (
          retrieval_id text not null,
          field text not null,
          value text not null,
          primary key (retrieval_id, field, value)
        )
        """
    )
    return con


def create_indexes(con: sqlite3.Connection) -> None:
    indexes = [
        "create index idx_metadata_v2_family on metadata_v2(dedup_family)",
        "create index idx_metadata_v2_route on metadata_v2(default_route)",
        "create index idx_metadata_v2_risk on metadata_v2(risk_scope)",
        "create index idx_metadata_v2_language on metadata_v2(primary_language)",
        "create index idx_metadata_v2_quality on metadata_v2(quality_confidence)",
        "create index idx_metadata_v2_source_weight on metadata_v2(source_weight)",
        "create index idx_metadata_v2_knowledge_type on metadata_v2(primary_knowledge_type)",
        "create index idx_metadata_v2_evidence_tier on metadata_v2(primary_evidence_tier)",
        "create index idx_metadata_v2_dataset on metadata_v2(primary_dataset)",
        "create index idx_metadata_v2_eligible on metadata_v2(retrieval_eligible, embedding_eligible)",
        "create index idx_metadata_v2_family_route_risk on metadata_v2(dedup_family, default_route, risk_scope)",
        "create index idx_metadata_v2_tokens_field_value on metadata_v2_tokens(field, value)",
        "create index idx_metadata_v2_tokens_retrieval on metadata_v2_tokens(retrieval_id)",
    ]
    for sql in indexes:
        con.execute(sql)


def metadata_row(obj: dict[str, Any]) -> tuple[Any, ...]:
    return (
        clean_scalar(obj.get("retrieval_id")),
        clean_scalar(obj.get("release_unit_id")),
        clean_scalar(obj.get("release_unit_type")),
        clean_scalar(obj.get("dedup_family")),
        clean_scalar(obj.get("canonical_knowledge_type")),
        clean_scalar(obj.get("primary_dataset")),
        clean_scalar(obj.get("primary_knowledge_type")),
        clean_scalar(obj.get("primary_evidence_tier")),
        clean_scalar(obj.get("primary_language")),
        clean_scalar(obj.get("quality_confidence")),
        clean_scalar(obj.get("registry_status")),
        clean_scalar(obj.get("source_weight")),
        clean_scalar(obj.get("risk_scope")),
        clean_scalar(obj.get("default_route")),
        1 if obj.get("retrieval_eligible") else 0,
        1 if obj.get("embedding_eligible") else 0,
        int(obj.get("member_count") or 0),
        int(obj.get("retrieval_text_chars") or 0),
        clean_scalar(obj.get("retrieval_text_sha256")),
    )


def token_rows(obj: dict[str, Any]) -> list[tuple[str, str, str]]:
    retrieval_id = clean_scalar(obj.get("retrieval_id"))
    if not retrieval_id:
        return []
    rows: list[tuple[str, str, str]] = []
    for field in LIST_FIELDS:
        for value in clean_list(obj.get(field)):
            rows.append((retrieval_id, field, value))
    return rows
