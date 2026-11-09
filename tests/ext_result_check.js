/* 外部应用结果卡自检：不点浏览器，直接把「已生成 + 已收起 + 有历史」三种状态
   塞进状态里，跑一遍渲染，检查关键控件是否真的出现在 HTML 里。

   跑法：
     node tests/ext_result_check.js
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

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

function boot() {
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
  const sandbox = {
    console, document, fetch: () => new Promise(() => {}),
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
    location: { hash: '#apps' }, history: { replaceState() {} },
    navigator: { userAgent: 'node' },
    requestAnimationFrame: (f) => setTimeout(f, 0),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  for (const f of ['data.js', 'pipeline.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(WEB, f), 'utf8'), sandbox, { filename: f });
  }
  return { sandbox, document };
}

let fail = 0;
function check(name, cond, detail) {
  console.log(`[${cond ? 'ok' : 'x'}] ${name}${cond ? '' : '  → ' + (detail || '')}`);
  if (!cond) fail++;
}

const { sandbox, document } = boot();
const AF = sandbox.AF;

/* pipeline.js 的 boot() 是 setTimeout(...,120) 启动的：等它把 window.AFU 挂上，
   再切到 apps 视图，否则 afterRender 里的 APP 还是 null，什么都不会画。 */
setTimeout(() => {
  const S = AF.state;
  const AFU = sandbox.AFU;
  if (!AFU) { console.log('[x] window.AFU 未挂载，pipeline.js boot 失败'); process.exit(1); }
  AFU.setView('apps');

  S.extLoaded = true;
  S.extApps = [{
    key: 'p02', name: '文案生成 Agent', desc: 'd',
    base_url: 'http://127.0.0.1:8978', status: 'online',
    vendors: [{ key: 'deepseek', name: 'DeepSeek', has_key: true }],
  }];
  S.extForm = { topic: 't', ctype: 'copywriting', words: '', vendor: 'deepseek' };
  S.extResult = {
    ok: true, vendor_name: 'DeepSeek', model: 'deepseek-chat', image_plan_label: '关键词配图',
    article: {
      title: '测试标题', subtitle: '测试副标题',
      sections: [{ heading: '第一节', content: ['段落一', '段落二'], image_suggestion: '配图' }],
    },
    saved: { path: 'runs/ext-p02/20261105-173449-测试标题.md' },
  };
  S.extHistory = [
    { path: 'runs/ext-p02/20261105-173449-测试标题.md', title: '测试标题', created_at: '2026-11-05 17:34', chars: 1714 },
  ];

  AF.afterRender();
  const box = document.getElementById('extApps');
  let html = box.innerHTML;

  check('结果卡出现', /测试标题/.test(html));
  check('有「收起」按钮', /收起/.test(html), '找不到收起');
  check('有「复制全文」', /复制全文/.test(html));
  check('有「下载 .md」', /下载 \.md/.test(html));
  check('显示存档路径', /runs\/ext-p02\//.test(html));
  check('历史生成列表存在', /历史生成/.test(html) && /复制/.test(html));

  /* 模拟点「收起」→ 正文收起，但卡片与「展开」必须还在 */
  S.extCollapsed = true;
  AF.afterRender();
  html = box.innerHTML;
  check('收起后仍在页面（不消失）', /测试标题/.test(html));
  check('收起后有「展开」按钮', /展开/.test(html), '收起后没有展开入口');
  check('收起后正文隐藏', !/段落一/.test(html), '收起后正文还在');
  check('收起后结果不丢', !!S.extResult);

  /* 再展开 → 正文回来 */
  S.extCollapsed = false;
  AF.afterRender();
  html = box.innerHTML;
  check('展开后正文回来', /段落一/.test(html));

  console.log(fail ? `\n结论：${fail} 处需要修` : '\n结论：PASS');
  process.exit(fail ? 1 : 0);
}, 260);
