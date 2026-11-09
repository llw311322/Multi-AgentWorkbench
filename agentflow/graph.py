"""任务图：TaskNode / TaskGraph 及其校验。

TaskGraph 是分发、监控、重试共同操作的唯一真相源。
所有状态变更都通过它，不给旁路。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

DOMAINS = ("code", "content", "research", "jobs")
STATUSES = ("pending", "ready", "running", "review", "done", "failed", "blocked")
TERMINAL = ("done", "blocked")


@dataclass
class TaskNode:
    task_id: str
    title: str
    domain: str
    goal: str
    depends_on: list[str] = field(default_factory=list)
    acceptance: list[dict] = field(default_factory=list)
    review_rubric: list[str] = field(default_factory=list)
    est_tokens: int = 8000
    parallel_group: str = ""
    output_contract: dict = field(default_factory=dict)
    context_refs: list[str] = field(default_factory=list)

    status: str = "pending"
    attempts: int = 0
    artifacts: list[dict] = field(default_factory=list)
    self_check: list[dict] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    notes_for_downstream: str = ""
    flags: list[str] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TaskNode":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class TaskGraph:
    goal: str
    nodes: dict[str, TaskNode] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    critical_path: list[str] = field(default_factory=list)
    edges: list[list[str]] = field(default_factory=list)

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "assumptions": self.assumptions,
            "critical_path": self.critical_path,
            "edges": self.edges,
            "nodes": [n.to_dict() for n in self.nodes.values()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskGraph":
        nodes = {}
        for nd in d.get("nodes", []):
            n = TaskNode.from_dict(nd)
            nodes[n.task_id] = n
        return cls(
            goal=d.get("goal", ""),
            nodes=nodes,
            assumptions=d.get("assumptions", []),
            critical_path=d.get("critical_path", []),
            edges=d.get("edges", []),
        )

    def dump(self, path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------- 查询
    def ordered(self) -> list[TaskNode]:
        return list(self.nodes.values())

    def downstream(self, task_id: str) -> list[TaskNode]:
        return [n for n in self.nodes.values() if task_id in n.depends_on]

    def deps_done(self, node: TaskNode) -> bool:
        return all(self.nodes[d].status == "done" for d in node.depends_on if d in self.nodes)

    def stats(self) -> dict:
        out = {s: 0 for s in STATUSES}
        for n in self.nodes.values():
            out[n.status] = out.get(n.status, 0) + 1
        return out

    def all_done(self) -> bool:
        return all(n.status == "done" for n in self.nodes.values())

    def blocked_or_failed(self) -> list[TaskNode]:
        return [n for n in self.nodes.values() if n.status in ("blocked", "failed")]


# ---------------------------------------------------------------- 校验
def validate_graph(g: TaskGraph) -> list[str]:
    """结构性校验：id 唯一、依赖存在、无环、节点数合理、领域合法。"""
    problems: list[str] = []
    nodes = g.nodes

    if not g.goal.strip():
        problems.append("graph.goal 为空")
    if not nodes:
        problems.append("graph 没有任何节点")
        return problems

    for n in nodes.values():
        if n.domain not in DOMAINS:
            problems.append(f"{n.task_id}.domain={n.domain!r} 不合法，必须是 {DOMAINS} 之一")
        if not n.goal.strip():
            problems.append(f"{n.task_id}.goal 为空，节点必须有可判定的完成态")
        for d in n.depends_on:
            if d not in nodes:
                problems.append(f"{n.task_id} 依赖不存在的节点 {d}")
            if d == n.task_id:
                problems.append(f"{n.task_id} 依赖了自己")

    if len(nodes) > 8:
        problems.append(f"节点数 {len(nodes)} 超过 8，说明拆太细或需求过大，建议合并或分期")
    if len(nodes) < 2:
        problems.append(f"节点数 {len(nodes)}，一个节点不构成任务图")

    # 环检测
    color: dict[str, int] = {}

    def dfs(cur: str, stack: list[str]) -> list[str] | None:
        color[cur] = 1
        for d in nodes[cur].depends_on:
            if d not in nodes:
                continue
            if color.get(d) == 1:
                return stack + [cur, d]
            if color.get(d, 0) == 0:
                found = dfs(d, stack + [cur])
                if found:
                    return found
        color[cur] = 2
        return None

    for tid in nodes:
        if color.get(tid, 0) == 0:
            cycle = dfs(tid, [])
            if cycle:
                problems.append("存在环：" + " -> ".join(cycle))
                break
    return problems


def topo_order(g: TaskGraph) -> list[str]:
    """拓扑序，仅用于展示与关键路径计算。"""
    indeg = {tid: len([d for d in n.depends_on if d in g.nodes]) for tid, n in g.nodes.items()}
    ready = [tid for tid, v in indeg.items() if v == 0]
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for child in g.downstream(cur):
            indeg[child.task_id] -= 1
            if indeg[child.task_id] == 0:
                ready.append(child.task_id)
    return order


def render_table(g: TaskGraph) -> str:
    """给 G1 闸门看的表格。"""
    order = topo_order(g) or list(g.nodes)
    lines = ["", f"  目标：{g.goal}", ""]
    lines.append("  " + "-" * 78)
    lines.append(f"  {'ID':<7}{'领域':<10}{'状态':<8}{'依赖':<12}标题")
    lines.append("  " + "-" * 78)
    for tid in order:
        n = g.nodes[tid]
        deps = ",".join(n.depends_on) or "—"
        lines.append(f"  {n.task_id:<7}{n.domain:<10}{n.status:<8}{deps:<12}{n.title}")
    lines.append("  " + "-" * 78)
    if g.assumptions:
        lines.append("")
        lines.append("  拆分时做的假设（重点核对）：")
        for a in g.assumptions:
            lines.append(f"    - {a}")
    return "\n".join(lines)
