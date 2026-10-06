import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

from app.context import BudgetExceeded, build_history, count_tokens

SYSTEM = "系统提示"


def _history(n_rounds, size=50):
    msgs = []
    for i in range(n_rounds):
        msgs.append(HumanMessage(f"问{i}" * size))
        msgs.append(AIMessage(f"答{i}" * size))
    return msgs


def _fixed(user_input):
    return count_tokens([SystemMessage(SYSTEM), HumanMessage(user_input)])


def test_keeps_all_history_under_budget():
    h = _history(2)
    out = build_history(h, SYSTEM, "新问题", budget=10_000)
    assert out == h


def test_drops_oldest_when_over_budget():
    h = _history(3)
    budget = _fixed("新问题") + count_tokens(h[2:]) + 1
    out = build_history(h, SYSTEM, "新问题", budget=budget)
    assert out == h[2:]


def test_trimmed_history_starts_with_human():
    h = _history(3)
    # 预算只够最后一条 AI 消息，start_on="human" 应丢弃它。
    budget = _fixed("新问题") + count_tokens(h[-1:]) + 1
    out = build_history(h, SYSTEM, "新问题", budget=budget)
    assert out == [] or isinstance(out[0], HumanMessage)


def test_fixed_part_over_budget_raises():
    with pytest.raises(BudgetExceeded):
        build_history([], SYSTEM, "字" * 500, budget=10)


def test_count_tokens_uses_chars_per_token():
    msgs = [HumanMessage("字" * 100)]
    assert count_tokens(msgs) == count_tokens_approximately(msgs, chars_per_token=2.0)
    # 实测值（langchain-core 1.6.6）：55。默认 4.0 字符/token 时明显更小。
    assert count_tokens(msgs) > count_tokens_approximately(msgs)
