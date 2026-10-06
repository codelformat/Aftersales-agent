from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import FAQ_MAX_RESULTS
from app.db.engine import get_sessionmaker
from app.repositories import faq


class QueryFaqArgs(BaseModel):
    keyword: str = Field(min_length=1, max_length=20, description="取用户原话中的关键词，不要替换为同义词")


@tool("query_faq", args_schema=QueryFaqArgs)
async def query_faq(keyword: str) -> dict:
    """按关键词查询常见问题，例如退货政策、运费、发票、账户、支付。"""
    async with get_sessionmaker()() as s:
        rows = await faq.search(s, keyword, FAQ_MAX_RESULTS)
        return {
            "results": [
                {"question": row.question, "answer": row.answer, "category": row.category}
                for row in rows
            ]
        }
