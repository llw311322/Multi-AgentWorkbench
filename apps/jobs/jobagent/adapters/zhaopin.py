"""智联招聘（sou.zhaopin.com）采集适配器。

通道说明：搜索页服务端渲染，页面内嵌 `__INITIAL_STATE__` JSON，包含岗位列表全部关键字段
（岗位名、薪资、学历、经验、城市、公司规模、行业、技能标签、JD 摘要、发布时间）。
实测无需登录即可获取第一页 ~20 条/关键词，翻页参数 `p=`。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .base import BaseAdapter, SourceError, empty_job, http_get, sleep_human
from .. import normalize, skills as skillmod

# 城市码：以 jobagent/data/zp_cities.json 为准（由搜索页标题逐个校验生成，396 个城市）。
# 内置这份小表只是兜底——文件丢了也还能跑主流城市。
FALLBACK_CITY_CODES = {
    "全国": "全国", "北京": "530", "上海": "538", "广州": "763", "深圳": "765", "杭州": "653",
    "成都": "801", "武汉": "736", "南京": "635", "苏州": "639", "西安": "854", "天津": "531",
    "重庆": "551", "郑州": "719", "青岛": "703", "济南": "702", "合肥": "664", "宁波": "654",
    "长沙": "749", "东莞": "779", "佛山": "768", "沈阳": "599", "大连": "600",
}

_CITY_FILE = Path(__file__).resolve().parent.parent / "data" / "zp_cities.json"


def _load_city_codes() -> dict[str, str]:
    codes = dict(FALLBACK_CITY_CODES)
    try:
        data = json.loads(_CITY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            codes.update({str(k).strip(): str(v).strip() for k, v in data.items() if k and v})
    except Exception:  # noqa: BLE001 —— 文件缺失/损坏就用兜底表，不影响采集
        pass
    return codes


CITY_CODES: dict[str, str] = _load_city_codes()


def _norm_city(city: str) -> str:
    """「青岛市」→「青岛」，去掉空格与行政区后缀，便于匹配用户输入。"""
    c = re.sub(r"\s+", "", city or "")
    for suf in ("市辖区", "地区", "自治州", "盟", "市"):
        if c.endswith(suf) and len(c) > len(suf) + 1:
            c = c[: -len(suf)]
            break
    return c


def _city_code(city: str) -> tuple[str, bool]:
    """返回 (jl 编码, 是否精确命中)。未收录的城市回退全国，由上层做本地城市过滤。"""
    c = _norm_city(city) or "全国"
    if c in CITY_CODES:
        return CITY_CODES[c], True
    if re.fullmatch(r"\d{3}", c):
        return c, True
    for k, v in CITY_CODES.items():
        if k == "全国":
            continue
        if k in c or c in k:
            return v, True
    return "全国", False


class ZhaopinAdapter(BaseAdapter):
    name = "zhaopin"
    label = "智联招聘"
    requires_cookie = False

    BASE = "https://sou.zhaopin.com/"

    def search(self, keyword: str, city: str = "全国", pages: int = 1, limit: int = 40,
               **filters: Any) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        jl, exact = _city_code(city)
        target = _norm_city(city) if exact and city and city != "全国" else ""
        if city and city != "全国" and not exact:
            self.last_note = f"{self.label}：城市库里没有「{city}」，已按全国检索，请核对结果城市"
        for p in range(1, max(1, pages) + 1):
            params = {"kw": keyword, "jl": jl, "p": str(p)}
            if filters.get("salary"):
                params["s"] = str(filters["salary"])
            url = self.BASE + "?" + urllib.parse.urlencode(params)
            try:
                _st, html = http_get(url, headers={"Referer": "https://www.zhaopin.com/"})
            except Exception as exc:  # 网络层异常
                if not jobs:
                    raise SourceError(self.label, f"请求失败：{exc}", "检查网络或稍后重试") from exc
                break

            if "aliyun_waf" in html or "acw_sc__v2" in html:
                raise SourceError(self.label, "被风控验证拦截（阿里云 WAF）",
                                  "降低频率后重试，或在设置中填入浏览器 Cookie")
            state = self._extract_state(html)
            if state is None:
                if not jobs:
                    raise SourceError(self.label, "页面结构变化，未找到内嵌数据",
                                      "可改用「导入页面 HTML」方式采集")
                break
            plist = state.get("positionList") or []
            if not plist:
                break
            new_count = 0
            for raw in plist:
                job = self._map(raw, keyword, city)
                if job and job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    jobs.append(job)
                    new_count += 1
            if new_count == 0:
                # 服务端忽略分页参数时会重复返回第一页，直接停
                break
            if len(jobs) >= limit:
                break
            if len(plist) < 20:
                break
            sleep_human(0.8, 1.6)

        # 服务端偶尔会忽略 jl（返回邻近城市或全国），这里按城市名再筛一次，
        # 否则「青岛」会拿回一堆济南岗位，用户看到的就是「答非所问」。
        if target:
            local = [j for j in jobs if target in _norm_city(j.get("city") or "")]
            dropped = len(jobs) - len(local)
            if dropped:
                self.last_note = (f"{self.label}：请求「{city}」但返回结果含 {dropped} 条外地岗位，已剔除")
            jobs = local
        return jobs[:limit]

    # ---------------- 解析 ----------------

    @staticmethod
    def _extract_state(html: str) -> dict[str, Any] | None:
        m = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            # 截断兜底：逐级配对括号
            s = m.group(1)
            depth = 0
            for i, ch in enumerate(s):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(s[: i + 1])
                        except json.JSONDecodeError:
                            return None
            return None

    def _map(self, raw: dict[str, Any], keyword: str, city: str) -> dict[str, Any] | None:
        title = normalize.clean(raw.get("name") or raw.get("jobName"))
        company = normalize.clean(raw.get("companyName"))
        if not title or not company:
            return None
        job = empty_job()
        job["id"] = str(raw.get("number") or raw.get("jobId") or raw.get("uuid") or title)
        job["title"] = title
        job["company"] = company
        job["salary_text"] = normalize.clean(raw.get("salary60") or self._card_salary(raw))
        lo, hi, months = normalize.parse_salary(job["salary_text"], raw.get("salaryReal"))
        job.update({"salary_low": lo, "salary_high": hi, "months": months,
                    "salary_mid": normalize.salary_mid(lo, hi)})
        job["city"] = normalize.clean(raw.get("workCity")) or city
        job["district"] = normalize.clean(raw.get("cityDistrict"))
        job["exp_text"] = normalize.clean(raw.get("workingExp"))
        job["exp_low"], job["exp_high"] = normalize.parse_experience(job["exp_text"])
        job["edu"] = normalize.parse_edu(raw.get("education"))
        job["industry"] = normalize.clean(raw.get("industryName"))
        job["company_size"] = normalize.clean(raw.get("companySize"))
        job["company_type"] = normalize.clean(raw.get("propertyName") or raw.get("property"))
        job["url"] = raw.get("positionURL") or raw.get("positionUrl") or ""
        job["publish_date"] = normalize.parse_date(raw.get("publishTime"))
        job["source"] = self.name
        job["jd"] = normalize.clean(raw.get("jobDescription"))

        tags: list[str] = []
        for t in (raw.get("jobSkillTags") or []):
            if isinstance(t, dict):
                tags.append(str(t.get("name") or ""))
        for t in (raw.get("skillLabel") or []):
            if isinstance(t, dict):
                tags.append(str(t.get("value") or ""))
        tags = [t for t in tags if t]
        job["tags"] = tags[:15]
        job["skills"] = skillmod.extract_skills(job["jd"], tags)
        return job

    @staticmethod
    def _card_salary(raw: dict[str, Any]) -> str:
        cj = raw.get("cardCustomJson")
        if isinstance(cj, str):
            try:
                return json.loads(cj).get("salary60", "") or ""
            except Exception:
                return ""
        return ""

    # ---------------- 详情补全 ----------------

    def fetch_detail(self, job: dict[str, Any]) -> dict[str, Any]:
        """补全 JD 全文。列表页的 jobDescription 被截断，详情页 __INITIAL_STATE__ 里有完整正文。"""
        url = job.get("url") or ""
        if not url or not url.startswith("http"):
            return job
        try:
            _st, html = http_get(url, headers={"Referer": self.BASE})
        except Exception:
            return job
        state = self._extract_state(html) or {}
        pos = (((state.get("jobDetail") or {}).get("detailedPosition")) or {})
        desc = pos.get("description") or pos.get("jobDesc") or ""
        if not desc:
            m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){80,})"', html)
            if m:
                try:
                    desc = json.loads('"' + m.group(1) + '"')
                except json.JSONDecodeError:
                    desc = m.group(1)
        desc = normalize.clean(desc, 8000)
        if len(desc) > len(job.get("jd") or ""):
            job["jd"] = desc
        # 详情页还带有福利/标签，补齐技能
        extra = []
        for k in ("welfare", "jobSkillTags", "skillLabel", "positionTag"):
            v = pos.get(k)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        extra.append(str(it.get("name") or it.get("value") or it.get("tag") or ""))
                    else:
                        extra.append(str(it))
        tags = list(dict.fromkeys([t for t in (job.get("tags") or []) + extra if t]))
        job["tags"] = tags[:20]
        job["skills"] = skillmod.extract_skills(job.get("jd") or "", tags)
        return job
