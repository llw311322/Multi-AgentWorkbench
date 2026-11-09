/* 视图渲染自检：不开浏览器，直接用真实 API 数据跑三个视图函数。
   目的不是"好看"，而是把模板里的 TypeError / undefined 提前打出来。

   跑法（先启动 agentflow.server）：
     node tests/render_check.js
*/
const fs = require('fs');
const vm = require('vm');

const BASE = 'http://127.0.0.1:8760';

async function main() {
  const health = await (await fetch(BASE + '/api/health')).json();
  const runs = (await (await fetch(BASE + '/api/runs')).json()).runs || [];
  const runId = runs.length ? runs[0].run_id : '';
  const detail = runId ? await (await fetch(BASE + '/api/run/' + encodeURIComponent(runId))).json() : null;

  const window = {};
  const document = {
    readyState: 'complete',
    addEventListener() {},
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement() { return { style: {}, appendChild() {}, remove() {}, classList: { add() {} } }; },
    body: { appendChild() {} },
  };
  const sandbox = {
    window, document, console,
    fetch, setTimeout, clearTimeout, setInterval: () => 0, clearInterval,
    location: { hash: '' }, history: { replaceState() {} },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(__dirname + '/../web/pipeline.js', 'utf8'), sandbox, { filename: 'pipeline.js' });

  const AF = window.AF;
  if (!AF) throw new Error('pipeline.js 没有导出 window.AF');

  AF.state.health = health;
  AF.state.runs = runs;
  AF.state.runId = runId;
  AF.state.detail = detail;

  const checks = [];
  for (const [name, fn] of Object.entries(AF.views)) {
    let html = '';
    let err = null;
    try { html = fn(); } catch (e) { err = e; }
    checks.push({ name, html: String(html == null ? '' : html), err });
  }

  let fail = 0;
  for (const c of checks) {
    if (c.err) {
      fail++;
      console.log(`[x] ${c.name}: ${c.err && c.err.message}\n${(c.err.stack || '').split('\n').slice(1, 4).join('\n')}`);
    } else {
      const m = c.html.match(/undefined|\[object Object\]|NaN/);
      const bad = !!m;
      console.log(`[${bad ? '!' : 'ok'}] ${c.name}: ${c.html.length} 字符`
        + (bad ? `（命中 ${m[0]} @${m.index}）` : ''));
      if (bad) {
        console.log('    上下文：' + JSON.stringify(c.html.slice(Math.max(0, m.index - 80), m.index + 40)));
        fail++;
      }
    }
  }

  // 关键内容断言
  const plan = AF.views.plan();
  const board = AF.views.board();
  const fb = AF.views.feedback();
  const assert = (cond, msg) => { if (!cond) { fail++; console.log('[x] ' + msg); } else console.log('[ok] ' + msg); };
  assert(plan.includes('af-dag'), '拆分页画出 DAG 画布');
  assert(plan.includes('af-node'), '拆分页有可点节点');
  assert(board.includes('af-board'), '执行页画出六态看板');
  assert(board.includes('exec_research') || board.includes('执行器') || board.includes('路由映射'), '执行页有路由映射');
  assert(plan.length > 800 && board.length > 800 && fb.length > 600, '三个视图都有实质内容');
  if (detail && detail.state && detail.state.history.length) {
    assert(fb.includes('af-ev'), '反馈页渲染出事件流');
  }

  console.log('\n结论：' + (fail ? `${fail} 处需要修` : 'PASS'));
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
