"""猎聘（www.liepin.com）采集适配器 —— 浏览器渲染通道。

为什么不用接口直连
------------------
猎聘搜索页是纯客户端渲染（React SPA），HTML 里没有任何岗位数据；数据由签名后的 XHR 拉取：
``POST https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job``
该接口需要 ``x-fscp-std-info`` 等签名头，匿名直连一律返回 ``{"code":"-1400"}``（实测空 body 也报 -1400，
说明是网关级签名校验，不是参数问题）。所以这里不逆向签名，改用浏览器渲染拿最终 DOM。

实测结论（无登录、无 Cookie）
----------------------------
* 列表页：40 条/页，字段齐全（岗位、薪资、城市/区、经验、学历、公司、行业、融资阶段、规模、招聘者）
* 翻页：``currentPage=0,1,2...``；城市必须用 ``dq=<编码>``，``city=`` 参数会被忽略
* 详情页：JD 全文在 ``[data-selector="job-intro-content"]``，另外有福利标签、公司简介
* 风控：UA 里带 ``HeadlessChrome`` 或短时间高频访问 → 跳「行为异常 / 安全验证」验证页。
  换真实 UA + 串行限速后可稳定采集；命中验证页会抛 SourceError 提示用户稍后再试。
"""
from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

from .base import BaseAdapter, SourceError, empty_job, sleep_human
from .. import normalize, skills as skillmod

# 猎聘城市编码（dq）。仅收录在页面筛选项里能直接取到编码的城市，其余城市走全国检索后本地过滤。
CITY_CODES: dict[str, str] = {
    "全国": "410",
    "北京": "010", "上海": "020", "天津": "030", "重庆": "040",
    "广州": "050020", "深圳": "050090",
    "苏州": "060080", "南京": "060020", "杭州": "070020",
    "大连": "210040", "成都": "280020", "武汉": "170020", "西安": "270020",
    # 山东：编码按地市递增，济南 250020 / 青岛 250070（实测 dq 改错会返回邻近城市）
    "济南": "250020", "青岛": "250070",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 渲染是重操作，且并发容易被判为异常行为 —— 全局串行 + 最小间隔
_RENDER_LOCK = threading.Semaphore(1)
_LAST_CALL = [0.0]
_MIN_GAP = 2.5


def _city_code(city: str) -> tuple[str, bool]:
    """返回 (编码, 是否精确匹配)。未收录的城市返回全国码 + False。"""
    c = (city or "全国").strip()
    if c in CITY_CODES:
        return CITY_CODES[c], True
    if re.fullmatch(r"\d{3,6}", c):
        return c, True
    for k, v in CITY_CODES.items():
        if k == "全国":
            continue
        if k in c or c in k:
            return v, True
    return CITY_CODES["全国"], False


def find_browser() -> str | None:
    """定位本机可用的 Chrome / Edge / Chromium。"""
    cands = [
        os.environ.get("LIEpin_BROWSER", ""),
        os.environ.get("BROWSER_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


# 带上「·14薪」，让 normalize.parse_salary 能算年薪月数
_SALARY_RE = re.compile(r">(\d+(?:\.\d+)?-\d+(?:\.\d+)?\s*[kK万](?:·\d{2}薪)?)<")
_EXP_RE = re.compile(r">(\d+年以[上内]|\d+-\d+年|经验不限|应届生|在校生)<")
# 猎聘常见「统招本科」「硕士及以上」，允许前后缀
_EDU_RE = re.compile(r">([^<]{0,6}?(?:博士|硕士|本科|大专|中专|高中|学历不限)[^<]{0,6})<")
# 注意：title 属性带 SEO 前缀（如「招聘数据分析师」），元素文本才是干净岗位名
_TITLE_RE = re.compile(r'<div class="ellipsis-1" title="([^"]*)">([^<]+)</div>')
_LOC_RE = re.compile(r'【</span><span class="ellipsis-1">([^<]+)</span>')
_COMPANY_RE = re.compile(r'companyName[^>]*>([^<]+)<')
_RECRUITER_RE = re.compile(r'recruiterName[^>]*>([^<]+)<')
_COMPANY_META_RE = re.compile(r'<div class="_[A-Za-z0-9]+ ellipsis-1">((?:<span>[^<]*</span>)+)</div>')
_STAGE = ("已上市", "不需要融资", "融资未公开", "D轮", "C轮", "B轮", "A轮", "天使轮",
          "未融资", "战略投资", "已融资")


def _parse_card(block: str, keyword: str, city: str) -> dict[str, Any] | None:
    """把一张岗位卡片解析成标准记录。"""
    m_id = re.search(r"jobId%22%3A%22(\d+)", block)
    m_url = re.search(r'href="(https://www\.liepin\.com/job/\d+\.shtml)', block)
    m_title = _TITLE_RE.search(block)
    if not (m_id and m_title):
        return None

    job = empty_job()
    job["id"] = "lp" + m_id.group(1)
    title = m_title.group(2)
    if not title.strip():
        title = re.sub(r"^招聘", "", m_title.group(1))
    job["title"] = normalize.clean(title, 80)
    job["url"] = m_url.group(1) if m_url else ""
    job["source"] = "liepin"

    loc = _LOC_RE.search(block)
    if loc:
        parts = loc.group(1).split("-", 1)
        job["city"] = parts[0].strip()
        job["district"] = parts[1].strip() if len(parts) > 1 else ""

    sal = _SALARY_RE.search(block)
    if sal:
        job["salary_text"] = sal.group(1).replace(" ", "")
        lo, hi, months = normalize.parse_salary(job["salary_text"])
        job["salary_low"], job["salary_high"], job["months"] = lo, hi, months
        job["salary_mid"] = normalize.salary_mid(lo, hi)
    elif "面议" in block:
        job["salary_text"] = "面议"

    exp = _EXP_RE.search(block)
    job["exp_text"] = exp.group(1) if exp else "不限"
    job["exp_low"], job["exp_high"] = normalize.parse_experience(job["exp_text"])

    edu = _EDU_RE.search(block)
    job["edu"] = normalize.parse_edu(edu.group(1)) if edu else "不限"

    comp = _COMPANY_RE.search(block)
    job["company"] = normalize.clean(comp.group(1), 60) if comp else ""

    meta = _COMPANY_META_RE.search(block)
    if meta:
        items = re.findall(r"<span>([^<]*)</span>", meta.group(1))
        items = [x.strip() for x in items if x.strip()]
        for it in items:
            if re.search(r"\d+\s*-\s*\d+人|\d+人以上|少于\d+人", it):
                job["company_size"] = it
            elif it in _STAGE or it.endswith("轮"):
                job["company_type"] = it
            elif not job["industry"]:
                job["industry"] = it

    rec = _RECRUITER_RE.search(block)
    if rec:
        name = normalize.clean(rec.group(1), 30)
        if name:
            job["tags"].append("猎头" if "猎头" in block else "HR直招")
            job["tags"].append(name)

    job["jd"] = ""
    return job


class LiepinAdapter(BaseAdapter):
    name = "liepin"
    label = "猎聘"
    requires_cookie = False
    BASE = "https://www.liepin.com/zhaopin/"

    def __init__(self, cookies: str = "", **opts: Any):
        super().__init__(cookies, **opts)
        self.browser = find_browser()
        self.budget = int(opts.get("budget", 11000))

    # ---------------- 状态 ----------------
    def status(self) -> dict[str, Any]:
        ok = bool(self.browser)
        return {
            "name": self.name, "label": self.label, "ready": ok,
            "requires_cookie": False, "has_cookie": bool(self.cookies),
            "note": (f"浏览器渲染通道：{os.path.basename(self.browser)}" if ok
                     else "未找到 Chrome/Edge，无法渲染（安装浏览器即可）"),
        }

    # ---------------- 渲染 ----------------
    def _render(self, url: str, wait: float | None = None,
                budget: int | None = None) -> str:
        if not self.browser:
            raise SourceError(self.label, "未找到可用的 Chrome/Edge",
                              "安装 Chrome 或 Edge，或设置环境变量 CHROME_PATH 指向浏览器")
        wait = _MIN_GAP if wait is None else wait
        with _RENDER_LOCK:
            gap = time.time() - _LAST_CALL[0]
            if gap < wait:
                time.sleep(wait - gap + random.uniform(0, 0.8))
            prof = tempfile.mkdtemp(prefix="liepin_prof_")
            cmd = [
                self.browser,
                "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                f"--user-agent={UA}", "--lang=zh-CN", "--window-size=1600,1000",
                f"--user-data-dir={prof}",
                f"--virtual-time-budget={budget or self.budget}",
                "--dump-dom", url,
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=90)
                html = res.stdout.decode("utf-8", "ignore")
            except subprocess.TimeoutExpired:
                raise SourceError(self.label, "页面渲染超时", "降低采集页数或稍后重试")
            except Exception as exc:  # pragma: no cover
                raise SourceError(self.label, f"浏览器启动失败：{exc}",
                                  "确认 Chrome/Edge 可正常启动") from exc
            finally:
                _LAST_CALL[0] = time.time()
                shutil.rmtree(prof, ignore_errors=True)
        if not html:
            raise SourceError(self.label, "页面渲染返回空内容", "稍后重试")
        return html

    def _render_until(self, url: str, markers: tuple[str, ...], tries: int = 3) -> str:
        """渲染直到 DOM 里出现任一 marker；网络慢时 --dump-dom 会早于数据返回。

        markers 里同时包含「有结果」和「零结果空状态」两种标识，避免把「真的搜不到」
        误判成渲染失败（后者会白白重试三轮）。
        """
        budget = self.budget
        for i in range(tries):
            html = self._render(url, wait=_MIN_GAP if i == 0 else 6.0, budget=budget)
            self._guard(html)
            if any(m in html for m in markers):
                return html
            budget += 5000
            time.sleep(1.5 + i * 1.5)
        raise SourceError(self.label, "页面渲染未返回岗位内容（可能网络较慢）",
                          "稍后重试，或减少采集页数")

    def _guard(self, html: str) -> None:
        if "行为异常" in html or "安全中心-验证码" in html:
            raise SourceError(
                self.label, "触发「行为异常」安全验证",
                "猎聘对高频访问会弹验证码墙。稍等 2-3 分钟再试，或减少单次采集页数")


    # ---------------- 检索 ----------------
    def search(self, keyword: str, city: str = "全国", pages: int = 1, limit: int = 40,
               **filters: Any) -> list[dict[str, Any]]:
        # 40 条/页，两页已足够；再翻容易触发风控且没有增量价值
        pages = min(max(1, pages), 2)
        code, exact = _city_code(city)
        jobs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for p in range(0, max(1, pages)):
            params = f"?key={_q(keyword)}&dq={code}&currentPage={p}"
            html = self._render_until(self.BASE + params,
                                      ("jobCardPcContainer", "job-list-box", "empty"))
            blocks = html.split("jobCardPcContainer")[1:]
            if not blocks:
                break
            new = 0
            for b in blocks:
                job = _parse_card(b[:4000], keyword, city)
                if job and job["id"] not in seen:
                    seen.add(job["id"])
                    jobs.append(job)
                    new += 1
            if new == 0:
                break
            if len(jobs) >= limit:
                break
            sleep_human(0.8, 1.6)

        # 猎聘列表里会插入外地推广位，按城市兜一遍；结果太少则保留原样
        target = (city or "").strip()
        if target and target != "全国":
            narrow = [j for j in jobs if not j["city"] or target in j["city"]]
            if len(narrow) >= max(5, len(jobs) // 3):
                jobs = narrow

        for j in jobs:
            if not j["skills"]:
                j["skills"] = skillmod.extract_skills(j["title"] + " " + j["jd"], j["tags"])
        return jobs[:limit]

    # ---------------- 详情 ----------------
    def fetch_detail(self, job: dict[str, Any]) -> dict[str, Any]:
        url = job.get("url") or ""
        if not url:
            return job
        try:
            html = self._render_until(url, ("job-intro-content", "job-intro-container"), tries=2)
        except SourceError:
            return job
        m = re.search(r'data-selector="job-intro-content"[^>]*>(.*?)</dd>', html, re.S)
        if not m:
            return job
        desc = re.sub(r"<br\s*/?>", "\n", m.group(1))
        desc = re.sub(r"</p>", "\n", desc)
        desc = normalize.clean(desc, 6000)
        if len(desc) > len(job.get("jd") or ""):
            job["jd"] = desc
        # 福利标签 + 更新时间
        welfare = re.findall(r'class="_[A-Za-z0-9]+[^"]*">([^<]{2,12})</span>', html)
        picked = [w for w in welfare if re.search(r"(五险|年终|奖金|补贴|体检|餐|旅游|培训|双休|弹性|股票|期权)", w)]
        if picked:
            job["tags"] = list(dict.fromkeys(job["tags"] + picked[:8]))
        mt = re.search(r"(\d+)\s*天前更新", html)
        if mt:
            import datetime
            d = datetime.date.today() - datetime.timedelta(days=int(mt.group(1)))
            job["publish_date"] = d.isoformat()
        job["skills"] = skillmod.extract_skills(
            (job.get("title") or "") + " " + (job.get("jd") or ""), job.get("tags"))
        return job


def _q(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s or "")
