"""汇总器：把多个节点的产物合成一份最终交付。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import store
from ..contracts import validate_aggregate
from ..graph import TaskGraph
from .base import call_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "aggregator.md"
MAX_PER_NODE = 3000


def _materials(graph: TaskGraph) -> list[dict]:
    out = []
    for n in graph.ordered():
        if n.status != "done":
            continue
        products = []
        for a in n.artifacts:
            p = store.ROOT / a["path"] if a.get("path") else None
            if p and p.exists():
                products.append(p.read_text(encoding="utf-8", errors="replace")[:MAX_PER_NODE])
        out.append(
            {
                "task_id": n.task_id,
                "title": n.title,
                "domain": n.domain,
                "goal": n.goal,
                "notes_for_downstream": n.notes_for_downstream,
                "products": products,
            }
        )
    return out


def run(graph: TaskGraph, run_id: str, *, trace: list[str] | None = None) -> dict:
    system = PROMPT_PATH.read_text(encoding="utf-8")
    user = (
        f"整体目标：{graph.goal}\n\n"
        f"已完成节点及其产物：\n{json.dumps(_materials(graph), ensure_ascii=False, indent=2)}\n\n"
        f"请合成一份最终交付。发现节点间矛盾就写进 conflicts，不要自行裁决。"
    )
    data = call_json(system, user, validate_aggregate, temperature=0.3, trace=trace)

    lines = [f"# {graph.goal}", ""]
    for s in data.get("sections", []):
        lines += [f"## {s.get('heading', '')}", "", s.get("content", ""), ""]
        src = "、".join(s.get("source_tasks", []))
        lines += [f"*来源：{src}*", ""]
    comp = data.get("completeness", {}) or {}
    if comp.get("missing"):
        lines += ["---", "", "## 未覆盖的内容", ""]
        for m in comp["missing"]:
            lines.append(f"- {m}")
        lines.append("")
    if data.get("conflicts"):
        lines += ["---", "", "## 待人工裁决的冲突", ""]
        for c in data["conflicts"]:
            lines.append(f"- [{'×'.join(c.get('between', []))}] {c.get('description', '')}")
            lines.append(f"  - 建议：{c.get('suggested_resolution', '')}")
        lines.append("")

    path = store.save_text(run_id, "final.md", "\n".join(lines))
    data["artifact_path"] = store.rel(path)
    store.save_json(run_id, "aggregate", data)
    return data
