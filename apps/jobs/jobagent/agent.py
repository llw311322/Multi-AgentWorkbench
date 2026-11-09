"""Agent 编排层：意图识别 → 采集计划 → 抓取 → 清洗 → 分析 → 生成回答。"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import adapters, analyzer, nlu, report
from .adapters.base import SourceError
from .skills import extract_skills
from .store import Store

DEFAULT_SETTINGS: dict[str, Any] = {"cookies": {"zhaopin": "", "liepin": ""},
                                    "deep_detail": True}

# 详情补全上限：猎聘走浏览器渲染，单条约 5 秒，压低条数避免风控
ENRICH_CAP = {"liepin": 8, "zhaopin": 20}

HELP_TEXT = (
    "我可以做这些事：\n\n"
    "1. **按需求找岗位** —— 直接说「帮我搜集杭州数据分析师的岗位，月薪 15K 以上」，我会解析城市/薪资/"
    "经验/学历条件，去智联招聘、猎聘抓 JD，自动去重清洗。\n"
    "2. **出详细数据分析** —— 采集完自动生成：薪资分布与分位、城市/经验/学历/公司规模/行业交叉对比、"
    "技能词频与薪资溢价、技能共现、招聘集中度、JD 质量体检。\n"
    "3. **追问式深挖** —— 结果出来后可以继续说「只看硕士学历的」「薪资最高的 10 个」"
    "「对比北京和上海」「这些岗位最缺什么技能」。\n"
    "4. **人岗匹配** —— 告诉我「我会 Python、SQL，3 年经验，本科，期望 25K」，"
    "我会算出匹配度和技能缺口。\n"
    "5. **导出** —— 说「导出报告」，得到一份可分享的 HTML 分析报告 + CSV 明细。\n\n"
    "数据源：智联招聘（可直接采集）、猎聘（走本地浏览器渲染，需装有 Chrome 或 Edge）。"
)


class Agent:
    def __init__(self, data_dir: str = "data"):
        self.store = Store(data_dir)

    # ---------------- 对外入口 ----------------

    def handle(self, sid: str, message: str, event: Callable[[str, dict], None] | None = None) -> dict[str, Any]:
        msg = (message or "").strip()
        state = self.store.load(sid)
        settings = state.setdefault("settings", json.loads(json.dumps(DEFAULT_SETTINGS)))

        if not msg:
            return self._reply(state, "说点什么吧，比如「帮我找上海的 Java 开发岗位」。", intent="empty")

        low = msg.lower()

        # 1) 帮助
        if low in {"help", "/help", "帮助", "怎么用", "你能做什么"}:
            return self._reply(state, HELP_TEXT, intent="help")

        # 2) 数据源状态
        if any(k in msg for k in ("数据源状态", "源状态", "连接状态", "能用吗", "采集状态")):
            return self._source_status(state, settings)

        # 3) 设置 Cookie
        m = re.search(r"(cookie|Cookie|COOKIE)\s*[:：=]\s*(.+)", msg, re.S)
        if m:
            return self._set_cookie(state, settings, m.group(2))

        # 4) 导出
        if any(k in msg for k in ("导出", "报告下载", "生成报告文件", "export")):
            return self._export(state, sid)

        # 5) 个人画像
        prof = nlu.extract_profile(msg) if any(
            k in msg for k in ("我会", "我熟悉", "我精通", "我有", "我是", "期望", "我的")) else None
        need_search = self._looks_like_search(msg)
        if prof and not need_search:
            state["profile"] = {**state.get("profile", {}), **prof}
            self.store.save(sid, state)
            return self._reply(state, self._profile_text(state["profile"]),
                               intent="profile", analysis=state.get("analysis"))
        if prof and need_search:
            state["profile"] = {**state.get("profile", {}), **prof}

        # 6) 追问（在已有结果上做筛选/排序/再分析）
        if state.get("jobs") and not need_search:
            # 只发了一个城市名、且跟当前结果的城市不同 —— 这是「换个城市看看」，不是追问
            probe = nlu.parse_query(msg)
            if probe["city"] and not probe["keywords"] and probe["city"] != "全国":
                cur = {(j.get("city") or "") for j in state.get("jobs") or []}
                if not any(probe["city"] in c for c in cur):
                    return self._search(state, sid, msg, event=event)
            # 带了新的岗位词（和上一轮不同）—— 也是新检索，不是在上轮结果里筛
            if probe["keywords"] and not nlu.is_followup(msg):
                cur_kw = {k.lower() for k in (state.get("query") or {}).get("keywords") or []}
                if not any(k.lower() in cur_kw for k in probe["keywords"]):
                    return self._search(state, sid, msg, event=event)
            return self._followup(state, sid, msg)

        # 7) 新检索
        return self._search(state, sid, msg, event=event)

    # ---------------- 意图判断 ----------------

    @staticmethod
    def _looks_like_search(msg: str) -> bool:
        """只有出现明确的「去找」动作才算新检索；「只看本科」「薪资最高」属于追问。"""
        verbs = ("帮我找", "帮我搜", "给我找", "找一下", "搜一下", "搜集", "收集", "采集", "抓取",
                 "搜索", "查询", "查一下", "看看", "有哪些", "找", "搜", "抓", "查")
        if re.search(r"(再来|再找|换一个|换个)", msg):
            return True
        if not any(v in msg for v in verbs):
            return False
        # 「只看/其中/这些里」是追问句式，不算检索
        if re.search(r"(只看|只要|其中|这些|结果里|上面|刚才|里面|筛|过滤)", msg):
            return False
        return True

    # ---------------- 主流程：检索 ----------------

    def _search(self, state: dict, sid: str, msg: str, event=None) -> dict[str, Any]:
        q = nlu.parse_query(msg)
        city_only = False
        if not q["keywords"]:
            if not q["city"]:
                return self._reply(state, "我没从这句话里认出具体岗位。可以说得更具体一点，"
                                         "比如「北京 Python 后端开发，3 年经验，20K 以上」。", intent="clarify")
            # 只说了城市：先按地区拉热门岗位，再让用户补岗位名 —— 「先地区、再职位」
            city_only = True
            q["keywords"] = [""]
            q["city_only"] = True

        settings = state.get("settings", DEFAULT_SETTINGS)

        # 关键词扩展：单关键词在智联只返回 20 条，用近义词把样本量做大
        kws: list[str] = []
        for kw in q["keywords"]:
            for k in nlu.expand_keywords(kw, max_n=3 if q["pages"] >= 3 else 2) if kw else [""]:
                if k not in kws:
                    kws.append(k)
        kws = kws[:4]

        sources = q["sources"]
        if city_only:
            # 空关键词只有智联这类「城市热门岗位」页面支持，其它源会被空串打回
            sources = [s for s in sources if s == "zhaopin"] or ["zhaopin"]

        plan = [
            f"解析需求：岗位={'、'.join(k for k in kws if k) or '不限（按城市热门岗位）'}｜"
            f"城市={q['city'] or '全国'}"
            + (f"｜月薪≥{int(q['salary_min'] / 1000)}K" if q.get("salary_min") else "")
            + (f"｜经验≥{q['exp_min']}年" if q.get("exp_min") is not None else ""),
            f"数据源：{', '.join(adapters.LABELS[s] for s in sources)}"
            f"，{len(kws)} 个关键词 × {q['pages']} 页",
            "去重清洗：标题+公司+链接三级去重，薪资/经验/学历归一化，技能抽取",
            "分析：薪资分布、分组对比、技能溢价与共现、集中度、质量体检",
        ]
        if event:
            event("plan", {"plan": plan})

        notes: list[str] = []
        jobs: list[dict[str, Any]] = []
        dead: set[str] = set()
        t0 = time.time()
        for src in sources:
            try:
                adapter = adapters.build(src, settings)
            except Exception as exc:
                notes.append(f"{src} 初始化失败：{exc}")
                continue
            for kw in kws:
                if src in dead:
                    break
                if event:
                    event("progress", {"source": adapters.LABELS.get(src, src),
                                       "keyword": kw or "（城市热门岗位）"})
                try:
                    got = adapter.search(kw, q["city"] or "全国", pages=q["pages"],
                                         limit=q["limit"])
                    used_kw = kw
                    # 关键词过窄时自动降级重试（去后缀 / 取英文主干）
                    if not got and kw:
                        for alt in nlu.fallback_keywords(kw):
                            if event:
                                event("progress", {"source": adapters.LABELS.get(src, src),
                                                   "keyword": f"{alt}（降级）", "retry": True})
                            try:
                                got = adapter.search(alt, q["city"] or "全国", pages=1, limit=q["limit"])
                            except SourceError:
                                got = []
                            if got:
                                used_kw = alt
                                notes.append(f"「{kw}」结果为空，已自动改用「{alt}」采集")
                                break
                            time.sleep(0.4)
                    jobs.extend(got)
                    if getattr(adapter, "last_note", ""):
                        notes.append(adapter.last_note)
                        adapter.last_note = ""
                    if not got:
                        notes.append(f"{adapters.LABELS.get(src, src)}「{kw or '城市热门岗位'}」无结果，可能关键词过窄")
                except SourceError as exc:
                    notes.append(f"{adapters.LABELS.get(src, src)}：" +
                                 (f"{exc.reason}。{exc.hint}" if exc.hint else exc.reason))
                    dead.add(src)
                except Exception as exc:  # 兜底，不让单源失败拖垮整体
                    notes.append(f"{adapters.LABELS.get(src, src)} 采集异常：{exc}")
                time.sleep(0.5)

        notes = list(dict.fromkeys(notes))
        jobs = self._dedupe(jobs)
        jobs, dropped_city = self._filter(jobs, q)
        if dropped_city:
            notes.append(f"按城市「{q['city']}」过滤，剔除 {dropped_city} 条外地岗位")

        if not jobs:
            text = "这次没采到可用的岗位数据。\n\n" + "\n".join(f"- {n}" for n in notes)
            if q.get("city") and q.get("city") != "全国":
                text += (f"\n\n「{q['city']}」这轮没有命中岗位。可以：① 只写城市名（如「{q['city']}」）"
                         f"让我先按地区拉热门岗位；② 换个更通用的关键词；③ 去掉薪资/学历等硬条件再试。")
            else:
                text += ("\n\n建议：① 换个更通用的关键词（如用「Python」代替「Python后端开发专家」）；"
                         "② 猎聘需要本机装有 Chrome 或 Edge（浏览器渲染采集）；"
                         "③ 也可以直接把招聘页另存为 HTML 拖进对话框。")
            return self._reply(state, text, intent="search_empty", warnings=notes)

        if settings.get("deep_detail"):
            if event:
                event("progress", {"source": "详情页", "keyword": "补全 JD 全文与技能标签"})
            filled = self._enrich(jobs, q["sources"], settings)
            if filled:
                notes.append(f"已补全 {filled} 条 JD 全文，技能识别更准")

        result = analyzer.analyze(jobs, state.get("profile"), q)
        state["jobs"] = jobs
        state["analysis"] = result
        state["query"] = q
        state["last_sid_msg"] = msg
        self.store.save(sid, state)

        text = self._search_text(q, result, jobs, notes, round(time.time() - t0, 1))
        return self._reply(state, text, intent="search", analysis=result,
                           jobs=self._table(jobs), warnings=notes,
                           meta={"elapsed": round(time.time() - t0, 1), "count": len(jobs)})

    # ---------------- 追问 ----------------

    def _followup(self, state: dict, sid: str, msg: str) -> dict[str, Any]:
        jobs: list[dict[str, Any]] = state.get("jobs") or []
        base_q = state.get("query") or {}
        acts: list[str] = []
        filtered = list(jobs)

        edu = nlu._find_edu(msg)
        if edu:
            filtered = [j for j in filtered if (j.get("edu") or "不限") in (edu, "不限")]
            acts.append(f"筛选学历={edu}（含不限）")

        city = nlu._find_city(msg)
        if city:
            filtered = [j for j in filtered if city in (j.get("city") or "")]
            acts.append(f"筛选城市={city}")

        smin, smax = nlu._find_salary(msg)
        if smin:
            filtered = [j for j in filtered if (j.get("salary_mid") or 0) >= smin]
            acts.append(f"筛选月薪≥{int(smin / 1000)}K")
        if smax:
            filtered = [j for j in filtered if (j.get("salary_mid") or 10 ** 9) <= smax]
            acts.append(f"筛选月薪≤{int(smax / 1000)}K")

        emin, emax = nlu._find_exp(msg)
        if emin is not None and ("年" in msg):
            filtered = [j for j in filtered if (j.get("exp_low") or 0) <= (emin or 0) or True]
            filtered = [j for j in filtered if (j.get("exp_high") or 99) >= (emin or 0)]
            acts.append(f"筛选经验≥{emin}年可投")

        if "非外包" in msg or "排除外包" in msg or "不要外包" in msg:
            filtered = [j for j in filtered if "外包" not in (j.get("company") or "") + (j.get("title") or "")]
            acts.append("排除外包")

        # 排序
        sort_key = None
        if re.search(r"(薪资|salary).*(最高|最高|top|排序|前)", msg) or "最贵" in msg:
            filtered.sort(key=lambda j: -(j.get("salary_mid") or 0))
            sort_key = "薪资降序"
        elif "最新" in msg or "新发布" in msg:
            filtered.sort(key=lambda j: (j.get("publish_date") or ""), reverse=True)
            sort_key = "发布时间降序"
        if sort_key:
            acts.append(f"排序：{sort_key}")

        m = re.search(r"(?:前|top|Top|TOP)\s*(\d+)", msg)
        if m:
            n = min(50, int(m.group(1)))
            filtered = filtered[:n]
            acts.append(f"取前 {n} 条")

        # 只有某种技能
        sk_msg = re.findall(r"(?:只会|只要|要求|具备|提到|有)\s*([A-Za-z][A-Za-z0-9+#.]{1,14})", msg)
        if sk_msg:
            need = [s.lower() for s in sk_msg]
            filtered = [j for j in filtered
                        if any(n in [x.lower() for x in (j.get("skills") or [])] for n in need)]
            acts.append("筛选技能：" + "、".join(sk_msg))

        if not filtered:
            return self._reply(state, f"按「{'、'.join(acts) or msg}」筛完没有结果了，换个条件试试？"
                                     f"当前结果集共 {len(jobs)} 条。", intent="followup_empty",
                               analysis=state.get("analysis"), jobs=self._table(jobs))

        q2 = dict(base_q)
        q2["followup"] = acts
        result = analyzer.analyze(filtered, state.get("profile"), q2)
        state["analysis"] = result
        self.store.save(sid, state)

        head = (f"在上一轮 {len(jobs)} 条结果里做了：{'、'.join(acts)}，命中 {len(filtered)} 条。"
                if acts else f"对当前 {len(filtered)} 条结果重新统计。")
        lines = [head + "\n"]
        s = result["overview"].get("salary") or {}
        if s.get("count"):
            lines.append(f"这批岗位月薪中位数 {self._k(s['median'])}，"
                         f"区间 P25–P75 为 {self._k(s.get('p25'))}–{self._k(s.get('p75'))}，"
                         f"最高 {self._k(s.get('max'))}。")
        for ins in [x for x in result["insights"] if x["title"] != "市场水位"][:3]:
            lines.append(f"- **{ins['title']}**：{ins['text']}")

        # 针对「缺什么技能 / 城市对比」给出专门回答
        mt = result.get("match")
        if re.search(r"(缺|短板|差距|补|学什么|不会|提升)", msg) and mt and mt.get("gaps"):
            gap_txt = "、".join(f"{g['name']}（{g['count']} 条 JD 要求）" for g in mt["gaps"][:8])
            lines.append(f"\n**你最该补的能力**：{gap_txt}。")
            lift = {t["name"]: t.get("lift") for t in result["skills"]["top"]}
            valued = [g for g in mt["gaps"] if (lift.get(g["name"]) or 0) > 0][:3]
            if valued:
                lines.append("其中带薪资溢价、值得优先补的：" + "、".join(
                    f"{v['name']}（+{self._k(lift.get(v['name']))}）" for v in valued) + "。")
        if re.search(r"(对比|比较)", msg) and len(result["demand"].get("cities") or []) >= 2:
            rows = sorted([c for c in result["demand"]["cities"] if c.get("median")],
                          key=lambda x: -x["median"])[:5]
            lines.append("\n**城市对比**（按中位薪资）：" + "；".join(
                f"{c['name']} {self._k(c['median'])}（{c['count']} 条）" for c in rows) + "。")

        if mt:
            lines.append(f"\n按你的画像，平均匹配度 {mt['avg_score']} 分，"
                         f"{mt['strong_fit']} 个岗位 ≥70 分。")
        return self._reply(state, "\n".join(lines), intent="followup", analysis=result,
                           jobs=self._table(filtered))

    # ---------------- 采集工具 ----------------

    @staticmethod
    def _dedupe(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for j in jobs:
            key = re.sub(r"\s+", "", (j.get("title") or ""))[:24] + "|" + \
                  re.sub(r"\s+", "", (j.get("company") or ""))[:20]
            url = (j.get("url") or "").split("?")[0]
            if key in seen or (url and url in seen):
                continue
            if url:
                seen.add(url)
            seen.add(key)
            out.append(j)
        return out

    @staticmethod
    def _filter(jobs: list[dict[str, Any]], q: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """硬条件过滤。返回 (保留的岗位, 因城市不符被剔除的条数)。"""
        out = []
        city = (q.get("city") or "").strip()
        dropped_city = 0
        for j in jobs:
            if city and city != "全国":
                jc = (j.get("city") or "").strip()
                if jc and city not in jc and jc not in city:
                    dropped_city += 1
                    continue
            if q.get("salary_min"):
                mid = j.get("salary_mid")
                if mid is not None and mid < q["salary_min"]:
                    continue
            if q.get("exp_min") is not None and q.get("exp_max") is None:
                if (j.get("exp_high") or 99) < q["exp_min"]:
                    continue
            if q.get("edu"):
                from .normalize import edu_rank
                if edu_rank(j.get("edu") or "不限") > edu_rank(q["edu"]) + 0:
                    continue
            if q.get("exclude"):
                blob = (j.get("title") or "") + (j.get("company") or "")
                if any(w in blob for w in q["exclude"]):
                    continue
            out.append(j)
        out.sort(key=lambda j: (j.get("publish_date") or ""), reverse=True)
        return out, dropped_city

    def _enrich(self, jobs: list[dict[str, Any]], sources: list[str],
                settings: dict[str, Any], cap: int = 20) -> int:
        """并发补全 JD 全文（列表页正文被截断到 70 字左右）。"""
        targets: list[tuple[Any, dict[str, Any]]] = []
        for src in sources:
            try:
                ad = adapters.build(src, settings)
            except Exception:
                continue
            for j in jobs:
                if j.get("source") != src:
                    continue
                if len(j.get("jd") or "") > 300 or not j.get("url"):
                    continue
                targets.append((ad, j))
                if len(targets) >= ENRICH_CAP.get(src, cap):
                    break
            if len(targets) >= cap:
                break
        if not targets:
            return 0

        def work(pair: tuple[Any, dict[str, Any]]) -> int:
            ad, job = pair
            before = len(job.get("jd") or "")
            try:
                ad.fetch_detail(job)
            except Exception:
                return 0
            return 1 if len(job.get("jd") or "") > before else 0

        with ThreadPoolExecutor(max_workers=6) as pool:
            return sum(pool.map(work, targets))

    @staticmethod
    def _table(jobs: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
        out = []
        for j in jobs[:limit]:
            out.append({
                "id": j.get("id"), "title": j.get("title"), "company": j.get("company"),
                "salary": j.get("salary_text") or "面议", "salary_mid": j.get("salary_mid"),
                "city": (j.get("city") or "") + (("·" + j["district"]) if j.get("district") else ""),
                "exp": j.get("exp_text"), "edu": j.get("edu"), "industry": j.get("industry"),
                "size": j.get("company_size"), "skills": (j.get("skills") or [])[:6],
                "publish": j.get("publish_date"), "url": j.get("url"), "source": j.get("source"),
                "jd": (j.get("jd") or "")[:160],
            })
        return out

    # ---------------- 文本生成 ----------------

    @staticmethod
    def _k(v: float | None) -> str:
        if not v:
            return "—"
        return f"{v / 1000:.1f}K" if v < 10000 else f"{v / 10000:.2f}万"

    def _search_text(self, q: dict, res: dict, jobs: list[dict], notes: list[str],
                     elapsed: float) -> str:
        ov = res["overview"]
        s = ov["salary"]
        label = "、".join(k for k in q["keywords"] if k)
        head = (f"已采集 **{ov['total']}** 条「{label}」岗位" if label
                else f"已采集 **{ov['total']}** 条{q.get('city') or ''}在招岗位")
        lines = [head + f"（{ov['companies']} 家公司，{ov['cities']} 个城市，耗时 {elapsed}s）。\n"]
        lines.append(f"**市场水位**：月薪中位数 **{self._k(s['median'])}**，均值 {self._k(s['mean'])}，"
                     f"P25 {self._k(s.get('p25'))} / P75 {self._k(s.get('p75'))} / P90 {self._k(s.get('p90'))}。")
        sk = res["skills"]
        if sk["top"]:
            lines.append(f"**技能热度**：" + "、".join(
                f"{t['name']}×{t['count']}" for t in sk["top"][:6]) + "。")
        edu = res["demand"]["edu"]
        if edu:
            main = max(edu, key=lambda x: x["count"])
            lines.append(f"**门槛**：学历以「{main['name']}」为主（{main['share']}%），"
                         f"经验以「{max(res['demand']['exp'], key=lambda x: x['count'])['name']}」为主。")
        lines.append("")
        for ins in res["insights"]:
            tag = {"info": "•", "warn": "⚠", "action": "→"}.get(ins["type"], "•")
            lines.append(f"{tag} **{ins['title']}**：{ins['text']}")
        if notes:
            lines.append("\n采集提示：" + "；".join(notes))
        lines.append("\n右侧可以切换「薪资 / 需求结构 / 技能 / 公司 / 明细」看完整图表，"
                     "也可以继续追问，例如「只看硕士，薪资前 10」。")
        return "\n".join(lines)

    def _profile_text(self, profile: dict[str, Any]) -> str:
        sk = extract_skills(" ".join(profile.get("skills_text") or []))
        parts = []
        if sk:
            parts.append(f"技能：{'、'.join(sk)}")
        if profile.get("years") is not None:
            parts.append(f"经验：{profile['years']:g} 年")
        if profile.get("edu"):
            parts.append(f"学历：{profile['edu']}")
        if profile.get("expect_salary"):
            parts.append(f"期望：{profile['expect_salary'] / 1000:.0f}K")
        txt = "记住你的画像了 —— " + "，".join(parts) + "。\n\n"
        txt += "接下来找岗位时我会自动算匹配度和技能缺口。现在可以说「帮我找上海的 AI 产品经理岗位」了。"
        return txt

    def _set_cookie(self, state: dict, settings: dict, raw: str) -> dict[str, Any]:
        raw = raw.strip()
        src = "zhaopin"
        if "liepin" in raw.lower() or "猎聘" in raw:
            src = "liepin"
        cookie = re.sub(r"^(zhaopin|liepin|智联|猎聘)\s*[:：]?\s*", "", raw, flags=re.I).strip()
        settings.setdefault("cookies", {})[src] = cookie
        state["settings"] = settings
        self.store.save(state["_sid"], state)
        label = adapters.LABELS.get(src, src)
        return self._reply(state, f"已保存 {label} 的 Cookie（{len(cookie)} 字符）。"
                                  f"下次采集会自动带上它。", intent="settings")

    def _source_status(self, state: dict, settings: dict) -> dict[str, Any]:
        rows = adapters.all_status(settings)
        lines = ["**数据源状态**\n"]
        for r in rows:
            icon = "✅" if r.get("ready") else ("⚠️" if r.get("requires_cookie") else "✅")
            lines.append(f"{icon} **{r['label']}**：{'可直接采集' if r.get('ready') else '未配置'}"
                         + (f" —— {r.get('note')}" if r.get("note") else ""))
        lines.append("\n配置方法：把浏览器里招聘网站的 Cookie 整段发我，格式 `cookie: <粘贴内容>`。"
                     "或直接说「导入页面」，把招聘页另存为 HTML 后拖进对话框。")
        return self._reply(state, "\n".join(lines), intent="status",
                           meta={"sources": rows})

    def _export(self, state: dict, sid: str) -> dict[str, Any]:
        jobs = state.get("jobs") or []
        res = state.get("analysis")
        if not jobs or not res:
            return self._reply(state, "还没有可导出的数据，先说个岗位让我去采集吧。", intent="export_empty")
        name = "jd_report_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = self.store.write_report(sid, name + ".html",
                                            report.render(res, jobs, state.get("query") or {},
                                                          state.get("profile")))
        csv_path = self.store.write_csv(sid, name + ".csv", jobs)
        return self._reply(state, f"报告已生成：\n\n- 分析报告（含全部图表）：`{html_path}`\n"
                                  f"- 岗位明细 CSV：`{csv_path}`\n\n"
                                  f"报告是独立 HTML，可以直接发给同事或存档。",
                           intent="export", meta={"files": [html_path, csv_path]},
                           analysis=res, jobs=self._table(jobs))

    # ---------------- 回复包装 ----------------

    @staticmethod
    def _reply(state: dict, text: str, intent: str = "", analysis: dict | None = None,
               jobs: list[dict] | None = None, warnings: list[str] | None = None,
               meta: dict | None = None) -> dict[str, Any]:
        return {
            "reply": text,
            "intent": intent,
            "analysis": analysis,
            "jobs": jobs or [],
            "warnings": warnings or [],
            "meta": meta or {},
            "profile": state.get("profile") or {},
            "settings": {"sources": adapters.all_status(state.get("settings") or {})},
        }
