import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import LowConfidenceQuestion
from app.graph import routing
from app.graph.nodes import intent as intent_mod
from app.graph.nodes import knowledge as knowledge_nodes
from app.graph.nodes.replies import chitchat_reply, complaint_reply, fallback_reply
from app.graph.nodes.turn import resolve_reference, start_turn
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, GATE_FALLBACK_REPLY, REFUSAL_PREFIX
from app.repositories import conversations
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import rt
from tests.test_layers import turn

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("intent, route", [
    ("商品咨询", "knowledge"), ("退款退货", "aftersales"), ("售后", "aftersales"),
    ("物流", "business"), ("订单", "business"), ("其他", "business"),
    ("投诉", "complaint"), ("闲聊", "chitchat"), (None, "business"), ("未知", "business"),
])
def test_route_for(intent, route):
    assert routing.route_for(intent) == route


def test_after_intent_splits_aftersales():
    assert routing.after_intent({"route": "aftersales", "order_scoped": True}) == "ensure_order"
    assert routing.after_intent({"route": "aftersales", "order_scoped": False}) == "expand_query"
    assert routing.after_intent({"route": "knowledge"}) == "knowledge"


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


async def test_start_turn_resets_turn_fields(db, caplog):
    caplog.set_level("INFO")
    stale = {"intent": "投诉", "route": "complaint", "evidence": [{"n": 1}], "retrieval": [{"chunk_id": 100, "score": 0.8}], "gate": {"passed": False},
             "agent_messages": ["x"], "steps": 3, "tokens_used": 999, "force_final": True,
             "standard_query": "旧问题", "product_category": "蓝牙耳机", "order_scoped": True,
             "order_id": "1001", "history_recall": True, "ticket_request": True, "status_query": True, "order": {"order_id": "1001"},
             "approvals": {"t1": "approved"}, "write_decision": "confirmed", "write_outcome": {"status": "成功"},
             "queries": ["旧问题"], "intent_confidence": 0.9,
             "summary": "旧梗概", "summary_upto": 4, "layer1_from": 8,
             "reply": "旧", "actions": [{"type": "handoff"}], "trace": ["a", "b"]}
    out = await start_turn(stale, rt(conversation_id=7))
    assert out == {"resolved_input": "", "intent": None, "route": "", "evidence": [], "retrieval": None, "gate": None,
                   "standard_query": "", "product_category": None, "order_scoped": False,
                   "order_id": None, "history_recall": False, "ticket_request": False, "status_query": False,
                   "approvals": {}, "write_decision": None, "write_outcome": None,
                   "order": None, "queries": [], "intent_confidence": None,
                   "agent_messages": [], "steps": 0, "tokens_used": 0, "force_final": False,
                   "reply": "", "actions": [], "trace": ["start_turn"],
                   "summary": None, "summary_upto": None, "layer1_from": None}
    assert out["standard_query"] == ""
    assert out["order_id"] is None
    assert out["order"] is None
    assert out["queries"] == []
    assert out["order_scoped"] is False
    assert out["product_category"] is None
    assert out["intent_confidence"] is None
    assert "node=start_turn conversation=7" in caplog.text


async def test_resolve_reference_writes_resolution(use_resolver, caplog):
    from langchain_core.messages import AIMessage, HumanMessage
    caplog.set_level("INFO")
    calls = use_resolver({"resolved_input": "订单 1001 能退吗", "standard_query": "退货条件",
                          "order_scoped": True, "order_id": "1001"})
    state = {"user_input": "它能退吗", "messages": [HumanMessage("订单 1001 到哪了"), AIMessage("运输中")], "trace": []}
    out = await resolve_reference(state, rt())
    assert out["resolved_input"] == "订单 1001 能退吗" and out["standard_query"] == "退货条件"
    assert out["order_scoped"] is True and out["order_id"] == "1001" and out["trace"] == ["resolve_reference"]
    assert calls[0]["history"] == "用户：订单 1001 到哪了\n客服：运输中"
    assert "resolved=订单 1001 能退吗" in caplog.text


async def test_resolve_reference_uses_layered_history(use_resolver, caplog):
    caplog.set_level("INFO")
    calls = use_resolver({})
    state = {"user_input": "那个呢", "trace": [], "summary": "第1段：订单 1001 要换货",
             "summary_upto": 2, "layer1_from": 4,
             "messages": [*turn(1, 2, user="很早的话"), *turn(3, 4, user="订单 1002 呢", reply="好" * 100),
                          *turn(5, 6, user="运费呢", reply="商家承担")]}
    await resolve_reference(state, rt(conversation_id=7))
    assert calls[0]["history"] == ("梗概：第1段：订单 1001 要换货\n用户：订单 1002 呢\n客服：" + "好" * 60 + "…"
                                   "\n用户：运费呢\n客服：商家承担")
    assert "history_ctx conversation=7 lines=5 summary=第1段：订单 1001 要换货" in caplog.text
    assert "很早的话" not in calls[0]["history"]


async def test_classify_intent_sets_route_and_emits_understood(use_intent, emitted, caplog):
    caplog.set_level("INFO")
    use_intent(("退款退货", 0.92))
    out = await intent_mod.classify_intent({"resolved_input": "蓝牙耳机能退吗", "trace": []}, rt())
    assert out["intent"] == "退款退货" and out["intent_confidence"] == 0.92
    assert out["route"] == routing.route_for("退款退货")
    assert emitted == [("understood", {"resolved_input": "蓝牙耳机能退吗", "intent": "退款退货"})]
    assert "confidence=0.92" in caplog.text and "intent_model=large" in caplog.text


async def test_classify_intent_failure_falls_back(use_intent, emitted):
    use_intent(None)
    out = await intent_mod.classify_intent({"resolved_input": "x", "trace": []}, rt())
    assert out["intent"] is None and out["intent_confidence"] is None and out["route"] == "business"
    assert emitted == [("understood", {"resolved_input": "x", "intent": None})]


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


def fake_retrieval(scores):
    items = [EvidenceItem(100 + i, f"退换货 > 规则{i}", f"问{i}", f"答{i}", s) for i, s in enumerate(scores)]
    kept = [e for e in items if e.score >= 0.20]
    return Retrieval(QueryPlan(standard_query="q"), items, kept)


async def new_conversation(db):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.commit()
    return cid


def use_checker(monkeypatch, useful=True, reason="依据[1]"):
    calls = []

    async def check(inputs):
        calls.append(inputs)
        return {"parsed": SelfCheck(useful=useful, reason=reason), "raw": None}

    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))
    return calls


async def test_retrieve_numbers_evidence_and_records_top_score(monkeypatch, caplog):
    caplog.set_level("INFO")
    seen = []

    async def fake(question, plan=None, top_n=None):
        seen.append((question, plan))
        return fake_retrieval([0.9, 0.5, 0.1])

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    out = await knowledge_nodes.retrieve_evidence(
        {"resolved_input": "退货运费谁出", "standard_query": "退货运费", "product_category": None, "trace": []}, rt())
    assert seen == [("退货运费谁出", QueryPlan(standard_query="退货运费", product_category=None))]
    assert [(e["n"], e["chunk_id"]) for e in out["evidence"]] == [(1, 100), (2, 101)]
    assert set(out["evidence"][0]) == {"n", "chunk_id", "section_path", "question", "answer"}
    assert out["gate"]["top_score"] == 0.9 and out["trace"] == ["retrieve"]
    assert "node=retrieve" in caplog.text
    # 快照取门槛过滤前的 ranked，包含 0.1 这条低分证据。
    assert [(r["chunk_id"], r["score"]) for r in out["retrieval"]] == [(100, 0.9), (101, 0.5), (102, 0.1)]
    assert set(out["retrieval"][0]) == {"chunk_id", "section_path", "question", "answer", "score"}


async def test_retrieve_with_no_hits(monkeypatch):
    seen = []

    async def fake(question, plan=None, top_n=None):
        seen.append((question, plan))
        return Retrieval(QueryPlan(standard_query="q"), [], [])

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    out = await knowledge_nodes.retrieve_evidence(
        {"resolved_input": "q", "standard_query": "q", "product_category": None, "trace": []}, rt())
    assert seen == [("q", QueryPlan(standard_query="q", product_category=None))]
    assert out["evidence"] == [] and out["gate"]["top_score"] is None
    assert out["retrieval"] == []


def gate_state(evidence, scores):
    snap = [{"chunk_id": 100 + i, "section_path": "p", "question": "q", "answer": "a", "score": sc}
            for i, sc in enumerate(scores)]
    return {"user_input": "原话", "resolved_input": "原话", "evidence": evidence, "retrieval": snap,
            "gate": {"passed": False, "top_score": scores[0] if scores else None, "reason": "", "source": None},
            "trace": []}


def gate_view(gate):
    return {k: gate[k] for k in ("passed", "reason", "source")}


EVIDENCE = [{"n": 1, "chunk_id": 100, "section_path": "退换货 > 运费", "question": "退货运费谁出",
             "answer": "质量问题商家承担"}]


async def test_gate_passes_and_emits_citations(db, monkeypatch, emitted):
    calls = use_checker(monkeypatch)
    out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, [0.8]), rt(await new_conversation(db)))
    assert gate_view(out["gate"]) == {"passed": True, "reason": "依据[1]", "source": None}
    assert out["gate"]["confidence"] == 0.8 and out["gate"]["signals"]["top1"] == 0.8
    assert out["gate"]["top_score"] == 0.8
    assert "[1] 退换货 > 运费" in calls[0]["evidence"]
    assert emitted == [("citations", {"items": EVIDENCE, "refused": False})]


@pytest.mark.parametrize("evidence,scores", [([], []), (EVIDENCE, [0.1])])
async def test_gate_low_score_pools_without_self_check(db, monkeypatch, emitted, evidence, scores):
    cid = await new_conversation(db)
    state = gate_state(evidence, scores)
    out = await knowledge_nodes.confidence_gate(state, rt(cid))
    assert out["gate"]["passed"] is False and out["gate"]["source"] == "retrieval_low_conf"
    assert emitted == []
    async with db() as s:
        row = (await s.execute(select(LowConfidenceQuestion))).scalar_one()
    assert (row.conversation_id, row.raw_question, row.source) == (cid, "原话", "retrieval_low_conf")
    assert row.retrieved_chunks == state["retrieval"]


async def test_gate_uses_weighted_confidence(db, monkeypatch, emitted):
    monkeypatch.setattr(knowledge_nodes, "GATE_WEIGHTS", (0.0, 0.0, 1.0))
    monkeypatch.setattr(knowledge_nodes, "GATE_CONF_THRESHOLD", 0.3)
    cid = await new_conversation(db)
    # Top-1 很高，但与 Top-2 几乎相同，分差 0.01 < 0.3。
    out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, [0.9, 0.89]), rt(cid))
    assert out["gate"]["passed"] is False and out["gate"]["source"] == "retrieval_low_conf"
    assert out["gate"]["signals"]["margin"] == pytest.approx(0.01, abs=1e-4)


async def test_gate_self_check_not_useful_pools_with_snapshot(db, monkeypatch, emitted):
    use_checker(monkeypatch, useful=False, reason="没写到防水")
    cid = await new_conversation(db)
    state = gate_state(EVIDENCE, [0.8])
    out = await knowledge_nodes.confidence_gate(state, rt(cid))
    assert gate_view(out["gate"]) == {"passed": False, "reason": "没写到防水", "source": "self_check"}
    assert emitted == []
    async with db() as s:
        row = (await s.execute(select(LowConfidenceQuestion))).scalar_one()
    assert (row.source, row.reason, row.retrieved_chunks) == ("self_check", "没写到防水", state["retrieval"])


async def test_gate_self_check_failure_fails_open(db, monkeypatch, emitted):
    async def boom(_):
        raise RuntimeError("upstream")

    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(boom))
    out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, [0.8]), rt(await new_conversation(db)))
    assert out["gate"]["passed"] is True


async def test_gate_aftersales_passes_on_score_without_self_check(db, emitted):
    state = gate_state(EVIDENCE, [0.8])
    state["route"] = "aftersales"
    out = await knowledge_nodes.confidence_gate(state, rt(await new_conversation(db)))
    assert gate_view(out["gate"]) == {"passed": True, "reason": "", "source": None}
    assert ("citations", {"items": EVIDENCE, "refused": False}) in emitted


async def test_gate_aftersales_low_score_still_falls_back(db, emitted):
    cid = await new_conversation(db)
    state = gate_state(EVIDENCE, [0.05])
    state["route"] = "aftersales"
    out = await knowledge_nodes.confidence_gate(state, rt(cid))
    assert out["gate"]["passed"] is False and out["gate"]["source"] == "retrieval_low_conf"
    async with db() as s:
        row = (await s.execute(select(LowConfidenceQuestion))).scalar_one()
    assert (row.conversation_id, row.raw_question, row.source) == (cid, "原话", "retrieval_low_conf")


async def test_start_turn_loads_anchors(db, emitted):
    from sqlalchemy import update
    from app.db.models import Conversation
    from app.graph.nodes.turn import start_turn
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.execute(update(Conversation).where(Conversation.id == cid)
                        .values(summary="第1段：订单 1001", summary_upto_msg_id=4, layer1_from_msg_id=8))
        await s.commit()
    out = await start_turn({}, rt(conversation_id=cid))
    assert (out["summary"], out["summary_upto"], out["layer1_from"]) == ("第1段：订单 1001", 4, 8)
    assert out["retrieval"] is None
