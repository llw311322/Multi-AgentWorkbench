"""一键启动：python run.py [--port 8080] [--auto-sync]

--auto-sync  同时开启盘后自动采集调度（推荐）
"""

import argparse

from market_board.collector import db
from market_board.server.http_app import main


def build_daemon(enabled):
    if not enabled:
        return None
    from market_board.collector.daemon import CollectorDaemon

    daemon = CollectorDaemon()
    daemon.start()
    return daemon


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动大盘可视化服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--auto-sync", action="store_true", help="开启盘后自动采集（盘中 5 分钟 / 11:32 午盘复盘 / 15:05 / 15:35）"
    )
    args = parser.parse_args()

    # 必须在起线程前一次性切到 WAL，之后所有连接只读不再抢锁
    db.enable_wal()
    main(args.host, args.port, build_daemon(args.auto_sync))
