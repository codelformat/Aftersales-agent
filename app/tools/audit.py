"""工具调用审计。写入失败只记日志，不影响工具执行。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from app.config import AUDIT_ERROR_MAX_CHARS, AUDIT_SUMMARY_MAX_CHARS, AUDIT_WRITE_TIMEOUT_SECONDS
from app.db.engine import get_sessionmaker
from app.repositories.audit import add_audit

logger = logging.getLogger(__name__)
AuditStatus = Literal["成功", "失败", "超时", "校验拦下", "权限拒绝"]


@dataclass(frozen=True)
class AuditRecord:
    conversation_id: int | None
    tool_call_id: str | None
    tool_name: str
    tool_source: Literal["builtin", "mcp"]
    mcp_server: str | None
    arguments: dict | None
    result_summary: str | None
    status: AuditStatus
    error_message: str | None
    retry_count: int
    duration_ms: int | None


async def write_db(rec: AuditRecord) -> None:
    async with get_sessionmaker()() as s:
        await add_audit(s, rec)
        await s.commit()


_writer: Callable[[AuditRecord], Awaitable[None]] = write_db


def set_audit_writer(writer: Callable[[AuditRecord], Awaitable[None]] | None) -> None:
    global _writer
    _writer = writer or write_db


def _cut(value: str | None, limit: int) -> str | None:
    return value if value is None or len(value) <= limit else value[:limit]


async def record(rec: AuditRecord) -> None:
    rec = replace(rec, result_summary=_cut(rec.result_summary, AUDIT_SUMMARY_MAX_CHARS),
                  error_message=_cut(rec.error_message, AUDIT_ERROR_MAX_CHARS))
    try:
        await asyncio.wait_for(_writer(rec), AUDIT_WRITE_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("audit_write_failed tool=%s call=%s error=%s", rec.tool_name, rec.tool_call_id,
                       type(exc).__name__)
