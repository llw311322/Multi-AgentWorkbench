# -*- coding: utf-8 -*-
"""图片方案策略。A=AI生图 / B=联网下载 / C=给来源+用户回传。当前默认 C，A/B 预留接口。"""


class ImagePlan:
    PLAN_A = "A"
    PLAN_B = "B"
    PLAN_C = "C"

    def __init__(self, plan="C", search_enabled=False):
        self.plan = plan
        self.search_enabled = search_enabled

    def label(self):
        return {
            "A": "AI 生成图片",
            "B": "联网下载",
            "C": "给来源 + 用户回传",
        }.get(self.plan, self.plan)

    def supports_upload(self):
        """当前方案是否支持用户本地回传图片（方案C 支持）。"""
        return self.plan == self.PLAN_C

    def generate_image(self, suggestion):
        """方案A：AI 生成图片（预留，未接入文生图 API）。"""
        raise NotImplementedError("方案A（AI 生图）预留中，暂未接入")

    def search_image(self, suggestion):
        """方案B：联网搜索下载（预留，依赖 SEARCH_ENABLED 搜索引擎接入）。"""
        raise NotImplementedError("方案B（联网搜图）预留中，需先接入搜索引擎")