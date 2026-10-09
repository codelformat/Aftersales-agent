from datetime import date

import pytest

from app.db.models import Message
from app.repositories import conversations, low_confidence, messages, tickets
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
    async with db() as s:
        written = await messages.add_turn(s, cid, [
            NewMessage(role="user", content="订单 1001"),
            NewMessage(role="assistant", content="已发货"),
        ])
        assert len(written) == 2
        assert all(isinstance(row, Message) and row.id > 0 for row in written)
        assert written[0].id < written[1].id
        await s.commit()
    async with db() as s:
        rows = await messages.list_for_conversation(s, cid)
    assert [(r.role, r.content) for r in rows] == [("user", "订单 1001"), ("assistant", "已发货")]
    assert [r.id for r in rows] == [r.id for r in written]
    assert all(r.tool_calls is None and r.tool_call_id is None for r in rows)


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


async def test_ticket_fk_error_not_retried(db):
    from sqlalchemy.exc import IntegrityError

    delays = []

    async def fake_sleep(d):
        delays.append(d)

    with pytest.raises(IntegrityError):
        await tickets.create_ticket_record(db, 99999999, "x", "售后", date(2026, 10, 6), sleep=fake_sleep)
    assert delays == []


async def test_ticket_number_after_999(db):
    from app.db.models import Ticket

    cid = await _new_conversation(db)
    async with db() as s:
        s.add(Ticket(ticket_no="T20261006999", conversation_id=cid, description="x", ticket_type="售后"))
        await s.commit()
    t = await tickets.create_ticket_record(db, cid, "y", "售后", date(2026, 10, 6))
    assert t.ticket_no == "T202610061000"


async def test_ticket_number_uses_numeric_max_across_widths(db):
    from app.db.models import Ticket

    cid = await _new_conversation(db)
    async with db() as s:
        for ticket_no in ("T20261006999", "T202610061000"):
            s.add(Ticket(ticket_no=ticket_no, conversation_id=cid, description="x", ticket_type="售后"))
        await s.commit()
    t = await tickets.create_ticket_record(db, cid, "y", "售后", date(2026, 10, 6))
    assert t.ticket_no == "T202610061001"


async def test_low_confidence_add(db):
    async with db() as s:
        row = await low_confidence.add(s, conversation_id=None, raw_question="q", source="self_check", reason=None)
        await s.commit()
        await s.refresh(row)
    assert row.id > 0 and row.created_at is not None and row.conversation_id is None


async def test_get_context_reads_anchors(db):
    from sqlalchemy import update
    from app.db.models import Conversation

    cid = await _new_conversation(db)
    async with db() as s:
        await s.execute(update(Conversation).where(Conversation.id == cid)
                        .values(summary="第1段：订单 1001", summary_upto_msg_id=4, layer1_from_msg_id=8))
        await s.commit()
    async with db() as s:
        anchors = await conversations.get_context(s, cid)
    assert (anchors.summary, anchors.summary_upto, anchors.layer1_from) == ("第1段：订单 1001", 4, 8)


async def test_get_context_returns_empty_for_missing_conversation(db):
    async with db() as s:
        anchors = await conversations.get_context(s, 99999999)
    assert (anchors.summary, anchors.summary_upto, anchors.layer1_from) == (None, None, None)
