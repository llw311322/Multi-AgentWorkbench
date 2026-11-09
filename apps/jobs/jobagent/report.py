"""独立 HTML 报告渲染（无外部依赖，纯 CSS 条形图 + 表格，可直接分享）。"""
from __future__ import annotations

import html as H
from datetime import datetime
from typing import Any


def _k(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.1f}K" if v < 10000 else f"{v / 10000:.2f}万"


def _pct(v: Any) -> str:
    return f"{v}%" if v is not None else "—"


def _bars(items: list[dict[str, Any]], value_key: str = "count", label_key: str = "name",
          fmt=None, show_median: bool = False) -> str:
    if not items:
        return '<p class="muted">无数据</p>'
    mx = max((it.get(value_key) or 0) for it in items) or 1
    rows = []
    for it in items:
        v = it.get(value_key) or 0
        w = max(2, round(v / mx * 100, 1))
        val = fmt(v) if fmt else v
        extra = f'<span class="sub">{_k(it.get("median"))}</span>' if show_median and it.get("median") else ""
        rows.append(
            f'<div class="bar-row"><div class="bar-label">{H.escape(str(it.get(label_key, "")))}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{w}%"></div></div>'
            f'<div class="bar-val">{val}{extra}</div></div>')
    return "".join(rows)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    th = "".join(f"<th>{H.escape(h)}</th>" for h in headers)
    trs = []
    for r in rows:
        tds = "".join(f"<td>{c if isinstance(c, str) and c.startswith('<') else H.escape(str(c))}</td>"
                      for c in r)
        trs.append(f"<tr>{tds}</tr>")
    return f'<table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'


CSS = """
:root{--ink:#1a1d21;--muted:#6b7280;--line:#e5e7eb;--bg:#f7f8fa;--card:#fff;--accent:#2563eb;
--accent2:#0ea5e9;--warn:#b45309;--warnbg:#fffbeb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 64px}
header{margin-bottom:24px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.3px}
.tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:999px;padding:2px 10px;
font-size:12px;margin-right:6px}
.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;
margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 14px;font-weight:600;display:flex;align-items:center;gap:8px}
.card h2::before{content:"";width:3px;height:14px;background:var(--accent);border-radius:2px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi .v{font-size:22px;font-weight:650;letter-spacing:-.5px}
.kpi .l{font-size:12px;color:var(--muted);margin-top:2px}
.bar-row{display:grid;grid-template-columns:150px 1fr 78px;align-items:center;gap:10px;margin-bottom:7px}
.bar-label{font-size:12.5px;color:#374151;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{background:#f1f3f5;border-radius:4px;height:18px;overflow:hidden}
.bar-fill{background:linear-gradient(90deg,#3b82f6,#60a5fa);height:100%;border-radius:4px}
.bar-val{font-size:12px;text-align:right;color:#111827;font-variant-numeric:tabular-nums}
.bar-val .sub{color:var(--muted);margin-left:5px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;background:#fafbfc;position:sticky;top:0}
td a{color:var(--accent);text-decoration:none}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.insight{border-left:3px solid var(--accent);background:#f8fafc;padding:10px 14px;border-radius:0 8px 8px 0;
margin-bottom:10px}
.insight.warn{border-color:#f59e0b;background:var(--warnbg)}
.insight.action{border-color:#10b981;background:#f0fdf4}
.insight b{display:block;font-size:12px;color:var(--muted);font-weight:600;margin-bottom:2px;
letter-spacing:.3px}
footer{margin-top:32px;color:var(--muted);font-size:12px;text-align:center}
@media(max-width:760px){.grid2{grid-template-columns:1fr}.bar-row{grid-template-columns:100px 1fr 62px}}
"""


def render(res: dict[str, Any], jobs: list[dict[str, Any]], query: dict[str, Any],
           profile: dict[str, Any] | None = None) -> str:
    ov = res.get("overview", {})
    sal = res.get("salary", {})
    dem = res.get("demand", {})
    s = ov.get("salary") or {}
    kw = "、".join(query.get("keywords") or []) or "岗位"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    kpis = [
        (_k(s.get("median")), "月薪中位数"),
        (_k(s.get("mean")), "平均月薪"),
        (ov.get("total", 0), "岗位数"),
        (ov.get("companies", 0), "公司数"),
        (_pct(ov.get("salary_coverage")), "薪资披露率"),
        (f'{_k(s.get("p25"))}–{_k(s.get("p75"))}', "P25–P75 区间"),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div></div>'
                       for v, l in kpis)

    insights = "".join(
        f'<div class="insight {i.get("type", "info")}"><b>{H.escape(i["title"])}</b>'
        f'{H.escape(i["text"])}</div>' for i in res.get("insights", []))

    sk = res.get("skills", {})
    skill_rows = []
    for t in sk.get("top", [])[:20]:
        lift = t.get("lift")
        lift_s = f'<span style="color:{"#dc2626" if (lift or 0) > 0 else "#6b7280"}">' \
                 f'{("+" if (lift or 0) > 0 else "") + _k(lift) if lift else "—"}</span>'
        skill_rows.append([H.escape(t["name"]), t["count"], _pct(t["share"]),
                           _k(t.get("median")), lift_s, H.escape(t.get("category", ""))])

    job_rows = []
    for j in jobs[:80]:
        link = j.get("url") or ""
        title = f'<a href="{H.escape(link)}" target="_blank">{H.escape(j.get("title") or "")}</a>' \
            if link else H.escape(j.get("title") or "")
        job_rows.append([title, H.escape(j.get("company") or ""), j.get("salary_text") or "面议",
                         _k(j.get("salary_mid")), H.escape(j.get("city") or ""),
                         j.get("exp_text") or "", j.get("edu") or "",
                         "、".join((j.get("skills") or [])[:5]),
                         {"zhaopin": "智联", "liepin": "猎聘"}.get(j.get("source"), j.get("source") or "")])

    match_html = ""
    if res.get("match"):
        m = res["match"]
        rows = [[r["title"], r["company"], r["salary"], r["city"], f'{r["score"]}',
                 "、".join(r["matched"][:4]), "、".join(r["missing"][:4])]
                for r in m.get("top", [])]
        match_html = f"""
<div class="card"><h2>人岗匹配（Top 10）</h2>
<p class="muted">你的技能：{H.escape("、".join(m["profile"].get("skills") or []) or "未提供")}
｜经验：{m["profile"].get("years") or "—"} 年｜学历：{m["profile"].get("edu") or "—"}
｜期望：{_k(m["profile"].get("expect"))}</p>
<p>平均匹配度 <b>{m.get("avg_score")}</b> 分，<b>{m.get("strong_fit")}</b> 个岗位 ≥70 分；
期望薪资达标率 <b>{_pct(m.get("salary_hit"))}</b>。</p>
{_table(["岗位", "公司", "薪资", "城市", "匹配分", "命中技能", "缺失技能"], rows)}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>「{H.escape(kw)}」招聘市场分析报告</title><style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <h1>「{H.escape(kw)}」招聘市场分析报告</h1>
  <div class="muted">数据源：{H.escape("、".join(x["name"] + f' {x["count"]}' for x in ov.get("sources", [])) or "—")}
   ｜城市：{H.escape(query.get("city") or "全国")} ｜生成时间：{now}</div>
</header>
<div class="kpis">{kpi_html}</div>
<div class="card"><h2>核心结论</h2>{insights}</div>
<div class="grid2">
  <div class="card"><h2>薪资分布</h2>{_bars(sal.get("hist", []))}</div>
  <div class="card"><h2>学历要求</h2>{_bars(dem.get("edu", []))}</div>
</div>
<div class="grid2">
  <div class="card"><h2>经验要求</h2>{_bars(dem.get("exp", []))}</div>
  <div class="card"><h2>城市分布（含中位薪资）</h2>{_bars(dem.get("cities", []), show_median=True)}</div>
</div>
<div class="card"><h2>各城市薪资中位数</h2>{_bars(sal.get("byCity", []), value_key="median", fmt=_k)}</div>
<div class="grid2">
  <div class="card"><h2>按经验看薪资</h2>{_bars(sal.get("byExp", []), value_key="median", fmt=_k)}</div>
  <div class="card"><h2>按学历看薪资</h2>{_bars(sal.get("byEdu", []), value_key="median", fmt=_k)}</div>
</div>
<div class="grid2">
  <div class="card"><h2>公司规模分布</h2>{_bars(dem.get("size", []))}</div>
  <div class="card"><h2>行业分布</h2>{_bars(dem.get("industry", []))}</div>
</div>
<div class="card"><h2>技能需求 Top 20（含薪资溢价）</h2>
{_table(["技能", "出现次数", "覆盖率", "中位薪资", "相对溢价", "能力方向"], skill_rows)}
</div>
<div class="card"><h2>技能共现（同一份 JD 中同时出现）</h2>
{_table(["技能 A", "技能 B", "共现次数"],
        [[c["a"], c["b"], c["count"]] for c in sk.get("cooccur", [])])}
</div>
<div class="card"><h2>招聘量 Top 公司</h2>
{_table(["公司", "在招岗位", "中位薪资", "行业", "规模"],
        [[H.escape(c["name"]), c["count"], _k(c.get("median")), H.escape(c.get("industry") or ""),
          H.escape(c.get("size") or "")] for c in res.get("companies", {}).get("top", [])])}
</div>
{match_html}
<div class="card"><h2>岗位明细（前 80 条）</h2>
{_table(["岗位", "公司", "薪资", "折算月薪", "城市", "经验", "学历", "技能", "来源"], job_rows)}
</div>
<footer>由招聘 JD 采集分析 Agent 生成 · 数据为公开渠道采集，仅供决策参考</footer>
</div></body></html>"""
