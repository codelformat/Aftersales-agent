"""按锚点分层历史消息，并渲染层 2 和指代消解文本。"""

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.config import LAYER2_REPLY_CHARS, LAYER2_TOOL_MAX_CHARS, RESOLVE_MESSAGE_MAX_CHARS
from app.services.history import db_id


def effective_ids(messages: Sequence[BaseMessage]) -> list[int]:
    out: list[int] = []
    current = 0
    for message in messages:
        n = db_id(message)
        if n is not None:
            current = n
        out.append(current)
    return out


@dataclass(frozen=True)
class Layers:
    layer2: list[BaseMessage]
    layer1: list[BaseMessage]
    ids2: list[int]
    ids1: list[int]


def split_layers(
    messages: Sequence[BaseMessage], summary_upto: int | None, layer1_from: int | None
) -> Layers:
    layer2: list[BaseMessage] = []
    layer1: list[BaseMessage] = []
    ids2: list[int] = []
    ids1: list[int] = []
    for message, n in zip(messages, effective_ids(messages)):
        if summary_upto is not None and n <= summary_upto:
            continue
        if layer1_from is not None and n <= layer1_from:
            layer2.append(message)
            ids2.append(n)
        else:
            layer1.append(message)
            ids1.append(n)
    return Layers(layer2, layer1, ids2, ids1)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def render_layer2(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """复制消息，截短客服答复和长工具结果，保留工具配对。"""
    out: list[BaseMessage] = []
    for message in messages:
        rendered = message.model_copy(deep=True)
        if isinstance(rendered, ToolMessage) and len(str(rendered.content)) > LAYER2_TOOL_MAX_CHARS:
            rendered.content = f"〔{rendered.name or '工具'} 结果已省略，约 {len(str(rendered.content))} 字〕"
        elif isinstance(rendered, AIMessage) and not rendered.tool_calls and isinstance(rendered.content, str):
            rendered.content = _cut(rendered.content, LAYER2_REPLY_CHARS)
        out.append(rendered)
    return out


def _dialog_lines(messages: Sequence[BaseMessage], *, layer2: bool, max_chars: int) -> list[str]:
    lines: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            lines.append("用户：" + (message.content if layer2 else message.content[:max_chars]))
        elif isinstance(message, AIMessage) and not message.tool_calls and isinstance(message.content, str) and message.content:
            lines.append("客服：" + (_cut(message.content, LAYER2_REPLY_CHARS) if layer2 else message.content[:max_chars]))
    return lines


def history_lines(
    summary: str | None, layers: Layers, max_chars: int = RESOLVE_MESSAGE_MAX_CHARS
) -> list[str]:
    """按梗概、层 2、层 1 的顺序渲染文本，省略工具消息。"""
    lines = [f"梗概：{summary}"] if summary else []
    lines += _dialog_lines(layers.layer2, layer2=True, max_chars=max_chars)
    lines += _dialog_lines(layers.layer1, layer2=False, max_chars=max_chars)
    return lines
