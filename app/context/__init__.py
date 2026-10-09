from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages

from app.config import CHARS_PER_TOKEN


class BudgetExceeded(Exception):
    """系统提示加当前消息已超出预算。"""


def count_tokens(messages: Sequence[BaseMessage]) -> int:
    return count_tokens_approximately(messages, chars_per_token=CHARS_PER_TOKEN)


def build_history(
    history: Sequence[BaseMessage], system_text: str, user_input: str, budget: int
) -> list[BaseMessage]:
    fixed = count_tokens([SystemMessage(system_text), HumanMessage(user_input)])
    if fixed > budget:
        raise BudgetExceeded
    return trim_messages(
        list(history),
        max_tokens=budget - fixed,
        strategy="last",
        token_counter=count_tokens,
        start_on="human",
    )
