"""内容执行器：为公众号 / 头条产出稿件。"""
from __future__ import annotations

import json
from pathlib import Path

from ..contracts import validate_article
from .base import call_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "content.md"


def run(topic: str, research: dict, *, platform: str = "微信公众号", trace: list[str] | None = None) -> dict:
    system = PROMPT_PATH.read_text(encoding="utf-8")

    # 只传必要上下文：结论 + 选题角度，不传全量分析过程
    brief = {
        "key_findings": [
            {"finding": f["finding"], "type": f.get("type"), "confidence": f.get("confidence")}
            for f in research.get("key_findings", [])
        ],
        "content_angles": [
            {"angle": a.get("angle"), "why_now": a.get("why_now")}
            for a in research.get("content_angles", [])
        ],
    }

    user = (
        f"选题：{topic}\n"
        f"平台：{platform}\n\n"
        f"上游分析产出（只能基于这些素材写，不要新增未提供的事实）：\n"
        f"{json.dumps(brief, ensure_ascii=False, indent=2)}\n\n"
        f"请选择其中一个角度写成可直接发布的稿件。"
    )
    return call_json(system, user, validate_article, temperature=0.6, trace=trace)


def render_markdown(article: dict) -> str:
    """把结构化稿件渲染成可读的 draft.md，供人工在闸门审阅和直接编辑。"""
    lines = ["# 候选标题", ""]
    for i, t in enumerate(article.get("title_candidates", []), 1):
        lines.append(f"{i}. {t}")
    lines += ["", "---", "", "# 正文", ""]
    lines.append(article.get("body", ""))
    lines += ["", "---", "", "## 事实核查", "", "| 表述 | 来源 | 日期 | 已核实 |", "|---|---|---|---|"]
    for c in article.get("fact_check", []):
        src = c.get("source_url") or "—"
        date = c.get("source_date") or "—"
        ok = "是" if c.get("verified") else "**否（需人工确认）**"
        lines.append(f"| {c.get('claim', '')} | {src} | {date} | {ok} |")
    comp = article.get("compliance", {})
    lines += ["", "## 合规自查", ""]
    for item in comp.get("checked", []):
        lines.append(f"- [x] {item}")
    flags = comp.get("flags", [])
    if flags:
        lines += ["", "### ⚠ 风险标记", ""]
        for f in flags:
            lines.append(f"- {f}")
    lines += ["", f"字数：约 {len(article.get('body', ''))} 字", ""]
    return "\n".join(lines)
