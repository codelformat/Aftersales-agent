import asyncio
import logging

import pytest
from sqlalchemy import select

from app.context.summarizer import (SummaryRejected, SummaryRunner, batch_text, check_summary, get_runner,
                                    projection, run_summary)
from app.db.models import Conversation, ConversationSummary
from app.repositories import conversations
from tests.test_layers import turn

pytestmark = pytest.mark.anyio


async def new_cid(db):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.commit()
    return cid


def test_batch_text():
    text = batch_text(turn(1, 2, user="订单 1001 没到", reply="已发货", tool="x" * 500))
    assert text == ("用户：订单 1001 没到\n调用 query_order(order_id=1001)\n工具结果：" + "x" * 200 + "\n客服：已发货")


def test_check_summary():
    check_summary("用户订单 1001 要换货", "用户：订单 1001 坏了")
    for bad, code in (("", "empty"), ("字" * 301, "too_long"), ("订单 9999 要换货", "unsupported_number")):
        with pytest.raises(SummaryRejected) as e:
            check_summary(bad, "用户：订单 1001 坏了")
        assert e.value.code == code


def test_projection_keeps_newest_within_reserve():
    assert projection(["甲", "乙"]) == "第1段：甲\n第2段：乙"
    long = ["字" * 600, "字" * 600, "新"]
    out = projection(long)
    assert out.endswith("第3段：新") and "第1段" not in out


async def test_run_summary_appends_segment_and_moves_anchor(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    calls = use_summarizer("用户报订单 1001，左耳没声音，想换货。")
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2, user="订单 1001 左耳没声音，想换货"), 1, 2)
    async with db() as s:
        seg = (await s.scalars(select(ConversationSummary))).one()
        conv = await s.get(Conversation, cid)
    assert (seg.seq, seg.from_msg_id, seg.upto_msg_id) == (1, 1, 2)
    assert conv.summary_upto_msg_id == 2 and conv.summary == "第1段：用户报订单 1001，左耳没声音，想换货。"
    assert calls[0]["previous"] == "（无）"
    assert "summary start conversation=%s range=1..2 msgs=2" % cid in caplog.text
    assert "summary done conversation=%s 第1段 range=1..2" % cid in caplog.text


async def test_previous_segments_are_background_only(db, use_summarizer):
    calls = use_summarizer("第一批", "第二批 1002")
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2), 1, 2)
    await run_summary(cid, turn(3, 4, user="订单 1002"), 3, 4)
    async with db() as s:
        segs = (await s.scalars(select(ConversationSummary).order_by(ConversationSummary.seq))).all()
        conv = await s.get(Conversation, cid)
    assert [s.content for s in segs] == ["第一批", "第二批 1002"]   # 旧段不变
    assert calls[1]["previous"] == "第1段：第一批"
    assert conv.summary == "第1段：第一批\n第2段：第二批 1002"


@pytest.mark.parametrize("value,code", [
    ("订单 8888", "unsupported_number"), (TimeoutError(), "TimeoutError"),
    ("  ", "empty"), ("字" * 301, "too_long"), (RuntimeError("上游失败"), "RuntimeError"),
])
async def test_run_summary_failure_keeps_anchor(db, use_summarizer, caplog, value, code):
    caplog.set_level(logging.INFO)
    use_summarizer(value)
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2), 1, 2)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id is None
        assert (await s.scalars(select(ConversationSummary))).all() == []
    assert "summary fail conversation=%s range=1..2" % cid in caplog.text and code in caplog.text


async def test_summary_does_not_move_anchor_backwards(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    use_summarizer("后一批", "前一批")
    cid = await new_cid(db)
    await run_summary(cid, turn(3, 4), 3, 4)
    await run_summary(cid, turn(1, 2), 1, 2)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id == 4
        assert len((await s.scalars(select(ConversationSummary))).all()) == 1
    assert "stale_range" in caplog.text


async def test_runner_skips_when_running(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    gate = asyncio.Event()
    use_summarizer(gate, "完成")
    cid = await new_cid(db)
    runner = get_runner()
    assert runner.start(cid, turn(1, 2), 1, 2, tokens=2000, budget=1695) is True
    assert runner.start(cid, turn(1, 2), 1, 2, tokens=2000, budget=1695) is False
    assert "summary trigger conversation=%s 层2 约 2000 token > 预算 1695 range=1..2" % cid in caplog.text
    assert "summary skip conversation=%s reason=running" % cid in caplog.text
    gate.set()
    await runner.drain()
    assert runner.running(cid) is False


async def test_cancel_all_logs(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    use_summarizer(asyncio.Event())
    cid = await new_cid(db)
    runner = get_runner()
    runner.start(cid, turn(1, 2), 1, 2, tokens=1, budget=0)
    await asyncio.sleep(0)
    await runner.cancel_all()
    assert "summary cancel conversation=%s" % cid in caplog.text
    assert not runner.running(cid)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id is None
        assert (await s.scalars(select(ConversationSummary))).all() == []


async def test_summary_actual_timeout_keeps_anchor(db, use_summarizer, monkeypatch, caplog):
    from app.context import summarizer

    monkeypatch.setattr(summarizer, "SUMMARY_TIMEOUT_SECONDS", 0.01)
    use_summarizer(asyncio.Event())
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2), 1, 2)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id is None
        assert await summarizer.summaries.list_for_conversation(s, cid) == []
    assert "TimeoutError" in caplog.text


async def test_summary_write_failure_rolls_back_segment(db, use_summarizer, monkeypatch, caplog):
    use_summarizer("完成")
    cid = await new_cid(db)

    async def fail_update(*args):
        raise RuntimeError("写库失败")

    monkeypatch.setattr(conversations, "set_summary", fail_update)
    await run_summary(cid, turn(1, 2), 1, 2)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id is None
        assert (await s.scalars(select(ConversationSummary))).all() == []
    assert "summary fail" in caplog.text and "RuntimeError" in caplog.text


async def test_previous_numbers_are_valid_background(db, use_summarizer):
    use_summarizer("用户报订单 1001。", "用户要换订单 1001 的商品。")
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2, user="订单 1001"), 1, 2)
    await run_summary(cid, turn(3, 4, user="那个订单我要换货"), 3, 4)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id == 4


async def test_summarizer_factory_returns_cached_plain_text(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from app import llm

    built, inputs = [], []

    def build(settings):
        built.append(settings)

        async def respond(prompt):
            inputs.append(prompt.to_messages())
            return AIMessage("用户报订单 1001。")

        return RunnableLambda(respond)

    monkeypatch.setattr(llm, "build_extract_model", build)
    llm.get_summarizer.cache_clear()
    try:
        runnable = llm.get_summarizer()
        assert llm.get_summarizer() is runnable
        assert await runnable.ainvoke({"previous": "旧事实", "dialog": "订单 1001"}) == "用户报订单 1001。"
        assert len(built) == 1
        assert inputs[0][0].type == "system"
        assert inputs[0][1].content == "已有梗概（只作背景）：\n旧事实\n\n本批对话：\n订单 1001"
    finally:
        llm.get_summarizer.cache_clear()
