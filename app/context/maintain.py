"""每轮结束后维护层的边界：层 1 超预算降级，层 2 超预算起后台摘要。"""

import logging
from collections.abc import Sequence

from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import trim_messages

from app.config import LAYER1_LOW_WATER
from app.context import count_tokens
from app.context.budget import get_budget
from app.context.layers import render_layer2, split_layers
from app.context.summarizer import get_runner
from app.db.engine import get_sessionmaker
from app.repositories import conversations

logger = logging.getLogger(__name__)


def _tokens(messages: Sequence[BaseMessage]) -> int:
    return count_tokens(messages) if messages else 0


async def maintain(cid: int, messages: Sequence[BaseMessage]) -> bool:
    """维护上下文；返回本次是否启动了新的后台摘要。"""
    budget = get_budget()
    sm = get_sessionmaker()
    # 后台摘要可在本轮进行中推进锚点，所以重新读数据库。
    async with sm() as s:
        anchors = await conversations.get_context(s, cid)
    layer1_from = anchors.layer1_from
    layers = split_layers(messages, anchors.summary_upto, layer1_from)
    l1 = _tokens(layers.layer1)
    if l1 > budget.layer1:
        kept = trim_messages(
            layers.layer1, max_tokens=int(budget.layer1 * LAYER1_LOW_WATER),
            strategy="last", start_on="human", token_counter=count_tokens,
        )
        dropped = len(layers.layer1) - len(kept)
        new_from = max(layers.ids1[:dropped]) if dropped else None
        if new_from is not None:
            async with sm() as s:
                await conversations.advance_layer1(s, cid, new_from)
                await s.commit()
            logger.info(
                "层1 降级 conversation=%s %s→%s 层1 约 %s token > 预算 %s",
                cid, layer1_from if layer1_from is not None else "-", new_from, l1, budget.layer1,
            )
            layer1_from = new_from
            layers = split_layers(messages, anchors.summary_upto, layer1_from)
    l2 = _tokens(render_layer2(layers.layer2))
    logger.info("context_usage conversation=%s layer1=%s/%s layer2=%s/%s",
                cid, _tokens(layers.layer1), budget.layer1, l2, budget.layer2)
    if layers.layer2 and l2 > budget.layer2:
        return get_runner().start(cid, layers.layer2, layers.ids2[0], layer1_from, l2, budget.layer2)
    return False
