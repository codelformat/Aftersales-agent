from datetime import date
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolArg, tool
from pydantic import BaseModel, Field

from app.db.engine import get_sessionmaker
from app.repositories import tickets

from app.tools.registry import register


class CreateTicketArgs(BaseModel):
    description: str = Field(min_length=1, max_length=500, description="问题描述，概括用户的诉求")
    ticket_type: Literal["售后", "投诉", "咨询"] = Field(description="工单类型")
    conversation_id: Annotated[int, InjectedToolArg]


@register(permission="write", inject_conversation_id=True)
@tool("create_ticket", args_schema=CreateTicketArgs)
async def create_ticket(description: str, ticket_type: str, conversation_id: Annotated[int, InjectedToolArg]) -> dict:
    """创建人工工单。用户明确要求人工，或投诉需要人工跟进时调用。"""
    ticket = await tickets.create_ticket_record(
        get_sessionmaker(), conversation_id, description, ticket_type, date.today()
    )
    return {"ticket_no": ticket.ticket_no, "status": ticket.status}
