import io
import json
from types import SimpleNamespace

import pytest

from domains.theraskill.theraskill_llm import ModelOutputError, OpenAICompatibleClient
from domains.theraskill.theraskill_support.contracts import RoutingDecision
from domains.theraskill.theraskill_support.engine import LLMSupportEngine


@pytest.mark.parametrize('finish_reason,content', [('length', '{}'), ('stop', '')])
def test_incomplete_model_output_is_not_accepted_after_bounded_recovery(finish_reason, content):
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
    assert opener.calls == 3


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
    args = (SimpleNamespace(output_language='auto'), routing, {}, 'I feel stressed.', None, [], {}, 0, {})
    if recovered:
        result, *_ = engine._respond(*args)
        assert result['response_text'] == 'I hear you.'
    else:
        with pytest.raises(ValueError, match='nonempty response_text'):
            engine._respond(*args)
    assert engine.client.calls == 2


@pytest.mark.parametrize('first', ['length', 'empty'])
def test_shared_client_recovers_complete_result_and_preserves_model_settings(first):
    requests = []
    class Opener:
        def open(self, request, **kwargs):
            requests.append(json.loads(request.data))
            message = {'content': '{"partial":' if first == 'length' else ''}
            if len(requests) == 2:
                message = {'content': '{"response_text":"complete"}'}
            return io.BytesIO(json.dumps({'choices': [{
                'finish_reason': 'length' if len(requests) == 1 and first == 'length' else 'stop',
                'message': message,
            }]}).encode())
    client = OpenAICompatibleClient({'name': 'any-model', 'base_url': 'http://unused',
        'max_output_tokens': 4096, 'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high'})
    client.opener = Opener()
    assert json.loads(client.chat('Return JSON', {}, force_json_response=True)) == {'response_text':'complete'}
    assert len(requests) == 2
    assert requests[1]['max_tokens'] == (8192 if first == 'length' else 4096)
    assert requests[1]['thinking'] == {'type':'enabled'}
    assert requests[1]['reasoning_effort'] == 'high'
    assert requests[1]['messages'] == requests[0]['messages']
    assert client.output_recovery[0]['reason'] == ('truncated' if first == 'length' else 'empty_response')


def test_recovery_stops_at_budget_cap_and_does_not_accept_partial_json():
    budgets=[]
    class Opener:
        def open(self, request, **kwargs):
            budgets.append(json.loads(request.data)['max_tokens'])
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}).encode())
    client=OpenAICompatibleClient({'name':'any-model','base_url':'http://unused','max_output_tokens':16384})
    client.opener=Opener()
    with pytest.raises(ModelOutputError, match='automatic recovery'):
        client.chat('JSON', {})
    assert budgets == [16384, 32768]


def test_bad_credentials_are_not_retried():
    import urllib.error
    class Opener:
        calls=0
        def open(self, request, **kwargs):
            self.calls+=1
            raise urllib.error.HTTPError(request.full_url,401,'unauthorized',{},io.BytesIO(b'bad credentials'))
    client=OpenAICompatibleClient({'name':'any-model','base_url':'http://unused','retry_attempts':2})
    client.opener=Opener()
    with pytest.raises(RuntimeError,match='HTTP 401'):
        client.chat('JSON',{})
    assert client.opener.calls == 1
