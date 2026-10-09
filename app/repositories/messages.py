from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message


@dataclass
class NewMessage:
    role: str
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


async def list_for_conversation(
    session: AsyncSession, conversation_id: int
) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(rows)


async def add_turn(
    session: AsyncSession, conversation_id: int, rows: list[NewMessage]
) -> list[Message]:
    objs = [
        Message(
            conversation_id=conversation_id,
            role=row.role,
            content=row.content,
            tool_calls=row.tool_calls,
            tool_call_id=row.tool_call_id,
        )
        for row in rows
    ]
    session.add_all(objs)
    await session.flush()
    return objs
