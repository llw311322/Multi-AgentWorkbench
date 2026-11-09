# -*- coding: utf-8 -*-
"""配置加载与厂商预设。base_url / api_key / model 由本地 .env 文件提供。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# 厂商预设：base_url 与默认模型，api_key 需用户在 .env 填写
VENDORS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "env_prefix": "DEEPSEEK",
    },
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"],
        "env_prefix": "QWEN",
    },
    "kimi": {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-k2-0711-preview"],
        "env_prefix": "KIMI",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long"],
        "env_prefix": "ZHIPU",
    },
    "doubao": {
        "name": "豆包（火山方舟）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-seed-2-1-pro-260628", "doubao-seed-2-1-turbo", "doubao-seed-2-1-flash"],
        "env_prefix": "DOUBAO",
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "models": [],
        "env_prefix": "CUSTOM",
    },
}

# 预留开关（后续接入）
SEARCH_ENABLED = False  # 是否接入联网搜索（方案B 与外部知识来源扩展）
IMAGE_PLAN = "C"        # 图片方案：A=AI生图 / B=联网下载 / C=给来源+用户回传


def load_env():
    """读取 .env，返回 dict；同时 setdefault 到 os.environ。"""
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                env[key] = val
                os.environ.setdefault(key, val)
    return env


def get_vendor_config(vendor_key):
    """根据厂商 key 取 base_url / model / api_key。"""
    vendor_key = (vendor_key or "deepseek").lower()
    vendor = VENDORS.get(vendor_key) or VENDORS["deepseek"]
    prefix = vendor["env_prefix"]
    env = load_env()
    base_url = env.get(prefix + "_BASE_URL") or vendor["base_url"]
    model = env.get(prefix + "_MODEL") or (vendor["models"][0] if vendor["models"] else "")
    api_key = env.get(prefix + "_API_KEY", "")
    return {
        "vendor": vendor_key,
        "name": vendor["name"],
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "models": vendor["models"],
    }


def list_vendors():
    """返回厂商列表（不含 api_key 明文，仅标记是否已配置）。"""
    env = load_env()
    result = []
    for key, v in VENDORS.items():
        result.append({
            "key": key,
            "name": v["name"],
            "base_url": v["base_url"],
            "models": v["models"],
            "has_key": bool(env.get(v["env_prefix"] + "_API_KEY")),
        })
    return result