# 部署 TheraSkillAgent

## 软件

Linux / Python 3.11+。本地开发源码需要 Node 22.12+ 来构建四个界面；部署已构建 wheel 不需要 Node。基础安装只需要 7 个直接 Python 依赖。索引进程锁使用 Linux 的 flock。

```bash
python -m venv .venv
source .venv/bin/activate
pip install /path/to/theraskill_agent-0.4.0-py3-none-any.whl
theraskill-agent project init /path/to/TheraSkillAgent
export THERASKILL_AGENT_ROOT=/path/to/TheraSkillAgent
theraskill-agent status
theraskill-agent run extraction.plan --goal 'Extract response strategies grounded in source quotations'
```

源码安装见根目录 README。需要 FAISS 向量构建与查询时，再安装同版本软件的 `retrieval` extra；模型权重和推理框架不在基础依赖中。

## 模型配置

共享的 provider 模板在 `configs/theraskill/providers.yaml`，本机凭据和覆盖项放在自动读取的 `configs/theraskill/providers.local.yaml`。这个本地文件被 Git 忽略，也不会进入 wheel 或源码发布物。LLM 和 embedding 使用独立 profile，因此可以分别填写 URL、模型名和 API key。

首次配置可复制模板：

```bash
cp configs/theraskill/providers.local.example.yaml configs/theraskill/providers.local.yaml
chmod 600 configs/theraskill/providers.local.yaml
```

编辑本地文件中的两个 `api_key`、模型名和 URL，并取消 `active` 注释让这两个本地块成为当前 provider：

```yaml
active:
  llm: local_llm
  embedding: local_embedding
llm:
  model: 你的LLM模型名
  base_url: https://你的LLM服务/v1
  api_key: "LLM 的密钥"
embedding:
  model: 你的Embedding模型名
  base_url: https://你的Embedding服务/v1
  endpoint: https://你的Embedding服务/v1/embeddings
  api_key: "Embedding 的密钥"
```

`api_key` 可以不同；如果两个服务共用一个密钥，也分别填写同一值。环境变量仍可作为旧部署的回退方式，但不再是必需项。

```bash
export THERASKILL_LLM_PROFILE=local_qwen
export THERASKILL_EMBEDDING_PROFILE=local_bge
theraskill-agent provider list
theraskill-agent provider check --kind llm
theraskill-agent provider check --kind embedding
```

加 `--live` 会发起一次真实请求。默认配置包含本地 Qwen、BGE 和远程兼容 API 示例，端点本身需另外准备。更换生成 LLM 不需重建已有索引；更换 embedding 应使用对应索引或重建。

## 启动

```bash
theraskill-agent ui --start-services
```

浏览器打开 `http://127.0.0.1:5176/`。UI 与资源管理共用端口；业务服务默认使用 8876、8878、8879、8881、8891、8892。端口可通过项目的 `THERASKILL_<SERVICE>_PORT` 环境变量配置。UI 的 `--port` 只改变 UI 端口。默认监听本机，供本地用户使用；跨机器共享部署应另行设置访问控制。

```bash
theraskill-agent service status
theraskill-agent service stop
```

`service stop` 管理领域服务；前台 UI 用 Ctrl-C 退出。不要在旧进程仍运行时认为源码改动已生效，更新后重启相应进程。

## 数据

`project init` 会创建配置和小型自编知识示例。完整知识、索引与模拟素材需独立安装，参见 [知识和索引](knowledge_and_indexes.zh.md)。首次体验可直接在界面导入 `examples/knowledge/demo.jsonl`，选择词法构建，无需启动任何模型。没有完整素材时，不应把界面可启动理解为所有正式训练课程均已安装。

保留整个可写工作目录可备份配置、已安装资源、skills/local、state 和用户业务数据。 `.env`、会话和运行数据不进入公开源码或下载包。升级 wheel 不会清空工作目录。
