"""LLM 调用层：只用标准库，兼容任何 OpenAI 格式的接口。

支持 mock 模式（无需任何 key 即可跑通全流程），用于验证管道而非验证模型质量。

真实调用需要的环境变量：
  LLM_API_KEY    必填
  LLM_BASE_URL   默认 https://api.deepseek.com/v1
  LLM_MODEL      默认 deepseek-chat
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

MOCK = os.environ.get("AGENTFLOW_MOCK") == "1"
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "180"))


class LLMError(Exception):
    pass


def chat(messages: list[dict], *, model: str | None = None, temperature: float = 0.3) -> str:
    if MOCK:
        return _mock_reply(messages)
    if not API_KEY:
        raise LLMError(
            "未设置 LLM_API_KEY。设置后重试，或加 --mock 用假数据跑通流程。\n"
            "  export LLM_API_KEY=sk-xxx\n"
            "  export LLM_BASE_URL=https://api.deepseek.com/v1\n"
            "  export LLM_MODEL=deepseek-chat"
        )

    payload = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise LLMError(last_err) from e
        except (urllib.error.URLError, TimeoutError, KeyError) as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
    raise LLMError(f"调用失败（已重试 3 次）：{last_err}")


def extract_json(text: str) -> tuple[dict | None, str]:
    """从模型输出里抠出第一个完整 JSON 对象。返回 (data, error)。"""
    if not text or not text.strip():
        return None, "输出为空"
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data, ""
        return None, f"顶层必须是 JSON 对象，实际是 {type(data).__name__}"
    except json.JSONDecodeError:
        pass
    # 退一步：扫描括号，取第一个平衡的 {...}
    start = cleaned.find("{")
    if start == -1:
        return None, "找不到 JSON 对象"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start : i + 1]), ""
                except json.JSONDecodeError as e:
                    return None, f"JSON 解析失败：{e}"
    return None, "JSON 括号未闭合"


# ---------------------------------------------------------------- mock 数据
def _topic_from(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        lines = [ln.strip() for ln in m.get("content", "").splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            for p in ("选题：", "用户需求：", "任务节点：", "整体目标："):
                if line.startswith(p):
                    rest = line[len(p) :].strip()
                    if rest:
                        return rest[:40]
                    if i + 1 < len(lines):
                        return lines[i + 1][:40]
        if lines:
            return lines[0][:40]
    return "未命名选题"


def _is(system: str, *markers: str) -> bool:
    return any(k in system for k in markers)


def _materials_from(messages: list[dict]) -> list[dict]:
    """从汇总器的 user 消息里捞出「已完成节点及其产物」那段 JSON（mock 用）。"""
    marker = "已完成节点及其产物："
    for m in messages:
        if m.get("role") != "user":
            continue
        text = m.get("content") or ""
        i = text.find(marker)
        if i < 0:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(text[i + len(marker):].lstrip())
        except Exception:
            return []
        return data if isinstance(data, list) else []
    return []


def _mock_reply(messages: list[dict]) -> str:
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    topic = _topic_from(messages)

    if _is(system, "任务拆分器"):
        # 招聘类需求在 mock 下也走 jobs 领域：这个节点的数据来自真实的本地采集服务，
        # 不是编的，正好用来演示「编排把节点派给专项 Agent」这条链路。
        if any(k in topic for k in ("招聘", "岗位", "JD", "薪资", "求职", "就业", "人才市场")):
            return json.dumps(
                {
                    "status": "ok",
                    "blocking_questions": [],
                    "graph": {
                        "goal": f"围绕「{topic}」产出一份带真实岗位样本的就业市场分析",
                        "nodes": [
                            {
                                "task_id": "t-001",
                                "title": "岗位样本采集与薪资分析",
                                "domain": "jobs",
                                "goal": f"采集杭州数据分析师岗位（月薪 10K 以上），产出薪资分位、技能要求与学历门槛分析（原始需求：{topic}）",
                                "depends_on": [],
                                "acceptance": [
                                    {"type": "cites_sources", "min": 2},
                                    {"type": "schema_valid"},
                                ],
                                "review_rubric": ["结论是否都能追到具体岗位样本"],
                                "est_tokens": 6000,
                                "parallel_group": "g1",
                            },
                            {
                                "task_id": "t-002",
                                "title": "写成就业市场解读",
                                "domain": "content",
                                "goal": "基于 t-001 的数据产出 300 字以上解读、3 个候选标题、完整事实核查表",
                                "depends_on": ["t-001"],
                                "acceptance": [
                                    {"type": "min_words", "min": 300},
                                    {"type": "title_count", "min": 3},
                                    {"type": "fact_check_present"},
                                ],
                                "review_rubric": ["是否只引用了 t-001 里出现过的数字"],
                                "est_tokens": 9000,
                                "parallel_group": "",
                            },
                        ],
                        "edges": [["t-001", "t-002"]],
                        "critical_path": ["t-001", "t-002"],
                        "assumptions": ["假设数据源为智联招聘 / 猎聘公开搜索结果"],
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "ok",
                "blocking_questions": [],
                "graph": {
                    "goal": f"围绕「{topic}」产出一篇可直接发布的稿件",
                    "nodes": [
                        {
                            "task_id": "t-001",
                            "title": "热点与事实梳理",
                            "domain": "research",
                            "goal": "给出至少 2 条带来源的关键事实，并区分事实与推断",
                            "depends_on": [],
                            "acceptance": [{"type": "cites_sources", "min": 2}],
                            "review_rubric": ["事实与推断是否严格区分"],
                            "est_tokens": 6000,
                            "parallel_group": "g1",
                        },
                        {
                            "task_id": "t-002",
                            "title": "读者关切与选题角度",
                            "domain": "research",
                            "goal": "给出 2 个可写角度及其时效理由",
                            "depends_on": [],
                            "acceptance": [{"type": "schema_valid"}],
                            "review_rubric": [],
                            "est_tokens": 5000,
                            "parallel_group": "g1",
                        },
                        {
                            "task_id": "t-003",
                            "title": "成稿",
                            "domain": "content",
                            "goal": "产出 300 字以上正文、3 个候选标题、完整事实核查表",
                            "depends_on": ["t-001", "t-002"],
                            "acceptance": [
                                {"type": "min_words", "min": 300},
                                {"type": "title_count", "min": 3},
                                {"type": "fact_check_present"},
                            ],
                            "review_rubric": ["单篇是否只有一个核心观点", "是否存在编造数据"],
                            "est_tokens": 9000,
                            "parallel_group": "",
                        },
                    ],
                    "edges": [["t-001", "t-003"], ["t-002", "t-003"]],
                    "critical_path": ["t-001", "t-003"],
                    "assumptions": ["假设平台为微信公众号", "假设篇幅 800–1500 字"],
                },
            },
            ensure_ascii=False,
        )

    if _is(system, "质量评审"):
        return json.dumps(
            {
                "task_id": "t-000",
                "verdict": "pass",
                "acceptance_check": [
                    {
                        "criterion": "逐条对照 acceptance 独立复核",
                        "result": "pass",
                        "evidence": "mock 复核通过",
                    }
                ],
                "rubric_scores": [
                    {"item": "整体质量", "score": 4, "evidence": "mock 评审：结构完整，观点集中"}
                ],
                "blocking_issue": None,
                "minor_notes": ["mock 模式，评审结果不具参考价值"],
            },
            ensure_ascii=False,
        )

    if _is(system, "汇总器"):
        # 按真实节点列表生成章节，而不是写死 t-001/t-002/t-003：
        # 任务图一旦换形状（比如加了 jobs 节点），写死的引用就会指向不存在的节点。
        mats = _materials_from(messages)
        sections = []
        for m in mats:
            body = (m.get("products") or [""])[0].strip()
            if len(body) > 600:
                body = body[:600] + "\n\n…（完整内容见任务产物）"
            sections.append(
                {
                    "heading": m.get("title") or m.get("task_id"),
                    "content": body or "（该节点没有产出正文）",
                    "source_tasks": [m.get("task_id")],
                }
            )
        if not sections:
            sections = [{"heading": "成稿", "content": f"围绕「{topic}」的交付（mock 占位）。", "source_tasks": []}]
        return json.dumps(
            {
                "sections": sections,
                "conflicts": [],
                "completeness": {
                    "covered_goals": [m.get("title", "") for m in mats] or ["产出可发布交付"],
                    "missing": [],
                },
            },
            ensure_ascii=False,
        )

    if _is(system, "代码执行器"):
        return json.dumps(
            {
                "status": "done",
                "files_changed": [],
                "diff_summary": "mock 模式：未实际改动任何文件，只走通流程",
                "tests_passed": True,
                "test_output": "mock: no tests run",
                "self_check": [
                    {"criterion": "max_files_changed", "result": "pass", "evidence": "改动 0 个文件"},
                    {"criterion": "tests_passed", "result": "pass", "evidence": "mock 标记为通过"},
                ],
                "open_questions": ["mock 结果不可用于真实仓库"],
                "notes_for_downstream": "mock 代码产物，仅验证流程",
            },
            ensure_ascii=False,
        )

    if _is(system, "市场与热点分析"):
        return json.dumps(
            {
                "status": "done",
                "topic": topic,
                "key_findings": [
                    {
                        "finding": f"围绕「{topic}」的讨论在近两周明显升温（mock 数据）",
                        "type": "fact",
                        "sources": [
                            {
                                "url": "https://example.com/a",
                                "date": "2026-10-28",
                                "publisher": "示例媒体A",
                            },
                            {
                                "url": "https://example.com/b",
                                "date": "2026-11-01",
                                "publisher": "示例媒体B",
                            },
                        ],
                        "confidence": "mid",
                    },
                    {
                        "finding": "热度上升主要来自供给端变化，而非需求端爆发（推断）",
                        "type": "inference",
                        "sources": [
                            {
                                "url": "https://example.com/c",
                                "date": "2026-10-30",
                                "publisher": "示例研报",
                            }
                        ],
                        "confidence": "low",
                    },
                ],
                "content_angles": [
                    {
                        "angle": f"从「{topic}」的供给端变化切入，讲清谁在推动",
                        "why_now": "话题处于上升期，但主流解读停在现象层",
                        "target_audience": "关注行业动态的从业者",
                        "risk_note": "低风险",
                    },
                    {
                        "angle": "对普通人的三个具体影响",
                        "why_now": "读者关心自身处境，此类角度转化率高",
                        "target_audience": "泛读者",
                        "risk_note": "避免给出投资建议",
                    },
                ],
                "data_freshness": {"oldest_source_date": "2026-10-28", "stale_items": []},
                "self_check": [
                    {"criterion": "cites_sources", "result": "pass", "evidence": "2 条结论均附来源"},
                    {"criterion": "schema_valid", "result": "pass", "evidence": "结构完整"},
                ],
                "open_questions": [],
                "notes_for_downstream": "mock 数据，来源不可信，真实使用时需换真实检索",
            },
            ensure_ascii=False,
        )

    body = (
        f"最近关于「{topic}」的讨论突然多了起来，但多数说法停在表面，真正值得关注的是背后的结构变化。\n\n"
        "先说清楚发生了什么。过去两周，围绕这个话题的公开信息密度明显上升，多家媒体在相近时间点给出了报道。"
        "这个同步性本身就是一个信号——它不是自然发酵，而是有明确的推动力。\n\n"
        "再看这个推动力来自哪里。从目前可核实的信息看，变化主要发生在供给端，也就是提供方在主动调整，"
        "而不是需求端突然爆发。这意味着热度可能不会持续太久，除非后续有需求侧的真实跟进。\n\n"
        "对普通人来说，结论其实很朴素：先别急着跟。看清谁在推、推到什么位置，比抢在第一时间表态更有价值。\n\n"
        "（本文为 mock 模式生成的示例文本，仅用于验证流程，不构成任何建议。）"
    )
    return json.dumps(
        {
            "status": "done",
            "title_candidates": [
                f"「{topic}」热度上升，但推手不在需求端",
                f"关于{topic}，多数人看错了重点",
                f"{topic}：先别急着跟，看清谁在推",
            ],
            "body": body,
            "fact_check": [
                {
                    "claim": "过去两周公开信息密度明显上升",
                    "source_url": "https://example.com/a",
                    "source_date": "2026-10-28",
                    "verified": True,
                },
                {
                    "claim": "变化主要发生在供给端",
                    "source_url": "https://example.com/c",
                    "source_date": "2026-10-30",
                    "verified": False,
                },
            ],
            "compliance": {
                "checked": ["无绝对化用语", "无时政评论", "已标注推断裂"],
                "flags": ["一条事实未核实，发布前需人工确认"],
            },
            "self_check": [
                {"criterion": "min_words", "result": "pass", "evidence": f"正文 {len(body)} 字"},
                {"criterion": "title_count", "result": "pass", "evidence": "3 个候选标题"},
                {"criterion": "fact_check_present", "result": "pass", "evidence": "2 条核查记录"},
            ],
            "open_questions": ["供给端结论未完全核实"],
            "notes_for_downstream": "第二段的数据建议再核一次",
            "word_count": len(body),
        },
        ensure_ascii=False,
    )
