# TheraSkillAgent 对话文本加工与评测流水线

## 目标

输入可以是患者与医师/咨询师的纯文本对话，也可以是语音转录后的混合语言文本。输入允许存在说话人标签不一致、缺少标点、断行不稳定、错别字、重复词和口语填充词。

系统必须保留原始证据，规范化结果和模型建议不能覆盖原文。风险识别和治疗过程分析只作为研究、训练和审核信号，不构成诊断、治疗建议或最终风险判断。

## 输入

支持当前本地文本读取能力能够读取的 UTF-8、GB18030、Big5 和 Latin-1 兼容文本。文本可以使用以下常见格式：

```text
患者：最近总是睡不好
医师: 听起来你很疲惫

[00:01:02] Therapist: Can you tell me more?
```

也支持连续行：没有新说话人标签的行会附加到上一轮，并保留源行号。无法可靠识别的标签会标记为 `unknown`，不会由系统强行猜测。

文件扩展名为 `.json`/`.jsonl` 时，会识别 `turns`、`messages`、`dialogue` 或
`segments` 数组中的 `speaker`/`role`、`text`/`content`、`timestamp` 字段；
`.srt`/`.vtt` 会按字幕时间轴拆成轮次。结构化源会保留原始文件哈希，同时把
规范化后的解析视图写入 `transcript.json`，方便追溯适配过程。

## 规范化输出

`transcript.json` 是完整文档，`turns.jsonl` 是逐轮处理文件。每一轮至少包含：

- `turn_id`、`speaker_id`、`speaker_label`、`speaker_role`；
- `raw_text`、`normalized_text`、可选 `corrected_text`；
- 时间戳和源文件行号；
- 语言、质量标记和 ASR 修订候选；
- 原始文本 SHA-256、文档 id 和规范化版本。

ASR 修订候选只提供 `original`、`suggested`、置信度和理由，默认不自动应用。人工或受控模型审核后，才可以写入 `corrected_text`。

## 分析输出

`analysis.json` 包含四类结果：

1. **结构质量**：轮数、说话人数量、角色未解析数量、重复/填充候选和缺失标签。
2. **对话统计**：语言、提问数、支持性表达标记、患者/治疗师轮次平衡。
3. **风险筛查**：带 `turn_id` 和原文证据的词法信号，明确标记 `screening_only`。
4. **模型评测计划**：编排 `transcript-quality-reviewer`、`transcript-evaluator`、`therapy-response-reviewer`，有词法风险信号时加入 `risk-triage`，并要求所有判断引用具体轮次。

## Agent 与处理流水线的分工

- TheraSkillAgent 的 Agent runtime 管理会话、澄清问题、用户确认、工具调用和恢复交互。
- 对话处理工具读取、规范化、保存原文，并生成确定性分析和审核工作流状态。
- `risk-triage`、`therapy-response-reviewer`、`transcript-quality-reviewer` 和 `transcript-evaluator` 负责分工评测。
- 评测结果进入候选审核队列；模型输出不能直接变成正式知识 release。

建议的交互顺序是：

```text
inspect → 用户确认语言和说话人角色 → analyze
→ review(mode=plan) → 用户批准模型调用 → review(mode=model)
→ 运行质量/风险/治疗过程 skill → 人工审核
→ package → 可选生成知识候选
```

## 多 skill review 编排

`review` 是独立于确定性分析的编排操作。默认 `mode=plan`，只读取本地
skill 的 `SKILL.md`，生成每个 skill 的系统提示、输入轮次和 JSON 输出契约，
不会访问模型，也不会把文本发送到网络。Agent 可以展示这个计划、让用户
调整 skill 或轮次上限，然后以 `mode=model` 恢复；模型使用项目本地
`configs/theraskill/providers.local.yaml` 中的 `llm` 配置。

每个模型评测结果都要求 `findings[].turn_ids`，运行后会丢弃不存在的轮次引用，
并报告无证据的 finding。`observed`、`inference`、`recommendation` 和
`uncertainty` 分开保存；结果仍是候选审核材料，不会自动进入正式知识库。
单个 skill 的 provider 调用失败时，其余 skill 仍会继续，结果标记为
`partial` 并在 `errors` 中记录失败原因。
评测计划描述了待调用的 skill、文档和轮次范围，以及
`theraskill_context`/`theraskill_knowledge_search` 等工具依赖。Agent 可以
逐个调度这些 skill，再把结构化结果汇总回同一个 `workflow_id`。

CLI 示例：

```bash
theraskill-agent transcript review ./dialogue.txt --mode plan \
  --skill transcript-quality-reviewer --skill risk-triage

# 经过人工确认后，再调用项目中配置的 LLM
theraskill-agent transcript review ./dialogue.txt --mode model \
  --max-turns 200
```

Agent 工具 `theraskill_transcript` 也支持 `review`。`plan` 会把工作流置于
`needs_approval`，`model` 完成后保存 `reviews`；随后 `package` 会把它写入
`reviews.json` 并在 `manifest.json` 中登记。工作流 checkpoint 位于本地
`data/theraskill/workflows/`，服务重启后可通过 `workflow_id` 继续。

## CLI 示例

```bash
theraskill-agent transcript inspect ./dialogue.txt
theraskill-agent transcript analyze ./dialogue.txt --output ./work/analysis.json
theraskill-agent transcript package ./dialogue.txt --output-dir ./work/dialogue_bundle
```

## Agent 工具示例

工具名为 `theraskill_transcript`，当前操作包括：

- `inspect`：编码、语言、说话人标签和样例轮次；
- `analyze`：确定性结构分析、风险词法筛查和后续 skill 计划；
- `review`：生成本地 review plan，或在明确批准后执行多 skill 模型评测；
- `package`：生成可交付的 transcript bundle。

`inspect` 和 `analyze` 会返回 `interaction`，其中包括 `workflow_id`、表单结构、当前值和恢复操作。Agent 可以澄清简单选择，并把复杂角色映射和审核交给工作台。

## 后续扩展

- 增加 PDF、DOCX、Parquet 等 Source Adapter；
- 增加用户确认后的 `corrected_text` 应用和修订 diff；
- 增加说话人合并、角色映射和时间戳校正；
- 增加说话人置信度和人工修订 diff；
- 将模型评测结果自动转成回归案例；
- 只有通过重复度、质量、专家审核和 release 门禁的知识，才允许进入正式知识库。
