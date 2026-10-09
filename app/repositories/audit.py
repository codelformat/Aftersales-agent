from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ToolAuditLog


async def add_audit(session: AsyncSession, rec) -> ToolAuditLog:
    row = ToolAuditLog(**asdict(rec))
    session.add(row)
    await session.flush()
    return row
