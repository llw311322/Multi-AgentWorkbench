"""内容执行器：为公众号 / 头条产出可发布稿件。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import store
from ..contracts import validate_article
from ..graph import TaskNode
from .base import ENVELOPE_NOTE, build_result, call_json, combined
from .content import render_markdown

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "content.md"


def run(node: TaskNode, ctx: dict, run_id: str, *, trace: list[str] | None = None) -> dict:
    system = PROMPT_PATH.read_text(encoding="utf-8") + ENVELOPE_NOTE
    user = (
        f"选题：{ctx.get('topic', '')}\n"
        f"任务节点：{node.title}\n"
        f"完成态：{node.goal}\n"
        f"验收标准：{json.dumps(node.acceptance, ensure_ascii=False)}\n"
        f"平台：{ctx.get('platform', '微信公众号')}\n\n"
        f"上游产物摘要（只能基于这些素材写，不要新增未提供的事实）：\n"
        f"{json.dumps(ctx.get('upstream', []), ensure_ascii=False, indent=2)}\n\n"
        f"请写成可直接发布的稿件。"
    )
    data = call_json(system, user, combined(validate_article), temperature=0.6, trace=trace)

    if data.get("status") == "blocked":
        return build_result(node, data, artifact_type="article")

    store.save_task_json(run_id, node.task_id, "article", data)
    md_path = store.save_task_text(run_id, node.task_id, "draft.md", render_markdown(data))
    return build_result(
        node,
        data,
        artifact_type="article",
        artifact_path=md_path,
        summary=f"约 {len(data.get('body', ''))} 字，{len(data.get('title_candidates', []))} 个标题",
        raw_text=json.dumps(data, ensure_ascii=False),
    )
