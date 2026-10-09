import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import Message
from app.graph.builder import get_graph, thread_config
from app.repositories import conversations, low_confidence
from app.schemas import UserId
from app.services.grounding import record_low_confidence
from app.services.history import msg_id

logger = logging.getLogger(__name__)
router = APIRouter()
FEEDBACK_SOURCE = "user_feedback"
FEEDBACK_REASON = "用户反馈未解决（回复 msg-{message_id}）"
NOT_FOUND = {"code": "message_not_found", "message": "消息不存在"}
NOT_REPLY = {"code": "not_a_reply", "message": "只能对客服回复反馈"}


class FeedbackRequest(BaseModel):
    conversation_id: int
    message_id: int
    rating: Literal["up", "down"]
    user_id: UserId


async def find_snapshot(graph, conversation_id: int, message_id: int) -> list[dict] | None:
    """回捞该回复所在轮的召回快照：取最后一条消息为该回复的最早 checkpoint。"""
    target, found = msg_id(message_id), None
    async for snap in graph.aget_state_history(thread_config(conversation_id)):
        msgs = snap.values.get("messages") or []
        if msgs and msgs[-1].id == target:
            found = snap.values.get("retrieval")
    return found


@router.post("/api/feedback")
async def feedback(req: FeedbackRequest, graph: Annotated[object, Depends(get_graph)]):
    async with get_sessionmaker()() as s:
        if await conversations.get_for_user(s, req.conversation_id, req.user_id) is None:
            raise HTTPException(404, detail=NOT_FOUND)
        reply = await s.get(Message, req.message_id)
        if reply is None or reply.conversation_id != req.conversation_id:
            raise HTTPException(404, detail=NOT_FOUND)
        if reply.role != "assistant":
            raise HTTPException(422, detail=NOT_REPLY)
        if req.rating == "up":
            return Response(status_code=204)
        reason = FEEDBACK_REASON.format(message_id=req.message_id)
        existing = await low_confidence.find_by_reason(
            s, conversation_id=req.conversation_id, source=FEEDBACK_SOURCE, reason=reason)
        if existing is not None:
            return {"id": existing.id, "duplicate": True}
        question = (await s.execute(
            select(Message.content).where(Message.conversation_id == req.conversation_id,
                                          Message.role == "user", Message.id < req.message_id)
            .order_by(Message.id.desc()).limit(1))).scalar_one_or_none()
    if not question:
        raise HTTPException(422, detail=NOT_REPLY)
    try:
        snap = await find_snapshot(graph, req.conversation_id, req.message_id)
    except Exception:
        # 回捞失败不影响落池。
        logger.exception("feedback_snapshot_failed conversation=%s message=%s", req.conversation_id, req.message_id)
        snap = None
    lcq_id = await record_low_confidence(
        req.conversation_id, question, reason, source=FEEDBACK_SOURCE, retrieved_chunks=snap)
    if lcq_id is None:
        raise HTTPException(500, detail={"code": "feedback_failed", "message": "反馈保存失败，请稍后重试"})
    logger.info("feedback down conversation=%s message=%s lcq=%s snapshot=%s", req.conversation_id,
                req.message_id, lcq_id, "-" if snap is None else len(snap))
    return JSONResponse(status_code=201, content={"id": lcq_id, "duplicate": False})
