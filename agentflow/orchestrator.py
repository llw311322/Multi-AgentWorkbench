"""编排器：主循环 = 状态推进 + 路由 + 执行 + 监控 + 评审 + 闸门。

没有框架，也没有魔法。整张图的状态只存在 graph 对象里，
每一轮做三件事：找就绪节点、跑它们、把结果写回去。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from . import gates, monitor, router, store
from .agents import EXECUTORS, run_aggregator, run_reviewer, run_splitter
from .contracts import ContractError, RunState
from .graph import TaskGraph, TaskNode, render_table
from .llm import LLMError, MOCK
from .router import MAX_CONCURRENT

CODE_DOMAINS_GATE = ("content",)


def _hr(char: str = "-", n: int = 62) -> str:
    return char * n


def _head(text: str) -> None:
    print("\n" + _hr("="))
    print(f"  {text}")
    print(_hr("="))


def _node_line(node: TaskNode, msg: str) -> None:
    print(f"  [{node.task_id}] {node.domain:<9}{node.title[:18]:<20}{msg}", flush=True)


def _dump_trace(state: RunState, trace: list[str]) -> None:
    """把 LLM 调用轨迹落盘，页面「反馈」视图直接读它。"""
    if not trace:
        return
    try:
        store.save_text(state.run_id, "trace.log", "\n".join(trace))
    except Exception:
        pass


# ---------------------------------------------------------------- 单节点执行
def _execute(node: TaskNode, graph: TaskGraph, run_id: str, topic: str, trace: list[str]) -> dict:
    executor_name = router.route(node)
    if executor_name is None:
        return {
            "task_id": node.task_id,
            "status": "blocked",
            "artifacts": [],
            "self_check": [{"criterion": "route", "result": "fail", "evidence": "没有匹配的执行器"}],
            "open_questions": [f"未知领域 {node.domain}"],
            "tokens_used": 0,
            "notes_for_downstream": "",
        }
    executor = EXECUTORS[executor_name]
    ctx = monitor.context_for(node, graph, str(store.run_dir(run_id)), topic=topic)
    try:
        return executor(node, ctx, run_id, trace=trace)
    except (ContractError, LLMError) as e:
        return {
            "task_id": node.task_id,
            "status": "failed",
            "artifacts": [],
            "self_check": [{"criterion": "契约校验", "result": "fail", "evidence": str(e)[:300]}],
            "open_questions": ["执行器输出无法满足契约，需调整提示词或拆细节点"],
            "tokens_used": 0,
            "notes_for_downstream": "",
        }


def _review(node: TaskNode, result: dict, trace: list[str]) -> str:
    """返回 pass / revise / reject。评审器崩了就当 reject 转人工，不猜。"""
    try:
        data = run_reviewer(node, result, trace=trace)
    except (ContractError, LLMError) as e:
        node.review_notes.append(f"评审器失败，转人工：{str(e)[:200]}")
        return "reject"
    verdict = data.get("verdict", "reject")
    node.review_notes.append(
        f"{verdict}: " + str((data.get("blocking_issue") or {}).get("what", ""))[:160]
    )
    return verdict


# ---------------------------------------------------------------- 主循环
def _run_loop(state: RunState, graph: TaskGraph, *, auto: bool, trace: list[str]) -> RunState:
    round_no = 0
    while True:
        round_no += 1
        store.save_graph(state.run_id, graph)

        # 1. 依赖就绪 → ready
        for n in graph.ordered():
            if n.status == "pending" and graph.deps_done(n):
                n.status = "ready"

        # 2. 上游阻塞 → 下游阻塞（避免整张图悬在等待里）
        newly = monitor.propagate_blocked(graph)
        for tid in newly:
            _node_line(graph.nodes[tid], "上游未完成，标记 blocked")

        # 3. 全部完成 → 汇总
        if graph.all_done():
            return _finalize(state, graph, auto=auto, trace=trace)

        # 4. 卡死检测
        dead = monitor.detect_deadlock(graph)
        if dead:
            print(f"\n  [!] 死锁：{dead}")
            state.status = "failed"
            state.log("deadlock", detail=dead)
            store.save_state(state)
            return state

        # 5. 无可推进 + 有阻塞 → 停下来等人
        ready = [n for n in graph.ordered() if n.status == "ready"]
        blocked = graph.blocked_or_failed()
        if not ready:
            print("\n  [!] 没有可推进的节点，以下节点需要人工处理：")
            for n in blocked:
                print(f"      [{n.task_id}] {n.title} :: {'; '.join(n.open_questions[-1:])}")
            state.status = "blocked"
            state.log("blocked", nodes=[n.task_id for n in blocked])
            store.save_state(state)
            return state

        # 6. 取一批（串行模式就是一个）
        batch = router.pick_batch(ready)
        print(f"\n  第 {round_no} 轮 · 待执行 {len(ready)} 个 · 本轮 {len(batch)} 个"
              f"{'（并行）' if len(batch) > 1 else ''}")
        for n in batch:
            n.status = "running"
            _node_line(n, "开始")
            state.log("node_start", task_id=n.task_id, title=n.title, domain=n.domain,
                      round=round_no, attempts=n.attempts)
        store.save_graph(state.run_id, graph)

        t0 = time.time()
        if len(batch) > 1:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futures = {
                    pool.submit(_execute, n, graph, state.run_id, state.topic, trace): n
                    for n in batch
                }
            results = {futures[f].task_id: f.result() for f in futures}
        else:
            n = batch[0]
            results = {n.task_id: _execute(n, graph, state.run_id, state.topic, trace)}
        elapsed_ms = int((time.time() - t0) * 1000)

        # 7. 写回并推进
        for n in batch:
            result = results[n.task_id]
            store.save_task_json(state.run_id, n.task_id, "result", result)
            status = monitor.apply_result(n, result)
            _node_line(
                n,
                f"{status}  {time.time() - t0:.1f}s  {result.get('status')}"
                + (f"  重试 {n.attempts}" if n.flags and "retry" in n.flags[-1] else ""),
            )
            tokens = int(result.get("tokens_used") or 0)

            # 7a. 需要评审 → 独立模型判
            if n.status == "review":
                verdict = _review(n, result, trace)
                _node_line(n, f"评审 {verdict}")
                state.log("review", task_id=n.task_id, verdict=verdict,
                          note=n.review_notes[-1] if n.review_notes else "")
                if verdict == "pass":
                    n.status = "done"
                elif verdict == "revise":
                    monitor.retry(n, "评审要求修改")
                    state.log("retry", task_id=n.task_id, reason="review_revise", attempts=n.attempts)
                else:
                    n.status = "blocked"
                    n.open_questions.append("评审 reject，需人工介入")

            # 7b. 内容类过 G2
            if n.status == "done" and n.domain in CODE_DOMAINS_GATE and n.artifacts:
                verdict = gates.g2_publish(
                    n.task_id,
                    store.ROOT / n.artifacts[0]["path"],
                    result,
                    auto=auto,
                )
                state.log("gate", gate="G2", task_id=n.task_id, verdict=verdict)
                if verdict == "revise":
                    monitor.retry(n, "G2 退回重写")
                    state.log("retry", task_id=n.task_id, reason="g2_revise", attempts=n.attempts)
                elif verdict != "approved":
                    n.status = "blocked"
                    n.open_questions.append("G2 未通过，人工驳回")

            state.log(
                "node_finish",
                task_id=n.task_id,
                status=n.status,
                attempts=n.attempts,
                flags=n.flags[-2:],
                duration_ms=elapsed_ms,
                tokens_used=tokens,
            )
        store.save_state(state)
        store.save_graph(state.run_id, graph)
        _dump_trace(state, trace)


# ---------------------------------------------------------------- 收尾
def _finalize(state: RunState, graph: TaskGraph, *, auto: bool, trace: list[str]) -> RunState:
    _head("全部节点完成，开始汇总")
    state.stage = "aggregating"
    state.log("aggregate_start")
    store.save_state(state)
    try:
        agg = run_aggregator(graph, state.run_id, trace=trace)
    except (ContractError, LLMError) as e:
        print(f"  [x] 汇总失败：{e}")
        state.status = "failed"
        state.log("aggregate_failed", detail=str(e)[:300])
        store.save_state(state)
        return state

    final_path = store.ROOT / agg["artifact_path"]
    state.artifacts["final"] = agg["artifact_path"]
    print(f"  定稿：{final_path}")
    state.log(
        "aggregate",
        path=agg["artifact_path"],
        sections=len(agg.get("sections", [])),
        conflicts=len(agg.get("conflicts", []) or []),
        missing=len((agg.get("completeness") or {}).get("missing", []) or []),
    )

    verdict = gates.g3_deliver(final_path, agg, auto=auto)
    state.log("gate", gate="G3", verdict=verdict)
    if verdict == "approved":
        state.status = "done"
        state.stage = "approved"
        state.log("delivered", path=agg["artifact_path"])
    else:
        state.status = "rejected"
        state.stage = "rejected"
        state.log("g3_rejected")
        print("\n  已放弃定稿。各节点产物保留在 runs/ 下。")

    store.save_state(state)
    store.save_graph(state.run_id, graph)
    _dump_trace(state, trace)
    return state


# ---------------------------------------------------------------- 入口
def plan(requirement: str, *, auto: bool = False, trace: list[str] | None = None):
    """只拆分 + 过 G1，不执行。"""
    trace = trace if trace is not None else []
    _head(f"拆分需求{'（mock 模式）' if MOCK else ''}")
    print(f"  {requirement}\n")

    graph, questions = run_splitter(requirement, trace=trace)
    if graph is None:
        print("  [!] 需求信息不足，拆分器拒绝硬拆：")
        for q in questions:
            print(f"      - {q}")
        print("\n  先把这些问题回答清楚再拆。猜着拆出来的图，后面每一步都错。")
        return None

    state = store.new_run(requirement)
    state.stage = "planned"
    graph.goal = graph.goal or requirement
    path = store.save_graph(state.run_id, graph)
    gates.set_run(state.run_id)
    state.log("split", nodes=len(graph.nodes), goal=graph.goal, path=store.rel(path))
    store.save_state(state)

    accepted = gates.g1_plan(state.run_id, graph, path, auto=auto)
    if accepted is None:
        print("\n  已放弃。")
        state.status = "rejected"
        state.stage = "rejected"
        state.log("gate", gate="G1", verdict="rejected")
        store.save_state(state)
        return None

    state.log("gate", gate="G1", verdict="approved" if accepted is graph else "edited")
    store.save_graph(state.run_id, accepted)
    state.log("planned", nodes=len(accepted.nodes))
    store.save_state(state)
    print(f"\n  任务图已保存：{path}")
    print(f"  运行 ID：{state.run_id}")
    return state, accepted


def run(
    requirement: str,
    *,
    auto: bool = False,
    trace: list[str] | None = None,
) -> RunState | None:
    """拆分 → G1 → 执行 → 评审 → G2 → 汇总 → G3。"""
    planned = plan(requirement, auto=auto, trace=trace)
    if planned is None:
        return None
    state, graph = planned

    _head(f"开始执行 · 并发 {MAX_CONCURRENT}")
    state.status = "running"
    state.stage = "executing"
    state.log("run_start", concurrency=MAX_CONCURRENT)
    store.save_state(state)
    return _run_loop(state, graph, auto=auto, trace=trace or [])


def resume(run_id: str, *, auto: bool = False, trace: list[str] | None = None) -> RunState:
    """从上次中断的地方继续。"""
    state = store.load_state(run_id)
    graph = store.load_graph(run_id)
    # 上次崩在 running 的节点退回 ready，重新跑
    for n in graph.ordered():
        if n.status in ("running", "review"):
            n.status = "ready"
    _head(f"继续运行 {run_id}")
    print(render_table(graph))
    gates.set_run(run_id)
    state.status = "running"
    state.stage = "executing"
    state.log("resume")
    store.save_state(state)
    return _run_loop(state, graph, auto=auto, trace=trace or [])
