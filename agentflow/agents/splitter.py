"""拆分器：需求 -> 任务图。"""
from __future__ import annotations

from pathlib import Path

from ..contracts import ContractError, validate_splitter
from ..graph import TaskGraph, validate_graph
from .base import call_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "splitter.md"


def _validator(data: dict) -> list[str]:
    problems = validate_splitter(data)
    if problems or data.get("status") == "blocked":
        return problems
    try:
        g = TaskGraph.from_dict(data.get("graph", {}))
    except Exception as e:
        return [f"graph 结构无法解析：{e}"]
    return validate_graph(g)


def run(requirement: str, *, trace: list[str] | None = None) -> tuple[TaskGraph | None, list[str]]:
    """返回 (任务图, 阻塞问题)。信息不足时图为 None。"""
    system = PROMPT_PATH.read_text(encoding="utf-8")
    user = (
        f"用户需求：\n{requirement}\n\n"
        f"请拆成任务图。注意：\n"
        f"- 节点 3–8 个，跨领域必须拆开\n"
        f"- 每个节点的 acceptance 只能用提示词表格里列出的类型\n"
        f"- 内容节点若需要分析结论，必须真的连 depends_on，不能靠「默认知道」\n"
    )
    data = call_json(system, user, _validator, temperature=0.2, trace=trace)

    if data.get("status") == "blocked":
        return None, data.get("blocking_questions", [])

    graph = TaskGraph.from_dict(data["graph"])
    problems = validate_graph(graph)
    if problems:
        raise ContractError("任务图结构不合法：\n- " + "\n- ".join(problems))
    return graph, []
