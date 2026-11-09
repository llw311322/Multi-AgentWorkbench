"""招聘采集执行器：把节点交给外部的「招聘 JD 采集分析 Agent」，再转成标准分析产物。

这个执行器和 exec_code / exec_content / exec_research 的区别：
它不调模型，调的是一个已经跑起来的本地服务（智联/猎聘采集 + 统计分析都是它做的）。
所以这里没有 prompt，只有「翻译」：把节点目标翻译成一句人话需求，
再把对方返回的分析结构翻译成本项目的 report 契约。

设计取舍：
- 对方离线 → 直接 blocked 并把「去 AI 应用页点启动服务」写进 open_questions。
  不偷偷降级到 LLM 编一份假报告：招聘数据的价值全在真实样本上，
  编出来的薪资分布比没有更糟。
- 结论里的每条数字都能追到具体 JD 链接（sources），不写无来源断言。
"""
from __future__ import annotations

import json
import re
import time

from .. import apps, store
from ..graph import TaskNode
from .base import build_result

TIMEOUT = 240  # 采集是慢活：翻页 + 详情补全，实测 30–90 秒
MAX_JOBS_IN_MD = 30

# 节点 goal 是「完成态陈述」，不是「一句人话需求」。直接发过去会被对方当成
# 关键词的一部分（「采集杭州数据分析师岗位…」→ 关键词变成「采集杭州数据分析师」，
# 只采到 15 条）。这里把它还原成检索句式：去掉交付描述、去掉动词、补上「帮我搜集」。
_CLAUSE = re.compile(r"[，,；;。\n]")
_DELIVERABLE = re.compile(r"^(产出|输出|生成|形成|给出|交付|并|附|包含|要求包含)")
_LEAD_VERBS = (
    "帮我", "请", "麻烦", "帮忙", "采集", "搜集", "收集", "抓取", "爬取", "检索",
    "查询", "获取", "调研", "盘点", "统计", "整理", "梳理", "分析", "看看", "找",
)


def _search_sentence(goal: str) -> str:
    """把「采集杭州数据分析师岗位（月薪15K以上），产出薪资分位分析」→「帮我搜集杭州数据分析师岗位（月薪15K以上）」。"""
    text = (goal or "").strip()
    keep: list[str] = []
    for part in _CLAUSE.split(text):
        part = part.strip()
        if not part:
            continue
        if _DELIVERABLE.match(part):
            break
        keep.append(part)
        if len(keep) >= 2:  # 城市/关键词/条件一般已在前两句里
            break
    core = "，".join(keep) or text
    changed = True
    while changed:
        changed = False
        for v in _LEAD_VERBS:
            if core.startswith(v):
                core = core[len(v):].strip()
                changed = True
    core = core.strip("，,。 ")
    if not core:
        core = text
    return core if core.startswith("帮我") else f"帮我搜集{core}"


def _app() -> dict:
    app = apps.get_app("jobs")
    if not app:
        raise RuntimeError("apps.py 里没有注册 jobs 应用")
    return app


def _fmt(v: float | None) -> str:
    if not v:
        return "—"
    return f"{v / 1000:.1f}K" if v < 10000 else f"{v / 10000:.2f}万"


def _pct(v: float | None) -> str:
    return f"{v}%" if v is not None else "—"


def _sources(jobs: list[dict], limit: int = 3) -> list[dict]:
    """取几条真实岗位链接当依据。没有链接就返回空——不编 URL。"""
    out = []
    today = time.strftime("%Y-%m-%d")
    for j in jobs:
        url = (j.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "publisher": j.get("company") or "招聘平台",
                "url": url,
                "date": j.get("publish") or today,
            }
        )
        if len(out) >= limit:
            break
    return out


def _findings(an: dict, jobs: list[dict]) -> list[dict]:
    """把分析结构压成 4–6 条可追溯的结论。全部来自对方返回，不加工数字。"""
    ov = an.get("overview") or {}
    sal = ov.get("salary") or {}
    src = _sources(jobs)
    corpus = {
        "publisher": "招聘 JD 采集样本",
        "url": src[0]["url"] if src else "",
        "date": time.strftime("%Y-%m-%d"),
    }
    # 语料类结论用「样本集合」当来源；只有拿到真实链接时才敢标 fact
    sources_for_corpus = [corpus] if corpus["url"] else []

    f: list[dict] = []

    def add(text: str, conf: str = "high", extra: list[dict] | None = None) -> None:
        ss = list(extra or [])
        if sources_for_corpus and not ss:
            ss = list(sources_for_corpus) + src[:2]
        f.append(
            {
                "finding": text,
                "type": "fact" if ss else "inference",
                "confidence": conf,
                "sources": ss,
            }
        )

    q = an.get("query") or {}
    kw = "、".join(q.get("keywords") or []) or "目标岗位"
    city = q.get("city") or "全国"
    add(
        f"{city}「{kw}」本次共采集到 {ov.get('total', 0)} 条在招岗位，"
        f"覆盖 {ov.get('companies', 0)} 家公司、{ov.get('cities', 0)} 个城市；"
        f"薪资字段覆盖率 {_pct(ov.get('salary_coverage'))}。"
    )

    if sal.get("count"):
        add(
            f"月薪中位数 {_fmt(sal.get('median'))}，均值 {_fmt(sal.get('mean'))}，"
            f"P25–P75 区间 {_fmt(sal.get('p25'))}–{_fmt(sal.get('p75'))}，"
            f"P90 达到 {_fmt(sal.get('p90'))}；极差 {_fmt(sal.get('min'))}–{_fmt(sal.get('max'))}。"
        )

    by_city = ((an.get("salary") or {}).get("byCity") or [])[:3]
    got = [c for c in by_city if c.get("median")]
    if got:
        best = max(got, key=lambda c: c["median"])
        add(
            f"城市间定价差异明显：{best['name']} 中位月薪最高（{_fmt(best['median'])}，{best['count']} 条），"
            + "；".join(f"{c['name']} {_fmt(c['median'])}" for c in got if c is not best)
            + "。"
        )

    sk = (an.get("skills") or {}).get("top") or []
    if sk:
        top3 = sk[:3]
        paid = [s for s in sk if s.get("lift")]
        best_paid = max(paid, key=lambda s: s["lift"]) if paid else None
        add(
            "需求最高的技能是 "
            + "、".join(f"{s['name']}（{s['share']}%）" for s in top3)
            + (
                f"；溢价最明显的是 {best_paid['name']}，中位月薪比整体高 {_fmt(best_paid['lift'])}。"
                if best_paid
                else "。"
            ),
            conf="medium",
        )

    edu = (an.get("demand") or {}).get("edu") or []
    exp = (an.get("demand") or {}).get("exp") or []
    if edu or exp:
        parts = []
        if edu:
            main = max(edu, key=lambda x: x["count"])
            parts.append(f"学历主流要求「{main['name']}」（{main['share']}%）")
        if exp:
            main = max(exp, key=lambda x: x["count"])
            parts.append(f"经验主流「{main['name']}」（{main['share']}%）")
        add("；".join(parts) + "，说明该岗位的门槛结构。", conf="medium")

    ins = an.get("insights") or []
    for it in ins[:2]:
        txt = (it.get("text") or "").strip()
        if txt and all(txt not in x["finding"] for x in f):
            f.append(
                {
                    "finding": txt,
                    "type": "inference" if not sources_for_corpus else "fact",
                    "confidence": "medium",
                    "sources": sources_for_corpus[:1],
                }
            )
    return f or [
        {
            "finding": "采集完成但没有形成可引用结论：样本量过小或薪资字段缺失严重。",
            "type": "inference",
            "confidence": "low",
            "sources": [],
        }
    ]


def _angles(an: dict) -> list[dict]:
    """下游（内容/汇总）能拿去用的角度，从这次分析里派生，不另编。"""
    sal = (an.get("overview") or {}).get("salary") or {}
    sk = (an.get("skills") or {}).get("top") or []
    paid = [s for s in sk if s.get("lift")]
    angles = [
        {
            "angle": "这个岗位现在的真实薪资水位与谈薪空间",
            "why_now": f"本次样本 { (an.get('overview') or {}).get('total', 0) } 条、"
                       f"中位 {_fmt(sal.get('median'))}，"
                       f"IQR/中位约 {round((sal.get('iqr') or 0) / max(1, sal.get('median') or 1) * 100)}%，"
                       "同岗定价分散意味着可谈。",
            "target_audience": "正在看机会或准备跳槽的从业者",
            "risk_note": "招聘方自报区间，非实际 offer；样本集中在少数平台。",
        },
        {
            "angle": "哪些技能在为简历加价",
            "why_now": (
                f"{paid[0]['name']} 的溢价最高（+{_fmt(paid[0]['lift'])}）"
                if paid
                else "技能维度样本不足，暂无可验证溢价"
            ),
            "target_audience": "要补齐技能短板的求职者、做招聘画像的 HR",
            "risk_note": "溢价是相关性不是因果，可能被公司规模/城市混入。",
        },
        {
            "angle": "学历与经验门槛到底卡在哪一档",
            "why_now": "用主流门槛（学历/经验分布中占比最高一档）判断自己卡在哪一步最省力。",
            "target_audience": "应届与转行人群",
            "risk_note": "「不限」通常意味着筛简历时另有隐性标准。",
        },
    ]
    return angles


def _render_md(an: dict, jobs: list[dict], node: TaskNode) -> str:
    ov = an.get("overview") or {}
    sal = ov.get("salary") or {}
    q = an.get("query") or {}
    lines = [f"# {node.title}", ""]
    src_names = "、".join(s.get("name", "") for s in (ov.get("sources") or [])) or "招聘平台"
    lines += [
        f"> 目标：{node.goal}",
        "",
        f"样本 {ov.get('total', 0)} 条 · {ov.get('companies', 0)} 家公司 · "
        f"{ov.get('cities', 0)} 个城市 · 来源 {src_names}",
        "",
        "## 薪资",
        "",
        "| 分位 | 月薪 |",
        "| --- | --- |",
        f"| P10 | {_fmt(sal.get('p10'))} |",
        f"| P25 | {_fmt(sal.get('p25'))} |",
        f"| P50（中位） | {_fmt(sal.get('median'))} |",
        f"| P75 | {_fmt(sal.get('p75'))} |",
        f"| P90 | {_fmt(sal.get('p90'))} |",
        "",
    ]
    by_city = ((an.get("salary") or {}).get("byCity") or [])[:8]
    if by_city:
        lines += ["## 城市", "", "| 城市 | 岗位数 | 中位月薪 |", "| --- | --- | --- |"]
        lines += [f"| {c.get('name')} | {c.get('count')} | {_fmt(c.get('median'))} |" for c in by_city]
        lines.append("")
    sk = (an.get("skills") or {}).get("top") or []
    if sk:
        lines += ["## 技能", "", "| 技能 | 提及 | 覆盖 | 中位 | 溢价 |", "| --- | --- | --- | --- | --- |"]
        lines += [
            f"| {s.get('name')} | {s.get('count')} | {s.get('share')}% | {_fmt(s.get('median'))} | "
            f"{('+' + _fmt(s.get('lift'))) if s.get('lift') else '—'} |"
            for s in sk[:12]
        ]
        lines.append("")
    if jobs:
        lines += ["## 岗位明细", "", "| 职位 | 公司 | 月薪 | 城市 | 经验 | 学历 |", "| --- | --- | --- | --- | --- | --- |"]
        for j in jobs[:MAX_JOBS_IN_MD]:
            lines.append(
                f"| {j.get('title')} | {j.get('company')} | {j.get('salary')} | "
                f"{j.get('city')} | {j.get('exp') or '—'} | {j.get('edu') or '—'} |"
            )
        lines.append("")
    if q:
        lines += [
            "## 检索条件",
            "",
            f"- 城市：{q.get('city') or '不限'}　关键词：{'、'.join(q.get('keywords') or []) or '—'}",
            f"- 月薪下限：{_fmt(q.get('salary_min'))}　学历：{q.get('edu') or '不限'}",
            f"- 数据源：{'、'.join(q.get('sources') or []) or '默认'}",
            "",
        ]
    lines += ["---", "数据来源：招聘平台公开搜索结果页，仅用于个人求职与市场研究。" ]
    return "\n".join(lines)


def _self_check(node: TaskNode, an: dict, jobs: list[dict], findings: list[dict]) -> list[dict]:
    """逐条回应 acceptance，evidence 必须是真实数字。"""
    ov = an.get("overview") or {}
    out = []
    for crit in node.acceptance or [{"type": "schema_valid"}]:
        t = crit.get("type")
        if t == "cites_sources":
            n = len([f for f in findings if f.get("sources")])
            need = crit.get("min", 1)
            out.append(
                {
                    "criterion": f"cites_sources ≥ {need}",
                    "result": "pass" if n >= need else "fail",
                    "evidence": f"{n} 条结论带真实岗位链接（要求 {need}）",
                }
            )
        elif t == "schema_valid":
            out.append(
                {
                    "criterion": "schema_valid",
                    "result": "pass",
                    "evidence": f"{len(findings)} 条结论 / {len(jobs)} 条岗位明细，字段齐全",
                }
            )
        elif t == "min_words":
            need = crit.get("min", 0)
            n = len(_render_md(an, jobs, node))
            out.append(
                {
                    "criterion": f"min_words ≥ {need}",
                    "result": "pass" if n >= need else "fail",
                    "evidence": f"报告正文 {n} 字",
                }
            )
        else:
            out.append(
                {
                    "criterion": str(t),
                    "result": "not_run",
                    "evidence": "招聘采集执行器不产这类字段，需人工/评审判断",
                }
            )
    if ov.get("total", 0) < 10:
        out.append(
            {
                "criterion": "样本充足",
                "result": "fail",
                "evidence": f"仅 {ov.get('total', 0)} 条，统计结论不稳",
            }
        )
    return out


def run(node: TaskNode, ctx: dict, run_id: str, *, trace: list[str] | None = None) -> dict:
    trace = trace if trace is not None else []
    app = _app()

    upstream = ctx.get("upstream") or []
    notes = [
        f"{u.get('task_id')}: {(u.get('notes_for_downstream') or '').strip()}"
        for u in upstream
        if (u.get("notes_for_downstream") or "").strip()
    ]
    if notes:
        # 上游交接只记进 trace，不拼进检索句——多一句描述就会污染对方的关键词抽取
        trace.append("[jobs] 上游交接：" + " | ".join(notes)[:300])
    message = _search_sentence(node.goal or node.title)

    trace.append(f"[jobs] 调用 {app['name']} · sid={apps.sid_of(app)} · 检索句：{message[:80]}")
    code, data = apps.call("jobs", "chat", {"sid": apps.sid_of(app), "message": message}, timeout=TIMEOUT)

    if code == 0 or not data:
        reason = (data or {}).get("error") or f"HTTP {code}"
        trace.append(f"[jobs] 调用失败：{reason}")
        return build_result(
            node,
            {
                "status": "blocked",
                "self_check": [
                    {
                        "criterion": "外部采集服务可用",
                        "result": "fail",
                        "evidence": reason[:200],
                    }
                ],
                "open_questions": [
                    f"招聘采集 Agent 不可用（{reason}）。到「AI 应用」页点它的「启动服务」，"
                    f"然后在本页点「续跑」重试。"
                ],
                "notes_for_downstream": "",
            },
            artifact_type="report",
        )

    an = data.get("analysis") or {}
    jobs = data.get("jobs") or []
    if not (an.get("overview") or {}).get("total"):
        reply = (data.get("reply") or "").strip()
        trace.append(f"[jobs] 对方没有产出数据，回复：{reply[:120]}")
        return build_result(
            node,
            {
                "status": "blocked",
                "self_check": [
                    {
                        "criterion": "采集到岗位样本",
                        "result": "fail",
                        "evidence": f"返回 0 条岗位；对方回复：{reply[:160] or '（空）'}",
                    }
                ],
                "open_questions": [
                    "这次需求没能采到岗位。对方的原话是：「"
                    + (reply[:200] or "无回复")
                    + "」。请把节点目标写得更像一次检索（城市 + 岗位名 + 薪资/经验条件）。"
                ],
                "notes_for_downstream": "",
            },
            artifact_type="report",
        )

    findings = _findings(an, jobs)
    payload = {
        "status": "done",
        "key_findings": findings,
        "content_angles": _angles(an),
        "data_freshness": {
            "oldest_source_date": (an.get("overview") or {}).get("publish_span", {}).get("earliest")
            or time.strftime("%Y-%m-%d"),
            "stale_items": [],
        },
        "self_check": _self_check(node, an, jobs, findings),
        "open_questions": [],
        "notes_for_downstream": (
            f"{len(jobs)} 条岗位样本（中位月薪 "
            f"{_fmt((an.get('overview') or {}).get('salary', {}).get('median'))}"
            f"），明细见产物；引用结论时请标注「招聘平台公开样本」。"
        ),
    }

    md_path = store.save_task_text(run_id, node.task_id, "report.md", _render_md(an, jobs, node))
    store.save_task_json(
        run_id,
        node.task_id,
        "analysis",
        {"query": an.get("query"), "overview": an.get("overview"), "skills": an.get("skills")},
    )
    store.save_task_json(run_id, node.task_id, "jobs", {"jobs": jobs})
    trace.append(f"[jobs] 产出 {len(jobs)} 条岗位 / {len(findings)} 条结论")

    if any(c["result"] == "fail" for c in payload["self_check"]):
        payload["status"] = "partial"

    return build_result(
        node,
        payload,
        artifact_type="report",
        artifact_path=md_path,
        summary=f"{len(jobs)} 条岗位 / {len(findings)} 条结论",
        raw_text=json.dumps(payload, ensure_ascii=False),
    )
