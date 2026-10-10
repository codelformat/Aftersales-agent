import json
import logging
import time
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.config import get_settings
from app.context import count_tokens
from app.db.engine import get_sessionmaker
from app.graph.builder import get_graph, thread_config
from app.graph.state import GraphContext
from app.llm import get_chat_model
from app.locks import LockRegistry, get_lock_registry
from app.repositories import conversations
from app.schemas import ChatRequest, ResumeRequest
from app.tools import audit
from app.tools.audit import AuditRecord

logger = logging.getLogger(__name__)
router = APIRouter()
UPSTREAM_ERROR = {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"}
NO_PENDING = {"code": "no_pending_selection", "message": "没有待选择的订单，请重新提问"}
NO_PENDING_CONFIRMATION = {"code": "no_pending_confirmation", "message": "没有待确认的工单，请重新提问"}
INVALID_ORDER = {"code": "invalid_order", "message": "订单不在可选列表中"}


@dataclass
class ChatTurn:
    conversation_id: int
    user_input: str
    today: date
    user_id: str
    resume_value: Any = None
    intent: str | None = None
    debug: bool = False
    started_at: float = field(default_factory=time.monotonic)


def sse(name: str, data: dict) -> ServerSentEvent:
    return ServerSentEvent(raw_data=json.dumps(data, ensure_ascii=False), event=name)


async def stream_graph(graph, graph_input, turn: ChatTurn, model) -> AsyncIterator[ServerSentEvent]:
    yield sse("session", {"session_id": str(turn.conversation_id)})
    ctx = GraphContext(conversation_id=turn.conversation_id, today=turn.today, model=model, user_id=turn.user_id,
                       debug=turn.debug, started_at=turn.started_at)
    interrupted = False
    saved_id = None
    try:
        async for mode, chunk in graph.astream(
            graph_input, thread_config(turn.conversation_id, turn.user_id, intent=turn.intent),
            context=ctx, stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                name, data = chunk
                if name == "saved":
                    saved_id = data["message_id"]
                    continue
                yield sse(name, data)
            elif mode == "updates" and "__interrupt__" in chunk:
                # 节点恢复时会重新执行，卡片由这里发出。
                value = chunk["__interrupt__"][0].value
                if value.get("type") == "ticket_confirm":
                    yield sse("ticket_preview", {k: value[k] for k in ("call_id", "ticket_type", "description")})
                else:
                    yield sse("order_picker", {"orders": value["orders"]})
                interrupted = True
    except Exception:
        logger.exception("对话图执行失败")
        yield sse("error", UPSTREAM_ERROR)
        return
    done = {"finish_reason": "interrupted" if interrupted else "stop"}
    if saved_id is not None and not interrupted:
        done["message_id"] = saved_id
    yield sse("done", done)


def get_input_token_limit() -> int:
    return get_settings().max_user_input_tokens


def get_today() -> date:
    return date.today()


async def prepare_chat_turn(
    req: ChatRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    limit: Annotated[int, Depends(get_input_token_limit)],
    today: Annotated[date, Depends(get_today)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterator[ChatTurn]:
    if count_tokens([HumanMessage(req.message)]) > limit:
        raise HTTPException(422, detail={"code": "budget_exceeded", "message": "消息过长，请缩短后重试"})

    sm = get_sessionmaker()
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
            pending = _pending(await graph.aget_state(thread_config(conversation_id)), "ticket_confirm")
            if pending is not None:
                await audit.record(AuditRecord(
                    conversation_id=conversation_id, tool_call_id=pending["call_id"], tool_name="create_ticket",
                    tool_source="builtin", mcp_server=None,
                    arguments={"description": pending["description"], "ticket_type": pending["ticket_type"]},
                    result_summary=None, status="权限拒绝", error_message="用户未确认，已被新消息取代",
                    retry_count=0, duration_ms=None,
                ))
        yield ChatTurn(conversation_id=conversation_id, user_input=req.message, today=today, user_id=req.user_id,
                       debug=req.debug)
    finally:
        lock.release()


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    turn: Annotated[ChatTurn, Depends(prepare_chat_turn)],
    model: Annotated[BaseChatModel, Depends(get_chat_model)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterable[ServerSentEvent]:
    async for event in stream_graph(graph, {"user_input": turn.user_input}, turn, model):
        yield event


def _pending(state, kind) -> dict | None:
    for item in getattr(state, "interrupts", ()) or ():
        if isinstance(item.value, dict) and item.value.get("type") == kind:
            return item.value
    return None


async def prepare_resume(
    req: ResumeRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    today: Annotated[date, Depends(get_today)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterator[ChatTurn]:
    cid = int(req.session_id)
    async with get_sessionmaker()() as s:
        if await conversations.get_for_user(s, cid, req.user_id) is None:
            raise HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})
    lock = locks.get(cid)
    if lock.locked():
        raise HTTPException(409, detail={"code": "session_busy", "message": "该会话正在处理上一条消息，请稍后重试"})
    await lock.acquire()
    try:
        state = await graph.aget_state(thread_config(cid))
        if req.order_id is not None:
            picker = _pending(state, "order_picker")
            if picker is None:
                raise HTTPException(409, detail=NO_PENDING)
            if req.order_id not in {o["order_id"] for o in picker["orders"]}:
                raise HTTPException(422, detail=INVALID_ORDER)
            resume_value = req.order_id
        else:
            if _pending(state, "ticket_confirm") is None:
                raise HTTPException(409, detail=NO_PENDING_CONFIRMATION)
            resume_value = {"confirmed": req.ticket_confirm}
        yield ChatTurn(conversation_id=cid, user_input="", today=today, user_id=req.user_id,
                       resume_value=resume_value, intent=state.values.get("intent"), debug=req.debug)
    finally:
        lock.release()


@router.post("/chat/resume", response_class=EventSourceResponse)
async def chat_resume(
    turn: Annotated[ChatTurn, Depends(prepare_resume)],
    model: Annotated[BaseChatModel, Depends(get_chat_model)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterable[ServerSentEvent]:
    async for event in stream_graph(graph, Command(resume=turn.resume_value), turn, model):
        yield event
