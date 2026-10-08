import pytest
from pydantic import ValidationError

from app.schemas import (
    INTENTS, REFUND_REASONS, AfterSalesRequest, ChatRequest, ExtractRequest, IntentResult,
    QueryExpansion, RefundRequest, RequestType, ResolvedQuery, ResumeRequest,
)


def test_request_type_values():
    assert [t.value for t in RequestType] == ["退货", "换货", "退款", "维修", "投诉", "咨询"]


def test_after_sales_request_order_id_optional():
    r = AfterSalesRequest(request_type="咨询", expected_solution="未说明")
    assert r.order_id is None


def test_chat_request_strips_and_rejects_blank():
    assert ChatRequest(message="  你好 ", user_id="u1").message == "你好"
    with pytest.raises(ValidationError):
        ChatRequest(message="   ", user_id="u1")


def test_chat_request_limits():
    with pytest.raises(ValidationError):
        ChatRequest(message="字" * 2001, user_id="u1")
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", user_id="u1", session_id="")


def test_extract_request_rejects_blank():
    with pytest.raises(ValidationError):
        ExtractRequest(text=" ")


def test_chat_request_ch02_fields():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi")
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", user_id="u 1")
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", user_id="u1", session_id="abc")
    assert ChatRequest(message="hi", user_id="u1", session_id="12").session_id == "12"


def test_intents_have_other_last():
    assert INTENTS == ("物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊", "其他")


def test_intent_result_confidence_range():
    assert IntentResult(intent="其他", confidence=0.3).confidence == 0.3
    with pytest.raises(ValidationError):
        IntentResult(intent="物流", confidence=1.5)
    with pytest.raises(ValidationError):
        IntentResult(intent="未知", confidence=0.5)


def test_resolved_query_null_like_values():
    r = ResolvedQuery(resolved_input="它能退吗", standard_query="退货条件",
                      product_category="null", order_scoped=False, order_id="")
    assert r.product_category is None and r.order_id is None
    with pytest.raises(ValidationError):
        ResolvedQuery(resolved_input="a", standard_query="b", order_id="10 01")


def test_query_expansion_limits():
    assert QueryExpansion(queries=[" 退货条件 "]).queries == ["退货条件"]
    with pytest.raises(ValidationError):
        QueryExpansion(queries=[])
    with pytest.raises(ValidationError):
        QueryExpansion(queries=["a"] * 5)
    with pytest.raises(ValidationError):
        QueryExpansion(queries=["长" * 101])


def test_refund_request():
    assert REFUND_REASONS == ("七天无理由", "质量问题", "商品与描述不符", "发错货或漏发", "物流损坏", "其他")
    req = RefundRequest(session_id="1", user_id="u1", order_id="1001", reason="质量问题")
    assert req.note is None
    with pytest.raises(ValidationError):
        RefundRequest(session_id="1", user_id="u1", order_id="1001", reason="不想要")
    with pytest.raises(ValidationError):
        RefundRequest(session_id="1", user_id="u1", order_id="1001", reason="其他", note="长" * 201)
    with pytest.raises(ValidationError):
        ResumeRequest(session_id="1", user_id="u1", order_id="10 01")
