"""日志记录节点：写结构化日志和 messages 表，再把本轮并入 State 历史。"""

import logging

from langchain_core.messages import AIMessage, HumanMessage

from app.db.engine import get_sessionmaker
from app.graph import events
from app.repositories import messages
from app.services.history import turn_messages_rows

logger = logging.getLogger("app.graph")


async def finalize(state, runtime):
    trace = events.enter("finalize", state, runtime)
    cid = runtime.context.conversation_id
    turn = state.get("agent_messages") or [AIMessage(content=state["reply"])]
    async with get_sessionmaker()() as s:
        await messages.add_turn(s, cid, turn_messages_rows(state["user_input"], turn))
        await s.commit()
    gate = state.get("gate") or {}
    logger.info(
        "turn conversation=%s intent=%s confidence=%s route=%s resolved=%s order=%s queries=%s trace=%s gate=%s "
        "steps=%s tokens=%s actions=%s",
        cid, state.get("intent"), state.get("intent_confidence"), state.get("route"), state.get("resolved_input"),
        state.get("order_id") or "-", len(state.get("queries") or []), ",".join(trace),
        f"{gate.get('passed')}/{gate.get('top_score')}/{gate.get('source')}" if gate else "-",
        state.get("steps", 0), state.get("tokens_used", 0),
        ",".join(a["type"] for a in state.get("actions", [])) or "-",
    )
    return {"messages": [HumanMessage(state["user_input"]), *turn], "trace": trace}
