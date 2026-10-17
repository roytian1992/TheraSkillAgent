from pathlib import Path

from scripts.TheraSkill.check_prompt_coverage import check


def test_all_simulation_prompts_have_english_counterparts() -> None:
    result = check(Path(__file__).parents[1] / "prompts")
    assert result["status"] == "complete", result["missing_english"]
