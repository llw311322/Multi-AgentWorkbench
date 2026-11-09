/* ===========================================================
   协作流水线 · 任务拆分 / 执行看板 / 反馈闭环
   接 agentflow 后端的真实数据，不改动原工作台的任何视图
   -----------------------------------------------------------
   分工：
     拆分 → viewPlan     ：需求 → DAG → G1 人工确认
     执行 → viewBoard    ：六态看板 + 路由表 + 续跑
     反馈 → viewFeedback ：闸门决策 / 验收 / 评审 / 事件流 / 调用轨迹
     监控 → 原「协作监控」视图（真实 Run 注入 state.runs 后自动复用泳道图）
   =========================================================== */
(() => {
  'use strict';

  const AF = (window.AF = {
    views: {},
    meta: {
      plan: { title: '任务拆分', sub: '需求 → 任务图 DAG → G1 人工确认' },
      board: { title: '执行看板', sub: '就绪队列 / 并发执行 / 路由映射' },
      feedback: { title: '反馈闭环', sub: '闸门决策 / 验收评审 / 事件与调用轨迹' },
    },
    state: {
      health: null,
      runs: [],
      runId: '',
      detail: null,
      graphCache: {},
      job: null,
      err: '',
      sig: '',
      polling: false,
      draftReq: '',
      selected: '',
      gateNote: '',
      extApps: [],
      extLoaded: false,
      extForm: { topic: '', ctype: 'copywriting', words: '', vendor: '' },
      extBusy: false,
      extResult: null,
      extCollapsed: false,
      extHistory: [],
      extError: '',
      /* 招聘采集（jobs）：和 P-02 共用一套「外部应用」通道，但交互是检索不是写稿 */
      jobsMsg: '',
      jobsBusy: false,
      jobsResult: null,
      jobsCollapsed: false,
      jobsError: '',
      jobsHistory: [],
      jobsLoaded: false,
      /* 热点雷达（hotspot）：命令型应用，点一次跑一次 collect.py */
      hsHours: 24,
      hsBusy: false,
      hsResult: null,
      hsCollapsed: false,
      hsError: '',
      hsHistory: [],
      /* 大盘可视化（market）：只做状态概览与跳转，图表看板在对方自己的前端里 */
      mkBusy: false,
      mkError: '',
    },
    poll: null,
    afterRender: null,
  });

  const S = AF.state;
  let APP = null;

  /* ---------------- 常量 ---------------- */
  const DOMAIN_COLOR = { research: '#2EA7FF', content: '#9381FF', code: '#13DDC4', jobs: '#FFB454' };
  const DOMAIN_TEXT = { research: '市场分析', content: '内容', code: '代码', jobs: '招聘采集' };
  const NODE_COLOR = {
    pending: '#5B5878', ready: '#2EA7FF', running: '#2EA7FF', review: '#FFB454',
    done: '#13DDC4', blocked: '#FF4D6D', failed: '#FF4D6D',
  };
  const NODE_TEXT = {
    pending: '待开始', ready: '就绪', running: '执行中', review: '待评审',
    done: '已完成', blocked: '阻塞', failed: '失败',
  };
  const STATUS_CLASS = {
    pending: 'pending', ready: 'ready', running: 'running', review: 'review',
    done: 'done', blocked: 'blocked', failed: 'blocked', orphaned: 'blocked',
  };
  const EV_TEXT = {
    split: '拆分完成', gate: '闸门决策', planned: '任务图确认', run_start: '开始执行',
    node_start: '节点开始', node_finish: '节点结束', review: '质量评审', retry: '重试',
    blocked: '阻塞待人工', deadlock: '死锁', aggregate_start: '开始汇总', aggregate: '汇总完成',
    aggregate_failed: '汇总失败', delivered: '已交付', g3_rejected: '定稿被放弃', resume: '续跑',
    interrupted: '服务中断',
  };
  /* Run 级状态（不是节点状态）。interrupted = 进程没了、任务不会再自己往前走。 */
  const RUN_STATUS_TEXT = {
    running: '执行中', done: '已交付', rejected: '已放弃', interrupted: '已中断',
    blocked: '阻塞', failed: '失败', planned: '待执行',
  };
  const SAMPLES = [
    '写一篇关于 AI Agent 落地为什么这么慢的公众号文章',
    '分析 AI 编程工具的竞争格局，写一篇 1200 字公众号',
    '给内容团队做一份 AI 提效方案，含 3 个可落地动作',
  ];

  /* ---------------- 工具 ---------------- */
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => Number(n || 0).toLocaleString('en-US');
  const clip = (s, n = 90) => {
    const t = String(s || '').replace(/\s+/g, ' ').trim();
    return t.length > n ? t.slice(0, n) + '…' : t;
  };
  function toast(msg, kind) {
    if (APP) APP.toast(msg, kind);
  }
  function badge(status, text) {
    return `<span class="badge ${STATUS_CLASS[status] || ''}">${esc(text || NODE_TEXT[status] || status)}</span>`;
  }

  async function api(path, opts) {
    try {
      const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
      const txt = await res.text();
      let data;
      try { data = JSON.parse(txt); } catch (e) { data = { raw: txt }; }
      return { ok: res.ok, status: res.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { error: '连不上后端：' + e.message } };
    }
  }
  const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

  /* ---------------- 数据加载 ---------------- */
  const enc = encodeURIComponent;

  function gates() { return (S.detail && S.detail.gates) || []; }
  function liveGates() { return gates().filter((g) => g.status === 'pending' && !g.decision); }
  function graph() { return (S.detail && S.detail.graph) || null; }
  function nodes() { return (graph() && graph().nodes) || []; }
  function summary() { return (S.detail && S.detail.summary) || null; }
  function runMeta() { return S.runs.find((r) => r.run_id === S.runId) || null; }
  function currentRun() { return summary() || runMeta(); }

  async function loadAll() {
    const [h, r] = await Promise.all([api('/api/health'), api('/api/runs')]);
    if (h.ok) S.health = h.data;
    if (r.ok && r.data.runs) {
      S.runs = r.data.runs;
      if (!S.runId && S.runs.length) S.runId = S.runs[0].run_id;
    } else if (!r.ok) {
      S.err = (r.data && r.data.error) || '后端没起来';
      return;
    }
    S.err = '';
    await loadDetail();
  }

  async function loadDetail() {
    if (!S.runId) { S.detail = null; return; }
    const d = await api('/api/run/' + enc(S.runId));
    if (d.ok) S.detail = d.data;
  }

  /* 外部应用（如 P-02 文案 Agent）：探活 + 厂商列表 */
  async function loadExt() {
    const r = await api('/api/ext');
    if (r.ok) {
      S.extApps = r.data.apps || [];
      S.extLoaded = true;
      const p02 = S.extApps.find((a) => a.key === 'p02');
      if (p02 && p02.status === 'online') {
        if (!S.extForm.vendor && (p02.vendors || []).length) {
          const act = p02.active_vendor || (p02.vendors[0] || {}).key || '';
          S.extForm.vendor = act;
        }
      }
    }
    // 「AI 应用」不在自动重绘名单里（AF.views 没有 apps），拿到数据后自己刷
    if (APP && APP.view() === 'apps') renderExtSection();
    if (!S.jobsLoaded) loadJobsSession();
  }

  /* 招聘采集 Agent 自己会持久化会话：页面刷新后把上一轮结果接回来，
     否则每次刷新都像「什么都没做过」。 */
  async function loadJobsSession() {
    const a = S.extApps.find((x) => x.key === 'jobs');
    if (!a || a.status !== 'online') return;
    const sid = a.sid || 'workspace';
    const r = await api('/api/ext/jobs/session?sid=' + enc(sid));
    S.jobsLoaded = true;
    if (!r.ok || !r.data) return;
    const an = r.data.analysis || {};
    if (!(an.overview || {}).total) return;
    if (S.jobsResult) return;   // 页面上已有更新的结果，别覆盖
    S.jobsResult = {
      analysis: an,
      jobs: r.data.jobs || [],
      reply: '',
      query: r.data.query || {},
      restored: true,
    };
    if (APP && APP.view() === 'apps') renderExtSection(true);
  }

  async function loadJobsHistory() {
    const r = await api('/api/ext/articles?key=jobs');
    if (r.ok && r.data && r.data.ok) {
      S.jobsHistory = r.data.articles || [];
      if (APP && APP.view() === 'apps') renderExtSection();
    }
  }

  /* 生成的稿件由后端落盘到 runs/ext-p02/，这里只负责列出来。
     页面同时只保留一份「当前结果」，历史靠这个列表找回。 */
  async function loadExtHistory() {
    const r = await api('/api/ext/articles?key=p02');
    if (r.ok && r.data && r.data.ok) {
      S.extHistory = r.data.articles || [];
      if (APP && APP.view() === 'apps') renderExtSection();
    }
  }

  /* 协作监控视图要显示每个 Run 的节点，所以把各 Run 的任务图缓存下来。
     只拉 light 版（不含 final / trace），并按 updated_at 判断是否需要重取。 */
  async function ensureGraphs() {
    for (const r of S.runs.slice(0, 12)) {
      const c = S.graphCache[r.run_id];
      if (c && c.updated_at === r.updated_at) continue;
      const res = await api('/api/run/' + enc(r.run_id) + '?light=1');
      if (res.ok) {
        S.graphCache[r.run_id] = {
          updated_at: r.updated_at,
          graph: res.data.graph,
          state: res.data.state,
        };
      }
    }
  }

  async function pollOnce() {
    if (S.polling) return;
    S.polling = true;
    try {
      const [h, r] = await Promise.all([api('/api/health'), api('/api/runs')]);
      if (h.ok) S.health = h.data;
      if (!r.ok) { S.err = '后端连接中断'; return; }
      S.err = '';
      const runs = r.data.runs || [];
      S.runs = runs;

      // 在「协作监控」里点开某个 Run 时，我的三个视图跟着切过去
      if (APP && APP.view() === 'run' && APP.runId && APP.runId() !== S.runId) {
        S.runId = APP.runId();
        S.detail = null;
      }

      // 任务跑着的时候自动切到它新建的 Run
      if (!S.runId && runs.length) S.runId = runs[0].run_id;
      if (S.job && S.job.status === 'running' && runs.length && runs[0].run_id !== S.runId) {
        S.runId = runs[0].run_id;
        S.detail = null;
      }

      if (S.job && S.job.job_id && (S.job.status === 'queued' || S.job.status === 'running')) {
        const j = await api('/api/job/' + enc(S.job.job_id));
        if (j.ok) {
          S.job = j.data;
          if (j.data.status === 'done' && j.data.result && j.data.result.run_id) {
            S.runId = j.data.result.run_id;
          }
        }
      }

      await loadDetail();
      await loadExt();
      await ensureGraphs();
      syncLiveRuns();

      const sig = JSON.stringify([
        runs.map((x) => [x.run_id, x.status, x.stage, x.counts, x.pending_gate, x.updated_at]),
        S.detail && S.detail.gates ? S.detail.gates.map((g) => [g.gate_id, g.status, g.decision || '']) : [],
        S.detail ? nodes().map((n) => [n.task_id, n.status, n.attempts]) : [],
        S.extApps.map((a) => [a.key, a.status]),
      ]);
      if (sig !== S.sig) {
        S.sig = sig;
        maybeRender();
      }
      paintBadge();
    } finally {
      S.polling = false;
    }
  }

  function maybeRender() {
    if (!APP) return;
    const ae = document.activeElement;
    const typing = ae && ae.closest && ae.closest('#content') && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName);
    if (typing) return;                     // 别把正在输入的内容刷掉
    if (!AF.views[APP.view()]) return;      // 只在自己的视图里自动刷新
    APP.render();
  }

  function paintBadge() {
    const el = document.getElementById('badge-gate');
    if (!el) return;
    const n = S.runs.reduce((a, r) => a + (r.pending_gate ? 1 : 0), 0) + S.runs.filter((r) => r.status === 'blocked').length;
    el.textContent = String(n);
    el.style.display = n ? '' : 'none';
  }

  /* ---------------- 真实 Run 注入原「协作监控」视图 ----------------
     原视图吃的是 {id,name,status,form,depth,agents[],messages[],alerts[]}
     这里把 agentflow 的节点与事件映射成同样的形状，泳道图与消息总线就能直接复用。
     连上后端时把演示数据换成真 Run；断连时回落到原演示数据。
  ------------------------------------------------------------- */
  let legacyRuns = null;

  function syncLiveRuns() {
    if (!APP) return;
    const store = APP.runs();
    if (legacyRuns === null) legacyRuns = store.filter((r) => !r.__live).slice();
    const keepDemo = !(S.health && !S.err);
    const mapped = S.runs.map(toRunShape).reverse();   // unshift 会让最早的跑到最上面
    store.length = 0;
    mapped.forEach((r) => store.unshift(r));
    if (keepDemo) legacyRuns.forEach((r) => store.push(r));
  }

  function toRunShape(run) {
    const cache = S.graphCache[run.run_id] || {};
    const d = run.run_id === S.runId ? S.detail : cache;
    const hist = (d && d.state && d.state.history) || [];
    const gnodes = (d && d.graph && d.graph.nodes) || [];
    const t0 = hist.length ? hist[0].t || 0 : 0;
    const tEnd = hist.length ? hist[hist.length - 1].t || 0 : 0;
    const span = Math.max(1, tEnd - t0);

    const startAt = {};
    const endAt = {};
    hist.forEach((ev) => {
      if (ev.event === 'node_start') startAt[ev.task_id] = (ev.t || 0) - t0;
      if (ev.event === 'node_finish') endAt[ev.task_id] = (ev.t || 0) - t0;
    });

    const agents = gnodes.map((n) => ({
      id: n.task_id,
      name: (DOMAIN_TEXT[n.domain] || n.domain) + '·' + clip(n.title, 8),
      role: DOMAIN_TEXT[n.domain] || n.domain,
      model: (S.health && S.health.model) || 'llm',
      status: n.status === 'pending' ? 'idle' : (n.status === 'blocked' || n.status === 'failed' ? 'blocked' : n.status),
      start: Math.round(startAt[n.task_id] || 0),
      end: Math.round(endAt[n.task_id] != null ? endAt[n.task_id] : span),
      tokens: '—',
      cost: 0,
      calls: 1 + (n.attempts || 0),
      retries: n.attempts || 0,
      note: n.status === 'blocked' ? clip((n.open_questions || [])[0], 40) : '',
    }));

    const messages = hist
      .filter((ev) => ['node_start', 'node_finish', 'review', 'gate', 'retry'].indexOf(ev.event) >= 0)
      .slice(-8)
      .map((ev, i) => ({
        id: 'M' + ev.seq,
        from: ev.task_id || 'A1',
        to: ev.task_id || 'A1',
        t: Math.round((ev.t || t0) - t0),
        kind: ev.event === 'retry' ? 'challenge' : ev.event === 'node_finish' ? 'result' : 'delegate',
        label: (EV_TEXT[ev.event] || ev.event) + '：' + (ev.task_id || (ev.gate || '')),
        payload: JSON.stringify({ event: ev.event, verdict: ev.verdict, status: ev.status, attempts: ev.attempts }, null, 2),
      }));

    const alerts = [];
    gnodes.forEach((n) => {
      if (n.status === 'blocked' || n.status === 'failed') {
        alerts.push({ level: 'bad', text: `${n.task_id} ${clip(n.title, 12)} 阻塞：${clip((n.open_questions || [])[0] || '待人工处理', 36)}` });
      } else if (n.attempts > 0) {
        alerts.push({ level: 'warn', text: `${n.task_id} 重试 ${n.attempts} 次` });
      }
    });
    if (run.pending_gate) alerts.push({ level: 'bad', text: `闸门 ${run.pending_gate} 等待人工决策` });

    return {
      __live: true,
      id: run.run_id,
      name: clip(run.topic || run.goal || run.run_id, 34),
      status: run.status === 'running' ? 'running'
        : run.status === 'done' ? 'done'
          : run.status === 'blocked' ? 'blocked'
            : run.status === 'interrupted' ? 'interrupted' : 'failed',
      form: 'chain',
      depth: maxDepth(gnodes),
      startedAt: (run.created_at || '').slice(5, 16),
      durationText: hist.length ? fmtSpan(tEnd - t0) : '—',
      durationSec: Math.max(1, Math.round(span)),
      cost: 0,
      tokens: num(run.tokens),
      goal: run.goal || run.topic,
      agents,
      messages,
      alerts,
    };
  }

  function maxDepth(gnodes) {
    const byId = {};
    gnodes.forEach((n) => { byId[n.task_id] = n; });
    const memo = {};
    const walk = (id, seen) => {
      if (memo[id] != null) return memo[id];
      if (seen.has(id)) return 0;
      seen.add(id);
      const n = byId[id];
      if (!n) return 0;
      let m = 0;
      (n.depends_on || []).forEach((dep) => { m = Math.max(m, walk(dep, seen) + 1); });
      memo[id] = m;
      return m;
    };
    let d = 0;
    gnodes.forEach((n) => { d = Math.max(d, walk(n.task_id, new Set()) + 1); });
    return d;
  }

  const fmtSpan = (sec) => {
    const s = Math.max(0, Math.round(sec));
    return s < 60 ? s + 's' : Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
  };

  /* ---------------- 公共片段 ---------------- */
  function runPicker() {
    if (!S.runs.length) return '';
    const opts = S.runs.slice(0, 24).map((r) => {
      const name = clip(r.topic || r.run_id, 30);
      const st = RUN_STATUS_TEXT[r.status] || r.status || '';
      return `<option value="${esc(r.run_id)}" ${r.run_id === S.runId ? 'selected' : ''}>${esc(name)} · ${esc(st)}</option>`;
    }).join('');
    return `<select class="af-select" id="afRunPick" style="width:320px">${opts}</select>`;
  }

  function livePill() {
    const h = S.health;
    if (S.err) return `<span class="af-live off"><i></i>未连接后端</span>`;
    if (!h) return `<span class="af-live"><i></i>连接中…</span>`;
    return `<span class="af-live" title="闸门 ${h.gate_mode} · 超时 ${h.gate_timeout}s"><i></i>${h.mock ? 'mock 模式' : h.model} · 并发 ${h.concurrency}</span>`;
  }

  function jobBanner() {
    const j = S.job;
    if (!j) return '';
    if (j.status === 'running' || j.status === 'queued') {
      const tail = (j.log_tail || []).filter(Boolean).slice(-2).map((l) => esc(clip(l, 90))).join(' · ');
      return `<section class="af-gate" style="border-color:rgba(46,167,255,.32);background:linear-gradient(180deg,rgba(46,167,255,.08),rgba(46,167,255,.02))">
        <div class="af-gate-head">
          <span class="af-gate-kind" style="background:rgba(46,167,255,.16);color:#BFE2FF">RUNNING</span>
          <span class="af-gate-title">编排执行中 · ${esc(j.action)}</span>
          <span class="af-mono" style="margin-left:auto">${esc(j.started || '')}</span>
        </div>
        <div class="af-gate-body">${tail || '正在等待模型返回或等待闸门决策…'}</div>
      </section>`;
    }
    if (j.status === 'failed' || j.status === 'busy') {
      return `<section class="af-gate" style="border-color:rgba(255,77,109,.35);background:linear-gradient(180deg,rgba(255,77,109,.08),rgba(255,77,109,.02))">
        <div class="af-gate-head">
          <span class="af-gate-kind" style="background:rgba(255,77,109,.16);color:#FFB3C1">${j.status === 'busy' ? 'BUSY' : 'FAILED'}</span>
          <span class="af-gate-title">${esc(j.error || '执行失败')}</span>
          <button class="af-btn sm" data-af="dismiss-job" style="margin-left:auto">关闭</button>
        </div>
      </section>`;
    }
    return '';
  }

  function emptyBlock(title, hint, actions) {
    return `<div class="af-empty"><div style="margin-bottom:6px;color:var(--muted)">${esc(title)}</div>
      <div style="font-size:11.5px">${esc(hint)}</div>
      ${actions ? `<div class="af-row" style="justify-content:center;margin-top:14px">${actions}</div>` : ''}</div>`;
  }

  /* ---------------- DAG 画布 ---------------- */
  function dagSvg(g, selected) {
    const ns = (g && g.nodes) || [];
    if (!ns.length) return emptyBlock('还没有任务图', '从上方输入需求并点「拆分任务图」');

    const byId = {};
    ns.forEach((n) => { byId[n.task_id] = n; });
    const memo = {};
    const depthOf = (id, seen) => {
      if (memo[id] != null) return memo[id];
      if (seen.has(id)) return 0;
      seen.add(id);
      const n = byId[id];
      if (!n) return 0;
      let d = 0;
      (n.depends_on || []).forEach((dep) => { d = Math.max(d, depthOf(dep, seen) + 1); });
      memo[id] = d;
      return d;
    };

    const cols = {};
    ns.forEach((n) => {
      const d = depthOf(n.task_id, new Set());
      (cols[d] = cols[d] || []).push(n);
    });
    const depths = Object.keys(cols).map(Number).sort((a, b) => a - b);
    const maxRows = Math.max.apply(null, depths.map((d) => cols[d].length));
    const W = 186, H = 64, GX = 78, GY = 22, PAD = 26;
    const width = PAD * 2 + depths.length * W + (depths.length - 1) * GX;
    const height = Math.max(180, PAD * 2 + maxRows * H + (maxRows - 1) * GY);

    const pos = {};
    depths.forEach((d, ci) => {
      const arr = cols[d];
      const blockH = arr.length * H + (arr.length - 1) * GY;
      const y0 = (height - blockH) / 2;
      arr.forEach((n, ri) => {
        pos[n.task_id] = { x: PAD + ci * (W + GX), y: y0 + ri * (H + GY), d };
      });
    });

    const defs = `<defs>
      <marker id="afArrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0 0L8 4L0 8z" fill="rgba(255,255,255,.34)"/>
      </marker>
      <marker id="afArrowHot" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M0 0L8 4L0 8z" fill="rgba(46,167,255,.85)"/>
      </marker>
    </defs>`;

    let edges = '';
    ns.forEach((n) => {
      (n.depends_on || []).forEach((dep) => {
        const a = pos[dep], b = pos[n.task_id];
        if (!a || !b) return;
        const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2;
        const mx = (x1 + x2) / 2;
        const hot = n.status === 'running' || n.status === 'review';
        edges += `<path class="af-edge ${hot ? 'hot' : ''}" d="M${x1} ${y1} C${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}"
          marker-end="url(#${hot ? 'afArrowHot' : 'afArrow'})"/>`;
      });
    });

    let boxes = '';
    ns.forEach((n) => {
      const p = pos[n.task_id];
      const color = DOMAIN_COLOR[n.domain] || '#FFB454';
      const sc = NODE_COLOR[n.status] || NODE_COLOR.pending;
      const sel = selected === n.task_id;
      const running = n.status === 'running';
      boxes += `<g class="af-node ${sel ? 'is-sel' : ''}" data-af-node="${esc(n.task_id)}">
        ${running ? `<rect class="af-pulse" x="${p.x - 4}" y="${p.y - 4}" width="${W + 8}" height="${H + 8}" rx="15" fill="none" stroke="${sc}" stroke-width="2"/>` : ''}
        <rect class="box" x="${p.x}" y="${p.y}" width="${W}" height="${H}" rx="12"
          fill="rgba(13,9,48,.94)" stroke="${sel ? sc : 'rgba(255,255,255,.16)'}" stroke-width="${sel ? 2.2 : 1.2}"/>
        <rect x="${p.x}" y="${p.y + 10}" width="3" height="${H - 20}" rx="2" fill="${color}"/>
        <text x="${p.x + 14}" y="${p.y + 21}" fill="#6E6C8F" font-family="JetBrains Mono" font-size="9.5">${esc(n.task_id)} · ${esc(DOMAIN_TEXT[n.domain] || n.domain)}</text>
        <text x="${p.x + 14}" y="${p.y + 42}" fill="#FFFFFF" font-size="12" font-weight="500">${esc(clip(n.title, 12))}</text>
        <circle cx="${p.x + 16}" cy="${p.y + 53}" r="3" fill="${sc}"/>
        <text x="${p.x + 25}" y="${p.y + 56}" fill="${sc}" font-size="9.5" font-family="JetBrains Mono">${esc(NODE_TEXT[n.status] || n.status)}</text>
        ${n.attempts ? `<text x="${p.x + W - 12}" y="${p.y + 56}" fill="#FFB454" font-size="9.5" font-family="JetBrains Mono" text-anchor="end">重试 ${n.attempts}</text>` : ''}
        ${n.parallel_group ? `<text x="${p.x + W - 12}" y="${p.y + 21}" fill="#6E6C8F" font-size="9" font-family="JetBrains Mono" text-anchor="end">${esc(n.parallel_group)}</text>` : ''}
      </g>`;
    });

    const legend = depths.map((d) => `<span class="af-badge-soft">第 ${d + 1} 层 · ${cols[d].length} 个</span>`).join(' ');

    return `<div class="af-row" style="margin-bottom:8px">${legend}
        <span class="af-hint" style="margin-left:auto">点节点看详情／在 G1 阶段可改依赖与验收</span></div>
      <div class="af-dag-wrap"><svg class="af-dag" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">
        ${defs}${edges}${boxes}
      </svg></div>`;
  }

  /* ---------------- 视图：任务拆分 ---------------- */
  function viewPlan() {
    if (S.err) return notConnected();
    const g = graph();
    const run = currentRun();
    const live = liveGates();
    const g1 = live.find((x) => String(x.gate_id).indexOf('G1') === 0);

    const stats = `<section class="af-stats">
      <div class="stat"><b>${nodes().length}</b><span>任务节点 · ${new Set(nodes().map((n) => n.parallel_group || '-')).size} 个执行分组</span></div>
      <div class="stat"><b>${(g && g.critical_path && g.critical_path.length) || 0}</b><span>关键路径节点</span></div>
      <div class="stat"><b style="color:${g1 ? 'var(--amber)' : 'inherit'}">${live.length}</b><span>待人工决策的闸门</span></div>
      <div class="stat"><b>${esc((run && run.stage) || '—')}</b><span>当前阶段</span></div>
    </section>`;

    const reqCard = `<article class="card">
      <div class="card-head">
        <h2 class="card-title">需求输入</h2>
        <span class="card-sub">拆分器只做「把需求变成可执行的任务图」，不做可行性评估</span>
      </div>
      <div class="af-input-card">
        <textarea class="af-textarea" id="afReq" placeholder="一句话说清要什么。信息不足时拆分器会返回 blocking_questions，而不是硬猜。">${esc(S.draftReq)}</textarea>
        <div class="af-row">
          <button class="af-btn primary" data-af="split">只拆分（停到 G1）</button>
          <button class="af-btn" data-af="split-run">拆分并执行</button>
          <button class="af-btn" data-af="refresh">刷新</button>
          <span class="af-hint">${livePill()}</span>
        </div>
        <div class="af-chips">${SAMPLES.map((s) => `<button class="af-chip" data-af-sample="${esc(s)}">${esc(clip(s, 26))}</button>`).join('')}</div>
      </div>
    </article>`;

    const dagCard = `<article class="card">
      <div class="card-head">
        <h2 class="card-title">任务图 DAG</h2>
        <div class="af-row" style="gap:8px">${runPicker()}</div>
      </div>
      <p class="card-sub" style="margin:0">${g ? esc(g.goal || '') : '拆出来的图就是分发、监控、重试共同操作的唯一真相源'}</p>
      ${dagSvg(g, S.selected)}
    </article>`;

    const side = [];
    if (g1) side.push(gateCard(g1));
    else if (g) {
      const st = (run && run.status) || '';
      side.push(`<article class="card">
        <div class="card-head"><h2 class="card-title">G1 已通过</h2>${badge(st === 'done' ? 'done' : 'running', st === 'done' ? '已交付' : '可执行')}</div>
        <p class="af-hint" style="margin:0">任务图已确认。执行由「执行看板」推进，验收与评审在「反馈闭环」里看。</p>
        <div class="af-row">
          <button class="af-btn ok" data-af="resume">继续执行</button>
          <button class="af-btn" data-af-goto="board">去看板 →</button>
        </div>
      </article>`);
    }

    if (g && (g.assumptions || []).length) {
      side.push(`<article class="card">
        <div class="card-head"><h2 class="card-title">拆分时做的假设</h2><span class="card-sub">重点核对这几条</span></div>
        <div class="rows">${(g.assumptions || []).map((a) => `<div class="row"><span class="name">${esc(a)}</span></div>`).join('')}</div>
      </article>`);
    }

    if (nodes().length) {
      side.push(`<article class="card">
        <div class="card-head"><h2 class="card-title">节点清单</h2><span class="card-sub">点开可编辑</span></div>
        <div class="rows">${nodes().map((n) => `
          <div class="row" data-af-node="${esc(n.task_id)}" style="cursor:pointer">
            <span class="af-dot" style="background:${DOMAIN_COLOR[n.domain] || '#FFB454'}"></span>
            <span class="name">${esc(n.task_id)} · ${esc(clip(n.title, 18))}</span>
            ${badge(n.status)}
          </div>`).join('')}</div>
      </article>`);
    }

    if (!nodes().length) {
      return `${stats}<div class="af-split">
        <div class="af-stack">${reqCard}
          <article class="card"><div class="card-head"><h2 class="card-title">任务图 DAG</h2></div>
          ${emptyBlock('还没有拆分结果', '输入需求后点「只拆分」，会生成任务图并停在 G1 等你确认')}</article>
        </div>
        <div class="af-stack">${howtoCard()}</div>
      </div>`;
    }

    return `${stats}${jobBanner()}
      <div class="af-split">
        <div class="af-stack">${reqCard}${dagCard}</div>
        <div class="af-stack">${side.join('')}</div>
      </div>`;
  }

  function howtoCard() {
    return `<article class="card">
      <div class="card-head"><h2 class="card-title">这一页干什么</h2></div>
      <div class="af-hint">
        <p style="margin:0 0 8px">1. 写清需求 → 拆分器输出结构化 DAG（不是任务清单）</p>
        <p style="margin:0 0 8px">2. 每个节点带 <b>domain / depends_on / acceptance / parallel_group</b></p>
        <p style="margin:0 0 8px">3. 停在 <b>G1</b> 等你确认；可以改标题、依赖、验收口径</p>
        <p style="margin:0">4. 确认后才进入执行，之后每一步都留痕</p>
      </div>
    </article>`;
  }

  function notConnected() {
    return `<article class="card">
      <div class="card-head"><h2 class="card-title">后端未连接</h2><span class="card-sub">${esc(S.err)}</span></div>
      <div class="af-hint">
        <p style="margin:0 0 8px">页面能打开说明静态文件没问题，但拿不到 run 数据。启动后端：</p>
        <div class="af-pre">cd F:\\project\\2026-11-04-09-46-50
python -m agentflow.server --mock --open</div>
        <p style="margin:8px 0 0">然后用 <b>http://127.0.0.1:8760/</b> 打开，不要直接双击 index.html。</p>
      </div>
    </article>`;
  }

  /* ---------------- 闸门卡片 ---------------- */
  function gateCard(g) {
    const kind = String(g.gate_id).split('-')[0];
    const p = g.payload || {};
    let body = '';
    if (kind === 'G1') {
      body = `<div class="af-gate-body">${esc(p.summary || '')}</div>
        <div class="af-hint">接受后按<b>页面保存的 graph.json</b> 执行；想改就先点节点编辑，再回来接受。</div>`;
    } else if (kind === 'G2') {
      body = `<div class="af-gate-body">
          <div>标题：<b>${esc(p.title || '')}</b>　字数：${esc(p.words || 0)}</div>
          <div>事实核查：${esc(p.facts_total || 0)} 条，其中 <b style="color:var(--rose)">${esc((p.facts_unverified || []).length)}</b> 条未核实</div>
        </div>
        ${(p.facts_unverified || []).length ? `<div class="rows">${p.facts_unverified.slice(0, 5).map((f) => `<div class="row"><span class="name" style="color:var(--rose)">! ${esc(clip(f.claim, 46))}</span></div>`).join('')}</div>` : ''}
        ${(p.compliance_flags || []).length ? `<div class="af-hint" style="color:var(--amber)">合规风险：${esc(p.compliance_flags.join('；'))}</div>` : ''}
        <div class="af-pre tall">${esc(p.draft_text || '（没有正文）')}</div>`;
    } else {
      body = `<div class="af-gate-body">
          <div>覆盖目标 ${esc((p.covered_goals || []).length)} 项${(p.missing || []).length ? `　未覆盖 <b style="color:var(--amber)">${esc(p.missing.length)}</b> 项` : ''}${(p.conflicts || []).length ? `　冲突 <b style="color:var(--rose)">${esc(p.conflicts.length)}</b> 处` : ''}</div>
        </div>
        ${(p.missing || []).length ? `<div class="rows">${p.missing.slice(0, 4).map((m) => `<div class="row"><span class="name" style="color:var(--amber)">! ${esc(clip(m, 46))}</span></div>`).join('')}</div>` : ''}
        ${(p.conflicts || []).length ? `<div class="rows">${p.conflicts.slice(0, 4).map((c) => `<div class="row"><span class="name" style="color:var(--rose)">× ${esc(clip(c.description, 44))}</span></div>`).join('')}</div>` : ''}
        <div class="af-pre tall">${esc(p.final_text || '')}</div>`;
    }

    const opts = Object.entries(g.options || {}).map(([k, label]) => {
      const cls = k === 'a' ? 'ok' : (k === 'q' ? 'danger' : '');
      return `<button class="af-btn ${cls}" data-af-gate="${esc(g.gate_id)}" data-af-decision="${esc(k)}">${esc(label)}</button>`;
    }).join('');

    return `<article class="af-gate">
      <div class="af-gate-head">
        <span class="af-gate-kind">${esc(kind)}</span>
        <span class="af-gate-title">${esc(g.prompt || '待决策')}</span>
        <span class="af-mono" style="margin-left:auto">${esc(g.gate_id)}</span>
      </div>
      ${body}
      <div class="af-gate-actions">${opts}</div>
    </article>`;
  }

  /* Run 停在 interrupted：进程没了，但状态还没往前走。必须显式告诉人，
     否则看板上「执行中」的节点会让人一直等下去。 */
  function intrBanner() {
    const run = currentRun();
    if (!run || run.status !== 'interrupted') return '';
    return `<section class="af-gate" style="border-color:rgba(255,180,84,.36);background:linear-gradient(180deg,rgba(255,180,84,.10),rgba(255,180,84,.02))">
      <div class="af-gate-head">
        <span class="af-gate-kind">已中断</span>
        <span class="af-gate-title">执行线程已经结束（服务重启或进程被杀），任务不会再自己往前走</span>
      </div>
      <div class="af-hint">续跑规则：卡在 <b>running / review</b> 的节点退回 ready 重跑，已 <b>done</b> 的节点不会重复执行。点「继续 / 续跑」从断点接着跑。</div>
    </section>`;
  }

  /* ---------------- 视图：执行看板 ---------------- */
  function viewBoard() {
    if (S.err) return notConnected();
    const run = currentRun();
    const ns = nodes();
    const c = (run && run.counts) || {};

    const stats = `<section class="af-stats">
      <div class="stat"><b>${c.done || 0}/${ns.length}</b><span>已完成节点</span></div>
      <div class="stat"><b style="color:var(--cyan)">${(c.ready || 0) + (c.running || 0)}</b><span>就绪 / 执行中</span></div>
      <div class="stat"><b style="color:${(c.blocked || 0) + (c.failed || 0) ? 'var(--rose)' : 'inherit'}">${(c.blocked || 0) + (c.failed || 0)}</b><span>阻塞 / 失败</span></div>
      <div class="stat"><b>${num((run && run.tokens) || 0)}</b><span>累计 token · 重试 ${(run && run.attempts) || 0} 次</span></div>
    </section>`;

    const ctrl = `<article class="card">
      <div class="card-head">
        <h2 class="card-title">执行控制</h2>
        <div class="af-row" style="gap:8px">${livePill()}${runPicker()}</div>
      </div>
      <div class="af-row">
        <button class="af-btn ok" data-af="resume">继续 / 续跑</button>
        <button class="af-btn" data-af-goto="plan">去拆分 →</button>
        <button class="af-btn" data-af-goto="feedback">看反馈 →</button>
        <span class="af-hint">状态机：pending → ready → running → review → done／blocked。并发受 AGENTFLOW_CONCURRENCY 控制。</span>
      </div>
    </article>`;

    const cols = [
      ['pending', '待开始', '#5B5878'],
      ['ready', '就绪', '#2EA7FF'],
      ['running', '执行中', '#2EA7FF'],
      ['review', '待评审', '#FFB454'],
      ['done', '已完成', '#13DDC4'],
      ['blocked', '阻塞 / 失败', '#FF4D6D'],
    ];
    const dur = {};
    ((S.detail && S.detail.state && S.detail.state.history) || []).forEach((ev) => {
      if (ev.event === 'node_finish' && ev.task_id) dur[ev.task_id] = ev.duration_ms;
    });

    const board = `<article class="card">
      <div class="card-head"><h2 class="card-title">执行看板</h2><span class="card-sub">点卡片看节点详情：验收、产物、待解决问题</span></div>
      ${ns.length ? `<div class="af-board">${cols.map(([key, label, color]) => {
      const list = ns.filter((n) => (key === 'blocked' ? (n.status === 'blocked' || n.status === 'failed') : n.status === key));
      return `<div class="af-col">
          <div class="af-col-head"><span class="af-dot" style="background:${color}"></span>${label}<b>${list.length}</b></div>
          ${list.map((n) => `
            <div class="af-tcard ${n.status}" data-af-node="${esc(n.task_id)}">
              <div class="af-row" style="justify-content:space-between">
                <span class="tid">${esc(n.task_id)}</span>
                <span class="af-badge-soft" style="color:${DOMAIN_COLOR[n.domain] || '#FFB454'}">${esc(DOMAIN_TEXT[n.domain] || n.domain)}</span>
              </div>
              <div class="ttl">${esc(clip(n.title, 22))}</div>
              <div class="meta">
                <span>${esc((n.depends_on || []).length ? '依赖 ' + n.depends_on.join(',') : '无依赖')}</span>
                ${dur[n.task_id] != null ? `<span>${esc(fmtSpan(dur[n.task_id] / 1000))}</span>` : ''}
                ${n.attempts ? `<span style="color:var(--amber)">重试 ${n.attempts}</span>` : ''}
              </div>
            </div>`).join('')}
        </div>`;
    }).join('')}</div>` : emptyBlock('这个 Run 还没有节点', '先去「任务拆分」拆一张图')}
    </article>`;

    const routes = (S.health && S.health.routes) || {};
    const routeCard = `<article class="card">
      <div class="card-head"><h2 class="card-title">路由映射</h2><span class="card-sub">分发是查表，不是让模型决定</span></div>
      <table class="table">
        <thead><tr><th>领域</th><th>执行器</th><th>本 Run 节点</th></tr></thead>
        <tbody>${Object.entries(routes).map(([k, v]) => `<tr>
          <td class="name"><span class="af-dot" style="background:${DOMAIN_COLOR[k] || '#FFB454'};display:inline-block;margin-right:6px"></span>${esc(DOMAIN_TEXT[k] || k)}</td>
          <td class="mono">${esc(v)}</td>
          <td class="mono">${ns.filter((n) => n.domain === k).length} 个</td>
        </tr>`).join('')}</tbody>
      </table>
    </article>`;

    return `${stats}${jobBanner()}${intrBanner()}${ctrl}${board}
      <div class="af-split" style="grid-template-columns:minmax(0,1fr) 360px">
        <div class="af-stack">${eventCard(6)}</div>
        <div class="af-stack">${routeCard}</div>
      </div>`;
  }

  /* ---------------- 视图：反馈闭环 ---------------- */
  function viewFeedback() {
    if (S.err) return notConnected();
    const run = currentRun();
    const live = liveGates();
    const ns = nodes();
    const hist = (S.detail && S.detail.state && S.detail.state.history) || [];
    const retries = hist.filter((e) => e.event === 'retry' || (e.event === 'node_finish' && (e.attempts || 0) > 0));

    const stats = `<section class="af-stats">
      <div class="stat"><b style="color:${live.length ? 'var(--amber)' : 'inherit'}">${live.length}</b><span>待你决策的闸门</span></div>
      <div class="stat"><b>${retries.length}</b><span>重试 / 退回次数</span></div>
      <div class="stat"><b>${gates().filter((g) => g.decision || g.status === 'decided').length}</b><span>已决闸门</span></div>
      <div class="stat"><b>${esc(RUN_STATUS_TEXT[(run && run.status) || ''] || (run && run.status) || '—')}</b><span>Run 状态 · ${esc((run && run.stage) || '')}</span></div>
    </section>`;

    const gateBlock = live.length
      ? live.map(gateCard).join('')
      : `<article class="card"><div class="card-head"><h2 class="card-title">没有待决策的闸门</h2><span class="card-sub">闸门只在关键节点出现：G1 拆分 / G2 发布 / G3 定稿</span></div>
         ${emptyBlock('当前无阻塞', '任务在跑时这里会自动出现决策卡片', '')}</article>`;

    const checkRows = ns.map((n) => {
      const result = (n.__result || {});
      const checks = (n.self_check || []).map((s) => {
        const cls = s.result === 'pass' ? 'pass' : s.result === 'fail' ? 'fail' : 'warn';
        return `<div class="af-check ${cls}"><span class="mark">${esc(s.result || '—')}</span><span>${esc(s.criterion)}：${esc(clip(s.evidence, 60))}</span></div>`;
      }).join('') || '<span class="af-hint">无验收项</span>';
      const review = (n.review_notes || []).map((r) => `<div class="af-hint">${esc(clip(r, 90))}</div>`).join('') || '<span class="af-hint">未走评审</span>';
      return `<tr data-af-node="${esc(n.task_id)}">
        <td class="name">${esc(n.task_id)}<div class="mono">${esc(clip(n.title, 16))}</div></td>
        <td>${badge(n.status)}</td>
        <td class="mono">${n.attempts || 0}</td>
        <td>${checks}</td>
        <td>${review}</td>
      </tr>`;
    }).join('');

    const checkCard = `<article class="card">
      <div class="card-head"><h2 class="card-title">验收与评审</h2><span class="card-sub">监控判「交没交」，验收判「对不对」——两件事分开</span></div>
      ${ns.length ? `<table class="table">
        <thead><tr><th>节点</th><th>状态</th><th>重试</th><th>自检 / 验收</th><th>评审意见</th></tr></thead>
        <tbody>${checkRows}</tbody></table>` : emptyBlock('还没有可验收的节点', '跑一次执行后这里会出现逐节点的验收明细')}
    </article>`;

    return `${stats}${jobBanner()}
      <div class="af-row" style="justify-content:flex-end">${runPicker()}</div>
      ${gateBlock}
      ${checkCard}
      <div class="af-split" style="grid-template-columns:minmax(0,1fr) 420px">
        <div class="af-stack">${eventCard(40)}</div>
        <div class="af-stack">${traceCard()}</div>
      </div>`;
  }

  function eventCard(limit) {
    const hist = (S.detail && S.detail.state && S.detail.state.history) || [];
    const rows = hist.slice(-limit).map((ev) => {
      const cls = ev.event === 'gate' ? 'gate'
        : ev.event === 'review' ? 'ok'
          : (ev.event === 'blocked' || ev.event === 'deadlock' || ev.event === 'aggregate_failed' || ev.event === 'g3_rejected') ? 'bad'
            : ev.event === 'node_start' || ev.event === 'run_start' || ev.event === 'resume' ? 'run' : 'ok';
      const detail = Object.entries(ev)
        .filter(([k]) => ['seq', 'ts', 't', 'event'].indexOf(k) < 0)
        .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : String(v).slice(0, 42)}`)
        .join('  ');
      return `<div class="af-ev ${cls}">
        <span class="ts">${esc((ev.ts || '').slice(11))}</span>
        <span class="nm">${esc(EV_TEXT[ev.event] || ev.event)}</span>
        <span class="dt">${esc(detail)}</span>
      </div>`;
    }).join('') || '<div class="af-hint">还没有事件</div>';

    return `<article class="card">
      <div class="card-head"><h2 class="card-title">事件流</h2><span class="card-sub">每一步都落盘，刷新页面也不丢</span></div>
      <div class="af-events">${rows}</div>
    </article>`;
  }

  function traceCard() {
    const trace = (S.detail && S.detail.trace) || '';
    const lines = trace ? trace.split('\n').filter(Boolean) : [];
    return `<article class="card">
      <div class="card-head"><h2 class="card-title">模型调用轨迹</h2><span class="card-sub">含「不合约 → 退回重做」的每一次</span></div>
      ${lines.length ? `<div class="af-pre">${esc(lines.slice(-40).join('\n'))}</div>`
        : `<div class="af-hint">还没有调用记录。真实模型模式跑一次就能看到契约校验与修复重试的轨迹。</div>`}
    </article>`;
  }

  /* ---------------- 抽屉：节点详情 / 文件预览 ---------------- */
  function drawerOpen(html) {
    const d = document.getElementById('drawer');
    const ov = document.getElementById('drawerOverlay');
    if (!d || !ov) return;
    d.innerHTML = html;
    ov.hidden = false;
  }
  function drawerClose() {
    const ov = document.getElementById('drawerOverlay');
    if (ov) ov.hidden = true;
  }

  async function openNode(tid) {
    S.selected = tid;
    const local = nodes().find((n) => n.task_id === tid) || {};
    let detail = { node: local, result: {}, files: [] };
    if (S.runId) {
      const r = await api('/api/run/' + enc(S.runId) + '/node/' + enc(tid));
      if (r.ok) detail = r.data;
    }
    const n = detail.node || local;
    const res = detail.result || {};
    const files = detail.files || [];
    const g1 = liveGates().find((g) => String(g.gate_id).indexOf('G1') === 0);
    const dur = res.duration_ms;
    const isPlan = APP && APP.view() === 'plan';

    const editForm = (g1 && isPlan) ? `
      <div class="af-sep"></div>
      <div class="af-field"><label>标题</label><input class="af-input" id="afEditTitle" value="${esc(n.title || '')}"></div>
      <div class="af-field"><label>目标（写给执行器看）</label><textarea class="af-textarea" id="afEditGoal" style="min-height:70px">${esc(n.goal || '')}</textarea></div>
      <div class="af-field"><label>依赖节点</label>
        ${nodes().filter((x) => x.task_id !== n.task_id).map((x) => `<label class="af-dep"><input type="checkbox" class="afEditDep" value="${esc(x.task_id)}" ${(n.depends_on || []).indexOf(x.task_id) >= 0 ? 'checked' : ''}>${esc(x.task_id)} · ${esc(clip(x.title, 16))}</label>`).join('') || '<span class="af-hint">没有其它节点</span>'}
      </div>
      <div class="af-field"><label>并行分组（同组才允许并发）</label><input class="af-input" id="afEditGroup" value="${esc(n.parallel_group || '')}"></div>
      <div class="af-field"><label>验收标准（JSON 数组）</label><textarea class="af-textarea" id="afEditAcc" style="min-height:78px;font-family:var(--mono);font-size:11.5px">${esc(JSON.stringify(n.acceptance || [], null, 1))}</textarea></div>
      <div class="af-row"><button class="af-btn primary" data-af-save-node="${esc(n.task_id)}">保存并写回 graph.json</button></div>` : '';

    drawerOpen(`
      <div class="drawer-head">
        <div style="min-width:0">
          <div class="drawer-id">${esc(n.task_id)} · ${esc(DOMAIN_TEXT[n.domain] || n.domain)}</div>
          <h2 class="drawer-title">${esc(n.title || '')}</h2>
        </div>
        <button class="drawer-close" data-close-drawer aria-label="关闭">✕</button>
      </div>
      <div class="af-row">${badge(n.status)}${n.parallel_group ? `<span class="af-badge-soft">并行组 ${esc(n.parallel_group)}</span>` : ''}${n.attempts ? `<span class="af-badge-soft" style="color:var(--amber)">重试 ${n.attempts}</span>` : ''}${dur ? `<span class="af-badge-soft">${esc(fmtSpan(dur / 1000))}</span>` : ''}</div>
      <dl class="kv">
        <dt>目标</dt><dd>${esc(n.goal || '—')}</dd>
        <dt>依赖</dt><dd>${esc((n.depends_on || []).join(', ') || '无')}</dd>
        <dt>token</dt><dd>${esc(res.tokens_used != null ? res.tokens_used : '—')}</dd>
        <dt>交接说明</dt><dd>${esc(clip(n.notes_for_downstream || '—', 120))}</dd>
      </dl>
      ${(n.self_check || []).length ? `<div><div class="af-hint" style="margin-bottom:6px">自检 / 验收</div>${(n.self_check || []).map((s) => `<div class="af-check ${s.result === 'pass' ? 'pass' : s.result === 'fail' ? 'fail' : 'warn'}"><span class="mark">${esc(s.result || '—')}</span><span>${esc(s.criterion)}：${esc(clip(s.evidence, 80))}</span></div>`).join('')}</div>` : ''}
      ${(n.open_questions || []).length ? `<div><div class="af-hint" style="margin-bottom:6px;color:var(--amber)">待解决的问题</div>${n.open_questions.map((q) => `<div class="af-hint" style="color:var(--muted)">! ${esc(q)}</div>`).join('')}</div>` : ''}
      ${files.length ? `<div><div class="af-hint" style="margin-bottom:6px">产物文件</div><div class="af-row">${files.map((f) => `<button class="af-btn sm" data-af-file="${esc(f.path)}">${esc(f.name)}</button>`).join('')}</div></div>` : ''}
      ${editForm}
    `);
    if (APP) APP.render();
  }

  async function openFile(path) {
    const r = await api('/api/file?path=' + enc(path));
    const text = r.ok ? (r.data && r.data.raw !== undefined ? r.data.raw : r.data) : '';
    drawerOpen(`
      <div class="drawer-head">
        <div style="min-width:0">
          <div class="drawer-id">产物预览</div>
          <h2 class="drawer-title" style="font-size:14px">${esc(path.split('/').pop())}</h2>
        </div>
        <button class="drawer-close" data-close-drawer>✕</button>
      </div>
      <div class="af-mono" style="word-break:break-all">${esc(path)}</div>
      <div class="af-pre tall">${esc(text || '（空文件）')}</div>
    `);
  }

  /* ---------------- 动作 ---------------- */
  async function doSplit(alsoRun) {
    const el = document.getElementById('afReq');
    const req = ((el && el.value) || S.draftReq || '').trim();
    if (!req) { toast('先写一句需求', 'warn'); return; }
    S.draftReq = req;
    const r = await post(alsoRun ? '/api/run' : '/api/plan', {
      requirement: req,
      mock: !!(S.health && S.health.mock),
    });
    if (!r.ok) {
      S.job = { status: 'busy', error: r.data.error || '发起失败' };
      if (r.data.run_id) S.runId = r.data.run_id;
      toast(r.data.error || '发起失败', 'warn');
      if (APP) APP.render();
      return;
    }
    S.job = { job_id: r.data.job_id, status: 'running', action: alsoRun ? 'run' : 'plan', started: '' };
    toast(alsoRun ? '已提交：拆分 + 执行' : '已提交拆分，稍后停在 G1');
    if (APP) APP.render();
  }

  async function doResume() {
    if (!S.runId) { toast('先选一个 Run', 'warn'); return; }
    const r = await post('/api/run', { run_id: S.runId, mock: !!(S.health && S.health.mock) });
    if (!r.ok) {
      S.job = { status: 'busy', error: r.data.error || '发起失败' };
      toast(r.data.error || '发起失败', 'warn');
      if (APP) APP.render();
      return;
    }
    S.job = { job_id: r.data.job_id, status: 'running', action: 'resume', started: '' };
    toast('已提交续跑');
    if (APP) APP.render();
  }

  async function doGate(gateId, decision) {
    if (!S.runId) return;
    const r = await post('/api/gate', { run_id: S.runId, gate_id: gateId, decision, note: '' });
    if (!r.ok) { toast(r.data.error || '决策失败', 'warn'); return; }
    const label = { a: '已通过', r: '已退回', q: '已驳回' }[decision] || '已提交';
    toast(`${gateId} ${label}`);
    await pollOnce();
  }

  async function saveNode(tid) {
    const g = JSON.parse(JSON.stringify(graph() || {}));
    const n = (g.nodes || []).find((x) => x.task_id === tid);
    if (!n) return;
    const title = (document.getElementById('afEditTitle') || {}).value || '';
    const goal = (document.getElementById('afEditGoal') || {}).value || '';
    const group = (document.getElementById('afEditGroup') || {}).value || '';
    const accText = (document.getElementById('afEditAcc') || {}).value || '[]';
    let acc;
    try { acc = JSON.parse(accText); } catch (e) { toast('验收标准不是合法 JSON', 'warn'); return; }
    const deps = Array.from(document.querySelectorAll('.afEditDep:checked')).map((x) => x.value);
    n.title = title.trim() || n.title;
    n.goal = goal.trim() || n.goal;
    n.parallel_group = group.trim();
    n.acceptance = acc;
    n.depends_on = deps;
    g.edges = [];
    (g.nodes || []).forEach((x) => (x.depends_on || []).forEach((d) => g.edges.push([d, x.task_id])));

    const r = await post('/api/graph', { run_id: S.runId, graph: g });
    if (!r.ok || (r.data && r.data.ok === false)) {
      const probs = (r.data && r.data.problems) || [r.data.error || '保存失败'];
      toast('校验未通过：' + probs[0], 'warn');
      return;
    }
    toast('已写回 graph.json');
    drawerClose();
    await pollOnce();
    if (APP) APP.render();
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    document.addEventListener('click', (e) => {
      const t = e.target.closest ? e.target : null;
      if (!t) return;

      const sample = e.target.closest('[data-af-sample]');
      if (sample) {
        S.draftReq = sample.dataset.afSample;
        const el = document.getElementById('afReq');
        if (el) el.value = S.draftReq;
        return;
      }
      const act = e.target.closest('[data-af]');
      if (act) {
        const a = act.dataset.af;
        if (a === 'split') doSplit(false);
        else if (a === 'split-run') doSplit(true);
        else if (a === 'resume') doResume();
        else if (a === 'refresh') { pollOnce().then(() => APP && APP.render()); }
        else if (a === 'dismiss-job') { S.job = null; if (APP) APP.render(); }
        return;
      }
      const gate = e.target.closest('[data-af-gate]');
      if (gate) { doGate(gate.dataset.afGate, gate.dataset.afDecision); return; }

      const nodeEl = e.target.closest('[data-af-node]');
      if (nodeEl) { openNode(nodeEl.dataset.afNode); return; }

      const fileEl = e.target.closest('[data-af-file]');
      if (fileEl) { openFile(fileEl.dataset.afFile); return; }

      const saveBtn = e.target.closest('[data-af-save-node]');
      if (saveBtn) { saveNode(saveBtn.dataset.afSaveNode); return; }

      const goto = e.target.closest('[data-af-goto]');
      if (goto) { if (APP) APP.setView(goto.dataset.afGoto); return; }

      const ext = e.target.closest('[data-ext]');
      if (ext) { extAction(ext.dataset.ext, ext.dataset); return; }

      if (e.target.closest('[data-close-drawer]')) { drawerClose(); return; }
      if (e.target.id === 'drawerOverlay') { drawerClose(); return; }
    });

    document.addEventListener('input', (e) => {
      if (e.target && e.target.id === 'extTopic') S.extForm.topic = e.target.value;
      if (e.target && e.target.id === 'extWords') S.extForm.words = e.target.value;
      if (e.target && e.target.id === 'jobsMsg') S.jobsMsg = e.target.value;
    });
    /* 招聘采集输入框里按 Enter 直接开跑（Shift+Enter 换行留给长需求） */
    document.addEventListener('keydown', (e) => {
      if (e.target && e.target.id === 'jobsMsg' && e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        jobsAction('jobs-run');
      }
    });
    document.addEventListener('change', (e) => {
      if (e.target && e.target.id === 'extCtype') S.extForm.ctype = e.target.value;
      if (e.target && e.target.id === 'extVendor') S.extForm.vendor = e.target.value;
    });
    /* 离开外部应用区后补一次重绘：交互期间被跳过的状态更新在这里补上 */
    document.addEventListener('focusout', () => {
      setTimeout(() => {
        if (!APP || APP.view() !== 'apps') return;
        const el = document.getElementById('extApps');
        if (!el) return;
        const ae = document.activeElement;
        if (ae && el.contains(ae)) return;
        renderExtSection();
      }, 0);
    });

    document.addEventListener('change', (e) => {
      if (e.target && e.target.id === 'afRunPick') {
        S.runId = e.target.value;
        S.detail = null;
        S.selected = '';
        loadDetail().then(() => { syncLiveRuns(); if (APP) APP.render(); });
      }
    });

    document.addEventListener('input', (e) => {
      if (e.target && e.target.id === 'afReq') S.draftReq = e.target.value;
    });
  }

  /* ---------------- 外部应用：AI 应用页的「已接入本地 Agent」 ---------------- */
  const CTYPE_TEXT = {
    report: '报告', copywriting: '文案', analysis: '解析', interpretation: '解读',
    learning: '知识深度学习', steps: '学习步骤', tutorial: '教程', custom: '自定义',
  };

  /* 外部应用（P-02）的报错大多来自厂商 API，原始信息对用户没意义。
     这里做一次翻译：保留关键信息，补上「该怎么办」。原文附在括号里备查。 */
  function niceExtError(raw) {
    const m = String(raw || '');
    const low = m.toLowerCase();
    const brief = m.length > 220 ? m.slice(0, 200) + '…' : m;
    if (/insufficient balance|exceeded_current_quota|欠费|余额不足/.test(m)) {
      return '所选厂商账户余额不足或额度用尽（429）。请到该厂商控制台充值，或换一个厂商重试。\n原始信息：' + brief;
    }
    if (/invalid.*(api.?key|token)|unauthorized|401/.test(low)) {
      return '所选厂商的 API Key 无效或已过期（401）。请检查该应用 .env 里的 key。\n原始信息：' + brief;
    }
    if (/未配置 ?api ?key/.test(m)) {
      return brief + '\n该厂商当前不可用，换一个带「已配 key」标记的厂商即可。';
    }
    if (/winerror 10061|拒绝|connection refused|连不上/.test(m)) {
      return '文案 Agent 服务没在运行。点上方「启动服务」，等徽标变成「● 在线」再生成。';
    }
    if (/timed? ?out|超时/.test(low)) {
      return '生成超时了（模型响应太慢）。可减少字数、换个厂商，或稍后重试。\n原始信息：' + brief;
    }
    return brief;
  }

  function extStatusBadge(st) {
    if (st === 'online') return '<span class="af-chip ok">● 在线</span>';
    if (st === 'ready') return '<span class="af-chip ok">● 就绪</span>';
    return '<span class="af-chip bad">○ 离线</span>';
  }

  function extAppsHtml() {
    if (!S.extLoaded) return '<article class="card"><div class="af-row">读取外部应用中…</div></article>';
    const cards = S.extApps.map((a) => {
      if (a.key === 'jobs') return jobsCardHtml(a);       // 检索型 Agent，交互与写稿型不同
      if (a.key === 'hotspot') return hotspotCardHtml(a); // 命令型 Agent，点一次抓一次
      if (a.key === 'market') return marketCardHtml(a);   // 可视化 Agent，图表在对方看板里
      const online = a.status === 'online';
      const f = S.extForm;
      const vendors = (a.vendors || []).map((v) =>
        `<option value="${esc(v.key)}" ${v.key === f.vendor ? 'selected' : ''}>${esc(v.name)}${v.has_key ? '' : '（未配 key）'}</option>`).join('');
      const form = online ? `
        <div class="af-row" style="margin-top:10px">
          <input id="extTopic" class="af-input" style="flex:1;min-width:180px" placeholder="内容主题，如：AI Agent 落地为什么这么慢" value="${esc(f.topic)}" />
          <select id="extCtype" class="af-input" style="width:100px">
            ${Object.entries(CTYPE_TEXT).map(([k, v]) => `<option value="${k}" ${k === f.ctype ? 'selected' : ''}>${v}</option>`).join('')}
          </select>
          <input id="extWords" class="af-input" style="width:84px" placeholder="字数" value="${esc(f.words)}" />
          <select id="extVendor" class="af-input" style="width:130px">${vendors || '<option value="deepseek">DeepSeek</option>'}</select>
          <button class="af-btn primary" data-ext="generate" ${S.extBusy ? 'disabled' : ''}>${S.extBusy ? '生成中…' : '生成'}</button>
        </div>
        ${S.extBusy ? '<div class="af-hint" style="margin-top:8px">正在调用模型，通常 10–40 秒；字数多或厂商繁忙会更久，请勿关页面。</div>' : ''}` : `
        <div class="af-row" style="margin-top:10px">
          <button class="af-btn" data-ext="start" ${S.extBusy ? 'disabled' : ''}>启动服务</button>
          <span class="af-hint">启动后即可在工作台里直接生成，也可打开原界面使用</span>
        </div>`;
      return `
      <article class="card">
        <div class="card-head">
          <h2 class="card-title">${esc(a.name)}</h2>
          ${extStatusBadge(a.status)}
        </div>
        <div class="af-row" style="margin-bottom:4px">
          <span class="af-hint">${esc(a.desc)}</span>
        </div>
        <div class="af-row">
          <span class="af-hint mono">${esc(a.base_url)} · ${esc(a.key)}</span>
          <span style="flex:1"></span>
          ${online ? '<button class="ghost-btn" data-ext="open">打开原界面</button>' : ''}
        </div>
        ${form}
        ${S.extError ? `<div class="af-ext-err">${esc(S.extError)}</div>` : ''}
      </article>`;
    }).join('');
    return `
      <article class="card" style="margin-bottom:14px">
        <div class="card-head"><h2 class="card-title">已接入的本地 Agent</h2>
        <span class="card-sub">注册于 agentflow/apps.py · EXTERNAL_APPS</span></div>
      </article>
      <div class="grid-2">${cards}</div>
      ${extResultHtml()}
      ${extHistoryHtml()}
      ${jobsResultHtml()}
      ${jobsHistoryHtml()}
      ${hotspotResultHtml()}
      ${hotspotHistoryHtml()}`;
  }

  function extResultHtml() {
    const r = S.extResult;
    if (!r) return '';
    const done = S.extCollapsed;
    const secs = (r.article && r.article.sections || []).map((s, i) => `
      <div style="margin:14px 0">
        <div class="af-node-title">${i + 1}. ${esc(s.heading || '')}</div>
        ${(s.content || []).map((p) => `<p style="margin:6px 0;line-height:1.7;color:var(--text)">${esc(String(p))}</p>`).join('')}
        ${s.image_suggestion ? `<div class="af-hint">配图建议：${esc(s.image_suggestion)}${s.image_source ? ' · 来源：' + esc(s.image_source) : ''}</div>` : ''}
      </div>`).join('');
    const savedPath = r.saved && r.saved.path;
    return `
    <article class="card" style="margin-top:14px">
      <div class="card-head">
        <h2 class="card-title">${esc((r.article && r.article.title) || '生成结果')}</h2>
        <div class="af-row" style="gap:8px">
          <span class="card-sub">${esc(r.vendor_name || '')} · ${esc(r.model || '')} · ${esc(r.image_plan_label || '')}</span>
          <button class="ghost-btn" data-ext="toggle-result">${done ? '展开 ▾' : '收起 ▴'}</button>
        </div>
      </div>
      ${done ? `<div class="af-hint">已收起，正文仍保留在这里（${(r.article && r.article.sections || []).length} 个章节）。点右上「展开」可重新查看。</div>` : `
        ${r.article && r.article.subtitle ? `<p style="color:var(--muted);margin:0 0 6px">${esc(r.article.subtitle)}</p>` : ''}
        ${secs || '<div class="af-hint">没有返回章节</div>'}
      `}
      <div class="af-row" style="margin-top:12px;gap:8px">
        <button class="af-btn primary" data-ext="copy-result">复制全文</button>
        <button class="af-btn" data-ext="download-result">下载 .md</button>
        <button class="ghost-btn" data-ext="open">去原界面排版/配图</button>
        ${savedPath ? `<span class="af-hint mono" style="margin-left:4px">已存档：${esc(savedPath)}</span>` : ''}
      </div>
    </article>`;
  }

  /* 历史生成：后端已落盘，页面收起/刷新/切页都不会丢。
     复制与下载都按需从 /api/file 取原文，不占页面状态。 */
  function extHistoryHtml() {
    const items = S.extHistory || [];
    if (!items.length) return '';
    const rows = items.map((a, i) => `
      <tr>
        <td style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.title)}</td>
        <td class="mono" style="white-space:nowrap">${esc(a.created_at)}</td>
        <td class="mono" style="white-space:nowrap">${(a.chars / 1000).toFixed(1)}k</td>
        <td style="white-space:nowrap">
          <button class="link" data-ext="copy-article" data-path="${esc(a.path)}">复制</button>
          <button class="link" data-ext="download-article" data-path="${esc(a.path)}">下载</button>
        </td>
      </tr>`).join('');
    return `
    <article class="card" style="margin-top:14px">
      <div class="card-head"><h2 class="card-title">历史生成</h2>
        <span class="card-sub">存在 runs\\ext-p02\\ · 最近 ${items.length} 篇</span></div>
      <table class="table" style="margin-top:8px">
        <thead><tr><th>标题</th><th>时间</th><th>字数</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </article>`;
  }

  /* ---------------- 招聘采集（jobs）：检索式外部 Agent ---------------- */

  /* 对方回复是轻量 markdown（**加粗** + 换行），不值得引一个解析器 */
  function mdLite(text) {
    return esc(text || '')
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/\n/g, '<br>');
  }

  function kSal(v) {
    if (v == null || v === 0) return '—';
    const n = Number(v);
    return n >= 10000 ? (n / 10000).toFixed(2) + '万' : (n / 1000).toFixed(1) + 'K';
  }

  function jobsCardHtml(a) {
    const online = a.status === 'online';
    const srcs = (a.sources || [])
      .map((s) => `<span class="af-chip ${s.ready ? 'ok' : 'bad'}" title="${esc(s.note || '')}">${esc(s.label)}${s.ready ? '' : '（需 Cookie）'}</span>`)
      .join('');
    const last = a.history && a.history.jobs
      ? `<span class="af-hint">上次采集 ${a.history.jobs} 条${a.history.median ? ' · 中位 ' + kSal(a.history.median) : ''}</span>`
      : '';
    const form = online
      ? `
      <div class="af-row">
        <input id="jobsMsg" class="af-input" style="flex:1;min-width:220px"
          placeholder="城市 + 岗位，例：青岛 数据分析师（可加薪资/经验/学历）" value="${esc(S.jobsMsg)}" />
        <button class="af-btn primary" data-ext="jobs-run" ${S.jobsBusy ? 'disabled' : ''}>${S.jobsBusy ? '采集中…' : '采集分析'}</button>
      </div>
      <div class="af-hint" style="margin-top:6px">按「城市 → 岗位 → 关键词降级」的顺序检索：先锁定城市码，命中不足再换近义关键词。</div>
      ${S.jobsBusy ? '<div class="af-hint" style="margin-top:8px">正在采集：翻页 + 补全 JD 全文，通常 40–120 秒。可以切到别的页，回来结果还在。</div>' : ''}`
      : `
      <div class="af-row">
        <button class="af-btn" data-ext="jobs-start" ${S.jobsBusy ? 'disabled' : ''}>启动服务</button>
        <span class="af-hint">启动后即可在这里直接采集；也可以打开原界面用完整看板与图表</span>
      </div>`;
    return `
    <article class="card">
      <div class="card-head">
        <h2 class="card-title">${esc(a.name)}</h2>
        ${extStatusBadge(a.status)}
      </div>
      <div class="af-row" style="margin-bottom:6px"><span class="af-hint">${esc(a.desc)}</span></div>
      <div class="af-row" style="margin-bottom:8px">
        <span class="af-hint mono">${esc(a.base_url)} · ${esc(a.key)}</span>
        <span style="flex:1"></span>
        ${last}
        ${online ? '<button class="ghost-btn" data-ext="jobs-open">打开完整看板</button>' : ''}
      </div>
      ${srcs ? `<div class="af-row" style="margin-bottom:8px">${srcs}</div>` : ''}
      ${form}
      ${S.jobsError ? `<div class="af-ext-err">${esc(S.jobsError)}</div>` : ''}
    </article>`;
  }

  function jobsResultHtml() {
    const r = S.jobsResult;
    if (!r) return '';
    const an = r.analysis || {};
    const ov = an.overview || {};
    const sal = ov.salary || {};
    const jobs = r.jobs || [];
    const done = S.jobsCollapsed;
    const app = S.extApps.find((a) => a.key === 'jobs') || {};

    const tiles = [
      ['样本岗位', ov.total || 0],
      ['公司', ov.companies || 0],
      ['中位月薪', kSal(sal.median)],
      ['P25–P75', `${kSal(sal.p25)}–${kSal(sal.p75)}`],
      ['P90', kSal(sal.p90)],
      ['薪资覆盖', (ov.salary_coverage || 0) + '%'],
    ]
      .map(([label, val]) => `<div class="stat"><b>${esc(String(val))}</b><span>${esc(label)}</span></div>`)
      .join('');

    const city = ((an.salary || {}).byCity || []).slice(0, 6).map((c) => `
      <tr><td class="name">${esc(c.name)}</td><td class="mono">${c.count}</td>
      <td class="mono">${kSal(c.median)}</td><td class="mono">${c.share}%</td></tr>`).join('');

    const skills = ((an.skills || {}).top || []).slice(0, 10).map((s) => `
      <tr><td class="name">${esc(s.name)}</td><td class="mono">${s.count}</td>
      <td class="mono">${s.share}%</td><td class="mono">${kSal(s.median)}</td>
      <td class="mono" style="color:${s.lift > 0 ? 'var(--teal)' : 'var(--dim)'}">${s.lift ? '+' + kSal(s.lift) : '—'}</td></tr>`).join('');

    const rows = jobs.slice(0, 12).map((j) => `
      <tr>
        <td class="name" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          ${j.url ? `<a href="${esc(j.url)}" target="_blank" rel="noopener" style="color:var(--text)">${esc(j.title)}</a>` : esc(j.title)}
        </td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(j.company || '')}</td>
        <td class="mono" style="white-space:nowrap">${esc(j.salary || '面议')}</td>
        <td style="white-space:nowrap">${esc(j.city || '')}</td>
        <td style="white-space:nowrap">${esc(j.exp || '—')}</td>
        <td style="white-space:nowrap">${esc(j.edu || '—')}</td>
      </tr>`).join('');

    return `
    <article class="card" style="margin-top:14px">
      <div class="card-head">
        <h2 class="card-title">${r.restored ? '上一轮采集结果' : '本次采集结果'}</h2>
        <div class="af-row" style="gap:8px">
          <span class="card-sub">${esc((an.query || {}).city || '不限城市')} · ${esc(((an.query || {}).keywords || []).filter(Boolean).join('、') || '城市热门岗位')}</span>
          <button class="ghost-btn" data-ext="jobs-toggle">${done ? '展开 ▾' : '收起 ▴'}</button>
        </div>
      </div>
      <section class="strip" style="margin-bottom:10px">${tiles}</section>
      ${r.reply ? `<div class="af-pre" style="white-space:normal;line-height:1.75">${mdLite(r.reply)}</div>` : ''}
      ${done ? `<div class="af-hint">已收起，数据仍在（${jobs.length} 条岗位明细）。点右上「展开」查看城市 / 技能 / 岗位表。</div>` : `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px">
          <div>
            <div class="af-node-title">城市定价</div>
            <table class="table"><thead><tr><th>城市</th><th>岗位</th><th>中位</th><th>占比</th></tr></thead>
            <tbody>${city || '<tr><td colspan="4" class="af-hint">无数据</td></tr>'}</tbody></table>
          </div>
          <div>
            <div class="af-node-title">技能需求与溢价</div>
            <table class="table"><thead><tr><th>技能</th><th>提及</th><th>覆盖</th><th>中位</th><th>溢价</th></tr></thead>
            <tbody>${skills || '<tr><td colspan="5" class="af-hint">无数据</td></tr>'}</tbody></table>
          </div>
        </div>
        <div class="af-node-title" style="margin-top:14px">岗位明细（前 12，点击标题跳原文）</div>
        <table class="table"><thead><tr><th>职位</th><th>公司</th><th>月薪</th><th>城市</th><th>经验</th><th>学历</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="af-hint">无数据</td></tr>'}</tbody></table>
      `}
      <div class="af-row" style="margin-top:12px;gap:8px">
        <button class="af-btn primary" data-ext="jobs-copy">复制摘要</button>
        <button class="af-btn" data-ext="jobs-download">下载 .md</button>
        ${app.report_url ? '<button class="af-btn" data-ext="jobs-report">导出 HTML 报告</button>' : ''}
        <button class="ghost-btn" data-ext="jobs-open">在完整看板里继续追问</button>
        ${r.saved && r.saved.path ? `<span class="af-hint mono">已存档：${esc(r.saved.path)}</span>` : ''}
      </div>
    </article>`;
  }

  function jobsHistoryHtml() {
    const items = S.jobsHistory || [];
    if (!items.length) return '';
    const rows = items.map((a) => `
      <tr>
        <td style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.title)}</td>
        <td class="mono" style="white-space:nowrap">${esc(a.created_at)}</td>
        <td class="mono" style="white-space:nowrap">${(a.chars / 1000).toFixed(1)}k</td>
        <td style="white-space:nowrap">
          <button class="link" data-ext="jobs-copy-doc" data-path="${esc(a.path)}">复制</button>
          <button class="link" data-ext="jobs-download-doc" data-path="${esc(a.path)}">下载</button>
        </td>
      </tr>`).join('');
    return `
    <article class="card" style="margin-top:14px">
      <div class="card-head"><h2 class="card-title">历史采集</h2>
        <span class="card-sub">存在 runs\\ext-jobs\\ · 最近 ${items.length} 份</span></div>
      <table class="table" style="margin-top:8px">
        <thead><tr><th>检索条件</th><th>时间</th><th>字数</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </article>`;
  }

  function plainJobsText(r) {
    const an = (r && r.analysis) || {};
    const ov = an.overview || {};
    const sal = ov.salary || {};
    const q = an.query || {};
    const lines = [
      `招聘 JD 采集 · ${q.city || '不限城市'} ${(q.keywords || []).join('、')}`,
      `样本 ${ov.total || 0} 条 / ${ov.companies || 0} 家公司 / ${ov.cities || 0} 个城市`,
      `月薪中位 ${kSal(sal.median)}，P25–P75 ${kSal(sal.p25)}–${kSal(sal.p75)}，P90 ${kSal(sal.p90)}`,
      '',
    ];
    if (r.reply) lines.push(r.reply, '');
    const sk = ((an.skills || {}).top || []).slice(0, 8);
    if (sk.length) {
      lines.push('技能需求：' + sk.map((s) => `${s.name} ${s.share}%${s.lift ? `(+${kSal(s.lift)})` : ''}`).join('、'), '');
    }
    (r.jobs || []).slice(0, 10).forEach((j, i) => {
      lines.push(`${i + 1}. ${j.title} | ${j.company} | ${j.salary} | ${j.city} | ${j.exp || '—'} | ${j.edu || '—'}`);
    });
    return lines.join('\n');
  }

  async function jobsAction(action, data) {
    const app = S.extApps.find((a) => a.key === 'jobs') || {};
    if (action === 'jobs-open') { window.open(app.web_url || 'http://127.0.0.1:8770/', '_blank'); return; }
    if (action === 'jobs-report') { window.open(app.report_url || '', '_blank'); return; }

    if (action === 'jobs-toggle') {
      S.jobsCollapsed = !S.jobsCollapsed;
      renderExtSection(true);
      return;
    }
    if (action === 'jobs-copy') {
      if (!S.jobsResult) return;
      copyText(plainJobsText(S.jobsResult), '摘要已复制');
      return;
    }
    if (action === 'jobs-download') {
      if (!S.jobsResult) return;
      const q = (S.jobsResult.analysis || {}).query || {};
      const name = `招聘采集-${q.city || '全国'}-${(q.keywords || []).join('') || '岗位'}`;
      downloadText(name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 40) + '.md', plainJobsText(S.jobsResult));
      return;
    }
    if (action === 'jobs-copy-doc' || action === 'jobs-download-doc') {
      const md = await fetchArticleMd((data && data.path) || '');
      if (!md) return;
      if (action === 'jobs-copy-doc') copyText(md, '已复制存档原文');
      else downloadText(String(data.path).split('/').pop() || 'jobs.md', md);
      return;
    }
    if (action === 'jobs-start') {
      S.jobsBusy = true; S.jobsError = ''; renderExtSection(true);
      const r = await api('/api/ext/jobs/start', { method: 'POST' });
      S.jobsBusy = false;
      if (!r.ok || !r.data.ok) S.jobsError = (r.data && r.data.error) || '启动失败';
      await loadExt();
      S.jobsLoaded = false;
      loadJobsSession();
      renderExtSection(true);
      return;
    }
    if (action === 'jobs-run') {
      const msg = (S.jobsMsg || '').trim();
      if (!msg) { S.jobsError = '先写一句需求：城市 + 岗位名（可加薪资/经验/学历条件）'; renderExtSection(true); return; }
      S.jobsBusy = true; S.jobsError = ''; S.jobsCollapsed = false; renderExtSection(true);
      const r = await api('/api/ext/jobs/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sid: app.sid || 'workspace', message: msg }),
      });
      S.jobsBusy = false;
      if (r.ok && r.data && !r.data.error) {
        S.jobsResult = {
          analysis: r.data.analysis || {},
          jobs: r.data.jobs || [],
          reply: r.data.reply || '',
          saved: r.data.saved || null,
          query: r.data.query || {},
        };
        S.jobsError = '';
        loadJobsHistory();
      } else {
        const raw = (r.data && r.data.error) || '采集失败';
        S.jobsError = /连不上|拒绝|refused|HTTP 0/.test(raw)
          ? '招聘采集服务没在运行。点上方「启动服务」，等徽标变成「● 在线」再采集。'
          : raw;
      }
      renderExtSection(true);
    }
  }


  /* ---------------- 热点雷达（hotspot）：命令型外部 Agent ---------------- */
  /* 没有 HTTP 服务，后端直接跑 collect.py。抓取要走 14 个源，通常 40–60 秒。 */

  function hotspotCardHtml(a) {
    const ready = a.status === 'ready' || a.status === 'online';
    const lc = a.last_collect;
    const last = lc
      ? `<span class="af-hint">上次 ${esc(String(lc.at || '').slice(0, 16).replace('T', ' '))} · 窗口 ${lc.window_hours}h · 去重 ${lc.total_after_dedupe} 条 · 源 ${lc.sources_ok}/${lc.sources_total}</span>`
      : '';
    const form = ready
      ? `
      <div class="af-row">
        <select id="hsHours" class="af-input" style="width:130px">
          ${[12, 24, 48].map((h) => `<option value="${h}" ${h === S.hsHours ? 'selected' : ''}>最近 ${h} 小时</option>`).join('')}
        </select>
        <button class="af-btn primary" data-ext="hs-run" ${S.hsBusy ? 'disabled' : ''}>${S.hsBusy ? '抓取中…' : '抓取热点'}</button>
      </div>
      ${S.hsBusy ? '<div class="af-hint" style="margin-top:8px">正在抓取 14 个源（HN / GitHub / arXiv / IT之家 / 华尔街见闻…），通常 40–60 秒，可以切到别的页，回来结果还在。</div>' : ''}`
      : `<div class="af-hint">入口脚本缺失，无法抓取</div>`;
    return `
    <article class="card">
      <div class="card-head">
        <h2 class="card-title">${esc(a.name)}</h2>
        ${extStatusBadge(a.status)}
      </div>
      <div class="af-row" style="margin-bottom:6px"><span class="af-hint">${esc(a.desc)}</span></div>
      <div class="af-row" style="margin-bottom:8px">
        <span class="af-hint mono">命令型 · apps/hotspot · ${esc(a.key)}</span>
        <span style="flex:1"></span>
        ${last}
      </div>
      ${form}
      ${S.hsError ? `<div class="af-ext-err">${esc(S.hsError)}</div>` : ''}
    </article>`;
  }

  function hotspotResultHtml() {
    const r = S.hsResult;
    if (!r) return '';
    const done = S.hsCollapsed;
    const st = r.stats || {};
    const src = (r.sources || []).map((s) =>
      `<span class="af-chip ${s.ok ? 'ok' : 'bad'}">${esc(s.name)} ${s.count}</span>`).join('');
    return `
    <article class="card" style="margin-top:14px">
      <div class="card-head">
        <h2 class="card-title">热点摘要 · 最近 ${r.hours} 小时</h2>
        <div class="af-row" style="gap:8px">
          <span class="card-sub">${esc(r.summary || '')}</span>
          <button class="ghost-btn" data-ext="hs-toggle">${done ? '展开 ▾' : '收起 ▴'}</button>
        </div>
      </div>
      ${done
        ? '<div class="af-hint">已收起，摘要仍保留在这里。点右上「展开」可重新查看。</div>'
        : `<div class="af-row" style="margin-bottom:8px">${src}</div>
           <pre class="mono" style="white-space:pre-wrap;max-height:420px;overflow:auto;background:var(--panel,#171427);padding:12px;border-radius:8px;font-size:12.5px;line-height:1.65">${esc(r.digest || '')}</pre>`}
      <div class="af-row" style="margin-top:12px;gap:8px">
        <button class="af-btn primary" data-ext="hs-copy">复制摘要</button>
        <button class="af-btn" data-ext="hs-download">下载 .md</button>
        ${r.saved ? `<span class="af-hint mono" style="margin-left:4px">已存档：${esc(r.saved.path)}</span>` : ''}
      </div>
    </article>`;
  }

  function hotspotHistoryHtml() {
    const items = S.hsHistory || [];
    if (!items.length) return '';
    const rows = items.map((a) => `
      <tr>
        <td style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.title)}</td>
        <td class="mono" style="white-space:nowrap">${esc(a.created_at)}</td>
        <td class="mono" style="white-space:nowrap">${(a.chars / 1000).toFixed(1)}k</td>
        <td style="white-space:nowrap">
          <button class="link" data-ext="hs-copy-doc" data-path="${esc(a.path)}">复制</button>
          <button class="link" data-ext="hs-download-doc" data-path="${esc(a.path)}">下载</button>
        </td>
      </tr>`).join('');
    return `
    <article class="card" style="margin-top:14px">
      <div class="card-head"><h2 class="card-title">历史抓取</h2>
        <span class="card-sub">存在 runs\\ext-hotspot\\ · 最近 ${items.length} 次</span></div>
      <table class="table" style="margin-top:8px">
        <thead><tr><th>摘要</th><th>时间</th><th>字数</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </article>`;
  }

  async function hotspotAction(action, data) {
    if (action === 'hs-toggle') {
      S.hsCollapsed = !S.hsCollapsed;
      renderExtSection(true);
      return;
    }
    if (action === 'hs-copy') {
      if (S.hsResult) copyText(S.hsResult.digest || '', '摘要已复制');
      return;
    }
    if (action === 'hs-download') {
      if (!S.hsResult) return;
      const name = `热点摘要-窗口${S.hsResult.hours}h`;
      downloadText(name.replace(/[\\/:*?"<>|]/g, '_') + '.md', S.hsResult.digest || '');
      return;
    }
    if (action === 'hs-copy-doc' || action === 'hs-download-doc') {
      const md = await fetchArticleMd((data && data.path) || '');
      if (!md) return;
      if (action === 'hs-copy-doc') copyText(md, '已复制存档原文');
      else downloadText(String(data.path).split('/').pop() || 'hotspot.md', md);
      return;
    }
    if (action === 'hs-run') {
      const sel = document.getElementById('hsHours');
      if (sel) S.hsHours = Number(sel.value) || 24;
      S.hsBusy = true; S.hsError = ''; S.hsCollapsed = false;
      renderExtSection(true);
      const r = await api('/api/ext/hotspot/collect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hours: S.hsHours }),
      });
      S.hsBusy = false;
      if (r.ok && r.data && r.data.ok) {
        S.hsResult = r.data;
        S.hsError = '';
        loadHsHistory();
      } else {
        S.hsError = (r.data && r.data.error) || '抓取失败';
      }
      renderExtSection(true);
    }
  }

  async function loadHsHistory() {
    const r = await api('/api/ext/articles?key=hotspot');
    if (r.ok && r.data && r.data.ok) {
      S.hsHistory = r.data.articles || [];
      if (APP && APP.view() === 'apps') renderExtSection();
    }
  }

  /* ---------------- 大盘可视化（market）：只做概览与跳转 ---------------- */
  /* K线/分时/复盘图表都在对方自己的看板前端里，这里不重复造图。
     涨跌颜色按 A 股习惯：涨红跌绿。 */

  function pctColor(v) {
    const n = Number(v);
    if (!isFinite(n) || n === 0) return 'var(--muted)';
    return n > 0 ? '#FF4D4D' : '#2ECC71';
  }

  function pctText(v) {
    const n = Number(v);
    if (!isFinite(n)) return '—';
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  function marketCardHtml(a) {
    const online = a.status === 'online';
    const rows = (a.indices || []).map((i) => `
      <tr>
        <td>${esc(i.name)}</td>
        <td class="mono">${i.close != null ? Number(i.close).toFixed(2) : '—'}</td>
        <td class="mono" style="color:${pctColor(i.pct)}">${pctText(i.pct)}</td>
        <td class="mono" style="color:${pctColor(i.ret20)}">${pctText(i.ret20)}</td>
        <td class="mono">${i.posIn60d != null ? Number(i.posIn60d).toFixed(0) + '%' : '—'}</td>
      </tr>`).join('');
    const body = online
      ? `
      <div class="af-row" style="margin-bottom:8px">
        ${a.market_state ? `<span class="af-chip ok">${esc(a.market_state)}</span>` : ''}
        ${a.collector ? `<span class="af-hint">采集调度：${esc(a.collector)}</span>` : ''}
        <span style="flex:1"></span>
        <button class="ghost-btn" data-ext="mk-refresh">刷新概览</button>
        <button class="ghost-btn" data-ext="mk-open">打开完整看板</button>
      </div>
      ${rows
        ? `<table class="table">
            <thead><tr><th>指数</th><th>收盘</th><th>当日</th><th>20日</th><th>60日位置</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`
        : '<div class="af-hint">还没有行情数据，等采集调度跑一轮后刷新</div>'}`
      : `
      <div class="af-row">
        <button class="af-btn" data-ext="mk-start" ${S.mkBusy ? 'disabled' : ''}>${S.mkBusy ? '启动中…' : '启动服务'}</button>
        <span class="af-hint">启动后自动开启采集调度（盘中 5 分钟 / 午盘 11:32 / 收盘 15:05）</span>
      </div>`;
    return `
    <article class="card">
      <div class="card-head">
        <h2 class="card-title">${esc(a.name)}</h2>
        ${extStatusBadge(a.status)}
      </div>
      <div class="af-row" style="margin-bottom:6px"><span class="af-hint">${esc(a.desc)}</span></div>
      <div class="af-row" style="margin-bottom:8px">
        <span class="af-hint mono">${esc(a.base_url)} · ${esc(a.key)}</span>
      </div>
      ${body}
      ${S.mkError ? `<div class="af-ext-err">${esc(S.mkError)}</div>` : ''}
    </article>`;
  }

  async function marketAction(action) {
    const app = S.extApps.find((a) => a.key === 'market') || {};
    if (action === 'mk-open') { window.open(app.web_url || 'http://127.0.0.1:8080/', '_blank'); return; }
    if (action === 'mk-refresh') { await loadExt(); renderExtSection(true); return; }
    if (action === 'mk-start') {
      S.mkBusy = true; S.mkError = ''; renderExtSection(true);
      const r = await api('/api/ext/market/start', { method: 'POST' });
      S.mkBusy = false;
      if (!r.ok || !r.data.ok) S.mkError = (r.data && r.data.error) || '启动失败';
      await loadExt();
      renderExtSection(true);
    }
  }

  /* ---------- 复制 / 下载 ---------- */
  function plainArticleText(r) {
    const a = (r && r.article) || {};
    const lines = [a.title || '', a.subtitle || ''].filter(Boolean);
    (a.sections || []).forEach((s, i) => {
      lines.push('', `${i + 1}. ${s.heading || ''}`);
      (s.content || []).forEach((p) => lines.push(String(p)));
      if (s.image_suggestion) lines.push(`[配图建议] ${s.image_suggestion}`);
    });
    return lines.join('\n');
  }

  function copyText(text, okMsg) {
    const fallback = () => {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        if (APP && APP.toast) APP.toast(okMsg || '已复制');
        else alert(okMsg || '已复制');
      } catch (e) {
        alert('复制失败，请手动选择文本：\n' + String(e.message || e));
      }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        if (APP && APP.toast) APP.toast(okMsg || '已复制');
        else alert(okMsg || '已复制');
      }).catch(fallback);
    } else {
      fallback();
    }
  }

  function downloadText(filename, text) {
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  async function fetchArticleMd(path) {
    const r = await api('/api/file?path=' + enc(path));
    if (r.ok && typeof r.data === 'string') return r.data;
    if (r.data && r.data.raw) return r.data.raw;
    if (r.data && r.data.error) { S.extError = '读取失败：' + r.data.error; renderExtSection(true); }
    return '';
  }

  let extSig = '';
  function extStateSig() {
    return JSON.stringify([
      S.extApps.map((a) => [a.key, a.status, (a.vendors || []).map((v) => v.key + (v.has_key ? 1 : 0)).join('|'),
        (a.sources || []).map((s) => s.name + (s.ready ? 1 : 0)).join('|'), (a.history || {}).jobs || 0]),
      S.extForm, S.extBusy, !!S.extResult, S.extCollapsed, S.extError,
      S.extHistory.map((a) => a.path),
      S.jobsMsg, S.jobsBusy, !!S.jobsResult, S.jobsCollapsed, S.jobsError,
      S.jobsHistory.map((a) => a.path),
      S.hsHours, S.hsBusy, !!S.hsResult, S.hsCollapsed, S.hsError,
      S.hsHistory.map((a) => a.path),
      S.mkBusy, S.mkError, (S.extApps.find((a) => a.key === 'market') || {}).market_state || '',
    ]);
  }

  /* force=true 用于「刚点完按钮、状态确实变了」；轮询来的调用不加 force。
     关键：DOM 整块替换会把展开中的 <select> 弹层销毁，看起来就像「下拉框会飞」。 */
  function renderExtSection(force) {
    const el = document.getElementById('extApps');
    if (!el) return;
    const ae = document.activeElement;
    const interacting = ae && el.contains(ae) && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName);
    const empty = el.childElementCount === 0;   // 刚切回本页时容器是空的，必须画
    const sig = extStateSig();
    if (!force && !empty) {
      if (interacting) return;        // 正在输入/下拉展开 → 一个字都别动
      if (sig === extSig) return;     // 数据没变 → 不白加一次重绘
    }
    extSig = sig;
    el.innerHTML = extAppsHtml();
  }

  async function extAction(action, ds) {
    if (action.indexOf('jobs-') === 0) return jobsAction(action, ds);
    if (action.indexOf('hs-') === 0) return hotspotAction(action, ds);
    if (action.indexOf('mk-') === 0) return marketAction(action, ds);
    const app = S.extApps.find((a) => a.key === 'p02');
    const data = ds || {};
    if (action === 'open') { window.open('http://127.0.0.1:8978', '_blank'); return; }

    /* 收起只改显示状态，绝不丢弃结果——丢了就找不回来，是最难受的一种设计。 */
    if (action === 'toggle-result') {
      S.extCollapsed = !S.extCollapsed;
      renderExtSection(true);
      return;
    }
    if (action === 'copy-result') {
      if (!S.extResult) return;
      copyText(plainArticleText(S.extResult), '全文已复制（含标题与各章节）');
      return;
    }
    if (action === 'download-result') {
      if (!S.extResult) return;
      const t = (S.extResult.article && S.extResult.article.title) || 'article';
      downloadText(t.replace(/[\\/:*?"<>|]/g, '_').slice(0, 40) + '.md', plainArticleText(S.extResult));
      return;
    }
    if (action === 'copy-article') {
      const md = await fetchArticleMd(data.path);
      if (md) copyText(md, '已复制存档原文');
      return;
    }
    if (action === 'download-article') {
      const md = await fetchArticleMd(data.path);
      if (md) downloadText(String(data.path).split('/').pop() || 'article.md', md);
      return;
    }

    if (action === 'start') {
      S.extBusy = true; S.extError = ''; renderExtSection(true);
      const r = await api('/api/ext/p02/start', { method: 'POST' });
      S.extBusy = false;
      if (!r.ok || !r.data.ok) S.extError = (r.data && r.data.error) || '启动失败';
      await loadExt(); renderExtSection(true); AF.poll && AF.poll();
      return;
    }
    if (action === 'generate') {
      const topic = S.extForm.topic.trim();
      if (!topic) { S.extError = '主题不能为空'; renderExtSection(true); return; }
      S.extBusy = true; S.extError = ''; S.extResult = null; S.extCollapsed = false; renderExtSection(true);
      const r = await api('/api/ext/p02/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vendor: S.extForm.vendor || undefined,
          fields: {
            topic,
            content_type: S.extForm.ctype,
            word_limit: S.extForm.words || undefined,
          },
        }),
      });
      S.extBusy = false;
      if (r.ok && r.data.ok) { S.extResult = r.data; S.extError = ''; }
      else S.extError = niceExtError((r.data && (r.data.error || '生成失败')) || '生成失败');
      renderExtSection(true);
      if (r.ok && r.data.ok) loadExtHistory();
    }
  }

  /* ---------------- 启动 ---------------- */
  AF.afterRender = function () {
    // app.js 每次 render 后调用：这里做视图挂载后的收尾
    if (APP && APP.view() === 'apps') renderExtSection();
    paintBadge();
  };

  function boot() {
    APP = window.AFU || null;
    if (!APP) { setTimeout(boot, 120); return; }
    bind();
    loadAll()
      .then(() => ensureGraphs())
      .then(() => loadExtHistory())
      .then(() => loadJobsHistory())
      .then(() => loadHsHistory())
      .then(() => { if (APP) { syncLiveRuns(); APP.render(); } });
    setInterval(() => { pollOnce(); }, 1600);
    AF.poll = pollOnce;
  }

  AF.views.plan = viewPlan;
  AF.views.board = viewBoard;
  AF.views.feedback = viewFeedback;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
