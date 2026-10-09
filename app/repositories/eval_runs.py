from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvalRun


async def add(s: AsyncSession, *, triggered_by: str, dataset_size: int, metrics: dict) -> EvalRun:
    row = EvalRun(triggered_by=triggered_by, dataset_size=dataset_size, metrics=metrics)
    s.add(row)
    await s.flush()
    await s.refresh(row)
    return row


async def list_recent(s: AsyncSession, limit: int) -> list[EvalRun]:
    rows = await s.execute(select(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id.desc()).limit(limit))
    return list(reversed(list(rows.scalars())))
