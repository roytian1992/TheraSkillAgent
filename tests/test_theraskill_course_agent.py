from domains.theraskill.theraskill_simulation.course_agent import course_agent_turn


def test_course_agent_asks_only_for_missing_decisions():
    result = course_agent_turn("我想做一门首次会谈中的情绪验证课程")
    assert result["suggested_action"] == "ask"
    assert "scenario_count" in result["missing"]
    assert result["draft"]["template_id"] == "foundation_first_session"
    assert result["draft"]["description"]


def test_course_agent_proposes_visible_plan_before_tools():
    result = course_agent_turn(
        "创建风险识别与安全沟通课程，生成 4 个中等难度、需关注风险的案例，然后开始",
        language="zh",
    )
    assert result["suggested_action"] == "confirm_blueprint"
    assert result["ready"] is True
    assert result["draft"]["scenario_count"] == 4
    assert result["draft"]["template_id"] == "risk_and_safety_basics"
    assert result["draft"]["difficulty_band"] == "medium"
    assert result["draft"]["risk_band"] == "elevated"
    assert result["tool_plan"] == ["course_blueprint", "knowledge_retrieval", "scenario_batch_generation"]


def test_course_agent_does_not_create_or_execute_from_ambiguous_ack():
    result = course_agent_turn("好的", {"description": "首次会谈中的情绪验证", "scenario_count": 3})
    assert result["suggested_action"] == "confirm_blueprint"
    assert result["tool_plan"]
    # The route only proposes an action; execution remains an explicit UI step.
    assert "course_id" not in result and "scenario_ids" not in result


def test_course_agent_works_in_english():
    result = course_agent_turn(
        "Create a first session validation course with 3 low risk cases and start",
        language="en",
    )
    assert result["suggested_action"] == "confirm_blueprint"
    assert result["draft"]["scenario_count"] == 3
    assert result["draft"]["risk_band"] == "low"
