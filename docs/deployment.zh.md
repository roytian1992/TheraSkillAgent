# 部署 TheraSkillAgent

## 软件

Linux / Python 3.11+。本地开发源码需要 Node 22.12+ 来构建四个界面；部署已构建 wheel 不需要 Node。基础安装只需要 7 个直接 Python 依赖。索引进程锁使用 Linux 的 flock。

```bash
python -m venv .venv
source .venv/bin/activate
pip install /path/to/theraskill_agent-0.5.0-py3-none-any.whl
theraskill-agent project init ~/TheraSkillAgent
theraskill-agent status
theraskill-agent run extraction.plan --goal 'Extract response strategies grounded in source quotations'
```

wheel 默认使用 `~/TheraSkillAgent` 保存配置和数据；源码安装默认使用项目目录。自定义目录时使用 `theraskill-agent --project /你的目录 ui`，无需设置环境变量。

源码安装见根目录 README。需要 FAISS 向量构建与查询时，再安装同版本软件的 `retrieval` extra；模型权重和推理框架不在基础依赖中。

## 模型配置

共享的 provider 模板在 `configs/theraskill/providers.yaml`，本机凭据和覆盖项放在自动读取的 `configs/theraskill/providers.local.yaml`。这个本地文件被 Git 忽略，也不会进入 wheel 或源码发布物。LLM 和 embedding 是两个直接配置块，可以分别填写 URL、模型名和 API key。

进入工作目录（wheel 安装为 `cd ~/TheraSkillAgent`），首次配置可复制模板：

```bash
cp configs/theraskill/providers.local.example.yaml configs/theraskill/providers.local.yaml
chmod 600 configs/theraskill/providers.local.yaml
```

编辑本地文件中的两个 `api_key`、模型名和 URL；`active` 直接指定两个服务块：

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

`api_key` 可以不同；如果两个服务共用一个密钥，也分别填写同一值。

```bash
theraskill-agent provider list
theraskill-agent provider check --kind llm
theraskill-agent provider check --kind embedding
```

加 `--live` 会发起一次真实请求。默认配置只提供本地服务地址示例，端点本身需另外准备。更换生成 LLM 不需重建已有索引；更换 embedding 应使用对应索引或重建。

正常部署只需填写上述模型名、URL 和密钥。输出截断与空回复由公共模型层自动进行有上限的恢复；高级参数见下方故障排查。

## 启动

```bash
theraskill-agent ui
```

浏览器打开 `http://127.0.0.1:5176/`。整个应用默认只有 **一个进程、一个监听端口**。情景模拟、支持助手、专家工作台、知识治理、知识抽取和文本处理是进程内模块，随主程序一起启动和关闭；无需分别启动六个服务。

```bash
# 修改整个应用的网页/API 入口端口
theraskill-agent ui --port 8080
# 检查运行中的应用与各模块
curl http://127.0.0.1:5176/health
curl http://127.0.0.1:5176/api/overview
```

前台用 Ctrl-C 退出。模型服务由本地模型进程或远程 API 提供，LLM 和 embedding 的地址各自配置；它们不属于上述应用模块。

从旧版升级：先在旧应用中确认没有正在执行的任务，退出旧 UI，再运行一次 `theraskill-agent service stop` 停止旧版独立服务，之后使用 `theraskill-agent ui`。旧端口 8876、8878、8879、8881、8891、8892 不再是默认部署依赖。`--start-services` 暂时兼容已有启动脚本，但效果等同于 `ui`，不会再拉起六个子进程。独立 `service` 命令仅用于显式的分离调试。

默认监听本机；跨机器共享时需要配置相应的访问控制。升级代码或修改模型配置后，重启这个应用进程即可。

源码更新并重新构建前端后，正在运行的 Python 进程仍可能保留旧接口。退出旧的 `theraskill-agent ui`（启动终端中按 Ctrl+C），再用原来的端口参数重新启动，最后刷新浏览器。若课程页提示微练习接口尚不可用，应先完成这个重启步骤；只刷新浏览器不能加载新的后端代码。

## 数据

`project init` 会创建配置和小型自编知识示例。完整知识、索引与模拟素材需独立安装，参见 [知识和索引](knowledge_and_indexes.zh.md)。首次体验可直接在界面导入 `examples/knowledge/demo.jsonl`，选择词法构建，无需启动任何模型。没有完整素材时，不应把界面可启动理解为所有正式训练课程均已安装。

保留整个可写工作目录可备份配置、已安装资源、skills/local、state 和用户业务数据。 `.env`、会话和运行数据不进入公开源码或下载包。升级 wheel 不会清空工作目录。

## 检索性能与诊断

启动方式和配置保持上述用法。高级检索会并行执行独立召回通道，自动复用与索引版本绑定的元数据清单及范围缓存。新建索引自带 `metadata_inventory.json`；旧索引第一次使用时自动生成运行缓存，这次准备可能较慢，后续进程可直接复用。缓存不保存模型规划答案，正常请求仍调用配置的 LLM。

混合检索的可选 `retrieval` extra 使用 `faiss-cpu>=1.13.2`，支持只读映射本项目的 Flat 向量索引，并只对符合过滤条件的向量做近邻选择。基础安装依赖数不变，无需增加进程、端口或手工缓存配置。

遇到慢请求时，查看专家/支持任务 provenance 中的 `retrieval_summary.timings`、`planner_timings` 和 `warnings`；对应 bundle 还包含 `planner_trace.inventory_cache`。规划、向量和词法耗时会分别记录，并行阶段不能相加当作总耗时。宽泛中文查询仍可能触及词法通道预算，此时会明确报告并保留已完成通道；模型或网络延迟也会影响总时间。真实测量和复现命令见[检索优化验收](retrieval_optimization_20261018.zh.md)。

## 业务验证与任务恢复

健康检查只证明服务可连接。部署后还应在界面运行一次对话分析与 bundle 下载、知识抽取试跑、有效训练及复盘、专家回应审核和课程批次。首页任务中心、抽取历史、专家任务历史与课程批次历史可以重新打开任务；批次和抽取失败可重试未完成项，训练状态更新与复盘也有单独的重试入口。无有效双边对话或状态更新失败时，训练标记为不可评估，不能提交训练评分。

## 模型输出故障排查

公共模型层会对输出截断或空回复最多恢复两次。截断时重新生成完整结果，并逐步增加输出预算，自动增长上限为 32768 tokens；如果原配置更大则保留该值，不再自动增加。不会拼接不完整 JSON，也不会把截断结果交给业务执行。临时网络故障与限流使用独立的有限重试；认证或参数错误会直接报告。

通常无需修改参数。如果服务本身的输出上限或推理消耗导致恢复后仍然失败，可在已有 `llm` 块增加可选的 `max_output_tokens`，以及服务明确支持的 `thinking` / `reasoning_effort`。应用不会根据模型名称判断行为，也不会自动关闭推理。字段与非空回复检查继续保留，支持助手的结构错误最多重新生成一次。

```yaml
llm:
  # 与已有 model/base_url/api_key 一起设置，仅在需要时添加。
  max_output_tokens: 8192
  retry_attempts: 2
```

修改后重启 `theraskill-agent ui`。失败任务可从界面恢复；业务记录和之前的输出会保留。

## 交互工作流与升级门禁

新功能使用方法见[工作流说明](workflows.zh.md)：持久技能执行、抽取至索引的治理、效果基线及课程练习均在现有工作台操作，不新增端口或运行依赖。备份应同时包含 `data/theraskill/tasks`、`data/theraskill/evaluations`、课程/会话数据库和 `skills/local`。

空设备可首次初始化索引。替换已有活动索引或同名技能需要已复核的效果对比报告；CLI 的 `knowledge activate` 和 `skill install --overwrite` 使用 `--behavior-report REPORT_ID`。专家页面的知识预发布正式发布也需要该门禁。被评估产物、模型设置或评价器变化后，旧报告不能继续放行；保留历史并重新评价。

## 部署简化验收

单进程合并、公共模型恢复、独立安装检查及真实浏览器结果见[部署简化验收记录](deployment_simplification_20261018.zh.md)。


## 在界面配置和测试模型

启动 `theraskill-agent ui` 后，顶部导航的 **模型与连接** 是模型配置入口。LLM 和 embedding 分开填写模型名、Base URL、embedding endpoint 和 API key；也可以填写超时、输出预算和推理强度等可选参数。

**测试当前配置** 会直接调用两个服务，但不会写文件；**保存配置** 会把设置写入当前项目的 `configs/theraskill/providers.local.yaml`；**测试通过后保存** 只有两个连接都通过才会写入。API key 在读取结果、页面展示、测试结果和日志中始终被遮罩；API key 留空表示保留原来的本机密钥。

首页的 **Workspace** 是主要功能入口。**任务中心** 是 durable task 的统一记录和恢复入口，包含已完成、运行中、等待输入/确认、失败和可重试的技能任务、知识抽取、专家审核及批量场景任务；它不是普通聊天历史。

## 完整资源 ZIP 与首页检查

完整知识和情景素材的安装见[资源安装说明](resource_installation.zh.md)。资源包使用 ZIP64、逐文件大小与 SHA-256 校验；界面可上传内层资源 ZIP，也可使用包旁的本地 catalog 安装。首次安装情景素材后重启应用；现有索引不会被自动替换。

首页“使用准备”显示配置、模型真实连接测试、活动索引、素材和技能依赖状态。配置检查不请求模型；点击连接测试才发起真实调用。内置 12 个技能均声明运行工具要求；第三方技能缺少工具或依赖时会在调用模型前报告。

`configs/theraskill/text.yaml` 配置抽取分段（默认 6000 字符、重叠 300）及 review 窗口。原文拼写和位置保留，规划样本覆盖文件首尾；默认 review 覆盖全部轮次，显式 `--max-turns` 才代表抽样。长 Agent 任务超过上下文预算时暂停并允许调整预算，不静默丢弃历史。

## 课程反馈

课程及微练习反馈使用现有 LLM 配置和当前知识索引，与应用同进程运行，不新增服务端口或依赖包。教师与学员操作流程及备份位置见[课程与学习指南](learning.zh.md)。
