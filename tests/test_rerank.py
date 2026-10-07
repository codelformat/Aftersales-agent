import json
import importlib

import httpx
import pytest

from app.config import RERANK_MODEL, Settings
import app.knowledge.rerank as rr

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def _close_test_reranker(_isolate_knowledge):
    yield
    await rr.close_rerank()


async def no_sleep(_):
    return None


def use_handler(handler):
    client = httpx.AsyncClient(
        base_url="https://rr.test/v1", transport=httpx.MockTransport(handler)
    )
    rr.set_rerank_client(client)
    return client


def ok(results):
    return httpx.Response(200, json={"results": results})


async def test_request_shape_and_sorted_results():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return ok([
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.95},
        ])

    use_handler(handler)
    assert await rr.rerank("q", ["a", "b", "c"], 2, sleep=no_sleep) == [
        (0, 0.95), (2, 0.9)
    ]
    assert seen == [("/v1/rerank", {
        "model": RERANK_MODEL,
        "query": "q",
        "documents": ["a", "b", "c"],
        "top_n": 2,
        "return_documents": False,
    })]


async def test_empty_documents_skips_call():
    use_handler(lambda request: pytest.fail("不应发请求"))
    assert await rr.rerank("q", [], 10) == []


async def test_retries_429_and_5xx_then_succeeds():
    responses = [
        httpx.Response(429), httpx.Response(503),
        ok([{"index": 0, "relevance_score": 0.5}]),
    ]
    calls = []
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def handler(request):
        calls.append(1)
        return responses[len(calls) - 1]

    use_handler(handler)
    assert await rr.rerank("q", ["a"], 1, sleep=sleep, rand=lambda: 1.0) == [(0, 0.5)]
    assert len(calls) == 3
    assert delays == [0.5, 1.0]


async def test_4xx_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"message": "invalid key"})

    use_handler(handler)
    with pytest.raises(rr.RerankError):
        await rr.rerank("q", ["a"], 1, sleep=no_sleep)
    assert len(calls) == 1


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_error_retried_then_raises(error_type):
    calls = []

    def handler(request):
        calls.append(1)
        raise error_type("down", request=request)

    use_handler(handler)
    with pytest.raises(rr.RerankError):
        await rr.rerank("q", ["a"], 1, sleep=no_sleep)
    assert len(calls) == 3


async def test_index_out_of_range_raises():
    use_handler(lambda request: ok([{"index": 5, "relevance_score": 0.5}]))
    with pytest.raises(rr.RerankError):
        await rr.rerank("q", ["a"], 1, sleep=no_sleep)


async def test_build_client_uses_settings(monkeypatch):
    monkeypatch.setenv("RERANK_API_KEY", "secret-r")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rr.example/v1")
    for key, value in {
        "CHAT_BASE_URL": "https://c/v1", "CHAT_MODEL": "m", "CHAT_API_KEY": "k",
        "DATABASE_URL": "mysql+asyncmy://u:p@h:1/d", "EMBED_API_KEY": "e",
        "MILVUS_URI": "http://m:1",
    }.items():
        monkeypatch.setenv(key, value)
    client = rr.build_rerank_client(Settings(_env_file=None))
    try:
        assert str(client.base_url) == "https://rr.example/v1/"
        assert client.headers["Authorization"] == "Bearer secret-r"
    finally:
        await client.aclose()


async def test_blocked_reranker_by_default():
    # conftest 的 autouse fixture 已设置 BlockedReranker。
    with pytest.raises(RuntimeError, match="重排"):
        await rr.rerank("q", ["a"], 1)


async def test_sorts_before_limiting_results():
    use_handler(lambda request: ok([
        {"index": 0, "relevance_score": 0.2},
        {"index": 1, "relevance_score": 0.9},
        {"index": 2, "relevance_score": 0.5},
    ]))
    assert await rr.rerank("q", ["a", "b", "c"], 1) == [(1, 0.9)]


@pytest.mark.parametrize("response", [
    httpx.Response(200, content="非 JSON 响应"),
    httpx.Response(200, json={}),
    ok(None),
    ok([{"index": 0}]),
    ok([{"index": 0, "relevance_score": "无效分数"}]),
])
async def test_invalid_response_is_logged_without_retry(response, caplog):
    calls = []

    def handler(request):
        calls.append(1)
        return response

    use_handler(handler)
    with pytest.raises(rr.RerankError, match="^重排失败$") as error:
        await rr.rerank("q", ["a"], 1, sleep=no_sleep)
    assert len(calls) == 1
    assert error.value.__cause__ is not None
    assert any(record.name == rr.__name__ and record.exc_info for record in caplog.records)


async def test_close_rerank_closes_and_resets_client(monkeypatch):
    client = use_handler(lambda request: pytest.fail("不应发请求"))
    await rr.close_rerank()
    assert client.is_closed
    sentinel = object()
    monkeypatch.setattr(rr, "build_rerank_client", lambda settings: sentinel)
    monkeypatch.setattr(rr, "get_settings", lambda: None)
    assert rr.get_rerank_client() is sentinel
    await rr.close_rerank()


@pytest.mark.parametrize("milvus_fails", [False, True])
async def test_lifespan_always_closes_rerank_after_milvus(monkeypatch, milvus_fails):
    main = importlib.import_module("app.main")
    calls = []

    async def close_milvus():
        calls.append("milvus")
        if milvus_fails:
            raise RuntimeError("Milvus 关闭失败")

    async def close_rerank():
        calls.append("rerank")

    monkeypatch.setattr(main, "close_milvus", close_milvus)
    monkeypatch.setattr(main, "close_rerank", close_rerank)
    if milvus_fails:
        with pytest.raises(RuntimeError, match="Milvus 关闭失败"):
            async with main.app.router.lifespan_context(main.app):
                assert calls == []
    else:
        async with main.app.router.lifespan_context(main.app):
            assert calls == []
    assert calls == ["milvus", "rerank"]
