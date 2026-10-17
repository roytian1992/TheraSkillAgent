# 动态知识抽取

## 设计目的

固定数据集脚本适合稳定的生产管线，但新资料、新知识类型和跨数据集试验会
不断出现。动态抽取把数据集降级为“输入源”，把抽取目标、知识单元定义、
产出 schema、证据规则和质量规则放入一个可审核的 `ExtractionPlan`。

## 计划的最小结构

```json
{
  "goal": "抽取适合情景模拟的来访者状态变化",
  "source": {"path": "data/dialogues.jsonl"},
  "unit_definition": "以一次完整咨询片段为单位",
  "language": "zh",
  "target_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "trigger": {"type": "string"},
      "state_after": {"type": "string"},
      "evidence": {"type": "string"},
      "confidence": {"type": "number"}
    },
    "required": ["trigger", "evidence"]
  },
  "evidence_required": true,
  "quality_rules": [
    "Only use information supported by the source record.",
    "Use unknown when the source does not support a field."
  ]
}
```

## 运行流程

1. `run_dynamic_extraction.py --dry-run` 读取源文件并生成计划草稿。
2. 计划可以由启发式模板生成，也可以使用 `--planner model` 让配置模型
   根据目标和样例提出字段；两种方式都要由研究人员或知识管理员检查。
3. 计划通过校验后，执行器对每条记录调用配置的 OpenAI-compatible 模型。
4. 每条结果都生成 schema QA 和 evidence QA；失败记录单独保存。
5. `manifest.json` 记录来源 SHA256、计划、模型和候选资格。
6. 结果进入 Hermes 候选治理流程，经过去重、审核、回归验证后才可发布。

## 为什么保留可选模板

动态方案是默认入口；CPsyCounD、ESConv 和 Simulation 的既有脚本仍保留，
用于稳定的高质量生产抽取、特殊输入预处理和回归基准。它们可以作为动态
计划的模板，而不是系统必须依赖的数据集适配器。
