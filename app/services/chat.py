import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from app.db.engine import get_sessionmaker
from app.prompts import TOOL_ROUND_CLOSING, chat_prompt, chat_prompt_vars
from app.repositories import messages
from app.services.grounding import (
    REFUSED_CONTENT,
    collect_evidence,
    parse_citations,
    record_low_confidence,
    render_evidence,
    self_check,
)
from app.services.history import turn_rows
from app.tools.executor import execute_tool_calls
from app.tools.registry import CH04_CHAT_TOOLS, get_registry

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
    prompt_vars = {
        **chat_prompt_vars(turn.today),
        "history": turn.history,
        "input": turn.user_input,
    }
    first_text = ""
    gathered = None
    try:
        async for chunk in (
            chat_prompt | model.bind_tools(get_registry().tools_for_model(CH04_CHAT_TOOLS), tool_choice="auto")
        ).astream(prompt_vars):
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
        try:
            async with get_sessionmaker()() as s:
                await messages.add_turn(
                    s, turn.conversation_id, turn_rows(turn.user_input, first_text)
                )
                await s.commit()
        except Exception:
            logger.exception("对话消息保存失败")
            yield "error", UPSTREAM_ERROR
            return
        yield "done", {"finish_reason": "stop"}
        return

    request = AIMessage(content=first_text, tool_calls=tool_calls)
    yield "tool_start", {"tools": [
        {"id": call["id"], "name": call["name"], "args": call["args"]}
        for call in tool_calls
    ]}
    try:
        outcomes = await execute(tool_calls, conversation_id=turn.conversation_id)
    except Exception:
        logger.exception("工具执行轮次失败")
        yield "error", UPSTREAM_ERROR
        return
    yield "tool_end", {"tools": [
        {"id": o.call_id, "name": o.name, "ok": o.ok} for o in outcomes
    ]}

    citations = []
    faq_calls = [
        (o, call) for o, call in zip(outcomes, tool_calls) if o.name == "query_faq" and o.ok
    ]
    if faq_calls:
        try:
            evidence = collect_evidence([
                (o.call_id, str(call["args"].get("question", "")), o.data)
                for o, call in faq_calls
            ])
            check = await self_check(evidence.questions, evidence.citations)
        except Exception:
            logger.exception("知识库证据处理失败")
            yield "error", UPSTREAM_ERROR
            return
        if check.useful:
            citations = evidence.citations
            for o, _ in faq_calls:
                o.message = ToolMessage(
                    content=render_evidence(evidence.by_call[o.call_id]),
                    tool_call_id=o.call_id, name=o.name,
                )
            yield "citations", {"items": [c.to_dict() for c in citations], "refused": False}
        else:
            # 独立事务，失败只记日志。
            await record_low_confidence(turn.conversation_id, turn.user_input, check.reason)
            for o, _ in faq_calls:
                # 模型看不到不足的证据；写库时存模型实际看到的内容。
                o.message = ToolMessage(
                    content=REFUSED_CONTENT, tool_call_id=o.call_id, name=o.name,
                )
            yield "citations", {"items": [], "refused": True}

    final_text = ""
    pending_tokens = []
    prefix_checked = False
    try:
        async for chunk in (chat_prompt | model).astream({
            **prompt_vars, "tool_round": [
                request, *[o.message for o in outcomes], SystemMessage(TOOL_ROUND_CLOSING)
            ]
        }):
            if isinstance(chunk.content, str) and chunk.content:
                final_text += chunk.content
                if not prefix_checked:
                    pending_tokens.append(chunk.content)
                    if len(final_text.lstrip()) < 2:
                        continue
                    if final_text.lstrip().startswith("<｜"):
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

    if final_text.lstrip().startswith("<｜"):
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
    out_of_range = [n for n in parse_citations(final_text) if not 1 <= n <= len(citations)]
    if out_of_range:
        logger.warning("回复含越界引用编号：%s", out_of_range)
    try:
        async with get_sessionmaker()() as s:
            await messages.add_turn(
                s,
                turn.conversation_id,
                turn_rows(turn.user_input, final_text, request, [o.message for o in outcomes]),
            )
            await s.commit()
    except Exception:
        logger.exception("工具轮次消息保存失败")
        yield "error", UPSTREAM_ERROR
        return
    yield "done", {"finish_reason": "stop"}
