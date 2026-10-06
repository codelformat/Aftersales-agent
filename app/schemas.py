from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.config import MAX_INPUT_CHARS

UserText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_INPUT_CHARS)]
SessionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class RequestType(str, Enum):
    RETURN = "退货"
    EXCHANGE = "换货"
    REFUND = "退款"
    REPAIR = "维修"
    COMPLAINT = "投诉"
    INQUIRY = "咨询"


class AfterSalesRequest(BaseModel):
    """从用户的售后描述中提取的结构化信息。"""

    order_id: str | None = Field(None, description="原文中出现的订单号，保持原样；原文没有则为 null，不许编造")
    request_type: RequestType = Field(description="诉求类型，按系统提示中的判定规则选择")
    expected_solution: str = Field(description="用户想要的结果，不复述问题；用户没说明时写“未说明”")


class ChatRequest(BaseModel):
    session_id: SessionId | None = None
    message: UserText


class ExtractRequest(BaseModel):
    text: UserText
