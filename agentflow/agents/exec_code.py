"""代码执行器。

注意：这个执行器默认不真的改仓库——它产出改动方案与自检结论。
真要落盘需要显式打开 AGENTFLOW_CODE_APPLY=1，且只走补丁文件，不直接写源码。
这是刻意的：本地工具误改仓库的代价远高于多一步确认。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import store
from ..graph import TaskNode
from .base import ENVELOPE_NOTE, build_result, call_json, combined

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "code.md"
APPLY = os.environ.get("AGENTFLOW_CODE_APPLY") == "1"


def _domain(data: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(data.get("files_changed", []), list):
        problems.append("files_changed 必须是数组（没改文件就给空数组）")
    if "tests_passed" not in data:
        problems.append("tests_passed 必须明确给出 true/false，没跑测试写 null")
    elif data["tests_passed"] is not None and not isinstance(data["tests_passed"], bool):
        problems.append("tests_passed 必须是 true / false / null")
    if not isinstance(data.get("diff_summary", ""), str):
        problems.append("diff_summary 必须是字符串")
    return problems


def _render_md(node: TaskNode, data: dict) -> str:
    lines = [f"# {node.title}", "", f"目标：{node.goal}", ""]
    lines.append("## 改动")
    lines.append(data.get("diff_summary", "（无）"))
    changed = data.get("files_changed", [])
    if changed:
        lines.append("")
        for f in changed:
            lines.append(f"- {f}")
    lines += ["", "## 测试", f"tests_passed = {data.get('tests_passed')}", "",
              "```", (data.get("test_output") or "（未运行）")[:2000], "```"]
    lines += ["", "## 自检"]
    for c in data.get("self_check", []):
        lines.append(f"- [{c.get('result')}] {c.get('criterion')} — {c.get('evidence')}")
    return "\n".join(lines)


def run(node: TaskNode, ctx: dict, run_id: str, *, trace: list[str] | None = None) -> dict:
    system = PROMPT_PATH.read_text(encoding="utf-8") + ENVELOPE_NOTE
    user = (
        f"选题：{ctx.get('topic', '')}\n"
        f"任务节点：{node.title}\n"
        f"完成态：{node.goal}\n"
        f"验收标准：{json.dumps(node.acceptance, ensure_ascii=False)}\n"
        f"仓库根目录：{ctx.get('run_root', '')}\n\n"
        f"上游产物：{json.dumps(ctx.get('upstream', []), ensure_ascii=False)}\n\n"
        f"{'请给出可应用的补丁方案。' if not APPLY else '允许写入源码，但只改范围内文件。'}"
    )
    data = call_json(system, user, combined(_domain), temperature=0.2, trace=trace)

    if data.get("status") == "blocked":
        return build_result(node, data, artifact_type="code_patch")

    store.save_task_json(run_id, node.task_id, "code_result", data)
    md_path = store.save_task_text(run_id, node.task_id, "change.md", _render_md(node, data))
    return build_result(
        node,
        data,
        artifact_type="code_patch",
        artifact_path=md_path,
        summary=f"改动 {len(data.get('files_changed', []))} 个文件，tests_passed={data.get('tests_passed')}",
        raw_text=json.dumps(data, ensure_ascii=False),
    )
