"""演示用：查询维修进度。复制到 mcp_servers/aftersales/tools/ 后重启 aftersales Server 即可使用。"""

from datetime import datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.common import OrderId, maybe_delay, not_found, rng


async def query_repair_progress(order_id: OrderId) -> dict[str, Any]:
    """按订单号查询商品的维修进度。"""
    await maybe_delay()
    missing = not_found(order_id)
    if missing is not None:
        return missing

    random = rng("repair", order_id)
    updated = datetime.now() - timedelta(hours=random.randint(1, 72))
    return {
        "found": True,
        "order_id": order_id,
        "repair_no": f"R{random.randint(10000000, 99999999)}",
        "status_code": random.choice(("RECEIVED", "DIAGNOSING", "REPAIRING", "SHIPPED_BACK")),
        "updated_at": updated.strftime("%Y-%m-%d %H:%M:%S"),
    }


def register(mcp: FastMCP) -> None:
    mcp.tool()(query_repair_progress)
