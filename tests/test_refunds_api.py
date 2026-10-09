import re

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.db.models import Message
from app.graph.builder import thread_config
from app.repositories import conversations

pytestmark = pytest.mark.anyio


async def new_cid(db, user_id="u1"):
    async with db() as s:
        cid = (await conversations.create(s, user_id)).id
        await s.commit()
    return cid


def body(cid, **kw):
    return {"session_id": str(cid), "user_id": "u1", "order_id": "1001", "reason": "质量问题", **kw}


async def test_refund_created_and_note_written(client, db, locks, memory_graph):
    cid = await new_cid(db)
    r = await client.post("/refunds", json=body(cid, note="耳机左边没声音"))
    assert r.status_code == 200
    data = r.json()
    assert re.fullmatch(r"R\d{8}\d{4}", data["refund_no"]) and data["status"] == "待审核"
    async with db() as s:
        msgs = (await s.execute(select(Message))).scalars().all()
    assert [m.role for m in msgs] == ["assistant"]
    assert data["refund_no"] in msgs[0].content and "1001" in msgs[0].content and "质量问题" in msgs[0].content
    state = await memory_graph.aget_state(thread_config(cid))
    assert data["refund_no"] in state.values["messages"][-1].content


async def test_refund_other_users_session_is_404(client, db, locks):
    cid = await new_cid(db, "u2")
    r = await client.post("/refunds", json=body(cid))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversation_not_found"


async def test_refund_note_state_message_has_db_id(client, db, locks, memory_graph):
    cid = await new_cid(db)
    r = await client.post("/refunds", json=body(cid))
    assert r.status_code == 200
    async with db() as s:
        row = (await s.scalars(select(Message).where(Message.conversation_id == cid))).one()
    state = await memory_graph.aget_state(thread_config(cid))
    assert state.values["messages"][-1].id == f"msg-{row.id}"


async def test_refund_note_without_db_id_inherits_previous(client, db, locks, memory_graph, monkeypatch):
    from app.api import refunds

    async def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(refunds.messages, "add_turn", broken)
    cid = await new_cid(db)
    await memory_graph.aupdate_state(thread_config(cid), {"messages": [AIMessage("上一条", id="msg-42")]},
                                    as_node="finalize")
    r = await client.post("/refunds", json=body(cid))
    assert r.status_code == 200
    state = await memory_graph.aget_state(thread_config(cid))
    msgs = state.values["messages"]
    assert len(msgs) == 2 and msgs[0].id == "msg-42"
    assert msgs[-1].id and not msgs[-1].id.startswith("msg-")
    assert r.json()["refund_no"] in msgs[-1].content
    async with db() as s:
        assert (await s.scalars(select(Message).where(Message.conversation_id == cid))).all() == []


async def test_refund_while_busy_is_409(client, db, locks):
    cid = await new_cid(db)
    lock = locks.get(cid)
    await lock.acquire()
    try:
        r = await client.post("/refunds", json=body(cid))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "session_busy"
    finally:
        lock.release()


@pytest.mark.parametrize("patch", [{"reason": "不想要"}, {"note": "长" * 201}, {"order_id": "10 01"}])
async def test_refund_validation_is_422(client, db, locks, patch):
    cid = await new_cid(db)
    assert (await client.post("/refunds", json=body(cid, **patch))).status_code == 422
