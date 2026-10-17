from __future__ import annotations

import json

from plugins.theraskill import TOOL_DEFINITIONS
from theraskill_agent.transcript import analyze_transcript, build_bundle, load_transcript, parse_transcript
from theraskill_agent.reviews import build_review_plan
from theraskill_agent.workflows import SkillWorkflow, WorkflowStore, interaction_for_hermes


def test_parse_transcript_preserves_raw_text_and_detects_roles() -> None:
    document = parse_transcript(
        """患者：我最近总是失眠，嗯嗯。\n[00:01:02] 医师: 听起来你很疲惫。\n这是补充的一句。\n""",
        source_path="sample.txt",
    )

    assert document.language == "zh"
    assert len(document.turns) == 2
    assert document.turns[0].speaker_role == "patient"
    assert document.turns[1].speaker_role == "clinician"
    assert document.turns[0].raw_text.endswith("嗯嗯。")
    assert document.turns[0].correction_candidates
    assert document.turns[1].timestamp == "00:01:02"
    assert "这是补充的一句。" in document.turns[1].normalized_text


def test_transcript_analysis_routes_risk_and_review_skills() -> None:
    document = parse_transcript("患者: 我想自杀。\n咨询师: 我听到了。")
    analysis = analyze_transcript(document)

    assert analysis["risk_screening"]["status"] == "review_required"
    assert analysis["risk_screening"]["flags"][0]["turn_id"] == "turn_0001"
    assert analysis["recommended_skills"][:2] == ["risk-triage", "transcript-quality-reviewer"]
    assert "transcript-evaluator" in analysis["recommended_skills"]
    assert analysis["risk_screening"]["flags"][0]["screening_only"] is True


def test_bundle_is_reproducible_and_can_exclude_raw(tmp_path) -> None:
    document = parse_transcript("patient: I feel unsafe.\ntherapist: I hear you.", source_path="x.txt")
    artifacts = build_bundle(document, analyze_transcript(document), tmp_path, include_raw=False, raw_text="secret")

    assert (tmp_path / "manifest.json").is_file()
    payload = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert "raw_text" not in payload
    assert artifacts["analysis"].endswith("analysis.json")


def test_workflow_store_provides_hermes_rendering_envelope(tmp_path) -> None:
    workflow = SkillWorkflow(kind="transcript_review")
    workflow.wait_for("needs_input", {"kind": "form", "title": "Review", "actions": ["confirm"]})
    store = WorkflowStore(tmp_path)
    store.save(workflow)

    restored = store.get(workflow.workflow_id)
    assert restored is not None
    envelope = interaction_for_hermes(restored)
    assert envelope["workflow_id"] == workflow.workflow_id
    assert envelope["kind"] == "form"


def test_transcript_tool_is_registered_with_structured_operations() -> None:
    definitions = {name: schema for name, schema, *_rest in TOOL_DEFINITIONS}
    assert "theraskill_transcript" in definitions
    operations = definitions["theraskill_transcript"]["properties"]["parameters"]["properties"]["operation"]["enum"]
    assert operations == ["analyze", "inspect", "package", "review"]


def test_review_plan_loads_skills_and_cites_turn_contract() -> None:
    document = parse_transcript("患者: 我最近很害怕。\n医师: 我听到了。")
    analysis = analyze_transcript(document)
    plans = build_review_plan(document, analysis, skills=["transcript-quality-reviewer"], max_turns=1)

    assert len(plans) == 1
    assert plans[0]["skill"] == "transcript-quality-reviewer"
    assert plans[0]["user_payload"]["turns"][0]["turn_id"] == "turn_0001"
    assert plans[0]["user_payload"]["output_contract"]["findings"][0]["turn_ids"] == ["turn_0001"]


def test_structured_and_subtitle_adapters_share_normalized_pipeline(tmp_path) -> None:
    structured = tmp_path / "dialogue.jsonl"
    structured.write_text(
        '{"speaker":"patient","text":"I feel unsafe.","timestamp":"00:00:01"}\n'
        '{"role":"therapist","content":"I hear you."}\n',
        encoding="utf-8",
    )
    document, raw = load_transcript(structured)
    assert document.metadata["source_format"] == "jsonl"
    assert document.raw_text_sha256
    assert document.turns[0].speaker_role == "patient"
    assert raw.startswith('{"speaker"')

    subtitles = tmp_path / "dialogue.srt"
    subtitles.write_text("1\n00:00:01,000 --> 00:00:03,000\nI feel tired.\n\n", encoding="utf-8")
    subtitle_document, _ = load_transcript(subtitles)
    assert subtitle_document.metadata["source_format"] == "srt"
    assert subtitle_document.turns[0].timestamp == "00:00:01"
