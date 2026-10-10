from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.db.engine import get_sessionmaker
from app.repositories import audit

router = APIRouter()
AuditStatus = Literal["成功", "失败", "超时", "校验拦下", "权限拒绝", ""]


class ToolAuditOut(BaseModel):
    id: int
    created_at: datetime
    conversation_id: int | None
    tool_call_id: str | None
    tool_name: str
    tool_source: str
    mcp_server: str | None
    status: str
    retry_count: int
    duration_ms: int | None
    error_message: str | None
    result_summary: str | None


@router.get("/api/tool-audit", response_model=list[ToolAuditOut])
async def list_tool_audit(limit: int = Query(50, ge=1, le=500),
                          status: AuditStatus | None = None) -> list[dict]:
    async with get_sessionmaker()() as session:
        rows = await audit.list_recent(session, limit, status or None)
    return [{key: getattr(row, key) for key in ToolAuditOut.model_fields} for row in rows]
