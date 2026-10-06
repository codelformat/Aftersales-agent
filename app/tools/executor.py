import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain_core.messages import ToolMessage
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.config import (
    TOOL_MAX_ATTEMPTS,
    TOOL_RESULT_MAX_CHARS,
    TOOL_RETRY_BASE_DELAY,
    TOOL_RETRY_MAX_DELAY,
)
from app.retry import retry_async
from app.tools.registry import ToolRegistry, get_registry

logger = logging.getLogger(__name__)

_ERROR_MESSAGES = {
    "invalid_arguments": "参数不合法",
    "unknown_tool": "工具不存在",
    "timeout": "查询超时",
    "tool_error": "查询失败",
}


@dataclass
class ToolOutcome:
    call_id: str
    name: str
    ok: bool
    message: ToolMessage


def _make_outcome(call: dict, *, ok: bool, content: str) -> ToolOutcome:
    if len(content) > TOOL_RESULT_MAX_CHARS:
        content = content[:TOOL_RESULT_MAX_CHARS] + "…(结果过长，已截断)"
    return ToolOutcome(
        call_id=call["id"],
        name=call["name"],
        ok=ok,
        message=ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]),
    )


def _failure(call: dict, code: str) -> ToolOutcome:
    content = json.dumps(
        {"ok": False, "error": code, "message": _ERROR_MESSAGES[code]},
        ensure_ascii=False,
    )
    return _make_outcome(call, ok=False, content=content)


async def execute_tool_calls(
    tool_calls: list[dict],
    *,
    conversation_id: int,
    registry: ToolRegistry | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> list[ToolOutcome]:
    """并行执行工具调用，并按输入顺序返回结果。"""
    if registry is None:
        registry = get_registry()

    async def execute_call(call: dict) -> ToolOutcome:
        spec = registry.get(call["name"])
        if spec is None:
            return _failure(call, "unknown_tool")

        args = call["args"]
        if not isinstance(args, dict):
            return _failure(call, "invalid_arguments")
        if spec.inject_conversation_id:
            args = {**args, "conversation_id": conversation_id}

        async def attempt():
            return await asyncio.wait_for(spec.tool.ainvoke(args), spec.timeout)

        try:
            if spec.retryable:
                result = await retry_async(
                    attempt,
                    attempts=TOOL_MAX_ATTEMPTS,
                    base_delay=TOOL_RETRY_BASE_DELAY,
                    max_delay=TOOL_RETRY_MAX_DELAY,
                    retry_on=(TimeoutError, OperationalError),
                    sleep=sleep,
                    rand=rand,
                )
            else:
                result = await attempt()
            content = json.dumps(
                {"ok": True, "data": result}, ensure_ascii=False, default=str
            )
        except ValidationError:
            logger.exception("工具参数校验失败")
            return _failure(call, "invalid_arguments")
        except TimeoutError:
            logger.exception("工具调用超时")
            return _failure(call, "timeout")
        except Exception:
            logger.exception("工具执行失败")
            return _failure(call, "tool_error")
        return _make_outcome(call, ok=True, content=content)

    return await asyncio.gather(*(execute_call(call) for call in tool_calls))
