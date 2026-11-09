/* ===========================================================
   AI 工作台 · 应用逻辑（无构建、无依赖）
   =========================================================== */

(() => {
  'use strict';

  /* ---------- 状态 ---------- */
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const state = {
    view: 'overview',
    tasks: clone(DB.tasks),
    collections: clone(DB.collections),
    models: clone(DB.models),
    runs: clone(DB.runs),
    runId: DB.runs[0].id,
    runFilter: 'all',
    selAgent: null,
    /* 真实数据：extActs ← /api/activity，extAppsStatus ← /api/ext */
    extActs: [],
    extAppsStatus: [],
    kbStatus: null,
    selMsg: null,
    taskFilter: 'all',
    query: '',
    hits: clone(DB.retrieval),
    paletteOpen: false,
    paletteIndex: 0,
    paletteQuery: '',
    toasts: [],
  };

  const VIEW_META = {
    overview: { title: '总览', sub: '真实协作概况 / AI 应用活动' },
    runs: { title: '协作监控', sub: '协作拓扑 / 泳道时间轴 / A2A 消息总线' },
    run: { title: 'Run 详情', sub: '多 Agent 协作追踪' },
    agents: { title: 'Agent 编排', sub: '任务队列 / 工具调用 / 运行日志' },
    apps: { title: 'AI 应用', sub: '内嵌 Agent 的真实调用入口' },
    knowledge: { title: '知识库', sub: '集合管理 / 索引任务 / 检索测试' },
    models: { title: '模型中心', sub: '真实厂商配置 / Key 状态 / 调用链路' },
    /* 协作流水线三页由 pipeline.js 提供数据，标题落在这里 */
    plan: { title: '任务拆分', sub: '需求 → 任务图 DAG → G1 人工确认' },
    board: { title: '执行看板', sub: '就绪队列 / 并发执行 / 路由映射' },
    feedback: { title: '反馈闭环', sub: '闸门决策 / 验收评审 / 事件与调用轨迹' },
  };

  /* 数字键 1-8 对应的视图顺序（run 详情不占快捷键） */
  const NAV_KEYS = ['overview', 'runs', 'agents', 'apps', 'knowledge', 'models', 'plan', 'board', 'feedback'];

  /* ---------- 工具 ---------- */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => n.toLocaleString('en-US');
  const pct = (a, b) => Math.round((a / b) * 100);

  let uid = 0;
  const svgId = () => 'g' + (++uid);

  const STATUS_TEXT = { running: '运行中', done: '已完成', failed: '异常', queued: '排队中', stalled: '停滞' };

  function badge(status, text) {
    return `<span class="badge ${status}">${esc(text || STATUS_TEXT[status] || status)}</span>`;
  }

  function spark(values, color) {
    const w = 120, h = 32, max = Math.max(...values), min = Math.min(...values);
    const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - ((v - min) / (max - min || 1)) * (h - 6) - 3]);
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" opacity=".85"/></svg>`;
  }

  /* 任务行 */
  function taskRow(t) {
    const barClass = t.status === 'failed' ? 'is-bad' : t.status === 'running' && t.progress > 80 ? 'is-warn' : '';
    const stalled = /已卡/.test(t.elapsed);
    return `<div class="task" data-task="${t.id}" role="button" tabindex="0">
      <div class="task-main">
        <div class="task-name">${esc(t.name)}</div>
        <div class="task-meta">
          <span>${esc(t.id)}</span>
          <span class="${stalled ? 'stall' : ''}">${esc(t.elapsed)}</span>
          <span>${esc(t.module)}</span>
        </div>
      </div>
      <div class="task-bar ${barClass}" data-bar="${t.id}"><i style="width:${t.progress}%"></i></div>
      ${badge(t.status)}
    </div>`;
  }

  /* ---------- 视图：总览 ---------- */
  function viewOverview() {
    const runs = state.runs;
    const activeRuns = runs.filter((x) => x.status === 'running' || x.status === 'blocked').length;
    const blockedAgents = runs.reduce((a, x) => a + x.agents.filter((y) => y.status === 'blocked').length, 0);
    const agentCount = runs.reduce((a, x) => a + x.agents.length, 0);
    const depth = (runs.reduce((a, x) => a + x.depth, 0) / runs.length).toFixed(1);
    const runCost = runs.reduce((a, x) => a + x.cost, 0);
    const doneRuns = runs.filter((x) => x.status === 'done').length;
    const acts = state.extActs || [];
    const actRunning = acts.filter((a) => a.state === 'running').length;
    const actFailed = acts.filter((a) => a.state === 'failed' || a.state === 'lost').length;

    return `
    <section class="kpi-row">
      <article class="kpi hero">
        <p class="kpi-label">活跃协作 Run <span class="en">ACTIVE RUNS</span></p>
        <div class="kpi-value">${activeRuns}</div>
        <div class="kpi-foot"><span>累计 ${runs.length} 个 · ${agentCount} 个 Agent 实例</span></div>
        ${spark(runs.map((r) => r.agents.length).reverse().concat([0, 0, 0]).slice(-11), '#2EA7FF')}
      </article>
      <article class="kpi">
        <p class="kpi-label">阻塞中 <span class="en">BLOCKED</span></p>
        <div class="kpi-value" style="${blockedAgents ? 'color:var(--rose)' : ''}">${blockedAgents}</div>
        <div class="kpi-foot"><span class="bad">${blockedAgents ? '需人工介入 · 去反馈闭环处理' : '无需人工介入'}</span></div>
      </article>
      <article class="kpi">
        <p class="kpi-label">已完成 Run <span class="en">DONE</span></p>
        <div class="kpi-value">${doneRuns}</div>
        <div class="kpi-foot"><span>平均委派深度 ${depth} 层</span></div>
      </article>
      <article class="kpi">
        <p class="kpi-label">协作成本 <span class="en">COST</span></p>
        <div class="kpi-value">¥${runCost.toFixed(1)}</div>
        <div class="kpi-foot"><span>真实编排 Run 累计</span></div>
      </article>
    </section>

    ${alertBar(3)}

    <section class="mid">
      <article class="card">
        <div class="card-head">
          <h2 class="card-title">AI 应用活动</h2>
          <div style="display:flex;align-items:center;gap:12px">
            <span class="card-sub">${acts.length} 次调用${actRunning ? ` · <span style="color:var(--cyan)">${actRunning} 运行中</span>` : ''}</span>
            <button class="link" data-nav="runs">协作监控</button>
          </div>
        </div>
        ${acts.length ? `
        <div class="rows" style="gap:8px">
          ${acts.slice(0, 6).map((a) => {
            const [cls, txt] = ACT_BADGE[a.state] || ACT_BADGE.running;
            return `<div class="row">
              <span class="name" style="max-width:46%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.app)}<span style="color:var(--dim)"> · ${esc(ACT_TEXT[a.action] || a.action)}</span></span>
              <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--dim);font-size:11px">${esc(String(a.summary || a.note || a.error || '—'))}</span>
              ${badge(cls, txt)}
            </div>`;
          }).join('')}
        </div>` : `
        <p class="card-sub" style="padding:6px 0 2px">还没有 AI 应用调用。去 <button class="link" data-nav="apps">AI 应用</button> 页生成文案、采集岗位或抓热点，活动会实时出现在这里和「协作监控」。</p>`}
      </article>

      <div class="right-col">
        <article class="card">
          <div class="card-head">
            <h2 class="card-title">编排流水线</h2>
            <span class="card-sub">拆分 → 执行 → 闸门 → 汇总</span>
          </div>
          <div class="rows">
            <div class="row"><span class="name">提交新需求，走完整多 Agent 编排</span><button class="link" data-nav="plan">任务拆分 →</button></div>
            <div class="row"><span class="name">查看执行进度与 Agent 泳道</span><button class="link" data-nav="board">执行看板 →</button></div>
            <div class="row"><span class="name">处理等待你决策的 G1/G2/G3 闸门</span><button class="link" data-nav="feedback">反馈闭环 →</button></div>
          </div>
        </article>

        <article class="card">
          <div class="card-head">
            <h2 class="card-title">内嵌 Agent 状态</h2>
            <button class="link" data-nav="apps">AI 应用 →</button>
          </div>
          <div class="rows">
            ${(state.extAppsStatus || []).map((a) => `
              <div class="row">
                <span class="name">${esc(a.name)}</span>
                ${badge(a.status === 'online' || a.status === 'ready' ? 'done' : 'failed', a.status === 'online' ? '在线' : a.status === 'ready' ? '就绪' : '离线')}
              </div>`).join('') || '<p class="card-sub">读取中…</p>'}
          </div>
        </article>
      </div>
    </section>`;
  }

  /* ---------- 视图：Agent 编排 ---------- */
  function viewAgents() {
    const f = state.taskFilter;
    const list = state.tasks.filter((t) => f === 'all' || t.status === f);
    const stats = [
      ['运行中', state.tasks.filter((t) => t.status === 'running').length],
      ['排队', state.tasks.filter((t) => t.status === 'queued').length],
      ['异常', state.tasks.filter((t) => t.status === 'failed').length],
      ['平均耗时', '3m12s'],
    ];
    return `
    <section class="strip">
      ${stats.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('')}
    </section>

    <article class="card">
      <div class="card-head">
        <h2 class="card-title">任务队列</h2>
        <div class="chips">
          ${[['all', '全部'], ['running', '运行中'], ['done', '已完成'], ['failed', '异常'], ['queued', '排队']]
            .map(([k, t]) => `<button class="chip ${f === k ? 'is-active' : ''}" data-filter="${k}">${t}</button>`).join('')}
        </div>
      </div>
      <table class="table">
        <thead><tr><th>任务</th><th>模块</th><th>执行者</th><th>进度</th><th>耗时</th><th>状态</th></tr></thead>
        <tbody>
          ${list.map((t) => `
            <tr data-task="${t.id}">
              <td class="name">${esc(t.name)}<div class="mono">${esc(t.id)}</div></td>
              <td>${esc(t.module)}</td>
              <td class="mono">${esc(t.owner)}</td>
              <td style="width:120px"><div class="task-bar" style="width:100%"><i style="width:${t.progress}%"></i></div></td>
              <td class="mono">${esc(t.elapsed)}</td>
              <td>${badge(t.status)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
      ${list.length ? '' : '<p class="card-sub">该筛选下暂无任务。</p>'}
    </article>

    <article class="card">
      <div class="card-head">
        <h2 class="card-title">最近事件</h2>
        <span class="card-sub">实时</span>
      </div>
      <div class="rows">
        ${DB.events.map((e) => `<div class="row"><span class="num">${esc(e.t)}</span><span class="name" style="color:${e.kind === 'bad' ? 'var(--rose)' : e.kind === 'ok' ? 'var(--muted)' : 'var(--dim)'}">${esc(e.text)}</span></div>`).join('')}
      </div>
    </article>`;
  }

  /* ---------- 视图：AI 应用 ---------- */
  function viewApps() {
    /* 整页只有一个真实区块：4 个内嵌 Agent 的调用入口（pipeline.js 渲染进 #extApps）。
       原来的演示卡（智能简历助手 / RAG 问答站）、假统计条和「部署记录」表已删除——
       它们的数据是编的，按钮只弹 toast，留着只会误导。 */
    return '<div id="extApps"></div>';
  }

  /* ---------- 视图：知识库 ---------- */
  function viewKnowledge() {
    const docs = state.collections.reduce((a, c) => a + c.docs, 0);
    const indexed = state.collections.reduce((a, c) => a + c.indexed, 0);
    return `
    <section class="strip">
      <div class="stat"><b>${num(docs)}</b><span>文档总数</span></div>
      <div class="stat"><b>${num(indexed)}</b><span>已索引</span></div>
      <div class="stat"><b>${state.collections.length}</b><span>集合</span></div>
      <div class="stat"><b>${num(docs - indexed)}</b><span>待索引</span></div>
    </section>

    <section class="mid" style="grid-template-columns:minmax(0,1fr) 420px">
      <article class="card">
        <div class="card-head">
          <h2 class="card-title">集合管理</h2>
          <button class="ghost-btn" data-toast="已打开文档上传面板">上传文档</button>
        </div>
        <div class="rows">
          ${state.collections.map((c) => `
            <div class="row" style="flex-wrap:wrap;gap:8px">
              <span class="name" style="flex:1 1 100%;color:var(--text)">${esc(c.name)}</span>
              <div class="progress" style="flex:1;min-width:120px"><i style="width:${pct(c.indexed, c.docs)}%"></i></div>
              <span class="num">${num(c.indexed)}/${num(c.docs)}</span>
              <span class="tail">${esc(c.updated)}</span>
            </div>`).join('')}
        </div>
        <div class="card-sub">论文笔记集合仍有 ${num(state.collections[2].docs - state.collections[2].indexed)} 篇待索引，已积压 38 天。</div>
      </article>

      <article class="card">
        <div class="card-head">
          <h2 class="card-title">检索测试</h2>
          <span class="card-sub">混合检索</span>
        </div>
        <form class="search-box" id="retrievalForm">
          <input id="retrievalInput" placeholder="问点什么，比如：分块策略怎么选？" value="${esc(state.query)}" />
          <button class="pill primary" type="submit" style="height:38px">检索</button>
        </form>
        <div class="rows" style="gap:8px">
          ${state.hits.map((h) => `
            <div class="hit">
              <div class="hit-top">
                <span class="hit-title">${esc(h.doc)}</span>
                <span class="hit-score">${h.score.toFixed(2)}</span>
              </div>
              <div class="hit-snip">${esc(h.snippet)}</div>
              <div class="hit-src">${esc(h.source)}</div>
            </div>`).join('')}
        </div>
      </article>
    </section>

    <article class="card">
      <div class="card-head">
        <h2 class="card-title">索引任务</h2>
        <span class="card-sub">队列</span>
      </div>
      <div class="rows">
        <div class="row"><span class="name">论文笔记 · 补齐 36 篇增量索引</span>${badge('running')}<span class="tail">预计 4m</span></div>
        <div class="row"><span class="name">代码片段 · 重新分块（chunk 512 → 语义分块）</span>${badge('queued')}<span class="tail">排队中</span></div>
        <div class="row"><span class="name">技术文档 · 全量重建向量</span>${badge('done')}<span class="tail">今天 09:40</span></div>
      </div>
    </article>`;
  }

  /* ---------- 视图：模型中心（真实数据：/api/models ← apps/p02/.env） ---------- */
  function viewModels() {
    const ms = state.models || [];
    const keyed = ms.filter((m) => m.key_set).length;
    return `
    <section class="strip">
      <div class="stat"><b>${ms.length}</b><span>接入厂商</span></div>
      <div class="stat"><b>${keyed}/${ms.length}</b><span>已配 API Key</span></div>
      <div class="stat"><b>${ms.filter((m) => m.model && m.model !== '—').length}</b><span>生效模型</span></div>
      <div class="stat"><b>${esc((ms.find((m) => m.active) || {}).vendor || '—')}</b><span>当前默认</span></div>
    </section>

    <article class="card">
      <div class="card-head">
        <h2 class="card-title">接入模型</h2>
        <span class="card-sub">${esc((state.modelsSource && state.modelsSource) || '读取中…')}</span>
      </div>
      <div class="model-row" style="font-size:10.5px;color:var(--dim);font-family:var(--mono);padding-top:0">
        <span>生效模型</span><span>供应商</span><span>接入点</span><span>Key</span><span>可选模型</span><span>状态</span>
      </div>
      ${ms.length ? ms.map((m) => `
        <div class="model-row">
          <span class="model-name">${esc(m.model || '—')}</span>
          <span class="model-vendor">${esc(m.vendor)}</span>
          <span class="model-metric" title="${esc(m.base_url)}">${esc((m.base_url || '').replace(/^https?:\/\//, '').replace(/\/v\d.*$/, '') || '—')}</span>
          <span class="model-metric">${m.key_set ? '<span style="color:var(--teal)">已配置</span>' : '<span style="color:#FF4D6D">未配置</span>'}</span>
          <span class="model-metric">${(m.options || []).length ? esc((m.options || []).slice(0, 3).join(' · ')) + ((m.options.length > 3) ? ` 等 ${m.options.length} 个` : '') : '—'}</span>
          ${m.active ? '<span class="badge ok">默认厂商</span>' : (m.key_set ? badge('online', '可用') : badge('muted', '待接入'))}
        </div>`).join('') : '<div class="model-row"><span class="model-vendor">读取真实模型配置中…</span></div>'}
    </article>

    ${(() => {
      // 向量化是独立于生成的一条通道：这里显示知识库真正在用的 embedding 模型与维度
      const kb = state.kbStatus;
      const ok = !!(kb && kb.ok);
      const dim = (kb && (kb.dim || kb.embed_dim)) || 1024;
      const cnt = ok ? `${kb.documents} 文档 / ${kb.chunks} 块` : '—';
      return `
      <article class="card">
        <div class="card-head">
          <h2 class="card-title">Embedding 通道</h2>
          <span class="card-sub">知识库（pgvector）的向量化链路，与上面的生成通道相互独立</span>
        </div>
        <div class="rows">
          <div class="row">
            <span class="name">Provider</span>
            <span class="tail mono">${esc(ok ? (kb.provider || '—') : '未就绪')}${ok ? badge('online', '已连接') : badge('muted', '容器未起')}</span>
          </div>
          <div class="row">
            <span class="name">向量模型</span>
            <span class="tail mono">${esc(ok ? (kb.model || '—') : '—')}</span>
          </div>
          <div class="row">
            <span class="name">向量维度</span>
            <span class="tail"><b>${dim}</b> 维 · 与表结构 <span class="mono">VECTOR(${dim})</span> 一致</span>
          </div>
          <div class="row">
            <span class="name">库内规模</span>
            <span class="tail">${esc(cnt)}${ok && kb.extensions ? ' · ' + esc((kb.extensions || []).join(' + ')) : ''}</span>
          </div>
        </div>
        <div class="card-sub">
          <b>关于 1024：</b>它由请求参数 <span class="mono">dimensions</span> 指定，不是任何厂商的强制规格。
          DeepSeek 开放 API <b>没有 /embeddings 端点</b>，产不出向量——「vector(1024) 匹配 DeepSeek embed」是网上教程的误传
          （那是它某些生成模型的隐藏层维度）。换 embedding 模型必须 <span class="mono">init --rebuild</span> 清库重灌，向量空间不可混用。
        </div>
      </article>`;
    })()}

    <section class="grid-2">
      <article class="card">
        <div class="card-head"><h2 class="card-title">厂商接入点</h2><span class="card-sub">文案生成 Agent 实际调用的地址</span></div>
        <div class="rows">
          ${ms.map((m) => `
          <div class="row" style="flex-wrap:wrap">
            <span class="name" style="flex:0 0 auto">${esc(m.vendor)}${m.active ? ' <span class="badge ok">默认</span>' : ''}</span>
            <span class="tail mono" style="flex:1;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(m.base_url || '未设置')}</span>
            <span class="num" style="color:${m.key_set ? 'var(--teal)' : '#FF4D6D'}">${m.key_set ? 'Key ✓' : 'Key ✗'}</span>
          </div>`).join('')}
        </div>
        <div class="card-sub">Key 保存在 apps/p02/.env，界面只显示是否已配置，不展示明文。生成文案时在「AI 应用」页可临时切换厂商。</div>
      </article>
      <article class="card">
        <div class="card-head"><h2 class="card-title">调用链路</h2><span class="card-sub">谁在用什么模型</span></div>
        <div class="rows">
          <div class="row"><span class="name">文案生成</span><span class="tail">${esc((ms.find((m) => m.active) || {}).model || '—')}（${esc((ms.find((m) => m.active) || {}).vendor || '—')}）</span></div>
          <div class="row"><span class="name">任务拆分 / 评审 / 汇总</span><span class="tail">agentflow · 未配 LLM_API_KEY（mock）</span></div>
          <div class="row"><span class="name">热点分析</span><span class="tail">脚本抓取 + Agent 方法论</span></div>
          <div class="row"><span class="name">知识库检索</span><span class="tail">${esc((state.kbStatus && state.kbStatus.ok ? state.kbStatus.provider + ' · ' + (state.kbStatus.model || '') : '未就绪'))}（向量通道，与生成通道独立）</span></div>
        </div>
        <div class="card-sub">给 agentflow 配上 LLM_API_KEY 后，编排拆分与评审即切换为真实模型调用。</div>
      </article>
    </section>`;
  }

  /* ===========================================================
     协作监控：Run 列表 + Run 详情（泳道时间轴 / 消息总线 / HITL）
     =========================================================== */
  const RUN_STATUS = {
    running: ['运行中', 'running'],
    blocked: ['需人工', 'failed'],
    done: ['已完成', 'done'],
    failed: ['失败', 'failed'],
    // 进程没了但没跑完：不能落进默认的「运行中」，那是最误导人的一种显示
    interrupted: ['已中断', 'failed'],
  };
  const FORM_TEXT = { fanout: '扇出', chain: '流水线', loop: '循环' };
  const MSG_TEXT = { delegate: '委派', result: '返回', challenge: '质询', error: '错误' };
  const AGENT_COLOR = { done: '#13DDC4', running: '#2EA7FF', blocked: '#FF4D6D', failed: '#FF4D6D', idle: '#5B5878' };

  const fmtT = (s) => {
    const m = Math.floor(s / 60);
    return m + ':' + String(Math.round(s % 60)).padStart(2, '0');
  };
  const runBadge = (s) => {
    const m = RUN_STATUS[s] || RUN_STATUS.running;
    return badge(m[1], m[0]);
  };
  const allAlerts = () => state.runs.flatMap((r) => (r.alerts || []).map((a) => ({ ...a, run: r.id, runName: r.name })));

  function alertBar(limit = 3) {
    const list = allAlerts();
    if (!list.length) return '';
    return `<section class="alert-bar">
      <span class="alert-title">异常优先</span>
      ${list.slice(0, limit).map((a) => `
        <button class="alert-item ${a.level}" data-run="${a.run}">
          <span class="adot ${a.level === 'bad' ? 'blocked' : 'idle'}"></span>
          <span>${esc(a.text)}</span>
          <span class="alert-go">定位 ↗</span>
        </button>`).join('')}
    </section>`;
  }

  /* ---------- 视图：协作监控（Run 列表） ---------- */
  const ACT_TEXT = { generate: '生成文案', chat: '岗位采集分析', collect: '热点抓取' };
  const ACT_BADGE = {
    running: ['running', '运行中'],
    done: ['done', '已完成'],
    failed: ['failed', '失败'],
    lost: ['failed', '已中断'],
  };
  const fmtDur = (ms) => {
    const s = Math.round((ms || 0) / 1000);
    return s < 60 ? s + 's' : Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0');
  };

  function extActCard() {
    const acts = state.extActs || [];
    const rows = acts.slice(0, 12).map((a) => {
      const [cls, txt] = ACT_BADGE[a.state] || ACT_BADGE.running;
      const dur = a.state === 'running'
        ? '<span style="color:var(--cyan)">' + fmtDur(Date.now() - a.ts * 1000) + ' …</span>'
        : fmtDur(a.duration_ms);
      const body = a.state === 'failed' || a.state === 'lost'
        ? (a.error || '无响应') + (a.note ? `<div class="mono" style="color:var(--dim)">触发：${esc(a.note)}</div>` : '')
        : (a.summary || a.note || '—');
      return `<tr>
        <td class="name">${esc(a.app)}<div class="mono">${esc(ACT_TEXT[a.action] || a.action)}</div></td>
        <td style="max-width:420px">${esc(String(body)).slice(0, 160)}${a.saved ? ` <a class="link" href="/api/file?path=${encodeURIComponent(a.saved)}" target="_blank">存档 ↗</a>` : ''}</td>
        <td class="mono">${dur}</td>
        <td class="mono">${new Date(a.ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</td>
        <td>${badge(cls, txt)}</td>
      </tr>`;
    }).join('');
    return `
    <article class="card">
      <div class="card-head">
        <h2 class="card-title">AI 应用活动</h2>
        <span class="card-sub">内嵌 Agent（文案 / 招聘 / 大盘 / 热点）的真实调用 · 约 5s 刷新</span>
      </div>
      ${acts.length ? `
      <table class="table">
        <thead><tr><th>应用 / 动作</th><th>内容</th><th>耗时</th><th>时间</th><th>状态</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>` : `
      <p class="card-sub" style="padding:6px 0 2px">还没有应用调用记录。去「AI 应用」页生成文案、采集岗位或抓热点，
      每次真实调用的开始、结束、耗时和结论都会实时出现在这里。</p>`}
    </article>`;
  }

  function viewRuns() {
    const acts = state.extActs || [];
    const actRunning = acts.filter((a) => a.state === 'running').length;
    const runs = state.runs.filter((r) => state.runFilter === 'all' || r.status === state.runFilter);
    const active = state.runs.filter((r) => r.status === 'running' || r.status === 'blocked').length;
    const blockedAgents = state.runs.reduce((a, r) => a + r.agents.filter((x) => x.status === 'blocked').length, 0);
    const depth = (state.runs.reduce((a, r) => a + r.depth, 0) / state.runs.length).toFixed(1);
    const cost = state.runs.reduce((a, r) => a + r.cost, 0);
    const totalAgents = state.runs.reduce((a, r) => a + r.agents.length, 0);
    const filters = [['all', '全部'], ['running', '运行中'], ['blocked', '需人工'], ['done', '已完成'], ['failed', '失败']];

    return `
    ${alertBar()}

    <section class="strip">
      <div class="stat"><b>${active}</b><span>活跃 Run</span></div>
      <div class="stat"><b style="color:${blockedAgents ? 'var(--rose)' : 'inherit'}">${blockedAgents}</b><span>阻塞中的 Agent</span></div>
      <div class="stat"><b>${depth}</b><span>平均委派深度 · 共 ${totalAgents} 个实例</span></div>
      <div class="stat"><b>${acts.length}${actRunning ? `<span style="color:var(--cyan)"> +${actRunning} 运行中</span>` : ''}</b><span>AI 应用调用</span></div>
      <div class="stat"><b>¥${cost.toFixed(2)}</b><span>今日协作成本</span></div>
    </section>

    <article class="card">
      <div class="card-head">
        <h2 class="card-title">协作 Run</h2>
        <div class="chips">
          ${filters.map(([k, t]) => `<button class="chip ${state.runFilter === k ? 'is-active' : ''}" data-run-filter="${k}">${t}</button>`).join('')}
        </div>
      </div>
      <table class="table">
        <thead><tr><th>Run</th><th>形态</th><th>Agent</th><th>深度</th><th>耗时</th><th>成本</th><th>状态</th></tr></thead>
        <tbody>
          ${runs.map((r) => {
            const bad = r.agents.filter((a) => a.status === 'blocked' || a.status === 'failed').length;
            return `<tr data-run="${r.id}">
              <td class="name">${esc(r.name)}<div class="mono">${esc(r.id)} · ${esc(r.startedAt)}</div></td>
              <td>${esc(FORM_TEXT[r.form] || r.form)}</td>
              <td class="mono">${r.agents.length} 个${bad ? ` · <span style="color:var(--rose)">${bad} 异常</span>` : ''}</td>
              <td class="mono">${r.depth} 层</td>
              <td class="mono">${esc(r.durationText)}</td>
              <td class="mono">¥${r.cost.toFixed(2)}</td>
              <td>${runBadge(r.status)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
      ${runs.length ? '' : '<p class="card-sub">该筛选下暂无 Run。</p>'}
    </article>

    ${extActCard()}`;
  }

  /* ---------- 泳道时间轴 ---------- */
  function laneChart(r) {
    const W = 1000, LABEL = 142, TOP = 34, LH = 50;
    const H = TOP + r.agents.length * LH + 26;
    const T = Math.max(r.durationSec || 1, 1);
    const x = (t) => LABEL + (Math.min(t, T) / T) * (W - LABEL - 30);
    const rowY = (i) => TOP + i * LH;
    const midY = (i) => rowY(i) + 20;
    const selA = state.selAgent;
    const selM = state.selMsg;

    let svg = `<defs>
      <marker id="arw-cyan" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#2EA7FF"/></marker>
      <marker id="arw-amber" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#FFB454"/></marker>
      <marker id="arw-rose" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#FF4D6D"/></marker>
    </defs>`;

    /* 时间刻度 */
    for (let k = 0; k <= 4; k++) {
      const t = (T * k) / 4;
      svg += `<line x1="${x(t).toFixed(1)}" y1="${TOP - 10}" x2="${x(t).toFixed(1)}" y2="${H - 22}" stroke="rgba(255,255,255,.06)"/>
        <text x="${x(t).toFixed(1)}" y="${H - 8}" fill="#6E6C8F" font-family="JetBrains Mono" font-size="9" text-anchor="middle">${fmtT(t)}</text>`;
    }

    /* 泳道 */
    r.agents.forEach((a, i) => {
      const y = rowY(i);
      const sx = x(a.start);
      const ex = x(a.end);
      const bw = Math.max(ex - sx, a.status === 'idle' ? 4 : 8);
      const color = AGENT_COLOR[a.status] || AGENT_COLOR.idle;
      const dim = selA && selA !== a.id ? 0.32 : 1;
      const isIdle = a.status === 'idle';
      svg += `<g class="lane-g" data-agent="${a.id}" opacity="${dim}">
        <title>${esc(a.name)} · ${esc(a.role)} · ${esc(a.model)}</title>
        <rect class="lane-bg" x="${LABEL}" y="${y}" width="${W - LABEL - 30}" height="${LH - 10}" rx="9" fill="rgba(255,255,255,.018)"/>
        <text x="0" y="${y + 17}" fill="#FFFFFF" font-family="Inter,Noto Sans SC" font-size="12">${esc(a.name)}</text>
        <text x="0" y="${y + 32}" fill="#6E6C8F" font-family="JetBrains Mono" font-size="9.5">${esc(a.role)} · ${esc(a.model)}</text>
        ${isIdle
          ? `<rect x="${sx}" y="${y + 10}" width="4" height="20" rx="2" fill="${color}" opacity=".5"/>`
          : `<rect x="${sx.toFixed(1)}" y="${y + 8}" width="${bw.toFixed(1)}" height="24" rx="6" fill="${color}" fill-opacity="${a.status === 'running' ? '.30' : '.20'}" stroke="${color}" stroke-width="1.2"${a.status === 'blocked' ? ' stroke-dasharray="5 3"' : ''}/>`}
        <text x="${(sx + bw + 8).toFixed(1)}" y="${y + 25}" fill="${color}" font-family="JetBrains Mono" font-size="9.5" opacity=".9">${esc(a.tokens)}</text>
      </g>`;
    });

    /* 消息边 */
    r.messages.forEach((m) => {
      const i = r.agents.findIndex((a) => a.id === m.from);
      const j = r.agents.findIndex((a) => a.id === m.to);
      if (i < 0 || j < 0) return;
      const x1 = x(m.t);
      const y1 = midY(i);
      const x2 = x1 + 30;
      const y2 = midY(j);
      const color = m.kind === 'challenge' ? '#FFB454' : m.kind === 'error' ? '#FF4D6D' : '#2EA7FF';
      const mk = m.kind === 'challenge' ? 'arw-amber' : m.kind === 'error' ? 'arw-rose' : 'arw-cyan';
      const isSel = selM === m.id;
      const dim = selM && !isSel ? 0.25 : 1;
      svg += `<g class="msg-g" data-msg="${m.id}" opacity="${dim}">
        <title>${esc(m.label)}</title>
        <path d="M${x1.toFixed(1)} ${y1} C${(x1 + 18).toFixed(1)} ${y1}, ${(x2 - 18).toFixed(1)} ${y2}, ${x2.toFixed(1)} ${y2}"
          fill="none" stroke="${color}" stroke-width="${isSel ? 2.2 : 1.3}" marker-end="url(#${mk})" opacity="${isSel ? 1 : .85}"/>
        <circle cx="${x1.toFixed(1)}" cy="${y1}" r="3" fill="${color}"/>
        <circle cx="${((x1 + x2) / 2).toFixed(1)}" cy="${((y1 + y2) / 2).toFixed(1)}" r="${isSel ? 5.5 : 3.6}" fill="${color}" opacity=".9"/>
      </g>`;
    });

    /* 进行中的时间指针 */
    if (r.status === 'running' || r.status === 'blocked') {
      svg += `<line x1="${x(T).toFixed(1)}" y1="${TOP - 10}" x2="${x(T).toFixed(1)}" y2="${H - 22}" stroke="#2EA7FF" stroke-width="1" stroke-dasharray="3 3" opacity=".8"/>`;
    }

    return `<svg class="lane-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="协作泳道时间轴">${svg}</svg>`;
  }

  /* ---------- 视图：Run 详情 ---------- */
  function viewRun() {
    const r = state.runs.find((x) => x.id === state.runId) || state.runs[0];
    if (!r) return '<p class="card-sub">暂无 Run 数据。</p>';
    const selAgent = r.agents.find((a) => a.id === state.selAgent);
    const selMsg = r.messages.find((m) => m.id === state.selMsg);

    const agentRail = `<aside class="agent-rail">
      <p class="rail-label">参与 Agent · ${r.agents.length}</p>
      ${r.agents.map((a) => `
        <button class="agent-item ${state.selAgent === a.id ? 'is-sel' : ''}" data-agent="${a.id}">
          <span class="an"><i class="adot ${a.status}"></i>${esc(a.name)}<span class="tag-mini">${esc(a.role)}</span></span>
          <span class="am"><span>${esc(a.tokens)}</span><span>${a.calls} 调用</span>${a.retries ? `<span style="color:var(--amber)">重试 ${a.retries}</span>` : ''}</span>
        </button>`).join('')}
    </aside>`;

    const laneCard = `<article class="card lane-card">
      <div class="lane-head">
        <h2 class="card-title">协作泳道 · 消息总线</h2>
        <div class="lane-legend">
          <span><i style="background:#13DDC4"></i>完成</span>
          <span><i style="background:#2EA7FF"></i>进行</span>
          <span><i style="background:#FF4D6D"></i>阻塞</span>
          <span><i style="background:#FFB454"></i>质询</span>
        </div>
      </div>
      ${laneChart(r)}
      <p class="card-sub" style="font-family:var(--mono);font-size:10.5px">点击泳道看 Agent 详情 · 点击连线看消息原文</p>
    </article>`;

    let pane;
    if (selMsg) {
      const from = r.agents.find((a) => a.id === selMsg.from);
      const to = r.agents.find((a) => a.id === selMsg.to);
      pane = `<article class="card detail-pane">
        <div class="card-head">
          <h2 class="card-title">消息原文</h2>
          <button class="link" data-clear-sel>收起</button>
        </div>
        <div class="msg-card">
          <div class="msg-route"><em>${esc(from ? from.name : selMsg.from)}</em> → <em>${esc(to ? to.name : selMsg.to)}</em></div>
          <div class="card-sub" style="font-family:var(--mono)">${esc(MSG_TEXT[selMsg.kind] || selMsg.kind)} · T+${fmtT(selMsg.t)}</div>
          <div style="font-size:12.5px;color:var(--text);line-height:1.6">${esc(selMsg.label)}</div>
        </div>
        <div>
          <p class="section-label">payload</p>
          <pre class="payload">${esc(selMsg.payload)}</pre>
        </div>
      </article>`;
    } else if (selAgent) {
      pane = `<article class="card detail-pane">
        <div class="card-head">
          <h2 class="card-title">${esc(selAgent.name)}</h2>
          <button class="link" data-clear-sel>收起</button>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <i class="adot ${selAgent.status}"></i>
          <span class="card-sub" style="font-family:var(--mono)">${esc(selAgent.role)} · ${esc(selAgent.model)}</span>
        </div>
        <dl class="kv">
          <dt>时间窗</dt><dd style="font-family:var(--mono)">${fmtT(selAgent.start)} – ${selAgent.status === 'running' ? '进行中' : fmtT(selAgent.end)}</dd>
          <dt>Token</dt><dd style="font-family:var(--mono)">${esc(selAgent.tokens)}</dd>
          <dt>成本</dt><dd style="font-family:var(--mono)">¥${selAgent.cost.toFixed(2)}</dd>
          <dt>工具调用</dt><dd style="font-family:var(--mono)">${selAgent.calls} 次</dd>
          <dt>重试</dt><dd style="font-family:var(--mono);color:${selAgent.retries ? 'var(--amber)' : 'inherit'}">${selAgent.retries} 次</dd>
        </dl>
        ${selAgent.note ? `<div class="suggest-item ${selAgent.status === 'blocked' || selAgent.status === 'failed' ? 'critical' : ''}"><div class="suggest-body">${esc(selAgent.note)}</div></div>` : ''}
        <div>
          <p class="section-label">消息往来</p>
          <div class="rows" style="margin-top:6px">
            ${r.messages.filter((m) => m.from === selAgent.id || m.to === selAgent.id).map((m) => `
              <button class="row" data-msg="${m.id}" style="text-align:left;width:100%">
                <span class="num">${fmtT(m.t)}</span>
                <span class="name" style="font-size:11.5px">${esc(MSG_TEXT[m.kind] || '')} · ${esc(m.label)}</span>
              </button>`).join('') || '<p class="card-sub">暂无消息</p>'}
          </div>
        </div>
      </article>`;
    } else {
      pane = `<article class="card detail-pane">
        <div class="card-head"><h2 class="card-title">Run 概览</h2></div>
        <p style="font-size:12.5px;color:var(--muted);line-height:1.7;margin:0">${esc(r.goal)}</p>
        <dl class="kv">
          <dt>委派深度</dt><dd style="font-family:var(--mono)">${r.depth} 层</dd>
          <dt>总 Token</dt><dd style="font-family:var(--mono)">${esc(r.tokens)}</dd>
          <dt>总成本</dt><dd style="font-family:var(--mono)">¥${r.cost.toFixed(2)}</dd>
          <dt>形态</dt><dd>${esc(FORM_TEXT[r.form] || r.form)}</dd>
        </dl>
        <div>
          <p class="section-label">告警</p>
          <div class="suggest" style="margin-top:8px">
            ${(r.alerts || []).length
              ? r.alerts.map((a) => `<div class="suggest-item ${a.level === 'bad' ? 'critical' : 'warn'}"><div class="suggest-body">${esc(a.text)}</div></div>`).join('')
              : '<p class="card-sub">本次 Run 无告警。</p>'}
          </div>
        </div>
        <div>
          <p class="section-label">成本归因</p>
          <div class="rows" style="margin-top:6px">
            ${r.agents.filter((a) => a.cost > 0).sort((a, b) => b.cost - a.cost).map((a) => `
              <div class="row">
                <span class="name" style="font-size:12px">${esc(a.name)}</span>
                <div class="progress" style="flex:1;min-width:60px"><i style="width:${(a.cost / r.cost) * 100}%"></i></div>
                <span class="num">¥${a.cost.toFixed(2)}</span>
              </div>`).join('')}
          </div>
        </div>
      </article>`;
    }

    const canAct = r.status === 'blocked' || r.status === 'running';
    return `
    <div class="run-head">
      <button class="back-btn" data-back-runs>← 协作监控</button>
      <div class="run-title-wrap">
        <h2 class="run-title">${esc(r.name)}</h2>
        <div class="run-meta">
          ${runBadge(r.status)}
          <span>${esc(r.id)}</span>
          <span>${esc(FORM_TEXT[r.form] || r.form)} · 深度 ${r.depth}</span>
          <span>${esc(r.durationText)}</span>
          <span>¥${r.cost.toFixed(2)} · ${esc(r.tokens)}</span>
        </div>
      </div>
      <div class="run-actions">
        ${canAct ? `<button class="pill primary" data-hitl="approve">批准继续</button>
        <button class="pill ghost" data-hitl="context">补充上下文</button>
        <button class="pill ghost" data-hitl="abort">终止 Run</button>` : `<button class="pill ghost" data-toast="已导出本次 Run 记录（JSON）">导出记录</button>
        <button class="pill ghost" data-toast="已复制 Run ID：${esc(r.id)}">复制 ID</button>`}
      </div>
    </div>
    <section class="run-grid">
      ${agentRail}
      ${laneCard}
      ${pane}
    </section>`;
  }

  const VIEWS = {
    overview: viewOverview, runs: viewRuns, run: viewRun, agents: viewAgents, apps: viewApps,
    knowledge: viewKnowledge, models: viewModels,
    /* 协作流水线三页（pipeline.js 注册，数据来自 agentflow 后端） */
    ...((window.AF && window.AF.views) || {}),
  };

  /* ---------- 渲染 ---------- */
  function render() {
    const meta = VIEW_META[state.view] || VIEW_META.overview;
    const r = state.view === 'run' ? state.runs.find((x) => x.id === state.runId) : null;
    $('#pageTitle').textContent = r ? r.name : meta.title;
    $('#pageSub').textContent = r
      ? `Run 详情 · ${FORM_TEXT[r.form] || ''} · 深度 ${r.depth} · ${r.agents.length} 个 Agent`
      : meta.sub;
    $('#content').innerHTML = VIEWS[state.view]();
    if (window.AF && typeof window.AF.afterRender === 'function') window.AF.afterRender();
    const navKey = state.view === 'run' ? 'runs' : state.view;
    $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.view === navKey));
    $('#badge-agents').textContent = state.tasks.filter((t) => t.status === 'running').length;
    const activeRuns = state.runs.filter((x) => x.status === 'running' || x.status === 'blocked').length;
    const badgeRuns = $('#badge-runs');
    if (badgeRuns) badgeRuns.textContent = activeRuns;
    document.title = (r ? r.name : meta.title) + ' · AI 工作台';
  }

  function setView(v, push = true) {
    if (!VIEWS[v] || v === 'run') return;
    state.view = v;
    state.selAgent = null;
    state.selMsg = null;
    if (push) history.replaceState(null, '', '#' + v);
    render();
    $('#content').scrollTop = 0;
  }

  function openRun(id, push = true) {
    if (!state.runs.some((x) => x.id === id)) return;
    state.runId = id;
    state.view = 'run';
    state.selAgent = null;
    state.selMsg = null;
    if (push) history.replaceState(null, '', '#run/' + id);
    render();
    $('#content').scrollTop = 0;
  }

  /* ---------- 详情抽屉 ---------- */
  function openTask(id) {
    const t = state.tasks.find((x) => x.id === id);
    if (!t) return;
    const steps = t.tools.length
      ? t.tools.map((tool, i) => {
        const done = t.status === 'done' || (t.status === 'running' && i < Math.ceil(t.tools.length * (t.progress / 100)));
        const bad = t.status === 'failed' && i === t.tools.length - 1;
        return `<div class="tl-item ${bad ? 'bad' : done ? 'done' : ''}">
          <b style="color:var(--text);font-weight:500">${esc(tool)}</b>
          <div style="font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:2px">${bad ? '超时重试 3 次后中断' : done ? 'ok · ' + (120 + i * 37) + 'ms' : '等待中'}</div>
        </div>`;
      }).join('')
      : '<p class="card-sub">任务尚未开始，暂无工具调用记录。</p>';

    $('#drawer').innerHTML = `
      <div class="drawer-head">
        <div>
          <div class="drawer-id">${esc(t.id)} · ${esc(t.module)}</div>
          <h2 class="drawer-title">${esc(t.name)}</h2>
        </div>
        <button class="drawer-close" data-close-drawer aria-label="关闭">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 2L12 12M12 2L2 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </button>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${badge(t.status)}
        <span class="card-sub" style="font-family:var(--mono)">${esc(t.createdAt)}</span>
      </div>
      <dl class="kv">
        <dt>执行者</dt><dd>${esc(t.owner)}</dd>
        <dt>步数</dt><dd>${num(t.steps)}</dd>
        <dt>耗时</dt><dd style="${/已卡/.test(t.elapsed) ? 'color:var(--rose)' : ''}">${esc(t.elapsed)}</dd>
        <dt>进度</dt><dd>${t.progress}%</dd>
      </dl>
      <div>
        <p class="section-label">任务说明</p>
        <p style="font-size:12.5px;color:var(--muted);line-height:1.7;margin:6px 0 0">${esc(t.summary)}</p>
      </div>
      <div>
        <p class="section-label">工具调用</p>
        <div class="timeline" style="margin-top:10px">${steps}</div>
      </div>
      <div style="display:flex;gap:8px;margin-top:auto;padding-top:16px">
        <button class="pill primary" style="height:34px" data-toast="已重新排队：${esc(t.name)}">重跑任务</button>
        <button class="pill ghost" data-toast="已复制任务 ID">复制 ID</button>
      </div>`;
    $('#drawerOverlay').hidden = false;
  }

  function openCreateAgent() {
    $('#drawer').innerHTML = `
      <div class="drawer-head">
        <div>
          <div class="drawer-id">NEW · AGENT</div>
          <h2 class="drawer-title">新建 Agent</h2>
        </div>
        <button class="drawer-close" data-close-drawer aria-label="关闭">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 2L12 12M12 2L2 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </button>
      </div>
      <div style="display:flex;flex-direction:column;gap:12px">
        <label class="section-label">名称
          <input class="search-box" style="width:100%;height:38px;margin-top:6px;padding:0 14px;border-radius:12px;background:rgba(255,255,255,.04);border:1px solid var(--stroke);color:#fff;outline:none" value="竞品情报官" />
        </label>
        <label class="section-label">模型
          <select class="search-box" style="width:100%;height:38px;margin-top:6px;padding:0 12px;border-radius:12px;background:#131038;border:1px solid var(--stroke);color:#fff;outline:none">
            ${DB.meta.models.map((m) => `<option>${m}</option>`).join('')}
          </select>
        </label>
        <div>
          <p class="section-label" style="margin-bottom:6px">可用工具</p>
          <div>${['browser', 'file', 'vector.search', 'shell', 'sim'].map((t) => `<span class="tag">${t}</span>`).join('')}</div>
        </div>
        <div>
          <p class="section-label" style="margin-bottom:6px">触发方式</p>
          <div>${['手动', '定时 09:00', 'Webhook'].map((t, i) => `<span class="tag" style="${i === 0 ? 'background:rgba(46,167,255,.16);color:#BFE2FF' : ''}">${t}</span>`).join('')}</div>
        </div>
      </div>
      <div style="display:flex;gap:8px;margin-top:auto;padding-top:16px">
        <button class="pill primary" style="height:34px" data-create-agent>创建并运行</button>
        <button class="pill ghost" style="height:34px" data-close-drawer>取消</button>
      </div>`;
    $('#drawerOverlay').hidden = false;
  }

  function closeDrawer() { $('#drawerOverlay').hidden = true; }

  /* ---------- Toast ---------- */
  function toast(text, kind = 'ok') {
    const box = document.createElement('div');
    box.className = 'toast ' + kind;
    box.innerHTML = `<span class="dot ${kind === 'warn' ? 'dot-amber' : 'dot-teal'}"></span><span>${esc(text)}</span>`;
    $('#toasts').appendChild(box);
    setTimeout(() => {
      box.style.transition = 'opacity .3s, transform .3s';
      box.style.opacity = '0';
      box.style.transform = 'translateY(6px)';
      setTimeout(() => box.remove(), 320);
    }, 2600);
  }

  /* ---------- 命令面板 ---------- */
  function paletteItems() {
    const navs = NAV_KEYS.map((k, i) => ({
      group: '跳转', label: VIEW_META[k].title, hint: VIEW_META[k].sub, icon: k === 'runs' ? 'agent' : 'grid',
      run: () => setView(k), key: i < 8 ? String(i + 1) : '',
    }));
    const runItems = state.runs.map((r) => ({
      group: '协作 Run',
      label: r.name,
      hint: `${r.id} · ${FORM_TEXT[r.form] || ''} · ${r.agents.length} 个 Agent`,
      icon: 'agent',
      run: () => openRun(r.id),
    }));
    const actions = [
      { group: '操作', label: '新建 Agent', hint: '创建并运行一个任务', icon: 'plus', run: openCreateAgent },
      { group: '操作', label: '上传文档到知识库', hint: '支持 md / pdf / url', icon: 'file', run: () => toast('已打开文档上传面板') },
      { group: '操作', label: '检索知识库', hint: '混合检索 + 引用溯源', icon: 'search', run: () => setView('knowledge') },
      { group: '操作', label: '刷新数据', hint: '重新拉取任务与用量', icon: 'refresh', run: () => { render(); toast('数据已刷新'); } },
    ];
    const tasks = state.tasks.map((t) => ({
      group: '任务', label: t.name, hint: t.id + ' · ' + t.module, icon: 'agent', run: () => openTask(t.id),
    }));
    return [...navs, ...actions, ...runItems, ...tasks];
  }

  const ICONS = {
    grid: '<path d="M3 3h5v5H3zM10 3h5v5h-5zM3 10h5v5H3zM10 10h5v5h-5z" stroke="currentColor" stroke-width="1.4" fill="none"/>',
    plus: '<path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    file: '<path d="M4 2h5l3 3v9H4z" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M9 2v3h3" stroke="currentColor" stroke-width="1.4"/>',
    robot: '<rect x="4" y="6" width="8" height="6" rx="2" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M8 6V3" stroke="currentColor" stroke-width="1.4"/>',
    search: '<circle cx="7" cy="7" r="4.5" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    refresh: '<path d="M13 8a5 5 0 1 1-1.6-3.7M13 2v3h-3" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/>',
    agent: '<circle cx="8" cy="4" r="2.2" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="4" cy="13" r="2.2" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="12" cy="13" r="2.2" stroke="currentColor" stroke-width="1.4" fill="none"/>',
  };

  function renderPalette() {
    const q = state.paletteQuery.trim().toLowerCase();
    const all = paletteItems();
    const items = q ? all.filter((i) => (i.label + ' ' + (i.hint || '')).toLowerCase().includes(q)) : all;
    state.paletteIndex = Math.min(state.paletteIndex, Math.max(items.length - 1, 0));

    if (!items.length) {
      $('#paletteList').innerHTML = '<p class="palette-group">没有匹配的命令，换个关键词试试。</p>';
      return;
    }
    let html = '';
    let lastGroup = '';
    items.forEach((it, i) => {
      if (it.group !== lastGroup) { html += `<p class="palette-group">${it.group}</p>`; lastGroup = it.group; }
      html += `<button class="palette-item ${i === state.paletteIndex ? 'is-active' : ''}" data-palette-index="${i}">
        <svg viewBox="0 0 16 16" fill="none">${ICONS[it.icon] || ICONS.grid}</svg>
        <span>${esc(it.label)}</span>
        <span class="key">${esc(it.key || it.hint || '')}</span>
      </button>`;
    });
    $('#paletteList').innerHTML = html;
    state.paletteResults = items;
  }

  function openPalette() {
    state.paletteOpen = true;
    state.paletteQuery = '';
    state.paletteIndex = 0;
    $('#palette').hidden = false;
    $('#paletteInput').value = '';
    renderPalette();
    setTimeout(() => $('#paletteInput').focus(), 30);
  }

  function closePalette() {
    state.paletteOpen = false;
    $('#palette').hidden = true;
  }

  function runPaletteItem(index) {
    const items = state.paletteResults || [];
    const item = items[index];
    if (!item) return;
    closePalette();
    item.run();
  }

  /* ---------- 事件绑定 ---------- */
  function bind() {
    document.addEventListener('click', (e) => {
      const navBtn = e.target.closest('.nav-item');
      if (navBtn) { setView(navBtn.dataset.view); return; }

      const navLink = e.target.closest('[data-nav]');
      if (navLink) { setView(navLink.dataset.nav); return; }

      /* 协作监控 */
      const runFilter = e.target.closest('[data-run-filter]');
      if (runFilter) { state.runFilter = runFilter.dataset.runFilter; render(); return; }

      if (e.target.closest('[data-back-runs]')) { setView('runs'); return; }
      if (e.target.closest('[data-clear-sel]')) { state.selAgent = null; state.selMsg = null; render(); return; }

      const hitl = e.target.closest('[data-hitl]');
      if (hitl) {
        const r = state.runs.find((x) => x.id === state.runId);
        const act = hitl.dataset.hitl;
        if (r && act === 'approve') {
          r.status = 'running';
          r.agents.forEach((a) => { if (a.status === 'blocked') { a.status = 'running'; a.note = '已获人工批准，继续执行'; } });
          r.alerts = (r.alerts || []).filter((a) => !/人工/.test(a.text));
          render();
          toast('已批准 · 阻塞的 Agent 恢复执行');
        } else if (r && act === 'abort') {
          r.status = 'failed';
          r.agents.forEach((a) => { if (a.status === 'running') a.status = 'failed'; });
          render();
          toast('Run 已终止，正在回收 Agent 实例', 'warn');
        } else {
          toast('已打开上下文补充面板：可粘贴数据源或覆盖指令');
        }
        return;
      }

      const msgEl = e.target.closest('[data-msg]');
      if (msgEl) {
        state.selMsg = state.selMsg === msgEl.dataset.msg ? null : msgEl.dataset.msg;
        state.selAgent = null;
        render();
        return;
      }

      const agentEl = e.target.closest('[data-agent]');
      if (agentEl) {
        state.selAgent = state.selAgent === agentEl.dataset.agent ? null : agentEl.dataset.agent;
        state.selMsg = null;
        render();
        return;
      }

      const runEl = e.target.closest('[data-run]');
      if (runEl) { openRun(runEl.dataset.run); return; }

      const taskEl = e.target.closest('[data-task]');
      if (taskEl) { openTask(taskEl.dataset.task); return; }

      const filter = e.target.closest('[data-filter]');
      if (filter) { state.taskFilter = filter.dataset.filter; render(); return; }

      const tst = e.target.closest('[data-toast]');
      if (tst) { toast(tst.dataset.toast); return; }

      if (e.target.closest('[data-close-drawer]')) { closeDrawer(); return; }
      if (e.target.closest('[data-create-agent]')) {
        closeDrawer();
        toast('Agent「竞品情报官」已创建，任务进入队列', 'ok');
        return;
      }
      if (e.target === $('#drawerOverlay')) { closeDrawer(); return; }
      if (e.target === $('#palette')) { closePalette(); return; }

      const palItem = e.target.closest('[data-palette-index]');
      if (palItem) { runPaletteItem(Number(palItem.dataset.paletteIndex)); return; }
    });

    $('#searchPill').addEventListener('click', openPalette);
    $('#ctaBtn').addEventListener('click', openCreateAgent);
    $('#modelPill').addEventListener('click', () => setView('models'));
    $('#userBtn').addEventListener('click', () => toast('个人工作空间：本地模式，数据存于浏览器'));

    $('#paletteInput').addEventListener('input', (e) => {
      state.paletteQuery = e.target.value;
      state.paletteIndex = 0;
      renderPalette();
    });

    $('#paletteInput').addEventListener('keydown', (e) => {
      const items = state.paletteResults || [];
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        state.paletteIndex = (state.paletteIndex + 1) % Math.max(items.length, 1);
        renderPalette();
        $$('.palette-item')[state.paletteIndex]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        state.paletteIndex = (state.paletteIndex - 1 + items.length) % Math.max(items.length, 1);
        renderPalette();
        $$('.palette-item')[state.paletteIndex]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        runPaletteItem(state.paletteIndex);
      }
    });

    document.addEventListener('keydown', (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        state.paletteOpen ? closePalette() : openPalette();
        return;
      }
      if (e.key === 'Escape') {
        if (state.paletteOpen) closePalette();
        else if (!$('#drawerOverlay').hidden) closeDrawer();
        return;
      }
      if (!typing && !state.paletteOpen && /^[1-8]$/.test(e.key)) {
        setView(NAV_KEYS[Number(e.key) - 1]);
      }
    });

    /* 检索测试 */
    document.addEventListener('submit', (e) => {
      if (e.target.id === 'retrievalForm') {
        e.preventDefault();
        const input = $('#retrievalInput');
        state.query = input ? input.value : '';
        const q = state.query.trim();
        state.hits = q
          ? DB.retrieval.map((h) => ({ ...h, score: Math.max(0.42, Math.min(0.98, h.score + (Math.random() - 0.5) * 0.06)) }))
          : DB.retrieval;
        render();
        if (q) toast('检索完成 · 返回 ' + state.hits.length + ' 条片段');
      }
    });
  }

  /* ---------- 任务推进（模拟实时） ---------- */
  function tick() {
    /* AI 应用活动轮询：挂在 1.2s 的 tick 上，每 4 拍（约 5s）拉一次 */
    tick.beat = (tick.beat || 0) + 1;
    if (tick.beat % 4 === 1 && state.view === 'runs') loadActs();
    let changed = false;
    let finished = null;
    state.tasks.forEach((t) => {
      if (t.status !== 'running') return;
      t.progress = Math.min(100, t.progress + 0.6 + Math.random() * 1.6);
      changed = true;
      if (t.progress >= 100) {
        t.status = 'done';
        t.elapsed = '刚刚完成';
        finished = t;
      }
    });
    if (finished) {
      render();
      toast('任务完成 · ' + finished.name);
      return;
    }
    if (changed) {
      state.tasks.forEach((t) => {
        const bar = document.querySelector('[data-bar="' + t.id + '"] i');
        if (bar && t.status === 'running') bar.style.width = t.progress.toFixed(0) + '%';
      });
      if (state.view === 'overview' || state.view === 'agents') {
        // 只在进度跨过阈值时整页刷新，避免频繁重排
        const runningPct = state.tasks.filter((t) => t.status === 'running').map((t) => Math.round(t.progress / 10)).join('');
        if (runningPct !== tick.lastKey) {
          tick.lastKey = runningPct;
          if (state.view === 'agents') render();
        }
      }
    }
  }

  /* ---------- 启动 ---------- */
  /* 暴露给 pipeline.js：它需要在同一套渲染/路由/toast 上挂三个新视图 */
  window.AFU = {
    render,
    setView,
    openRun,
    toast,
    esc,
    $,
    $$,
    view: () => state.view,
    runs: () => state.runs,
    runId: () => state.runId,
    state,   // 自检脚本需要注入后端数据后重渲染（pipeline.js 的 AF.state 是另一个对象）
  };

  /* 模型中心的真实数据：/api/models 由 agentflow/models.py 读 apps/p02/.env 生成。
     data.js 里的静态列表只是首屏兜底，这里以后端为准覆盖。 */
  async function loadModels() {
    try {
      const r = await fetch('/api/models');
      const d = await r.json();
      if (d && d.ok) {
        state.models = d.models || [];
        state.modelsSource = d.source || '';
        const act = state.models.find((m) => m.active) || {};
        const pill = document.querySelector('#modelPill span');
        if (pill && act.model) pill.textContent = act.model + '（默认）';
        if (state.view === 'models') render();
      }
    } catch (e) { /* 后端没起：保留静态兜底 */ }
  }

  /* 知识库状态：pgvector 连通性 + 块数 + 真正的 embedding 模型（模型中心页展示） */
  async function loadKb() {
    try {
      const r = await fetch('/api/kb/status');
      const d = await r.json();
      state.kbStatus = d || { ok: false };
      if (state.view === 'models' || state.view === 'knowledge') render();
    } catch (e) {
      state.kbStatus = { ok: false };
    }
  }

  /* 内嵌 Agent 状态（总览右栏）：与 AI 应用页同一份 /api/ext */
  async function loadExtStatus() {
    try {
      const r = await fetch('/api/ext');
      const d = await r.json();
      if (d && d.apps) {
        state.extAppsStatus = d.apps;
        if (state.view === 'overview') render();
      }
    } catch (e) { /* 后端没起 */ }
  }

  /* AI 应用活动：协作监控页约 5s 拉一次，状态有变化才整页重绘 */
  async function loadActs() {
    try {
      const r = await fetch('/api/activity');
      const d = await r.json();
      if (!d || !d.ok) return;
      const acts = d.activities || [];
      const sig = acts.map((a) => a.id + ':' + a.state + ':' + (a.duration_ms || '')).join('|');
      if (sig === loadActs.sig) return;
      loadActs.sig = sig;
      state.extActs = acts;
      if (state.view === 'runs' || state.view === 'overview') render();
    } catch (e) { /* 后端没起 */ }
  }

  function init() {
    const hash = location.hash.replace('#', '');
    const [head, param] = hash.split('/');
    if (head === 'run' && param && state.runs.some((r) => r.id === param)) {
      state.runId = param;
      state.view = 'run';
    } else if (VIEWS[head] && head !== 'run') {
      state.view = head;
    }
    render();
    bind();
    loadModels();
    loadActs();
    loadExtStatus();
    loadKb();
    setInterval(tick, 1200);

    // 首次进入的引导提示
    setTimeout(() => toast('按 Ctrl K 打开命令面板，数字键 1-8 快速切页'), 900);
  }

  init();
})();
