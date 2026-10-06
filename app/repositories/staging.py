from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, QaExtractionStaging


@dataclass(frozen=True)
class NewStaging:
    batch_no: str
    source_ref: str
    question: str
    answer: str


async def db_utc_offset(s: AsyncSession) -> timedelta:
    """获取数据库会话时区相对 UTC 的偏移。"""
    seconds = await s.scalar(text("SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())"))
    return timedelta(seconds=seconds)


def local_to_db(dt: datetime, offset: timedelta) -> datetime:
    """按日期对应的本地时区转为数据库墙钟时间，保留夏令时规则。"""
    return (dt.astimezone(timezone.utc) + offset).replace(tzinfo=None)


async def unmined_conversation_ids(s: AsyncSession, day: date) -> list[int]:
    offset = await db_utc_offset(s)
    start = local_to_db(datetime.combine(day, time.min), offset)
    end = local_to_db(datetime.combine(day + timedelta(days=1), time.min), offset)
    ref = func.concat("conversation:", Conversation.id)
    mined = exists().where(QaExtractionStaging.source_ref == ref)
    rows = await s.scalars(
        select(Conversation.id)
        .where(Conversation.created_at >= start, Conversation.created_at < end, ~mined)
        .order_by(Conversation.id)
    )
    return list(rows)


async def max_batch_seq(s: AsyncSession, day: date) -> int:
    prefix = f"{day:%Y%m%d}-"
    values = await s.scalars(
        select(QaExtractionStaging.batch_no).where(QaExtractionStaging.batch_no.like(f"{prefix}%"))
    )
    return max((int(v[len(prefix):]) for v in values), default=0)


async def add_rows(s: AsyncSession, rows: list[NewStaging]) -> None:
    s.add_all([QaExtractionStaging(**vars(r)) for r in rows])


async def list_extracted(s: AsyncSession) -> list[QaExtractionStaging]:
    return list(await s.scalars(
        select(QaExtractionStaging).where(QaExtractionStaging.status == "extracted").order_by(QaExtractionStaging.id)
    ))


async def set_status(s: AsyncSession, row_id: int, status: str) -> None:
    await s.execute(update(QaExtractionStaging).where(QaExtractionStaging.id == row_id).values(status=status))


async def status_counts(s: AsyncSession) -> dict[str, int]:
    rows = await s.execute(select(QaExtractionStaging.status, func.count()).group_by(QaExtractionStaging.status))
    return {st: int(n) for st, n in rows}
