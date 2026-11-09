"""分析引擎：把岗位记录变成结构化洞察。"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import date
from typing import Any, Callable, Iterable

from .skills import extract_skills, skill_category

SALARY_BUCKETS = [
    ("8K 以下", 0, 8000), ("8-12K", 8000, 12000), ("12-18K", 12000, 18000),
    ("18-25K", 18000, 25000), ("25-35K", 25000, 35000), ("35-50K", 35000, 50000),
    ("50K 以上", 50000, 10 ** 9),
]
EXP_ORDER = ["应届/1年内", "1-3年", "3-5年", "5-10年", "10年以上", "经验不限"]
EDU_ORDER = ["不限", "高中", "中专", "大专", "本科", "硕士", "博士"]


# ---------------- 基础统计 ----------------

def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vs = sorted(values)
    if len(vs) == 1:
        return float(vs[0])
    k = (len(vs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(vs) - 1)
    return round(vs[lo] + (vs[hi] - vs[lo]) * (k - lo), 1)


def salary_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": round(statistics.median(values), 1),
        "p10": percentile(values, 10), "p25": percentile(values, 25),
        "p50": percentile(values, 50), "p75": percentile(values, 75),
        "p90": percentile(values, 90),
        "min": round(min(values), 1), "max": round(max(values), 1),
        "stdev": round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0,
        "iqr": round((percentile(values, 75) or 0) - (percentile(values, 25) or 0), 1),
    }


def exp_bucket(job: dict[str, Any]) -> str:
    txt = job.get("exp_text") or ""
    if "不限" in txt or "无要求" in txt:
        return "经验不限"
    if "应届" in txt or "在校" in txt:
        return "应届/1年内"
    lo, hi = job.get("exp_low") or 0, job.get("exp_high") or 99
    if hi <= 1:
        return "应届/1年内"
    if lo < 3:
        return "1-3年"
    if lo < 5:
        return "3-5年"
    if lo < 10:
        return "5-10年"
    return "10年以上"


def _dist(jobs: Iterable[dict], key: Callable[[dict], str], order: list[str] | None = None,
          limit: int = 12) -> list[dict[str, Any]]:
    jobs = list(jobs)
    c = Counter(key(j) or "未知" for j in jobs)
    total = sum(c.values()) or 1
    items = sorted(c.items(), key=lambda x: -x[1])
    if order:
        items.sort(key=lambda x: order.index(x[0]) if x[0] in order else 99)
    else:
        items = items[:limit]
    out = []
    for name, n in items:
        sub = [j["salary_mid"] for j in jobs if (key(j) or "未知") == name and j.get("salary_mid")]
        out.append({"name": name, "count": n, "share": round(n / total * 100, 1),
                    "median": round(statistics.median(sub), 1) if sub else None,
                    "salary_n": len(sub)})
    return out


def _city_name(job: dict[str, Any]) -> str:
    return (job.get("city") or "未知").replace("市", "")


# ---------------- 主分析 ----------------

def analyze(jobs: list[dict[str, Any]], profile: dict[str, Any] | None = None,
            query: dict[str, Any] | None = None) -> dict[str, Any]:
    jobs = [j for j in jobs if j.get("title")]
    sal = [j["salary_mid"] for j in jobs if j.get("salary_mid")]
    companies = Counter(j.get("company") for j in jobs if j.get("company"))

    res: dict[str, Any] = {}
    res["query"] = query or {}
    res["overview"] = {
        "total": len(jobs),
        "companies": len(companies),
        "cities": len({_city_name(j) for j in jobs if j.get("city")}),
        "salary_covered": len(sal),
        "salary_coverage": round(len(sal) / max(1, len(jobs)) * 100, 1),
        "jd_coverage": round(sum(1 for j in jobs if len(j.get("jd") or "") > 60) / max(1, len(jobs)) * 100, 1),
        "salary": salary_stats(sal),
        "sources": _dist(jobs, lambda j: {"zhaopin": "智联招聘", "liepin": "猎聘",
                                          "import": "导入",
                                          "text": "文本"}.get(j.get("source"), "其他")),
        "publish_span": _publish_span(jobs),
    }

    res["salary"] = {
        "hist": _salary_hist(sal),
        "byCity": _dist(jobs, _city_name, limit=10),
        "byExp": _dist(jobs, exp_bucket, order=EXP_ORDER),
        "byEdu": _dist(jobs, lambda j: j.get("edu") or "不限", order=EDU_ORDER),
        "bySize": _dist(jobs, lambda j: _size_label(j.get("company_size")), limit=8),
        "byIndustry": _dist(jobs, lambda j: j.get("industry") or "未标注", limit=8),
    }

    res["demand"] = {
        "edu": _dist(jobs, lambda j: j.get("edu") or "不限", order=EDU_ORDER),
        "exp": _dist(jobs, exp_bucket, order=EXP_ORDER),
        "industry": _dist(jobs, lambda j: j.get("industry") or "未标注", limit=10),
        "size": _dist(jobs, lambda j: _size_label(j.get("company_size")), limit=10),
        "companyType": _dist(jobs, lambda j: j.get("company_type") or "未标注", limit=8),
        "cities": _dist(jobs, _city_name, limit=10),
    }

    res["skills"] = _skill_analysis(jobs, sal)
    res["companies"] = _company_analysis(jobs, companies)
    res["quality"] = _quality(jobs)
    res["insights"] = []
    if profile:
        res["match"] = _match(jobs, profile)
    res["insights"] = build_insights(res, profile)
    return res


def _publish_span(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    dates = [j["publish_date"] for j in jobs if j.get("publish_date")]
    if not dates:
        return {"count": 0}
    dates.sort()
    days = []
    today = date.today()
    for d in dates:
        try:
            y, m, dd = (int(x) for x in d.split("-"))
            days.append((today - date(y, m, dd)).days)
        except Exception:
            continue
    return {"count": len(dates), "earliest": dates[0], "latest": dates[-1],
            "median_age_days": round(statistics.median(days), 1) if days else None,
            "fresh7": sum(1 for x in days if x <= 7), "fresh30": sum(1 for x in days if x <= 30)}


def _size_label(text: str | None) -> str:
    t = (text or "").strip()
    if not t:
        return "未标注"
    return t.replace("人", "人")


def _salary_hist(values: list[float]) -> list[dict[str, Any]]:
    total = len(values) or 1
    out = []
    for label, lo, hi in SALARY_BUCKETS:
        n = sum(1 for v in values if lo <= v < hi)
        out.append({"name": label, "count": n, "share": round(n / total * 100, 1)})
    return out


def _skill_analysis(jobs: list[dict[str, Any]], sal_all: list[float]) -> dict[str, Any]:
    counter: Counter = Counter()
    with_skill: dict[str, list[float]] = {}
    job_skill_sets = []
    for j in jobs:
        sks = j.get("skills") or []
        job_skill_sets.append(set(sks))
        for s in sks:
            counter[s] += 1
            if j.get("salary_mid"):
                with_skill.setdefault(s, []).append(j["salary_mid"])

    base_median = statistics.median(sal_all) if sal_all else None
    total = len(jobs) or 1
    top = []
    for name, n in counter.most_common(24):
        vals = with_skill.get(name, [])
        med = round(statistics.median(vals), 1) if vals else None
        min_n = max(5, total * 0.05)
        lift = (round(med - base_median, 1)
                if (med and base_median and n >= min_n) else None)
        top.append({"name": name, "count": n, "share": round(n / total * 100, 1),
                    "median": med, "lift": lift, "category": skill_category(name)})

    # 共现
    pairs: Counter = Counter()
    for s in job_skill_sets:
        ranked = [x for x in (t["name"] for t in top) if x in s]
        for i in range(len(ranked)):
            for k in range(i + 1, len(ranked)):
                pairs[(ranked[i], ranked[k])] += 1
    cooccur = [{"a": a, "b": b, "count": n} for (a, b), n in pairs.most_common(12)]

    # 分类分布
    cat: Counter = Counter()
    for name, n in counter.items():
        cat[skill_category(name)] += n
    cat_total = sum(cat.values()) or 1
    categories = [{"name": k, "count": v, "share": round(v / cat_total * 100, 1)}
                  for k, v in cat.most_common()]

    # 覆盖率 ≥90% 的技能等于检索词本身（搜「数据分析师」必然人人都要「数据分析」），
    # 列入 base 单列展示，不参与「谁更值钱」的排序，否则会挤掉真正有区分度的技能。
    core = [t for t in top if t["share"] >= 90]
    rest = [t for t in top if t["share"] < 90]
    if len(rest) < 4:
        rest, core = top, []
    return {"top": rest, "base": core, "cooccur": cooccur, "categories": categories,
            "unique": len(counter), "avg_per_job": round(sum(len(s) for s in job_skill_sets) / total, 1)}


def _company_analysis(jobs: list[dict[str, Any]], companies: Counter) -> dict[str, Any]:
    top = []
    for name, n in companies.most_common(15):
        sub = [j for j in jobs if j.get("company") == name]
        sals = [j["salary_mid"] for j in sub if j.get("salary_mid")]
        top.append({"name": name, "count": n,
                    "median": round(statistics.median(sals), 1) if sals else None,
                    "industry": next((j.get("industry") for j in sub if j.get("industry")), ""),
                    "size": next((j.get("company_size") for j in sub if j.get("company_size")), "")})
    total = len(jobs) or 1
    top10 = sum(x["count"] for x in top[:10])
    return {"top": top, "unique": len(companies),
            "top10_share": round(top10 / total * 100, 1),
            "avg_per_company": round(total / max(1, len(companies)), 2)}


def _quality(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(jobs) or 1
    checks = [
        ("薪资明确", sum(1 for j in jobs if j.get("salary_mid"))),
        ("JD 正文完整", sum(1 for j in jobs if len(j.get("jd") or "") > 120)),
        ("识别出技能", sum(1 for j in jobs if j.get("skills"))),
        ("有发布时间", sum(1 for j in jobs if j.get("publish_date"))),
        ("有原始链接", sum(1 for j in jobs if j.get("url"))),
    ]
    return [{"name": k, "count": v, "share": round(v / total * 100, 1)} for k, v in checks]


# ---------------- 人岗匹配 ----------------

def _match(jobs: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    my_skills = set(profile.get("skills") or [])
    if not my_skills:
        my_skills = set(extract_skills(profile.get("skills_text") or "") if isinstance(
            profile.get("skills_text"), str) else [])
    if not my_skills and profile.get("skills_text"):
        raw = profile["skills_text"]
        my_skills = set(extract_skills(" ".join(raw) if isinstance(raw, list) else str(raw)))
    years = profile.get("years")
    edu = profile.get("edu") or ""
    expect = profile.get("expect_salary")

    ranked = []
    for j in jobs:
        req = set(j.get("skills") or [])
        overlap = my_skills & req
        denom = max(3, min(len(req), 8)) if req else 3
        skill_score = min(1.0, len(overlap) / denom) * 1.0
        # 命中岗位核心技能额外加分
        if overlap and j.get("skills") and overlap & set(j["skills"][:3]):
            skill_score = min(1.0, skill_score + 0.15)

        exp_score = 1.0
        if years is not None:
            lo, hi = j.get("exp_low") or 0, j.get("exp_high") or 99
            if years < lo:
                exp_score = max(0.2, 1 - (lo - years) * 0.25)
            elif years > hi:
                exp_score = max(0.5, 1 - (years - hi) * 0.1)

        edu_score = 1.0
        if edu:
            from .normalize import edu_rank
            gap = edu_rank(edu) - edu_rank(j.get("edu") or "不限")
            edu_score = 1.0 if gap >= 0 else max(0.2, 1 + gap * 0.25)

        sal_score = 0.7
        if expect:
            mid = j.get("salary_mid")
            if mid:
                sal_score = 1.0 if mid >= expect else max(0.15, mid / expect)
        else:
            sal_score = 0.75

        total = (skill_score * 0.5 + exp_score * 0.18 + edu_score * 0.12 + sal_score * 0.2) * 100
        ranked.append({
            "job": j, "score": round(total, 1),
            "matched": sorted(overlap)[:8],
            "missing": sorted(req - my_skills)[:8],
            "breakdown": {"skill": round(skill_score * 100, 1), "exp": round(exp_score * 100, 1),
                          "edu": round(edu_score * 100, 1), "salary": round(sal_score * 100, 1)},
        })
    ranked.sort(key=lambda x: (-x["score"], -(x["job"].get("salary_mid") or 0)))

    # 技能缺口统计（按需求频次）
    gap: Counter = Counter()
    for j in jobs:
        for s in (set(j.get("skills") or []) - my_skills):
            gap[s] += 1
    sals = [j["salary_mid"] for j in jobs if j.get("salary_mid")]
    salary_hit = None
    if expect and sals:
        salary_hit = round(sum(1 for s in sals if s >= expect) / len(sals) * 100, 1)

    # 哪个维度拖后腿
    dims = {"技能覆盖": statistics.fmean([r["breakdown"]["skill"] for r in ranked]) if ranked else 0,
            "经验匹配": statistics.fmean([r["breakdown"]["exp"] for r in ranked]) if ranked else 0,
            "学历匹配": statistics.fmean([r["breakdown"]["edu"] for r in ranked]) if ranked else 0,
            "薪资达标": statistics.fmean([r["breakdown"]["salary"] for r in ranked]) if ranked else 0}
    weakest = sorted(dims.items(), key=lambda x: x[1])[:2]

    return {
        "profile": {"skills": sorted(my_skills), "years": years, "edu": edu, "expect": expect},
        "dims": {k: round(v, 1) for k, v in dims.items()},
        "weakest": [{"name": k, "score": round(v, 1)} for k, v in weakest],
        "top": [{"id": r["job"].get("id"), "title": r["job"]["title"], "company": r["job"]["company"],
                 "salary": r["job"].get("salary_text"), "city": r["job"].get("city"),
                 "score": r["score"], "matched": r["matched"], "missing": r["missing"],
                 "breakdown": r["breakdown"], "url": r["job"].get("url")}
                for r in ranked[:10]],
        "avg_score": round(statistics.fmean([r["score"] for r in ranked]), 1) if ranked else None,
        "strong_fit": sum(1 for r in ranked if r["score"] >= 70),
        "salary_hit": salary_hit,
        "gaps": [{"name": k, "count": v,
                  "share": round(v / max(1, len(jobs)) * 100, 1)} for k, v in gap.most_common(12)],
    }


# ---------------- 文字洞察 ----------------

def _fmt_salary(v: float | None) -> str:
    if not v:
        return "—"
    return f"{v / 1000:.1f}K" if v < 10000 else f"{v / 10000:.1f}万"


def build_insights(res: dict[str, Any], profile: dict[str, Any] | None = None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    ov = res.get("overview", {})
    s = ov.get("salary") or {}
    if not s.get("count"):
        return [{"type": "warn", "title": "薪资样本不足",
                 "text": "本次采集到的岗位几乎没有公开薪资，建议扩大关键词或开启「详情补全」后重试。"}]
    q = res.get("query") or {}
    kw = "、".join(q.get("keywords") or []) or "目标岗位"
    out.append({
        "type": "info", "title": "市场水位",
        "text": f"「{kw}」共采集 {ov['total']} 条岗位（{ov['companies']} 家公司），"
                f"月薪中位数 {_fmt_salary(s['median'])}，均值 {_fmt_salary(s['mean'])}，"
                f"P25–P75 区间 {_fmt_salary(s.get('p25'))}–{_fmt_salary(s.get('p75'))}。"
                f"薪资离散度（IQR/中位）约 {round((s.get('iqr') or 0) / max(1, s['median']) * 100)}%，"
                + ("说明同岗定价差异较大，谈薪空间明显。" if (s.get("iqr") or 0) / max(1, s["median"]) > 0.5
                   else "说明该岗位市场定价相对集中，薪资锚点清晰。"),
    })

    edu = res.get("demand", {}).get("edu") or []
    if edu:
        main = max(edu, key=lambda x: x["count"])
        out.append({"type": "info", "title": "学历门槛",
                    "text": f"主流要求是「{main['name']}」（{main['share']}%，{main['count']} 条）。"
                            + ("硕士及以上占比超过三成，学历是硬门槛。" if _share_of(edu, ["硕士", "博士"]) > 30
                               else "学历要求以本科及以下为主，经验与技能权重更高。")})

    exp = res.get("demand", {}).get("exp") or []
    if exp:
        main = max(exp, key=lambda x: x["count"])
        ex = next((x for x in exp if x["name"] == "3-5年"), None)
        txt = f"经验要求集中在「{main['name']}」（{main['share']}%）。"
        if ex and ex.get("median"):
            txt += f"3-5 年经验的岗位中位薪资 {_fmt_salary(ex['median'])}。"
        out.append({"type": "info", "title": "经验门槛", "text": txt})

    sk = res.get("skills", {})
    if sk.get("top"):
        hot = sk["top"][:5]
        names = "、".join(f"{t['name']}({t['count']})" for t in hot)
        best = max([t for t in sk["top"] if t.get("lift") is not None and t["count"] >= 5]
                   or [{"name": "", "lift": 0}],
                   key=lambda x: x.get("lift") or 0)
        txt = f"最高频技能：{names}。平均每条 JD 命中 {sk.get('avg_per_job')} 项技能。"
        if best.get("name") and (best.get("lift") or 0) > 0:
            txt += f"其中「{best['name']}」的薪资溢价最明显（+{_fmt_salary(best['lift'])}）。"
        out.append({"type": "info", "title": "技能需求", "text": txt})
        if sk.get("categories"):
            c = sk["categories"][0]
            out.append({"type": "info", "title": "能力结构",
                        "text": f"技能需求最集中的方向是「{c['name']}」（占技能提及量的 {c['share']}%）。"})

    city = res.get("salary", {}).get("byCity") or []
    if len(city) >= 2:
        city_sorted = sorted([c for c in city if c.get("median")], key=lambda x: -x["median"])
        hi, lo = city_sorted[0], city_sorted[-1]
        if hi["median"] and lo["median"] and hi["median"] > lo["median"]:
            out.append({"type": "info", "title": "城市差异",
                        "text": f"{hi['name']} 中位薪资 {_fmt_salary(hi['median'])}，为最高；"
                                f"{lo['name']} 为 {_fmt_salary(lo['median'])}，"
                                f"差距 {round((hi['median'] / lo['median'] - 1) * 100)}%。"})

    co = res.get("companies", {})
    if co.get("top") and co.get("unique", 0) >= 12:
        t = co["top"][0]
        out.append({"type": "info", "title": "招聘集中度",
                    "text": f"招聘量最大的是 {t['name']}（{t['count']} 个在招岗位）。"
                            f"Top10 公司占据 {co.get('top10_share')}% 的岗位，"
                            + ("头部集中明显，投递需尽早。" if (co.get("top10_share") or 0) > 45
                               else "长尾公司机会多，可分散投递。")})

    if profile and res.get("match"):
        m = res["match"]
        txt = f"按你的画像（{ '、'.join(m['profile']['skills'][:6]) or '未提供技能' }）匹配，"
        txt += f"平均匹配度 {m.get('avg_score')} 分，{m.get('strong_fit')} 个岗位达到 70 分以上。"
        if m.get("salary_hit") is not None:
            txt += f"期望薪资达标率 {m['salary_hit']}%。"
        if m.get("dims"):
            txt += "各维度得分：" + "，".join(f"{k} {v}" for k, v in m["dims"].items()) + "。"
        if m.get("gaps"):
            g = "、".join(f"{x['name']}({x['count']})" for x in m["gaps"][:5])
            txt += f"最该补的能力：{g}。"
        out.append({"type": "action", "title": "匹配结论", "text": txt})

    dq = res.get("quality") or []
    if dq:
        low = [x for x in dq if x["share"] < 60]
        if low:
            out.append({"type": "warn", "title": "数据质量提示",
                        "text": "以下维度缺失较多，结论需谨慎：" + "、".join(
                            f"{x['name']}({x['share']}%)" for x in low) + "。"})
    return out


def _share_of(items: list[dict[str, Any]], names: list[str]) -> float:
    return sum(x["share"] for x in items if x["name"] in names)
