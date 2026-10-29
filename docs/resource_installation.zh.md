# 独立资源 ZIP 安装

[English](resource_installation.en.md) · [部署](deployment.zh.md)

程序单独安装。知识与情景素材各是一份 ZIP，按需分别交付、安装或更新。当前资源版本为 `phase7-20261028`。

## 收到什么

收到下面两份资源 ZIP 后，直接将需要的一份交给对应安装命令，不要解压 ZIP：

| 文件 | 内容 | 压缩后 / 安装后 |
| --- | --- | --- |
| `knowledge-phase7-20261028.zip` | 2,196,286 条知识发布记录；保留证据、策略与来源字段 | 约 699 MiB / 6.28 GiB |
| `simulation-phase7-20261028.zip` | 场景、病例、persona、注册表及现有语言覆盖文件 | 约 91 MiB / 669 MiB |

每个 ZIP 内的 `package.json` 已包含包类型、文件大小、SHA-256 和来源/许可说明；安装器会自动核对，不需要外置 catalog 或 provenance 文件。包内不含模型权重、预构建向量索引、本机密钥、用户会话、业务数据库或生成草稿。代码许可不能代替原数据集的使用和再分发条件。

安装资源本身约需 7 GiB；另外为 ZIP、临时解包、日志和索引预留空间。索引大小取决于模式、可检索记录与 embedding 维度，先运行 `index plan`。全量 hybrid 构建会产生真实 embedding 请求和服务费用。

## 安装和首次启用

先完成 [程序安装](deployment.zh.md)，使用 0.5.0 或更新版本。以下命令显式指定工作目录，避免把资源装到另一份项目中：

```bash
theraskill-agent --project ~/TheraSkillAgent knowledge install \
  --archive /path/to/knowledge-phase7-20261028.zip
theraskill-agent --project ~/TheraSkillAgent simulation install \
  --archive /path/to/simulation-phase7-20261028.zip

theraskill-agent --project ~/TheraSkillAgent index plan \
  --knowledge knowledge-phase7-20261028
theraskill-agent --project ~/TheraSkillAgent index build \
  --knowledge knowledge-phase7-20261028 --id my-knowledge --mode lexical
theraskill-agent --project ~/TheraSkillAgent knowledge activate \
  --index my-knowledge --knowledge knowledge-phase7-20261028
theraskill-agent --project ~/TheraSkillAgent ui
```

源码项目在别处时，把所有 `~/TheraSkillAgent` 换成实际路径。Lexical 构建不需要模型或 FAISS；首次全量构建仍需读取数百万条记录。Hybrid 检索需要安装 `retrieval` extra，再将构建命令改为 `--mode hybrid`，并使用新索引名。中断后可用相同输入和配置加 `--resume` 继续。

首次安装模拟素材会在空工作目录中自动选中；已运行的应用需要重启。安装新版本不会覆盖已有资源，也不会自动替换活动知识索引。替换现有索引要完成效果对比和人工复核；上面的首次启用命令仅适用于尚无活动索引的设备。

也可在“知识与索引 → 添加资源”中分别上传：知识 ZIP 放入“安装知识包”，场景 ZIP 放入独立的“安装模拟场景包”。界面会拒绝放错入口的包。大型文件建议使用上述 CLI。

## 自定义资源包格式

所有资源包使用同一个外壳：压缩包第一项必须是 `package.json`，包含
`schema_version: theraskill_resource_package_v1`、唯一的 `package_id`、资源
`kind`、带 hash 的 `files` 清单和 `contract`。外壳统一，内容入口严格分开：

| 类型 | 必须包含 | 记录要求 | 安装命令 |
| --- | --- | --- | --- |
| `knowledge` | `knowledge.jsonl` | 每行一个 JSON 对象，包含稳定的 `release_unit_id`（或在构建索引时提供字段映射）和可检索内容 | `theraskill-agent knowledge install` |
| `index` | 已校验的索引目录和 `manifest.json` | 由 TheraSkillAgent 索引构建器生成 | `theraskill-agent knowledge install` |
| `simulation` | `scenarios/scenario_registry.jsonl`，以及 JSON/JSONL/YAML/Markdown/文本素材 | 注册表每行是对象，必须有非空 `scenario_id`；病例和患者素材与注册表一起打包 | `theraskill-agent simulation install` |

使用 CLI 分别制作自定义包：

```bash
theraskill-agent knowledge pack --source ./my-knowledge.jsonl \
  --output ./my-knowledge.zip --id my-knowledge-v1 --kind knowledge --license CC-BY-4.0
theraskill-agent simulation pack --source ./my-simulation \
  --output ./my-simulation.zip --id my-simulation-v1 --license CC-BY-4.0
```

`my-simulation` 必须包含 `scenarios/scenario_registry.jsonl`。安装器会先检查
清单、每个文件的大小与 SHA-256、注册表 JSON 和每条记录的 `scenario_id`，通过
后才原子移动到独立的资源目录。模拟场景包不会触发知识索引构建；资源版本不
可覆盖，修改后使用新的 ID。

## 检查安装

```bash
theraskill-agent --project ~/TheraSkillAgent knowledge list
theraskill-agent --project ~/TheraSkillAgent provider check --kind llm --live
theraskill-agent --project ~/TheraSkillAgent provider check --kind embedding --live
theraskill-agent --project ~/TheraSkillAgent skill check transcript-normalizer
```

首页“使用准备”会显示模型连接、索引、情景素材与技能检查状态。进入情景控制台创建会话，确认素材可用。

现有英文模拟素材为 **15 个试点场景、12 个病例覆盖**，并非全库翻译。现有知识中 `synthetic_construction` 没有英文记录；强制仅英文检索时会报告缺少这一类证据。中文来源可用于生成英文结果，但原文证据保持原语言；这与“仅检索英文来源”的过滤条件不同。

## 维护者重新制作包

在具有完整发布数据的开发目录运行：

```bash
python scripts/TheraSkill/package_user_resources.py \
  --source data/theraskill/current \
  --output /path/to/new-resource-folder --version phase7-20261028
```

输出目录必须不存在，并且只生成两份可独立安装的 ZIP。资源格式和索引安装见[知识与索引](knowledge_and_indexes.zh.md)。
