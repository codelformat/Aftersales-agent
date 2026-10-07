from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FaithCase

OPEN = "未解决"


async def upsert_case(
    s: AsyncSession, *, eval_id: str, bucket: str, query: str, answer: str, reason: str,
    citations: list[dict], judge_model: str | None, strategy: str = "hybrid_rerank",
) -> FaithCase:
    """一题一行。重复判出时更新快照和次数。已处置的题复发时退回未解决。"""
    row = await s.scalar(select(FaithCase).where(FaithCase.eval_id == eval_id).with_for_update())
    if row is None:
        row = FaithCase(
            eval_id=eval_id, bucket=bucket, query=query, strategy=strategy, answer=answer,
            reason=reason, citations=citations, judge_model=judge_model,
        )
        s.add(row)
        await s.flush()
        return row
    row.bucket, row.query, row.strategy = bucket, query, strategy
    row.answer, row.reason, row.citations, row.judge_model = answer, reason, citations, judge_model
    row.seen_count = FaithCase.seen_count + 1
    row.last_seen_at = func.now()
    if row.status != OPEN:
        # 复发：退回未解决。保留 resolved_at，供页面标记「复发」。
        row.status, row.resolution = OPEN, None
    await s.flush()
    return row


async def list_cases(s: AsyncSession, status: str | None = None) -> list[FaithCase]:
    stmt = select(FaithCase).order_by(FaithCase.last_seen_at.desc(), FaithCase.id.desc())
    if status is not None:
        stmt = stmt.where(FaithCase.status == status)
    return list(await s.scalars(stmt))


async def resolve_case(
    s: AsyncSession, case_id: int, status: str, resolution: str,
) -> FaithCase | None:
    row = await s.get(FaithCase, case_id)
    if row is None:
        return None
    row.status, row.resolution, row.resolved_at = status, resolution, func.now()
    await s.flush()
    return row
