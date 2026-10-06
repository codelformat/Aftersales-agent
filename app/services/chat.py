import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import Runnable

from app.prompts import chat_prompt, chat_prompt_vars
from app.session import Session

logger = logging.getLogger(__name__)

UPSTREAM_ERROR = {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"}


@dataclass
class ChatTurn:
    session: Session
    history: list[BaseMessage]
    user_input: str
    today: date


async def stream_reply(turn: ChatTurn, model: Runnable) -> AsyncIterator[tuple[str, dict]]:
    # 发送会话和回复事件，在生成成功后保存对话消息。
    yield "session", {"session_id": turn.session.id}
    buffer = ""
    try:
        async for chunk in (chat_prompt | model).astream({
            **chat_prompt_vars(turn.today),
            "history": turn.history,
            "input": turn.user_input,
        }):
            if isinstance(chunk.content, str) and chunk.content:
                buffer += chunk.content
                yield "token", {"text": chunk.content}
    except Exception:
        logger.exception("上游对话流失败")
        yield "error", UPSTREAM_ERROR
    else:
        if not buffer:
            logger.warning("上游对话流返回空回复")
            yield "error", UPSTREAM_ERROR
            return
        turn.session.messages.append(HumanMessage(turn.user_input))
        turn.session.messages.append(AIMessage(buffer))
        yield "done", {"finish_reason": "stop"}
