# 知识、索引与独立资源包

## 输入

接受 Phase 6 发布 JSONL（`release_unit_id`、`content`、`retrieval_policy`）以及通用 JSONL（见 `schemas/knowledge.schema.json`）。原始数据集和旧研究目录不是依赖。通用格式至少包含稳定 `id`、`title` / `text`；默认候选不参与检索，需 `review_status=approved/released` 且检索策略明确允许。构建本身不会审批候选。

原字段不同可使用 JSON 映射文件，如 `{"id":"record.id","text":"content.summary"}`，CLI 传 `--mapping mapping.json`；这是字段路径映射，不执行自定义代码。

`examples/knowledge/demo.jsonl` 是自编小样例。完整知识与模拟素材单独发布，当前没有公共下载链接。

## 构建和查询

```bash
theraskill-agent index plan --knowledge examples/knowledge/demo.jsonl
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl --id demo-v1
theraskill-agent index verify --index demo-v1
theraskill-agent index search --index demo-v1 --query uncertainty
theraskill-agent knowledge activate --index demo-v1
theraskill-agent knowledge list
```

默认产物位于 `data/theraskill/indexes/<id>/`。词法构建生成主 FTS、metadata 和 compact-label 三个 SQLite 文件，保留旧检索器使用的来源与路由字段。不会调用 LLM 或 embedding 服务，也不要求安装 NumPy / FAISS。

```bash
pip install -e '.[retrieval]'
theraskill-agent index plan --knowledge examples/knowledge/demo.jsonl --mode hybrid
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl --id demo-bge --mode hybrid
theraskill-agent index search --index demo-bge --query uncertainty
```

`plan` 扫描输入并估计请求量，不发 embedding 请求。磁盘估算为粗略的词法估算，向量另需空间。完整文件的扫描和 hash 会耗时。`build` 才调用 `providers.yaml` 的 `embedding` 服务；可在该块设置 `dimensions`、`batch_size`、`revision`。`dimensions` 会发送给服务，并验证响应。模型服务不提供 revision 时应自行维护配置版本，系统无法识别同名服务在后台静默换权重。

中断后传相同参数并加 `--resume`。词法阶段中断会重建词法文件；已完成的 embedding 批次由 SQLite 事务保存，恢复时只补缺失记录。恢复前核对输入 hash、映射、投影版本、模型身份和已有词法文件；不兼容则拒绝。已完成索引不可覆盖，需使用新 ID。

输出清单记录所有文件 hash / 大小、输入知识 hash、模型身份、维度和覆盖数量。`total` 是知识总数，`indexed` 是可词法检索数量，`embedding_eligible` / `vectors` 是可嵌入 / 实际嵌入数量；不能把总知识数当向量数。

## 安装、切换和界面

```bash
theraskill-agent knowledge install --archive /path/to/knowledge.tar.gz
theraskill-agent knowledge install --catalog /path/to/catalog.json --package demo
theraskill-agent knowledge activate --index demo-v1 --knowledge demo
theraskill-agent knowledge rollback
```

目录可以使用本地路径或 HTTPS URL，支持校验下载、分卷拼接和分卷断点继续。资源解包逐文件核对清单；拒绝路径越界、链接、重复成员或未声明文件。安装不会自动启用索引。下载前检查磁盘空间。取消下载保留已下载部分；本地解包进入原子安装阶段后会完成该阶段。

当前选择存储在 `active-index.json`，上一版本在 `previous-index.json`。知识版本与索引版本分别管理。切换新索引不覆盖原 `current` 知识发布物。支持助手、知识搜索工具和候选去重会读取当前选择；向量不匹配时拒绝使用，支持/搜索可回退到带策略的词法检索并报告原因。

工作台「知识与索引」提供 JSONL 上传（32 MiB 内）、本机大文件路径、构建任务、取消/恢复、包安装、校验、检索测试、启用/回滚和中英文切换。API 在现有 UI 服务的 `/api/resources`，无需新端口。后台任务写入 `state/resource-jobs`；单进程串行执行，索引目录另有进程锁。进程停止后任务显示中断，需显式继续。

`theraskill_resources` 工具与 `knowledge-index-manager` 技能使用同一任务实现，不需要界面服务才能调用。工具提交构建后返回 job ID，通过 `status` 获取最终结果。

## 单独打包

```bash
theraskill-agent knowledge pack --source examples/knowledge/demo.jsonl --output /tmp/demo.tar.gz --id demo --kind knowledge --license CC0-1.0
theraskill-agent knowledge pack --source data/theraskill/indexes/demo-v1 --output /tmp/demo-index.tar.gz --id demo-index --kind index
```

命令同时输出 `.download.json`，可把其中的记录放入 `catalog.json` 的 `packages` 数组。下载地址默认是相对压缩包文件名；上传后填写实际 URL。需要分卷时加 `--part-bytes 1073741824`，记录每卷顺序与 hash；分卷不可分别解压。完整包保留在本地便于核对。

打包知识需要明确数据许可，参数不会替你确认第三方数据的再分发权。预构建索引也可能含原文，需遵守相同来源限制。目前提供知识和索引的打包命令，模拟素材的专门打包入口仍待整理。公开 catalog 不填虚构 URL。
