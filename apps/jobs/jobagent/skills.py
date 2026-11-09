"""技能词典与抽取：把 JD 文本 + 平台标签统一映射成标准技能名。"""
from __future__ import annotations

import re

# 标准技能 -> (别名列表, 分类)
SKILL_DICT: dict[str, tuple[list[str], str]] = {
    # ---- 编程语言 ----
    "Python": (["python", "py3"], "编程语言"),
    "Java": (["java", "javaee", "j2ee"], "编程语言"),
    "Go": (["golang", "go语言", "go开发", "go工程师"], "编程语言"),
    "C++": (["c++", "cpp"], "编程语言"),
    "C#": (["c#", ".net", "dotnet"], "编程语言"),
    "JavaScript": (["javascript", "js", "es6"], "编程语言"),
    "TypeScript": (["typescript", "ts"], "编程语言"),
    "PHP": (["php"], "编程语言"),
    "Rust": (["rust"], "编程语言"),
    "Scala": (["scala"], "编程语言"),
    "Kotlin": (["kotlin"], "编程语言"),
    "Swift": (["swift"], "编程语言"),
    "R": (["r语言"], "编程语言"),
    "MATLAB": (["matlab"], "编程语言"),
    "Shell": (["shell", "bash脚本", "linux脚本"], "编程语言"),
    "VBA": (["vba"], "编程语言"),
    # ---- 前端 ----
    "React": (["react", "reactjs"], "前端"),
    "Vue": (["vue", "vue2", "vue3", "vue.js"], "前端"),
    "Angular": (["angular"], "前端"),
    "Node.js": (["node.js", "nodejs", "node"], "前端"),
    "小程序": (["小程序", "微信小程序"], "前端"),
    "Flutter": (["flutter"], "前端"),
    "React Native": (["react native"], "前端"),
    "Webpack": (["webpack", "vite"], "前端"),
    "HTML/CSS": (["html", "css", "h5"], "前端"),
    # ---- 后端与中间件 ----
    "Spring": (["spring", "springboot", "spring boot", "springcloud", "spring cloud"], "后端框架"),
    "Django": (["django"], "后端框架"),
    "Flask": (["flask"], "后端框架"),
    "FastAPI": (["fastapi"], "后端框架"),
    "Scrapy": (["scrapy"], "后端框架"),
    "MyBatis": (["mybatis"], "后端框架"),
    "Dubbo": (["dubbo"], "后端框架"),
    "gRPC": (["grpc"], "后端框架"),
    "Kafka": (["kafka"], "后端框架"),
    "RabbitMQ": (["rabbitmq", "rocketmq", "消息队列", "mq"], "后端框架"),
    "Redis": (["redis"], "后端框架"),
    "Nginx": (["nginx"], "后端框架"),
    "Celery": (["celery", "定时任务"], "后端框架"),
    "微服务": (["微服务", "分布式", "高并发", "服务治理"], "后端框架"),
    # ---- 数据库 ----
    "MySQL": (["mysql"], "数据库"),
    "PostgreSQL": (["postgresql", "pgsql", "postgres"], "数据库"),
    "Oracle": (["oracle"], "数据库"),
    "SQL Server": (["sql server", "sqlserver"], "数据库"),
    "MongoDB": (["mongodb", "mongo"], "数据库"),
    "Redis缓存": (["缓存设计", "缓存"], "数据库"),
    "Elasticsearch": (["elasticsearch", "es搜索", "elastic search"], "数据库"),
    "HBase": (["hbase"], "数据库"),
    "ClickHouse": (["clickhouse"], "数据库"),
    "Hive": (["hive"], "数据库"),
    "SQL": (["sql", "sql语句", "sql优化"], "数据库"),
    # ---- 大数据 ----
    "Hadoop": (["hadoop"], "大数据"),
    "Spark": (["spark"], "大数据"),
    "Flink": (["flink"], "大数据"),
    "Airflow": (["airflow", "调度平台", "dolphinscheduler"], "大数据"),
    "ETL": (["etl", "数据清洗", "数据同步"], "大数据"),
    "数据仓库": (["数据仓库", "数仓", "维度建模", "数据建模"], "大数据"),
    # ---- AI / 算法 ----
    "机器学习": (["机器学习", "machine learning", "ml算法"], "AI算法"),
    "深度学习": (["深度学习", "deep learning", "神经网络"], "AI算法"),
    "TensorFlow": (["tensorflow", "tf框架"], "AI算法"),
    "PyTorch": (["pytorch", "torch"], "AI算法"),
    "PaddlePaddle": (["paddlepaddle", "paddle", "飞桨"], "AI算法"),
    "scikit-learn": (["scikit-learn", "sklearn"], "AI算法"),
    "XGBoost": (["xgboost", "lightgbm", "catboost"], "AI算法"),
    "NLP": (["nlp", "自然语言处理", "文本挖掘", "分词"], "AI算法"),
    "计算机视觉": (["计算机视觉", "cv算法", "图像识别", "ocr"], "AI算法"),
    "推荐系统": (["推荐系统", "推荐算法", "召回", "排序模型"], "AI算法"),
    "知识图谱": (["知识图谱", "图谱"], "AI算法"),
    "大模型": (["大模型", "llm", "gpt", "千问", "llama", "qwen"], "AI算法"),
    "RAG": (["rag", "检索增强", "向量数据库", "milvus", "faiss"], "AI算法"),
    "Agent": (["agent", "智能体", "langchain", "langgraph", "multi-agent"], "AI算法"),
    "Prompt工程": (["prompt", "提示词"], "AI算法"),
    "AIGC": (["aigc", "生成式", "stable diffusion", "diffusion"], "AI算法"),
    "模型微调": (["微调", "fine-tune", "finetune", "lora", "sft"], "AI算法"),
    "MLOps": (["mlops", "模型部署", "模型服务", "triton", "vllm"], "AI算法"),
    # ---- 数据分析 ----
    "Pandas": (["pandas", "numpy"], "数据分析"),
    "数据分析": (["数据分析", "数据统计分析", "业务分析"], "数据分析"),
    "数据挖掘": (["数据挖掘", "数据建模分析"], "数据分析"),
    "A/B测试": (["ab测试", "a/b测试", "abtest", "实验平台"], "数据分析"),
    "用户画像": (["用户画像", "标签体系"], "数据分析"),
    "指标体系": (["指标体系", "北极星指标", "数据看板", "报表开发"], "数据分析"),
    "Tableau": (["tableau", "power bi", "powerbi", "finebi", "superset", "帆软"], "数据分析"),
    "Excel": (["excel", "vlookup", "数据透视"], "数据分析"),
    "统计分析": (["统计分析", "回归分析", "假设检验", "ab实验"], "数据分析"),
    # ---- 云与运维 ----
    "Docker": (["docker", "容器化"], "云与运维"),
    "Kubernetes": (["kubernetes", "k8s"], "云与运维"),
    "Linux": (["linux", "centos", "ubuntu"], "云与运维"),
    "CI/CD": (["ci/cd", "jenkins", "gitlab ci", "持续集成", "devops"], "云与运维"),
    "阿里云": (["阿里云", "aliyun", "ecs", "oss"], "云与运维"),
    "腾讯云": (["腾讯云", "tencent cloud"], "云与运维"),
    "AWS": (["aws", "amazon web services"], "云与运维"),
    "监控告警": (["prometheus", "grafana", "zabbix", "监控告警"], "云与运维"),
    # ---- 测试 ----
    "自动化测试": (["自动化测试", "测试开发", "ui自动化"], "测试"),
    "接口测试": (["接口测试", "postman", "jmeter", "pytest", "selenium", "appium"], "测试"),
    # ---- 产品/项目 ----
    "需求分析": (["需求分析", "需求调研", "需求评审"], "产品与项目"),
    "产品设计": (["产品设计", "产品规划", "prd", "原型设计", "axure", "墨刀"], "产品与项目"),
    "项目管理": (["项目管理", "pmp", "敏捷开发", "scrum", "跨部门协作"], "产品与项目"),
    "竞品分析": (["竞品分析", "行业研究", "市场分析"], "产品与项目"),
    # ---- 通用能力 ----
    "沟通协作": (["沟通能力", "团队协作", "跨团队", "协调能力"], "通用能力"),
    "英语": (["英语", "english", "英文读写", "六级", "雅思"], "通用能力"),
    "文档能力": (["文档撰写", "方案编写", "标书"], "通用能力"),
    "带团队": (["团队管理", "带团队", "人员管理", "leader"], "通用能力"),
    "客户沟通": (["客户沟通", "售前", "客户对接", "甲方沟通"], "通用能力"),
}

# 需要词边界防护的短词，避免 "C"/"R"/"Go" 误命中
_AMBIGUOUS = {"C", "R", "Go"}

_ALIAS_INDEX: list[tuple[str, str, str]] = []  # (alias_lower, standard, category)
for _std, (_aliases, _cat) in SKILL_DICT.items():
    _ALIAS_INDEX.append((_std.lower(), _std, _cat))
    for _a in _aliases:
        _ALIAS_INDEX.append((_a.lower(), _std, _cat))
_ALIAS_INDEX.sort(key=lambda x: -len(x[0]))


def _make_pattern(alias: str) -> re.Pattern:
    esc = re.escape(alias)
    if re.search(r"[\u4e00-\u9fff]", alias):
        return re.compile(esc, re.I)
    # 纯英文/符号：用非字母数字边界
    return re.compile(rf"(?<![A-Za-z0-9+#_.]){esc}(?![A-Za-z0-9+#_])", re.I)


_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (_make_pattern(a), std, cat) for a, std, cat in _ALIAS_INDEX
]


def extract_skills(text: str, tags: list[str] | None = None) -> list[str]:
    """从 JD 正文 + 平台标签中抽取标准技能名（保序去重）。"""
    found: list[str] = []
    seen: set[str] = set()

    def add(std: str) -> None:
        if std not in seen:
            seen.add(std)
            found.append(std)

    for tag in tags or []:
        t = str(tag).strip()
        if not t:
            continue
        low = t.lower()
        for alias, std, _cat in _ALIAS_INDEX:
            if low == alias or (len(alias) >= 3 and alias in low):
                add(std)
                break

    if text:
        body = text[:6000]
        for pat, std, _cat in _PATTERNS:
            if std in seen:
                continue
            if std in _AMBIGUOUS:
                continue
            if pat.search(body):
                add(std)
    return found


def skill_category(skill: str) -> str:
    return SKILL_DICT.get(skill, ([], "其他"))[1]
