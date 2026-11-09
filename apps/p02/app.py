# -*- coding: utf-8 -*-
"""微信小程序文案生成智能体 - Flask 主服务（本地部署，端口 8978）。"""
import os
import re
import json
import time
import uuid

from flask import Flask, request, jsonify, render_template, send_from_directory

from engine import config as cfg
from engine import llm_client, prompt_builder, output_formatter, image_plan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB 上传上限

# 内存态：article_id -> {"article": dict, "images": {idx: url}}
STORE = {}


def _extract_json(text):
    """从大模型返回文本中稳健提取 JSON 对象。"""
    if not text:
        raise RuntimeError("大模型返回为空")
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        s = text.find("{")
        e = text.rfind("}")
        if s != -1 and e != -1 and e > s:
            text = text[s:e + 1]
    try:
        return json.loads(text)
    except Exception:
        raise RuntimeError("大模型返回的内容不是合法 JSON，请重试或换一个模型")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory(IMAGES_DIR, filename)


@app.route("/api/vendors")
def api_vendors():
    return jsonify({
        "vendors": cfg.list_vendors(),
        "active_vendor": cfg.load_env().get("ACTIVE_VENDOR", "deepseek"),
    })


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True, silent=True) or {}
    vendor = data.get("vendor") or "deepseek"
    fields = data.get("fields") or {}

    if not (fields.get("topic") or "").strip():
        return jsonify({"ok": False, "error": "主题不能为空"}), 400

    conf = cfg.get_vendor_config(vendor)
    # 允许界面临时覆盖 base_url / model（api_key 仍只从 .env 读）
    if data.get("base_url"):
        conf["base_url"] = data["base_url"].strip()
    if data.get("model"):
        conf["model"] = data["model"].strip()

    if not conf["api_key"]:
        return jsonify({
            "ok": False,
            "error": "未配置 API KEY，请在 .env 文件填写 %s_API_KEY" % conf["vendor"].upper(),
        }), 400

    messages = prompt_builder.build_messages(fields)
    try:
        raw = llm_client.chat_completion(conf["base_url"], conf["api_key"], conf["model"], messages)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    try:
        article = _extract_json(raw)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "raw": raw[:500]}), 500

    if not isinstance(article, dict):
        article = {"title": str(article), "sections": []}
    article.setdefault("sections", [])

    article_id = uuid.uuid4().hex[:12]
    STORE[article_id] = {"article": article, "images": {}}
    html = output_formatter.render_article(article, {})

    return jsonify({
        "ok": True,
        "article_id": article_id,
        "html": html,
        "article": article,
        "image_plan": cfg.IMAGE_PLAN,
        "image_plan_label": image_plan.ImagePlan(cfg.IMAGE_PLAN, cfg.SEARCH_ENABLED).label(),
        "vendor_name": conf["name"],
        "model": conf["model"],
    })


@app.route("/api/upload_image", methods=["POST"])
def api_upload_image():
    article_id = request.form.get("article_id", "")
    section_index = request.form.get("section_index", "")
    f = request.files.get("image")
    rec = STORE.get(article_id)
    if not rec:
        return jsonify({"ok": False, "error": "会话已失效，请重新生成"}), 400
    if not f or f.filename == "":
        return jsonify({"ok": False, "error": "未收到图片"}), 400
    try:
        idx = int(section_index)
    except Exception:
        idx = 0

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".png"
    name = "img_%s_%d%s" % (article_id, idx, ext)
    f.save(os.path.join(IMAGES_DIR, name))
    url = "/images/" + name

    rec["images"][idx] = url
    html = output_formatter.render_article(rec["article"], rec["images"])
    return jsonify({"ok": True, "html": html, "url": url})


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "time": time.time()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8978"))
    print("文案生成智能体已启动：http://127.0.0.1:%d" % port)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)