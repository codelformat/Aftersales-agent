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


def _make_outcome(call_id: str, name: str, *, ok: bool, content: str) -> ToolOutcome:
    if len(content) > TOOL_RESULT_MAX_CHARS:
        content = content[:TOOL_RESULT_MAX_CHARS] + "…(结果过长，已截断)"
    return ToolOutcome(
        call_id=call_id,
        name=name,
        ok=ok,
        message=ToolMessage(content=content, tool_call_id=call_id, name=name),
    )


def _failure(call_id: str, name: str, code: str) -> ToolOutcome:
    content = json.dumps(
        {"ok": False, "error": code, "message": _ERROR_MESSAGES[code]},
        ensure_ascii=False,
    )
    return _make_outcome(call_id, name, ok=False, content=content)


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
        call_id = name = ""
        try:
            call_id = call.get("id") or ""
            name = call.get("name") or ""
            args = call.get("args")
            if not isinstance(args, dict):
                return _failure(call_id, name, "invalid_arguments")
            spec = registry.get(name)
            if spec is None:
                return _failure(call_id, name, "unknown_tool")
            if spec.inject_conversation_id:
                args = {**args, "conversation_id": conversation_id}

            # 调用前校验参数，工具内部的校验异常由执行失败分支处理。
            try:
                spec.tool.args_schema.model_validate(args)
            except ValidationError:
                logger.exception("工具参数校验失败")
                return _failure(call_id, name, "invalid_arguments")

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
            except TimeoutError:
                logger.exception("工具调用超时")
                return _failure(call_id, name, "timeout")
            content = json.dumps(
                {"ok": True, "data": result}, ensure_ascii=False, default=str
            )
            return _make_outcome(call_id, name, ok=True, content=content)
        except Exception:
            logger.exception("工具执行失败")
            # 标识字段异常时使用空字符串，保证失败结果仍可构造。
            return _failure(
                call_id if isinstance(call_id, str) else "",
                name if isinstance(name, str) else "",
                "tool_error",
            )

    return await asyncio.gather(*(execute_call(call) for call in tool_calls))
