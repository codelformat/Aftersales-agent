"""主力 Agent 的上下文拼装：固定 System → 层 2 → 层 1 → 用户这句 → 参考资料 → 本轮循环。"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.context import count_tokens
from app.context.layers import render_layer2, split_layers
from app.prompts import render_agent_system

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentPrompt:
    messages: list[BaseMessage]
    layer2: int
    layer1: int
    summary: str | None


def build_agent_prompt(*, history: Sequence[BaseMessage], summary_upto: int | None, layer1_from: int | None,
                       summary: str | None, user_input: str, reference: str,
                       agent_messages: Sequence[BaseMessage]) -> AgentPrompt:
    layers = split_layers(history, summary_upto, layer1_from)
    layer2 = render_layer2(layers.layer2)
    messages = [SystemMessage(render_agent_system()), *layer2, *layers.layer1,
                HumanMessage(user_input), HumanMessage(reference), *agent_messages]
    return AgentPrompt(messages, len(layer2), len(layers.layer1), summary)


def _line(tag: str, m: BaseMessage) -> str:
    if isinstance(m, HumanMessage):
        role = "user"
    elif isinstance(m, ToolMessage):
        role = "tool"
    elif isinstance(m, AIMessage) and m.tool_calls:
        role = "assistant(tool_calls=" + ",".join(c["name"] for c in m.tool_calls) + ")"
    else:
        role = "assistant"
    return f"  [{tag}] {role}: {m.content}"


def log_model_ctx(conversation_id: int, step: int, prompt: AgentPrompt) -> None:
    window = prompt.messages[1:1 + prompt.layer2 + prompt.layer1]
    lines = [_line("L2" if i < prompt.layer2 else "L1", m) for i, m in enumerate(window)]
    logger.info("model_ctx conversation=%s step=%s window=%s tokens≈%s summary=%s\n%s",
                conversation_id, step, len(window), count_tokens(prompt.messages), prompt.summary or "-",
                "\n".join(lines) or "  （无）")
