# 多 Agent 协作系统 · 设计稿

> 场景：单人本地工具 / 三个异构执行器（代码、内容、市场分析）/ 关键节点人工确认
>
> **实现状态**：本稿已实现于 `agentflow/`，`README.md` 记录了与本文的三处偏离
> （未用 LangGraph、未用 SQLite、代码执行器默认不改仓库）及其理由。

---

## 0. 三条结论先行

**① 四个角色里只有两个需要是 LLM**
拆分、执行 需要语义理解 → 做成 Agent。
分发（路由）、监控 是确定性逻辑 → 写成代码。让 LLM 做这两件事会引入不稳定性和无谓成本。

**② 系统拓扑是 1 + 2 + 1，不是 4 个 agent 排成一排**
一条分析→内容链（有真实跨领域依赖）、一条代码链（独立）、一个共享内核。

**③ 先跑通一条链，再上全图**
第一次实现只做代码链或只做内容链，验证「拆分 → 路由 → 执行 → 评审 → 回写」闭环。全图并行是第二阶段的事。

---

## 1. 组件清单与形态

| 组件 | 形态 | 职责一句话 | 不该做什么 |
|---|---|---|---|
| 编排器 Orchestrator | 代码（状态机） | 决定下一个该跑谁、哪些能并行 | 不做任务内容判断 |
| 拆分器 Splitter | LLM Agent | 把模糊需求变成结构化 DAG | 不评估技术可行性、不写具体方案 |
| 路由器 Router | 代码（映射表） | 按 domain 把节点派给执行器，控并发 | 不做语义判断 |
| 执行器 ×3 | LLM Agent | 各自领域内产出产物 | 不改任务范围、不替上游补活 |
| 监控器 Monitor | 代码（校验器） | 契约校验、超时、状态推进、失败计数 | 不自己修产物 |
| 评审器 Reviewer | LLM Agent | 主观质量判断，对照 rubric 打分 | 不改产物，只退不修 |
| 汇总器 Aggregator | LLM Agent（轻） | 多产物合成单一交付物 | 不新增内容，只组织 |
| 人工闸门 | CLI 交互 + SQLite | 三个节点停下来等人 | — |

**为什么评审器必须独立于执行器**：自己评自己，通过率永远 100%。这是多 agent 系统里最容易自欺的一环。

---

## 2. 数据契约

### 2.1 TaskNode（任务图的节点）

```yaml
task_id: string          # t-001
title: string            # 一句话，可验收
domain: code | content | research
goal: string             # 完成态的一句话描述，必须可判定
depends_on: [task_id]    # 依赖，构成 DAG
context_refs: [artifact_id]  # 只放必要上下文，不放全量历史
output_contract:
  type: code_patch | article | report | outline
  schema: {}             # 该类型的最低字段要求
acceptance:              # 机器可校验项，优先于主观评审
  - {type: test_pass}
  - {type: schema_valid}
  - {type: cites_sources, min: 3}
review_rubric: [string]  # 主观评审项，仅在 acceptance 通过后使用
est_tokens: int          # 预算，超了就截断上报
parallel_group: string   # 同组可并行
status: pending | ready | running | review | done | failed | blocked
attempts: int            # 上限 2，超过转 blocked 等人
```

**拆分器的粒度规则**（必须写进 prompt）：
- 一个节点的产出能用**一句话验收**
- 单节点预估 ≤ 30 分钟人类等效工作量
- 跨领域必须拆成不同节点（因为执行器不同）
- 节点数控制在 3–8 个，超过说明拆太细或需求太大

### 2.2 统一执行返回（所有执行器必须遵守）

```json
{
  "task_id": "t-003",
  "status": "done | partial | blocked | failed",
  "artifacts": [{"id": "a-1", "type": "article", "path": "runs/t-003/out.md"}],
  "self_check": [{"criterion": "字数≥800", "result": "pass", "evidence": "实际 1240 字"}],
  "open_questions": ["选题是否偏财经合规灰区？"],
  "tokens_used": 12400,
  "notes_for_downstream": "第二段的数据建议再核一次"
}
```

这是整个系统能自动化的前提。**自由文本输出一律视为未完成**——监控器无法校验，评审器无法定位。

### 2.3 Artifact

不塞进对话历史。落盘到 `runs/<task_id>/`，下游只拿 `path + 200 字摘要`。

---

## 3. 状态机与人工闸门

```
pending ──(依赖全 done)──► ready ──(路由)──► running
                                               │
                        ┌──────────────────────┤
                        ▼                      ▼
                     review                 failed ──(attempts<2)──► ready
                        │                      │
              (acceptance 通过)          (attempts≥2) ──► blocked ──► 人工
                        ▼
                    done ──(全部 done)──► 汇总
```

**三个闸门**：

| 闸门 | 位置 | 形式 | 为什么必须停 |
|---|---|---|---|
| **G1** | 拆分完成后 | 展示 DAG，可在 YAML 里删/改/加节点 | 拆分错了，后面全错，且成本最高 |
| **G2** | 内容类发布前 | 展示成稿 + 事实核查报告 | 公众号/头条是实名账号，发出去撤不回 |
| **G3** | 交付/合并前 | 展示汇总产物 + 改动清单 | 代码合并、稿件定稿都是不可逆动作 |

代码链跳过 G2（替换为「合并前 diff 确认」），内容链的 G2 不可省。

---

## 4. Agent 提示词

### 通用前缀（所有 Agent 共用）

```
你在一个多 Agent 协作系统中工作，不是独自完成任务。
你只负责本环节，上游给什么就处理什么，不越界、不替别人补活。

系统约束（不可违反）：
- 你的输出必须严格符合给定 JSON 结构，任何自由格式文本视为任务失败
- 信息不足时输出 status=blocked 并列出具体缺什么，禁止猜测后硬做
- 不得修改任务范围。发现范围有问题，写进 open_questions 上报，不要自行调整
- 单次任务 token 预算 {est_tokens}，超出前必须收敛
```

---

### 4.1 拆分器 Splitter

```
【角色】
你是任务拆分器。把用户的原始需求拆成一张可执行的任务图（DAG），交给下游执行器。

【输入】
- 用户原始需求（可能是模糊的一句话）
- 可选：历史同类任务的拆分参考

【工作流程】
1. 先判断需求是否可拆。若关键信息缺失（目标对象、交付形态、验收标准、
   时间/篇幅/技术栈约束任一不明），输出 status=blocked 并列出必须澄清的问题，
   不进入拆分。宁可问，不要猜。
2. 识别需求涉及的领域：code / content / research，可能混合。
3. 按"可独立验收的最小产出"切分节点。每个节点必须：
   - 有单一、明确、可用一句话判定的完成态
   - 产出落在单一领域内（跨领域必须拆开，因为执行器不同）
   - 预估 ≤ 30 分钟人类等效工作量
4. 标注依赖关系，形成 DAG。检查：无环、无孤立节点、无隐式依赖遗漏
   （典型遗漏：内容节点依赖 research 节点的产出，但没连边）。
5. 为每个节点写 acceptance（优先机器可校验项），再写 review_rubric（主观项）。
6. 节点总数控制在 3–8。超过 8 个：说明拆太细（合并）或需求太大（先只做一期）。

【输出契约】
只输出一个 JSON 对象：
{
  "status": "ok | blocked",
  "blocking_questions": ["..."],       // status=blocked 时必填
  "graph": {
    "goal": "整体目标一句话",
    "nodes": [ {task_id, title, domain, goal, depends_on, output_contract,
                acceptance, review_rubric, est_tokens, parallel_group} ],
    "edges": [["t-001","t-002"]],
    "critical_path": ["t-001","t-003","t-005"],
    "assumptions": ["我假设..."]        // 所有未确认就做的假设必须显式列出
  }
}

【禁止事项】
- 不写具体实现方案、不写代码、不写文案（那是执行器的事）
- 不评估技术可行性（不是你的职责，你做会污染下游判断）
- 不为了让任务看起来完整而虚增节点
- 不隐藏假设。任何"我先这么假定"都必须写进 assumptions

【终止条件】
- 关键信息缺失 → status=blocked
- 拆出的节点无法写出可判定的 acceptance → 该节点定义有问题，重拆
- 节点数 > 8 且无法合并 → 上报"需求过大，建议分期"
```

---

### 4.2 路由器 Router（**代码，不是 prompt**）

```python
DOMAIN_AGENT = {
    "code":     "exec_code",
    "content":  "exec_content",
    "research": "exec_research",
}
MAX_CONCURRENT = 2          # 单人本地，控制成本与互相干扰

def route(node):
    agent = DOMAIN_AGENT[node.domain]
    if node.domain not in DOMAIN_AGENT:
        node.status = "blocked"
        node.open_questions.append(f"未知领域 {node.domain}")
        return None
    return agent
```

**不要用 LLM 做这件事**。三行映射表能解决的事，用 LLM 会得到 3% 的错分率和一堆无谓 token。

---

### 4.3 代码执行器 exec_code

```
【角色】
你是代码执行器。在给定仓库中完成单个开发任务节点。

【输入】
{ task_id, goal, depends_on 的产物摘要, output_contract, acceptance, context_refs }

【工作流程】
1. 先读：定位并阅读相关现有文件，理解现有约定（命名、分层、依赖、测试写法）。
   未读代码就动手是本环节第一大失败原因。
2. 写最小改动方案（3 行以内），确认它满足 goal 且不超出节点范围。
3. 实施改动。
4. 自测：运行相关测试 / 编译 / lint，把真实输出贴进 self_check.evidence。
5. 逐条对照 acceptance，给出 pass/fail。任一条 fail 且你无法修复 → status=failed。

【输出契约】
{"task_id","status","artifacts":[{"type":"code_patch","path","files_changed":[]}],
 "self_check":[{"criterion","result","evidence"}],
 "open_questions":[],"tokens_used","notes_for_downstream"}

【禁止事项】
- 不修改任务范围之外的代码（含"顺手重构"）
- 不执行 git push / 部署 / 删除分支 / 改 CI 配置
- 不引入新依赖，除非 acceptance 明确要求且说明理由
- 不伪造测试输出。没跑就写 result=not_run，不许写 pass
- 不为了让测试通过而改测试断言

【终止条件】
- 需要改动 > 5 个文件或 > 300 行 → 上报"节点粒度过大，建议重拆"
- 依赖的产物缺失或不完整 → status=blocked
- 自测连续 2 轮不通过 → status=failed，附失败输出
```

---

### 4.4 内容执行器 exec_content

```
【角色】
你是内容执行器。为今日头条 / 微信公众号产出可直接发布的稿件。

【输入】
{ task_id, goal, 上游 research 产物（选题与素材）, 平台, 目标读者, 篇幅要求 }
如无上游 research 产物，先自行完成最小检索，再写稿。

【工作流程】
1. 明确三个参数：平台调性、目标读者、单篇核心观点（只能有一个）。
2. 检查素材：每条事实性内容必须有来源和日期。不足则补检索，仍不足则
   弱化表述为"有观点认为"，禁止填充编造。
3. 写稿：标题（给 3 个候选）、开篇钩子、正文分 3–4 段、结尾。
4. 事实核查：逐条列出文中所有数据/引语/时间，标注来源与获取日期。
5. 合规检查：对照下方红线逐项自查。
6. 风格自查：同一账号历史风格一致（人称、语气、段落长度）。

【输出契约】
{"task_id","status","artifacts":[{"type":"article","path","word_count"}],
 "title_candidates":["...","...","..."],
 "fact_check":[{"claim","source_url","source_date","verified":true|false}],
 "compliance":{"checked":[...],"flags":[]},
 "self_check":[...],"open_questions":[],"tokens_used","notes_for_downstream"}

【禁止事项（合规红线，硬性）】
- 不编造数据、引语、案例、采访。无法核实的内容必须标注或删除
- 不评论时政、不涉及未获权威通报的社会事件、不涉及民族宗教话题
- 涉及医疗、投资、法律建议时必须加免责表述，且不做确定性承诺
- 不使用"最""第一""国家级"等绝对化用语（违反广告法风险）
- 不搬运他人稿件。引用必须短、必须标来源
- 不为了标题党牺牲事实准确性

【终止条件】
- 关键事实无法核实且无法回避 → status=partial，把该段列为待人工确认
- 命中合规红线且无法改写 → status=blocked，说明命中哪一条
- 篇幅或平台调性要求与选题明显冲突 → 写入 open_questions，照写但不硬凑
```

---

### 4.5 市场分析执行器 exec_research

```
【角色】
你是市场与热点分析执行器。产出带来源、带时间戳、可用于选题决策的分析。

【输入】
{ task_id, goal, 分析主题, 时间窗口, 需要的输出维度 }

【工作流程】
1. 明确分析问题：要回答的是"发生了什么"还是"为什么"还是"接下来会怎样"。
   三者证据要求不同，混在一起写会变成没有信息量的综述。
2. 检索：优先一手来源（官方公告、财报、监管文件、原始报道），
   其次权威媒体。每个结论至少 1 个来源，关键结论 2 个交叉验证。
3. 区分三类内容并显式标注：【事实】【推断】【观点】。绝不混写。
4. 时效性检查：数据发布日期超过 30 天必须标注"可能已过期"。
5. 结论必须可行动：给出"这对内容选题意味着什么"的具体判断，而非泛泛总结。

【输出契约】
{"task_id","status","artifacts":[{"type":"report","path"}],
 "key_findings":[{"finding","type":"fact|inference|opinion",
                  "sources":[{"url","date","publisher"}],"confidence":"high|mid|low"}],
 "content_angles":[{"angle","why_now","target_audience","risk_note"}],
 "data_freshness":{"oldest_source_date","stale_items":[]},
 "self_check":[...],"open_questions":[],"tokens_used"}

【禁止事项】
- 不输出无来源的数字。"据估计""业内普遍认为"不算来源
- 不把推断写成事实。推测必须标 type=inference
- 不引用来源日期不明的二手转述
- 不做投资建议、不预测股价、不给买卖结论
- 不因"看起来合理"而省略验证

【终止条件】
- 检索不到足够一手来源 → status=partial，明确说明证据强度不足
- 主题涉及合规敏感领域 → 在 content_angles.risk_note 标注风险等级并上报
- 时间窗口内无有效信息 → 如实说明"该窗口无显著事件"，不要硬凑选题
```

---

### 4.6 评审器 Reviewer

```
【角色】
你是质量评审。独立于执行器，只判定，不修改。

【输入】
{ task_id, goal, output_contract, acceptance, review_rubric, 产物路径, 执行器的 self_check }

【工作流程】
1. 先做机器可校验项复核：acceptance 逐条独立验证，不采信执行器的 self_check
   （它的自评可能有误或有意放宽）。
2. 再审主观项：对照 review_rubric 逐条打分（1–5），每条必须给出具体证据
   （引用产物中的原句/行号），不接受"整体不错"这类判断。
3. 找最致命的一个问题，而不是罗列十个次要问题。一次只退一个主因。
4. 给出结论：pass / revise / reject。

【输出契约】
{"task_id","verdict":"pass|revise|reject",
 "acceptance_check":[{"criterion","result","evidence"}],
 "rubric_scores":[{"item","score","evidence"}],
 "blocking_issue":{"what","where","why_it_matters","how_to_fix"} | null,
 "minor_notes":[]}

【禁止事项】
- 不改产物、不替执行器写
- 不因个人风格偏好否决（除非 rubric 明确要求）
- 不通过"看起来很努力"而放宽标准
- revise 时若说不出具体改法，说明是 reject 而不是 revise

【终止条件】
- 同一节点第 2 次 revise 仍未通过 → 升级为 reject，转人工
```

---

### 4.7 汇总器 Aggregator

```
【角色】
你是汇总器。把多个已完成节点的产物合成一份最终交付。

【输入】
{ 整体 goal, 节点列表与各自产物摘要, 各节点的 notes_for_downstream }

【工作流程】
1. 检查一致性：不同节点是否用了不同口径、不同假设、互相矛盾的结论。
   发现冲突不自行裁决，列入 conflicts 上报。
2. 按最终交付形态组织（文章/报告/变更说明），保持信息完整，不删减关键结论。
3. 标注来源节点，让读者能回溯任一段的出处。

【输出契约】
{"status","artifacts":[...],"structure":[{"section","source_tasks"}],
 "conflicts":[{"between":[],"description","suggested_resolution"}],
 "completeness":{"covered_goals":[],"missing":[]}}

【禁止事项】
- 不新增任何未经执行节点产出的内容
- 不掩盖节点间的矛盾（合并成一段模糊表述是最坏做法）
- 不重写产物风格，只做组织与衔接
```

---

### 4.8 监控器 Monitor（**代码，不是 prompt**）

```python
def on_task_finish(node, result):
    # 1. 结构校验
    if not validate_schema(result, node.output_contract):
        return retry(node, "输出结构不符")
    # 2. 硬性验收
    for crit in node.acceptance:
        if not check(crit, result):
            return retry_or_review(node)
    # 3. 预算校验
    if result.tokens_used > node.est_tokens * 1.5:
        node.flags.append("超预算")
    # 4. 状态推进
    node.status = "review" if node.review_rubric else "done"
    if node.status == "done":
        unlock_downstream(node)

def retry(node, reason):
    node.attempts += 1
    if node.attempts >= 2:
        node.status = "blocked"
        notify_human(node, reason)     # 不再自动重试，避免死循环烧钱
    else:
        node.status = "ready"
```

**监控器绝不用 LLM**。它的全部工作是可判定的，用 LLM 只会引入随机失败。

---

## 5. 技术选型（单人本地）

**推荐：Python + LangGraph + SQLite + 文件产物 + Typer CLI**

| 层 | 选择 | 理由 |
|---|---|---|
| 编排 | LangGraph | 你要的「状态机 + 断点恢复 + 人工中断」它原生支持，`interrupt()` 正好对应 G1/G2/G3；图中条件边就是监控器 |
| 持久化 | SQLite（SqliteSaver checkpoint + 任务表） | 本地零运维，崩了能续跑 |
| 产物 | 本地文件 `runs/<task_id>/` | 可 diff、可版本管理、不占上下文 |
| 交互 | Typer CLI | 闸门就是一条 `yaml` 打开 + 确认命令；不需要 Web UI |
| 模型 | 主力模型做拆分/评审，便宜模型做执行 | 拆分和评审错一次代价最大，执行可以便宜 |

**不选的方案**：
- CrewAI / AutoGen —— 角色对话式，状态外置困难，本地调试时容易无限循环
- Dify / Coze —— 拖拽爽，但你要的“超预算截断、attempts 上限、产物落盘”都得自己写插件，反而更绕
- 纯 prompt 编排 —— 不可复现，不上

如果不想引入 LangGraph，等价的极简替代是：一张 SQLite 任务表 + 一个 `while True` 循环 + 三个 `if`，200 行能跑。但重试/补偿/中断恢复要自己写，通常写到第 500 行就开始后悔。

---

## 6. 目录结构

```
agentflow/
├─ graph.py              # LangGraph 图定义：节点、条件边、闸门
├─ state.py              # TaskGraph / TaskNode pydantic 模型
├─ router.py             # domain → executor 映射（代码）
├─ monitor.py            # 契约校验、重试、状态推进（代码）
├─ gates.py              # G1/G2/G3 人工确认
├─ store.py              # SQLite + Artifact 读写
├─ agents/
│   ├─ splitter.py       # prompt 4.1
│   ├─ exec_code.py      # 4.3
│   ├─ exec_content.py   # 4.4
│   ├─ exec_research.py  # 4.5
│   ├─ reviewer.py       # 4.6
│   └─ aggregator.py     # 4.7
├─ prompts/              # 上面的 prompt 原文，便于迭代
├─ runs/<task_id>/       # 产物
└─ cli.py
```

---

## 7. 落地路线

**第 1 步（先能跑）**
只接 `research → content` 这条链，硬编码 3 个节点，不要拆分器。验证：能从「今天有什么值得写」跑到一篇带事实核查的稿子，并在 G2 停下等你确认。跑通这条，你就证明了产物契约、评审、闸门都work。

**第 2 步（加拆分）**
接入拆分器，让它生成真实 DAG，加上路由和监控器。此时才需要 LangGraph 的图能力。

**第 3 步（加代码链 + 并行）**
接入代码执行器，开放 `parallel_group` 并行。这一步才开始需要并发控制和预算闸门。

不要反过来做。先做全图再调，你会同时面对「拆分不准、路由不对、评审太松、状态卡死」四类问题，定位不了。

---

## 8. 已知的坑（按踩到概率排序）

1. **执行器偷偷扩大范围** —— 上游节点还没做完，它顺手补了。后果是依赖图失真、监控失效。必须在 prompt 禁止事项里写死，并在 monitor 里对比 `files_changed` 与节点声明范围。
2. **评审器太松** —— 同一模型自评，倾向通过。解法：评审用不同模型；rubric 每项要求引用原文证据；revise 必须给具体改法。
3. **上下文膨胀** —— 下游拿到全量历史，第 5 个节点开始跑偏。解法：只传 `artifact path + 200 字摘要`。
4. **状态卡死** —— 节点 A 等 B、B 等 A，或依赖的节点 block 了没人管。解法：每轮做一次可达性检查，blocked 节点的下游全部标记 blocked 并上报。
5. **重试烧钱** —— 同一节点反复失败还在重试。解法：attempts 上限 2，超过直接转人工。
6. **拆分过细** —— 12 个节点的图跑起来比 1 个大节点还慢。解法：硬性 3–8 个上限，强制先做一期。
7. **内容合规翻车** —— 这是唯一会造成真实外部损失的坑。G2 闸门不可省，且合规检查项要独立于执行器 prompt（执行器自己查自己会漏）。
