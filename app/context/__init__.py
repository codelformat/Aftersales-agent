from collections.abc import Sequence

from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import count_tokens_approximately

from app.config import CHARS_PER_TOKEN


def count_tokens(messages: Sequence[BaseMessage]) -> int:
    return count_tokens_approximately(messages, chars_per_token=CHARS_PER_TOKEN)
