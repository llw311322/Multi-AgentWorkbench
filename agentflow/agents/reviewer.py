"""评审器：独立于执行器，只判定不修改。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import store
from ..contracts import validate_review
from ..graph import TaskNode
from .base import call_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "reviewer.md"
MAX_PRODUCT_CHARS = 6000


def _read_products(result: dict) -> str:
    chunks = []
    for a in result.get("artifacts", []):
        p = store.ROOT / a["path"] if a.get("path") else None
        if p and p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            chunks.append(f"--- {a.get('type')} ({a.get('path')}) ---\n{text[:MAX_PRODUCT_CHARS]}")
    return "\n\n".join(chunks) or "（没有可读产物）"


def run(node: TaskNode, result: dict, *, trace: list[str] | None = None) -> dict:
    system = PROMPT_PATH.read_text(encoding="utf-8")

    def _v(data: dict) -> list[str]:
        # task_id 由系统已知，模型漏写不算错
        data.setdefault("task_id", node.task_id)
        data.setdefault("rubric_scores", [])
        if not isinstance(data.get("acceptance_check"), list):
            data["acceptance_check"] = []
        return validate_review(data)

    user = (
        f"任务节点：{node.task_id} {node.title}\n"
        f"完成态：{node.goal}\n"
        f"验收标准：{json.dumps(node.acceptance, ensure_ascii=False)}\n"
        f"评审维度：{json.dumps(node.review_rubric or ['整体质量'], ensure_ascii=False)}\n"
        f"执行器自评（不可直接采信，需独立复核）："
        f"{json.dumps(result.get('self_check', []), ensure_ascii=False)}\n\n"
        f"产物内容：\n{_read_products(result)}\n\n"
        f"请独立复核并给出 verdict。"
    )
    return call_json(system, user, _v, temperature=0.1, trace=trace)
