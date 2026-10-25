"""Small, stateful course-design conversation orchestrator.

The conversational layer deliberately does not invent course content. It
collects the teacher's intent, asks for the smallest missing decision, and
returns a typed patch that the existing blueprint and scenario skills can
consume after explicit confirmation. Keeping this layer deterministic makes
local models optional and makes every proposed setting visible to the teacher.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_TEMPLATES = {
    "foundation_first_session": ("首次会谈基础", "first-session foundations"),
    "risk_and_safety_basics": ("风险识别与安全沟通", "risk and safety communication"),
    "integrated_case_formulation": ("从评估到工作假设", "assessment to working formulation"),
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(message: str) -> int | None:
    match = re.search(r"(?:生成|做|要|需要|create|generate|make)?\s*(\d{1,2})\s*(?:个|例|组|个案例|cases?|scenarios?)", message, re.I)
    if not match:
        match = re.search(r"\b(\d{1,2})\s+(?:(?:low|medium|high|低|中等|高)(?:[- ]risk|风险)?\s+)?(?:risk|cases?|scenarios?)\b", message, re.I)
    if not match:
        return None
    return max(1, min(12, int(match.group(1))))


def _template(message: str) -> str | None:
    lowered = message.casefold()
    if any(token in message for token in ("风险识别", "安全沟通", "危机沟通")) or any(token in lowered for token in ("risk assessment", "risk and safety", "safety communication", "crisis communication")):
        return "risk_and_safety_basics"
    if any(token in message for token in ("概念化", "工作假设", "评估")) or "formulation" in lowered:
        return "integrated_case_formulation"
    if any(token in message for token in ("首次", "初次", "建立关系")) or "first session" in lowered:
        return "foundation_first_session"
    return None


def _difficulty(message: str) -> str | None:
    if any(token in message for token in ("进阶", "困难", "高难")) or any(token in message.casefold() for token in ("advanced", "hard")):
        return "high"
    if any(token in message for token in ("中等", "适中")) or "intermediate" in message.casefold():
        return "medium"
    if any(token in message for token in ("基础", "简单")) or any(token in message.casefold() for token in ("basic", "foundational", "easy")):
        return "low"
    return None


def _risk(message: str) -> str | None:
    if any(token in message for token in ("高风险", "高危")) or "high risk" in message.casefold():
        return "high"
    if any(token in message for token in ("需关注", "中风险", "升高")) or "elevated" in message.casefold():
        return "elevated"
    if any(token in message for token in ("低风险", "不涉及风险")) or "low risk" in message.casefold():
        return "low"
    return None


def _goal(message: str) -> str | None:
    """Extract a useful goal without pretending to perform semantic extraction."""
    cleaned = _text(message)
    if len(cleaned) < 8:
        return None
    # Strip a command prefix while keeping the teacher's actual wording.
    cleaned = re.sub(r"^(?:我想|希望|请帮我|帮我|想做|我要|设计|创建|做一个|create|help me|i want)\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"(?:生成|做|要|需要)\s*\d{1,2}\s*(?:个|例|组|个案例|cases?|scenarios?)", "", cleaned, flags=re.I)
    cleaned = _text(cleaned.strip("，,。.!！ "))
    if not cleaned or cleaned in {"课程", "一个课程", "课程草案", "course", "a course"}:
        return None
    return cleaned[:800]


def course_agent_turn(message: str, draft: Mapping[str, Any] | None = None, *, language: str = "zh") -> dict[str, Any]:
    """Return a conversational reply and a visible course-draft patch."""
    if language not in {"zh", "en"}:
        language = "zh"
    text = _text(message)
    if not text:
        raise ValueError("message cannot be empty")
    current = dict(draft or {})
    patch: dict[str, Any] = {}
    count = _number(text)
    template = _template(text)
    difficulty = _difficulty(text)
    risk = _risk(text)
    goal = _goal(text)
    if count is not None:
        patch["scenario_count"] = count
    if template:
        patch["template_id"] = template
    if difficulty:
        patch["difficulty_band"] = difficulty
    if risk:
        patch["risk_band"] = risk
    if goal and not _text(current.get("description")):
        patch["description"] = goal
    merged = {**current, **patch}
    has_goal = bool(_text(merged.get("description")) or _text(merged.get("title")))
    has_count = bool(merged.get("scenario_count"))
    wants_build = bool(re.search(r"(?:生成|创建|开始|可以|确认|继续|就这样|generate|create|start|yes|confirm|go ahead)", text, re.I))
    missing: list[str] = []
    if not has_goal:
        missing.append("goal")
    if not has_count:
        missing.append("scenario_count")
    if missing:
        if language == "zh":
            reply = "先告诉我这门课想让学员练会什么，以及准备练习几个案例。比如：‘做一门首次会谈中的情绪验证，生成 3 个低风险案例。’"
        else:
            reply = "Tell me what learners should practise and how many cases you want. For example: ‘Create a first-session validation course with 3 low-risk cases.’"
        action = "ask"
    elif wants_build:
        title = _text(merged.get("title")) or (goal or _text(merged.get("description")))[:80]
        patch["title"] = title
        patch["description"] = _text(merged.get("description")) or goal or title
        patch.setdefault("template_id", "foundation_first_session")
        patch.setdefault("difficulty_band", "low")
        patch.setdefault("risk_band", "low")
        if language == "zh":
            reply = f"我整理成一个课程计划：{patch['title']}；{patch['scenario_count']} 个案例；{patch['difficulty_band']}难度；{patch['risk_band']}风险。确认后我会检索教学依据、生成知识卡和微练习，再进入案例生成。"
        else:
            reply = f"I have a course plan: {patch['title']}; {patch['scenario_count']} cases; {patch['difficulty_band']} difficulty; {patch['risk_band']} risk. After you confirm, I will retrieve teaching references, create cards and micro-practices, then generate cases."
        action = "confirm_blueprint"
    else:
        if language == "zh":
            reply = "我记下了。还可以补充案例数量、难度或风险；信息足够后我会先展示计划，等你确认再生成。"
        else:
            reply = "I have noted that. You can add the case count, difficulty or risk. Once the essentials are clear, I will show a plan and wait for confirmation before generating anything."
        action = "ask" if missing else "confirm_blueprint"
    return {
        "schema_version": "theraskill_course_agent_turn_v1",
        "reply": reply,
        "draft_patch": patch,
        "draft": {**merged, **patch},
        "missing": missing,
        "ready": not missing,
        "suggested_action": action,
        "tool_plan": ["course_blueprint", "knowledge_retrieval", "scenario_batch_generation"] if action == "confirm_blueprint" else [],
    }
