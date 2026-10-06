from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, text
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ID = BIGINT(unsigned=True)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        Enum("进行中", "已转人工", "已结束"), server_default=text("'进行中'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ID, ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(Enum("user", "assistant", "tool"))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


class Faq(Base):
    __tablename__ = "faq"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(512))
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))


class Ticket(Base):
    __tablename__ = "tickets"

    ticket_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ID, ForeignKey("conversations.id"))
    description: Mapped[str] = mapped_column(Text)
    ticket_type: Mapped[str] = mapped_column(Enum("售后", "投诉", "咨询"))
    status: Mapped[str] = mapped_column(Enum("待处理", "已处理"), server_default=text("'待处理'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
