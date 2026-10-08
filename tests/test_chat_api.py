import asyncio
import json

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.api.chat import get_token_budget
from app.db.models import Message
from app.graph.nodes import knowledge as knowledge_nodes
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.llm import get_chat_model
from app.main import app
from app.prompts import CHITCHAT_REPLY
from app.repositories import conversations, messages as messages_repo
from app.repositories.messages import NewMessage
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import text, tools

pytestmark = pytest.mark.anyio
UPSTREAM_ERROR = ("error", {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"})


def parse_sse(body):
    events = []
    for block in body.strip().split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        events.append((name, data))
    return events


async def chat(client, message, session_id=None, user_id="u1"):
    body = {"user_id": user_id, "message": message}
    if session_id is not None:
        body["session_id"] = session_id
    r = await client.post("/chat/stream", json=body)
    return r, (parse_sse(r.text) if r.status_code == 200 else None)


async def rows(db):
    async with db() as s:
        return (await s.execute(select(Message).order_by(Message.id))).scalars().all()


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_chitchat_events(client, db, use_script, use_intent):
    use_intent("闲聊")
    rec = use_script()
    r, ev = await chat(client, "你好")
    assert ev[0][0] == "session" and ev[0][1]["session_id"].isdigit()
    assert ev[1:] == [("understood", {"resolved_input": "你好", "intent": "闲聊"}),
                      ("token", {"text": CHITCHAT_REPLY}), ("done", {"finish_reason": "stop"})]
    assert rec == [] and "\\u" not in r.text


async def test_business_tool_round_events(client, db, use_script, use_intent):
    use_intent("物流")
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    assert [e for e, _ in ev] == ["session", "understood", "tool_start", "tool_end", "token", "token", "token", "done"]
    assert ev[2][1] == {"tools": [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]}
    assert rec[1]["tools"] == ["query_order", "query_logistics", "query_product", "offer_human_options"]
    assert [m.role for m in await rows(db)] == ["user", "assistant", "tool", "assistant"]


async def test_knowledge_events(client, db, use_script, use_intent, monkeypatch):
    use_intent("退款退货")

    async def fake(q, plan=None):
        item = EvidenceItem(7, "退换货 > 运费", "退货运费谁出", "商家承担", 0.9)
        return Retrieval(QueryPlan(standard_query=q), [item], [item])

    async def check(_):
        return {"parsed": SelfCheck(useful=True, reason="r"), "raw": None}

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))
    use_script(text("商家承担[1]"))
    _, ev = await chat(client, "退货运费谁出")
    assert ev[2] == ("citations", {"items": [{"n": 1, "chunk_id": 7, "section_path": "退换货 > 运费",
                                              "question": "退货运费谁出", "answer": "商家承担"}],
                                   "refused": False})
    assert ev[-1] == ("done", {"finish_reason": "stop"})


async def test_complaint_actions_event(client, db, use_script, use_intent):
    use_intent("投诉")
    use_script()
    _, ev = await chat(client, "我要投诉")
    assert [e for e, _ in ev] == ["session", "understood", "token", "actions", "done"]
    assert [o["type"] for o in ev[3][1]["options"]] == ["handoff", "ticket"]


async def test_second_turn_uses_checkpoint_history(client, db, use_script, use_intent):
    use_intent("物流", "物流")
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"), text("明天到"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    await chat(client, "那哪天到？", session_id=ev[0][1]["session_id"])
    assert any(getattr(m, "tool_call_id", None) == "c1" for m in rec[2]["messages"])


async def test_old_conversation_without_checkpoint_continues(client, db, use_script, use_intent):
    use_intent("闲聊")
    use_script()
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await messages_repo.add_turn(s, cid, [NewMessage(role="user", content="旧"),
                                              NewMessage(role="assistant", content="旧答")])
        await s.commit()
    r, ev = await chat(client, "你好", session_id=str(cid))
    assert r.status_code == 200 and ev[-1] == ("done", {"finish_reason": "stop"})


async def test_other_users_conversation_is_404(client, db, use_script, use_intent):
    use_intent("闲聊")
    use_script()
    _, ev = await chat(client, "你好", user_id="alice")
    r, _ = await chat(client, "你好", session_id=ev[0][1]["session_id"], user_id="bob")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversation_not_found"


async def test_unknown_session_is_404(client, db, use_script):
    use_script()
    r, _ = await chat(client, "你好", session_id="999999")
    assert r.status_code == 404


async def test_upstream_error_writes_nothing(client, db, use_script, use_intent):
    use_intent("物流")
    use_script([RuntimeError("boom")])
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR and await rows(db) == []


async def test_error_after_tools_writes_nothing(client, db, use_script, use_intent):
    use_intent("物流")
    use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), [RuntimeError("boom")])
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR and await rows(db) == []


async def test_empty_reply_is_error(client, db, use_script, use_intent):
    use_intent("物流")
    use_script([])
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR


async def test_tool_markup_is_error_and_not_streamed(client, db, use_script, use_intent):
    use_intent("物流")
    use_script(text("  <｜DSML｜invoke"))
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR
    assert not any(e == "token" and "<" in d["text"] for e, d in ev[:-1])
    assert await rows(db) == []


async def test_recursion_limit_is_error(client, db, use_script, use_intent, monkeypatch):
    from app.graph import builder
    monkeypatch.setattr(builder, "GRAPH_RECURSION_LIMIT", 4)
    use_intent("物流")
    use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("好"))
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR


async def test_budget_exceeded(client, db, use_script):
    use_script()
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "你好")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "budget_exceeded"


async def test_budget_exceeded_on_new_session_creates_no_conversation(client, db, use_script):
    from app.db.models import Conversation
    use_script()
    app.dependency_overrides[get_token_budget] = lambda: 10
    await chat(client, "你好")
    async with db() as s:
        assert (await s.execute(select(Conversation))).scalars().all() == []


async def test_validation(client, db, use_script):
    use_script()
    for body in ({"user_id": "u1", "message": "  "}, {"message": "hi"},
                 {"user_id": "u 1", "message": "hi"}, {"user_id": "u1", "message": "hi", "session_id": "abc"}):
        assert (await client.post("/chat/stream", json=body)).status_code == 422


async def test_lock_held_during_stream_and_released_after(client, db, use_script, use_intent, locks):
    use_intent("物流", "闲聊")
    gate = asyncio.Event()
    use_script([gate, *text("好")])
    first = asyncio.create_task(chat(client, "到哪了"))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(lock.locked() for lock in locks._locks.values()):
            break
    cid = next(iter(locks._locks))
    r, _ = await chat(client, "你好", session_id=str(cid))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_busy"
    gate.set()
    await first
    assert not locks.get(cid).locked()


async def test_lock_released_when_model_dependency_fails(client, db, locks):
    def boom():
        raise RuntimeError("模型配置错误")

    app.dependency_overrides[get_chat_model] = boom
    with pytest.raises(RuntimeError):
        await chat(client, "你好")
    assert not any(lock.locked() for lock in locks._locks.values())


async def test_budget_exceeded_on_existing_session_releases_lock(client, db, use_script, use_intent, locks):
    use_intent("闲聊")
    use_script()
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "再问一句", session_id=sid)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "budget_exceeded"
    assert not locks.get(int(sid)).locked()


async def test_busy_session_does_not_read_history(client, db, use_script, use_intent, locks, memory_graph, monkeypatch):
    use_intent("闲聊")
    use_script()
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]

    async def blocked_read(*args, **kwargs):
        raise AssertionError("忙碌会话不能读取历史")

    monkeypatch.setattr(memory_graph, "aget_state", blocked_read)
    lock = locks.get(int(sid))
    await lock.acquire()
    try:
        r, _ = await chat(client, "再问一句", session_id=sid)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "session_busy"
    finally:
        lock.release()


async def test_existing_session_holds_lock_while_reading_history(client, db, use_script, use_intent, locks, memory_graph, monkeypatch):
    use_intent("闲聊")
    use_script()
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    observed = []
    original = memory_graph.aget_state

    async def record_read(config):
        observed.append(locks.get(int(config["configurable"]["thread_id"])).locked())
        return await original(config)

    monkeypatch.setattr(memory_graph, "aget_state", record_read)
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "再问一句", session_id=sid)
    assert r.status_code == 422
    assert observed == [True]
    assert not locks.get(int(sid)).locked()
