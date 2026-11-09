"""产物库 + 运行状态持久化。

原则：产物落盘，下游只传引用和摘要，不传全量文本。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .contracts import RunState

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"

# 最近一次创建的 run：web 服务用它告诉页面「当前占着编排锁的是谁」
LAST_RUN = {"run_id": ""}


def _slug(text: str, limit: int = 24) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text).strip("-")
    return (cleaned[:limit] or "run").rstrip("-")


def new_run(topic: str) -> RunState:
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(topic)}"
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    state = RunState(run_id=run_id, topic=topic, created_at=now, updated_at=now)
    LAST_RUN["run_id"] = run_id
    save_state(state)
    return state


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def save_state(state: RunState) -> None:
    state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    p = run_dir(state.run_id) / "state.json"
    p.write_text(state.to_json(), encoding="utf-8")


def load_state(run_id: str) -> RunState:
    p = run_dir(run_id) / "state.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return RunState(**data)


def save_json(run_id: str, name: str, data: dict) -> Path:
    p = run_dir(run_id) / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def save_text(run_id: str, name: str, text: str) -> Path:
    p = run_dir(run_id) / name
    p.write_text(text, encoding="utf-8")
    return p


def read_text(run_id: str, name: str) -> str:
    return (run_dir(run_id) / name).read_text(encoding="utf-8")


def _is_run_dir(d: Path) -> bool:
    """runs/ 底下还住着 runs/ext-<key>/（外部 Agent 的存档），它们不是编排 Run。"""
    return d.is_dir() and not d.name.startswith("ext-")


def newest_run() -> str | None:
    if not RUNS_DIR.exists():
        return None
    dirs = sorted([d for d in RUNS_DIR.iterdir() if _is_run_dir(d)], key=lambda d: d.name)
    return dirs[-1].name if dirs else None


def list_runs() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted([d.name for d in RUNS_DIR.iterdir() if _is_run_dir(d)], reverse=True)


# ---------------------------------------------------------------- 任务图
def save_graph(run_id: str, graph) -> Path:
    p = run_dir(run_id) / "graph.json"
    p.write_text(
        json.dumps(graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return p


def load_graph(run_id: str):
    from .graph import TaskGraph

    p = run_dir(run_id) / "graph.json"
    return TaskGraph.from_dict(json.loads(p.read_text(encoding="utf-8")))


def graph_path(run_id: str) -> Path:
    return run_dir(run_id) / "graph.json"


# ---------------------------------------------------------------- 单节点产物
def task_dir(run_id: str, task_id: str) -> Path:
    d = run_dir(run_id) / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_task_json(run_id: str, task_id: str, name: str, data: dict) -> Path:
    p = task_dir(run_id, task_id) / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def save_task_text(run_id: str, task_id: str, name: str, text: str) -> Path:
    p = task_dir(run_id, task_id) / name
    p.write_text(text, encoding="utf-8")
    return p


def read_task_json(run_id: str, task_id: str, name: str) -> dict | None:
    p = run_dir(run_id) / "tasks" / task_id / f"{name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_task_files(run_id: str, task_id: str) -> list[dict]:
    """列出某节点的产物文件（给页面展示，不带全量文本）。"""
    d = run_dir(run_id) / "tasks" / task_id
    if not d.exists():
        return []
    out = []
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        out.append(
            {
                "name": f.name,
                "path": rel(f),
                "size": f.stat().st_size,
                "ext": f.suffix.lstrip(".").lower(),
            }
        )
    return out


# ---------------------------------------------------------------- 闸门（HITL）
def gate_dir(run_id: str) -> Path:
    d = run_dir(run_id) / "gates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def gate_path(run_id: str, gate_id: str) -> Path:
    return gate_dir(run_id) / f"{gate_id}.json"


def _atomic_write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def write_gate(run_id: str, gate_id: str, payload: dict) -> Path:
    p = gate_path(run_id, gate_id)
    _atomic_write(p, payload)
    return p


def read_gate(run_id: str, gate_id: str) -> dict | None:
    p = gate_path(run_id, gate_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def decide_gate(run_id: str, gate_id: str, decision: str, note: str = "") -> bool:
    """页面上点了按钮 → 写回决策。编排线程轮询这个字段。"""
    p = gate_path(run_id, gate_id)
    if not p.exists():
        return False
    data = read_gate(run_id, gate_id) or {}
    data["decision"] = decision
    data["note"] = note
    data["decided_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write(p, data)
    return True


def list_gates(run_id: str) -> list[dict]:
    d = run_dir(run_id) / "gates"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        data["gate_id"] = f.stem
        out.append(data)
    return out


def rel(path: Path) -> str:
    """转成相对项目根的路径，写进图里便于迁移。"""
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
