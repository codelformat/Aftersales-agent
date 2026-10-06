"""测试用的脚本化聊天模型。"""

import asyncio
import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk


class Recorder(list):
    """记录每次模型调用的消息、工具名和工具选择策略。"""


class ScriptedChatModel(BaseChatModel):
    """每次调用消费 scripts 中的一个列表。

    列表元素：AIMessageChunk 依次流出；Exception 在该位置抛出；asyncio.Event 在该位置等待。
    """

    scripts: list
    recorder: Any = None
    bound_tools: list = []
    bound_kwargs: dict = {}

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound_tools": list(tools), "bound_kwargs": dict(kwargs)})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("只支持流式调用")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        if self.recorder is not None:
            self.recorder.append({
                "messages": list(messages),
                "tools": [t.name for t in self.bound_tools],
                "tool_choice": self.bound_kwargs.get("tool_choice"),
            })
        for item in self.scripts.pop(0):
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, asyncio.Event):
                await item.wait()
                continue
            yield ChatGenerationChunk(message=item)


def text(s: str) -> list:
    """把文字拆成逐字 chunk。"""
    return [AIMessageChunk(content=ch) for ch in s]


def tools(*calls: tuple[str, str, dict]) -> list:
    """生成一轮 tool_call chunk。每个参数为 (id, name, args)。"""
    return [
        AIMessageChunk(content="", tool_call_chunks=[tool_call_chunk(
            name=name, args=json.dumps(args, ensure_ascii=False), id=cid, index=i)])
        for i, (cid, name, args) in enumerate(calls)
    ]
