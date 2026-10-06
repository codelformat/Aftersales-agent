import json
from collections.abc import AsyncIterable, AsyncIterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.runnables import Runnable

from app.config import TOKEN_BUDGET
from app.context import BudgetExceeded, build_history
from app.llm import get_chat_model
from app.prompts import render_chat_system
from app.schemas import ChatRequest
from app.services.chat import ChatTurn, stream_reply
from app.session import SessionStore, get_session_store

router = APIRouter()


def get_token_budget() -> int:
    return TOKEN_BUDGET


def get_today() -> date:
    return date.today()


async def prepare_chat_turn(
    req: ChatRequest,
    store: Annotated[SessionStore, Depends(get_session_store)],
    budget: Annotated[int, Depends(get_token_budget)],
    today: Annotated[date, Depends(get_today)],
) -> AsyncIterator[ChatTurn]:
    # 检查会话状态和消息预算，获取锁并准备对话轮次。
    session = store.get_or_create(req.session_id)
    if session.lock.locked():
        raise HTTPException(409, detail={
            "code": "session_busy",
            "message": "该会话正在处理上一条消息，请稍后重试",
        })
    try:
        history = build_history(
            session.messages, render_chat_system(today), req.message, budget
        )
    except BudgetExceeded:
        raise HTTPException(422, detail={
            "code": "budget_exceeded",
            "message": "消息过长，请缩短后重试",
        })
    await session.lock.acquire()
    # 使用 yield 依赖在响应结束或后续依赖出错时释放锁。
    try:
        yield ChatTurn(session=session, history=history, user_input=req.message, today=today)
    finally:
        session.lock.release()


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    turn: Annotated[ChatTurn, Depends(prepare_chat_turn)],
    model: Annotated[Runnable, Depends(get_chat_model)],
) -> AsyncIterable[ServerSentEvent]:
    async for name, data in stream_reply(turn, model):
        yield ServerSentEvent(raw_data=json.dumps(data, ensure_ascii=False), event=name)
