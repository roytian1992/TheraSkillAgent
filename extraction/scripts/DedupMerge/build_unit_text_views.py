#!/usr/bin/env python3
"""Build deduplication text views for raw TheraSkill knowledge units."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, write_json  # noqa: E402


DEFAULT_INPUT_DIR = "outputs/knowledge/units"
DEFAULT_OUTPUT = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/unit_text_view_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/unit_text_view_qa.json"

FAMILY_BY_KNOWLEDGE_TYPE = {
    # Style / response
    "style_example": "style_response",
    "low_weight_style_example": "style_response",
    "empathy_response_unit": "style_response",
    "emotion_style_pattern": "style_response",
    # QA / psychoeducation
    "support_qa_unit": "qa_psychoeducation",
    "psychoeducation_candidate": "qa_psychoeducation",
    "answer_strategy_sequence": "qa_psychoeducation",
    "answer_structure_pattern": "qa_psychoeducation",
    # Risk / assessment
    "suicide_risk_signal": "risk_assessment",
    "safety_eval_case": "risk_assessment",
    "suicide_risk_lexicon": "risk_assessment",
    "risk_or_symptom_signal": "risk_assessment",
    "assessment_eval_case": "risk_assessment",
    "assessment_task": "risk_assessment",
    "depression_severity_signal": "risk_assessment",
    "triage_boundary_example": "risk_assessment",
    "cognitive_distortion_signal": "risk_assessment",
    "cognitive_path_example": "risk_assessment",
    # Therapy process / strategy
    "interaction_pattern": "therapy_process",
    "case_trajectory": "therapy_process",
    "case_formulation_pattern": "therapy_process",
    "action_sequence_pattern": "therapy_process",
    "support_strategy_sequence": "therapy_process",
    "support_strategy": "therapy_process",
    "evaluation_dialogue_case": "therapy_process",
    "topic_support_dialogue_pattern": "therapy_process",
    # Method / rubric
    "mi_method_step": "method_rubric",
    "mi_quality_rubric": "method_rubric",
    "change_talk_pattern": "method_rubric",
    "thinking_trap_unit": "method_rubric",
    "reframing_step": "method_rubric",
    "quality_check_rubric": "method_rubric",
    "empathy_quality_rubric": "method_rubric",
    # Synthetic construction / inventory / governance
    "source_sample_inventory": "source_inventory",
    "synthetic_construction_method": "synthetic_construction",
    "construction_pattern": "synthetic_construction",
    "construction_review_pattern": "synthetic_construction",
    "dialogue_expansion_pattern": "synthetic_construction",
    "client_followup_pattern": "synthetic_construction",
    "support_response_packaging_pattern": "synthetic_construction",
    "synthetic_quality_boundary": "synthetic_construction",
    "source_route_audit": "source_route_audit",
}

RISK_TERMS = [
    "自杀",
    "轻生",
    "自残",
    "不想活",
    "结束生命",
    "suicide",
    "suicidal",
    "self-harm",
    "kill myself",
    "end my life",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Optional debug limit; do not use for release outputs.")
    return parser.parse_args()


def normalize_for_hash(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact_text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return normalize_for_hash(text)[:limit]


def iter_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        if value:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)
    else:
        yield str(value)


def first_existing(content: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = content.get(key)
        if value not in (None, "", [], {}):
            return compact_text(value)
    return ""


def empathy_annotation_summary(content: dict[str, Any]) -> tuple[str, str]:
    annotations = content.get("empathy_annotations")
    if not isinstance(annotations, dict):
        return "", ""
    labels = []
    spans = []
    for dimension, payload in sorted(annotations.items()):
        if not isinstance(payload, dict):
            continue
        source_level = payload.get("source_level")
        level_definition = payload.get("level_definition")
        if source_level not in (None, ""):
            labels.append(f"{dimension}:level_{source_level}")
        elif level_definition:
            labels.append(f"{dimension}:{level_definition}")
        for span in payload.get("rationale_spans") or []:
            if span:
                spans.append(str(span))
    return compact_text(" ".join(labels), 1000), compact_text(" ".join(spans), 1500)


def content_for(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    knowledge_type = row.get("knowledge_type")
    envelope_content = row.get("content") or {}
    content = envelope_content.get(knowledge_type) if isinstance(envelope_content, dict) else None
    if isinstance(content, dict):
        return knowledge_type, content
    if isinstance(envelope_content, dict):
        return knowledge_type, envelope_content
    return knowledge_type, {}


def dedup_text_for(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    knowledge_type, content = content_for(row)
    source = row.get("source") or {}
    family = FAMILY_BY_KNOWLEDGE_TYPE.get(knowledge_type, "unknown")
    key_fields: dict[str, Any] = {}

    if family == "style_response":
        response = first_existing(content, ["response_text", "assistant_text", "final_response_text", "response_post", "text", "utterance", "response"])
        context = first_existing(content, ["input_text", "client_text", "context_summary", "prompt_text", "seeker_post", "user_text", "current_client_utterance"])
        labels = " ".join(str(x) for x in (content.get("style_features") or content.get("emotion_labels") or []))
        empathy_labels, empathy_spans = empathy_annotation_summary(content)
        function = compact_text(content.get("response_function", ""))
        text = "\n".join(part for part in [context, response, function, labels, empathy_labels, empathy_spans] if part)
        key_fields = {
            "context": context[:500],
            "response": response[:1000],
            "response_function": function,
            "empathy_annotation_labels": empathy_labels,
            "empathy_rationale_spans": empathy_spans[:1000],
        }
    elif family == "qa_psychoeducation":
        question = first_existing(content, ["question_text", "input_text", "context_text", "problem_text", "client_question", "current_client_utterance"])
        answer = first_existing(content, ["answer_text", "response_text", "final_response_text", "support_answer", "output_text"])
        structure = compact_text(content.get("answer_structure") or content.get("response_structure") or content.get("strategy_sequence") or "")
        text = "\n".join(part for part in [question, answer, structure] if part)
        key_fields = {"question": question[:1000], "answer": answer[:1000], "structure": structure[:500]}
    elif family == "risk_assessment":
        body = first_existing(content, ["text", "dialogue_text", "post_text", "input_text", "context_text", "case_text", "instruction", "user_text"])
        label = first_existing(content, ["risk_label", "severity_label", "source_label", "label", "task_name"])
        rationale = first_existing(content, ["explanation", "generated_explanation", "rationale", "quality_note"])
        text = "\n".join(part for part in [body, label, rationale] if part)
        key_fields = {"text": body[:1200], "label": label[:300]}
    elif family == "therapy_process":
        pieces = []
        for key in [
            "pre_state",
            "client_state",
            "presenting_problem",
            "therapist_action",
            "move_sequence",
            "strategy_sequence",
            "client_response",
            "transition",
            "overall_process_summary",
            "support_process_summary",
            "content_summary",
        ]:
            if key in content:
                pieces.append(compact_text(content.get(key), 1200))
        if not pieces:
            pieces = [compact_text(content, 2500)]
        text = "\n".join(piece for piece in pieces if piece)
        key_fields = {"process_text": text[:2000]}
    elif family == "method_rubric":
        pieces = []
        for key in [
            "method_name",
            "step_name",
            "trap_label",
            "quality_dimension",
            "criteria",
            "definition",
            "recognition_cues",
            "positive_criteria",
            "failure_patterns",
            "reframe",
            "thinking_trap",
        ]:
            if key in content:
                pieces.append(compact_text(content.get(key), 1000))
        if not pieces:
            pieces = [compact_text(content, 2500)]
        text = "\n".join(piece for piece in pieces if piece)
        key_fields = {"method_text": text[:2000]}
    elif family == "synthetic_construction":
        pieces = []
        for key in [
            "method_or_framework_name",
            "generation_or_packaging_steps",
            "progressive_dialogue_synthesis_steps",
            "construction_pattern",
            "coverage_rule",
            "dialogue_stage_sequence",
            "stage_consistency_checks",
            "therapy_fidelity_checks",
            "limitations",
        ]:
            if key in content:
                pieces.append(compact_text(content.get(key), 1200))
        if not pieces:
            pieces = [compact_text(content, 2500)]
        text = "\n".join(piece for piece in pieces if piece)
        key_fields = {"construction_text": text[:2000]}
    elif family == "source_inventory":
        text = "\n".join(
            part
            for part in [
                compact_text(source.get("dataset")),
                compact_text(content.get("raw_pointer") or content.get("source_id") or source.get("source_id")),
                compact_text(content.get("normalized_tag") or content.get("topic") or content.get("sample_type")),
                compact_text(content.get("first_user_excerpt") or content.get("context_excerpt")),
            ]
            if part
        )
        key_fields = {"raw_pointer": compact_text(content.get("raw_pointer") or source.get("source_id"), 500)}
    elif family == "source_route_audit":
        text = compact_text(content, 2500)
        key_fields = {"dataset": compact_text(content.get("dataset"), 200)}
    else:
        text = compact_text(content, 2500)
        key_fields = {"fallback": text[:1000]}

    if not text:
        text = compact_text(content, 2500)
    return text, key_fields


def risk_flags(text: str, source: dict[str, Any], content: dict[str, Any]) -> list[str]:
    joined = " ".join([text, compact_text(source.get("evidence_tier", "")), compact_text(content.get("risk_level", ""))]).lower()
    flags = []
    if any(term.lower() in joined for term in RISK_TERMS):
        flags.append("self_harm_or_suicide_term")
    if "high" in joined or "重度" in joined or "高自杀风险" in joined:
        flags.append("high_risk_or_severe_label")
    return flags


def license_constraints(source: dict[str, Any], content: dict[str, Any]) -> list[str]:
    license_text = " ".join(
        [
            compact_text(source.get("license", "")),
            compact_text(content.get("license_status", "")),
        ]
    ).lower()
    constraints = []
    if "rail" in license_text:
        constraints.append("rail_d_no_rewrite_or_commercial_constraint")
    if "not_specified" in license_text or "other" in license_text:
        constraints.append("license_requires_review")
    if "cc-by-nc-nd" in license_text:
        constraints.append("noncommercial_no_derivatives")
    return constraints


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"output exists: {output}; pass --overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(input_dir.glob("*.jsonl"))
    counters = {
        "files": len(files),
        "input_rows": 0,
        "output_rows": 0,
        "empty_dedup_text": 0,
        "parse_errors": 0,
    }
    by_family = Counter()
    by_knowledge_type = Counter()
    by_dataset = Counter()
    by_file: dict[str, dict[str, Any]] = {}
    text_hash_counts = Counter()
    duplicate_text_hashes = 0
    issues = []
    digest = hashlib.sha256()

    with output.open("w", encoding="utf-8") as out:
        for path in files:
            file_rows = 0
            file_families = Counter()
            with path.open("r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    if args.limit is not None and counters["input_rows"] >= args.limit:
                        break
                    if not line.strip():
                        continue
                    counters["input_rows"] += 1
                    file_rows += 1
                    try:
                        row = json.loads(line)
                    except Exception as exc:  # pragma: no cover - release audit records details
                        counters["parse_errors"] += 1
                        issues.append({"issue": "parse_error", "file": str(path), "line": line_no, "error": str(exc)})
                        continue
                    knowledge_type, content = content_for(row)
                    source = row.get("source") or {}
                    dataset = source.get("dataset") or path.name.split(".")[0]
                    family = FAMILY_BY_KNOWLEDGE_TYPE.get(knowledge_type, "unknown")
                    text, key_fields = dedup_text_for(row)
                    normalized_text = normalize_for_hash(text)
                    if not normalized_text:
                        counters["empty_dedup_text"] += 1
                    text_hash = hashlib.sha256((family + "\n" + normalized_text).encode("utf-8")).hexdigest()
                    text_hash_counts[text_hash] += 1
                    view = {
                        "schema_version": "theraskill_dedup_text_view_v1",
                        "unit_id": row.get("unit_id"),
                        "dataset": dataset,
                        "knowledge_type": knowledge_type,
                        "source_construction_type": source.get("source_construction_type"),
                        "evidence_tier": source.get("evidence_tier"),
                        "language": source.get("language", "unknown"),
                        "dedup_family": family,
                        "dedup_text": normalized_text,
                        "dedup_text_sha256": text_hash,
                        "dedup_key_fields": key_fields,
                        "risk_or_safety_flags": risk_flags(normalized_text, source, content),
                        "license_constraints": license_constraints(source, content),
                        "raw_file": str(path),
                        "raw_line_no": line_no,
                    }
                    encoded = json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                    digest.update(encoded.encode("utf-8"))
                    out.write(encoded)
                    counters["output_rows"] += 1
                    by_family[family] += 1
                    by_knowledge_type[str(knowledge_type)] += 1
                    by_dataset[str(dataset)] += 1
                    file_families[family] += 1
            by_file[path.name] = {"rows": file_rows, "family_distribution": dict(file_families.most_common())}
            if args.limit is not None and counters["input_rows"] >= args.limit:
                break

    duplicate_text_hashes = sum(1 for count in text_hash_counts.values() if count > 1)
    if counters["parse_errors"]:
        issues.append({"issue": "parse_errors_present", "count": counters["parse_errors"]})
    if counters["empty_dedup_text"]:
        issues.append({"issue": "empty_dedup_text_present", "count": counters["empty_dedup_text"]})
    if counters["output_rows"] != counters["input_rows"] - counters["parse_errors"]:
        issues.append(
            {
                "issue": "row_count_mismatch",
                "input_rows": counters["input_rows"],
                "parse_errors": counters["parse_errors"],
                "output_rows": counters["output_rows"],
            }
        )

    status = "complete" if not issues else "incomplete"
    output_report = {
        "path": str(output),
        "rows": counters["output_rows"],
        "sha256": digest.hexdigest(),
        "file_sha256": file_sha256(output),
    }
    manifest = {
        "schema_version": "theraskill_dedup_text_view_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "input_dir": str(input_dir),
        "input_file_count": len(files),
        "input_files": {path.name: {"path": str(path), "sha256": file_sha256(path)} for path in files},
        "output": output_report,
        "counts": counters,
        "family_distribution": dict(by_family.most_common()),
        "knowledge_type_distribution": dict(by_knowledge_type.most_common()),
        "dataset_distribution": dict(by_dataset.most_common()),
        "file_distribution": by_file,
        "duplicate_text_hash_count": duplicate_text_hashes,
        "issues_count": len(issues),
        "qa_report": args.qa_report,
        "limit": args.limit,
    }
    qa = {
        "schema_version": "theraskill_dedup_text_view_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "counts": counters,
        "family_distribution": dict(by_family.most_common()),
        "knowledge_type_distribution": dict(by_knowledge_type.most_common()),
        "dataset_distribution": dict(by_dataset.most_common()),
        "duplicate_text_hash_count": duplicate_text_hashes,
        "issues": issues,
        "issues_count": len(issues),
        "raw_units_unchanged": True,
    }
    write_json(args.manifest, manifest, sort_keys=False)
    write_json(args.qa_report, qa, sort_keys=False)
    print(json.dumps({"status": status, "issues_count": len(issues), "output": output_report, "qa_report": args.qa_report}, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
