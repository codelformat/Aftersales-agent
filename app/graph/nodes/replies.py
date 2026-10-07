"""固定话术出口。不调用回答模型。"""

from app.graph import events
from app.prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, GATE_FALLBACK_REPLY

TICKET_DESCRIPTION_MAX_CHARS = 500


async def chitchat_reply(state, runtime):
    trace = events.enter("chitchat_reply", state, runtime)
    events.emit("token", {"text": CHITCHAT_REPLY})
    return {"reply": CHITCHAT_REPLY, "trace": trace}


async def complaint_reply(state, runtime):
    trace = events.enter("complaint_reply", state, runtime)
    actions = [
        {"type": "handoff"},
        {"type": "ticket", "description": state["user_input"][:TICKET_DESCRIPTION_MAX_CHARS],
         "ticket_type": "投诉"},
    ]
    events.emit("token", {"text": COMPLAINT_REPLY})
    events.emit("actions", {"options": actions})
    return {"reply": COMPLAINT_REPLY, "actions": actions, "trace": trace}


async def fallback_reply(state, runtime):
    trace = events.enter("fallback_reply", state, runtime)
    events.emit("token", {"text": GATE_FALLBACK_REPLY})
    return {"reply": GATE_FALLBACK_REPLY, "trace": trace}
