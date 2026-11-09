#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点抓取器 —— 科技/AI + 财经/市场
仅使用 Python 标准库，保证定时任务在干净环境下也能直接运行。

用法:
    python collect.py                    # 抓取最近 12 小时
    python collect.py --hours 24         # 抓取最近 24 小时
    python collect.py --hours 12 --out data/latest.json

输出: JSON（默认 data/latest.json），同时向 stdout 打印人类可读摘要。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import gzip
import html
import io
import difflib
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15
DEFAULT_HOURS = 12

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(url: str, timeout: int = TIMEOUT, retries: int = 2) -> bytes:
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except OSError:
                        pass
                return raw
        except Exception as e:                     # 网络抖动重试
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def http_text(url: str, timeout: int = TIMEOUT) -> str:
    raw = http_get(url, timeout)
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


# --------------------------------------------------------------------------
# 时间与文本工具
# --------------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_dt(value) -> datetime | None:
    """把各种时间字符串转成 aware datetime。"""
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, ValueError):
            return None
    s = str(value).strip()
    # RFC 2822 (RSS)
    try:
        dt = parsedate_to_datetime(s)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, IndexError):
        pass
    # ISO 8601
    s2 = s.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.fromisoformat(s2) if fmt is None else datetime.strptime(s2[:19], fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return None


def to_dt_cn(value) -> datetime | None:
    """解析不带时区的北京时间字符串（东财等国内接口）。"""
    dt = to_dt(value)
    if dt is None:
        return None
    s = str(value)
    has_tz = ("+" in s[10:]) or s.endswith("Z") or ("GMT" in s) or ("+0800" in s)
    if has_tz:
        return dt
    return dt.replace(tzinfo=timezone(timedelta(hours=8)))


def strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(t: str) -> str:
    t = strip_tags(t or "")
    t = re.sub(r"\s*[-|_—]\s*(IT之家|36氪|机器之心|TechCrunch|CNBC|量子位|InfoQ|少数派|开源中国|钛媒体|MarketWatch)\s*$", "", t)
    return t.strip()


def norm_key(t: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", (t or "").lower())


def within(dt: datetime | None, hours: int) -> bool:
    if dt is None:
        return True          # 无时间信息的条目保留，交由分析阶段判断
    return dt >= now_utc() - timedelta(hours=hours)


# --------------------------------------------------------------------------
# 通用 RSS / Atom 解析
# --------------------------------------------------------------------------
def parse_feed(text: str, limit: int = 25) -> list[dict]:
    out: list[dict] = []
    try:
        root = ET.fromstring(text.encode("utf-8", "ignore"))
    except ET.ParseError:
        return out

    def txt(node, *tags):
        for tag in tags:
            el = node.find(tag)
            if el is not None and (el.text or "").strip():
                return el.text.strip()
        return ""

    items = root.iter()
    for node in items:
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = clean_title(txt(node, "{*}title", "title"))
        if not title:
            continue
        link = ""
        for el in node:
            if el.tag.split("}")[-1] == "link":
                link = (el.get("href") or el.text or "").strip() or link
        summary = strip_tags(txt(node, "{*}description", "{*}summary", "description", "summary"))
        dt = to_dt(txt(node, "{*}pubDate", "{*}published", "{*}updated", "pubDate", "published"))
        out.append({
            "title": title,
            "url": link,
            "summary": summary[:600],
            "published_at": dt.isoformat() if dt else None,
        })
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# 各数据源
# --------------------------------------------------------------------------
def src_hackernews(hours: int) -> list[dict]:
    ids = json.loads(http_text("https://hacker-news.firebaseio.com/v0/topstories.json"))[:35]

    def one(i):
        try:
            d = json.loads(http_text(
                f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=10))
            if not d or d.get("type") != "story" or d.get("dead"):
                return None
            return {
                "title": clean_title(d.get("title", "")),
                "url": d.get("url") or f"https://news.ycombinator.com/item?id={i}",
                "discussion": f"https://news.ycombinator.com/item?id={i}",
                "summary": strip_tags(d.get("text", ""))[:400],
                "published_at": datetime.fromtimestamp(d.get("time", 0), tz=timezone.utc).isoformat(),
                "signals": {"points": d.get("score", 0), "comments": d.get("descendants", 0)},
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(one, ids))
    return [r for r in res if r and within(to_dt(r["published_at"]), hours)]


def src_github_trending(hours: int) -> list[dict]:
    try:
        return _github_from_html()
    except Exception:
        return _github_from_api()


def _github_from_api() -> list[dict]:
    """降级方案：近 7 天新建、按星数排序的仓库。"""
    since = (now_utc() - timedelta(days=7)).strftime("%Y-%m-%d")
    url = (f"https://api.github.com/search/repositories?q=created:%3E{since}"
           "&sort=stars&order=desc&per_page=10")
    data = json.loads(http_text(url, timeout=25))
    out = []
    for r in data.get("items", [])[:10]:
        out.append({
            "title": f"GitHub 新晋热门: {r['full_name']}",
            "url": r.get("html_url", ""),
            "summary": (r.get("description") or "")[:300] or f"{r['full_name']} 近 7 天新建高星项目",
            "published_at": r.get("created_at"),
            "signals": {"stars": r.get("stargazers_count"), "language": r.get("language")},
        })
    return out


def _github_from_html() -> list[dict]:
    page = http_text("https://github.com/trending?since=daily", timeout=35)
    out = []
    for block in re.findall(r'<article class="Box-row".*?</article>', page, flags=re.S)[:15]:
        m = re.search(r'<h2[^>]*>\s*<a href="/([^"]+)"', block, flags=re.S)
        if not m:
            continue
        repo = m.group(1).strip()
        title = repo.replace("/", " / ")
        dm = re.search(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', block, flags=re.S)
        desc = strip_tags(dm.group(1)) if dm else ""
        sm = re.search(r'([\d,]+)\s*stars? today', block)
        lm = re.search(r'<span itemprop="programmingLanguage">([^<]+)</span>', block)
        out.append({
            "title": f"GitHub Trending: {title}",
            "url": f"https://github.com/{repo}",
            "summary": desc or f"{repo} 今日登上 GitHub Trending",
            "published_at": now_utc().isoformat(),
            "signals": {
                "stars_today": int(sm.group(1).replace(",", "")) if sm else None,
                "language": lm.group(1).strip() if lm else None,
            },
        })
    return out


def src_arxiv(hours: int) -> list[dict]:
    cats = "cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.LG+OR+cat:cs.CV"
    url = ("https://export.arxiv.org/api/query?search_query=" + cats +
           "&sortBy=submittedDate&sortOrder=descending&max_results=20")
    text = http_text(url, timeout=20)
    out = []
    for it in parse_feed(text, limit=20):
        it["title"] = "[arXiv] " + it["title"]
        it["summary"] = re.sub(r"\s+", " ", it["summary"])[:700]
        out.append(it)
    return [i for i in out if within(to_dt(i["published_at"]), hours)]


def src_wallstreetcn(hours: int) -> list[dict]:
    raw = http_text("https://api-one.wallstcn.com/apiv1/content/lives"
                    "?channel=global-channel&client=pc&limit=50")
    data = json.loads(raw)
    out = []
    for it in (data.get("data", {}) or {}).get("items", []) or []:
        content = strip_tags(it.get("content_text") or it.get("title") or "")
        if not content:
            continue
        title = (strip_tags(it.get("title") or "") or content)[:80]
        ts = it.get("display_time") or it.get("created_at")
        out.append({
            "title": title,
            "url": it.get("uri") or "https://wallstreetcn.com/live/global",
            "summary": content[:600],
            "published_at": to_dt(ts).isoformat() if to_dt(ts) else None,
            "signals": {"importance": it.get("score")},
        })
    return [i for i in out if within(to_dt(i["published_at"]), hours)]


def src_eastmoney(hours: int) -> list[dict]:
    text = http_text("https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html")
    payload = text[text.index("{"):].rstrip().rstrip(";")
    data = json.loads(payload)
    out = []
    for it in data.get("LivesList", []) or []:
        title = clean_title(it.get("title") or it.get("simtitle") or "")
        digest = strip_tags(it.get("digest") or it.get("simdigest") or "")
        if not title and not digest:
            continue
        dt = to_dt_cn(it.get("showtime"))
        out.append({
            "title": (title or digest)[:90],
            "url": it.get("url_w") or it.get("url_unique") or "https://kuaixun.eastmoney.com/",
            "summary": digest[:600],
            "published_at": dt.isoformat() if dt else None,
            "signals": {"comments": int(it.get("commentnum") or 0)},
        })
    return [i for i in out if within(to_dt(i["published_at"]), hours)]


# (name, category, fetcher 或 (url, limit))
SOURCES = [
    ("Hacker News",      "tech",    src_hackernews),
    ("GitHub Trending",  "tech",    src_github_trending),
    ("arXiv",            "tech",    src_arxiv),
    ("IT之家",            "tech",    ("https://www.ithome.com/rss/", 25)),
    ("量子位",            "tech",    ("https://www.qbitai.com/feed", 20)),
    ("InfoQ 中文",        "tech",    ("https://www.infoq.cn/feed", 15)),
    ("开源中国",           "tech",    ("https://www.oschina.net/news/rss", 15)),
    ("少数派",            "tech",    ("https://sspai.com/feed", 12)),
    ("TechCrunch",       "tech",    ("https://techcrunch.com/feed/", 15)),
    ("钛媒体",            "tech",    ("https://www.tmtpost.com/rss", 15)),
    ("华尔街见闻",         "finance", src_wallstreetcn),
    ("东方财富快讯",       "finance", src_eastmoney),
    ("CNBC",             "finance", ("https://www.cnbc.com/id/100003114/device/rss/rss.html", 20)),
    ("MarketWatch",      "finance", ("https://feeds.content.dowjones.io/public/rss/mw_topstories", 15)),
]


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------
def dedupe(items: list[dict]) -> list[dict]:
    kept: list[dict] = []
    keys: list[str] = []
    for it in items:
        k = norm_key(it["title"])
        if len(k) < 4:
            continue
        dup = False
        for i, prev in enumerate(keys):
            if k == prev or (abs(len(k) - len(prev)) < 12 and
                             difflib.SequenceMatcher(None, k, prev).ratio() > 0.86):
                kept[i].setdefault("also_reported_by", []).append(it["source"])
                dup = True
                break
        if not dup:
            keys.append(k)
            kept.append(it)
    return kept


def run(hours: int) -> dict:
    started = time.time()
    results: list[dict] = []
    status: list[dict] = []

    def fetch_one(entry):
        name, cat, spec = entry
        try:
            if callable(spec):
                rows = spec(hours)
            else:
                url, limit = spec
                rows = [r for r in parse_feed(http_text(url), limit)
                        if within(to_dt(r["published_at"]), hours)]
            for r in rows:
                r.setdefault("summary", "")
                r.setdefault("signals", {})
                r["source"] = name
                r["category"] = cat
            return {"name": name, "ok": True, "count": len(rows), "rows": rows}
        except Exception as e:
            return {"name": name, "ok": False, "count": 0, "rows": [],
                    "error": f"{type(e).__name__}: {str(e)[:120]}"}

    with ThreadPoolExecutor(max_workers=10) as ex:
        for res in ex.map(fetch_one, SOURCES):
            status.append({k: res[k] for k in ("name", "ok", "count")}
                          | ({"error": res["error"]} if not res["ok"] else {}))
            results.extend(res["rows"])

    for i, it in enumerate(results):
        it["id"] = f"H{i + 1:03d}"

    results.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    merged = dedupe(results)

    return {
        "generated_at": now_utc().isoformat(),
        "generated_at_local": datetime.now().astimezone().isoformat(),
        "window_hours": hours,
        "total_raw": len(results),
        "total_after_dedupe": len(merged),
        "source_status": status,
        "items": merged,
        "elapsed_sec": round(time.time() - started, 1),
    }


def print_digest(payload: dict, max_per_cat: int = 200) -> None:
    """紧凑文本摘要，供分析阶段直接阅读（比整份 JSON 省很多 token）。"""
    print(f"# 热点摘要 {payload['generated_at_local'][:16]} | 窗口 {payload['window_hours']}h "
          f"| 原始 {payload['total_raw']} → 去重 {payload['total_after_dedupe']}")
    for cat, label in (("tech", "科技 / AI"), ("finance", "财经 / 市场")):
        rows = [i for i in payload["items"] if i["category"] == cat][:max_per_cat]
        print(f"\n## {label}（{len(rows)} 条）")
        for it in rows:
            sig = " ".join(f"{k}={v}" for k, v in (it.get("signals") or {}).items() if v)
            multi = "+".join(it.get("also_reported_by", []) or [])
            print(f"{it['id']} [{it['source']}{'|' + multi if multi else ''}] {it['title']}"
                  + (f"  <{sig}>" if sig else ""))
            s = (it.get("summary") or "").strip()
            if s and s[:30] not in it["title"]:
                print(f"    · {s[:180]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取科技/AI + 财经热点")
    ap.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="时间窗口（小时）")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "latest.json"), help="输出 JSON 路径")
    ap.add_argument("--digest", action="store_true", help="额外打印紧凑文本摘要")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    payload = run(args.hours)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    ok = sum(1 for s in payload["source_status"] if s["ok"])
    print(f"[collect] 窗口 {args.hours}h | 源 {ok}/{len(SOURCES)} 成功 | "
          f"原始 {payload['total_raw']} 条 → 去重后 {payload['total_after_dedupe']} 条 | "
          f"耗时 {payload['elapsed_sec']}s")
    for s in payload["source_status"]:
        flag = "OK " if s["ok"] else "ERR"
        extra = f"  <{s['error']}>" if s.get("error") else ""
        print(f"  [{flag}] {s['name']:<16} {s['count']:>3} 条{extra}")
    print(f"[collect] 已写入 {args.out}")
    if args.digest:
        print()
        print_digest(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
