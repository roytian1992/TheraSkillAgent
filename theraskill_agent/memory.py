"""Hermes memory adapter for TheraSkill's domain-owned memory service.

This provider deliberately does not persist raw turns. TheraSkill remains the
source of truth for consent, ownership, sensitivity, expiry and deletion.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping

from .memory_provider import MemoryProvider
from domains.theraskill.theraskill_contribution.client import (
    ContributionClient,
    ContributionClientError,
    HTTPContributionClient,
)


def _json_result(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)


def _error(message: str, *, code: str = "theraskill_memory_error") -> str:
    return _json_result({"ok": False, "error": {"code": code, "message": message}})


MEMORY_TOOL_SCHEMAS = [
    {
        "name": "theraskill_memory_context",
        "description": "Read the current user's confirmed, non-sensitive TheraSkill memory context.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "theraskill_memory_propose",
        "description": "Propose a user memory for confirmation. This never activates memory immediately.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "enum": [
                        "communication_preference",
                        "user_confirmed_goal",
                        "user_confirmed_context",
                        "coping_preference",
                        "explicit_boundary",
                        "do_not_remember",
                    ],
                },
                "content": {"type": "string", "minLength": 1, "maxLength": 2000},
                "sensitivity_level": {"type": "string", "enum": ["standard", "sensitive"]},
                "ttl_days": {"type": "integer", "minimum": 1, "maximum": 365},
            },
            "required": ["memory_type", "content"],
        },
    },
    {
        "name": "theraskill_memory_approve",
        "description": "Confirm a proposed TheraSkill memory for the current user.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "minLength": 1},
                "ttl_days": {"type": "integer", "minimum": 1, "maximum": 365},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "theraskill_memory_update",
        "description": "Edit the current user's proposed or active TheraSkill memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "minLength": 1},
                "content": {"type": "string", "minLength": 1, "maxLength": 2000},
                "ttl_days": {"type": "integer", "minimum": 1, "maximum": 365},
                "sensitivity_level": {"type": "string", "enum": ["standard", "sensitive"]},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "theraskill_memory_delete",
        "description": "Delete a TheraSkill memory for the current user and erase its stored content.",
        "parameters": {
            "type": "object",
            "properties": {"memory_id": {"type": "string", "minLength": 1}},
            "required": ["memory_id"],
        },
    },
]


class TheraSkillMemoryProvider(MemoryProvider):
    """Expose domain-owned TheraSkill memory through Hermes lifecycle hooks."""

    def __init__(self, client: ContributionClient | None = None) -> None:
        self._client_override = client
        self._client: ContributionClient | None = client
        self._subject_id = ""
        self._session_id = ""
        self._hermes_home = ""

    @property
    def name(self) -> str:
        return "theraskill"

    def is_available(self) -> bool:
        return bool(
            os.environ.get("THERASKILL_CONTRIBUTION_API_URL", "inprocess://contribution").strip()
        )

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "contribution_api_url",
                "description": "Internal TheraSkill contribution API URL.",
                "default": "inprocess://contribution",
                "env_var": "THERASKILL_CONTRIBUTION_API_URL",
            },
            {
                "key": "contribution_api_token",
                "description": "Internal TheraSkill contribution API token.",
                "secret": True,
                "env_var": "THERASKILL_CONTRIBUTION_API_TOKEN",
            },
        ]

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._subject_id = str(
            kwargs.get("user_id")
            or kwargs.get("user_id_alt")
            or os.environ.get("THERASKILL_MEMORY_SUBJECT_ID", "")
        ).strip()
        if self._client is None:
            base_url = os.environ.get(
                "THERASKILL_CONTRIBUTION_API_URL", "inprocess://contribution"
            ).strip()
            token = os.environ.get("THERASKILL_CONTRIBUTION_API_TOKEN", "")
            self._client = HTTPContributionClient(base_url, api_token=token)

    def system_prompt_block(self) -> str:
        return (
            "TheraSkill domain memory is user-confirmed and scoped to the current user. "
            "Do not infer or persist memory from ordinary conversation; use the explicit "
            "TheraSkill memory tools when the user asks to remember, change, or delete something."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._subject_id or self._client is None:
            return ""
        try:
            context = self._client.memory_context(
                subject_id=self._subject_id,
                role="user",
                include_sensitive=False,
                max_chars=3000,
            )
        except ContributionClientError:
            return ""
        text = str(context.get("context_text") or "").strip()
        if not text:
            return ""
        return "[TheraSkill user-confirmed memory]\n" + text

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        # Deliberately no-op: raw transcript capture requires an explicit
        # domain memory proposal and the existing TheraSkill consent flow.
        return None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(MEMORY_TOOL_SCHEMAS)

    def _require_context(self) -> tuple[ContributionClient, str]:
        if self._client is None or not self._subject_id:
            raise ContributionClientError("TheraSkill memory subject is not initialized")
        return self._client, self._subject_id

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        try:
            client, subject_id = self._require_context()
            if tool_name == "theraskill_memory_context":
                return _json_result(
                    client.memory_context(
                        subject_id=subject_id,
                        role="user",
                        include_sensitive=False,
                        max_chars=3000,
                    )
                )
            if tool_name == "theraskill_memory_propose":
                payload = {
                    "memory_type": args.get("memory_type"),
                    "content": args.get("content"),
                    "sensitivity_level": args.get("sensitivity_level", "standard"),
                    "ttl_days": args.get("ttl_days", 90),
                    "source_session_id": self._session_id or None,
                    "consent_scope": "cross_session_support_continuity",
                }
                return _json_result(
                    client.propose_memory(subject_id=subject_id, role="user", payload=payload)
                )
            memory_id = str(args.get("memory_id") or "").strip()
            if not memory_id:
                return _error("memory_id is required", code="invalid_request")
            if tool_name == "theraskill_memory_approve":
                payload = {"ttl_days": args["ttl_days"]} if "ttl_days" in args else {}
                return _json_result(
                    client.approve_memory(
                        memory_id=memory_id, subject_id=subject_id, role="user", payload=payload
                    )
                )
            if tool_name == "theraskill_memory_update":
                payload = {
                    key: args[key]
                    for key in ("content", "ttl_days", "sensitivity_level")
                    if key in args
                }
                return _json_result(
                    client.update_memory(
                        memory_id=memory_id, subject_id=subject_id, role="user", payload=payload
                    )
                )
            if tool_name == "theraskill_memory_delete":
                return _json_result(
                    client.delete_memory(memory_id=memory_id, subject_id=subject_id, role="user")
                )
            return _error(f"unknown tool: {tool_name}", code="unknown_tool")
        except ContributionClientError as exc:
            return _error(str(exc), code="domain_memory_unavailable")
        except (TypeError, ValueError, KeyError) as exc:
            return _error(str(exc), code="invalid_request")

    def on_session_switch(self, new_session_id: str, **kwargs: Any) -> None:
        self._session_id = new_session_id

    def shutdown(self) -> None:
        self._client = self._client_override


def register(ctx) -> None:
    """Register TheraSkill as an opt-in Hermes memory provider."""
    ctx.register_memory_provider(TheraSkillMemoryProvider())


__all__ = ["MEMORY_TOOL_SCHEMAS", "TheraSkillMemoryProvider"]
