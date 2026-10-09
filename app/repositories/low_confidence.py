from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LowConfidenceQuestion


async def add(
    s: AsyncSession, *, conversation_id: int | None, raw_question: str, source: str, reason: str | None,
    retrieved_chunks: list[dict] | None = None,
) -> LowConfidenceQuestion:
    row = LowConfidenceQuestion(
        conversation_id=conversation_id, raw_question=raw_question, source=source, reason=reason,
        retrieved_chunks=retrieved_chunks,
    )
    s.add(row)
    await s.flush()
    return row


async def get(s: AsyncSession, lcq_id: int) -> LowConfidenceQuestion | None:
    return await s.get(LowConfidenceQuestion, lcq_id)


async def set_matched(s: AsyncSession, lcq_id: int, review_id: int) -> None:
    await s.execute(update(LowConfidenceQuestion).where(LowConfidenceQuestion.id == lcq_id)
                    .values(matched_review_id=review_id))


async def list_unmatched_ids(s: AsyncSession, limit: int | None = None) -> list[int]:
    stmt = (select(LowConfidenceQuestion.id).where(LowConfidenceQuestion.matched_review_id.is_(None))
            .order_by(LowConfidenceQuestion.id))
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await s.execute(stmt)).scalars())


async def count_unmatched(s: AsyncSession) -> int:
    return (await s.execute(select(func.count()).select_from(LowConfidenceQuestion)
                            .where(LowConfidenceQuestion.matched_review_id.is_(None)))).scalar_one()


async def list_for_review(s: AsyncSession, review_id: int) -> list[LowConfidenceQuestion]:
    rows = await s.execute(select(LowConfidenceQuestion).where(LowConfidenceQuestion.matched_review_id == review_id)
                           .order_by(LowConfidenceQuestion.created_at, LowConfidenceQuestion.id))
    return list(rows.scalars())


async def find_by_reason(
    s: AsyncSession, *, conversation_id: int, source: str, reason: str
) -> LowConfidenceQuestion | None:
    rows = await s.execute(select(LowConfidenceQuestion).where(
        LowConfidenceQuestion.conversation_id == conversation_id,
        LowConfidenceQuestion.source == source, LowConfidenceQuestion.reason == reason).limit(1))
    return rows.scalar_one_or_none()
