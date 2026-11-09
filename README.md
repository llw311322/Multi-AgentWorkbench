# agentflow

按 `multi-agent-design.md` 实现的多 Agent 协作系统。**零第三方依赖**，Python 3.9+ 直接跑。

```
需求 ──► 拆分器(LLM) ──► 任务图 DAG ──► [G1 人工确认]
                              │
                    ┌─────────┴─────────┐
                    │   编排循环         │
                    │  ① 依赖就绪→ready  │
                    │  ② 路由派发        │
                    │  ③ 执行(可并行)    │
                    │  ④ 契约校验        │
                    │  ⑤ 独立评审        │
                    │  ⑥ [G2 发布确认]   │
                    └─────────┬─────────┘
                              ▼
                     汇总器 ──► [G3 定稿确认] ──► final.md
```

## 跑起来

### 方式一：Web 控制台（推荐，闸门在浏览器上点）

```bash
python -m agentflow.server --mock --open     # 假数据，自动开浏览器
python -m agentflow.server --open            # 真实模型（需 LLM_API_KEY）
```

打开 `http://127.0.0.1:8760/`，四个环节各有自己的页面：

| 环节 | 页面 | 里头有什么 |
|---|---|---|
| **拆分** | 任务拆分 | 需求输入 → DAG 画布（点节点可改标题/依赖/验收/并行组）→ G1 决策 |
| **执行** | 执行看板 | pending/ready/running/review/done/blocked 六列看板 + 路由映射表 + 续跑 |
| **监控** | 协作监控（原工作台） | 真实 Run 自动注入，泳道时间轴与消息总线直接复用原视图 |
| **反馈** | 反馈闭环 | 待决策闸门卡片（G1/G2/G3 全文预览+决策按钮）、逐节点验收与评审、事件流、模型调用轨迹 |

原工作台（总览 / 任务编排 / 任务图 / 协作监控 / AI 应用 / 模型中心 / 知识库等）**全部保留**，
断开后端时回落到演示数据。前端在 `web/`，纯静态：`index.html + styles.css + app.js + data.js + pipeline.js + pipeline.css`。

### 首次拉取后的准备

```bash
pip install -r requirements.txt                     # 只有内嵌应用需要：Flask、psycopg（知识库）
cp apps/p02/.env.example apps/p02/.env              # 然后填入自己的 API Key（不填则该应用不可用）
export LLM_API_KEY=sk-xxx                           # 可选：真实模型模式（不加 --mock 时使用）
```

密钥、`runs/`、`logs/`、行情库、招聘会话、知识库数据**都不在仓库里**，由各自代码首次运行时生成。
知识库需要本地 Postgres + pgvector，启动方式见 `apps/kb/README.md`；没有它工作台照常运行，
只是 `/api/kb/status` 返回 `ok=false` 并降级显示。

要点：

- **闸门不是跳过，是搬家。** 网页模式下 G1/G2/G3 写成 `runs/<id>/gates/*.json` 挂起，
  页面轮询到后显示决策卡片；点「通过/退回/驳回」写回 decision，编排线程继续。超时（默认 1800s）按驳回处理。
- 同一时刻只允许一个编排任务（单人本地工具，串行才是对的）。若已有 Run 卡在闸门，
  新任务会返回 busy——先去「反馈闭环」处理它。
- 服务重启会把挂起的闸门标记为 orphaned（原线程已不在），对应 Run 用「续跑」继续；
  同时把停在 `running` 的 Run 标成 `interrupted`，看板上会出现「已中断」横幅。

### 常驻运行（后台跑、不占终端）

前台跑（上面那条命令）的问题是：终端一关、窗口被误关、父进程被杀，服务和正在跑的编排一起消失。
用 daemon 把它拉成独立进程：

```bash
python -m agentflow.daemon start --mock     # 后台起（pythonw，无黑窗），默认 :8760
python -m agentflow.daemon status           # 活着吗 / 什么模式 / 几个 Run 中断待续跑
python -m agentflow.daemon logs -n 60       # 日志尾部（logs/server.log）
python -m agentflow.daemon restart --mock
python -m agentflow.daemon stop             # 连内嵌的 p02 / jobs / market 子进程一起停
```

- pid 与日志在 `logs/`（`server.pid` / `server.log`，超过 5MB 自动换 `.log.1`）。
- 不依赖 nssm / pywin32；`stop` 用 `taskkill /T` 结束整棵进程树，不会留下孤儿子服务。
- **开机自启**两条路：① `Win+R` → `shell:startup` → 放一个指向
  `pythonw.exe -m agentflow.server --mock --port 8760` 的快捷方式（工作目录设成本项目根）；
  ② 计划任务更可控：
  ```powershell
  schtasks /Create /TN "AgentFlow" /SC ONLOGON /TR "\"<pythonw路径>\" -m agentflow.server --mock --port 8760" /F
  ```

### 断点续跑：中断之后到底怎么接上

1. **中断被记录**：服务启动时扫一遍 `runs/*/state.json`，凡 `status=running` 而线程已不存在的，
   改成 `interrupted` 并写一条 `interrupted` 事件（启动日志会打印条数）。编排线程自身抛异常崩掉时也当场标记，不用等重启。
2. **页面说人话**：执行看板顶部出现「已中断」横幅，Run 状态下拉显示「已中断」，
   协作监控页的徽章不再是误导人的「运行中」。
3. **点「继续 / 续跑」**：`orchestrator.resume()` 把卡在 `running` / `review` 的节点退回 `ready` 重跑，
   `done` 的节点直接跳过——不会重复烧 token。已决策的 G1/G2/G3 不会重问。
4. **闸门也要收拾**：旧进程挂起的 pending 闸门标 `orphaned`，否则页面一直显示「待确认」、点了石沉大海。

### 方式二：命令行（闸门在终端敲）

```bash
# 不需要任何 key，用假数据走完全流程（含三个闸门）
python -m agentflow.cli run "写一篇关于 AI Agent 落地为什么这么慢的公众号文章" --mock

# 只想看拆出来什么图，不执行
python -m agentflow.cli plan "同样的需求" --mock

# 真实调用（任何 OpenAI 兼容接口）
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_MODEL=deepseek-chat
python -m agentflow.cli run "写一篇关于 AI Agent 落地的公众号文章"

# 中断后继续（崩了、ctrl-c、闸门没选完都可以）
python -m agentflow.cli resume <run_id>

# 查看
python -m agentflow.cli list
python -m agentflow.cli show <run_id>
```

## 自检

```bash
python tests/smoke_web.py     # 端到端：拆分→G1→执行→G2→汇总→G3，全程 API 驱动
node tests/page_check.js      # 假 DOM 下跑全部 9 个视图，含真实数据注入
node tests/render_check.js    # 三个流水线视图的模板渲染检查
node tests/jobs_check.js      # 招聘采集 Agent：卡片渲染 / 会话回读 / 字段契约
```

## 内嵌 Agent（AI 应用页）

「AI 应用」页顶部的「已接入的本地 Agent」源码已**整体拷入本项目的 `apps/` 目录**，
随工作台一起走、随目录一起搬，注册表和调用通道在 `agentflow/apps.py`
（网页后端与编排执行器共用同一份定义，执行器不该 import 一个 HTTP 服务模块）。已内嵌：

- **P-02 · 文案生成 Agent**（`apps/p02/`，Flask，端口 8978；密钥不进仓库，
  由 `apps/p02/.env.example` 复制为 `.env` 后自行填写）
- **jobs · 招聘 JD 采集分析 Agent**（`apps/jobs/`，标准库，端口 8770，会话数据落在 `apps/jobs/data/`）
- **market · 大盘可视化 Agent**（`apps/market/`，标准库，端口 8080，含 `market.db` 行情库与采集调度）
- **hotspot · 热点雷达 Agent**（`apps/hotspot/`，命令型应用，无 HTTP 服务：`call()` 直接跑
  `collect.py --hours N --digest`，14 源抓取约 40–60 秒，摘要落 `runs/ext-hotspot/`）

工作台提供：探活（在线/离线）、一键启动（用该应用自带的解释器拉起）、
代理转发（页面 → `:8760/api/ext/<key>/<action>` → 应用自己的 API，绕开浏览器 CORS，
动作映射写在注册表的 `proxy` 里，新增应用不用改服务端）、结果留档。
启动调用已加锁串行化——Windows 下 `allow_reuse_address` 允许双进程绑同一端口，
并发启动会让后绑定者抢走连接。

**生成/采集结果自动落盘到 `runs/ext-<key>/<时间戳>-<标题>.md`**——页面只是显示器，
应用把状态存在自己内存/数据目录里，工作台不存档的话收起/刷新就没了。页面提供复制 / 下载 /
收起·展开 / 历史列表。注意：内嵌应用子进程随工作台退出而结束，
工作台启动时会自动把离线应用拉起一次。

**应用活动接入协作监控**：`EXTERNAL_APPS` 里「干活」的动作（`p02/generate`、`jobs/chat`、
`hotspot/collect`）每次被调用都会在 `data/activity.json` 记一条事件——转发前先写「运行中」，
结束后回填完成/失败、耗时、一行结论和存档路径；探活、读会话这类只读转发不记录。
`GET /api/activity` 输出最近 200 条，「协作监控」页底部「AI 应用活动」卡片约 5 秒轮询展示，
运行中的采集能实时看到进行状态，失败会带出对方的真实报错，存档可点开原文。
新 Agent 想进监控：在 `apps.py` 的 `_TRACK_ACTIONS` 里加一行即可。
要接入新的本地 Agent，把源码放进 `apps/<key>/`，再往 `EXTERNAL_APPS` 加一条即可：
`base_url` + `dir` + `launch` + `proxy`（目录默认取 `apps/<key>`，可用 `P02_DIR`/`JOBAGENT_DIR`
等环境变量覆盖）。

### jobs 的双层融合（和 P-02 的差别）

P-02 只是「AI 应用页里的一张卡」。招聘 Agent 除了卡片，还是**编排里的第四个领域**：

1. **卡片**：一句话检索 → 城市定价 / 技能溢价 / 岗位明细三张表；会话回读（刷新页面
   结果还在）；导出 HTML 报告 / 打开完整看板；历史采集列表。
2. **领域 `jobs`**：拆分器（真实模型模式）会把招聘/薪资/岗位类需求拆成 `jobs` 节点，
   路由器派给 `agents/exec_jobs.py`——它不调模型，把节点目标还原成检索句发给
   招聘 Agent（`_search_sentence` 归一化，避免「采集杭州数据分析师」被当成关键词），
   再把返回的分析翻译成 report 契约：结论必须能追到真实岗位链接（`cites_sources`），
   对方离线就直接 blocked 并写清楚怎么恢复，**绝不偷偷用 LLM 编一份假薪资报告**。
   mock 模式下招聘类需求同样产出 jobs 节点——节点数据是真的（来自采集服务），
   其余节点仍是 mock。

自检：`node tests/jobs_check.js`（卡片渲染 + 会话回读 + 字段契约）。

## 知识库存储层（apps/kb · pgvector）

数据库框架：**Postgres 17 + pgvector 0.8.7 + pg_trgm**，跑在 Docker 容器
`agentflow-pg`（端口 **5433**，数据卷 `agentflow-pgdata`，重启策略 `unless-stopped`）。

```bash
# 容器（已建好，换机器时执行一次）
docker run -d --name agentflow-pg \
  -e POSTGRES_USER=agentflow -e POSTGRES_PASSWORD=agentflow -e POSTGRES_DB=agentflow \
  -p 5433:5432 -v agentflow-pgdata:/var/lib/postgresql/data \
  --restart unless-stopped docker.m.daocloud.io/pgvector/pgvector:pg17

# 建表 / 灌示例 / 检索
python -m apps.kb.ingest init          # 建表（幂等）；换 embedding 维度加 --rebuild
python -m apps.kb.ingest demo          # 5 条示例知识
python -m apps.kb.ingest add 资料.md   # 整篇入库，按标题切块、保留条号
python -m apps.kb.ingest search "pgvector 是什么" -k 5
python -m apps.kb.ingest status
```

- **双通道混合检索**：向量通道 `embedding <=> query`（HNSW 余弦）＋ 关键词通道
  `pg_trgm word_similarity`（中文子串命中，GIN 索引），结果按 **RRF（k=60）** 融合，
  两路都命中的自然置顶。
- **Embedding 通道可换**：默认 qwen `text-embedding-v4`（1024 维，复用 `apps/p02/.env`
  的 `QWEN_API_KEY`）；`KB_EMBED_PROVIDER=zhipu` 换智谱 embedding-3；
  `KB_EMBED_PROVIDER=mock` 用确定性哈希向量（不联网，只为跑通管线）。换维度要 `init --rebuild`。
- **`vector(1024)` 的 1024 是我们定的，不是 DeepSeek 的规格**：DeepSeek 开放 API 没有
  `/embeddings` 端点，产不出向量；网上「vector(1024) 正好匹配 DeepSeek embed」是把它某些
  生成模型的隐藏层维度当成了 embedding 输出。1024 对应的是请求体里的 `"dimensions": 1024`
  （qwen text-embedding-v4）。向量通道与生成通道相互独立，详见 `apps/kb/README.md`。
- **表结构**：`kb_documents`（source/title/version/status/is_mandatory，同一 source 重复入库=替换）
  ＋ `kb_chunks`（clause_no 条号 / heading 小节 / content / embedding VECTOR(1024) / tokens）。
  出处（篇名+条号）入库时就定好，检索结果天然可引用——这是防幻觉的地基。
- **状态接口**：`GET /api/kb/status` 返回扩展、文档/块数、维度、当前 provider 与模型名；
  容器没起时返回 `ok=false`，模型中心页的「Embedding 通道」卡与知识库页据此降级。

## 八个组件（对照设计稿第 1 节）

| 文件 | 组件 | 形态 |
|---|---|---|
| `graph.py` | 任务图 TaskGraph / TaskNode | 数据结构 + 环检测 + 拓扑排序 |
| `orchestrator.py` | 编排器 | 代码（主循环） |
| `agents/splitter.py` | 拆分器 | LLM |
| `router.py` | 路由器 | 代码（6 行映射表） |
| `agents/exec_code.py` `exec_content.py` `exec_research.py` | 执行器 ×3 | LLM |
| `monitor.py` | 监控器 | 代码（校验 + 状态推进） |
| `agents/reviewer.py` | 评审器 | LLM（独立于执行器） |
| `agents/aggregator.py` | 汇总器 | LLM（轻） |
| `gates.py` | 三个人工闸门 | 代码（input 循环） |

**只有 5 个是 LLM**。分发和监控刻意写成代码——用 LLM 做这两件事只会引入随机失败。

## 数据契约（`contracts.py`）

三层，缺一层系统就自动化不起来：

1. **拆分器输出** `validate_splitter` — status 必须 ok/blocked；blocked 必须带 `blocking_questions`（禁止猜着拆）
2. **执行器信封** `validate_exec_result` — `status / artifacts / self_check / open_questions / tokens_used / notes_for_downstream`，缺一个就退回重做
3. **评审器输出** `validate_review` — verdict 为 revise/reject 时 `blocking_issue` 四字段必须全填

领域硬规则写在各自的校验器里，比如「标为 fact 但没 sources 直接拒收」「正文含绝对化用语直接拒收」。

**所有输出都走 `call_json()`**（`agents/base.py`）：不合约就把问题清单退回给模型重做，最多 2 次。这是把自由文本变成可自动化组件的唯一办法。

## 状态机与闸门

```
pending → ready → running → review → done
                    │         │
                    └→ failed ┴→ blocked →（人等）
```

- `attempts` 上限 2，超过转 blocked，不再自动重试（防止烧钱）
- blocked / failed 节点的**所有下游自动标记 blocked**，避免整张图悬在等待里
- 每轮做一次死锁检测：没有可推进节点又没跑完 → 报错而不是死等
- 内容节点走完评审后必须过 **G2**；代码节点不过 G2，由 **G3** 统一做合并确认

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_API_KEY` | — | 必填（`--mock` 时不需要） |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | 任何 OpenAI 兼容端点 |
| `LLM_MODEL` | `deepseek-chat` | |
| `AGENTFLOW_MOCK` | `0` | `1` 时用假数据，等价 `--mock` |
| `AGENTFLOW_CONCURRENCY` | `1` | 并发数。>1 时同一 `parallel_group` 的节点并行 |
| `AGENTFLOW_CODE_APPLY` | `0` | `1` 时才允许代码执行器写源码（默认只出改动方案） |
| `AGENTFLOW_GATE_MODE` | `cli` | `web` 时闸门挂到浏览器（server 启动会自动设置） |
| `AGENTFLOW_GATE_TIMEOUT` | `1800` | 闸门等待秒数，超时按驳回处理 |

## 与设计稿的三处偏离（明知故犯）

1. **没用 LangGraph。** 主循环是 `while True` + `ThreadPoolExecutor`，约 120 行。设计稿里写的是 LangGraph，
   但那条建议的前提是「已经到并行 + 多闸门 + 断点续跑」这个复杂度。现在这一版已经把这些能力手写出来了，
   换 LangGraph 的收益是更稳的 superstep 语义和可视化，不是「不做就做不了」。触发条件见下。
2. **没用 SQLite，用 JSON。** 因为 G1 闸门要让人**手改**任务图，JSON 直接可编辑、可 diff。
   3–8 个节点的单人本地工具，SQLite 的原子性收益小于可读性损失。并发写由主循环串行处理，不存在竞态。
3. **代码执行器默认不改仓库。** 设计稿没说这条，是我加的：本地工具误改源码的代价远高于多一步确认。
   要真的落地得显式打开 `AGENTFLOW_CODE_APPLY=1`。

## 什么时候该换 LangGraph

三个问题任一为「是」：

1. 同一时刻有 3 个以上节点在跑，且它们会写同一份状态？
2. 需要从**任意节点**恢复，而不只是「当前轮重新跑」？
3. 闸门会多到每类节点各异，硬编码 `input()` 循环开始失控？

现在只做到 1（同组并行，靠主线程串行写回规避竞态）。2、3 都还没有。

## 已知限制

- **research 没接真实搜索**。模型会用格式正确但可能不存在的 URL 满足「必须标来源」，校验器拦得住"没来源"，拦不住"假来源"。这是当前最大的假象，第二个该修的就是它。
- **没有 token 计量**。`tokens_used` 按字符数估算，只用于超预算标记，不用于计费。
- **评审器默认同一模型**。真实使用时应给 `reviewer.py` 配不同模型，否则自评偏松的问题只是减轻，没有消失。
- **mock 数据只验证管道**，不代表任何内容质量。

## 目录

```
agentflow/
├─ contracts.py      # 全部数据契约与校验器
├─ graph.py          # TaskGraph / TaskNode / 环检测 / 渲染
├─ orchestrator.py   # 主循环 + 三个闸门的挂点 + 事件遥测
├─ router.py         # domain → executor，并发批次
├─ monitor.py        # 验收判定、重试、阻塞传播、死锁检测
├─ gates.py          # G1 / G2 / G3（cli 阻塞式 + web 文件轮询式）
├─ server.py         # Web 控制台：静态托管 + JSON API + 编排任务
├─ store.py          # runs/<run_id>/ 下的图、产物、状态、闸门
├─ llm.py            # OpenAI 兼容调用 + JSON 抽取 + mock
├─ cli.py            # plan / run / resume / show / list / new
├─ pipeline.py       # 旧的单链，保留作对照
├─ agents/
│   ├─ base.py       # call_json + 信封构造
│   ├─ splitter.py   └─ exec_code.py / exec_content.py / exec_research.py
│   ├─ reviewer.py   └─ aggregator.py
│   └─ content.py / research.py   # 单链用的旧版
└─ prompts/          # 6 份提示词，与代码分离
web/                 # 工作台前端（styles/app/data 为原工作台原样保留）
├─ index.html        # 导航增加「协作流水线」分组
├─ pipeline.js       # 拆分 / 执行 / 反馈 三个视图 + 后端通信
├─ pipeline.css      # 三个视图的样式（只新增不覆盖）
└─ …
tests/
├─ smoke_web.py      # 端到端 API 冒烟
├─ page_check.js     # 整页集成自检（假 DOM）
└─ render_check.js   # 视图渲染自检
runs/<run_id>/
├─ graph.json        # 任务图（G1 可编辑，页面改完写回这里）
├─ state.json        # 阶段、产物索引、事件历史（带时间戳，驱动监控页）
├─ trace.log         # 模型调用轨迹（反馈页直接展示）
├─ gates/*.json      # 挂起/已决的闸门（web 模式 HITL 的载体）
├─ aggregate.json    # 汇总结果
├─ final.md          # 定稿
└─ tasks/<task_id>/  # 每个节点的产物 + result.json（含自检明细）
```
