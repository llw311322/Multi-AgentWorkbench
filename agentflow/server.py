"""Web 控制台后端：零依赖（http.server），只做三件事。

  1. 托管 web/ 下的前端文件
  2. 把 runs/ 的落盘状态序列化成 JSON 给页面读
  3. 接收页面的动作：拆分、执行、续跑、闸门决策、编辑任务图

刻意不引入 Flask/FastAPI：这个项目从第一天就是零第三方依赖，
加一个 web 层不应该破坏这个性质。单人本地工具也不需要 WSGI 生态。

启动：
  python -m agentflow.server                 # 调真实模型
  python -m agentflow.server --mock          # 假数据跑通全流程
  python -m agentflow.server --open          # 启动后自动开浏览器
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# ---------------------------------------------------------------- 外部应用
# 注册表、探活、转发、留档都在 apps.py：网页后端与编排执行器共用同一份定义，
# 放在这里会让 agents/exec_jobs.py 反过来 import 一个 HTTP 服务模块。
from .apps import (  # noqa: E402
    EXTERNAL_APPS,
    activities as _ext_activities,
    activity_end as _ext_act_end,
    activity_note as _ext_act_note,
    activity_start as _ext_act_start,
    activity_summary as _ext_act_summary,
    call as _ext_call,
    get as _ext_get,
    list_docs as api_ext_articles,
    save_doc as _ext_save_doc,
    start as _ext_start,
    summary as api_ext_apps,
    tracked as _ext_tracked,
)
from .models import snapshot as models_snapshot  # noqa: E402


MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".md": "text/markdown; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".ico": "image/x-icon",
}

# ---------------------------------------------------------------- 任务（job）
JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()   # 同一时刻只允许一个编排任务：单人本地，串行才是对的


class Tee(io.TextIOBase):
    """同时写控制台和缓冲区，页面能看到命令行的那份日志。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def _new_job(action: str, meta: dict) -> tuple[str, dict]:
    jid = f"J{int(time.time() * 1000) % 1000000}-{len(JOBS) + 1}"
    job = {
        "job_id": jid,
        "action": action,
        "status": "queued",
        "started": time.strftime("%H:%M:%S"),
        "log": [],
        "error": "",
        **meta,
    }
    with JOB_LOCK:
        JOBS[jid] = job
    return jid, job


def _run_job(job: dict, fn) -> None:
    """在后台线程里跑编排，把 stdout 里的日志收进 job。"""
    from . import llm

    if not RUN_LOCK.acquire(blocking=False):
        job["status"] = "busy"
        job["error"] = "已有编排在跑（很可能正卡在闸门等决策），先处理它再来"
        return

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = Tee(old, buf)
    job["status"] = "running"
    try:
        if job.get("mock") is not None:
            llm.MOCK = bool(job["mock"])
            os.environ["AGENTFLOW_MOCK"] = "1" if job["mock"] else "0"
        result = fn()
        job["result"] = result
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 —— 后台线程不能把异常吞掉不报
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
        job["traceback"] = traceback.format_exc()[-2000:]
        # 线程炸了，state.json 还停在 running：不标出来，页面会一直以为它还在跑
        try:
            from . import store

            _mark_run_interrupted(
                job.get("run_id") or store.LAST_RUN.get("run_id", ""),
                f"编排线程异常退出：{type(e).__name__}",
            )
        except Exception:  # noqa: BLE001 —— 兜底失败不能遮住原始异常
            pass
    finally:
        sys.stdout = old
        job["log"] = buf.getvalue().splitlines()[-400:]
        RUN_LOCK.release()


# ---------------------------------------------------------------- 数据投影
def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _gate_is_live(gate: dict) -> bool:
    """闸门是否还活着：编排线程早退了（服务重启/超时）就不该继续显示为「待确认」。"""
    from . import gates

    if gate.get("decision") or gate.get("status") != "pending":
        return False
    try:
        created = time.mktime(time.strptime(gate.get("created_at", ""), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return False
    return (time.time() - created) < gates.GATE_TIMEOUT


def _project_run(run_id: str) -> dict:
    from . import store

    d = store.run_dir(run_id)
    state = _read_json(d / "state.json") or {}
    graph = _read_json(d / "graph.json") or {}
    nodes = graph.get("nodes") or []

    counts = {"pending": 0, "ready": 0, "running": 0, "review": 0, "done": 0, "blocked": 0, "failed": 0}
    tokens = 0
    for n in nodes:
        st = n.get("status") or "pending"
        counts[st] = counts.get(st, 0) + 1

    # token 从事件流里累加（result.json 里也有，但事件流在跑的过程中就能读到）
    for ev in state.get("history") or []:
        if ev.get("event") == "node_finish":
            tokens += int(ev.get("tokens_used") or 0)

    gates = store.list_gates(run_id)
    pending = [g for g in gates if _gate_is_live(g)]

    return {
        "run_id": run_id,
        "topic": state.get("topic", ""),
        "goal": graph.get("goal", ""),
        "status": state.get("status", "unknown"),
        "stage": state.get("stage", ""),
        "created_at": state.get("created_at", ""),
        "updated_at": state.get("updated_at", ""),
        "counts": counts,
        "nodes_total": len(nodes),
        "attempts": sum(int(n.get("attempts") or 0) for n in nodes),
        "tokens": tokens,
        "pending_gate": pending[0]["gate_id"] if pending else "",
        "has_final": bool((d / "final.md").exists()),
        "history_len": len(state.get("history") or []),
    }


def api_runs() -> dict:
    from . import store

    runs = [_project_run(r) for r in store.list_runs()]
    runs.sort(key=lambda r: r["run_id"], reverse=True)
    return {"runs": runs}


def api_overview() -> dict:
    from . import router

    data = api_runs()["runs"]
    total_nodes = sum(r["nodes_total"] for r in data)
    done_nodes = sum(r["counts"].get("done", 0) for r in data)
    blocked = sum(r["counts"].get("blocked", 0) + r["counts"].get("failed", 0) for r in data)
    tokens = sum(r["tokens"] for r in data)
    return {
        "runs": len(data),
        "active": len([r for r in data if r["status"] == "running"]),
        "waiting_human": len([r for r in data if r["pending_gate"] or r["status"] == "blocked"]),
        "nodes_total": total_nodes,
        "nodes_done": done_nodes,
        "blocked": blocked,
        "tokens": tokens,
        "concurrency": router.MAX_CONCURRENT,
        "routes": router.DOMAIN_EXECUTOR,
    }


def api_run(run_id: str, light: bool = False) -> dict:
    from . import store

    d = store.run_dir(run_id)
    if not (d / "state.json").exists():
        raise FileNotFoundError(run_id)
    state = _read_json(d / "state.json") or {}
    graph = _read_json(d / "graph.json") or {}
    trace = ""
    tp = d / "trace.log"
    if tp.exists() and not light:
        trace = tp.read_text(encoding="utf-8", errors="replace")[-20000:]
    final = ""
    fp = d / "final.md"
    if fp.exists() and not light:
        final = fp.read_text(encoding="utf-8", errors="replace")[:40000]
    return {
        "summary": _project_run(run_id),
        "state": state,
        "graph": graph,
        "gates": store.list_gates(run_id),
        "trace": trace,
        "final": final,
    }


def api_node(run_id: str, task_id: str) -> dict:
    from . import store

    graph = _read_json(store.run_dir(run_id) / "graph.json") or {}
    node = next((n for n in graph.get("nodes") or [] if n.get("task_id") == task_id), None)
    if node is None:
        raise FileNotFoundError(task_id)
    return {
        "node": node,
        "result": store.read_task_json(run_id, task_id, "result") or {},
        "files": store.list_task_files(run_id, task_id),
    }


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "AgentFlow/1.0"

    def log_message(self, fmt, *args):  # 静音：只保留错误
        pass

    # -------------------------------------------------- 响应工具
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        # 浏览器刷新/关页面会直接断开连接：从响应头到正文的每一步都可能抛
        # ConnectionResetError / ConnectionAbortedError(WinError 10053) / BrokenPipeError，
        # 三者都是 ConnectionError 的子类，统一吞掉，不再往上冒成 traceback
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            self.close_connection = True

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   MIME[".json"])

    def _err(self, msg: str, code: int = 400) -> None:
        self._json({"ok": False, "error": msg}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # -------------------------------------------------- GET
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            if path.startswith("/api/"):
                return self._api_get(path, qs)
            return self._static(path)
        except FileNotFoundError as e:
            if path.startswith("/api/"):
                return self._err(f"未找到：{e}", 404)
            return self._static("/index.html")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # 客户端已断开（刷新/关页面），静默收尾
        except Exception as e:  # noqa: BLE001
            return self._err(f"{type(e).__name__}: {e}", 500)

    def _api_get(self, path: str, qs: dict) -> None:
        from . import gates, router

        if path == "/api/health":
            from . import llm

            return self._json({
                "ok": True,
                "mock": llm.MOCK,
                "model": llm.MODEL if not llm.MOCK else "mock",
                "gate_mode": gates.GATE_MODE,
                "gate_timeout": gates.GATE_TIMEOUT,
                "concurrency": router.MAX_CONCURRENT,
                "root": str(WEB_DIR.parent),
            })
        if path == "/api/overview":
            return self._json(api_overview())
        if path == "/api/runs":
            return self._json(api_runs())
        if path == "/api/jobs":
            with JOB_LOCK:
                jobs = sorted(JOBS.values(), key=lambda j: j["job_id"], reverse=True)[:20]
            return self._json({"jobs": [{k: v for k, v in j.items() if k != "log"} | {"log_tail": (j.get("log") or [])[-6:]} for j in jobs]})
        if path.startswith("/api/job/"):
            jid = path.rsplit("/", 1)[-1]
            job = JOBS.get(jid)
            if not job:
                return self._err("没有这个任务", 404)
            return self._json(job)
        if path.startswith("/api/run/"):
            rest = path[len("/api/run/"):]
            if "/node/" in rest:
                run_id, task_id = rest.split("/node/", 1)
                return self._json(api_node(run_id, task_id))
            light = (qs.get("light") or ["0"])[0] in ("1", "true")
            return self._json(api_run(rest, light=light))
        if path == "/api/ext":
            return self._json(api_ext_apps())
        if path == "/api/activity":
            return self._json({"ok": True, "activities": _ext_activities()})
        if path == "/api/models":
            return self._json(models_snapshot())
        if path == "/api/kb/status":
            # 知识库（apps/kb，pgvector）的连通与规模；没起容器时返回 ok=false，页面据此降级
            sys.path.insert(0, str(WEB_DIR.parent / "apps" / "kb"))
            try:
                from apps.kb import db as kb_db, embed as kb_embed

                st = kb_db.status()
                st["provider"] = kb_embed.provider_name()
                st["model"] = kb_embed.model_name()
                st["dim"] = kb_embed.DIM
                return self._json(st)
            except Exception as e:  # noqa: BLE001 —— 驱动没装/路径变了都归为未就绪
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})
        if path == "/api/ext/articles":
            key = (qs.get("key") or ["p02"])[0]
            return self._json(api_ext_articles(key))
        if path.startswith("/api/ext/"):
            # GET 型代理：/api/ext/<key>/session?sid=... —— 让页面刷新后能取回上一轮结果
            rest = path[len("/api/ext/"):]
            key, _, action = rest.partition("/")
            app = EXTERNAL_APPS.get(key)
            if not app:
                return self._err("没有这个外部应用", 404)
            spec = (app.get("proxy") or {}).get(action)
            if not spec or spec[0] != "GET":
                return self._err("不支持的动作", 404)
            query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
            code, data = _ext_get(app, spec[1] + ("?" + query if query else ""), timeout=5)
            if code != 200 or data is None:
                return self._json({"ok": False, "error": "应用不在线或接口无响应"}, 502)
            return self._json(data)
        if path == "/api/file":
            from . import store

            rel = (qs.get("path") or [""])[0]
            target = (store.ROOT / rel).resolve()
            if not str(target).startswith(str(store.ROOT.resolve())):
                return self._err("路径越界", 403)
            if not target.exists() or not target.is_file():
                return self._err("文件不存在", 404)
            text = target.read_text(encoding="utf-8", errors="replace")[:80000]
            return self._send(200, text.encode("utf-8"), MIME[".txt"])
        return self._err("未知接口", 404)

    # -------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/plan":
                return self._post_plan(self._body())
            if path == "/api/run":
                return self._post_run(self._body())
            if path == "/api/resume":
                return self._post_resume(self._body())
            if path == "/api/gate":
                return self._post_gate(self._body())
            if path == "/api/graph":
                return self._post_graph(self._body())
            if path == "/api/file":
                return self._post_file(self._body())
            if path.startswith("/api/ext/"):
                return self._post_ext(path, self._body())
            return self._err("未知接口", 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # 客户端已断开，无需再回错误
        except Exception as e:  # noqa: BLE001
            return self._err(f"{type(e).__name__}: {e}", 500)

    def _busy(self) -> dict | None:
        from . import store

        if RUN_LOCK.locked():
            return {
                "ok": False,
                "busy": True,
                "run_id": store.LAST_RUN["run_id"],
                "error": "已有编排在运行（很可能正卡在闸门等你的决策）。先去「反馈闭环」处理它，或驳回。",
            }
        return None

    def _post_plan(self, body: dict) -> None:
        from .orchestrator import plan

        busy = self._busy()
        if busy:
            return self._json(busy, 409)
        req = (body.get("requirement") or "").strip()
        if not req:
            return self._err("需求不能为空")
        jid, job = _new_job("plan", {"requirement": req, "mock": body.get("mock")})

        def task():
            out = plan(req, auto=False)
            return {"run_id": out[0].run_id if out else ""}

        job["thread"] = threading.Thread(target=_run_job, args=(job, task), daemon=True)
        job["thread"].start()
        return self._json({"ok": True, "job_id": jid})

    def _post_run(self, body: dict) -> None:
        from .orchestrator import resume, run
        from . import store

        busy = self._busy()
        if busy:
            return self._json(busy, 409)
        run_id = (body.get("run_id") or "").strip()
        req = (body.get("requirement") or "").strip()
        auto = bool(body.get("auto", False))

        if run_id:
            if not (store.run_dir(run_id) / "state.json").exists():
                return self._err("没有这个运行")
            jid, job = _new_job("resume", {"run_id": run_id, "mock": body.get("mock")})

            def task():
                st = resume(run_id, auto=auto)
                return {"run_id": st.run_id, "status": st.status}

            job["thread"] = threading.Thread(target=_run_job, args=(job, task), daemon=True)
            job["thread"].start()
            return self._json({"ok": True, "job_id": jid})

        if not req:
            return self._err("需求不能为空")
        jid, job = _new_job("run", {"requirement": req, "mock": body.get("mock")})

        def task():
            st = run(req, auto=auto)
            return {"run_id": st.run_id if st else "", "status": st.status if st else "aborted"}

        job["thread"] = threading.Thread(target=_run_job, args=(job, task), daemon=True)
        job["thread"].start()
        return self._json({"ok": True, "job_id": jid})

    def _post_resume(self, body: dict) -> None:
        return self._post_run(body)

    def _post_gate(self, body: dict) -> None:
        from . import store

        run_id = (body.get("run_id") or "").strip()
        gate_id = (body.get("gate_id") or "").strip()
        decision = (body.get("decision") or "").strip()
        note = (body.get("note") or "").strip()
        if not (run_id and gate_id and decision):
            return self._err("run_id / gate_id / decision 都必填")
        ok = store.decide_gate(run_id, gate_id, decision, note)
        if not ok:
            return self._err("闸门不存在或已过期", 404)
        return self._json({"ok": True, "gate_id": gate_id, "decision": decision})

    def _post_graph(self, body: dict) -> None:
        from . import store
        from .graph import TaskGraph, validate_graph

        run_id = (body.get("run_id") or "").strip()
        graph = body.get("graph")
        if not (run_id and isinstance(graph, dict)):
            return self._err("run_id 和 graph 都必填")
        try:
            g = TaskGraph.from_dict(graph)
        except Exception as e:  # noqa: BLE001
            return self._err(f"图结构解析失败：{e}")
        problems = validate_graph(g)
        if problems:
            return self._json({"ok": False, "problems": problems}, 200)
        store.save_graph(run_id, g)
        return self._json({"ok": True, "nodes": len(g.nodes)})

    def _post_ext(self, path: str, body: dict) -> None:
        """POST /api/ext/<key>/<action> —— 启停，以及按 apps.py 注册表原样转发。

        新增一个外部 Agent 只需要在注册表里加一条 proxy 映射，这里不用动。
        「干活」的动作（tracked）会登记进活动日志，协作监控页实时可见——
        先记「运行中」再转发，慢活（岗位采集 1–2 分钟）在监控页是活的。
        """
        rest = path[len("/api/ext/"):]
        key, _, action = rest.partition("/")
        app = EXTERNAL_APPS.get(key)
        if not app:
            return self._err("没有这个外部应用", 404)
        if action == "start":
            return self._json(_ext_start(key))
        track = _ext_tracked(key, action)
        eid = _ext_act_start(key, action, _ext_act_note(key, body)) if track else None
        t0 = time.time()
        code, data = _ext_call(key, action, body)
        if code == 0 or data is None:
            msg = (data or {}).get("error") or "应用无响应，请先点「启动服务」"
            if track:
                _ext_act_end(eid, False, (time.time() - t0) * 1000, error=msg)
            return self._json({"ok": False, "error": msg}, 502)
        # 成功就留档：页面收起 / 刷新 / 切页都不会丢
        saved = ""
        if code == 200:
            s = _ext_save_doc(key, data)
            if s:
                data["saved"] = s
                saved = s.get("path", "")
        if track:
            ok = code == 200 and not (isinstance(data, dict) and data.get("ok") is False)
            _ext_act_end(
                eid,
                ok,
                (time.time() - t0) * 1000,
                summary=_ext_act_summary(key, data),
                saved=saved,
                error=(data or {}).get("error") if not ok else "",
            )
        return self._json(data, 200 if code == 200 else 400)

    def _post_file(self, body: dict) -> None:
        from . import store

        rel = (body.get("path") or "").strip()
        content = body.get("content")
        if not rel or content is None:
            return self._err("path 和 content 都必填")
        target = (store.ROOT / rel).resolve()
        if not str(target).startswith(str(store.ROOT.resolve() / "runs")):
            return self._err("只允许写 runs/ 下的产物文件", 403)
        if target.suffix.lower() not in (".md", ".txt", ".log", ".json"):
            return self._err("只允许写 .md/.txt/.log/.json", 403)
        if target.name in ("graph.json",):
            return self._err("任务图请走 /api/graph", 403)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return self._json({"ok": True, "path": str(target.relative_to(store.ROOT)).replace("\\", "/")})

    # -------------------------------------------------- 静态文件
    def _static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        target = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())):
            return self._err("路径越界", 403)
        if not target.exists() or not target.is_file():
            return self._send(404, b"404", MIME[".txt"])
        ctype = MIME.get(target.suffix.lower(), "application/octet-stream")
        return self._send(200, target.read_bytes(), ctype)


def _mark_orphan_gates() -> int:
    """服务重启后，旧进程挂起的闸门永远等不到结果，标成 orphaned。

    不标的话页面会一直显示「待确认」，点了也石沉大海——这比直接报错更伤信任。
    """
    from . import store

    n = 0
    for run_id in store.list_runs():
        for g in store.list_gates(run_id):
            if g.get("status") == "pending" and not g.get("decision"):
                g["status"] = "orphaned"
                g["note"] = "服务重启，原编排线程已结束；用「续跑」继续"
                store.write_gate(run_id, g["gate_id"], g)
                n += 1
    return n


def _mark_run_interrupted(run_id: str, reason: str) -> bool:
    """把「卡在 running 但已经没人推进」的 Run 标成 interrupted。

    RunState.status 只有 running/done/failed/rejected 四态，进程一死它就永远是
    running：页面显示「执行中」、看板上节点原地不动，用户在等一个不会来的结果。
    这比直接报错更伤信任——错了他能重试，装死他只能干等。
    """
    from . import store

    if not run_id:
        return False
    try:
        state = store.load_state(run_id)
    except Exception:  # noqa: BLE001
        return False
    if state.status != "running":
        return False
    state.run_id = run_id          # 目录名才是权威：state.json 里的 run_id 可能是旧值
    state.status = "interrupted"
    state.stage = "interrupted"
    state.log("interrupted", reason=reason)
    store.save_state(state)
    return True


def _mark_interrupted_runs() -> int:
    """服务重启后，旧进程留下的 running 全是死的，逐条标成 interrupted。

    节点状态不动：running/review 的节点由 orchestrator.resume() 退回 ready 重跑，
    已 done 的节点不会再执行一次——断点续跑靠的就是这份状态。
    """
    from . import store

    return sum(
        1 for run_id in store.list_runs() if _mark_run_interrupted(run_id, "服务重启，原编排线程已结束")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentflow.server", description="多 Agent 协作 Web 控制台")
    parser.add_argument("--port", type=int, default=8760)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--mock", action="store_true", help="用假数据跑通，不调用模型")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--gate-timeout", type=int, default=None, help="闸门等待秒数，默认 1800")
    args = parser.parse_args(argv)

    if args.mock:
        os.environ["AGENTFLOW_MOCK"] = "1"
    # 网页模式下闸门挂在浏览器上，不能用 input()
    os.environ["AGENTFLOW_GATE_MODE"] = "web"
    if args.gate_timeout:
        os.environ["AGENTFLOW_GATE_TIMEOUT"] = str(args.gate_timeout)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if not (WEB_DIR / "index.html").exists():
        print(f"[x] 找不到前端目录：{WEB_DIR}")
        return 1

    orphaned = _mark_orphan_gates()
    if orphaned:
        print(f"  [!] {orphaned} 个旧闸门已标记为 orphaned（对应 Run 请用「续跑」继续）")

    interrupted = _mark_interrupted_runs()
    if interrupted:
        print(f"  [!] {interrupted} 个 Run 停在 running（服务重启），已标记 interrupted，可在看板点「继续 / 续跑」")

    def _boot_ext() -> None:
        for key, app in EXTERNAL_APPS.items():
            if not app.get("auto_start"):
                continue
            code, _ = _ext_get(app, app["health_path"])
            if code == 200:
                continue
            res = _ext_start(key)
            print(f"  [ext] {app['name']}：{res.get('msg') or res.get('error')}")

    threading.Thread(target=_boot_ext, daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("=" * 62)
    print("  AgentFlow 控制台")
    print(f"  {url}")
    print(f"  模式：{'mock（假数据）' if args.mock else '真实模型'}")
    print(f"  闸门：web 模式，等待页面决策，超时 {os.environ.get('AGENTFLOW_GATE_TIMEOUT', '1800')}s")
    print(f"  产物：{WEB_DIR.parent / 'runs'}")
    print("=" * 62)
    print("  Ctrl+C 停止\n")

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
