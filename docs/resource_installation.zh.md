# 独立资源 ZIP 安装

[English](resource_installation.en.md) · [部署](deployment.zh.md)

代码从 Git 安装；完整知识与情景素材由项目维护者另外提供。当前资源版本为 `phase6-20261019`，没有公开下载地址。收到文件后，保留随包的清单、校验和与来源说明。

## 收到什么

维护者可以提供一个 `TheraSkillAgent-resources-phase6-20261019.zip` 交付包。包中同时附带 0.5.0 安装 wheel，可按包内 README 安装，无需 Node。先将这个外层 ZIP 解压到任意目录，例如 `~/theraskill-resources`。里面的两个 ZIP 才是应用可以安装的资源包：

| 文件 | 内容 | 压缩后 / 安装后 |
| --- | --- | --- |
| `knowledge-phase6-20261019.zip` | 2,196,286 条 Phase 6 知识发布记录；保留证据、策略与来源字段 | 约 699 MiB / 6.28 GiB |
| `simulation-phase6-20261019.zip` | 场景、病例、persona、注册表及现有语言覆盖文件 | 约 91 MiB / 669 MiB |
| `catalog.json`、`SHA256SUMS` | 本地安装目录、文件大小与 SHA-256 | 安装时自动核对 |
| `source-release.json`、`provenance.json` | 原发布清单和来源边界 | 保留原数据限制 |

包内不含模型权重、预构建向量索引、本机密钥、用户会话、业务数据库或生成草稿。原发布清单也描述了未随 ZIP 提供的旧索引；实际包内容以各 ZIP 的 `package.json` 为准。代码许可不能代替原数据集的使用和再分发条件。

安装资源本身约需 7 GiB；另外为 ZIP、临时解包、日志和索引预留空间。索引大小取决于模式、可检索记录与 embedding 维度，先运行 `index plan`。全量 hybrid 构建会产生真实 embedding 请求和服务费用。

## 安装和首次启用

先完成 [代码与模型配置](deployment.zh.md)，使用 0.5.0 或更新版本。进入已解压的资源目录，激活 Python 环境。以下命令显式指定工作目录，避免把资源装到另一份项目中：

```bash
cd ~/theraskill-resources
sha256sum -c SHA256SUMS

theraskill-agent --project ~/TheraSkillAgent knowledge install \
  --catalog ./catalog.json --package knowledge-phase6-20261019
theraskill-agent --project ~/TheraSkillAgent knowledge install \
  --catalog ./catalog.json --package simulation-phase6-20261019

theraskill-agent --project ~/TheraSkillAgent index plan \
  --knowledge knowledge-phase6-20261019
theraskill-agent --project ~/TheraSkillAgent index build \
  --knowledge knowledge-phase6-20261019 --id my-knowledge --mode lexical
theraskill-agent --project ~/TheraSkillAgent knowledge activate \
  --index my-knowledge --knowledge knowledge-phase6-20261019
theraskill-agent --project ~/TheraSkillAgent ui
```

源码项目在别处时，把所有 `~/TheraSkillAgent` 换成实际路径。Lexical 构建不需要模型或 FAISS；首次全量构建仍需读取数百万条记录。Hybrid 检索需要安装 `retrieval` extra，再将构建命令改为 `--mode hybrid`，并使用新索引名。中断后可用相同输入和配置加 `--resume` 继续。

首次安装模拟素材会在空工作目录中自动选中；已运行的应用需要重启。安装新版本不会覆盖已有资源，也不会自动替换活动知识索引。替换现有索引要完成效果对比和人工复核；上面的首次启用命令仅适用于尚无活动索引的设备。

也可在“知识与索引 → 安装资源 ZIP”分别上传两个内层 ZIP，等待安装任务完成。大型文件建议使用上述 CLI。不要把外层交付包上传到资源安装器。

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
  --output /path/to/new-resource-folder --version phase6-20261019
```

输出目录必须不存在。可把该目录内容再放入一个普通 ZIP 便于分发。上传后再填入真实下载地址；没有实际发布的 URL 不进入公共 catalog。资源的单独打包和索引安装格式见[知识与索引](knowledge_and_indexes.zh.md)。
