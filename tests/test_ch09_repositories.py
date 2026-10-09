import pytest
from sqlalchemy import text

from app.repositories import conversations, eval_runs, low_confidence, review_queue

pytestmark = pytest.mark.anyio

SNAP = [{"chunk_id": 1, "section_path": "退换货", "question": "q", "answer": "a", "score": 0.3123}]


async def _cid(db):
    async with db() as s:
        c = await conversations.create(s, "u1")
        await s.commit()
        return c.id


async def test_lcq_snapshot_and_match(db):
    cid = await _cid(db)
    async with db() as s:
        row = await low_confidence.add(s, conversation_id=cid, raw_question="保温杯能进洗碗机吗",
                                       source="retrieval_low_conf", reason="r", retrieved_chunks=SNAP)
        item = await review_queue.add(s, normalized_question="保温杯可以用洗碗机清洗吗？",
                                      suggested_answer="（待核实）不建议。")
        await low_confidence.set_matched(s, row.id, item.id)
        await s.commit()
    async with db() as s:
        got = await low_confidence.get(s, row.id)
        assert got.retrieved_chunks == SNAP and got.matched_review_id == item.id
        assert [r.id for r in await low_confidence.list_for_review(s, item.id)] == [row.id]
        assert await low_confidence.list_unmatched_ids(s) == []


async def test_unmatched_and_find_by_reason(db):
    cid = await _cid(db)
    async with db() as s:
        a = await low_confidence.add(s, conversation_id=cid, raw_question="1", source="self_check", reason="x")
        b = await low_confidence.add(s, conversation_id=cid, raw_question="2", source="user_feedback",
                                     reason="用户反馈未解决（回复 msg-9）")
        await s.commit()
    async with db() as s:
        assert await low_confidence.list_unmatched_ids(s) == [a.id, b.id]
        assert await low_confidence.list_unmatched_ids(s, limit=1) == [a.id]
        assert await low_confidence.count_unmatched(s) == 2
        hit = await low_confidence.find_by_reason(s, conversation_id=cid, source="user_feedback",
                                                  reason="用户反馈未解决（回复 msg-9）")
        assert hit.id == b.id
        assert await low_confidence.find_by_reason(s, conversation_id=cid, source="user_feedback",
                                                   reason="用户反馈未解决（回复 msg-10）") is None


async def test_review_queue_order_increment_status(db):
    async with db() as s:
        a = await review_queue.add(s, normalized_question="A？", suggested_answer="（待核实）a")
        b = await review_queue.add(s, normalized_question="B？", suggested_answer=None)
        await review_queue.increment(s, b.id)
        await s.commit()
    async with db() as s:
        items = await review_queue.list_items(s, "待审")
        assert [(i.id, i.occurrence_count) for i in items] == [(b.id, 2), (a.id, 1)]
        assert await review_queue.list_pending_questions(s) == [(a.id, "A？"), (b.id, "B？")]
        await review_queue.set_status(s, a.id, "通过", approved_answer="核准")
        await s.commit()
    async with db() as s:
        got = await review_queue.get(s, a.id)
        assert (got.review_status, got.approved_answer) == ("通过", "核准")
        assert [i.id for i in await review_queue.list_items(s, "待审")] == [b.id]
        assert len(await review_queue.list_items(s, None)) == 2


async def test_eval_runs_recent_ascending(db):
    async with db() as s:
        for i in range(3):
            await eval_runs.add(s, triggered_by="手动", dataset_size=300, metrics={"mrr": i / 10})
        await s.commit()
    async with db() as s:
        rows = await eval_runs.list_recent(s, 2)
    assert [r.metrics["mrr"] for r in rows] == [0.1, 0.2]
    assert rows[0].triggered_by == "手动"


async def test_chinese_enum_bytes(db):
    sql = ("SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS "
           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{t}' AND COLUMN_NAME = '{c}'")
    async with db() as s:
        review = (await s.execute(text(sql.format(t="review_queue", c="review_status")))).scalar_one()
        trigger = (await s.execute(text(sql.format(t="eval_runs", c="triggered_by")))).scalar_one()
    assert "E5BE85E5AEA1" in review   # “待审”
    assert "E6898BE58AA8" in trigger  # “手动”
