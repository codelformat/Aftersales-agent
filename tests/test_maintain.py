import logging

import pytest
from sqlalchemy import update

from app.context import count_tokens
from app.context.summarizer import get_runner
from app.db.models import Conversation
from app.repositories import conversations
from tests.test_layers import turn

pytestmark = pytest.mark.anyio


async def new_cid(db, **cols):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        if cols:
            await s.execute(update(Conversation).where(Conversation.id == cid).values(**cols))
        await s.commit()
    return cid


def history(n, size=40):
    msgs = []
    for i in range(n):
        msgs += turn(2 * i + 1, 2 * i + 2, user="问" * size, reply="答" * size)
    return msgs


async def anchors(db, cid):
    async with db() as s:
        return await conversations.get_context(s, cid)


async def test_under_budget_does_nothing(db, use_budget, caplog):
    from app.context.maintain import maintain

    caplog.set_level(logging.INFO)
    use_budget(layer1=100000, layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, history(20))
    a = await anchors(db, cid)
    assert (a.layer1_from, a.summary_upto) == (None, None)
    assert "层1 降级" not in caplog.text and "summary trigger" not in caplog.text
    assert f"context_usage conversation={cid}" in caplog.text


async def test_layer1_over_budget_degrades_a_batch(db, use_budget, caplog):
    from app.context.maintain import maintain

    caplog.set_level(logging.INFO)
    msgs = history(10)
    use_budget(layer1=count_tokens(msgs[:2]) * 5, layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, msgs)
    # 预算为 5 轮，水位为 0.6，保留最后 3 轮。
    assert (await anchors(db, cid)).layer1_from == 14
    assert f"层1 降级 conversation={cid} -→14" in caplog.text


async def test_degrade_all_when_single_turn_exceeds(db, use_budget):
    from app.context.maintain import maintain

    use_budget(layer1=10, layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, turn(1, 2, tool="x" * 2000))
    assert (await anchors(db, cid)).layer1_from == 2


async def test_layer2_over_budget_triggers_summary(db, use_budget, use_summarizer, caplog):
    from app.context.maintain import maintain

    caplog.set_level(logging.INFO)
    use_summarizer("梗概")
    use_budget(layer1=100000, layer2=10)
    cid = await new_cid(db, layer1_from_msg_id=4)
    await maintain(cid, history(4))
    assert f"summary trigger conversation={cid}" in caplog.text and "range=1..4" in caplog.text
    await get_runner().drain()
    a = await anchors(db, cid)
    assert a.summary_upto == 4 and a.summary == "第1段：梗概"


async def test_maintain_reads_fresh_anchors(db, use_budget, caplog):
    from app.context.maintain import maintain

    caplog.set_level(logging.INFO)
    use_budget(layer1=100000, layer2=10)
    cid = await new_cid(db, layer1_from_msg_id=4, summary_upto_msg_id=4)
    await maintain(cid, history(4))
    assert "summary trigger" not in caplog.text
    assert f"context_usage conversation={cid} layer1=" in caplog.text
    assert "layer2=0/10" in caplog.text


async def test_exact_layer1_budget_does_not_degrade(db, use_budget):
    from app.context.maintain import maintain

    msgs = history(2)
    use_budget(layer1=count_tokens(msgs), layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, msgs)
    assert (await anchors(db, cid)).layer1_from is None


async def test_degrade_keeps_whole_tool_turns(db, use_budget):
    from app.context.maintain import maintain
    from app.context.layers import split_layers

    msgs = [*turn(1, 2, tool="x" * 2000), *turn(3, 4, tool="{}")]
    use_budget(layer1=500, layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, msgs)
    a = await anchors(db, cid)
    assert a.layer1_from == 2
    assert split_layers(msgs, a.summary_upto, a.layer1_from).layer1 == msgs[4:]


async def test_layer2_budget_uses_rendered_messages(db, use_budget, caplog):
    from app.context.maintain import maintain

    caplog.set_level(logging.INFO)
    use_budget(layer1=100000, layer2=100)
    cid = await new_cid(db, layer1_from_msg_id=2)
    await maintain(cid, turn(1, 2, reply="答" * 2000))
    assert "summary trigger" not in caplog.text
    assert (await anchors(db, cid)).summary_upto is None
