"""复盘引擎：把 1 分钟序列、日线、全市场广度合成一份可读的专业复盘。

两个时点：
  noon   午盘 11:30 截断 —— 调度器 11:32 触发，5 分钟内出结果
  close  收盘 15:00 截断 —— 调度器 15:06 触发

口径约定（这几条决定了数字能不能对得上）：
  · 成交额/成交量一律由 1 分钟序列累加得到，与交易所口径一致，不用日线估；
  · 量比 = (区间成交量 ÷ 已交易分钟数) ÷ (近 5 日日均成交量 ÷ 240)，标准定义；
  · 量能倍数 = 区间成交额 ÷ 近 20 个交易日「同一时点截断」的成交额均值。
    历史同时点数据来自本地 index_minute；不足 3 日时回退为「近 20 日均额 × 时段占比」，
    该占比在数据不足时按 0.52 兜底，随着分时库积累会自动校准；
  · 换手率只对个股/全市场有定义，指数没有 —— 指数层面用「量比 + 量能倍数」表达活跃度。
"""

from datetime import datetime

from ..collector import db
from ..config import INDICES
from . import stats

SESSION_META = {
    "noon": {"label": "午盘", "cutoff": "11:30", "minutes": 120, "next": "午后"},
    "close": {"label": "收盘", "cutoff": "15:00", "minutes": 240, "next": "次日"},
}

GLOSSARY = {
    "量比": "当日平均每分钟成交量 ÷ 过去 5 个交易日平均每分钟成交量。>1 说明交投比近期活跃，是看「有没有增量资金」最快的指标。",
    "量能倍数": "区间成交额 ÷ 近 20 个交易日「同一时点」的成交额均值。量比看分钟级强度，量能倍数看资金总量级别。",
    "换手率": "成交额 ÷ 流通市值。只有个股与全市场有定义，指数没有。全市场 <1% 清淡，1~2% 温和，2~3% 活跃，>3% 偏过热。",
    "涨跌比": "上涨家数 ÷ 下跌家数。>2.5 普涨，1.2~2.5 结构偏强，0.8~1.2 分化，<0.8 偏弱，<0.4 普跌。",
    "VWAP": "成交量加权均价，即分时图均价线。现价在其上方，说明日内买入筹码平均浮盈，尾段承接偏强。",
    "振幅": "(最高 - 最低) ÷ 昨收，衡量日内多空分歧程度。",
    "涨停家数": "按各板块涨跌幅限制判定：主板 10%（ST 5%）、创业板/科创板 20%、北交所 30%，并排除新股首日无涨跌幅限制的异常值。",
}


# ---------------------------------------------------------------- 通用小工具

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _f(x, nd=2, dash="--"):
    return dash if x is None else f"{x:.{nd}f}"


def _pct_of(a, b):
    return (a / b - 1) * 100 if b else None


def _plus_minutes(hm, delta):
    h, m = hm.split(":")
    total = int(h) * 60 + int(m) + delta
    return "%02d:%02d" % divmod(total, 60)


# ---------------------------------------------------------------- 单指数指标

def _pre_close(conn, code, trade_date):
    row = conn.execute(
        """SELECT trade_date, close FROM index_daily
           WHERE code=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1""",
        (code, trade_date),
    ).fetchone()
    return (row["close"], row["trade_date"]) if row else (None, None)


def _session_share(conn, code, cutoff):
    """估算截断时点成交额占全日比例，用于量能倍数的兜底基准。"""
    if cutoff >= "15:00":
        return 1.0
    rows = conn.execute(
        """SELECT trade_date,
                  SUM(CASE WHEN substr(minute,12,5)<=? THEN amount ELSE 0 END) AS part,
                  SUM(amount) AS full
           FROM index_minute WHERE code=? GROUP BY trade_date""",
        (cutoff, code),
    ).fetchall()
    ratios = [r["part"] / r["full"] for r in rows if r["full"] and r["part"]]
    return _mean(ratios) or 0.52


def _index_block(conn, meta, trade_date, session):
    """单个指数在指定时点的全部量价指标。数据不足返回 None。"""
    code = meta["code"]
    spec = SESSION_META[session]
    cutoff = spec["cutoff"]

    rows = conn.execute(
        """SELECT minute, price, volume, amount FROM index_minute
           WHERE code=? AND trade_date=? AND substr(minute,12,5)<=?
           ORDER BY minute""",
        (code, trade_date, cutoff),
    ).fetchall()
    if not rows:
        return None

    prices = [r["price"] for r in rows if r["price"]]
    if not prices:
        return None

    volume = sum(r["volume"] or 0 for r in rows)
    amount = sum(r["amount"] or 0 for r in rows)
    pv = sum((r["price"] or 0) * (r["volume"] or 0) for r in rows)
    vwap = pv / volume if volume else prices[-1]

    pre_close, pre_date = _pre_close(conn, code, trade_date)
    pre_close = pre_close or prices[0]

    last = prices[-1]
    high, low = max(prices), min(prices)
    first = prices[0]

    # 量比：分钟级强度
    prev5 = conn.execute(
        """SELECT volume FROM index_daily WHERE code=? AND trade_date<?
           ORDER BY trade_date DESC LIMIT 5""",
        (code, trade_date),
    ).fetchall()
    avg_vol5 = _mean([r["volume"] for r in prev5])
    vol_ratio = None
    if avg_vol5:
        per_min_now = volume / spec["minutes"]
        per_min_base = avg_vol5 / 240
        vol_ratio = per_min_now / per_min_base if per_min_base else None

    # 量能倍数：资金总量级别
    hist = conn.execute(
        """SELECT trade_date, SUM(amount) AS a FROM index_minute
           WHERE code=? AND trade_date<? AND substr(minute,12,5)<=?
           GROUP BY trade_date ORDER BY trade_date DESC LIMIT 20""",
        (code, trade_date, cutoff),
    ).fetchall()
    hist_amounts = [r["a"] for r in hist if r["a"]]
    if len(hist_amounts) >= 3:
        energy, basis, energy_days = amount / _mean(hist_amounts), "intraday", len(hist_amounts)
    else:
        prev20 = conn.execute(
            """SELECT amount FROM index_daily WHERE code=? AND trade_date<?
               ORDER BY trade_date DESC LIMIT 20""",
            (code, trade_date),
        ).fetchall()
        base = _mean([r["amount"] for r in prev20])
        share = _session_share(conn, code, cutoff)
        energy = amount / (base * share) if base else None
        basis, energy_days = "daily", len(prev20)

    # 量能分布：开盘首 30 分钟 / 尾段 15 分钟占比
    early_share = tail_share = None
    if cutoff > "10:00":
        early = conn.execute(
            """SELECT SUM(amount) AS a FROM index_minute
               WHERE code=? AND trade_date=? AND substr(minute,12,5)<='10:00'""",
            (code, trade_date),
        ).fetchone()["a"]
        early_share = (early or 0) / amount if amount else None
    if cutoff >= "14:45":
        tail = conn.execute(
            """SELECT SUM(amount) AS a FROM index_minute
               WHERE code=? AND trade_date=? AND substr(minute,12,5)>='14:45'""",
            (code, trade_date),
        ).fetchone()["a"]
        tail_share = (tail or 0) / amount if amount else None

    # 均线与区间位置
    closes = [
        r["close"]
        for r in conn.execute(
            """SELECT close FROM index_daily WHERE code=? AND trade_date<?
               ORDER BY trade_date DESC LIMIT 60""",
            (code, trade_date),
        ).fetchall()
    ]
    ma5 = _mean(closes[:5])
    ma20 = _mean(closes[:20])
    win = closes[:60]
    pos60 = None
    if len(win) >= 20 and max(win) > min(win):
        pos60 = (last - min(win)) / (max(win) - min(win)) * 100

    return {
        "code": code,
        "name": meta["name"],
        "price": round(last, 2),
        "pct": round(_pct_of(last, pre_close), 2),
        "preClose": round(pre_close, 2),
        "open": round(first, 2),
        "gap": round(_pct_of(first, pre_close), 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "amplitude": round((high - low) / pre_close * 100, 2),
        "vwap": round(vwap, 2),
        "vwapDev": round(_pct_of(last, vwap), 2),
        "volume": int(volume),
        "amount": round(amount, 0),
        "volRatio": round(vol_ratio, 2) if vol_ratio else None,
        "volRatioDays": len(prev5),
        "energy": round(energy, 2) if energy else None,
        "energyBasis": basis,
        "energyDays": energy_days,
        "earlyShare": round(early_share * 100, 1) if early_share else None,
        "tailShare": round(tail_share * 100, 1) if tail_share else None,
        "ma5": round(ma5, 2) if ma5 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "aboveMa20": last > ma20 if ma20 else None,
        "pos60": round(pos60, 1) if pos60 is not None else None,
        "barCount": len(rows),
        "prevDate": pre_date,
    }


# ---------------------------------------------------------------- 全市场广度

def _breadth_for(conn, trade_date, session):
    """优先读存档；当日且未过期则实时抓；历史时点无法回溯时返回 None。"""
    saved = db.load_breadth(conn, trade_date, session)
    if saved:
        return saved, "archive"

    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = SESSION_META[session]["cutoff"]
    now_hm = datetime.now().strftime("%H:%M")
    if trade_date != today:
        return None, "unavailable"

    data = stats.market_breadth()
    if not data.get("available"):
        return None, "error"

    # 超过截断时点 10 分钟以上才生成，说明拿到的是更晚的快照，标注为「非当时点」
    data["staleAtPoint"] = now_hm > _plus_minutes(cutoff, 10)
    db.save_breadth(conn, trade_date, session, data)
    return data, "live"


# ---------------------------------------------------------------- 文案生成

def _price_volume_tag(pct, energy):
    if energy is None:
        return ""
    if energy >= 1.15:
        if pct > 0:
            return "量价齐升，属增量资金推动的上涨"
        if pct < 0:
            return "放量下挫，抛压释放较重"
        return "放量滞涨，多空分歧明显加大"
    if energy <= 0.85:
        if pct > 0:
            return "缩量上行，上涨缺少量能支撑，持续性待观察"
        if pct < 0:
            return "缩量回调，抛压相对有限"
        return "缩量整理，多空均无心恋战"
    return "量能基本持平，属存量资金博弈"


def _breadth_tag(b):
    ad = b.get("adRatio")
    if ad is None:
        return ""
    if ad >= 2.5:
        return "个股呈普涨格局，赚钱效应显著"
    if ad >= 1.2:
        return "赚钱效应偏正面，以结构性机会为主"
    if ad >= 0.8:
        return "涨跌分化，多空力量接近均衡"
    if ad >= 0.4:
        return "个股整体偏弱，指数与个股出现背离"
    return "个股普跌，市场情绪明显承压"


def _turnover_tag(t):
    if t is None:
        return ""
    if t >= 3:
        return "筹码交换偏过热，短线分歧通常随之加大"
    if t >= 2:
        return "换手处于活跃区间，资金参与度较高"
    if t >= 1:
        return "换手温和，增量资金有限"
    return "换手清淡，观望情绪主导"


def _compose(indices, breadth, session, trade_date):
    spec = SESSION_META[session]
    label = spec["label"]
    next_word = spec["next"]
    sections = []
    notes = []

    by_code = {x["code"]: x for x in indices}
    sh = by_code.get("000001.SH") or (indices[0] if indices else None)
    if not sh:
        return "本地库暂无该交易日分钟数据，请先执行分时采集。", [], ["无分钟线数据"]

    # fresh=False 表示这份广度快照晚于截断时点采集（事后补生成），
    # 此时家数可用作方向参考，但成交额/换手率不能当作「半日值」引用。
    fresh = bool(breadth) and not breadth.get("staleAtPoint")

    order = ["一", "二", "三", "四", "五", "六", "七"]

    def add(title, text):
        sections.append({"title": f"{order[len(sections)]}、{title}", "text": text})

    # ---- 一句话定调
    amount_desc = "沪市半日成交" if session == "noon" else "沪市成交"
    head = (
        f"{trade_date} {label}，上证指数报 {sh['price']:.2f} 点，"
        f"{'涨' if sh['pct'] >= 0 else '跌'} {abs(sh['pct']):.2f}%"
    )
    if sh["amount"]:
        head += f"，{amount_desc} {sh['amount'] / 1e8:.0f} 亿元"
    if sh["energy"]:
        head += f"，量能 {sh['energy']:.2f} 倍"
    if breadth and breadth.get("adRatio"):
        head += f"，涨跌比 {breadth['adRatio']:.2f}" + ("（收盘口径）" if not fresh else "")
    head += "。"

    # ---- 量价与量能
    t = [
        f"上证指数开于 {sh['open']:.2f} 点（{'高开' if sh['gap'] > 0 else '低开' if sh['gap'] < 0 else '平开'} "
        f"{abs(sh['gap']):.2f}%），最高 {sh['high']:.2f}、最低 {sh['low']:.2f}，振幅 {sh['amplitude']:.2f}%。",
        f"量加权均价（VWAP）{sh['vwap']:.2f}，现价{'高于' if sh['vwapDev'] >= 0 else '低于'}均价 "
        f"{abs(sh['vwapDev']):.2f}%，"
        + (
            "尾段承接偏强、日内买入筹码平均浮盈。"
            if sh["vwapDev"] >= 0
            else "尾段抛压偏重、日内买入筹码平均浮亏。"
        ),
    ]
    if sh["volRatio"]:
        t.append(f"量比 {sh['volRatio']:.2f}（以近 {sh['volRatioDays']} 日均量为基准 1.00）。")
    if sh["energy"]:
        tag = _price_volume_tag(sh["pct"], sh["energy"])
        if sh["energyBasis"] == "intraday":
            t.append(
                f"量能相当于近 {sh['energyDays']} 个交易日同一时段的 {sh['energy']:.2f} 倍，{tag}。"
            )
        else:
            t.append(
                f"量能按日线折算约为近 {sh['energyDays']} 日均值的 {sh['energy']:.2f} 倍"
                f"（分时样本不足，口径偏粗），{tag}。"
            )
    if sh["earlyShare"] is not None:
        if sh["earlyShare"] >= 42:
            t.append(
                f"开盘首 30 分钟成交即占区间总量 {sh['earlyShare']:.1f}%，早盘集中度极高，"
                f"多为情绪盘与抢筹资金，后续量能通常难以维持同等强度。"
            )
        elif sh["earlyShare"] >= 32:
            t.append(
                f"开盘首 30 分钟占区间总量 {sh['earlyShare']:.1f}%，早盘放量明显，属资金主动进攻形态。"
            )
        elif sh["earlyShare"] <= 24:
            t.append(f"开盘首 30 分钟仅占 {sh['earlyShare']:.1f}%，早盘参与意愿谨慎，量能后置。")
        else:
            t.append(f"开盘首 30 分钟占 {sh['earlyShare']:.1f}%，量能分布较为均匀。")
    if sh["tailShare"] is not None:
        t.append(
            f"尾盘 15 分钟成交占全日 {sh['tailShare']:.1f}%，"
            f"{'尾盘有明显资金进出' if sh['tailShare'] >= 8 else '尾盘相对平静'}。"
        )
    add("量价与量能", "".join(t))

    # ---- 涨跌结构
    if breadth:
        b = breadth
        prefix = "" if fresh else "（以下为收盘口径的全市场快照，仅作方向参考）"
        t = [
            prefix,
            f"全市场 {b['traded']} 只可交易个股中 {b['up']} 涨 {b['down']} 跌 {b['flat']} 平"
            + (f"，另有 {b['halted']} 只无成交（停牌或退市整理）" if b.get("halted") else "")
            + f"；上涨家数占比 {_f(b['upRatio'], 1)}%，涨跌比 {_f(b['adRatio'])}。",
            f"涨停 {b['limitUp']} 家、跌停 {b['limitDown']} 家；涨幅超 5% 的 {b['up5']} 家，跌幅超 5% 的 {b['down5']} 家。",
        ]
        tag = _breadth_tag(b)
        if tag:
            t.append(f"{tag}。")
        if b["limitUp"] >= 100:
            t.append("涨停家数破百，短线情绪处于高位，需注意高位股分歧风险。")
        elif b["limitUp"] <= 30:
            t.append("涨停家数偏少，短线接力意愿不足。")
        add("涨跌结构", "".join(t))
        if not fresh:
            notes.append("本次生成晚于 11:30 时点，全市场广度取的是更晚的快照，家数可看方向，金额与换手不可当半日值。")
    else:
        add(
            "涨跌结构",
            "该交易日的全市场广度未能获取（免费接口不支持历史时点回溯），本节从略。",
        )
        notes.append("全市场广度缺失：涨跌家数、涨停数、换手率均无法计算，仅保留指数层面指标。")

    # ---- 换手与筹码（只在时点吻合时给，避免把收盘换手当半日换手）
    if breadth and fresh and breadth.get("turnover"):
        b = breadth
        t = [
            f"全市场成交额 {b['amount'] / 1e8:.0f} 亿元，对应流通市值 {b['floatCap'] / 1e12:.2f} 万亿元，"
            f"整体换手率 {b['turnover']:.2f}%，{_turnover_tag(b['turnover'])}。",
        ]
        if b.get("avgTurnover") and abs(b["avgTurnover"] - b["turnover"]) >= 0.15:
            t.append(
                f"市值加权个股平均换手 {b['avgTurnover']:.2f}%，与整体换手相差 "
                f"{abs(b['avgTurnover'] - b['turnover']):.2f} 个百分点，"
                f"说明成交{'向大市值集中' if b['turnover'] > b['avgTurnover'] else '偏中小市值个股'}。"
            )
        else:
            t.append("整体换手与市值加权个股换手基本吻合，成交分布较均衡。")
        add("换手与筹码", "".join(t))

        board_rows = [x for x in b.get("boards", []) if x["count"]]
        if board_rows:
            ordered = sorted(board_rows, key=lambda x: -(x["pct"] if x["pct"] is not None else -99))
            strong, weak = ordered[0], ordered[-1]
            add(
                "板块结构",
                f"分市场看，{strong['name']}加权涨幅 {_f(strong['pct'])}% 最强，"
                f"{weak['name']} {_f(weak['pct'])}% 最弱；换手率 {strong['name']} "
                f"{_f(strong['turnover'])}%、{weak['name']} {_f(weak['turnover'])}%。"
                f"成交额分别为 {strong['amount'] / 1e8:.0f} 亿元与 {weak['amount'] / 1e8:.0f} 亿元。",
            )
    elif breadth and not fresh:
        notes.append("换手率与板块结构未纳入本节：换手率必须取 11:30 时点快照，事后补算会失真。")

    # ---- 风格与分化
    style = None
    large = by_code.get("000300.SH")
    small = by_code.get("899050.BJ")
    growth = by_code.get("399006.SZ")
    if large and small:
        diff = small["pct"] - large["pct"]
        if diff >= 0.5:
            style = f"北证50 跑赢沪深300 {diff:.2f} 个百分点，小盘风格明显占优"
        elif diff <= -0.5:
            style = f"沪深300 跑赢北证50 {abs(diff):.2f} 个百分点，大盘权重风格占优"
        else:
            style = "沪深300 与北证50 涨幅接近，大小盘风格相对均衡"
    spread = max(x["pct"] for x in indices) - min(x["pct"] for x in indices)
    t = [
        "、".join(f"{x['name']} {x['pct']:+.2f}%" for x in indices) + "。",
        f"指数间最大分化 {spread:.2f} 个百分点，" + (f"{style}。" if style else "结构相对均衡。"),
    ]
    if large and growth:
        t.append(
            f"成长（创业板指 {growth['pct']:+.2f}%）与价值（沪深300 {large['pct']:+.2f}%）"
            f"的差值为 {growth['pct'] - large['pct']:+.2f} 个百分点。"
        )
    add("风格与分化", "".join(t))

    # ---- 观察要点
    t = []
    if sh["energy"] and sh["pct"] > 0 and sh["energy"] >= 1.15 and sh["vwapDev"] >= 0:
        t.append(f"量价配合良好，{next_word}关注能否守住 VWAP 均价线，守住则上行结构未破。")
    elif sh["pct"] > 0 and sh["energy"] and sh["energy"] <= 0.85:
        t.append(f"缩量上涨缺少量能确认，{next_word}重点看量能能否补上，否则易冲高回落。")
    elif sh["pct"] < 0 and sh["energy"] and sh["energy"] >= 1.15:
        t.append(f"放量下跌需警惕，{next_word}若无法快速收回均价线，调整可能延续。")
    elif sh["pct"] < 0:
        t.append(f"缩量回调，{next_word}关注前期均线与成交密集区的支撑力度。")
    else:
        t.append(f"{next_word}关注量能能否延续，以及指数在 VWAP 均价线附近的争夺。")

    if breadth and breadth.get("limitUp", 0) >= 80 and sh["pct"] <= 0.2:
        t.append("涨停家数偏高而指数滞涨，属典型结构性行情，需注意高低切换节奏。")
    if sh["aboveMa20"] is False:
        t.append("指数运行于 20 日均线下方，中期结构仍待修复。")
    elif sh["aboveMa20"] is True:
        t.append("指数站稳 20 日均线，中期结构保持完好。")
    if sh["pos60"] is not None:
        t.append(f"当前价处于近 60 日区间的 {sh['pos60']:.0f}% 分位。")
    if session == "noon":
        t.append("需说明：午盘数据仅覆盖上午 120 分钟，全天量能与广度以收盘为准。")
    add("观察要点", "".join(t))

    return head, sections, notes


# ---------------------------------------------------------------- 对外入口

def _resolve_trade_date(conn, trade_date):
    if trade_date:
        return trade_date
    today = datetime.now().strftime("%Y-%m-%d")
    if conn.execute(
        "SELECT 1 FROM index_minute WHERE trade_date=? LIMIT 1", (today,)
    ).fetchone():
        return today
    row = conn.execute("SELECT MAX(trade_date) AS d FROM index_minute").fetchone()
    if row and row["d"]:
        return row["d"]
    row = conn.execute("SELECT MAX(trade_date) AS d FROM index_daily").fetchone()
    return row["d"] if row and row["d"] else today


def build_review(session="noon", trade_date=None, persist=True):
    """生成复盘。session: noon（午盘 11:30）/ close（收盘 15:00）。"""
    if session not in SESSION_META:
        raise ValueError(f"不支持的时点: {session}")
    spec = SESSION_META[session]

    conn = db.connect()
    db.init_db(conn)
    try:
        trade_date = _resolve_trade_date(conn, trade_date)
        indices = []
        for meta in INDICES:
            block = _index_block(conn, meta, trade_date, session)
            if block:
                indices.append(block)
        if not indices:
            return {
                "tradeDate": trade_date,
                "session": session,
                "label": spec["label"],
                "available": False,
                "message": f"{trade_date} 无 {spec['cutoff']} 前的分钟数据，请先执行分时采集",
                "headline": "",
                "sections": [],
                "notes": [],
                "indices": [],
                "breadth": None,
                "glossary": GLOSSARY,
            }

        complete = max(x["barCount"] for x in indices)
        breadth, breadth_source = _breadth_for(conn, trade_date, session)
        headline, sections, notes = _compose(indices, breadth, session, trade_date)
        if complete < spec["minutes"] * 0.9:
            notes.append(
                f"当日分钟序列仅 {complete} 个点（预期约 {spec['minutes']} 个），"
                f"数据可能不完整，量能与换手指标偏低。"
            )
        samples = [x["energyDays"] for x in indices if x.get("energyBasis") == "intraday"]
        if samples and min(samples) < 10:
            notes.append(
                f"量能倍数的历史基准目前只有 {min(samples)} 个交易日样本，"
                f"参考价值有限；分时库每多存一天就多一个样本，会自动趋于稳定。"
            )

        summary = headline + "\n" + "\n".join(f"{s['title']}：{s['text']}" for s in sections)
        payload = {
            "tradeDate": trade_date,
            "session": session,
            "label": spec["label"],
            "cutoff": spec["cutoff"],
            "available": True,
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "headline": headline,
            "summary": summary,
            "sections": sections,
            "notes": notes,
            "indices": indices,
            "breadth": breadth,
            "breadthSource": breadth_source,
            "glossary": GLOSSARY,
            "disclaimer": "数据由本地采集服务基于公开行情计算，仅作数据观察，不构成投资建议。",
        }
        if persist:
            db.save_review(conn, trade_date, session, headline, summary, payload)
        return payload
    finally:
        conn.close()


def get_review(session="noon", trade_date=None, refresh=False):
    """优先返回存档（保证午盘 11:32 生成的版本不被后续快照覆盖），没有再现场生成。"""
    conn = db.connect()
    try:
        resolved = _resolve_trade_date(conn, trade_date)
        if not refresh:
            saved = db.load_review(conn, resolved, session)
            if saved:
                return saved
    finally:
        conn.close()
    return build_review(session=session, trade_date=resolved, persist=True)


def get_noon_brief(trade_date=None):
    return get_review("noon", trade_date)


def history(limit=20):
    conn = db.connect()
    try:
        return db.recent_reviews(conn, limit)
    finally:
        conn.close()
