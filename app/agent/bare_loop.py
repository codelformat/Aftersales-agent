"""热身：不用框架的 Agent 循环。执行工具后回传结果，无工具调用时返回答案。"""

import json
from dataclasses import dataclass, field
from datetime import date

from app.tools import mock_data

SYSTEM = "你是售后客服助手。订单和物流问题调用工具查询，只根据工具结果回答，不编造。"

TOOLS = [
    {"type": "function", "function": {
        "name": "query_order",
        "description": "按订单号查询订单状态、商品和金额。",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "订单号"}},
            "required": ["order_id"],
        },
    }},
    {"type": "function", "function": {
        "name": "query_logistics",
        "description": "按订单号查询承运商、物流状态和轨迹。",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "订单号"}},
            "required": ["order_id"],
        },
    }},
]


@dataclass
class BareResult:
    answer: str
    steps: int
    calls: list[list[str]] = field(default_factory=list)


def _run_tool(name: str, arguments: str, today: date) -> dict:
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        return {"error": "invalid_arguments"}
    if name == "query_order":
        return mock_data.order(str(args.get("order_id", "")), today)
    if name == "query_logistics":
        return mock_data.logistics(str(args.get("order_id", "")), today)
    return {"error": "unknown_tool"}


async def run_bare_agent(
    client, model: str, question: str, *, today: date,
    max_steps: int = 4, extra_body: dict | None = None,
) -> BareResult:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    extra = {"extra_body": extra_body} if extra_body else {}
    steps, calls = 0, []
    while True:
        if steps >= max_steps:
            # 达到步数上限后，不提供工具，让模型用文字收尾。
            resp = await client.chat.completions.create(model=model, messages=messages, **extra)
            return BareResult(resp.choices[0].message.content or "", steps, calls)
        resp = await client.chat.completions.create(model=model, messages=messages, tools=TOOLS, **extra)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return BareResult(msg.content or "", steps, calls)
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": c.id, "type": "function", "function": {
                "name": c.function.name, "arguments": c.function.arguments,
            }}
            for c in msg.tool_calls
        ]})
        for c in msg.tool_calls:
            result = _run_tool(c.function.name, c.function.arguments, today)
            messages.append({
                "role": "tool", "tool_call_id": c.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        steps += 1
        calls.append([c.function.name for c in msg.tool_calls])
