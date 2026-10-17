"""LLM routing, v3 hybrid retrieval, and grounded response generation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .contracts import (
    RoutingDecision,
    SupportEngineResult,
    SupportMessage,
    SupportSessionRecord,
)
from .safety import screen_safety_risk
from domains.theraskill.hybrid_retrieval import (
    HybridRetrievalError,
    HybridRetrievalPaths,
    TheraSkillHybridRetriever,
    search_policy_lexical,
    selected_retrieval_paths,
)
from domains.theraskill.runtime import runtime_root


ALLOWED_SKILLS = {
    "emotional-support",
    "risk-triage",
    "cognitive-reframing-support",
    "rebt-belief-work-support",
    "synthetic-dialogue-builder",
    "therapy-response-reviewer",
}
RISK_LEVELS = {"none_observed", "ambiguous", "moderate", "high", "imminent"}
PRESENTATION_MODES = {
    "conversation",
    "cognitive_reframe",
    "rebt_abc",
    "risk_support",
    "response_review",
    "synthetic_dialogue",
}


class SupportEngine(Protocol):
    def generate(self, record: SupportSessionRecord, job_id: str) -> SupportEngineResult: ...


class LLMSupportEngine:
    def __init__(
        self,
        *,
        project_root: str | Path,
        extraction_config: str | Path,
        model_config: str | Path,
        work_root: str | Path,
        knowledge_release_id: str,
        top_k: int = 8,
        max_runtime_prompt_tokens: int = 12000,
        memory_provider: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.extraction_config_path = Path(extraction_config).resolve()
        self.model_config_path = Path(model_config).resolve()
        self.work_root = Path(work_root).resolve()
        self.knowledge_release_id = knowledge_release_id
        self.top_k = top_k
        self.max_runtime_prompt_tokens = max_runtime_prompt_tokens
        self.memory_provider = memory_provider

        from domains.theraskill.theraskill_llm import (
            OpenAICompatibleClient,
            estimate_prompt_tokens,
            load_prompt,
            load_yaml,
            merged_model_config,
        )

        extraction = load_yaml(self.extraction_config_path)
        local = load_yaml(self.model_config_path)
        resolved = merged_model_config(extraction, local)
        self.client = OpenAICompatibleClient(resolved)
        self.model_name = self.client.model
        self.estimate_prompt_tokens = estimate_prompt_tokens
        self.routing_prompt = load_prompt(self.project_root / "prompts/Support/support_routing.md")
        self.response_prompt = load_prompt(self.project_root / "prompts/Support/support_response.md")
        self.compaction_prompt = load_prompt(
            self.project_root / "prompts/Support/support_history_compaction.md"
        )
        self.retriever = None
        self.retrieval_paths = selected_retrieval_paths(self.model_config_path, self.work_root / "cache")

    def _route(
        self,
        record: SupportSessionRecord,
        latest_text: str,
        conversation_summary: str | None,
        recent_messages: list[dict[str, str]],
    ) -> tuple[RoutingDecision, int]:
        from domains.theraskill.theraskill_llm import chat_json_with_repair

        safety = screen_safety_risk(latest_text)
        payload = {
            "conversation_summary": conversation_summary,
            "recent_messages": recent_messages,
            "latest_user_message": latest_text,
            "deterministic_safety_screen": {
                "risk_level": safety.risk_level,
                "requires_immediate_attention": safety.requires_immediate_attention,
                "signal_codes": list(safety.signal_codes),
            },
            "required_schema": {
                "primary_skill": sorted(ALLOWED_SKILLS),
                "secondary_skills": ["zero or more allowed skill names"],
                "task_type": "support|risk_attention|cognitive_reframe|rebt_belief_work|synthetic_construction|response_review",
                "detected_language": "BCP-47-like language label or mixed",
                "risk_level": {
                    "allowed": sorted(RISK_LEVELS),
                    "definition": (
                        "Safety risk only: none_observed=no safety cue; ambiguous=indirect or unclear death/safety cue; "
                        "moderate=explicit self-harm/suicidal thought without plan/immediacy; high=plan, means, or near-term intent; "
                        "imminent=danger underway or about to occur. Distress, insomnia, anxiety, or sadness alone are none_observed."
                    ),
                },
                "requires_immediate_safety_attention": False,
                "retrieval_query": "short semantic retrieval query",
                "user_intent_summary": "one concise sentence",
            },
        }
        started = time.monotonic()
        routing_source = "llm_with_deterministic_safety"
        try:
            raw = chat_json_with_repair(
                self.client,
                self.routing_prompt,
                payload,
                parse_retries=1,
                max_prompt_tokens=self.max_runtime_prompt_tokens,
                force_json_first=True,
            )
            primary = str(raw.get("primary_skill") or "")
            risk_level = str(raw.get("risk_level") or "none_observed")
            immediate = bool(raw.get("requires_immediate_safety_attention"))
            if primary not in ALLOWED_SKILLS:
                raise ValueError(f"routing returned unsupported skill: {primary}")
            if risk_level not in RISK_LEVELS:
                raise ValueError(f"routing returned unsupported risk level: {risk_level}")
        except Exception:
            raw = {}
            primary = "emotional-support"
            risk_level = "none_observed"
            immediate = False
            routing_source = "deterministic_fallback"
        risk_order = {
            "none_observed": 0,
            "ambiguous": 1,
            "moderate": 2,
            "high": 3,
            "imminent": 4,
        }
        if risk_order[safety.risk_level] > risk_order[risk_level]:
            risk_level = safety.risk_level
        immediate = immediate or safety.requires_immediate_attention
        if immediate or risk_level in {"moderate", "high", "imminent"}:
            primary = "risk-triage"
        secondary = [
            str(item)
            for item in (raw.get("secondary_skills") or [])
            if str(item) in ALLOWED_SKILLS and str(item) != primary
        ]
        decision = RoutingDecision(
            primary_skill=primary,
            secondary_skills=list(dict.fromkeys(secondary)),
            task_type=str(raw.get("task_type") or "support"),
            detected_language=str(raw.get("detected_language") or "unknown"),
            risk_level=risk_level,
            requires_immediate_safety_attention=immediate,
            safety_signal_codes=list(safety.signal_codes),
            routing_source=routing_source,
            retrieval_query=str(raw.get("retrieval_query") or latest_text).strip(),
            user_intent_summary=str(raw.get("user_intent_summary") or latest_text[:240]).strip(),
        )
        if not decision.retrieval_query:
            raise ValueError("routing returned an empty retrieval query")
        return decision, int((time.monotonic() - started) * 1000)

    def _retrieve(
        self, session_id: str, job_id: str, routing: RoutingDecision
    ) -> tuple[dict[str, Any], Path, int]:
        output = self.work_root / session_id / "retrieval" / f"{job_id}.bundle.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            selected = selected_retrieval_paths(self.model_config_path, self.work_root / "cache")
            if selected != self.retrieval_paths:
                self.retrieval_paths = selected
                self.retriever = None
            if self.retriever is None:
                self.retriever = TheraSkillHybridRetriever(self.project_root, self.retrieval_paths)
            bundle = self.retriever.search(
                routing.retrieval_query, skill=routing.primary_skill, top_k=self.top_k,
            )
        except HybridRetrievalError as exc:
            bundle = search_policy_lexical(
                self.project_root, self.retrieval_paths.sqlite_fts, routing.retrieval_query,
                skill=routing.primary_skill, top_k=self.top_k,
            )
            bundle["warnings"] = [str(exc)]
        output.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        return bundle, output, latency_ms

    def _skill_context(self, skill: str) -> dict[str, str]:
        from theraskill_agent.skills import SkillManager

        root = SkillManager().resolve(skill).parent
        paths = {
            "instruction": root / "SKILL.md",
            "boundaries": root / "references/boundaries.md",
            "source_policy": root / "references/source-policy.md",
        }
        limits = {"instruction": 4500, "boundaries": 2500, "source_policy": 2500}
        return {
            key: path.read_text(encoding="utf-8")[: limits[key]] if path.exists() else ""
            for key, path in paths.items()
        }

    def _compact_history(
        self, record: SupportSessionRecord, latest_text: str
    ) -> tuple[
        str | None,
        list[dict[str, str]],
        str,
        dict[str, Any],
        int,
    ]:
        messages = [
            {"role": str(message.role), "text": message.text}
            for message in record.public.messages
        ]
        combined = json.dumps(messages, ensure_ascii=False)
        estimated = self.estimate_prompt_tokens(combined)
        latest_estimated = self.estimate_prompt_tokens(latest_text)
        requires_compaction = estimated > 10000 or latest_estimated > 7000
        if not requires_compaction:
            return record.conversation_summary, messages[-11:-1], latest_text, {
                "applied": False,
                "estimated_history_tokens": estimated,
            }, 0

        older = messages[:-5] if len(messages) > 5 else []
        recent = messages[-5:-1]
        if latest_estimated > 7000:
            older = messages[:-1]
            recent = messages[-4:-1]
        payload = {
            "previous_summary": record.conversation_summary,
            "conversation_to_compact": older,
            "latest_long_message": latest_text if latest_estimated > 7000 else None,
            "target": "balanced compact representation under 3500 estimated tokens",
        }
        started = time.monotonic()
        summary = self.client.chat(
            self.compaction_prompt,
            payload,
            force_json_response=False,
        ).strip()
        summary_tokens = self.estimate_prompt_tokens(summary)
        if not summary or summary_tokens > 4000:
            raise ValueError("LLM history compaction did not produce a usable bounded summary")
        runtime_latest = latest_text
        if latest_estimated > 7000:
            runtime_latest = "The latest full user message is represented in conversation_summary."
        return summary, recent, runtime_latest, {
            "applied": True,
            "estimated_history_tokens": estimated,
            "latest_message_tokens": latest_estimated,
            "summary_estimated_tokens": summary_tokens,
            "preserved_recent_messages": len(recent),
            "method": "llm_balanced_compaction",
        }, int((time.monotonic() - started) * 1000)

    @staticmethod
    def _retrieved_context(bundle: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "dedup_family": item.get("dedup_family"),
                "canonical_label": item.get("canonical_label"),
                "evidence_role": item.get("evidence_role"),
                "allowed_use": item.get("allowed_use"),
                "forbidden_use": item.get("forbidden_use"),
                "source_tier": item.get("primary_evidence_tier"),
                "text_preview": str(item.get("text_preview") or "")[:500],
            }
            for item in (bundle.get("items") or [])[:6]
        ]

    @staticmethod
    def _public_evidence_summary(bundle: dict[str, Any]) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        retrieval_mode = str(
            bundle.get("retrieval_mode")
            or (bundle.get("retrieval_config") or {}).get("version")
            or "policy_aware_hybrid"
        )
        for item in (bundle.get("items") or [])[:6]:
            label = str(item.get("canonical_label") or item.get("dedup_family") or "参考模式")
            role = str(item.get("evidence_role") or "supporting_context")
            key = (label, role)
            if key in seen:
                continue
            seen.add(key)
            allowed_use = str(item.get("allowed_use") or "")
            forbidden_use = str(item.get("forbidden_use") or "")
            source_type = str(
                item.get("primary_knowledge_type")
                or item.get("knowledge_type")
                or item.get("primary_evidence_tier")
                or "knowledge_unit"
            )
            updated_at = str(item.get("updated_at") or item.get("source_updated_at") or "")
            summaries.append({
                "label": label[:100],
                "role": role[:80],
                "source_type": source_type[:80],
                "retrieval_mode": retrieval_mode[:100],
                "used_for": allowed_use[:240],
                "boundary": forbidden_use[:240],
                "updated_at": updated_at[:80],
                "inference": "模型根据参考依据生成支持性回应；不等同于诊断或治疗结论。",
            })
        return summaries

    def _respond(
        self,
        record: SupportSessionRecord,
        routing: RoutingDecision,
        bundle: dict[str, Any],
        latest_text: str,
        summary: str | None,
        recent: list[dict[str, str]],
        compaction: dict[str, Any],
        compaction_ms: int,
        domain_memory: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None, dict[str, Any], dict[str, int]]:
        from domains.theraskill.theraskill_llm import chat_json_with_repair, prompt_budget_report

        payload = {
            "routing": routing.model_dump(mode="json"),
            "skill_guidance": self._skill_context(routing.primary_skill),
            "conversation_summary": summary,
            "recent_messages": recent,
            "latest_user_message": latest_text,
            "retrieved_context": self._retrieved_context(bundle),
            "user_confirmed_domain_memory": [
                {
                    "memory_type": item.get("memory_type"),
                    "content": item.get("content"),
                    "sensitivity_level": item.get("sensitivity_level"),
                }
                for item in (domain_memory.get("items") or [])[:20]
            ],
        }
        audit = prompt_budget_report(
            self.response_prompt, payload, self.max_runtime_prompt_tokens
        )
        if audit["over_budget"]:
            raise ValueError(
                f"support response prompt exceeds runtime budget: {audit['estimated_prompt_tokens']}"
            )
        started = time.monotonic()
        def validate_response(value: dict[str, Any]) -> None:
            text = value.get('response_text')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('support response requires nonempty response_text')

        result = chat_json_with_repair(
            self.client,
            self.response_prompt,
            payload,
            parse_retries=1,
            max_prompt_tokens=self.max_runtime_prompt_tokens,
            force_json_first=True,
            validate=validate_response,
        )
        response_ms = int((time.monotonic() - started) * 1000)
        text = str(result.get("response_text") or "").strip()
        mode = str(result.get("presentation_mode") or "conversation")
        structured = result.get("structured_content") or {}
        if not text:
            raise ValueError("support response model returned empty response_text")
        if mode not in PRESENTATION_MODES:
            mode = "conversation"
        if not isinstance(structured, dict):
            structured = {}
        result["response_text"] = text
        result["presentation_mode"] = mode
        result["structured_content"] = structured
        compaction["response_prompt_audit"] = audit
        compaction["domain_memory"] = {
            "active_count": len(domain_memory.get("items") or []),
            "memory_ids": [
                str(item.get("memory_id"))
                for item in (domain_memory.get("items") or [])
                if item.get("memory_id")
            ],
            "truncated": bool(domain_memory.get("truncated")),
            "content_persisted_in_trace": False,
        }
        return result, summary, compaction, {
            "history_compaction": compaction_ms,
            "response_generation": response_ms,
        }

    def generate(self, record: SupportSessionRecord, job_id: str) -> SupportEngineResult:
        user_message = next(
            message
            for message in reversed(record.public.messages)
            if str(message.role) == "user"
        )
        summary, recent, runtime_latest, compaction, compaction_ms = (
            self._compact_history(record, user_message.text)
        )
        route, route_ms = self._route(
            record,
            runtime_latest,
            summary,
            recent,
        )
        bundle, retrieval_path, retrieval_ms = self._retrieve(
            record.public.session_id, job_id, route
        )
        domain_memory: dict[str, Any] = {}
        if self.memory_provider is not None:
            try:
                domain_memory = self.memory_provider(record.owner_id) or {}
            except Exception:
                domain_memory = {"items": [], "truncated": False, "fetch_failed": True}
        response, summary, compaction, response_latencies = self._respond(
            record,
            route,
            bundle,
            runtime_latest,
            summary,
            recent,
            compaction,
            compaction_ms,
            domain_memory,
        )
        items = bundle.get("items") or []
        unit_ids = [
            str(item.get("release_unit_id"))
            for item in items
            if item.get("release_unit_id")
        ]
        return SupportEngineResult(
            response_text=response["response_text"],
            presentation_mode=response["presentation_mode"],
            structured_content=response["structured_content"],
            evidence_summary=self._public_evidence_summary(bundle),
            routing=route,
            retrieval_report_path=str(retrieval_path),
            retrieved_unit_ids=unit_ids,
            retrieval_summary={
                "hit_count": len(items),
                "retrieval_mode": bundle.get("retrieval_mode", "hybrid_v3"),
                "warnings": bundle.get("warnings", []),
                "planner_status": (bundle.get("planner_trace") or {}).get("planner_status"),
                "structured_filter": bundle.get("structured_filter") or {},
                "policy_checks": bundle.get("policy_checks") or {},
                "quality_checks": bundle.get("quality_checks") or {},
                "channel_counts": bundle.get("channel_counts") or {},
            },
            model_config_id=self.model_name,
            prompt_release_id="support_routing_response_v2",
            conversation_summary=summary,
            latency_ms={
                "routing": route_ms,
                "retrieval": retrieval_ms,
                **response_latencies,
            },
            history_compaction=compaction,
        )
