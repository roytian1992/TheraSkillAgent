from theraskill_agent.workspace_router import route_workspace


def test_explicit_workspace_requests_are_routed_without_model_call():
    assert route_workspace("帮我设计一门首次会谈课程，生成 3 个案例").to_dict()["workspace"] == "learning"
    assert route_workspace("把患者和医师的语音转录整理并分析").to_dict()["workspace"] == "transcript"
    assert route_workspace("我想设计 schema 并抽取知识").to_dict()["workspace"] == "extraction"
    assert route_workspace("生成一个高难度模拟患者").to_dict()["workspace"] == "simulation"
    assert route_workspace("帮我重建知识索引 embedding").to_dict()["workspace"] == "knowledge"
    assert route_workspace("运行一次回应回归测试，用 llm as judge").to_dict()["workspace"] == "evaluation"
    assert route_workspace("怎么配置 LLM API").to_dict()["workspace"] == "settings"
    assert route_workspace("打开任务历史，恢复上次任务").to_dict()["workspace"] == "tasks"


def test_support_questions_and_ambiguous_requests_stay_in_assistant():
    assert route_workspace("我最近很焦虑，怎么办？").to_dict()["handoff_ready"] is False
    assert route_workspace("我想了解一些心理知识").to_dict()["workspace"] == "support"
    assert route_workspace("课程和对话都想看看").to_dict()["handoff_ready"] is False
