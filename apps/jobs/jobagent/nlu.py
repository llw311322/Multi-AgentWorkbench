"""自然语言理解：把用户的一句话解析成结构化检索条件 / 个人画像。"""
from __future__ import annotations

import re
from typing import Any

CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "苏州", "西安", "天津", "重庆",
    "长沙", "郑州", "青岛", "合肥", "宁波", "厦门", "福州", "济南", "大连", "沈阳", "哈尔滨", "长春",
    "石家庄", "太原", "南昌", "贵阳", "昆明", "南宁", "海口", "兰州", "乌鲁木齐", "佛山", "东莞",
    "无锡", "常州", "珠海", "中山", "温州", "嘉兴", "泉州", "烟台", "徐州", "香港",
]

TITLE_SUFFIX = ("工程师", "架构师", "分析师", "经理", "专员", "主管", "总监", "设计师", "助理", "顾问",
                "专家", "研究员", "实习生", "科学家", "运营", "开发", "测试", "算法", "销售", "产品经理",
                "会计", "律师", "教师", "策划", "编辑", "客服", "店长", "技师", "司机", "采购", "人事")

STOP_WORDS = [
    "帮我", "帮忙", "请", "麻烦", "我想", "我要", "想", "要", "找", "搜", "搜集", "收集", "搜索", "查",
    "查询", "看看", "看下", "一下", "分析", "生成", "整理", "统计", "汇总", "对比", "比较", "相关",
    "岗位", "职位", "招聘", "jd", "JD", "信息", "数据", "报告", "最新", "最近", "目前", "现在",
    "的", "了", "吗", "呢", "吧", "啊", "给我", "列出", "输出", "做", "一份", "详细", "深度",
    "在", "有", "和", "与", "及", "以及", "还有", "哪些", "什么", "怎么样", "如何", "情况",
]

SOURCE_WORDS = {
    "zhaopin": ["智联", "zhilian", "zhaopin"],
    "liepin": ["猎聘", "liepin", "猎头网站"],
}

DEFAULT_SOURCES = ["zhaopin", "liepin"]

EDU_WORDS = [("博士后", "博士后"), ("博士", "博士"), ("硕士", "硕士"), ("研究生", "硕士"),
             ("本科", "本科"), ("大专", "大专"), ("专科", "大专"), ("中专", "中专"), ("高中", "高中")]


def _find_city(text: str) -> str:
    hits = [(text.index(c), c) for c in CITIES if c in text]
    if not hits:
        return ""
    hits.sort()
    return hits[0][1]


def _find_salary(text: str) -> tuple[float | None, float | None]:
    t = text.lower().replace(" ", "")
    # 15-25k / 15k-25k
    m = re.search(r"(\d+(?:\.\d+)?)\s*[k千]\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*[k千]", t)
    if m:
        return float(m.group(1)) * 1000, float(m.group(2)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*[k千]", t)
    if m:
        return float(m.group(1)) * 1000, float(m.group(2)) * 1000
    # 1.5-2.5万
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*万", t)
    if m:
        return float(m.group(1)) * 10000, float(m.group(2)) * 10000
    # 20k以上 / 2万以上 / 月薪2万
    m = re.search(r"(\d+(?:\.\d+)?)\s*[k千]\s*(?:以上|起|\+)", t)
    if m:
        return float(m.group(1)) * 1000, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*(?:以上|起|\+)", t)
    if m:
        return float(m.group(1)) * 10000, None
    # 期望 25k / 期望月薪 20000 / 目标 2 万
    m = re.search(r"(?:期望|期望薪资|目标|希望)\s*(?:月薪|薪资|工资)?\s*(\d+(?:\.\d+)?)\s*([kK千万])?", t)
    if m and m.group(1):
        v = float(m.group(1))
        unit = m.group(2)
        if unit == "万":
            v *= 10000
        elif unit in ("k", "K", "千"):
            v *= 1000
        elif v < 200:
            v *= 1000
        return v, None
    m = re.search(r"(?:月薪|薪资|工资)\s*(\d+(?:\.\d+)?)\s*[k千]?\s*(?:以上|起)?", t)
    if m and m.group(1):
        v = float(m.group(1))
        return (v * 1000 if v < 200 else v), None
    return None, None


def _find_exp(text: str) -> tuple[float | None, float | None]:
    t = text.replace(" ", "")
    if "应届" in t or "校招" in t:
        return 0.0, 1.0
    if "经验不限" in t or "不限经验" in t:
        return 0.0, 99.0
    m = re.search(r"(\d+)\s*[-~到至]\s*(\d+)\s*年", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+)\s*年以上", t)
    if m:
        return float(m.group(1)), None
    m = re.search(r"(\d+)\s*年(?:经验|工作|开发)?", t)
    if m:
        return float(m.group(1)), None
    return None, None


def _find_edu(text: str) -> str:
    for word, std in EDU_WORDS:
        if word in text:
            return std
    return ""


def _find_sources(text: str) -> list[str]:
    t = text.lower()
    found = []
    for src, words in SOURCE_WORDS.items():
        if any(w.lower() in t for w in words):
            found.append(src)
    if not found and ("两个" in text or "都" in text or "全平台" in text):
        return list(DEFAULT_SOURCES)
    return found


# 只用于「从左往右剥掉」的口语前缀/边界词，绝不从词中间删字（否则会切碎「数据分析师」）
PREFIX_TOKENS = sorted(set([
    "帮我", "帮忙", "麻烦", "请你", "请", "我想", "我要", "我想找", "想找", "想要", "想", "要",
    "找一下", "找找", "找", "搜一下", "搜搜", "搜", "搜集", "收集", "搜索", "查一下", "查询", "查查", "查",
    "看看", "看一下", "看", "一下", "有哪些", "哪些", "什么", "最近", "最新", "目前", "现在", "在招",
    "我", "在", "去", "到", "的", "个", "几", "份", "相关", "有", "都", "也", "还", "并", "以及",
    "和", "与", "及", "还有", "一些", "所有", "全部", "尽可能", "尽量", "多", "的岗位", "岗位",
    "职位", "招聘", "jd", "JD", "谢谢", "请问", "有没有", "没有", "能不能", "可以", "推荐",
    "跳槽", "远程", "远程办公", "线下", "全职", "兼职", "给我", "麻烦你", "想知道", "了解",
    "只看", "仅看", "只要", "只找", "猎聘", "智联", "平台", "招聘网站",
] + CITIES), key=len, reverse=True)

# 过于宽泛、单拎出来搜索结果会很杂的兜底词
GENERIC_ONLY = {"开发工程师", "工程师", "分析师", "产品经理", "运营", "测试", "开发", "销售",
                "经理", "专员", "助理", "实习生", "数据分析", "岗位", "职位"}

# 常见岗位/领域词：不带「师/工程师/经理」后缀时（「青岛 数据分析」「青岛 前端」）
# 只能靠词典命中，否则会被 STOP_WORDS 里的「数据」「分析」吃干净
DOMAIN_TERMS = [
    "数据分析", "数据挖掘", "数据治理", "数据开发", "商业分析", "机器学习", "深度学习",
    "自然语言处理", "计算机视觉", "大模型", "算法", "前端", "后端", "全栈", "测试", "运维",
    "网络安全", "嵌入式", "硬件", "结构设计", "电气", "机械", "工艺", "质量",
    "产品经理", "产品运营", "用户运营", "内容运营", "新媒体", "短视频", "直播", "电商", "跨境电商",
    "外贸", "供应链", "采购", "仓储", "物流",
    "会计", "审计", "财务", "风控", "投资", "量化", "理财", "保险",
    "销售", "客服", "人事", "人力资源", "行政", "法务", "律师",
    "教师", "讲师", "医生", "护士", "药师", "编辑", "记者", "文案",
    "设计", "UI", "交互", "平面", "室内", "建筑", "土木", "造价", "化工", "医药", "生物",
]


def _trim_prefix(s: str) -> str:
    changed = True
    while changed and s:
        changed = False
        for tok in PREFIX_TOKENS:
            if s.startswith(tok):
                s = s[len(tok):].lstrip()
                changed = True
                break
        if not changed:
            s2 = re.sub(r"^[\d\.]+\s*(?:年|k|K|万|千)?", "", s).lstrip()
            s2 = re.sub(r"^[，,。.;；:：!！?？、\s　]+", "", s2)
            if s2 != s:
                s = s2
                changed = True
    return s.strip()


def extract_keywords(text: str) -> list[str]:
    """抽取岗位关键词，支持「数据分析师」「AI产品经理」「Java开发」等复合词。"""
    t = text
    found: list[str] = []
    spans: list[tuple[int, int]] = []

    # 1) 优先用职位后缀定位，向左回溯 12 字符后剥掉口语前缀
    for suf in sorted(TITLE_SUFFIX, key=len, reverse=True):
        for m in re.finditer(re.escape(suf), t):
            if any(a <= m.start() < b for a, b in spans):
                continue
            start = max(0, m.start() - 12)
            window = t[start:m.end()]
            # 先按标点切断，避免把上一句话的残渣带进来
            cut = re.split(r"[，,。．.；;：:！!？?、\n\r]", window)
            if cut and cut[-1].strip():
                window = cut[-1].strip()
            cand = _trim_prefix(window)
            cand = re.sub(r"\s+", "", cand)
            if 2 <= len(cand) <= 16 and not cand.isdigit() and re.search(r"[\u4e00-\u9fffA-Za-z]", cand):
                found.append(cand)
                spans.append((m.start(), m.end()))

    # 2) 单独出现的技术名词：Python、SQL、AI
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9+#.\-]{1,14})", t):
        w = m.group(1)
        if w.lower() in {"k", "hr", "okr", "kpi", "ppt", "ui", "ue", "bi", "saas", "crm", "erp",
                         "jd", "pdf", "excel"} and w.lower() != "excel":
            continue
        if any(a <= m.start() < b for a, b in spans):
            continue
        if len(w) >= 2:
            found.append(w)

    # 3) 领域词典：没有职位后缀的写法（「青岛 数据分析」「青岛 前端」）
    if not found:
        probe = text
        for c in sorted(CITIES, key=len, reverse=True):
            probe = probe.replace(c, " ")
        for term in sorted(DOMAIN_TERMS, key=len, reverse=True):
            if term.lower() in probe.lower():
                found.append(term)
                break

    # 4) 兜底：整句清洗后取最长实词块
    if not found:
        cleaned = text
        cleaned = re.sub(r"(在|去|到)?(" + "|".join(CITIES) + ")", " ", cleaned)
        cleaned = re.sub(r"\d+\s*[kK千万]?\S*", " ", cleaned)
        for w in sorted(STOP_WORDS, key=len, reverse=True):
            cleaned = cleaned.replace(w, " ")
        cleaned = re.sub(r"[，,。.;；:：!！?？、\s]+", " ", cleaned)
        parts = [p for p in cleaned.split() if len(p) >= 2]
        if parts:
            found.append(max(parts, key=len))

    # 剥掉残留的口语前缀与城市名（"猎聘上深圳的测试开发" -> "测试开发"）
    stripped: list[str] = []
    for k in found:
        k = re.sub(r"^(上|在|去|到|看|找|搜|要|想|的)+", "", k.strip())
        for c in sorted(CITIES, key=len, reverse=True):
            if k.startswith(c) and len(k) > len(c):
                k = k[len(c):]
                break
        k = re.sub(r"^(的|上|里|中|在|市|省|区|县)+", "", k).strip()
        if len(k) >= 2:
            stripped.append(k)
    found = stripped or found

    # 去重保序；若只剩泛化词则保留最长者
    uniq: list[str] = []
    seen: set[str] = set()
    for k in found:
        kl = k.lower().strip()
        if not kl or kl in seen:
            continue
        seen.add(kl)
        uniq.append(k.strip())
    specific = [k for k in uniq if k not in GENERIC_ONLY]
    if specific:
        uniq = specific
    result = [k for k in uniq if not any(k != o and k.lower() in o.lower() and len(k) < len(o)
                                        for o in uniq)]
    return result[:3]


def fallback_keywords(kw: str) -> list[str]:
    """采集无结果时的降级关键词：去后缀 / 取英文主干 / 截短。"""
    out: list[str] = []
    for suf in sorted(TITLE_SUFFIX, key=len, reverse=True):
        if kw.endswith(suf) and len(kw) > len(suf) + 1:
            out.append(kw[: -len(suf)])
            break
    latin = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,14}", kw)
    out.extend(latin)
    if len(kw) > 4:
        out.append(kw[:4])
    seen, res = set(), []
    for k in out:
        k = k.strip()
        if k and k.lower() not in seen and k != kw:
            seen.add(k.lower())
            res.append(k)
    return res[:2]


def parse_query(text: str, default_sources: list[str] | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {}
    q["city"] = _find_city(text)
    lo, hi = _find_salary(text)
    q["salary_min"], q["salary_max"] = lo, hi
    elo, ehi = _find_exp(text)
    q["exp_min"], q["exp_max"] = elo, ehi
    q["edu"] = _find_edu(text)
    q["keywords"] = extract_keywords(text)
    srcs = _find_sources(text)
    q["sources"] = srcs or (default_sources or DEFAULT_SOURCES)
    q["exclude"] = []
    if "不要外包" in text or "排除外包" in text:
        q["exclude"].append("外包")
    if "非外包" in text:
        q["exclude"].append("外包")
    q["pages"] = 3 if re.search(r"(尽可能|全部|所有|大量|更多|多抓|越多)", text) else 2
    q["limit"] = 60 if q["pages"] >= 2 else 40
    q["raw"] = text
    return q


def extract_profile(text: str) -> dict[str, Any] | None:
    """识别「我会 X，有 N 年经验，本科，期望 25k」这类个人情况陈述。"""
    t = text
    if not re.search(r"(我会|我熟悉|我精通|我有|我是|我的|期望|期望薪资|目前).{0,30}", t):
        return None
    if not re.search(r"(会|熟悉|精通|掌握|经验|期望|本科|硕士|大专|年)", t):
        return None
    profile: dict[str, Any] = {}
    edu = _find_edu(t)
    if edu:
        profile["edu"] = edu
    m = re.search(r"(\d+)\s*年(?:经验|工作|开发|从业)?", t)
    if m:
        profile["years"] = float(m.group(1))
    lo, hi = _find_salary(t)
    if lo or hi:
        profile["expect_salary"] = int(lo or hi or 0)
    skill_hits = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,14}", t)
    profile["skills_text"] = skill_hits[:12]
    return profile if len(profile) >= 1 else None


def is_followup(text: str) -> bool:
    """判断是否是对上一轮结果的追问（不重新采集）。"""
    t = text.strip()
    if len(t) <= 12 and re.search(r"(呢|吗|？|\?)$", t):
        return True
    markers = ["那", "这", "其中", "这些", "上面", "刚才", "结果里", "里面", "排序", "前", "top",
               "只看", "筛", "过滤", "对比", "为什么", "哪个", "最高", "最低", "平均"]
    return any(m in t for m in markers) and not re.search(r"(搜|找|采集|抓|新的)", t)


# ---------------- 关键词扩展 ----------------

DOMAIN_SYNONYMS: dict[str, list[str]] = {
    "数据分析": ["数据分析", "商业分析", "BI", "数据运营"],
    "算法": ["算法", "机器学习", "深度学习"],
    "大模型": ["大模型", "LLM", "AIGC"],
    "产品": ["产品经理", "产品"],
    "运营": ["运营", "运营专员"],
    "前端": ["前端", "web前端"],
    "后端": ["后端", "服务端"],
    "测试": ["测试", "测试工程师"],
    "运维": ["运维", "SRE"],
    "人力": ["人力资源", "HR"],
    "财务": ["财务", "会计"],
    "销售": ["销售", "大客户销售"],
    "设计": ["UI设计", "视觉设计", "交互设计"],
}


def expand_keywords(kw: str, max_n: int = 3) -> list[str]:
    """把用户词扩成少量近义关键词，弥补单关键词只返回 20 条的限制。"""
    base = kw
    for suf in sorted(TITLE_SUFFIX, key=len, reverse=True):
        if kw.endswith(suf) and len(kw) > len(suf) + 1:
            base = kw[: -len(suf)].strip()
            break

    cands: list[str] = [kw]
    if base and base != kw:
        cands.append(base)

    for key, syns in DOMAIN_SYNONYMS.items():
        if key not in kw and key not in base:
            continue
        if key in kw or key in base:
            cands.extend(syns)

    out, seen = [], set()
    for c in cands:
        c = (c or "").strip()
        if not c or c.lower() in seen:
            continue
        # 中文词至少 3 字，纯拉丁词至少 2 字，避免「数据」「AI」这类过泛的检索词
        if len(c) < 3 and not re.fullmatch(r"[A-Za-z]{2,}", c):
            continue
        if len(c) == 2 and re.fullmatch(r"[A-Za-z]{2}", c) and c.lower() in {"ai", "bi", "hr", "ui", "qa"}:
            continue
        seen.add(c.lower())
        out.append(c)
    return out[:max_n]
