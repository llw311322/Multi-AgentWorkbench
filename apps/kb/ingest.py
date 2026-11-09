"""知识库入库 / 检索 CLI。

跑法（项目根目录）：
  python -m apps.kb.ingest init                 # 建表（幂等）
  python -m apps.kb.ingest demo                 # 灌入示例知识
  python -m apps.kb.ingest add 资料.md          # 整篇入库（按标题切块）
  python -m apps.kb.ingest search "pgvector 是什么" -k 5
  python -m apps.kb.ingest status               # 库状态 / 连通性
  python -m apps.kb.ingest provider             # 当前 embedding 通道

也可直接 `python apps/kb/ingest.py ...`（自带 sys.path 引导）。
环境变量：KB_EMBED_PROVIDER=qwen|zhipu|mock；AGENTFLOW_PG_*（见 db.py）。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from apps.kb import db, embed
else:
    from . import db, embed

CHUNK_TARGET = 600  # 单块目标字符数：太小召回碎，太大稀释向量语义


# ---------------------------------------------------------------- 切块
def split_chunks(text: str) -> list[dict]:
    """按标题切段，长段按段落累计切到 CHUNK_TARGET。保留条号与小节标题。"""
    out: list[dict] = []
    heading = ""
    clause = ""
    buf: list[str] = []
    size = 0

    def flush():
        nonlocal buf, size
        body = "\n".join(buf).strip()
        if body:
            out.append({"clause_no": clause, "heading": heading, "content": body})
        buf, size = [], 0

    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        m2 = re.match(r"^\s*(\d+(?:\.\d+)*)\s+(\S.*)$", line) if not m else None
        if m or (m2 and (not buf or size > CHUNK_TARGET // 2)):
            flush()
            heading = (m.group(2) if m else m2.group(2))[:60]
            cm = re.match(r"^(\d+(?:\.\d+)*)", heading)
            clause = cm.group(1) if cm else clause if not m else ""
            continue
        buf.append(line)
        size += len(line) + 1
        if size >= CHUNK_TARGET and line.strip() == "":
            flush()
    flush()
    return out


# ---------------------------------------------------------------- 命令
def cmd_init(args) -> int:
    info = db.init_db(args.dim, rebuild=args.rebuild)
    print(f"建库完成：dim={info['dim']}{'（已清库重建）' if info['rebuilt'] else ''}")
    return 0


def cmd_demo(args) -> int:
    demo_doc = {
        "source": "kb://demo/agentflow-notes",
        "title": "AgentFlow 工作台要点摘录",
        "doc_kind": "doc",
        "version": "v1",
        "chunks": [
            {"clause_no": "1", "heading": "为什么用 pgvector", "content":
             "知识库选 pgvector 而不是 SQLite+numpy：数据要跨应用共享、要撑十万级 chunk、"
             "检索和入库要在同一个事务里。向量通道用 HNSW 索引做余弦相似度，"
             "关键词通道用 pg_trgm 的 word_similarity 做中文子串命中，两路结果用 RRF 融合。"},
            {"clause_no": "2", "heading": "RRF 融合公式", "content":
             "Reciprocal Rank Fusion 把两路检索的排名（而不是分数）拿来融合："
             "每个结果的得分是 1/(k+rank)，k 取 60。两路都命中的条目自然浮到最前面，"
             "不需要为「向量分数和关键词分数不可比」做归一化。"},
            {"clause_no": "3", "heading": "HNSW 索引", "content":
             "HNSW 是分层可导航小世界图：查询时从顶层稀疏图往下走，"
             "近似最近邻的召回速度远快于全表扫描。pgvector 里用 "
             "USING hnsw (embedding vector_cosine_ops) 建索引，余弦距离用 <=> 操作符。"},
            {"clause_no": "4", "heading": "切块原则", "content":
             "入库前要切块：单块目标 600 字左右，太碎则召回的上下文不够，"
             "太大则向量语义被稀释。按标题切段并保留条号，"
             "引用时才能给出「出自哪篇哪节」，这是防幻觉的基础。"},
            {"clause_no": "5", "heading": "防幻觉三铁律", "content":
             "知识库问答必须：一、答案只基于检索到的条文；二、每个结论附出处（篇名+条号）；"
             "三、召回不足时明说「库里没有相关内容」，禁止用模型先验补写规范条文。"},
        ],
    }
    conn = db.connect()
    t0 = time.time()
    vecs = embed.embed([c["content"] for c in demo_doc["chunks"]])
    doc_id = db.add_document(conn, vectors=vecs, **demo_doc)
    print(f"示例已入库：doc_id={doc_id}，{len(demo_doc['chunks'])} 块，"
          f"embedding={embed.provider_name()}，耗时 {time.time()-t0:.1f}s")
    return 0


def cmd_add(args) -> int:
    p = Path(args.file)
    if not p.exists():
        print(f"文件不存在：{p}")
        return 1
    text = p.read_text(encoding="utf-8", errors="replace")
    chunks = split_chunks(text)
    if not chunks:
        print("没切出任何块（文件是空的？）")
        return 1
    conn = db.connect()
    t0 = time.time()
    vecs = embed.embed([c["content"] for c in chunks])
    doc_id = db.add_document(
        conn,
        source=args.source or p.resolve().as_uri(),
        title=args.title or p.stem,
        chunks=chunks,
        doc_kind=args.kind,
        version=args.version,
        is_mandatory=args.mandatory,
        vectors=vecs,
    )
    print(f"入库完成：doc_id={doc_id}，{len(chunks)} 块，embedding={embed.provider_name()}，"
          f"耗时 {time.time()-t0:.1f}s")
    return 0


def cmd_search(args) -> int:
    conn = db.connect()
    res = db.search(conn, query=args.query, k=args.k, mode=args.mode)
    print(f"模式 {res['mode']} · {res['took_ms']}ms · 命中 {len(res['hits'])}")
    for i, h in enumerate(res["hits"], 1):
        src = f"{h['vec_rank'] is not None and 'V' or ''}{h['kw_rank'] is not None and 'K' or ''}"
        snippet = h["content"][:90].replace("\n", " ")
        print(f"{i}. [{src}] {h['title']} {('#' + h['clause_no']) if h['clause_no'] else ''} "
              f"score={h['score']:.4f}")
        print(f"   {snippet}…")
    return 0


def cmd_status(args) -> int:
    st = db.status()
    if not st["ok"]:
        print(f"连不上库：{st['error']}")
        print("检查：docker ps 看 agentflow-pg 是否在跑；端口 5433")
        return 1
    print(f"库正常：扩展 {st['extensions']}，文档 {st['documents']} 篇 / 块 {st['chunks']}，"
          f"维度 {st['embed_dim']}")
    print(f"embedding 通道：{embed.provider_name()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kb.ingest", description="知识库入库/检索")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="建表（幂等）")
    pi.add_argument("--dim", type=int, default=embed.DIM)
    pi.add_argument("--rebuild", action="store_true", help="清库重建（换维度时必须）")
    pi.set_defaults(fn=cmd_init)

    sub.add_parser("demo", help="灌入示例知识").set_defaults(fn=cmd_demo)

    pa = sub.add_parser("add", help="入库一个 md/txt 文件")
    pa.add_argument("file")
    pa.add_argument("--title")
    pa.add_argument("--source")
    pa.add_argument("--kind", default="doc")
    pa.add_argument("--version", default="")
    pa.add_argument("--mandatory", action="store_true", help="强制性条文")
    pa.set_defaults(fn=cmd_add)

    ps = sub.add_parser("search", help="混合检索")
    ps.add_argument("query")
    ps.add_argument("-k", type=int, default=5)
    ps.add_argument("--mode", choices=["hybrid", "vector", "keyword"], default="hybrid")
    ps.set_defaults(fn=cmd_search)

    sub.add_parser("status", help="库状态").set_defaults(fn=cmd_status)
    sub.add_parser("provider", help="embedding 通道").set_defaults(
        fn=lambda a: (print(f"provider = {embed.provider_name()}（dim={embed.DIM}）"), 0)[1]
    )

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
