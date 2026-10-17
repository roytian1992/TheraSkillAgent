"""Compact label schema compatible with the policy-aware retriever."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any

def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def compact_text(obj: dict[str, Any], prefix_chars: int) -> str:
    parts: list[str] = []
    for key in (
        "canonical_label",
        "canonical_knowledge_type",
        "primary_knowledge_type",
        "primary_evidence_tier",
        "primary_dataset",
        "dedup_family",
        "risk_scope",
        "source_weight",
        "quality_confidence",
    ):
        value = clean(obj.get(key))
        if value:
            parts.append(value.replace("_", " "))
    for key in ("knowledge_type_keys", "evidence_tier_keys", "dataset_keys", "language_keys"):
        for value in obj.get(key) or []:
            text = clean(value)
            if text:
                parts.append(text.replace("_", " "))
    retrieval_text = clean(obj.get("retrieval_text"))
    if retrieval_text:
        parts.append(retrieval_text[:prefix_chars])
    return " ".join(parts)


def connect(path: Path, overwrite: bool) -> sqlite3.Connection:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"compact label DB exists; pass --overwrite: {path}")
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.execute("pragma temp_store=memory")
    con.execute(
        """
        create table compact_label_meta (
          retrieval_id text primary key,
          release_unit_id text not null,
          dedup_family text,
          canonical_label text,
          canonical_knowledge_type text,
          primary_dataset text,
          primary_knowledge_type text,
          primary_evidence_tier text,
          primary_language text,
          quality_confidence text,
          source_weight text,
          risk_scope text,
          default_route text,
          retrieval_eligible integer,
          embedding_eligible integer,
          member_count integer,
          retrieval_text_chars integer
        )
        """
    )
    con.execute(
        """
        create virtual table compact_label_fts using fts5(
          retrieval_id unindexed,
          compact_text,
          tokenize='unicode61'
        )
        """
    )
    return con


def create_indexes(con: sqlite3.Connection) -> None:
    for sql in (
        "create index idx_compact_label_meta_family on compact_label_meta(dedup_family)",
        "create index idx_compact_label_meta_route on compact_label_meta(default_route)",
        "create index idx_compact_label_meta_risk on compact_label_meta(risk_scope)",
        "create index idx_compact_label_meta_knowledge_type on compact_label_meta(primary_knowledge_type)",
        "create index idx_compact_label_meta_dataset on compact_label_meta(primary_dataset)",
        "create index idx_compact_label_meta_source_weight on compact_label_meta(source_weight)",
        "create index idx_compact_label_meta_family_route on compact_label_meta(dedup_family, default_route)",
    ):
        con.execute(sql)


def meta_row(obj: dict[str, Any]) -> tuple[Any, ...]:
    return (
        clean(obj.get("retrieval_id")),
        clean(obj.get("release_unit_id")),
        clean(obj.get("dedup_family")),
        clean(obj.get("canonical_label")),
        clean(obj.get("canonical_knowledge_type")),
        clean(obj.get("primary_dataset")),
        clean(obj.get("primary_knowledge_type")),
        clean(obj.get("primary_evidence_tier")),
        clean(obj.get("primary_language")),
        clean(obj.get("quality_confidence")),
        clean(obj.get("source_weight")),
        clean(obj.get("risk_scope")),
        clean(obj.get("default_route")),
        1 if obj.get("retrieval_eligible") else 0,
        1 if obj.get("embedding_eligible") else 0,
        int(obj.get("member_count") or 0),
        int(obj.get("retrieval_text_chars") or 0),
    )
