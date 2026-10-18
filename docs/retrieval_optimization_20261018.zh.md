# 检索优化验收记录

本轮在现有完整混合索引上优化查询链路，2026-10-18 开始测量，2026-10-19 整理验收。当前单进程应用已加载新代码，入口仍为 `http://127.0.0.1:5176/`。启动、模型配置和业务操作不增加步骤。

## 原因与改动

之前「一分钟以上」指查询已有索引，不是每次重新构建索引。独立进程的真实规划基线为 67.99 秒，其中规划器读取元数据枚举占 40.28 秒，模型请求占 10.86 秒。词法通道依次等待、重复计算候选 ID、SQLite 选择低选择性的 eligibility 索引，以及小范围查询预加载大 FAISS 分片，进一步放大耗时。

| 改动 | 行为与边界 |
| --- | --- |
| 元数据清单 | 新构建同步生成清单；历史索引一次扫描后缓存。绑定索引版本，可跨进程复用；真实 LLM 规划照常执行。 |
| 并行召回 | 查询理解的词法召回与规划重叠；BM25、标签、必要知识家族和向量使用最多四个工作线程并行执行。 |
| 范围计算 | 请求内共享范围 ID，小范围按索引和过滤条件缓存；去掉重复 eligibility 条件，避免 SQLite 优先扫描几乎整个发布语料。 |
| 向量读取 | Flat 索引只读映射，选择器直接访问范围内向量；ID 与行号采用紧凑结构，避免全分片复制。 |
| 功能匹配 | required-family 逐家族补足，修复问答候选挤掉方法量规；显式语言、来源等过滤及功能边界继续生效。 |
| 可诊断性 | 分阶段耗时、清单缓存来源、召回预算 warnings 进入 bundle、支持/专家 provenance 与知识工具 diagnostics。 |

没有切换活动索引、重算向量或改变模型选择。当前索引为 `data/theraskill/indexes/phase6-v2-20261018-hybrid`，使用配置的 `deepseek-v4-pro` 和 `text-embedding-3-large`。公开文档不包含本机密钥。

## 实际结果

浏览器重新提交自编中文专家审核，202 提交耗时 0.22 秒，成功生成非空审核建议并保存在任务历史。上次同类业务记录的检索为 84.81 秒、生成 18.61 秒；本次检索为 **14.77 秒**、生成为 **15.05 秒**，两阶段共约 **29.82 秒**。两个输入和模型计划并非严格相同，此项属于业务体验复测，不能当作受控性能倍数。

本次任务的 planner 元数据清单读取为 0.857 毫秒，模型规划约 12.08 秒。词法约 6 秒的预算等待与规划重叠，未再串行加到规划之后。并行阶段耗时不能求和作为总耗时。

下表六类任务均使用真实模型规划、真实 embedding 和完整活动索引；每类一个自编测试查询，明确指定语言，返回八条结果。时间包括检索规划，不含后续业务回复生成。

| 功能 | 语言 | 检索耗时 | 结果中的主要知识 |
| --- | --- | ---: | --- |
| 情绪支持 | 英文 | 14.21 秒 | 回应风格、咨询过程、心理教育 |
| 风险分流 | 中文 | 16.88 秒 | 临床风险评估 |
| 认知重构 | 中文 | 18.40 秒 | 前三条为方法量规，另有心理教育与回应风格 |
| REBT 信念工作 | 英文 | 9.53 秒 | 前四条为方法量规 |
| 情景构建 | 中文 | 16.46 秒 | 合成对话构建方法 |
| 回应审核 | 英文 | 11.52 秒 | 咨询过程、方法量规、回应风格与心理教育 |

六次结果均通过策略检查、对应的家族工程检查和硬过滤复核。进程连续执行六例时记录的累计峰值 RSS 最大为 709.7 MiB；这是该进程观测值，不是全应用内存上限，也不包括系统页缓存或所有查询情形。

另有固定同一已记录模型计划的性能诊断：基线两次为 20.10 / 13.79 秒，并行与范围复用的中间版本为 9.61 / 6.04 秒，八条结果 ID 和顺序相同。最终版本为 6.09 / 6.03 秒；增加方法量规覆盖后排序有意改变，因此不把最终结果表述为「排名完全不变」。这个诊断显式跳过规划，仅用于定位本地检索开销；生产和六类业务复测均继续真实调用模型。

## 验证与证据

- 专属回归：**228 passed**，4 条既有 Pydantic 警告，21.55 秒。覆盖缓存失效、过滤隔离、召回并行、同技能并发、FAISS 选择器结果一致性及方法家族补足。
- 浏览器：八个页面 HTTP 200，无页面异常或请求失败；全部请求使用 5176；390 px 首页无横向溢出。真实专家审核成功。
- 安装：新 wheel 在隔离虚拟环境以 `python -I` 通过 smoke，六模块健康、四个已构建界面、抽取/交接和策略词法检索可运行。该测试使用明确的本地 HTTP fixture，不代表真实模型评估。基础环境没有 NumPy、FAISS、PyTorch 或旧 agent 模块。
- wheel 含三个新增索引辅助模块，不含 `providers.local.yaml`；可选 `retrieval` extra 要求 `faiss-cpu>=1.13.2`，基础直接依赖数保持七个。

原始证据根目录：`artifacts/retrieval_optimization_20261018/`。其中 `index.html` 展示截图与结果，`manifest.json` 汇总来源及代码 hash；`baseline-live/`、`baseline-v1/`、`optimized-reference-v1/`、`final-review-reference/`、`final-six-skills-live/` 各自保留计划、结果与测量清单。界面原始输出在 `expert-result.json` 与 `browser-validation.json`，回归日志为 `regression.log`，隔离安装日志为 `lean-validation.log`。

## 复现

工作目录为项目根；本次使用 `/vepfs-mlp2/c20250513/241404044/users/roytian/anaconda3/bin/python`，Python 3.12.7、FAISS 1.13.2。模型配置由本地 provider 文件自动读取。复现需要同一知识与索引资源以及可用模型服务；公开源码和小示例不含完整语料。

```bash
# 回归测试
python -m pytest -q -o addopts='' \
  tests/test_standalone_kernel.py tests/test_theraskill*.py \
  tests/smoke_theraskill_install.py

# 从实际保存的 bundle 取得复现输入；输出目录必须尚不存在
python scripts/AdvancedRetrieval/profile_runtime_retrieval.py \
  --reference-bundle /path/to/saved.bundle.json \
  --plan-mode live --runs 2 \
  --output-dir artifacts/retrieval-new-live

# 仅用于固定规划后的检索性能比较，不用于业务效果验收
python scripts/AdvancedRetrieval/profile_runtime_retrieval.py \
  --reference-bundle /path/to/saved.bundle.json \
  --plan-mode reference --runs 2 \
  --output-dir artifacts/retrieval-new-reference
```

六类输入保存在 `artifacts/retrieval_optimization_20261018/cases.json`，可在 `live` 模式增加 `--cases` 指向该文件。运行清单记录实际解释器、命令、索引路径、参考输入 hash 与代码 hash；每次使用新目录，保留失败与中间结果。

## 限制

六例和一次专家工作流是有界工程验收，不能替代专家相关性评价、全功能临床评价或并发负载测试。此次没有测量全语料构建吞吐提升。宽泛中文查询仍可能耗尽词法预算，通道缺失会出现在 warnings；保留真实向量、标签和策略候选不等于完整词法召回。旧索引首次生成清单仍有扫描成本，未过滤的精确向量搜索仍可能大量读取文件。模型、网络盘和上游服务波动仍会影响响应，当前测量不构成固定时延承诺。
