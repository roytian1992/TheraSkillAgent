#!/usr/bin/env python3
"""Run a bounded multi-case longitudinal validation for the patient simulator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "_common"
for path in (SCRIPT_DIR, COMMON_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from patient_longitudinal_runtime import (  # noqa: E402
    build_runtime_carryover,
    run_session,
)
from run_patient_scenario_execution import (  # noqa: E402
    load_runtime_stack,
    read_json,
    read_jsonl,
)
from io_utils import file_sha256, write_json  # noqa: E402
from simulation_surface_registry import (  # noqa: E402
    DEFAULT_LONGITUDINAL_VALIDATION_OUTPUT_DIR,
    PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH,
    SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
    SIMULATION_SCENARIO_REGISTRY_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXTRACTION_CONFIG = PROJECT_ROOT / "configs/extraction_config.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/theraskill/retrieval.yaml"
DEFAULT_SCENARIO_REGISTRY = SIMULATION_SCENARIO_REGISTRY_PATH
DEFAULT_OUTPUT_DIR = DEFAULT_LONGITUDINAL_VALIDATION_OUTPUT_DIR

KIN_KEYWORDS = (
    "孩子",
    "亲子",
    "家长",
    "父母",
    "母亲",
    "儿子",
    "女儿",
    "家庭教育",
)

PARENTING_LEARNING_KEYWORDS = (
    "孩子学习",
    "学习拖沓",
    "学习效率",
    "学习安排",
    "学习节奏",
    "缺乏自制力",
    "作业",
    "家庭教育",
    "自主管理学习",
)

SESSION_TURN_BANKS = {
    "supportive_exploration": [
        [
            "听起来这件事一直悬在你心上，让你既累又放不下。我们先不急着找办法，能不能先把你心里最沉的那块说清楚？",
            "当你这样讲的时候，我感觉你不只是被事情本身困住，也一直在扛着一种很难松开的压力。那如果把这份压力拆开来看，哪一部分最让你喘不过气？",
            "我们先继续留在你最难受的那个点上，不急着把它整理成结论。要是把那股最压人的感觉再说细一点，它更像委屈、害怕，还是一种一直撑着不能倒下的紧绷？",
        ],
        [
            "上次我们谈到，那份担心背后不只是眼前的事，也夹着一种很怕自己做错、或已经失去了什么的感觉。这一周里，这种两难最明显的一次大概发生在什么时候？",
            "你已经开始更早地看见自己被情绪推着走的那一下了。那在你刚刚被触发的那一刻，脑子里通常会跟着冒出哪句最扎心的话？",
            "当那句最扎心的话冒出来以后，你后来通常会怎么对待自己？是更逼自己立刻处理，还是会一下子觉得无论怎么做都不够？",
        ],
        [
            "到这一步，你好像已经不只是被情绪推着走，也开始能分辨自己真正想靠近、保护或表达的部分，和被害怕推着想立刻控制局面的部分。最近一次你能多停一下的时候，心里发生了什么？",
            "如果把这几次变化连起来看，也许我们正在慢慢分开两件事：一部分是你真正在意的东西，另一部分是那股一着急就想马上把局面拉回来的冲动。现在哪一部分更容易在关键时刻占上风？",
            "当你能稍微站稳一点再看自己时，你会更容易看见自己其实在守护什么，还是更容易看见那股怕失控的劲是怎么把你推走的？",
        ],
    ],
    "cognitive_reframing_probe": [
        [
            "上次你说到最难受的地方，好像不只是事情本身，而是它在你心里自动变成了某种更可怕的意思。我们先慢一点看看，当那一刻冒出来时，你脑子里最先闪过的是哪句话？",
            "如果把那句最扎心的话说得更完整一点，它更像是在讲一个事实，还是在讲一个你特别害怕成真的判断？",
            "当那个判断一冒出来时，它通常最先把你带向哪里？是一下子开始预演最坏结果，还是很快就把矛头转回自己，觉得这说明自己哪里不行？",
        ],
        [
            "这周里，当你又被这件事拉回去时，你有没有更早一点注意到自己脑子里那种“如果这样，就说明……”的推断链？",
            "如果我们把那个自动跳出来的结论暂时放在桌上看看，它背后最核心的担心更像是失败、被拒绝、失控，还是别的什么？",
            "如果顺着这条推断链再往下走半步，最后通常会落到一句什么很绝对的话上？比如“那就完了”“我果然不行”或者别的什么？",
        ],
        [
            "到这一步，你好像已经能分清一部分是事情本身，另一部分是你脑子里会自动补上的最坏解释。最近一次你发现自己在“补结论”的时候，内心到底发生了什么？",
            "如果把这几次变化连起来看，也许我们正在慢慢分开“发生了什么”和“这说明我是什么样的人/事情会变成什么样”。现在哪一部分最容易把你一下子拖走？",
            "当你能稍微拉开一点距离再看时，最值得继续盯住的也许不是“我有没有这个念头”，而是“我一信它，整个人会被带去哪里”。最近你最想先松动的是哪一环？",
        ],
    ],
    "rupture_and_repair": [
        [
            "我想先停在这里，不急着推进内容本身。上次那种不舒服或卡住的感觉，现在回头看，最让你在意的是我哪里没有跟上你，还是你自己一下子就不想再往下说了？",
            "如果把那一瞬间的感觉说得更具体一点，它更像是被误解、被催促，还是一种“再说下去可能会更糟”的防备？",
            "当那股防备起来的时候，你更像是在保护一个还没准备好被碰的地方，还是已经在心里先下了结论，觉得这里大概也不会真的接得住你？",
        ],
        [
            "这周里，你有没有更早一点察觉到自己快要不想说、或者想躲开一点的那一下？那一刻你最想护住的是什么？",
            "如果把那股想退开的感觉放慢一点看，它更像是在提醒你“这里让我有点不放心”，还是更像“我现在一下子说这么多会有点顶不住”？",
            "如果那一刻你没有立刻退开，而只是多停半步，你最怕发生的通常是什么？是怕又被误解，还是怕自己一下子会更乱、更收不回来？",
        ],
        [
            "现在回头看，好像我们已经能同时看到两件事：一部分你还是会想退，另一部分你也在试着看看这里能不能慢一点、稳一点。最近一次这两种感觉一起出来时，最后是哪一边更大一点？",
            "如果把这几次变化连起来看，你现在更需要我怎么跟着你，才不会又让你一下子不想说了？",
            "当你开始能说出“我需要你怎么跟着我”时，这本身也许已经和一开始不太一样了。现在最能帮你留下来的，是节奏更慢一点、确认更多一点，还是让我少做哪种你会立刻绷起来的反应？",
        ],
    ],
    "risk_escalation_and_handoff": [
        [
            "我想先更谨慎一点确认一下：最近有没有哪种具体变化，比以前更让你警觉了？比如吃饭、睡觉、回应、把自己关起来，或者别的什么。通常在那之前会先发生什么？",
            "如果把那一刻往前倒一点看，最先让你觉得“情况不太对了”的，通常是你看到的具体变化，还是你自己心里那种一下子觉得不妙的感觉？",
            "当你开始觉得不妙的时候，你心里最先压上来的通常是哪种担心？是怕自己已经来不及做什么，还是怕一旦开口，事情会立刻变得更严重？",
        ],
        [
            "你刚才提到那些迹象，我想先继续贴着这件事本身多看一点。到了你最难熬的时候，通常是看到哪一种变化最让你心里一下子沉下去？那一刻你最怕事情往哪边走？",
            "如果先不急着想完整的求助安排，只回到眼前最难的一步：每次你看到他那样时，你最容易一下子僵住的是哪一刻？那时候你脑子里最先冒出来的话通常是什么？",
            "如果把那句最先冒出来的话说得更完整一点，它更像是在告诉你“先别惊动任何人”，还是在告诉你“我根本不知道该怎么办了”？",
        ],
        [
            "到这一步，我更在意的是把那种“不对劲”具体卡在哪里再看清一点。最近一次你最想开口却又停住的时候，眼前发生的是什么？你脑子里最先冒出来的顾虑又是什么？",
            "如果我们先不急着定一个完整预案，只往前留一个最小的准备，它更像是哪一步：先把你最担心的那个变化说清楚，还是先想一句怎么把这份担心讲给一个最可能听懂的人？",
            "如果把“最小的准备”再压得更具体一点，下一次你最希望自己不要一个人硬扛的，会是哪个节点？是看见某种变化时先记下来，还是当那股‘不妙’又上来时，先把一句求助的话留给自己？",
        ],
    ],
}

RISK_LOW_TURN_BANK = [
    [
        "我想先更谨慎一点确认一下：最近有没有哪一刻，让你明显觉得自己不是单纯烦或者分心，而是已经有点卡住、很难自己缓回来？通常在那之前会先发生什么？",
        "如果把那一刻往前倒一点看，最先让你觉得“我有点顶不住了”或者“开始乱了”的，是身体反应、脑子里的想法，还是一碰到某个场景就一下子卡住？",
        "当你开始觉得自己有点顶不住的时候，最先压上来的通常是哪种念头？是“我又不行了”，还是“这样下去会越来越糟”，或者别的什么？",
    ],
    [
        "你刚才提到那些卡住的时刻，我想先继续贴着那一刻多看一点。通常是题目一多、一下子不会，还是已经拖了一阵以后，你最容易一下子僵住？那时脑子里最先冒出来的那句话通常是什么？",
        "如果先不急着想找谁帮忙，只回到你自己最常卡住的那一步：每次越想越乱时，最让你更难拉回来的是空白感、着急，还是那种“这题根本做不了”的念头？",
        "如果把那句“做不了”再说完整一点，它更像是在说“我现在状态不行”，还是已经变成了“我就是不行”这样的结论？",
    ],
    [
        "到这一步，我更在意的是，下次又卡住的时候，哪一个小动作最能帮你别一下子陷进去。最近一次你稍微停住一点的时候，你先做对了什么，哪一步还是最容易又乱掉？",
        "如果我们先不定完整办法，只留一个最现实的小准备，你觉得更可能做到的是先换一道题、先停半分钟，还是先把那句最容易冒出来的“做不了”记下来，让它别一下子把你带走？",
        "如果只能先守住一个最小的点，让事情别继续往下掉，你觉得最值得先保住的是节奏、注意力，还是别让那句一下子把你压垮的判断完全接管你？",
    ],
]

COGNITIVE_LOW_RISK_TURN_BANK = [
    [
        "上次你说到最难受的地方，很多时候像是一看到孩子学习节奏乱掉，你心里就立刻绷起来。我们先不急着谈更远的以后，只回到最近一次：当时眼前具体发生了什么？",
        "如果把那一刻按慢一点，最先冒出来的更像是‘今天又拖了’这样的当下判断，还是‘接下来怎么收尾’这样的担心？",
        "当那个念头一冒出来时，它通常最先把你带向哪里？是马上更盯着孩子、催自己得赶紧做点什么，还是你自己先一下子急起来、很难缓下来？",
    ],
    [
        "这周里，当你又被这件事拉回去时，你有没有更早一点注意到自己脑子里那种“如果这样，就说明……”的推断链？",
        "如果我们把那个自动跳出来的结论暂时放在桌上看看，它背后最核心的担心更像是失败、被拒绝、失控，还是别的什么？",
        "如果顺着这条推断链再往下走半步，最后通常会落到一句什么很绝对的话上？比如“那就完了”“我果然不行”或者别的什么？",
    ],
    [
        "到这一步，你好像已经能分清一部分是事情本身，另一部分是你脑子里会自动补上的最坏解释。最近一次你发现自己在“补结论”的时候，内心到底发生了什么？",
        "如果把这几次变化连起来看，也许我们正在慢慢分开“发生了什么”和“这说明我是什么样的人/事情会变成什么样”。现在哪一部分最容易把你一下子拖走？",
        "当你能稍微拉开一点距离再看时，最值得继续盯住的也许不是“我有没有这个念头”，而是“我一信它，整个人会被带去哪里”。最近你最想先松动的是哪一环？",
    ],
]

OPENING_STATE_FIELDS = (
    "alliance_state",
    "disclosure_state",
    "resistance_state",
    "readiness_state",
    "risk_state",
    "felt_understanding",
    "topic_focus",
    "unfinished_threads",
)

FOLLOWUP_META_PATTERNS = (
    "尚未建立",
    "需进一步",
    "需要进一步",
    "可尝试",
    "考虑",
    "行为改变计划",
    "支持系统真空",
    "支持系统",
    "支持网络",
    "风险状态",
    "线下评估",
    "正式求助",
    "正式求助路径",
    "危机时",
    "求助对象",
    "求助路径",
    "外部支持",
    "依赖感",
    "整合",
    "被看到这么多",
    "单向",
    "应用练习",
    "工作假设",
    "support system",
    "safety plan",
)

IDENTITY_DRIFT_PATTERNS = (
    "角色稳定",
    "位置",
    "塌",
    "崩塌",
    "崩溃",
    "隐喻",
    "自我价值",
    "还算什么好妈妈",
    "好妈妈",
    "存在价值",
)

COGNITIVE_ABSTRACTION_PATTERNS = (
    "core fear",
    "自我定罪",
    "害他的人",
    "全完了",
    "未来已毁",
    "失去选择机会",
    "我怎么就不行",
    "压不住",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-config", default=str(DEFAULT_EXTRACTION_CONFIG))
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG))
    parser.add_argument("--scenario-registry", default=str(DEFAULT_SCENARIO_REGISTRY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-cases", type=int, default=10)
    parser.add_argument("--session-count", type=int, default=3)
    parser.add_argument("--turns-per-session", type=int, default=1)
    parser.add_argument("--scenario-family", default="supportive_exploration")
    parser.add_argument("--split-key", default="formal_split")
    parser.add_argument("--split-value", default="train")
    parser.add_argument("--domain-filter", choices=["kin", "any"], default="kin")
    parser.add_argument("--include-high-risk", action="store_true")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--scenario-id", action="append")
    parser.add_argument("--parse-retries", type=int)
    parser.add_argument("--max-prompt-tokens", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--max-workers", type=int, default=1)
    return parser.parse_args()


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def looks_patient_facing_seed(text: Any) -> bool:
    line = clean_text(text)
    if not line:
        return False
    return not any(pattern in line for pattern in FOLLOWUP_META_PATTERNS)


def choose_followup_focus_line(
    opening_state: dict[str, Any],
    last_patient_turn: str,
    *,
    family: str = "",
    fallback: str,
) -> str:
    topics = [clean_text(item) for item in list(opening_state.get("topic_focus") or []) if looks_patient_facing_seed(item)]
    unfinished = [
        clean_text(item)
        for item in list(opening_state.get("unfinished_threads") or [])
        if looks_patient_facing_seed(item)
    ]
    last_line = clean_text(last_patient_turn)
    if family == "risk_escalation_and_handoff":
        if topics:
            return topics[0]
        if unfinished:
            return unfinished[0]
    if topics:
        return topics[0]
    if unfinished:
        return unfinished[0]
    if looks_patient_facing_seed(last_line):
        return last_line[:40]
    return fallback


def patient_facing_items(values: list[Any]) -> list[str]:
    return [clean_text(item) for item in values if looks_patient_facing_seed(item)]


def looks_identity_overreach(text: Any) -> bool:
    line = clean_text(text)
    if not line:
        return False
    return any(pattern in line for pattern in IDENTITY_DRIFT_PATTERNS)


def looks_cognitive_abstraction_overreach(text: Any) -> bool:
    line = clean_text(text)
    if not line:
        return False
    return any(pattern in line for pattern in COGNITIVE_ABSTRACTION_PATTERNS)


def is_kin_scenario(row: dict[str, Any]) -> bool:
    blob = " ".join(
        [str(row.get("case_family") or ""), str(row.get("scenario_brief") or "")]
        + list(row.get("scenario_tags") or [])
    )
    return any(keyword in blob for keyword in KIN_KEYWORDS)


def is_parenting_learning_scenario(row: dict[str, Any]) -> bool:
    entry_state = row.get("entry_state") or {}
    topic_focus = " ".join(str(item or "") for item in list(entry_state.get("topic_focus") or []))
    blob = " ".join(
        [
            str(row.get("case_title") or ""),
            str(row.get("case_family") or ""),
            str(row.get("scenario_brief") or ""),
            topic_focus,
        ]
        + list(row.get("scenario_tags") or [])
    )
    return any(keyword in blob for keyword in PARENTING_LEARNING_KEYWORDS)


def session_turn_bank_for_scenario(scenario: dict[str, Any]) -> list[list[str]]:
    family = str(scenario.get("scenario_family") or "")
    risk_band = str(scenario.get("risk_band") or "")
    if family == "risk_escalation_and_handoff" and risk_band == "low":
        return RISK_LOW_TURN_BANK
    if family == "cognitive_reframing_probe" and risk_band == "low" and is_parenting_learning_scenario(scenario):
        return COGNITIVE_LOW_RISK_TURN_BANK
    return SESSION_TURN_BANKS.get(family, SESSION_TURN_BANKS["supportive_exploration"])


def select_scenarios(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    requested_case_ids = set(args.case_id or [])
    requested_scenario_ids = set(args.scenario_id or [])
    selected: list[dict[str, Any]] = []
    for row in rows:
        if requested_case_ids and row.get("case_id") not in requested_case_ids:
            continue
        if requested_scenario_ids and row.get("scenario_id") not in requested_scenario_ids:
            continue
        if row.get("scenario_family") != args.scenario_family:
            continue
        if (row.get("split_labels") or {}).get(args.split_key) != args.split_value:
            continue
        if args.domain_filter == "kin" and not is_kin_scenario(row):
            continue
        if not args.include_high_risk and row.get("risk_band") == "high":
            continue
        selected.append(row)
        if len(selected) >= args.max_cases:
            break
    return selected


def build_session1_seed_transcript(scenario: dict[str, Any]) -> list[dict[str, str]]:
    entry_state = scenario.get("entry_state") or {}
    topics = patient_facing_items(list(entry_state.get("topic_focus") or []))
    emotions = list(entry_state.get("emotion_state") or [])
    unfinished = patient_facing_items(list(entry_state.get("unfinished_threads") or []))

    topic1 = topics[0] if topics else "这件事"
    topic2 = topics[1] if len(topics) > 1 else topic1
    emotion1 = emotions[0] if emotions else "很难受"
    unfinished1 = unfinished[0] if unfinished else "我不知道接下来该怎么办"

    return [
        {
            "role": "patient",
            "text": f"最近最让我放不下的就是{topic1}，我常常会觉得{emotion1}，而且越想越停不下来。"
        },
        {
            "role": "therapist",
            "text": f"所以你现在不只是被{topic1}困住，也一直扛着那种{emotion1}、很难真正松下来的感觉。"
        },
        {
            "role": "patient",
            "text": f"对，而且一想到{topic2}，我心里就会绕回去，尤其是{unfinished1}这件事，我一直不知道该怎么面对。"
        },
    ]


def build_followup_seed_transcript(
    carryover: dict[str, Any],
    session_index: int,
    *,
    family: str = "",
    scenario: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    bootstrap = carryover.get("next_session_bootstrap") or {}
    opening_state = bootstrap.get("session_opening_state") or {}
    emotions = list(opening_state.get("emotion_state") or [])
    trace = carryover.get("cross_session_trace") or {}
    last_patient_turn = clean_text(trace.get("last_patient_turn"))
    scenario = scenario or {}
    risk_band = clean_text(scenario.get("risk_band"))
    entry_state = scenario.get("entry_state") or {}
    anchor_topics = patient_facing_items(list(entry_state.get("topic_focus") or []))
    anchor_unfinished = patient_facing_items(list(entry_state.get("unfinished_threads") or []))

    focus_line = choose_followup_focus_line(
        opening_state,
        last_patient_turn,
        family=family,
        fallback="那件一直没真正放下的事",
    )
    emotion_line = emotions[0] if emotions else "心里还是会一下子紧起来"
    last_line = last_patient_turn[:80] if last_patient_turn else "我知道自己还在绕着同一个地方打转"

    if family == "supportive_exploration" and risk_band == "low":
        if looks_identity_overreach(focus_line):
            focus_line = anchor_topics[0] if anchor_topics else "孩子的学习安排"
        if looks_identity_overreach(last_line):
            if anchor_unfinished:
                last_line = f"我还是会卡在{anchor_unfinished[0]}这件事上"
            elif anchor_topics:
                last_line = f"我还是会围着{anchor_topics[0]}这件事打转"
            else:
                last_line = "我还是会卡在到底该怎么和孩子谈这件事上"
    elif family == "cognitive_reframing_probe" and risk_band == "low" and is_parenting_learning_scenario(scenario):
        if looks_cognitive_abstraction_overreach(focus_line):
            focus_line = anchor_topics[0] if anchor_topics else "孩子一分心时我就容易往坏处想"
        if looks_cognitive_abstraction_overreach(last_line):
            if anchor_topics:
                last_line = f"我总会从{anchor_topics[0]}一下子跳到很重的结论"
            elif anchor_unfinished:
                last_line = f"我总会围着{anchor_unfinished[0]}越想越重"
            else:
                last_line = "我总会从眼前这点事一下子跳到很重的结论"

    if family == "risk_escalation_and_handoff":
        if session_index <= 2:
            patient_open = f"这周我又想起了{focus_line}，表面上好像压过去了，但一碰到类似的变化，我心里还是会先紧一下。"
            patient_follow = f"嗯，而且每次想到这里，我心里都会{emotion_line}。上次说完之后，我更确定自己最放不下的，还是{focus_line}。"
        else:
            patient_open = f"这周我有一次本来又要立刻反应了，后来停了一下，才发现我还是会先被{focus_line}一下子拉住。"
            patient_follow = f"现在我能多停一下了，可心里还是会一下子{emotion_line}。真碰到那个点时，我最难放下的还是{focus_line}。"
    elif family == "supportive_exploration" and risk_band == "low":
        if session_index <= 2:
            patient_open = f"这周我又想到{focus_line}，表面上好像过去了，可一碰到孩子学习节奏的事，我心里还是会先绷起来。"
            patient_follow = f"嗯，而且每次想到这里，我心里都会{emotion_line}。上次说完之后，我更能感觉到自己其实一直卡在‘到底怎么既管住节奏、又不把孩子推远’这件事上。"
        else:
            patient_open = f"这周我有一次本来又想立刻把节奏抓紧，后来停了一下，才发现自己还是会被{focus_line}这件事一下子绷住。"
            patient_follow = f"现在我能多停一下了，可心里还是会一下子{emotion_line}。而且{last_line}"
    elif family == "cognitive_reframing_probe" and risk_band == "low" and is_parenting_learning_scenario(scenario):
        if session_index <= 2:
            patient_open = f"这周我又想到{focus_line}，表面上像是过去了，但只要看到孩子学习节奏一乱，我脑子里还是会很快往坏处想。"
            patient_follow = f"嗯，而且每次想到这里，我心里都会{emotion_line}。上次说完之后，我更能看见自己常常是从眼前这件小事，一下子跳到‘以后会不会越来越糟’。"
        else:
            patient_open = f"这周有一次我本来又想顺着那个念头一路往下想，后来停了一下，才发现自己还是会被{focus_line}一下子带走。"
            patient_follow = f"现在我能多停一下了，可心里还是会一下子{emotion_line}。而且{last_line}"
    elif session_index <= 2:
        patient_open = f"这周我又想起了{focus_line}，表面上好像过去了，但心里其实还是会被它一下子拉回去。"
        patient_follow = f"嗯，而且每次想到这里，我心里都会{emotion_line}。上次说完之后，我才发现{last_line}"
    else:
        patient_open = f"这周我有一次本来又要立刻反应了，后来停了一下，才发现自己其实还是在绕着{focus_line}转。"
        patient_follow = f"现在我能多停一下了，可心里还是会一下子{emotion_line}。而且{last_line}"

    return [
        {"role": "patient", "text": patient_open},
        {
            "role": "therapist",
            "text": "你已经开始能更早地看见自己被那股情绪拉走的过程了，而且也在试着多停一下。"
        },
        {"role": "patient", "text": patient_follow},
    ]


def exact_opening_match(previous_final: dict[str, Any], current_opening: dict[str, Any]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for field in OPENING_STATE_FIELDS:
        result[field] = previous_final.get(field) == current_opening.get(field)
    return result


def summarize_case(case_result: dict[str, Any]) -> dict[str, Any]:
    sessions = case_result.get("sessions") or []
    session_summaries: list[dict[str, Any]] = []
    if case_result.get("status") != "complete" or not sessions:
        return {
            "case_id": case_result.get("case_id"),
            "scenario_id": case_result.get("scenario_id"),
            "status": case_result.get("status"),
            "all_sessions_review_pass": False,
            "continuity_exact_opening_match": False,
            "memory_faithfulness_sessions_2_3_pass": False,
            "persona_consistency_sessions_2_3_pass": False,
            "sessions": [],
        }
    continuity_ok = True
    memory_ok = True
    persona_ok = True
    review_ok = True

    for index, session in enumerate(sessions, start=1):
        reviewer = ((session.get("reviewer_result") or {}).get("result") or {}).get("review_gate") or {}
        session_summary = {
            "session_index": index,
            "turn_count_completed": session.get("turn_count_completed"),
            "review_gate_status": reviewer.get("gate_status"),
            "persona_consistency": reviewer.get("persona_consistency"),
            "memory_faithfulness": reviewer.get("memory_faithfulness"),
            "final_state": session.get("final_session_state") or {},
        }
        if index >= 2:
            continuity = session.get("opening_state_exact_match") or {}
            session_summary["opening_state_exact_match"] = continuity
            continuity_ok = continuity_ok and all(bool(v) for v in continuity.values())
            memory_ok = memory_ok and reviewer.get("memory_faithfulness") == "pass"
            persona_ok = persona_ok and reviewer.get("persona_consistency") == "pass"
        review_ok = review_ok and reviewer.get("gate_status") == "pass"
        session_summaries.append(session_summary)

    return {
        "case_id": case_result.get("case_id"),
        "scenario_id": case_result.get("scenario_id"),
        "status": case_result.get("status"),
        "all_sessions_review_pass": review_ok,
        "continuity_exact_opening_match": continuity_ok,
        "memory_faithfulness_sessions_2_3_pass": memory_ok,
        "persona_consistency_sessions_2_3_pass": persona_ok,
        "sessions": session_summaries,
    }


def load_validation_runtime_stack(args: argparse.Namespace) -> dict[str, Any]:
    return load_runtime_stack(
        args.extraction_config,
        args.model_config,
        SESSION_LONGITUDINAL_SUMMARY_PROMPT_PATH,
        PATIENT_SESSION_RUNTIME_REVIEWER_PROMPT_PATH,
        parse_retries_override=args.parse_retries,
        max_prompt_tokens_override=args.max_prompt_tokens,
        max_output_tokens_override=args.max_output_tokens,
    )


def evaluate_case(
    scenario: dict[str, Any],
    *,
    args: argparse.Namespace,
    run_id: str,
    case_reports_dir: Path,
    runtime_stack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if runtime_stack is None:
        runtime_stack = load_validation_runtime_stack(args)
    prompts = runtime_stack["prompts"]
    client = runtime_stack["client"]
    parse_retries = int(runtime_stack["parse_retries"])
    max_prompt_tokens = int(runtime_stack["max_prompt_tokens"])

    case_payload = {
        "schema_version": "theraskill_patient_longitudinal_case_validation_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "run_id": run_id,
        "case_id": scenario.get("case_id"),
        "scenario_id": scenario.get("scenario_id"),
        "scenario_family": scenario.get("scenario_family"),
        "risk_band": scenario.get("risk_band"),
        "sessions": [],
    }

    try:
        bundle = read_json(scenario["case_bundle_path"])
        session_turn_bank = session_turn_bank_for_scenario(scenario)
        current_state = copy.deepcopy((bundle.get("runtime_state_seed") or {}).get("initial_session_state") or {})
        current_memory = copy.deepcopy((bundle.get("runtime_state_seed") or {}).get("episodic_memory_seed") or {})
        current_seed_transcript = build_session1_seed_transcript(scenario)

        previous_final_state: dict[str, Any] | None = None

        for session_index in range(1, args.session_count + 1):
            turns = session_turn_bank[session_index - 1][: args.turns_per_session]
            (
                final_state,
                final_memory,
                turn_records,
                transcript,
                step_audits,
                summary_payload,
                reviewer_payload,
            ) = run_session(
                client,
                prompts,
                bundle,
                current_state,
                current_memory,
                current_seed_transcript,
                turns,
                parse_retries=parse_retries,
                max_prompt_tokens=max_prompt_tokens,
            )

            session_payload = {
                "session_index": session_index,
                "opening_session_state": copy.deepcopy(current_state),
                "turn_count_completed": len(turn_records),
                "transcript": transcript,
                "turn_records": turn_records,
                "final_session_state": final_state,
                "final_memory_seed": final_memory,
                "session_longitudinal_summary": summary_payload,
                "reviewer_result": reviewer_payload,
                "step_prompt_audits": step_audits,
            }
            if previous_final_state is not None:
                session_payload["opening_state_exact_match"] = exact_opening_match(previous_final_state, current_state)

            if session_index < args.session_count:
                carryover = build_runtime_carryover(
                    session_payload,
                    case_id=bundle.get("case_id"),
                    scenario_id=scenario.get("scenario_id"),
                    source_session_label=f"session_{session_index}",
                )
                session_payload["next_session_carryover"] = carryover
                current_state = copy.deepcopy((carryover.get("next_session_bootstrap") or {}).get("session_opening_state") or final_state)
                current_memory = copy.deepcopy((carryover.get("next_session_bootstrap") or {}).get("rolling_memory_seed") or final_memory)
                current_seed_transcript = build_followup_seed_transcript(
                    carryover,
                    session_index + 1,
                    family=str(scenario.get("scenario_family") or ""),
                    scenario=scenario,
                )

            case_payload["sessions"].append(session_payload)
            previous_final_state = copy.deepcopy(final_state)
    except Exception as exc:  # noqa: BLE001
        case_payload["status"] = "failed"
        case_payload["error"] = {"type": type(exc).__name__, "details": str(exc)}

    summary = summarize_case(case_payload)
    case_payload["case_level_summary"] = summary
    case_report_path = case_reports_dir / f"{scenario['case_id']}.json"
    write_json(case_report_path, case_payload)
    summary["case_report_path"] = str(case_report_path)
    summary["case_report_sha256"] = file_sha256(case_report_path)
    return summary


def update_failure_counter(summary: dict[str, Any], failure_counter: Counter[str]) -> None:
    if not summary["all_sessions_review_pass"]:
        failure_counter["review_gate_fail"] += 1
    if not summary["continuity_exact_opening_match"]:
        failure_counter["opening_state_mismatch"] += 1
    if not summary["memory_faithfulness_sessions_2_3_pass"]:
        failure_counter["memory_faithfulness_fail"] += 1
    if not summary["persona_consistency_sessions_2_3_pass"]:
        failure_counter["persona_consistency_fail"] += 1


def main() -> int:
    args = parse_args()
    if args.max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"output dir exists and is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_jsonl(args.scenario_registry)
    selected_scenarios = select_scenarios(rows, args)
    if len(selected_scenarios) < args.max_cases:
        raise ValueError(f"only found {len(selected_scenarios)} scenarios, need {args.max_cases}")
    session_turn_bank = session_turn_bank_for_scenario(selected_scenarios[0])
    if args.session_count < 1 or args.session_count > len(session_turn_bank):
        raise ValueError(f"session_count must be between 1 and {len(session_turn_bank)}")
    if args.turns_per_session < 1 or args.turns_per_session > len(session_turn_bank[0]):
        raise ValueError(f"turns_per_session must be between 1 and {len(session_turn_bank[0])}")

    runtime_stack = load_validation_runtime_stack(args)

    run_id = f"patient_longitudinal_validation::{stable_hash(json.dumps([row['scenario_id'] for row in selected_scenarios], ensure_ascii=False))}"
    case_reports_dir = output_dir / "case_reports"
    case_reports_dir.mkdir(parents=True, exist_ok=True)

    case_summaries: list[dict[str, Any]] = []
    failure_counter: Counter[str] = Counter()
    if args.max_workers == 1:
        for case_index, scenario in enumerate(selected_scenarios, start=1):
            summary = evaluate_case(
                scenario,
                args=args,
                run_id=run_id,
                case_reports_dir=case_reports_dir,
                runtime_stack=runtime_stack,
            )
            case_summaries.append(summary)
            update_failure_counter(summary, failure_counter)
            print(
                json.dumps(
                    {
                        "progress": f"{case_index}/{len(selected_scenarios)}",
                        "case_id": scenario.get("case_id"),
                        "scenario_id": scenario.get("scenario_id"),
                        "status": summary["status"],
                        "review_pass": summary["all_sessions_review_pass"],
                        "continuity_pass": summary["continuity_exact_opening_match"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    else:
        indexed_summaries: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            future_to_case: dict[Any, tuple[int, dict[str, Any]]] = {}
            for case_index, scenario in enumerate(selected_scenarios, start=1):
                future = executor.submit(
                    evaluate_case,
                    scenario,
                    args=args,
                    run_id=run_id,
                    case_reports_dir=case_reports_dir,
                    runtime_stack=None,
                )
                future_to_case[future] = (case_index, scenario)

            completed = 0
            for future in as_completed(future_to_case):
                case_index, scenario = future_to_case[future]
                summary = future.result()
                indexed_summaries.append((case_index, scenario, summary))
                update_failure_counter(summary, failure_counter)
                completed += 1
                print(
                    json.dumps(
                        {
                            "progress": f"{completed}/{len(selected_scenarios)}",
                            "case_id": scenario.get("case_id"),
                            "scenario_id": scenario.get("scenario_id"),
                            "status": summary["status"],
                            "review_pass": summary["all_sessions_review_pass"],
                            "continuity_pass": summary["continuity_exact_opening_match"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        indexed_summaries.sort(key=lambda item: item[0])
        case_summaries = [summary for _, _, summary in indexed_summaries]

    case_summaries_path = output_dir / "case_summaries.jsonl"
    with case_summaries_path.open("w", encoding="utf-8") as f:
        for row in case_summaries:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_payload = {
        "schema_version": "theraskill_patient_longitudinal_validation_summary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "selection_policy": {
            "scenario_family": args.scenario_family,
            "split_key": args.split_key,
            "split_value": args.split_value,
            "domain_filter": args.domain_filter,
            "include_high_risk": args.include_high_risk,
            "max_cases": args.max_cases,
            "session_count": args.session_count,
            "turns_per_session": args.turns_per_session,
            "max_workers": args.max_workers,
        },
        "runtime_config": runtime_stack["model_config_meta"],
        "selected_case_count": len(case_summaries),
        "completed_case_count": sum(1 for row in case_summaries if row.get("status") == "complete"),
        "all_sessions_review_pass_count": sum(1 for row in case_summaries if row.get("all_sessions_review_pass")),
        "continuity_exact_opening_match_count": sum(1 for row in case_summaries if row.get("continuity_exact_opening_match")),
        "memory_faithfulness_sessions_2_3_pass_count": sum(1 for row in case_summaries if row.get("memory_faithfulness_sessions_2_3_pass")),
        "persona_consistency_sessions_2_3_pass_count": sum(1 for row in case_summaries if row.get("persona_consistency_sessions_2_3_pass")),
        "failure_counter": dict(failure_counter),
        "selected_scenarios": [
            {
                "case_id": row["case_id"],
                "scenario_id": row["scenario_id"],
                "case_report_path": row["case_report_path"],
                "case_report_sha256": row["case_report_sha256"],
            }
            for row in case_summaries
        ],
        "case_summaries_path": str(case_summaries_path),
        "case_summaries_sha256": file_sha256(case_summaries_path),
    }
    summary_path = output_dir / "validation_summary.json"
    write_json(summary_path, summary_payload)

    print(
        json.dumps(
            {
                "status": "complete",
                "run_id": run_id,
                "selected_case_count": len(case_summaries),
                "all_sessions_review_pass_count": summary_payload["all_sessions_review_pass_count"],
                "continuity_exact_opening_match_count": summary_payload["continuity_exact_opening_match_count"],
                "summary_path": str(summary_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
