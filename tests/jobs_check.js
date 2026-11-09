/* 「AI 应用」页里招聘采集 Agent 的集成自检。

   验证的是接进来之后最容易坏的三件事：
     1. 卡片能不能画出来（status / 数据源 / 输入框 / 快捷 chip）
     2. 会话回读能不能把上一轮结果接上（刷新页面不该像没做过）
     3. 结果区表格的字段名是否和招聘 Agent 返回的结构对得上

   跑法（先启动 agentflow.server，且 8770 的招聘 Agent 在线）：
     node tests/jobs_check.js
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const BASE = 'http://127.0.0.1:8760';
const WEB = path.join(__dirname, '..', 'web');

function makeEl(name) {
  const el = {
    __name: name,
    innerHTML: '', textContent: '', value: '', hidden: false, scrollTop: 0,
    style: {}, dataset: {}, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, focus() {}, remove() {},
    appendChild(c) { el.children.push(c); return c; },
    closest() { return null; },
    contains() { return false; },
    querySelector() { return makeEl('child'); },
    querySelectorAll() { return []; },
    insertBefore(c) { return c; },
    setAttribute() {}, getAttribute() { return null; },
    getBoundingClientRect() { return { width: 100, height: 100, top: 0, left: 0 }; },
  };
  Object.defineProperty(el, 'childElementCount', { get: () => el.children.length });
  return el;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const cache = new Map();
  const byId = new Map();
  const document = {
    readyState: 'complete',
    title: '',
    activeElement: { tagName: 'BODY', closest() { return null; } },
    addEventListener() {}, removeEventListener() {},
    createElement: (t) => makeEl(t),
    body: makeEl('body'),
    documentElement: makeEl('html'),
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, makeEl('#' + id));
      return byId.get(id);
    },
    querySelector(sel) {
      if (!cache.has(sel)) cache.set(sel, makeEl(sel));
      return cache.get(sel);
    },
    querySelectorAll() { return []; },
  };

  // 页面里的 api() 用相对路径（浏览器能解析），node 里必须补上 base
  const fetchBase = (u, o) => fetch(String(u).startsWith('http') ? u : BASE + u, o);
  const sandbox = {
    console, document, fetch: fetchBase, setTimeout, clearTimeout,
    setInterval: () => 0, clearInterval() {},
    location: { hash: '#apps' }, history: { replaceState() {} },
    navigator: { userAgent: 'node' },
    requestAnimationFrame: (f) => setTimeout(f, 0),
    Blob: class { constructor() {} },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);

  vm.runInContext(fs.readFileSync(path.join(WEB, 'data.js'), 'utf8'), sandbox, { filename: 'data.js' });
  vm.runInContext(fs.readFileSync(path.join(WEB, 'pipeline.js'), 'utf8'), sandbox, { filename: 'pipeline.js' });
  vm.runInContext(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'), sandbox, { filename: 'app.js' });

  const AF = sandbox.AF;
  const AFU = sandbox.AFU;
  if (!AF || !AFU) { console.log('[x] AF / AFU 没暴露'); process.exit(1); }

  const ext = await (await fetch(BASE + '/api/ext')).json();
  const jobsApp = (ext.apps || []).find((a) => a.key === 'jobs');
  if (!jobsApp) { console.log('[x] /api/ext 里没有 jobs 应用，注册表没生效'); process.exit(1); }
  console.log(`[ok] 注册表读到 jobs：${jobsApp.status} · sid=${jobsApp.sid || '(无)'} · 数据源 ${(jobsApp.sources || []).length} 个`);

  // 走真实轮询：AF.poll 里会 loadExt → renderExtSection
  AFU.setView('apps');
  await AF.poll();
  await sleep(600);

  let fail = 0;
  const S = AF.state;
  const el = document.getElementById('extApps');
  const html = el.innerHTML || '';

  const must = [
    ['卡片标题', '招聘 JD 采集分析 Agent'],
    ['状态徽标', jobsApp.status === 'online' ? '● 在线' : '○ 离线'],
  ];
  if (jobsApp.status === 'online') {
    must.push(['数据源 chip', '智联招聘'], ['输入框', 'id="jobsMsg"'],
              ['采集按钮', 'data-ext="jobs-run"'], ['检索顺序提示', '先锁定城市码']);
  } else {
    must.push(['启动按钮', 'data-ext="jobs-start"']);
  }
  for (const [name, needle] of must) {
    const ok = html.indexOf(needle) >= 0;
    if (!ok) fail++;
    console.log(`[${ok ? 'ok' : 'x'}] ${name}：${needle}`);
  }
  console.log(`[ok] extApps 渲染 ${html.length} 字符，extLoaded=${S.extLoaded}`);

  // 默认搜索标签已按需求移除：卡片里不应再出现快捷 chip
  const chipLeft = html.indexOf('data-ext="jobs-chip"') >= 0;
  if (chipLeft) fail++;
  console.log(`[${chipLeft ? 'x' : 'ok'}] 默认搜索标签已移除（chip 数量 0）`);

  // 会话回读
  console.log(`[ok] 会话回读：jobsLoaded=${S.jobsLoaded}，恢复结果=${!!S.jobsResult}`);
  if (S.jobsResult) {
    const an = S.jobsResult.analysis || {};
    const ov = an.overview || {};
    const checks = [
      ['样本量', ov.total > 0],
      ['薪资分位', !!ov.salary && ov.salary.p50 != null],
      ['城市分布', (((an.salary || {}).byCity) || []).length > 0],
      ['技能榜', (((an.skills || {}).top) || []).length > 0],
      ['岗位明细', (S.jobsResult.jobs || []).length > 0],
    ];
    for (const [name, ok] of checks) {
      if (!ok) fail++;
      console.log(`[${ok ? 'ok' : 'x'}] 字段契约 ${name}`);
    }
    const again = document.getElementById('extApps').innerHTML || '';
    for (const needle of ['采集结果', '技能需求与溢价', 'data-ext="jobs-report"', 'data-ext="jobs-copy"']) {
      const ok = again.indexOf(needle) >= 0;
      if (!ok) fail++;
      console.log(`[${ok ? 'ok' : 'x'}] 结果区：${needle}`);
    }
  } else {
    console.log('[!] 招聘 Agent 会话里还没有数据，结果区未验证（先跑一次采集再看）');
  }

  console.log('\n结论：' + (fail ? `FAIL（${fail} 项）` : 'PASS'));
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.log('[x] 崩溃：' + e.message + '\n' + e.stack); process.exit(1); });
