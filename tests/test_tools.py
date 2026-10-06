from datetime import date

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import Ticket
from app.repositories import conversations
from app.tools import mock_data
from app.tools.registry import get_registry

TODAY = date(2026, 10, 6)


def test_mock_is_deterministic():
    assert mock_data.order("2002", TODAY) == mock_data.order("2002", TODAY)
    assert mock_data.product("P009") == mock_data.product("P009")
    assert mock_data.logistics("2002", TODAY) == mock_data.logistics("2002", TODAY)


def test_order_1001_is_shipped_with_traces():
    assert mock_data.order("1001", TODAY)["status"] == "已发货"
    lg = mock_data.logistics("1001", TODAY)
    assert lg["status"] == "运输中"
    assert 2 <= len(lg["traces"]) <= 4
    assert "已签收" not in lg["traces"][-1]["description"]


def test_order_and_logistics_are_consistent():
    expected = {"待付款": "未发货", "待发货": "未发货", "已取消": "未发货", "已发货": "运输中", "已签收": "已签收"}
    seen = set()
    for i in range(2000, 2200):
        oid = str(i)
        status = mock_data.order(oid, TODAY)["status"]
        lg = mock_data.logistics(oid, TODAY)
        seen.add(status)
        assert lg["status"] == expected[status]
        if lg["status"] == "未发货":
            assert lg["traces"] == [] and lg["carrier"] is None
        if lg["status"] == "已签收":
            assert "已签收" in lg["traces"][-1]["description"]
    assert seen == set(expected)


def test_catalog_product():
    p = mock_data.product("P001")
    assert (p["name"], p["price"]) == ("蓝牙耳机", 299)


def test_registry_lists_five_tools_and_flags():
    reg = get_registry()
    assert sorted(reg.names()) == ["create_ticket", "query_faq", "query_logistics", "query_order", "query_product"]
    assert reg.get("create_ticket").retryable is False
    assert reg.get("create_ticket").inject_conversation_id is True
    assert reg.get("query_faq").retryable is True
    assert reg.get("nope") is None


def test_create_ticket_hides_conversation_id_from_model():
    schema = convert_to_openai_tool(get_registry().get("create_ticket").tool)
    assert set(schema["function"]["parameters"]["properties"]) == {"description", "ticket_type"}


@pytest.mark.anyio
async def test_order_id_pattern_rejected():
    with pytest.raises(ValidationError):
        await get_registry().get("query_order").tool.ainvoke({"order_id": "1001; DROP TABLE"})


@pytest.mark.anyio
async def test_query_faq_tool(db):
    tool = get_registry().get("query_faq").tool
    hit = await tool.ainvoke({"keyword": "退货政策"})
    miss = await tool.ainvoke({"keyword": "邮费"})
    assert hit["results"][0]["question"] == "退货政策是什么？"
    assert miss == {"results": []}


@pytest.mark.anyio
async def test_create_ticket_tool_writes_row(db):
    async with db() as s:
        conv = await conversations.create(s, "u1")
        await s.commit()
    tool = get_registry().get("create_ticket").tool
    out = await tool.ainvoke({"description": "耳机坏了要人工", "ticket_type": "售后", "conversation_id": conv.id})
    assert out["ticket_no"].startswith("T") and out["status"] == "待处理"
    async with db() as s:
        row = (await s.execute(select(Ticket))).scalar_one()
    assert row.conversation_id == conv.id


def test_logistics_traces_have_location_and_valid_timeline():
    from datetime import datetime

    for i in list(range(2000, 2200)) + [1001]:
        oid = str(i)
        created = datetime.strptime(mock_data.order(oid, TODAY)["created_at"], "%Y-%m-%d %H:%M")
        traces = mock_data.logistics(oid, TODAY)["traces"]
        times = [datetime.strptime(t["time"], "%Y-%m-%d %H:%M") for t in traces]
        for t in traces:
            assert isinstance(t["location"], str) and t["location"]
        if times:
            assert times[0] > created
            assert all(a < b for a, b in zip(times, times[1:]))
            assert times[-1] <= datetime(TODAY.year, TODAY.month, TODAY.day, 23, 59)
