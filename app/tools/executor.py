import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from langgraph.config import get_config
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.config import (
    CHARS_PER_TOKEN,
    TOOL_RETRY_BASE_DELAY,
    TOOL_RETRY_MAX_DELAY,
    get_settings,
)
from app.retry import retry_async
from app.tools import audit
from app.tools.registry import ToolEntry
from app.tools.toolset import Toolset, builtin_toolset
from app.tools.validation import validate_args

logger = logging.getLogger(__name__)
APPROVED = "approved"

_ERROR_MESSAGES = {
    "invalid_arguments": "参数不合法",
    "unknown_tool": "工具不存在",
    "tool_unavailable": "工具暂时不可用",
    "timeout": "查询超时，暂时不可用",
    "tool_error": "查询失败，暂时不可用",
}


@dataclass
class ToolOutcome:
    call_id: str
    name: str
    ok: bool
    message: ToolMessage
    data: Any = None
    status: str = "成功"
    found: bool = True
    retry_count: int = 0
    duration_ms: int = 0
    error: str | None = None


def tool_result_max_chars() -> int:
    return int(get_settings().tool_result_max_tokens * CHARS_PER_TOKEN)


def _make_outcome(
    call_id: str, name: str, *, ok: bool, content: str, data: Any = None,
    status: str = "成功", found: bool = True, retry_count: int = 0,
    duration_ms: int = 0, error: str | None = None,
) -> ToolOutcome:
    limit = tool_result_max_chars()
    if len(content) > limit:
        content = content[:limit] + "…(结果过长，已截断)"
    return ToolOutcome(
        call_id=call_id, name=name, ok=ok,
        message=ToolMessage(content=content, tool_call_id=call_id, name=name),
        data=data, status=status, found=found, retry_count=retry_count,
        duration_ms=duration_ms, error=error,
    )


def failure_outcome(call_id: str, name: str, code: str) -> ToolOutcome:
    """保留旧调用方的失败结果接口。"""
    reason = _ERROR_MESSAGES[code]
    content = json.dumps({"ok": False, "error": code, "message": reason}, ensure_ascii=False)
    return _make_outcome(call_id, name, ok=False, content=content, status="失败", error=reason)


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return all(is_transient(child) for child in exc.exceptions)
    if isinstance(exc, ToolException):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (502, 503, 504)
    return isinstance(exc, (TimeoutError, OperationalError, httpx.TransportError, ConnectionError))


async def _finish(
    call_id: str, name: str, *, conversation_id: int | None, entry: ToolEntry | None,
    arguments: dict | None, payload: dict, status: str, started: float,
    retry_count: int, data: Any = None, found: bool = True, error: str | None = None,
) -> ToolOutcome:
    outcome = _make_outcome(
        call_id, name, ok=status == "成功",
        content=json.dumps(payload, ensure_ascii=False, default=str), data=data,
        status=status, found=found, retry_count=retry_count,
        duration_ms=int((time.monotonic() - started) * 1000), error=error,
    )
    source = entry.source if entry is not None else "builtin"
    server = entry.server if entry is not None else None
    rec = await audit.record(audit.AuditRecord(
        conversation_id=conversation_id, tool_call_id=call_id, tool_name=name,
        tool_source=source, mcp_server=server, arguments=arguments,
        result_summary=outcome.message.content, status=status, error_message=error,
        retry_count=retry_count, duration_ms=outcome.duration_ms,
    ))
    # 延迟导入，避免 state → executor → events → state 的循环依赖。
    from app.graph import events

    try:
        node = get_config().get("metadata", {}).get("langgraph_node")
    except RuntimeError:
        node = None
    events.trace("tool", {
        "call_id": rec.tool_call_id, "name": rec.tool_name, "source": rec.tool_source,
        "mcp_server": rec.mcp_server, "status": rec.status, "retry_count": rec.retry_count,
        "duration_ms": rec.duration_ms, "error_message": rec.error_message,
    }, node=node)
    logger.info("tool_call conversation=%s name=%s source=%s status=%s retries=%s ms=%s",
                conversation_id, name, f"mcp:{server}" if source == "mcp" else source,
                status, retry_count, outcome.duration_ms)
    return outcome


async def execute_tool_calls(
    calls: list[dict],
    *,
    conversation_id: int | None,
    toolset: Toolset | None = None,
    approvals: Mapping[str, str] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> list[ToolOutcome]:
    """并行执行工具调用，并按输入顺序返回结果。"""
    if toolset is None:
        toolset = builtin_toolset()
    if approvals is None:
        approvals = {}

    async def execute_call(call: dict) -> ToolOutcome:
        started = time.monotonic()
        call_id = name = ""
        entry = None
        arguments = None
        retry_count = 0

        async def finish(payload, status, *, data=None, found=True, error=None):
            return await _finish(
                call_id, name, conversation_id=conversation_id, entry=entry,
                arguments=arguments, payload=payload, status=status, started=started,
                retry_count=retry_count, data=data, found=found, error=error,
            )

        async def reject(code, status, reason, *, error=None):
            return await finish({"ok": False, "error": code, "message": reason}, status,
                                error=reason if error is None else error)

        async def invalid(errors):
            reason = "；".join(errors)
            return await reject("invalid_arguments", "校验拦下",
                                f"参数不合法：{reason}。请向用户追问缺少的信息，或修正后重试",
                                error=reason)

        def on_retry(number, exc):
            nonlocal retry_count
            retry_count = number

        try:
            call_id = call.get("id") if isinstance(call.get("id"), str) else ""
            name = call.get("name") if isinstance(call.get("name"), str) else ""
            args = call.get("args")
            entry = toolset.get(name)
            if not isinstance(args, dict):
                return await invalid(["参数必须是 JSON 对象"])
            arguments = dict(args)
            if name in toolset.closed:
                return await reject("permission_denied", "权限拒绝", toolset.closed[name])
            if name in toolset.unavailable:
                return await reject("tool_unavailable", "失败", _ERROR_MESSAGES["tool_unavailable"])
            if entry is None:
                return await reject("unknown_tool", "失败", _ERROR_MESSAGES["unknown_tool"])
            errors = validate_args(entry.parameters, args)
            if errors:
                return await invalid(errors)
            if entry.permission == "write":
                approval = approvals.get(call_id, "未经用户确认")
                if approval != APPROVED:
                    return await reject("permission_denied", "权限拒绝", approval)
            if entry.inject_conversation_id:
                args = {**args, "conversation_id": conversation_id}

            async def attempt():
                return await asyncio.wait_for(entry.runner(args, call_id), entry.timeout)

            if entry.permission == "read" and entry.max_retries > 0:
                result = await retry_async(
                    attempt, attempts=entry.max_retries + 1,
                    base_delay=TOOL_RETRY_BASE_DELAY, max_delay=TOOL_RETRY_MAX_DELAY,
                    should_retry=is_transient, on_retry=on_retry, sleep=sleep, rand=rand,
                )
            else:
                result = await attempt()
        except ValidationError as exc:
            return await invalid([error["msg"] for error in exc.errors()])
        except TimeoutError:
            return await reject("timeout", "超时", _ERROR_MESSAGES["timeout"],
                                error=f"超时（共 {retry_count + 1} 次尝试）")
        except Exception as exc:
            return await reject("tool_error", "失败", _ERROR_MESSAGES["tool_error"],
                                error=type(exc).__name__)

        try:
            if (result is None or result == {} or result == []
                    or isinstance(result, dict) and result.get("found") is False):
                message = "没有查到相关数据"
                if isinstance(result, dict) and "reason" in result:
                    message += f"：{result['reason']}"
                return await finish({"ok": True, "found": False, "message": message}, "成功",
                                    data=result, found=False, error="查询落空")
            formatted = entry.formatter(result) if entry.formatter is not None else result
            return await finish({"ok": True, "data": formatted}, "成功", data=result)
        except Exception as exc:
            return await reject("tool_error", "失败", _ERROR_MESSAGES["tool_error"],
                                error=type(exc).__name__)

    return await asyncio.gather(*(execute_call(call) for call in calls))
