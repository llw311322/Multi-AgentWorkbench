/* 轻量图表：全部本地渲染，无外部依赖 */
(function (global) {
  const PALETTE = ["#2563eb", "#60a5fa", "#0ea5e9", "#14b8a6", "#6366f1",
                   "#f59e0b", "#ef4444", "#8b5cf6", "#10b981", "#ec4899"];

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function fmtSal(v) {
    if (v == null || isNaN(v)) return "—";
    return v < 10000 ? (v / 1000).toFixed(1) + "K" : (v / 10000).toFixed(2).replace(/0$/, "") + "万";
  }
  function fmtPct(v) { return v == null ? "—" : v + "%"; }
  function fmtNum(v) { return v == null ? "—" : Number(v).toLocaleString("zh-CN"); }

  /* 横向条形图（HTML 版本，天然自适应） */
  function bars(items, opt) {
    opt = opt || {};
    const valKey = opt.valueKey || "count";
    const fmt = opt.fmt || fmtNum;
    const showSub = opt.sub;
    const cls = opt.cls || "";
    if (!items || !items.length) return '<p class="muted small">无数据</p>';
    const max = Math.max.apply(null, items.map(i => Number(i[valKey]) || 0)) || 1;
    return items.map(function (it) {
      const v = Number(it[valKey]) || 0;
      const w = Math.max(1.5, Math.round(v / max * 100));
      let sub = "";
      if (showSub && it[showSub] != null) {
        sub = showSub === "count"
          ? '<span class="sub">' + it[showSub] + " 条</span>"
          : '<span class="sub">' + esc(fmt(it[showSub])) + "</span>";
      }
      return '<div class="bar-row"><div class="bar-label" title="' + esc(it.name) + '">' + esc(it.name) + "</div>" +
        '<div class="bar-track"><div class="bar-fill ' + cls + '" style="width:' + w + '%"></div></div>' +
        '<div class="bar-val">' + esc(fmt(v)) + sub + "</div></div>";
    }).join("");
  }

  /* 环形图 */
  function donut(items, opt) {
    opt = opt || {};
    if (!items || !items.length) return '<p class="muted small">无数据</p>';
    const total = items.reduce((a, b) => a + (Number(b.count) || 0), 0) || 1;
    const R = 52, CX = 62, CY = 62, SW = 18;
    const C = 2 * Math.PI * R;
    let acc = 0, segs = "";
    items.forEach(function (it, i) {
      const val = Number(it.count) || 0;
      const frac = val / total;
      const dash = Math.max(0, frac * C - 2);
      segs += '<circle cx="' + CX + '" cy="' + CY + '" r="' + R + '" fill="none" stroke="' +
        PALETTE[i % PALETTE.length] + '" stroke-width="' + SW + '" stroke-linecap="butt" ' +
        'stroke-dasharray="' + dash.toFixed(2) + " " + (C - dash).toFixed(2) + '" ' +
        'stroke-dashoffset="' + (-acc * C).toFixed(2) + '" transform="rotate(-90 ' + CX + " " + CY + ')"></circle>';
      acc += frac;
    });
    const center = opt.center || total;
    const centerLabel = opt.centerLabel || "总计";
    const legend = items.map(function (it, i) {
      const pct = total ? Math.round((Number(it.count) || 0) / total * 1000) / 10 : 0;
      return '<div><i style="background:' + PALETTE[i % PALETTE.length] + '"></i>' + esc(it.name) +
        '<span class="n">' + pct + "%</span></div>";
    }).join("");
    return '<div class="donut-wrap"><svg viewBox="0 0 124 124" width="124" height="124" style="flex:none">' +
      '<circle cx="' + CX + '" cy="' + CY + '" r="' + R + '" fill="none" stroke="#f1f3f5" stroke-width="' + SW + '"></circle>' +
      segs +
      '<text x="' + CX + '" y="' + (CY - 2) + '" text-anchor="middle" font-size="17" font-weight="650" fill="#111827">' + esc(center) + "</text>" +
      '<text x="' + CX + '" y="' + (CY + 14) + '" text-anchor="middle" font-size="9.5" fill="#6b7280">' + esc(centerLabel) + "</text>" +
      "</svg><div class=\"donut-legend\">" + legend + "</div></div>";
  }

  /* 纵向柱状图（薪资分布） */
  function hist(items, opt) {
    opt = opt || {};
    if (!items || !items.length) return '<p class="muted small">无数据</p>';
    const W = 620, H = 176, PAD_L = 8, PAD_B = 26, PAD_T = 14;
    const n = items.length;
    const innerW = W - PAD_L * 2;
    const slot = innerW / n;
    const barW = Math.min(58, slot * 0.56);
    const max = Math.max.apply(null, items.map(i => Number(i.count) || 0)) || 1;
    const plotH = H - PAD_T - PAD_B;
    let out = '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" height="' + H + '" preserveAspectRatio="xMidYMid meet">';
    items.forEach(function (it, i) {
      const v = Number(it.count) || 0;
      const h = Math.max(v ? 3 : 0, (v / max) * plotH);
      const x = PAD_L + slot * i + (slot - barW) / 2;
      const y = PAD_T + plotH - h;
      const pct = it.share != null ? it.share : 0;
      out += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + barW.toFixed(1) +
        '" height="' + h.toFixed(1) + '" rx="4" fill="url(#histGrad)"></rect>';
      out += '<text x="' + (x + barW / 2).toFixed(1) + '" y="' + (y - 5).toFixed(1) +
        '" text-anchor="middle" font-size="11" fill="#111827" font-weight="600">' + v + "</text>";
      out += '<text x="' + (x + barW / 2).toFixed(1) + '" y="' + (PAD_T + plotH + 15) +
        '" text-anchor="middle" font-size="10" fill="#6b7280">' + esc(it.name) + "</text>";
      if (pct) {
        out += '<text x="' + (x + barW / 2).toFixed(1) + '" y="' + (PAD_T + plotH + 26) +
          '" text-anchor="middle" font-size="9" fill="#9ca3af">' + pct + "%</text>";
      }
    });
    out += '<defs><linearGradient id="histGrad" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#60a5fa"/><stop offset="100%" stop-color="#2563eb"/>' +
      "</linearGradient></defs></svg>";
    return out;
  }

  global.C = { bars: bars, donut: donut, hist: hist, fmtSal: fmtSal, fmtPct: fmtPct, fmtNum: fmtNum,
               esc: esc, PALETTE: PALETTE };
})(window);
