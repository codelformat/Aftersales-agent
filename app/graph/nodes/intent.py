import logging

from app.graph import events
from app.graph.routing import route_for
from app.services import understanding

logger = logging.getLogger(__name__)


async def classify_intent(state, runtime):
    trace = events.enter("classify_intent", state, runtime)
    decision = await understanding.classify(state["resolved_input"])
    route = route_for(decision.intent)
    logger.info("intent=%s confidence=%s intent_model=%s route=%s conversation=%s", decision.intent,
                decision.confidence, decision.model, route, runtime.context.conversation_id)
    events.emit("understood", {"resolved_input": state["resolved_input"], "intent": decision.intent})
    return {"intent": decision.intent, "intent_confidence": decision.confidence, "route": route, "trace": trace}
