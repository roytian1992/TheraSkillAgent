# 离线安装 TheraSkillAgent

这份应用离线 ZIP 面向与制作机器相同的 **Ubuntu 22.04 x86_64** 服务器。服务器
不需要联网，也不需要预先安装 Python、Conda、Node 或项目的 pip 包。安装器不用
root 权限。系统需有 `bash`、`tar`、`unzip` 和 Ubuntu 22.04 自带的 glibc。

应用包包含项目 wheel、构建好的网页、Python 3.12 运行时、它所需的 Linux 共享
库（包括 OpenMP/libgomp），以及全部 Python 依赖 wheel。它不包含知识、模拟场景、
模型权重或 vLLM 服务。知识和场景是另外两份独立的 ZIP，想用哪一类就安装哪一类。

## 安装应用

```bash
unzip TheraSkillAgent-app-offline-ubuntu22.04-x86_64-20261029.zip -d ~/theraskill-app
cd ~/theraskill-app
sha256sum -c SHA256SUMS
./install_offline.sh
```

安装器把自带 Python 解包到 `~/TheraSkillAgent/.runtime`，运行 `conda-unpack`，
并使用 `pip --no-index` 从包内 wheelhouse 安装应用和检索依赖。不会访问网络，
不会安装或更改系统 Python，也不会安装知识和场景。只使用词法检索时可加
`--no-retrieval`，跳过 NumPy/FAISS。安装后可直接运行：

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent status --json
```

## 连接已有 vLLM

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
  base_url: http://127.0.0.1:8001/v1
  endpoint: http://127.0.0.1:8001/v1/embeddings
  api_key: ""
```

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent provider check --kind llm --live
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent provider check --kind embedding --live
./start_offline.sh --host 0.0.0.0 --port 5176
```

浏览器访问 `http://服务器地址:5176/`。应用只占用一个端口；已有 vLLM 进程照常
运行，不由安装器管理。

图文操作指南随程序安装到 `~/TheraSkillAgent/docs/user_guide.zh.md` 和
`user_guide.en.md`，配图也在同一个 `docs/images/user-guide/` 目录。

## 按需安装知识或场景

这一步需要另行取得对应 ZIP。两份资源可以单独交付，不用放在同一目录，也不需
`catalog.json`、`provenance.json` 或外层资源包。ZIP 内的 `package.json` 会由安装器
自动校验。

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  knowledge install --archive /path/to/knowledge-phase7-20261028.zip
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  simulation install --archive /path/to/simulation-phase7-20261028.zip
```

安装知识后，还需基于本机 embedding 服务构建索引；安装程序不会自行发起海量
embedding 请求。先运行 `index plan` 查看规模，再从工作台构建，或用 CLI：

```bash
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  index plan --knowledge knowledge-phase7-20261028
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  index build --knowledge knowledge-phase7-20261028 --id knowledge-v1 --mode lexical
~/TheraSkillAgent/.runtime/bin/theraskill-agent --project ~/TheraSkillAgent \
  knowledge activate --index knowledge-v1 --knowledge knowledge-phase7-20261028
```

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
