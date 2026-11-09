"""三个人工闸门。

闸门是代码，不是提示词。靠"模型记得停下来"是不可靠的，
物理上不继续执行才可靠。

两种工作模式：
  cli  —— 阻塞在 input() 上，等终端敲字（默认）
  web  —— 把待决事项写成 runs/<id>/gates/<gate_id>.json，轮询等页面写回 decision

web 模式不是"跳过闸门"，而是把同一个闸门搬到浏览器上。区别只在于
input() 换成了文件轮询，语义完全一致：不拿到 decision 就绝不往下走。
"""
from __future__ import annotations

import json
import os
import re
import time
import webbrowser
from pathlib import Path

from .graph import TaskGraph, render_table, validate_graph

AUTO = False

GATE_MODE = os.environ.get("AGENTFLOW_GATE_MODE", "cli").strip().lower()
GATE_TIMEOUT = int(os.environ.get("AGENTFLOW_GATE_TIMEOUT", "1800"))

# 当前编排线程所属的 run；web 模式写 gate 文件时要用
_CTX = {"run_id": ""}


def set_run(run_id: str) -> None:
    _CTX["run_id"] = run_id


def is_web() -> bool:
    return GATE_MODE == "web"


def _slug(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(text)).strip("-")[:40] or "gate"


def _ask(prompt: str, options: dict[str, str]) -> str:
    keys = "/".join(options)
    while True:
        try:
            choice = input(f"  {prompt} [{keys}] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  已中断。状态已保存，可用 resume 继续。")
            return "abort"
        if choice in options:
            return choice
        print(f"  无效输入，请输入 {' / '.join(options)}")


def _web_ask(gate_id: str, prompt: str, options: dict[str, str], payload: dict) -> str:
    """把闸门挂起，等页面点击。超时按 abort 处理——宁可不做，也不要静默放行。"""
    from . import store

    run_id = _CTX["run_id"]
    if not run_id:
        # 没有 run 上下文说明调用方忘了 set_run，退回终端，避免静默放行
        return _ask(prompt, options)

    record = {
        "gate_id": gate_id,
        "kind": gate_id.split("-")[0],
        "prompt": prompt,
        "options": options,
        "payload": payload,
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timeout_sec": GATE_TIMEOUT,
    }
    store.write_gate(run_id, gate_id, record)

    deadline = time.time() + GATE_TIMEOUT
    while time.time() < deadline:
        cur = store.read_gate(run_id, gate_id) or {}
        decision = cur.get("decision")
        if decision:
            cur["status"] = "decided"
            store.write_gate(run_id, gate_id, cur)
            return str(decision)
        time.sleep(0.35)

    record["status"] = "timeout"
    store.write_gate(run_id, gate_id, record)
    return "abort"


def _open(path: Path) -> None:
    try:
        webbrowser.open(path.as_uri())
    except Exception:
        pass


# ---------------------------------------------------------------- G1 拆分确认
def g1_plan(run_id: str, graph: TaskGraph, path: Path, *, auto: bool = False) -> TaskGraph | None:
    """G1：拆分结果确认。返回确认后的图，或 None 表示放弃。"""
    print(render_table(graph))
    set_run(run_id)

    if auto:
        print("\n  --auto：跳过 G1 确认")
        return graph

    if is_web():
        payload = {
            "graph": graph.to_dict(),
            "graph_path": str(path),
            "summary": f"{len(graph.nodes)} 个节点 / 关键路径 {' → '.join(graph.critical_path) or '未标注'}",
        }
        c = _web_ask("G1", "拆分结果确认：接受后开始执行", {"a": "接受", "e": "编辑", "q": "放弃"}, payload)
        if c in ("q", "abort"):
            return None
        # 页面可能已经改过 graph.json，所以无论 a 还是 e 都以磁盘上的为准
        try:
            new_graph = TaskGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None
        problems = validate_graph(new_graph)
        if problems:
            print("  [x] 页面编辑后的图校验未通过：")
            for p in problems:
                print(f"      - {p}")
            return None
        print("  [ok] 任务图校验通过（以页面保存的版本为准）。")
        print(render_table(new_graph))
        return new_graph

    print()
    print(f"  任务图：{path}")
    print("  选项：")
    print("    a  接受，开始执行")
    print("    e  编辑 graph.json（改标题/合并节点/调依赖），改完回来按回车")
    print("    q  放弃本次")

    while True:
        c = _ask("请选择：", {"a": "接受", "e": "编辑", "q": "放弃"})
        if c == "q" or c == "abort":
            return None
        if c == "a":
            return graph
        if c == "e":
            _open(path)
            print(f"\n  编辑：{path}")
            print("  改完保存后按回车，我会重新校验。")
            input("  > ")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                new_graph = TaskGraph.from_dict(data)
            except Exception as e:
                print(f"  [x] 解析失败：{e}\n  请修正后重新按回车。")
                input("  > ")
                continue
            problems = validate_graph(new_graph)
            if problems:
                print("  [x] 校验未通过：")
                for p in problems:
                    print(f"      - {p}")
                print("  继续编辑，或按 a 放弃这次编辑（保持原图）。")
                continue
            print("  [ok] 校验通过。")
            print(render_table(new_graph))
            return new_graph


# ---------------------------------------------------------------- G2 发布确认
_G2_SEQ = {"n": 0}


def g2_publish(node_id: str, draft_path: Path, article: dict, *, auto: bool = False) -> str:
    """G2：内容发布前确认。返回 approved / revise / rejected。"""
    titles = article.get("title_candidates") or []
    title = titles[0] if titles else article.get("title", "")
    body = article.get("body", "")
    facts = article.get("fact_check", [])
    comp = article.get("compliance") if isinstance(article.get("compliance"), dict) else {}
    flags = comp.get("flags", []) if comp else []
    unverified = [f for f in facts if f.get("verified") is False]

    print("\n" + "-" * 62)
    print(f"  闸门 G2　内容发布前确认　[{node_id}]")
    print("-" * 62)
    print(f"  标题：{title}")
    print(f"  字数：{len(body)}")
    print(f"  事实核查：{len(facts)} 条，其中 {len(unverified)} 条未核实")
    if unverified:
        for f in unverified[:5]:
            print(f"    ! {f.get('claim', '')[:50]}")
    if flags:
        print("  合规风险：")
        for f in flags:
            print(f"    ! {f}")
    print(f"\n  全文：{draft_path}")

    if auto:
        print("\n  --auto：跳过 G2（真实发布切勿使用）")
        return "approved"

    if is_web():
        _G2_SEQ["n"] += 1
        gate_id = f"G2-{_slug(node_id)}-{_G2_SEQ['n']}"
        try:
            draft_text = draft_path.read_text(encoding="utf-8")[:20000]
        except Exception:
            draft_text = ""
        payload = {
            "node_id": node_id,
            "title": title,
            "titles": titles,
            "words": len(body),
            "body": body[:20000],
            "draft_path": str(draft_path),
            "draft_text": draft_text,
            "facts": facts[:40],
            "facts_total": len(facts),
            "facts_unverified": [
                {"claim": f.get("claim", ""), "source": f.get("source", "")} for f in unverified[:10]
            ],
            "compliance_flags": flags,
        }
        c = _web_ask(
            gate_id,
            "发布前确认：通过后进入定稿汇总",
            {"a": "通过", "r": "退回重写", "q": "驳回"},
            payload,
        )
        if c == "a":
            return "approved"
        return "rejected" if c in ("q", "abort") else "revise"

    print("\n  选项：a 通过 / e 编辑后通过 / r 退回重写")
    c = _ask("请选择：", {"a": "通过", "e": "编辑", "r": "退回"})
    if c == "a":
        return "approved"
    if c == "e":
        _open(draft_path)
        print(f"\n  编辑：{draft_path}\n  改完保存后按回车。")
        input("  > ")
        return "approved"
    return "rejected" if c == "abort" else "revise"


# ---------------------------------------------------------------- G3 定稿确认
def g3_deliver(final_path: Path, aggregate: dict, *, auto: bool = False) -> str:
    """G3：交付/定稿前确认。"""
    print("\n" + "-" * 62)
    print("  闸门 G3　交付前确认")
    print("-" * 62)
    print(f"  定稿：{final_path}")

    comp = aggregate.get("completeness", {}) or {}
    covered = comp.get("covered_goals", []) or []
    missing = comp.get("missing", []) or []
    print(f"  覆盖目标：{len(covered)} 项")
    if missing:
        print("  未覆盖：")
        for m in missing:
            print(f"    ! {m}")

    conflicts = aggregate.get("conflicts", []) or []
    if conflicts:
        print("  节点间冲突：")
        for c in conflicts:
            between = "×".join(c.get("between", []))
            print(f"    ! [{between}] {c.get('description', '')}")
            print(f"      建议：{c.get('suggested_resolution', '')}")

    if auto:
        print("\n  --auto：跳过 G3")
        return "approved"

    if is_web():
        try:
            final_text = final_path.read_text(encoding="utf-8")[:40000]
        except Exception:
            final_text = ""
        payload = {
            "final_path": str(final_path),
            "final_text": final_text,
            "structure": aggregate.get("structure", []),
            "covered_goals": covered,
            "missing": missing,
            "conflicts": conflicts,
            "overview": aggregate.get("overview", ""),
        }
        c = _web_ask("G3", "定稿确认：接受后写入 final.md", {"a": "接受", "r": "放弃"}, payload)
        return "approved" if c == "a" else "rejected"

    print("\n  选项：a 接受定稿 / e 编辑后接受 / r 放弃")
    c = _ask("请选择：", {"a": "接受", "e": "编辑", "r": "放弃"})
    if c == "a":
        return "approved"
    if c == "e":
        _open(final_path)
        print(f"\n  编辑：{final_path}\n  改完保存后按回车。")
        input("  > ")
        return "approved"
    return "rejected" if c == "abort" else "revise"
