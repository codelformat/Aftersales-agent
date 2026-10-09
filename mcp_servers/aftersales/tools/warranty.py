from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.common import PRODUCTS, OrderId, maybe_delay, not_found, rng


async def query_warranty(order_id: OrderId) -> dict[str, Any]:
    """按订单号查询商品保修状态和保修截止日期。"""
    await maybe_delay()
    missing = not_found(order_id)
    if missing is not None:
        return missing

    random = rng("warranty", order_id)
    today = date.today()
    items = []
    for index in random.sample(range(len(PRODUCTS)), random.randint(1, 2)):
        status = random.choice(("IN_WARRANTY", "EXPIRED", "NO_WARRANTY"))
        days = random.randint(1, 365)
        end = today + timedelta(days=days if status == "IN_WARRANTY" else -days)
        items.append({
            "sku_id": f"P{index + 1:03d}",
            "name": PRODUCTS[index],
            "warranty_status": status,
            "warranty_end": end.isoformat(),
        })
    return {"found": True, "order_id": order_id, "policy_code": "STD_365", "items": items}


def register(mcp: FastMCP) -> None:
    mcp.tool()(query_warranty)
