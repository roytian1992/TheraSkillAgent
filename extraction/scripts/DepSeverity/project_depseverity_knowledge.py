#!/usr/bin/env python3
"""Project DepSeverity source CSV into raw knowledge units.

This deterministic projection preserves all source text/label rows as
depression_severity_signal units and creates label-level triage boundary
examples from the same source rows. It does not call an LLM, sample, filter,
merge, or deduplicate rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET = "DepSeverity"
SOURCE_ROOT = "datasets/DepSeverity"
LICENSE = "not_specified_in_local_readme"
OUTPUTS = {
    "depression_severity_signal": "outputs/knowledge/units/DepSeverity.depression_severity_signal.jsonl",
    "triage_boundary_example": "outputs/knowledge/units/DepSeverity.triage_boundary_example.jsonl",
}
MANIFEST = "outputs/knowledge/manifests/DepSeverity.projection_manifest.json"
QA = "outputs/knowledge/manifests/DepSeverity.projection_qa.json"

SEVERITY_ORDER = {"minimum": 0, "mild": 1, "moderate": 2, "severe": 3}
SEVERITY_DEFINITIONS = {
    "minimum": "Source label for minimum depression severity in the released Reddit depression severity dataset.",
    "mild": "Source label for mild depression severity in the released Reddit depression severity dataset.",
    "moderate": "Source label for moderate depression severity in the released Reddit depression severity dataset.",
    "severe": "Source label for severe depression severity in the released Reddit depression severity dataset.",
}

MARKER_PATTERNS = {
    "symptom_markers": [
        "depress",
        "sad",
        "hopeless",
        "worthless",
        "guilt",
        "empty",
        "numb",
        "anxiety",
        "panic",
        "sleep",
        "insomnia",
        "tired",
        "exhausted",
        "appetite",
    ],
    "functional_impairment_markers": [
        "work",
        "school",
        "job",
        "class",
        "study",
        "relationship",
        "family",
        "friends",
        "can't get out",
        "cant get out",
        "unable",
        "avoid",
    ],
    "risk_related_markers": [
        "suicide",
        "suicidal",
        "kill myself",
        "die",
        "death",
        "self harm",
        "self-harm",
        "cut",
        "overdose",
        "abuse",
        "violence",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--qa-report", default=QA)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def unit_id(kind: str, source_id: str) -> str:
    return f"ku_depseverity_{kind}_{stable_hash(source_id)}"


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


def source_base(source_id: str, evidence_tier: str) -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "source_id": source_id,
        "language": "en",
        "source_files": ["Reddit_depression_dataset.csv"],
        "source_construction_type": "reddit_depression_severity_labelled_dataset",
        "evidence_tier": evidence_tier,
        "license": LICENSE,
        "citation": [
            "Naseem et al. Early Identification of Depression Severity Levels on Reddit Using Ordinal Classification. WWW 2022.",
        ],
    }


def match_markers(text: str, patterns: list[str]) -> list[str]:
    lowered = text.lower()
    return sorted({pattern for pattern in patterns if pattern in lowered})


def marker_summary(text: str) -> dict[str, list[str]]:
    return {name: match_markers(text, terms) for name, terms in MARKER_PATTERNS.items()}


def evidence_spans(text: str, markers: dict[str, list[str]]) -> list[dict[str, str]]:
    lowered = text.lower()
    spans = []
    for marker_type, terms in markers.items():
        for term in terms[:8]:
            idx = lowered.find(term)
            if idx >= 0:
                spans.append(
                    {
                        "marker_type": marker_type,
                        "matched_term": term,
                        "text_excerpt": text[max(0, idx - 120) : idx + len(term) + 180],
                    }
                )
    return spans[:20]


def build_embedding_text(kind: str, content: dict[str, Any]) -> str:
    parts = [
        content.get("severity_label", ""),
        content.get("severity_definition", ""),
        content.get("text", ""),
        json.dumps(content.get("symptom_markers", []), ensure_ascii=False),
        json.dumps(content.get("risk_related_markers", []), ensure_ascii=False),
    ]
    return "\n".join(part for part in parts if part)[:4000]


def wrap_unit(kind: str, source: dict[str, Any], content: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": unit_id(kind, source["source_id"]),
        "knowledge_type": kind,
        "source": source,
        "content": {kind: content},
        "quality": {
            "review_status": "deterministic_projection",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    "Severity label is the source dataset label, not a new clinical diagnosis.",
                    "Marker fields are deterministic lexical metadata and should not override source labels.",
                    "Local README does not specify a license.",
                ],
            },
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "source_labels_preserved",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": build_embedding_text(kind, content),
        },
        "provenance": provenance,
    }


def read_rows(source_root: Path) -> list[dict[str, str]]:
    path = source_root / "Reddit_depression_dataset.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def project_signals(rows: list[dict[str, str]], source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    label_counts = Counter()
    marker_counts = {name: Counter() for name in MARKER_PATTERNS}
    max_len = 0
    over_10k = 0
    over_12k = 0
    duplicate_texts = Counter()
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for idx, row in enumerate(rows):
        text = row["text"]
        label = row["label"].lower().strip()
        label_counts[label] += 1
        duplicate_texts[text] += 1
        max_len = max(max_len, len(text))
        over_10k += int(len(text) > 10000)
        over_12k += int(len(text) > 12000)
        markers = marker_summary(text)
        for marker_type, values in markers.items():
            marker_counts[marker_type].update(values)
        spans = evidence_spans(text, markers)
        source_id = f"Reddit_depression_dataset.csv:row/{idx}"
        content = {
            "sample_id": source_id,
            "text": text,
            "severity_label": label,
            "severity_order": SEVERITY_ORDER.get(label),
            "severity_definition": SEVERITY_DEFINITIONS.get(label, "unknown_source_label"),
            "symptom_markers": markers["symptom_markers"],
            "functional_impairment_markers": markers["functional_impairment_markers"],
            "risk_related_markers": markers["risk_related_markers"],
            "evidence_spans": spans,
            "text_length": len(text),
            "source_text_duplicate_count": duplicate_texts[text],
        }
        by_label[label].append(content)
        writer.write(
            wrap_unit(
                "depression_severity_signal",
                source_base(source_id, "source_depression_severity_label"),
                content,
                {
                    "projection": "deterministic_depseverity_signal_projection",
                    "source_root": str(source_root),
                    "row_index": idx,
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                    "deduplication": "none",
                },
            )
        )
    return {
        "rows": len(rows),
        "label_counts": dict(label_counts.most_common()),
        "max_text_length": max_len,
        "contexts_over_10k_chars": over_10k,
        "contexts_over_12k_chars": over_12k,
        "duplicate_text_rows": sum(count - 1 for count in duplicate_texts.values() if count > 1),
        "unique_texts": len(duplicate_texts),
        "marker_counts_top20": {name: dict(counter.most_common(20)) for name, counter in marker_counts.items()},
        "by_label": by_label,
    }


def project_boundaries(signal_stats: dict[str, Any], source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    rows_written = 0
    for label in ["minimum", "mild", "moderate", "severe"]:
        examples = signal_stats["by_label"].get(label, [])
        # Preserve source ordering and include a compact representative set for each label.
        example_refs = [
            {
                "sample_id": item["sample_id"],
                "text_excerpt": item["text"][:500],
                "symptom_markers": item["symptom_markers"],
                "functional_impairment_markers": item["functional_impairment_markers"],
                "risk_related_markers": item["risk_related_markers"],
                "evidence_spans": item["evidence_spans"][:5],
            }
            for item in examples[:20]
        ]
        source_id = f"triage_boundary:{label}"
        content = {
            "severity_label": label,
            "severity_order": SEVERITY_ORDER.get(label),
            "severity_definition": SEVERITY_DEFINITIONS.get(label, "unknown_source_label"),
            "source_row_count": len(examples),
            "triage_boundary": "Source label boundary for depression severity; no new clinical threshold inferred.",
            "distress_summary": "Label-level aggregation of Reddit depression severity source rows.",
            "example_refs": example_refs,
            "limitations": [
                "This boundary unit summarizes source labels and examples, not a clinical screening rule.",
                "Examples are not deduplicated or filtered; first source rows are preserved as references.",
            ],
        }
        writer.write(
            wrap_unit(
                "triage_boundary_example",
                source_base(source_id, "source_depression_severity_label_aggregation"),
                content,
                {
                    "projection": "deterministic_depseverity_boundary_projection",
                    "source_root": str(source_root),
                    "severity_label": label,
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                    "deduplication": "none",
                },
            )
        )
        rows_written += 1
    return {"rows": rows_written}


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root)
    rows = read_rows(source_root)
    signal_writer = JsonlWriter(OUTPUTS["depression_severity_signal"], overwrite=args.overwrite)
    boundary_writer = JsonlWriter(OUTPUTS["triage_boundary_example"], overwrite=args.overwrite)
    signal_stats = project_signals(rows, source_root, signal_writer)
    boundary_stats = project_boundaries(signal_stats, source_root, boundary_writer)
    # Do not serialize full by_label rows into manifest.
    manifest_signal_stats = {k: v for k, v in signal_stats.items() if k != "by_label"}
    unit_files = {
        "depression_severity_signal": signal_writer.close(),
        "triage_boundary_example": boundary_writer.close(),
    }
    source_rels = ["README.md", "Reddit_depression_dataset.csv"]
    manifest = {
        "schema_version": "theraskill_depseverity_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete",
        "source_root": str(source_root),
        "source_files": {
            rel: {"path": str(source_root / rel), "sha256": file_sha256(source_root / rel)}
            for rel in source_rels
        },
        "projection_policy": "deterministic raw DepSeverity projection; no LLM calls, no sampling, no filtering, no cross-dataset deduplication",
        "source_audit": manifest_signal_stats,
        "boundary_projection": boundary_stats,
        "unit_files": unit_files,
        "qa_report": args.qa_report,
        "issues_count": 0,
    }
    write_json(args.manifest, manifest)
    expected_rows = {"depression_severity_signal": 3553, "triage_boundary_example": 4}
    issues = []
    for kind, expected in expected_rows.items():
        if unit_files[kind]["rows"] != expected:
            issues.append({"issue": "unexpected_row_count", "kind": kind, "expected": expected, "actual": unit_files[kind]["rows"]})
    qa = {
        "schema_version": "theraskill_depseverity_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete" if not issues else "incomplete",
        "unit_files": unit_files,
        "source_audit": manifest_signal_stats,
        "expected_rows": expected_rows,
        "issues": issues,
    }
    qa["issues_count"] = len(issues)
    write_json(args.qa_report, qa)
    if issues:
        manifest["status"] = "incomplete"
        manifest["issues_count"] = len(issues)
        write_json(args.manifest, manifest)
    print(json.dumps({"status": qa["status"], "issues_count": len(issues), "unit_files": unit_files, "qa_report": args.qa_report}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
