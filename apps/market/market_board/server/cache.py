"""进程内 TTL 缓存：保护上游接口，避免盘中重复请求把限频打满。"""

import time

_store = {}


def cached(key, ttl, producer):
    hit = _store.get(key)
    if hit is not None and time.time() - hit[0] < ttl:
        return hit[1]
    value = producer()
    _store[key] = (time.time(), value)
    return value


def invalidate(prefix=""):
    for key in [k for k in list(_store) if k.startswith(prefix)]:
        _store.pop(key, None)


def stats():
    return {k: round(time.time() - v[0], 1) for k, v in _store.items()}
