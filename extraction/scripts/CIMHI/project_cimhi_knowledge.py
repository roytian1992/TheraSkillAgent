#!/usr/bin/env python3
"""Project C-IMHI source files into raw knowledge units.

This deterministic projection preserves C-IMHI instruction samples, expert
examples, task definitions, quality-check rubric, and evaluation test cases.
Generated model predictions are recorded only in the manifest audit and are not
promoted into knowledge units.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET = "C-IMHI"
SOURCE_ROOT = "datasets/C-IMHI"
LICENSE = "not_specified_in_local_readme"
OUTPUTS = {
    "assessment_task": "outputs/knowledge/units/C-IMHI.assessment_task.jsonl",
    "cognitive_distortion_signal": "outputs/knowledge/units/C-IMHI.cognitive_distortion_signal.jsonl",
    "risk_or_symptom_signal": "outputs/knowledge/units/C-IMHI.risk_or_symptom_signal.jsonl",
    "cognitive_path_example": "outputs/knowledge/units/C-IMHI.cognitive_path_example.jsonl",
    "quality_check_rubric": "outputs/knowledge/units/C-IMHI.quality_check_rubric.jsonl",
    "assessment_eval_case": "outputs/knowledge/units/C-IMHI.assessment_eval_case.jsonl",
}
MANIFEST = "outputs/knowledge/manifests/C-IMHI.projection_manifest.json"
QA = "outputs/knowledge/manifests/C-IMHI.projection_qa.json"

POST_RE = re.compile(r"帖子：“(.*?)”问题：", re.S)
CD_LABEL_RE = re.compile(r"体现了(.+?)的认知歪曲")
SUICIDE_LABEL_RE = re.compile(r"体现了(高自杀风险|低自杀风险)")

TASKS = [
    {
        "task_name": "cognitive_distortion_detection",
        "knowledge_type": "cognitive_distortion_signal",
        "source_file": "Instruct data/Instruct_CD.json",
        "input_type": "Chinese social media post",
        "label_schema": "one or more cognitive distortion labels in Chinese",
        "target_construct": "cognitive distortion classification with explanation",
        "source_construction_type": "cimhi_instruction_sample_with_generated_or_expert_seeded_explanation",
        "evidence_tier": "human_labelled_social_media_instruction_with_generated_explanation",
    },
    {
        "task_name": "suicide_risk_detection",
        "knowledge_type": "risk_or_symptom_signal",
        "source_file": "Instruct data/Instruct_suiside.json",
        "input_type": "Chinese social media post",
        "label_schema": "高自杀风险 / 低自杀风险",
        "target_construct": "binary suicide-risk classification with explanation",
        "source_construction_type": "cimhi_instruction_sample_with_generated_or_expert_seeded_explanation",
        "evidence_tier": "human_labelled_social_media_instruction_with_generated_explanation",
    },
    {
        "task_name": "cognitive_path_extraction",
        "knowledge_type": "cognitive_path_example",
        "source_file": "Expert_example/CP_example.xlsx",
        "input_type": "Chinese social media post",
        "label_schema": "cognitive path category or reasoning step in expert explanation",
        "target_construct": "cognitive pathway extraction",
        "source_construction_type": "cimhi_expert_written_example",
        "evidence_tier": "expert_written_interpretable_example",
    },
]

QUALITY_RUBRIC = [
    {
        "dimension": "consistency",
        "definition": "Whether the explanation supports the classification result and remains readable and coherent.",
        "levels": {
            "0": "Inconsistent with the classification result.",
            "1": "Consistent with the classification result but poorly readable and contains many errors.",
            "2": "Consistent with the classification result; mostly coherent and easy to read with minor errors.",
            "3": "Fully fluent, coherent, error-free, and consistent with the classification result.",
        },
    },
    {
        "dimension": "reliability",
        "definition": "Whether the explanation is factually accurate, free of misinformation, and based on sound reasoning.",
        "levels": {
            "0": "Completely unreliable information with factual hallucinations.",
            "1": "Partially reliable information with flawed fact-based reasoning.",
            "2": "Mostly reliable with non-critical misinformation or reasoning errors.",
            "3": "Completely reliable information.",
        },
    },
    {
        "dimension": "professionality",
        "definition": "Whether the explanation is psychologically rational, CBT-aware, and professionally grounded.",
        "levels": {
            "0": "No psychological theory and missing or misleading analysis of emotions and behaviors.",
            "1": "Attempts theory but applies it inaccurately or simplistically.",
            "2": "Mostly aligned with psychological theory with room for improvement.",
            "3": "Deep, accurate, theory-aligned, and professionally rigorous.",
        },
    },
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
    return f"ku_cimhi_{kind}_{stable_hash(source_id)}"


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


def source_base(source_id: str, source_files: list[str], construction_type: str, evidence_tier: str) -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "source_id": source_id,
        "language": "zh",
        "source_files": source_files,
        "source_construction_type": construction_type,
        "evidence_tier": evidence_tier,
        "license": LICENSE,
        "citation": [
            "MentalGLM Series: Explainable Large Language Models for Mental Health Analysis on Chinese Social Media.",
        ],
    }


def build_embedding_text(kind: str, content: dict[str, Any]) -> str:
    parts = []
    for key in ["task_name", "post_text", "source_label", "label_definition", "explanation", "expert_explanation", "target_construct"]:
        value = content.get(key)
        if isinstance(value, str):
            parts.append(value)
    if not parts:
        parts.append(json.dumps(content, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)[:4000]


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
                    "Instruction explanations include GPT-4 supplementary explanations according to the README; treat them as generated rationale, not direct expert evidence.",
                    "Expert example files contain expert-written examples but are small representative subsets.",
                    "Generated prediction files are not promoted into knowledge units.",
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


def extract_post(instruction: str) -> str:
    match = POST_RE.search(instruction)
    return match.group(1).strip() if match else ""


def parse_cd_labels(output: str) -> list[str]:
    match = CD_LABEL_RE.search(output)
    if not match:
        return []
    return [part.strip() for part in re.split(r"[,，、]", match.group(1)) if part.strip()]


def parse_suicide_label(output: str) -> str:
    match = SUICIDE_LABEL_RE.search(output)
    return match.group(1).strip() if match else ""


def read_xlsx_rows(path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                texts = [t.text or "" for t in si.findall(".//a:t", ns)]
                shared_strings.append("".join(texts))
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet.findall(".//a:sheetData/a:row", ns):
            values = []
            for cell in row.findall("a:c", ns):
                value_node = cell.find("a:v", ns)
                value = "" if value_node is None else value_node.text or ""
                if cell.attrib.get("t") == "s" and value:
                    value = shared_strings[int(value)]
                values.append(value)
            if any(v.strip() for v in values):
                rows.append(values)
        return rows


def project_tasks(writer: JsonlWriter, source_root: Path) -> dict[str, Any]:
    for task in TASKS:
        source_id = f"task:{task['task_name']}"
        content = {
            "task_name": task["task_name"],
            "input_type": task["input_type"],
            "label_schema": task["label_schema"],
            "target_construct": task["target_construct"],
            "source_file": task["source_file"],
            "example_input": "",
            "example_label": "",
            "limitations": [
                "Task definition is projected from README and local source file structure.",
                "Future skill or pack assignment is deferred.",
            ],
        }
        writer.write(
            wrap_unit(
                "assessment_task",
                source_base(source_id, ["README.md", task["source_file"]], task["source_construction_type"], "dataset_task_definition"),
                content,
                {
                    "projection": "deterministic_cimhi_task_projection",
                    "source_root": str(source_root),
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                    "deduplication": "none",
                },
            )
        )
    return {"rows": len(TASKS)}


def project_quality_rubric(writer: JsonlWriter, source_root: Path) -> dict[str, Any]:
    for item in QUALITY_RUBRIC:
        source_id = f"quality_rubric:{item['dimension']}"
        content = {
            "rubric_name": "C-IMHI explanation quality check",
            "dimension": item["dimension"],
            "definition": item["definition"],
            "score_levels": item["levels"],
            "source_section": "README.md#2 Expert quality check and evaluation guideline",
            "limitations": [
                "Rubric evaluates explanation quality; it is not a therapy intervention rubric.",
            ],
        }
        writer.write(
            wrap_unit(
                "quality_check_rubric",
                source_base(source_id, ["README.md"], "cimhi_expert_quality_check_guideline", "expert_quality_guideline"),
                content,
                {
                    "projection": "deterministic_cimhi_quality_rubric_projection",
                    "source_root": str(source_root),
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                    "deduplication": "none",
                },
            )
        )
    return {"rows": len(QUALITY_RUBRIC)}


def project_instruction_samples(source_root: Path, cd_writer: JsonlWriter, risk_writer: JsonlWriter) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    cd_path = source_root / "Instruct data" / "Instruct_CD.json"
    cd_rows = json.loads(cd_path.read_text(encoding="utf-8"))
    label_counts = Counter()
    no_post = 0
    max_chars = 0
    for idx, item in enumerate(cd_rows):
        instruction = item.get("instruction", "")
        output = item.get("output", "")
        post_text = extract_post(instruction)
        labels = parse_cd_labels(output)
        no_post += int(not post_text)
        label_counts.update(labels)
        max_chars = max(max_chars, len(instruction) + len(output))
        source_id = f"Instruct_CD.json:row/{idx}"
        content = {
            "sample_id": source_id,
            "task_name": "cognitive_distortion_detection",
            "post_text": post_text,
            "instruction": instruction,
            "source_label": ", ".join(labels),
            "distortion_labels": labels,
            "label_definition": "Source cognitive distortion label(s) from C-IMHI instruction output.",
            "explanation": output,
            "explanation_source_note": "README states full instruction explanations include GPT-4 supplementary explanations based on expert annotations.",
        }
        cd_writer.write(
            wrap_unit(
                "cognitive_distortion_signal",
                source_base(
                    source_id,
                    ["Instruct data/Instruct_CD.json"],
                    "cimhi_instruction_with_generated_or_expert_seeded_explanation",
                    "human_labelled_cognitive_distortion_instruction_with_generated_explanation",
                ),
                content,
                {
                    "projection": "deterministic_cimhi_cd_instruction_projection",
                    "source_root": str(source_root),
                    "row_index": idx,
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                    "deduplication": "none",
                },
            )
        )
    stats["cognitive_distortion"] = {
        "rows": len(cd_rows),
        "no_post": no_post,
        "label_counts": dict(label_counts.most_common()),
        "max_instruction_output_chars": max_chars,
    }

    suicide_path = source_root / "Instruct data" / "Instruct_suiside.json"
    suicide_rows = json.loads(suicide_path.read_text(encoding="utf-8"))
    risk_counts = Counter()
    no_post = 0
    max_chars = 0
    for idx, item in enumerate(suicide_rows):
        instruction = item.get("instruction", "")
        output = item.get("output", "")
        post_text = extract_post(instruction)
        label = parse_suicide_label(output)
        no_post += int(not post_text)
        risk_counts[label] += 1
        max_chars = max(max_chars, len(instruction) + len(output))
        source_id = f"Instruct_suiside.json:row/{idx}"
        content = {
            "sample_id": source_id,
            "task_name": "suicide_risk_detection",
            "post_text": post_text,
            "instruction": instruction,
            "source_label": label,
            "risk_label": label,
            "risk_domain": "suicide_risk",
            "severity_hint": "high" if label == "高自杀风险" else "low" if label == "低自杀风险" else "unknown",
            "label_definition": "Source high/low suicide-risk label from C-IMHI instruction output.",
            "explanation": output,
            "explanation_source_note": "README states full instruction explanations include GPT-4 supplementary explanations based on expert annotations.",
        }
        risk_writer.write(
            wrap_unit(
                "risk_or_symptom_signal",
                source_base(
                    source_id,
                    ["Instruct data/Instruct_suiside.json"],
                    "cimhi_instruction_with_generated_or_expert_seeded_explanation",
                    "human_labelled_suicide_risk_instruction_with_generated_explanation",
                ),
                content,
                {
                    "projection": "deterministic_cimhi_suicide_instruction_projection",
                    "source_root": str(source_root),
                    "row_index": idx,
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                    "deduplication": "none",
                },
            )
        )
    stats["suicide_risk"] = {
        "rows": len(suicide_rows),
        "no_post": no_post,
        "label_counts": dict(risk_counts.most_common()),
        "max_instruction_output_chars": max_chars,
    }
    return stats


def project_expert_examples(source_root: Path, cd_writer: JsonlWriter, risk_writer: JsonlWriter, cp_writer: JsonlWriter) -> dict[str, Any]:
    specs = [
        ("CD_example.xlsx", "cognitive_distortion_signal", cd_writer, "cognitive_distortion_detection", "expert_written_cognitive_distortion_example"),
        ("Suiside_example.xlsx", "risk_or_symptom_signal", risk_writer, "suicide_risk_detection", "expert_written_suicide_risk_example"),
        ("CP_example.xlsx", "cognitive_path_example", cp_writer, "cognitive_path_extraction", "expert_written_cognitive_path_example"),
    ]
    stats = {}
    for filename, kind, writer, task_name, construction_type in specs:
        rows = read_xlsx_rows(source_root / "Expert_example" / filename)
        for idx, row in enumerate(rows):
            post_text = row[0] if row else ""
            explanation = row[1] if len(row) > 1 else ""
            source_id = f"{filename}:row/{idx}"
            if kind == "cognitive_distortion_signal":
                labels = parse_cd_labels(explanation)
                content = {
                    "sample_id": source_id,
                    "task_name": task_name,
                    "post_text": post_text,
                    "source_label": ", ".join(labels),
                    "distortion_labels": labels,
                    "expert_explanation": explanation,
                    "label_definition": "Expert-written cognitive distortion explanation example.",
                }
            elif kind == "risk_or_symptom_signal":
                content = {
                    "sample_id": source_id,
                    "task_name": task_name,
                    "post_text": post_text,
                    "source_label": "",
                    "risk_domain": "suicide_risk",
                    "severity_hint": "",
                    "expert_explanation": explanation,
                    "label_definition": "Expert-written suicide-risk explanation example.",
                }
            else:
                content = {
                    "sample_id": source_id,
                    "task_name": task_name,
                    "post_text": post_text,
                    "source_label": "",
                    "cognitive_path_explanation": explanation,
                    "expert_explanation": explanation,
                    "label_definition": "Expert-written cognitive path extraction example.",
                }
            writer.write(
                wrap_unit(
                    kind,
                    source_base(
                        source_id,
                        [f"Expert_example/{filename}"],
                        construction_type,
                        "expert_written_interpretable_example",
                    ),
                    content,
                    {
                        "projection": "deterministic_cimhi_expert_example_projection",
                        "source_root": str(source_root),
                        "source_file": f"Expert_example/{filename}",
                        "row_index": idx,
                        "llm_calls": 0,
                        "sampling": "none",
                        "filtering": "none",
                        "deduplication": "none",
                    },
                )
            )
        stats[filename] = {"rows": len(rows)}
    return stats


def project_eval_cases(source_root: Path, writer: JsonlWriter) -> dict[str, Any]:
    path = source_root / "Evaluation" / "LLM" / "test_set.tsv"
    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if not line:
                continue
            label, text = line.split("\t", 1)
            rows.append({"label": label, "text": text})
    counts = Counter()
    for idx, row in enumerate(rows):
        label = row["label"]
        counts[label] += 1
        source_id = f"Evaluation/LLM/test_set.tsv:row/{idx}"
        content = {
            "case_id": source_id,
            "task_name": "suicide_risk_detection_eval",
            "input_text": row["text"],
            "gold_label": label,
            "label_mapping": {"0": "low suicide risk", "1": "high suicide risk"},
            "source_label": label,
            "evaluation_notes": [
                "Held-out LLM evaluation test case from local C-IMHI evaluation directory.",
                "Generated predictions are not included in this knowledge unit.",
            ],
        }
        writer.write(
            wrap_unit(
                "assessment_eval_case",
                source_base(source_id, ["Evaluation/LLM/test_set.tsv"], "cimhi_evaluation_test_case", "labelled_evaluation_case"),
                content,
                {
                    "projection": "deterministic_cimhi_eval_case_projection",
                    "source_root": str(source_root),
                    "row_index": idx,
                    "llm_calls": 0,
                    "sampling": "none",
                    "filtering": "none",
                    "deduplication": "none",
                },
            )
        )
    return {"rows": len(rows), "label_counts": dict(counts.most_common())}


def audit_generated_predictions(source_root: Path) -> dict[str, Any]:
    path = source_root / "Evaluation" / "LLM" / "generated_predictions.jsonl"
    rows = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows += 1
    return {
        "path": str(path),
        "rows": rows,
        "sha256": file_sha256(path),
        "promotion_policy": "not_promoted_to_knowledge_units_generated_model_predictions_only",
    }


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root)
    writers = {kind: JsonlWriter(path, overwrite=args.overwrite) for kind, path in OUTPUTS.items()}
    task_stats = project_tasks(writers["assessment_task"], source_root)
    quality_stats = project_quality_rubric(writers["quality_check_rubric"], source_root)
    instruction_stats = project_instruction_samples(source_root, writers["cognitive_distortion_signal"], writers["risk_or_symptom_signal"])
    expert_stats = project_expert_examples(
        source_root,
        writers["cognitive_distortion_signal"],
        writers["risk_or_symptom_signal"],
        writers["cognitive_path_example"],
    )
    eval_stats = project_eval_cases(source_root, writers["assessment_eval_case"])
    unit_files = {kind: writer.close() for kind, writer in writers.items()}

    source_rels = [
        "README.md",
        "Instruct data/Instruct_CD.json",
        "Instruct data/Instruct_suiside.json",
        "Expert_example/CD_example.xlsx",
        "Expert_example/Suiside_example.xlsx",
        "Expert_example/CP_example.xlsx",
        "Evaluation/LLM/test_set.tsv",
        "Evaluation/LLM/generated_predictions.jsonl",
    ]
    manifest = {
        "schema_version": "theraskill_cimhi_projection_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete",
        "source_root": str(source_root),
        "source_files": {
            rel: {"path": str(source_root / rel), "sha256": file_sha256(source_root / rel)}
            for rel in source_rels
        },
        "projection_policy": "deterministic raw C-IMHI projection; no LLM calls, no sampling, no filtering, no cross-dataset deduplication",
        "task_audit": task_stats,
        "quality_rubric_audit": quality_stats,
        "instruction_audit": instruction_stats,
        "expert_example_audit": expert_stats,
        "eval_case_audit": eval_stats,
        "generated_prediction_audit": audit_generated_predictions(source_root),
        "unit_files": unit_files,
        "qa_report": args.qa_report,
        "issues_count": 0,
    }
    write_json(args.manifest, manifest)
    expected_rows = {
        "assessment_task": 3,
        "cognitive_distortion_signal": 3407 + 28,
        "risk_or_symptom_signal": 1249 + 22,
        "cognitive_path_example": 28,
        "quality_check_rubric": 3,
        "assessment_eval_case": 250,
    }
    issues = []
    for kind, expected in expected_rows.items():
        if unit_files[kind]["rows"] != expected:
            issues.append({"issue": "unexpected_row_count", "kind": kind, "expected": expected, "actual": unit_files[kind]["rows"]})
    qa = {
        "schema_version": "theraskill_cimhi_projection_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "status": "complete" if not issues else "incomplete",
        "unit_files": unit_files,
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
