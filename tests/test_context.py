from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately

from app.context import count_tokens


def test_count_tokens_uses_chars_per_token():
    msgs = [HumanMessage("字" * 100)]
    assert count_tokens(msgs) == count_tokens_approximately(msgs, chars_per_token=1.5)
    # 默认 4.0 字符/token 时明显更小。
    assert count_tokens(msgs) > count_tokens_approximately(msgs)
