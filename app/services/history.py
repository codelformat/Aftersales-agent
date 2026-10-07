from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.db.models import Message
from app.repositories.messages import NewMessage


def to_langchain(rows: Sequence[Message]) -> list[BaseMessage]:
    """按记录顺序转换消息，保留工具调用关系。"""
    messages: list[BaseMessage] = []
    for row in rows:
        if row.role == "user":
            messages.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            messages.append(
                AIMessage(content=row.content or "", tool_calls=row.tool_calls or [])
            )
        elif row.role == "tool":
            messages.append(
                ToolMessage(content=row.content, tool_call_id=row.tool_call_id)
            )
        else:
            raise ValueError("不支持的消息角色")
    return messages


def turn_rows(
    user_input: str,
    final_text: str,
    tool_request: AIMessage | None = None,
    tool_messages: Sequence[ToolMessage] = (),
) -> list[NewMessage]:
    """生成一轮对话的记录，工具请求和结果放在最终回复之前。"""
    rows = [NewMessage(role="user", content=user_input)]
    if tool_request is not None:
        rows.append(
            NewMessage(
                role="assistant",
                content=tool_request.content or None,
                tool_calls=[
                    {"id": call["id"], "name": call["name"], "args": call["args"]}
                    for call in tool_request.tool_calls
                ],
            )
        )
        rows.extend(
            NewMessage(
                role="tool", content=message.content, tool_call_id=message.tool_call_id
            )
            for message in tool_messages
        )
    rows.append(NewMessage(role="assistant", content=final_text))
    return rows


def turn_messages_rows(user_input: str, turn: Sequence[BaseMessage]) -> list[NewMessage]:
    """一轮的记录：用户消息，然后按顺序写 AI 工具请求、工具结果和最终回复。"""
    rows = [NewMessage(role="user", content=user_input)]
    for message in turn:
        if isinstance(message, ToolMessage):
            rows.append(NewMessage(role="tool", content=message.content, tool_call_id=message.tool_call_id))
        elif message.tool_calls:
            rows.append(NewMessage(
                role="assistant",
                content=message.content or None,
                tool_calls=[{"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls],
            ))
        else:
            rows.append(NewMessage(role="assistant", content=message.content))
    return rows
