from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LowConfidenceQuestion


async def add(
    s: AsyncSession, *, conversation_id: int | None, raw_question: str, source: str, reason: str | None
) -> LowConfidenceQuestion:
    row = LowConfidenceQuestion(
        conversation_id=conversation_id, raw_question=raw_question, source=source, reason=reason,
    )
    s.add(row)
    await s.flush()
    return row
