"""Reject teaching inputs that an exact-objective planner can never satisfy."""
import pytest

from domains.theraskill.theraskill_simulation.course_content import normalize_course_content
from domains.theraskill.theraskill_simulation.course_generation import _normal
from domains.theraskill.theraskill_simulation.scenario_generation import ScenarioGenerationBatchRequest


@pytest.mark.parametrize("descriptions", [
    ["Reflect emotions", "Reflect emotions"],
    ["Reflect emotions", "REFLECT EMOTIONS!"],
    ["Reflect emotions", "Ｒｅｆｌｅｃｔ ｅｍｏｔｉｏｎｓ"],
    ["Case 1: Reflect emotions", "Case 2: Reflect emotions"],
    ["反映情绪", "反映 情绪。"],
    ["..."], ["🙂"],
])
def test_invalid_objective_descriptions_fail_before_course_or_batch_is_created(descriptions):
    objectives = [{"id": f"goal-{index}", "description": text, "observable_behaviors": ["Check understanding"]}
                  for index, text in enumerate(descriptions)]
    keys = [_normal(text) for text in descriptions]
    assert not all(keys) or len(set(keys)) < len(keys)
    with pytest.raises(ValueError, match="descriptions must"):
        normalize_course_content(objectives, {}, [])
    with pytest.raises(ValueError, match="descriptions must"):
        ScenarioGenerationBatchRequest(course_title="Practice course", brief="Practise emotional reflection in two cases",
                                       learning_objectives=objectives)


def test_distinct_teacher_objectives_retain_exact_original_wording():
    descriptions = ["Reflect the client's emotion.", "邀请来访者纠正自己的理解。"]
    normalized = normalize_course_content([{"id": str(index), "description": description}
                                          for index, description in enumerate(descriptions)], {}, [])
    assert [row["description"] for row in normalized["learning_objectives"]] == descriptions
