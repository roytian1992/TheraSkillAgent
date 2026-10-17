"""Deterministic, high-recall safety screen used before LLM routing."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyScreen:
    risk_level: str = "none_observed"
    requires_immediate_attention: bool = False
    signal_codes: tuple[str, ...] = ()


_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "imminent",
        "imminent_action",
        (
            r"(?:现在|马上|此刻|今晚).{0,16}(?:自杀|结束.{0,4}生命|伤害自己)",
            r"(?:about to|right now|tonight).{0,24}(?:kill myself|end my life|hurt myself)",
            r"(?:正在|已经开始).{0,12}(?:割腕|吞药|上吊|跳楼)",
        ),
    ),
    (
        "high",
        "plan_or_means",
        (
            r"(?:已经|正在|准备|计划).{0,18}(?:自杀|割腕|吞药|上吊|跳楼|结束.{0,4}生命)",
            r"(?:have a plan|made a plan|have the (?:pills|gun|knife|rope)|plan to).{0,24}(?:kill myself|end my life|hurt myself)?",
        ),
    ),
    (
        "moderate",
        "explicit_self_harm",
        (
            r"(?:我)?(?:想|考虑|打算).{0,8}(?:自杀|伤害自己|结束.{0,4}生命)",
            r"(?:不想活了|想死|自杀念头|伤害自己的念头)",
            r"(?:want to die|want to kill myself|thinking about suicide|suicidal thoughts?|self[- ]harm)",
        ),
    ),
    (
        "ambiguous",
        "indirect_death_language",
        (
            r"(?:活着没意思|消失算了|再也不醒来|没有活下去的意义)",
            r"(?:wish I could disappear|wish I would not wake up|better off dead|life is not worth living)",
        ),
    ),
)


def screen_safety_risk(text: str) -> SafetyScreen:
    """Return signal codes only; never persist the matched sensitive passage."""
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return SafetyScreen()
    for risk_level, signal_code, patterns in _PATTERNS:
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns):
            return SafetyScreen(
                risk_level=risk_level,
                requires_immediate_attention=risk_level == "imminent",
                signal_codes=(signal_code,),
            )
    return SafetyScreen()
