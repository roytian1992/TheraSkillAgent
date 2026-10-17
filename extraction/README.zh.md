# TheraSkill 干净知识抽取项目

这是一个只包含“数据适配 → 结构化抽取/投影 → QA/去重 → release 构建”代码的独立项目。它不包含任何原始数据、现有知识库、模型权重、运行时会话或 Hermes 代码。

## 快速开始

```bash
cd TheraSkill_Knowledge_Extraction_20261013
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp configs/model_config.yaml configs/model_config.local.yaml
# 编辑 model_config.local.yaml，填写 OpenAI-compatible endpoint / model / token
python scripts/list_surfaces.py
```

## 动态抽取（无需数据集专用适配器）

动态入口由自然语言目标和 JSON Schema 驱动。数据源只需要是 JSON 或
JSONL；抽取出的知识类型由 `ExtractionPlan` 定义，可以在小样本预览后再跑
全量。示例：

```bash
python scripts/run_dynamic_extraction.py \
  --source examples/input/cpsycoune_demo.json \
  --goal "抽取适合情景模拟的来访者状态变化" \
  --plan-output work/state_transition.plan.json \
  --dry-run
```

需要让配置的模型根据目标和样例自行提出字段时，使用
`--planner model --model-config ...`。模型只负责提出可审核的计划，计划仍
会经过本地 schema 校验，且不会自动发布知识。

检查生成的 plan 后，使用模型配置执行：

```bash
python scripts/run_dynamic_extraction.py \
  --source examples/input/cpsycoune_demo.json \
  --plan work/state_transition.plan.json \
  --model-config configs/model_config.local.yaml \
  --output work/state_transition.units.jsonl \
  --manifest work/state_transition.manifest.json
```

动态结果只具备 `candidate_only` 资格。它们保留来源、证据、计划、提示词和
模型信息，必须进入 Hermes 的候选知识、去重、专家审核、回归测试和 release
流程，不能直接覆盖生产知识。

运行一个小规模抽取时，从项目根目录执行，例如：

```bash
python scripts/CPsyCounE/run_cpsycoune_knowledge_extraction.py \
  --stage evaluation_dialogue_case \
  --source-root datasets/CPsyCounE \
  --model-config configs/model_config.local.yaml \
  --limit 2 \
  --output outputs/knowledge/units/CPsyCounE.evaluation_dialogue_case.demo.jsonl \
  --manifest outputs/knowledge/manifests/CPsyCounE.evaluation_dialogue_case.demo.manifest.json
```

CPsyCounD、ESConv、Simulation 各 runner 都支持 `--limit`、`--resume`、`--overwrite`、模型配置和输出路径。正式全量运行前先用 `--dry-run` 或少量样本检查 prompt token 预算。

## 目录

- `scripts/`：可执行抽取器、确定性数据投影器、候选构建器和 Phase 6 去重/发布器。
- `scripts/dynamic_extraction/`：由目标和 schema 驱动的通用抽取计划、校验器和执行器。
- `prompts/`：按数据集和知识类型组织的提示词。
- `configs/`：抽取策略、规范化注册表和可替换模型配置。
- `docs/datasets_and_outputs.zh.md`：数据集、抽取方式、产出知识类型和来源边界。
- `examples/`：最小输入样例，不代表正式数据集。
- `outputs/`：运行时生成目录，默认为空，不纳入交付包。

## 重要边界

抽取出的内容先写为 `knowledge unit`，保留 `dataset`、`source_id`、证据层级、prompt/model 配置和质量状态；之后才进入去重、专家审核、release 和检索索引。抽取结果不会自动进入 Hermes 生产知识库。
