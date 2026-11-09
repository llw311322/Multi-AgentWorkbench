"""服务层：只读查询 + 周期聚合 + 上游缓存。

对外只暴露纯 Python 结构，HTTP 层只负责序列化。
"""

import time
from datetime import date, datetime

from ..config import INDEX_MAP, INDICES, MINUTE_SLOTS, SECID_MAP
from ..collector.db import connect
from ..collector.sources import eastmoney
from .cache import cached as _cached


def market_state(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "休市"
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 15:
        return "盘前"
    if hm < 9 * 60 + 30:
        return "集合竞价"
    if hm <= 11 * 60 + 30:
        return "盘中"
    if hm < 13 * 60:
        return "午间休市"
    if hm <= 15 * 60:
        return "盘中"
    return "已收盘"


# ---------------------------------------------------------------- 周期聚合

def _period_key(day, period):
    if period == "week":
        iso = date.fromisoformat(day).isocalendar()
        return f"{iso[0]}-{iso[1]:02d}"
    if period == "month":
        return day[:7]
    if period == "year":
        return day[:4]
    return day


def _period_label(day, period):
    if period == "week":
        return _period_key(day, period)
    if period == "month":
        return day[:7]
    if period == "year":
        return day[:4]
    return day


def _aggregate(bars, period):
    """把日线按周/月/年聚合。开=首日开，收=末日收，高低=区间极值，量额求和。"""
    out = []
    for bar in bars:
        key = _period_key(bar["d"], period)
        if out and out[-1]["_k"] == key:
            cur = out[-1]
            if bar["h"] > cur["h"]:
                cur["h"] = bar["h"]
            if bar["l"] < cur["l"]:
                cur["l"] = bar["l"]
            cur["c"] = bar["c"]
            cur["v"] += bar["v"]
            cur["a"] += bar["a"]
        else:
            out.append(
                {
                    "_k": key,
                    "d": _period_label(bar["d"], period),
                    "o": bar["o"],
                    "h": bar["h"],
                    "l": bar["l"],
                    "c": bar["c"],
                    "v": bar["v"],
                    "a": bar["a"],
                }
            )
    prev_close = None
    for item in out:
        item["p"] = round((item["c"] / prev_close - 1) * 100, 2) if prev_close else 0.0
        prev_close = item["c"]
        item.pop("_k")
    return out


# ---------------------------------------------------------------- 查询接口

def get_kline(code, period="day", limit=800):
    if code not in INDEX_MAP:
        raise KeyError(f"未知指数代码: {code}")
    conn = connect()
    rows = conn.execute(
        """SELECT trade_date, open, high, low, close, volume, amount, pct_chg
           FROM index_daily WHERE code=? ORDER BY trade_date""",
        (code,),
    ).fetchall()
    conn.close()

    bars = [
        {
            "d": r["trade_date"],
            "o": round(r["open"], 2),
            "h": round(r["high"], 2),
            "l": round(r["low"], 2),
            "c": round(r["close"], 2),
            "v": int(r["volume"] or 0),
            "a": round(r["amount"] or 0, 2),
            "p": round(r["pct_chg"] or 0, 2),
        }
        for r in rows
    ]
    if period in ("week", "month", "year"):
        bars = _aggregate(bars, period)
    bars = bars[-max(1, min(limit, 10000)):]

    meta = INDEX_MAP[code]
    return {
        "code": code,
        "name": meta["name"],
        "period": period,
        "bars": bars,
        "total": len(bars),
        "asOf": bars[-1]["d"] if bars else None,
    }


def _pre_close(code, trade_date):
    conn = connect()
    row = conn.execute(
        """SELECT trade_date, close FROM index_daily
           WHERE code=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1""",
        (code, trade_date),
    ).fetchone()
    conn.close()
    return (row["close"], row["trade_date"]) if row else (None, None)


def _load_minute_from_db(code, trade_date):
    conn = connect()
    rows = conn.execute(
        """SELECT minute, price, volume, amount FROM index_minute
           WHERE code=? AND trade_date=? ORDER BY minute""",
        (code, trade_date),
    ).fetchall()
    last = conn.execute(
        "SELECT MAX(trade_date) AS d FROM index_minute WHERE code=?", (code,)
    ).fetchone()
    conn.close()
    return rows, (last["d"] if last else None)


def get_intraday(code):
    """返回某一交易日的分时序列，按固定 242 槽位对齐（未走的时段为 None）。"""
    if code not in INDEX_MAP:
        raise KeyError(f"未知指数代码: {code}")
    meta = INDEX_MAP[code]
    today = datetime.now().strftime("%Y-%m-%d")

    live, pre_close, source = None, None, "db"
    # 盘中优先取实时（带 20s 缓存），失败则回落到库
    if market_state() in ("盘中", "午间休市", "集合竞价"):
        try:
            live = _cached(
                f"trends:{code}",
                20,
                lambda: eastmoney.fetch_intraday(meta["secid"], ndays=1),
            )
            source = "eastmoney"
        except Exception:  # noqa: BLE001
            live = None

    if live and live["rows"]:
        raw = live["rows"]
        trade_date = raw[-1]["date"]
        pre_close = live.get("preClose") or None
    else:
        rows, latest = _load_minute_from_db(code, today)
        if not rows and latest:
            rows, _ = _load_minute_from_db(code, latest)
        if not rows:
            return {
                "code": code,
                "name": meta["name"],
                "date": today,
                "preClose": None,
                "points": [],
                "slots": MINUTE_SLOTS,
                "source": "db",
            }
        trade_date = rows[0]["minute"].split(" ")[0]
        raw = [
            {
                "minute": r["minute"],
                "date": trade_date,
                "price": r["price"],
                "volume": r["volume"],
                "amount": r["amount"],
            }
            for r in rows
        ]

    if not pre_close:
        pre_close, _ = _pre_close(code, trade_date)

    by_slot = {}
    for r in raw:
        by_slot[r["minute"].split(" ")[1]] = r

    points = []
    cum_pv, cum_v = 0.0, 0.0
    for slot in MINUTE_SLOTS:
        r = by_slot.get(slot)
        if r is None:
            points.append({"t": slot, "p": None, "v": None, "a": None, "avg": None})
            continue
        vol = r["volume"] or 0
        cum_pv += (r["price"] or 0) * vol
        cum_v += vol
        points.append(
            {
                "t": slot,
                "p": round(r["price"], 2),
                "v": int(vol),
                "a": round(r["amount"] or 0, 2),
                "avg": round(cum_pv / cum_v, 2) if cum_v else round(r["price"], 2),
            }
        )

    return {
        "code": code,
        "name": meta["name"],
        "date": trade_date,
        "preClose": round(pre_close, 2) if pre_close else None,
        "points": points,
        "slots": MINUTE_SLOTS,
        "source": source,
        "marketState": market_state(),
    }


def get_snapshot():
    def _load():
        return eastmoney.fetch_snapshot([i["secid"] for i in INDICES])

    try:
        raw = _cached("snapshot", 5, _load)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for secid, val in raw.items():
        code = SECID_MAP.get(secid)
        if code:
            out[code] = val
    return out


def get_indices():
    conn = connect()
    result = []
    for item in INDICES:
        last = conn.execute(
            """SELECT trade_date, close, pct_chg, amount FROM index_daily
               WHERE code=? ORDER BY trade_date DESC LIMIT 1""",
            (item["code"],),
        ).fetchone()
        tail = conn.execute(
            """SELECT close FROM index_daily WHERE code=?
               ORDER BY trade_date DESC LIMIT 30""",
            (item["code"],),
        ).fetchall()
        result.append(
            {
                "code": item["code"],
                "name": item["name"],
                "date": last["trade_date"] if last else None,
                "close": round(last["close"], 2) if last else None,
                "pct": round(last["pct_chg"] or 0, 2) if last else None,
                "amount": last["amount"] if last else None,
                "spark": [r["close"] for r in reversed(tail)],
            }
        )
    conn.close()

    snap = get_snapshot()
    for row in result:
        s = snap.get(row["code"])
        if s and s.get("price"):
            row["close"] = round(s["price"], 2)
            row["pct"] = round(s["pct"], 2)
            row["amount"] = s.get("amount")
            row["live"] = True
        else:
            row["live"] = False

    return {
        "indices": result,
        "marketState": market_state(),
        "serverTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "eastmoney" if snap else "db",
    }


# ---------------------------------------------------------------- 复盘摘要

def _pct(a, b):
    return round((a / b - 1) * 100, 2) if b else None


def get_brief(lookback=60):
    """结构化大盘摘要：给 Agent 消费的数据契约，全部字段由本地库实时计算。"""
    conn = connect()
    rows_out = []
    for item in INDICES:
        rows = conn.execute(
            """SELECT trade_date, open, high, low, close, volume, amount, pct_chg
               FROM index_daily WHERE code=? ORDER BY trade_date DESC LIMIT ?""",
            (item["code"], max(lookback, 61)),
        ).fetchall()
        rows = list(reversed(rows))
        if len(rows) < 2:
            continue

        closes = [r["close"] for r in rows]
        last, prev = rows[-1], rows[-2]
        win = closes[-60:]
        hi, lo = max(win), min(win)
        ma5 = sum(closes[-5:]) / min(5, len(closes))
        ma20 = sum(closes[-20:]) / min(20, len(closes))
        ma60 = sum(closes[-60:]) / min(60, len(closes))
        prev_amts = [r["amount"] for r in rows[-6:-1]]
        avg_amt = sum(prev_amts) / len(prev_amts) if prev_amts else None

        rows_out.append(
            {
                "code": item["code"],
                "name": item["name"],
                "date": last["trade_date"],
                "close": round(last["close"], 2),
                "pct": round(last["pct_chg"] or 0, 2),
                "amplitude": round((last["high"] - last["low"]) / prev["close"] * 100, 2),
                "amount": round(last["amount"], 0),
                "ma5": round(ma5, 2),
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
                "aboveMa20": last["close"] > ma20,
                "aboveMa60": last["close"] > ma60,
                "ret5": _pct(last["close"], closes[-6]) if len(closes) > 6 else None,
                "ret20": _pct(last["close"], closes[-21]) if len(closes) > 21 else None,
                "posIn60d": round((last["close"] - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0,
                "volRatio": round(last["amount"] / avg_amt, 2) if avg_amt else None,
            }
        )
    conn.close()

    if not rows_out:
        return {"asOf": None, "indices": [], "summary": "本地库暂无数据，请先执行日线采集。"}

    ups = [r for r in rows_out if r["pct"] > 0]
    downs = [r for r in rows_out if r["pct"] < 0]
    strong = max(rows_out, key=lambda r: r["pct"])
    weak = min(rows_out, key=lambda r: r["pct"])
    above = [r for r in rows_out if r["aboveMa20"]]
    amt_total = sum(r["amount"] for r in rows_out)
    vol_ratios = [r["volRatio"] for r in rows_out if r["volRatio"]]

    as_of = rows_out[0]["date"]
    summary = (
        f"{as_of}，{len(rows_out)} 个大盘指数中 {len(ups)} 涨 {len(downs)} 跌，"
        f"{len(above)} 个收于 20 日均线上方；"
        f"领涨 {strong['name']} {strong['pct']:+.2f}%，领跌 {weak['name']} {weak['pct']:+.2f}%；"
        f"合计成交额 {amt_total / 1e8:.0f} 亿元"
    )
    if vol_ratios:
        summary += f"，量能平均为近 5 日均值的 {sum(vol_ratios) / len(vol_ratios):.2f} 倍。"
    else:
        summary += "。"

    return {
        "asOf": as_of,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "marketState": market_state(),
        "indices": rows_out,
        "summary": summary,
        "note": "数据由本地采集服务基于东方财富公开行情计算，仅供数据参考，不构成投资建议。",
    }
