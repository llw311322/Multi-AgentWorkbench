"""单链管道：分析 -> 撰写 -> 人工闸门 G2 -> 定稿。

刻意不用任何编排框架。这一条链只有一个线性路径，
引入图引擎只会让你在 interrupt/resume 上花的时间比写这 120 行还多。
"""
from __future__ import annotations

import re
import time

from . import store
from .agents import run_legacy_content as run_content
from .agents import run_legacy_research as run_research
from .agents.content import render_markdown
from .contracts import ContractError, RunState
from .llm import LLMError, MOCK


def _step(n: int, total: int, text: str) -> float:
    print(f"\n[{n}/{total}] {text} ...", flush=True)
    return time.time()


def _done(t0: float, note: str = "") -> None:
    print(f"      完成 {time.time() - t0:.1f}s  {note}", flush=True)


def extract_from_draft(text: str) -> tuple[list[str], str]:
    """从 draft.md 里取回标题候选和正文。人工编辑过也照样能解析。"""
    parts = re.split(r"\n---\s*\n", text)
    titles: list[str] = []
    body = ""
    if parts:
        for line in parts[0].splitlines():
            m = re.match(r"^\s*\d+\.\s+(.+)$", line)
            if m:
                titles.append(m.group(1).strip())
    if len(parts) > 1:
        body = re.sub(r"^#\s*正文\s*$", "", parts[1], flags=re.MULTILINE).strip()
    return titles, body


def gate_review(state: RunState, draft_path, *, auto: bool) -> str:
    """G2 闸门：内容类发布前必须人工过目。返回 approved / rejected。"""
    if auto:
        print("      --auto 模式：跳过人工确认（仅用于调试，真实发布切勿使用）")
        return "approved"

    print("\n" + "=" * 62)
    print("  闸门 G2　内容发布前确认")
    print("=" * 62)
    print(f"  稿件：{draft_path}")
    print("\n  选项：")
    print("    a  通过，生成 final.md")
    print("    e  编辑（打开文件改完后回来按回车）")
    print("    r  驳回，本次运行终止")
    print()

    while True:
        try:
            choice = input("  请选择 [a/e/r] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  已中断，状态保留在 state.json")
            return "rejected"

        if choice == "a":
            return "approved"
        if choice == "r":
            return "rejected"
        if choice == "e":
            print(f"\n  请编辑：{draft_path}")
            print("  改完保存后回到这里按回车继续。")
            input("  > ")
            state.log("gate_edit", path=str(draft_path))
            return "approved"
        print("  无效输入，请输入 a / e / r")


def run(
    topic: str,
    *,
    platform: str = "微信公众号",
    auto_approve: bool = False,
) -> RunState:
    total = 3
    trace: list[str] = []

    print("\n" + "=" * 62)
    print(f"  选题：{topic}")
    print(f"  平台：{platform}    模式：{'MOCK（假数据）' if MOCK else '真实调用'}")
    print("=" * 62)

    state = store.new_run(topic)
    print(f"  运行目录：runs/{state.run_id}/")

    try:
        # ---- 1. 市场分析
        t0 = _step(1, total, "市场与热点分析")
        research = run_research(topic, trace=trace)
        store.save_json(state.run_id, "research", research)
        state.stage = "researched"
        state.artifacts["research"] = f"runs/{state.run_id}/research.json"
        state.log("research_done", findings=len(research.get("key_findings", [])))
        store.save_state(state)
        _done(t0, f"{len(research.get('key_findings', []))} 条结论 / {len(research.get('content_angles', []))} 个角度")

        # ---- 2. 撰写
        t0 = _step(2, total, "撰写稿件")
        article = run_content(topic, research, platform=platform, trace=trace)
        store.save_json(state.run_id, "article", article)
        draft_md = render_markdown(article)
        draft_path = store.save_text(state.run_id, "draft.md", draft_md)
        state.stage = "drafted"
        state.artifacts["draft"] = f"runs/{state.run_id}/draft.md"
        state.log("content_done", words=len(article.get("body", "")))
        store.save_state(state)
        _done(t0, f"约 {len(article.get('body', ''))} 字")

        # ---- 3. 闸门
        _step(3, total, "人工确认")
        verdict = gate_review(state, draft_path, auto=auto_approve)

        if verdict == "rejected":
            state.status = "rejected"
            state.stage = "rejected"
            state.log("gate_rejected")
            store.save_state(state)
            print("\n  已驳回。未生成发布稿。")
            return state

        # ---- 定稿
        titles, body = extract_from_draft(draft_path.read_text(encoding="utf-8"))
        title = titles[0] if titles else topic
        final = f"{title}\n\n{body}\n"
        final_path = store.save_text(state.run_id, "final.md", final)
        state.artifacts["final"] = f"runs/{state.run_id}/final.md"
        state.status = "done"
        state.stage = "approved"
        state.log("finalized", title=title)
        store.save_state(state)

        print(f"\n  完成。发布稿：{final_path}")
        print(f"  标题：{title}")
        print(f"  试运行记录：{len(trace)} 条契约校验事件，详见 state.json\n")
        return state

    except ContractError as e:
        state.status = "failed"
        state.log("contract_error", detail=str(e))
        store.save_state(state)
        print(f"\n  [x] 输出未通过契约校验，已重试仍不合格：\n{e}")
        print(f"  中间产物保留在 runs/{state.run_id}/，可人工检查后调整 prompt 再跑。\n")
        raise SystemExit(2)

    except LLMError as e:
        state.status = "failed"
        state.log("llm_error", detail=str(e))
        store.save_state(state)
        print(f"\n  [x] 模型调用失败：{e}\n")
        raise SystemExit(3)
