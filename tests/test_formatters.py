import pytest

from mcp_servers.logistics.server import query_logistics
from mcp_servers.aftersales.tools.warranty import query_warranty
from mcp_servers.aftersales.tools.returns import query_return_progress

pytestmark = pytest.mark.anyio


async def test_logistics_translates_codes_and_removes_internal_fields():
    from app.tools.formatters import formatter_for

    raw = await query_logistics("1001")
    result = formatter_for("logistics", "query_logistics")(raw)
    assert result["status"] == "运输中"
    assert result["carrier"] in {"顺丰", "中通", "圆通", "京东物流"}
    assert set(result) == {"order_id", "carrier", "tracking_no", "status", "eta", "traces"}
    assert result["traces"] == [{"time": t["time"], "location": t["city"], "desc": t["desc"]}
                                for t in raw["traces"]]


async def test_warranty_translates_codes_and_removes_internal_fields():
    from app.tools.formatters import formatter_for

    result = formatter_for("aftersales", "query_warranty")(await query_warranty("1001"))
    assert set(result) == {"order_id", "items"}
    for item in result["items"]:
        assert set(item) == {"name", "warranty", "warranty_end"}
        assert item["warranty"] in {"在保", "已过保", "不保修"}


async def test_return_progress_translates_codes_and_removes_internal_fields():
    from app.tools.formatters import formatter_for

    raw = await query_return_progress("1001")
    result = formatter_for("aftersales", "query_return_progress")(raw)
    assert set(result) == {"order_id", "status", "rma_no", "updated_at", "refund_amount"}
    assert result["status"] in {"已申请", "已同意退货", "退货寄回中", "商家已收货", "退款处理中", "已退款", "已拒绝"}
    assert result["rma_no"] == raw["rma_no"]
    assert result["updated_at"] == raw["updated_at"]
    assert result["refund_amount"] == raw["refund_amount"]
    assert formatter_for("aftersales", "unknown") is None
