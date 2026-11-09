# -*- coding: utf-8 -*-
"""提示词模板编排：智能体负责结构化模板与流程控制，主题拆解与生成交给厂商大模型。"""

TYPE_LABELS = {
    "report": "报告",
    "copywriting": "文案",
    "analysis": "解析",
    "interpretation": "解读",
    "learning": "知识深度学习",
    "steps": "学习步骤 / 内容列举",
    "tutorial": "教程",
    "custom": "自定义",
}

SYSTEM_PROMPT = """你是一位资深的中文新媒体文案专家，擅长微信公众号深度内容的主题拆解、结构化写作与排版规划。

收到一个主题后，你的工作方式是：
1. 先独立思考该主题应拆分为哪 3~5 个逻辑板块（大标题），每个板块聚焦一个清晰的知识点、观点或问题；
2. 每个板块写出信息密度高、有观点、可直接成稿的正文内容，用若干段落或要点阐述；
3. 为每个板块给出「配图建议」（描述这张图应表达什么）和「图片来源建议」（检索关键词或来源渠道，供用户自行下载）。

写作要求：
- 语言专业、流畅、接地气，避免空话套话与正确的废话；
- 严格遵循用户给定的字数、读者、语气等约束；
- 每个大标题下内容充实，结构层级清晰；
- 只输出最终结果，不要解释你的思考过程，不要输出任何 JSON 之外的文字。"""


def build_messages(fields):
    """把界面字段组装成 messages。fields 含 occupation/field/industry/topic/content_type/word_limit/audience/tone/extra。"""
    lines = ["请根据以下要求，生成一篇公众号深度内容（含排版结构）。", ""]
    if fields.get("occupation"):
        lines.append("- 我的职业 / 身份：%s" % fields["occupation"])
    if fields.get("field"):
        lines.append("- 所属领域：%s" % fields["field"])
    if fields.get("industry"):
        lines.append("- 所属行业：%s" % fields["industry"])
    lines.append("- 内容主题：%s" % (fields.get("topic") or ""))
    ctype = fields.get("content_type") or "copywriting"
    lines.append("- 内容类型：%s" % TYPE_LABELS.get(ctype, ctype))
    if fields.get("word_limit"):
        lines.append("- 字数限制：约 %s 字（可上下浮动 10%%）" % fields["word_limit"])
    if fields.get("audience"):
        lines.append("- 目标读者：%s" % fields["audience"])
    if fields.get("tone"):
        lines.append("- 语气风格：%s" % fields["tone"])
    if fields.get("extra"):
        lines.append("- 补充要求：%s" % fields["extra"])
    lines.append("")
    lines.append("输出格式要求：请严格输出一个 JSON 对象，不要输出任何其它文字或代码块标记，结构如下：")
    lines.append('{"title":"文章主标题","subtitle":"副标题或一句导语",')
    lines.append('"sections":[{"heading":"大标题1","content":["正文段落1","要点2"],"image_suggestion":"配图建议","image_source":"检索关键词/来源"},')
    lines.append('{"heading":"大标题2",...} ... 共 3~5 个 section]}')
    lines.append("")
    lines.append("说明：每个 section 的 heading 是一个大标题；content 是字符串数组（每项一段正文或一个要点）；image_suggestion 描述配图内容，image_source 给出可检索的关键词或来源渠道。")

    user_prompt = "\n".join(lines)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]