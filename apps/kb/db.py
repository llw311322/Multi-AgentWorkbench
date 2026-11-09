"""知识库存储层：Postgres + pgvector（容器 agentflow-pg，端口 5433）。

选 pgvector 而不是 SQLite+numpy 的理由（用户拍板）：数据将来要跨应用共享、
要撑十万级 chunk、检索要和入库在同一个事务里；pg 一套东西全包。

双通道检索，都在同一条 SQL 里完成，不需要额外的 BM25 库：
  向量通道  embedding <=> query（HNSW 索引，余弦距离）——语义相似
  关键词通道 pg_trgm word_similarity（GIN gin_trgm_ops 索引）——字面命中
中文分词的 tsvector 在默认镜像里没有 zhparser，所以关键词通道用 trigram；
它按字符切 3-gram，对中文子串命中已经够用，要精确分词再上 zhparser 扩展。

两路结果用 RRF（Reciprocal Rank Fusion，k=60）融合：不依赖两路分数可比，
谁排前面谁贡献大，工程上最省心。

连接配置（环境变量，均有默认值）：
  AGENTFLOW_PG_HOST / PORT / USER / PASSWORD / DB
  默认 agentflow:agentflow@127.0.0.1:5433/agentflow
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import psycopg

RRF_K = 60

# ---------------------------------------------------------------- 连接
def dsn() -> str:
    return (
        f"host={os.environ.get('AGENTFLOW_PG_HOST', '127.0.0.1')} "
        f"port={os.environ.get('AGENTFLOW_PG_PORT', '5433')} "
        f"user={os.environ.get('AGENTFLOW_PG_USER', 'agentflow')} "
        f"password={os.environ.get('AGENTFLOW_PG_PASSWORD', 'agentflow')} "
        f"dbname={os.environ.get('AGENTFLOW_PG_DB', 'agentflow')}"
    )


def connect(timeout: float = 5.0):
    return psycopg.connect(dsn(), connect_timeout=int(timeout))


# ---------------------------------------------------------------- 建库
def _ddl(dim: int) -> list[str]:
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        """
        CREATE TABLE IF NOT EXISTS kb_documents (
          id           BIGSERIAL PRIMARY KEY,
          source       TEXT NOT NULL UNIQUE,        -- 文件路径 / URL，同一来源重复入库=替换
          title        TEXT NOT NULL,
          doc_kind     TEXT NOT NULL DEFAULT 'doc', -- doc / clause / table
          version      TEXT NOT NULL DEFAULT '',    -- 版本号，如 GB 50016-2014(2018)
          status       TEXT NOT NULL DEFAULT 'current',  -- current / abolished
          is_mandatory BOOLEAN NOT NULL DEFAULT FALSE,  -- 强制性条文
          meta         JSONB NOT NULL DEFAULT '{}',
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """
        CREATE TABLE IF NOT EXISTS kb_chunks (
          id         BIGSERIAL PRIMARY KEY,
          doc_id     BIGINT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
          chunk_no   INT   NOT NULL,
          clause_no  TEXT  NOT NULL DEFAULT '',   -- 条号 / 小节号，引用时给得出出处
          heading    TEXT NOT NULL DEFAULT '',    -- 所在小节标题
          content    TEXT NOT NULL,
          embedding  VECTOR(%d),
          tokens     INT NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (doc_id, chunk_no)
        )""" % dim,
        "CREATE INDEX IF NOT EXISTS kb_chunks_hnsw  ON kb_chunks USING hnsw (embedding vector_cosine_ops)",
        "CREATE INDEX IF NOT EXISTS kb_chunks_trgm  ON kb_chunks USING gin (content gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS kb_chunks_doc   ON kb_chunks (doc_id)",
        """
        CREATE TABLE IF NOT EXISTS kb_meta (
          key   TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )""",
    ]


def init_db(dim: int, *, rebuild: bool = False) -> dict:
    """建表（幂等）。rebuild=True 时先清库重建——换 embedding 维度时必须。"""
    with connect() as conn, conn.cursor() as cur:
        if rebuild:
            cur.execute("DROP TABLE IF EXISTS kb_chunks, kb_documents, kb_meta CASCADE")
        for stmt in _ddl(dim):
            cur.execute(stmt)
        old = _meta(cur, "embed_dim")
        if old and int(old) != dim:
            raise RuntimeError(
                f"库已按 {old} 维建过，现在要用 {dim} 维：请 init --rebuild（会清空已入库内容）"
            )
        cur.execute(
            "INSERT INTO kb_meta(key, value) VALUES ('embed_dim', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (str(dim),),
        )
    return {"dim": dim, "rebuilt": rebuild}


def _meta(cur, key: str) -> str | None:
    try:
        cur.execute("SELECT value FROM kb_meta WHERE key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    except psycopg.errors.UndefinedTable:
        return None


# ---------------------------------------------------------------- 状态
def status() -> dict:
    try:
        with connect(timeout=2.5) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm') ORDER BY 1"
            )
            exts = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT count(*) FROM kb_documents")
            docs = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM kb_chunks")
            chunks = cur.fetchone()[0]
            cur.execute("SELECT value FROM kb_meta WHERE key = 'embed_dim'")
            row = cur.fetchone()
            return {
                "ok": True,
                "extensions": exts,
                "documents": docs,
                "chunks": chunks,
                "embed_dim": int(row[0]) if row else None,
            }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- 入库
def add_document(
    conn,
    *,
    source: str,
    title: str,
    chunks: list[dict],
    doc_kind: str = "doc",
    version: str = "",
    is_mandatory: bool = False,
    status: str = "current",
    meta: dict[str, Any] | None = None,
    vectors: list[list[float]] | None = None,
) -> int:
    """整篇入库（同一 source 覆盖替换）。chunks: [{clause_no, heading, content}]。"""
    if vectors is None:
        from . import embed as _embed

        vectors = _embed.embed([c["content"] for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(f"向量数 {len(vectors)} 与 chunk 数 {len(chunks)} 不一致")

    with conn.cursor() as cur:
        cur.execute("DELETE FROM kb_chunks WHERE doc_id IN (SELECT id FROM kb_documents WHERE source = %s)", (source,))
        cur.execute("DELETE FROM kb_documents WHERE source = %s", (source,))
        cur.execute(
            """INSERT INTO kb_documents(source, title, doc_kind, version, status, is_mandatory, meta)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (source, title, doc_kind, version, status, is_mandatory, json.dumps(meta or {}, ensure_ascii=False)),
        )
        doc_id = cur.fetchone()[0]
        rows = [
            (
                doc_id,
                i,
                c.get("clause_no", ""),
                c.get("heading", ""),
                c["content"],
                vectors[i],
                max(1, len(c["content"]) // 2),  # 中文粗略 token 估计
            )
            for i, c in enumerate(chunks)
        ]
        cur.executemany(
            """INSERT INTO kb_chunks(doc_id, chunk_no, clause_no, heading, content, embedding, tokens)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            rows,
        )
    conn.commit()
    return doc_id


def list_documents(limit: int = 50) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.id, d.source, d.title, d.doc_kind, d.version, d.status, d.is_mandatory,
                      count(c.id) AS chunks, d.created_at
               FROM kb_documents d LEFT JOIN kb_chunks c ON c.doc_id = d.id
               GROUP BY d.id ORDER BY d.created_at DESC LIMIT %s""",
            (limit,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------- 检索
def search(
    conn,
    *,
    query: str,
    query_vec: list[float] | None = None,
    k: int = 5,
    mode: str = "hybrid",
    only_current: bool = True,
) -> dict:
    """mode: hybrid（默认，RRF 融合）/ vector / keyword。

    返回 {took_ms, mode, hits: [{id, title, source, clause_no, heading, content,
                                 vec_rank, kw_rank, score}]}
    """
    t0 = time.time()
    vec_rows: list[tuple[int, dict]] = []
    kw_rows: list[tuple[int, dict]] = []
    cond = "d.status = 'current'" if only_current else "TRUE"
    k_fetch = max(k * 4, 20)  # 每路多取一些再融合，单路 LIMIT 太小会错过交叉结果

    with conn.cursor() as cur:
        if mode in ("hybrid", "vector"):
            if query_vec is None:
                from . import embed as _embed

                query_vec = _embed.embed_one(query)
            cur.execute(
                f"""SELECT c.id, d.title, d.source, c.clause_no, c.heading, c.content,
                           1 - (c.embedding <=> %s::vector) AS sim
                    FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
                    WHERE {cond}
                    ORDER BY c.embedding <=> %s::vector LIMIT %s""",
                (query_vec, query_vec, k_fetch),
            )
            for rank, r in enumerate(cur.fetchall()):
                vec_rows.append((rank, {"id": r[0], "title": r[1], "source": r[2], "clause_no": r[3],
                                        "heading": r[4], "content": r[5], "sim": round(float(r[6]), 4)}))
        if mode in ("hybrid", "keyword"):
            cur.execute(
                f"""SELECT c.id, d.title, d.source, c.clause_no, c.heading, c.content,
                           word_similarity(%(q)s, c.content) AS sim
                    FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
                    WHERE {cond} AND word_similarity(%(q)s, c.content) > 0.15
                    ORDER BY sim DESC LIMIT %(k)s""",
                {"q": query, "k": k_fetch},
            )
            for rank, r in enumerate(cur.fetchall()):
                kw_rows.append((rank, {"id": r[0], "title": r[1], "source": r[2], "clause_no": r[3],
                                       "heading": r[4], "content": r[5], "sim": round(float(r[6]), 4)}))

    # RRF 融合：rank 从 0 起，1/(k+rank+1)；两路都命中的自然浮上来
    fused: dict[int, dict] = {}
    for rows in (vec_rows, kw_rows):
        for rank, row in rows:
            item = fused.setdefault(row["id"], {**row, "vec_rank": None, "kw_rank": None, "score": 0.0})
            item["score"] += 1.0 / (RRF_K + rank + 1)
            key = "vec_rank" if rows is vec_rows else "kw_rank"
            item[key] = rank

    hits = sorted(fused.values(), key=lambda x: -x["score"])[:k]
    return {"took_ms": round((time.time() - t0) * 1000), "mode": mode, "hits": hits}
