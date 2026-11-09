"""工作台常驻进程管理：start / stop / status / restart / logs。

为什么需要它：`python -m agentflow.server` 是前台进程，终端一关、SSH 一断、
父进程被杀（Windows 上常见 exit code 1073807364），服务和正在跑的编排一起消失。
编排状态虽然落盘了，但没人把它标成「中断」，页面就会一直显示执行中。

这里做的事很朴素：把 server 拉成一个脱离控制台的独立进程，写 pid 与日志，
提供查询和停止。不引入 nssm / pywin32 这类依赖，Windows 与 POSIX 都能跑。

用法：
  python -m agentflow.daemon start --mock      # 后台起（mock 模式）
  python -m agentflow.daemon status            # 活着吗、什么模式、几个 run
  python -m agentflow.daemon stop              # 停（连子进程树一起）
  python -m agentflow.daemon restart --mock
  python -m agentflow.daemon logs -n 60        # 看最近 60 行
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
PID_FILE = LOG_DIR / "server.pid"
LOG_FILE = LOG_DIR / "server.log"
LOG_MAX = 5 * 1024 * 1024  # 5MB 换一个文件，别让日志吃满磁盘


# ---------------------------------------------------------------- 基础探针
def _health(port: int, timeout: float = 1.5) -> dict | None:
    """本地地址必须绕开系统代理，否则会被代理吞成 502。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_from_port(port: int) -> int:
    """手工在终端里起的 server 没有 pidfile，靠端口反查，stop 才停得掉。"""
    try:
        if os.name == "nt":
            # netstat 输出是 GBK/OEM 编码，按 utf-8 解会直接抛 UnicodeDecodeError，
            # 所以拿 bytes 再宽容解码
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"], capture_output=True, timeout=8
            ).stdout.decode("utf-8", "replace")
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
                    return int(parts[4])
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, timeout=8
            ).stdout.decode("utf-8", "replace")
            if out.strip():
                return int(out.split()[0])
    except Exception:  # noqa: BLE001
        pass
    return 0


def _read_pid() -> int:
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        return 0


def _write_pid(pid: int) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _rotate_log() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    except Exception:  # noqa: BLE001
        pass


def _pythonw() -> str:
    """pythonw 不挂控制台：任务计划/启动文件夹拉起时不会弹黑窗。"""
    exe = Path(sys.executable)
    if os.name == "nt":
        cand = exe.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


# ---------------------------------------------------------------- 命令
def start(port: int, mock: bool) -> int:
    h = _health(port)
    if h:
        pid = _read_pid() or _pid_from_port(port)
        print(f"已在运行：pid={pid or '?'}  http://127.0.0.1:{port}/  "
              f"({'mock' if h.get('mock') else h.get('model', '真实模型')})")
        return 0

    pid = _read_pid()
    if pid and _pid_alive(pid):
        print(f"pid={pid} 还活着但健康检查不通，先 stop 再 start；或看 logs")
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_log()

    cmd = [_pythonw(), "-m", "agentflow.server", "--port", str(port)]
    if mock:
        cmd.append("--mock")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"      # 日志里有中文，编码不对会直接抛
    env["PYTHONUNBUFFERED"] = "1"

    flags = 0
    if os.name == "nt":
        # DETACHED_PROCESS：脱离父控制台；CREATE_NEW_PROCESS_GROUP：便于整棵树一起结束
        flags = 0x00000008 | 0x00000200

    with open(LOG_FILE, "ab") as log:
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start {' '.join(cmd)}\n".encode("utf-8"))
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=env,
            creationflags=flags,
            close_fds=True,
        )

    _write_pid(proc.pid)

    deadline = time.time() + 20
    while time.time() < deadline:
        if _health(port):
            print(f"已启动：pid={proc.pid}  http://127.0.0.1:{port}/")
            print(f"日志：{LOG_FILE}")
            return 0
        if proc.poll() is not None:
            print(f"启动失败（进程已退出，exit={proc.returncode}），日志尾部：")
            _logs(25)
            return 1
        time.sleep(0.4)

    print(f"进程在跑（pid={proc.pid}）但 20s 内健康检查未通过，看日志：{LOG_FILE}")
    return 1


def stop(port: int) -> int:
    pid = _read_pid() or _pid_from_port(port)
    if not pid or not _pid_alive(pid):
        print("没在运行")
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return 0

    if os.name == "nt":
        # /T 连子进程一起（内嵌的 p02 / jobs / market 都是它拉起来的）
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass

    deadline = time.time() + 15
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.3)

    if _pid_alive(pid):
        print(f"pid={pid} 还在，可能需要管理员权限")
        return 1

    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    print(f"已停止（pid={pid}）")
    return 0


def status(port: int) -> int:
    pid = _read_pid() or _pid_from_port(port)
    h = _health(port)
    if not h:
        print("状态：未运行" + (f"（残留 pid={pid}）" if pid else ""))
        return 1

    print(f"状态：运行中   pid={pid or '?'}   http://127.0.0.1:{port}/")
    print(f"模式：{'mock（假数据）' if h.get('mock') else h.get('model', '真实模型')}"
          f"   并发：{h.get('concurrency')}   闸门超时：{h.get('gate_timeout')}s")

    runs = _api(port, "/api/runs") or {}
    rows = runs.get("runs") or []
    if rows:
        alive = [r for r in rows if r.get("status") == "running"]
        intr = [r for r in rows if r.get("status") == "interrupted"]
        print(f"Run：共 {len(rows)} 个，执行中 {len(alive)}，中断待续跑 {len(intr)}")
        if intr:
            for r in intr[:3]:
                print(f"  · {r.get('run_id')}  （看板点「继续 / 续跑」）")
    return 0


def _api(port: int, path: str) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def _logs(n: int) -> None:
    if not LOG_FILE.exists():
        print("还没有日志（服务没通过 daemon 启动过）")
        return
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-n:]:
        print(line)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentflow.daemon", description="工作台常驻进程管理")
    p.add_argument("cmd", choices=["start", "stop", "status", "restart", "logs"])
    p.add_argument("--port", type=int, default=8760)
    p.add_argument("--mock", action="store_true", help="mock 模式（不调模型）")
    p.add_argument("-n", type=int, default=40, help="logs 显示行数")
    args = p.parse_args(argv)

    if args.cmd == "start":
        return start(args.port, args.mock)
    if args.cmd == "stop":
        return stop(args.port)
    if args.cmd == "status":
        return status(args.port)
    if args.cmd == "restart":
        stop(args.port)
        time.sleep(0.8)
        return start(args.port, args.mock)
    _logs(args.n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
