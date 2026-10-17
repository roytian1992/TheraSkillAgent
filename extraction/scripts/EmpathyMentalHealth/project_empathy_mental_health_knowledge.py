#!/usr/bin/env python3
"""Project Empathy-Mental-Health Reddit annotations into raw knowledge units.

The local dataset contains the same Reddit post-response rows annotated across
three empathy communication mechanisms. This script deterministically preserves
the source labels and rationale spans without LLM calls, sampling, filtering,
or cross-dataset deduplication.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET = "Empathy-Mental-Health"
SOURCE_ROOT = "datasets/Empathy-Mental-Health"
LICENSE = "not_specified_in_local_readme"
OUTPUTS = {
    "empathy_response_unit": "outputs/knowledge/units/Empathy-Mental-Health.empathy_response_unit.jsonl",
    "empathy_quality_rubric": "outputs/knowledge/units/Empathy-Mental-Health.empathy_quality_rubric.jsonl",
}
MANIFEST = "outputs/knowledge/manifests/Empathy-Mental-Health.projection_manifest.json"
QA = "outputs/knowledge/manifests/Empathy-Mental-Health.projection_qa.json"

MECHANISM_FILES = {
    "emotional_reactions": "emotional-reactions-reddit.csv",
    "interpretations": "interpretations-reddit.csv",
    "explorations": "explorations-reddit.csv",
}

MECHANISM_DEFINITIONS = {
    "emotional_reactions": "Response communicates affective resonance, warmth, concern, or validation toward the seeker's experience.",
    "interpretations": "Response communicates understanding of the seeker's feelings, experiences, situation, or implied meanings.",
    "explorations": "Response invites further disclosure, elaboration, or collaborative exploration of the seeker's experience.",
}

LEVEL_DEFINITIONS = {
    "0": "no_or_weak_source_evidence",
    "1": "weak_communication",
    "2": "strong_communication",
}

RISK_TERMS = [
    "suicide",
    "suicidal",
    "kill myself",
    "end it all",
    "self harm",
    "self-harm",
    "harmed myself",
    "cut myself",
    "overdose",
    "abuse",
    "abused",
    "violent",
    "hospital",
]


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
    return f"ku_empathy_mh_{kind}_{stable_hash(source_id)}"


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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def split_rationales(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def risk_terms(text: str) -> list[str]:
    lowered = text.lower()
    return sorted({term for term in RISK_TERMS if term in lowered})


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def source_base(source_id: str, source_files: list[str], evidence_tier: str) -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "source_id": source_id,
        "language": "en",
        "source_files": source_files,
        "source_construction_type": "expert_annotated_reddit_empathy_dataset",
        "evidence_tier": evidence_tier,
        "license": LICENSE,
        "citation": [
            "Sharma, Miner, Atkins, and Althoff. A Computational Approach to Understanding Empathy Expressed in Text-Based Mental Health Support. EMNLP 2020.",
        ],
    }


def build_embedding_text(knowledge_type: str, content: dict[str, Any]) -> str:
    if knowledge_type == "empathy_response_unit":
        parts = [
            content.get("seeker_post", ""),
            content.get("response_post", ""),
            json.dumps(content.get("empathy_annotations", {}), ensure_ascii=False, sort_keys=True),
        ]
    else:
        parts = [
            content.get("empathy_dimension", ""),
            content.get("source_level", ""),
            content.get("level_definition", ""),
            content.get("dimension_definition", ""),
            json.dumps(content.get("positive_markers_from_rationales", []), ensure_ascii=False),
            json.dumps(content.get("example_response_refs", []), ensure_ascii=False),
        ]
    return "\n".join(part for part in parts if part)[:4000]


def wrap_unit(knowledge_type: str, source: dict[str, Any], content: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": unit_id(knowledge_type, source["source_id"]),
        "knowledge_type": knowledge_type,
        "source": source,
        "content": {knowledge_type: content},
        "quality": {
            "review_status": "deterministic_projection",
            "quality_status": {
                "status": "raw_source_knowledge",
                "limitations": [
                    "Rows are Reddit peer-support post-response pairs, not clinical treatment evidence.",
                    "Empathy levels and rationales are source annotations and should not be treated as therapy outcome labels.",
                    "Local README does not specify a redistribution license.",
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
            "embedding_text": build_embedding_text(knowledge_type, content),
        },
        "provenance": provenance,
    }


def load_mechanism_rows(source_root: Path) -> dict[str, list[dict[str, str]]]:
    rows = {}
    for mechanism, filename in MECHANISM_FILES.items():
        rows[mechanism] = read_csv(source_root / "dataset" / filename)
    return rows


def project_response_units(rows_by_mechanism: dict[str, list[dict[str, str]]], source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    row_count = len(next(iter(rows_by_mechanism.values())))
    mechanism_row_counts = {mechanism: len(rows) for mechanism, rows in rows_by_mechanism.items()}
    same_key_order = True
    exact_text_same_order = True
    normalized_text_same_order = True
    exact_text_variant_rows = 0
    normalized_text_variant_rows = 0
    key_counter: Counter[tuple[str, str]] = Counter()
    level_counts = defaultdict(Counter)
    rationale_nonempty = Counter()
    rationale_span_counts = Counter()
    risk_counts = Counter()
    max_chars = 0
    over_10k = 0
    over_12k = 0

    first_mechanism = next(iter(MECHANISM_FILES))
    base_rows = rows_by_mechanism[first_mechanism]

    for idx in range(row_count):
        base = base_rows[idx]
        pair_key = (base["sp_id"], base["rp_id"])
        key_counter[pair_key] += 1
        annotations = {}
        source_files = []
        row_exact_text_variant = False
        row_normalized_text_variant = False
        for mechanism, filename in MECHANISM_FILES.items():
            row = rows_by_mechanism[mechanism][idx]
            source_files.append(f"dataset/{filename}")
            if (row["sp_id"], row["rp_id"]) != (base["sp_id"], base["rp_id"]):
                same_key_order = False
            if (row["seeker_post"], row["response_post"]) != (base["seeker_post"], base["response_post"]):
                exact_text_same_order = False
                row_exact_text_variant = True
            if (normalize_ws(row["seeker_post"]), normalize_ws(row["response_post"])) != (
                normalize_ws(base["seeker_post"]),
                normalize_ws(base["response_post"]),
            ):
                normalized_text_same_order = False
                row_normalized_text_variant = True
            spans = split_rationales(row.get("rationales", ""))
            level = str(row.get("level", ""))
            annotations[mechanism] = {
                "source_level": level,
                "level_definition": LEVEL_DEFINITIONS.get(level, "unknown_source_level"),
                "dimension_definition": MECHANISM_DEFINITIONS[mechanism],
                "rationale_spans": spans,
                "rationale_span_count": len(spans),
            }
            level_counts[mechanism][level] += 1
            if spans:
                rationale_nonempty[mechanism] += 1
            rationale_span_counts[mechanism] += len(spans)

        source_id = f"reddit_pair_row:{idx:05d}:sp:{base['sp_id']}:rp:{base['rp_id']}"
        text_for_risk = "\n".join([base.get("seeker_post", ""), base.get("response_post", "")])
        risks = risk_terms(text_for_risk)
        risk_counts.update(risks)
        total_chars = len(base.get("seeker_post", "")) + len(base.get("response_post", ""))
        max_chars = max(max_chars, total_chars)
        over_10k += int(total_chars > 10000)
        over_12k += int(total_chars > 12000)
        content = {
            "row_index": idx,
            "sp_id": base["sp_id"],
            "rp_id": base["rp_id"],
            "seeker_post": base.get("seeker_post", ""),
            "response_post": base.get("response_post", ""),
            "empathy_annotations": annotations,
            "risk_terms": risks,
            "total_char_length": total_chars,
            "source_pair_duplicate_count": key_counter[pair_key],
            "source_text_variant_audit": {
                "canonical_text_source": "dataset/emotional-reactions-reddit.csv",
                "exact_text_variant_across_mechanism_files": row_exact_text_variant,
                "normalized_text_variant_across_mechanism_files": row_normalized_text_variant,
            },
        }
        exact_text_variant_rows += int(row_exact_text_variant)
        normalized_text_variant_rows += int(row_normalized_text_variant)
        writer.write(
            wrap_unit(
                "empathy_response_unit",
                source_base(source_id, source_files, "expert_annotated_peer_support_empathy_response"),
                content,
                {
                    "projection": "deterministic_empathy_response_projection",
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
        "row_count": row_count,
        "mechanism_row_counts": mechanism_row_counts,
        "mechanism_files_same_key_order": same_key_order,
        "mechanism_files_exact_text_same_order": exact_text_same_order,
        "mechanism_files_normalized_text_same_order": normalized_text_same_order,
        "exact_text_variant_rows": exact_text_variant_rows,
        "normalized_text_variant_rows": normalized_text_variant_rows,
        "unique_pair_keys": len(key_counter),
        "duplicate_pair_key_rows": sum(count - 1 for count in key_counter.values() if count > 1),
        "level_counts": {mechanism: dict(counter.most_common()) for mechanism, counter in level_counts.items()},
        "rationale_nonempty_counts": dict(rationale_nonempty),
        "rationale_span_counts": dict(rationale_span_counts),
        "risk_term_counts": dict(risk_counts.most_common()),
        "max_total_char_length": max_chars,
        "contexts_over_10k_chars": over_10k,
        "contexts_over_12k_chars": over_12k,
    }


def project_rubric_units(rows_by_mechanism: dict[str, list[dict[str, str]]], source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    rows_written = 0
    for mechanism, filename in MECHANISM_FILES.items():
        rows = rows_by_mechanism[mechanism]
        by_level: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            by_level[str(row.get("level", ""))].append(row)
        for level in ["0", "1", "2"]:
            level_rows = by_level.get(level, [])
            spans = []
            examples = []
            for row_index, row in enumerate(rows):
                if str(row.get("level", "")) != level:
                    continue
                row_spans = split_rationales(row.get("rationales", ""))
                if row_spans and len(spans) < 20:
                    spans.extend(row_spans[: max(0, 20 - len(spans))])
                if len(examples) < 8:
                    examples.append(
                        {
                            "source_ref": f"dataset/{filename}:row/{row_index}",
                            "sp_id": row.get("sp_id", ""),
                            "rp_id": row.get("rp_id", ""),
                            "seeker_post_excerpt": row.get("seeker_post", "")[:260],
                            "response_post_excerpt": row.get("response_post", "")[:360],
                            "rationale_spans": row_spans[:5],
                        }
                    )
            source_id = f"rubric:{mechanism}:level:{level}"
            content = {
                "empathy_dimension": mechanism,
                "dimension_definition": MECHANISM_DEFINITIONS[mechanism],
                "source_level": level,
                "level_definition": LEVEL_DEFINITIONS.get(level, "unknown_source_level"),
                "source_row_count": len(level_rows),
                "source_rationale_nonempty_count": sum(1 for row in level_rows if split_rationales(row.get("rationales", ""))),
                "positive_markers_from_rationales": spans[:20],
                "example_response_refs": examples,
                "limitations": [
                    "This is a source-label aggregation, not a newly inferred clinical rubric.",
                    "Level semantics come from the published empathy framework and source annotations.",
                    "Examples are retained as evidence pointers; they are not deduplicated or quality-filtered.",
                ],
            }
            writer.write(
                wrap_unit(
                    "empathy_quality_rubric",
                    source_base(source_id, [f"dataset/{filename}"], "expert_annotated_empathy_label_aggregation"),
                    content,
                    {
                        "projection": "deterministic_empathy_rubric_aggregation",
                        "source_root": str(source_root),
                        "mechanism": mechanism,
                        "source_level": level,
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
    rows_by_mechanism = load_mechanism_rows(source_root)
    response_writer = JsonlWriter(OUTPUTS["empathy_response_unit"], overwrite=args.overwrite)
    rubric_writer = JsonlWriter(OUTPUTS["empathy_quality_rubric"], overwrite=args.overwrite)
    response_stats = project_response_units(rows_by_mechanism, source_root, response_writer)
    rubric_stats = project_rubric_units(rows_by_mechanism, source_root, rubric_writer)
    unit_files = {
        "empathy_response_unit": response_writer.close(),
        "empathy_quality_rubric": rubric_writer.close(),
    }

    source_files = {
        "README.md": str(source_root / "README.md"),
        "dataset_contents.json": str(source_root / "dataset_contents.json"),
        **{filename: str(source_root / "dataset" / filename) for filename in MECHANISM_FILES.values()},
    }
    manifest = {
        "schema_version": "theraskill_empathy_mh_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete",
        "source_root": str(source_root),
        "source_files": {
            name: {"path": path, "sha256": file_sha256(path)}
            for name, path in source_files.items()
        },
        "projection_policy": "deterministic raw empathy annotation projection; no LLM calls, no sampling, no filtering, no cross-dataset deduplication",
        "source_audit": response_stats,
        "rubric_projection": rubric_stats,
        "unit_files": unit_files,
        "qa_report": args.qa_report,
        "issues_count": 0,
    }
    write_json(args.manifest, manifest)

    qa = {
        "schema_version": "theraskill_empathy_mh_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete",
        "unit_files": unit_files,
        "source_audit": response_stats,
        "issues": [],
    }
    if not response_stats["mechanism_files_same_key_order"]:
        qa["status"] = "incomplete"
        qa["issues"].append({"issue": "mechanism_files_not_same_key_order"})
    if unit_files["empathy_response_unit"]["rows"] != 3084:
        qa["status"] = "incomplete"
        qa["issues"].append({"issue": "unexpected_response_rows", "actual": unit_files["empathy_response_unit"]["rows"]})
    if unit_files["empathy_quality_rubric"]["rows"] != 9:
        qa["status"] = "incomplete"
        qa["issues"].append({"issue": "unexpected_rubric_rows", "actual": unit_files["empathy_quality_rubric"]["rows"]})
    qa["issues_count"] = len(qa["issues"])
    write_json(args.qa_report, qa)
    if qa["issues_count"]:
        manifest["status"] = "incomplete"
        manifest["issues_count"] = qa["issues_count"]
        write_json(args.manifest, manifest)
    print(
        json.dumps(
            {
                "status": qa["status"],
                "issues_count": qa["issues_count"],
                "unit_files": unit_files,
                "qa_report": args.qa_report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if qa["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
