from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from theraskill_agent.extraction.contracts import ExtractionPlan, PlanError
from theraskill_agent.extraction.evidence import evidence_report
from theraskill_agent.extraction.executor import extract_records, validate_output


def nested_plan(**kwargs):
    return ExtractionPlan(
        goal="Extract source-grounded observations", evidence_fields=("items.evidence",),
        target_schema={"type": "object", "additionalProperties": False,
            "properties": {
                "items": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"claim": {"type": "string", "minLength": 1}, "evidence": {"type": "string", "minLength": 1}},
                    "required": ["claim", "evidence"]}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
            "required": ["items", "confidence"]}, **kwargs)


def simple_plan(**kwargs):
    return ExtractionPlan(goal="Extract observations", target_schema={"type": "object",
        "properties": {"claim": {"type": "string"}, "evidence": {"type": "string"}},
        "required": ["claim", "evidence"]}, **kwargs)


def quote_only_plan(**kwargs):
    return ExtractionPlan(goal="Collect verbatim quotes", target_schema={"type": "object",
        "properties": {"evidence": {"type": "string"}}, "required": ["evidence"]}, **kwargs)


def candidate(claim="The speaker feels worried.", evidence="I feel worried."):
    return {"items": [{"claim": claim, "evidence": evidence}], "confidence": .8}


def review(verdict="pass"):
    return {"verdict": verdict, "findings": [] if verdict == "pass" else [
        {"path": "$.items[0].claim", "issue": "The diagnosis is unsupported by this source."}], "summary": "Checked against source."}


class ScriptedClient:
    model = "fixture-local-model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, system_prompt, payload, **kwargs):
        self.calls.append((system_prompt, payload))
        return json.dumps(next(self.responses))


def test_nested_schema_rejects_missing_quotes_out_of_range_and_extra_fields():
    errors = validate_output({"items": [{"claim": "", "other": 1}], "confidence": 2}, nested_plan().target_schema)
    assert any("$.items[0].evidence: missing" in item for item in errors)
    assert any("$.items[0].other: unexpected" in item for item in errors)
    assert any("$.items[0].claim: violates minLength" in item for item in errors)
    assert any("$.confidence: violates maximum" in item for item in errors)


@pytest.mark.parametrize("constraint", [{"format": "date-time"}, {"oneOf": [{"type": "string"}]}, {"pattern": "x+"}, {"$ref": "https://example.invalid/schema"}])
def test_unknown_nested_constraints_are_rejected(constraint):
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string", **constraint}}}}
    with pytest.raises(PlanError, match="unsupported schema keywords"):
        ExtractionPlan(goal="test", target_schema=schema)


def test_nullable_and_json_boolean_equality():
    schema = {"type": "object", "properties": {"value": {"type": ["number", "null"], "enum": [1, None]}}}
    assert validate_output({"value": None}, schema) == []
    assert validate_output({"value": True}, schema)
    schema["properties"]["value"] = {"type": "string"}
    assert validate_output({"value": None}, schema)


def test_mixed_true_and_fabricated_evidence_cannot_pass():
    output = candidate()
    output["items"].append({"claim": "Invented", "evidence": "Fabricated quotation."})
    report = evidence_report(output, {"text": "I feel worried."}, nested_plan())
    assert report["status"] == "needs_review"
    assert report["grounded_fields"] == []
    assert [quote["status"] for quote in report["quotes"]] == ["grounded", "ungrounded"]
    assert report["quotes"][0]["source_matches"] == [{"source_path": "$.text", "start": 0, "end": 15}]


def test_missing_evidence_is_reported_per_array_item():
    output = candidate()
    output["items"].append({"claim": "another claim"})
    report = evidence_report(output, "I feel worried.", nested_plan())
    assert report["status"] == "missing"
    assert report["missing_paths"] == ["$.items[1].evidence"]


def test_quotes_match_raw_newlines_and_cannot_cross_source_fields():
    plan = simple_plan()
    quote = '他说："难过"\n第二行'
    report = evidence_report({"claim": "a", "evidence": quote}, {"turns": [{"text": "前缀" + quote}]}, plan)
    assert report["status"] == "pass"
    assert report["quotes"][0]["source_matches"][0]["start"] == 2
    # JSON punctuation/key names cannot supply an invented evidence span.
    assert evidence_report({"evidence": 'left", "b": "right'}, {"a": "left", "b": "right"}, plan)["status"] != "pass"


def test_quote_only_records_explicitly_mark_semantic_review_not_run():
    client = ScriptedClient([{"evidence": "I feel worried."}])
    units, failures = extract_records([{"text": "I feel worried."}], quote_only_plan(), client)
    assert not failures and len(client.calls) == 1
    assert units[0]["qa"]["semantic_review"]["verdict"] == "not_run"
    assert units[0]["qa"]["semantic_review"]["reason"] == "verbatim_quotes_only"


def test_short_simple_claim_still_requires_semantic_review():
    bad = {"claim": "The speaker has suicidal intent.", "evidence": "I feel worried."}
    client = ScriptedClient([bad, review("reject"), bad, review("reject")])
    units, failures = extract_records([{"text": "I feel worried."}], simple_plan(), client)
    assert not units and len(client.calls) == 4
    assert failures[0]["qa"]["semantic_review"]["verdict"] == "reject"
    assert "non_evidence_fields" in failures[0]["workflow"]["review_reasons"]


def test_quote_only_schema_cannot_hide_extra_generated_claims():
    output = {"evidence": "I feel worried.", "extra_claim": "The speaker feels worried."}
    client = ScriptedClient([output, review()])
    units, failures = extract_records([{"text": "I feel worried."}], quote_only_plan(), client)
    assert not failures and len(client.calls) == 2
    assert units[0]["qa"]["semantic_review"]["verdict"] == "pass"


def test_complex_record_requires_independent_review():
    client = ScriptedClient([candidate(), review()])
    units, failures = extract_records([{"text": "I feel worried."}], nested_plan(), client)
    assert not failures and len(units) == 1 and len(client.calls) == 2
    assert units[0]["qa"]["semantic_review"]["verdict"] == "pass"
    stages = units[0]["provenance"]["workflow"]["stages"]
    assert [stage["stage"] for stage in stages] == ["extract", "review"]
    assert all(len(stage["prompt_sha256"]) == 64 for stage in stages)


def test_semantic_repair_requires_re_review_of_repaired_candidate():
    bad = candidate(claim="The speaker has a diagnosed disorder.")
    fixed = candidate()
    client = ScriptedClient([bad, review("revise"), fixed, review()])
    units, failures = extract_records([{"text": "I feel worried."}], nested_plan(), client)
    assert not failures and units[0]["content"] == fixed
    assert units[0]["qa"]["repair_attempts"] == 1
    assert client.calls[2][1]["semantic_review"]["findings"]
    assert client.calls[3][1]["candidate"] == fixed
    assert len(client.calls) == 4


def test_repair_does_not_bypass_evidence_validation_or_create_approved_unit():
    client = ScriptedClient([candidate(evidence="invented"), candidate(evidence="still invented")])
    units, failures = extract_records([{"text": "I feel worried."}], nested_plan(), client)
    assert not units and len(client.calls) == 2
    assert failures[0]["reason"] == "quality_validation_failed"
    assert failures[0]["evidence"]["status"] == "needs_review"
    assert failures[0]["candidate_draft"] == candidate(evidence="still invented")


def test_semantic_review_cannot_pass_with_unresolved_findings():
    bad_review = {**review("revise"), "verdict": "pass"}
    client = ScriptedClient([candidate(), bad_review])
    units, failures = extract_records([{"text": "I feel worried."}], nested_plan(), client, parse_retries=0)
    assert not units and failures[0]["reason"] == "extraction_failed"
    assert "pass requires no findings" in failures[0]["error"]


def test_semantic_rejection_is_preserved_after_bounded_repair():
    client = ScriptedClient([candidate(), review("reject"), candidate(), review("reject")])
    units, failures = extract_records([{"text": "I feel worried."}], nested_plan(), client)
    assert not units and len(client.calls) == 4
    assert failures[0]["qa"]["semantic_review"]["verdict"] == "reject"


def test_trial_spans_entire_input_and_retry_keeps_original_sample(monkeypatch):
    seen = []
    def chat(client, prompt, payload, **kwargs):
        if prompt.startswith("Independently review"):
            return review()
        seen.append(payload["source_id"])
        return {"claim": "observation", "evidence": payload["source_record"]["text"]}
    monkeypatch.setattr("theraskill_agent.extraction.executor.chat_json_with_repair", chat)
    records = [{"text": f"record number {i}"} for i in range(10)]
    units, failures = extract_records(records, simple_plan(), SimpleNamespace(model="fake"), limit=3)
    assert not failures and seen == ["record_000000", "record_000004", "record_000009"]
    seen.clear()
    retried, failures = extract_records(records, simple_plan(), SimpleNamespace(model="fake"), limit=3,
                                       skip_source_ids={units[0]["source_id"], units[1]["source_id"]})
    assert not failures and seen == ["record_000009"] and len(retried) == 1


def test_cancellation_between_model_stages_does_not_checkpoint_partial_record():
    client = ScriptedClient([candidate()])
    progress = []
    units, failures = extract_records([{"text": "I feel worried."}], nested_plan(), client,
        should_cancel=lambda: bool(client.calls), on_progress=progress.append)
    assert units == failures == progress == []
    assert len(client.calls) == 1


def test_simple_record_can_request_semantic_review():
    client = ScriptedClient([{"evidence": "I feel worried."}, review()])
    units, failures = extract_records([{"text": "I feel worried."}], quote_only_plan(semantic_review="always"), client)
    assert not failures and len(client.calls) == 2
    assert units[0]["provenance"]["workflow"]["review_reasons"] == ["explicit_always"]
