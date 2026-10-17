# 抽取项目运行说明

## 1. 准备输入

不要把原始数据复制进交付压缩包。将本地数据集放在项目根目录的 `datasets/` 下，或通过各 runner 的 `--source-root` 指向受控路径。

## 2. 配置模型

编辑 `configs/model_config.yaml`：

- `model.name`：请求中使用的 served model name；
- `model.base_url`：OpenAI-compatible `/v1` 地址；
- `model.api_key`：本地 token；
- `embedding`：如果要构建 dense index，配置 BGE-M3 endpoint。

模型名称不写死在抽取逻辑里；更换模型只需换配置文件或通过 runner 的 `--model-config` 指向另一份配置。

如果任务没有现成数据集模板，使用动态入口：

```bash
python scripts/run_dynamic_extraction.py \
  --source examples/input/cpsycoune_demo.json \
  --goal "抽取支持者回应策略" \
  --plan-output work/response_strategy.plan.json \
  --dry-run
```

检查计划中的目标、字段和证据规则后，再提供 `--model-config` 去掉
`--dry-run` 执行。`--planner model` 可以让配置模型根据目标和样例提出
schema，但仍必须人工检查计划。

## 3. 运行顺序

1. 数据集预处理或确定性投影；
2. CPsyCounD/CPsyCounE/ESConv 等 LLM 抽取；
3. Simulation 候选构建和三类 simulation pattern 抽取；
4. JSONL schema/manifest/QA 检查；
5. DedupMerge 去重、冲突审阅和 Phase 6 release；
6. 由单独的 Hermes 安装流程挂载 release。

## 4. 最小验收

```bash
python scripts/list_surfaces.py
python -m compileall -q scripts
pytest -q
```
