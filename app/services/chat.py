import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from app.db.engine import get_sessionmaker
from app.prompts import TOOL_ROUND_CLOSING, chat_prompt, chat_prompt_vars
from app.repositories import messages
from app.services.history import turn_rows
from app.tools.executor import execute_tool_calls
from app.tools.registry import get_registry

logger = logging.getLogger(__name__)

UPSTREAM_ERROR = {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"}
TOOL_MARKUP_MARKERS = ("<｜", "｜DSML｜", "invoke name=")


@dataclass
class ChatTurn:
    conversation_id: int
    history: list[BaseMessage]
    user_input: str
    today: date


async def stream_reply(
    turn: ChatTurn, model: BaseChatModel, *, execute=execute_tool_calls
) -> AsyncIterator[tuple[str, dict]]:
    """流式发送回复。完整回复生成后，提交整轮消息。"""
    yield "session", {"session_id": str(turn.conversation_id)}
    vars = {
        **chat_prompt_vars(turn.today),
        "history": turn.history,
        "input": turn.user_input,
    }
    first_text = ""
    gathered = None
    try:
        async for chunk in (
            chat_prompt | model.bind_tools(get_registry().tools_for_model(), tool_choice="auto")
        ).astream(vars):
            if isinstance(chunk.content, str) and chunk.content:
                yield "token", {"text": chunk.content}
                first_text += chunk.content
            gathered = chunk if gathered is None else gathered + chunk
    except Exception:
        logger.exception("上游对话流失败")
        yield "error", UPSTREAM_ERROR
        return

    tool_calls = gathered.tool_calls if gathered else []
    if not tool_calls:
        if not first_text:
            logger.warning("上游对话流返回空回复")
            yield "error", UPSTREAM_ERROR
            return
        async with get_sessionmaker()() as s:
            await messages.add_turn(
                s, turn.conversation_id, turn_rows(turn.user_input, first_text)
            )
            await s.commit()
        yield "done", {"finish_reason": "stop"}
        return

    request = AIMessage(content=first_text, tool_calls=tool_calls)
    yield "tool_start", {"tools": [
        {"id": call["id"], "name": call["name"], "args": call["args"]}
        for call in tool_calls
    ]}
    outcomes = await execute(tool_calls, conversation_id=turn.conversation_id)
    yield "tool_end", {"tools": [
        {"id": o.call_id, "name": o.name, "ok": o.ok} for o in outcomes
    ]}

    final_text = ""
    pending_tokens = []
    prefix_checked = False
    try:
        async for chunk in (chat_prompt | model).astream({
            **vars, "tool_round": [
                request, *[o.message for o in outcomes], SystemMessage(TOOL_ROUND_CLOSING)
            ]
        }):
            if isinstance(chunk.content, str) and chunk.content:
                final_text += chunk.content
                if not prefix_checked:
                    pending_tokens.append(chunk.content)
                    if len(final_text) < 2:
                        continue
                    if final_text.startswith("<｜"):
                        break
                    prefix_checked = True
                    for token in pending_tokens:
                        yield "token", {"text": token}
                    pending_tokens.clear()
                else:
                    yield "token", {"text": chunk.content}
    except Exception:
        logger.exception("上游工具结果回复流失败")
        yield "error", UPSTREAM_ERROR
        return

    if final_text.startswith("<｜"):
        logger.warning("上游工具结果回复流以工具调用标记开头")
        yield "error", UPSTREAM_ERROR
        return
    for token in pending_tokens:
        yield "token", {"text": token}
    if any(marker in final_text for marker in TOOL_MARKUP_MARKERS):
        logger.warning("上游工具结果回复流包含工具调用标记")
        yield "error", UPSTREAM_ERROR
        return
    if not final_text:
        logger.warning("上游工具结果回复流返回空回复")
        yield "error", UPSTREAM_ERROR
        return
    async with get_sessionmaker()() as s:
        await messages.add_turn(
            s,
            turn.conversation_id,
            turn_rows(turn.user_input, final_text, request, [o.message for o in outcomes]),
        )
        await s.commit()
    yield "done", {"finish_reason": "stop"}
