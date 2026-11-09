"""公共底座：带契约校验和修复重试的 JSON 调用 + 执行器信封。

这一段是整个系统的核心机制——不是「调用模型」，是「调用模型并把不合约的输出退回去重做」。
"""
from __future__ import annotations

from typing import Callable

from .. import llm, store
from ..contracts import ContractError, validate_exec_result
from ..graph import TaskNode

MAX_REPAIR = 2

ENVELOPE_NOTE = """
## 系统信封（与领域字段并列放在 JSON 顶层，不可省略）

- "status": "done | partial | blocked | failed"
- "self_check": [{"criterion": "...", "result": "pass | fail | not_run", "evidence": "真实依据"}]
- "open_questions": ["..."]
- "notes_for_downstream": "给下游的一句交接说明"

self_check 必须逐条覆盖任务给出的 acceptance，evidence 必须是真实依据
（数字、行号、命令输出），不许写"已完成""符合要求"这类空话。
"""


def call_json(
    system: str,
    user: str,
    validator: Callable[[dict], list[str]],
    *,
    temperature: float = 0.3,
    trace: list[str] | None = None,
) -> dict:
    """调用模型拿结构化输出，不合约就带着问题清单退回重做。

    Raises:
        ContractError: 重试 MAX_REPAIR 次仍不合约。
    """
    trace = trace if trace is not None else []
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_problems: list[str] = []

    for attempt in range(MAX_REPAIR + 1):
        raw = llm.chat(messages, temperature=temperature)
        data, err = llm.extract_json(raw)

        if data is None:
            trace.append(f"第 {attempt + 1} 次输出无法解析：{err}")
            messages.append({"role": "assistant", "content": raw[:4000]})
            messages.append(
                {
                    "role": "user",
                    "content": f"你的输出不是合法 JSON（{err}）。"
                    f"请只返回一个 JSON 对象，不要任何解释文字、不要代码块标记。",
                }
            )
            continue

        problems = validator(data)
        if not problems:
            trace.append(f"第 {attempt + 1} 次输出通过契约校验")
            return data

        last_problems = problems
        trace.append(f"第 {attempt + 1} 次输出未通过校验：{problems}")
        messages.append({"role": "assistant", "content": raw[:4000]})
        messages.append(
            {
                "role": "user",
                "content": "输出未通过契约校验，逐条修正后重新只输出 JSON：\n- "
                + "\n- ".join(problems),
            }
        )

    raise ContractError("重试后仍不符合契约：\n- " + "\n- ".join(last_problems))


# ---------------------------------------------------------------- 信封
def envelope_problems(data: dict) -> list[str]:
    problems: list[str] = []
    if data.get("status") not in ("done", "partial", "blocked", "failed"):
        problems.append(
            f"status 必须是 done/partial/blocked/failed，当前为 {data.get('status')!r}"
        )
    sc = data.get("self_check")
    if not isinstance(sc, list) or not sc:
        problems.append("self_check 必须是非空数组，逐条覆盖 acceptance")
    else:
        for i, c in enumerate(sc):
            if not isinstance(c, dict):
                problems.append(f"self_check[{i}] 必须是对象")
                continue
            for k in ("criterion", "result", "evidence"):
                if not c.get(k):
                    problems.append(f"self_check[{i}] 缺少 {k}")
            if c.get("result") not in ("pass", "fail", "not_run"):
                problems.append(f"self_check[{i}].result 必须是 pass/fail/not_run")
    if not isinstance(data.get("open_questions", []), list):
        problems.append("open_questions 必须是数组")
    return problems


def combined(base: Callable[[dict], list[str]]) -> Callable[[dict], list[str]]:
    """领域校验 + 信封校验。status=blocked 时只查信封（blocked 没有产物可查）。"""

    def _v(data: dict) -> list[str]:
        problems = envelope_problems(data)
        if data.get("status") == "blocked":
            if not data.get("open_questions"):
                problems.append("status=blocked 时必须在 open_questions 里说明缺什么")
            return problems
        return problems + base(data)

    return _v


def build_result(
    node: TaskNode,
    out: dict,
    *,
    artifact_type: str,
    artifact_path=None,
    summary: str = "",
    raw_text: str = "",
) -> dict:
    """把模型输出补成完整信封：task_id / artifacts / tokens_used 由代码填空。"""
    result = dict(out)
    result["task_id"] = node.task_id
    if artifact_path:
        result["artifacts"] = [
            {
                "id": f"a-{node.task_id}-1",
                "type": artifact_type,
                "path": store.rel(artifact_path),
                "summary": summary or "",
            }
        ]
    else:
        result.setdefault("artifacts", [])
    # 没有真实 usage 时按字符数估算，宁可标"估算"也不假装精确
    result.setdefault("tokens_used", max(1, len(raw_text) // 2) if raw_text else 0)
    result.setdefault("notes_for_downstream", "")
    result.setdefault("open_questions", [])

    problems = validate_exec_result(result)
    if problems:
        raise ContractError("信封不完整：\n- " + "\n- ".join(problems))
    return result
