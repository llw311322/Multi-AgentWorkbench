"""路由器：按 domain 把节点派给执行器，并控制并发。

这 6 行映射就是「任务发布 / 分发 agent」的全部工作。
用 LLM 做这件事会得到错分率和无谓 token，收益为零。
"""
from __future__ import annotations

import os

from .graph import TaskNode

DOMAIN_EXECUTOR = {
    "code": "exec_code",
    "content": "exec_content",
    "research": "exec_research",
    # jobs 不调模型：它把节点转给本地的招聘采集服务，拿回真实岗位样本再变成分析产物
    "jobs": "exec_jobs",
}

# 单人本地：默认串行，避免多路同时烧 token 和互相干扰
MAX_CONCURRENT = int(os.environ.get("AGENTFLOW_CONCURRENCY", "1"))


def route(node: TaskNode) -> str | None:
    """返回执行器名；未知领域返回 None 并就地标记 blocked。"""
    ex = DOMAIN_EXECUTOR.get(node.domain)
    if ex is None:
        node.status = "blocked"
        node.open_questions.append(f"未知领域 {node.domain!r}，没有对应执行器")
        node.flags.append("route_failed")
    return ex


def pick_batch(ready: list[TaskNode], limit: int | None = None) -> list[TaskNode]:
    """从就绪节点里挑一批可并行的。

    规则：同一 parallel_group 才允许同批，避免把互相有依赖的两条链混跑。
    串行模式下永远只返回一个。
    """
    if not ready:
        return []
    cap = MAX_CONCURRENT if limit is None else limit
    if cap <= 1:
        return ready[:1]

    head = ready[0]
    batch = [head]
    for n in ready[1:]:
        if len(batch) >= cap:
            break
        if n.parallel_group and n.parallel_group == head.parallel_group:
            batch.append(n)
    return batch
