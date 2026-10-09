from dataclasses import dataclass

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation


@dataclass(frozen=True)
class ContextAnchors:
    summary: str | None
    summary_upto: int | None
    layer1_from: int | None


async def get_context(session: AsyncSession, conversation_id: int) -> ContextAnchors:
    row = (await session.execute(
        select(Conversation.summary, Conversation.summary_upto_msg_id, Conversation.layer1_from_msg_id)
        .where(Conversation.id == conversation_id))).first()
    return ContextAnchors(*row) if row else ContextAnchors(None, None, None)


async def create(session: AsyncSession, user_id: str) -> Conversation:
    conversation = Conversation(user_id=user_id)
    session.add(conversation)
    await session.flush()
    return conversation


async def get_for_user(
    session: AsyncSession, conversation_id: int, user_id: str
) -> Conversation | None:
    return await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )


async def set_status(
    session: AsyncSession, conversation_id: int, status: str
) -> None:
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(status=status)
    )


async def set_summary(session: AsyncSession, conversation_id: int, upto: int, projection: str) -> bool:
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id,
               or_(Conversation.summary_upto_msg_id.is_(None), Conversation.summary_upto_msg_id < upto))
        .values(summary_upto_msg_id=upto, summary=projection))
    return result.rowcount > 0


async def advance_layer1(session: AsyncSession, conversation_id: int, new_from: int) -> bool:
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id,
               or_(Conversation.layer1_from_msg_id.is_(None), Conversation.layer1_from_msg_id < new_from))
        .values(layer1_from_msg_id=new_from))
    return result.rowcount > 0
