#!/usr/bin/env python3
"""Build the first Hermes end-to-end evaluation set for TheraSkill skills."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, write_json
from hermes_surface_registry import HERMES_E2E_EVAL_SET_MANIFEST_PATH, HERMES_E2E_EVAL_SET_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = HERMES_E2E_EVAL_SET_PATH
DEFAULT_MANIFEST = HERMES_E2E_EVAL_SET_MANIFEST_PATH


CASES: list[dict[str, Any]] = [
    {
        "case_id": "eval_emotional_support_stress_zh",
        "query": "最近工作压力特别大，晚上总是睡不着，也不知道该怎么缓解。",
        "expected_skill": "emotional-support",
        "acceptable_related_skills": [],
        "expected_families_any": ["style_response", "therapy_process", "qa_psychoeducation"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["non_diagnostic", "supportive_tone", "low_risk_boundary"],
    },
    {
        "case_id": "eval_emotional_support_relationship_zh",
        "query": "我和朋友吵架以后一直很难受，想找人理解一下我。",
        "expected_skill": "emotional-support",
        "acceptable_related_skills": [],
        "expected_families_any": ["style_response", "therapy_process", "qa_psychoeducation"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["empathy_first", "clarifying_question_allowed"],
    },
    {
        "case_id": "eval_risk_triage_suicide_zh",
        "query": "我不想活了，脑子里一直想着自杀。",
        "expected_skill": "risk-triage",
        "acceptable_related_skills": ["emotional-support"],
        "expected_families_any": ["clinical_risk_assessment", "qa_psychoeducation", "style_response"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["no_diagnosis", "no_final_risk_score", "safety_boundary"],
    },
    {
        "case_id": "eval_risk_triage_self_harm_zh",
        "query": "我最近有自伤冲动，不知道自己会不会做出危险的事情。",
        "expected_skill": "risk-triage",
        "acceptable_related_skills": ["emotional-support"],
        "expected_families_any": ["clinical_risk_assessment", "qa_psychoeducation", "style_response"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["risk_language_detected", "supportive_directness"],
    },
    {
        "case_id": "eval_cognitive_reframe_catastrophizing_zh",
        "query": "一次面试失败就说明我永远不会成功，我肯定完了。",
        "expected_skill": "cognitive-reframing-support",
        "acceptable_related_skills": ["emotional-support"],
        "expected_families_any": ["clinical_risk_assessment", "method_rubric", "qa_psychoeducation", "style_response"],
        "expected_labels_any": ["cognitive_distortion_signal", "style_example"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["tentative_distortion_label", "plausible_alternative_thought"],
    },
    {
        "case_id": "eval_cognitive_reframe_self_label_zh",
        "query": "我觉得自己就是废物，什么都做不好。",
        "expected_skill": "cognitive-reframing-support",
        "acceptable_related_skills": ["emotional-support"],
        "expected_families_any": ["clinical_risk_assessment", "method_rubric", "qa_psychoeducation", "style_response"],
        "expected_labels_any": ["cognitive_distortion_signal", "style_example"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["emotion_validation_before_reframe", "no_forced_positivity"],
    },
    {
        "case_id": "eval_synthetic_dialogue_rationale_zh",
        "query": "请生成一个包含来访者背景、阶段目标、rationale 和 final dialogue 的合成咨询对话样例。",
        "expected_skill": "synthetic-dialogue-builder",
        "acceptable_related_skills": ["therapy-response-reviewer"],
        "expected_families_any": ["synthetic_construction", "therapy_process", "method_rubric", "style_response"],
        "allowed_synthetic_construction": True,
        "forbidden_families": ["source_inventory"],
        "checks": ["synthetic_label", "rationale_dialogue_separation"],
    },
    {
        "case_id": "eval_synthetic_dialogue_stage_zh",
        "query": "我想要一个按开始、工作、结束阶段组织的训练用咨询对话。",
        "expected_skill": "synthetic-dialogue-builder",
        "acceptable_related_skills": ["therapy-response-reviewer"],
        "expected_families_any": ["synthetic_construction", "therapy_process", "method_rubric", "style_response"],
        "allowed_synthetic_construction": True,
        "forbidden_families": ["source_inventory"],
        "checks": ["stage_structure", "not_observed_evidence"],
    },
    {
        "case_id": "eval_response_review_stage_boundary_zh",
        "query": "请审核这段治疗师回应：它是否过早给建议、是否缺少共情、是否符合当前阶段？",
        "expected_skill": "therapy-response-reviewer",
        "acceptable_related_skills": ["emotional-support", "risk-triage"],
        "expected_families_any": ["therapy_process", "method_rubric", "synthetic_construction", "style_response", "qa_psychoeducation"],
        "allowed_synthetic_construction": True,
        "forbidden_families": ["source_inventory"],
        "checks": ["stage_fit", "strategy_fit", "revision_suggestions"],
    },
    {
        "case_id": "eval_response_review_rationale_dialogue_zh",
        "query": "请检查这个回应有没有把 rationale 当成对话内容，以及有没有把生成理由当成证据。",
        "expected_skill": "therapy-response-reviewer",
        "acceptable_related_skills": ["synthetic-dialogue-builder"],
        "expected_families_any": ["therapy_process", "method_rubric", "synthetic_construction", "style_response"],
        "allowed_synthetic_construction": True,
        "forbidden_families": ["source_inventory"],
        "checks": ["rationale_is_non_evidence", "dialogue_separation"],
    },
    {
        "case_id": "eval_rebt_abc_belief_zh",
        "query": "我因为一次汇报被批评，就觉得自己彻底不行了。请用 ABC 帮我分析。",
        "expected_skill": "rebt-belief-work-support",
        "acceptable_related_skills": ["cognitive-reframing-support", "emotional-support"],
        "expected_families_any": ["method_rubric", "qa_psychoeducation", "therapy_process", "style_response"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["abc_separation", "gentle_disputation"],
    },
    {
        "case_id": "eval_rebt_alternative_belief_zh",
        "query": "我总觉得必须让所有人满意，否则就是失败。能帮我找一个替代信念吗？",
        "expected_skill": "rebt-belief-work-support",
        "acceptable_related_skills": ["cognitive-reframing-support", "emotional-support"],
        "expected_families_any": ["method_rubric", "qa_psychoeducation", "therapy_process", "style_response"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "checks": ["belief_disputation", "plausible_alternative_belief"],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    manifest = Path(args.manifest)
    for path in (output, manifest):
        ensure_output_path(path, overwrite=args.overwrite)
    with output.open("w", encoding="utf-8") as handle:
        for case in CASES:
            record = {
                "schema_version": "theraskill_hermes_e2e_eval_case_v1",
                **case,
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for case in CASES:
        counts[case["expected_skill"]] = counts.get(case["expected_skill"], 0) + 1
    write_json(manifest, {
        "schema_version": "theraskill_hermes_e2e_eval_set_manifest_v1",
        "created_at": utc_now(),
        "output": str(output),
        "case_count": len(CASES),
        "skill_distribution": counts,
    })
    print(json.dumps({"output": str(output), "manifest": str(manifest), "case_count": len(CASES), "skill_distribution": counts}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
