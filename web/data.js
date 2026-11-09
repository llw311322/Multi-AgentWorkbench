/* ===========================================================
   数据层：全部为本地模拟数据，方便替换为真实接口
   =========================================================== */
window.DB = {
  meta: {
    user: { name: '李良伟', initials: 'LLW', plan: 'Personal workspace' },
    models: ['deepseek-v4-flash', 'qwen-max', 'kimi-k3', 'glm-4-flash', 'doubao-seed-2-1-pro-260628'],
  },

  /* 任务：status = running | queued | done | failed */
  tasks: [
    {
      id: 'T-2401',
      name: '深度调研 · 竞品分析报告',
      module: 'Agent',
      owner: 'Researcher',
      steps: 18,
      elapsed: '2m14s',
      status: 'running',
      progress: 62,
      createdAt: '今天 09:12',
      summary: '抓取 12 个竞品站点，抽取定价与功能矩阵，输出对比报告与结论。',
      tools: ['browser.goto', 'browser.extract', 'llm.summarize', 'file.write'],
    },
    {
      id: 'T-2402',
      name: '知识库 · 批量索引 320 篇文档',
      module: '知识库',
      owner: 'Indexer',
      steps: 320,
      elapsed: '6m40s',
      status: 'done',
      progress: 100,
      createdAt: '今天 08:40',
      summary: '分块 1.2 万段，写入向量库；抽取 86 条实体关系。',
      tools: ['kb.chunk', 'embed.batch', 'vector.upsert'],
    },
    {
      id: 'T-2405',
      name: 'AI 应用 · 简历助手评测迭代',
      module: 'AI 应用',
      owner: 'Evaluator',
      steps: 120,
      elapsed: '评测中',
      status: 'running',
      progress: 34,
      createdAt: '今天 10:20',
      summary: '跑 120 条评测集，当前得分 96.2（+1.8），继续扩大样本。',
      tools: ['eval.run', 'llm.judge', 'report.diff'],
    },
    {
      id: 'T-2406',
      name: '记忆层 · Agent Memory 重构',
      module: 'Agent',
      owner: 'Architect',
      steps: 0,
      elapsed: '排队中',
      status: 'queued',
      progress: 0,
      createdAt: '今天 10:31',
      summary: '将短期记忆切换为分层记忆（工作记忆 + 摘要 + 长期向量）。',
      tools: [],
    },
  ],

  /* 知识库集合 */
  collections: [
    { id: 'KB-01', name: '技术文档 · RAG 问答集', docs: 620, indexed: 620, updated: '今天 09:40', tag: '技术' },
    { id: 'KB-02', name: '代码片段 · 全栈脚手架', docs: 215, indexed: 215, updated: '昨天', tag: '代码' },
    { id: 'KB-03', name: '论文笔记 · 具身智能专题', docs: 348, indexed: 312, updated: '9 月 22 日', tag: '论文' },
  ],

  /* 检索测试的模拟结果 */
  retrieval: [
    { doc: 'RAG 分块策略对比 · chunk 512 vs 1024', source: '技术文档 · RAG 问答集', score: 0.92, snippet: '当文档结构清晰时，按标题层级分块（semantic chunking）比固定长度分块召回率提升约 14%…' },
    { doc: '混合检索：向量 + BM25 的工程取舍', source: '技术文档 · RAG 问答集', score: 0.87, snippet: '纯向量检索在专有名词上表现不稳，叠加 BM25 后可显著改善精确匹配类问题…' },
    { doc: 'Agent Memory 分层设计笔记', source: '论文笔记 · 具身智能专题', score: 0.81, snippet: '工作记忆保存最近 N 轮，摘要层压缩历史，长期层写入向量库并按需检索…' },
  ],

  /* 模型：真实厂商配置（首屏静态兜底，init 后由 /api/models 刷新，
     数据源 = apps/p02/.env 实际生效配置，key 只显示是否已配，不展示明文） */
  models: [
    { key: 'deepseek', vendor: 'DeepSeek', model: 'deepseek-v4-flash', base_url: 'https://api.deepseek.com/v1', key_set: true, options: ['deepseek-chat', 'deepseek-reasoner'], active: true },
    { key: 'qwen', vendor: '通义千问', model: 'qwen-max', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', key_set: true, options: ['qwen-plus', 'qwen-max', 'qwen-turbo', 'qwen-long'], active: false },
    { key: 'kimi', vendor: 'Kimi（月之暗面）', model: 'kimi-k3', base_url: 'https://api.moonshot.cn/v1', key_set: true, options: ['moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k', 'kimi-k2-0711-preview'], active: false },
    { key: 'zhipu', vendor: '智谱 GLM', model: 'glm-4-flash', base_url: 'https://open.bigmodel.cn/api/paas/v4', key_set: true, options: ['glm-4-plus', 'glm-4-air', 'glm-4-flash', 'glm-4-long'], active: false },
    { key: 'doubao', vendor: '豆包（火山方舟）', model: 'doubao-seed-2-1-pro-260628', base_url: 'https://ark.cn-beijing.volces.com/api/v3', key_set: false, options: ['doubao-seed-2-1-pro-260628', 'doubao-seed-2-1-turbo', 'doubao-seed-2-1-flash'], active: false },
    { key: 'custom', vendor: '自定义（OpenAI 兼容）', model: '—', base_url: '', key_set: false, options: [], active: false },
  ],

  /* 事件流 */
  events: [
    { t: '11:42', text: '简历助手评测 34% · 当前 96.2', kind: 'ok' },
    { t: '11:20', text: '四足行走 V1 保存 checkpoint', kind: 'ok' },
    { t: '10:31', text: '任务 T-2406 进入队列', kind: 'muted' },
    { t: '10:05', text: 'ai-workbench 生产部署成功', kind: 'ok' },
    { t: '09:58', text: '机械臂 V3 第 3 次重启，仍停在 87.4%', kind: 'bad' },
  ],

  /* ===========================================================
     多 Agent 协作监控
     -----------------------------------------------------------
     run.agents[]  : 一次协作中的 Agent 实例（泳道）
       start / end : 相对本次 Run 起始的秒数（end 为空表示仍在进行）
       status      : running | done | blocked | failed | idle
     run.messages[]: Agent 之间的消息边（A2A 通信）
       t           : 发送时刻（秒）
       kind        : delegate 委派 | result 返回 | challenge 质询 | error 错误
     =========================================================== */
  runs: [
    {
      id: 'RUN-2041',
      name: '竞品分析 · 多 Agent 扇出',
      status: 'blocked',
      form: 'fanout',
      depth: 3,
      startedAt: '今天 10:12',
      durationText: '4m28s',
      durationSec: 268,
      cost: 1.86,
      tokens: '184K',
      goal: '抓取 12 个竞品站点，抽取定价与功能矩阵，输出对比报告与结论。',
      agents: [
        { id: 'A1', name: 'Planner', role: '规划', model: 'GPT-5.2', status: 'done', start: 0, end: 14, tokens: '12.4K', cost: 0.12, calls: 6, retries: 0 },
        { id: 'A2', name: 'Researcher', role: '检索', model: 'Claude 4.6', status: 'running', start: 14, end: 196, tokens: '86.2K', cost: 0.78, calls: 34, retries: 2 },
        { id: 'A3', name: 'Analyst', role: '分析', model: 'GPT-5.2', status: 'blocked', start: 196, end: 268, tokens: '4.1K', cost: 0.04, calls: 2, retries: 0, note: '等待人工确认数据源' },
        { id: 'A4', name: 'Writer', role: '写作', model: 'Claude 4.6', status: 'idle', start: 268, end: 268, tokens: '0', cost: 0, calls: 0, retries: 0, note: '未开始 · 依赖 Analyst 输出' },
      ],
      messages: [
        { id: 'M1', from: 'A1', to: 'A2', t: 14, kind: 'delegate', label: '委派：抓取 12 个竞品站点', payload: '{\n  "task": "crawl_competitors",\n  "targets": 12,\n  "fields": ["pricing", "features", "positioning"],\n  "budget_tokens": 60000\n}' },
        { id: 'M2', from: 'A2', to: 'A1', t: 96, kind: 'result', label: '返回：8 个站点结构（4 个超时）', payload: '{\n  "ok": 8,\n  "timeout": 4,\n  "note": "4 个站点需 JS 渲染，服务端抓取返回空 DOM"\n}' },
        { id: 'M3', from: 'A1', to: 'A2', t: 100, kind: 'delegate', label: '重试超时站点，改用移动端 UA', payload: '{\n  "retry": ["c4", "c7", "c9", "c11"],\n  "user_agent": "mobile_safari_17",\n  "wait_ms": 2500\n}' },
        { id: 'M4', from: 'A2', to: 'A3', t: 196, kind: 'result', label: '交接：竞品功能矩阵', payload: '{\n  "rows": 12,\n  "cols": 46,\n  "missing": ["c7.pricing", "c9.enterprise_plan"]\n}' },
        { id: 'M5', from: 'A3', to: 'A1', t: 210, kind: 'challenge', label: '质询：缺 2 个关键字段，无法出结论', payload: '{\n  "reason": "pricing 缺失导致无法计算价格带",\n  "ask": "人工确认 c7/c9 数据源或允许降级为估算"\n}' },
        { id: 'M6', from: 'A1', to: 'A3', t: 214, kind: 'delegate', label: '已请求人工确认，先暂停分支', payload: '{\n  "action": "pause",\n  "escalate": "human_in_the_loop"\n}' },
      ],
      alerts: [
        { level: 'bad', text: 'Analyst 阻塞 4m12s，等待人工确认数据源' },
        { level: 'warn', text: 'Researcher 重试 2 次，4 个站点抓取失败' },
      ],
    },
    {
      id: 'RUN-2040',
      name: '论文精读 · 抽取与审校循环',
      status: 'running',
      form: 'loop',
      depth: 2,
      startedAt: '今天 10:26',
      durationText: '3m02s',
      durationSec: 182,
      cost: 2.34,
      tokens: '268K',
      goal: '精读 6 篇具身智能论文，抽取方法/指标，交叉审校后写入知识库。',
      agents: [
        { id: 'A1', name: 'Reader', role: '精读', model: 'Claude 4.6', status: 'running', start: 0, end: 182, tokens: '92.4K', cost: 0.86, calls: 18, retries: 0 },
        { id: 'A2', name: 'Extractor', role: '抽取', model: 'Qwen3-Max', status: 'running', start: 8, end: 164, tokens: '118.0K', cost: 1.02, calls: 42, retries: 1 },
        { id: 'A3', name: 'Critic', role: '审校', model: 'GPT-5.2', status: 'running', start: 70, end: 182, tokens: '57.6K', cost: 0.46, calls: 21, retries: 0 },
      ],
      messages: [
        { id: 'M1', from: 'A1', to: 'A2', t: 8, kind: 'delegate', label: '委派：按章节抽取方法段', payload: '{\n  "paper": "p1",\n  "sections": ["method", "metrics"]\n}' },
        { id: 'M2', from: 'A2', to: 'A3', t: 70, kind: 'result', label: '交接：方法抽取结果', payload: '{\n  "claims": 14,\n  "confidence": 0.71\n}' },
        { id: 'M3', from: 'A3', to: 'A2', t: 92, kind: 'challenge', label: '质询：第 7 条缺乏指标支撑', payload: '{\n  "claim_id": 7,\n  "issue": "no_metric_cited"\n}' },
        { id: 'M4', from: 'A2', to: 'A3', t: 116, kind: 'result', label: '补充 Table 3 指标引用', payload: '{\n  "claim_id": 7,\n  "cite": "Table 3, p.7"\n}' },
        { id: 'M5', from: 'A3', to: 'A2', t: 138, kind: 'challenge', label: '质询：引用表格与正文数值不一致', payload: '{\n  "claim_id": 7,\n  "diff": "0.82 vs 0.78"\n}' },
        { id: 'M6', from: 'A2', to: 'A3', t: 160, kind: 'result', label: '返回：已按正文数值修正', payload: '{\n  "claim_id": 7,\n  "value": 0.82\n}' },
        { id: 'M7', from: 'A3', to: 'A2', t: 172, kind: 'challenge', label: '质询：第 11 条同样问题（第 5 轮）', payload: '{\n  "claim_id": 11,\n  "round": 5\n}' },
      ],
      alerts: [
        { level: 'bad', text: 'Extractor ↔ Critic 往复 6 轮，疑似循环' },
        { level: 'warn', text: 'Token 增速 4.2K/min，按当前速度 6 分钟后超预算' },
      ],
    },
    {
      id: 'RUN-2039',
      name: '知识库 · 补索引流水线',
      status: 'done',
      form: 'chain',
      depth: 2,
      startedAt: '今天 09:40',
      durationText: '1m34s',
      durationSec: 94,
      cost: 0.42,
      tokens: '38K',
      goal: '对论文笔记集合补齐 36 篇增量文档的索引。',
      agents: [
        { id: 'A1', name: 'Planner', role: '规划', model: 'GPT-5.2', status: 'done', start: 0, end: 9, tokens: '3.2K', cost: 0.03, calls: 3, retries: 0 },
        { id: 'A2', name: 'Indexer', role: '索引', model: 'Qwen3-Max', status: 'done', start: 9, end: 78, tokens: '28.6K', cost: 0.31, calls: 12, retries: 0 },
        { id: 'A3', name: 'Verifier', role: '校验', model: 'GPT-5.2', status: 'done', start: 78, end: 94, tokens: '6.2K', cost: 0.08, calls: 4, retries: 0 },
      ],
      messages: [
        { id: 'M1', from: 'A1', to: 'A2', t: 9, kind: 'delegate', label: '委派：补齐 36 篇增量索引', payload: '{\n  "collection": "KB-03",\n  "docs": 36,\n  "chunk": "semantic"\n}' },
        { id: 'M2', from: 'A2', to: 'A3', t: 78, kind: 'result', label: '交接：写入 1,284 个分块', payload: '{\n  "chunks": 1284,\n  "failed": 0\n}' },
        { id: 'M3', from: 'A3', to: 'A1', t: 94, kind: 'result', label: '校验通过，回写集合状态', payload: '{\n  "verified": 36,\n  "status": "indexed"\n}' },
      ],
      alerts: [],
    },
    {
      id: 'RUN-2038',
      name: '工作台前端 · 发布链路',
      status: 'failed',
      form: 'chain',
      depth: 2,
      startedAt: '今天 10:02',
      durationText: '0m58s',
      durationSec: 58,
      cost: 0.18,
      tokens: '14K',
      goal: '构建并发布 ai-workbench 到生产环境。',
      agents: [
        { id: 'A1', name: 'Planner', role: '规划', model: 'GPT-5.2', status: 'done', start: 0, end: 6, tokens: '2.1K', cost: 0.02, calls: 2, retries: 0 },
        { id: 'A2', name: 'Builder', role: '构建', model: 'Llama-4-70B', status: 'failed', start: 6, end: 52, tokens: '9.8K', cost: 0.16, calls: 8, retries: 3, note: 'exit 1 · 依赖解析失败' },
        { id: 'A3', name: 'SmokeTester', role: '冒烟', model: 'GPT-5.2', status: 'idle', start: 58, end: 58, tokens: '0', cost: 0, calls: 0, retries: 0, note: '未执行' },
      ],
      messages: [
        { id: 'M1', from: 'A1', to: 'A2', t: 6, kind: 'delegate', label: '委派：构建生产产物', payload: '{\n  "target": "prod",\n  "node": 20\n}' },
        { id: 'M2', from: 'A2', to: 'A1', t: 52, kind: 'error', label: '构建失败：ERESOLVE 依赖冲突', payload: '{\n  "code": "ERESOLVE",\n  "detail": "react@19 vs @types/react@18"\n}' },
      ],
      alerts: [
        { level: 'bad', text: 'Builder 重试 3 次后中断，依赖冲突未解决' },
      ],
    },
  ],
};
