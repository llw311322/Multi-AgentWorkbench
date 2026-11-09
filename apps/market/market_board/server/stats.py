"""全市场广度统计：涨跌分布、涨跌停、换手率、分市场结构。

为什么要单独一层：
  · 指数没有「换手率」概念 —— 换手率 = 成交额 / 流通市值，只有个股才有；
  · 涨跌家数、涨停跌停也必须在个股层面统计。
  所以活跃度在指数层面用「量比 / 量能倍数」表达，在市场层面用「换手率 / 涨跌比」表达，
  两套口径互补，不能混用。

数据来源：东财全市场列表接口，约 5900 只，分页并发 ≈ 6-9 秒，带 TTL 缓存。
"""

from datetime import datetime

from ..collector.sources import eastmoney
from .cache import cached

BOARD_ORDER = ["沪主板", "深主板", "创业板", "科创板", "北交所"]

# 涨跌幅分布分档，用于前端画直方图
BUCKETS = ["≤-7%", "-7~-5%", "-5~-3%", "-3~-1%", "-1~0%", "平盘", "0~1%", "1~3%", "3~5%", "5~7%", "≥7%"]


def board_of(code):
    """按代码段归属板块。北交所 8xxxxx / 4xxxxx / 920xxx。"""
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("8", "4", "920")):
        return "北交所"
    if code.startswith(("600", "601", "603", "605")):
        return "沪主板"
    if code.startswith(("000", "001", "002", "003")):
        return "深主板"
    return "其他"


def limit_pct(code, name):
    """涨跌幅限制：主板 10%（ST 5%），创业板/科创板 20%，北交所 30%。"""
    board = board_of(code)
    if board == "北交所":
        return 30.0
    if board in ("创业板", "科创板"):
        return 20.0
    return 5.0 if "ST" in (name or "").upper() else 10.0


def _bucket_index(pct):
    if pct <= -7:
        return 0
    if pct <= -5:
        return 1
    if pct <= -3:
        return 2
    if pct <= -1:
        return 3
    if pct < 0:
        return 4
    if pct == 0:
        return 5
    if pct <= 1:
        return 6
    if pct <= 3:
        return 7
    if pct <= 5:
        return 8
    if pct <= 7:
        return 9
    return 10


def _is_trading_hours(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 15 <= hm <= 11 * 60 + 31) or (13 * 60 <= hm <= 15 * 60 + 2)


def _aggregate(stocks):
    up = down = flat = halted = 0
    limit_up = limit_down = up5 = down5 = 0
    amount = float_cap = weighted_turnover = 0.0
    bucket_counts = [0] * len(BUCKETS)
    boards = {
        b: {
            "name": b,
            "count": 0,
            "up": 0,
            "down": 0,
            "flat": 0,
            "amount": 0.0,
            "floatCap": 0.0,
            "weightedPct": 0.0,
        }
        for b in BOARD_ORDER
    }

    for s in stocks:
        pct = s["pct"]
        name = s["name"]
        board = boards.get(board_of(s["code"]))
        if pct is None:  # 停牌
            halted += 1
            continue

        amt = s["amount"]
        cap = s["floatCap"]
        amount += amt
        float_cap += cap
        weighted_turnover += s["turnover"] * cap

        if board is not None:
            board["count"] += 1
            board["amount"] += amt
            board["floatCap"] += cap
            board["weightedPct"] += pct * cap
            if pct > 0:
                board["up"] += 1
            elif pct < 0:
                board["down"] += 1
            else:
                board["flat"] += 1

        bucket_counts[_bucket_index(pct)] += 1

        limit = limit_pct(s["code"], name)
        if limit - 0.15 <= pct <= limit + 0.6:  # 上限放宽，排除新股首日无涨跌幅限制的异常值
            limit_up += 1
        if -(limit + 0.6) <= pct <= -(limit - 0.15):
            limit_down += 1
        if pct >= 5:
            up5 += 1
        if pct <= -5:
            down5 += 1

        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1

    traded = up + down + flat
    board_rows = []
    for b in BOARD_ORDER:
        item = boards[b]
        cap = item["floatCap"]
        board_rows.append(
            {
                "name": item["name"],
                "count": item["count"],
                "up": item["up"],
                "down": item["down"],
                "flat": item["flat"],
                "amount": round(item["amount"], 0),
                "turnover": round(item["amount"] / cap * 100, 2) if cap else None,
                "pct": round(item["weightedPct"] / cap, 2) if cap else None,
            }
        )

    return {
        "available": True,
        "asOf": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": traded + halted,
        "traded": traded,
        "halted": halted,
        "up": up,
        "down": down,
        "flat": flat,
        "limitUp": limit_up,
        "limitDown": limit_down,
        "up5": up5,
        "down5": down5,
        "upRatio": round(up / traded * 100, 1) if traded else None,
        "adRatio": round(up / down, 2) if down else None,
        "amount": round(amount, 0),
        "floatCap": round(float_cap, 0),
        "turnover": round(amount / float_cap * 100, 2) if float_cap else None,
        "avgTurnover": round(weighted_turnover / float_cap, 2) if float_cap else None,
        "distribution": [
            {"label": BUCKETS[i], "count": bucket_counts[i]} for i in range(len(BUCKETS))
        ],
        "boards": board_rows,
    }


def market_breadth(force=False):
    """全市场广度（带缓存：盘中 60s，非盘中 900s）。失败时返回 available=False。"""
    ttl = 60 if _is_trading_hours() else 900
    key = "breadth:all"

    def _load():
        return _aggregate(eastmoney.fetch_all_stocks())

    try:
        if force:
            from .cache import invalidate

            invalidate(key)
        return cached(key, ttl, _load)
    except Exception as exc:  # noqa: BLE001 上游抖动不能让复盘整体失败
        return {
            "available": False,
            "error": str(exc),
            "asOf": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
