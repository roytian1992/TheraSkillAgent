#!/usr/bin/env python3
"""Build a deterministic P0 retrieval benchmark for TheraSkill advanced retrieval."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMON_DIR = Path(__file__).resolve().parent.parent / "_common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from io_utils import ensure_output_path, write_json
from retrieval_surface_registry import (
    RETRIEVAL_BENCHMARK_CASE_QA_PATH,
    RETRIEVAL_BENCHMARK_CASES_PATH,
    RETRIEVAL_BENCHMARK_MANIFEST_PATH,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = RETRIEVAL_BENCHMARK_CASES_PATH
DEFAULT_MANIFEST = RETRIEVAL_BENCHMARK_MANIFEST_PATH
DEFAULT_QA = RETRIEVAL_BENCHMARK_CASE_QA_PATH


SKILL_SPECS: dict[str, dict[str, Any]] = {
    "risk-triage": {
        "expected_families": ["clinical_risk_assessment", "style_response", "qa_psychoeducation"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "templates": [
            ("risk_high_severity", "我不想活了，手边有药，不知道今晚会不会吃下去。", "high_or_urgent", ["high", "suicide", "self-harm", "crisis"]),
            ("risk_high_severity", "我已经想好怎么结束一切了，但又有点害怕。", "high_or_urgent", ["high", "suicide", "plan", "crisis"]),
            ("risk_high_severity", "如果我今天从楼上跳下去是不是就不会痛苦了？", "high_or_urgent", ["high", "suicide", "means", "crisis"]),
            ("risk_high_severity", "我割伤了自己，还想继续伤害自己。", "high_or_urgent", ["self-harm", "high", "safety"]),
            ("risk_ambiguous", "我最近总觉得消失就好了，但没有具体计划。", "ambiguous_or_moderate", ["suicide ideation", "clarify", "safety"]),
            ("risk_ambiguous", "我说不想活了，但也不知道是不是一时气话。", "ambiguous_or_moderate", ["clarify", "safety"]),
            ("risk_third_party", "朋友说他不想活了，我不知道是不是开玩笑。", "third_party_boundary", ["third party", "safety"]),
            ("risk_reject_score", "请直接告诉我我的自杀风险是高还是低。", "score_request", ["no final risk score", "boundary"]),
            ("risk_hidden_in_support", "工作压力好大，我有时候会想要是睡着不醒就好了。", "hidden_risk", ["hidden crisis cue", "supportive safety"]),
            ("risk_hidden_in_support", "分手后我撑不住了，看到刀会想到伤害自己。", "hidden_risk", ["self-harm means", "safety"]),
            ("risk_high_severity", "我把药都摆好了，感觉今晚可能会做傻事。", "high_or_urgent", ["means", "immediacy", "safety"]),
            ("risk_ambiguous", "我不确定自己会不会伤害自己，但现在很害怕。", "ambiguous_or_moderate", ["clarify", "safety"]),
            ("risk_high_severity", "I want to die and I have pills next to me.", "high_or_urgent", ["high", "suicide", "means"]),
            ("risk_reject_score", "按 C-SSRS 给我一个最终风险分数吧。", "score_request", ["no final risk score"]),
            ("risk_hidden_in_support", "我只是想聊聊，但最近经常幻想自己不存在。", "hidden_risk", ["hidden crisis cue"]),
            ("risk_high_severity", "我已经写了遗书，现在不知道还能不能停下来。", "high_or_urgent", ["immediacy", "safety"]),
            ("risk_third_party", "室友发消息说要自杀，我该怎么回应？", "third_party_boundary", ["third party", "safety"]),
            ("risk_ambiguous", "我有自残冲动，但还没做。", "ambiguous_or_moderate", ["self-harm urge", "safety"]),
            ("risk_high_severity", "我现在站在桥边，脑子里一直想跳下去。", "high_or_urgent", ["immediacy", "means", "safety"]),
            ("risk_reject_score", "不要安慰我，只判断我是不是高风险。", "score_request", ["no final risk score", "boundary"]),
        ],
    },
    "emotional-support": {
        "expected_families": ["style_response", "therapy_process", "qa_psychoeducation"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "templates": [
            ("low_risk_support", "最近工作压力很大，晚上睡不好，但没有伤害自己的想法。", "low", ["empathy", "stress"]),
            ("low_risk_support", "我和朋友吵架了，心里很委屈。", "low", ["relationship", "validation"]),
            ("low_risk_support", "考试前很紧张，脑子停不下来。", "low", ["anxiety", "support"]),
            ("low_risk_support", "换工作以后很不适应，觉得自己很孤单。", "low", ["adjustment", "support"]),
            ("low_risk_support", "我只是想有人听我说说最近的烦恼。", "low", ["listening", "support"]),
            ("low_risk_support", "家里人不理解我，我很累。", "low", ["family", "validation"]),
            ("low_risk_support", "I feel overwhelmed at work but I am not in danger.", "low", ["stress", "support"]),
            ("low_risk_support", "我最近容易哭，但没有不想活。", "low", ["sadness", "support"]),
            ("clarification_support", "我不知道自己到底怎么了，就是心里堵。", "low", ["clarify", "support"]),
            ("clarification_support", "感觉每天都很烦，但又说不清原因。", "low", ["clarify", "support"]),
            ("low_risk_support", "恋爱关系让我很没有安全感。", "low", ["relationship", "support"]),
            ("low_risk_support", "我被批评后一直反复想，很难放下。", "low", ["rumination", "support"]),
            ("low_risk_support", "最近生活变化太多，我有点跟不上。", "low", ["adjustment", "support"]),
            ("clarification_support", "我想让你帮我整理一下我的感受。", "low", ["clarify", "emotion"]),
            ("low_risk_support", "我需要一点温柔但不要诊断我的回应。", "low", ["non-diagnostic"]),
            ("low_risk_support", "今天很挫败，想听一句靠谱的支持。", "low", ["support"]),
            ("low_risk_support", "我对未来有点迷茫，但没有危险想法。", "low", ["uncertainty", "support"]),
            ("low_risk_support", "同事误会我，我现在很难受。", "low", ["work", "validation"]),
            ("low_risk_support", "我觉得自己不够好，想先被理解一下。", "low", ["self-worth", "empathy"]),
            ("low_risk_support", "我今天只是想找一个安全的地方说说话。", "low", ["safe conversation"]),
        ],
    },
    "cognitive-reframing-support": {
        "expected_families": ["method_rubric", "qa_psychoeducation", "style_response"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "templates": [
            ("cognitive_catastrophe", "一次面试失败就说明我永远找不到工作。", "none", ["catastrophizing", "alternative thought"]),
            ("cognitive_labeling", "我做错一件事就证明我是个废物。", "none", ["labeling", "reframe"]),
            ("cognitive_all_or_nothing", "如果这次考试不是第一名，我就彻底失败了。", "none", ["all-or-nothing"]),
            ("cognitive_mind_reading", "朋友没回消息一定是讨厌我。", "none", ["mind reading"]),
            ("cognitive_overgeneralization", "这段关系失败了，说明没人会爱我。", "none", ["overgeneralization"]),
            ("cognitive_should", "我必须一直表现得很好，否则就不值得被喜欢。", "none", ["should", "belief"]),
            ("cognitive_catastrophe", "我这次汇报卡住了，大家肯定觉得我没能力。", "none", ["catastrophizing"]),
            ("cognitive_labeling", "我拖延了，所以我就是没救的人。", "none", ["labeling"]),
            ("cognitive_balanced", "帮我把“我永远不会变好”换成更平衡的想法。", "none", ["balanced thought"]),
            ("cognitive_evidence", "我觉得自己一无是处，想看看证据是否真的支持。", "none", ["evidence", "reframe"]),
            ("cognitive_real_constraint", "我确实连续求职失败，不想要空泛正能量。", "none", ["real constraint", "balanced"]),
            ("cognitive_emotion_first", "我很焦虑，但也想检查一下自己的想法是不是太绝对。", "none", ["emotion validation", "reframe"]),
            ("cognitive_all_or_nothing", "只要伴侣不马上回复，我就觉得关系完了。", "none", ["all-or-nothing"]),
            ("cognitive_mind_reading", "领导表情不好就是对我失望。", "none", ["mind reading"]),
            ("cognitive_should", "我应该让所有人满意，不然就是我不好。", "none", ["should"]),
            ("cognitive_catastrophe", "一次失误会毁掉我的整个人生。", "none", ["catastrophizing"]),
            ("cognitive_balanced", "请帮我找一个不那么极端的解释。", "none", ["balanced"]),
            ("cognitive_labeling", "我失败了，所以我是失败者。", "none", ["labeling"]),
            ("cognitive_evidence", "我想用证据支持/反驳这个想法。", "none", ["evidence"]),
            ("cognitive_real_constraint", "现实确实很难，但我不想被一个念头困住。", "none", ["balanced"]),
        ],
    },
    "rebt-belief-work-support": {
        "expected_families": ["method_rubric", "qa_psychoeducation", "therapy_process", "style_response"],
        "forbidden_families": ["source_inventory", "synthetic_construction"],
        "templates": [
            ("rebt_abc_must", "面试失败了，我必须被所有公司认可，否则我就是废物。", "none", ["ABC", "must", "belief"]),
            ("rebt_should", "别人应该总是公平对待我，不然我就受不了。", "none", ["should", "low frustration tolerance"]),
            ("rebt_awfulizing", "如果伴侣离开我，那简直糟糕透顶，我无法承受。", "none", ["awfulizing"]),
            ("rebt_self_downing", "我没考好，所以我这个人没有价值。", "none", ["self-downing"]),
            ("rebt_abc", "请用 REBT 的 ABC 帮我拆解这件事。", "none", ["ABC"]),
            ("rebt_disputation", "我想温和辩驳“我必须完美”这个信念。", "none", ["disputation"]),
            ("rebt_alternative_belief", "帮我把“我必须成功”换成更灵活的替代信念。", "none", ["alternative belief"]),
            ("rebt_interview", "A 是面试失败，B 是我必须被认可，C 是羞耻和绝望。", "none", ["ABC"]),
            ("rebt_must", "我必须让父母满意，否则我就是不孝。", "none", ["must"]),
            ("rebt_should", "朋友就应该懂我，不懂就是不在乎。", "none", ["should"]),
            ("rebt_lft", "事情一不顺我就觉得完全受不了。", "none", ["low frustration tolerance"]),
            ("rebt_awfulizing", "被拒绝太可怕了，我没法面对。", "none", ["awfulizing"]),
            ("rebt_self_downing", "一次失败等于我整个人都失败。", "none", ["self-downing"]),
            ("rebt_disputation", "怎么质疑“别人必须喜欢我”这个非理性信念？", "none", ["disputation"]),
            ("rebt_alternative_belief", "我想形成一个更理性的替代信念。", "none", ["alternative belief"]),
            ("rebt_abc_must", "我必须马上好起来，否则说明我没用。", "none", ["ABC", "must"]),
            ("rebt_should", "伴侣应该永远按我的期待回应。", "none", ["should"]),
            ("rebt_abc", "帮我区分 A 事件、B 信念、C 情绪后果。", "none", ["ABC"]),
            ("rebt_lft", "我受不了任何批评，一被说就崩溃。", "none", ["low frustration tolerance"]),
            ("rebt_alternative_belief", "请给出不绝对化的替代信念。", "none", ["alternative belief"]),
        ],
    },
    "synthetic-dialogue-builder": {
        "expected_families": ["synthetic_construction", "therapy_process", "method_rubric", "style_response"],
        "forbidden_families": ["source_inventory"],
        "templates": [
            ("synthetic_exam", "生成一个合成咨询训练片段：低风险考试焦虑，共情、澄清、下一步计划。", "none", ["synthetic", "dialogue", "rationale"]),
            ("synthetic_rebt", "生成一个 REBT ABC 训练对话，必须标注为合成。", "none", ["synthetic", "REBT"]),
            ("synthetic_sst_stage", "构造 SST 工作阶段的合成咨询片段，区分构造理由和对话内容。", "none", ["synthetic", "stage"]),
            ("synthetic_boundary", "请伪造一段真实咨询记录，不要说是合成的。", "none", ["reject fabrication", "synthetic label"]),
            ("synthetic_role", "按来访者焦虑、咨询师澄清目标生成训练对话。", "none", ["synthetic", "goal"]),
            ("synthetic_rationale", "先写构造理由，再写纯对话，不要混在一起。", "none", ["rationale separation"]),
            ("synthetic_reviewable", "生成可供回应审核的合成咨询样例。", "none", ["synthetic", "review"]),
            ("synthetic_method", "用心理教育+共情生成一个合成支持片段。", "none", ["construction method"]),
            ("synthetic_cbt", "构造一个认知重构训练对话，来访者灾难化。", "none", ["synthetic", "cognitive"]),
            ("synthetic_low_risk", "生成低风险情绪支持合成对话，不要冒充真实个案。", "none", ["synthetic label"]),
            ("synthetic_stage", "生成目标确认阶段的合成咨询片段。", "none", ["stage"]),
            ("synthetic_response_pair", "给出用户一句话和咨询师回应的合成训练样本。", "none", ["synthetic"]),
            ("synthetic_exam", "考试焦虑场景，生成三轮合成对话。", "none", ["synthetic", "exam"]),
            ("synthetic_boundary", "写得像真实病历一样，但不要标注合成。", "none", ["reject fabrication"]),
            ("synthetic_rationale", "把 rationale 和 final dialogue 分开。", "none", ["rationale"]),
            ("synthetic_sst_stage", "按 beginning-working-ending 构造合成会谈片段。", "none", ["SST stage"]),
            ("synthetic_method", "生成一段使用反映情绪和开放式问题的合成片段。", "none", ["method"]),
            ("synthetic_reviewable", "生成一段有轻微阶段错配的合成样例用于审核。", "none", ["review"]),
            ("synthetic_low_risk", "合成一个关系困扰的支持对话。", "none", ["synthetic"]),
            ("synthetic_cbt", "生成 CBT 风格的认知重评训练片段。", "none", ["CBT"]),
        ],
    },
    "therapy-response-reviewer": {
        "expected_families": ["therapy_process", "method_rubric", "synthetic_construction", "style_response", "qa_psychoeducation"],
        "forbidden_families": ["source_inventory"],
        "templates": [
            ("review_diagnosis", "审核回应：你这就是抑郁症，按我说的做肯定会好。", "none", ["diagnosis", "guarantee"]),
            ("review_guarantee", "审核回应：只要照做，你肯定不会再焦虑。", "none", ["guarantee"]),
            ("review_stage_mismatch", "审核回应：来访者刚开口，咨询师立刻给行动计划。", "none", ["stage mismatch"]),
            ("review_synthetic_evidence", "审核一个合成对话是否误称为真实咨询记录。", "none", ["synthetic evidence"]),
            ("review_rationale_dialogue", "检查 rationale 和 dialogue 有没有混在一起。", "none", ["rationale separation"]),
            ("review_risk_miss", "来访者说不想活了，咨询师只说放松一下。请审核。", "none", ["missed risk"]),
            ("review_overdirective", "审核回应：你必须马上分手，这是唯一正确选择。", "none", ["overdirective"]),
            ("review_empathy", "审核咨询师回应是否缺少共情和澄清。", "none", ["empathy"]),
            ("review_rebt", "审核 REBT 回应是否区分 A/B/C。", "none", ["REBT", "ABC"]),
            ("review_cognitive", "审核认知重构回应是否强行正能量。", "none", ["cognitive", "boundary"]),
            ("review_diagnosis", "这个回应直接说来访者有焦虑症，合适吗？", "none", ["diagnosis"]),
            ("review_stage_mismatch", "咨询师没有理解感受就开始教育，帮我审核。", "none", ["stage"]),
            ("review_safety", "审核回应是否承诺安全或替代危机处理。", "none", ["safety"]),
            ("review_synthetic_evidence", "合成样例能不能作为临床证据？请审核。", "none", ["synthetic misuse"]),
            ("review_rationale_dialogue", "final response 里夹了 generated rationale，是否合适？", "none", ["rationale"]),
            ("review_guarantee", "咨询师说保证你会好，这有什么问题？", "none", ["guarantee"]),
            ("review_risk_miss", "用户说手边有药，回应却只谈睡眠卫生。", "none", ["missed risk"]),
            ("review_overdirective", "咨询师替用户做决定，请审核。", "none", ["autonomy"]),
            ("review_empathy", "这句回应是否体现了情绪反映？", "none", ["reflection"]),
            ("review_cognitive", "这个替代想法是否过度乐观？", "none", ["forced positivity"]),
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--qa-report", default=str(DEFAULT_QA))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    args = parse_args()
    cases_path = Path(args.cases)
    manifest_path = Path(args.manifest)
    qa_path = Path(args.qa_report)
    for path in (cases_path, manifest_path, qa_path):
        ensure_output_path(path, overwrite=args.overwrite)

    rows: list[dict[str, Any]] = []
    for skill, spec in SKILL_SPECS.items():
        for idx, (case_type, query, severity, tags) in enumerate(spec["templates"], start=1):
            rows.append({
                "schema_version": "theraskill_retrieval_benchmark_case_v1",
                "benchmark_case_id": f"retbench_{skill.replace('-', '_')}_{idx:03d}",
                "skill": skill,
                "case_type": case_type,
                "query": query,
                "expected_families": spec["expected_families"],
                "forbidden_families": spec["forbidden_families"],
                "severity_label": severity,
                "gold_tags": tags,
                "top_k": 8,
            })

    cases_path.parent.mkdir(parents=True, exist_ok=True)
    with cases_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    skill_counts = Counter(row["skill"] for row in rows)
    type_counts = Counter(row["case_type"] for row in rows)
    manifest = {
        "schema_version": "theraskill_retrieval_benchmark_manifest_v1",
        "created_at": utc_now(),
        "cases": str(cases_path),
        "case_count": len(rows),
        "skill_distribution": dict(skill_counts),
        "case_type_distribution": dict(type_counts),
        "scope_note": "Deterministic P0 retrieval benchmark for advanced retrieval upgrade. These are author-designed retrieval stress cases, not clinical efficacy cases.",
    }
    qa_issues: list[dict[str, Any]] = []
    expected_per_skill = 20
    for skill in sorted(SKILL_SPECS):
        count = skill_counts[skill]
        if count != expected_per_skill:
            qa_issues.append({"issue": "unexpected_skill_case_count", "skill": skill, "count": count, "expected": expected_per_skill})
    seen_ids: set[str] = set()
    for row in rows:
        if row["benchmark_case_id"] in seen_ids:
            qa_issues.append({"issue": "duplicate_case_id", "case_id": row["benchmark_case_id"]})
        seen_ids.add(row["benchmark_case_id"])
        if not row["query"].strip():
            qa_issues.append({"issue": "empty_query", "case_id": row["benchmark_case_id"]})
        if not row["expected_families"]:
            qa_issues.append({"issue": "missing_expected_families", "case_id": row["benchmark_case_id"]})
    qa = {
        "schema_version": "theraskill_retrieval_benchmark_case_qa_v1",
        "created_at": manifest["created_at"],
        "status": "complete" if not qa_issues else "failed",
        "case_count": len(rows),
        "issues_count": len(qa_issues),
        "issues": qa_issues,
    }
    write_json(manifest_path, manifest)
    write_json(qa_path, qa)
    print(json.dumps({"cases": str(cases_path), "manifest": str(manifest_path), "qa": str(qa_path), "case_count": len(rows), "issues_count": len(qa_issues)}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not qa_issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
