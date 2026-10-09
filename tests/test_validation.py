from app.tools.registry import builtin_registry
from app.tools.validation import validate_args


def schema(name):
    return builtin_registry()[name].parameters


def test_missing_required():
    assert validate_args(schema("create_ticket"), {"ticket_type": "售后"}) == ["缺少必填参数 description"]


def test_enum_and_pattern_and_length():
    errs = validate_args(schema("create_ticket"), {"description": "", "ticket_type": "退货"})
    assert "ticket_type 只能是 售后/投诉/咨询" in errs
    assert "description 长度不能少于 1 个字" in errs
    assert validate_args(schema("query_order"), {"order_id": "订单一"}) == ["order_id 格式不对"]


def test_type():
    assert validate_args(schema("query_order"), {"order_id": 1001}) == ["order_id 类型应为字符串"]


def test_not_object():
    assert validate_args(schema("query_order"), "1001") == ["参数必须是 JSON 对象"]


def test_ok():
    assert validate_args(schema("query_order"), {"order_id": "1001"}) == []
