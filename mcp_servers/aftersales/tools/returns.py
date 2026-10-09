from datetime import date, datetime, time, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.common import OrderId, maybe_delay, not_found, rng


async def query_return_progress(order_id: OrderId) -> dict[str, Any]:
    """按订单号查询退货退款进度、退款金额和更新时间。"""
    await maybe_delay()
    missing = not_found(order_id)
    if missing is not None:
        return missing

    random = rng("returns", order_id)
    today = date.today()
    updated = datetime.combine(today - timedelta(days=random.randint(1, 3)), time(15, 30))
    return {
        "found": True,
        "order_id": order_id,
        "rma_no": f"RMA{updated:%Y%m%d}{random.randint(1, 999):03d}",
        "status_code": random.choice((
            "REQUESTED", "APPROVED", "RETURN_IN_TRANSIT", "RETURN_RECEIVED",
            "REFUND_PROCESSING", "REFUNDED", "REJECTED",
        )),
        "updated_at": updated.strftime("%Y-%m-%d %H:%M"),
        "refund_amount": random.randint(39, 1999),
        "internal_note": "仓库复检通过",
    }


def register(mcp: FastMCP) -> None:
    mcp.tool()(query_return_progress)
