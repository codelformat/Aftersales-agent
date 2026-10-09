from datetime import date

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools import mock_data

from app.tools.registry import register


class QueryOrderArgs(BaseModel):
    order_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$", description="订单号")


@register()
@tool("query_order", args_schema=QueryOrderArgs)
async def query_order(order_id: str) -> dict:
    """按订单号查询订单状态、商品和金额。"""
    return mock_data.order(order_id, date.today())
