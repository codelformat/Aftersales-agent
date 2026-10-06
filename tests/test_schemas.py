import pytest
from pydantic import ValidationError

from app.schemas import AfterSalesRequest, ChatRequest, ExtractRequest, RequestType


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
