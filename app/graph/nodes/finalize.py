"""日志记录节点：写结构化日志和 messages 表，再把本轮并入 State 历史。"""

import logging

from langchain_core.messages import AIMessage, HumanMessage

from app.context.maintain import maintain
from app.db.engine import get_sessionmaker
from app.graph import events
from app.repositories import messages
from app.services.history import final_rows, msg_id

logger = logging.getLogger("app.graph")


async def finalize(state, runtime):
    trace = events.enter("finalize", state, runtime)
    cid = runtime.context.conversation_id
    turn = state.get("agent_messages") or [AIMessage(content=state["reply"])]
    final = turn[-1]
    async with get_sessionmaker()() as s:
        user_row, reply_row = await messages.add_turn(s, cid, final_rows(state["user_input"], final.content))
        await s.commit()
    new = [HumanMessage(state["user_input"], id=msg_id(user_row.id)), *turn[:-1],
           final.model_copy(update={"id": msg_id(reply_row.id)})]
    gate = state.get("gate") or {}
    logger.info(
        "turn conversation=%s intent=%s confidence=%s route=%s resolved=%s order=%s queries=%s trace=%s gate=%s "
        "steps=%s tokens=%s actions=%s ticket_request=%s status_query=%s write=%s",
        cid, state.get("intent"), state.get("intent_confidence"), state.get("route"), state.get("resolved_input"),
        state.get("order_id") or "-", len(state.get("queries") or []), ",".join(trace),
        f"{gate.get('passed')}/{gate.get('top_score')}/{gate.get('source')}" if gate else "-",
        state.get("steps", 0), state.get("tokens_used", 0),
        ",".join(a["type"] for a in state.get("actions", [])) or "-",
        state.get("ticket_request", False), state.get("status_query", False),
        state.get("write_decision") or "-",
    )
    try:
        await maintain(cid, [*state.get("messages", []), *new])
    except Exception:
        logger.exception("context_maintain_failed conversation=%s", cid)
    return {"messages": new, "trace": trace}
