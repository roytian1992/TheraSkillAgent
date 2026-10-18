---
name: therapy-response-reviewer
description: 审查咨询式回应的阶段适配、策略质量、边界和证据一致性。
runtime:
  required_tools:
  - theraskill_context
  - theraskill_knowledge_search
---

# Therapy Response Reviewer

当用户要求检查咨询回应是否安全、合适、符合阶段或策略时使用。调用
`theraskill_context` 和 `theraskill_knowledge_search`，分别报告可观察文本、推断 rationale、风险边界和证据来源，不提供正式临床督导或认证结论。
