"""测试用的脚本化聊天模型。"""

import asyncio
import json
import math
import random
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk

from app.config import EMBED_DIM
from app.knowledge.milvus import Entity


def unit(i: int) -> list[float]:
    """第 i 维为 1 的单位向量。"""
    v = [0.0] * EMBED_DIM
    v[i] = 1.0
    return v


def blend(i: int, j: int, cos: float) -> list[float]:
    """和 unit(i) 的余弦相似度为 cos 的单位向量。"""
    v = [0.0] * EMBED_DIM
    v[i] = cos
    v[j] = math.sqrt(1 - cos * cos)
    return v


class FakeEmbeddings(Embeddings):
    """确定性假嵌入。按 rules 顺序匹配子串，返回首个匹配向量。

    未匹配时，按文本生成随机单位向量。calls 记录每次调用的文本列表。
    """

    def __init__(self, rules: list[tuple[str, list[float]]] | None = None):
        self.rules = rules or []
        self.calls: list[list[str]] = []

    def _vec(self, text: str) -> list[float]:
        for sub, v in self.rules:
            if sub in text:
                return v
        rng = random.Random(text)
        v = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.calls.append([text])
        return self._vec(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


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


def entity(id: int, vector: list[float], text: str = "t", category: str = "通用",
           content_type: str = "policy") -> Entity:
    return Entity(id=id, vector=vector, text=text, product_category=category, content_type=content_type)
