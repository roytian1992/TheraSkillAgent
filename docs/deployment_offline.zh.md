# 离线安装 TheraSkillAgent

**已经安装了 20261029 版？**无需重装。直接按[旧版安装后的启动与资源配置步骤](offline_after_install_20261029.zh.md)继续；以下安装命令用于新设备。

这份应用离线 ZIP 面向与制作机器相同的 **Ubuntu 22.04 x86_64** 服务器。服务器
不需要联网，也不需要预先安装 Python、Conda、Node 或项目的 pip 包。安装器不用
root 权限。系统需有 `bash`、`tar`、`unzip` 和 Ubuntu 22.04 自带的 glibc。

应用包包含项目 wheel、构建好的网页、Python 3.12 运行时、它所需的 Linux 共享
库（包括 OpenMP/libgomp），以及全部 Python 依赖 wheel。它不包含知识、模拟场景、
模型权重或 vLLM 服务。知识和场景是另外两份独立的 ZIP，想用哪一类就安装哪一类。

## 安装应用

```bash
unzip TheraSkillAgent-app-offline-ubuntu22.04-x86_64-20261102.zip -d ~/theraskill-app
cd ~/theraskill-app
sha256sum -c SHA256SUMS
./install_offline.sh
```

安装器把自带 Python 解包到 `~/TheraSkillAgent/.runtime`，运行 `conda-unpack`，
并使用隔离的 `pip --no-index` 从包内 wheelhouse 安装应用和检索依赖。它不读取
目标机的用户 Python 包，不访问网络，也不会更改系统 Python、安装知识或场景。
只使用词法检索时可加 `--no-retrieval`，跳过 NumPy/FAISS。安装后执行：

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent status --json
~/TheraSkillAgent/start_offline.sh --host 0.0.0.0 --port 5176
```

浏览器打开 `http://服务器地址:5176/`。这个启动脚本已复制到安装目录，解压目录可以
在确认安装成功后移走。前台按 Ctrl-C 停止。`/health` 应返回 `status: ok`；只有网页
正常启动还不能证明模型或知识已就绪。服务器允许其他设备访问 5176 时，应把端口限制
在可信网络内。

## 在界面连接已有 vLLM

首次打开首页，在 **模型与连接** 中分别填写 LLM 和 Embedding 的模型名、Base URL、
密钥，以及 Embedding endpoint。点击 **测试当前配置**，两项都显示 `ok` 后点击
**保存配置**，或直接点 **测试通过后保存**。启动工作台不要求模型已连接；对话、生成、
语义索引等任务则需要对应模型服务。已有 vLLM 由服务器自行管理，安装器不会启动它。

不使用网页时，也可编辑安装目录中的本机文件：

```bash
cp ~/TheraSkillAgent/configs/theraskill/providers.local.example.yaml \
   ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
chmod 600 ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
vi ~/TheraSkillAgent/configs/theraskill/providers.local.yaml
```

填写实际模型名和 OpenAI 兼容端点；LLM、embedding 可以是不同服务：

```yaml
active:
  llm: local_llm
  embedding: local_embedding
llm:
  model: 你的LLM模型名
  base_url: http://127.0.0.1:8000/v1
  api_key: ""
embedding:
  model: 你的Embedding模型名
  base_url: http://127.0.0.1:8081/v1
  endpoint: http://127.0.0.1:8081/v1/embeddings
  api_key: ""
```

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent provider check --kind llm --live
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent provider check --kind embedding --live
```

如果已有服务开启了 API key 验证，两个 `api_key` 必须填写各自服务实际使用的值；
项目中的模板密钥只是占位符。两个检查都为 `ok` 才表示模型可用。应用只占用一个
网页/API 端口，模型端口由已有 vLLM 提供。

图文操作指南随程序安装到 `~/TheraSkillAgent/docs/user_guide.zh.md` 和
`user_guide.en.md`，配图也在同一个 `docs/images/user-guide/` 目录。

## 按需安装知识或场景

这一步按使用范围选做；没有完整知识和场景时，工作台及模型对话仍可启动，但正式
检索和模拟训练不会就绪。另行取得对应 ZIP。两份资源可以单独交付，不用放在同一目录，也不需
`catalog.json`、`provenance.json` 或外层资源包。ZIP 内的 `package.json` 会由安装器
自动校验。

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  knowledge install --archive /path/to/knowledge-phase7-20261028.zip
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  simulation install --archive /path/to/simulation-phase7-20261028.zip
```

安装知识后还需构建并启用索引；安装程序不会自行发起海量 embedding 请求。先运行
`index plan` 查看规模，再从工作台构建，或用 CLI。下面的词法模式不请求 embedding，
但全量知识仍可能耗时较久；要使用语义检索，改用 `--mode hybrid` 并确保 BGE 等
Embedding 服务已通过连接测试：

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  index plan --knowledge knowledge-phase7-20261028
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  index build --knowledge knowledge-phase7-20261028 --id knowledge-v1 --mode lexical
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  knowledge activate --index knowledge-v1 --knowledge knowledge-phase7-20261028
```

资源安装或索引启用后，重启工作台，并在首页 **使用准备** 检查模型、活动索引、
模拟场景和技能状态。也可以运行 `knowledge list`（同时列出知识与索引）和
`simulation list` 查看安装结果。只有需要的资源、模型检查和对应业务试跑均通过，才算
完成该功能的部署。

资源解包后约占 7.4 GiB；另外为索引、临时文件和工作数据预留空间。更多资源格式
见[资源安装说明](resource_installation.zh.md)。

## 维护者重新制作程序包

以下步骤在**可联网的同版 Ubuntu 22.04 构建机**上运行；离线目标机不需要
Conda 或这些构建命令。先准备可迁移 Python，再生成程序 ZIP：

```bash
conda create -y -p artifacts/_build/python-env python=3.12.7 pip=24.2 libgomp
conda-pack -p artifacts/_build/python-env \
  -o artifacts/_build/theraskill-python-3.12.7-linux-x86_64.tar.gz
python scripts/TheraSkill/build_offline_bundle.py \
  --output artifacts/TheraSkillAgent-app-offline-ubuntu22.04-x86_64-NEW.zip
```

构建机还需有 `python -m build` 和 `conda-pack`；依赖 wheel 下载发生在构建机。
目标机上的安装器始终使用包内文件和 `pip --no-index`。
