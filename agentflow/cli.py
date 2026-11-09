"""命令行入口。

单链：
  python -m agentflow.cli new "选题" --mock

全流程（拆分 → G1 → 执行 → 评审 → G2 → 汇总 → G3）：
  python -m agentflow.cli plan "需求" --mock      # 只拆分并过 G1
  python -m agentflow.cli run  "需求" --mock      # 跑完全程
  python -m agentflow.cli resume <run_id>         # 从中断处继续

其他：
  python -m agentflow.cli show [run_id]
  python -m agentflow.cli list
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _fix_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    parser = argparse.ArgumentParser(
        prog="agentflow", description="多 Agent 协作：拆分 → 路由 → 执行 → 评审 → 汇总"
    )
    sub = parser.add_subparsers(dest="cmd")

    p_plan = sub.add_parser("plan", help="只拆分需求并过 G1 闸门")
    p_plan.add_argument("requirement", help="一句话需求")
    p_plan.add_argument("--mock", action="store_true", help="用假数据跑通流程，不调用模型")
    p_plan.add_argument("--auto", action="store_true", help="跳过人工闸门（仅调试用）")

    p_run = sub.add_parser("run", help="拆分 → 执行 → 汇总，跑完全程")
    p_run.add_argument("requirement", help="一句话需求")
    p_run.add_argument("--mock", action="store_true")
    p_run.add_argument("--auto", action="store_true", help="跳过全部人工闸门（仅调试用）")

    p_resume = sub.add_parser("resume", help="从中断处继续")
    p_resume.add_argument("run_id", nargs="?")
    p_resume.add_argument("--mock", action="store_true")
    p_resume.add_argument("--auto", action="store_true")

    p_new = sub.add_parser("new", help="【单链】分析→撰写→G2，不拆分")
    p_new.add_argument("topic")
    p_new.add_argument("--platform", default="微信公众号")
    p_new.add_argument("--mock", action="store_true")
    p_new.add_argument("--auto", action="store_true")

    p_show = sub.add_parser("show", help="查看运行状态与任务图")
    p_show.add_argument("run_id", nargs="?")

    sub.add_parser("list", help="列出所有运行")

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0

    if getattr(args, "mock", False):
        os.environ["AGENTFLOW_MOCK"] = "1"

    if args.cmd == "list":
        from . import store

        runs = store.list_runs()
        if not runs:
            print("还没有任何运行记录。")
            return 0
        for r in runs:
            try:
                st = store.load_state(r)
                print(f"  {r}  [{st.status}/{st.stage}]")
            except Exception:
                print(f"  {r}  [状态文件缺失]")
        return 0

    if args.cmd == "show":
        from . import store
        from .graph import render_table

        run_id = getattr(args, "run_id", None) or store.newest_run()
        if not run_id:
            print("还没有任何运行记录。")
            return 1
        state = store.load_state(run_id)
        print(json.dumps(state.__dict__, ensure_ascii=False, indent=2))
        gp = store.graph_path(run_id)
        if gp.exists():
            print(render_table(store.load_graph(run_id)))
        return 0

    if args.cmd == "new":
        from .pipeline import run as run_single

        run_single(args.topic, platform=args.platform, auto_approve=args.auto)
        return 0

    if args.cmd == "plan":
        from .orchestrator import plan

        plan(args.requirement, auto=args.auto)
        return 0

    if args.cmd == "run":
        from .orchestrator import run

        state = run(args.requirement, auto=args.auto)
        if state is None:
            return 1
        print(f"\n  结果：{state.status}　运行目录：runs/{state.run_id}/")
        return 0 if state.status == "done" else 1

    if args.cmd == "resume":
        from . import store
        from .orchestrator import resume

        run_id = args.run_id or store.newest_run()
        if not run_id:
            print("没有可续跑的运行。")
            return 1
        state = resume(run_id, auto=args.auto)
        print(f"\n  结果：{state.status}")
        return 0 if state.status == "done" else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
