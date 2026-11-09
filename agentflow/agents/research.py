"""市场与热点分析 agent。"""
from __future__ import annotations

from pathlib import Path

from ..contracts import validate_research
from .base import call_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "research.md"


def run(topic: str, *, trace: list[str] | None = None) -> dict:
    system = PROMPT_PATH.read_text(encoding="utf-8")
    user = (
        f"选题：{topic}\n\n"
        f"请分析这个主题当前的热点状况，给出可用于内容创作的选题角度。\n"
        f"注意：标注为 fact 的结论必须附带 sources（url + date），否则会被系统拒收。"
    )
    return call_json(system, user, validate_research, temperature=0.2, trace=trace)
