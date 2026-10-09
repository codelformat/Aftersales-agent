import uuid
from types import SimpleNamespace

import pytest

from app import observability
from app.config import Settings
from app.graph.builder import build_graph, thread_config

# 模块导入时 autouse fixture _no_langfuse 尚未替换，这里保存原函数。
REAL_GET_HANDLER = observability.get_langfuse_handler


def _settings(**kw):
    base = dict(chat_base_url="http://x", chat_model="m", chat_api_key="k", database_url="mysql+asyncmy://a@b/c",
                embed_api_key="e", milvus_uri="http://m", rerank_api_key="r",
                langfuse_public_key=None, langfuse_secret_key=None, langfuse_base_url=None)
    return Settings(_env_file=None, **{**base, **kw})


def test_handler_off_without_config(caplog):
    caplog.set_level("INFO")
    assert REAL_GET_HANDLER(_settings()) is None
    assert "langfuse=off" in caplog.text


def test_thread_config_metadata():
    cfg = thread_config(7, "u1")
    assert cfg["configurable"] == {"thread_id": "7"}
    assert cfg["metadata"] == {"langfuse_session_id": "7", "langfuse_trace_name": "chat_turn",
                               "langfuse_user_id": "u1"}
    assert "langfuse_user_id" not in thread_config(7)["metadata"]


def test_thread_config_carries_resume_intent():
    cfg = thread_config(7, "u1", intent="售后")
    assert cfg["metadata"]["intent"] == "售后"
    assert cfg["metadata"]["langfuse_user_id"] == "u1"
    assert "intent" not in thread_config(7, "u1")["metadata"]


def test_build_graph_attaches_callbacks():
    from langgraph.checkpoint.memory import InMemorySaver
    marker = object()
    g = build_graph(InMemorySaver(), callbacks=[marker])
    assert g.config["callbacks"] == [marker]
    assert "callbacks" not in (build_graph(InMemorySaver()).config or {})


class FakeRoot:
    def __init__(self):
        self.metadata = None
        self.attrs = {}
        self._otel_span = SimpleNamespace(set_attribute=lambda k, v: self.attrs.__setitem__(k, v))

    def update(self, **kw):
        self.metadata = kw.get("metadata")


def _run_intent_node(handler, outputs):
    root, node = uuid.uuid4(), uuid.uuid4()
    handler.on_chain_start({}, {}, run_id=root, parent_run_id=None, metadata={}, name="LangGraph")
    fake = FakeRoot()
    handler._runs[root] = fake
    handler.on_chain_start({}, {}, run_id=node, parent_run_id=root,
                           metadata={"langgraph_node": "classify_intent"}, name="classify_intent")
    handler.on_chain_end(outputs, run_id=node, parent_run_id=root)
    return fake


def test_intent_written_to_root_and_trace():
    handler = observability.IntentCallbackHandler()
    fake = _run_intent_node(handler, {"intent": "物流", "route": "business"})
    assert fake.metadata == {"intent": "物流"}
    assert fake.attrs == {"langfuse.trace.metadata.intent": "物流"}


def test_missing_intent_written_as_dash():
    fake = _run_intent_node(observability.IntentCallbackHandler(), {"intent": None})
    assert fake.metadata == {"intent": "-"}


def test_tag_intent_failure_does_not_break_run(monkeypatch, caplog):
    handler = observability.IntentCallbackHandler()

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(handler, "_tag_intent", boom)
    _run_intent_node(handler, {"intent": "物流"})
    assert "langfuse_intent_tag_failed" in caplog.text


def test_no_proxy_added_for_loopback(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("no_proxy", "example.com")
    observability._bypass_proxy("http://127.0.0.1:3100")
    import os
    assert set(os.environ["NO_PROXY"].split(",")) >= {"127.0.0.1", "localhost"}
    assert set(os.environ["no_proxy"].split(",")) >= {"example.com", "127.0.0.1", "localhost"}


@pytest.mark.anyio
async def test_graph_runs_with_unreachable_langfuse(db, client, use_script, use_intent):
    """Langfuse 不可达时，带回调的图照常完成一轮。"""
    from langgraph.checkpoint.memory import InMemorySaver
    from app.graph.builder import set_graph
    handler = REAL_GET_HANDLER(_settings(
        langfuse_public_key="pk-lf-test", langfuse_secret_key="sk-lf-test",
        langfuse_base_url="http://127.0.0.1:9"))
    assert handler is not None
    set_graph(build_graph(InMemorySaver(), callbacks=[handler]))  # client 的 memory_graph 在结束时置回 None
    use_script()
    use_intent("闲聊")
    try:
        r = await client.post("/chat/stream", json={"user_id": "u1", "message": "你好"})
        assert "event: done" in r.text and "upstream_error" not in r.text
    finally:
        observability.shutdown_langfuse()
