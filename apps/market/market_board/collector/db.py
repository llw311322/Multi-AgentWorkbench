"""SQLite 存储层：唯一写入口在这里定义 schema 与幂等 upsert。"""

import sqlite3
from datetime import datetime

from ..config import DATA_DIR, DB_PATH, INDICES

SCHEMA = """
CREATE TABLE IF NOT EXISTS index_basic (
    code     TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    market   TEXT,
    secid    TEXT,
    sort_no  INTEGER
);

CREATE TABLE IF NOT EXISTS index_daily (
    code       TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     INTEGER,
    amount     REAL,
    pct_chg    REAL,
    updated_at TEXT,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_code_date ON index_daily(code, trade_date);

CREATE TABLE IF NOT EXISTS index_minute (
    code      TEXT NOT NULL,
    minute    TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    price     REAL,
    volume    INTEGER,
    amount    REAL,
    updated_at TEXT,
    PRIMARY KEY (code, minute)
);
CREATE INDEX IF NOT EXISTS idx_minute_code_date ON index_minute(code, trade_date);

CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date TEXT PRIMARY KEY,
    is_open    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task        TEXT,
    code        TEXT,
    started_at  TEXT,
    finished_at TEXT,
    rows        INTEGER,
    ok          INTEGER,
    msg         TEXT
);

-- 全市场广度存档：随交易日积累后可算换手率/涨跌比的历史分位
CREATE TABLE IF NOT EXISTS market_breadth (
    trade_date TEXT NOT NULL,
    session    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (trade_date, session)
);

-- 复盘存档：午盘 / 收盘各一份，Agent 与前端都从这里取
CREATE TABLE IF NOT EXISTS market_review (
    trade_date TEXT NOT NULL,
    session    TEXT NOT NULL,
    headline   TEXT,
    summary    TEXT,
    payload    TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY (trade_date, session)
);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_wal_ready = False


def enable_wal():
    """在进程启动、线程开始前调用一次。

    journal_mode 是库级持久设置，切换时需要排他锁。若放在每次 connect/init_db 里执行，
    会与采集任务的并发连接互相等待，只读接口直接被堵死（实测表现为接口 20s 超时）。
    """
    global _wal_ready
    if _wal_ready:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
    finally:
        conn.close()
    _wal_ready = True


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    for item in INDICES:
        conn.execute(
            """INSERT INTO index_basic(code, name, market, secid, sort_no) VALUES(?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, market=excluded.market,
                 secid=excluded.secid, sort_no=excluded.sort_no""",
            (item["code"], item["name"], item["market"], item["secid"], item["sort_no"]),
        )
    conn.commit()


def upsert_daily(conn, code, rows):
    """rows: [{date, open, high, low, close, volume, amount, pct_chg}]"""
    if not rows:
        return 0
    ts = now_str()
    sql = """INSERT INTO index_daily
             (code, trade_date, open, high, low, close, volume, amount, pct_chg, updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(code, trade_date) DO UPDATE SET
               open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
               volume=excluded.volume, amount=excluded.amount, pct_chg=excluded.pct_chg,
               updated_at=excluded.updated_at"""
    payload = [
        (
            code,
            r["date"],
            r["open"],
            r["high"],
            r["low"],
            r["close"],
            r["volume"],
            r["amount"],
            r["pct_chg"],
            ts,
        )
        for r in rows
    ]
    conn.executemany(sql, payload)
    conn.commit()
    return len(payload)


def upsert_minute(conn, code, rows):
    """rows: [{minute:'2026-11-05 09:30', date:'2026-11-05', price, volume, amount}]"""
    if not rows:
        return 0
    ts = now_str()
    sql = """INSERT INTO index_minute
             (code, minute, trade_date, price, volume, amount, updated_at)
             VALUES (?,?,?,?,?,?,?)
             ON CONFLICT(code, minute) DO UPDATE SET
               price=excluded.price, volume=excluded.volume,
               amount=excluded.amount, updated_at=excluded.updated_at"""
    payload = [
        (code, r["minute"], r["date"], r["price"], r["volume"], r["amount"], ts) for r in rows
    ]
    conn.executemany(sql, payload)
    conn.commit()
    return len(payload)


def rebuild_calendar(conn):
    """交易日历直接由日线日期并集推导，避免额外数据源。"""
    conn.execute("DELETE FROM trade_calendar")
    conn.execute(
        """INSERT INTO trade_calendar(trade_date, is_open)
           SELECT DISTINCT trade_date, 1 FROM index_daily ORDER BY trade_date"""
    )
    conn.commit()


def log_sync(conn, task, code, started, rows, ok, msg=""):
    conn.execute(
        """INSERT INTO sync_log(task, code, started_at, finished_at, rows, ok, msg)
           VALUES (?,?,?,?,?,?,?)""",
        (task, code, started, now_str(), rows, 1 if ok else 0, msg[:500]),
    )
    conn.commit()


def last_trade_date(conn, code):
    row = conn.execute(
        "SELECT MAX(trade_date) AS d FROM index_daily WHERE code=?", (code,)
    ).fetchone()
    return row["d"] if row and row["d"] else None


# ---------------------------------------------------------------- 复盘存档

def save_breadth(conn, trade_date, session, payload):
    import json

    conn.execute(
        """INSERT INTO market_breadth(trade_date, session, payload, created_at)
           VALUES (?,?,?,?)
           ON CONFLICT(trade_date, session) DO UPDATE SET
             payload=excluded.payload, created_at=excluded.created_at""",
        (trade_date, session, json.dumps(payload, ensure_ascii=False), now_str()),
    )
    conn.commit()


def load_breadth(conn, trade_date, session):
    import json

    row = conn.execute(
        "SELECT payload FROM market_breadth WHERE trade_date=? AND session=?",
        (trade_date, session),
    ).fetchone()
    return json.loads(row["payload"]) if row else None


def save_review(conn, trade_date, session, headline, summary, payload):
    import json

    conn.execute(
        """INSERT INTO market_review(trade_date, session, headline, summary, payload, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(trade_date, session) DO UPDATE SET
             headline=excluded.headline, summary=excluded.summary,
             payload=excluded.payload, created_at=excluded.created_at""",
        (
            trade_date,
            session,
            headline,
            summary,
            json.dumps(payload, ensure_ascii=False),
            now_str(),
        ),
    )
    conn.commit()


def load_review(conn, trade_date, session):
    import json

    row = conn.execute(
        "SELECT headline, summary, payload, created_at FROM market_review"
        " WHERE trade_date=? AND session=?",
        (trade_date, session),
    ).fetchone()
    if not row:
        return None
    data = json.loads(row["payload"])
    data["createdAt"] = row["created_at"]
    return data


def recent_reviews(conn, limit=20):
    rows = conn.execute(
        """SELECT trade_date, session, headline, created_at FROM market_review
           ORDER BY trade_date DESC, session ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
