"""模型中心数据源：读内嵌 P-02 的真实厂商与模型配置。

模型中心页以前是一份演示数据（GPT-5.2 / Claude 4.6 …）。工作台融合后，
真正在用的模型来自 apps/p02（文案生成 Agent）：它的 engine/config.py
定义厂商预设，.env 决定每个厂商实际生效的 model / base_url / key。

这里只输出「生效模型 + 接入点 + key 是否已配」，绝不返回 key 明文。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

P02_DIR = Path(__file__).resolve().parent.parent / "apps" / "p02"


def _load_config():
    """动态加载 apps/p02/engine/config.py（纯标准库，加载无副作用）。"""
    if not (P02_DIR / "engine" / "config.py").is_file():
        return None
    mod_name = "_p02_engine_config"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, P02_DIR / "engine" / "config.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def snapshot() -> dict[str, Any]:
    """模型中心快照：真实厂商、生效模型、接入点、key 状态。"""
    cfg = _load_config()
    if not cfg:
        return {"ok": False, "error": "apps/p02 不存在，模型中心没有真实数据源"}

    env = cfg.load_env()          # .env 实际内容（key 只用于判断，不外传）
    vendors = cfg.VENDORS         # 厂商预设：name / base_url / models / env_prefix
    active = env.get("ACTIVE_VENDOR", "deepseek")

    models = []
    for key, v in vendors.items():
        prefix = v.get("env_prefix") or key.upper()
        env_model = (env.get(f"{prefix}_MODEL") or "").strip()
        env_base = (env.get(f"{prefix}_BASE_URL") or "").strip()
        model = env_model or (v.get("models") or ["—"])[0]
        base_url = env_base or v.get("base_url") or ""
        models.append(
            {
                "key": key,
                "vendor": v.get("name") or key,
                "model": model,
                "base_url": base_url,
                "key_set": bool((env.get(f"{prefix}_API_KEY") or "").strip()),
                "options": list(v.get("models") or []),
                "active": key == active,
            }
        )

    return {
        "ok": True,
        "source": "apps/p02 · 文案生成 Agent（.env 实际生效配置）",
        "active_vendor": active,
        "models": models,
    }
