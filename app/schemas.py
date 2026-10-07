from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.config import MAX_INPUT_CHARS, MINED_CATEGORIES, PRODUCT_CATEGORIES

UserText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_INPUT_CHARS)]
SessionId = Annotated[str, StringConstraints(pattern=r"^\d{1,19}$")]
UserId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


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
    user_id: UserId
    message: UserText


class ExtractRequest(BaseModel):
    text: UserText


class QaPair(BaseModel):
    question: str = Field(description="用户的真实问法，去掉订单号等个人信息")
    answer: str = Field(description="客服在对话中给出的答案，不补充、不编造")


class QaPairs(BaseModel):
    """从一通历史对话中抽出的可复用问答对。"""

    pairs: list[QaPair] = Field(description="问答对列表；没有可抽的内容时为空列表")


MinedCategory = Literal[MINED_CATEGORIES]


class DedupVerdict(BaseModel):
    """去重裁定结果。"""

    duplicate_of: int | None = Field(description="重复时填候选序号（从 1 开始）；不重复时为 null")
    category: MinedCategory = Field(description="新问答对的分类")


class QueryPlan(BaseModel):
    """知识库检索的标准问法和商品品类。"""

    standard_query: str = Field(min_length=1, description="改写后的标准问法，保留型号、数字和限定条件")
    product_category: Literal[PRODUCT_CATEGORIES] | None = Field(
        default=None, description="用户明确提到的商品品类；没有提到时为 null"
    )


class SelfCheck(BaseModel):
    useful: bool = Field(description="证据是否足以回答问题的全部要点")
    reason: str = Field(description="够用时写依据的证据编号；不够用时写缺了什么")


class FaithVerdict(BaseModel):
    faithful: bool = Field(description="unsupported_claims 为空时为 true")
    unsupported_claims: list[str] = Field(default_factory=list, description="证据中找不到依据的句子，原样摘录")
    reason: str = Field(description="一句话说明判定依据")
