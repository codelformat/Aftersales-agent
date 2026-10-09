"""演示用：查询会员积分。复制到 app/tools/builtin/ 后重启服务即可使用。"""

import random

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools.registry import register


class QueryMemberPointsArgs(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$", description="会员手机号，11 位")


@register()
@tool("query_member_points", args_schema=QueryMemberPointsArgs)
async def query_member_points(phone: str) -> dict:
    """按会员手机号查询当前积分和即将过期的积分。"""
    rng = random.Random(f"points:{phone}")
    return {"phone": phone, "points": rng.randint(100, 5000), "expiring": rng.randint(0, 300)}
