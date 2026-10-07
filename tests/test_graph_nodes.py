import asyncio

import pytest

from app.graph import routing
from app.graph.nodes.intent import classify_intent
from app.graph.nodes.replies import chitchat_reply, complaint_reply, fallback_reply
from app.graph.nodes.turn import resolve_reference, start_turn
from app.prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, GATE_FALLBACK_REPLY, REFUSAL_PREFIX
from tests.fakes import rt

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("intent,route", [
    ("商品咨询", "knowledge"), ("退款退货", "knowledge"),
    ("物流", "business"), ("订单", "business"), ("售后", "business"),
    ("投诉", "complaint"), ("闲聊", "chitchat"), (None, "business"),
])
def test_route_for(intent, route):
    assert routing.route_for(intent) == route


def test_every_intent_has_a_route():
    from app.schemas import INTENTS
    assert set(routing.INTENT_ROUTES) == set(INTENTS)


def test_after_gate_and_after_agent():
    from langchain_core.messages import AIMessage
    assert routing.after_gate({"gate": {"passed": True}}) == "agent_model"
    assert routing.after_gate({"gate": {"passed": False}}) == "fallback_reply"
    call = AIMessage(content="", tool_calls=[{"id": "c1", "name": "query_order", "args": {}}])
    assert routing.after_agent({"agent_messages": [call], "force_final": False}) == "agent_tools"
    assert routing.after_agent({"agent_messages": [call], "force_final": True}) == "finalize"
    assert routing.after_agent({"agent_messages": [AIMessage(content="好")]}) == "finalize"


async def test_start_turn_resets_turn_fields(caplog):
    caplog.set_level("INFO")
    stale = {"intent": "投诉", "route": "complaint", "evidence": [{"n": 1}], "gate": {"passed": False},
             "agent_messages": ["x"], "steps": 3, "tokens_used": 999, "force_final": True,
             "reply": "旧", "actions": [{"type": "handoff"}], "trace": ["a", "b"]}
    out = await start_turn(stale, rt(conversation_id=7))
    assert out == {"resolved_input": "", "intent": None, "route": "", "evidence": [], "gate": None,
                   "agent_messages": [], "steps": 0, "tokens_used": 0, "force_final": False,
                   "reply": "", "actions": [], "trace": ["start_turn"]}
    assert "node=start_turn conversation=7" in caplog.text


async def test_resolve_reference_passes_through():
    out = await resolve_reference({"user_input": "那它呢", "trace": ["start_turn"]}, rt())
    assert out == {"resolved_input": "那它呢", "trace": ["start_turn", "resolve_reference"]}


async def test_classify_intent_sets_route(use_intent):
    calls = use_intent("物流")
    out = await classify_intent({"resolved_input": "到哪了", "trace": []}, rt())
    assert (out["intent"], out["route"]) == ("物流", "business")
    assert calls == [{"text": "到哪了"}]


@pytest.mark.parametrize("value", [None, RuntimeError("boom"), asyncio.TimeoutError()])
async def test_classify_intent_failure_falls_back(use_intent, value, caplog):
    use_intent(value)
    out = await classify_intent({"resolved_input": "q", "trace": []}, rt())
    assert (out["intent"], out["route"]) == (None, "business")
    assert "意图识别失败" in caplog.text


async def test_classify_intent_timeout(monkeypatch, use_intent):
    from langchain_core.runnables import RunnableLambda
    from app.graph.nodes import intent as intent_mod
    monkeypatch.setattr(intent_mod, "INTENT_TIMEOUT_SECONDS", 0.01)

    async def slow(_):
        await asyncio.sleep(1)

    monkeypatch.setattr(intent_mod, "get_intent_classifier", lambda: RunnableLambda(slow))
    out = await classify_intent({"resolved_input": "q", "trace": []}, rt())
    assert out["route"] == "business"


async def test_chitchat_reply(emitted):
    out = await chitchat_reply({"trace": []}, rt())
    assert out == {"reply": CHITCHAT_REPLY, "trace": ["chitchat_reply"]}
    assert emitted == [("token", {"text": CHITCHAT_REPLY})]


async def test_complaint_reply_offers_two_independent_options(emitted):
    out = await complaint_reply({"user_input": "我要投诉" + "很" * 600, "trace": []}, rt())
    assert out["reply"] == COMPLAINT_REPLY
    handoff, ticket = out["actions"]
    assert handoff == {"type": "handoff"}
    assert ticket["type"] == "ticket" and ticket["ticket_type"] == "投诉" and len(ticket["description"]) == 500
    assert emitted == [("token", {"text": COMPLAINT_REPLY}), ("actions", {"options": out["actions"]})]


async def test_fallback_reply(emitted):
    out = await fallback_reply({"trace": []}, rt())
    assert out["reply"] == GATE_FALLBACK_REPLY and GATE_FALLBACK_REPLY.startswith(REFUSAL_PREFIX)
    assert emitted == [("token", {"text": GATE_FALLBACK_REPLY})]
