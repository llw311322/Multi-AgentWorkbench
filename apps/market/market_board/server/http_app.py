"""HTTP 服务：标准库 ThreadingHTTPServer，既出 API 又托管前端静态文件。"""

import json
import mimetypes
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..config import INDEX_MAP, WEB_DIR
from . import review, service

_daemon = None

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "MarketBoard/0.1"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 工具方法
    def _send(self, status, body, content_type, cache="no-store"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=200, cache="no-store"):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self._send(status, body, "application/json; charset=utf-8", cache)

    def _error(self, status, message):
        self._json({"ok": False, "error": message}, status)

    # ------------------------------------------------------------ 路由
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                return self._handle_api(path, query)
            if path == "/" or path == "/index.html":
                return self._serve_file("index.html")
            if path == "/demo":
                return self._serve_file("embed-demo.html")
            if path in ("/noon", "/review"):
                return self._serve_file("noon.html")
            if path.startswith("/static/"):
                return self._serve_file(path[len("/static/"):])
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            return self._error(404, f"未找到: {path}")
        except KeyError as exc:
            self._error(400, str(exc).strip("'\""))
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._error(500, f"服务异常: {exc}")

    def _handle_api(self, path, query):
        q = {k: v[0] for k, v in query.items()}

        if path == "/api/indices":
            return self._json({"ok": True, "data": service.get_indices()})

        if path == "/api/kline":
            code = q.get("code", "000001.SH")
            if code not in INDEX_MAP:
                return self._error(400, f"未知指数代码: {code}")
            period = q.get("period", "day")
            if period not in ("day", "week", "month", "year"):
                return self._error(400, f"不支持的周期: {period}")
            try:
                limit = int(q.get("limit", "800"))
            except ValueError:
                limit = 800
            data = service.get_kline(code, period, limit)
            return self._json({"ok": True, "data": data}, cache="public, max-age=30")

        if path == "/api/intraday":
            code = q.get("code", "000001.SH")
            if code not in INDEX_MAP:
                return self._error(400, f"未知指数代码: {code}")
            data = service.get_intraday(code)
            return self._json({"ok": True, "data": data})

        if path == "/api/brief":
            return self._json({"ok": True, "data": service.get_brief()}, cache="public, max-age=60")

        if path == "/api/noon-brief":
            date = q.get("date")
            refresh = q.get("refresh") == "1"
            data = review.get_review("noon", date, refresh=refresh)
            return self._json({"ok": True, "data": data})

        if path == "/api/review":
            session = q.get("session", "noon")
            if session not in ("noon", "close"):
                return self._error(400, f"不支持的时点: {session}")
            date = q.get("date")
            refresh = q.get("refresh") == "1"
            data = review.get_review(session, date, refresh=refresh)
            return self._json({"ok": True, "data": data})

        if path == "/api/review-history":
            try:
                limit = int(q.get("limit", "20"))
            except ValueError:
                limit = 20
            return self._json({"ok": True, "data": review.history(max(1, min(limit, 120)))})

        if path == "/api/health":
            return self._json(
                {
                    "ok": True,
                    "marketState": service.market_state(),
                    "indices": len(INDEX_MAP),
                    "collector": _daemon.status() if _daemon else {"running": False},
                }
            )

        return self._error(404, f"未知接口: {path}")

    def _serve_file(self, rel):
        target = (WEB_DIR / rel).resolve()
        if not target.is_file() or not target.is_relative_to(WEB_DIR.resolve()):
            return self._error(404, f"静态文件不存在: {rel}")
        ext = target.suffix.lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        cache = "public, max-age=86400" if ext in (".js", ".css", ".svg") else "no-cache"
        self._send(200, target.read_bytes(), ctype, cache)

    def log_message(self, fmt, *args):  # 精简访问日志
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))


def main(host="127.0.0.1", port=8080, daemon=None):
    global _daemon
    _daemon = daemon
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"大盘可视化已启动  ->  http://{host}:{port}")
    print(
        "  API: /api/indices  /api/kline  /api/intraday  /api/brief"
        "  /api/noon-brief  /api/review  /api/health"
    )
    print(f"  复盘页: http://{host}:{port}/noon")
    if daemon:
        print("  采集调度: 已开启（盘中 5 分钟 / 午盘复盘 11:32 / 收盘 15:05 / 收盘复盘 15:06）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
        if daemon:
            daemon.stop()


if __name__ == "__main__":
    main()
