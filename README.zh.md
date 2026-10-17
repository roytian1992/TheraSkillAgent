# TheraSkillAgent

TheraSkillAgent 是一个面向治疗文本和知识流程的本地 skill-based agent
工作台。它把确定性的文本处理、可配置的 LLM/embedding 服务、知识抽取、
对话评估、情景训练、审核和知识索引放在同一个项目里。

项目强调人工可控：模型输出先保存为可追溯的产物或候选，经过质量检查和
明确审批后才进入正式知识发布。模型权重、患者对话、密钥和完整知识库均
不随源码仓库分发。

[English README](README.md) · [English deployment guide](docs/deployment.en.md) ·
[中文部署说明](docs/deployment.zh.md)

## 能做什么

| 工作流 | 能力 |
| --- | --- |
| 对话工作台 | 导入 TXT/JSON/CSV/Markdown 或粘贴文本，整理轮次和说话人，分析风险信号，执行多 skill review，打包保留证据链的结果。 |
| 知识抽取 | 检查数据源，按任务生成 schema，抽取带证据和来源的记录，验证候选并交给治理队列。 |
| 知识与检索 | 接收合格 JSONL，构建词法或 hybrid 索引，校验清单，检索、启用、回滚和打包资源。 |
| Skill 与任务 | 安装、扫描、优化和回滚本地 skill；通过统一任务契约执行文本、数据源、抽取和对话任务。 |
| 训练与审核 | 从同一个工作台启动支持、情景模拟、专家审核、贡献和抽取服务。 |

界面由项目自身提供：主工作台以及支持、模拟、专家页面共用一个 UI 进程，
需要时再启动本地领域服务。

## 快速开始

Python 需要 3.11+。从源码构建界面需要 Node 22.12+；安装 wheel 时不需要
Node。

```bash
git clone https://github.com/roytian1992/TheraSkillAgent.git
cd TheraSkillAgent

python -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# 源码安装需要构建浏览器界面
npm ci
npm run build

theraskill-agent project init .
cp configs/theraskill/providers.local.example.yaml \
  configs/theraskill/providers.local.yaml
chmod 600 configs/theraskill/providers.local.yaml
theraskill-agent provider check --kind llm
theraskill-agent provider check --kind embedding
theraskill-agent ui --start-services
```

浏览器打开 `http://127.0.0.1:5176/`。完整的 wheel 安装、端口、服务管理、
升级和故障检查见 [英文部署文档](docs/deployment.en.md) 或
[中文部署文档](docs/deployment.zh.md)。

## 模型配置

项目只支持一种 provider 配置结构：`active` 加两个直接配置块 `llm` 和
`embedding`。两类模型的模型名、URL 和密钥相互独立：

```yaml
active:
  llm: local_llm
  embedding: local_embedding

llm:
  model: 你的LLM模型名
  base_url: https://你的LLM服务/v1
  api_key: "LLM_API_KEY"

embedding:
  model: 你的Embedding模型名
  base_url: https://你的Embedding服务/v1
  endpoint: https://你的Embedding服务/v1/embeddings
  api_key: "EMBEDDING_API_KEY"
```

机器相关配置放到被 Git 忽略的
`configs/theraskill/providers.local.yaml`。共享的
`configs/theraskill/providers.yaml` 只提供安全的本地默认值。旧的
`profiles` 注册表会直接报错，不会被静默转换。

```bash
theraskill-agent provider list
theraskill-agent provider check --kind llm --live
theraskill-agent provider check --kind embedding --live
```

不加 `--live` 时不会访问网络；展示结果会隐藏密钥。

## 不启动模型也能试用知识流程

仓库包含 6 条中英双语 synthetic JSONL 记录，仅用于演示格式。词法索引
不调用模型，也不需要 FAISS：

```bash
theraskill-agent index plan --knowledge examples/knowledge/demo.jsonl
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl --id demo-v1
theraskill-agent index verify --index demo-v1
theraskill-agent index search --index demo-v1 --query uncertainty
theraskill-agent knowledge activate --index demo-v1
```

需要 hybrid 检索时安装可选依赖，系统会使用 `providers.yaml` 的
`embedding` 块：

```bash
python -m pip install -e '.[retrieval]'
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl \
  --id demo-hybrid --mode hybrid
```

中断后使用相同输入和直接模型配置加 `--resume`。更换 embedding 模型或
维度后应新建索引。字段映射、候选资格、索引清单、资源安装和回滚见
[知识、索引与资源包](docs/knowledge_and_indexes.zh.md)。

## 对话与任务示例

```bash
# 确定性操作只在本地执行
theraskill-agent text normalize dialogue.txt
theraskill-agent text sections dialogue.txt
theraskill-agent transcript inspect dialogue.txt
theraskill-agent transcript analyze dialogue.txt

# 先生成 review 计划，再显式执行模型 review
theraskill-agent transcript review dialogue.txt --mode plan \
  --skill transcript-quality-reviewer
theraskill-agent transcript review dialogue.txt --mode model \
  --skill transcript-quality-reviewer --max-turns 200
```

CLI、界面和 skill/tool 调用共用相同的任务和结果契约，
审核和资源启用都保留明确的人工操作节点。

## 仓库与数据边界

源码仓库包含 agent 内核、领域服务、内置 skills、提示词、schema、索引代码、
界面源码、配置模板和小型 synthetic 示例。完整知识库、预构建索引、persona、
情景素材、模型权重、原始研究数据、密钥、会话、日志和运行状态均作为独立
资源管理。

`releases/catalog.json` 记录可下载资源的发布状态。目前完整知识和模拟素材
没有公开下载地址；示例数据不代表临床指南或经过验证的知识库。

参考文档：

- [知识格式、索引和资源包](docs/knowledge_and_indexes.zh.md)
- [英文部署文档](docs/deployment.en.md)
- [中文部署文档](docs/deployment.zh.md)
- [源码发布边界](docs/source_release.zh.md)
- [数据集抽取与产出说明](extraction/docs/datasets_and_outputs.zh.md)

## 开发与验证

```bash
python -m pip install -e '.[dev,retrieval]'
python -m pytest -q -o addopts='' \
  tests/test_standalone_kernel.py tests/test_theraskill*.py \
  tests/smoke_theraskill_install.py tests/test_theraskill_indexing.py \
  --ignore=tests/test_theraskill_agent_regression.py
npm run build
python -m build
```

基础安装不包含重量较大的模型和检索框架；只有需要 FAISS hybrid 索引时才
安装 `retrieval` extra。软件采用 MIT 许可证，数据、模型和患者对话的授权
需要分别确认。
