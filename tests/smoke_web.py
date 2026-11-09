"""端到端冒烟：起服务 → 拆分 → G1 决策 → 执行 → G2 决策 → 汇总 → G3 决策。

跑法：
  python -m agentflow.server --mock --port 8760   # 另开一个终端
  python tests/smoke_web.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8760"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕开系统代理


def _q(path: str) -> str:
    # run_id 里含中文，必须转义
    return urllib.parse.quote(path, safe="/:?&=")


def get(path: str) -> dict:
    with OPENER.open(BASE + _q(path), timeout=30) as r:
        return json.loads(r.read())


def post(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        BASE + _q(path),
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with OPENER.open(req, timeout=60) as r:
        return json.loads(r.read())


def main() -> int:
    health = get("/api/health")
    print(f"服务：mock={health['mock']} gate_mode={health['gate_mode']} 并发={health['concurrency']}")

    before = {r["run_id"] for r in get("/api/runs")["runs"]}
    print("提交需求：", post("/api/run", {
        "requirement": "写一篇关于多 Agent 协作为什么难落地的公众号文章",
        "mock": True,
    }))

    run_id = ""
    decided: set[str] = set()
    for i in range(120):
        time.sleep(0.8)
        fresh = [r for r in get("/api/runs")["runs"] if r["run_id"] not in before]
        if not fresh:
            continue
        run = fresh[0]
        run_id = run["run_id"]
        gid = run["pending_gate"]
        if gid and gid not in decided:
            detail = get(f"/api/run/{run_id}")
            gate = next(g for g in detail["gates"] if g["gate_id"] == gid)
            print(f"[{i}] 闸门 {gid}｜{gate['prompt']}｜选项 {list(gate['options'])}"
                  f"｜payload {list(gate['payload'])}")
            post("/api/gate", {"run_id": run_id, "gate_id": gid, "decision": "a", "note": "冒烟自动批"})
            decided.add(gid)
            continue
        if run["status"] in ("done", "failed", "rejected") and not gid:
            print(f"[{i}] 结束：{run['status']}/{run['stage']} counts={run['counts']}")
            break

    if not run_id:
        print("[x] 没有产生新的 run")
        return 1

    detail = get(f"/api/run/{run_id}")
    print("\n事件流：")
    for ev in detail["state"]["history"]:
        extra = {k: v for k, v in ev.items() if k not in ("seq", "ts", "t", "event")}
        print(f"  {ev['seq']:>2} {ev['ts'][-8:]} {ev['event']:<14} {extra}")
    print(f"\n定稿长度 {len(detail['final'])} 字符；闸门 {len(detail['gates'])} 个；"
          f"trace {len(detail['trace'].splitlines())} 行")

    node_id = detail["graph"]["nodes"][0]["task_id"]
    node = get(f"/api/run/{run_id}/node/{node_id}")
    print(f"节点 {node_id}：产物 {[f['name'] for f in node['files']]}｜"
          f"self_check {len(node['result'].get('self_check', []))} 条")

    ok = detail["state"]["status"] == "done" and detail["final"]
    print("\n结果：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
