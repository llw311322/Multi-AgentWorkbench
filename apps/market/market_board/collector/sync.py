"""采集任务 CLI。

用法（在工作区根目录执行）：
    python -m market_board.collector.sync full        # 首次全量拉日线（1990 至今）
    python -m market_board.collector.sync daily       # 日线增量补齐
    python -m market_board.collector.sync minute      # 当日 1 分钟序列落库
    python -m market_board.collector.sync all         # daily + minute
    python -m market_board.collector.sync status      # 查看入库情况
"""

import argparse
import time
from datetime import datetime

from ..config import INDICES, INDEX_MAP
from . import db
from .sources import eastmoney


def _log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def sync_daily(full=False, codes=None):
    conn = db.connect()
    db.init_db(conn)
    total = 0
    for item in INDICES:
        if codes and item["code"] not in codes:
            continue
        started = db.now_str()
        name, code = item["name"], item["code"]
        try:
            beg = "0" if full else (db.last_trade_date(conn, code) or "0")
            result = eastmoney.fetch_daily(item["secid"], beg=beg)
            rows = result["rows"]
            n = db.upsert_daily(conn, code, rows)
            total += n
            db.log_sync(conn, "daily-full" if full else "daily-inc", code, started, n, True)
            span = f"{rows[0]['date']} ~ {rows[-1]['date']}" if rows else "空"
            _log(f"日线 {name:<6} 写入 {n:>5} 行  ({span})")
        except Exception as exc:  # noqa: BLE001
            db.log_sync(conn, "daily", code, started, 0, False, str(exc))
            _log(f"日线 {name:<6} 失败: {exc}")
        time.sleep(0.4)
    db.rebuild_calendar(conn)
    conn.close()
    _log(f"日线同步完成，共处理 {total} 行")


def sync_minute(ndays=1, codes=None):
    conn = db.connect()
    db.init_db(conn)
    total = 0
    for item in INDICES:
        if codes and item["code"] not in codes:
            continue
        started = db.now_str()
        name, code = item["name"], item["code"]
        try:
            result = eastmoney.fetch_intraday(item["secid"], ndays=ndays)
            n = db.upsert_minute(conn, code, result["rows"])
            total += n
            db.log_sync(conn, "minute", code, started, n, True)
            dates = sorted({r["date"] for r in result["rows"]})
            _log(f"分时 {name:<6} 写入 {n:>5} 点  ({', '.join(dates)})")
        except Exception as exc:  # noqa: BLE001
            db.log_sync(conn, "minute", code, started, 0, False, str(exc))
            _log(f"分时 {name:<6} 失败: {exc}")
        time.sleep(0.4)
    conn.close()
    _log(f"分时同步完成，共 {total} 点")


def show_status():
    conn = db.connect()
    db.init_db(conn)
    print(f"{'代码':<12}{'名称':<10}{'日线起':<12}{'日线止':<12}{'日线条数':>8}{'分时最后':>12}")
    for item in INDICES:
        row = conn.execute(
            """SELECT MIN(trade_date) AS a, MAX(trade_date) AS b, COUNT(*) AS n
               FROM index_daily WHERE code=?""",
            (item["code"],),
        ).fetchone()
        m = conn.execute(
            "SELECT MAX(trade_date) AS d, COUNT(*) AS n FROM index_minute WHERE code=?",
            (item["code"],),
        ).fetchone()
        print(
            f"{item['code']:<12}{item['name']:<10}{str(row['a'] or '-'):<12}"
            f"{str(row['b'] or '-'):<12}{row['n']:>8}{str(m['d'] or '-'):>12}"
        )
    cal = conn.execute("SELECT COUNT(*) AS n FROM trade_calendar").fetchone()["n"]
    print(f"\n交易日历：{cal} 个交易日")
    print(f"数据库：{db.DB_PATH}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="大盘行情采集任务")
    parser.add_argument(
        "cmd", choices=["full", "daily", "minute", "all", "status"], help="要执行的任务"
    )
    parser.add_argument("--ndays", type=int, default=1, help="分时回溯天数（1-5）")
    parser.add_argument("--codes", default="", help="只处理指定代码，逗号分隔")
    args = parser.parse_args()
    db.enable_wal()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    for c in codes:
        if c not in INDEX_MAP:
            parser.error(f"未知指数代码: {c}")

    if args.cmd == "full":
        sync_daily(full=True, codes=codes)
    elif args.cmd == "daily":
        sync_daily(full=False, codes=codes)
    elif args.cmd == "minute":
        sync_minute(ndays=args.ndays, codes=codes)
    elif args.cmd == "all":
        sync_daily(full=False, codes=codes)
        sync_minute(ndays=args.ndays, codes=codes)
    else:
        show_status()


if __name__ == "__main__":
    main()
