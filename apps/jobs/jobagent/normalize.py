"""字段标准化：薪资、经验、学历、公司规模等把原始中文文本转成可比数值。"""
from __future__ import annotations

import re

# 学历层级
EDU_RANK = {"不限": 0, "学历不限": 0, "初中": 1, "初中及以下": 1, "高中": 2, "中专": 3, "中技": 3,
            "大专": 4, "本科": 5, "硕士": 6, "MBA": 6, "博士": 7, "博士后": 8}
EDU_ORDER = ["不限", "高中及以下", "中专", "大专", "本科", "硕士", "博士"]


def parse_edu(text: str | None) -> str:
    if not text:
        return "不限"
    t = str(text)
    for k in ("博士后", "博士", "MBA", "硕士", "研究生", "本科", "大专", "专科", "中专", "中技", "高中", "初中"):
        if k in t:
            if k == "研究生":
                return "硕士"
            if k == "专科":
                return "大专"
            return k
    if "不限" in t or "无要求" in t:
        return "不限"
    return "不限"


def edu_rank(edu: str) -> int:
    return EDU_RANK.get(edu, 0)


def parse_experience(text: str | None) -> tuple[float, float]:
    """返回 (最小年限, 最大年限)。经验不限/无要求 -> (0, 99)。"""
    if not text:
        return (0.0, 99.0)
    t = str(text)
    if "应届" in t or "无经验" in t or "在校" in t:
        return (0.0, 1.0)
    if "不限" in t or "无要求" in t:
        return (0.0, 99.0)
    nums = re.findall(r"\d+(?:\.\d+)?", t)
    if not nums:
        return (0.0, 99.0)
    if "以上" in t or "及以上" in t:
        return (float(nums[0]), 99.0)
    if "以下" in t or "以内" in t:
        return (0.0, float(nums[0]))
    if len(nums) >= 2:
        return (float(nums[0]), float(nums[1]))
    return (float(nums[0]), float(nums[0]))


def parse_size(text: str | None) -> tuple[int, int]:
    """公司规模 -> (人数下限, 上限)。"""
    if not text:
        return (0, 0)
    t = str(text)
    nums = re.findall(r"\d+", t)
    if not nums:
        return (0, 0)
    if "以上" in t:
        return (int(nums[0]), 999999)
    if "少于" in t or "以下" in t:
        return (0, int(nums[0]))
    if len(nums) >= 2:
        return (int(nums[0]), int(nums[1]))
    return (int(nums[0]), int(nums[0]))


def size_bucket(text: str | None) -> str:
    lo, hi = parse_size(text)
    if hi == 0:
        return "未知"
    if hi <= 20:
        return "20人以下"
    if hi <= 99:
        return "20-99人"
    if hi <= 499:
        return "100-499人"
    if hi <= 999:
        return "500-999人"
    if hi <= 9999:
        return "1000-9999人"
    return "10000人以上"


_NUM = r"(\d+(?:\.\d+)?)"


def parse_salary(text: str | None, real: str | None = None, months: float = 0) -> tuple[float | None, float | None, int]:
    """把薪资文本解析成月薪区间。返回 (low, high, 年薪月数)。

    支持：1-1.5万、8千-1万、15-25K、10001-11000、20-30万/年、300元/天、面议。
    """
    # 智联的 salaryReal 已经是规范的月薪数字区间
    if real:
        m = re.match(rf"\s*{_NUM}\s*[-~]\s*{_NUM}\s*$", str(real).replace(",", ""))
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            # 智联 lower bound 常见 10001/12001 这类 +1 尾巴，抹平到百位
            lo = round(lo, -2)
            hi = round(hi, -2)
            if lo > 0 and hi >= lo:
                return (lo, hi, int(months) or 12)
    if not text:
        return (None, None, 0)

    t = str(text).replace(",", "").replace("，", "").replace(" ", "")
    if "面议" in t or "薪资面议" in t:
        return (None, None, 0)

    # 年薪月数
    mm = re.search(r"(\d{2})薪", t)
    pay_months = int(mm.group(1)) if mm else (int(months) if months else 12)

    per_year = ("年" in t and "年薪" in t) or "/年" in t or "万/年" in t
    per_day = "元/天" in t or "/天" in t
    per_hour = "元/时" in t or "/小时" in t

    unit = 1.0
    if "万" in t:
        unit = 10000.0
    elif "千" in t or "k" in t.lower() or "K" in t:
        unit = 1000.0

    m = re.search(rf"{_NUM}\s*[-~—至]\s*{_NUM}\s*(万|千|[kK])?", t)
    if m:
        lo, hi = float(m.group(1)) * unit, float(m.group(2)) * unit
    else:
        m1 = re.search(rf"{_NUM}\s*(万|千|[kK])?\s*(以上|起)", t)
        if m1:
            u = 10000.0 if m1.group(2) == "万" else (1000.0 if m1.group(2) in ("千", "k", "K") else unit)
            lo = float(m1.group(1)) * u
            hi = lo * 1.35
        else:
            m2 = re.search(rf"{_NUM}\s*(万|千|[kK])?", t)
            if not m2:
                return (None, None, 0)
            u = 10000.0 if m2.group(2) == "万" else (1000.0 if m2.group(2) in ("千", "k", "K") else unit)
            lo = hi = float(m2.group(1)) * u

    if per_day:
        lo, hi = lo * 21.75, hi * 21.75
        pay_months = 12
    elif per_hour:
        lo, hi = lo * 8 * 21.75, hi * 8 * 21.75
        pay_months = 12
    elif per_year or (unit == 10000.0 and lo > 100000):
        lo, hi = lo / 12.0, hi / 12.0
        pay_months = 12

    # 单位是"千"但数值像月薪千元，无需处理；极端值兜底
    if lo and hi and (lo < 500 or hi > 500000):
        if lo < 500:
            return (None, None, 0)
        hi = min(hi, 500000)
    return (round(lo, 1), round(hi, 1), pay_months)


def salary_mid(low: float | None, high: float | None) -> float | None:
    if low is None or high is None:
        return None
    return round((low + high) / 2, -2)


def parse_date(text: str | None) -> str:
    if not text:
        return ""
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(text))
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return ""


def clean(text: str | None, limit: int = 0) -> str:
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", str(text))
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = t.replace("\r", " ").strip()
    if limit and len(t) > limit:
        return t[:limit]
    return t
