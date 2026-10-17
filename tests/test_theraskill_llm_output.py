import io
import json

import pytest

from domains.theraskill.theraskill_llm import ModelOutputError, OpenAICompatibleClient
from domains.theraskill.theraskill_support.contracts import RoutingDecision
from domains.theraskill.theraskill_support.engine import LLMSupportEngine


@pytest.mark.parametrize('finish_reason,content', [('length', '{}'), ('stop', '')])
def test_incomplete_model_output_is_not_accepted_or_retried_as_transport(finish_reason, content):
    class Opener:
        calls = 0
        def open(self, *args, **kwargs):
            self.calls += 1
            return io.BytesIO(json.dumps({'choices': [{
                'finish_reason': finish_reason, 'message': {'content': content},
            }]}).encode())
    client = OpenAICompatibleClient({'name': 'configured-model', 'base_url': 'http://unused', 'retry_attempts': 2})
    opener = Opener()
    client.opener = opener
    with pytest.raises(ModelOutputError):
        client.chat('Return JSON', {})
    assert opener.calls == 1


@pytest.mark.parametrize('recovered', [True, False])
def test_support_valid_json_with_no_response_uses_bounded_schema_recovery(monkeypatch, recovered):
    monkeypatch.setattr('domains.theraskill.theraskill_llm.time.sleep', lambda _: None)
    class Client:
        calls = 0
        def chat(self, *args, **kwargs):
            self.calls += 1
            return json.dumps({'response_text': 'I hear you.'}) if recovered and self.calls == 2 else '{}'
    engine = LLMSupportEngine.__new__(LLMSupportEngine)
    engine.client = Client()
    engine.response_prompt = 'Return JSON with response_text.'
    engine.max_runtime_prompt_tokens = 12000
    engine._skill_context = lambda skill: {}
    engine._retrieved_context = lambda bundle: []
    routing = RoutingDecision(primary_skill='emotional-support', task_type='support',
                              detected_language='en', risk_level='none_observed',
                              requires_immediate_safety_attention=False,
                              retrieval_query='work stress', user_intent_summary='support')
    args = (None, routing, {}, 'I feel stressed.', None, [], {}, 0, {})
    if recovered:
        result, *_ = engine._respond(*args)
        assert result['response_text'] == 'I hear you.'
    else:
        with pytest.raises(ValueError, match='nonempty response_text'):
            engine._respond(*args)
    assert engine.client.calls == 2
