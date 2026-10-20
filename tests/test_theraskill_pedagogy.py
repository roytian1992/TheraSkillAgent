import pytest

from domains.theraskill.theraskill_simulation.pedagogy import (
    DEFAULT_COMPETENCIES,
    DEFAULT_TEMPLATES,
    ProgressObservation,
    design_course_seed,
    summarize_progress,
)


def test_course_seed_starts_from_template_and_keeps_knowledge_provenance():
    seed = design_course_seed(
        template_id="foundation_first_session",
        scenario_count=4,
        target_level="guided",
        knowledge_units=[
            {
                "release_unit_id": "ku_1",
                "text_preview": "Check whether the client feels understood.",
                "canonical_label": "alliance",
                "evidence_role": "teaching_reference",
            }
        ],
    )
    assert seed.template_id == "foundation_first_session"
    assert seed.scenario_constraints["count"] == 4
    assert seed.competency_ids == ["alliance_and_validation", "assessment_and_formulation"]
    assert seed.knowledge_references[0].unit_id == "ku_1"
    assert seed.knowledge_references[0].quote.startswith("Check")
    assert all(item["observable_behaviors"] for item in seed.objectives)
    assert len(seed.knowledge_cards) == 2
    assert len(seed.micro_practices) == 3
    assert all(item.scenario_text and item.example_response for item in seed.micro_practices)
    assert not any(item.knowledge_unit_ids for item in seed.knowledge_cards)
    assert seed.micro_practices[0].target_behavior


def test_progress_requires_repeated_independent_cross_case_evidence():
    observations = [
        ProgressObservation(attempt_id="a1", competency_id="alliance_and_validation", scenario_id="s1", rating=4, level="independent", transfer_context="first client context"),
        ProgressObservation(attempt_id="a2", competency_id="alliance_and_validation", scenario_id="s2", rating=4.5, level="independent", transfer_context="different client context"),
    ]
    result = summarize_progress(
        observations,
        ["alliance_and_validation"],
        completion_policy={"minimum_attempts": 2, "minimum_independent": 2, "require_transfer": True},
    )
    assert result["status"] == "ready_for_transfer"
    assert result["competencies"][0]["status"] == "transfer"
    assert result["competencies"][0]["transfer_case_count"] == 2


def test_safety_issue_blocks_average_score():
    observations = [
        ProgressObservation(attempt_id="a1", competency_id="risk_and_safety", scenario_id="s1", rating=5, level="independent", safety_issue=True),
        ProgressObservation(attempt_id="a2", competency_id="risk_and_safety", scenario_id="s2", rating=5, level="independent"),
    ]
    result = summarize_progress(observations, ["risk_and_safety"], completion_policy={"minimum_independent": 2})
    assert result["status"] == "blocked_safety"
    assert result["competencies"][0]["status"] == "blocked_safety"


def test_default_catalog_is_valid_and_unknown_template_is_rejected():
    assert {item.template_id for item in DEFAULT_TEMPLATES}
    assert {item.competency_id for item in DEFAULT_COMPETENCIES}
    try:
        design_course_seed(template_id="does_not_exist")
    except ValueError as exc:
        assert "Unknown course template" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown template should fail")


def test_reassessment_counts_once_and_uses_observation_time_not_random_ids():
    rows = [
        dict(attempt_id="a", competency_id="empathy", scenario_id="s2", rating=4,
             level="independent", observed_at="2026-10-20T10:00:00Z"),
        dict(attempt_id="z", competency_id="empathy", scenario_id="s1", rating=2,
             level="guided", observed_at="2026-10-20T08:00:00Z", safety_issue=True),
        dict(attempt_id="z", competency_id="empathy", scenario_id="s1", rating=3,
             level="guided", observed_at="2026-10-20T09:00:00Z", safety_issue=False),
    ]
    result = summarize_progress(rows, ["empathy"])
    state = result["competencies"][0]
    assert state["attempt_count"] == 2
    assert (state["first_rating"], state["latest_rating"]) == (3, 4)
    assert state["safety_issue_count"] == 0
    assert result["signals"]["attempts"] == 2


def test_minimum_attempts_is_required_even_with_high_independent_ratings():
    rows = [dict(attempt_id=f"a{i}", competency_id="empathy", scenario_id=f"s{i}",
                 rating=5, level="independent") for i in range(2)]
    result = summarize_progress(rows, ["empathy"], completion_policy={"minimum_attempts": 4})
    assert result["completed_competencies"] == 0
    repeated = summarize_progress([rows[0], rows[0]], ["empathy"])
    assert repeated["competencies"][0]["attempt_count"] == 1
    assert repeated["completed_competencies"] == 0


@pytest.mark.parametrize("rating", [True, float("nan"), 6, "4"])
def test_dictionary_observations_cannot_bypass_rating_validation(rating):
    with pytest.raises(ValueError):
        summarize_progress([dict(attempt_id="a", competency_id="empathy", scenario_id="s", rating=rating)], ["empathy"])


@pytest.mark.parametrize("template", [item.template_id for item in DEFAULT_TEMPLATES])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_all_teaching_starters_are_actionable_and_saveable(template, language):
    from domains.theraskill.theraskill_simulation.course_content import normalize_course_content
    seed = design_course_seed(template_id=template, language=language).model_dump(mode="json")
    content = normalize_course_content(seed["objectives"],
        {"knowledge_cards": seed["knowledge_cards"], "micro_practices": seed["micro_practices"]}, seed["knowledge_references"])
    assert content["learning_objectives"][0]["rubric_version"]
    assert all(row["scenario_text"] for row in content["learning_units"]["micro_practices"])
    if language == "en":
        assert not any("\u4e00" <= c <= "\u9fff" for c in seed["title"] + seed["micro_practices"][0]["scenario_text"])
