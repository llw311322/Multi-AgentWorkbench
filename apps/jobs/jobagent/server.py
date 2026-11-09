"""零依赖 HTTP 服务（Python 标准库），提供网页界面与 JSON API。"""
from __future__ import annotations

import json
import mimetypes
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import adapters, report
from .agent import Agent
from .adapters import htmlimport

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
DATA = os.path.join(ROOT, "data")
AGENT = Agent(DATA)


class Handler(BaseHTTPRequestHandler):
    server_version = "JobAgent/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 基础响应 ----------
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _err(self, msg: str, code: int = 400) -> None:
        self._json({"error": msg}, code)

    def log_message(self, fmt: str, *args: Any) -> None:  # 静默
        pass

    # ---------- 路由 ----------
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path == "/api/health":
            # 给工作台/运维脚本探活用：只回进程存活，不做采集，也不读会话文件
            return self._json({"ok": True, "app": "jobagent", "version": "1.0"})
        if path.startswith("/web/"):
            return self._static(path[5:])
        if path == "/api/session":
            sid = (qs.get("sid") or ["default"])[0]
            st = AGENT.store.load(sid)
            return self._json({
                "analysis": st.get("analysis"),
                "jobs": AGENT._table(st.get("jobs") or []),
                "profile": st.get("profile") or {},
                "query": st.get("query") or {},
                "settings": {"sources": adapters.all_status(st.get("settings") or {})},
            })
        if path == "/api/report":
            sid = (qs.get("sid") or ["default"])[0]
            st = AGENT.store.load(sid)
            if not st.get("analysis"):
                return self._err("暂无数据", 404)
            html = report.render(st["analysis"], st.get("jobs") or [],
                                 st.get("query") or {}, st.get("profile"))
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if path.startswith("/files/"):
            name = urllib.parse.unquote(path[7:])
            fp = os.path.join(DATA, "outputs", os.path.basename(name))
            if os.path.exists(fp):
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), ctype,
                                      {"Content-Disposition": f'attachment; filename="{os.path.basename(fp)}"'})
            return self._err("文件不存在", 404)
        return self._err("Not Found", 404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._err("请求体不是合法 JSON")

        if parsed.path == "/api/chat":
            sid = str(payload.get("sid") or "default")
            msg = str(payload.get("message") or "")
            try:
                res = AGENT.handle(sid, msg)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                return self._err(f"处理失败：{exc}", 500)
            res["sid"] = sid
            return self._json(res)

        if parsed.path == "/api/import":
            sid = str(payload.get("sid") or "default")
            text = str(payload.get("text") or "")
            if len(text) < 40:
                return self._err("内容太短，没识别到岗位数据")
            try:
                jobs, src = htmlimport.parse_payload(text, str(payload.get("hint") or ""))
            except Exception as exc:
                return self._err(f"解析失败：{exc}", 500)
            if not jobs:
                return self._err("没能从这段内容里解析出岗位，确认是招聘搜索结果页的 HTML？")
            st = AGENT.store.load(sid)
            from . import analyzer
            st["jobs"] = jobs
            st["query"] = {"keywords": [payload.get("hint") or "导入数据"], "city": "",
                           "imported": True, "source_hint": src}
            st["analysis"] = analyzer.analyze(jobs, st.get("profile"), st["query"])
            AGENT.store.save(sid, st)
            return self._json({
                "reply": f"已从导入内容中解析出 **{len(jobs)}** 条岗位（识别为 {src}），"
                         f"分析已重新计算。",
                "intent": "import", "analysis": st["analysis"],
                "jobs": AGENT._table(jobs), "warnings": [],
                "meta": {"count": len(jobs), "source": src},
                "settings": {"sources": adapters.all_status(st.get("settings") or {})},
            })

        if parsed.path == "/api/settings":
            sid = str(payload.get("sid") or "default")
            st = AGENT.store.load(sid)
            settings = st.setdefault("settings", {"cookies": {"zhaopin": "", "liepin": ""},
                                                  "deep_detail": False})
            if "cookies" in payload:
                settings.setdefault("cookies", {}).update(payload["cookies"])
            if "deep_detail" in payload:
                settings["deep_detail"] = bool(payload["deep_detail"])
            AGENT.store.save(sid, st)
            return self._json({"ok": True, "sources": adapters.all_status(settings)})

        if parsed.path == "/api/export":
            sid = str(payload.get("sid") or "default")
            res = AGENT.handle(sid, "导出报告")
            return self._json(res)

        return self._err("Not Found", 404)

    # ---------- 静态文件 ----------
    def _static(self, rel: str) -> None:
        fp = os.path.join(WEB, os.path.basename(rel))
        if not os.path.exists(fp):
            return self._err("Not Found", 404)
        ctype = mimetypes.guess_type(fp)[0] or "text/plain"
        if fp.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif fp.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif fp.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)


def main(port: int = 8770) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"招聘 JD 采集分析 Agent 已启动： http://127.0.0.1:{port}")
    print("按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
