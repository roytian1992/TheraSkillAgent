# TheraSkillAgent

TheraSkillAgent 将知识抽取、对话整理与分析、情景训练、支持对话、审核和技能管理放在一个项目中。LLM 与 embedding 分别配置，既支持本地服务，也支持兼容 API 服务。

## 安装与使用

Python 3.11+；源码构建界面需要 Node 22.12+。安装已构建的 wheel 不需要 Node。

```bash
git clone https://github.com/roytian1992/TheraSkillAgent.git
cd TheraSkillAgent
python -m venv .venv
source .venv/bin/activate
pip install -e .
npm ci
npm run build
theraskill-agent project init .
theraskill-agent ui --start-services
```

浏览器打开 `http://127.0.0.1:5176/`。共享模型模板在 `configs/theraskill/providers.yaml`，本机可以复制 `configs/theraskill/providers.local.example.yaml` 为 `configs/theraskill/providers.local.yaml`，分别填写 LLM 和 embedding 的 URL、模型名与 `api_key`。本地文件会自动加载且被 Git 忽略；默认的本地地址需要你自行启动模型服务。

## 先用小样例构建索引

```bash
theraskill-agent index plan --knowledge examples/knowledge/demo.jsonl
theraskill-agent index build --knowledge examples/knowledge/demo.jsonl --id demo-v1
theraskill-agent index verify --index demo-v1
theraskill-agent index search --index demo-v1 --query uncertainty
theraskill-agent knowledge activate --index demo-v1
```

以上不调用模型。工作台的「知识与索引」也能完成上传、计划、构建、检索测试、启用和回滚。样例是 3 个类别、每类中英文各 1 条自编记录，仅演示格式，不代表正式临床知识。

需要混合检索时安装 `pip install -e '.[retrieval]'`，构建时增加 `--mode hybrid --embedding-profile local_bge`，或者填写自己的 embedding 配置名。更换 embedding 模型需要重建或选择对应索引。未完成构建可用相同输入、名称和配置加 `--resume` 继续。

## 发布边界

源码仓库包含业务代码、12 个技能、提示词、界面、抽取与完整索引构建代码、配置模板和小样例。完整知识、预构建索引、persona 和场景单独发布。模型权重、原始数据、密钥、会话和日志不随源码分发。

完整知识与模拟素材目前尚未公开上传，下载目录 `releases/catalog.json` 明确标记为不可用；现阶段可使用小样例或自己的合格知识 JSONL。原有本地数据不会因安装代码而被替换。

- [知识格式、构建索引与独立资源包](docs/knowledge_and_indexes.zh.md)
- [部署说明](docs/deployment.zh.md)
- [源码发布范围、验收与待完成项](docs/source_release.zh.md)
- [各数据集如何抽取、产出什么](extraction/docs/datasets_and_outputs.zh.md)

代码使用 MIT 许可证并保留上游版权说明。数据和模型的许可独立于代码许可。
