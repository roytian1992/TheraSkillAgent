#!/usr/bin/env python3
"""Project AnnoMI expert annotations into knowledge-unit envelopes.

This is a deterministic projection. AnnoMI already provides expert labels for
MI quality, therapist behavior, subtypes, and client talk type, so this script
does not call an LLM.
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


DATASET = "AnnoMI"
SOURCE_ROOT = "datasets/AnnoMI"
LICENSE = "not_specified_in_local_readme"
OUTPUTS = {
    "mi_method_step": "outputs/knowledge/units/AnnoMI.mi_method_step.jsonl",
    "mi_quality_rubric": "outputs/knowledge/units/AnnoMI.mi_quality_rubric.jsonl",
    "change_talk_pattern": "outputs/knowledge/units/AnnoMI.change_talk_pattern.jsonl",
}
MANIFEST = "outputs/knowledge/manifests/AnnoMI.projection_manifest.json"
QA = "outputs/knowledge/manifests/AnnoMI.projection_qa.json"

METHOD_DEFINITIONS = {
    "question": "Therapist asks a question to invite information, reflection, or commitment from the client.",
    "reflection": "Therapist reflects or reformulates client meaning, feeling, ambivalence, or motivation.",
    "therapist_input": "Therapist provides information, advice, options, or negotiated input.",
    "other": "Therapist utterance annotated as outside the main question/reflection/input categories.",
}

SUBTYPE_DEFINITIONS = {
    "question:open": "Open question that invites elaboration rather than a yes/no answer.",
    "question:closed": "Closed question that can usually be answered briefly or with yes/no.",
    "reflection:simple": "Simple reflection that closely repeats or paraphrases client content.",
    "reflection:complex": "Complex reflection that adds inferred meaning, feeling, emphasis, or deeper formulation.",
    "therapist_input:information": "Therapist provides factual information or feedback.",
    "therapist_input:advice": "Therapist offers advice or a recommendation.",
    "therapist_input:negotiation": "Therapist negotiates permission, agenda, or next steps.",
    "therapist_input:options": "Therapist presents options or alternatives.",
}

TALK_DEFINITIONS = {
    "change": "Client utterance expresses desire, ability, reasons, need, commitment, activation, or steps toward change.",
    "sustain": "Client utterance expresses reasons, desire, barriers, commitment, or preference for maintaining the status quo.",
    "neutral": "Client utterance does not clearly indicate change talk or sustain talk in the source annotation.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=SOURCE_ROOT)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--qa-report", default=QA)
    return parser.parse_args()


def unit_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def unit_id(knowledge_type: str, source_id: str) -> str:
    return f"ku_annomi_{knowledge_type}_{unit_hash(source_id)}"


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


def write_jsonl(path: str | Path, rows: list[dict[str, Any]], *, overwrite: bool) -> str:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"output exists: {path}; pass --overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            digest.update(line.encode("utf-8"))
            handle.write(line)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def row_source_id(row: dict[str, str]) -> str:
    return f"transcript:{row['transcript_id']}/utterance:{row['utterance_id']}"


def label_counts(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(Counter(row.get(field, "") for row in rows).most_common())


def majority_label(rows: list[dict[str, str]], field: str) -> tuple[str, dict[str, int], bool]:
    counts = Counter(row.get(field, "") for row in rows)
    most = counts.most_common()
    if not most:
        return "", {}, False
    top_count = most[0][1]
    tied = sorted(label for label, count in most if count == top_count)
    return tied[0], dict(most), len(tied) > 1


def build_full_annotation_audit(full_rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in full_rows:
        grouped[(row["transcript_id"], row["utterance_id"])].append(row)

    audit: dict[tuple[str, str], dict[str, Any]] = {}
    audited_fields = [
        "main_therapist_behaviour",
        "client_talk_type",
        "therapist_input_exists",
        "therapist_input_subtype",
        "reflection_exists",
        "reflection_subtype",
        "question_exists",
        "question_subtype",
    ]
    for key, rows in grouped.items():
        field_audit = {}
        for field in audited_fields:
            maj, counts, tied = majority_label(rows, field)
            field_audit[field] = {
                "majority_label": maj,
                "label_counts": counts,
                "is_tied": tied,
                "agreement_rate": round((counts.get(maj, 0) / len(rows)) if rows else 0.0, 4),
            }
        audit[key] = {
            "annotator_count": len({row["annotator_id"] for row in rows if row.get("annotator_id")}),
            "row_count": len(rows),
            "fields": field_audit,
        }
    return audit


def canonical_rows(simple_rows: list[dict[str, str]], full_audit: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in simple_rows:
        key = (row["transcript_id"], row["utterance_id"])
        out = dict(row)
        out["_source_id"] = row_source_id(row)
        out["_full_annotation_audit"] = full_audit.get(key)
        rows.append(out)
    return rows


def source_base(source_id: str, source_files: list[str], **extra: Any) -> dict[str, Any]:
    source = {
        "dataset": DATASET,
        "source_id": source_id,
        "language": "en",
        "source_construction_type": "expert_annotated_motivational_interviewing_dialogue",
        "evidence_tier": "expert_annotated_method_dataset",
        "license": LICENSE,
        "source_files": source_files,
        "citation": [
            "Wu et al. Anno-MI: A Dataset of Expert-Annotated Counselling Dialogues. ICASSP 2022.",
            "Wu et al. Creation, Analysis and Evaluation of AnnoMI. Future Internet 2023.",
        ],
    }
    source.update(extra)
    return source


def wrap_unit(knowledge_type: str, source: dict[str, Any], content: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "theraskill_knowledge_unit_v1",
        "unit_id": unit_id(knowledge_type, source["source_id"]),
        "knowledge_type": knowledge_type,
        "source": source,
        "content": {knowledge_type: content},
        "quality": {
            "quality_status": content.get("quality_status"),
            "review_status": "deterministic_projection",
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "source_label_preserved",
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


def build_embedding_text(knowledge_type: str, content: dict[str, Any]) -> str:
    if knowledge_type == "mi_method_step":
        parts = [
            content.get("mi_behavior_label", ""),
            content.get("mi_behavior_subtype", ""),
            content.get("definition", ""),
            json.dumps(content.get("source_examples", []), ensure_ascii=False),
        ]
    elif knowledge_type == "mi_quality_rubric":
        parts = [
            content.get("behavior_label", ""),
            content.get("quality_level", ""),
            json.dumps(content.get("observed_patterns", []), ensure_ascii=False),
            json.dumps(content.get("source_examples", []), ensure_ascii=False),
        ]
    else:
        parts = [
            content.get("talk_type", ""),
            content.get("definition", ""),
            json.dumps(content.get("recognition_cues_from_examples", []), ensure_ascii=False),
            json.dumps(content.get("source_examples", []), ensure_ascii=False),
        ]
    return "\n".join(str(part) for part in parts if part).strip()


def utterance_context(rows_by_transcript: dict[str, list[dict[str, Any]]], row: dict[str, Any], window: int = 2) -> list[dict[str, str]]:
    rows = rows_by_transcript[row["transcript_id"]]
    idx = next(i for i, candidate in enumerate(rows) if candidate["utterance_id"] == row["utterance_id"])
    context = []
    for item in rows[max(0, idx - window) : min(len(rows), idx + window + 1)]:
        context.append(
            {
                "utterance_id": item["utterance_id"],
                "speaker": item["interlocutor"],
                "text": item["utterance_text"],
                "main_therapist_behaviour": item["main_therapist_behaviour"],
                "client_talk_type": item["client_talk_type"],
            }
        )
    return context


def example_payload(rows_by_transcript: dict[str, list[dict[str, Any]]], row: dict[str, Any]) -> dict[str, Any]:
    audit = row.get("_full_annotation_audit") or {}
    return {
        "source_id": row["_source_id"],
        "transcript_id": row["transcript_id"],
        "utterance_id": int(row["utterance_id"]),
        "mi_quality": row["mi_quality"],
        "topic": row["topic"],
        "speaker_role": row["interlocutor"],
        "timestamp": row["timestamp"],
        "utterance_text": row["utterance_text"],
        "context_window": utterance_context(rows_by_transcript, row),
        "full_annotation_audit": audit if audit and audit.get("row_count", 0) > 1 else None,
    }


def select_examples(rows: list[dict[str, Any]], rows_by_transcript: dict[str, list[dict[str, Any]]], limit: int = 12) -> list[dict[str, Any]]:
    selected = []
    seen_transcripts = set()
    sorted_rows = sorted(rows, key=lambda item: (item["mi_quality"] != "high", len(item["utterance_text"]), int(item["transcript_id"]), int(item["utterance_id"])))
    for row in sorted_rows:
        if row["transcript_id"] in seen_transcripts and len(seen_transcripts) < min(limit, len({r["transcript_id"] for r in rows})):
            continue
        selected.append(example_payload(rows_by_transcript, row))
        seen_transcripts.add(row["transcript_id"])
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        selected_ids = {item["source_id"] for item in selected}
        for row in sorted_rows:
            if row["_source_id"] not in selected_ids:
                selected.append(example_payload(rows_by_transcript, row))
                selected_ids.add(row["_source_id"])
                if len(selected) >= limit:
                    break
    return selected


def subtype_for_row(row: dict[str, Any]) -> str | None:
    label = row["main_therapist_behaviour"]
    if label == "question" and row.get("_full_annotation_audit"):
        value = row["_full_annotation_audit"]["fields"]["question_subtype"]["majority_label"]
        return value if value and value != "n/a" else None
    if label == "reflection" and row.get("_full_annotation_audit"):
        value = row["_full_annotation_audit"]["fields"]["reflection_subtype"]["majority_label"]
        return value if value and value != "n/a" else None
    if label == "therapist_input" and row.get("_full_annotation_audit"):
        value = row["_full_annotation_audit"]["fields"]["therapist_input_subtype"]["majority_label"]
        return value if value and value != "n/a" else None
    return None


def build_method_units(rows: list[dict[str, Any]], rows_by_transcript: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    therapist_rows = [row for row in rows if row["interlocutor"] == "therapist" and row["main_therapist_behaviour"] != "n/a"]
    by_label: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for row in therapist_rows:
        by_label[(row["main_therapist_behaviour"], subtype_for_row(row))].append(row)

    units = []
    for (label, subtype), group in sorted(by_label.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        source_id = f"method/{label}" + (f"/{subtype}" if subtype else "")
        definition = SUBTYPE_DEFINITIONS.get(f"{label}:{subtype}") if subtype else METHOD_DEFINITIONS.get(label, "Source-provided MI behavior label.")
        content = {
            "mi_behavior_label": label,
            "mi_behavior_subtype": subtype,
            "definition": definition,
            "speaker_role": "therapist",
            "source_example_count": len(group),
            "mi_quality_distribution": label_counts(group, "mi_quality"),
            "topic_distribution_top20": dict(Counter(row["topic"] for row in group).most_common(20)),
            "source_examples": select_examples(group, rows_by_transcript, limit=12),
            "quality_status": {
                "status": "usable",
                "limitations": [
                    "This unit aggregates source labels and examples; it is not a complete MI training manual.",
                    "Subtypes are available primarily through AnnoMI-full majority annotation for duplicated full-annotation transcripts.",
                ],
            },
        }
        source = source_base(
            source_id,
            ["AnnoMI-simple.csv", "AnnoMI-full.csv"],
            mi_behavior_label=label,
            mi_behavior_subtype=subtype,
            source_example_count=len(group),
        )
        provenance = {
            "projection": "deterministic_label_aggregation",
            "source_root": SOURCE_ROOT,
            "canonical_source": "AnnoMI-simple.csv",
            "supplemental_annotation_source": "AnnoMI-full.csv",
            "subtype_policy": "majority_label_from_full_when_available",
        }
        units.append(wrap_unit("mi_method_step", source, content, provenance))
    return units


def build_quality_units(rows: list[dict[str, Any]], rows_by_transcript: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    therapist_rows = [row for row in rows if row["interlocutor"] == "therapist" and row["main_therapist_behaviour"] != "n/a"]
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in therapist_rows:
        by_key[(row["main_therapist_behaviour"], row["mi_quality"])].append(row)

    units = []
    for (label, quality), group in sorted(by_key.items()):
        source_id = f"quality/{label}/{quality}"
        content = {
            "behavior_label": label,
            "quality_level": quality,
            "positive_criteria": [
                f"Source transcript is annotated as {quality} overall MI quality.",
                f"Therapist utterance is annotated as {label}.",
            ],
            "failure_patterns": [] if quality == "high" else [
                "Source label indicates low overall MI quality; inspect examples before reusing as a preferred response pattern.",
            ],
            "better_alternative": None,
            "observed_patterns": [
                {"label": "topic_distribution_top20", "value": dict(Counter(row["topic"] for row in group).most_common(20))},
                {"label": "subtype_distribution", "value": dict(Counter(subtype_for_row(row) or "unspecified" for row in group).most_common())},
            ],
            "source_example_count": len(group),
            "source_examples": select_examples(group, rows_by_transcript, limit=10),
            "quality_status": {
                "status": "usable" if group else "empty",
                "limitations": [
                    "AnnoMI mi_quality is transcript-level high/low quality, not a direct per-utterance goodness score.",
                    "better_alternative is not generated deterministically; use high-quality counterpart units as comparison evidence.",
                ],
            },
        }
        source = source_base(
            source_id,
            ["AnnoMI-simple.csv", "AnnoMI-full.csv"],
            mi_behavior_label=label,
            mi_quality=quality,
            source_example_count=len(group),
        )
        provenance = {
            "projection": "deterministic_quality_label_aggregation",
            "source_root": SOURCE_ROOT,
            "quality_label_scope": "transcript_level",
            "no_llm_generated_rubric": True,
        }
        units.append(wrap_unit("mi_quality_rubric", source, content, provenance))
    return units


def build_change_talk_units(rows: list[dict[str, Any]], rows_by_transcript: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    client_rows = [row for row in rows if row["interlocutor"] == "client" and row["client_talk_type"] != "n/a"]
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in client_rows:
        by_label[row["client_talk_type"]].append(row)

    response_map = {
        "change": ["reflection", "open_question", "affirmation", "summary"],
        "sustain": ["reflection", "open_question", "empathic_validation"],
        "neutral": ["open_question", "reflection", "agenda_navigation"],
    }
    units = []
    for label, group in sorted(by_label.items()):
        source_id = f"client_talk/{label}"
        examples = select_examples(group, rows_by_transcript, limit=14)
        content = {
            "talk_type": label,
            "definition": TALK_DEFINITIONS.get(label, "Source-provided client talk type label."),
            "recognition_cues_from_examples": [
                {
                    "source_id": example["source_id"],
                    "utterance_text": example["utterance_text"],
                    "topic": example["topic"],
                }
                for example in examples[:8]
            ],
            "example_client_utterances": examples,
            "recommended_mi_response_type": response_map.get(label, ["reflection", "open_question"]),
            "source_example_count": len(group),
            "mi_quality_distribution": label_counts(group, "mi_quality"),
            "topic_distribution_top20": dict(Counter(row["topic"] for row in group).most_common(20)),
            "quality_status": {
                "status": "usable",
                "limitations": [
                    "Recommended response types are conservative MI-oriented mappings, not directly annotated target replies.",
                    "Talk type labels come from source annotations and may be context-sensitive.",
                ],
            },
        }
        source = source_base(
            source_id,
            ["AnnoMI-simple.csv", "AnnoMI-full.csv"],
            client_talk_type=label,
            source_example_count=len(group),
        )
        provenance = {
            "projection": "deterministic_client_talk_label_aggregation",
            "source_root": SOURCE_ROOT,
            "canonical_source": "AnnoMI-simple.csv",
            "response_type_mapping_policy": "conservative_static_mi_mapping",
        }
        units.append(wrap_unit("change_talk_pattern", source, content, provenance))
    return units


def build_source_audit(simple_rows: list[dict[str, str]], full_rows: list[dict[str, str]], full_audit: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    repeated = [audit for audit in full_audit.values() if audit["row_count"] > 1]
    conflict_counts = Counter()
    tied_counts = Counter()
    for audit in repeated:
        for field, payload in audit["fields"].items():
            if len(payload["label_counts"]) > 1:
                conflict_counts[field] += 1
            if payload["is_tied"]:
                tied_counts[field] += 1
    return {
        "simple_rows": len(simple_rows),
        "full_rows": len(full_rows),
        "unique_utterances": len({(row["transcript_id"], row["utterance_id"]) for row in simple_rows}),
        "transcripts": len({row["transcript_id"] for row in simple_rows}),
        "full_repeated_utterance_keys": len(repeated),
        "full_max_rows_per_utterance": max((audit["row_count"] for audit in full_audit.values()), default=0),
        "multi_annotator_conflict_counts": dict(conflict_counts.most_common()),
        "multi_annotator_tie_counts": dict(tied_counts.most_common()),
        "mi_quality_distribution": label_counts(simple_rows, "mi_quality"),
        "interlocutor_distribution": label_counts(simple_rows, "interlocutor"),
        "main_therapist_behaviour_distribution": label_counts(simple_rows, "main_therapist_behaviour"),
        "client_talk_type_distribution": label_counts(simple_rows, "client_talk_type"),
        "topic_distribution_top30": dict(Counter(row["topic"] for row in simple_rows).most_common(30)),
    }


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root)
    simple_path = source_root / "AnnoMI-simple.csv"
    full_path = source_root / "AnnoMI-full.csv"
    simple_rows = read_csv(simple_path)
    full_rows = read_csv(full_path)
    full_audit = build_full_annotation_audit(full_rows)
    rows = canonical_rows(simple_rows, full_audit)

    rows_by_transcript: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_transcript[row["transcript_id"]].append(row)
    for transcript_rows in rows_by_transcript.values():
        transcript_rows.sort(key=lambda item: int(item["utterance_id"]))

    unit_sets = {
        "mi_method_step": build_method_units(rows, rows_by_transcript),
        "mi_quality_rubric": build_quality_units(rows, rows_by_transcript),
        "change_talk_pattern": build_change_talk_units(rows, rows_by_transcript),
    }
    output_reports = {}
    for knowledge_type, output_path in OUTPUTS.items():
        output_reports[knowledge_type] = {
            "path": output_path,
            "rows": len(unit_sets[knowledge_type]),
            "sha256": write_jsonl(output_path, unit_sets[knowledge_type], overwrite=args.overwrite),
        }

    source_audit = build_source_audit(simple_rows, full_rows, full_audit)
    qa = {
        "schema_version": "theraskill_annomi_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete",
        "source_audit": source_audit,
        "output_reports": output_reports,
        "issues": [],
    }
    write_json(args.qa_report, qa)
    manifest = {
        "schema_version": "theraskill_annomi_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete",
        "source_root": str(source_root),
        "source_files": {
            "AnnoMI-simple.csv": {"path": str(simple_path), "rows": len(simple_rows), "sha256": file_sha256(simple_path)},
            "AnnoMI-full.csv": {"path": str(full_path), "rows": len(full_rows), "sha256": file_sha256(full_path)},
            "README.md": {"path": str(source_root / "README.md"), "sha256": file_sha256(source_root / "README.md")},
        },
        "projection_policy": "deterministic aggregation from source expert annotations; no LLM calls",
        "license_note": "Local README does not specify a license; keep citation/provenance and treat reuse policy as requiring review.",
        "unit_files": output_reports,
        "qa_report": args.qa_report,
    }
    write_json(args.manifest, manifest)
    print(json.dumps({"status": "complete", "outputs": output_reports, "qa_report": args.qa_report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
