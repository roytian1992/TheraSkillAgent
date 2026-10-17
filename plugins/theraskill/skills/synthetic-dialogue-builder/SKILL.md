---
name: synthetic-dialogue-builder
description: "基于框架、角色、阶段和构建知识生成标注清晰的合成咨询对话。"
---

# Synthetic Dialogue Builder

用于合成咨询对话、角色扮演样例和训练数据草稿。调用
`theraskill_context`、`theraskill_knowledge_search`，将生成的 rationale 与 observed dialogue 分开，并明确标记 synthetic，不把合成内容当作真实临床证据。
