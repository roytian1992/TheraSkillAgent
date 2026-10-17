#!/usr/bin/env python3
"""Second-stage deterministic refinement for oversized style-response clusters."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_common"))

from io_utils import file_sha256, write_json  # noqa: E402


DEFAULT_TEXT_VIEW = "outputs/knowledge/dedup/indexes/unit_text_views/all_units.text_view.jsonl"
DEFAULT_V1_INPUT = "outputs/knowledge/dedup/graphs/oversized_component_refinement/style_response.oversized_refinement.jsonl"
DEFAULT_OUTPUT = "outputs/knowledge/dedup/graphs/style_response_v2_refinement/style_response.v2_refinement.jsonl"
DEFAULT_REPORT = "outputs/knowledge/dedup/reports/style_response_v2_refinement_report.json"
DEFAULT_MANIFEST = "outputs/knowledge/dedup/manifests/style_response_v2_refinement_manifest.json"
DEFAULT_QA = "outputs/knowledge/dedup/manifests/style_response_v2_refinement_qa.json"
DEFAULT_THRESHOLD = 200

TOPIC_PATTERNS: list[tuple[str, list[str]]] = [
    ("professional_referral", ["心理咨询", "心理医生", "咨询师", "治疗", "医生", "专业", "求助", "寻求帮助", "机构", "医院", "therapist", "doctor"]),
    ("relaxation_exercise", ["运动", "锻炼", "散步", "跑步", "快走", "瑜伽", "冥想", "放松", "呼吸", "音乐", "阅读", "读书", "娱乐活动", "活动"]),
    ("planning_goal_steps", ["计划", "目标", "小目标", "步骤", "一步", "慢慢", "方向", "规划", "记录", "写下", "安排", "制定"]),
    ("emotion_regulation", ["情绪", "冷静", "烦躁", "焦虑", "压力", "紧张", "负担", "心态", "积极", "调节", "控制", "平静"]),
    ("self_worth_strength", ["自我价值", "优点", "长处", "肯定", "相信自己", "自信", "价值", "值得", "能力", "成就", "努力"]),
    ("social_support_relationship", ["朋友", "家人", "父母", "同学", "老师", "伴侣", "男朋友", "女朋友", "关系", "沟通", "交流", "支持"]),
    ("study_work", ["学习", "学校", "工作", "职业", "辅导", "老师", "考试", "作业", "成绩", "班"]),
    ("sleep_body_health", ["睡眠", "身体", "健康", "休息", "饮食", "失眠", "疲惫", "身体健康"]),
    ("normalization_validation", ["正常", "很常见", "理解", "不用谢", "没问题", "别担心", "不是你的错", "可以理解"]),
    ("risk_boundary", ["自杀", "自残", "不想活", "伤害自己", "危机", "热线", "紧急"]),
]

MODAL_PATTERNS: list[tuple[str, list[str]]] = [
    ("try_can", ["可以", "尝试", "试着", "建议"]),
    ("should_need", ["应该", "需要", "必须", "最好"]),
    ("encouragement", ["相信", "一定", "加油", "希望", "祝", "能够"]),
    ("permission_choice", ["选择", "自己决定", "愿意", "如果你愿意"]),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-view", default=DEFAULT_TEXT_VIEW)
    parser.add_argument("--v1-input", default=DEFAULT_V1_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--qa-report", default=DEFAULT_QA)
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def stable_hash(payload: Any, length: int = 20) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:length]


def counter_dict(counter: Counter, limit: int | None = None) -> dict[str, int]:
    return {str(k): int(v) for k, v in counter.most_common(limit)}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def bucket_length(text: str) -> str:
    n = len(text or "")
    if n < 60:
        return "very_short"
    if n < 140:
        return "short"
    if n < 280:
        return "medium_short"
    if n < 500:
        return "medium"
    return "long"


def sentence_shape(text: str) -> str:
    text = text or ""
    sentence_count = len([part for part in re.split(r"[。！？!?；;]", text) if part.strip()])
    has_list = bool(re.search(r"(首先|其次|另外|同时|第一|第二|1[.)、]|2[.)、])", text))
    if has_list:
        return "list_or_steps"
    if sentence_count <= 1:
        return "single_sentence"
    if sentence_count <= 3:
        return "short_paragraph"
    return "multi_sentence"


def matched_labels(text: str, patterns: list[tuple[str, list[str]]]) -> list[str]:
    normalized = normalize_text(text)
    labels = []
    for label, words in patterns:
        if any(normalize_text(word) in normalized for word in words):
            labels.append(label)
    return labels


def primary_topic(labels: list[str]) -> str:
    return labels[0] if labels else "general_support"


def extract_v2_key(row: dict[str, Any]) -> dict[str, Any]:
    fields = row.get("dedup_key_fields") or {}
    response = fields.get("response") or row.get("dedup_text") or ""
    context = fields.get("context") or ""
    topics = matched_labels(response, TOPIC_PATTERNS)
    modals = matched_labels(response, MODAL_PATTERNS)
    style_labels = []
    text = row.get("dedup_text") or ""
    for label in ["professional_referral_or_boundary", "actionable_suggestion", "encouragement", "explicit_empathy", "soft_suggestion"]:
        if label in text:
            style_labels.append(label)
    return {
        "dataset": row.get("dataset"),
        "knowledge_type": row.get("knowledge_type"),
        "language": row.get("language"),
        "response_function": (fields.get("response_function") or "unknown")[:120],
        "primary_action_topic": primary_topic(topics),
        "all_action_topics": "+".join(topics[:4]) if topics else "general_support",
        "modal_signature": "+".join(modals[:3]) if modals else "none",
        "style_label_signature": "+".join(sorted(set(style_labels))) if style_labels else "none",
        "response_length_bucket": bucket_length(response),
        "sentence_shape": sentence_shape(response),
        "has_context": bool(str(context).strip()),
    }


def load_text_rows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    stats = {"rows": 0, "parse_errors": 0}
    issues: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:
                stats["parse_errors"] += 1
                issues.append({"issue": "text_view_parse_error", "line": line_no, "error": str(exc)})
                continue
            unit_id = row.get("unit_id")
            if unit_id:
                rows[unit_id] = row
    return rows, stats, issues


def iter_residual_subcomponents(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    residuals: list[dict[str, Any]] = []
    stats = {"parent_rows": 0, "subcomponents": 0, "residual_subcomponents": 0, "parse_errors": 0}
    issues: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            stats["parent_rows"] += 1
            try:
                row = json.loads(line)
            except Exception as exc:
                stats["parse_errors"] += 1
                issues.append({"issue": "v1_parse_error", "line": line_no, "error": str(exc)})
                continue
            for sub in row.get("subcomponents") or []:
                stats["subcomponents"] += 1
                if sub.get("still_oversized_after_refinement"):
                    residuals.append(sub)
                    stats["residual_subcomponents"] += 1
    return residuals, stats, issues


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"output exists: {output}; pass --overwrite")

    text_rows, text_stats, issues = load_text_rows(Path(args.text_view))
    residuals, v1_stats, v1_issues = iter_residual_subcomponents(Path(args.v1_input))
    issues.extend(v1_issues)

    output.parent.mkdir(parents=True, exist_ok=True)
    aggregate = {
        "input_residual_subcomponents": 0,
        "input_residual_members": 0,
        "v2_subcomponent_count": 0,
        "v2_multi_member_subcomponent_count": 0,
        "v2_singleton_subcomponent_count": 0,
        "v2_residual_oversized_count": 0,
        "largest_v2_subcomponent_size": 0,
        "v2_subcomponent_count_by_primary_topic": Counter(),
        "v2_residual_oversized_count_by_primary_topic": Counter(),
    }
    largest: list[dict[str, Any]] = []

    with output.open("w", encoding="utf-8") as handle:
        for residual in residuals:
            members = [str(uid) for uid in residual.get("member_unit_ids") or []]
            groups: dict[str, dict[str, Any]] = {}
            for unit_id in members:
                row = text_rows.get(unit_id)
                if not row:
                    issues.append({"issue": "missing_text_view_member", "subcomponent_id": residual.get("subcomponent_id"), "unit_id": unit_id})
                    continue
                key = extract_v2_key(row)
                key_hash = stable_hash(key, 16)
                if key_hash not in groups:
                    groups[key_hash] = {"v2_key": key, "member_unit_ids": []}
                groups[key_hash]["member_unit_ids"].append(unit_id)
            subcomponents = []
            for key_hash, payload in groups.items():
                sub_members = sorted(payload["member_unit_ids"])
                v2_id = f"stylev2_{stable_hash([residual.get('subcomponent_id'), key_hash], 20)}"
                still_oversized = len(sub_members) > args.threshold
                record = {
                    "v2_subcomponent_id": v2_id,
                    "v1_subcomponent_id": residual.get("subcomponent_id"),
                    "parent_component_id": residual.get("parent_component_id"),
                    "member_count": len(sub_members),
                    "member_unit_ids": sub_members,
                    "representative_unit_id": sub_members[0] if sub_members else None,
                    "v2_refinement_key": payload["v2_key"],
                    "v2_refinement_key_sha256": hashlib.sha256(json.dumps(payload["v2_key"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                    "still_oversized_after_v2": still_oversized,
                }
                subcomponents.append(record)
            subcomponents.sort(key=lambda item: (-item["member_count"], item["v2_subcomponent_id"]))
            parent_record = {
                "schema_version": "theraskill_style_response_v2_refinement_v1",
                "v1_subcomponent_id": residual.get("subcomponent_id"),
                "parent_component_id": residual.get("parent_component_id"),
                "v1_member_count": residual.get("member_count"),
                "v1_refinement_key": residual.get("refinement_key"),
                "v2_threshold": args.threshold,
                "v2_refinement_method": "deterministic_action_topic_keyword_signature",
                "v2_subcomponent_count": len(subcomponents),
                "v2_multi_member_subcomponent_count": sum(1 for item in subcomponents if item["member_count"] > 1),
                "v2_largest_subcomponent_size": max((item["member_count"] for item in subcomponents), default=0),
                "v2_residual_oversized_count": sum(1 for item in subcomponents if item["still_oversized_after_v2"]),
                "v2_subcomponents": subcomponents,
                "not_final_merged_unit": True,
                "raw_units_unchanged": True,
            }
            handle.write(json.dumps(parent_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

            aggregate["input_residual_subcomponents"] += 1
            aggregate["input_residual_members"] += int(residual.get("member_count") or 0)
            aggregate["v2_subcomponent_count"] += len(subcomponents)
            aggregate["v2_multi_member_subcomponent_count"] += parent_record["v2_multi_member_subcomponent_count"]
            aggregate["v2_singleton_subcomponent_count"] += sum(1 for item in subcomponents if item["member_count"] == 1)
            aggregate["v2_residual_oversized_count"] += parent_record["v2_residual_oversized_count"]
            aggregate["largest_v2_subcomponent_size"] = max(aggregate["largest_v2_subcomponent_size"], parent_record["v2_largest_subcomponent_size"])
            for item in subcomponents:
                topic = item["v2_refinement_key"]["primary_action_topic"]
                aggregate["v2_subcomponent_count_by_primary_topic"][topic] += 1
                if item["still_oversized_after_v2"]:
                    aggregate["v2_residual_oversized_count_by_primary_topic"][topic] += 1
                largest.append({
                    "v2_subcomponent_id": item["v2_subcomponent_id"],
                    "v1_subcomponent_id": item["v1_subcomponent_id"],
                    "member_count": item["member_count"],
                    "still_oversized_after_v2": item["still_oversized_after_v2"],
                    "v2_refinement_key": item["v2_refinement_key"],
                })

    aggregate_payload = {
        key: counter_dict(value) if isinstance(value, Counter) else value
        for key, value in aggregate.items()
    }
    largest.sort(key=lambda item: item["member_count"], reverse=True)
    status = "complete" if not issues else "incomplete"
    report = {
        "schema_version": "theraskill_style_response_v2_refinement_report_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": {
            "text_view": {"path": args.text_view, "sha256": file_sha256(args.text_view)},
            "v1_input": {"path": args.v1_input, "sha256": file_sha256(args.v1_input)},
        },
        "output": {"path": args.output, "sha256": file_sha256(args.output)},
        "threshold": args.threshold,
        "text_view_stats": text_stats,
        "v1_scan_stats": v1_stats,
        "aggregate": aggregate_payload,
        "largest_v2_subcomponents_top100": largest[:100],
        "interpretation": {
            "not_a_merge": True,
            "purpose": "Second-stage deterministic topic/shape refinement for SoulChatCorpus-style short suggestion chains.",
            "next_gate": "Residual oversized v2 subcomponents need stronger lexical or embedding bridge checks before LLM adjudication.",
        },
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    manifest = {
        "schema_version": "theraskill_style_response_v2_refinement_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "inputs": report["inputs"],
        "output": report["output"],
        "threshold": args.threshold,
        "aggregate": aggregate_payload,
        "report": args.report,
        "qa_report": args.qa_report,
        "issues_count": len(issues),
        "not_final_merge_release": True,
    }
    qa = {
        "schema_version": "theraskill_style_response_v2_refinement_qa_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "threshold": args.threshold,
        "aggregate": aggregate_payload,
        "issues_count": len(issues),
        "issues": issues[:500],
        "not_final_merge_release": True,
        "raw_units_unchanged": True,
    }
    write_json(args.report, report)
    write_json(args.manifest, manifest)
    write_json(args.qa_report, qa)
    print(json.dumps({
        "status": status,
        "issues_count": len(issues),
        "aggregate": aggregate_payload,
        "output": args.output,
    }, ensure_ascii=False, indent=2))
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
