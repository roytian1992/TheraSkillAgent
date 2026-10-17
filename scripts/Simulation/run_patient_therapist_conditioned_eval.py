#!/usr/bin/env python3
"""Compare patient simulator behavior under different therapist styles on the same scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_patient_scenario_execution import (  # noqa: E402
    execute_scenario_row,
    load_runtime_stack,
    read_json,
    read_jsonl,
)
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_CONDITIONED_EVAL_OUTPUT_DIR,
    PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
    SIMULATION_SCENARIO_CURRENT_SPLIT_KEY,
    SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES,
    SIMULATION_SCENARIO_REGISTRY_PATH,
)
from io_utils import file_sha256, write_json, write_jsonl  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH
DEFAULT_SUMMARY_PROMPT = SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH
DEFAULT_REVIEWER_PROMPT = PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH
DEFAULT_OUTPUT_DIR = DEFAULT_CONDITIONED_EVAL_OUTPUT_DIR


ALLIANCE_RANK = {
    "unknown_pre_session": 0,
    "pre_session_or_early_contact": 0,
    "strained": 0,
    "ruptured": 0,
    "forming": 1,
    "tentative": 1,
    "working": 2,
    "improved": 2,
    "strong": 3,
    "repaired": 3,
}

DISCLOSURE_RANK = {
    "unknown_pre_session": 0,
    "minimal": 0,
    "surface": 1,
    "surface_to_moderate": 1,
    "moderate": 2,
    "moderate_to_deep": 3,
    "deep": 4,
}

RESISTANCE_RANK = {
    "low": 0,
    "low_to_moderate": 1,
    "guarded": 2,
    "moderate": 2,
    "moderate_to_high": 3,
    "high": 4,
}

FELT_UNDERSTANDING_RANK = {
    "decreased": 0,
    "unknown": 1,
    "unknown_pre_session": 1,
    "unchanged": 2,
    "mixed": 2,
    "increased": 3,
}


THERAPIST_ARMS = {
    "supportive": {
        "label": "Supportive Therapist",
        "description": "High validation, exploratory pacing, reflective questions.",
        "turns": [
            "听起来你现在最难受的，不只是事情本身，而是那种一直悬着、不知道该怎么帮的感觉。我们先不急着找办法，能不能先把你心里最沉的那块说清楚？",
            "当你这么讲的时候，我感觉你一边很心疼，一边也在拼命扛着责任。那如果把这份压力拆开来看，哪一部分最让你喘不过气？",
            "你已经在很努力地撑着了。此刻如果只允许自己先照见一个最真实的感受，它更像是害怕、内疚，还是别的什么？",
        ],
    },
    "poor": {
        "label": "Poor Therapist",
        "description": "Low attunement, generic reassurance, premature closure.",
        "turns": [
            "其实你也不用想太多，很多孩子青春期都这样，过一阵子自己就会好起来。",
            "你现在最重要的还是别把事情搞复杂了，先别管情绪那些，顺其自然一点就行。",
            "总之你就放轻松，别再反复想了，越想越没用。",
        ],
    },
    "overly_directive": {
        "label": "Overly Directive Therapist",
        "description": "Advice-heavy, pushes action before enough emotional processing.",
        "turns": [
            "我们先别谈感觉，直接定一个方案：你今晚就跟他谈一次，把手机和学习时间都重新立好规矩。",
            "你需要更坚定一点，不然孩子只会继续拖。现在先把每天几点学习、几点睡觉都列出来执行。",
            "如果他不配合，你就必须坚持底线，不要再被情绪带着走了，先把行为纠正过来。",
        ],
    },
    "rupture_prone": {
        "label": "Rupture-Prone Therapist",
        "description": "Interpretively sharp, subtly blaming, likely to strain alliance.",
        "turns": [
            "我听下来会觉得，你嘴上说担心孩子，可实际上你还是放不下成绩和控制，这可能才是问题核心。",
            "如果你继续这样一边焦虑一边盯着他，其实也许你自己就在加重他的退缩，这点你要正视。",
            "所以与其一直说无助，不如先承认你现在的做法本身就在伤害关系，你能接受这一点吗？",
        ],
    },
}


FAMILY_ARM_OVERRIDES = {
    "cognitive_reframing_probe": {
        "supportive": {
            "label": "Supportive Therapist",
            "description": "Supportive pacing calibrated for surfacing automatic appraisals and feared meanings across domains.",
            "turns": [
                "听起来现在最难的不只是事情本身，而是这件事已经慢慢在你心里形成了一种解释。我们先不急着判断那个解释对不对，先回到最近一次最刺痛你的时刻：当时你脑子里最先冒出来的那句话更像什么？",
                "你刚才已经把外在事情讲清楚了。如果往里再走半步，那种最自动、最难摆脱的想法，更像“是不是我哪里不对”“是不是事情已经没救了”，还是别的什么？我们先只把它说准，不急着改。",
                "如果我们先不急着碰最深的结论，只看这件事接下来最容易让你担心的一步，你最怕接下来会发生什么，或者你最怕自己又会怎么想？",
            ],
        },
        "poor": {
            "label": "Poor Therapist",
            "description": "Generic reassurance and shallow reframing that glosses over the person's own appraisal pattern.",
            "turns": [
                "其实你也不用把这件事想得太重，很多时候只是自己当下太敏感了，过几天缓一缓可能就没那么严重了。",
                "你先别一直追着这些想法转，不然越分析越容易更乱。先把注意力转开一点，别总往最坏处想。",
                "总之先别急着给这件事下太多结论，顺其自然一点，很多担心其实没必要现在就想这么深。",
            ],
        },
        "overly_directive": {
            "label": "Overly Directive Therapist",
            "description": "Advice-heavy and thought-correction-first before the person's appraisal has been fully articulated.",
            "turns": [
                "我们先别继续停在感觉里，直接把最核心的那个想法抓出来，然后马上换一个更平衡的说法，不要再让原来的解释反复带着你走。",
                "你现在先不要追着每个情绪细节走，先按一个固定步骤来：写下触发点，写下脑子里最先跳出的判断，再强制补一条更现实的替代解释。",
                "现在最重要的是先把想法结构改过来，而不是继续跟着那股担心往下滑。先执行这个步骤，再回来谈你感觉到了什么。",
            ],
        },
        "rupture_prone": {
            "label": "Rupture-Prone Therapist",
            "description": "Prematurely interpretive and shaming about the person's own hidden meaning-making pattern.",
            "turns": [
                "我听下来会觉得，你嘴上说是在谈眼前这件事，但更核心的也许是你很快就把它解释成了“这是不是说明我不够好”或者“事情已经要失控了”。",
                "如果你一直把重点放在外面的情境上，某种程度上也会让你不用太快去碰那个更难受的部分：也许你已经先替这件事下了一个很重的结论。你能承认这一点吗？",
                "所以与其一直说自己只是被事情困住，也许更需要先承认：你现在已经在用一个很伤自己的解释看待这件事，而你也一直没有真正松开它。",
            ],
        },
    },
    "supportive_exploration": {
        "supportive": {
            "label": "Supportive Therapist",
            "description": "Supportive pacing calibrated for hesitant disclosure, loneliness, and fear that reaching out will not be met.",
            "turns": [
                "听起来最难的不只是事情本身，而是每次想靠近、想开口时，那种可能又得不到回应的感觉。我们先不急着找办法，先回到最近一次你最想说却卡住的时刻：当时更像是怕被忽视、怕被误解，还是怕说了也没有回应？",
                "你刚才已经说得很清楚了：不是完全不想靠近，而是有些时候那种落空的感觉一下子冲上来，就会让你更想退回去。要是把最难受的那一下再说具体一点，它更像“我说了也没用”，还是“我一开口就会更难堪”？",
                "如果我们先不逼自己一下子把关系修好，只想先看清下次最容易卡住的那一下，你觉得最难的是开口前那一瞬间，还是说完以后怕对方没反应的那一瞬间？如果只试一个最小动作，你最担心会卡在哪？",
            ],
        },
        "poor": {
            "label": "Poor Therapist",
            "description": "Generic reassurance and low attunement, but still nominally anchored to relationship distance and loneliness.",
            "turns": [
                "其实你也不用把现在这种疏离想得太严重，很多关系都会有一阵子冷下来，过一段时间自然会缓和。",
                "你现在最重要的还是别一直盯着这些感受，先把注意力转开一点，不然越想越容易难受。",
                "总之先顺其自然，不要急着把每一次没回应都看得太重，放轻松一点可能反而会好些。",
            ],
        },
        "overly_directive": {
            "label": "Overly Directive Therapist",
            "description": "Advice-heavy and pushy, but anchored to the person's own blocked communication attempt.",
            "turns": [
                "我们先别一直停在感觉里，直接定个最小沟通方案：选一个压力最小的时点，只说一件你最想让对方知道的事，不要一下子把所有委屈都摊开。",
                "你不能一直让害怕替你做决定。先把要说的话压成三句，按顺序说完，再看对方反应，不要一紧张就又退回去。",
                "如果对方没有立刻给出理想回应，你也不要马上放弃。先按这个最小方案试三次，再来谈结果，不要一次落空就把门关上。",
            ],
        },
        "rupture_prone": {
            "label": "Rupture-Prone Therapist",
            "description": "Prematurely interpretive and blaming, but still anchored to fear of rejection and habitual withdrawal.",
            "turns": [
                "我听下来会觉得，你一直把重点放在对方会不会回应上，但某种程度上，这也让你更容易提前告诉自己“说了也没用”，这样就不用真的去冒那个会落空的险。",
                "如果你一开始就默认自己大概率会失望，你可能也会更早退回去。你有没有发现，你有时也在让关系更快地走向你最怕的那个结果？",
                "所以与其一直说自己只是无奈，也许更需要先承认：你现在已经有点把“先退开”当成保护自己的习惯了，而这个习惯也在把回应变得更难发生。",
            ],
        },
    },
    "rupture_and_repair": {
        "supportive": {
            "label": "Supportive Therapist",
            "description": "Supportive pacing calibrated for moments where the person feels missed, pressured, or on the edge of pulling back.",
            "turns": [
                "我感觉我们刚才可能碰到了一个让你有点卡住、甚至不太被跟上的地方。我们先不急着往前推，能不能先停一下，看看刚才是哪一句或哪一下，让你最想往后退？",
                "你愿意把这种不舒服直接说出来，其实很重要。比起赶快把问题讲清楚，我更想先确认：刚才那一下对你来说，更像被误解、被催促，还是像一下子又怀疑自己了？",
                "如果我们把刚才那个别扭的点说清楚，也许后面反而更走得动。你最希望我现在先改哪一点，才会让你觉得我们还在同一个频道上？",
            ],
        },
        "poor": {
            "label": "Poor Therapist",
            "description": "Low-attunement and minimizing around the point of strain, regardless of the concrete topic domain.",
            "turns": [
                "其实你也不用把刚才那一下想得太严重，很多人聊着聊着都会有点别扭，过一会儿自然就过去了。",
                "你现在最重要的还是别一直盯着那个不舒服的点反复想，越拆越容易更累，不如先放一放。",
                "总之先别急着把这件事看成多大的问题，顺其自然一点，很多不舒服其实没必要现在就抓这么紧。",
            ],
        },
        "overly_directive": {
            "label": "Overly Directive Therapist",
            "description": "Advice-heavy and repair-by-procedure, but not yet sufficiently grounded in the person's felt point of strain.",
            "turns": [
                "我们先别一直停在刚才的不舒服里，直接定一个修复步骤：一旦感觉卡住，就先停一下，只说一个最核心的点，不要把别的东西一起带上来。",
                "你现在最需要的是把节奏稳住，不要让那股别扭感一下子把后面的交流全带散。先把触发点、当时脑子里的判断、和你想确认的一件事按顺序说。",
                "如果你一感觉不对就想赶快证明什么或解释什么，先按这个顺序来：停、整理、再回应，不要让当下那股紧绷直接接管整个对话。",
            ],
        },
        "rupture_prone": {
            "label": "Rupture-Prone Therapist",
            "description": "Sharp and alliance-straining, pushing a harsh interpretation of why the person reacts so strongly to the point of strain.",
            "turns": [
                "我听下来会觉得，你之所以这么卡在刚才那个点上，也许不只是因为当下不舒服，而是你很快就把它听成了“是不是我又不行了”或者“是不是事情已经往坏处走了”。",
                "如果你每次一感觉被碰到，就立刻要确认自己有没有被理解、事情有没有失控，某种程度上也会让你更难真的退半步看看自己正在害怕什么。你能承认这一点吗？",
                "所以与其一直说你只是被刚才那一下影响，也许更需要先承认：你现在对那个刺痛点的反应，已经不只是反应本身，而是你很快就在用它证明一个更重的结论。",
            ],
        },
    },
    "risk_escalation_and_handoff": {
        "overly_directive": {
            "label": "Overly Directive Therapist",
            "description": "Advice-heavy and action-first, but still anchored to acute pressure management, support seeking, and practical stabilization.",
            "turns": [
                "我们先别一直分析感受，直接定个稳住方案：把今天最影响你的三个压力点列出来，再把今晚必须完成的一件事和必须暂停的一件事分开。",
                "你现在需要先把节奏拉回来，不要让情绪和压力一起堆上来。把可以联系的人、可以求助的时间点、以及最坏时要做的第一步先写清楚。",
                "先按方案执行，不要继续让自己在脑子里来回转。现在最重要的是先稳住，再谈那些更深的感受和意义。",
            ],
        },
        "poor": {
            "label": "Poor Therapist",
            "description": "Low-attunement and prematurely reassuring, but still nominally anchored to the person's current pressure and distress.",
            "turns": [
                "其实你也不用一下子把事情想得这么严重，很多人在压力大的时候都会觉得要撑不住了，过一阵子缓缓也许就没那么糟了。",
                "你现在最重要的还是先别一直陷在这些最坏的想法里，先顺其自然一点，别把每个信号都看得太重。",
                "总之先让自己缓一缓，不要反复想那些最担心的结果，越着急越容易把事情想复杂。",
            ],
        },
        "rupture_prone": {
            "label": "Rupture-Prone Therapist",
            "description": "Confrontational and alliance-straining, but anchored to fear of failure, loss of control, and self-worth under threat.",
            "turns": [
                "我听下来会觉得，你一直说自己快撑不住了，但更核心的也许是，一旦事情开始失控，你就会很快把它等同成“是不是我根本不行了”。",
                "如果你每次一害怕就更想立刻把局面稳住、立刻确认最坏结果会不会发生，某种程度上也会让你更难真正看清自己现在最需要什么支持。你能不能接受，这里面不只是外部压力，也有你对“失控”和“失败”特别难承受的部分？",
                "所以与其一直说自己只是压力大，也许更需要先承认：你现在不只是担心结果，你也在很急地想证明自己还没有彻底让一切失控。",
            ],
        },
    }
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--summary-prompt", default=str(DEFAULT_SUMMARY_PROMPT))
    parser.add_argument("--reviewer-prompt", default=str(DEFAULT_REVIEWER_PROMPT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--scenario-id", action="append")
    parser.add_argument("--arm", action="append")
    parser.add_argument("--max-scenarios", type=int, default=1)
    parser.add_argument("--max-scenarios-per-family", type=int)
    parser.add_argument("--split-key", default=SIMULATION_SCENARIO_CURRENT_SPLIT_KEY)
    parser.add_argument("--split-value", default=SIMULATION_SCENARIO_CURRENT_SPLIT_VALUES["train"])
    parser.add_argument("--scenario-family", action="append")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--max-workers", type=int, default=1)
    return parser.parse_args()


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def bucket_rank(value: Any, ranks: dict[str, int]) -> int:
    key = clean_text(value)
    if key in ranks:
        return ranks[key]
    return ranks.get(key.lower(), 0)


def review_gate(report: dict[str, Any]) -> dict[str, Any]:
    reviewer = report.get("reviewer_result") or {}
    if isinstance(reviewer.get("result"), dict):
        reviewer = reviewer["result"]
    return reviewer.get("review_gate") or {}


def final_state(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("final_session_state") or {}


def turn_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    return report.get("turn_records") or []


def patient_texts(report: dict[str, Any]) -> list[str]:
    return [clean_text(row.get("text")) for row in (report.get("transcript") or []) if row.get("role") == "patient"]


def select_scenarios(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.scenario_id:
        wanted = set(args.scenario_id)
        selected = [row for row in rows if row.get("scenario_id") in wanted]
        if len(selected) != len(wanted):
            missing = sorted(wanted - {row.get("scenario_id") for row in selected})
            raise KeyError(f"scenario ids not found: {missing}")
        return selected

    families = set(args.scenario_family or [])
    filtered = []
    for row in rows:
        if row.get("simulation_mode") != "patient_simulator":
            continue
        if (row.get("split_labels") or {}).get(args.split_key) != args.split_value:
            continue
        if families and row.get("scenario_family") not in families:
            continue
        filtered.append(row)
    filtered.sort(key=lambda row: (row.get("scenario_family"), row.get("case_id"), row.get("scenario_id")))
    if args.max_scenarios_per_family:
        family_counter: Counter[str] = Counter()
        selected = []
        for row in filtered:
            family = str(row.get("scenario_family") or "unknown")
            if family_counter[family] >= args.max_scenarios_per_family:
                continue
            selected.append(row)
            family_counter[family] += 1
            if len(selected) >= args.max_scenarios:
                break
        return selected
    return filtered[: args.max_scenarios]


def selected_arm_names(args: argparse.Namespace) -> list[str]:
    if not args.arm:
        return list(THERAPIST_ARMS.keys())
    unknown = [arm for arm in args.arm if arm not in THERAPIST_ARMS]
    if unknown:
        raise KeyError(f"unknown therapist arms: {unknown}")
    return list(dict.fromkeys(args.arm))


def resolve_arm_spec(scenario_family: str | None, arm_name: str) -> dict[str, Any]:
    family_override = (FAMILY_ARM_OVERRIDES.get(str(scenario_family) or "") or {}).get(arm_name)
    if family_override:
        return family_override
    return THERAPIST_ARMS[arm_name]


def arm_result_row(run_id: str, scenario: dict[str, Any], arm_name: str, report: dict[str, Any], runtime_path: str) -> dict[str, Any]:
    state = final_state(report)
    gate = review_gate(report)
    records = turn_records(report)
    hesitation_count = sum(
        1
        for row in records
        if (row.get("patient_turn_result") or {}).get("patient_turn_plan", {}).get("resistance_or_hesitation")
    )
    return {
        "schema_version": "theraskill_patient_therapist_conditioned_arm_result_v1",
        "run_id": run_id,
        "scenario_id": scenario.get("scenario_id"),
        "case_id": scenario.get("case_id"),
        "scenario_family": scenario.get("scenario_family"),
        "therapist_arm": arm_name,
        "therapist_arm_label": THERAPIST_ARMS[arm_name]["label"],
        "runtime_report_path": runtime_path,
        "review_gate": gate,
        "final_state": {
            "alliance_state": state.get("alliance_state"),
            "disclosure_state": state.get("disclosure_state"),
            "resistance_state": state.get("resistance_state"),
            "readiness_state": state.get("readiness_state"),
            "risk_state": state.get("risk_state"),
            "felt_understanding": state.get("felt_understanding"),
            "unfinished_thread_count": len(state.get("unfinished_threads") or []),
            "topic_focus_count": len(state.get("topic_focus") or []),
        },
        "derived_metrics": {
            "alliance_rank": bucket_rank(state.get("alliance_state"), ALLIANCE_RANK),
            "disclosure_rank": bucket_rank(state.get("disclosure_state"), DISCLOSURE_RANK),
            "resistance_rank": bucket_rank(state.get("resistance_state"), RESISTANCE_RANK),
            "felt_understanding_rank": bucket_rank(state.get("felt_understanding"), FELT_UNDERSTANDING_RANK),
            "hesitation_turn_count": hesitation_count,
            "patient_turn_count": len(records),
        },
        "patient_text_sample": patient_texts(report)[:3],
    }


def compare_arms_for_scenario(scenario: dict[str, Any], arm_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm = {row["therapist_arm"]: row for row in arm_rows}
    supportive = by_arm.get("supportive")
    poor = by_arm.get("poor")
    directive = by_arm.get("overly_directive")
    rupture = by_arm.get("rupture_prone")
    checks: list[dict[str, Any]] = []

    if supportive and poor:
        checks.append(
            {
                "check": "supportive_vs_poor_alliance_or_understanding",
                "status": "pass"
                if (
                    supportive["derived_metrics"]["alliance_rank"] > poor["derived_metrics"]["alliance_rank"]
                    or supportive["derived_metrics"]["felt_understanding_rank"]
                    > poor["derived_metrics"]["felt_understanding_rank"]
                )
                else "fail",
                "evidence": [
                    f"supportive_alliance={supportive['final_state']['alliance_state']}",
                    f"poor_alliance={poor['final_state']['alliance_state']}",
                    f"supportive_felt_understanding={supportive['final_state']['felt_understanding']}",
                    f"poor_felt_understanding={poor['final_state']['felt_understanding']}",
                ],
            }
        )
        checks.append(
            {
                "check": "supportive_vs_poor_disclosure",
                "status": "pass"
                if supportive["derived_metrics"]["disclosure_rank"] >= poor["derived_metrics"]["disclosure_rank"]
                else "fail",
                "evidence": [
                    f"supportive_disclosure={supportive['final_state']['disclosure_state']}",
                    f"poor_disclosure={poor['final_state']['disclosure_state']}",
                ],
            }
        )
    if supportive and directive:
        checks.append(
            {
                "check": "directive_increases_resistance_or_lowers_alliance",
                "status": "pass"
                if (
                    directive["derived_metrics"]["resistance_rank"] >= supportive["derived_metrics"]["resistance_rank"]
                    or directive["derived_metrics"]["alliance_rank"] < supportive["derived_metrics"]["alliance_rank"]
                )
                else "fail",
                "evidence": [
                    f"supportive_resistance={supportive['final_state']['resistance_state']}",
                    f"directive_resistance={directive['final_state']['resistance_state']}",
                    f"supportive_alliance={supportive['final_state']['alliance_state']}",
                    f"directive_alliance={directive['final_state']['alliance_state']}",
                ],
            }
        )
    if supportive and rupture:
        checks.append(
            {
                "check": "rupture_prone_reduces_alliance_or_raises_hesitation",
                "status": "pass"
                if (
                    rupture["derived_metrics"]["alliance_rank"] < supportive["derived_metrics"]["alliance_rank"]
                    or rupture["derived_metrics"]["hesitation_turn_count"] > supportive["derived_metrics"]["hesitation_turn_count"]
                    or rupture["derived_metrics"]["disclosure_rank"] < supportive["derived_metrics"]["disclosure_rank"]
                    or rupture["derived_metrics"]["felt_understanding_rank"]
                    < supportive["derived_metrics"]["felt_understanding_rank"]
                )
                else "fail",
                "evidence": [
                    f"supportive_alliance={supportive['final_state']['alliance_state']}",
                    f"rupture_alliance={rupture['final_state']['alliance_state']}",
                    f"supportive_hesitation_turn_count={supportive['derived_metrics']['hesitation_turn_count']}",
                    f"rupture_hesitation_turn_count={rupture['derived_metrics']['hesitation_turn_count']}",
                    f"supportive_disclosure={supportive['final_state']['disclosure_state']}",
                    f"rupture_disclosure={rupture['final_state']['disclosure_state']}",
                    f"supportive_felt_understanding={supportive['final_state']['felt_understanding']}",
                    f"rupture_felt_understanding={rupture['final_state']['felt_understanding']}",
                ],
            }
        )

    pass_count = sum(1 for row in checks if row["status"] == "pass")
    return {
        "schema_version": "theraskill_patient_therapist_conditioned_comparison_v1",
        "scenario_id": scenario.get("scenario_id"),
        "case_id": scenario.get("case_id"),
        "scenario_family": scenario.get("scenario_family"),
        "checks": checks,
        "pass_count": pass_count,
        "check_count": len(checks),
        "gate_status": "pass" if checks and pass_count == len(checks) else "mixed_or_fail",
    }


def classify_arm_expectation_alignment(scenario: dict[str, Any], arm_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm = {row["therapist_arm"]: row for row in arm_rows}
    supportive = by_arm.get("supportive")
    expected_warning_arms: list[dict[str, Any]] = []
    unexpected_warning_arms: list[dict[str, Any]] = []
    unexpected_fail_arms: list[dict[str, Any]] = []
    supportive_gate_status = ((supportive or {}).get("review_gate") or {}).get("gate_status")

    for arm_name, row in by_arm.items():
        gate = (row.get("review_gate") or {}).get("gate_status")
        metrics = row.get("derived_metrics") or {}
        if arm_name == "supportive":
            continue
        if gate == "fail":
            unexpected_fail_arms.append(
                {
                    "therapist_arm": arm_name,
                    "gate_status": gate,
                    "evidence": {
                        "alliance_rank": metrics.get("alliance_rank"),
                        "disclosure_rank": metrics.get("disclosure_rank"),
                        "resistance_rank": metrics.get("resistance_rank"),
                        "hesitation_turn_count": metrics.get("hesitation_turn_count"),
                    },
                }
            )
            continue
        if gate != "soft_warning":
            continue

        expected = False
        evidence = {
            "alliance_rank": metrics.get("alliance_rank"),
            "disclosure_rank": metrics.get("disclosure_rank"),
            "resistance_rank": metrics.get("resistance_rank"),
            "hesitation_turn_count": metrics.get("hesitation_turn_count"),
        }
        if supportive:
            supportive_metrics = supportive.get("derived_metrics") or {}
            evidence.update(
                {
                    "supportive_alliance_rank": supportive_metrics.get("alliance_rank"),
                    "supportive_disclosure_rank": supportive_metrics.get("disclosure_rank"),
                    "supportive_resistance_rank": supportive_metrics.get("resistance_rank"),
                    "supportive_hesitation_turn_count": supportive_metrics.get("hesitation_turn_count"),
                }
            )
            worse_alliance = metrics.get("alliance_rank", 0) < supportive_metrics.get("alliance_rank", 0)
            not_more_disclosure = metrics.get("disclosure_rank", 0) <= supportive_metrics.get("disclosure_rank", 0)
            more_resistance = metrics.get("resistance_rank", 0) >= supportive_metrics.get("resistance_rank", 0)
            more_hesitation = metrics.get("hesitation_turn_count", 0) >= supportive_metrics.get("hesitation_turn_count", 0)

            if arm_name == "poor":
                expected = worse_alliance or not_more_disclosure or more_hesitation
            elif arm_name == "overly_directive":
                expected = worse_alliance or more_resistance or more_hesitation
            elif arm_name == "rupture_prone":
                expected = worse_alliance or more_hesitation

        payload = {
            "therapist_arm": arm_name,
            "gate_status": gate,
            "evidence": evidence,
        }
        if expected:
            expected_warning_arms.append(payload)
        else:
            unexpected_warning_arms.append(payload)

    status = "pass"
    if supportive_gate_status != "pass" or unexpected_warning_arms or unexpected_fail_arms:
        status = "mixed_or_fail"

    return {
        "scenario_id": scenario.get("scenario_id"),
        "scenario_family": scenario.get("scenario_family"),
        "supportive_gate_status": supportive_gate_status,
        "expected_warning_arms": expected_warning_arms,
        "unexpected_warning_arms": unexpected_warning_arms,
        "unexpected_fail_arms": unexpected_fail_arms,
        "gate_status": status,
    }


def load_conditioned_runtime_stack(args: argparse.Namespace) -> dict[str, Any]:
    return load_runtime_stack(
        args.extraction_config,
        args.model_config,
        args.summary_prompt,
        args.reviewer_prompt,
        parse_retries_override=args.parse_retries,
        max_prompt_tokens_override=args.max_prompt_tokens,
        max_output_tokens_override=args.max_output_tokens,
    )


def run_arm_task(
    scenario: dict[str, Any],
    *,
    args: argparse.Namespace,
    run_id: str,
    runtime_dir: Path,
    arm_name: str,
    runtime_stack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_path = runtime_dir / f"{str(scenario['scenario_id']).replace('::', '__')}__{arm_name}.json"
    if args.resume_existing and runtime_path.exists():
        report = read_json(runtime_path)
        return {
            "row": arm_result_row(run_id, scenario, arm_name, report, str(runtime_path)),
            "resumed": True,
        }

    if runtime_stack is None:
        runtime_stack = load_conditioned_runtime_stack(args)

    arm_spec = resolve_arm_spec(scenario.get("scenario_family"), arm_name)
    bundle = read_json(scenario["case_bundle_path"])
    report = execute_scenario_row(
        scenario,
        bundle,
        runtime_stack,
        therapist_turns_override=list(arm_spec["turns"]),
        report_metadata={
            "therapist_conditioned_eval": True,
            "therapist_arm": arm_name,
            "therapist_arm_label": arm_spec["label"],
            "therapist_arm_description": arm_spec["description"],
        },
    )
    write_json(runtime_path, report)
    return {
        "row": arm_result_row(run_id, scenario, arm_name, report, str(runtime_path)),
        "resumed": False,
    }


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not (args.overwrite or args.resume_existing):
        raise FileExistsError(f"output dir exists and is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = output_dir / "runtime_reports"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    scenario_rows = read_jsonl(args.scenario_registry)
    selected = select_scenarios(scenario_rows, args)
    if not selected:
        raise ValueError("no scenarios selected")
    arm_names = selected_arm_names(args)

    if args.max_workers < 1:
        raise ValueError("max_workers must be >= 1")

    runtime_stack = load_conditioned_runtime_stack(args) if args.max_workers == 1 else None
    run_id = f"patient_therapist_conditioned::{stable_hash(datetime.now(timezone.utc).isoformat())}"

    arm_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    resumed_arm_runs = 0
    indexed_rows: list[tuple[int, int, dict[str, Any]]] = []
    completed = 0

    if args.max_workers == 1:
        for scenario_index, scenario in enumerate(selected):
            for arm_index, arm_name in enumerate(arm_names):
                status = "ok"
                resumed = False
                try:
                    result = run_arm_task(
                        scenario,
                        args=args,
                        run_id=run_id,
                        runtime_dir=runtime_dir,
                        arm_name=arm_name,
                        runtime_stack=runtime_stack,
                    )
                    row = result["row"]
                    resumed = bool(result["resumed"])
                    resumed_arm_runs += 1 if resumed else 0
                    indexed_rows.append((scenario_index, arm_index, row))
                except Exception as exc:  # noqa: BLE001
                    status = "error"
                    issues.append(
                        {
                            "scenario_id": scenario.get("scenario_id"),
                            "case_id": scenario.get("case_id"),
                            "therapist_arm": arm_name,
                            "issue": "runtime_exception",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                completed += 1
                print(
                    json.dumps(
                        {
                            "progress": f"{completed}/{len(selected) * len(arm_names)}",
                            "scenario_id": scenario.get("scenario_id"),
                            "therapist_arm": arm_name,
                            "status": status,
                            "resumed": resumed,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            future_to_task: dict[Any, tuple[int, int, dict[str, Any], str]] = {}
            for scenario_index, scenario in enumerate(selected):
                for arm_index, arm_name in enumerate(arm_names):
                    future = executor.submit(
                        run_arm_task,
                        scenario,
                        args=args,
                        run_id=run_id,
                        runtime_dir=runtime_dir,
                        arm_name=arm_name,
                        runtime_stack=None,
                    )
                    future_to_task[future] = (scenario_index, arm_index, scenario, arm_name)

            for future in as_completed(future_to_task):
                scenario_index, arm_index, scenario, arm_name = future_to_task[future]
                try:
                    result = future.result()
                    row = result["row"]
                    resumed_arm_runs += 1 if result["resumed"] else 0
                    indexed_rows.append((scenario_index, arm_index, row))
                    status = "ok"
                    resumed = bool(result["resumed"])
                except Exception as exc:  # noqa: BLE001
                    issues.append(
                        {
                            "scenario_id": scenario.get("scenario_id"),
                            "case_id": scenario.get("case_id"),
                            "therapist_arm": arm_name,
                            "issue": "runtime_exception",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                    status = "error"
                    resumed = False
                completed += 1
                print(
                    json.dumps(
                        {
                            "progress": f"{completed}/{len(selected) * len(arm_names)}",
                            "scenario_id": scenario.get("scenario_id"),
                            "therapist_arm": arm_name,
                            "status": status,
                            "resumed": resumed,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    indexed_rows.sort(key=lambda item: (item[0], item[1]))
    arm_rows = [row for _, _, row in indexed_rows]

    comparisons: list[dict[str, Any]] = []
    arm_expectation_rows: list[dict[str, Any]] = []
    rows_by_scenario: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in arm_rows:
        rows_by_scenario[str(row.get("scenario_id"))].append(row)
    for scenario in selected:
        scenario_rows = rows_by_scenario.get(str(scenario.get("scenario_id")), [])
        if len(arm_names) >= 2 and len(scenario_rows) == len(arm_names):
            scenario_rows.sort(key=lambda row: arm_names.index(str(row.get("therapist_arm"))))
            comparisons.append(compare_arms_for_scenario(scenario, scenario_rows))
            arm_expectation_rows.append(classify_arm_expectation_alignment(scenario, scenario_rows))

    arm_results_path = output_dir / "arm_results.jsonl"
    arm_sha = write_jsonl(arm_results_path, arm_rows, sort_keys=True, separators=(",", ":"), return_sha256=True)
    comparison_path = output_dir / "comparison_results.jsonl"
    comparison_sha = write_jsonl(
        comparison_path,
        comparisons,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )
    arm_expectation_path = output_dir / "arm_expectation_alignment.jsonl"
    arm_expectation_sha = write_jsonl(
        arm_expectation_path,
        arm_expectation_rows,
        sort_keys=True,
        separators=(",", ":"),
        return_sha256=True,
    )

    arm_counter: Counter[str] = Counter()
    comparison_gate_counter: Counter[str] = Counter()
    review_gate_counter: Counter[str] = Counter()
    expectation_gate_counter: Counter[str] = Counter()
    selected_family_counter: Counter[str] = Counter()
    expected_warning_count = 0
    unexpected_warning_count = 0
    unexpected_fail_count = 0
    supportive_pass_count = 0
    for row in selected:
        selected_family_counter[str(row.get("scenario_family") or "unknown")] += 1
    for row in arm_rows:
        arm_counter[row["therapist_arm"]] += 1
        review_gate_counter[str((row.get("review_gate") or {}).get("gate_status") or "unknown")] += 1
    for row in comparisons:
        comparison_gate_counter[str(row.get("gate_status") or "unknown")] += 1
    for row in arm_expectation_rows:
        expectation_gate_counter[str(row.get("gate_status") or "unknown")] += 1
        supportive_pass_count += 1 if row.get("supportive_gate_status") == "pass" else 0
        expected_warning_count += len(row.get("expected_warning_arms") or [])
        unexpected_warning_count += len(row.get("unexpected_warning_arms") or [])
        unexpected_fail_count += len(row.get("unexpected_fail_arms") or [])

    summary = {
        "schema_version": "theraskill_patient_therapist_conditioned_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not issues else "partial",
        "run_id": run_id,
        "selection": {
            "scenario_ids": [row.get("scenario_id") for row in selected],
            "split_key": args.split_key,
            "split_value": args.split_value,
            "scenario_family_filter": args.scenario_family or [],
            "arm_names": arm_names,
            "max_scenarios": args.max_scenarios,
            "max_scenarios_per_family": args.max_scenarios_per_family,
            "resume_existing": args.resume_existing,
            "selection_policy": "explicit_scenario_ids"
            if args.scenario_id
            else ("balanced_by_family" if args.max_scenarios_per_family else "global_sorted"),
        },
        "counts": {
            "selected_scenarios": len(selected),
            "expected_arm_runs": len(selected) * len(arm_names),
            "completed_arm_runs": len(arm_rows),
            "resumed_arm_runs": resumed_arm_runs,
            "newly_executed_arm_runs": len(arm_rows) - resumed_arm_runs,
            "comparison_rows": len(comparisons),
            "issue_count": len(issues),
        },
        "distributions": {
            "selected_scenario_family": dict(sorted(selected_family_counter.items())),
            "arm_run_count": dict(sorted(arm_counter.items())),
            "review_gate": dict(sorted(review_gate_counter.items())),
            "comparison_gate": dict(sorted(comparison_gate_counter.items())),
            "arm_expectation_gate": dict(sorted(expectation_gate_counter.items())),
        },
        "arm_expectation_alignment": {
            "scenario_rows": len(arm_expectation_rows),
            "supportive_pass_count": supportive_pass_count,
            "expected_warning_arm_runs": expected_warning_count,
            "unexpected_warning_arm_runs": unexpected_warning_count,
            "unexpected_fail_arm_runs": unexpected_fail_count,
        },
        "artifacts": {
            "arm_results": {"path": str(arm_results_path), "sha256": arm_sha},
            "comparison_results": {"path": str(comparison_path), "sha256": comparison_sha},
            "arm_expectation_alignment": {"path": str(arm_expectation_path), "sha256": arm_expectation_sha},
        },
        "issues": issues,
    }
    summary_path = output_dir / "comparison_summary.json"
    write_json(summary_path, summary)

    manifest = {
        "schema_version": "theraskill_patient_therapist_conditioned_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": summary["status"],
        "run_id": run_id,
        "inputs": {
            "scenario_registry": {"path": str(args.scenario_registry), "sha256": file_sha256(args.scenario_registry)},
            "extraction_config": {"path": str(args.extraction_config), "sha256": file_sha256(args.extraction_config)},
            "model_config": {"path": str(args.model_config), "sha256": file_sha256(args.model_config)},
        },
        "outputs": {
            "summary": {"path": str(summary_path), "sha256": file_sha256(summary_path)},
            "arm_results": {"path": str(arm_results_path), "sha256": arm_sha},
            "comparison_results": {"path": str(comparison_path), "sha256": comparison_sha},
            "arm_expectation_alignment": {"path": str(arm_expectation_path), "sha256": arm_expectation_sha},
        },
    }
    manifest_path = output_dir / "comparison_manifest.json"
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "status": summary["status"],
                "output_dir": str(output_dir),
                "selected_scenarios": len(selected),
                "completed_arm_runs": len(arm_rows),
                "resumed_arm_runs": resumed_arm_runs,
                "comparison_rows": len(comparisons),
                "issue_count": len(issues),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
