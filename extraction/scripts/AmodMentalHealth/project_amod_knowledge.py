#!/usr/bin/env python3
"""Project Amod mental-health counseling conversations into raw knowledge units."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET = "Amod_mental_health_counseling_conversations"
SOURCE_ROOT = "datasets/Amod_mental_health_counseling_conversations"
LICENSE = "RAIL-D"
UNIT_FILE = "outputs/knowledge/units/Amod_mental_health_counseling_conversations.psychoeducation_candidate.jsonl"
MANIFEST = "outputs/knowledge/manifests/Amod_mental_health_counseling_conversations.projection_manifest.json"
QA = "outputs/knowledge/manifests/Amod_mental_health_counseling_conversations.projection_qa.json"

TOPIC_TERMS = {
    "risk_suicide_self_harm": ["suicide", "suicidal", "kill myself", "self harm", "self-harm", "hurt myself", "end my life", "shouldn't be here"],
    "anxiety_panic": ["anxiety", "panic", "anxious", "worry", "worried"],
    "depression_mood": ["depress", "sad", "hopeless", "worthless", "mood"],
    "relationship_family": ["relationship", "boyfriend", "girlfriend", "husband", "wife", "marriage", "family", "mother", "father", "parent"],
    "trauma_abuse": ["trauma", "abuse", "abusive", "ptsd", "assault"],
    "therapy_process": ["therapist", "counselor", "counselling", "counseling", "therapy", "session"],
    "medication_diagnosis": ["medication", "diagnos", "bipolar", "adhd", "ocd", "borderline"],
}
DATA_URI_RE = re.compile(r"data:image/[^\"'\\s<>]+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--unit-file", default=UNIT_FILE)
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--qa-report", default=QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def file_sha256(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class JsonlWriter:
    def __init__(self, path: str | Path, *, overwrite: bool) -> None:
        self.path = Path(path)
        if self.path.exists() and not overwrite:
            raise FileExistsError(f"output exists: {self.path}; pass --overwrite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8")
        self.digest = hashlib.sha256()
        self.rows = 0

    def write(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        self.digest.update(line.encode("utf-8"))
        self.handle.write(line)
        self.rows += 1

    def close(self) -> dict[str, Any]:
        self.handle.close()
        return {"path": str(self.path), "rows": self.rows, "sha256": self.digest.hexdigest()}


def read_rows(source_root: Path) -> list[dict[str, Any]]:
    rows = []
    with (source_root / "combined_dataset.json").open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def topic_signals(text: str) -> list[str]:
    lower = text.lower()
    labels = [label for label, terms in TOPIC_TERMS.items() if any(term in lower for term in terms)]
    return labels or ["general_mental_health_support"]


def response_structure(response: str) -> list[str]:
    lower = response.lower()
    structure = []
    if not response.strip():
        structure.append("empty_response")
    if len(response) > 10000:
        structure.append("very_long_response")
    if "?" in response:
        structure.append("uses_reflective_questions")
    if any(marker in response for marker in ["1.", "2.", "- ", "\n-"]):
        structure.append("list_or_step_structure")
    if any(term in lower for term in ["therapist", "counselor", "therapy", "professional help"]):
        structure.append("refers_to_professional_support")
    if any(term in lower for term in ["lifeline", "crisis", "911", "emergency", "741741"]):
        structure.append("mentions_crisis_or_emergency_resource")
    if any(term in lower for term in ["medication", "psychiatrist", "diagnosis", "diagnosed"]):
        structure.append("mentions_diagnosis_or_medication")
    if "data:image/" in lower:
        structure.append("contains_embedded_image_data")
    return structure or ["plain_advice_response"]


def clean_for_embedding(text: str) -> str:
    text = DATA_URI_RE.sub("[embedded image data omitted from embedding_text]", text)
    text = TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_embedding_text(context: str, response: str, topics: list[str], structure: list[str]) -> str:
    parts = [
        "dataset: Amod mental health counseling conversations",
        "topics: " + ", ".join(topics),
        "response_structure: " + ", ".join(structure),
        "context: " + clean_for_embedding(context),
        "response: " + clean_for_embedding(response),
    ]
    return "\n".join(part for part in parts if part)[:4000]


def make_unit(row: dict[str, Any], row_index: int, source_root: Path) -> dict[str, Any]:
    context = str(row.get("Context", ""))
    response = str(row.get("Response", ""))
    source_id = f"combined_dataset.json:row/{row_index}"
    topics = topic_signals(context + "\n" + response)
    structure = response_structure(response)
    flags = {
        "empty_response": not response.strip(),
        "context_over_10k_chars": len(context) > 10000,
        "response_over_10k_chars": len(response) > 10000,
        "pair_over_10k_chars": len(context) + len(response) > 10000,
        "pair_over_12k_chars": len(context) + len(response) > 12000,
        "mentions_suicide_or_self_harm": "risk_suicide_self_harm" in topics,
        "contains_data_uri": "data:image/" in response.lower(),
    }
    content = {
        "sample_id": str(row_index),
        "context_text": context,
        "response_text": response,
        "topic_signals": topics,
        "response_structure": structure,
        "lengths": {
            "context_chars": len(context),
            "response_chars": len(response),
            "pair_chars": len(context) + len(response),
        },
        "safety_and_quality_flags": flags,
        "license_status": "RAIL-D; README states non-commercial research use is free, commercial use requires donation proof, and individual QA pairs must not be rewritten, deleted, or altered.",
        "quality_note": "raw_public_counseling_qa_reference_preserved_verbatim",
    }
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": f"ku_amod_psychoeducation_candidate_{stable_hash(source_id)}",
        "knowledge_type": "psychoeducation_candidate",
        "source": {
            "dataset": DATASET,
            "source_id": source_id,
            "language": "en",
            "source_files": ["combined_dataset.json"],
            "source_construction_type": "amod_public_counseling_qa_compilation",
            "evidence_tier": "public_counseling_qa_pair",
            "license": LICENSE,
            "citation": "Amod/mental_health_counseling_conversations local Hugging Face dataset card; README describes public counselling website QA pairs.",
        },
        "content": {"psychoeducation_candidate": content},
        "quality": {
            "review_status": "deterministic_projection",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    "Source README says answers are from licensed professionals, but credentials are not independently verified in this projection.",
                    "Some rows are duplicated by question or response; no deduplication is performed in the raw knowledge phase.",
                    "Some rows have empty responses or embedded image data; these are preserved and flagged.",
                    "This dataset is advice/psychoeducation QA, not a controlled therapy process dataset.",
                ],
            },
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "no_source_controlled_labels; deterministic topic and structure metadata added",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": build_embedding_text(context, response, topics, structure),
        },
        "provenance": {
            "projection": "deterministic_amod_psychoeducation_candidate_projection",
            "source_root": str(source_root),
            "row_index": row_index,
            "llm_calls": 0,
            "sampling": "none",
            "filtering": "none",
            "deduplication": "none",
            "content_modification": "none; source Context and Response preserved verbatim",
        },
    }


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = Counter()
    topics = Counter()
    structures = Counter()
    context_counts = Counter()
    response_counts = Counter()
    context_lengths = []
    response_lengths = []
    empty_response_rows = []
    over_10k_rows = []
    over_12k_rows = []
    data_uri_rows = []
    for row_index, row in enumerate(rows):
        keys.update(row.keys())
        context = str(row.get("Context", ""))
        response = str(row.get("Response", ""))
        topic = topic_signals(context + "\n" + response)
        structure = response_structure(response)
        topics.update(topic)
        structures.update(structure)
        context_counts[context] += 1
        response_counts[response] += 1
        context_lengths.append(len(context))
        response_lengths.append(len(response))
        pair_len = len(context) + len(response)
        if not response.strip():
            empty_response_rows.append(row_index)
        if pair_len > 10000:
            over_10k_rows.append({"row_index": row_index, "context_chars": len(context), "response_chars": len(response), "pair_chars": pair_len})
        if pair_len > 12000:
            over_12k_rows.append({"row_index": row_index, "context_chars": len(context), "response_chars": len(response), "pair_chars": pair_len})
        if "data:image/" in response.lower():
            data_uri_rows.append(row_index)

    def percentile(values: list[int], q: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * q))]

    return {
        "rows": len(rows),
        "key_distribution": dict(keys.most_common()),
        "unique_contexts": len(context_counts),
        "duplicate_context_rows": sum(count - 1 for count in context_counts.values() if count > 1),
        "unique_responses": len(response_counts),
        "duplicate_response_rows": sum(count - 1 for count in response_counts.values() if count > 1),
        "empty_response_rows": empty_response_rows,
        "empty_response_count": len(empty_response_rows),
        "context_length": {
            "min": min(context_lengths) if context_lengths else 0,
            "max": max(context_lengths) if context_lengths else 0,
            "p95": percentile(context_lengths, 0.95),
            "p99": percentile(context_lengths, 0.99),
        },
        "response_length": {
            "min": min(response_lengths) if response_lengths else 0,
            "max": max(response_lengths) if response_lengths else 0,
            "p95": percentile(response_lengths, 0.95),
            "p99": percentile(response_lengths, 0.99),
        },
        "pair_over_10k_rows": over_10k_rows,
        "pair_over_12k_rows": over_12k_rows,
        "data_uri_rows": data_uri_rows,
        "topic_signal_distribution": dict(topics.most_common()),
        "response_structure_distribution": dict(structures.most_common()),
    }


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root)
    rows = read_rows(source_root)
    writer = JsonlWriter(args.unit_file, overwrite=args.overwrite)
    for row_index, row in enumerate(rows):
        writer.write(make_unit(row, row_index, source_root))
    unit_file = writer.close()
    audit = audit_rows(rows)
    source_files = ["README.md", "LICENSE-RAIL-D.txt", "dataset_infos.json", "combined_dataset.json"]
    issues = []
    if unit_file["rows"] != 3512:
        issues.append({"issue": "unexpected_row_count", "expected": 3512, "actual": unit_file["rows"]})
    manifest = {
        "schema_version": "theraskill_amod_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete" if not issues else "incomplete",
        "source_root": str(source_root),
        "source_files": {
            rel: {"path": str(source_root / rel), "sha256": file_sha256(source_root / rel)}
            for rel in source_files
        },
        "projection_policy": "deterministic full projection; source Context and Response preserved verbatim; no LLM calls, no sampling, no filtering, no cross-dataset deduplication",
        "source_audit": audit,
        "unit_files": {"psychoeducation_candidate": unit_file},
        "qa_report": args.qa_report,
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest)
    qa = {
        "schema_version": "theraskill_amod_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": manifest["status"],
        "unit_file": unit_file,
        "source_audit": audit,
        "issues": issues,
        "issues_count": len(issues),
    }
    write_json(args.qa_report, qa)
    print(json.dumps({"status": qa["status"], "issues_count": len(issues), "unit_file": unit_file, "qa_report": args.qa_report}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
