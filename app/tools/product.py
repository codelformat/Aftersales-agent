from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools import mock_data


class QueryProductArgs(BaseModel):
    product_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$", description="商品号，例如 P001")


@tool("query_product", args_schema=QueryProductArgs)
async def query_product(product_id: str) -> dict:
    """按商品号查询价格、库存、保修天数和是否支持 7 天无理由退货。"""
    return mock_data.product(product_id)
