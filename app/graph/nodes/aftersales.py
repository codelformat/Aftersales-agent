"""退款退货和售后的确定性子流程：拿订单 → 扩写 → 多查询检索。"""

import logging
import uuid

from langgraph.types import interrupt

from app.graph import events
from app.graph.nodes.knowledge import evidence_update
from app.knowledge.retrieval import retrieve_multi
from app.schemas import QueryPlan
from app.services import understanding
from app.tools import mock_data

logger = logging.getLogger(__name__)


async def ensure_order(state, runtime):
    trace = events.enter("ensure_order", state, runtime)
    if state.get("order_id"):
        return {"trace": trace}
    ctx = runtime.context
    # 恢复时本节点从头重新执行。interrupt 之前只做确定性的读操作。
    cards = mock_data.user_orders(ctx.user_id, ctx.today)
    logger.info("interrupt=order_picker conversation=%s", ctx.conversation_id)
    chosen = interrupt({"type": "order_picker", "orders": cards})
    if chosen not in {c["order_id"] for c in cards}:
        raise ValueError(f"选择的订单不在列表中：{chosen!r}")
    return {"order_id": chosen, "trace": trace}


async def fetch_order(state, runtime):
    trace = events.enter("fetch_order", state, runtime)
    ctx = runtime.context
    call = {"id": f"fetch-{uuid.uuid4().hex[:8]}", "name": "query_order", "args": {"order_id": state["order_id"]}}
    outcome = (await ctx.execute([call], conversation_id=ctx.conversation_id))[0]
    if not outcome.ok:
        logger.warning("订单查询失败 order_id=%s conversation=%s", state["order_id"], ctx.conversation_id)
        return {"order": None, "trace": trace}
    return {"order": outcome.data, "trace": trace}


async def expand_query(state, runtime):
    trace = events.enter("expand_query", state, runtime)
    names = [item["name"] for item in (state.get("order") or {}).get("items", [])]
    queries = await understanding.expand(state["standard_query"], names)
    return {"queries": queries, "trace": trace}


async def retrieve_multi_evidence(state, runtime):
    trace = events.enter("retrieve_multi", state, runtime)
    plan = QueryPlan(standard_query=state["standard_query"], product_category=state.get("product_category"))
    result = await retrieve_multi(state["queries"], plan)
    return evidence_update(state["resolved_input"], result, trace)
