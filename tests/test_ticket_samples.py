import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

SAMPLES_PATH = Path(__file__).resolve().parent.parent / "evals" / "ticket_samples.jsonl"


def test_ticket_samples_shape():
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(samples) == 13
    assert len({sample["id"] for sample in samples}) == 13
    assert {sample["expect"] for sample in samples} == {"ask", "call"}
    for sample in samples:
        assert {"id", "history", "user", "expect"} <= set(sample)
        assert set(sample) <= {"id", "history", "user", "expect", "ticket_type", "must_include"}
        assert isinstance(sample["id"], str) and sample["id"].strip()
        assert isinstance(sample["user"], str) and sample["user"].strip()
        assert sample["expect"] in {"ask", "call"}
        assert isinstance(sample["history"], list)
        for message in sample["history"]:
            assert isinstance(message, list) and len(message) == 2
            role, text = message
            assert role in {"user", "assistant"}
            assert isinstance(text, str) and text.strip()
        if sample["expect"] == "call":
            assert sample["ticket_type"] in {"售后", "投诉", "咨询"}
            assert isinstance(sample["must_include"], list) and sample["must_include"]
            assert all(isinstance(term, str) and term.strip() for term in sample["must_include"])


def ticket_call(description="耳机左耳没声音", ticket_type="售后", call_id="ticket-1"):
    return {"name": "create_ticket", "args": {"ticket_type": ticket_type, "description": description},
            "id": call_id, "type": "tool_call"}


@pytest.mark.parametrize("message, expected", [
    (AIMessage("请描述一下遇到的问题。"), True),
    (AIMessage(""), False),
    (AIMessage("  \n"), False),
    (AIMessage(content=[{"type": "text", "text": "请描述问题。"}]), True),
    (AIMessage(content=[{"type": "reasoning", "reasoning": "需要追问"}]), False),
    (AIMessage(content="请描述问题。", tool_calls=[ticket_call()]), False),
    (AIMessage(content="请描述问题。", tool_calls=[
        {"name": "query_order", "args": {"order_id": "1001"}, "id": "order-1", "type": "tool_call"},
    ]), True),
])
def test_judge_ask_requires_text_without_ticket_call(message, expected):
    from evals.run_ticket_eval import judge

    sample = {"id": "ask", "history": [], "user": "帮我建个工单", "expect": "ask"}
    ok, reason = judge(sample, message)
    assert ok is expected
    assert isinstance(reason, str)
    if not ok:
        assert reason.strip()


@pytest.mark.parametrize("calls, expected", [
    ([ticket_call()], True),
    ([], False),
    ([ticket_call(), ticket_call(call_id="ticket-2")], False),
    ([ticket_call(ticket_type="投诉")], False),
    ([ticket_call(description="耳机没有声音")], False),
    ([ticket_call(description="左耳没有声音")], False),
    ([ticket_call(description="耳机左耳没声音，订单9999")], False),
    ([{"name": "create_ticket", "args": {"ticket_type": "售后"}, "id": "ticket-1"}], False),
    ([ticket_call(description=1234)], False),
])
def test_judge_call_checks_count_type_facts_and_invented_numbers(calls, expected):
    from evals.run_ticket_eval import judge

    sample = {"id": "call", "history": [], "user": "帮我建工单，耳机左耳没声音", "expect": "call",
              "ticket_type": "售后", "must_include": ["耳机", "左耳"]}
    ok, reason = judge(sample, AIMessage(content="", tool_calls=calls))
    assert ok is expected
    assert isinstance(reason, str)
    if not ok:
        assert reason.strip()


@pytest.mark.parametrize("description, expected", [
    ("用户要求建工单", False),
    ("要求建工单处理。", False),
    ("用户希望创建一个售后工单跟进。", False),
    ("要求建工单，耳机左耳没声音", True),
    ("故障", False),
    ("左耳坏， 。\t\n", False),
    ("    ", False),
    ("耳机故障", True),
    ("耳 机，故 障。", True),
])
def test_judge_rejects_empty_ticket_descriptions(description, expected):
    from evals.run_ticket_eval import judge

    sample = {"history": [], "user": "耳机坏了，帮我建工单", "expect": "call",
              "ticket_type": "售后", "must_include": []}
    ok, reason = judge(sample, AIMessage(content="", tool_calls=[ticket_call(description)]))
    assert ok is expected
    if not ok:
        assert reason == "问题描述为空话或过短"


@pytest.mark.parametrize("description, ticket_type, must_include", [
    ("用户反馈蓝牙耳机左耳没有声音，购买约一周，要求建工单处理。", "售后", ["耳机", "左耳"]),
    ("用户咨询发票如何开具，希望建工单跟进。", "咨询", ["发票"]),
])
def test_judge_accepts_facts_with_ticket_request(description, ticket_type, must_include):
    from evals.run_ticket_eval import judge

    sample = {"history": [], "user": description, "expect": "call",
              "ticket_type": ticket_type, "must_include": must_include}
    assert judge(sample, AIMessage(content="", tool_calls=[ticket_call(description, ticket_type)])) == (True, "")


@pytest.mark.parametrize("description, expected", [
    ("订单1001耳机故障，实付1299元", True),
    ("订单1001耳机故障，实付9999元", False),
    ("订单1001耳机故障，实付12999元", False),
    ("订单1001耳机故障，金额1299，编号10011299", False),
    ("订单1001耳机故障，编号1234", True),
    ("订单1001耳机故障，编号5678", True),
])
def test_judge_numbers_must_appear_in_user_or_history(description, expected):
    from evals.run_ticket_eval import judge

    sample = {"history": [["user", "耳机实付1299元，商品编号1234"], ["assistant", "收到，参考编号5678"]],
              "user": "订单1001的耳机坏了，帮我建工单", "expect": "call",
              "ticket_type": "售后", "must_include": ["1001", "耳机"]}
    ok, reason = judge(sample, AIMessage(content="", tool_calls=[ticket_call(description)]))
    assert ok is expected
    if not ok:
        assert reason.strip()
