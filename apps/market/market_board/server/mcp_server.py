"""MCP Server（stdio 传输，JSON-RPC 2.0）。

注册后 Agent 可直接调用三个工具读取本地行情库，用于生成每日复盘。

在工作台 MCP 配置里添加：
{
  "market-board": {
    "command": "python",
    "args": ["-m", "market_board.server.mcp_server"],
    "cwd": "<本项目根目录>"
  }
}

自测：
  echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m market_board.server.mcp_server
"""

import json
import sys

from ..config import INDEX_MAP
from . import review, service

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "get_market_brief",
        "description": (
            "获取 A 股主要指数当日复盘摘要：涨跌幅、成交额、MA5/20/60 位置、"
            "量能倍数、60 日分位。返回结构化 JSON 与一句可读 summary。无需参数。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_index_kline",
        "description": "获取指数 K 线序列（日/周/月/年），用于画图或计算技术指标。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "指数代码，如 000001.SH / 399006.SZ / 000688.SH",
                },
                "period": {
                    "type": "string",
                    "enum": ["day", "week", "month", "year"],
                    "default": "day",
                },
                "limit": {"type": "integer", "default": 60, "minimum": 1, "maximum": 2000},
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_noon_brief",
        "description": (
            "获取午盘（11:30 截断）专业复盘：成交量、量能倍数、量比、换手率、涨跌家数、"
            "涨停数、VWAP 偏离、风格分化，附分节文字解读与术语口径。"
            "午盘复盘由服务在 11:32 自动生成并存档；传 session=close 可取收盘版。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "enum": ["noon", "close"],
                    "default": "noon",
                    "description": "noon=午盘 11:30，close=收盘 15:00",
                },
                "date": {
                    "type": "string",
                    "description": "交易日 YYYY-MM-DD，不传则为最近交易日",
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 时忽略存档重新计算",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_intraday",
        "description": "获取指数当日 1 分钟分时序列（含昨收基准与量加权均价）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "指数代码，如 000001.SH"},
                "compact": {
                    "type": "boolean",
                    "default": True,
                    "description": "true 时只返回有成交的分钟点，节省 token",
                },
            },
            "required": ["code"],
        },
    },
]

SUPPORTED_CODES = sorted(INDEX_MAP.keys())


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _text_payload(data):
    return {
        "content": [
            {"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}
        ]
    }


def call_tool(name, args):
    if name == "get_market_brief":
        return service.get_brief()

    if name == "get_noon_brief":
        session = args.get("session", "noon")
        if session not in ("noon", "close"):
            raise ValueError("session 只能是 noon 或 close")
        return review.get_review(
            session=session,
            trade_date=args.get("date"),
            refresh=bool(args.get("refresh", False)),
        )

    if name == "get_index_kline":
        code = args.get("code", "")
        if code not in INDEX_MAP:
            raise ValueError(f"未知指数代码 {code}，可用：{', '.join(SUPPORTED_CODES)}")
        period = args.get("period", "day")
        limit = int(args.get("limit", 60))
        data = service.get_kline(code, period, limit)
        return {
            "code": data["code"],
            "name": data["name"],
            "period": data["period"],
            "asOf": data["asOf"],
            "bars": data["bars"],
        }

    if name == "get_intraday":
        code = args.get("code", "")
        if code not in INDEX_MAP:
            raise ValueError(f"未知指数代码 {code}，可用：{', '.join(SUPPORTED_CODES)}")
        data = service.get_intraday(code)
        points = data["points"]
        if args.get("compact", True):
            points = [
                {"t": p["t"], "p": p["p"], "v": p["v"], "avg": p["avg"]}
                for p in points
                if p["p"] is not None
            ]
        return {
            "code": data["code"],
            "name": data["name"],
            "date": data["date"],
            "preClose": data["preClose"],
            "marketState": data.get("marketState"),
            "points": points,
        }

    raise ValueError(f"未知工具：{name}")


def handle(req):
    method = req.get("method")
    rid = req.get("id")

    if method == "initialize":
        return _ok(
            rid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "market-board", "version": "0.2.0"},
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _ok(rid, {})

    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            return _ok(rid, _text_payload(call_tool(name, args)))
        except Exception as exc:  # noqa: BLE001 工具异常要回给调用方而不是崩掉
            return _ok(
                rid,
                {"isError": True, "content": [{"type": "text", "text": f"工具执行失败：{exc}"}]},
            )

    if rid is None:
        return None
    return _err(rid, -32601, f"未实现的方法：{method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(
                json.dumps(_err(None, -32700, f"JSON 解析失败：{exc}"), ensure_ascii=False) + "\n"
            )
            sys.stdout.flush()
            continue

        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
