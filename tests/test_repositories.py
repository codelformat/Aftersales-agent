from datetime import date

import pytest

from app.repositories import conversations, faq, messages, tickets
from app.repositories.messages import NewMessage

pytestmark = pytest.mark.anyio


async def _new_conversation(db, user_id="u1"):
    async with db() as s:
        conv = await conversations.create(s, user_id)
        await s.commit()
        return conv.id


async def test_get_for_user_checks_owner(db):
    cid = await _new_conversation(db, "u1")
    async with db() as s:
        assert (await conversations.get_for_user(s, cid, "u1")).id == cid
        assert await conversations.get_for_user(s, cid, "u2") is None
        assert await conversations.get_for_user(s, cid + 999, "u1") is None


async def test_add_turn_and_list_in_order(db):
    cid = await _new_conversation(db)
    calls = [{"id": "c1", "name": "query_order", "args": {"order_id": "1001"}}]
    async with db() as s:
        await messages.add_turn(s, cid, [
            NewMessage(role="user", content="订单 1001"),
            NewMessage(role="assistant", content=None, tool_calls=calls),
            NewMessage(role="tool", content='{"ok": true}', tool_call_id="c1"),
            NewMessage(role="assistant", content="已发货"),
        ])
        await s.commit()
    async with db() as s:
        rows = await messages.list_for_conversation(s, cid)
    assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1].tool_calls == calls
    assert rows[2].tool_call_id == "c1"


async def test_faq_search_hits_and_misses(db):
    async with db() as s:
        hit = await faq.search(s, "退货政策", 3)
        miss = await faq.search(s, "邮费", 3)
        many = await faq.search(s, "退货", 3)
    assert [f.question for f in hit] == ["退货政策是什么？"]
    assert miss == []
    assert len(many) == 3


async def test_faq_search_escapes_wildcards(db):
    async with db() as s:
        assert await faq.search(s, "%", 3) == []
        assert await faq.search(s, "_", 3) == []


async def test_ticket_numbers_increment(db):
    cid = await _new_conversation(db)
    today = date(2026, 10, 6)
    t1 = await tickets.create_ticket_record(db, cid, "耳机坏了", "售后", today)
    t2 = await tickets.create_ticket_record(db, cid, "快递员态度差", "投诉", today)
    assert (t1.ticket_no, t2.ticket_no) == ("T20261006001", "T20261006002")
    assert t1.status == "待处理"
    async with db() as s:
        conv = await conversations.get_for_user(s, cid, "u1")
    assert conv.status == "已转人工"


async def test_ticket_number_conflict_retries_with_backoff(db):
    cid = await _new_conversation(db)
    today = date(2026, 10, 6)
    await tickets.create_ticket_record(db, cid, "第一单", "售后", today)
    calls, delays = [], []

    async def stale_then_fresh(session, day):
        calls.append(day)
        if len(calls) == 1:
            return "T20261006001"  # 模拟并发：拿到已被占用的号
        return await tickets.next_ticket_no(session, day)

    async def fake_sleep(d):
        delays.append(d)

    t = await tickets.create_ticket_record(
        db, cid, "第二单", "投诉", today, sleep=fake_sleep, rand=lambda: 1.0, next_no=stale_then_fresh,
    )
    assert t.ticket_no == "T20261006002"
    assert delays == [0.05]
