"""Langfuse 链路追踪。编译图时挂一次回调；未配置时不挂。"""

import logging
import os
from urllib.parse import urlparse
from uuid import UUID

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
TRACE_NAME = "chat_turn"
INTENT_NODE = "classify_intent"
INTENT_ATTRIBUTE = "langfuse.trace.metadata.intent"
_LOOPBACK = ("127.0.0.1", "localhost")
_client: Langfuse | None = None


class IntentCallbackHandler(CallbackHandler):
    """classify_intent 结束时，把意图写到根 observation 的元数据和 trace 元数据。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._intent_runs: set[UUID] = set()

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None,
                       **kwargs):
        if (parent_run_id is not None and kwargs.get("name") == INTENT_NODE
                and (metadata or {}).get("langgraph_node") == INTENT_NODE):
            self._intent_runs.add(run_id)
        return super().on_chain_start(serialized, inputs, run_id=run_id, parent_run_id=parent_run_id,
                                      tags=tags, metadata=metadata, **kwargs)

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        if run_id in self._intent_runs:
            self._intent_runs.discard(run_id)
            try:
                self._tag_intent(run_id, outputs)
            except Exception:
                # 打标失败只影响统计，不影响本轮。
                logger.exception("langfuse_intent_tag_failed")
        return super().on_chain_end(outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs)

    def _tag_intent(self, run_id: UUID, outputs) -> None:
        intent = outputs.get("intent") if isinstance(outputs, dict) else None
        state = self._run_states.get(run_id)
        root = self._runs.get(state.root_run_id) if state is not None else None
        if root is None:
            return
        value = intent or "-"
        root.update(metadata={"intent": value})
        root._otel_span.set_attribute(INTENT_ATTRIBUTE, value)


def _bypass_proxy(base_url: str) -> None:
    """本机 shell 有 HTTP_PROXY 没有 NO_PROXY。OTLP 导出访问本机 Langfuse 时绕过代理。"""
    if urlparse(base_url).hostname not in _LOOPBACK:
        return
    for var in ("NO_PROXY", "no_proxy"):
        hosts = [h for h in os.environ.get(var, "").split(",") if h]
        os.environ[var] = ",".join(dict.fromkeys([*hosts, *_LOOPBACK]))


def get_langfuse_handler(settings: Settings | None = None) -> IntentCallbackHandler | None:
    global _client
    s = settings or get_settings()
    secret = s.langfuse_secret_key.get_secret_value() if s.langfuse_secret_key else None
    if not (s.langfuse_public_key and secret and s.langfuse_base_url):
        logger.info("langfuse=off")
        return None
    _bypass_proxy(s.langfuse_base_url)
    _client = Langfuse(public_key=s.langfuse_public_key, secret_key=secret, base_url=s.langfuse_base_url)
    logger.info("langfuse=on base_url=%s", s.langfuse_base_url)
    return IntentCallbackHandler(public_key=s.langfuse_public_key)


def shutdown_langfuse() -> None:
    global _client
    if _client is None:
        return
    try:
        _client.flush()
        _client.shutdown()
    except Exception:
        logger.exception("langfuse_shutdown_failed")
    finally:
        _client = None
