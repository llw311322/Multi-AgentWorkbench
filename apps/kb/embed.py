"""知识库向量/关键词双通道 Embedding。

为什么单独一个模块：换 embedding 厂商不该动存储层。db.py 只认「1024 维浮点数组」，
这里负责把它生产出来。

三个 Provider：
  qwen   通义 text-embedding-v4（走 apps/p02/.env 里的 QWEN_API_KEY，OpenAI 兼容协议）
  zhipu  智谱 embedding-3（同上，ZHIPU_API_KEY）
  mock   确定性哈希向量——不联网，只为把检索管线跑通；得分没有语义意义，
         但能让「入库 → 混合检索 → 排序」全链路可测试。

维度：qwen v4 默认 1024，与 schema 的 VECTOR(1024) 对齐；换厂商记得同步 KB_EMBED_DIM
并重建表（init --rebuild）。

常见误解（写在这里防止以后再被教程带偏）：1024 **不是** DeepSeek 的规格。
DeepSeek 开放 API 没有 /embeddings 端点，根本产不出向量；网上「vector(1024) 正好匹配
DeepSeek embed」是把它某些生成模型的隐藏层维度（hidden size）当成了 embedding 输出。
1024 就是本文件请求体里 "dimensions": 1024 定的，对应 qwen text-embedding-v4。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

P02_ENV = Path(__file__).resolve().parent.parent / "p02" / ".env"

DIM = int(os.environ.get("KB_EMBED_DIM", "1024"))
BATCH = 8  # dashscope embeddings 单次最多 10 条，留点余量


# ---------------------------------------------------------------- 基础
def _env() -> dict[str, str]:
    out: dict[str, str] = {}
    if P02_ENV.exists():
        for line in P02_ENV.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_]+)\s*=\s*(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


def _post_json(url: str, body: dict, key: str) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    )
    last_err: Exception | None = None
    # 先走系统代理，TLS 被中间人拦截时退回直连再试一次
    for opener in (
        urllib.request.build_opener(),
        urllib.request.build_opener(urllib.request.ProxyHandler({})),
    ):
        try:
            with opener.open(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"embedding 接口返回 HTTP {e.code}：{detail}") from e
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"embedding 请求失败：{type(last_err).__name__}: {last_err}")


# ---------------------------------------------------------------- mock
def _mock_embed(texts: list[str]) -> list[list[float]]:
    """确定性哈希向量：同一个文本永远得到同一个向量，检索管线可复现。"""
    out = []
    for t in texts:
        vec = [0.0] * DIM
        # 2-gram shingle：让相邻用词的变化能反映到向量里
        shingles = {t[i : i + 2] for i in range(max(0, len(t) - 1))} or {t or "空"}
        for s in shingles:
            h = int.from_bytes(hashlib.md5(s.encode("utf-8")).digest()[:4], "big")
            vec[h % DIM] += 1.0
            vec[(h >> 16) % DIM] += 0.5
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        out.append([round(v / norm, 6) for v in vec])
    return out


# ---------------------------------------------------------------- 远端
def _qwen_embed(texts: list[str], env: dict) -> list[list[float]]:
    base = env.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    model = os.environ.get("KB_EMBED_MODEL", "text-embedding-v4")
    data = _post_json(
        f"{base}/embeddings", {"model": model, "input": texts, "dimensions": DIM}, env["QWEN_API_KEY"]
    )
    items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
    vecs = [it["embedding"] for it in items]
    if len(vecs) != len(texts):
        raise RuntimeError(f"qwen 返回 {len(vecs)} 条向量，期望 {len(texts)} 条")
    return vecs


def _zhipu_embed(texts: list[str], env: dict) -> list[list[float]]:
    base = env.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    model = os.environ.get("KB_EMBED_MODEL", "embedding-3")
    vecs = []
    for t in texts:  # 智谱一次一条最稳
        data = _post_json(f"{base}/embeddings", {"model": model, "input": t}, env["ZHIPU_API_KEY"])
        vecs.append(data["embeddings"][0]["embedding"])
    return vecs


# ---------------------------------------------------------------- 对外
def provider_name() -> str:
    forced = os.environ.get("KB_EMBED_PROVIDER", "").strip().lower()
    if forced:
        return forced
    env = _env()
    if env.get("QWEN_API_KEY"):
        return "qwen"
    if env.get("ZHIPU_API_KEY"):
        return "zhipu"
    return "mock"


def model_name() -> str:
    """当前 provider 实际使用的 embedding 模型名（给状态接口和界面用）。"""
    name = provider_name()
    if name == "qwen":
        return os.environ.get("KB_EMBED_MODEL", "text-embedding-v4")
    if name == "zhipu":
        return os.environ.get("KB_EMBED_MODEL", "embedding-3")
    return f"hash-{DIM}（离线，无语义）"


def embed(texts: list[str]) -> list[list[float]]:
    """一批文本 → 一批向量。空列表直接返回。"""
    if not texts:
        return []
    name = provider_name()
    if name == "mock":
        return _mock_embed(texts)
    env = _env()
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        part = texts[i : i + BATCH]
        if name == "qwen":
            if not env.get("QWEN_API_KEY"):
                raise RuntimeError("KB_EMBED_PROVIDER=qwen 但 apps/p02/.env 里没有 QWEN_API_KEY")
            out.extend(_qwen_embed(part, env))
        elif name == "zhipu":
            if not env.get("ZHIPU_API_KEY"):
                raise RuntimeError("KB_EMBED_PROVIDER=zhipu 但 apps/p02/.env 里没有 ZHIPU_API_KEY")
            out.extend(_zhipu_embed(part, env))
        else:
            raise RuntimeError(f"未知 embedding provider：{name!r}（可选 qwen / zhipu / mock）")
    if any(len(v) != DIM for v in out):
        bad = next(len(v) for v in out if len(v) != DIM)
        raise RuntimeError(
            f"provider {name} 返回 {bad} 维向量，与 KB_EMBED_DIM={DIM} 不一致；"
            f"请用 KB_EMBED_DIM={bad} 重建（init --rebuild）或换模型"
        )
    return out


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
