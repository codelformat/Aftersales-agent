"""确认工单写入，并生成固定回复。"""

import logging

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.graph import events
from app.graph.nodes.agent import TICKET_TOOL
from app.prompts import TICKET_CANCELLED_REPLY, TICKET_CREATED_NOTE, TICKET_FAILED_REPLY, TICKET_TIMEOUT_REPLY
from app.tools.executor import APPROVED
from app.tools.registry import builtin_registry
from app.tools.validation import validate_args

logger = logging.getLogger(__name__)


async def confirm_write(state, runtime):
    trace = events.enter("confirm_write", state, runtime)
    # 恢复时节点从头执行。interrupt 前只算参数，不发事件、不写库、不调上游。
    calls = [c for c in state["agent_messages"][-1].tool_calls if c["name"] == TICKET_TOOL]
    first, extras = calls[0], calls[1:]
    approvals = {c["id"]: "一次只能提交一张工单" for c in extras}
    if validate_args(builtin_registry()[TICKET_TOOL].parameters, first["args"]):
        return {"approvals": approvals, "write_decision": None, "trace": trace}
    logger.info("interrupt=ticket_confirm conversation=%s call=%s", runtime.context.conversation_id, first["id"])
    answer = interrupt({"type": "ticket_confirm", "call_id": first["id"],
                        "ticket_type": first["args"]["ticket_type"], "description": first["args"]["description"]})
    confirmed = isinstance(answer, dict) and answer.get("confirmed") is True
    approvals[first["id"]] = APPROVED if confirmed else "用户取消"
    return {"approvals": approvals, "write_decision": "confirmed" if confirmed else "cancelled", "trace": trace}


async def ticket_reply(state, runtime):
    trace = events.enter("ticket_reply", state, runtime)
    outcome = state.get("write_outcome") or {}
    if state.get("write_decision") == "cancelled":
        reply = TICKET_CANCELLED_REPLY
    elif outcome.get("status") == "成功":
        reply = TICKET_CREATED_NOTE.format(ticket_no=outcome["ticket_no"], ticket_type=outcome["ticket_type"])
    elif outcome.get("status") == "超时":
        reply = TICKET_TIMEOUT_REPLY
    else:
        reply = TICKET_FAILED_REPLY
    events.emit("token", {"text": reply})
    return {"reply": reply, "agent_messages": [*state["agent_messages"], AIMessage(content=reply)], "trace": trace}
