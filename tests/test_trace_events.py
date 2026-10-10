from collections import Counter

import pytest
from langchain_core.runnables import RunnableLambda

from app.graph.nodes import knowledge as knowledge_nodes
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import text
from tests.protocol import validate_event
from tests.test_chat_api import parse_sse
from tests.test_resume_api import kb_multi, start_picker

pytestmark = pytest.mark.anyio


@pytest.fixture
def knowledge_scenario(use_script, use_intent, monkeypatch):
    use_intent("商品咨询")
    use_script(text("商家承担[1]"))

    async def retrieve(q, plan=None, top_n=None):
        item = EvidenceItem(7, "退换货 > 运费", "退货运费谁出", "商家承担", 0.9)
        return Retrieval(QueryPlan(standard_query=q), [item], [item])

    async def check(_):
        return {"parsed": SelfCheck(useful=True, reason="r"), "raw": None}

    monkeypatch.setattr(knowledge_nodes, "retrieve", retrieve)
    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))


async def knowledge_events(client, **fields):
    response = await client.post("/chat/stream", json={
        "user_id": "u1", "message": "退货运费谁出", **fields,
    })
    assert response.status_code == 200
    return parse_sse(response.text)


def node_events(events):
    traces = [data for name, data in events if name == "trace"]
    assert traces, "debug=True 应发出 trace"
    times = [data["t_ms"] for data in traces]
    assert times == sorted(times) and times[0] >= 0
    starts = Counter(data["node"] for data in traces if data["kind"] == "node_start")
    ends = Counter(data["node"] for data in traces if data["kind"] == "node_end")
    assert starts == ends
    for data in traces:
        if data["kind"] == "node_end":
            assert data["data"]["ms"] >= 0
    return traces


async def test_debug_off_stream_identical(client, db, knowledge_scenario):
    events = await knowledge_events(client)
    assert [name for name, _ in events] == [
        "session", "understood", "citations", *(["token"] * len("商家承担[1]")), "done",
    ]
    assert all(name != "trace" for name, _ in events)


async def test_debug_on_emits_node_events(client, db, knowledge_scenario):
    events = await knowledge_events(client, debug=True)
    traces = node_events(events)
    expected = {"start_turn", "resolve_reference", "classify_intent", "retrieve",
                "confidence_gate", "agent_model", "finalize"}
    for kind in ("node_start", "node_end"):
        assert {data["node"] for data in traces if data["kind"] == kind} == expected
    assert events[-1][0] == "done" and "message_id" in events[-1][1]


async def test_debug_events_match_schema(client, db, knowledge_scenario):
    events = await knowledge_events(client, debug=True)
    for name, data in events:
        validate_event(name, data)
    node_events(events)


async def test_interrupt_emits_node_end(client, db, use_script, use_resolver, use_intent):
    use_script()
    use_resolver({"resolved_input": "我要退货", "standard_query": "退货流程", "order_scoped": True})
    use_intent("退款退货")
    response = await client.post("/chat/stream", json={
        "user_id": "u1", "message": "我要退货", "debug": True,
    })
    assert response.status_code == 200
    events = parse_sse(response.text)
    traces = node_events(events)
    [end] = [data for data in traces if data["kind"] == "node_end" and data["node"] == "ensure_order"]
    assert end["data"]["interrupted"] is True
    assert events[-1] == ("done", {"finish_reason": "interrupted"})
    for name, data in events:
        validate_event(name, data)


async def test_resume_accepts_debug(client, db, use_script, use_resolver, use_intent, use_expander, monkeypatch):
    use_script(text("可以退[1]"))
    use_expander(["退货运费"])
    kb_multi(monkeypatch)
    sid, events = await start_picker(client, use_resolver, use_intent)
    response = await client.post("/chat/resume", json={
        "session_id": sid, "user_id": "u1", "order_id": events[2][1]["orders"][0]["order_id"], "debug": True,
    })
    assert response.status_code == 200
    resumed = parse_sse(response.text)
    assert "ensure_order" in {data["node"] for data in node_events(resumed)}
    assert resumed[-1][1]["finish_reason"] == "stop"
    for name, data in resumed:
        validate_event(name, data)


async def test_timed_preserves_interrupt(monkeypatch):
    from langgraph.errors import GraphInterrupt
    from app.graph import events
    from app.graph.builder import timed

    caught = []
    monkeypatch.setattr(events, "trace", lambda kind, data, node=None: caught.append((kind, data, node)))
    interrupt = GraphInterrupt(())

    async def node(state, runtime):
        raise interrupt

    with pytest.raises(GraphInterrupt) as raised:
        await timed("ensure_order", node)({}, None)
    assert raised.value is interrupt
    assert [kind for kind, _, _ in caught] == ["node_start", "node_end"]
    assert caught[-1][1]["interrupted"] is True


def test_trace_without_runtime_is_noop(monkeypatch):
    from app.graph import events

    def unexpected(*args):
        pytest.fail("没有 runtime 时不应发事件")

    monkeypatch.setattr(events, "emit", unexpected)
    events.trace("node_start", {}, node="outside_graph")


def test_schema_rejects_invalid_field_with_path():
    with pytest.raises(AssertionError, match="/data/text"):
        validate_event("token", {"text": 123})
