from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationSummary


async def list_for_conversation(session: AsyncSession, conversation_id: int) -> list[ConversationSummary]:
    rows = await session.scalars(select(ConversationSummary)
                                 .where(ConversationSummary.conversation_id == conversation_id)
                                 .order_by(ConversationSummary.seq))
    return list(rows)


async def append(session: AsyncSession, conversation_id: int, from_id: int, upto_id: int, content: str) -> int:
    current = await session.scalar(select(func.max(ConversationSummary.seq))
                                   .where(ConversationSummary.conversation_id == conversation_id))
    seq = (current or 0) + 1
    session.add(ConversationSummary(conversation_id=conversation_id, seq=seq, from_msg_id=from_id,
                                    upto_msg_id=upto_id, content=content))
    await session.flush()
    return seq
