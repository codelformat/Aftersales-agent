from datetime import date

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools import mock_data


class QueryLogisticsArgs(BaseModel):
    order_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$", description="订单号")


@tool("query_logistics", args_schema=QueryLogisticsArgs)
async def query_logistics(order_id: str) -> dict:
    """按订单号查询承运商、运单号、物流状态、轨迹和预计送达时间。"""
    return mock_data.logistics(order_id, date.today())
