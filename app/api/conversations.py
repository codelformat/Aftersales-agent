"""会话侧栏的两个只读接口。"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.db.engine import get_sessionmaker
from app.repositories import conversations, messages
from app.schemas import UserId

router = APIRouter(prefix="/api/conversations")
PREVIEW_CHARS = 30


@router.get("")
async def list_conversations(user_id: Annotated[UserId, Query()]) -> list[dict]:
    async with get_sessionmaker()() as s:
        rows = await conversations.list_for_user(s, user_id)
    return [
        {"session_id": str(c.id), "created_at": c.created_at.isoformat(),
         "updated_at": c.updated_at.isoformat(), "preview": (p or "")[:PREVIEW_CHARS],
         "summarized": c.summary_upto_msg_id is not None}
        for c, p in rows
    ]


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: int, user_id: Annotated[UserId, Query()]) -> list[dict]:
    async with get_sessionmaker()() as s:
        if await conversations.get_for_user(s, conversation_id, user_id) is None:
            raise HTTPException(
                404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"}
            )
        rows = await messages.list_for_conversation(s, conversation_id)
    return [
        {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
        for m in rows if m.role in ("user", "assistant") and m.content
    ]
