import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableGenerator

from app.api.chat import get_token_budget
from app.llm import get_chat_model
from app.main import app

pytestmark = pytest.mark.anyio


def parse_sse(text):
    """把 SSE 文本解析为 [(event, data_dict), ...]。"""
    events = []
    for block in text.strip().split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        events.append((name, data))
    return events


async def post_chat(client, **body):
    return await client.post("/chat/stream", json=body)


async def test_health(client):
    r = await client.get("/health")
    assert r.json() == {"status": "ok"}


async def test_event_order_and_tokens(client, store, use_model):
    use_model(FakeListChatModel(responses=["您好"]))
    r = await post_chat(client, session_id="s1", message="你好")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert events[0] == ("session", {"session_id": "s1"})
    assert events[1:3] == [("token", {"text": "您"}), ("token", {"text": "好"})]
    assert events[-1] == ("done", {"finish_reason": "stop"})


async def test_chinese_not_escaped_in_sse(client, store, use_model):
    use_model(FakeListChatModel(responses=["您好"]))
    r = await post_chat(client, session_id="s1", message="你好")
    assert "\\u" not in r.text
    assert '"text": "您"' in r.text


async def test_generated_session_id(client, store, use_model):
    use_model(FakeListChatModel(responses=["好"]))
    r = await post_chat(client, message="你好")
    sid = parse_sse(r.text)[0][1]["session_id"]
    assert store.get(sid) is not None


async def test_second_turn_sees_first_turn(client, store, use_model):
    rec = use_model(FakeListChatModel(responses=["第一答", "第二答"]))
    await post_chat(client, session_id="s1", message="第一问")
    await post_chat(client, session_id="s1", message="第二问")
    second_input = [(type(m).__name__, m.content) for m in rec.calls[1]]
    assert second_input[1:] == [
        ("HumanMessage", "第一问"), ("AIMessage", "第一答"), ("HumanMessage", "第二问"),
    ]
    assert [m.content for m in store.get("s1").messages] == ["第一问", "第一答", "第二问", "第二答"]


async def test_upstream_error_mid_stream(client, store, use_model):
    use_model(FakeListChatModel(responses=["您好请问"], error_on_chunk_number=2))
    r = await post_chat(client, session_id="s1", message="你好")
    events = parse_sse(r.text)
    assert events[-1] == ("error", {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"})
    assert ("done", {"finish_reason": "stop"}) not in events
    assert store.get("s1").messages == []
    assert not store.get("s1").lock.locked()


async def test_budget_exceeded(client, store, use_model):
    use_model(FakeListChatModel(responses=["x"]))
    app.dependency_overrides[get_token_budget] = lambda: 10
    r = await post_chat(client, session_id="s1", message="你好")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "budget_exceeded"
    assert not store.get("s1").lock.locked()


async def test_session_busy(client, store, use_model):
    use_model(FakeListChatModel(responses=["x"]))
    s = store.get_or_create("s1")
    await s.lock.acquire()
    r = await post_chat(client, session_id="s1", message="你好")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_busy"
    s.lock.release()


async def test_blank_message_rejected(client, store, use_model):
    rec = use_model(FakeListChatModel(responses=["x"]))
    r = await post_chat(client, session_id="s1", message="   ")
    assert r.status_code == 422
    assert rec.calls == []


async def test_empty_session_id_rejected(client, store, use_model):
    use_model(FakeListChatModel(responses=["x"]))
    r = await post_chat(client, session_id="", message="你好")
    assert r.status_code == 422


async def test_message_with_braces(client, store, use_model):
    use_model(FakeListChatModel(responses=["好"]))
    r = await post_chat(client, session_id="s1", message="订单{A1}怎么退")
    assert parse_sse(r.text)[-1][0] == "done"


async def test_empty_chunks_are_skipped(client, store):
    # 模拟思考模式：上游先发送 content 为空的 chunk。
    # RunnableGenerator 的函数接收输入的异步迭代器（langchain-core 1.6.6 实测）。
    async def gen(inputs):
        async for _ in inputs:
            pass
        for text in ["", "", "好", ""]:
            yield AIMessageChunk(content=text)

    app.dependency_overrides[get_chat_model] = lambda: RunnableGenerator(gen)
    r = await post_chat(client, session_id="s1", message="你好")
    tokens = [d for e, d in parse_sse(r.text) if e == "token"]
    assert tokens == [{"text": "好"}]


async def test_lock_released_when_model_dependency_fails(client, store):
    # 回归：预检获取锁后，后续依赖出错，锁也必须释放。
    def boom():
        raise RuntimeError("config error")

    app.dependency_overrides[get_chat_model] = boom
    with pytest.raises(RuntimeError):
        await post_chat(client, session_id="s1", message="你好")
    assert not store.get("s1").lock.locked()


async def test_lock_held_during_stream_and_released_after(client, store):
    # 回归：锁必须覆盖整个流，流结束后由依赖释放。
    release = asyncio.Event()

    async def gen(inputs):
        async for _ in inputs:
            pass
        yield AIMessageChunk(content="a")
        await release.wait()
        yield AIMessageChunk(content="b")

    app.dependency_overrides[get_chat_model] = lambda: RunnableGenerator(gen)
    first = asyncio.create_task(post_chat(client, session_id="s1", message="hi"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        session = store.get("s1")
        if session is not None and session.lock.locked():
            break
    assert store.get("s1").lock.locked()
    second = await post_chat(client, session_id="s1", message="again")
    assert second.status_code == 409
    release.set()
    r1 = await first
    assert parse_sse(r1.text)[-1][0] == "done"
    assert not store.get("s1").lock.locked()
    assert [m.content for m in store.get("s1").messages] == ["hi", "ab"]


async def test_empty_reply_is_error_and_not_saved(client, store):
    # 上游只返回空 chunk（例如思考 token 耗尽）时，按上游错误处理。
    async def gen(inputs):
        async for _ in inputs:
            pass
        for text in ["", ""]:
            yield AIMessageChunk(content=text)

    app.dependency_overrides[get_chat_model] = lambda: RunnableGenerator(gen)
    r = await post_chat(client, session_id="s1", message="你好")
    events = parse_sse(r.text)
    assert events[-1] == ("error", {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"})
    assert ("done", {"finish_reason": "stop"}) not in events
    assert store.get("s1").messages == []
