from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Faq


async def search(session: AsyncSession, keyword: str, limit: int) -> list[Faq]:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    rows = await session.scalars(
        select(Faq)
        .where(
            or_(
                Faq.question.like(pattern, escape="\\"),
                Faq.answer.like(pattern, escape="\\"),
            )
        )
        .order_by(Faq.id)
        .limit(limit)
    )
    return list(rows)
