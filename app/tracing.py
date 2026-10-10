"""本轮 LLM 用量回调；通过线程安全队列交给 SSE 层。"""

import asyncio
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult


class UsageCollector(BaseCallbackHandler):
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.queue = queue
        self.loop = loop
        self._runs: dict[UUID, tuple[str, str, float]] = {}

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *,
        run_id: UUID, metadata: dict[str, Any] | None = None, **kwargs: Any,
    ) -> None:
        metadata = metadata or {}
        params = kwargs.get("invocation_params") or {}
        model = (metadata.get("ls_model_name") or params.get("model_name") or params.get("model")
                 or serialized.get("name") or "unknown")
        self._runs[run_id] = (metadata.get("langgraph_node") or "unknown", model, time.monotonic())

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        node, model, started_at = run
        output = response.llm_output or {}
        message = next((generation.message for batch in response.generations for generation in batch
                        if getattr(generation, "message", None) is not None), None)
        usage = getattr(message, "usage_metadata", None)
        response_metadata = getattr(message, "response_metadata", {})
        model = response_metadata.get("model_name") or output.get("model_name") or model
        if usage is not None:
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_read = (usage.get("input_token_details") or {}).get("cache_read", 0)
            reasoning = (usage.get("output_token_details") or {}).get("reasoning", 0)
        else:
            usage = output.get("token_usage") or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            cache_read = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        event = {"kind": "llm", "node": node, "data": {
            "node": node, "model": model, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cache_read_tokens": cache_read,
            "reasoning_tokens": reasoning, "ms": int((time.monotonic() - started_at) * 1000),
        }}
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
