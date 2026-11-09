"""东方财富行情源（非官方接口，需 UA/Referer，失败重试 + 限速）。

只做「取原始数据 + 解析」，不碰数据库。
"""

import json
import time
import urllib.parse
import urllib.request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# 全 A 股范围：沪深主板 + 创业板 + 科创板 + 北交所（约 5900 只）
MARKET_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
STOCK_FIELDS = "f2,f3,f5,f6,f8,f12,f14,f21"

# 周期 -> 东财 klt 参数（本项目周期全部由日线聚合，这里仅备用）
KLT = {"day": 101, "week": 102, "month": 103}


class SourceError(RuntimeError):
    pass


def _fetch(url, retries=3, timeout=15, gap=0.35):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 网络层任何异常都重试
            last_err = exc
            time.sleep(gap * (attempt + 1))
    raise SourceError(f"请求失败: {last_err}")


def _num(text, cast=float, default=0.0):
    try:
        if text in ("", "-", None):
            return cast(default)
        return cast(text)
    except (TypeError, ValueError):
        return cast(default)


def fetch_daily(secid, beg="0", end="20500101", limit=100000):
    """拉取日线全量/区间。返回 {name, rows:[...]}。"""
    query = urllib.parse.urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f59",
            "klt": 101,
            "fqt": 0,  # 指数无复权概念，固定不复权
            "beg": beg,
            "end": end,
            "lmt": limit,
        }
    )
    payload = json.loads(_fetch(f"{KLINE_URL}?{query}"))
    data = payload.get("data")
    if not data or not data.get("klines"):
        raise SourceError(f"日线无数据 secid={secid}")

    rows = []
    for line in data["klines"]:
        p = line.split(",")
        if len(p) < 8:
            continue
        # 字段顺序：日期,开,收,高,低,成交量(手),成交额(元),涨跌幅(%)
        rows.append(
            {
                "date": p[0],
                "open": _num(p[1]),
                "close": _num(p[2]),
                "high": _num(p[3]),
                "low": _num(p[4]),
                "volume": _num(p[5], int, 0),
                "amount": _num(p[6]),
                "pct_chg": _num(p[7]),
            }
        )
    return {"name": data.get("name"), "rows": rows}


def fetch_intraday(secid, ndays=1):
    """拉取最近 N 个交易日的 1 分钟序列。返回 {name, preClose, rows:[...]}。"""
    query = urllib.parse.urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "ndays": max(1, min(int(ndays), 5)),
            "iscr": 0,
        }
    )
    payload = json.loads(_fetch(f"{TRENDS_URL}?{query}"))
    data = payload.get("data")
    if not data or not data.get("trends"):
        raise SourceError(f"分时无数据 secid={secid}")

    rows = []
    for line in data["trends"]:
        p = line.split(",")
        if len(p) < 7:
            continue
        stamp = p[0]  # '2026-11-05 09:30'
        rows.append(
            {
                "minute": stamp,
                "date": stamp.split(" ")[0],
                "price": _num(p[2]),
                "volume": _num(p[5], int, 0),
                "amount": _num(p[6]),
            }
        )
    return {
        "name": data.get("name"),
        "preClose": _num(data.get("preClose")),
        "rows": rows,
    }


def fetch_snapshot(secids):
    """批量快照，一次请求拿多个指数（fltt=2 返回已换算的价格）。"""
    if not secids:
        return {}
    query = urllib.parse.urlencode(
        {
            "fltt": 2,
            "invt": 2,
            "secids": ",".join(secids),
            "fields": "f2,f3,f4,f6,f12,f13,f14",
        }
    )
    payload = json.loads(_fetch(f"{ULIST_URL}?{query}"))
    diff = (payload.get("data") or {}).get("diff") or []
    out = {}
    for item in diff:
        market = item.get("f13")
        code = item.get("f12")
        out[f"{market}.{code}"] = {
            "price": _num(item.get("f2")),
            "pct": _num(item.get("f3")),
            "chg": _num(item.get("f4")),
            "amount": _num(item.get("f6")),
            "name": item.get("f14"),
        }
    return out


def _clist_page(pn, pz=100, fs=MARKET_FS, fields=STOCK_FIELDS):
    """单页全市场列表。东财 pz 上限实测 100，超过会被静默截断。"""
    query = urllib.parse.urlencode(
        {
            "pn": pn,
            "pz": pz,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": fs,
            "fields": fields,
        }
    )
    payload = json.loads(_fetch(f"{CLIST_URL}?{query}", retries=2, timeout=20))
    data = payload.get("data") or {}
    return data.get("diff") or [], int(data.get("total") or 0)


def fetch_all_stocks(max_workers=6):
    """全市场个股快照（分页并发，约 60 页 / 6000 只，实测 6-9 秒）。

    返回 [{code, name, price, pct, volume, amount, turnover, floatCap}]。
    pct 为 None 表示停牌（东财返回 '-'），统计时单独计为停牌家数。
    """
    from concurrent.futures import ThreadPoolExecutor

    first, total = _clist_page(1)
    if not total:
        raise SourceError("全市场快照无数据")

    pages = (total + 99) // 100
    raw = list(first)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for diff, _ in pool.map(_clist_page, range(2, pages + 1)):
                raw.extend(diff)

    out = []
    for item in raw:
        code = str(item.get("f12") or "")
        if not code:
            continue
        pct = item.get("f3")
        out.append(
            {
                "code": code,
                "name": str(item.get("f14") or ""),
                "price": _num(item.get("f2")),
                "pct": float(pct) if isinstance(pct, (int, float)) else None,
                "volume": _num(item.get("f5"), int, 0),
                "amount": _num(item.get("f6")),
                "turnover": _num(item.get("f8")),
                "floatCap": _num(item.get("f21")),
            }
        )
    return out
