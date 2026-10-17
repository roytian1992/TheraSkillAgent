"""Compile a frozen patient persona into bounded, turn-specific runtime context."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
from typing import Any


STATE_BAND_ORDER = {"surface": 0, "moderate": 1, "deep": 2}
SENSITIVITY_ORDER = {"surface": 0, "moderate": 1, "sensitive": 2}


def _clean_list(values: Any) -> list[str]:
    rows = values if isinstance(values, list) else []
    return list(dict.fromkeys(text for value in rows if (text := " ".join(str(value or "").split()))))


def _fact_contents(rows: list[dict[str, Any]], limit: int = 12) -> list[str]:
    return [str(row.get("content")) for row in rows if row.get("content")][:limit]


def disclosure_state_band(runtime_state: dict[str, Any]) -> str:
    value = str(runtime_state.get("disclosure_state") or "surface").lower()
    if any(token in value for token in ("deep", "open", "high", "full")):
        return "deep"
    if any(token in value for token in ("moderate", "explor", "increas", "partial")):
        return "moderate"
    return "surface"


def move_signals(move_result: dict[str, Any] | None) -> dict[str, bool]:
    move = (move_result or {}).get("therapist_move_interpretation") or move_result or {}
    move_types = set(_clean_list(move.get("move_type")))
    intents = set(_clean_list(move.get("intent")))
    supportive = (
        move.get("validation_signal") == "present"
        or move.get("support_level") == "high"
        or bool(move_types & {"validation", "reflection", "open_question"})
    )
    rupture_pressure = (
        move.get("challenge_level") == "high"
        and move.get("validation_signal") in {"absent", "mixed"}
        and move.get("repair_signal") != "present"
    )
    return {
        "supportive": supportive,
        "rupture_pressure": rupture_pressure,
        "repair": move.get("repair_signal") == "present" or "repair_attempt" in move_types,
        "risk_probe": move.get("risk_probe_signal") == "present" or "risk_probe" in move_types,
        "open_exploration": "open_question" in move_types or "increase_disclosure" in intents,
    }


def public_marital_status(value: Any) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.lower()
    if any(token in lowered for token in ("divorced", "marriage ended")):
        return "has been married"
    if "widowed" in lowered:
        return "widowed"
    if any(token in lowered for token in ("single", "unmarried")):
        return "single"
    if any(token in lowered for token in ("dating", "boyfriend", "girlfriend")):
        return "dating"
    if any(token in lowered for token in ("cohabiting", "partner")):
        return "in a relationship"
    if any(token in lowered for token in ("married", "marriage")):
        return "married"
    if any(token in text for token in ("离异", "离婚", "婚姻失败")):
        return "有婚姻经历"
    if "丧偶" in text:
        return "丧偶"
    if any(token in text for token in ("未婚", "单身")):
        return "单身"
    if any(token in text for token in ("恋爱", "男友", "女友")):
        return "恋爱中"
    if any(token in text for token in ("同居", "伴侣")):
        return "伴侣关系"
    if any(token in text for token in ("已婚", "结婚", "婚姻")):
        return "已婚"
    return "未说明"


def public_demographic_profile(
    identity: dict[str, Any], forbidden_contents: set[str] | None = None
) -> dict[str, Any]:
    demographic = identity.get("demographic_profile") or {}
    forbidden = forbidden_contents or set()

    def public_value(field: str) -> Any:
        value = demographic.get(field)
        text = " ".join(str(value or "").split())
        return "未说明" if any(content and content in text for content in forbidden) else value

    marital_status = public_marital_status(demographic.get("marital_status"))
    if any(content and content in marital_status for content in forbidden):
        marital_status = "未说明"
    return {
        "age": public_value("age"),
        "gender": public_value("gender"),
        "occupation": public_value("occupation"),
        "marital_status": marital_status,
    }


def effective_disclosure_nodes(persona: dict[str, Any]) -> list[dict[str, Any]]:
    """Elevate a node when its text contains another, more sensitive fact."""
    nodes = deepcopy((persona.get("disclosure_graph") or {}).get("nodes") or [])
    for node in nodes:
        content = str(node.get("content") or "")
        current = str(node.get("sensitivity") or "surface")
        effective = current
        for other in nodes:
            other_content = str(other.get("content") or "")
            other_sensitivity = str(other.get("sensitivity") or "surface")
            if (
                other_content
                and other_content != content
                and other_content in content
                and SENSITIVITY_ORDER.get(other_sensitivity, 0)
                > SENSITIVITY_ORDER.get(effective, 0)
            ):
                effective = other_sensitivity
        if effective != current:
            node["sensitivity"] = effective
            node["allowed_state_bands"] = {
                "surface": ["surface", "moderate", "deep"],
                "moderate": ["moderate", "deep"],
                "sensitive": ["deep"],
            }[effective]
            node["default_response_mode"] = {
                "surface": "answer", "moderate": "hint", "sensitive": "withhold"
            }[effective]
    return nodes


def effective_initially_disclosed_fact_ids(persona: dict[str, Any]) -> list[str]:
    return [
        str(node.get("fact_id"))
        for node in effective_disclosure_nodes(persona)
        if node.get("fact_id") and node.get("sensitivity") == "surface"
    ]


def identity_checksum(persona: dict[str, Any]) -> str:
    payload = json.dumps(
        persona.get("immutable_identity") or {}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CompiledPersonaContext:
    persona_id: str
    state_band: str
    allowed_fact_ids: list[str]
    forbidden_fact_ids: list[str]
    active_principle_ids: list[str]
    runtime_context: dict[str, Any]
    decision_trace: dict[str, Any]


class PersonaCompiler:
    """Enforce a context firewall before patient response generation."""

    def compile_for_move(self, persona: dict[str, Any]) -> CompiledPersonaContext:
        identity = persona.get("immutable_identity") or {}
        psychological = persona.get("psychological_model") or {}
        communication = persona.get("communication_profile") or {}
        nodes = effective_disclosure_nodes(persona)
        public_nodes = [node for node in nodes if node.get("sensitivity") == "surface"]
        public_ids = [str(node["fact_id"]) for node in public_nodes]
        public_contents = {str(node.get("content")) for node in public_nodes}
        forbidden_contents = {
            str(node.get("content"))
            for node in nodes
            if node.get("sensitivity") != "surface" and node.get("content")
        }
        runtime_context = {
            "schema_version": "theraskill_compiled_patient_persona_context_v1",
            "persona_id": persona.get("persona_id"),
            "immutable_identity_checksum": identity_checksum(persona),
            "context_scope": "move_interpretation_public_only",
            "immutable_identity": {
                "display_name": identity.get("display_name"),
                "demographic_profile": public_demographic_profile(
                    identity, forbidden_contents,
                ),
            },
            "public_presenting_problems": [
                content
                for content in _fact_contents(psychological.get("presenting_problems") or [])
                if content in public_contents
            ],
            "communication_constraints": self._communication_constraints(
                communication, public_contents, forbidden_contents
            ),
            "allowed_fact_nodes": [
                self._runtime_node(node, forbidden_contents) for node in public_nodes
            ],
        }
        all_ids = [str(node["fact_id"]) for node in nodes]
        return CompiledPersonaContext(
            persona_id=str(persona.get("persona_id") or ""),
            state_band="surface",
            allowed_fact_ids=public_ids,
            forbidden_fact_ids=[fact_id for fact_id in all_ids if fact_id not in set(public_ids)],
            active_principle_ids=[],
            runtime_context=runtime_context,
            decision_trace={"policy": "move_interpretation_public_only", "surface_fact_count": len(public_ids)},
        )

    def compile_for_turn(
        self,
        persona: dict[str, Any],
        runtime_state: dict[str, Any],
        disclosed_fact_ids: list[str],
        move_result: dict[str, Any] | None,
    ) -> CompiledPersonaContext:
        band = disclosure_state_band(runtime_state)
        signals = move_signals(move_result)
        disclosed = set(disclosed_fact_ids)
        nodes = effective_disclosure_nodes(persona)
        allowed_nodes: list[dict[str, Any]] = []
        forbidden_nodes: list[dict[str, Any]] = []
        for node in nodes:
            fact_id = str(node.get("fact_id") or "")
            previously_disclosed = fact_id in disclosed
            prerequisites_met = set(node.get("prerequisite_fact_ids") or []).issubset(disclosed)
            band_allowed = band in set(node.get("allowed_state_bands") or [])
            sensitivity = str(node.get("sensitivity") or "surface")
            facilitated = sensitivity == "surface" or signals["supportive"] or signals["risk_probe"]
            blocked = signals["rupture_pressure"] and not signals["repair"] and not previously_disclosed
            allowed = previously_disclosed or (
                prerequisites_met and band_allowed and facilitated and not blocked
            )
            (allowed_nodes if allowed else forbidden_nodes).append(node)

        principles = (persona.get("behavioral_constitution") or {}).get("principles") or []
        forbidden_contents = {
            str(node.get("content")) for node in forbidden_nodes if node.get("content")
        }
        active_principles = [
            principle
            for principle in principles
            if self._principle_active(str(principle.get("category") or ""), signals)
            and not any(
                content in " ".join(
                    str(principle.get(field) or "") for field in ("when", "then", "unless")
                )
                for content in forbidden_contents
            )
        ]
        runtime_context = {
            "schema_version": "theraskill_compiled_patient_persona_context_v1",
            "persona_id": persona.get("persona_id"),
            "immutable_identity_checksum": identity_checksum(persona),
            "context_scope": "turn_specific_allowed_facts_only",
            "state_band": band,
            "immutable_identity": {
                "display_name": (persona.get("immutable_identity") or {}).get("display_name"),
                "demographic_profile": public_demographic_profile(
                    persona.get("immutable_identity") or {}, forbidden_contents
                ),
                "facts": [
                    content
                    for content in _fact_contents(
                        (persona.get("immutable_identity") or {}).get("facts") or []
                    )
                    if content in {str(node.get("content")) for node in allowed_nodes}
                ],
            },
            "communication_constraints": self._communication_constraints(
                persona.get("communication_profile") or {},
                {str(node.get("content")) for node in allowed_nodes},
                forbidden_contents,
            ),
            "allowed_fact_nodes": [
                self._runtime_node(node, forbidden_contents) for node in allowed_nodes
            ],
            "active_behavioral_principles": [
                {
                    "principle_id": principle.get("principle_id"),
                    "category": principle.get("category"),
                    "when": principle.get("when"),
                    "then": principle.get("then"),
                    "unless": principle.get("unless"),
                }
                for principle in active_principles
            ],
            "already_disclosed_fact_ids": sorted(disclosed),
            "forbidden_fact_ids": [str(node["fact_id"]) for node in forbidden_nodes],
        }
        return CompiledPersonaContext(
            persona_id=str(persona.get("persona_id") or ""),
            state_band=band,
            allowed_fact_ids=[str(node["fact_id"]) for node in allowed_nodes],
            forbidden_fact_ids=[str(node["fact_id"]) for node in forbidden_nodes],
            active_principle_ids=[str(item["principle_id"]) for item in active_principles],
            runtime_context=runtime_context,
            decision_trace={
                "policy": "context_firewall_v1",
                "immutable_identity_checksum": identity_checksum(persona),
                "state_band": band,
                "move_signals": signals,
                "allowed_fact_count": len(allowed_nodes),
                "forbidden_fact_count": len(forbidden_nodes),
                "active_principle_count": len(active_principles),
                "allowed_fact_ids": [str(node["fact_id"]) for node in allowed_nodes],
                "forbidden_fact_ids": [str(node["fact_id"]) for node in forbidden_nodes],
                "active_principle_ids": [str(item["principle_id"]) for item in active_principles],
            },
        )

    @staticmethod
    def _runtime_node(
        node: dict[str, Any], forbidden_contents: set[str]
    ) -> dict[str, Any]:
        def safe_conditions(field: str) -> list[str]:
            return [
                condition
                for condition in _clean_list(node.get(field))
                if not any(content and content in condition for content in forbidden_contents)
            ]

        return {
            "fact_id": node.get("fact_id"),
            "content": node.get("content"),
            "sensitivity": node.get("sensitivity"),
            "default_response_mode": node.get("default_response_mode"),
            "facilitating_conditions": safe_conditions("facilitating_conditions"),
            "blocking_conditions": safe_conditions("blocking_conditions"),
        }

    @staticmethod
    def _communication_constraints(
        profile: dict[str, Any],
        allowed_contents: set[str],
        forbidden_contents: set[str],
    ) -> dict[str, Any]:
        def allowed_facts(field: str) -> list[str]:
            return [
                content
                for content in _fact_contents(profile.get(field) or [], 6)
                if content in allowed_contents
            ]

        def safe_scalar(field: str, fallback: str) -> Any:
            value = profile.get(field)
            text = " ".join(str(value or "").split())
            return fallback if any(content and content in text for content in forbidden_contents) else value

        return {
            "response_length": safe_scalar("response_length", "随当前对话自然变化"),
            "vocabulary_and_abstraction": safe_scalar(
                "vocabulary_and_abstraction", "使用日常、可理解的表达"
            ),
            "narrative_organization": safe_scalar(
                "narrative_organization", "围绕当前问题逐步展开"
            ),
            "initiative_level": safe_scalar("initiative_level", "根据问题适度回应"),
            "emotional_expression": allowed_facts("emotional_expression"),
            "hesitation_and_avoidance": allowed_facts("hesitation_and_avoidance"),
            "advice_response_style": allowed_facts("advice_response_style"),
            "variation_instruction": (
                "Keep wording natural and varied; do not repeat a fixed template or persona description"
                if profile.get("language") == "en"
                else "保持自然措辞变化，不逐轮复读固定模板或 persona 描述"
            ),
        }

    @staticmethod
    def _principle_active(category: str, signals: dict[str, bool]) -> bool:
        if category in {"identity_consistency", "risk_expression_boundary", "communication_style"}:
            return True
        if category == "disclosure_behavior":
            return signals["supportive"] or signals["open_exploration"] or signals["rupture_pressure"]
        if category == "alliance_and_rupture":
            return signals["supportive"] or signals["rupture_pressure"] or signals["repair"]
        if category == "resistance_and_readiness":
            return True
        return False


def runtime_bundle_with_context(case_bundle: dict[str, Any], context: CompiledPersonaContext) -> dict[str, Any]:
    """Return a transient bundle whose runtime view contains no forbidden fact content."""
    llm_context = deepcopy(context.runtime_context)
    llm_context.pop("forbidden_fact_ids", None)
    return {
        "bundle_id": case_bundle.get("bundle_id"),
        "case_id": case_bundle.get("case_id"),
        "metadata": case_bundle.get("metadata"),
        "runtime_priors": case_bundle.get("runtime_priors") or {},
        "_runtime_persona_context": llm_context,
    }
