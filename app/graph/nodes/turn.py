import logging

from app.graph import events
from app.services import understanding

logger = logging.getLogger(__name__)


async def start_turn(state, runtime):
    return {
        "resolved_input": "", "standard_query": "", "product_category": None, "order_scoped": False,
        "order_id": None, "order": None, "queries": [], "intent": None, "intent_confidence": None,
        "route": "", "evidence": [], "gate": None, "agent_messages": [], "steps": 0, "tokens_used": 0,
        "force_final": False, "reply": "", "actions": [], "trace": events.enter("start_turn", {}, runtime),
    }


async def resolve_reference(state, runtime):
    trace = events.enter("resolve_reference", state, runtime)
    history = understanding.history_text(state.get("messages", []))
    r = await understanding.resolve(state["user_input"], history)
    logger.info("resolved=%s order_scoped=%s order_id=%s conversation=%s",
                r.resolved_input, r.order_scoped, r.order_id, runtime.context.conversation_id)
    return {"resolved_input": r.resolved_input, "standard_query": r.standard_query,
            "product_category": r.product_category, "order_scoped": r.order_scoped,
            "order_id": r.order_id, "trace": trace}
