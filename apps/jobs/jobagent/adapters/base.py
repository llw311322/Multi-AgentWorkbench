"""采集适配器基类与公共记录结构。"""
from __future__ import annotations

import random
import time
import urllib.request
import gzip
import io
import ssl
import zlib
from typing import Any

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


class SourceError(Exception):
    """采集失败，带可展示给用户的提示。"""

    def __init__(self, source: str, reason: str, hint: str = ""):
        self.source = source
        self.reason = reason
        self.hint = hint
        super().__init__(f"[{source}] {reason}")


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 18,
             cookies: str = "") -> tuple[int, str]:
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if cookies:
        h["Cookie"] = cookies
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        return r.status, raw.decode("utf-8", "ignore")


def sleep_human(lo: float = 0.6, hi: float = 1.4) -> None:
    time.sleep(random.uniform(lo, hi))


def empty_job() -> dict[str, Any]:
    return {
        "id": "", "title": "", "company": "", "salary_text": "",
        "salary_low": None, "salary_high": None, "salary_mid": None, "months": 12,
        "city": "", "district": "", "exp_text": "", "exp_low": 0.0, "exp_high": 99.0,
        "edu": "不限", "industry": "", "company_size": "", "company_type": "",
        "skills": [], "jd": "", "url": "", "publish_date": "",
        "source": "", "tags": [],
    }


class BaseAdapter:
    name = "base"
    label = "基础"
    requires_cookie = False

    def __init__(self, cookies: str = "", **opts: Any):
        self.cookies = (cookies or "").strip()
        self.opts = opts
        # 最近一次 search 的补充说明（如「城市码不支持，已回退全国」），由 Agent 收进采集提示
        self.last_note = ""

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "ready": True,
                "requires_cookie": self.requires_cookie, "has_cookie": bool(self.cookies)}

    def search(self, keyword: str, city: str, pages: int = 1, limit: int = 40,
               **filters: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_detail(self, job: dict[str, Any]) -> dict[str, Any]:
        return job
