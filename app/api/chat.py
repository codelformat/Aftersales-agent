import json
import logging
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import TOKEN_BUDGET
from app.context import BudgetExceeded, build_history
from app.db.engine import get_sessionmaker
from app.graph.builder import get_graph, thread_config
from app.graph.state import GraphContext
from app.llm import get_chat_model
from app.locks import LockRegistry, get_lock_registry
from app.prompts import render_agent_system
from app.repositories import conversations
from app.schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()
UPSTREAM_ERROR = {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"}


@dataclass
class ChatTurn:
    conversation_id: int
    user_input: str
    today: date


def get_token_budget() -> int:
    return TOKEN_BUDGET


def get_today() -> date:
    return date.today()


async def prepare_chat_turn(
    req: ChatRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    budget: Annotated[int, Depends(get_token_budget)],
    today: Annotated[date, Depends(get_today)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterator[ChatTurn]:
    def check_budget(previous) -> None:
        try:
            build_history(previous, render_agent_system(today), req.message, budget)
        except BudgetExceeded:
            raise HTTPException(422, detail={"code": "budget_exceeded", "message": "消息过长，请缩短后重试"})

    sm = get_sessionmaker()
    # 新会话先校验预算，避免拒绝请求时留下空会话。
    if req.session_id is None:
        check_budget([])
    async with sm() as s:
        if req.session_id is not None:
            conversation = await conversations.get_for_user(s, int(req.session_id), req.user_id)
            if conversation is None:
                raise HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})
        else:
            conversation = await conversations.create(s, req.user_id)
            await s.commit()
        conversation_id = conversation.id

    lock = locks.get(conversation_id)
    if lock.locked():
        raise HTTPException(409, detail={"code": "session_busy", "message": "该会话正在处理上一条消息，请稍后重试"})
    await lock.acquire()
    # 使用 yield 依赖在响应结束或后续依赖出错时释放锁。
    try:
        if req.session_id is not None:
            # 历史从 checkpoint 读取。老会话没有 checkpoint，按空历史处理。
            state = await graph.aget_state(thread_config(conversation_id))
            check_budget(state.values.get("messages", []))
        yield ChatTurn(conversation_id=conversation_id, user_input=req.message, today=today)
    finally:
        lock.release()


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    turn: Annotated[ChatTurn, Depends(prepare_chat_turn)],
    model: Annotated[BaseChatModel, Depends(get_chat_model)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterable[ServerSentEvent]:
    def sse(name: str, data: dict) -> ServerSentEvent:
        return ServerSentEvent(raw_data=json.dumps(data, ensure_ascii=False), event=name)

    yield sse("session", {"session_id": str(turn.conversation_id)})
    ctx = GraphContext(conversation_id=turn.conversation_id, today=turn.today, model=model)
    try:
        async for name, data in graph.astream(
            {"user_input": turn.user_input}, thread_config(turn.conversation_id),
            context=ctx, stream_mode="custom",
        ):
            yield sse(name, data)
    except Exception:
        logger.exception("对话图执行失败")
        yield sse("error", UPSTREAM_ERROR)
        return
    yield sse("done", {"finish_reason": "stop"})
