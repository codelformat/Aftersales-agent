import asyncio
from datetime import date

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import Ticket
from app.graph.control import (
    OfferHumanOptionsArgs, OfferRefundFormArgs, actions_from_args, offer_refund_form, refund_action,
)
from app.repositories import conversations
from app.tools import mock_data
from app.tools.registry import CH04_CHAT_TOOLS, build_default_registry, get_registry

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


def test_registry_lists_seven_tools_and_flags():
    reg = get_registry()
    assert sorted(reg.names()) == ["create_ticket", "offer_human_options", "offer_refund_form", "query_faq", "query_logistics", "query_order", "query_product"]
    assert reg.get("create_ticket").retryable is False
    assert reg.get("create_ticket").inject_conversation_id is True
    refund = reg.get("offer_refund_form")
    assert refund.retryable is False and refund.timeout == 5
    assert refund.inject_conversation_id is False
    faq = reg.get("query_faq")
    assert faq.retryable is False and faq.timeout == 20
    assert reg.get("query_order").retryable is True and reg.get("query_order").timeout == 5
    assert reg.get("nope") is None


def test_create_ticket_hides_conversation_id_from_model():
    schema = convert_to_openai_tool(get_registry().get("create_ticket").tool)
    assert set(schema["function"]["parameters"]["properties"]) == {"description", "ticket_type"}


@pytest.mark.anyio
async def test_order_id_pattern_rejected():
    with pytest.raises(ValidationError):
        await get_registry().get("query_order").tool.ainvoke({"order_id": "1001; DROP TABLE"})


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


@pytest.mark.anyio
@pytest.mark.parametrize("terms, questions, expected", [
    (["X3 Pro", "续航"], ["X3 Pro 续航多久"], True),
    (["X3 Pro", "续航"], ["X3 Pro 怎么用"], False),
    (["X3 Pro", "续航"], ["X3 Pro 怎么用", "续航多久"], False),
    (["邮费"], [], False),
    (None, [], True),
])
async def test_tool_selection_eval_requires_terms_in_one_question(monkeypatch, terms, questions, expected):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from evals import run_tool_selection_eval as ev

    calls = [
        {"id": str(i), "name": "query_faq", "args": {"question": question}}
        for i, question in enumerate(questions)
    ]

    class Model:
        def bind_tools(self, *args, **kwargs):
            return RunnableLambda(lambda _: AIMessage(content="", tool_calls=calls))

    monkeypatch.setattr(ev, "get_chat_model", lambda: Model())
    sample = {"text": "用户原话", "expected_tools": ["query_faq"], "faq_terms": terms}
    _, correct, line = await ev.evaluate_sample(1, sample, asyncio.Semaphore(1))
    assert correct is expected
    if questions or terms is not None:
        assert "query_faq question:" in line


def test_query_product_accepts_product_id_only():
    from app.tools.product import query_product

    schema = query_product.args_schema
    for ok in ("P001", "P12345678"):
        schema.model_validate({"product_id": ok})
    for bad in ("X3", "X3 Pro", "p001", "P01", "K1"):
        with pytest.raises(ValidationError):
            schema.model_validate({"product_id": bad})


def test_query_product_description_rejects_model_lookup():
    from app.tools.product import query_product

    assert "不能按型号查询" in query_product.description


@pytest.mark.anyio
async def test_query_product_model_name_is_invalid_arguments():
    import json

    from app.tools.executor import execute_tool_calls

    out = await execute_tool_calls(
        [{"id": "c1", "name": "query_product", "args": {"product_id": "X3"}}], conversation_id=1
    )
    assert out[0].ok is False
    assert json.loads(out[0].message.content)["error"] == "invalid_arguments"


def test_tools_for_model_filters_by_name_in_given_order():
    reg = get_registry()
    names = [t.name for t in reg.tools_for_model(("query_logistics", "offer_human_options"))]
    assert names == ["query_logistics", "offer_human_options"]
    assert "offer_human_options" in [t.name for t in reg.tools_for_model()]
    with pytest.raises(KeyError):
        reg.tools_for_model(("nope",))


def test_ch04_tool_set_is_unchanged():
    assert CH04_CHAT_TOOLS == ("query_order", "query_product", "query_logistics", "query_faq", "create_ticket")


@pytest.mark.parametrize("args", [
    {"options": []},
    {"options": ["handoff", "handoff"]},
    {"options": ["ticket"]},
    {"options": ["ticket"], "ticket_description": "坏了"},
    {"options": ["call"]},
])
def test_offer_args_rejects_invalid(args):
    with pytest.raises(ValidationError):
        OfferHumanOptionsArgs.model_validate(args)


def test_actions_from_args():
    assert actions_from_args({"options": ["handoff"]}) == [{"type": "handoff"}]
    assert actions_from_args({"options": ["ticket", "handoff"], "ticket_description": "耳机坏了",
                              "ticket_type": "售后"}) == [
        {"type": "ticket", "description": "耳机坏了", "ticket_type": "售后"}, {"type": "handoff"}]


@pytest.mark.anyio
async def test_offer_tool_has_no_side_effect():
    spec = get_registry().get("offer_human_options")
    assert spec.retryable is False and spec.inject_conversation_id is False
    assert await spec.tool.ainvoke({"options": ["handoff"]}) == {"shown": ["handoff"]}


def test_user_orders_deterministic_and_consistent():
    today = date(2026, 10, 6)
    cards = mock_data.user_orders("u1", today)
    assert len(cards) == 3 and cards == mock_data.user_orders("u1", today)
    assert len({c["order_id"] for c in cards}) == 3
    assert mock_data.user_orders("u2", today) != cards
    for card in cards:
        assert set(card) == {"order_id", "title", "total", "created_at", "status"}
        order = mock_data.order(card["order_id"], today)
        assert card["total"] == order["total"] and card["status"] == order["status"]
        assert card["status"] not in ("待付款", "已取消")
        assert card["title"].startswith(order["items"][0]["name"])


def test_order_card_title_counts_items():
    today = date(2026, 10, 6)
    for oid in ("1001", "1002", "1003", "1004", "1005"):
        order = mock_data.order(oid, today)
        title = mock_data.order_card(oid, today)["title"]
        if len(order["items"]) > 1:
            assert title == f"{order['items'][0]['name']} 等 {len(order['items'])} 件"
        else:
            assert title == order["items"][0]["name"]


@pytest.mark.anyio
async def test_offer_refund_form_only_shows_button():
    assert await offer_refund_form.ainvoke({"order_id": "1001"}) == {"shown": "refund"}
    assert refund_action("1001") == {"type": "refund", "order_id": "1001"}
    assert "offer_refund_form" in build_default_registry().names()
    spec = build_default_registry().get("offer_refund_form")
    assert spec.retryable is False


def test_offer_refund_form_rejects_bad_order_id():
    with pytest.raises(ValidationError):
        OfferRefundFormArgs(order_id="10 01")
