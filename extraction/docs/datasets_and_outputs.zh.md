# 数据集、抽取方式与知识产出

本项目把“LLM 抽取”和“确定性投影”分开。所有知识单元都使用 JSONL，每行是一条带来源和质量信息的 `theraskill_knowledge_unit_v1`。

| 数据集 | 代码入口 | 处理方式 | 主要产出 |
|---|---|---|---|
| CPsyCounD | `scripts/CPsyCounD/` | 对咨询对话做窗口/案例级 LLM 抽取，先构造时间安全的状态和行动证据，再抽 trajectory、formulation、action sequence | `interaction_pattern`、`case_trajectory`、`case_formulation_pattern`、`action_sequence_pattern`、状态/风险时间线 |
| CPsyCounE | `scripts/CPsyCounE/run_cpsycoune_knowledge_extraction.py` | 先按 evaluation dialogue case 抽取，再按 topic 聚合二次抽取 | `evaluation_dialogue_case`、`topic_support_dialogue_pattern` |
| ESConv | `scripts/ESConv/run_esconv_knowledge_extraction.py` | 从支持对话中识别策略标签和多步策略序列，保留对话证据 | `support_strategy`、`support_strategy_sequence` |
| AnnoMI | `scripts/AnnoMI/project_annomi_knowledge.py` | 基于公开标注做确定性投影和规范化 | `mi_method_step`、`mi_quality_rubric`、`change_talk_pattern` |
| PsyQA | `scripts/PsyQA/project_psyqa_knowledge.py` | 解析问答、答案结构和策略标注；不凭空补写临床结论 | `support_qa_unit`、`answer_strategy_sequence`、`answer_structure_pattern` |
| C-IMHI | `scripts/CIMHI/project_cimhi_knowledge.py` | 解析 assessment/cognitive distortion/risk 标签和认知路径 | `assessment_task`、`cognitive_distortion_signal`、`risk_or_symptom_signal`、`cognitive_path_example`、质量 rubric |
| Cognitive-Reframing | `scripts/CognitiveReframing/project_cognitive_reframing_knowledge.py` | 将认知陷阱与重构步骤规范化 | `thinking_trap_unit`、`reframing_step` |
| Empathy-Mental-Health | `scripts/EmpathyMentalHealth/project_empathy_mental_health_knowledge.py` | 按 empathy mechanism 和 level 投影回应样例 | `empathy_response_unit`、`empathy_quality_rubric` |
| DepSeverity | `scripts/DepSeverity/project_depseverity_knowledge.py` | 保留来源严重度标签，提取症状信号和 triage boundary example | `depression_severity_signal`、`triage_boundary_example` |
| Amod Mental Health | `scripts/AmodMentalHealth/project_amod_knowledge.py` | 对英文支持对话做主题/风险/边界筛选，形成候选，不把筛选当诊断 | `psychoeducation_candidate` |
| Anna-CPsyCounD | `scripts/AnnaCPsyCounD/run_anna_cpsycound_patient_profile_extraction.py` | 对病例报告做结构化 persona/profile 抽取，保留隐藏状态与来源桥接 | `patient_persona_profile`、`hidden_patient_profile_pattern`、memory seed |
| Simulation multisource | `scripts/Simulation/` | 先从多源 raw units 构造 candidates，再用 LLM 抽取状态转移、回应政策、案例记忆和 protocol | `patient_state_transition_pattern`、`therapist_response_policy_pattern`、`therapist_case_memory_pattern`、`simulation_protocol_pattern` |

## 知识单元长什么样

```json
{
  "schema_version": "theraskill_knowledge_unit_v1",
  "unit_id": "ku_...",
  "knowledge_type": "therapist_response_policy_pattern",
  "source": {"dataset": "Simulation.multisource", "source_unit_ids": ["..."]},
  "content": {"response_policy_pattern": {"triggering_state": [], "preferred_moves": []}},
  "quality": {"quality_status": "pass", "review_status": "llm_extracted_unreviewed"},
  "retrieval": {"embedding_status": "not_embedded", "embedding_text": "..."},
  "provenance": {"prompt_sha256": "...", "model": "..."}
}
```

## 证据与安全边界

- 数据集标签是来源证据，不等于临床诊断。
- 合成/聚合 pattern 必须标记 `source_construction_type` 和 `evidence_tier`。
- risk 相关字段只表达识别和升级边界，不生成自治诊疗结论。
- 未经 QA、去重和专家治理的 JSONL 不能直接安装为生产 release。
