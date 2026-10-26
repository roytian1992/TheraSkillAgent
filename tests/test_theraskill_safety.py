from __future__ import annotations
from types import SimpleNamespace

from domains.theraskill.theraskill_support.engine import LLMSupportEngine
from domains.theraskill.theraskill_support.safety import screen_safety_risk


def test_deterministic_safety_screen_distinguishes_signal_levels() -> None:
    assert screen_safety_risk("I feel sad and cannot sleep").risk_level == "none_observed"
    assert screen_safety_risk("我觉得活着没意思").risk_level == "ambiguous"
    assert screen_safety_risk("我想自杀").risk_level == "moderate"
    imminent = screen_safety_risk("我现在就要结束自己的生命")
    assert imminent.risk_level == "imminent"
    assert imminent.requires_immediate_attention is True


def test_support_route_cannot_downgrade_deterministic_safety(monkeypatch) -> None:
    monkeypatch.setattr(
        "domains.theraskill.theraskill_llm.chat_json_with_repair",
        lambda *_args, **_kwargs: {
            "primary_skill": "emotional-support",
            "secondary_skills": [],
            "task_type": "support",
            "detected_language": "zh",
            "risk_level": "none_observed",
            "requires_immediate_safety_attention": False,
            "retrieval_query": "情绪支持",
            "user_intent_summary": "用户需要支持",
        },
    )
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.client = object()
    engine.routing_prompt = "route"
    engine.max_runtime_prompt_tokens = 12000

    decision, _latency = engine._route(SimpleNamespace(workflow="support", output_language="auto"), "我想自杀", None, [])

    assert decision.primary_skill == "risk-triage"
    assert decision.risk_level == "moderate"
    assert decision.safety_signal_codes == ["explicit_self_harm"]


def test_support_route_has_deterministic_fallback_when_llm_fails(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("domains.theraskill.theraskill_llm.chat_json_with_repair", fail)
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.client = object()
    engine.routing_prompt = "route"
    engine.max_runtime_prompt_tokens = 12000

    decision, _latency = engine._route(SimpleNamespace(workflow="support", output_language="auto"), "我现在就要结束自己的生命", None, [])

    assert decision.primary_skill == "risk-triage"
    assert decision.risk_level == "imminent"
    assert decision.routing_source == "deterministic_fallback"


def test_public_evidence_summary_exposes_source_and_boundary() -> None:
    summary = LLMSupportEngine._public_evidence_summary({
        "retrieval_config": {"version": "hybrid-test"},
        "items": [{
            "canonical_label": "情绪验证",
            "evidence_role": "supporting_context",
            "primary_knowledge_type": "method_rubric",
            "allowed_use": "用于支持性回应",
            "forbidden_use": "不得作为诊断结论",
            "source_updated_at": "2026-10-01",
        }],
    })
    assert summary == [{
        "label": "情绪验证",
        "role": "supporting_context",
        "source_type": "method_rubric",
        "retrieval_mode": "hybrid-test",
        "used_for": "用于支持性回应",
        "boundary": "不得作为诊断结论",
        "updated_at": "2026-10-01",
        "inference": "模型根据参考依据生成支持性回应；不等同于诊断或治疗结论。",
    }]


def test_explicit_review_workflow_is_not_rerouted_by_quoted_risk(monkeypatch) -> None:
    def unexpected(*args, **kwargs):
        raise AssertionError('Explicit workflow does not require another intent-classification call')
    monkeypatch.setattr('domains.theraskill.theraskill_llm.chat_json_with_repair', unexpected)
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    record = SimpleNamespace(workflow='response_review', output_language='en')
    decision, latency = engine._route(record, 'Review this quoted patient: I want to die.', None, [])
    assert decision.primary_skill == 'therapy-response-reviewer'
    assert decision.task_type == 'response_review'
    assert decision.detected_language == 'en'
    assert decision.routing_source == 'explicit_workflow' and latency == 0


def test_evidence_summary_keeps_traceable_source_and_readable_boundaries() -> None:
    result = LLMSupportEngine._public_evidence_summary({'items': [{
        'canonical_label': 'Validation', 'allowed_use': ['support', 'review'],
        'forbidden_use': ['diagnosis'], 'primary_dataset': 'synthetic-test',
        'release_unit_id': 'unit-test', 'text_preview': 'Source excerpt.',
    }]})[0]
    assert result['source_id'] == 'unit-test' and result['dataset'] == 'synthetic-test'
    assert result['excerpt'] == 'Source excerpt.'
    assert result['used_for'] == 'support; review' and result['boundary'] == 'diagnosis'


def test_evidence_summary_binds_release_provenance_and_keeps_same_label_units() -> None:
    summary = LLMSupportEngine._public_evidence_summary({
        'retrieval_mode': 'hybrid_v3',
        'source_index_id': 'index-pinned',
        'knowledge_sha256': 'knowledge-pinned',
        'items': [
            {'release_unit_id': 'unit-a', 'canonical_label': 'Validation', 'evidence_role': 'supporting_context'},
            {'release_unit_id': 'unit-b', 'canonical_label': 'Validation', 'evidence_role': 'supporting_context'},
            {'release_unit_id': 'unit-a', 'canonical_label': 'Validation', 'evidence_role': 'supporting_context'},
        ],
    })
    assert [item['source_id'] for item in summary] == ['unit-a', 'unit-b']
    assert all(item['source_index_id'] == 'index-pinned' for item in summary)
    assert all(item['knowledge_sha256'] == 'knowledge-pinned' for item in summary)


def test_retrieve_pins_release_provenance_before_query(monkeypatch, tmp_path) -> None:
    import domains.theraskill.theraskill_support.engine as engine_module

    index_db = tmp_path / 'pinned.sqlite'
    selected_paths = SimpleNamespace(sqlite_fts=index_db)
    selected_release = (
        tmp_path / 'index-pinned',
        {'index_id': 'index-pinned', 'input': {'knowledge_sha256': 'knowledge-pinned'}},
    )

    class Retriever:
        def __init__(self, *_args, **_kwargs):
            pass

        def search(self, *_args, **_kwargs):
            return {
                'source_index': str(index_db),
                'items': [{'release_unit_id': 'unit-1', 'evidence_role': 'supporting_context'}],
                'retrieval_mode': 'hybrid_v3',
            }

    monkeypatch.setattr(engine_module, 'TheraSkillHybridRetriever', Retriever)
    monkeypatch.setattr(
        engine_module,
        'selected_retrieval_paths',
        lambda *_args, **_kwargs: selected_paths,
    )
    monkeypatch.setattr(
        'theraskill_agent.resources.active_index',
        lambda: selected_release,
    )
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.work_root = tmp_path / 'work'
    engine.model_config_path = tmp_path / 'model.yaml'
    engine.project_root = tmp_path
    engine.top_k = 8
    engine.retrieval_paths = None
    engine.retriever = None
    routing = SimpleNamespace(retrieval_query='support query', primary_skill='emotional-support')

    bundle, _path, _latency = engine._retrieve('session', 'job', routing)

    assert bundle['source_index_id'] == 'index-pinned'
    assert bundle['knowledge_sha256'] == 'knowledge-pinned'
    assert bundle['items'][0]['source_index_id'] == 'index-pinned'
    assert bundle['items'][0]['knowledge_sha256'] == 'knowledge-pinned'


def test_fast_path_is_limited_to_lightweight_low_risk_exchanges() -> None:
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    record = SimpleNamespace(workflow="support", output_language="auto")

    greeting = engine._fast_path_route(record, "你好")
    assert greeting is not None
    assert greeting.routing_source == "deterministic_fast_path"
    assert greeting.task_type == "simple_conversation"
    assert engine._fast_path_route(record, "我睡不着怎么办？") is None
    assert engine._fast_path_route(record, "我想自杀") is None
    assert engine._fast_path_route(record, "当前每个对话都会抽取 evidence 吗？") is not None
    assert engine._fast_path_route(record, "怎么配置 LLM API") is not None
    assert engine._fast_path_route(record, "如何测试 embedding API") is not None
    assert engine._fast_path_route(
        SimpleNamespace(workflow="response_review", output_language="auto"), "谢谢"
    ) is None


def test_fast_path_preserves_active_safety_context_for_acknowledgements() -> None:
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    record = SimpleNamespace(
        workflow="support",
        output_language="auto",
        public=SimpleNamespace(
            safety=SimpleNamespace(
                attention_level="urgent",
                source_risk_level="imminent",
                signal_codes=["imminent_action"],
                handoff_status="offered",
            )
        ),
        turn_traces=[],
    )

    # The user may be acknowledging a safety check-in; do not answer through
    # the lightweight route while the safety state still needs attention.
    assert engine._fast_path_route(record, "好的") is None


def test_fast_path_preserves_prior_risk_route_when_state_is_from_an_older_record() -> None:
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    record = SimpleNamespace(
        workflow="support",
        output_language="auto",
        public=SimpleNamespace(
            safety=SimpleNamespace(
                attention_level="none",
                source_risk_level="none_observed",
                signal_codes=[],
                handoff_status="not_needed",
            )
        ),
        turn_traces=[
            SimpleNamespace(
                routing=SimpleNamespace(
                    risk_level="moderate",
                    requires_immediate_safety_attention=False,
                )
            )
        ],
    )

    assert engine._fast_path_route(record, "收到") is None


def test_fast_path_skips_routing_and_retrieval_but_keeps_one_response_call(tmp_path) -> None:
    from domains.theraskill.theraskill_support.contracts import (
        SupportMessage,
        SupportPublicSession,
        SupportSessionRecord,
    )

    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.work_root = tmp_path
    engine.model_name = "test-model"
    engine.memory_provider = None
    record = SupportSessionRecord(
        public=SupportPublicSession(
            session_id="session",
            messages=[
                SupportMessage(
                    message_id="message",
                    sequence=1,
                    role="user",
                    text="你好",
                )
            ],
        ),
        knowledge_release_id="test-release",
    )
    calls = {"route": 0, "retrieve": 0, "respond": 0}

    def unexpected_route(*_args, **_kwargs):
        calls["route"] += 1
        raise AssertionError("fast path should not call the routing model")

    def unexpected_retrieve(*_args, **_kwargs):
        calls["retrieve"] += 1
        raise AssertionError("fast path should not retrieve evidence")

    def response(*_args, **kwargs):
        calls["respond"] += 1
        return (
            {"response_text": "你好，很高兴见到你。", "presentation_mode": "conversation", "structured_content": {}},
            kwargs.get("summary"),
            kwargs.get("compaction") or {},
            {"response_generation": 1},
        )

    engine._route = unexpected_route
    engine._retrieve = unexpected_retrieve
    engine._respond = response
    result = engine.generate(record, "job")

    assert calls == {"route": 0, "retrieve": 0, "respond": 1}
    assert result.evidence_summary == []
    assert result.retrieval_summary["retrieval_mode"] == "skipped_fast_path"
    assert result.retrieval_summary["retrieval_skipped"] is True
    assert result.retrieval_summary["skip_reason"] == "low_risk_lightweight_conversation"
    assert result.retrieval_summary["planner_status"] == "skipped"
    assert result.routing.routing_source == "deterministic_fast_path"


def test_clinical_questions_with_product_words_still_retrieve():
    record = SimpleNamespace(workflow="support", output_language="auto")
    for question in ("治疗焦虑有什么 evidence 吗？", "我的性功能下降，怎么办？", "What evidence is there for CBT?", "这个模型认为我有抑郁症，我应该怎么办？", "哪些知识可以帮助我戒酒？"):
        assert LLMSupportEngine._fast_path_route(record, question) is None


def test_lightweight_turn_uses_one_short_response_and_preserves_summary(tmp_path):
    import json
    from domains.theraskill.theraskill_support.contracts import SupportMessage, SupportPublicSession, SupportSessionRecord
    class Client:
        calls = []
        def chat(self, prompt, payload, **kwargs):
            self.calls.append((prompt, payload))
            return json.dumps({"response_text": "你好！", "presentation_mode": "conversation", "structured_content": {}})
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.client = Client()
    engine.work_root = tmp_path
    engine.model_name = "test-model"
    engine.memory_provider = None
    engine.max_runtime_prompt_tokens = 1800
    def forbidden(*args, **kwargs):
        raise AssertionError("Simple turn must not load a therapeutic skill, route, retrieve or compact")
    engine._skill_context = engine._route = engine._retrieve = engine._compact_history = forbidden
    record = SupportSessionRecord(public=SupportPublicSession(session_id="fast-prompt", messages=[
        SupportMessage(message_id="hello", sequence=1, role="user", text="你好")]),
        knowledge_release_id="test", conversation_summary="Previous context. " * 3000, summary_through_message=0)
    result = engine.generate(record, "one-model")
    assert len(engine.client.calls) == 1
    prompt, payload = engine.client.calls[0]
    assert "brief conversational assistant" in prompt
    assert "skill_guidance" not in payload and "retrieved_context" not in payload
    assert len(payload["conversation_summary"]) <= 2000
    assert result.conversation_summary == record.conversation_summary
    assert result.retrieval_summary["retrieval_skipped"] is True
    assert result.latency_ms["routing"] == result.latency_ms["retrieval"] == 0


def test_explicit_missing_selection_does_not_resolve_active_index_again(monkeypatch, tmp_path):
    from domains.theraskill.hybrid_retrieval import selected_retrieval_paths
    monkeypatch.setattr("theraskill_agent.resources.active_index", lambda: (_ for _ in ()).throw(AssertionError("must not reselect")))
    paths = selected_retrieval_paths(tmp_path / "provider.yaml", tmp_path / "cache", selection=None)
    assert paths.sqlite_fts.name == "phase6_retrieval_fts.sqlite"


def test_retrieval_fallback_stays_on_pinned_path_during_cache_change(monkeypatch, tmp_path):
    import domains.theraskill.theraskill_support.engine as module
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    pinned = SimpleNamespace(sqlite_fts=tmp_path / "original.sqlite")
    replacement = SimpleNamespace(sqlite_fts=tmp_path / "new.sqlite")
    engine.work_root, engine.model_config_path, engine.project_root = tmp_path, tmp_path / "model.yaml", tmp_path
    engine.top_k = 8
    class Retriever:
        def search(self, *args, **kwargs):
            engine.retrieval_paths = replacement
            raise module.HybridRetrievalError("synthetic unavailable")
    engine.retrieval_paths, engine.retriever = pinned, Retriever()
    monkeypatch.setattr(module, "selected_retrieval_paths", lambda *a, **kw: pinned)
    monkeypatch.setattr("theraskill_agent.resources.active_index", lambda: (tmp_path, {"index_id": "old-index", "input": {"knowledge_sha256": "old-sha"}}))
    def fallback(_root, path, *_args, **_kwargs):
        assert path == pinned.sqlite_fts
        return {"items": [{"release_unit_id": "unit"}]}
    monkeypatch.setattr(module, "search_policy_lexical", fallback)
    result, *_ = engine._retrieve("session", "job", SimpleNamespace(retrieval_query="support", primary_skill="emotional-support"))
    assert result["items"][0]["source_index_id"] == "old-index"
