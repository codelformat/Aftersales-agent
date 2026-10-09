from collections.abc import Callable
from typing import Any

CARRIERS = {"SF": "顺丰", "ZTO": "中通", "YTO": "圆通", "JD": "京东物流"}
LOGISTICS_STATUS = {"PICKED_UP": "已揽收", "IN_TRANSIT": "运输中", "OUT_FOR_DELIVERY": "派送中", "DELIVERED": "已签收"}
WARRANTY_STATUS = {"IN_WARRANTY": "在保", "EXPIRED": "已过保", "NO_WARRANTY": "不保修"}
RETURN_STATUS = {"REQUESTED": "已申请", "APPROVED": "已同意退货", "RETURN_IN_TRANSIT": "退货寄回中",
                 "RETURN_RECEIVED": "商家已收货", "REFUND_PROCESSING": "退款处理中", "REFUNDED": "已退款", "REJECTED": "已拒绝"}


def logistics(r):
    return {"order_id": r["order_id"], "carrier": CARRIERS.get(r["carrier_code"], r["carrier_code"]),
            "tracking_no": r["tracking_no"], "status": LOGISTICS_STATUS.get(r["status_code"], r["status_code"]),
            "eta": r["eta"], "traces": [{"time": t["time"], "location": t["city"], "desc": t["desc"]} for t in r["traces"]]}


def warranty(r):
    return {"order_id": r["order_id"], "items": [
        {"name": item["name"], "warranty": WARRANTY_STATUS.get(item["warranty_status"], item["warranty_status"]),
         "warranty_end": item["warranty_end"]} for item in r["items"]]}


def return_progress(r):
    return {"order_id": r["order_id"], "status": RETURN_STATUS.get(r["status_code"], r["status_code"]),
            "rma_no": r["rma_no"], "updated_at": r["updated_at"], "refund_amount": r["refund_amount"]}


MCP_FORMATTERS: dict[tuple[str, str], Callable[[Any], Any]] = {
    ("logistics", "query_logistics"): logistics,
    ("aftersales", "query_warranty"): warranty,
    ("aftersales", "query_return_progress"): return_progress,
}


def formatter_for(server: str, tool: str) -> Callable[[Any], Any] | None:
    return MCP_FORMATTERS.get((server, tool))
