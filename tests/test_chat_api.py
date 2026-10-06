import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage, ToolMessage
from sqlalchemy import select

from app.api.chat import get_token_budget
from app.db.models import Message, Ticket
from app.llm import get_chat_model
from app.main import app
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


async def test_plain_answer_single_call(client, db, use_script):
    rec = use_script(text("您好"))
    r, ev = await chat(client, "你好")
    assert ev[0][0] == "session" and ev[0][1]["session_id"].isdigit()
    assert ev[1:] == [("token", {"text": "您"}), ("token", {"text": "好"}), ("done", {"finish_reason": "stop"})]
    assert len(rec) == 1 and len(rec[0]["tools"]) == 5
    assert [(m.role, m.content) for m in await rows(db)] == [("user", "你好"), ("assistant", "您好")]
    assert "\\u" not in r.text


async def test_tool_round_events_and_persistence(client, db, use_script):
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    names = [e for e, _ in ev]
    assert names == ["session", "tool_start", "tool_end", "token", "token", "token", "done"]
    assert ev[1][1] == {"tools": [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]}
    assert ev[2][1] == {"tools": [{"id": "c1", "name": "query_logistics", "ok": True}]}
    assert rec[1]["tools"] == []
    second_input = rec[1]["messages"]
    assert isinstance(second_input[-3], AIMessage) and isinstance(second_input[-2], ToolMessage)
    assert isinstance(second_input[-1], SystemMessage) and "本轮不能再调用任何工具" in second_input[-1].content
    assert json.loads(second_input[-2].content)["data"]["status"] == "运输中"
    saved = await rows(db)
    assert [m.role for m in saved] == ["user", "assistant", "tool", "assistant"]
    assert saved[1].content is None and saved[1].tool_calls[0]["name"] == "query_logistics"
    assert saved[2].tool_call_id == "c1" and saved[3].content == "运输中"


async def test_parallel_tools_in_one_round(client, db, use_script):
    use_script(tools(("c1", "query_order", {"order_id": "1001"}), ("c2", "query_logistics", {"order_id": "1001"})),
               text("好"))
    _, ev = await chat(client, "订单 1001 买了什么、到哪了")
    assert [t["name"] for t in ev[1][1]["tools"]] == ["query_order", "query_logistics"]
    assert [m.role for m in await rows(db)] == ["user", "assistant", "tool", "tool", "assistant"]


async def test_create_ticket_injects_conversation(client, db, use_script):
    use_script(tools(("c1", "create_ticket", {"description": "要人工", "ticket_type": "投诉"})), text("已建单"))
    _, ev = await chat(client, "我要投诉，转人工")
    cid = int(ev[0][1]["session_id"])
    assert ev[2][1]["tools"][0]["ok"] is True
    async with db() as s:
        ticket = (await s.execute(select(Ticket))).scalar_one()
    assert ticket.conversation_id == cid


async def test_second_turn_sees_previous_tool_result(client, db, use_script):
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"), text("明天到"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    sid = ev[0][1]["session_id"]
    await chat(client, "那哪天到？", session_id=sid)
    third = rec[2]["messages"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in third)


async def test_other_users_conversation_is_404(client, db, use_script):
    use_script(text("好"))
    _, ev = await chat(client, "你好", user_id="alice")
    r, _ = await chat(client, "你好", session_id=ev[0][1]["session_id"], user_id="bob")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversation_not_found"


async def test_unknown_session_is_404(client, db, use_script):
    use_script(text("好"))
    r, _ = await chat(client, "你好", session_id="999999")
    assert r.status_code == 404


async def test_upstream_error_in_first_call_writes_nothing(client, db, use_script):
    use_script([*text("您"), RuntimeError("boom")])
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_upstream_error_in_second_call_writes_nothing(client, db, use_script):
    use_script(tools(("c1", "query_order", {"order_id": "1001"})), [RuntimeError("boom")])
    _, ev = await chat(client, "订单 1001")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_empty_reply_is_error(client, db, use_script):
    use_script([AIMessageChunk(content="")])
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_budget_exceeded(client, db, use_script):
    use_script(text("x"))
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "你好")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "budget_exceeded"


async def test_validation(client, db, use_script):
    use_script(text("x"))
    for body in ({"user_id": "u1", "message": "  "}, {"message": "hi"},
                 {"user_id": "u 1", "message": "hi"}, {"user_id": "u1", "message": "hi", "session_id": "abc"}):
        assert (await client.post("/chat/stream", json=body)).status_code == 422


async def test_lock_held_during_stream_and_released_after(client, db, use_script, locks):
    gate = asyncio.Event()
    use_script([*text("a"), gate, *text("b")])
    task = asyncio.create_task(chat(client, "hi"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if any(l.locked() for l in locks._locks.values()):
            break
    cid = next(k for k, l in locks._locks.items() if l.locked())
    busy, _ = await chat(client, "again", session_id=str(cid))
    assert busy.status_code == 409
    gate.set()
    _, ev = await task
    assert ev[-1] == ("done", {"finish_reason": "stop"})
    assert not locks.get(cid).locked()


async def test_lock_released_when_model_dependency_fails(client, db, locks):
    def boom():
        raise RuntimeError("config error")

    app.dependency_overrides[get_chat_model] = boom
    with pytest.raises(RuntimeError):
        await chat(client, "你好")
    assert not any(l.locked() for l in locks._locks.values())


async def test_disconnect_after_tools_writes_no_messages(db, locks):
    from app.services.chat import ChatTurn, stream_reply
    from app.repositories import conversations
    from tests.fakes import ScriptedChatModel
    from datetime import date

    async with db() as s:
        conv = await conversations.create(s, "u1")
        await s.commit()
    model = ScriptedChatModel(scripts=[tools(("c1", "create_ticket", {"description": "人工", "ticket_type": "投诉"})),
                                       text("已建单")])
    gen = stream_reply(ChatTurn(conversation_id=conv.id, history=[], user_input="转人工", today=date(2026, 10, 6)), model)
    names = []
    async for name, _ in gen:
        names.append(name)
        if name == "tool_end":
            break
    await gen.aclose()
    assert names == ["session", "tool_start", "tool_end"]
    assert await rows(db) == []
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalar_one().conversation_id == conv.id


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_tool_markup_at_start_of_second_call_is_error(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "邮费"})),
               text('<｜｜DSML｜｜ invoke name="query_faq">'))
    _, ev = await chat(client, "邮费是多少")
    assert [e for e, _ in ev] == ["session", "tool_start", "tool_end", "error"]
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_tool_markup_later_in_second_call_is_not_saved(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "邮费"})),
               text('没查到。<｜｜DSML｜｜ invoke name="query_faq">'))
    _, ev = await chat(client, "邮费是多少")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_normal_second_call_text_still_streams_per_chunk(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "退货"})), text("可以退"))
    _, ev = await chat(client, "能退吗")
    assert [d["text"] for e, d in ev if e == "token"] == ["可", "以", "退"]
    assert ev[-1] == ("done", {"finish_reason": "stop"})


async def test_persist_failure_sends_error(client, db, use_script, monkeypatch):
    from app.services import chat as chat_service

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service.messages, "add_turn", boom)
    use_script(text("您好"))
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR


async def test_tool_execution_crash_sends_error(db, locks):
    from datetime import date

    from app.repositories import conversations
    from app.services.chat import ChatTurn, stream_reply
    from tests.fakes import ScriptedChatModel

    async with db() as s:
        conv = await conversations.create(s, "u1")
        await s.commit()

    async def boom(*args, **kwargs):
        raise RuntimeError("executor crashed")

    model = ScriptedChatModel(scripts=[tools(("c1", "query_order", {"order_id": "1001"}))])
    turn = ChatTurn(conversation_id=conv.id, history=[], user_input="订单", today=date(2026, 10, 6))
    events = [e async for e in stream_reply(turn, model, execute=boom)]
    assert events[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_tool_markup_after_leading_whitespace_is_not_streamed(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "邮费"})),
               text('\n <｜｜DSML｜｜ invoke name="query_faq">'))
    _, ev = await chat(client, "邮费是多少")
    assert [e for e, _ in ev] == ["session", "tool_start", "tool_end", "error"]


async def test_budget_exceeded_on_new_session_creates_no_conversation(client, db, use_script):
    from app.db.models import Conversation

    use_script(text("x"))
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "你好")
    assert r.status_code == 422
    async with db() as s:
        assert (await s.execute(select(Conversation))).scalars().all() == []


async def test_budget_exceeded_on_existing_session_releases_lock(client, db, use_script, locks):
    use_script(text("好"))
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "再问一句", session_id=sid)
    assert r.status_code == 422
    assert not locks.get(int(sid)).locked()


async def test_tool_args_split_across_chunks_and_tool_choice_auto(client, db, use_script):
    from langchain_core.messages.tool import tool_call_chunk

    split = [
        AIMessageChunk(content="", tool_call_chunks=[tool_call_chunk(name="query_logistics", args='{"order_', id="c1", index=0)]),
        AIMessageChunk(content="", tool_call_chunks=[tool_call_chunk(name=None, args='id": "1001"}', id=None, index=0)]),
    ]
    rec = use_script(split, text("好"))
    _, ev = await chat(client, "物流")
    assert ev[1][1]["tools"][0]["args"] == {"order_id": "1001"}
    assert rec[0]["tool_choice"] == "auto"
    assert rec[1]["tool_choice"] is None


async def test_tool_round_persist_failure_sends_error(client, db, use_script, monkeypatch):
    from app.services import chat as chat_service

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service.messages, "add_turn", boom)
    use_script(tools(("c1", "query_order", {"order_id": "1001"})), text("好"))
    _, ev = await chat(client, "订单")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_busy_session_does_not_read_history(client, db, use_script, locks, monkeypatch):
    from app.repositories import messages

    use_script(text("好"))
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    reads = []

    async def record_read(*args, **kwargs):
        reads.append(args)
        return []

    monkeypatch.setattr(messages, "list_for_conversation", record_read)
    lock = locks.get(int(sid))
    await lock.acquire()
    try:
        r, _ = await chat(client, "再问一句", session_id=sid)
        assert r.status_code == 409
        assert reads == []
    finally:
        lock.release()


async def test_existing_session_holds_lock_while_reading_history(client, db, use_script, locks, monkeypatch):
    from app.repositories import messages

    use_script(text("好"))
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    observed = []
    original = messages.list_for_conversation

    async def record_read(session, conversation_id):
        observed.append(locks.get(conversation_id).locked())
        return await original(session, conversation_id)

    monkeypatch.setattr(messages, "list_for_conversation", record_read)
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "再问一句", session_id=sid)
    assert r.status_code == 422
    assert observed == [True]
    assert not locks.get(int(sid)).locked()
