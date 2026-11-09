# 热点雷达 · 科技/AI + 财经

定时抓取全网热点，产出带判断的深度简报。**抓取交给脚本，判断交给 Agent** —— 脚本负责"数据不能错"，Agent 负责"结论要有信息量"。

```
hotspot-agent/
├── collect.py                     # 抓取器（纯标准库，14 个源，JSON + 紧凑摘要）
├── prompts/analysis-playbook.md   # 分析方法论（去噪 → 聚类 → 打分 → 深读 → 交叉）
├── data/latest.json               # 最近一次抓取的原始条目
├── data/digest.txt                # 最近一次的紧凑文本摘要
└── reports/2026-11-05-1239.md     # 报告存档（文件名 = 年月日-时分）
```

## 手动跑一次

```bash
cd F:/project/2026-11-05-12-30-22/hotspot-agent
python collect.py --hours 12            # 抓最近 12 小时
python collect.py --hours 24 --digest   # 抓 24 小时并打印可读摘要
```

输出示例：

```
[collect] 窗口 24h | 源 14/14 成功 | 原始 281 条 → 去重后 276 条 | 耗时 36.4s
  [OK ] Hacker News       28 条
  [ERR] GitHub Trending    0 条  <TimeoutError: ...>
```

## 数据源

| 类别 | 源 | 接入方式 | 备注 |
|---|---|---|---|
| 科技/AI | Hacker News | Firebase API | 取 top 35，带 points / comments |
| | GitHub Trending | HTML 解析 → API 降级 | HTML 被拦时自动切"近 7 天新建高星仓库" |
| | arXiv | Atom API | cs.AI / cs.CL / cs.LG / cs.CV |
| | IT之家、量子位、InfoQ、开源中国、少数派、钛媒体 | RSS | |
| | TechCrunch | RSS | |
| 财经 | 华尔街见闻 | 公开 API | 50 条实时快讯 |
| | 东方财富快讯 | 公开接口 | 50 条，北京时间需转 UTC |
| | CNBC、MarketWatch | RSS | |

单个源失败不会中断整体抓取，失败原因会写进 JSON 的 `source_status`，并在报告的「数据与口径」里如实披露。

### 加一个源

在 `collect.py` 的 `SOURCES` 里加一行即可：

```python
SOURCES = [
    # RSS：直接给 URL 和条数上限
    ("某科技媒体", "tech", ("https://example.com/rss", 20)),
    # 自定义接口：写一个 hours -> list[dict] 的函数
    ("某财经接口", "finance", src_custom),
]
```

自定义函数返回的每条记录需含 `title` / `url` / `summary` / `published_at`（ISO 8601）/ `signals`（可选，热度信号）。

## 报告长什么样

见 `reports/2026-11-05-1239.md`。结构是固定的六段：

1. **一分钟速览**——三条，每条给结论 + 为什么重要
2. **深读**——3–5 条，每条六问：事实 / 触发点 / 影响链 / 反方 / 接下来盯 / 置信度
3. **科技 & AI 扫描**——按主题簇，一句话一条
4. **财经 & 市场扫描**——含跨市场传导
5. **交叉信号**——技术层与资本层的错位或共振（最值钱的部分）
6. **数据与口径**——源状态、去重规则、未获取项

分析标准写在 `prompts/analysis-playbook.md`，想调整口味（更偏技术、更偏交易、更短、更长）改那里，不要改脚本。

## 定时任务

已配置为每天 **08:00 / 20:00** 各跑一次（每次覆盖最近 12 小时，合计无缝覆盖全天）。要改频率或时间，直接改这个定时任务的配置即可，脚本本身与频率无关。

## 设计取舍

- **为什么用脚本抓而不是让 Agent 自己搜**：WebSearch 的返回不稳定、无法保证每次都覆盖同一批源，且成本高。脚本 30 秒拿回 300 条结构化数据，Agent 只在需要核实某个数字时做 3–8 次定向检索，又快又省。
- **为什么保留原始 JSON**：报告里每个数字都要能回溯到 `data/latest.json` 的某个 `id`。这是"不编造"的技术保证。
- **为什么去重阈值设 0.86 这么低**：宁可有几条重复，不可漏掉同一事件的不同表述——跨源重复本身是热度信号（记在 `also_reported_by`）。
