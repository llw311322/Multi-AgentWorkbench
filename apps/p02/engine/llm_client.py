# -*- coding: utf-8 -*-
"""大模型调用客户端，基于 OpenAI 兼容协议（DeepSeek / 千问 / Kimi / 智谱通用）。"""
import requests


def chat_completion(base_url, api_key, model, messages,
                    temperature=0.8, max_tokens=4000, timeout=180):
    """调用 chat/completions，返回文本内容。"""
    if not api_key:
        raise RuntimeError("未检测到 api_key：请在 .env 文件中填写对应厂商的 API KEY")
    if not base_url:
        raise RuntimeError("base_url 为空：请在 .env 或界面配置表填写")
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        raise RuntimeError("调用超时（超过 %d 秒），可尝试减少字数或换更快的模型" % timeout)
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError("无法连接 base_url，请检查网络或地址：%s" % str(e)[:120])

    if resp.status_code != 200:
        raise RuntimeError("调用失败 HTTP %d：%s" % (resp.status_code, resp.text[:400]))

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("返回结构异常：%s" % resp.text[:400])