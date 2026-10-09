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


MSG_ID_PREFIX = "msg-"


def msg_id(n: int) -> str:
    return f"{MSG_ID_PREFIX}{n}"


def db_id(message: BaseMessage) -> int | None:
    """返回 State 消息对应的数据库主键，没有有效 id 时返回 None。"""
    mid = message.id or ""
    if mid.startswith(MSG_ID_PREFIX) and mid[len(MSG_ID_PREFIX):].isdecimal():
        return int(mid[len(MSG_ID_PREFIX):])
    return None


def final_rows(user_input: str, reply: str) -> list[NewMessage]:
    """一轮只写用户消息和最终回复，工具调用和结果留在 State。"""
    return [NewMessage(role="user", content=user_input), NewMessage(role="assistant", content=reply)]
