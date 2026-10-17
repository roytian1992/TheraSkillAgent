from __future__ import annotations

import json

import pytest

from plugins.theraskill import (
    ArtifactManifest,
    ArtifactStore,
    ArtifactStoreError,
    TheraSkillRequest,
    TheraSkillResult,
)
from plugins.theraskill import TOOL_DEFINITIONS, register
from plugins.theraskill.service import service_config
from theraskill_agent.memory import TheraSkillMemoryProvider
from domains.theraskill import launcher
from domains.theraskill.theraskill_llm import OpenAICompatibleClient, merged_model_config
from theraskill_agent.apps import list_apps, open_app


def test_request_and_result_share_contract_version() -> None:
    request = TheraSkillRequest.from_mapping(
        {"action": "knowledge_search", "query": "情绪识别"}
    )
    result = TheraSkillResult.success(
        request.action,
        {"matches": []},
        request_id=request.request_id,
    )

    assert request.schema_version == result.schema_version
    assert request.to_dict()["action"] == "knowledge_search"
    assert result.to_dict()["ok"] is True
    assert result.to_dict()["status"] == "completed"


def test_runtime_model_config_uses_active_project_provider() -> None:
    resolved = merged_model_config(
        {"extraction": {"model": {"name": "legacy-extraction-model"}}},
        {"retrieval": {"embedding": {"enabled": True}}},
    )

    assert resolved["provider"] == "openai-compatible"
    assert resolved["name"] == "Qwen3.8-27B"
    assert resolved["base_url"].endswith("/v1")


def test_artifact_store_loads_current_release_and_rejects_undeclared_file(tmp_path) -> None:
    release = tmp_path / "releases" / "r1"
    release.mkdir(parents=True)
    artifact = release / "retrieval.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    (release / "manifest.json").write_text(
        json.dumps(
            {
                "release_id": "r1",
                "status": "complete",
                "artifacts": {"retrieval": "retrieval.jsonl"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "current").symlink_to(release, target_is_directory=True)

    store = ArtifactStore(tmp_path)
    assert store.load_manifest().release_id == "r1"
    assert store.resolve("retrieval") == artifact.resolve()

    with pytest.raises(ArtifactStoreError, match="not declared"):
        store.resolve("missing")


def test_incomplete_release_is_not_runtime_selectable() -> None:
    with pytest.raises(ValueError, match="status='complete'"):
        ArtifactManifest.from_mapping(
            {"release_id": "r1", "status": "candidate", "artifacts": {}}
        )


def test_plugin_registers_the_theraskill_action_tools() -> None:
    class Context:
        def __init__(self) -> None:
            self.names = []

        def register_tool(self, *, name, **kwargs) -> None:
            self.names.append(name)
            assert kwargs["toolset"] == "theraskill"
            assert callable(kwargs["handler"])

        def register_skill(self, name, path, description="") -> None:
            assert path.is_file()
            assert description


    context = Context()
    register(context)

    assert context.names == [definition[0] for definition in TOOL_DEFINITIONS]
    assert len(context.names) == 9


def test_domain_tools_publish_typed_operation_schemas() -> None:
    definitions = {name: schema for name, schema, *_rest in TOOL_DEFINITIONS}

    support_operations = definitions["theraskill_support"]["properties"]["parameters"]["properties"]["operation"]["enum"]
    simulation_parameters = definitions["theraskill_simulation"]["properties"]["parameters"]

    assert "submit_message" in support_operations
    assert "safety_action" in support_operations
    assert "update_plan" in support_operations
    assert "submit_turn" in simulation_parameters["properties"]["operation"]["enum"]
    assert "run_dual_cycle" in simulation_parameters["properties"]["operation"]["enum"]
    assert simulation_parameters["additionalProperties"] is False


def test_theraskill_registers_dashboard_apps() -> None:
    class Context:
        def register_tool(self, **_kwargs) -> None:
            pass

        def register_skill(self, _name, path, _description="") -> None:
            assert path.is_file()

    register(Context())
    apps = {app.app_id: app for app in list_apps()}

    assert apps["theraskill.support"].path == "/theraskill/support"
    assert apps["theraskill.simulation"].context_keys == ("session_id", "scenario_id")
    assert "expert" in apps["theraskill.expert"].required_roles


def test_hermes_open_app_only_resolves_registered_routes() -> None:
    register(type("Context", (), {"register_tool": lambda *_args, **_kwargs: None, "register_skill": lambda *_args, **_kwargs: None})())

    result = json.loads(
        open_app(
            {
                "app_id": "theraskill.simulation",
                "view": "session",
                "context": {"session_id": "session-123"},
            }
        )
    )
    assert result["ok"] is True
    assert result["path"] == "/theraskill/simulation?view=session&session_id=session-123"
    assert json.loads(open_app({"app_id": "https://example.com"}))["error"] == "unknown_app"
    assert json.loads(
        open_app(
            {
                "app_id": "theraskill.simulation",
                "context": {"redirect_url": "https://example.com"},
            }
        )
    )["error"] == "invalid_context"


def test_service_config_uses_platform_profile_values() -> None:
    config = {
        "platforms": {
            "theraskill": {
                "services": {
                    "support": {
                        "base_url": "http://support.internal/",
                        "path": "v2/sessions",
                        "timeout_seconds": 4,
                    }
                }
            }
        }
    }

    service = service_config("support", config)

    assert service is not None
    assert service.base_url == "http://support.internal"
    assert service.path == "/v2/sessions"
    assert service.timeout_seconds == 4


def test_default_artifact_store_is_inside_hermes_home(monkeypatch, tmp_path) -> None:
    hermes_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    store = ArtifactStore()

    assert store.root == hermes_home / "platform-data" / "theraskill"


def test_launcher_rejects_an_occupied_service_port(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(
        launcher,
        "service_specs",
        lambda: {"support": {"module": "unused:create_app", "port": 8878}},
    )
    monkeypatch.setattr(launcher, "_port_available", lambda port: False)

    with pytest.raises(launcher.RuntimeLaunchError, match="already in use"):
        launcher.start(services=["support"])


def test_tool_request_is_audited_below_hermes_home(monkeypatch, tmp_path) -> None:
    hermes_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    handler = next(
        definition[2]
        for definition in TOOL_DEFINITIONS
        if definition[0] == "theraskill_context"
    )

    result = handler({"profile": "test", "actor": "reviewer", "scope": "research"})

    assert result["status"] == "failed"
    audit = hermes_home / "platform-data/theraskill/audit/tool-events.jsonl"
    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [event["status"] for event in events] == ["received", "failed"]
    assert events[0]["actor"] == "reviewer"
    assert events[0]["scope"] == "research"


def test_high_risk_contribution_requires_approval_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    handler = next(
        definition[2]
        for definition in TOOL_DEFINITIONS
        if definition[0] == "theraskill_contribution"
    )

    result = handler({"parameters": {"requires_approval": True}})

    assert result["error"]["code"] == "approval_required"


def test_review_decision_requires_approval_by_operation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    handler = next(
        definition[2]
        for definition in TOOL_DEFINITIONS
        if definition[0] == "theraskill_response_review"
    )

    result = handler({
        "parameters": {
            "operation": "submit_decision",
            "contribution_id": "contribution-1",
            "decision": {"decision": "accept"},
        }
    })

    assert result["error"]["code"] == "approval_required"


def test_user_contribution_withdrawal_is_not_blocked_by_review_approval(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(
        "plugins.theraskill.tools.call_service",
        lambda _config, _request: (204, {}),
    )
    handler = next(
        definition[2]
        for definition in TOOL_DEFINITIONS
        if definition[0] == "theraskill_contribution"
    )

    result = handler({
        "parameters": {
            "operation": "withdraw",
            "contribution_id": "contribution-1",
        }
    })

    assert result["error"] is None
    assert result["status"] == "completed"


def test_successful_domain_action_returns_app_handoff(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(
        "plugins.theraskill.tools.call_service",
        lambda _config, _request: (201, {"session_id": "support-1"}),
    )
    handler = next(
        definition[2]
        for definition in TOOL_DEFINITIONS
        if definition[0] == "theraskill_support"
    )

    result = handler({"parameters": {"operation": "create_session"}})

    assert result["next_action"] == "open_app"
    assert result["view"] == {
        "app_id": "theraskill.support",
        "label": "TheraSkill Support",
        "view": "session",
        "context": {"session_id": "support-1"},
        "path": "/theraskill/support?view=session&session_id=support-1",
        "markdown": "[TheraSkill Support](/theraskill/support?view=session&session_id=support-1)",
    }


class _MemoryClient:
    def __init__(self) -> None:
        self.calls = []

    def memory_context(self, **kwargs):
        self.calls.append(("context", kwargs))
        return {"subject_id": kwargs["subject_id"], "context_text": "language: English"}

    def propose_memory(self, **kwargs):
        self.calls.append(("propose", kwargs))
        return {"memory_id": "memory_1", "confirmation_status": "proposed"}

    def approve_memory(self, **kwargs):
        self.calls.append(("approve", kwargs))
        return {"memory_id": kwargs["memory_id"], "confirmation_status": "active"}

    def update_memory(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {"memory_id": kwargs["memory_id"], "confirmation_status": "active"}

    def delete_memory(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return {"memory_id": kwargs["memory_id"], "confirmation_status": "deleted"}


def test_theraskill_memory_provider_uses_domain_memory_and_never_auto_syncs() -> None:
    client = _MemoryClient()
    provider = TheraSkillMemoryProvider(client)
    provider.initialize(session_id="session-1", user_id="subject-1")

    assert "language: English" in provider.prefetch("hello")
    proposal = json.loads(
        provider.handle_tool_call(
            "theraskill_memory_propose",
            {"memory_type": "communication_preference", "content": "Use English"},
        )
    )
    assert proposal["confirmation_status"] == "proposed"
    assert json.loads(
        provider.handle_tool_call("theraskill_memory_approve", {"memory_id": "memory_1"})
    )["confirmation_status"] == "active"
    provider.sync_turn("raw user transcript", "raw assistant transcript")

    assert [name for name, _ in client.calls] == ["context", "propose", "approve"]
    assert client.calls[1][1]["subject_id"] == "subject-1"
    assert client.calls[1][1]["payload"]["source_session_id"] == "session-1"
