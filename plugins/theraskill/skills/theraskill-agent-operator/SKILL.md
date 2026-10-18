---
name: theraskill-agent-operator
description: 管理 TheraSkillAgent 技能并完成安全的文本预处理任务。
runtime:
  required_tools:
  - text.normalize
  - text.chunk
  - source.inspect
  - skill.manage
---

# TheraSkillAgent Operator

## 目标

帮助用户发现、检查、安装和优化本地技能，并在不调用模型的情况下完成常用文本预处理。

## 输入

- 技能名称、`SKILL.md` 路径或本地技能目录。
- 文本文件路径，以及规范化、分块或章节提取需求。

## 输出

- 技能元数据、质量分数和可执行改进建议。
- 规范化文本、带 offset 的文本块或 Markdown 章节 JSON。

## 步骤

1. 先运行 `theraskill-agent skill list` 或 `skill inspect` 确认目标。
2. 安装前使用 `theraskill-agent skill optimize` 检查元数据、流程、安全边界和验证章节。
3. 使用 `theraskill-agent skill install` 安装本地技能；只在用户明确要求覆盖时传入 `--overwrite`。
4. 对知识抽取和报告文本先运行 `text normalize`，再按任务需要运行 `text chunk` 或 `text sections`。

## 安全边界

- 只处理用户明确给出的本地路径，不自行联网下载或执行技能脚本。
- 安装结果必须通过静态安全扫描；扫描拒绝时解释原因并停止。
- 日志和回复中不得输出 API key、Bearer token、会话凭据或完整私密文档。
- 技能优化默认只报告问题；只有用户明确要求 `--write` 时才补充缺失的元数据。

## 验证

- 安装后用 `skill inspect` 确认来源为 user 目录。
- 文本分块检查每块不超过 `max_chars`，并保留正确的 `start/end` offset。
- 需要交付时运行项目测试和领域治理检查，不能把候选知识直接写入生产 release。
