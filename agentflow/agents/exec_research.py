"""市场与分析执行器：产出带来源、带时间戳的分析报告。"""
from __future__ import annotations

import json
from pathlib import Path

from .. import store
from ..contracts import validate_research
from ..graph import TaskNode
from .base import ENVELOPE_NOTE, build_result, call_json, combined

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "research.md"


def _render_md(data: dict) -> str:
    lines = ["# 分析报告", ""]
    lines.append("## 关键结论")
    for i, f in enumerate(data.get("key_findings", []), 1):
        kind = {"fact": "事实", "inference": "推断", "opinion": "观点"}.get(f.get("type"), "?")
        lines.append(f"{i}. **[{kind}]** {f.get('finding', '')}　（置信度 {f.get('confidence', '?')}）")
        for s in f.get("sources", []):
            lines.append(f"   - 来源：{s.get('publisher', '')} {s.get('date', '')} {s.get('url', '')}")
    lines += ["", "## 可写的选题角度"]
    for a in data.get("content_angles", []):
        lines.append(f"- **{a.get('angle', '')}**")
        lines.append(f"  - 为什么是现在：{a.get('why_now', '')}")
        lines.append(f"  - 目标读者：{a.get('target_audience', '')}")
        lines.append(f"  - 风险：{a.get('risk_note', '')}")
    df = data.get("data_freshness", {}) or {}
    lines += ["", "## 时效", f"- 最旧来源：{df.get('oldest_source_date', '未标注')}"]
    for s in df.get("stale_items", []):
        lines.append(f"- 可能过期：{s}")
    return "\n".join(lines)


def run(node: TaskNode, ctx: dict, run_id: str, *, trace: list[str] | None = None) -> dict:
    system = PROMPT_PATH.read_text(encoding="utf-8") + ENVELOPE_NOTE
    user = (
        f"选题：{ctx.get('topic', '')}\n"
        f"任务节点：{node.title}\n"
        f"完成态：{node.goal}\n"
        f"验收标准：{json.dumps(node.acceptance, ensure_ascii=False)}\n\n"
        f"上游产物：{json.dumps(ctx.get('upstream', []), ensure_ascii=False)}\n\n"
        f"请完成这次分析。标注为 fact 的结论必须附 sources（url + date），否则会被系统拒收。"
    )
    data = call_json(system, user, combined(validate_research), temperature=0.2, trace=trace)

    if data.get("status") == "blocked":
        return build_result(node, data, artifact_type="report")

    json_path = store.save_task_json(run_id, node.task_id, "report", data)
    md_path = store.save_task_text(run_id, node.task_id, "report.md", _render_md(data))
    findings = len(data.get("key_findings", []))
    return build_result(
        node,
        data,
        artifact_type="report",
        artifact_path=md_path,
        summary=f"{findings} 条结论 / {len(data.get('content_angles', []))} 个角度",
        raw_text=json.dumps(data, ensure_ascii=False),
    )
