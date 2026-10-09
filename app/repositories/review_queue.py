from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReviewItem


async def add(s: AsyncSession, *, normalized_question: str, suggested_answer: str | None) -> ReviewItem:
    row = ReviewItem(normalized_question=normalized_question, ai_suggested_answer=suggested_answer)
    s.add(row)
    await s.flush()
    await s.refresh(row)
    return row


async def increment(s: AsyncSession, review_id: int) -> None:
    await s.execute(update(ReviewItem).where(ReviewItem.id == review_id)
                    .values(occurrence_count=ReviewItem.occurrence_count + 1))


async def list_pending_questions(s: AsyncSession) -> list[tuple[int, str]]:
    rows = await s.execute(select(ReviewItem.id, ReviewItem.normalized_question)
                           .where(ReviewItem.review_status == "待审").order_by(ReviewItem.id))
    return [(i, q) for i, q in rows]


async def get(s: AsyncSession, review_id: int, *, for_update: bool = False) -> ReviewItem | None:
    stmt = select(ReviewItem).where(ReviewItem.id == review_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await s.execute(stmt)).scalar_one_or_none()


async def list_items(s: AsyncSession, status: str | None) -> list[ReviewItem]:
    stmt = select(ReviewItem)
    if status is not None:
        stmt = stmt.where(ReviewItem.review_status == status)
    stmt = stmt.order_by(ReviewItem.occurrence_count.desc(), ReviewItem.updated_at.desc(), ReviewItem.id.desc())
    return list((await s.execute(stmt)).scalars())


async def set_status(s: AsyncSession, review_id: int, status: str, approved_answer: str | None = None) -> None:
    values = {"review_status": status}
    if approved_answer is not None:
        values["approved_answer"] = approved_answer
    await s.execute(update(ReviewItem).where(ReviewItem.id == review_id).values(**values))
