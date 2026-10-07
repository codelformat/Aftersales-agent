from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.tools.executor import execute_tool_calls


class Action(TypedDict, total=False):
    type: Literal["handoff", "ticket"]
    description: str
    ticket_type: str


class ChatState(TypedDict, total=False):
    # 跨轮字段：checkpointer 持久化，只由 finalize 和 POST /tickets 追加。
    messages: Annotated[list[AnyMessage], add_messages]
    # 本轮字段：start_turn 每轮重置，节点整体覆盖。
    user_input: str
    resolved_input: str
    intent: str | None
    route: str
    evidence: list[dict]
    gate: dict | None
    agent_messages: list[AnyMessage]
    steps: int
    tokens_used: int
    force_final: bool
    reply: str
    actions: list[Action]
    trace: list[str]


@dataclass
class GraphContext:
    """每轮的运行时依赖，不持久化。"""

    conversation_id: int
    today: date
    model: BaseChatModel
    execute: Callable[..., Any] = execute_tool_calls
