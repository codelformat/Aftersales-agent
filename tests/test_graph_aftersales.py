from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph.nodes import aftersales
from app.graph.state import ChatState, GraphContext
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.schemas import QueryPlan
from app.tools import mock_data
from app.tools.executor import execute_tool_calls
from tests.fakes import rt

pytestmark = pytest.mark.anyio
TODAY = date(2026, 10, 6)


async def test_ensure_order_with_order_id_does_not_interrupt():
    out = await aftersales.ensure_order({"order_id": "1001", "trace": []}, rt())
    assert out == {"trace": ["ensure_order"]}


def _one_node_graph():
    g = StateGraph(ChatState, context_schema=GraphContext)
    g.add_node("ensure_order", aftersales.ensure_order)
    g.add_edge(START, "ensure_order")
    g.add_edge("ensure_order", END)
    return g.compile(checkpointer=InMemorySaver())


async def test_ensure_order_interrupts_and_resumes():
    graph = _one_node_graph()
    cfg = {"configurable": {"thread_id": "t1"}}
    ctx = GraphContext(conversation_id=1, today=TODAY, model=None, execute=execute_tool_calls, user_id="u1")
    chunks = [c async for c in graph.astream({"order_id": None, "trace": []}, cfg, context=ctx, stream_mode="updates")]
    value = chunks[-1]["__interrupt__"][0].value
    assert value == {"type": "order_picker", "orders": mock_data.user_orders("u1", TODAY)}
    chosen = value["orders"][1]["order_id"]
    await graph.ainvoke(Command(resume=chosen), cfg, context=ctx)
    assert (await graph.aget_state(cfg)).values["order_id"] == chosen


async def test_ensure_order_rejects_unknown_choice():
    graph = _one_node_graph()
    cfg = {"configurable": {"thread_id": "t2"}}
    ctx = GraphContext(conversation_id=1, today=TODAY, model=None, execute=execute_tool_calls, user_id="u1")
    await graph.ainvoke({"order_id": None, "trace": []}, cfg, context=ctx)
    with pytest.raises(ValueError):
        await graph.ainvoke(Command(resume="999"), cfg, context=ctx)


async def test_fetch_order_success_and_failure(caplog):
    out = await aftersales.fetch_order({"order_id": "1001", "trace": []}, rt())
    assert out["order"] == mock_data.order("1001", date.today()) and out["trace"] == ["fetch_order"]

    async def failing(calls, conversation_id):
        from app.tools.executor import failure_outcome
        return [failure_outcome(calls[0]["id"], "query_order", "timeout")]

    out = await aftersales.fetch_order({"order_id": "1001", "trace": []}, rt(execute=failing))
    assert out["order"] is None and "订单查询失败" in caplog.text


async def test_expand_query_passes_product_names(use_expander):
    calls = use_expander(["退货运费"])
    order = {"items": [{"name": "蓝牙耳机"}, {"name": "台灯"}]}
    out = await aftersales.expand_query({"standard_query": "退货条件", "order": order, "trace": []}, rt())
    assert out["queries"] == ["退货条件", "退货运费"] and calls[0]["products"] == "蓝牙耳机、台灯"
    out = await aftersales.expand_query({"standard_query": "退货条件", "order": None, "trace": []}, rt())
    assert out["trace"] == ["expand_query"]


async def test_retrieve_multi_evidence(monkeypatch):
    seen = {}

    async def fake(queries, plan, top_n=None):
        seen["queries"], seen["plan"] = queries, plan
        item = EvidenceItem(7, "退货政策 > 条件", "拆封能退吗", "不影响二次销售可退", 0.8)
        return Retrieval(plan, [item], [item])

    monkeypatch.setattr(aftersales, "retrieve_multi", fake)
    state = {"resolved_input": "蓝牙耳机能退吗", "standard_query": "退货条件", "product_category": None,
             "queries": ["退货条件", "退货运费"], "trace": []}
    out = await aftersales.retrieve_multi_evidence(state, rt())
    assert seen["queries"] == ["退货条件", "退货运费"]
    assert seen["plan"] == QueryPlan(standard_query="退货条件", product_category=None)
    assert out["evidence"][0]["n"] == 1 and out["gate"]["top_score"] == 0.8 and out["trace"] == ["retrieve_multi"]
    assert [r["score"] for r in out["retrieval"]] == [0.8]
