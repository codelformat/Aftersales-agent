import logging

from app.context.layers import history_lines, split_layers
from app.db.engine import get_sessionmaker
from app.graph import events
from app.repositories import conversations
from app.services import understanding

logger = logging.getLogger(__name__)


async def start_turn(state, runtime):
    async with get_sessionmaker()() as s:
        anchors = await conversations.get_context(s, runtime.context.conversation_id)
    return {
        "summary": anchors.summary, "summary_upto": anchors.summary_upto, "layer1_from": anchors.layer1_from,
        "resolved_input": "", "standard_query": "", "product_category": None, "order_scoped": False,
        "order_id": None, "history_recall": False, "ticket_request": False, "order": None, "queries": [],
        "intent": None, "intent_confidence": None,
        "route": "", "evidence": [], "gate": None, "agent_messages": [], "steps": 0, "tokens_used": 0,
        "force_final": False, "reply": "", "actions": [], "trace": events.enter("start_turn", {}, runtime),
    }


async def resolve_reference(state, runtime):
    trace = events.enter("resolve_reference", state, runtime)
    cid = runtime.context.conversation_id
    layers = split_layers(state.get("messages", []), state.get("summary_upto"), state.get("layer1_from"))
    lines = history_lines(state.get("summary"), layers)
    history = "\n".join(lines)
    logger.info("history_ctx conversation=%s lines=%s summary=%s\n%s", cid, len(lines),
                state.get("summary") or "-", "\n".join(f"  {line}" for line in lines) or "  （无）")
    r = await understanding.resolve(state["user_input"], history)
    logger.info("resolved=%s order_scoped=%s order_id=%s history_recall=%s ticket_request=%s conversation=%s",
                r.resolved_input, r.order_scoped, r.order_id, r.history_recall, r.ticket_request, cid)
    return {"resolved_input": r.resolved_input, "standard_query": r.standard_query,
            "product_category": r.product_category, "order_scoped": r.order_scoped,
            "order_id": r.order_id, "history_recall": r.history_recall,
            "ticket_request": r.ticket_request, "trace": trace}
