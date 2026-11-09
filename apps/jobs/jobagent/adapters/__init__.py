"""采集适配器注册表。"""
from __future__ import annotations

from typing import Any

from .base import BaseAdapter, SourceError
from .liepin import LiepinAdapter
from .zhaopin import ZhaopinAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "zhaopin": ZhaopinAdapter,
    "liepin": LiepinAdapter,
}

LABELS = {"zhaopin": "智联招聘", "liepin": "猎聘"}


def build(source: str, settings: dict[str, Any] | None = None) -> BaseAdapter:
    settings = settings or {}
    cls = ADAPTERS.get(source)
    if not cls:
        raise KeyError(f"未知数据源: {source}")
    cookies = (settings.get("cookies") or {}).get(source, "")
    return cls(cookies=cookies)


def all_status(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out = []
    for name in ADAPTERS:
        try:
            out.append(build(name, settings).status())
        except Exception as exc:  # pragma: no cover
            out.append({"name": name, "label": LABELS.get(name, name), "ready": False,
                        "requires_cookie": True, "has_cookie": False, "note": str(exc)})
    return out


__all__ = ["ADAPTERS", "LABELS", "build", "all_status", "BaseAdapter", "SourceError"]
