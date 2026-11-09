"""全局配置：指数清单、路径、分时槽位。"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"
DB_PATH = DATA_DIR / "market.db"

# 首批 6 个大盘指数。secid 为东方财富口径：1=沪市，0=深市/北交所
INDICES = [
    {"code": "000001.SH", "name": "上证指数", "secid": "1.000001", "market": "SH", "sort_no": 1},
    {"code": "399001.SZ", "name": "深证成指", "secid": "0.399001", "market": "SZ", "sort_no": 2},
    {"code": "399006.SZ", "name": "创业板指", "secid": "0.399006", "market": "SZ", "sort_no": 3},
    {"code": "000688.SH", "name": "科创50", "secid": "1.000688", "market": "SH", "sort_no": 4},
    {"code": "000300.SH", "name": "沪深300", "secid": "1.000300", "market": "SH", "sort_no": 5},
    {"code": "899050.BJ", "name": "北证50", "secid": "0.899050", "market": "BJ", "sort_no": 6},
]

INDEX_MAP = {i["code"]: i for i in INDICES}
SECID_MAP = {i["secid"]: i["code"] for i in INDICES}

PERIODS = ("minute", "day", "week", "month", "year")


def minute_slots():
    """分时图的固定横轴：09:30-11:30 + 13:00-15:00，共 242 个分钟槽。"""
    slots = []
    for m in range(9 * 60 + 30, 11 * 60 + 31):
        slots.append("%02d:%02d" % divmod(m, 60))
    for m in range(13 * 60, 15 * 60 + 1):
        slots.append("%02d:%02d" % divmod(m, 60))
    return slots


MINUTE_SLOTS = minute_slots()
