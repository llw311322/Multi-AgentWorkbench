"""会话与文件存储（JSON + CSV，便于直接查看/备份）。"""
from __future__ import annotations

import csv
import json
import os
import threading
from typing import Any

_LOCK = threading.Lock()


class Store:
    def __init__(self, data_dir: str = "data"):
        self.root = os.path.abspath(data_dir)
        self.sessions = os.path.join(self.root, "sessions")
        self.outputs = os.path.join(self.root, "outputs")
        os.makedirs(self.sessions, exist_ok=True)
        os.makedirs(self.outputs, exist_ok=True)

    def _path(self, sid: str) -> str:
        safe = "".join(c for c in sid if c.isalnum() or c in "-_")[:64] or "default"
        return os.path.join(self.sessions, f"{safe}.json")

    def load(self, sid: str) -> dict[str, Any]:
        p = self._path(sid)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                state = {}
        else:
            state = {}
        state["_sid"] = sid
        return state

    def save(self, sid: str, state: dict[str, Any]) -> None:
        state = dict(state)
        state["_sid"] = sid
        p = self._path(sid)
        with _LOCK:
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)

    def write_report(self, sid: str, filename: str, html: str) -> str:
        p = os.path.join(self.outputs, filename)
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        return p

    def write_csv(self, sid: str, filename: str, jobs: list[dict[str, Any]]) -> str:
        p = os.path.join(self.outputs, filename)
        cols = [("title", "岗位"), ("company", "公司"), ("salary_text", "薪资原文"),
                ("salary_mid", "月薪中位数"), ("city", "城市"), ("district", "区域"),
                ("exp_text", "经验"), ("edu", "学历"), ("industry", "行业"),
                ("company_size", "公司规模"), ("company_type", "公司性质"),
                ("skills", "技能"), ("publish_date", "发布时间"), ("source", "来源"),
                ("url", "链接"), ("jd", "JD")]
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([c[1] for c in cols])
            for j in jobs:
                row = []
                for key, _ in cols:
                    v = j.get(key)
                    if isinstance(v, list):
                        v = "、".join(str(x) for x in v)
                    row.append("" if v is None else str(v))
                w.writerow(row)
        return p
