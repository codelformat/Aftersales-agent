"""用户点击「建工单」后调用。后端不自动建单。"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage

from app.db.engine import get_sessionmaker
from app.graph.builder import get_graph, thread_config
from app.locks import LockRegistry, get_lock_registry
from app.prompts import TICKET_CREATED_NOTE
from app.repositories import conversations, messages
from app.repositories.messages import NewMessage
from app.schemas import TicketRequest
from app.tools.executor import execute_tool_calls

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/tickets")
async def create_ticket(
    req: TicketRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    graph: Annotated[object, Depends(get_graph)],
) -> dict:
    cid = int(req.session_id)
    async with get_sessionmaker()() as s:
        if await conversations.get_for_user(s, cid, req.user_id) is None:
            raise HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})
    lock = locks.get(cid)
    if lock.locked():
        raise HTTPException(409, detail={"code": "session_busy", "message": "该会话正在处理上一条消息，请稍后重试"})
    async with lock:
        call = {"id": f"ticket-{uuid.uuid4().hex[:8]}", "name": "create_ticket",
                "args": {"description": req.description, "ticket_type": req.ticket_type}}
        outcome = (await execute_tool_calls([call], conversation_id=cid))[0]
        if not outcome.ok:
            raise HTTPException(502, detail={"code": "ticket_failed", "message": "工单创建失败，请稍后重试"})
        ticket = outcome.data
        note = TICKET_CREATED_NOTE.format(ticket_no=ticket["ticket_no"], ticket_type=req.ticket_type)
        # 工单已提交，不回滚。下面两步失败只记日志。
        try:
            async with get_sessionmaker()() as s:
                await messages.add_turn(s, cid, [NewMessage(role="assistant", content=note)])
                await s.commit()
        except Exception:
            logger.exception("工单提示写入 messages 表失败")
        try:
            await graph.aupdate_state(thread_config(cid), {"messages": [AIMessage(content=note)]}, as_node="finalize")
        except Exception:
            logger.exception("工单提示写入 State 失败")
    return {"ticket_no": ticket["ticket_no"], "status": ticket["status"]}
