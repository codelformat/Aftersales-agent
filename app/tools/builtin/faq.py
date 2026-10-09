from dataclasses import asdict

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import QUERY_FAQ_TIMEOUT_SECONDS
from app.knowledge.retrieval import retrieve

from app.tools.registry import register


class QueryFaqArgs(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=200,
        description="用户关于店铺政策、商品型号参数或使用问题的原话，可以去掉订单号等个人信息，不要改写",
    )


@register(timeout=QUERY_FAQ_TIMEOUT_SECONDS, max_retries=0, agent=False)
@tool("query_faq", args_schema=QueryFaqArgs)
async def query_faq(question: str) -> dict:
    """查询店铺知识库，例如退换货政策、运费、发票、维修流程、商品型号的参数和常见故障。"""
    result = await retrieve(question)
    return {"evidence": [asdict(e) for e in result.evidence]}
