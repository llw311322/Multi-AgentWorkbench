# apps/kb —— 知识库存储与检索层（Postgres 17 + pgvector）

工作台的第 5 个内嵌应用。给编排器提供「条文级」检索：入库、切块、向量化、混合检索、带出处返回。

```
apps/kb/
├── db.py       # 表结构、双通道检索、RRF 融合、status()
├── embed.py    # 向量化通道（qwen / zhipu / mock）
├── ingest.py   # CLI：init / demo / add / search / status / provider
└── README.md
```

---

## 一、`vector(1024)` 的 1024 是哪来的

**结论：1024 是我们自己定的维度，对应通义 `text-embedding-v4` 的默认输出维度。它和 DeepSeek 没有任何关系。**

网上很多 pgvector 教程会写这样一段：

```sql
CREATE TABLE docs (
  id bigserial PRIMARY KEY,
  content text,
  embedding vector(1024)   -- 注释：正好匹配 DeepSeek embed
);
```

这条注释是**错的**，两个概念被混在了一起：

| 常见误解 | 事实 |
|---|---|
| DeepSeek 提供 embedding 接口，输出 1024 维 | DeepSeek 开放 API **没有 `/embeddings` 端点**，只有 chat / FIM / models / balance。用它的 base_url 调向量化会直接 400 |
| 「1024」是 DeepSeek 的模型规格 | 1024 只是某些 DeepSeek **生成模型**的隐藏层维度（hidden size）。隐藏层维度是模型内部结构，不是你能通过 API 拿到的向量 |
| `vector(1024)` 绑定了某个厂商 | 完全没绑定。它只声明「这一列每行存 1024 个 float」，**谁灌进来的向量，1024 就是谁定的** |

所以判断依据只有一条：**看写入时实际调用的是哪个 embedding 模型**。本项目里就是：

```
provider = qwen
model    = text-embedding-v4        # DashScope OpenAI 兼容模式
dim      = 1024                     # 请求体里显式传 "dimensions": 1024
key      = apps/p02/.env → QWEN_API_KEY
```

自查命令：

```bash
python -m apps.kb.ingest provider     # 打印 provider / model / dim
python -m apps.kb.ingest status       # 打印库内实际块数、维度、扩展
```

也可以用 SQL 验一遍「写入的向量和查询的向量在同一个空间」：

```sql
SELECT id, left(content, 30), embedding <-> (SELECT embedding FROM kb_chunks LIMIT 1) AS dist
FROM kb_chunks ORDER BY dist LIMIT 3;
```

距离算得出来、排序合理，说明维度自洽。

### 换个说法记住这件事

> **生成通道和向量通道是两条独立的链路。** 模型中心里显示「默认 DeepSeek」说的是**生成**（写文案、拆任务）；向量化走的是另一条通道（qwen）。RAG 里「检索一家、生成一家」是常态，混为一谈就会得出「1024 匹配 DeepSeek」这种结论。

### 换 provider 的硬约束

向量空间不可混用。换 embedding 模型必须清库重灌，否则新旧向量之间的距离没有可比性，检索结果会乱：

```bash
KB_EMBED_PROVIDER=zhipu python -m apps.kb.ingest init --rebuild   # 智谱 embedding-3 = 2048 维
KB_EMBED_DIM=2048      python -m apps.kb.ingest init --rebuild
KB_EMBED_PROVIDER=mock  python -m apps.kb.ingest init --rebuild    # 离线哈希向量，仅测试
```

`embed.py` 里做了维度校验：provider 返回的维度与 `KB_EMBED_DIM` 不一致时直接抛错，不会静默写坏数据。选 1024 的理由是性价比——2048 维召回略好但存储翻倍、HNSW 建索引更慢；768 维在中文长条文上略欠；1024 还正好与 bge-m3 对齐，将来想本地化不用改表。

---

## 二、表结构

比截图里那种 `docs(id, content, embedding)` 单表多了「能追溯出处」的部分——规范类知识库没有条号和版本就是废的。

**`kb_documents`** —— 一篇资料一行

| 字段 | 说明 |
|---|---|
| `id` / `source` | 主键 / 文件路径或 URL（唯一） |
| `title` | 文档标题 |
| `doc_no` | 规范号，如 `GB 50016-2014` |
| `version` | 版本标识 |
| `status` | `current` / `abolished` —— 检索默认只查现行 |
| `meta` | JSONB，放强条标志、发布单位等 |

**`kb_chunks`** —— 一个条文/段落一行

| 字段 | 说明 |
|---|---|
| `id` / `doc_id` | 主键 / 外键 |
| `clause_no` | 条号，如 `5.2.1`，检索结果直接引用得出 |
| `heading` | 所在小节标题 |
| `content` | 条文正文（切块后，约 600 字） |
| `embedding` | `VECTOR(1024)`，与 `kb_meta.embed_dim` 校验 |
| `meta` | JSONB，表格原文、脚注等 |

索引：`embedding` 上 HNSW（`vector_cosine_ops`）、`content` 上 GIN（`pg_trgm`）。

---

## 三、检索：双通道 + RRF

```sql
-- 通道 1：语义
ORDER BY embedding <=> $1::vector LIMIT k*4
-- 通道 2：字面（中文不需要分词器，pg_trgm 直接吃子串）
WHERE word_similarity($2, content) > 0.15 ORDER BY sim DESC LIMIT k*4
-- 融合：RRF，k=60
score = Σ 1 / (60 + rank_i)
```

为什么不只用向量：规范文本里「第 5.2.1 条」「GB 50016」这类**精确标识符**，向量召回经常漏；为什么不只用关键词：同义表述（「疏散宽度」vs「安全出口宽度」）字面对不上。两路各取前 `4k` 条再融合，成本可控。

融合在 SQL 里完成，**不引入 rank_bm25 / LangChain**——这个规模（单机、万级条文）用不上，多一层依赖就多一层失控点。

---

## 四、CLI

```bash
python -m apps.kb.ingest status            # 连通性 + 文档/块数 + 维度 + provider
python -m apps.kb.ingest init              # 建表建扩展（幂等）
python -m apps.kb.ingest init --rebuild    # 清空重建（换 embedding 模型时用）
python -m apps.kb.ingest demo              # 灌 5 条示例知识，验证管线
python -m apps.kb.ingest add 资料.md        # 入库：按标题切块、逐块向量化
python -m apps.kb.ingest search "疏散宽度" -k 5
python -m apps.kb.ingest search "5.2.1" --mode keyword
```

`--mode` 可选 `hybrid`（默认）/ `vector` / `keyword`，用来对比单通道效果。

---

## 五、运行环境

```bash
docker run -d --name agentflow-pg \
  -e POSTGRES_USER=agentflow -e POSTGRES_PASSWORD=agentflow -e POSTGRES_DB=agentflow \
  -p 5433:5432 -v agentflow-pgdata:/var/lib/postgresql/data \
  --restart unless-stopped pgvector/pgvector:pg17
```

默认连 `127.0.0.1:5433`（避开本机可能的 5432），可用 `KB_DSN` 覆盖。扩展：`vector 0.8.7` + `pg_trgm 1.6`。

后端接口 `GET /api/kb/status` 返回扩展、文档数、块数、维度、provider、model；容器没起时 `ok=false`，前端据此降级而不是白屏。
