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
12. 全量写入完成：总数 2,196,286、可检索/可嵌入 1,808,415、排除 387,871。正在完成持久化副本校验；此阶段尚未启用新索引。向量构建改为按对齐行号顺序读取元数据，使用同事务保存的恢复游标避免对每条待构建知识再做一次随机缓存查询。对应构建/恢复检查 15 项通过；完整回归在此前传输与排序改动后为 144 项通过。
13. 在已完成、已校验的 tmpfs 全量副本先做词法测试，30 题无错误、无范围越界，家族前五命中 19/28，空检查 2/2，中位耗时 1.0017 秒；这是内存盘延迟，不能代表持久化服务性能。见 `lexical-tmpfs-evaluation/summary.json`。检查失败案例发现 170 万条回应风格样例挤占方法量规候选，补充主知识家族定向文本召回和一条匹配证据保留，维持硬过滤且不任意回填。相应检查 39 项通过；后续结果另存新目录。
14. 持久化校验的 SQLite 默认 2 MiB 缓存造成大量随机页读取。改为每次校验连接最多使用 256 MiB，保留 SHA-256、SQLite quick_check 和计数检查；安全停止只读校验后，由 `publish_lexical.py` 对原持久化副本重新完整校验再发布。保留重启前状态 `lexical-before-cache-restart.json`，未重建或丢弃已写入的数据。
15. 主知识家族召回后的 tmpfs 全量词法检查为 26/28，空检查 2/2，无错误、无范围越界；中位耗时 1.0926 秒、最大 8.1694 秒，见 `lexical-tmpfs-evaluation-v2/summary.json`。两条未命中保留为失败案例。继续修正跨语言概念扩展：英文概念短语同时分解为词元，避免要求方法记录必须逐字包含翻译短语。相应检查仍为 39 项通过。
16. 为减少网络盘的重复等待，混合构建从已经完整验证的不可变 tmpfs 词法副本复用文件；构建器再次验证后复制至独立持久化输出，再生成真实向量。最终运行不依赖 tmpfs，向量模型与知识内容不变。
17. 持久化词法副本完整校验通过并发布，计数与输入资格一致，见 `lexical-fast-run.json`。持久化初轮评估在稀少方法家族召回时过慢，保留 8 条已完成结果并标记中断。直接按行号逐条执行 FTS/BM25 仍可能反复计算全局词频，改为在相同硬过滤下读出小家族，在临时内存 FTS 中计算该家族的词频和排序；不截断记录，不任意填充不匹配知识。新版本另存评估目录。
18. 实际支持 API 初测失败，后台记录为 `support response model returned empty response_text`，见 `support-before-activation/job-error.json`。同一输入的完整引擎复查成功，真实路由、检索、回复耗时分别 24.707、153.490、10.894 秒；见 `support-response-diagnostic/result.json`。原失败未在复查中复现，不能推断一定由输出预算导致。增加非空回复 schema 校验与一次有限重生成，并明确拒绝截断/空内容响应；不使用模板或规则代替模型回复。
19. 当前服务确实接受 `thinking: {type: disabled}`，有限兼容探针耗时 2.362 秒、reasoning_tokens 为 0，见 `thinking-option-check/result.json`；这不是业务端到端评估，也没有据此更改本地 LLM 默认推理设置。
20. 真实 embedding 批量探针（与 16 并发全量构建同时运行）：32/128/512 条各完成一次，耗时 2.986/4.361/7.241 秒，均返回 base64、3072 维，见 `embedding-batch-throughput/`。仅为诊断样本。全量构建调整为最多 512 条、最多 200,000 字符组合、16 并发，完整保留每条文本和已提交检查点；吞吐参数不改变模型指纹。发布 FAISS 时按来源顺序读向量，并为附加 checkpoint 数据库设置独立缓存，减少随机读取。
21. 新增可选快速 scratch 校验，持久化文件和临时 SQLite 副本均核对 SHA-256，再执行完整性与计数检查，未省略检查。新增副本损坏、临时清理、可变批量恢复、空/截断模型输出和 schema 重生成的检查。当前聚焦回归 58 项通过、3 条既有依赖警告，见 `rollout-hardening-tests-v2.log`。
22. 网络盘持久化评估进一步暴露小家族主证据召回的随机读取长尾。改为在全局 FTS 倒排结果上施加有限家族 ID 集合；英文语言过滤新增独立的小型 FTS/metadata 分区，并在词法索引路径提前返回，避免再次打开多 GB 混合库。当前英文过滤实测从此前约 710 秒降至约 0.12 秒；相关改动已提交为 `e99c433` 并推送 `main`。
23. 真实服务验证：词法索引启用后，支持助手通过工作台代理完成一次完整中文支持对话，生成状态回到 `idle`，可见 assistant 回复非空；记录在 `support-after-lexical/summary.json`。浏览器实测工作台中英文切换、知识与索引页面、中文检索提交、异步结果展示和截图均通过，截图在 `ui-after-lexical-*.png` 与 `ui-knowledge-query-after-lexical.png`。
24. 回归结果：TheraSkillAgent 专属测试 147 项通过、4 条既有依赖警告；检索/索引/模型输出聚焦检查 41 项通过、2 条既有依赖警告。仓库完整 pytest 仍包含未安装的上游 ACP 可选依赖，不能将该集合报告为通过。
