"""页面 / JSON 导入解析：用户把招聘页另存为 HTML 或直接粘贴文本，本地解析成标准岗位记录。

覆盖：智联 __INITIAL_STATE__、通用 {"jobName":...} 数组、纯文本 JD 兜底。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .. import normalize, skills as skillmod
from .base import empty_job
from .zhaopin import ZhaopinAdapter


def detect_source(text: str) -> str:
    low = text.lower()
    if "zhaopin" in low or "__INITIAL_STATE__" in text:
        return "zhaopin"
    return "unknown"


def parse_payload(text: str, keyword_hint: str = "") -> tuple[list[dict[str, Any]], str]:
    """返回 (岗位列表, 来源标记)。"""
    text = text or ""
    jobs: list[dict[str, Any]] = []

    zp = ZhaopinAdapter()
    state = ZhaopinAdapter._extract_state(text)
    if state and isinstance(state.get("positionList"), list):
        for raw in state["positionList"]:
            j = zp._map(raw, keyword_hint, keyword_hint or "全国")
            if j:
                jobs.append(j)
        if jobs:
            return jobs, "zhaopin"

    # 纯文本 JD 兜底：按空行切分，取首行做标题
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if len(b.strip()) > 40]
    for b in blocks[:200]:
        lines = [x.strip() for x in b.splitlines() if x.strip()]
        if not lines:
            continue
        job = empty_job()
        job["title"] = normalize.clean(lines[0], 40)
        job["company"] = "（导入）"
        job["jd"] = normalize.clean(b, 6000)
        job["skills"] = skillmod.extract_skills(job["jd"])
        job["source"] = "import"
        job["city"] = keyword_hint or ""
        jobs.append(job)
    return jobs, "text"


def parse_json_file(text: str) -> tuple[list[dict[str, Any]], str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return parse_payload(text)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        # 已是标准记录格式
        if "salary_mid" in data[0] or "salary_low" in data[0]:
            out = []
            for d in data:
                job = empty_job()
                job.update({k: v for k, v in d.items() if k in job})
                if not job["skills"]:
                    job["skills"] = skillmod.extract_skills(job.get("jd", ""), job.get("tags"))
                out.append(job)
            return out, "json"
    return parse_payload(text)
