import json
from collections.abc import AsyncIterable, AsyncIterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from app.config import TOKEN_BUDGET
from app.context import BudgetExceeded, build_history
from app.db.engine import get_sessionmaker
from app.llm import get_chat_model
from app.locks import LockRegistry, get_lock_registry
from app.prompts import render_chat_system
from app.repositories import conversations, messages
from app.schemas import ChatRequest
from app.services import history
from app.services.chat import ChatTurn, stream_reply

router = APIRouter()


def get_token_budget() -> int:
    return TOKEN_BUDGET


def get_today() -> date:
    return date.today()


async def prepare_chat_turn(
    req: ChatRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    budget: Annotated[int, Depends(get_token_budget)],
    today: Annotated[date, Depends(get_today)],
) -> AsyncIterator[ChatTurn]:
    def trim_history(previous: list[BaseMessage]) -> list[BaseMessage]:
        try:
            return build_history(previous, render_chat_system(today), req.message, budget)
        except BudgetExceeded:
            raise HTTPException(422, detail={
                "code": "budget_exceeded",
                "message": "消息过长，请缩短后重试",
            })

    sm = get_sessionmaker()
    trimmed_history = None
    # 新会话先校验预算，避免拒绝请求时留下空会话。
    if req.session_id is None:
        trimmed_history = trim_history([])
    async with sm() as s:
        if req.session_id is not None:
            conversation = await conversations.get_for_user(
                s, int(req.session_id), req.user_id
            )
            if conversation is None:
                raise HTTPException(404, detail={
                    "code": "conversation_not_found",
                    "message": "会话不存在或已失效",
                })
        else:
            conversation = await conversations.create(s, req.user_id)
            await s.commit()
        conversation_id = conversation.id

    lock = locks.get(conversation_id)
    if lock.locked():
        raise HTTPException(409, detail={
            "code": "session_busy",
            "message": "该会话正在处理上一条消息，请稍后重试",
        })
    await lock.acquire()
    # 使用 yield 依赖在响应结束或后续依赖出错时释放锁。
    try:
        if trimmed_history is None:
            # 历史读取和预算检查均在锁内，异常退出也释放锁。
            async with sm() as s:
                rows = await messages.list_for_conversation(s, conversation_id)
                previous = history.to_langchain(rows)
            trimmed_history = trim_history(previous)
        yield ChatTurn(
            conversation_id=conversation_id,
            history=trimmed_history,
            user_input=req.message,
            today=today,
        )
    finally:
        lock.release()


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    turn: Annotated[ChatTurn, Depends(prepare_chat_turn)],
    model: Annotated[BaseChatModel, Depends(get_chat_model)],
) -> AsyncIterable[ServerSentEvent]:
    async for name, data in stream_reply(turn, model):
        yield ServerSentEvent(raw_data=json.dumps(data, ensure_ascii=False), event=name)
