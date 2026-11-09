/* 前端逻辑：对话 + 看板渲染 */
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const params = new URLSearchParams(location.search);
  const state = {
    sid: params.get("sid") || localStorage.getItem("jdagent_sid") ||
         ("s_" + Math.random().toString(36).slice(2, 10)),
    analysis: null,
    jobs: [],
    busy: false,
    tab: params.get("tab") || "overview",
  };
  localStorage.setItem("jdagent_sid", state.sid);

  /* ---------------- 基础工具 ---------------- */
  function md(text) {
    const lines = String(text || "").split("\n");
    let html = "", list = false;
    const inline = (s) => s
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
    for (let raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (!line.trim()) { if (list) { html += "</ul>"; list = false; } continue; }
      if (/^[•\-·]\s+/.test(line.trim())) {
        if (!list) { html += "<ul>"; list = true; }
        html += "<li>" + inline(line.trim().replace(/^[•\-·]\s+/, "")) + "</li>";
        continue;
      }
      if (list) { html += "</ul>"; list = false; }
      if (/^⚠/.test(line.trim())) { html += '<div class="warnline">' + inline(line.trim()) + "</div>"; continue; }
      html += "<p>" + inline(line) + "</p>";
    }
    if (list) html += "</ul>";
    return html;
  }

  function setStatus(txt) { $("#statusText").textContent = txt; }

  function scrollDown() {
    const box = $("#messages");
    box.scrollTop = box.scrollHeight;
  }

  function addUser(text) {
    const div = document.createElement("div");
    div.className = "msg user";
    div.innerHTML = '<div class="bubble"></div>';
    div.firstChild.textContent = text;
    $("#messages").appendChild(div);
    scrollDown();
  }

  function addBot(text, opts) {
    opts = opts || {};
    const div = document.createElement("div");
    div.className = "msg bot" + (opts.plain ? " plain" : "");
    let html = '<div class="md">' + md(text) + "</div>";
    if (opts.pills && opts.pills.length) {
      html += '<div class="meta-row">' + opts.pills.map(p =>
        '<span class="pill' + (p.gray ? " gray" : "") + '">' + C.esc(p.text) + "</span>").join("") + "</div>";
    }
    div.innerHTML = html;
    $("#messages").appendChild(div);
    scrollDown();
    return div;
  }

  function thinking() {
    const div = document.createElement("div");
    div.className = "msg bot plain";
    div.innerHTML = '<div class="md"><div class="thinking">' +
      ["解析需求，生成检索计划", "采集岗位列表", "清洗字段、去重归一", "计算分布与技能溢价", "生成结论"]
        .map((t, i) => '<div class="step' + (i === 0 ? "" : " wait") + '"><span class="spin"></span>' + t + "</div>")
        .join("") + "</div></div>";
    $("#messages").appendChild(div);
    scrollDown();
    let idx = 0;
    const timer = setInterval(() => {
      const steps = $$(".step", div);
      if (idx < steps.length) steps[idx].classList.add("done");
      idx++;
      if (idx >= steps.length) clearInterval(timer);
    }, 1300);
    return { el: div, stop: () => { clearInterval(timer); $$(".step", div).forEach(s => s.classList.add("done")); } };
  }

  /* ---------------- 接口 ---------------- */
  async function api(path, body) {
    const res = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ sid: state.sid }, body || {})),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("请求失败 " + res.status));
    return data;
  }

  async function send(text) {
    if (state.busy || !text.trim()) return;
    state.busy = true;
    $("#btnSend").disabled = true;
    addUser(text);
    $("#input").value = "";
    const th = thinking();
    setStatus("采集中…");
    try {
      const data = await api("/api/chat", { message: text });
      th.stop();
      setTimeout(() => th.el.remove(), 400);
      const pills = [];
      if (data.meta && data.meta.count) pills.push({ text: data.meta.count + " 条岗位" });
      if (data.meta && data.meta.elapsed) pills.push({ text: data.meta.elapsed + "s" });
      if (data.intent === "followup") pills.push({ text: "基于上一轮结果" });
      if (data.warnings && data.warnings.length) pills.push({ text: data.warnings.length + " 条采集提示", gray: true });
      addBot(data.reply, { pills: pills });
      if (data.analysis) {
        state.analysis = data.analysis;
        state.jobs = data.jobs || [];
        renderAll();
        $("#btnReport").disabled = false;
      }
      if (data.settings) renderBadges(data.settings.sources);
      setStatus("就绪");
    } catch (e) {
      th.stop();
      setTimeout(() => th.el.remove(), 300);
      addBot("出错了：" + e.message, { plain: true });
      setStatus("出错");
    } finally {
      state.busy = false;
      $("#btnSend").disabled = false;
    }
  }

  /* ---------------- 看板 ---------------- */
  function kpi(v, l, t) {
    return '<div class="kpi"><div class="v">' + v + '</div><div class="l">' + l + "</div>" +
      (t ? '<div class="t">' + t + "</div>" : "") + "</div>";
  }
  function card(title, body, note) {
    return '<div class="card"><h3>' + title + (note ? '<span class="note">' + note + "</span>" : "") +
      "</h3>" + body + "</div>";
  }

  function renderAll() {
    const an = state.analysis;
    if (!an) return;
    $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === state.tab));
    const panels = $("#panels");
    panels.innerHTML = {
      overview: viewOverview(an),
      salary: viewSalary(an),
      demand: viewDemand(an),
      skills: viewSkills(an),
      company: viewCompany(an),
      match: viewMatch(an),
      jobs: viewJobs(state.jobs),
    }[state.tab] || viewOverview(an);
    bindPanelEvents();
  }

  function viewOverview(an) {
    const ov = an.overview, s = ov.salary || {};
    let h = '<div class="kpis">' +
      kpi(C.fmtSal(s.median), "月薪中位数", "均值 " + C.fmtSal(s.mean)) +
      kpi(ov.total, "岗位数", ov.companies + " 家公司") +
      kpi(C.fmtSal(s.p25) + "–" + C.fmtSal(s.p75), "P25–P75 区间", "P90 " + C.fmtSal(s.p90)) +
      kpi(C.fmtPct(ov.salary_coverage), "薪资披露率", ov.salary_covered + " 条有明确薪资") +
      kpi(C.fmtPct(ov.jd_coverage), "JD 完整率", "技能识别更准") +
      kpi(ov.cities, "覆盖城市", "") +
      "</div>";
    if (an.insights && an.insights.length) {
      h += card("核心结论", an.insights.map(i =>
        '<div class="insight ' + (i.type || "info") + '"><b>' + C.esc(i.title) + "</b>" + C.esc(i.text) + "</div>").join(""));
    }
    h += card("薪资分布", C.hist(an.salary.hist, {}), "按折算月薪分桶");
    h += '<div class="grid2">' +
      card("学历要求", C.donut(an.demand.edu.slice(0, 5), { center: an.overview.total, centerLabel: "岗位" })) +
      card("经验要求", C.bars(an.demand.exp)) + "</div>";
    h += card("数据质量体检", C.bars(an.quality, { sub: "count" }).replace(/class="bar-fill /g, 'class="bar-fill g '),
              "各字段可用比例");
    return h;
  }

  function viewSalary(an) {
    const s = an.overview.salary || {};
    let h = '<div class="kpis">' +
      kpi(C.fmtSal(s.median), "中位数") + kpi(C.fmtSal(s.mean), "均值") +
      kpi(C.fmtSal(s.p10), "P10") + kpi(C.fmtSal(s.p90), "P90") +
      kpi(s.stdev ? C.fmtSal(s.stdev) : "—", "标准差") +
      kpi(Math.round((s.iqr || 0) / Math.max(1, s.median || 1) * 100) + "%", "离散度 IQR/中位") +
      "</div>";
    h += card("薪资分布", C.hist(an.salary.hist, {}));
    h += card("各城市中位薪资", C.bars(an.salary.byCity, { valueKey: "median", fmt: C.fmtSal, sub: "count" }),
              "条形=中位薪资，灰字=岗位数");
    h += '<div class="grid2">' +
      card("按经验", C.bars(an.salary.byExp, { valueKey: "median", fmt: C.fmtSal, sub: "count" })) +
      card("按学历", C.bars(an.salary.byEdu, { valueKey: "median", fmt: C.fmtSal, sub: "count" })) +
      "</div>";
    h += '<div class="grid2">' +
      card("按公司规模", C.bars(an.salary.bySize, { valueKey: "median", fmt: C.fmtSal, sub: "count" })) +
      card("按行业", C.bars(an.salary.byIndustry, { valueKey: "median", fmt: C.fmtSal, sub: "count" })) +
      "</div>";
    return h;
  }

  function viewDemand(an) {
    const d = an.demand;
    let h = '<div class="kpis">' +
      kpi(d.edu.length ? d.edu.reduce((a, b) => a.count > b.count ? a : b).name : "—",
          "主流学历",
          d.edu.length ? d.edu.reduce((a, b) => a.count > b.count ? a : b).share + "%" : "") +
      kpi(d.exp.length ? d.exp.reduce((a, b) => a.count > b.count ? a : b).name : "—", "主流经验段") +
      kpi(an.skills.unique, "识别到技能种类", "平均 " + an.skills.avg_per_job + " 项/条") +
      kpi(an.companies.unique, "公司数", "平均 " + an.companies.avg_per_company + " 岗/家") +
      "</div>";
    h += '<div class="grid2">' +
      card("学历要求", C.donut(d.edu.slice(0, 5), { center: an.overview.total, centerLabel: "岗位" })) +
      card("经验要求", C.bars(d.exp)) + "</div>";
    h += '<div class="grid2">' +
      card("行业分布", C.bars(d.industry)) +
      card("公司规模", C.bars(d.size)) + "</div>";
    h += '<div class="grid2">' +
      card("公司性质", C.bars(d.companyType)) +
      card("城市分布", C.bars(d.cities)) + "</div>";
    return h;
  }

  function viewSkills(an) {
    const sk = an.skills;
    let h = '<div class="kpis">' +
      kpi(sk.unique, "技能种类") + kpi(sk.avg_per_job, "平均技能数/条") +
      kpi(sk.top.length ? sk.top[0].name : "—", "最高频技能",
          sk.top.length ? sk.top[0].count + " 条 JD 提及" : "") +
      "</div>";
    const rows = sk.top.map(t => {
      const lift = t.lift;
      const cls = lift > 0 ? "lift-up" : "lift-down";
      return "<tr><td><strong>" + C.esc(t.name) + "</strong></td>" +
        '<td class="num">' + t.count + "</td>" +
        '<td class="num">' + C.fmtPct(t.share) + "</td>" +
        '<td class="num">' + C.fmtSal(t.median) + "</td>" +
        '<td class="num ' + cls + '">' + (lift ? (lift > 0 ? "+" : "") + C.fmtSal(lift) : "—") + "</td>" +
        "<td>" + C.esc(t.category) + "</td></tr>";
    }).join("");
    h += card("技能需求 Top 24",
      '<div class="tbl-wrap"><table><thead><tr><th>技能</th><th class="num">出现</th>' +
      '<th class="num">覆盖率</th><th class="num">中位薪资</th><th class="num">相对溢价</th>' +
      "<th>能力方向</th></tr></thead><tbody>" + rows + "</tbody></table></div>",
      "溢价 = 含该技能岗位中位薪资 − 全样本中位薪资");
    h += '<div class="grid2">' +
      card("能力方向分布", C.donut(sk.categories.slice(0, 6), { center: sk.unique, centerLabel: "技能" })) +
      card("技能共现 Top", sk.cooccur.length ? sk.cooccur.map(c =>
        '<div class="bar-row" style="grid-template-columns:1fr 1fr 46px">' +
        '<div class="bar-label">' + C.esc(c.a) + "</div>" +
        '<div class="bar-label" style="text-align:right">' + C.esc(c.b) + "</div>" +
        '<div class="bar-val">' + c.count + "×</div></div>").join("")
        : '<p class="muted small">样本不足</p>', "同一份 JD 中同时出现") +
      "</div>";
    return h;
  }

  function viewCompany(an) {
    const c = an.companies;
    let h = '<div class="kpis">' +
      kpi(c.unique, "公司数") + kpi(c.avg_per_company, "平均岗位/家") +
      kpi(C.fmtPct(c.top10_share), "Top10 集中度") +
      "</div>";
    const rows = c.top.map((x, i) =>
      "<tr><td>" + (i + 1) + "</td><td><strong>" + C.esc(x.name) + "</strong></td>" +
      '<td class="num">' + x.count + "</td>" +
      '<td class="num">' + C.fmtSal(x.median) + "</td>" +
      "<td>" + C.esc(x.industry || "—") + "</td>" +
      "<td>" + C.esc(x.size || "—") + "</td></tr>").join("");
    h += card("招聘量 Top 公司",
      '<div class="tbl-wrap"><table><thead><tr><th>#</th><th>公司</th><th class="num">在招</th>' +
      '<th class="num">中位薪资</th><th>行业</th><th>规模</th></tr></thead><tbody>' + rows +
      "</tbody></table></div>");
    h += card("岗位分布热度", C.bars(c.top.slice(0, 12).map(x => ({ name: x.name, count: x.count }))));
    return h;
  }

  function viewMatch(an) {
    const m = an.match;
    if (!m) {
      return '<div class="empty"><div class="empty-icon">🎯</div><h3>还没有你的画像</h3>' +
        "<p>在左侧告诉我你的情况，例如：<br><code>我会 Python、SQL，4 年经验，本科，期望 25K</code></p></div>";
    }
    let h = '<div class="kpis">' +
      kpi(m.avg_score, "平均匹配度") +
      kpi(m.strong_fit, "≥70 分岗位") +
      kpi(m.salary_hit == null ? "—" : C.fmtPct(m.salary_hit), "期望薪资达标率") +
      kpi(C.fmtSal(m.profile.expect), "期望月薪") +
      "</div>";
    if (m.dims) h += card("各维度得分", C.bars(
      Object.keys(m.dims).map(k => ({ name: k, count: m.dims[k] })), { fmt: v => v + " 分" }));
    const rows = m.top.map(t => {
      const cls = t.score >= 70 ? "hi" : t.score >= 50 ? "mid" : "lo";
      return "<tr><td>" + (t.url ? '<a href="' + C.esc(t.url) + '" target="_blank">' + C.esc(t.title) + "</a>" : C.esc(t.title)) + "</td>" +
        "<td>" + C.esc(t.company) + "</td><td>" + C.esc(t.salary) + "</td><td>" + C.esc(t.city) + "</td>" +
        '<td class="num"><span class="score ' + cls + '">' + t.score + "</span></td>" +
        "<td>" + (t.matched.map(s => '<span class="tag">' + C.esc(s) + "</span>").join("") || '<span class="muted small">—</span>') + "</td>" +
        "<td>" + (t.missing.slice(0, 4).map(s => '<span class="tag">' + C.esc(s) + "</span>").join("") || "—") + "</td></tr>";
    }).join("");
    h += card("最匹配的岗位 Top 10",
      '<div class="tbl-wrap"><table><thead><tr><th>岗位</th><th>公司</th><th>薪资</th><th>城市</th>' +
      '<th class="num">匹配分</th><th>命中技能</th><th>缺失技能</th></tr></thead><tbody>' + rows +
      "</tbody></table></div>", "匹配分 = 技能 50% + 薪资 20% + 经验 18% + 学历 12%");
    if (m.gaps && m.gaps.length) h += card("技能缺口（按 JD 需求频次）", C.bars(m.gaps, { sub: "share" }));
    return h;
  }

  function viewJobs(jobs) {
    if (!jobs || !jobs.length) return '<div class="empty"><h3>暂无明细</h3></div>';
    let h = '<div class="toolbar">' +
      '<input type="search" id="jobFilter" placeholder="筛选岗位 / 公司 / 技能…">' +
      '<span class="muted small" id="jobCount">' + jobs.length + " 条</span>" +
      '<div class="spacer"></div>' +
      '<button class="btn small" id="btnCsv">下载 CSV</button></div>';
    h += '<div class="tbl-wrap" style="max-height:calc(100vh - 260px)"><table id="jobTable"><thead><tr>' +
      "<th>岗位</th><th>公司</th><th>薪资</th><th>城市</th><th>经验</th><th>学历</th>" +
      "<th>技能</th><th>发布</th><th>来源</th></tr></thead><tbody>" +
      jobs.map(jobRow).join("") + "</tbody></table></div>";
    return h;
  }

  function jobRow(j) {
    const link = j.url ? '<a href="' + C.esc(j.url) + '" target="_blank">' + C.esc(j.title) + "</a>" : C.esc(j.title);
    const src = { zhaopin: "智联", "51job": "51job", import: "导入" }[j.source] || (j.source || "");
    return "<tr data-k='" + C.esc((j.title + " " + j.company + " " + (j.skills || []).join(" ")).toLowerCase()) + "'>" +
      "<td>" + link + "</td><td>" + C.esc(j.company) + "</td>" +
      '<td class="num">' + C.esc(j.salary || "面议") + "</td>" +
      "<td>" + C.esc(j.city || "") + "</td><td>" + C.esc(j.exp || "") + "</td>" +
      "<td>" + C.esc(j.edu || "") + "</td>" +
      "<td>" + (j.skills || []).slice(0, 4).map(s => '<span class="tag">' + C.esc(s) + "</span>").join("") + "</td>" +
      '<td class="nowrap">' + C.esc(j.publish || "") + '</td><td class="nowrap">' + C.esc(src) + "</td></tr>";
  }

  function bindPanelEvents() {
    const f = $("#jobFilter");
    if (f) {
      f.addEventListener("input", () => {
        const q = f.value.trim().toLowerCase();
        let n = 0;
        $$("#jobTable tbody tr").forEach(tr => {
          const hit = !q || (tr.dataset.k || "").indexOf(q) >= 0;
          tr.style.display = hit ? "" : "none";
          if (hit) n++;
        });
        $("#jobCount").textContent = n + " 条";
      });
    }
    const csv = $("#btnCsv");
    if (csv) csv.addEventListener("click", downloadCsv);
  }

  function downloadCsv() {
    const cols = [["title", "岗位"], ["company", "公司"], ["salary", "薪资"], ["city", "城市"],
                  ["exp", "经验"], ["edu", "学历"], ["industry", "行业"], ["size", "公司规模"],
                  ["skills", "技能"], ["publish", "发布时间"], ["url", "链接"]];
    const lines = [cols.map(c => c[1]).join(",")];
    state.jobs.forEach(j => {
      lines.push(cols.map(c => {
        let v = j[c[0]];
        if (Array.isArray(v)) v = v.join("、");
        return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
      }).join(","));
    });
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "岗位明细_" + new Date().toISOString().slice(0, 10) + ".csv";
    a.click();
  }

  /* ---------------- 数据源 / 设置 ---------------- */
  function renderBadges(sources) {
    if (!sources) return;
    $("#sourceBadges").innerHTML = sources.map(s => {
      const cls = s.ready ? "" : (s.requires_cookie ? "warn" : "off");
      const txt = s.ready ? s.label : s.label + "（未配置）";
      return '<span class="badge ' + cls + '"><span class="dot"></span>' + C.esc(txt) + "</span>";
    }).join("");
  }

  function renderSourceList(sources) {
    $("#sourceList").innerHTML = (sources || []).map(s =>
      '<div class="source-item"><div class="t">' +
      '<span class="badge ' + (s.ready ? "" : "warn") + '"><span class="dot"></span>' +
      (s.ready ? "可用" : "需配置") + "</span>" + C.esc(s.label) + "</div>" +
      '<div class="d">' + C.esc(s.note || (s.ready ? "可直接采集公开搜索结果" : "需要凭证")) + "</div></div>").join("");
  }

  async function loadSession() {
    try {
      const r = await fetch("/api/session?sid=" + encodeURIComponent(state.sid));
      const d = await r.json();
      if (d.settings) {
        renderBadges(d.settings.sources);
        renderSourceList(d.settings.sources);
      }
      if (d.analysis) {
        state.analysis = d.analysis;
        state.jobs = d.jobs || [];
        renderAll();
        $("#btnReport").disabled = false;
        addBot("已恢复上次的分析结果（" + (d.query.keywords || []).join("、") + "）。可以继续追问。",
               { plain: true });
      }
    } catch (e) { /* ignore */ }
  }

  /* ---------------- 事件绑定 ---------------- */
  $("#btnSend").addEventListener("click", () => send($("#input").value));
  $("#input").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#input").value); }
  });
  $("#chips").addEventListener("click", e => {
    const b = e.target.closest(".chip");
    if (b) send(b.dataset.q);
  });
  $("#tabs").addEventListener("click", e => {
    const b = e.target.closest(".tab");
    if (!b) return;
    $$(".tab").forEach(t => t.classList.toggle("active", t === b));
    state.tab = b.dataset.tab;
    if (state.analysis) renderAll();
  });
  $("#btnReport").addEventListener("click", () => {
    window.open("/api/report?sid=" + encodeURIComponent(state.sid), "_blank");
  });
  $("#btnSettings").addEventListener("click", async () => {
    $("#modal").hidden = false;
    const r = await fetch("/api/session?sid=" + encodeURIComponent(state.sid));
    const d = await r.json();
    if (d.settings) renderSourceList(d.settings.sources);
  });
  $("#btnCloseModal").addEventListener("click", () => { $("#modal").hidden = true; });
  $("#modal").addEventListener("click", e => { if (e.target.id === "modal") $("#modal").hidden = true; });
  $("#btnSaveSettings").addEventListener("click", async () => {
    const cookie = $("#cookieInput").value.trim();
    await api("/api/settings", {
      cookies: cookie ? { "51job": cookie } : {},
      deep_detail: $("#deepDetail").checked,
    });
    $("#modal").hidden = true;
    addBot(cookie ? "前程无忧 Cookie 已保存，下次采集会自动带上。" : "设置已保存。",
           { plain: true });
    loadSession();
  });
  $("#fileInput").addEventListener("change", async e => {
    const f = e.target.files[0];
    if (!f) return;
    const text = await f.text();
    addUser("导入文件：" + f.name + "（" + Math.round(text.length / 1024) + " KB）");
    const th = thinking();
    try {
      const d = await api("/api/import", { text: text, hint: $("#input").value || "" });
      th.stop();
      setTimeout(() => th.el.remove(), 300);
      addBot(d.reply, { pills: [{ text: (d.meta || {}).count + " 条" }] });
      state.analysis = d.analysis;
      state.jobs = d.jobs || [];
      renderAll();
      $("#btnReport").disabled = false;
    } catch (err) {
      th.stop();
      setTimeout(() => th.el.remove(), 300);
      addBot("导入失败：" + err.message, { plain: true });
    }
    e.target.value = "";
  });

  /* 拖拽导入 */
  document.addEventListener("dragover", e => { e.preventDefault(); });
  document.addEventListener("drop", async e => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (!f) return;
    const text = await f.text();
    addUser("拖入文件：" + f.name);
    try {
      const d = await api("/api/import", { text: text });
      addBot(d.reply, { pills: [{ text: (d.meta || {}).count + " 条" }] });
      state.analysis = d.analysis;
      state.jobs = d.jobs || [];
      renderAll();
    } catch (err) { addBot("导入失败：" + err.message, { plain: true }); }
  });

  loadSession();
})();
