"""用户在退款单中选择原因并提交后调用。本章为 mock，不落库。"""

import logging
import secrets
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage

from app.api.chat import get_today
from app.db.engine import get_sessionmaker
from app.graph.builder import get_graph, thread_config
from app.locks import LockRegistry, get_lock_registry
from app.prompts import REFUND_CREATED_NOTE
from app.repositories import conversations, messages
from app.repositories.messages import NewMessage
from app.schemas import RefundRequest
from app.services.history import msg_id

logger = logging.getLogger(__name__)
router = APIRouter()


def new_refund_no(today: date) -> str:
    return f"R{today:%Y%m%d}{secrets.randbelow(10000):04d}"


@router.post("/refunds")
async def create_refund(
    req: RefundRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    graph: Annotated[object, Depends(get_graph)],
    today: Annotated[date, Depends(get_today)],
) -> dict:
    cid = int(req.session_id)
    async with get_sessionmaker()() as s:
        if await conversations.get_for_user(s, cid, req.user_id) is None:
            raise HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})
    lock = locks.get(cid)
    if lock.locked():
        raise HTTPException(409, detail={"code": "session_busy", "message": "该会话正在处理上一条消息，请稍后重试"})
    async with lock:
        refund_no = new_refund_no(today)
        logger.info("refund_created refund_no=%s order=%s reason=%s conversation=%s",
                    refund_no, req.order_id, req.reason, cid)
        note = REFUND_CREATED_NOTE.format(refund_no=refund_no, order_id=req.order_id, reason=req.reason)
        # 退款单已提交，不回滚。下面两步失败只记日志。
        note_id = None
        try:
            async with get_sessionmaker()() as s:
                (row,) = await messages.add_turn(s, cid, [NewMessage(role="assistant", content=note)])
                await s.commit()
                note_id = msg_id(row.id)
        except Exception:
            logger.exception("退款提示写入 messages 表失败")
        try:
            await graph.aupdate_state(thread_config(cid), {"messages": [AIMessage(content=note, id=note_id)]},
                                      as_node="finalize")
        except Exception:
            logger.exception("退款提示写入 State 失败")
    return {"refund_no": refund_no, "status": "待审核"}
