from collections import Counter
import asyncio

import pytest
from langchain_core.runnables import RunnableLambda

from app.graph.nodes import knowledge as knowledge_nodes
from app.config import GATE_CONF_THRESHOLD, GATE_WEIGHTS
from app.context import count_tokens
from app.context.budget import get_budget
from app.context.layers import render_layer2, split_layers
from app.context.summarizer import get_runner
from app.graph.builder import thread_config
from app.repositories import conversations
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import text, tools
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


def domain_events(events, debug, expected):
    for name, data in events:
        validate_event(name, data)
    assert events[-1][0] == "done"
    if not debug:
        assert all(name != "trace" for name, _ in events)
        return {}
    traces = node_events(events)
    grouped = {}
    for event in traces:
        if event["kind"] not in {"node_start", "node_end"}:
            grouped.setdefault(event["kind"], []).append(event)
    assert set(grouped) == expected
    return grouped


async def checkpoint(memory_graph, events):
    sid = int(events[0][1]["session_id"])
    return (await memory_graph.aget_state(thread_config(sid))).values


def assert_resolution_and_intent(grouped, state, escalated=True):
    [resolved] = grouped["resolve"]
    assert resolved["node"] == "resolve_reference"
    assert resolved["data"] == {
        "original": state["user_input"],
        **{key: state[key] for key in (
            "resolved_input", "standard_query", "order_id", "order_scoped",
            "ticket_request", "status_query", "history_recall",
        )},
    }
    [intent] = grouped["intent"]
    assert intent["node"] == "classify_intent"
    assert intent["data"] == {"intent": state["intent"], "confidence": state["intent_confidence"],
                              "route": state["route"], "escalated": escalated}


def assert_retrieval_and_gate(grouped, state, node, self_check):
    [retrieval] = grouped["retrieval"]
    assert retrieval["node"] == node
    queries = state["queries"] if node == "retrieve_multi" else [state["standard_query"]]
    assert retrieval["data"] == {"queries": queries, "top": state["retrieval"],
                                 "kept": len(state["evidence"])}
    [gate] = grouped["gate"]
    assert gate["node"] == "confidence_gate"
    assert gate["data"] == {
        **{key: state["gate"][key] for key in (
            "passed", "confidence", "signals", "source", "reason",
        )},
        "weights": list(GATE_WEIGHTS), "threshold": GATE_CONF_THRESHOLD, "self_check": self_check,
    }


def assert_tools(grouped, audit_log, node):
    assert len(grouped["tool"]) == len(audit_log) > 0
    records = {rec.tool_call_id: rec for rec in audit_log}
    for event in grouped["tool"]:
        assert event["node"] == node
        rec = records[event["data"]["call_id"]]
        assert event["data"] == {
            "call_id": rec.tool_call_id, "name": rec.tool_name, "source": rec.tool_source,
            "mcp_server": rec.mcp_server, "status": rec.status, "retry_count": rec.retry_count,
            "duration_ms": rec.duration_ms, "error_message": rec.error_message,
        }


async def assert_final_context(grouped, state, db, events, triggered=False):
    event = grouped["context"][-1]
    assert event["node"] == "finalize"
    async with db() as session:
        anchors = await conversations.get_context(session, int(events[0][1]["session_id"]))
    layers = split_layers(state["messages"], anchors.summary_upto, anchors.layer1_from)
    budget = get_budget()
    assert event["data"] == {
        "layer1_tokens": count_tokens(layers.layer1) if layers.layer1 else 0,
        "layer1_budget": budget.layer1,
        "layer2_tokens": count_tokens(render_layer2(layers.layer2)) if layers.layer2 else 0,
        "layer2_budget": budget.layer2, "summary_triggered": triggered,
    }


@pytest.mark.parametrize("debug", [True, False])
@pytest.mark.parametrize("outcome", ["passed", "low_score", "self_check"])
async def test_knowledge_domain_trace(
    client, db, memory_graph, knowledge_scenario, monkeypatch, debug, outcome,
):
    if outcome == "low_score":
        async def low(q, plan=None, top_n=None):
            item = EvidenceItem(9, "运费", "谁出运费", "商家承担", 0.1)
            return Retrieval(plan, [], [item])
        monkeypatch.setattr(knowledge_nodes, "retrieve", low)
    elif outcome == "self_check":
        async def refused(_):
            return {"parsed": SelfCheck(useful=False, reason="没有完整覆盖"), "raw": None}
        monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(refused))
    events = await knowledge_events(client, debug=debug)
    grouped = domain_events(events, debug, {"resolve", "intent", "retrieval", "gate", "context"})
    state = await checkpoint(memory_graph, events)
    assert state["gate"]["passed"] is (outcome == "passed")
    if debug:
        assert_resolution_and_intent(grouped, state)
        assert_retrieval_and_gate(grouped, state, "retrieve", outcome != "low_score")
        assert [e["node"] for e in grouped["context"]] == (
            ["agent_model", "finalize"] if outcome == "passed" else ["finalize"]
        )
        await assert_final_context(grouped, state, db, events)


@pytest.mark.parametrize("debug", [True, False])
async def test_aftersales_domain_trace_interrupt_resume(
    client, db, memory_graph, use_script, use_resolver, use_intent, use_expander,
    monkeypatch, audit_log, debug,
):
    use_resolver({"standard_query": "退货流程", "order_scoped": True})
    use_intent("退款退货")
    use_script(text("可以退[1]"))
    use_expander(["退货运费"])
    kb_multi(monkeypatch)
    response = await client.post("/chat/stream", json={
        "user_id": "u1", "message": "我要退货", "debug": debug,
    })
    initial = parse_sse(response.text)
    grouped = domain_events(initial, debug, {"resolve", "intent"})
    if debug:
        assert_resolution_and_intent(grouped, await checkpoint(memory_graph, initial))
    assert initial[-1][1]["finish_reason"] == "interrupted"
    [picker] = [data for name, data in initial if name == "order_picker"]
    response = await client.post("/chat/resume", json={
        "session_id": initial[0][1]["session_id"], "user_id": "u1",
        "order_id": picker["orders"][0]["order_id"], "debug": debug,
    })
    resumed = parse_sse(response.text)
    grouped = domain_events(resumed, debug, {"retrieval", "gate", "tool", "context"})
    state = await checkpoint(memory_graph, resumed)
    assert state["queries"] == ["退货流程", "退货运费"]
    assert state["gate"]["passed"] is True
    if debug:
        assert_retrieval_and_gate(grouped, state, "retrieve_multi", False)
        assert_tools(grouped, audit_log, "fetch_order")
        await assert_final_context(grouped, state, db, resumed)


@pytest.mark.parametrize("debug", [True, False])
@pytest.mark.parametrize("args, status", [
    ({"order_id": "1001"}, "成功"), ({}, "校验拦下"),
])
async def test_business_tool_domain_trace(
    client, db, memory_graph, use_script, use_intent, audit_log, debug, args, status,
):
    use_intent("物流")
    use_script(tools(("trace-tool-1", "query_order", args)), text("查询结束"))
    response = await client.post("/chat/stream", json={
        "user_id": "u1", "message": "订单 1001 的物流到哪了", "debug": debug,
    })
    events = parse_sse(response.text)
    grouped = domain_events(events, debug, {"resolve", "intent", "tool", "context"})
    state = await checkpoint(memory_graph, events)
    assert audit_log[0].status == status
    if debug:
        assert_resolution_and_intent(grouped, state)
        assert_tools(grouped, audit_log, "agent_tools")
        assert [e["node"] for e in grouped["context"]] == ["agent_model", "agent_model", "finalize"]
        assert all(e["data"]["summary_triggered"] is False for e in grouped["context"][:-1])
        await assert_final_context(grouped, state, db, events)


@pytest.mark.parametrize("debug", [True, False])
@pytest.mark.parametrize("model", ["large", "small", "escalated", "failed"])
async def test_chitchat_intent_domain_trace(
    client, db, memory_graph, use_script, use_intent, use_small_intent, monkeypatch, debug, model,
):
    from app import config

    use_script(text("请说明需求")) if model == "failed" else use_script()
    if model in {"small", "escalated"}:
        monkeypatch.setattr(config, "INTENT_SMALL_MODEL", "test-small")
        use_small_intent(("闲聊", 0.9 if model == "small" else 0.2))
    use_intent(None if model == "failed" else "闲聊")
    response = await client.post("/chat/stream", json={
        "user_id": "u1", "message": "你好", "debug": debug,
    })
    events = parse_sse(response.text)
    grouped = domain_events(events, debug, {"resolve", "intent", "context"})
    if debug:
        state = await checkpoint(memory_graph, events)
        assert_resolution_and_intent(grouped, state, model in {"large", "escalated"})
        assert [e["node"] for e in grouped["context"]] == (
            ["agent_model", "finalize"] if model == "failed" else ["finalize"]
        )
        await assert_final_context(grouped, state, db, events)


async def test_context_trace_counts_tokens_and_reports_new_summary(
    client, db, memory_graph, knowledge_scenario, use_intent, use_script, use_budget, use_summarizer,
):
    first = await knowledge_events(client, debug=True)
    old = await checkpoint(memory_graph, first)
    use_intent("商品咨询")
    use_script(text("商家承担[1]"))
    use_budget(layer1=1, layer2=1)
    use_summarizer(asyncio.Event(), "梗概")
    try:
        events = await knowledge_events(client, debug=True, session_id=first[0][1]["session_id"])
        grouped = domain_events(events, True, {"resolve", "intent", "retrieval", "gate", "context"})
        [agent, final] = grouped["context"]
        assert agent["data"] == {
            "layer1_tokens": count_tokens(old["messages"]), "layer1_budget": 1,
            "layer2_tokens": 0, "layer2_budget": 1, "summary_triggered": False,
        }
        assert agent["data"]["layer1_tokens"] > len(old["messages"])
        state = await checkpoint(memory_graph, events)
        await assert_final_context(grouped, state, db, events, triggered=True)
        assert get_runner().running(int(events[0][1]["session_id"]))
        # 上一轮摘要仍在运行，本轮不能把跳过误报为新触发。
        use_intent("闲聊")
        use_script()
        again = await knowledge_events(client, debug=True, session_id=first[0][1]["session_id"])
        grouped = domain_events(again, True, {"resolve", "intent", "context"})
        await assert_final_context(grouped, await checkpoint(memory_graph, again), db, again, triggered=False)
    finally:
        await get_runner().cancel_all()
