# 20261029 离线版安装完成后怎么启动

[English](offline_after_install_20261029.en.md) · [离线部署总说明](deployment_offline.zh.md)

本页适用于已经运行过 `TheraSkillAgent-app-offline-ubuntu22.04-x86_64-20261029.zip` 中的 `install_offline.sh`，并把程序装在默认位置 `~/TheraSkillAgent` 的 Ubuntu 22.04 x86_64 服务器。**无需为完成以下步骤重新安装 20261102 程序包。** `~/theraskill-app` 是旧 ZIP 的解压目录；真正的程序和项目数据在 `~/TheraSkillAgent`。

## 1. 确认安装完成

在服务器终端执行。下面的环境设置只对当前终端有效，避免旧安装器遇到的用户 Python 包干扰；它不会改动系统 Python。

```bash
APP_ROOT="$HOME/TheraSkillAgent"
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export LD_LIBRARY_PATH="$APP_ROOT/.runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

test -f "$APP_ROOT/.runtime/.theraskill-ready"
test -x "$APP_ROOT/.runtime/bin/theraskill-agent"
"$APP_ROOT/.runtime/bin/python" -m pip --isolated check
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" status --json
```

`pip check` 应显示 `No broken requirements found.`。若 `test` 失败或 `pip check` 报缺包，说明当初的安装没有完成；先不要删除 `~/TheraSkillAgent` 或其中的数据。保留旧 ZIP 的解压目录，从下面的[缺包修复](#旧安装缺包时)继续。

## 2. 启动网页

在**同一个终端**继续执行：

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" ui \
  --host 0.0.0.0 --port 5176
```

浏览器打开 `http://服务器地址:5176/`。`curl -f http://127.0.0.1:5176/health` 可在服务器本机检查网页服务。进程在前台运行，关闭该终端或按 Ctrl-C 会停止服务；5176 已占用时把命令和网址中的端口一起改成 5177。请只允许可信网络访问该端口。

**旧包的路径区别：**20261029 安装器没有把 `start_offline.sh` 复制到 `~/TheraSkillAgent`。如果 `~/theraskill-app` 仍在，也可以在清理上述 Python 环境变量后运行 `~/theraskill-app/start_offline.sh --target "$APP_ROOT" --host 0.0.0.0 --port 5176`。本页的直接启动命令不依赖那个解压目录；不要运行不存在的 `~/TheraSkillAgent/start_offline.sh`。

## 3. 连接服务器已有模型

打开首页的「模型与连接」，分别填写服务器已有 LLM 和 Embedding 服务的**实际模型名、Base URL、API key**；Embedding 还要填写 `/v1/embeddings` endpoint。同机部署时 URL 通常是 `http://127.0.0.1:模型服务端口/v1`，以已有 vLLM 的实际监听地址为准。先点「测试当前配置」，两项均为 `ok` 后保存；也可点「测试通过后保存」。程序不会启动或安装 vLLM。

若更习惯编辑文件，可在另一个服务器终端执行（已有 `providers.local.yaml` 时不要覆盖）：

```bash
APP_ROOT="$HOME/TheraSkillAgent"
test -f "$APP_ROOT/configs/theraskill/providers.local.yaml" || \
  cp "$APP_ROOT/configs/theraskill/providers.local.example.yaml" \
     "$APP_ROOT/configs/theraskill/providers.local.yaml"
chmod 600 "$APP_ROOT/configs/theraskill/providers.local.yaml"
vi "$APP_ROOT/configs/theraskill/providers.local.yaml"
```

保存后在已设置上述环境变量的终端验证：

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" provider check --kind llm --live
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" provider check --kind embedding --live
```

`providers.local.yaml` 是本机配置，LLM 与 Embedding 各自有模型名、URL 和密钥。**不要把密钥写进 Git 或资源 ZIP。** 浏览器能打开只说明应用已启动，不代表模型服务已连接。

## 4. 按需安装知识和场景

将单独交付的 `knowledge-phase7-20261028.zip`（知识）和 `simulation-phase7-20261028.zip`（场景）分别传到离线服务器。它们不在 Git 仓库内，**不需要先解压资源 ZIP**。把命令中的 `/path/to/...zip` 换成服务器上的实际路径；可只安装需要的那一份。

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" knowledge install \
  --archive /path/to/knowledge-phase7-20261028.zip
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" simulation install \
  --archive /path/to/simulation-phase7-20261028.zip
```

**安装知识还不等于可以检索。** 先预估索引所需空间，再构建和首次启用。词法索引不调用 Embedding；全量知识约 220 万条，`index plan` 对这份资源给出的新增索引空间粗估约 **37.7 GiB**，此外还要存放约 **6.3 GiB** 的解包知识、临时文件和项目数据。请检查**当前用户配额**，不能只看 `df -h` 的整盘剩余空间。

```bash
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index plan \
  --knowledge knowledge-phase7-20261028 --mode lexical
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index build \
  --knowledge knowledge-phase7-20261028 --id knowledge-v1 --mode lexical
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index verify \
  --index knowledge-v1
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" knowledge activate \
  --index knowledge-v1 --knowledge knowledge-phase7-20261028
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" index search \
  --index knowledge-v1 --query '焦虑 情绪低落' --skill emotional-support --limit 3
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" knowledge list
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" simulation list
```

也可在「知识与索引」选择已安装知识、快速搜索模式并点击「构建可检索版本」，完成后依次点击「验证」「启用」「查找测试」。20261029 版网页的「查看进度详情」显示处理条数而非进度条；界面长时间显示「进行中」不一定是卡死。若失败提示 `disk I/O error`，先检查索引目录所在磁盘及用户配额；空间足够后，用同一版本 ID 选择「从已保存进度继续」。词法阶段会从头重建，不能直接复用半成品。不要启用未完成的索引。

**实测验收（2026-11-03）：**使用 20261029 版程序和上述知识 ZIP，在空间足够的隔离目录中通过网页完成全量构建、验证、启用和中英文检索。结果为 2,196,286 条总记录、1,808,415 条可检索记录，最终索引文件约 15.2 GiB；业务检索也能返回带原文的知识。`index plan` 的 37.7 GiB 是保守预估，仍应按其预留容量并给临时文件留余量。空间或用户配额不足时，本次在约 59.3 万条处复现了 `disk I/O error`。

需要语义检索时，在**第一次构建前**将 `--mode lexical` 改为 `--mode hybrid`，并先确认 Embedding 连接通过、安装时没有用 `--no-retrieval`。若已有活动索引，切换到新索引需要效果比较报告，不能直接重复上述首次启用命令。知识和场景安装完成后，停止并重新启动网页进程；在首页「使用准备」检查状态，再分别试一次助手检索和模拟训练。

## 旧安装缺包时

只有第 1 步的 `pip check` 报缺包、且仍保留 `~/theraskill-app` 解压目录时，才用旧包内的 wheelhouse 补齐依赖。**无需删除项目或重新解压运行时。**

```bash
"$APP_ROOT/.runtime/bin/python" -m pip --isolated install --no-index --no-cache-dir \
  --find-links "$HOME/theraskill-app/wheelhouse" \
  "$HOME/theraskill-app/packages/theraskill_agent-0.5.0-py3-none-any.whl[retrieval]"
"$APP_ROOT/.runtime/bin/python" -m pip --isolated check
"$APP_ROOT/.runtime/bin/theraskill-agent" --project "$APP_ROOT" project init "$APP_ROOT"
```

这些命令沿用当前终端第 1 步设置的隔离环境。若旧包是用 `--no-retrieval` 安装，且只需要词法检索，可去掉 wheel 路径后的 `[retrieval]`。若解压目录已删除，先从手头的**同一份 20261029 ZIP**重新解压出 `wheelhouse/` 和 `packages/`，不要覆盖已安装目录。

## Git 文档与程序版本

Git `main` 的[离线部署说明](deployment_offline.zh.md)已改为新设备使用 20261102 程序包，因此其安装命令不适用于“已经装好 20261029”的情况；本页专门记录旧版后续步骤。阅读或拉取新的 Git 文档**不会更新**离线服务器里已安装的 wheel。后续新增的助手「仅模型」、诊断信息和索引进度条等界面功能，也不会因更新文档而出现在 20261029 安装中；需要另行升级程序时再使用新的离线程序包。知识和场景 ZIP 与程序包仍分别安装。
