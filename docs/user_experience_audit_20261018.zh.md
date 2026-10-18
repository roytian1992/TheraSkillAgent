# TheraSkillAgent 深度使用体验与功能审计

本报告保留当时的审计/复测状态。最新部署见[部署说明](deployment.zh.md)，后续改动见[更新记录](../CHANGELOG.md)。文中的 `artifacts/` 是原开发设备上的本地证据，不随 Git 源码分发。

日期：2026-10-18。审计版本：`0e0fe53f57bccb859b52b48f90edeb4909555277`。

**当前已有可用的业务组件，但还不能作为“所有功能均可连续完成”的产品交付。** 最大的问题是任务中途失败后无法恢复、界面与后端能力脱节，以及评估结果的可信度。继续增加页面和 skill 数量之前，应先打通现有的完整任务。

本轮实际操作了工作台、支持助手、训练台、专家工作室、知识抽取、对话分析和知识索引界面，调用了真实模型，并检查了 CLI、运行记录和相关源码。下文明确区分浏览器操作、API 诊断和代码检查；后端成功不记作界面通过。

截图浏览页：`artifacts/user_experience_audit_20261018/index.html` · 逐项覆盖清单：`artifacts/user_experience_audit_20261018/coverage.json` · 运行与证据清单：`artifacts/user_experience_audit_20261018/manifest.json`

## 体验方式与边界

| 项目 | 本轮条件 |
| --- | --- |
| 项目 | `TheraSkillAgent/`，业务代码与模型配置未修改 |
| 浏览器 | Playwright 操作真实 Chromium：点击、输入、上传、下载、刷新、角色切换、截图与请求检查 |
| 页面 | 工作台 `http://127.0.0.1:5176/` 及三个业务子应用；抽取与对话分析从工作台打开 |
| 视口 | 桌面 1440×1000；移动视口 390×844。没有进行真机 Safari、软键盘或完整无障碍验收 |
| 模型 | 当前本地配置连接的真实 `deepseek-v4-pro`、`text-embedding-3-large`（3072 维）；没有用 mock 替代业务生成 |
| 运行中的知识索引 | `indexes/phase6-v2-20261018-hybrid`，索引 ID `idx_d5b9f0077e1ca31b990080de`；本轮没有切换此索引 |
| 索引切换试验 | 在临时工作台 5186 和独立数据根目录，使用 6 条合成知识验证安装、构建、启用、再次启用及回滚 |
| 输入 | 自建中英文混合对话、JSON/JSONL、SRT、含 ASR 错字的文本、工作压力问题、明确危险表达、明显不合适的治疗师回应 |
| 数据副作用 | 只创建或修改 QA 合成记录；支持用户标识 `qa-ux-20261018`。在线生成的 QA 场景最终退回；没有发布候选知识 |
| 耗时解释 | 部分真实请求并发执行，耗时只代表本轮观测，不是独立压测的 p50/p95，也不能计算稳定失败率 |

原始脚本中有过选择器、等待 URL 和超时设置错误。报告使用纠正后的请求记录及最终状态，不把测试脚本自身失败算作产品故障。候选去重在客户端等待 150 秒后超时，但随后查询确认服务端已完成，下文保留这个区别。

## 各功能实际覆盖

| 功能 | 实际完成的操作 | 结果与限制 |
| --- | --- | --- |
| 工作台 | 六服务状态、中英文入口、子应用导航、刷新 | 可打开；任务中心与模型配置主要是说明卡片，缺少真实的统一操作与恢复入口 |
| 支持对话 | 英文问题、实际回复、历史会话、结束对话 | 一次完整单轮成功；另一重复请求最终失败。没有完成长期、多轮连续支持验收 |
| 支持附属功能 | 保存目标/行动、记忆创建与确认/编辑/删除、反馈/撤回、JSON 下载 | 上述操作成功；目标的编辑、完成与后续跟进体验不足 |
| 风险流程 | 明确危险表达、安全卡、仍不安全/安全、联系可信任的人 | 最终回复与按钮操作成功，但安全卡等待约 462 秒才出现，不能视为安全流程通过 |
| 情景训练 | 搜索、创建、暂停/恢复、治疗师/来访者/双模拟模式 | 暂停恢复成功；治疗师与双模拟生成失败；来访者模式首轮成功后被记忆更新故障阻断 |
| 训练评估 | 结束复盘、查看 gate、研究员 review-only | 空对话错误显示 pass；独立 review-only 最终失败，不能视为评估能力通过 |
| 场景生成与治理 | 单场景生成、编辑、批准、退回 | 单场景成功；“质量 100”主要反映结构检查，容易误解为教学质量结论 |
| 课程 | 生成两场景批次、教师分配任务 | 分配成功；批次 502，未完成课程到学员训练和结果汇总闭环 |
| 专家回应审核 | 提交明显不合适的回应、拿到报告、保存版本、下载 Markdown | 成功识别无依据诊断与强指令建议，但检索路由进入情绪支持，且耗时约 8.5 分钟 |
| 专家内容实验室 | 请求两轮中文 CBT 教学对话 | 服务端最终 failed，界面仍显示“尚未生成结果” |
| 专家反馈/贡献 | 查看队列、提交知识提案 | 当前入口缺少专家档案，提示 `expert profile not found`，正常流程受阻 |
| 案例工作区 | 输入不存在的训练 ID | 返回 500 且页面没有明确错误提示；未将有效案例的完整编辑流程记作通过 |
| 对话分析工作台 | 检查格式、确定性分析、多 skill 评估、生成 bundle | 四个界面操作全部 422；去掉旧字段后直接 API 能工作，说明前后端契约断裂 |
| 对话格式兼容 | 文本、JSON、SRT、ASR 说话人/错字，比较 API 与 CLI | CLI 对 JSON/SRT 分轮正确，浏览器 API 误解析；ASR 角色需要人工确认能力 |
| 知识抽取 | 上传 JSONL、检查、自定义 schema、真实抽取、预览、交给治理 | 1 条输入生成 1 条候选、0 条抽取失败；刷新失去任务入口，handoff 回执不清楚 |
| 候选治理 | 抽取候选接收、结构化、去重、验证 | 额外使用 curator 请求头诊断后 API 成功；不是默认界面的完整用户闭环；没有发布知识 |
| 知识与索引 | 安装合成知识包、计划、构建、校验、词法查询 | 小样本成功；错误文件能拒绝。没有重建全量知识或重复进行全量性能评估 |
| 索引启用/回滚 | 独立根目录构建 A/B、依次启用、恢复 A | 真实界面成功；主服务活动索引保持原样 |
| Skill 管理 | 独立目录安装、检查、优化、覆盖升级、回滚 | Python 管理器操作成功；当前主界面没有相应管理工作区 |
| 通用 runtime | 文本规范化/分块/章节、输入检查、抽取计划、自建 skill 试验 | 已知任务类型成功；自建 skill 安装后作为新 task kind 运行失败，不等于任意 skill 都可执行 |
| 双语与移动布局 | 各入口切换语言、四个页面移动视口截图 | 部分成功；存在残留中文、模型输出语言独立、移动侧栏遮挡等问题 |

回应比较、有效训练后的逐轮标注/完整反馈、课程学习路径、专家再生成、候选正式发布与回滚、跨版本 skill 行为进化、长期多轮稳定性，本轮未完成完整验收。部分被前置故障阻断，不能据此宣称这些功能通过，也不能一概判定它们不存在。

## 需要首先修复的阻塞

### UX-01 / P0：对话工作台的四个操作全部不可用

复现：打开对话分析，粘贴带患者/医师标签的中英文文本，依次执行检查格式、确定性分析、多 skill 评估和生成 bundle。

实际：四次均为 HTTP 422，服务端拒绝 `body.provider_profile`；界面只显示 `执行失败: [object Object]`。即使不填写该字段，前端仍发送 null。直接 API 去掉它后四类操作可返回 200，模型评估也实际完成。

定位：`views/theraskill/workbench/src/TranscriptStudio.tsx` 仍保留旧配置字段；`domains/theraskill/services/transcript_api/app.py` 已使用禁止额外字段的新请求模型。

验收：通过真实浏览器文件/文本输入跑完四个操作；前端请求不包含废弃字段；错误显示字段名和可执行的修复方法；加入使用实际前端请求形状的契约检查。

证据：请求记录：`artifacts/user_experience_audit_20261018/transcript-ui-result.json`、错误截图：`artifacts/user_experience_audit_20261018/transcript-ui-error.png`、绕过旧字段的 API 对照：`artifacts/user_experience_audit_20261018/transcript-backend.json`。

### UX-02 / P0：高风险界面提示被慢生成流程阻塞

复现：在支持助手输入明确的当前自杀危险、独处且不安全表达。

实际：等待阶段公共状态仍是 `attention_level=none`，初期没有安全卡；约 461.9 秒后回复完成，才出现 urgent 安全提示。最终回复方向合理，但用户在最需要帮助的等待阶段看不到即时引导。

定位：`theraskill_support/service.py` 的 `submit_message` 只写入消息和 pending 状态；公共 safety 在 `finalize_response` 之后更新。现有风险筛查没有及时投射到界面。这里建议把已有快速安全状态与完整模型回应分开呈现，不以模板替代模型回复。

验收：明确危险输入在进入漫长检索/生成之前呈现安全引导；模型超时/失败时安全卡仍存在；按钮清楚说明“记录意向/提供步骤”与是否真的完成联络，不能让用户误认为系统已经替其联系他人。为明确危险表达建立独立的浏览器时序验收。

证据：等待中状态：`artifacts/user_experience_audit_20261018/support-recovery-ui-result.json`、最终时序与按钮结果：`artifacts/user_experience_audit_20261018/risk-observation-ui-result.json`、最终安全卡：`artifacts/user_experience_audit_20261018/support-risk-final.png`。

### UX-03 / P0：没有任何对话的训练可以显示“评审通过”

复现：治疗师模式首轮生成失败后，点击结束并复盘。

实际：transcript 为空、训练进度 0/3，复盘返回 `status=complete, gate_status=pass`。同一结果的文字又说没有足够证据，不能评估。绿色通过标识与证据状态直接矛盾；空对话还出现了评分控件。

验收：空记录、生成失败、有效轮数不足和评审失败应分别标记为不可评估/未完成/失败；不得落入 pass。把完成状态、有效证据量与教学质量结论分开显示，防止无效训练进入学习成绩。

证据：完整返回：`artifacts/user_experience_audit_20261018/simulation-training-ui-result.json`、0/3 与 pass 同屏：`artifacts/user_experience_audit_20261018/simulation-training-reviewed.png`。

### UX-04 / P0：辅助记忆更新失败会锁死有效训练

复现：扮演来访者，提交“最近上课注意力不集中”的问题。

实际：第一轮治疗师回应正常；随后 deferred memory 更新失败，公共状态仍 active。结束并复盘返回 409：`therapist memory update failed; session cannot continue`。界面没有可操作的恢复入口，已有对话也不能正常收尾。

验收：保存已成功的对话；明确展示失败步骤，提供有界重试/恢复；允许以明确的“记忆未完成”状态结束并保留记录，不能悄悄把缺失记忆当成正常完成。恢复后重复请求不应重复追加对话。

证据：成功首轮：`artifacts/user_experience_audit_20261018/simulation-client-ui-result.json`、409 和失败计数：`artifacts/user_experience_audit_20261018/client-review-checkpoint.json`。

## 影响任务完成和输出可信度的问题

### UX-05 / P1：模型兼容仅到“能连通”，尚未到任务级可靠运行

治疗师模式和双模拟返回 500，日志涉及模型输出达到 token 上限；内容实验室也最终因该问题失败。当前本地 LLM 配置有输出预算，但 simulation engine 仍存在自己的较小默认预算。不同模型的 reasoning 行为没有统一到任务配置。批次还遇到上游 `RemoteDisconnected`，这本身不能证明模型服务或本项目哪一方必然有错，但产品缺少恢复路径。

更明显的体验问题是失败呈现：内容实验室服务端已 failed，界面仍是“尚未生成结果”；部分 500 为纯文本，前端却按 JSON 解析；案例 ID 不存在返回 500，前端未捕获异常。独立 review-only 最终失败，界面也没有可用的步骤重试。

建议：任务使用明确的模型能力与预算配置；保留原始失败原因供诊断，对用户展示可理解的错误、已保存内容与恢复动作。不要只把全局 token 限额调大，也不要靠规则回答掩盖模型失败。

验收：用当前真实配置完成三种训练模式、内容生成和独立复盘；再主动测试截断、非 JSON 错误、断线与超时，保证 UI 能正确终止等待并恢复。模型健康检查展示“连接可用”和“业务 smoke 通过”的区别。

证据：内容实验室失败：`artifacts/user_experience_audit_20261018/expert-content-ui-result.json`、界面空结果截图：`artifacts/user_experience_audit_20261018/expert-content-final.png`、双模拟：`artifacts/user_experience_audit_20261018/simulation-dual-ui-result.json`、无效案例：`artifacts/user_experience_audit_20261018/expert-cases-ui-result.json`、独立复盘最终状态：`artifacts/user_experience_audit_20261018/review-only-final.json`。

### UX-06 / P1：已知业务任务仍会检索错误类型的知识

专家回应审核实际能指出“无依据诊断、直接要求辞职、否定感受”等问题，这是有效能力。但 trace 显示审核任务被路由为 `emotional-support`、`task_type=support`，且走了 deterministic fallback。审核场景通过自然语言包装复用了支持管道，业务工作流没有可靠地传入检索契约。

同一任务检索耗时约 471.8 秒，而生成约 28.2 秒；浏览器整体约 510.7 秒。普通支持的一个成功样本约 715.7 秒。并发与真实远程服务会影响耗时，但仅显示跳动圆点不足以让用户继续等待。

建议：由页面/任务明确传递 `workflow/task_kind`、所需证据类型、允许家族和排除项；query understanding 负责理解本任务内的问题，而不是再次猜已知的页面用途。审核检索量规、边界与过程规则；合成任务检索构建规则和教学目标；训练反馈检索阶段策略与评估准则。界面显示当前步骤、已用时间、失败/降级状态和引用依据。

验收：从真实专家页面提交审核，trace 中工作流和知识策略必须匹配；分别为支持、风险、审核、内容构建、训练评估、抽取治理设检查样例。检查家族符合业务只是工程门槛，还需要人工判断证据是否真的相关。

证据：路由与耗时 trace：`artifacts/user_experience_audit_20261018/expert-routing-evidence.json`、审核结果：`artifacts/user_experience_audit_20261018/expert-review-ui-result.json`。

### UX-07 / P1：课程批量生成绕过了已经存在的持久任务接口

实际点击生成两个场景，前端请求同步 `/scenarios/generate-batch`，约 127.6 秒后返回 502。后端已经有 `/scenarios/generate-batch/jobs` 及查询/重试接口，前端没有使用。

用户看不到逐项进度、已经完成的条目、失败项或可恢复任务。同步批次在全部生成后才集中保存，中途失败可能让先前的生成工作无法复用。

验收：UI 使用持久批次任务，逐项保存；刷新可找回；只重试失败项；同一批次不产生重复场景。课程页同时显示课程目标、场景覆盖与待审状态。

证据：实际请求与 502：`artifacts/user_experience_audit_20261018/simulation-batch-ui-observed-result.json`、界面：`artifacts/user_experience_audit_20261018/simulation-batch-observed.png`。定位：simulation console `src/api.ts` 与 simulation API 的两组批次端点。

### UX-08 / P1：统一工作台尚未提供连续的任务体验

抽取任务可在后台保存，但刷新后回到首页，重新打开抽取工作区找不到任务。根页面内部工作区没有稳定 URL。专家任务虽支持手工带 `job_id` 打开，但界面切换标签会清空当前 job，没有任务列表与版本入口。多个子应用没有共享导航或统一返回路径。

建议：实现真正的任务列表、稳定任务 URL、草稿保存和跨工作区跳转。页面离开不应等于任务丢失；列表区分运行、等人确认、失败可重试和完成可下载。

验收：在抽取和审核进行中刷新、关闭标签后重开、切换工作区，都能回到同一个任务和结果；无需用户复制内部 ID。首页“任务中心”应展示真实任务。

证据：刷新与草稿丢失：`artifacts/user_experience_audit_20261018/navigation-edge-ui-result.json`、抽取完成记录：`artifacts/user_experience_audit_20261018/extraction-ui-result.json`。

### UX-09 / P1：知识抽取仍要求用户自己设计 JSON，交互规划能力没有进入 UI

成功样本证明动态输入与真实抽取链路可用：上传 JSONL、自定义 claim/evidence schema、生成有逐字证据的候选。问题在于普通用户仍要填写抽取单元和 JSON schema；错误只显示 JSON parser 的英文异常。默认目标是固定中文，切换英文界面不会变成自然的英文任务设计。

建议：以“检查样本 → 澄清目标 → 推荐并解释 schema → 用户修改确认 → 少量试抽 → 批量运行”为主流程。保留高级 JSON 编辑，但同时提供字段表、例子、证据必填项和未知值策略。计划阶段说明条数与范围，允许先跑小样本。

handoff 后页面仍显示旧预览，候选 ID 和下一步入口不明显。应给出导入数量、去重处理、可点击的治理链接，区分交接成功和知识已发布。

验收：用户只提供文件和自然语言目标，能在对话中确认 schema；试抽结果可回到计划修改；正式运行可恢复；handoff 后一键看到对应候选。

证据：成功抽取：`artifacts/user_experience_audit_20261018/extraction-ui-result.json`、handoff 页面：`artifacts/user_experience_audit_20261018/extraction-handoff.png`、schema 错误：`artifacts/user_experience_audit_20261018/extraction-invalid-schema.png`。

### UX-10 / P1：浏览器导入、CLI 与原文追溯还没有统一

绕过 UX-01 后，JSON 对话在浏览器 API 被当成一整段未知说话人文本，SRT 被拆出包含序号和时间行的五个 turn；CLI 对相同文件各得到正确的两轮。浏览器 API 没有走相同的文件适配逻辑。

ASR 样本的“说话人1/说话人2”均为 unknown，明显的“牙力/压力”问题没有形成可确认的语义校正。不能期待系统自动改掉所有错字，但用户至少需要角色映射、疑似错词确认、合并/拆分 turn 的工作区。

此外，parser 先规范化全文，再生成 turn 的 `raw_text`，导致原本的全角标点已经改变。原输入的 hash 仍保留，不能说完整原文完全丢失；问题是逐轮 raw/normalized 层级没有严格区分，修订追溯不够直观。

bundle 返回服务器路径和 download 提示，但 UI 没有实际下载链路。普通用户不应 SSH 找文件。

验收：浏览器与 CLI 共用格式适配层；提供原文、规范化文本、确认修订三层与可定位来源；导出规范 JSON、可阅读报告和真正可下载的 bundle。

证据：API/CLI 与不同格式对照：`artifacts/user_experience_audit_20261018/transcript-backend.json`。定位：`theraskill_agent/transcript.py`、`services/transcript_api/app.py`、`TranscriptStudio.tsx`。

### UX-11 / P1：展示了“知识依据”，但用户不能充分检查依据

支持回复中的依据卡主要是 `style_example`、`risk_or_symptom_signal` 等技术标签；一些边界和用途是字符串化的列表，日期为空。缺少原始片段、来源位置、知识版本与其支持了哪一句判断的清晰连接。

对话模型评估会引用 turn ID，这是值得保留的部分；但源码中 review 计划列出的 `knowledge_search` 不等于实际执行工具，当前模型 review 主要将 skill 文本与材料交给模型，没有据此完成知识工具调用。不能只靠“使用了某个 skill”声明来证明评估有知识依据。

验收：每项重要评估能点开原对话证据及知识片段，区分观察、推断、示例与规则；没有检索时如实标记；不能以框架名称或家族命中替代证据相关性。

证据：风险回复依据字段：`artifacts/user_experience_audit_20261018/risk-observation-ui-result.json`、对话模型评估：`artifacts/user_experience_audit_20261018/transcript-backend.json`；工具执行判断来自 `theraskill_agent/reviews.py` 代码检查。

### UX-12 / P1：治理状态闭环已存在，质量闭环仍不足

本轮候选成功从抽取流入治理；通过诊断请求头执行结构化、去重，最后验证返回 200。去重曾超过客户端 150 秒等待，后续查询状态为 `dedup_checked`，因此不是“去重服务失败”。默认界面的治理身份与导航问题阻止了普通流程。

需要补强的核心在质量验证：`BuiltinCandidateRegressionRunner` 生成 canonical_label/summary 两个查询用例，但检查的是“生成了两个用例”等局部不变量，没有实际把查询交给检索器测召回，也没有执行回应行为回归。它的 pass 不能解释成“新知识已提升 Agent”。

建议：把候选编辑/证据核对、重复关系解释、人审决策、真实检索回归、任务行为回归和版本发布连接起来，分别显示结构通过和行为验证结果。本轮未发布候选，未测试正式知识 release promotion/rollback；已验证的索引回滚不能冒充知识发布回滚。

证据：去重最终状态：`artifacts/user_experience_audit_20261018/governance-final-observation.json`、验证诊断：`artifacts/user_experience_audit_20261018/closing-checks.json`。定位：`theraskill_contribution/candidate.py:317`。

### UX-13 / P1：专家功能缺少可完成的首次使用流程

反馈审阅和知识贡献提示未绑定专家档案，提案返回 404。默认前端身份由构建配置决定，用户没有清楚的“当前角色、专家档案状态、如何完成配置”的引导。提案与版本编辑还直接暴露 JSON，而没有证据选取或字段表单。

建议：把本地专家档案/职责配置、队列空状态说明和待办入口补齐，给审核员提供证据、建议、结论和退回理由的阅读/编辑视图。本项不扩大成此前已决定暂缓的用户归属工程。

验收：新部署者依照页面引导可完成一次提案、审阅、退回修改和再次提交；不存在的资源返回明确 404，页面保留输入并给出提示。

证据：专家导航与提案结果：`artifacts/user_experience_audit_20261018/expert-navigation-ui-result.json`、缺少档案界面：`artifacts/user_experience_audit_20261018/expert-queue.png`。

### UX-14 / P1：训练与课程还缺少教学产品的组织方式

初始列表显示 200 个场景，前列大量同类“围绕某问题的认知重评探针”标题，类别与难度不易理解，缺少清晰的分层推荐与分页。技术性训练目标、英文标签、正式数据划分信息容易暴露给学习者，教师信息与学员信息需要按教学意图区分。是否构成不当提示，应由后续教学设计验证，而不是仅凭字段存在下结论。

单场景能生成、编辑、批准/退回；质量 100 应明确解释为哪些自动检查通过，不能暗示临床或教学质量满分。需要可阅读的患者设定/隐藏信息/评分标准编辑、版本差异和退回说明。

教师分配任务成功，但目前不能据此认定“课程建设 → 审批发布 → 学员接收 → 完成练习 → 汇总能力变化”已完成。支持助手的目标与下一步也偏向追加记录，没有完整的编辑、完成和跟进体验。

验收：先给新手一个可完成的短课程，显示目标、前置要求、下一步、练习结果与复练建议；教师可查看学员任务状态；训练复盘必须有有效轮次与具体改写建议。

证据：场景列表与空训练：`artifacts/user_experience_audit_20261018/simulation-training-reviewed.png`、生成/审核：`artifacts/user_experience_audit_20261018/simulation-governance-ui-result.json`、教师分配：`artifacts/user_experience_audit_20261018/simulation-teacher-ui-result.json`。

### UX-15 / P1：Skill 管理能力与统一 Agent 执行能力之间还有缺口

独立目录中安装、检查、格式优化、覆盖升级和回滚都成功，发现 12 个 bundled skills。文本规范化/分块/章节和抽取规划可运行。但把新安装的合成 skill 作为新 task kind 提交，runtime 返回 `unsupported task kind`。当前 runtime 仍由已知任务类型分支执行；这个试验说明“安装文件”本身没有形成通用执行能力，不能推导成所有内置 skill 都不可用。

现有 optimize 主要修复 frontmatter、名称和描述，属于包装质量优化。它不等于基于任务失败样本提升行为质量。GUI 也没有 skill 目录、依赖/权限说明、安装升级回滚与效果对比；没有通用的文件对话工作区将澄清、工具使用和人工确认串起来。

建议：统一 skill 的输入/输出契约、工具能力、交互状态、依赖与版本；runtime 解释执行经许可的 skill 流程，领域任务保留可靠工具。增加用户可见的计划确认、schema 确认、评审决策与失败恢复组件。技能进化必须有固定样例和前后对比，不能只按文档格式打分。

验收：从界面安装一个合法的新 skill 后，能够解释它能做什么、收集输入、调用允许的工具、等待确认并输出可追溯结果；升级前后有任务级回归，失败可回滚。

证据：SkillManager 与 runtime 实测：`artifacts/user_experience_audit_20261018/local-skills.json`。定位：`theraskill_agent/runtime.py`、`skills.py`、`workflows.py`。

## 界面、上手与一致性

### UX-16 / P2：首次使用仍要求理解内部部署细节

首页突出端口、服务数量、配置路径和命令，实际用户更需要“我要练习、整理一份对话、做评估、抽取知识、维护知识”的任务入口。模型卡指向 `providers.yaml`，没有说明可能实际生效的 `providers.local.yaml`；知识包安装主要要求服务器路径或 catalog 地址，没有直接上传压缩包的浏览器入口，和对话/抽取的文件上传体验不一致。

建议：首页围绕任务组织，技术诊断放到设置；展示已生效的 LLM/embedding 模型和服务地址摘要，隐藏密钥；提供配置校验与可读的知识包安装状态。用户仍可坚持用本地配置文件，不必被迫改成网页存储密钥。

### UX-17 / P2：双语切换不等于任务与输出语言兼容

英文首页仍有中文服务说明；专家导航部分中文未切换；支持目标区残留中文；输出报告也可能在中文界面显示英文。各子应用命名还混用 TheraSkill 与 TheraSkillAgent。DOM 翻译字典与动态界面维护容易脱节。

建议：统一组件级 i18n，分别控制界面语言、输入语言和期望输出语言，向 prompt 及任务契约传递明确参数。报告、错误、下载文件、schema 字段说明均纳入双语验收。

证据：英文首页：`artifacts/user_experience_audit_20261018/home-en.png`、英文专家界面：`artifacts/user_experience_audit_20261018/expert-proposal-en.png`、英文支持界面：`artifacts/user_experience_audit_20261018/support-en.png`。

### UX-18 / P2：移动布局能缩放，但还没有成为顺手的移动体验

390px 视口没有横向溢出，这是正面结果；但训练台默认展开的场景侧栏盖住主要内容，角色按钮挤成两行，表单与结果常需长距离滚动。仅凭无溢出不能视为移动端可用性验收通过。

验收：默认展示主要任务，抽屉可明确关闭；角色/语言/任务进度在小屏可识别；再用真机完成一次上传、训练、反馈与报告下载，并测试软键盘和焦点导航。

证据：训练移动布局：`artifacts/user_experience_audit_20261018/simulation-mobile.png`、支持移动布局：`artifacts/user_experience_audit_20261018/support-mobile.png`。

## 建议的推进顺序与完成标准

| 顺序 | 交付内容 | 结束本阶段的标准 |
| --- | --- | --- |
| 第一阶段：可靠完成 | UX-01～05；修复错误状态、危险提示时序、空记录 gate、记忆失败恢复和任务预算 | 真实浏览器完成对话分析四操作、三种训练模式、有效训练复盘；显式危险输入即时显示安全引导；断线/截断后有明确恢复；没有空记录 pass |
| 第二阶段：任务连通 | UX-06～13；任务契约、业务检索策略、持久任务列表、schema 澄清、证据引用、专家与治理入口 | 用户仅靠界面完成“文件 → 确认格式/角色 → 分析评估 → 下载”和“文件 → 计划/试抽 → 编辑审核 → 检索验证”；刷新/换页可继续；不要求抄 ID 或修改请求头 |
| 第三阶段：学习与进化 | UX-14～18；完整课程、skill 执行与回归、上手和双语/移动完善 | 教师到学员闭环可用；新 skill 可安装且有契约执行与效果验证；候选知识的行为回归与发布分离清楚；新部署者能独立上手 |

这里的“统一 Agent”应体现为一个能理解任务、提出澄清、调用工具、等待确认、保留证据并恢复失败的工作流。现有领域服务适合承担可靠工具，统一工作台与 runtime 负责把它们连接成用户能够完成的任务。

## 测试结论如何解释

本轮另外执行已有检查：

```bash
python -m pytest -q -o addopts='' \
  tests/test_theraskill_ui_transports.py \
  tests/test_theraskill_transcript.py \
  tests/test_theraskill_support_workflows.py \
  tests/test_theraskill_simulation_review.py
```

结果 **14 passed，3 warnings，2.35 秒**，见 原始日志：`artifacts/user_experience_audit_20261018/existing-tests.log`。这些检查不能证明真实前端请求、当前远程模型、全量检索时延和完整用户旅程都正确；本轮的四个 422 和空记录 pass 正说明需要补浏览器验收与真实任务 smoke。

本报告不提供临床效果结论、稳定性通过率或“全部功能已测完”的承诺。临床内容只检查了少量合成案例的明显边界问题；长程支持、多语言全矩阵、并发容量、正式人评和生产发布仍需独立验收。

本轮交付的是体验结果、证据和可执行的修复清单；尚未修复上述业务代码，也未推送或生成新的项目压缩包。原始证据位于本地被忽略的 `artifacts/`，仅提交本文不会同时包含截图。
