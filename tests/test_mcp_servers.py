import json
import subprocess
import sys
import time
from datetime import date, datetime

import pytest

from mcp_servers.aftersales import server as aftersales
from mcp_servers.aftersales import tools as aftersales_tools
from mcp_servers.aftersales.tools.returns import query_return_progress
from mcp_servers.aftersales.tools.warranty import query_warranty
from mcp_servers.common import PRODUCTS, delay, not_found
from mcp_servers.logistics import server as logistics

pytestmark = pytest.mark.anyio

QUERIES = [logistics.query_logistics, query_warranty, query_return_progress]


async def tool_names(srv):
    return sorted(t.name for t in await srv.list_tools())


async def test_tool_lists():
    assert await tool_names(logistics.build_server(port=0)) == ["query_logistics"]
    assert await tool_names(aftersales.build_server(port=0)) == [
        "query_return_progress", "query_warranty",
    ]


async def test_input_schema_has_pattern():
    for srv in (logistics.build_server(port=0), aftersales.build_server(port=0)):
        for tool in await srv.list_tools():
            schema = tool.inputSchema
            assert schema["properties"]["order_id"]["pattern"] == r"^[A-Za-z0-9-]{1,32}$"
            assert schema["properties"]["order_id"]["description"] == "订单号"
            assert schema["required"] == ["order_id"]


@pytest.mark.parametrize("build", [logistics.build_server, aftersales.build_server])
async def test_registered_tools_return_structured_content(build):
    server = build(port=0)
    for tool in await server.list_tools():
        assert tool.outputSchema is not None
        for order_id in ("1001", "9001"):
            result = await server.call_tool(tool.name, {"order_id": order_id})
            assert isinstance(result, tuple)
            content, structured = result
            assert structured["found"] is (order_id == "1001")
            assert structured["order_id"] == order_id
            assert json.loads(content[0].text) == structured


async def test_logistics_deterministic_and_1001_in_transit():
    data = await logistics.query_logistics("1001")
    assert data == await logistics.query_logistics("1001")
    assert data["found"] is True and data["order_id"] == "1001"
    assert data["status_code"] == "IN_TRANSIT"
    assert {"warehouse_id", "route_id"} <= set(data)
    assert data["carrier_code"] in {"SF", "ZTO", "YTO", "JD"}
    assert data["tracking_no"].startswith(data["carrier_code"])
    assert date.fromisoformat(data["eta"]) > date.today()
    assert data["traces"]
    times = [datetime.strptime(t["time"], "%Y-%m-%d %H:%M") for t in data["traces"]]
    assert all(a < b for a, b in zip(times, times[1:]))
    assert all({"node_code", "city", "desc"} <= set(t) for t in data["traces"])


async def test_delivered_logistics_ends_with_signature():
    for order_id in map(str, range(1002, 1102)):
        data = await logistics.query_logistics(order_id)
        if data["status_code"] == "DELIVERED":
            assert data["traces"][-1]["desc"] == "快件已签收"
            times = [t["time"] for t in data["traces"]]
            assert times == sorted(set(times))
            return
    pytest.fail("样例订单中没有签收状态")


@pytest.mark.parametrize("query", QUERIES)
async def test_not_found_prefix_9(query):
    assert await query("9001") == {"found": False, "order_id": "9001", "reason": "订单不存在"}
    assert not_found("1001") is None


@pytest.mark.parametrize("query", QUERIES)
async def test_delay(monkeypatch, query):
    monkeypatch.setenv("MOCK_DELAY_SECONDS", "0.2")
    t0 = time.monotonic()
    await query("1001")
    assert time.monotonic() - t0 >= 0.2


def test_delay_default(monkeypatch):
    monkeypatch.delenv("MOCK_DELAY_SECONDS", raising=False)
    assert delay() == 0


async def test_warranty_contract_and_determinism():
    data = await query_warranty("1001")
    assert data == await query_warranty("1001")
    assert data["found"] is True and data["order_id"] == "1001"
    assert data["policy_code"] == "STD_365"
    assert data["items"]
    for item in data["items"]:
        assert item["sku_id"].startswith("P")
        assert item["name"] in PRODUCTS
        assert item["warranty_status"] in {"IN_WARRANTY", "EXPIRED", "NO_WARRANTY"}
        end = date.fromisoformat(item["warranty_end"])
        if item["warranty_status"] == "IN_WARRANTY":
            assert end >= date.today()
        elif item["warranty_status"] == "EXPIRED":
            assert end < date.today()


async def test_return_contract_and_determinism():
    data = await query_return_progress("1001")
    assert data == await query_return_progress("1001")
    assert data["found"] is True and data["order_id"] == "1001"
    assert data["rma_no"].startswith("RMA")
    assert data["status_code"] in {
        "REQUESTED", "APPROVED", "RETURN_IN_TRANSIT", "RETURN_RECEIVED",
        "REFUND_PROCESSING", "REFUNDED", "REJECTED",
    }
    assert datetime.strptime(data["updated_at"], "%Y-%m-%d %H:%M").date() <= date.today()
    assert isinstance(data["refund_amount"], (int, float)) and data["refund_amount"] >= 0
    assert isinstance(data["internal_note"], str)


def test_products_match_catalog_names():
    assert tuple(PRODUCTS) == (
        "蓝牙耳机", "羊毛衫", "扫地机器人", "电动牙刷", "台灯", "保温杯", "运动鞋", "手机壳",
    )


async def test_aftersales_scans_new_tool_module(tmp_path, monkeypatch):
    (tmp_path / "extra.py").write_text(
        'async def query_extra() -> dict:\n'
        '    """查询演示信息。"""\n'
        '    return {"found": True}\n\n'
        'def register(mcp):\n'
        '    mcp.tool()(query_extra)\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(aftersales_tools, "__path__", [str(tmp_path)])
    try:
        assert await tool_names(aftersales.build_server(port=0)) == ["query_extra"]
    finally:
        sys.modules.pop("mcp_servers.aftersales.tools.extra", None)


@pytest.mark.parametrize("build, name", [(logistics.build_server, "logistics"), (aftersales.build_server, "aftersales")])
def test_server_transport_settings(build, name):
    srv = build(host="127.0.0.2", port=8123)
    assert srv.name == name
    assert srv.settings.host == "127.0.0.2" and srv.settings.port == 8123
    assert srv.settings.stateless_http is True and srv.settings.json_response is True
    assert srv.settings.streamable_http_path == "/mcp"


@pytest.mark.parametrize("name", ["logistics", "aftersales"])
def test_module_cli_accepts_port(name):
    out = subprocess.run(
        [sys.executable, "-m", f"mcp_servers.{name}", "--help"],
        capture_output=True, text=True, check=True,
    )
    assert "--port" in out.stdout


def test_servers_do_not_import_app():
    code = (
        "import sys, mcp_servers.logistics.server, mcp_servers.aftersales.server; "
        "mcp_servers.logistics.server.build_server(port=0); "
        "mcp_servers.aftersales.server.build_server(port=0); "
        "print(any(m == 'app' or m.startswith('app.') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
