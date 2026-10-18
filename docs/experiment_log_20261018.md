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
20. 真实 embedding 批量探针（与 16 并发全量构建同时运行）：32/128/512 条各完成一次，耗时 2.986/4.361/7.241 秒，均返回 base64、3072 维，见 `embedding-batch-throughput/`。仅为诊断样本。全量构建最终使用最多 512 条、最多 1,000,000 字符组合、24 并发，完整保留每条文本和已提交检查点；吞吐参数不改变模型指纹。发布 FAISS 时按来源顺序读向量，并把 checkpoint 放到 tmpfs 后恢复，减少网络盘随机写入。
21. 新增可选快速 scratch 校验，持久化文件和临时 SQLite 副本均核对 SHA-256，再执行完整性与计数检查，未省略检查。新增副本损坏、临时清理、可变批量恢复、空/截断模型输出和 schema 重生成的检查。当前聚焦回归 58 项通过、3 条既有依赖警告，见 `rollout-hardening-tests-v2.log`。
22. 网络盘持久化评估进一步暴露小家族主证据召回的随机读取长尾。改为在全局 FTS 倒排结果上施加有限家族 ID 集合；英文语言过滤新增独立的小型 FTS/metadata 分区，并在词法索引路径提前返回，避免再次打开多 GB 混合库。当前英文过滤实测从此前约 710 秒降至约 0.12 秒；相关改动已提交为 `e99c433` 并推送 `main`。
23. 真实服务验证：词法索引启用后，支持助手通过工作台代理完成一次完整中文支持对话，生成状态回到 `idle`，可见 assistant 回复非空；记录在 `support-after-lexical/summary.json`。浏览器实测工作台中英文切换、知识与索引页面、中文检索提交、异步结果展示和截图均通过，截图在 `ui-after-lexical-*.png` 与 `ui-knowledge-query-after-lexical.png`。
24. 回归结果：TheraSkillAgent 专属测试 147 项通过、4 条既有依赖警告；检索/索引/模型输出聚焦检查 41 项通过、2 条既有依赖警告。仓库完整 pytest 仍包含未安装的上游 ACP 可选依赖，不能将该集合报告为通过。
25. 混合索引全量构建完成并通过独立 scratch 校验：总数 2,196,286、可检索/可嵌入 1,808,415、排除 387,871、实际向量 1,808,415、3072 维、9 个 FAISS 分片、失败 0。真实 embedding 使用 `text-embedding-3-large`，指纹为 `813fba430908c8991f809e467fc40aeed636d732054c87b4f22ec0db12fffee5`；校验同时核对文件 SHA、SQLite quick_check、行数、FAISS 向量和 ID 数量。
26. 混合索引 30 条完整工程评估无错误、无范围越界，28 个有预期知识家族的用例中前五命中 27 条，空结果 2/2，中位 16.6217 秒、最大 42.2656 秒。唯一未命中为 `rebt_zh_demand`；该家族命中指标仍是工程覆盖检查，不是专家相关性或临床效果结论。结果见 `hybrid-evaluation-v2/summary.json`。
27. 为 CJK FTS 长尾增加两阶段召回：先在中文倒排表取有界 rowid，再按有限 ID 回读正文和元数据；小家族主证据也使用有界 ranked page 后在 Python 侧筛选，避免大量字符串 ID 过滤连接。相关提交为 `92f30d5`，147 项 TheraSkillAgent 专属测试仍全部通过。
28. 混合索引已原子启用：`idx_d5b9f0077e1ca31b990080de`，当前模式 `hybrid`，active selection 写入 `data/theraskill/active-index.json`。六个领域服务和工作台全部重启并健康返回 200；健康记录在 `service-health-after-hybrid.json`。
29. 启用后的真实支持 E2E 成功，响应状态回到 `idle`，中文 assistant 回复非空，证据中的检索模式为 `advanced_retrieval_v3_llm_structured_hybrid`，总耗时 150.971 秒，见 `support-after-hybrid/summary.json`。浏览器实测工作台中英文切换和知识页，页面明确显示 active hybrid、1,808,415 条可检索记录及上一词法索引；截图为 `ui-after-hybrid-*.png`、`ui-knowledge-after-hybrid.png`。
30. 用户要求完整、深入地从用户角度体验。对 `0e0fe53f57bccb859b52b48f90edeb4909555277` 进行了真实 Chromium 操作与当前真实 `deepseek-v4-pro` / `text-embedding-3-large` 业务调用，另以有界 API、独立根目录 Python 管理器及代码检查补充定位。结果保存在 `artifacts/user_experience_audit_20261018/`，详细报告为 `docs/user_experience_audit_20261018.zh.md`。覆盖清单 48 项：15 项限定操作成功、14 项部分完成、13 项阻塞、6 项未完成验收；不是产品整体通过率。15 张截图组成 `index.html`，`manifest.json` 保存版本、输入 hash、证据、命令和限制。明确发现对话工作台四操作全部 422（旧 provider_profile 字段）、危险输入安全卡约 461.9 秒后才出现、空训练 gate=pass、训练记忆失败导致结束 409；专家审核被路由为 emotional-support，检索约 471.8 秒；批次 UI 仍用同步端点并实测 502。抽取单样本、支持记忆/反馈/导出/结束、单场景生成与编辑批准退回、隔离索引安装构建启用回滚实际成功。候选去重客户端 150 秒超时后后台完成，后续验证 pass 只代表局部不变量，不代表真实检索或行为回归通过。已有四测试文件 14 passed / 3 warnings / 2.35 秒，日志 `existing-tests.log`；它们不能替代浏览器端到端验收。全部输入为 QA 合成内容，原用户记录只读；只保留 QA 新记录，未发布知识，QA 场景最终退回。临时 5186 工作台已停止，5176 和已有领域服务保留，主活动混合索引未改变。本轮未改业务代码/模型配置、未打包、未提交或推送；既有未跟踪文件未清理。未经完整验收的多轮支持、有效训练后的回应比较/标注、正式知识发布回滚和课程学习结果在报告单列，未冒充通过。
31. 根据上述审计完成本地体验修复与真实业务复测：统一对话文件适配器、修复旧字段 422、提前展示即时安全状态、拒绝无效训练评分、补齐训练记忆/复盘恢复和独立复盘源上下文、专家工作流显式路由、批量场景持久任务逐项恢复、抽取模型规划与失败项重试/源指纹保护、任务中心与草稿恢复、治理定位和窄屏布局。当前直接配置的 LLM 输出预算由 4096 调到 8192，并显式设置 thinking disabled；早期预算试验仍被公共抽取模板覆盖，随后去掉覆盖并确认有效设置为 8192/disabled/retry_attempts=2。仍用原有真实模型和 embedding，主混合索引未切换。限定浏览器/API 复测清单 27 项通过；三种训练模式和独立复盘均完成真实模型调用，但上游断线、截断的初次失败另存保留，不据不同输入作受控延迟结论。最终专属回归 168 passed / 4 warnings / 20.34 秒，四个前端生产构建通过，没有新增运行依赖；没有宣称整个仓库所有可选测试通过。详细报告 `docs/ux_improvements_20261018.zh.md`，证据、输入/代码/提示词哈希及日志在 `artifacts/ux_improvements_20261018/`，最终回归日志为 `regression-release.log`。全部为合成 QA 输入，两条批次场景已退回，试抽候选待治理，未发布知识。正式行为回归、任意 skill 通用执行、完整课程学习结果和全面双语仍为后续工作。本轮未打包、提交或推送，既有未跟踪文件保留。

32. 完成四项平台升级：统一持久技能任务与完整抽取生命周期、真实效果评测/人工复核门禁、课程与练习/教师评价闭环、界面与输出语言兼容。执行器调用注册工具并支持交互暂停、写操作确认和检查点恢复；模型生成技能优化草稿，替换技能、知识发布及活动索引变更均校验效果报告，报告绑定 LLM/embedding、索引、技能及引用。真实合成抽取首次因模型 operations 类型与执行契约不一致而失败，保留失败，补齐共同校验和最多两次有错误反馈的模型重规划，随后试抽 1 条/全量 2 条均通过并交接 2 个候选。真实评测保留 7 份报告共 16 个案例；最终四例套件和原版/优化草稿专项比较均无新增工程失败、模型平均变化 0，未据此声称质量提升或临床效果，所有真实报告仍待人工复核。浏览器验证课程创建、分配、进入真实练习、完成复盘并记录“继续练习”的自动化 QA 反馈，没有伪造教师批准。中文对话→英文评估保留中文引文，中文复盘的混杂语言提示词已修正并真实复测。双向语言切换保留原文，7 个界面在 390 px 下无整页横向溢出，最终无浏览器异常。最终回归 202 passed / 4 warnings / 19.74 秒（含独立内核和安装相关测试），四界面构建通过，wheel 隔离资源导入检查通过（4 案例、12 内置技能），没有新增运行依赖。原始输入、失败、代码/模型/提示指纹和报告在 `artifacts/platform_upgrade_20261018/`，交付说明见 `docs/platform_upgrade_20261018.zh.md`，中英文工作流与部署文档及 README 已同步。主索引、模型选择和生产知识未改变；全部 QA 候选/技能草稿保留审核状态，本轮未提交或推送。

## 33. 单进程部署与公共模型恢复

默认 UI 改为在进程内托管六个业务模块及其生命周期；工具、专家调用、贡献/记忆客户端和首页探测同步切换，不再要求六个独立服务端口。保留旧独立服务命令用于迁移和显式调试。增加 `--project` 和初始化时的最简本地 provider 示例。

公共模型客户端对截断/空回复做有上限的恢复，推理设置保持原配置。真实 32-token 截断请求以 8192 tokens 恢复成功。实际专家审核暴露大语料 FTS 等待，增加通道执行预算和诊断；用户明确的知识过滤条件继续执行。最终真实审核成功，提交约 0.14 秒，检索约 84.8 秒，生成约 18.6 秒；不能据此宣称普遍的质量或时延提升。

验证：项目回归 214 passed；禁用业务网络连接的跨模块集成；生命周期和 202 后台语义；八页浏览器检查与移动端宽度；最终 wheel 在精简环境通过 python -I 独立安装 smoke。当前应用端口 5176，旧六端口关闭。具体限制见 `docs/deployment_simplification_20261018.zh.md`；证据在 `artifacts/deployment_simplification_20261018/`。未执行 git commit/push。

## 34. 检索链路优化与完整索引复测（2026-10-19 整理验收）

- 目标与授权：用户要求继续优化检索，保留功能对应的知识范围与真实模型规划。基线为上次专家任务检索 84.810 秒，以及本轮独立进程 `baseline-live/manifest.json` 的 67.994 秒，其中五次元数据枚举扫描累计 40.284 秒、LLM 请求 10.864 秒。这里测量查询已有索引，不是构建索引。
- 环境：工作目录 `/vepfs-mlp2/c20250513/241404044/users/roytian/TheraSkillAgent`；解释器 `/vepfs-mlp2/c20250513/241404044/users/roytian/anaconda3/bin/python`，Python 3.12.7、FAISS 1.13.2。活动索引仍为 `data/theraskill/indexes/phase6-v2-20261018-hybrid`，模型仍为 `deepseek-v4-pro` / `text-embedding-3-large`；未切换索引或重建向量。
- 实现：构建时同步写元数据清单，旧索引一次扫描生成可跨进程的版本缓存；独立通道并行并与 LLM 规划重叠；请求内共享范围 ID，小范围按完整约束及文件指纹缓存；移除同技能整请求串行锁，保护共享 FAISS 初始化；修正重复 eligibility 谓词触发的 SQLite 广泛扫描；只读映射 Flat 向量，使用 IDSelectorArray 与紧凑 ID/行号映射。新增 `inventory.py`、`diagnostics.py`、`vectors.py`，可选 FAISS 最低版本更新为 1.13.2；基础依赖数不变。
- 功能修正：发现 required-family 合并 LIMIT 会被问答记录占满，逐家族补缺后认知重构前三条包含方法量规，REBT 前四条包含量规。回填仅放宽模型建议范围，保留用户硬过滤及功能禁用范围。新增认知重构家族检查。此修正有意改变最终排序，不与纯性能优化的结果一致性混称。
- 对比界限：`baseline-v1/` 固定记录计划的两次检索为 20.098 / 13.791 秒；`optimized-reference-v1/` 为 9.609 / 6.038 秒，八条 ID 和排序相同。最终 `final-review-reference/` 为 6.094 / 6.033 秒，因量规覆盖修正而改变排名。reference 模式是显式诊断参数，不进入生产或真实业务评估；生产每次继续真实 LLM 规划，没有以缓存答案或规则替代。
- 真实检索结果：`final-six-skills-live/manifest.json` 的六例全部完成，情绪支持 14.214、风险分流 16.883、认知重构 18.400、REBT 9.530、情景构建 16.456、回应审核 11.524 秒，均包括真实规划；每例八条，共 48 条结果。策略与对应家族检查均通过，硬过滤另按主索引元数据复核。累计进程峰值 RSS 最大 726756 KiB（709.7 MiB），不是全应用内存承诺。
- 界面与业务：八页浏览器 200，无页面异常/请求失败，所有浏览器请求端口为 5176，390 px 无整页溢出。真实中文专家任务 `expert_job_8d59ae32159d479c9173f097e1735cc5` 提交 0.22 秒，最终 idle 且结果非空；检索 14.773 秒、生成 15.049 秒。上次同类任务输入和模型计划不同，不据此声称受控加速倍数。应用已加载新代码，PID 2116572。
- 验证：`python -m pytest -q -o addopts='' tests/test_standalone_kernel.py tests/test_theraskill*.py tests/smoke_theraskill_install.py` 为 228 passed / 4 条既有警告 / 21.55 秒。重建 wheel 后在 `/tmp/theraskill-lean-verify-0_b93602/venv` 以 `python -I tests/smoke_theraskill_install.py` 通过；隔离环境无 NumPy、FAISS、PyTorch 或旧 agent 模块，模型接口使用明确的本地 fixture。wheel 新增模块完整，不含本机 provider 文件。
- 证据与复现：`artifacts/retrieval_optimization_20261018/` 保留各阶段 manifest/bundle/log、六例输入 `cases.json`、`expert-result.json`、`browser-validation.json`、`regression.log`、`lean-validation.log`；最终 `manifest.json` 记录代码/提示词 hash、来源、范围复核与安装结果。诊断脚本 `scripts/AdvancedRetrieval/profile_runtime_retrieval.py` 支持 `--plan-mode live|reference`、`--reference-bundle`、新 `--output-dir`、`--runs` 和 live 下的 `--cases`。完整命令和比较边界见 `docs/retrieval_optimization_20261018.zh.md`。
- 排查与限制：单独使用 FTS rank 或 rowid 提示未消除大语料开销，SQL 诊断结果保留；最终处理重复 eligibility 条件后小范围才能使用语言索引。宽泛中文词法仍可能触及预算，明确暴露 warnings；旧索引首次清单准备、网络盘、模型调用和无过滤全局向量读取仍可能慢。六例工程检查及一次业务复测不能替代正式相关性或临床评价，也未进行并发负载测试。
- 状态与保留：本轮本地实现、部署复测和文档完成；保留已有未提交改动和全部中间结果。没有额外发布知识、备份上传、git commit 或 push。

## 35. README 与 main 源码同步准备（2026-10-19）

- 用户要求更新 Git 和 README；沿用此前直接推送 main 的授权。更新中英文 README 的产品流程、单进程入口、检索说明和编辑配置步骤，增加 `CHANGELOG.md`，同步前端说明、中英文工作流和源码验收说明。
- 补齐发布清单缺少的共享翻译组件、评估 JSON、新功能与验收文档。按清单暂存本轮及此前已完成的平台/部署/检索源码，不把旧宿主未跟踪代码、完整数据、运行日志或本机 provider 文件加入 Git。开发版 Vite 的 `/api` 改为统一转发至单个 5186 后端，与当前部署路由一致；正常应用端口仍为 5176。
- 基线提交 `0e0fe53f57bccb859b52b48f90edeb4909555277`，远端 main 经 SSH 读取和 fetch 核对一致。HTTPS fetch 首次等待未完成后终止，改用既有 SSH 凭据；没有改写 Git 全局配置。
- 在 `/tmp/theraskill-git-source-p4bw51_l/source` 验证发布清单导出的干净源码：Node 22.22.2 / npm 10.9.7，`npm ci` 与四界面 `npm run build` 成功；Python 3.12.7，`python -m pytest -q -o addopts='' tests/test_standalone_kernel.py tests/test_theraskill*.py tests/smoke_theraskill_install.py` 为 219 passed / 6 skipped / 3 warnings / 17.96 秒。六项跳过依赖未分发的模拟语言素材，三项旧宿主测试未导出；不可把此口径与完整本机 228 passed 混为一谈。
- 同一干净源码 `python -m build --no-isolation` 成功生成 sdist 并从 sdist 构建 wheel；在 `/tmp/theraskill-lean-verify-0_b93602/venv` 以 `python -I` 运行导出源码中的安装 smoke，通过四界面、六模块、fixture 抽取交接及词法检索检查。没有为这轮 Git 同步额外发起真实业务模型任务。
- 证据：`artifacts/git_sync_20261019/source-manifest.json`、`npm-ci.log`、`frontend-build.log`、`clean-source-tests.log`、`package-build.log`、`lean-validation.log`。提交前继续执行清单、链接、暂存范围及文件 hash 核对；实际提交和推送收据记录到本地 `git-receipt.json`。远端 CI 以实际运行状态为准。
