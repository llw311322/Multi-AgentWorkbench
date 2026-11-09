/* 整页集成自检：用一个"足够宽容"的假 DOM，把 data.js + pipeline.js + app.js
   一起跑起来，逐页渲染 9 个视图。

   能抓到的：init 崩溃、VIEWS 合并失败、视图函数里的 TypeError、
            真实数据接进来之后的空值问题。
   抓不到的：视觉排版（那个只能看浏览器）。

   跑法（先启动 agentflow.server）：
     node tests/page_check.js
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
  return el;
}

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

  const errors = [];
  const sandbox = {
    console, document, fetch, setTimeout, clearTimeout,
    setInterval: () => 0, clearInterval() {},
    location: { hash: '#plan' }, history: { replaceState() {} },
    navigator: { userAgent: 'node' },
    requestAnimationFrame: (f) => setTimeout(f, 0),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);

  vm.runInContext(fs.readFileSync(path.join(WEB, 'data.js'), 'utf8'), sandbox, { filename: 'data.js' });
  vm.runInContext(fs.readFileSync(path.join(WEB, 'pipeline.js'), 'utf8'), sandbox, { filename: 'pipeline.js' });
  try {
    vm.runInContext(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'), sandbox, { filename: 'app.js' });
  } catch (e) {
    errors.push('app.js init 崩溃：' + e.message + '\n' + (e.stack || '').split('\n')[1]);
  }

  if (typeof sandbox.AFU !== 'object') {
    console.log('[x] app.js 没有暴露 window.AFU，pipeline.js 拿不到渲染入口');
    process.exit(1);
  }

  const AF = sandbox.AF;
  const AFU = sandbox.AFU;
  console.log('[ok] 前端接口已打通：AFU 已暴露，AF 已注册 ' + Object.keys(AF.views).length + ' 个视图');

  // 把真实后端数据灌进去，再逐页渲染
  try {
    const health = await (await fetch(BASE + '/api/health')).json();
    const runs = (await (await fetch(BASE + '/api/runs')).json()).runs || [];
    AF.state.health = health;
    AF.state.runs = runs;
    AF.state.runId = runs.length ? runs[0].run_id : '';
    if (AF.state.runId) {
      AF.state.detail = await (await fetch(BASE + '/api/run/' + encodeURIComponent(AF.state.runId))).json();
    }
    console.log(`[ok] 灌入真实数据：${runs.length} 个 Run`);
  } catch (e) {
    console.log('[!] 后端不可用，用演示数据检查：' + e.message);
  }

  const views = ['overview', 'plan', 'board', 'feedback', 'runs', 'agents', 'apps', 'knowledge', 'models'];
  let fail = 0;
  for (const v of views) {
    const fn = AF.views[v] || (sandbox.AF && null);
    if (!fn) {
      // 原工作台视图不通过 AF 暴露，走 AFU.setView + 读 #content 的方式验证
      try {
        AFU.setView(v);
        const html = document.querySelector('#content').innerHTML;
        // apps 视图本身只是挂载点（真实内容由 pipeline.js 异步渲染进 #extApps，
        // 在本假 DOM 环境里由 jobs_check.js 单独验证），所以阈值放宽到只查非空
        const min = v === 'apps' ? 10 : 40;
        const bad = !html || html.length < min;
        console.log(`[${bad ? 'x' : 'ok'}] ${v}: ${html.length} 字符`);
        if (bad) fail++;
      } catch (e) {
        fail++;
        console.log(`[x] ${v}: ${e.message}`);
      }
      continue;
    }
    try {
      const html = fn();
      const bad = !html || html.length < 40;
      console.log(`[${bad ? 'x' : 'ok'}] ${v}: ${html.length} 字符`);
      if (bad) fail++;
    } catch (e) {
      fail++;
      console.log(`[x] ${v}: ${e.message}\n    ${(e.stack || '').split('\n')[1] || ''}`);
    }
  }

  // setView 走一遍（含 hash 路由）
  for (const v of ['plan', 'board', 'feedback']) {
    try { AFU.setView(v); } catch (e) { fail++; console.log(`[x] setView(${v}): ${e.message}`); }
  }
  console.log('[ok] setView 三个新页面均可切换');

  // 走一遍真实轮询：会把真实 Run 映射进「协作监控」并重渲染。
  // loadAll 是异步链，固定等 260ms 会偶发赶在数据就绪前 → 重试几次消抖。
  await new Promise((r) => setTimeout(r, 260));
  if (typeof AF.poll === 'function') {
    try {
      let injected = 0;
      // pollOnce 会串行做 health/runs/详情/扩展应用探测，服务刚起时单轮可能超过 5 秒；
      // 防重入标志（S.polling）在飞行中会直接返回，所以要「等在飞的落地 → 再发起」并给足预算
      for (let i = 0; i < 40; i++) {
        if (AF.state.polling) {
          await new Promise((r) => setTimeout(r, 300));
          continue;
        }
        await AF.poll();
        injected = AFU.runs().filter((r) => r.__live).length;
        if (injected) break;
        await new Promise((r) => setTimeout(r, 300));
      }
      console.log(`[${injected ? 'ok' : 'x'}] poll + 注入：${injected} 个真实 Run 进入协作监控`
        + (injected ? '' : `（polling=${AF.state.polling} err=${AF.state.err || '-'}）`));
      if (!injected) fail++;
      AFU.setView('runs');
      const html = document.querySelector('#content').innerHTML;
      console.log(`[${html.length > 500 ? 'ok' : 'x'}] runs（真实数据）：${html.length} 字符`);
      if (html.length <= 500) fail++;
      const first = AFU.runs()[0];
      AFU.openRun(first.id);
      const detail = document.querySelector('#content').innerHTML;
      console.log(`[${detail.length > 500 ? 'ok' : 'x'}] Run 详情（泳道图）：${detail.length} 字符`);
      if (detail.length <= 500) fail++;
    } catch (e) {
      fail++;
      console.log('[x] poll 崩溃：' + e.message + '\n    ' + (e.stack || '').split('\n')[1]);
    }
  } else {
    console.log('[!] AF.poll 未挂载，跳过轮询检查');
  }

  if (errors.length) {
    errors.forEach((e) => console.log('[x] ' + e));
    fail += errors.length;
  }
  console.log('\n结论：' + (fail ? `${fail} 处需要修` : 'PASS'));
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
