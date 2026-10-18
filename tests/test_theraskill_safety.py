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
