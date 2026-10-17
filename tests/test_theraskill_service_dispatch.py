from __future__ import annotations

from plugins.theraskill.contracts import TheraSkillRequest
from plugins.theraskill.service import ServiceConfig, call_service


class _Response:
    status_code = 201

    @staticmethod
    def json():
        return {"session_id": "session-1"}


class _Client:
    calls = []

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _Response()


def test_support_action_dispatches_to_typed_domain_route(monkeypatch) -> None:
    _Client.calls = []
    monkeypatch.setattr("plugins.theraskill.service.httpx.Client", _Client)
    request = TheraSkillRequest.from_mapping({
        "action": "support",
        "actor": "stable-user-1",
        "session_id": "support-1",
        "idempotency_key": "message-1",
        "parameters": {"operation": "submit_message", "text": "hello"},
    })

    status, _body = call_service(ServiceConfig("support", "http://support", "/sessions"), request)

    method, url, options = _Client.calls[0]
    assert status == 201
    assert (method, url) == ("POST", "http://support/sessions/support-1/messages")
    assert options["json"] == {"text": "hello"}
    assert options["headers"]["X-Support-User"] == "stable-user-1"
    assert options["headers"]["Idempotency-Key"] == "message-1"


def test_simulation_list_dispatches_query_without_generation_payload(monkeypatch) -> None:
    _Client.calls = []
    monkeypatch.setattr("plugins.theraskill.service.httpx.Client", _Client)
    request = TheraSkillRequest.from_mapping({
        "action": "simulation",
        "parameters": {
            "operation": "list_scenarios", "query": "anxiety", "language": "en", "limit": 12,
        },
    })

    call_service(ServiceConfig("simulation", "http://simulation", "/sessions"), request)

    method, url, options = _Client.calls[0]
    assert (method, url) == ("GET", "http://simulation/scenarios")
    assert options["json"] is None
    assert options["params"] == {"search": "anxiety", "language": "en", "limit": 12}


def test_runtime_user_identity_is_not_model_supplied(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    captured = {}

    def fake_call(_config, request):
        captured["actor"] = request.actor
        return 201, {"session_id": "support-1"}

    monkeypatch.setattr("plugins.theraskill.tools.call_service", fake_call)
    from plugins.theraskill.tools import TOOL_DEFINITIONS

    handler = next(item[2] for item in TOOL_DEFINITIONS if item[0] == "theraskill_support")
    handler(
        {"parameters": {"operation": "create_session"}},
        user_id="platform-user",
        user_id_alt="stable-platform-user",
        task_id="task-1",
    )

    assert captured["actor"] == "stable-platform-user"


def test_simulation_dispatch_uses_domain_actor_roles(monkeypatch) -> None:
    _Client.calls = []
    monkeypatch.setattr("plugins.theraskill.service.httpx.Client", _Client)
    request = TheraSkillRequest.from_mapping({
        "action": "simulation",
        "actor": "stable-user-2",
        "session_id": "simulation-1",
        "idempotency_key": "turn-1",
        "parameters": {"operation": "submit_turn", "actor": "therapist_agent", "text": "hello"},
    })

    call_service(ServiceConfig("simulation", "http://simulation", "/sessions"), request)

    method, url, options = _Client.calls[0]
    assert (method, url) == ("POST", "http://simulation/sessions/simulation-1/turns")
    assert options["json"] == {"actor": "therapist_agent", "text": "hello"}
    assert options["headers"]["X-Simulation-Role"] == "therapist_agent"


def test_simulation_dispatch_supports_dual_agent_cycles(monkeypatch) -> None:
    _Client.calls = []
    monkeypatch.setattr("plugins.theraskill.service.httpx.Client", _Client)
    request = TheraSkillRequest.from_mapping({
        "action": "simulation",
        "actor": "researcher-1",
        "session_id": "simulation-dual-1",
        "idempotency_key": "cycle-1",
        "parameters": {
            "operation": "run_dual_cycle",
            "actor": "researcher",
            "initial_therapist_utterance": "你好，今天想从哪里开始？",
        },
    })

    call_service(ServiceConfig("simulation", "http://simulation", "/sessions"), request)

    method, url, options = _Client.calls[0]
    assert (method, url) == ("POST", "http://simulation/sessions/simulation-dual-1/dual-cycle")
    assert options["json"]["initial_therapist_utterance"].startswith("你好")
    assert options["headers"]["Idempotency-Key"] == "cycle-1"


def test_simulation_dispatch_supports_review_only_creation(monkeypatch) -> None:
    _Client.calls = []
    monkeypatch.setattr("plugins.theraskill.service.httpx.Client", _Client)
    request = TheraSkillRequest.from_mapping({
        "action": "simulation",
        "actor": "researcher-1",
        "parameters": {
            "operation": "create_review_session",
            "source_session_id": "source-1",
            "scenario_id": "scenario-1",
            "language": "zh",
        },
    })

    call_service(ServiceConfig("simulation", "http://simulation", "/sessions"), request)

    method, url, options = _Client.calls[0]
    assert (method, url) == ("POST", "http://simulation/sessions")
    assert options["json"] == {
        "scenario_id": "scenario-1",
        "mode": "review_only",
        "language": "zh",
        "source_session_id": "source-1",
    }
    assert options["headers"]["X-Simulation-Role"] == "researcher"


def test_support_dispatches_plan_and_safety_resources(monkeypatch) -> None:
    _Client.calls = []
    monkeypatch.setattr("plugins.theraskill.service.httpx.Client", _Client)
    request = TheraSkillRequest.from_mapping({
        "action": "support",
        "actor": "stable-user-3",
        "session_id": "support-2",
        "parameters": {
            "operation": "update_plan",
            "goals": ["改善睡眠"],
            "action_items": ["今晚提前半小时休息"],
        },
    })

    call_service(ServiceConfig("support", "http://support", "/sessions"), request)
    method, url, options = _Client.calls[0]
    assert (method, url) == ("PATCH", "http://support/sessions/support-2/plan")
    assert options["json"]["goals"] == ["改善睡眠"]

    _Client.calls = []
    resources = TheraSkillRequest.from_mapping({
        "action": "support",
        "actor": "stable-user-3",
        "parameters": {"operation": "list_safety_resources", "region": "cn"},
    })
    call_service(ServiceConfig("support", "http://support", "/sessions"), resources)
    method, url, options = _Client.calls[0]
    assert (method, url) == ("GET", "http://support/safety/resources")
    assert options["params"] == {"region": "cn"}
