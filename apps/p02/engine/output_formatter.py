# -*- coding: utf-8 -*-
"""浅色清爽公众号排版生成器。产出内联样式 HTML，可直接复制到公众号编辑器。"""

BG = "#FFFFFF"
BG_GRAD = "linear-gradient(160deg,#F0FDFA 0%,#F0F9FF 100%)"
BG_CARD = "#F9FAFB"
ACCENT = "#14B8A6"
BLUE = "#0EA5E9"
PURPLE = "#8B5CF6"
TEXT = "#1F2937"
TEXT_DIM = "#6B7280"
WHITE = "#111827"


def render_article(article, image_urls=None):
    """article: {title, subtitle, sections:[{heading, content, image_suggestion, image_source}]}
    image_urls: {section_index(int,0-based): 图片URL}，方案C回传后使用。"""
    image_urls = image_urls or {}
    title = article.get("title") or "未命名文章"
    subtitle = article.get("subtitle") or ""
    sections = article.get("sections") or []

    out = []
    out.append(
        f'<div style="background:{BG};color:{TEXT};font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;line-height:1.9;font-size:15px;max-width:680px;margin:0 auto;letter-spacing:0.3px;">'
    )

    # 顶部主标题
    out.append(
        f'<div style="padding:42px 26px 30px;text-align:center;background:{BG_GRAD};border-bottom:2px solid {ACCENT};box-shadow:0 2px 24px rgba(20,184,166,0.12);">'
    )
    out.append(f'<div style="font-size:12px;letter-spacing:5px;color:{BLUE};margin-bottom:14px;">✦ 深度内容 ✦</div>')
    out.append(
        f'<h1 style="margin:0;font-size:27px;font-weight:800;color:{WHITE};letter-spacing:1px;line-height:1.4;">{title}</h1>'
    )
    if subtitle:
        out.append(f'<p style="margin:16px 0 0;font-size:14px;color:{TEXT};letter-spacing:1px;">{subtitle}</p>')
    out.append('</div>')

    # 正文各板块
    for i, sec in enumerate(sections):
        heading = sec.get("heading") or ("板块 %d" % (i + 1))
        content = sec.get("content") or []
        img_sug = sec.get("image_suggestion") or ""
        img_src = sec.get("image_source") or ""
        local_img = image_urls.get(i)

        out.append('<div style="padding:30px 26px 22px;border-bottom:1px solid rgba(20,184,166,0.15);">')
        # 大标题：编号 + 青蓝竖条
        out.append(
            f'<h2 style="margin:0;font-size:21px;font-weight:700;color:{WHITE};border-left:6px solid {ACCENT};padding-left:14px;line-height:1.5;">'
        )
        out.append(f'<span style="color:{ACCENT};font-size:18px;margin-right:10px;">0{i+1}</span>{heading}</h2>')
        out.append(f'<div style="height:1px;background:linear-gradient(90deg,{ACCENT},transparent);margin:14px 0 20px;"></div>')

        # 配图位
        if local_img:
            out.append('<div style="margin:14px 0 22px;text-align:center;">')
            out.append(
                f'<img src="{local_img}" alt="{heading}" style="max-width:100%;border-radius:8px;box-shadow:0 0 0 2px {BLUE},0 4px 16px rgba(14,165,233,0.2);" />'
            )
            out.append('</div>')
        else:
            out.append(
                f'<div style="margin:14px 0 22px;padding:16px;background:{BG_CARD};border-radius:8px;border:1px dashed {BLUE};font-size:13px;color:{TEXT};">'
            )
            out.append(f'<div style="color:{ACCENT};font-weight:600;margin-bottom:6px;">📷 配图建议（方案C：按来源自行下载后回传）</div>')
            out.append(f'<div>{img_sug or "（无配图建议）"}</div>')
            if img_src:
                out.append(f'<div style="margin-top:6px;color:{TEXT_DIM};">检索关键词 / 来源：{img_src}</div>')
            out.append('</div>')

        # 正文
        for para in content:
            p = (para or "").strip()
            if not p:
                continue
            if p.startswith(("•", "·", "- ")) or (len(p) > 2 and p[1] == "." and p[0].isdigit()):
                out.append(f'<p style="margin:6px 0;padding-left:16px;color:{TEXT};line-height:1.8;">{p}</p>')
            else:
                out.append(f'<p style="margin:10px 0;color:{TEXT};line-height:1.85;text-align:justify;">{p}</p>')

        out.append('</div>')

    # 结尾
    out.append(
        f'<div style="padding:30px 26px 36px;text-align:center;font-size:12px;color:{TEXT_DIM};letter-spacing:2px;">— END · 本文由本地文案智能体生成 —</div>'
    )
    out.append('</div>')
    return "".join(out)