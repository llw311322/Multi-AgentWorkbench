"""盘中自动采集调度（标准库实现，随服务进程一起跑）。

时间策略：
  09:30-11:31 / 13:00-15:01  每 5 分钟落一次 1 分钟序列
  >= 11:32                    午盘复盘：先补落分时，再生成 11:30 截断的复盘并存档，每天一次
  >= 15:05                    收盘落库（分时 + 日线），每天一次
  >= 15:06                    收盘复盘（15:00 截断），每天一次
  >= 15:35                    日线增量补齐 + 交易日历重建，每天一次
  非交易日（周末）            直接跳过
服务启动时若已过对应时间，会立刻补跑一次，保证重启后数据不缺。
"""

import threading
import time
from datetime import datetime

from . import sync as sync_mod


class CollectorDaemon(threading.Thread):
    def __init__(self, minute_every=300, tick=20):
        super().__init__(daemon=True, name="collector")
        self.minute_every = minute_every
        self.tick_sec = tick
        self._stop_event = threading.Event()
        self._last = {}
        self.logs = []
        self.last_run = None

    def stop(self):
        self._stop_event.set()

    def _log(self, msg):
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        print("[collector] " + line, flush=True)
        self.logs.append(line)
        del self.logs[:-60]
        self.last_run = line

    def _run(self, label, fn):
        self._log("%s 开始" % label)
        started = time.time()
        try:
            fn()
            self._log("%s 完成（%.1fs）" % (label, time.time() - started))
        except Exception as exc:  # noqa: BLE001 采集失败不能拖垮服务
            self._log("%s 失败：%s" % (label, exc))

    def _review_job(self, session):
        """生成复盘前先补一次分时，避免最后 5 分钟数据没落库导致量能偏低。"""
        from ..server import review

        def _job():
            sync_mod.sync_minute(ndays=1)
            review.build_review(session=session, persist=True)

        return _job

    def tick_once(self):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.weekday() >= 5:
            return
        hm = now.hour * 60 + now.minute
        in_session = (9 * 60 + 30 <= hm <= 11 * 60 + 31) or (13 * 60 <= hm <= 15 * 60 + 1)

        if in_session:
            last = self._last.get("minute")
            if last is None or (now - last).total_seconds() >= self.minute_every:
                self._last["minute"] = now
                self._run("盘中分时", lambda: sync_mod.sync_minute(ndays=1))

        # 午盘复盘：11:32 触发，5 分钟内完成（抓全市场约 6-9 秒）
        if hm >= 11 * 60 + 32 and self._last.get("noon") != today:
            self._last["noon"] = today
            self._run("午盘复盘", self._review_job("noon"))

        if hm >= 15 * 60 + 5 and self._last.get("close") != today:
            self._last["close"] = today
            self._run(
                "收盘落库",
                lambda: (sync_mod.sync_minute(ndays=1), sync_mod.sync_daily(full=False)),
            )

        if hm >= 15 * 60 + 6 and self._last.get("close_review") != today:
            self._last["close_review"] = today
            self._run("收盘复盘", self._review_job("close"))

        if hm >= 15 * 60 + 35 and self._last.get("daily") != today:
            self._last["daily"] = today
            self._run("日线补齐", lambda: sync_mod.sync_daily(full=False))

    def run(self):
        self._log(
            "采集调度已启动（盘中每 %d 分钟 / 午盘复盘 11:32 / 收盘 15:05 / 收盘复盘 15:06 / 补齐 15:35）"
            % (self.minute_every // 60)
        )
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception as exc:  # noqa: BLE001
                self._log("调度异常：%s" % exc)
            self._stop_event.wait(self.tick_sec)

    def status(self):
        return {
            "running": self.is_alive(),
            "lastRun": self.last_run,
            "recentLogs": self.logs[-8:],
        }


if __name__ == "__main__":
    daemon = CollectorDaemon()
    daemon.start()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        daemon.stop()
