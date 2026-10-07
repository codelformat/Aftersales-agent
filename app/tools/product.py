from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools import mock_data


class QueryProductArgs(BaseModel):
    product_id: str = Field(
        pattern=r"^P\d{3,8}$",
        description="商品号，格式为 P 加数字，例如 P001。不是商品型号；X3、S10 Max 这类型号的参数用 query_faq 查询",
    )


@tool("query_product", args_schema=QueryProductArgs)
async def query_product(product_id: str) -> dict:
    """按商品号（例如 P001）查询价格、库存、保修天数和是否支持 7 天无理由退货。不能按型号查询。"""
    return mock_data.product(product_id)
