from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ToolAuditLog


async def add_audit(session: AsyncSession, rec) -> ToolAuditLog:
    row = ToolAuditLog(**asdict(rec))
    session.add(row)
    await session.flush()
    return row


async def list_recent(session: AsyncSession, limit: int = 50,
                      status: str | None = None) -> list[ToolAuditLog]:
    query = select(ToolAuditLog).order_by(ToolAuditLog.id.desc()).limit(limit)
    if status:
        query = query.where(ToolAuditLog.status == status)
    return list(await session.scalars(query))
