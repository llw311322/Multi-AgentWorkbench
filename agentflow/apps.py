"""内嵌 Agent 注册表 + 调用通道 + 结果留档。

两个本地 Agent（P-02 文案生成、jobs 招聘采集）的源码已整体拷入本项目的
apps/<key>/ 目录，随工作台一起走：工作台负责三件事——探活、转发、把结果
落盘留档；启动时用它们自己的入口把它们拉起为本地子服务。

为什么单独一个模块：网页后端（server.py）和编排执行器（agents/exec_jobs.py）
都要用同一份注册表。放在 server.py 里会让执行器 import 一个 HTTP 服务模块，
是隐形的循环依赖。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 注册表
# key 同时用于 URL 路径（/api/ext/<key>/...），只用小写英文。
# proxy 里的动作会被原样转发到应用服务：action -> (HTTP 方法, 对方路径)
_APPS_ROOT = Path(__file__).resolve().parent.parent / "apps"

EXTERNAL_APPS: dict[str, dict[str, Any]] = {
    "p02": {
        "key": "p02",
        "name": "文案生成 Agent",
        "desc": "主题拆解 → 结构化文章生成（多厂商 LLM，Flask 本地服务）",
        "base_url": "http://127.0.0.1:8978",
        # 源码已内嵌在 apps/p02/（含 .env 密钥），不再依赖外部目录
        "dir": os.environ.get("P02_DIR", str(_APPS_ROOT / "p02")),
        "launch": ["app.py"],
        # 该应用依赖 Flask，优先用 P02_PYTHON 指定的解释器；否则找本机 pythonw（无黑窗），
        # 都没有就退回当前解释器
        "python": (os.environ.get("P02_PYTHON")
                   or (r"F:\python\python311\pythonw.exe"
                       if os.path.exists(r"F:\python\python311\pythonw.exe") else sys.executable)),
        "health_path": "/api/health",
        # 工作台启动时若它是离线的，自动拉起一次
        "auto_start": True,
        "proxy": {
            "generate": ("POST", "/api/generate"),
            "vendors": ("GET", "/api/vendors"),
        },
    },
    "jobs": {
        "key": "jobs",
        "name": "招聘 JD 采集分析 Agent",
        "desc": "一句话需求 → 智联/猎聘采集 → 清洗归一 → 薪资·技能·人岗匹配分析",
        "base_url": os.environ.get("JOBAGENT_URL", "http://127.0.0.1:8770"),
        # 源码已内嵌在 apps/jobs/，目录可用环境变量覆盖
        "dir": os.environ.get("JOBAGENT_DIR", str(_APPS_ROOT / "jobs")),
        "launch": ["run.py", "8770"],
        "health_path": "/api/health",
        "auto_start": True,
        # 工作台与它共用一个会话 id：同一份采集结果，页面刷新、导出报告都对得上
        "sid": "workspace",
        "proxy": {
            "chat": ("POST", "/api/chat"),
            "session": ("GET", "/api/session"),
            "settings": ("POST", "/api/settings"),
        },
    },
    "market": {
        "key": "market",
        "name": "大盘可视化 Agent",
        "desc": "6 大盘指数自动采集 → K线/分时/午盘·收盘复盘，自带看板前端与采集调度",
        "base_url": os.environ.get("MARKET_URL", "http://127.0.0.1:8080"),
        "dir": os.environ.get("MARKET_DIR", str(_APPS_ROOT / "market")),
        "launch": ["run.py", "--port", "8080", "--auto-sync"],
        "health_path": "/api/health",
        "auto_start": True,
        "proxy": {
            "brief": ("GET", "/api/brief"),
            "indices": ("GET", "/api/indices"),
        },
    },
    # 命令型应用：没有常驻 HTTP 服务，call() 直接跑子进程。
    "hotspot": {
        "key": "hotspot",
        "name": "热点雷达 Agent",
        "desc": "14 源抓取科技/AI + 财经热点 → 去重聚类 → 紧凑摘要（命令型，无 HTTP 服务）",
        "kind": "command",
        "dir": os.environ.get("HOTSPOT_DIR", str(_APPS_ROOT / "hotspot")),
        "launch": ["collect.py"],
        "actions": {
            # payload 里的键会被替换进参数模板
            "collect": ["--hours", "{hours}", "--digest"],
        },
        "auto_start": False,
    },
}

_LOCK = threading.Lock()


def get_app(key: str) -> dict[str, Any] | None:
    return EXTERNAL_APPS.get(key)


def sid_of(app: dict[str, Any]) -> str:
    return app.get("sid") or "default"


# ---------------------------------------------------------------- 探活 / 调用
def _opener():
    # 本地地址不要走系统代理，否则会被代理吞掉
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(app: dict[str, Any], path: str, timeout: float = 1.2) -> tuple[int, dict | None]:
    """GET 外部应用。返回 (http状态码, json或None)。超时不抛异常。"""
    try:
        with _opener().open(urllib.request.Request(app["base_url"] + path), timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return r.status, None
    except Exception:  # noqa: BLE001
        return 0, None


def post(app: dict[str, Any], path: str, data: dict, timeout: float = 300) -> tuple[int, dict | None]:
    """转发 POST（绕开浏览器 CORS：浏览器只跟我们说话）。

    外部应用用 4xx/5xx 返回「业务错误」，但 urllib 遇到这些码会抛 HTTPError，
    错误体在异常对象里。必须把它读出来原样透传——否则页面只能看到
    「HTTP Error 500」，真实原因（余额不足 / 未配 key / 采集被风控）全丢。
    """
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        app["base_url"] + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _opener().open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except Exception:  # noqa: BLE001
            pass
        detail = None
        if raw:
            try:
                detail = json.loads(raw.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                detail = {"ok": False, "error": raw.decode("utf-8", "replace")[:500]}
        if isinstance(detail, dict) and detail.get("error"):
            return e.code, detail
        return e.code, {"ok": False, "error": f"{app['name']} 返回 HTTP {e.code}"}
    except urllib.error.URLError as e:
        return 0, {
            "ok": False,
            "error": f"连不上 {app['name']}（{app['base_url']}）：{e.reason}。请先在页面上点「启动服务」",
        }
    except Exception as e:  # noqa: BLE001
        return 0, {"ok": False, "error": f"调用 {app['name']} 失败：{type(e).__name__}: {e}"}


def is_online(app: dict[str, Any]) -> bool:
    if app.get("kind") == "command":
        # 命令型应用没有端口可探：入口脚本存在即视为「就绪」
        return (Path(app["dir"]) / app["launch"][0]).is_file()
    code, health = get(app, app["health_path"])
    if code != 200:
        return False
    # 有的应用健康检查只回 200 不带头字段，不能强求 ok 字段
    return not (isinstance(health, dict) and health.get("ok") is False)


def call(key: str, action: str, payload: dict | None = None, timeout: float = 300) -> tuple[int, dict | None]:
    """按注册表里的 proxy 转发一次动作（命令型应用则直接跑子进程）。返回 (状态码, 数据)。"""
    app = get_app(key)
    if not app:
        return 0, {"ok": False, "error": f"没有注册外部应用 {key!r}"}
    if app.get("kind") == "command":
        return _run_command(app, action, payload or {}, timeout=timeout)
    spec = (app.get("proxy") or {}).get(action)
    if not spec:
        return 0, {"ok": False, "error": f"{app['name']} 没有动作 {action!r}"}
    method, path = spec
    if method == "GET":
        q = urllib.parse.urlencode(payload or {})
        return get(app, path + ("?" + q if q else ""), timeout=timeout)
    return post(app, path, payload or {}, timeout=timeout)


# ---------------------------------------------------------------- 活动登记
# 协作监控页「AI 应用活动」的数据源：每次真实调用记开始/结束两条信息，
# 落盘 data/activity.json，工作台重启不丢。只登记「干活」的动作——
# 探活、读会话这类只读转发不记，否则页面一次刷新就刷出一屏假活动。
_TRACK_ACTIONS = {("p02", "generate"), ("jobs", "chat"), ("hotspot", "collect")}
_ACT_LOCK = threading.Lock()
_ACT_CACHE: list[dict[str, Any]] | None = None
_ACT_SEQ = 0


def tracked(key: str, action: str) -> bool:
    return (key, action) in _TRACK_ACTIONS


def _act_path() -> Path:
    from . import store

    p = store.ROOT / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p / "activity.json"


def _act_load() -> list[dict[str, Any]]:
    global _ACT_CACHE
    if _ACT_CACHE is None:
        try:
            _ACT_CACHE = json.loads(_act_path().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _ACT_CACHE = []
    return _ACT_CACHE


def _act_save() -> None:
    try:
        _act_path().write_text(json.dumps(_ACT_CACHE or [], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def activity_start(key: str, action: str, note: str = "") -> str:
    """登记一次调用的开始，返回事件 id；结束时用 id 回填结果。

    先写「运行中」再干活：jobs 采集要跑一两分钟，监控页轮询时能实时看到它。
    """
    global _ACT_SEQ
    app = get_app(key) or {}
    with _ACT_LOCK:
        _ACT_SEQ += 1
        eid = f"act-{time.strftime('%Y%m%d%H%M%S')}-{_ACT_SEQ}"
        acts = _act_load()
        acts.append(
            {
                "id": eid,
                "key": key,
                "app": app.get("name", key),
                "action": action,
                "state": "running",
                "note": (note or "").strip()[:120],
                "ts": time.time(),
            }
        )
        del acts[:-200]  # 只留最近 200 条
        _act_save()
    return eid


def activity_end(eid: str, ok: bool, duration_ms: float, summary: str = "", saved: str = "", error: str = "") -> None:
    with _ACT_LOCK:
        for ev in reversed(_ACT_CACHE or []):
            if ev.get("id") == eid:
                ev["state"] = "done" if ok else "failed"
                ev["duration_ms"] = int(duration_ms)
                ev["summary"] = (summary or "")[:200]
                if saved:
                    ev["saved"] = saved
                if error:
                    ev["error"] = str(error)[:300]
                break
        _act_save()


def activities(limit: int = 60) -> list[dict[str, Any]]:
    """给监控页读的最近活动（新的在前）。僵死的「运行中」（>30 分钟）标为中断。"""
    with _ACT_LOCK:
        acts = [dict(e) for e in _act_load()]
    now = time.time()
    out = []
    for ev in reversed(acts[-limit:]):
        if ev.get("state") == "running" and now - ev.get("ts", now) > 1800:
            ev["state"] = "lost"
        out.append(ev)
    return out


def activity_note(key: str, body: dict) -> str:
    """调用开始时的人话备注：这次调用想干什么。"""
    if key == "p02":
        return str((body or {}).get("topic") or "")
    if key == "jobs":
        return str((body or {}).get("message") or "")
    if key == "hotspot":
        return f"{(body or {}).get('hours') or 24}h 窗口抓取"
    return ""


def activity_summary(key: str, data: Any) -> str:
    """把一次成功返回压成一行结论，给监控页直接显示。"""
    if not isinstance(data, dict):
        return ""
    if key == "p02":
        art = data.get("article") or {}
        t = (art.get("title") or "").strip()
        if not t:
            return ""
        who = " · ".join(str(x) for x in (data.get("vendor_name"), data.get("model")) if x)
        return f"《{t}》" + (f"（{who}）" if who else "")
    if key == "jobs":
        an = data.get("analysis") or {}
        ov = an.get("overview") or {}
        q = data.get("query") or an.get("query") or {}
        cond = " ".join(str(x) for x in (q.get("city"), "、".join(q.get("keywords") or [])) if x)
        n = ov.get("total")
        return " · ".join(p for p in (cond, f"样本 {n} 条" if n else "") if p)
    if key == "hotspot":
        st = data.get("stats") or {}
        src = data.get("sources") or []
        ok = sum(1 for s in src if s.get("ok"))
        return f"窗口 {data.get('hours')}h · 源 {ok}/{len(src)} · 去重 {st.get('total_after_dedupe') or '?'} 条"
    return ""


# ---------------------------------------------------------------- 命令型应用
def _run_command(app: dict[str, Any], action: str, payload: dict, timeout: float = 300) -> tuple[int, dict | None]:
    """命令型应用：动作 = 跑一次入口脚本。目前支持 hotspot 的 collect。"""
    if app["key"] != "hotspot" or action != "collect":
        return 0, {"ok": False, "error": f"{app['name']} 没有动作 {action!r}"}
    try:
        hours = max(1, min(72, int(payload.get("hours") or 24)))
    except (TypeError, ValueError):
        hours = 24

    py = sys.executable
    cmd = [py, *app["launch"], "--hours", str(hours), "--digest"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=app["dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 0, {"ok": False, "error": f"采集超时（>{timeout}s），14 个源里可能有源卡住了，可缩小时间窗重试"}
    except Exception as e:  # noqa: BLE001
        return 0, {"ok": False, "error": f"运行 collect.py 失败：{type(e).__name__}: {e}"}

    out = proc.stdout or ""
    lines = out.splitlines()
    summary_line = next((l for l in lines if l.startswith("[collect]")), "")
    src_rows = []
    for l in lines:
        m = re.match(r"\s*\[(OK|ERR)\s*\]\s+(.+?)\s{2,}(\d+)\s+条", l)
        if m:
            src_rows.append({"ok": m.group(1) == "OK", "name": m.group(2).strip(), "count": int(m.group(3))})
    # digest 在「已写入」提示行之后
    digest = ""
    for i, l in enumerate(lines):
        if l.startswith("[collect] 已写入"):
            digest = "\n".join(lines[i + 1:]).strip()
            break
    if proc.returncode != 0 or not digest:
        err = (proc.stderr or out or "").strip()[-500:]
        return 0, {"ok": False, "error": f"采集失败（exit {proc.returncode}）：{err}"}

    # 源状态与耗时以脚本自己的汇报为准；条数取自 latest.json 兜底
    stats = {"window_hours": hours, "total_raw": None, "total_after_dedupe": None, "elapsed_sec": None}
    latest = Path(app["dir"]) / "data" / "latest.json"
    try:
        raw = json.loads(latest.read_text(encoding="utf-8"))
        stats["total_raw"] = raw.get("total_raw")
        stats["total_after_dedupe"] = raw.get("total_after_dedupe")
        stats["elapsed_sec"] = raw.get("elapsed_sec")
        stats["generated_at_local"] = raw.get("generated_at_local")
    except Exception:  # noqa: BLE001
        pass
    return 200, {
        "ok": True,
        "hours": hours,
        "summary": summary_line,
        "sources": src_rows,
        "stats": stats,
        "digest": digest,
    }


# ---------------------------------------------------------------- 启动
def start(key: str) -> dict:
    app = get_app(key)
    if not app:
        return {"ok": False, "error": "没有这个外部应用"}
    # 串行化：boot 线程和页面「启动服务」可能并发调进来。
    # Windows 下 http.server 的 allow_reuse_address 等价于 SO_REUSEADDR，
    # 允许两个进程同时 bind 同一端口，后绑定者会抢走连接——必须防双拉起。
    with _LOCK:
        return _start_locked(app)


def _start_locked(app: dict[str, Any]) -> dict:
    key = app["key"]
    if app.get("kind") == "command":
        ok = (Path(app["dir"]) / app["launch"][0]).is_file()
        return {"ok": ok, "status": "ready" if ok else "missing",
                "msg": "命令型应用，无需启动" if ok else f"入口脚本缺失：{app['dir']}/{app['launch'][0]}"}
    if is_online(app):
        return {"ok": True, "status": "online", "msg": "服务已在运行"}

    py = app.get("python") or sys.executable
    if not Path(py).exists():
        py = sys.executable
    cmd = [py, *app["launch"]]
    kwargs: dict[str, Any] = {"cwd": app["dir"]}
    if os.name == "nt":
        # DETACHED_PROCESS：让外部应用脱离本进程的作业对象。
        # 否则工作台一重启/被 Ctrl-C，外部应用会跟着一起死——页面上就会莫名变「离线」。
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0x8)
        )
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"启动失败：{type(e).__name__}: {e}"}
    for _ in range(24):  # 最多等 12 秒
        time.sleep(0.5)
        if is_online(app):
            return {"ok": True, "status": "online", "msg": "已启动"}
    return {"ok": False, "error": "启动超时（12s 内未探活成功），请看该应用自己的日志"}


def summary() -> dict:
    """给页面读的注册表快照：状态 + 该应用自己的元信息。"""
    apps = []
    for a in EXTERNAL_APPS.values():
        online = is_online(a)
        info: dict[str, Any] = {
            k: a[k] for k in ("key", "name", "desc", "base_url") if k in a
        }
        if a.get("kind") == "command":
            info["status"] = "ready" if online else "missing"
        else:
            info["status"] = "online" if online else "offline"
        info["web_url"] = a["base_url"] + "/" if a.get("base_url") else ""
        if a.get("sid"):
            info["sid"] = a["sid"]
        if online:
            if a["key"] == "p02":
                _, vendors = get(a, "/api/vendors")
                if vendors:
                    info["vendors"] = vendors.get("vendors") or []
                    info["active_vendor"] = vendors.get("active_vendor", "deepseek")
            elif a["key"] == "jobs":
                sid = sid_of(a)
                _, sess = get(a, f"/api/session?sid={urllib.parse.quote(sid)}")
                if sess:
                    info["sources"] = (sess.get("settings") or {}).get("sources") or []
                    ov = ((sess.get("analysis") or {}).get("overview") or {})
                    info["history"] = {
                        "jobs": ov.get("total") or 0,
                        "median": (ov.get("salary") or {}).get("median"),
                    }
                info["report_url"] = f"{a['base_url']}/api/report?sid={urllib.parse.quote(sid)}"
            elif a["key"] == "market":
                _, brief = get(a, "/api/brief", timeout=3)
                if brief and brief.get("ok"):
                    d = brief["data"]
                    info["market_state"] = d.get("marketState")
                    info["indices"] = [
                        {k: i.get(k) for k in ("name", "code", "close", "pct", "ret20", "posIn60d")}
                        for i in (d.get("indices") or [])
                    ]
                _, health = get(a, "/api/health", timeout=3)
                if health:
                    info["collector"] = (health.get("collector") or {}).get("lastRun") or ""
            elif a["key"] == "hotspot":
                latest = Path(a["dir"]) / "data" / "latest.json"
                try:
                    raw = json.loads(latest.read_text(encoding="utf-8"))
                    info["last_collect"] = {
                        "at": raw.get("generated_at_local"),
                        "window_hours": raw.get("window_hours"),
                        "total_after_dedupe": raw.get("total_after_dedupe"),
                        "sources_ok": sum(1 for s in raw.get("source_status") or [] if s.get("ok")),
                        "sources_total": len(raw.get("source_status") or []),
                    }
                except Exception:  # noqa: BLE001
                    pass
        apps.append(info)
    return {"apps": apps}


# ---------------------------------------------------------------- 结果留档
def docs_dir(key: str) -> Path:
    from . import store

    d = store.ROOT / "runs" / f"ext-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fmt_k(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{n / 1000:.1f}K" if n < 10000 else f"{n / 10000:.2f}万"


def _md_article(app: dict, data: dict) -> str:
    """P-02：把生成结果转成可复制的 Markdown。页面收起后靠它留档。"""
    art = data.get("article") or {}
    lines = [f"# {art.get('title') or '未命名'}", ""]
    if art.get("subtitle"):
        lines += [f"> {art['subtitle']}", ""]
    meta = " · ".join(
        str(x) for x in (data.get("vendor_name"), data.get("model"), data.get("image_plan_label")) if x
    )
    if meta:
        lines += [f"_{meta}_", ""]
    for i, s in enumerate(art.get("sections") or [], 1):
        lines += [f"## {i}. {s.get('heading') or ''}", ""]
        for p in s.get("content") or []:
            lines += [str(p), ""]
        if s.get("image_suggestion"):
            lines.append(f"**配图建议**：{s['image_suggestion']}")
            if s.get("image_source"):
                lines.append(f"**图片来源**：{s['image_source']}")
            lines.append("")
    lines += ["---", f"生成应用：{app['name']}"]
    return "\n".join(lines)


def _md_jobs(app: dict, data: dict) -> str:
    """招聘采集：把一轮分析压成一份可存档的报告。数字全部来自对方返回，不二次加工。"""
    an = data.get("analysis") or {}
    ov = an.get("overview") or {}
    sal = ov.get("salary") or {}
    q = data.get("query") or an.get("query") or {}
    jobs = data.get("jobs") or []
    meta = data.get("meta") or {}

    head = " · ".join(
        str(x)
        for x in (
            q.get("city"),
            "、".join(q.get("keywords") or []),
            f"月薪≥{_fmt_k(q['salary_min'])}" if q.get("salary_min") else "",
            q.get("edu"),
        )
        if x
    ) or "（未指定条件）"

    lines = [f"# 招聘 JD 采集分析 · {head}", ""]
    lines += [
        f"_{time.strftime('%Y-%m-%d %H:%M')} · {app['name']} · "
        f"样本 {ov.get('total', 0)} 条 / {ov.get('companies', 0)} 家公司 / {ov.get('cities', 0)} 个城市_",
        "",
    ]
    if data.get("reply"):
        lines += ["## 本轮结论", "", str(data["reply"]).strip(), ""]

    if sal.get("count"):
        lines += [
            "## 薪资分位（月薪）",
            "",
            "| 指标 | 数值 |",
            "| --- | --- |",
            f"| 中位数 P50 | {_fmt_k(sal.get('p50'))} |",
            f"| 均值 | {_fmt_k(sal.get('mean'))} |",
            f"| P25 – P75 | {_fmt_k(sal.get('p25'))} – {_fmt_k(sal.get('p75'))} |",
            f"| P10 – P90 | {_fmt_k(sal.get('p10'))} – {_fmt_k(sal.get('p90'))} |",
            f"| 区间 | {_fmt_k(sal.get('min'))} – {_fmt_k(sal.get('max'))} |",
            f"| 薪资覆盖 | {ov.get('salary_coverage', 0)}%（{sal.get('count')} 条） |",
            "",
        ]

    by_city = ((an.get("salary") or {}).get("byCity") or [])[:6]
    if by_city:
        lines += ["## 城市对比", "", "| 城市 | 岗位数 | 中位月薪 |", "| --- | --- | --- |"]
        for c in by_city:
            lines.append(f"| {c.get('name')} | {c.get('count')} | {_fmt_k(c.get('median'))} |")
        lines.append("")

    sk = (an.get("skills") or {}).get("top") or []
    if sk:
        lines += ["## 技能需求与溢价", "", "| 技能 | 提及 | 覆盖率 | 中位月薪 | 相对溢价 |", "| --- | --- | --- | --- | --- |"]
        for s in sk[:12]:
            lift = s.get("lift")
            lines.append(
                f"| {s.get('name')} | {s.get('count')} | {s.get('share')}% | "
                f"{_fmt_k(s.get('median'))} | {('+' + _fmt_k(lift)) if lift else '—'} |"
            )
        lines.append("")

    if jobs:
        lines += ["## 岗位明细（前 20）", "", "| 职位 | 公司 | 月薪 | 城市 | 经验 | 学历 |", "| --- | --- | --- | --- | --- | --- |"]
        for j in jobs[:20]:
            lines.append(
                f"| {j.get('title')} | {j.get('company')} | {j.get('salary')} | "
                f"{j.get('city')} | {j.get('exp') or '—'} | {j.get('edu') or '—'} |"
            )
        lines.append("")

    warn = data.get("warnings") or []
    if warn:
        lines += ["## 采集提示", ""] + [f"- {w}" for w in warn] + [""]

    lines += [
        "---",
        f"来源：智联招聘 / 猎聘 / 前程无忧（公开搜索结果） · 耗时 {meta.get('elapsed', '—')}s",
        f"生成应用：{app['name']}（{app['base_url']}）",
    ]
    return "\n".join(lines)


def _md_hotspot(app: dict, data: dict) -> str:
    """热点雷达：digest 本身就是 markdown（# 热点摘要 …），补上源状态与元信息。"""
    stats = data.get("stats") or {}
    src = data.get("sources") or []
    lines = [str(data.get("digest") or "").strip(), ""]
    ok = sum(1 for s in src if s.get("ok"))
    lines += [
        "---",
        f"_{time.strftime('%Y-%m-%d %H:%M')} · {app['name']} · 窗口 {data.get('hours')}h · "
        f"源 {ok}/{len(src)} 成功_",
    ]
    if src:
        lines += ["", "| 数据源 | 条数 | 状态 |", "| --- | --- | --- |"]
        lines += [
            f"| {s['name']} | {s['count']} | {'✓' if s['ok'] else '✗ ' + str(s.get('error') or '')} |"
            for s in src
        ]
    lines.append("")
    return "\n".join(lines)


_RENDERERS = {"p02": _md_article, "jobs": _md_jobs, "hotspot": _md_hotspot}


def _doc_title(key: str, data: dict) -> str:
    if key == "p02":
        return ((data.get("article") or {}).get("title") or "").strip()
    if key == "hotspot":
        return f"热点摘要·窗口{data.get('hours', '?')}h"
    q = data.get("query") or ((data.get("analysis") or {}).get("query") or {})
    parts = [q.get("city") or "", "、".join(q.get("keywords") or []) or "招聘采集"]
    return " ".join(p for p in parts if p).strip()


def save_doc(key: str, data: dict) -> dict | None:
    """把一次成功的调用落盘。页面只是显示器，磁盘才是留档。"""
    app = get_app(key)
    renderer = _RENDERERS.get(key)
    if not app or not renderer:
        return None
    if key == "jobs" and not (data.get("analysis") or {}).get("overview"):
        return None
    title = _doc_title(key, data)
    if not title:
        return None
    md = renderer(app, data)
    slug = "".join(c for c in title if c not in '\\/:*?"<>|').strip()[:40] or "untitled"
    ts = time.strftime("%Y%m%d-%H%M%S")
    target = docs_dir(key) / f"{ts}-{slug}.md"
    target.write_text(md, encoding="utf-8")
    from . import store

    return {
        "path": str(target.relative_to(store.ROOT)).replace("\\", "/"),
        "title": title,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chars": len(md),
    }


def list_docs(key: str, limit: int = 60) -> dict:
    """历史留档列表：扫 runs/ext-<key>/*.md，按时间倒序。"""
    if key not in EXTERNAL_APPS:
        return {"ok": False, "error": "没有这个外部应用"}
    from . import store

    root = store.ROOT
    items = []
    for p in sorted(docs_dir(key).glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        title = p.stem
        chars = 0
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            chars = len(text)
            first = text.split("\n", 1)[0].strip()
            if first.startswith("# "):
                title = first[2:].strip()
        except Exception:  # noqa: BLE001
            pass
        items.append(
            {
                "path": str(p.relative_to(root)).replace("\\", "/"),
                "title": title,
                "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                "chars": chars,
            }
        )
    return {"ok": True, "articles": items}
