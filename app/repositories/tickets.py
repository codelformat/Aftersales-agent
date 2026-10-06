import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Ticket
from app.repositories import conversations
from app.retry import retry_async


class DuplicateTicketNo(Exception):
    """工单号已被占用。"""


async def next_ticket_no(session: AsyncSession, today: date) -> str:
    prefix = f"T{today:%Y%m%d}"
    latest = await session.scalar(
        select(func.max(cast(func.substr(Ticket.ticket_no, len(prefix) + 1), Integer)))
        .where(Ticket.ticket_no.like(f"{prefix}%"))
    )
    sequence = latest + 1 if latest is not None else 1
    return f"{prefix}{sequence:03d}"


async def create_ticket_record(
    sm: async_sessionmaker,
    conversation_id: int,
    description: str,
    ticket_type: str,
    today: date,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
    next_no: Callable[[AsyncSession, date], Awaitable[str]] = next_ticket_no,
) -> Ticket:
    async def create_once() -> Ticket:
        # 每次尝试使用新会话。关闭失败的会话时，回滚未提交的事务。
        try:
            async with sm() as session:
                ticket = Ticket(
                    ticket_no=await next_no(session, today),
                    conversation_id=conversation_id,
                    description=description,
                    ticket_type=ticket_type,
                )
                session.add(ticket)
                await conversations.set_status(session, conversation_id, "已转人工")
                await session.commit()
                # 刷新数据库默认值，使返回对象可在会话关闭后读取。
                await session.refresh(ticket)
                return ticket
        except IntegrityError as exc:
            if exc.orig.args[0] == 1062:
                raise DuplicateTicketNo from exc
            raise

    return await retry_async(
        create_once,
        attempts=3,
        base_delay=0.05,
        max_delay=0.5,
        retry_on=(DuplicateTicketNo,),
        sleep=sleep,
        rand=rand,
    )
