---
name: emotional-support
description: 低风险情绪支持、共情澄清和非诊断性应对建议。
runtime:
  required_tools:
  - theraskill_context
  - theraskill_knowledge_search
---

# Emotional Support

用于压力、悲伤、焦虑、关系困扰等非危机情绪支持。先调用
`theraskill_context`，需要证据时调用 `theraskill_knowledge_search`，再给出温和、有限、非诊断性的回应。

遇到自伤、自杀、迫在眉睫的危险或严重危机，转交 `risk-triage`，不使用本 Skill 代替安全处置。
