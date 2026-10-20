"""Bilingual, authored teaching starters, not a reproduced clinical rating scale."""

COMPETENCIES_EN = {
    'alliance_and_validation': ('Alliance and validation', 'Build a collaborative relationship, reflect emotion and meaning, and respect the client’s autonomy.', ['Reflect a specific emotion', 'Check whether the client feels understood', 'Respect refusal and pace']),
    'assessment_and_formulation': ('Assessment and formulation', 'Organize context, timing and maintaining factors into a tentative, testable account.', ['Use open questions', 'Separate facts, hypotheses and missing information', 'Check the working hypothesis in the client’s language']),
    'risk_and_safety': ('Risk and safety communication', 'Recognize risk cues, clarify immediate safety and follow the institution’s escalation procedures.', ['Ask about risk directly without judgment', 'Clarify immediate safety and available support', 'Escalate concerns beyond the training role']),
    'intervention_fidelity': ('Choosing and adapting interventions', 'Connect an intervention to the agreed goal and client preferences, and check its effects.', ['Explain the purpose and options', 'Ask permission before a practice', 'Adapt to feedback instead of applying a technique mechanically']),
    'culture_boundaries_closure': ('Culture, boundaries and closure', 'Explore cultural meaning and power differences, explain role boundaries and plan next steps collaboratively.', ['Invite correction of assumptions', 'Explain role and confidentiality boundaries', 'Summarize and agree on next steps']),
}
TEMPLATES_EN = {
    'foundation_first_session': ('First-session foundations', 'Practice collaboration, clarify the client’s goals and agree on a structure for the first meeting.', ['Worked example', 'Practice with hints', 'Practice without hints', 'Independent practice']),
    'risk_and_safety_basics': ('Risk recognition and safety communication', 'Practice asking about risk, clarifying safety and following escalation procedures in simulated cases.', ['Process card', 'Decision-point hints', 'Scenario practice', 'Independent reflection']),
    'integrated_case_formulation': ('From assessment to a working hypothesis', 'Organize information into a testable account and revise it collaboratively during conversation.', ['Structured outline', 'Compare two hypotheses', 'Missing-information case', 'Transfer across contexts']),
}


def localize_catalog(competencies, templates, language):
    if language != 'en':
        return competencies, templates
    cs, ts = [], []
    for item in competencies:
        values = COMPETENCIES_EN.get(item.competency_id)
        cs.append(item.model_copy(update=dict(name=values[0], description=values[1], observable_behaviors=values[2])) if values else item)
    for item in templates:
        values = TEMPLATES_EN.get(item.template_id)
        ts.append(item.model_copy(update=dict(title=values[0], description=values[1], scaffolding=values[2])) if values else item)
    return cs, ts


# Each exercise provides the actual client utterance, a narrow task, a hint and
# a discussion example. Examples are alternatives to discuss, never answer keys.
EXERCISES = {
    'alliance_and_validation': {
        'zh': ('当来访者描述重要感受，或指出你没有理解自己时。', '泛泛安慰、急于建议，或把自己的猜测说成对方的事实。', [
            ('反映感受', '来访者：“昨天会上我又没敢发言。回家以后一直想，大家肯定觉得我很没用。我不想听你说下次勇敢点。”', '写一到两句回应，体现你听到的情绪和意义，并留出纠正空间。', '先贴近被评价的担忧；此刻不急着安排训练。', '我的理解来自哪句话？是否把自己的推测当成了事实？', '“那一刻没能说出来，之后又一直担心别人怎么看你，听起来很难受。我这样理解贴近你的感受吗？”', '这个版本把情绪理解作为待核实的假设。其他贴合语境的措辞同样可以成立。'),
            ('修复理解偏差', '咨询师：“你可能就是太在乎父母看法了。”\n来访者：“不是！你跟他们一样，总觉得问题在我。”', '用一到两句回应承认理解偏差，并邀请对方说明。', '回应此刻的关系裂痕，暂缓解释自己的本意。', '我有没有为自己的理解辩解？对方是否有纠正我的空间？', '“我刚才太快下了判断，让你觉得又被归咎了。对不起。你愿意告诉我，我漏掉了什么吗？”', '承认影响并邀请修正，比重复原先的解释更有助于继续了解。')]),
        'en': ('When the client shares meaningful feelings or says you have misunderstood them.', 'Generic reassurance, premature advice, or treating an inference as a fact.', [
            ('Reflect emotion', 'Client: “I stayed silent in yesterday’s meeting again. All evening I thought they must see me as useless. Please don’t just tell me to be braver.”', 'Write one or two sentences reflecting emotion and meaning, with room for correction.', 'Attend to the fear of being judged before suggesting a practice.', 'Which words support my understanding? Have I stated an inference as a fact?', '“Being unable to speak and then worrying how they see you sounds painful. Is that close to what it was like?”', 'The response offers a tentative understanding. Other context-sensitive wording can work too.'),
            ('Repair a misunderstanding', 'Counselor: “Maybe you care too much about your parents’ opinions.”\nClient: “No! You are just like them, acting as if it is all my fault.”', 'Write one or two sentences acknowledging the misunderstanding and inviting correction.', 'Address the rupture before explaining your intentions.', 'Am I defending myself? Can the client correct my understanding?', '“I jumped to a conclusion and left you feeling blamed again. I’m sorry. Would you tell me what I missed?”', 'Acknowledge the impact and invite correction rather than repeating the original interpretation.')]),
    },
    'assessment_and_formulation': {
        'zh': ('当问题描述笼统、信息冲突或工作假设尚未确定时。', '连续盘问，或凭一个线索直接给出诊断。', [
            ('澄清一个具体情境', '来访者：“最近我什么都做不好。周末和朋友出去时还行，可一想到周一上班就难受。我不知道咨询能帮什么。”', '提出一个开放式问题，澄清具体情境或对本次会谈的期待；不要直接诊断。', '选择一个问题，把“什么都做不好”落到一次具体经历。', '这个问题会帮助区分哪些可能性？哪些仍然未知？', '“能跟我讲讲，最近一次想到周一上班就难受时，发生了什么、你脑子里出现了什么吗？”', '问题从具体经历收集信息；当前片段还不足以判断原因或诊断。')]),
        'en': ('When a problem is broad, information conflicts, or a hypothesis needs checking.', 'A barrage of questions or diagnosing from a single cue.', [
            ('Clarify one situation', 'Client: “I can’t do anything right lately. I feel okay with friends on weekends, but thinking about Monday at work makes me dread it. I don’t know what counseling could help.”', 'Ask one open question to clarify a situation or the client’s hopes for this meeting; do not diagnose.', 'Ground the broad statement in one recent experience.', 'Which possibilities would this clarify? What remains unknown?', '“Could you walk me through the last time you felt that dread about Monday—what was happening and what went through your mind?”', 'The question gathers context. This excerpt does not establish a cause or diagnosis.')]),
    },
    'risk_and_safety': {
        'zh': ('当出现可能涉及自身安全的表达，需要澄清其含义时。', '用含糊暗示替代直接询问，或保证绝对保密。', [
            ('澄清风险线索', '模拟来访者：“这两天我觉得自己是个负担，有时候想，要是我不在了就好了。”\n场景：你在有督导支持的训练门诊，机构有既定升级流程。', '写出下一步直接、平静的风险澄清问题，并说明若有即时危险将采用的支持流程。', '明确询问当前是否在想伤害自己或结束生命；处理流程遵循所在机构规定。', '我是否直接询问了安全？哪些信息还需要进一步确认？', '“谢谢你告诉我。我想认真了解你的安全：你现在有伤害自己或结束生命的想法吗？如果有立即的危险，我会联系督导并按我们的紧急支持流程一起处理。”', '这是初始澄清，不等于完整风险评估或安全计划；后续根据回应和机构流程继续。')]),
        'en': ('When a statement may concern personal safety and its meaning needs clarification.', 'Vague hints instead of direct questions, or promising absolute confidentiality.', [
            ('Clarify a risk cue', 'Simulated client: “For two days I have felt like a burden. Sometimes I think it would be better if I were not here.”\nSetting: a supervised training clinic with an established escalation procedure.', 'Write a direct, calm next question about safety, and explain the support process if danger is immediate.', 'Ask clearly about current thoughts of self-harm or ending life; follow the institution’s procedures.', 'Did I ask directly about safety? What still needs clarification?', '“Thank you for telling me. I want to understand your safety: are you thinking about harming yourself or ending your life right now? If there is immediate danger, I will involve my supervisor and our urgent support process.”', 'This is an opening clarification, not a complete risk assessment or safety plan. Follow-up depends on the response and local procedure.')]),
    },
    'intervention_fidelity': {
        'zh': ('已明确一个共同目标，并准备提出练习或调整方法时。', '未经同意安排技巧，或在对方不适时要求坚持。', [
            ('解释选择并邀请决定', '来访者：“我想睡前少一些反复想事情。上次有人让我做呼吸练习，我反而更紧张了。”', '用两三句话说明一种可讨论的下一步及其目的，邀请对方选择；不要承诺效果。', '先接住既往不适经验，再提出可拒绝的选项。', '建议怎样连接到对方的目标？我有没有允许拒绝？', '“上次的练习让你更紧张，我们可以先不重复它。要不要一起看看睡前反复想的内容，找出最困扰你的部分？也可以先谈谈你觉得更可行的方法。”', '重点是目标、选择和合作，而非是否使用某个固定技巧。')]),
        'en': ('After agreeing on a goal and before proposing or adapting a practice.', 'Prescribing a technique without permission or insisting despite discomfort.', [
            ('Explain options and invite a choice', 'Client: “I want less overthinking before bed. Someone previously made me do breathing exercises, and I felt more tense.”', 'In two or three sentences, explain a possible next step and its purpose, and invite a choice without promising an outcome.', 'Acknowledge the previous discomfort and offer an option the client may decline.', 'How does the proposal connect to the goal? Can the client decline?', '“That exercise made things harder, so we can leave it aside. Would it help to look together at what comes up before bed and which part troubles you most? We can also start with what feels more workable to you.”', 'Judge the connection to goals, choice and collaboration rather than use of a particular technique.')]),
    },
    'culture_boundaries_closure': {
        'zh': ('当家庭、文化或角色期待影响对问题的理解时。', '用文化标签替代个人经验，或把自己的价值偏好当成目标。', [
            ('邀请修正文化假设', '来访者：“你一直说要独立决定，可我家里重要的事都是一起商量。我不觉得这就是没有主见。”', '回应对方的修正，并提出一个问题了解共同商量对其个人的意义。', '先承认自己的假设，再问对方希望保留与改变什么。', '我是否把自己的价值取向设成了标准？', '“我把独立决定当成了默认方向，忽略了你重视的商量方式。对你来说，这种共同决定有哪些支持，又有哪些地方让你为难？”', '邀请个人描述，避免从文化群体标签推断其偏好。')]),
        'en': ('When family, cultural or role expectations affect the meaning of a problem.', 'Replacing personal experience with a cultural label or imposing the counselor’s values.', [
            ('Invite correction of a cultural assumption', 'Client: “You keep saying I should decide independently. In my family we discuss important decisions together. I don’t think that means I have no mind of my own.”', 'Acknowledge the correction and ask what deciding together means to this person.', 'Recognize the assumption, then explore what the client wants to retain or change.', 'Have I made my own values the standard?', '“I assumed deciding independently should be the goal and missed how you value discussing things together. What feels supportive about that, and where, if anywhere, does it become difficult for you?”', 'Invite the individual’s account rather than inferring preferences from a cultural group label.')]),
    },
}


def exercise_content(competency_id, language):
    content = EXERCISES.get(competency_id)
    if content is None:
        raise ValueError(f'No teaching starter for competency: {competency_id}')
    when, misuse, rows = content[language]
    fields = ('title', 'scenario_text', 'prompt', 'hint', 'reflection_prompt', 'example_response', 'example_discussion')
    return {'when': when, 'misuse': misuse, 'exercises': [dict(zip(fields, row)) for row in rows]}
