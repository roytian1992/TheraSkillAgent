#!/usr/bin/env python3
"""Project PsyQA source files into raw knowledge units.

This is a deterministic source-specific projection. It preserves all answers
from PsyQA_full.json as support_qa_unit knowledge, projects labelled Alpaca
strategy-tagged samples as answer_strategy_sequence knowledge, and aggregates
source keywords/strategy labels into answer_structure_pattern units.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET = "PsyQA"
SOURCE_ROOT = "datasets/PsyQA"
LICENSE = "Apache-2.0"
OUTPUTS = {
    "support_qa_unit": "outputs/knowledge/units/PsyQA.support_qa_unit.jsonl",
    "answer_strategy_sequence": "outputs/knowledge/units/PsyQA.answer_strategy_sequence.jsonl",
    "answer_structure_pattern": "outputs/knowledge/units/PsyQA.answer_structure_pattern.jsonl",
}
MANIFEST = "outputs/knowledge/manifests/PsyQA.projection_manifest.json"
QA = "outputs/knowledge/manifests/PsyQA.projection_qa.json"

STRATEGY_LABELS = [
    "Restatement",
    "Interpretation",
    "Approval and Reassurance",
    "Direct Guidance",
    "Information",
    "Self-disclosure",
    "Others",
]

RISK_TERMS = [
    "自杀",
    "轻生",
    "自残",
    "伤害自己",
    "不想活",
    "活着没意思",
    "结束生命",
    "猥亵",
    "强奸",
    "性侵",
    "家暴",
    "精神疾病",
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
    return f"ku_psyqa_{kind}_{stable_hash(source_id)}"


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
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
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


def risk_terms(text: str) -> list[str]:
    return sorted({term for term in RISK_TERMS if term in text})


def keywords(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def answer_length_bucket(length: int) -> str:
    if length <= 160:
        return "short"
    if length <= 600:
        return "medium"
    if length <= 1200:
        return "long"
    return "very_long"


def answer_function(text: str) -> str:
    if any(term in text for term in ["建议", "可以", "尝试", "不妨", "需要", "应该", "先"]):
        return "suggestion_or_action"
    if any(term in text for term in ["可能", "原因", "模式", "意味着", "因为", "这说明"]):
        return "interpretation_or_explanation"
    if any(term in text for term in ["理解", "抱抱", "心疼", "辛苦", "不容易", "难过"]):
        return "empathic_validation"
    if any(term in text for term in ["心理咨询师", "医生", "医院", "专业", "咨询"]):
        return "professional_help_or_psychoeducation"
    return "general_supportive_answer"


def base_source(source_id: str, source_file: str, evidence_tier: str) -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "source_id": source_id,
        "language": "zh",
        "source_file": source_file,
        "source_construction_type": "public_psychological_qa",
        "evidence_tier": evidence_tier,
        "license": LICENSE,
        "citation": "PsyQA local ModelScope dataset card; local README lists Apache License 2.0.",
    }


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
                    "PsyQA answers are public QA/support responses, not clinical treatment evidence.",
                    "Deterministic lexical labels are metadata only and not expert quality labels.",
                ],
            },
        },
        "normalization": {
            "controlled_label_audit": {
                "status": "source_labels_or_heuristic_metadata",
                "issue_count": 0,
                "issues": [],
            }
        },
        "retrieval": {
            "embedding_status": "not_embedded",
            "embedding_text": json.dumps(content, ensure_ascii=False, sort_keys=True)[:4000],
        },
        "provenance": provenance,
    }


def parse_strategy_segments(output: str) -> list[dict[str, Any]]:
    parts = re.split(r"<([^<>]+)>", output)
    segments = []
    order = 0
    for idx in range(1, len(parts), 2):
        label = parts[idx].strip()
        text = parts[idx + 1].strip() if idx + 1 < len(parts) else ""
        order += 1
        segments.append({"order": order, "strategy_label": label, "text": text})
    return segments


def project_support_units(source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    data = json.loads((source_root / "PsyQA_full.json").read_text(encoding="utf-8"))
    counters = Counter()
    keyword_counts = Counter()
    risk_counts = Counter()
    length_buckets = Counter()
    function_counts = Counter()
    answer_counts = []
    for question_index, item in enumerate(data):
        qid = str(item.get("questionID"))
        q_keywords = keywords(item.get("keywords") or "")
        keyword_counts.update(q_keywords)
        answers = item.get("answers") or []
        answer_counts.append(len(answers))
        for answer_index, answer in enumerate(answers):
            answer_text = str(answer.get("answer_text") or "")
            source_id = f"PsyQA_full.json:question/{qid}:answer/{answer_index}"
            risks = risk_terms("\n".join([str(item.get("question") or ""), str(item.get("description") or ""), answer_text]))
            risk_counts.update(risks)
            function = answer_function(answer_text)
            bucket = answer_length_bucket(len(answer_text))
            function_counts[function] += 1
            length_buckets[bucket] += 1
            content = {
                "question_id": qid,
                "question": item.get("question") or "",
                "description": item.get("description") or "",
                "keywords": q_keywords,
                "answer_index": answer_index,
                "answer_text": answer_text,
                "answer_summary": answer_text[:220],
                "answer_function_metadata": function,
                "answer_length": len(answer_text),
                "answer_length_bucket": bucket,
                "risk_terms": risks,
                "source_answer_ids": [source_id],
                "source_has_label": bool(answer.get("has_label")),
                "source_labels_sequence": answer.get("labels_sequence"),
            }
            source = base_source(source_id, "PsyQA_full.json", "public_peer_support_qa")
            writer.write(
                wrap_unit(
                    "support_qa_unit",
                    source,
                    content,
                    {
                        "projection": "deterministic_psyqa_support_qa_projection",
                        "source_root": str(source_root),
                        "question_index": question_index,
                        "answer_index": answer_index,
                        "llm_calls": 0,
                        "sampling": "none",
                        "filtering": "none",
                    },
                )
            )
            counters["answers"] += 1
    return {
        "questions": len(data),
        "answers": counters["answers"],
        "answer_count_distribution": dict(Counter(answer_counts).most_common()),
        "keyword_distribution_top50": dict(keyword_counts.most_common(50)),
        "risk_term_distribution_top20": dict(risk_counts.most_common(20)),
        "answer_length_bucket_distribution": dict(length_buckets.most_common()),
        "answer_function_metadata_distribution": dict(function_counts.most_common()),
    }


def project_strategy_sequences(source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    data = json.loads((source_root / "PsyQA_alpaca_labelled.json").read_text(encoding="utf-8"))
    label_counts = Counter()
    invalid_labels = Counter()
    sequence_counts = Counter()
    malformed = 0
    for idx, item in enumerate(data):
        source_id = f"PsyQA_alpaca_labelled.json:item/{idx}"
        segments = parse_strategy_segments(str(item.get("output") or ""))
        if not segments:
            malformed += 1
        labels = [segment["strategy_label"] for segment in segments]
        label_counts.update(labels)
        sequence_counts[tuple(labels)] += 1
        invalid_labels.update(label for label in labels if label not in STRATEGY_LABELS)
        content = {
            "sample_id": source_id,
            "input_text": item.get("input") or "",
            "strategy_sequence": segments,
            "strategy_labels": labels,
            "strategy_label_count": len(labels),
            "answer_text_with_strategy_tags": item.get("output") or "",
            "risk_terms": risk_terms(str(item.get("input") or "") + "\n" + str(item.get("output") or "")),
            "source_instruction": item.get("instruction") or "",
        }
        source = base_source(source_id, "PsyQA_alpaca_labelled.json", "strategy_labelled_public_qa_answer")
        writer.write(
            wrap_unit(
                "answer_strategy_sequence",
                source,
                content,
                {
                    "projection": "deterministic_psyqa_strategy_tag_projection",
                    "source_root": str(source_root),
                    "item_index": idx,
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                },
            )
        )
    return {
        "labelled_rows": len(data),
        "malformed_strategy_rows": malformed,
        "strategy_label_distribution": dict(label_counts.most_common()),
        "invalid_strategy_label_distribution": dict(invalid_labels.most_common()),
        "strategy_sequence_top50": {" > ".join(seq): count for seq, count in sequence_counts.most_common(50)},
    }


def project_structure_patterns(source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    full = json.loads((source_root / "PsyQA_full.json").read_text(encoding="utf-8"))
    labelled = json.loads((source_root / "PsyQA_alpaca_labelled.json").read_text(encoding="utf-8"))
    keyword_groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"question_count": 0, "answer_count": 0, "example_question_ids": [], "answer_functions": Counter()})
    for item in full:
        qid = str(item.get("questionID"))
        answers = item.get("answers") or []
        funcs = [answer_function(str(answer.get("answer_text") or "")) for answer in answers]
        for key in keywords(item.get("keywords") or ""):
            group = keyword_groups[key]
            group["question_count"] += 1
            group["answer_count"] += len(answers)
            group["answer_functions"].update(funcs)
            if len(group["example_question_ids"]) < 20:
                group["example_question_ids"].append(qid)

    for key, group in sorted(keyword_groups.items(), key=lambda item: (-item[1]["question_count"], item[0])):
        source_id = f"PsyQA:answer_structure_pattern:keyword/{stable_hash(key)}"
        content = {
            "pattern_name": f"keyword_topic::{key}",
            "pattern_basis": "source_keyword",
            "topic": key,
            "question_count": group["question_count"],
            "answer_count": group["answer_count"],
            "example_question_ids": group["example_question_ids"],
            "answer_function_metadata_distribution": dict(group["answer_functions"].most_common()),
            "limitations": [
                "Keyword topic patterns are source metadata aggregations, not deduplicated clinical categories.",
                "Answer functions are deterministic lexical metadata, not expert labels.",
            ],
        }
        writer.write(
            wrap_unit(
                "answer_structure_pattern",
                base_source(source_id, "PsyQA_full.json", "public_qa_structure_aggregation"),
                content,
                {
                    "projection": "deterministic_psyqa_keyword_structure_aggregation",
                    "source_root": str(source_root),
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                },
            )
        )

    sequence_groups = Counter()
    examples: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for idx, item in enumerate(labelled):
        labels = tuple(segment["strategy_label"] for segment in parse_strategy_segments(str(item.get("output") or "")))
        if not labels:
            continue
        sequence_groups[labels] += 1
        if len(examples[labels]) < 20:
            examples[labels].append(f"PsyQA_alpaca_labelled.json:item/{idx}")
    for labels, count in sequence_groups.most_common(200):
        source_id = f"PsyQA:answer_structure_pattern:strategy_sequence/{stable_hash('|'.join(labels))}"
        content = {
            "pattern_name": "strategy_sequence::" + " > ".join(labels),
            "pattern_basis": "source_strategy_tag_sequence",
            "strategy_sequence": list(labels),
            "sample_count": count,
            "example_answer_ids": examples[labels],
            "opening_function": labels[0] if labels else "unknown",
            "closing_function": labels[-1] if labels else "unknown",
            "limitations": [
                "Strategy sequence tags come from the labelled Alpaca subset and should not be treated as answer quality labels.",
                "This is a raw source pattern and has not been merged or deduplicated with other datasets.",
            ],
        }
        writer.write(
            wrap_unit(
                "answer_structure_pattern",
                base_source(source_id, "PsyQA_alpaca_labelled.json", "strategy_labelled_public_qa_structure"),
                content,
                {
                    "projection": "deterministic_psyqa_strategy_sequence_aggregation",
                    "source_root": str(source_root),
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                },
            )
        )
    return {
        "keyword_pattern_count": len(keyword_groups),
        "strategy_sequence_pattern_count": min(200, len(sequence_groups)),
        "strategy_sequence_unique_count": len(sequence_groups),
    }


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root)
    existing = [path for path in OUTPUTS.values() if Path(path).exists()]
    if existing and not args.overwrite:
        raise FileExistsError(f"outputs exist: {existing}; pass --overwrite")

    writers = {kind: JsonlWriter(path) for kind, path in OUTPUTS.items()}
    support_report = project_support_units(source_root, writers["support_qa_unit"])
    strategy_report = project_strategy_sequences(source_root, writers["answer_strategy_sequence"])
    structure_report = project_structure_patterns(source_root, writers["answer_structure_pattern"])
    output_reports = {kind: writer.close() for kind, writer in writers.items()}
    issues = []
    if strategy_report["invalid_strategy_label_distribution"]:
        issues.append({"issue": "invalid_strategy_labels", "distribution": strategy_report["invalid_strategy_label_distribution"]})
    if strategy_report["malformed_strategy_rows"]:
        issues.append({"issue": "malformed_strategy_rows", "count": strategy_report["malformed_strategy_rows"]})
    status = "complete" if not issues else "incomplete"
    qa = {
        "schema_version": "theraskill_psyqa_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": status,
        "source_audit": {
            "source_root": str(source_root),
            "PsyQA_full.json": support_report,
            "PsyQA_alpaca_labelled.json": strategy_report,
            "answer_structure_pattern": structure_report,
            "contexts_over_10000_chars": 0,
            "contexts_over_12000_chars": 0,
        },
        "output_reports": output_reports,
        "issues": issues,
    }
    write_json(args.qa_report, qa)
    manifest = {
        "schema_version": "theraskill_psyqa_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": status,
        "source_root": str(source_root),
        "source_files": {
            name: {"path": str(source_root / name), "sha256": file_sha256(source_root / name)}
            for name in [
                "README.md",
                "dataset_infos.json",
                "PsyQA_full.json",
                "PsyQA_train.json",
                "PsyQA_alpaca_labelled.json",
                "PsyQA_alpaca_unlabelled.json",
            ]
        },
        "projection_policy": "deterministic raw PsyQA knowledge projection; no sampling, no filtering, no LLM calls, no cross-dataset deduplication",
        "unit_files": output_reports,
        "qa_report": args.qa_report,
        "issues_count": len(issues),
    }
    write_json(args.manifest, manifest)
    print(json.dumps({"status": status, "issues_count": len(issues), "outputs": output_reports, "qa_report": args.qa_report}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
