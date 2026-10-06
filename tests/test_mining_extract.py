from datetime import date, datetime, time, timedelta, timezone
import json

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import Conversation, Message, QaExtractionStaging
from app.knowledge import mining
from app.knowledge.history_seed import seed_history
from app.repositories.staging import db_utc_offset, local_to_db, unmined_conversation_ids
from app.schemas import QaPair, QaPairs

pytestmark = pytest.mark.anyio
DAY = date(2026, 10, 5)


async def _conv(db, day: date, turns: list[tuple[str, str | None]]) -> int:
    async with db() as s:
        offset = await db_utc_offset(s)
        conv = Conversation(user_id="u1", created_at=local_to_db(datetime.combine(day, time(10)), offset))
        s.add(conv)
        await s.flush()
        for role, content in turns:
            s.add(Message(conversation_id=conv.id, role=role, content=content))
        await s.commit()
        return conv.id


def _extractor(answers: dict[str, list[tuple[str, str]] | Exception], seen: list | None = None):
    """按对话中的第一句用户话选择输出。"""
    async def run(inputs):
        first = inputs["transcript"].split("\n")[0]
        if seen is not None:
            seen.append(inputs["transcript"])
        result = answers[first]
        if isinstance(result, Exception):
            raise result
        return {"raw": None, "parsing_error": None,
                "parsed": QaPairs(pairs=[QaPair(question=q, answer=a) for q, a in result])}
    return RunnableLambda(run)


async def _staging(db):
    async with db() as s:
        return list((await s.execute(select(QaExtractionStaging).order_by(QaExtractionStaging.id))).scalars())


def test_format_transcript_excludes_tool_messages():
    rows = [
        Message(role="user", content="能开专票吗"),
        Message(role="assistant", content=None, tool_calls=[{"id": "c1", "name": "query_faq", "args": {}}]),
        Message(role="tool", content='{"ok": true}', tool_call_id="c1"),
        Message(role="assistant", content="可以开专票。"),
    ]
    assert mining.format_transcript(rows) == "用户：能开专票吗\n客服：可以开专票。"


def test_format_transcript_without_reply_is_none():
    assert mining.format_transcript([Message(role="user", content="在吗")]) is None


def test_batch_no_format():
    assert mining.batch_no(DAY, 3) == "20261005-03"


async def test_extract_day_writes_staging_by_batch(db):
    c1 = await _conv(db, DAY, [("user", "能开专票吗"), ("assistant", "可以开专票。")])
    c2 = await _conv(db, DAY, [("user", "你好"), ("assistant", "您好。")])
    c3 = await _conv(db, DAY, [("user", "能改地址吗"), ("assistant", "发货前可以改。")])
    await _conv(db, date(2026, 10, 4), [("user", "能开专票吗"), ("assistant", "可以。")])  # 其他日期
    ex = _extractor({
        "用户：能开专票吗": [("能开专票吗", "可以开专票。")],
        "用户：你好": [],
        "用户：能改地址吗": [("能改地址吗", "发货前可以改。")],
    })
    stats = await mining.extract_day(DAY, ex, batch_size=2)
    rows = await _staging(db)
    assert [(r.batch_no, r.source_ref, r.question, r.status) for r in rows] == [
        ("20261005-01", f"conversation:{c1}", "能开专票吗", "extracted"),
        ("20261005-02", f"conversation:{c3}", "能改地址吗", "extracted"),
    ]
    assert (stats.conversations, stats.pairs, stats.failed) == (3, 2, 0)


async def test_one_conversation_failure_does_not_block_others(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    await _conv(db, DAY, [("user", "乙"), ("assistant", "乙答。")])
    ex = _extractor({"用户：甲": RuntimeError("上游失败"), "用户：乙": [("乙", "乙答。")]})
    stats = await mining.extract_day(DAY, ex)
    assert [r.question for r in await _staging(db)] == ["乙"]
    assert stats.failed == 1


async def test_unparsed_output_counts_as_failure(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    ex = RunnableLambda(lambda _: {"raw": "x", "parsed": None, "parsing_error": "bad"})
    stats = await mining.extract_day(DAY, ex)
    assert stats.failed == 1 and await _staging(db) == []


async def test_extract_day_twice_skips_mined_and_continues_batch_numbers(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    seen: list = []
    ex = _extractor({"用户：甲": [("甲", "甲答。")], "用户：乙": [("乙", "乙答。")]}, seen)
    await mining.extract_day(DAY, ex)
    await _conv(db, DAY, [("user", "乙"), ("assistant", "乙答。")])
    await mining.extract_day(DAY, ex)
    rows = await _staging(db)
    assert [(r.batch_no, r.question) for r in rows] == [("20261005-01", "甲"), ("20261005-02", "乙")]
    assert len(seen) == 2  # 第二次只抽取新会话


async def test_blank_pairs_are_dropped(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    ex = _extractor({"用户：甲": [("  ", "答"), ("甲", " ")]})
    await mining.extract_day(DAY, ex)
    assert await _staging(db) == []


async def test_seed_history_is_idempotent(db, tmp_path):
    f = tmp_path / "h.jsonl"
    f.write_text("\n".join(json.dumps({"messages": [
        {"role": "user", "content": f"问题{i}"}, {"role": "assistant", "content": f"回答{i}"}
    ]}, ensure_ascii=False) for i in range(3)), encoding="utf-8")
    assert await seed_history(DAY, f) == 3
    assert await seed_history(DAY, f) == 0
    async with db() as s:
        convs = list((await s.execute(select(Conversation).where(Conversation.user_id == "history-seed").order_by(Conversation.id))).scalars())
        offset = await db_utc_offset(s)
        messages = list(await s.scalars(select(Message).order_by(Message.id)))
    assert len(convs) == 3
    assert all((c.created_at - offset).replace(tzinfo=timezone.utc).astimezone().date() == DAY for c in convs)
    for i, conv in enumerate(convs):
        expected = datetime.combine(DAY, time(9)) + timedelta(minutes=10 * i)
        assert (conv.created_at - offset).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None) == expected
        assert conv.updated_at == conv.created_at
        assert [m.created_at for m in messages if m.conversation_id == conv.id] == [
            conv.created_at, conv.created_at + timedelta(seconds=1),
        ]


def test_local_to_db_converts_local_to_utc_plus_offset():
    local = datetime(2026, 10, 5, 4, 0)
    utc = local.astimezone(timezone.utc).replace(tzinfo=None)
    assert local_to_db(local, timedelta(0)) == utc
    assert local_to_db(local, timedelta(hours=1)) == utc + timedelta(hours=1)


async def test_local_day_window_includes_early_morning(db):
    async with db() as s:
        offset = await db_utc_offset(s)
        convs = [Conversation(user_id="window", created_at=local_to_db(at, offset)) for at in (
            datetime.combine(DAY, time(4)),
            datetime.combine(DAY - timedelta(days=1), time(23)),
            datetime.combine(DAY, time(23, 30)),
        )]
        s.add_all(convs)
        await s.commit()
        assert await unmined_conversation_ids(s, DAY) == [convs[0].id, convs[2].id]
