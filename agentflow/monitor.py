"""监控器：契约校验、验收判定、状态推进。

全部是确定性逻辑。这里一旦引入 LLM，系统的可复现性就没了。
"""
from __future__ import annotations

import re

from .graph import TaskGraph, TaskNode

MAX_ATTEMPTS = 2

FACTUAL_TYPES = {"fact"}


# ---------------------------------------------------------------- 验收项
def _payload(result: dict) -> dict:
    """领域字段与信封字段平铺在同一层，这里直接返回整份结果。"""
    p = result.get("payload")
    return p if isinstance(p, dict) else result


CHECKERS = {
    # 信封结构已由 validate_exec_result 保证，到这里必然是合法的
    "schema_valid": lambda p, c: (True, "信封结构校验通过"),
    "min_words": lambda p, c: (
        len(p.get("body", "")) >= c.get("min", 0),
        f"正文 {len(p.get('body', ''))} 字 / 要求 ≥{c.get('min', 0)}",
    ),
    "title_count": lambda p, c: (
        len(p.get("title_candidates", [])) >= c.get("min", 1),
        f"标题 {len(p.get('title_candidates', []))} 个 / 要求 ≥{c.get('min', 1)}",
    ),
    "cites_sources": lambda p, c: (
        len([f for f in p.get("key_findings", []) if f.get("sources")]) >= c.get("min", 1),
        f"有来源的结论 {len([f for f in p.get('key_findings', []) if f.get('sources')])} 条 / "
        f"要求 ≥{c.get('min', 1)}",
    ),
    "fact_check_present": lambda p, c: (
        bool(p.get("fact_check")),
        f"事实核查 {len(p.get('fact_check', []))} 条",
    ),
    "no_unverified": lambda p, c: (
        not [x for x in p.get("fact_check", []) if x.get("verified") is False],
        f"未核实条目 {len([x for x in p.get('fact_check', []) if x.get('verified') is False])} 条",
    ),
    "max_files_changed": lambda p, c: (
        len(p.get("files_changed", [])) <= c.get("max", 5),
        f"改动 {len(p.get('files_changed', []))} 个文件 / 上限 {c.get('max', 5)}",
    ),
    "tests_passed": lambda p, c: (
        p.get("tests_passed") is True,
        f"tests_passed={p.get('tests_passed')}",
    ),
}


def check_acceptance(node: TaskNode, result: dict) -> tuple[list[str], list[str]]:
    """返回 (不通过的项, 无法自动校验的项)。

    无法自动校验的项不算失败——而是转给评审器判断，这是设计上的分工。
    """
    failures: list[str] = []
    unchecked: list[str] = []
    p = _payload(result)

    for crit in node.acceptance:
        ctype = crit.get("type")
        checker = CHECKERS.get(ctype)
        if checker is None:
            unchecked.append(f"未知验收类型 {ctype!r}，转人工/评审判断")
            continue
        try:
            ok, evidence = checker(p, crit)
        except Exception as e:  # 自定义字段缺失也不该让整条链崩
            ok, evidence = False, f"校验异常：{e}"
        if not ok:
            failures.append(f"验收未通过 [{ctype}] {evidence}")
    return failures, unchecked


# ---------------------------------------------------------------- 状态推进
def retry(node: TaskNode, reason: str) -> None:
    node.attempts += 1
    node.flags.append(f"retry:{reason}")
    if node.attempts >= MAX_ATTEMPTS:
        node.status = "blocked"
        node.open_questions.append(f"重试 {node.attempts} 次仍未通过：{reason}")
    else:
        node.status = "ready"


def apply_result(node: TaskNode, result: dict) -> str:
    """把执行器返回落到节点上，返回节点新状态。"""
    node.artifacts = result.get("artifacts", [])
    node.self_check = result.get("self_check", [])
    node.open_questions = result.get("open_questions", [])
    node.notes_for_downstream = result.get("notes_for_downstream", "")

    # 预算校验：超了只标记，不阻断（阻断会让长文任务永远过不去）
    if result.get("tokens_used", 0) > node.est_tokens * 1.5:
        node.flags.append("over_budget")

    status = result.get("status")

    if status == "blocked":
        node.status = "blocked"
        node.flags.append("executor_blocked")
        return node.status

    if status == "failed":
        retry(node, "执行器返回 failed")
        return node.status

    failures, unchecked = check_acceptance(node, result)
    if unchecked:
        node.flags.extend(f"unchecked:{u}" for u in unchecked)
    if failures:
        retry(node, "; ".join(failures))
        return node.status

    # partial 也算通过验收，但必须走评审；有无法自动校验的验收项时同样转评审
    if status == "partial":
        node.flags.append("partial")

    if node.review_rubric or status == "partial" or unchecked:
        node.status = "review"
    else:
        node.status = "done"
    return node.status


def propagate_blocked(graph: TaskGraph) -> list[str]:
    """blocked / failed 节点的下游一律标记 blocked，避免整张图悬在等待里。"""
    changed: list[str] = []
    dirty = True
    while dirty:
        dirty = False
        for n in graph.ordered():
            if n.status in ("blocked", "failed"):
                continue
            bad_deps = [
                d for d in n.depends_on
                if d in graph.nodes and graph.nodes[d].status in ("blocked", "failed")
            ]
            if bad_deps:
                n.status = "blocked"
                n.flags.append(f"upstream_blocked:{','.join(bad_deps)}")
                n.open_questions.append(f"上游 {','.join(bad_deps)} 未完成，本节点无法执行")
                changed.append(n.task_id)
                dirty = True
    return changed


def detect_deadlock(graph: TaskGraph) -> str | None:
    """没有任何节点可推进，但整张图又没跑完 → 说明卡死了。"""
    if graph.all_done():
        return None
    pending = [n for n in graph.ordered() if n.status == "pending"]
    running = [n for n in graph.ordered() if n.status in ("ready", "running", "review")]
    if running:
        return None
    if pending:
        ids = ", ".join(n.task_id for n in pending)
        return f"这些节点永远等不到依赖完成：{ids}（可能是环或依赖被跳过）"
    return None


# ---------------------------------------------------------------- 上下文裁剪
def summarize_text(text: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def context_for(node: TaskNode, graph: TaskGraph, run_root: str, topic: str = "") -> dict:
    """只给下游必要上下文：上游产物摘要 + 一句交接说明，不给全量文本。"""
    ups = []
    for d in node.depends_on:
        dep = graph.nodes.get(d)
        if not dep:
            continue
        ups.append(
            {
                "task_id": dep.task_id,
                "title": dep.title,
                "domain": dep.domain,
                "artifacts": [
                    {"type": a.get("type"), "path": a.get("path")} for a in dep.artifacts
                ],
                "notes_for_downstream": summarize_text(dep.notes_for_downstream, 300),
                "open_questions": dep.open_questions[:5],
            }
        )
    return {
        "task_id": node.task_id,
        "title": node.title,
        "goal": node.goal,
        "domain": node.domain,
        "topic": topic,
        "acceptance": node.acceptance,
        "review_rubric": node.review_rubric,
        "upstream": ups,
        "run_root": run_root,
    }
