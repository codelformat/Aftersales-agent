import asyncio

import pytest
from sqlalchemy import func, select

from app.db import engine as engine_mod
from app.db.models import ToolAuditLog
from app.tools import audit

pytestmark = pytest.mark.anyio


def rec(**kw):
    base = dict(
        conversation_id=None, tool_call_id="c1", tool_name="query_order", tool_source="builtin",
        mcp_server=None, arguments={"order_id": "1001"}, result_summary="ok", status="成功",
        error_message=None, retry_count=0, duration_ms=12,
    )
    return audit.AuditRecord(**{**base, **kw})


async def test_default_fixture_captures_in_memory(audit_log):
    await audit.record(rec())
    assert audit_log == [rec()]
    assert engine_mod._sessionmaker is None  # 没有连开发库。


async def test_record_truncates(audit_log):
    await audit.record(rec(result_summary="字" * 600, error_message="错" * 600))
    assert len(audit_log[0].result_summary) == 500 and len(audit_log[0].error_message) == 512


async def test_writer_failure_is_swallowed(caplog):
    async def boom(_):
        raise RuntimeError("db down")

    audit.set_audit_writer(boom)
    await audit.record(rec())
    assert "audit_write_failed" in caplog.text


async def test_writer_timeout_is_swallowed(caplog, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_WRITE_TIMEOUT_SECONDS", 0.01)

    async def slow(_):
        await asyncio.sleep(1)

    audit.set_audit_writer(slow)
    await audit.record(rec())
    assert "audit_write_failed" in caplog.text


async def test_db_writer_persists_chinese_status(db, db_audit):
    await audit.record(rec(status="权限拒绝", error_message="用户取消", conversation_id=None))
    async with db() as s:
        row = (await s.execute(select(ToolAuditLog))).scalar_one()
    assert (row.status, row.error_message, row.arguments, row.retry_count) == (
        "权限拒绝", "用户取消", {"order_id": "1001"}, 0,
    )
    async with db() as s:
        hexed = (await s.execute(select(func.hex(ToolAuditLog.status)))).scalar_one()
    assert hexed == "E69D83E99990E68B92E7BB9D"  # 校验存储字节，避免漏检双重编码。
