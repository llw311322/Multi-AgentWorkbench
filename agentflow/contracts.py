"""数据契约：所有 agent 的输入输出都必须落在这些结构里。

设计原则：契约先行。执行器可以换、框架可以换，契约不能变。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

FINDING_TYPES = {"fact", "inference", "opinion"}


class ContractError(Exception):
    """输出未通过契约校验。"""


# ---------------------------------------------------------------- 校验工具
def _req(obj: Any, key: str, where: str, problems: list[str]) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        problems.append(f"{where} 缺少字段 {key}")
        return None
    return obj[key]


def _nonempty_str(obj: Any, key: str, where: str, problems: list[str]) -> str:
    v = _req(obj, key, where, problems)
    if v is not None and (not isinstance(v, str) or not v.strip()):
        problems.append(f"{where}.{key} 必须是非空字符串")
        return ""
    return v or ""


# ---------------------------------------------------------------- 市场分析
def validate_research(data: dict) -> list[str]:
    """校验 research 产物。返回问题列表，空列表表示通过。"""
    problems: list[str] = []
    findings = _req(data, "key_findings", "report", problems)
    if not isinstance(findings, list) or not findings:
        problems.append("key_findings 必须是非空数组")
        findings = []

    for i, f in enumerate(findings):
        where = f"key_findings[{i}]"
        _nonempty_str(f, "finding", where, problems)
        t = _req(f, "type", where, problems)
        if t not in FINDING_TYPES:
            problems.append(f"{where}.type 必须是 fact / inference / opinion 之一，当前为 {t!r}")
        sources = f.get("sources") or []
        if not isinstance(sources, list):
            problems.append(f"{where}.sources 必须是数组")
            sources = []
        # 硬规则：标注为 fact 的结论必须有来源，且来源必须有 url 和 date
        if t == "fact" and not sources:
            problems.append(f"{where} 标注为 fact 但没有任何来源，禁止无来源断言事实")
        for j, s in enumerate(sources):
            sw = f"{where}.sources[{j}]"
            _nonempty_str(s, "url", sw, problems)
            _nonempty_str(s, "date", sw, problems)

    angles = _req(data, "content_angles", "report", problems)
    if not isinstance(angles, list) or not angles:
        problems.append("content_angles 必须是非空数组")
        angles = []
    for i, a in enumerate(angles):
        where = f"content_angles[{i}]"
        _nonempty_str(a, "angle", where, problems)
        _nonempty_str(a, "why_now", where, problems)
    return problems


# ---------------------------------------------------------------- 内容稿
BANNED_ABSOLUTE = ("最好", "最佳", "第一品牌", "国家级", "顶级", "唯一", "100%")


def validate_article(data: dict) -> list[str]:
    """校验内容产物。"""
    problems: list[str] = []

    titles = _req(data, "title_candidates", "article", problems)
    if not isinstance(titles, list) or len(titles) < 3:
        problems.append("title_candidates 至少需要 3 个候选标题")

    body = _nonempty_str(data, "body", "article", problems)
    if body and len(body) < 300:
        problems.append(f"body 过短（{len(body)} 字），正文至少 300 字")

    checks = _req(data, "fact_check", "article", problems)
    if not isinstance(checks, list) or not checks:
        problems.append("fact_check 必须是非空数组，文中每个事实都要列出")
        checks = []
    for i, c in enumerate(checks):
        where = f"fact_check[{i}]"
        _nonempty_str(c, "claim", where, problems)
        # 未核实的内容必须显式标 verified=false，不能留空糊过去
        if "verified" not in c:
            problems.append(f"{where} 缺少 verified 字段（true/false 必须明确）")
        elif c["verified"] is True and not c.get("source_url"):
            problems.append(f"{where} 标记为已核实但没有 source_url")

    comp = _req(data, "compliance", "article", problems)
    if not isinstance(comp, dict) or not isinstance(comp.get("checked"), list):
        problems.append("compliance.checked 必须是数组（逐条列出自查过的红线）")

    # 绝对化用语：广告法风险，硬拦
    hits = [w for w in BANNED_ABSOLUTE if w in body]
    if hits:
        problems.append(f"正文含绝对化用语 {hits}，违反广告法风险，必须改写")
    return problems


# ---------------------------------------------------------------- 拆分器输出
DOMAINS = ("code", "content", "research", "jobs")


def validate_splitter(data: dict) -> list[str]:
    """拆分器输出校验。比 validate_graph 更早一层——先保证字段齐全，再验图结构。"""
    problems: list[str] = []
    status = _req(data, "status", "splitter", problems)
    if status not in ("ok", "blocked"):
        problems.append(f"status 必须是 ok / blocked，当前为 {status!r}")
        return problems

    if status == "blocked":
        qs = data.get("blocking_questions") or []
        if not isinstance(qs, list) or not qs:
            problems.append("status=blocked 时必须给出 blocking_questions（至少一条）")
        return problems

    graph = _req(data, "graph", "splitter", problems)
    if not isinstance(graph, dict):
        problems.append("graph 缺失或不是对象")
        return problems

    _nonempty_str(graph, "goal", "graph", problems)
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        problems.append("graph.nodes 必须是非空数组")
        return problems

    seen = set()
    for i, n in enumerate(nodes):
        where = f"nodes[{i}]"
        tid = _nonempty_str(n, "task_id", where, problems)
        if tid:
            if tid in seen:
                problems.append(f"{where}.task_id={tid} 重复")
            seen.add(tid)
        _nonempty_str(n, "title", where, problems)
        _nonempty_str(n, "goal", where, problems)
        dom = _req(n, "domain", where, problems)
        if dom not in DOMAINS:
            problems.append(f"{where}.domain={dom!r} 不合法，必须是 {DOMAINS} 之一")
        if not isinstance(n.get("depends_on", []), list):
            problems.append(f"{where}.depends_on 必须是数组")
        acc = n.get("acceptance")
        if not isinstance(acc, list) or not acc:
            problems.append(f"{where}.acceptance 必须是非空数组——没有验收标准的节点无法被监控")
        else:
            for j, a in enumerate(acc):
                if not isinstance(a, dict) or "type" not in a:
                    problems.append(f"{where}.acceptance[{j}] 必须是含 type 字段的对象")
        if not isinstance(n.get("review_rubric", []), list):
            problems.append(f"{where}.review_rubric 必须是数组")
        if not isinstance(n.get("est_tokens", 0), int):
            problems.append(f"{where}.est_tokens 必须是整数")

    # 依赖必须指向存在的节点
    for n in nodes:
        for d in n.get("depends_on", []) or []:
            if d not in seen:
                problems.append(f"{n.get('task_id')} 依赖不存在的节点 {d}")

    for key in ("edges", "critical_path", "assumptions"):
        if key in graph and not isinstance(graph[key], list):
            problems.append(f"graph.{key} 必须是数组")
    return problems


# ---------------------------------------------------------------- 执行器统一返回
ENVELOPE_STATUS = ("done", "partial", "blocked", "failed")


def validate_exec_result(data: dict) -> list[str]:
    """所有执行器必须遵守的返回信封。领域数据放在 payload 里，不参与信封校验。"""
    problems: list[str] = []
    _nonempty_str(data, "task_id", "result", problems)
    st = _req(data, "status", "result", problems)
    if st not in ENVELOPE_STATUS:
        problems.append(f"status 必须是 {ENVELOPE_STATUS} 之一，当前为 {st!r}")

    arts = _req(data, "artifacts", "result", problems)
    if not isinstance(arts, list):
        problems.append("artifacts 必须是数组（没有产物就给空数组）")
    elif st in ("done", "partial") and not arts:
        problems.append("status 为 done/partial 时必须至少有一个 artifact")

    sc = data.get("self_check")
    if not isinstance(sc, list) or not sc:
        problems.append("self_check 必须是非空数组——必须逐条对照 acceptance 自检")
    else:
        for i, c in enumerate(sc):
            where = f"self_check[{i}]"
            _nonempty_str(c, "criterion", where, problems)
            r = _req(c, "result", where, problems)
            if r not in ("pass", "fail", "not_run"):
                problems.append(f"{where}.result 必须是 pass / fail / not_run，当前为 {r!r}")
            _nonempty_str(c, "evidence", where, problems)

    if not isinstance(data.get("open_questions", []), list):
        problems.append("open_questions 必须是数组")
    if not isinstance(data.get("tokens_used", 0), int):
        problems.append("tokens_used 必须是整数")
    return problems


# ---------------------------------------------------------------- 评审器输出
def validate_review(data: dict) -> list[str]:
    problems: list[str] = []
    _nonempty_str(data, "task_id", "review", problems)
    verdict = _req(data, "verdict", "review", problems)
    if verdict not in ("pass", "revise", "reject"):
        problems.append(f"verdict 必须是 pass / revise / reject，当前为 {verdict!r}")

    checks = data.get("acceptance_check")
    if not isinstance(checks, list) or not checks:
        problems.append("acceptance_check 必须逐条复核，不能为空")

    scores = data.get("rubric_scores")
    if not isinstance(scores, list):
        problems.append("rubric_scores 必须是数组")
    else:
        for i, s in enumerate(scores):
            where = f"rubric_scores[{i}]"
            _nonempty_str(s, "item", where, problems)
            if not isinstance(s.get("score"), int) or not (1 <= s["score"] <= 5):
                problems.append(f"{where}.score 必须是 1–5 的整数")
            _nonempty_str(s, "evidence", where, problems)

    bi = data.get("blocking_issue")
    if verdict in ("revise", "reject"):
        if not isinstance(bi, dict):
            problems.append(f"verdict={verdict} 时必须给出 blocking_issue")
        else:
            for k in ("what", "where", "why_it_matters", "how_to_fix"):
                _nonempty_str(bi, k, "blocking_issue", problems)
    return problems


# ---------------------------------------------------------------- 汇总器输出
def validate_aggregate(data: dict) -> list[str]:
    problems: list[str] = []
    sections = _req(data, "sections", "aggregate", problems)
    if not isinstance(sections, list) or not sections:
        problems.append("sections 必须是非空数组，只给目录不给内容不算完成")
    else:
        for i, s in enumerate(sections):
            where = f"sections[{i}]"
            _nonempty_str(s, "heading", where, problems)
            _nonempty_str(s, "content", where, problems)
            if not isinstance(s.get("source_tasks"), list) or not s["source_tasks"]:
                problems.append(f"{where}.source_tasks 必须标注来源节点，便于回溯")
    if not isinstance(data.get("conflicts", []), list):
        problems.append("conflicts 必须是数组（没有冲突就给空数组）")
    comp = data.get("completeness")
    if not isinstance(comp, dict):
        problems.append("completeness 必须是对象，含 covered_goals / missing")
    else:
        for k in ("covered_goals", "missing"):
            if not isinstance(comp.get(k, []), list):
                problems.append(f"completeness.{k} 必须是数组")
    return problems


# ---------------------------------------------------------------- 运行时状态
@dataclass
class RunState:
    run_id: str
    topic: str
    stage: str = "created"          # created -> researched -> drafted -> approved / rejected
    status: str = "running"         # running -> done / rejected / interrupted（进程没了没跑完）
    artifacts: dict[str, str] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def log(self, event: str, **kw) -> None:
        """事件流是前端监控/反馈视图的唯一数据源，所以必须自带时间戳与序号。

        没有 ts 的话，页面只能显示「发生了什么」，无法显示「什么时候、隔了多久」，
        泳道图和耗时统计就都画不出来。
        """
        self.history.append(
            {
                "seq": len(self.history) + 1,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "t": round(time.time(), 3),
                "event": event,
                **kw,
            }
        )

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)
