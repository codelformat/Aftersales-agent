import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.db.models import Message, Ticket
from app.graph.builder import thread_config
from app.repositories import conversations
from tests.fakes import text

pytestmark = pytest.mark.anyio


async def new_cid(db, user_id="u1"):
    async with db() as s:
        cid = (await conversations.create(s, user_id)).id
        await s.commit()
    return cid


def body(cid, **kw):
    return {"session_id": str(cid), "user_id": "u1", "description": "快递员态度差", "ticket_type": "投诉", **kw}


async def test_create_ticket_writes_table_message_and_state(client, db, locks, memory_graph):
    cid = await new_cid(db)
    r = await client.post("/tickets", json=body(cid))
    assert r.status_code == 200
    ticket_no = r.json()["ticket_no"]
    assert r.json()["status"] == "待处理"
    async with db() as s:
        ticket = (await s.execute(select(Ticket))).scalar_one()
        msg = (await s.execute(select(Message))).scalar_one()
    assert (ticket.ticket_no, ticket.conversation_id, ticket.ticket_type) == (ticket_no, cid, "投诉")
    note = f"已为您创建工单 {ticket_no}，类型：投诉，我们会尽快处理。"
    assert (msg.role, msg.content) == ("assistant", note)
    state = await memory_graph.aget_state(thread_config(cid))
    assert [m.content for m in state.values["messages"]] == [note]
    assert isinstance(state.values["messages"][0], AIMessage) and state.next == ()


async def test_other_user_is_404(client, db, locks):
    cid = await new_cid(db, user_id="alice")
    r = await client.post("/tickets", json=body(cid))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversation_not_found"


async def test_ticket_note_state_message_has_db_id(client, db, locks, memory_graph):
    cid = await new_cid(db)
    r = await client.post("/tickets", json=body(cid))
    assert r.status_code == 200
    async with db() as s:
        row = (await s.scalars(select(Message).where(Message.conversation_id == cid))).one()
    state = await memory_graph.aget_state(thread_config(cid))
    assert state.values["messages"][-1].id == f"msg-{row.id}"


async def test_ticket_note_without_db_id_inherits_previous(client, db, locks, memory_graph, monkeypatch):
    from app.api import tickets

    async def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(tickets.messages, "add_turn", broken)
    cid = await new_cid(db)
    await memory_graph.aupdate_state(thread_config(cid), {"messages": [AIMessage("上一条", id="msg-42")]},
                                    as_node="finalize")
    r = await client.post("/tickets", json=body(cid))
    assert r.status_code == 200
    state = await memory_graph.aget_state(thread_config(cid))
    msgs = state.values["messages"]
    assert len(msgs) == 2 and msgs[0].id == "msg-42"
    assert msgs[-1].id and not msgs[-1].id.startswith("msg-")
    assert r.json()["ticket_no"] in msgs[-1].content
    async with db() as s:
        assert (await s.scalars(select(Message).where(Message.conversation_id == cid))).all() == []
        assert (await s.scalars(select(Ticket).where(Ticket.conversation_id == cid))).one()


@pytest.mark.parametrize("patch", [{"ticket_type": "退款"}, {"description": ""}, {"description": "x" * 501},
                                   {"session_id": "abc"}])
async def test_validation(client, db, locks, patch):
    cid = await new_cid(db)
    assert (await client.post("/tickets", json=body(cid, **patch))).status_code == 422


async def test_ticket_while_streaming_is_409(client, db, locks):
    cid = await new_cid(db)
    await locks.get(cid).acquire()
    try:
        r = await client.post("/tickets", json=body(cid))
    finally:
        locks.get(cid).release()
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_busy"
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalars().all() == []


async def test_tool_failure_is_502(client, db, locks, monkeypatch):
    from app.api import tickets as tickets_api
    from app.tools.executor import ToolOutcome
    from langchain_core.messages import ToolMessage

    async def fail(calls, *, conversation_id):
        return [ToolOutcome(calls[0]["id"], "create_ticket", False,
                            ToolMessage(content="{}", tool_call_id=calls[0]["id"]))]

    monkeypatch.setattr(tickets_api, "execute_tool_calls", fail)
    r = await client.post("/tickets", json=body(await new_cid(db)))
    assert r.status_code == 502 and r.json()["detail"]["code"] == "ticket_failed"


async def test_state_update_failure_still_returns_ticket(client, db, locks, memory_graph, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("checkpoint down")

    monkeypatch.setattr(memory_graph, "aupdate_state", boom)
    r = await client.post("/tickets", json=body(await new_cid(db)))
    assert r.status_code == 200
    async with db() as s:
        assert len((await s.execute(select(Ticket))).scalars().all()) == 1


async def test_next_turn_sees_ticket_note(client, db, locks, memory_graph, use_script, use_intent):
    # 真实流程：先有一轮投诉，再点建工单。build_history 要求历史从用户消息开始。
    use_intent("投诉", "订单")
    rec = use_script(text("您的工单已创建"))
    r = await client.post("/chat/stream", json={"user_id": "u1", "message": "我要投诉"})
    cid = int(r.text.split('"session_id": "')[1].split('"')[0])
    ticket_no = (await client.post("/tickets", json=body(cid))).json()["ticket_no"]
    await client.post("/chat/stream", json={"session_id": str(cid), "user_id": "u1", "message": "工单号多少"})
    assert any(ticket_no in str(m.content) for m in rec[0]["messages"])
