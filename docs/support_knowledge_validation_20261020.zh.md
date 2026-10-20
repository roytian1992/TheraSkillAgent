# 支持助手与知识编辑验收（2026-10-20）

本轮覆盖支持助手轻量回复、证据阅读、知识草稿、候选索引重建及其界面联动。

## 已完成

- 对问候、确认和明确的产品导航问题使用短提示词，跳过路由模型、检索和历史压缩。存在安全上下文或心理问题时继续走原有处理流程。
- 回复公开记录本轮是否检索、是否命中；引用绑定索引版本及知识源校验值。旧引用没有版本信息时明确提示无法确认历史版本。
- 证据抽屉及知识工作区共用结构化阅读器，展示原始字段、列表、嵌套示例和独立草稿；来源与索引元信息可展开查看。
- 保存仅提交已修改字段，保留数组类型；版本冲突时保留输入，并读取新版本供核对。原始发布文件不被改写。
- 重建任务固定来源、草稿修订和 embedding 身份，支持持久化进度、失败重试和已完成结果复用。候选仍须验证、评估后启用。
- 大型来源文件后台建立校验绑定的字节偏移目录，原文读取不再逐次扫描全文件。

## 自动化验证

后端回归 **138 项通过**：

~~~bash
python -m pytest -q \
  tests/test_theraskill_safety.py \
  tests/test_theraskill_support_workflows.py \
  tests/test_theraskill_llm_output.py \
  tests/test_theraskill_session_evidence.py \
  tests/test_theraskill_knowledge_editor.py \
  tests/test_theraskill_indexing.py \
  tests/test_theraskill_retrieval.py \
  tests/test_theraskill_hybrid_retrieval.py \
  tests/test_theraskill_retrieval_performance.py \
  tests/test_theraskill_managed_retrieval_passages.py \
  tests/test_theraskill_promotion_gates.py
~~~

浏览器回归 **11 项通过**：

~~~bash
npm run build
python -m pytest -q \
  tests/test_theraskill_support_knowledge_browser.py \
  tests/test_theraskill_knowledge_browser_integration.py \
  tests/test_theraskill_task_history_browser.py
~~~

四个前端工作区均构建成功。浏览器覆盖中英文、390px 手机布局、键盘操作、未保存输入保护、409 冲突合并、请求竞态、历史索引缺失和候选跳转。真实后端集成测试使用隔离的合成知识，实际执行读取、保存、词法重建、检索与验证；原始文件和当前索引保持不变。hybrid 的重建及配置漂移使用假 embedding 服务验证，不向生产服务批量发请求。

## 本地运行验收

确认没有活动业务任务后，重启了单进程应用，保留原启动参数及配置；入口仍为 5176，没有增加端口或软件依赖。

- 新建独立的合成问候会话，使用当前配置的真实回复模型；完成后已删除该测试会话。
- 此次端到端耗时 **5.095 秒**；记录的回复生成耗时 **4481 ms**，路由、检索、历史压缩均为 **0 ms**，公开消息标记 retrieval_skipped=true。这是单次功能测量，不代表所有问题的延时。
- 正式知识源的首次定位目录已完成。目录准备期间详情请求立即返回已索引段落，之后实测完整字段详情 **0.167 秒**返回，编辑和重建入口可用。
- 没有为正式知识库提交重建或替换当前索引。本轮不宣称心理问题的完整检索延时已经降到上述问候水平。

本机验收证据保存在 artifacts/support_knowledge_upgrade_20261020/：live-fastpath.json、live-lookup.json、live-ui.json 和合成界面截图。运行产物与生产知识不进入 Git。

## 当前行为边界

修改一条知识后，现有按钮构建的是**新的完整候选索引**，不是单条向量增量补丁。大型 hybrid 库可能产生全库 embedding 请求，页面与文档已明确说明；单条向量更新可作为后续独立优化。普通支持对话执行的是现有知识检索，不是知识抽取任务。
