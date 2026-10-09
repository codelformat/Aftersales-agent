from datetime import date, datetime, time, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.common import OrderId, maybe_delay, not_found, rng


async def query_logistics(order_id: OrderId) -> dict[str, Any]:
    """按订单号查询物流状态、预计送达日期和轨迹。"""
    await maybe_delay()
    missing = not_found(order_id)
    if missing is not None:
        return missing

    random = rng("logistics", order_id)
    today = date.today()
    status = random.choice(("PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED"))
    if order_id == "1001":
        status = "IN_TRANSIT"
    carrier = random.choice(("SF", "ZTO", "YTO", "JD"))
    origin, destination = random.sample(("上海", "杭州", "广州", "深圳", "北京", "成都", "武汉", "南京"), 2)
    stages = [
        (origin, "快件已揽收"),
        (origin, "快件已到达分拨中心"),
        (destination, "快件运输中"),
        (destination, "快件正在派送"),
        (destination, "快件已签收"),
    ]
    count = {"PICKED_UP": 1, "IN_TRANSIT": 3, "OUT_FOR_DELIVERY": 4, "DELIVERED": 5}[status]
    start = datetime.combine(today - timedelta(days=1), time(9, 12))
    traces = [
        {
            "time": (start + timedelta(hours=i * 3)).strftime("%Y-%m-%d %H:%M"),
            "node_code": f"N{i + 1:02d}",
            "city": city,
            "desc": desc,
        }
        for i, (city, desc) in enumerate(stages[:count])
    ]
    return {
        "found": True,
        "order_id": order_id,
        "carrier_code": carrier,
        "tracking_no": f"{carrier}{random.randint(1000000000, 9999999999)}",
        "status_code": status,
        "eta": (today + timedelta(days=0 if status == "DELIVERED" else random.randint(1, 3))).isoformat(),
        "warehouse_id": f"WH-{random.randint(1, 20):02d}",
        "route_id": f"R-{random.randint(1000, 9999)}",
        "traces": traces,
    }


def build_server(host: str = "127.0.0.1", port: int = 8101) -> FastMCP:
    mcp = FastMCP("logistics", host=host, port=port, stateless_http=True, json_response=True)
    mcp.tool()(query_logistics)
    return mcp
