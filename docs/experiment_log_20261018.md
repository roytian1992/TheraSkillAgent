# 2026-10-18 检索全量迁移与验证

## 授权与范围

继续此前查询理解和按功能检索的优化，完成本机全量索引构建、真实检索检查和服务切换。用户已授权直接推送 `main`；完整知识、索引、凭据和运行数据继续排除在 Git 外。

不更换当前配置的模型，不混用 BGE 与其他 embedding 向量，不用模拟响应代替真实模型测试。并发和批量调整只影响构建吞吐，所有符合原始策略的知识均保留。

## 输入与环境

- 项目：`TheraSkillAgent`。
- 起始代码：`e6877481173f50ff81f23eb4000b525b619d5e13`。
- Python：本机 `anaconda3/bin/python`，具体路径和版本见审计文件。
- 输入：`data/theraskill/releases/phase6_v1/knowledge/phase6_reviewed_knowledge_units.jsonl`。
- 输入 SHA-256：`e05200742c20de8e5536d9aa7987ce94549ad3db82f9e7335963ec332c70b058`。
- 来源记录 2,196,286；原策略允许检索和 embedding 各 1,808,415。以上是输入统计，完成数量必须以最终索引清单为准。
- 当前直接配置：LLM `deepseek-v4-pro`、embedding `text-embedding-3-large`。二者真实连通检查通过，embedding 返回 3072 维。凭据仅保存在忽略的本地配置。

审计文件：`artifacts/retrieval_rollout_20261018/audit.json`。包括版本、脱敏配置、模型指纹、历史向量分片实际计数；不包含 API 密钥。

## 历史覆盖纠正

历史 `faiss_bge_m3_shards_v1/manifest.json` 的 dense 总量描述为 1,808,415，但逐个读取八个 FAISS 文件得到的实际向量总数是 **112,620**。缺失的主要是原始回应风格记录。不能把检索资格总数或上游构建计数当作已发布分片的真实覆盖量。

本次索引验证增加 FAISS 实际数量、ID 行数和维度检查，防止仅核对清单中的自报数字。

## 构建与恢复

首轮在网络盘直接构建词法 SQLite，发现随机写和同步写入明显限制吞吐。保留该轮部分产物与 `run.json`，状态标记为已被替代。用 10,000 条真实回应风格记录测得 tmpfs 构建 3.486 秒，详见 `storage-speed-check.json`。

后续改为内存盘构建，完成后复制到持久化目录，再做完整校验才允许启用。临时构建位置不作为服务运行依赖。

```bash
python artifacts/retrieval_rollout_20261018/build_lexical_fast.py
python artifacts/retrieval_rollout_20261018/build_hybrid.py
```

这些本机协调脚本、PID、完整命令、阶段和恢复状态分别记录在 `lexical-fast-run.json`、`hybrid-run.json` 及相应日志。正式可移植构建入口仍为：

```bash
theraskill-agent index build --knowledge /path/to/knowledge.jsonl \
  --id knowledge-lexical-v2
theraskill-agent index build --knowledge /path/to/knowledge.jsonl \
  --id knowledge-hybrid-v2 --mode hybrid --reuse-lexical knowledge-lexical-v2
```

embedding 请求最多 16 个并发、每批 128 条；向量与恢复游标按来源顺序提交。未完成索引不可启用。词法复用要求输入 hash、投影版本和映射一致，并复制经校验的文件，不复用旧模型向量。

## 检索检查

固定问题集：`examples/retrieval/rollout_cases.jsonl`，30 个工程检查问题，覆盖六个功能、中英文、混合字符、风险、语言/家族硬过滤和空问题。

```bash
python scripts/AdvancedRetrieval/evaluate_managed_retrieval.py \
  --baseline-only \
  --baseline-db data/theraskill/current/retrieval/phase6_retrieval_fts.sqlite \
  --baseline-metadata data/theraskill/current/retrieval/metadata_v2.sqlite \
  --output artifacts/retrieval_rollout_20261018/baseline-evaluation
python scripts/AdvancedRetrieval/evaluate_managed_retrieval.py \
  --index data/theraskill/indexes/phase6-v2-20261018-lexical-fast \
  --output artifacts/retrieval_rollout_20261018/lexical-evaluation
```

历史基线取自 `e687748^` 的本地 `_query_fts` 原函数，保留其原有行为：FTS 别名错误会进入整句 LIKE 回退。这是历史**本地词法回退**基线，不代表历史高级混合检索。30 题完成，28 个有预期知识家族的题目中，前五条家族命中 0/28；两个空结果检查通过；中位耗时 4.1523 秒，最大 12.0765 秒。来源为 `baseline-evaluation/summary.json`。

家族命中和硬过滤是工程检查，不是专家相关性标注，也不是临床效果评估。高级查询规划和业务模型调用需另行使用真实配置的 LLM / embedding 检查，不以本地词法结果冒充。

## 服务与界面

支持、专家、贡献三个旧目录进程已停止，六个领域服务统一从当前项目启动。切换前对旧/新三个业务数据库各做 SQLite 备份，并确认逻辑内容一致。备份目录：`artifacts/retrieval_rollout_20261018/service-backup/`。

工作台地址：`http://127.0.0.1:5176/`；健康结果记录于 `service-health.json`。UI 构建通过；浏览器截图和检查结果在同一运行目录。仅 HTTP 健康检查不作为模型功能通过的证据。

## 轨迹

1. 核对全量知识与原策略资格，发现历史分片覆盖与清单总量不符。
2. 验证当前远程 LLM / embedding，启动全量构建与独立历史基线测试。
3. 增加并发 embedding、按序事务恢复、已完成词法文件复用和真实向量计数验证。
4. 修正中文 FTS 按未索引 ID 连接造成的全表扫描，改用构建时对齐的行号。
5. 备份业务数据库，统一服务进程，启动工作台；修正测试对本机凭据和运行中服务的意外依赖。
6. 根据真实存储测量把构建移到 tmpfs，持久化校验后再切换。全量结果与最终切换状态将在完成后追加。
7. 真实 API 小规模预检：每类按来源顺序选前 128 条 eligible 知识，共 768 条，全部完成 3072 维 embedding。30 题无错误、无范围越界，预期家族前五命中 22/28，两个空结果检查通过；这是有意限定的 smoke，不能替代全量评估。见 `balanced-smoke-input.json` 与 `balanced-smoke-evaluation/summary.json`。
8. 实际 LLM planner 预检发现未知枚举和一次不完整 JSON。补充真实索引枚举提示、紧凑 JSON 指令和最多两次模型校验尝试，保留初始失败结果，单独输出修正后的 `advanced-smoke-v2/`。另补充高级向量检索的提前元数据选择器，防止过滤后候选不足。
9. 修正后的六个中文高级检索 smoke 全部生成有效模型计划，均只需一次规划请求、未降级，所有策略和质量检查通过；这是 768 条真实知识上的有限预检。见 `advanced-smoke-v2/advanced-smoke.json`。最新自动化检查为 143 项通过，4 条既有依赖警告，见 `tests-final.log`。
10. 六个英文高级检索 smoke 同样全部生成有效计划且无降级、无策略/质量检查失败；结果在 `advanced-smoke-en/advanced-smoke.json`。当前服务实测支持 base64 embedding，两个中英文输入与浮点响应的 float32 值最大绝对差为 0；见 `embedding-encoding-check.json`。全量向量构建使用此传输格式，模型与维度保持不变。
11. 通过 SQLite 原生 `rank` 排序减少候选排序开销。中文行号连接与提前元数据过滤保持有效；构建期间的诊断对比记录在 `fts-rank-speed-check.json`，不把这一动态语料测量当作最终全量延迟结果。
