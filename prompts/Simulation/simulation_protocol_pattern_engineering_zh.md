# Simulation Protocol Pattern Engineering

你现在处理的不是原始咨询对话，也不是单个 case 的会话抽取。

你的任务是：基于已经稳定发布的上游 knowledge units，整编出可被 patient / therapist / dual-session simulation runtime 直接消费的 protocol-level knowledge。

## 输入边界

- 输入只来自已经完成 projection QA 的上游 knowledge units：
  - `Simulation.patient_state_transition_pattern`
  - `Simulation.therapist_response_policy_pattern`
  - `Simulation.therapist_case_memory_pattern`
- 不重新回读原始咨询对话。
- 不把 runtime prompt wording 当成 evidence。
- 不把 generated rationale、`<think>`、system prompt、review note 当作 observed therapeutic evidence。

## 目标输出

输出一个 `simulation_protocol_pattern` 对象，用于描述某种 simulation mode 下的一类 protocol constraint / rule bundle。

建议字段：

- `protocol_id`
- `simulation_mode`
- `protocol_family`
- `required_input_slots`
- `patient_visible_context`
- `therapist_visible_context`
- `hidden_state_rules`
- `memory_write_rules`
- `memory_read_rules`
- `retrieval_trigger_rules`
- `turn_termination_rules`
- `risk_escalation_rules`
- `synthetic_labeling_rules`
- `review_hooks`
- `non_goals_or_do_not_infer`
- `evidence_unit_ids`
- `quality_status`

## Controlled Labels

`simulation_mode` 只能取：

- `patient_simulator`
- `therapist_simulator`
- `dual_session_simulator`
- `simulation_reviewer`

`protocol_family` 只能取：

- `context_loading`
- `patient_state_visibility`
- `therapist_planning_visibility`
- `hidden_state_update`
- `memory_write`
- `memory_read`
- `retrieval_routing`
- `turn_management`
- `escalation_and_handoff`
- `review_and_labeling`

## Engineering Rules

1. 每条 protocol unit 必须能回指到真实上游 `unit_id`。
2. 优先输出可执行的结构化 rule，而不是长段解释。
3. `patient_visible_context` 与 `hidden_state_rules` 必须分开。
4. `memory_write_rules` 与 `memory_read_rules` 必须分开。
5. `non_goals_or_do_not_infer` 不能为空。
6. 不允许输出诊断、病理推断、未经 evidence 支持的人格判断。
7. 不允许把上游 free-text evidence 原封不动拼成长段 narrative。

## Family Intent

- `context_loading`
  - 定义开局时要加载哪些上下文，以及哪些上下文只对特定角色可见。
- `patient_state_visibility`
  - 定义 patient simulator 可暴露什么表层状态，不可暴露什么隐藏状态。
- `therapist_planning_visibility`
  - 定义 therapist simulator 在规划回应时需要看到哪些结构化上下文。
- `hidden_state_update`
  - 定义 alliance/readiness/disclosure/risk 等隐藏状态如何更新。
- `memory_write`
  - 定义何时把新信息写入 case memory。
- `memory_read`
  - 定义何时从 case memory 读取历史信息辅助生成。
- `retrieval_routing`
  - 定义何时触发 retrieval，以及优先取哪类 knowledge。
- `turn_management`
  - 定义一轮何时继续、何时收束、何时切阶段。
- `escalation_and_handoff`
  - 定义何时升级风险、何时暂停、何时转介或交接。
- `review_and_labeling`
  - 定义 simulation review / labeling 的钩子与边界。

## Style

- 用短句、规则化表达。
- 尽量输出列表，不写空洞大段说明。
- 如果 evidence 只能支持弱规则，明确写成保守条件。
