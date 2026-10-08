# tests/test_graph.py
from datetime import date

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.types import Command
from sqlalchemy import select

from app.db.models import LowConfidenceQuestion, Message
from app.graph.builder import get_graph, set_graph, thread_config
from app.graph.nodes import aftersales as aftersales_nodes
from app.graph.nodes import agent as agent_mod
from app.graph.nodes import knowledge as knowledge_nodes
from app.graph.state import GraphContext
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, GATE_FALLBACK_REPLY
from app.repositories import conversations
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import Recorder, ScriptedChatModel, text, tools

pytestmark = pytest.mark.anyio


class Turn:
    def __init__(self, events, state):
        self.events, self.state = events, state

    @property
    def trace(self):
        return self.state.values["trace"]


async def run(graph, cid, message, *scripts):
    rec = Recorder()
    model = ScriptedChatModel(scripts=list(scripts), recorder=rec)
    ctx = GraphContext(conversation_id=cid, today=date(2026, 10, 6), model=model, user_id="u1")
    graph_input = message if isinstance(message, Command) else {"user_input": message}
    events = [chunk async for mode, chunk in graph.astream(
        graph_input, thread_config(cid), context=ctx, stream_mode=["custom", "updates"],
    ) if mode == "custom"]
    return Turn(events, await graph.aget_state(thread_config(cid))), rec


async def new_cid(db):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.commit()
    return cid


async def saved(db, cid):
    async with db() as s:
        rows = (await s.execute(select(Message).where(Message.conversation_id == cid).order_by(Message.id))).scalars()
        return [(m.role, m.content) for m in rows]


def kb(monkeypatch, scores=(0.9,), useful=True):
    async def fake(question, plan=None):
        items = [EvidenceItem(100 + i, "退换货 > 运费", "退货运费谁出", "质量问题商家承担", s)
                 for i, s in enumerate(scores)]
        return Retrieval(QueryPlan(standard_query=question), items, [e for e in items if e.score >= 0.2])

    async def check(_):
        return {"parsed": SelfCheck(useful=useful, reason="r"), "raw": None}

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))


async def test_chitchat_route_uses_no_chat_model(db, memory_graph, use_intent):
    use_intent("闲聊")
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "你好")
    assert turn.trace == ["start_turn", "resolve_reference", "classify_intent", "chitchat_reply", "finalize"]
    assert turn.events == [("understood", {"resolved_input": "你好", "intent": "闲聊"}),
                           ("token", {"text": CHITCHAT_REPLY})] and rec == []
    assert await saved(db, cid) == [("user", "你好"), ("assistant", CHITCHAT_REPLY)]
    assert [m.content for m in turn.state.values["messages"]] == ["你好", CHITCHAT_REPLY]


async def test_complaint_route_offers_actions_and_writes_no_ticket(db, memory_graph, use_intent):
    from app.db.models import Ticket
    use_intent("投诉")
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "我要投诉")
    assert turn.trace[-2:] == ["complaint_reply", "finalize"] and rec == []
    assert turn.events[1] == ("token", {"text": COMPLAINT_REPLY})
    assert [o["type"] for o in turn.events[2][1]["options"]] == ["handoff", "ticket"]
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalars().all() == []


async def test_knowledge_route_passes_gate_into_agent(db, memory_graph, use_intent, monkeypatch, caplog):
    caplog.set_level("INFO")
    use_intent("商品咨询")
    kb(monkeypatch)
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "X3 Pro 续航多久", text("商家承担[1]"))
    assert turn.trace == ["start_turn", "resolve_reference", "classify_intent", "retrieve",
                          "confidence_gate", "agent_model", "finalize"]
    assert "node=retrieve" in caplog.text
    assert [e[0] for e in turn.events][1] == "citations"
    assert "## 知识库证据" in rec[0]["messages"][0].content


async def test_knowledge_route_weak_evidence_falls_back(db, memory_graph, use_intent, monkeypatch):
    use_intent("商品咨询")
    kb(monkeypatch, scores=(0.05,))
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "X9 防水吗")
    assert turn.trace[-3:] == ["confidence_gate", "fallback_reply", "finalize"] and rec == []
    assert turn.events == [("understood", {"resolved_input": "X9 防水吗", "intent": "商品咨询"}),
                           ("token", {"text": GATE_FALLBACK_REPLY})]
    async with db() as s:
        assert (await s.execute(select(LowConfidenceQuestion))).scalar_one().source == "retrieval_low_conf"


async def test_business_route_multi_step_react(db, memory_graph, use_intent, caplog):
    caplog.set_level("INFO")
    use_intent("订单")
    cid = await new_cid(db)
    turn, rec = await run(
        memory_graph, cid, "订单 1001 第一件商品保修多久，物流到哪了",
        tools(("c1", "query_order", {"order_id": "1001"})),
        tools(("c2", "query_product", {"product_id": "P002"}), ("c3", "query_logistics", {"order_id": "1001"})),
        text("保修 180 天，运输中"),
    )
    assert turn.trace[3:] == ["agent_model", "agent_tools", "agent_model", "agent_tools", "agent_model", "finalize"]
    assert turn.state.values["steps"] == 2
    assert [e[0] for e in turn.events].count("tool_start") == 2
    assert [r for r, _ in await saved(db, cid)] == ["user", "assistant", "tool", "assistant", "tool", "tool", "assistant"]
    assert "steps=2" in caplog.text and "route=business" in caplog.text


async def test_agent_create_ticket_call_writes_no_ticket(db, memory_graph, use_intent):
    from app.db.models import Ticket

    use_intent("订单")
    cid = await new_cid(db)
    turn, rec = await run(
        memory_graph, cid, "耳机坏了",
        tools(("c1", "create_ticket", {"description": "x", "ticket_type": "投诉"})),
        text("请点击按钮建工单"),
    )
    assert len(rec) == 2 and turn.state.values["reply"] == "请点击按钮建工单"
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalars().all() == []


async def test_step_limit_forces_text_answer(db, memory_graph, use_intent, monkeypatch):
    monkeypatch.setattr(agent_mod, "AGENT_MAX_STEPS", 1)
    use_intent("物流")
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "到哪了",
                          tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    assert rec[1]["tools"] == [] and turn.state.values["force_final"] is True
    assert turn.state.values["reply"] == "运输中"


async def test_second_turn_sees_first_turn_history(db, memory_graph, use_intent):
    use_intent("物流", "物流")
    cid = await new_cid(db)
    await run(memory_graph, cid, "订单 1001 到哪了",
              tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, rec = await run(memory_graph, cid, "那哪天到？", text("明天"))
    sent = rec[0]["messages"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in sent)
    assert sent[-1].content == "那哪天到？"


async def test_failed_turn_leaves_history_and_next_turn_restarts(db, memory_graph, use_intent):
    use_intent("物流", "闲聊")
    cid = await new_cid(db)
    with pytest.raises(RuntimeError):
        await run(memory_graph, cid, "到哪了",
                  tools(("c1", "query_logistics", {"order_id": "1001"})), [RuntimeError("upstream")])
    state = await memory_graph.aget_state(thread_config(cid))
    assert state.values.get("messages", []) == [] and state.next == ("agent_model",)
    assert await saved(db, cid) == []
    turn, _ = await run(memory_graph, cid, "你好")
    assert turn.trace[0] == "start_turn" and turn.state.values["steps"] == 0
    assert [m.content for m in turn.state.values["messages"]] == ["你好", CHITCHAT_REPLY]


async def test_finalize_db_failure_raises_and_keeps_history(db, memory_graph, use_intent, monkeypatch):
    from app.repositories import messages as messages_repo

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(messages_repo, "add_turn", boom)
    use_intent("闲聊")
    cid = await new_cid(db)
    with pytest.raises(RuntimeError):
        await run(memory_graph, cid, "你好")
    assert (await memory_graph.aget_state(thread_config(cid))).values.get("messages", []) == []


async def test_turn_log_line(db, memory_graph, use_intent, caplog):
    caplog.set_level("INFO")
    use_intent("闲聊")
    cid = await new_cid(db)
    await run(memory_graph, cid, "你好")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("turn "))
    assert f"conversation={cid}" in line and "intent=闲聊" in line and "route=chitchat" in line
    assert "trace=" in line and "steps=0" in line


def test_get_graph_requires_initialisation():
    set_graph(None)
    with pytest.raises(RuntimeError):
        get_graph()


async def test_open_graph_creates_sqlite_file(tmp_path):
    from app.graph.builder import open_graph
    path = tmp_path / "sub" / "cp.sqlite"
    async with open_graph(str(path)) as graph:
        assert get_graph() is graph
    assert path.exists()
    with pytest.raises(RuntimeError):
        get_graph()


async def test_sqlite_checkpointer_concurrent_conversations(db, tmp_path, use_intent):
    import asyncio
    from app.graph.builder import open_graph
    use_intent("物流", "物流")
    a, b = await new_cid(db), await new_cid(db)
    async with open_graph(str(tmp_path / "cp.sqlite")) as graph:
        await asyncio.gather(
            run(graph, a, "订单 1 到哪了", tools(("a1", "query_logistics", {"order_id": "1"})), text("A")),
            run(graph, b, "订单 2 到哪了", tools(("b1", "query_logistics", {"order_id": "2"})), text("B")),
        )
        sa = await graph.aget_state(thread_config(a))
        sb = await graph.aget_state(thread_config(b))
    assert [m.content for m in sa.values["messages"]][0] == "订单 1 到哪了"
    assert [m.content for m in sb.values["messages"]][-1] == "B"


def kb_multi(monkeypatch, scores=(0.9,), useful=True):
    seen = []

    async def fake(queries, plan):
        seen.append((queries, plan))
        items = [EvidenceItem(200 + i, "退货政策 > 条件", f"问{i}", f"答{i}", s) for i, s in enumerate(scores)]
        return Retrieval(plan, items, [e for e in items if e.score >= 0.20])

    async def check(_):
        return {"parsed": SelfCheck(useful=useful, reason="r"), "raw": None}

    monkeypatch.setattr(aftersales_nodes, "retrieve_multi", fake)
    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))
    return seen


async def test_aftersales_with_order_runs_subflow(db, memory_graph, use_intent, use_resolver, use_expander,
                                                monkeypatch, caplog):
    caplog.set_level("INFO")
    use_resolver({"resolved_input": "订单 1001 能退吗", "standard_query": "退货条件",
                  "order_scoped": True, "order_id": "1001"})
    use_intent("退款退货")
    use_expander(["退货运费"])
    seen = kb_multi(monkeypatch)
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "订单 1001 能退吗", text("可以退[1]"))
    assert turn.trace == ["start_turn", "resolve_reference", "classify_intent", "ensure_order", "fetch_order",
                          "expand_query", "retrieve_multi", "confidence_gate", "agent_model", "finalize"]
    assert seen[0][0] == ["退货条件", "退货运费"]
    assert turn.state.values["order"]["order_id"] == "1001"
    assert "## 订单数据" in rec[0]["messages"][0].content
    assert "order=1001" in caplog.text and "queries=2" in caplog.text and "resolved=订单 1001 能退吗" in caplog.text


async def test_aftersales_policy_only_skips_order(db, memory_graph, use_intent, use_resolver, use_expander, monkeypatch):
    use_resolver({"resolved_input": "拆封了还能退吗", "standard_query": "拆封后退货条件", "order_scoped": False})
    use_intent("退款退货")
    use_expander(["拆封退货运费"])
    kb_multi(monkeypatch)
    cid = await new_cid(db)
    turn, _ = await run(memory_graph, cid, "拆封了还能退吗", text("不影响二次销售可退[1]"))
    assert "ensure_order" not in turn.trace and "fetch_order" not in turn.trace
    assert turn.trace[3:5] == ["expand_query", "retrieve_multi"]


async def test_aftersales_weak_policy_falls_back(db, memory_graph, use_intent, use_resolver, use_expander, monkeypatch):
    use_resolver({"order_scoped": False})
    use_intent("售后")
    use_expander(["x"])
    kb_multi(monkeypatch, scores=(0.05,))
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "维修要多久")
    assert turn.trace[-3:] == ["confidence_gate", "fallback_reply", "finalize"] and rec == []


async def test_invented_order_id_leads_to_picker(db, memory_graph, use_intent, use_resolver):
    use_resolver({"resolved_input": "订单 2002 能退吗", "standard_query": "退货条件",
                  "order_scoped": True, "order_id": "2002"})
    use_intent("退款退货")
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "这个能退吗")
    state = await memory_graph.aget_state(thread_config(cid))
    assert state.next == ("ensure_order",) and state.interrupts[0].value["type"] == "order_picker"
    assert rec == [] and await saved(db, cid) == []


async def test_interrupt_then_resume_finishes_turn_once(db, memory_graph, use_intent, use_resolver, use_expander,
                                                      monkeypatch):
    use_resolver({"resolved_input": "我要退货", "standard_query": "退货流程", "order_scoped": True})
    use_intent("退款退货")
    use_expander(["退货运费"])
    kb_multi(monkeypatch)
    cid = await new_cid(db)
    await run(memory_graph, cid, "我要退货")
    picked = (await memory_graph.aget_state(thread_config(cid))).interrupts[0].value["orders"][0]["order_id"]
    turn, rec = await run(memory_graph, cid, Command(resume=picked), text("可以退[1]"))
    assert turn.state.values["order_id"] == picked
    assert turn.trace[2:] == ["classify_intent", "ensure_order", "fetch_order", "expand_query",
                              "retrieve_multi", "confidence_gate", "agent_model", "finalize"]
    assert await saved(db, cid) == [("user", "我要退货"), ("assistant", "可以退[1]")]
    assert [m.content for m in turn.state.values["messages"]] == ["我要退货", "可以退[1]"]


async def test_new_input_while_interrupted_restarts(db, memory_graph, use_intent, use_resolver):
    use_resolver({"order_scoped": True}, {})
    use_intent("退款退货", "闲聊")
    cid = await new_cid(db)
    await run(memory_graph, cid, "我要退货")
    turn, _ = await run(memory_graph, cid, "你好")
    assert turn.trace[-2:] == ["chitchat_reply", "finalize"]
    state = await memory_graph.aget_state(thread_config(cid))
    assert state.interrupts == () and [m.content for m in state.values["messages"]] == ["你好", CHITCHAT_REPLY]
