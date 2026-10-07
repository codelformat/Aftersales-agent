"""硅基流动 /rerank 客户端。返回文档下标和相关性分数。"""

import asyncio
import logging
import random
from typing import Any

import httpx

from app.config import (
    RERANK_MAX_ATTEMPTS,
    RERANK_MODEL,
    RERANK_RETRY_BASE_DELAY,
    RERANK_RETRY_MAX_DELAY,
    RERANK_TIMEOUT_SECONDS,
    Settings,
    get_settings,
)
from app.retry import retry_async

logger = logging.getLogger(__name__)
_client: Any | None = None


class RerankError(RuntimeError):
    """重排失败：重试用尽、HTTP 错误或响应无效。"""


class _RetryableStatus(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")


def build_rerank_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.rerank_base_url,
        headers={"Authorization": f"Bearer {settings.rerank_api_key.get_secret_value()}"},
        timeout=RERANK_TIMEOUT_SECONDS,
    )


def get_rerank_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = build_rerank_client(get_settings())
    return _client


def set_rerank_client(client) -> None:
    global _client
    _client = client


async def close_rerank() -> None:
    global _client
    if isinstance(_client, httpx.AsyncClient):
        await _client.aclose()
    _client = None


async def rerank(
    query: str, documents: list[str], top_n: int, *, sleep=asyncio.sleep, rand=random.random
) -> list[tuple[int, float]]:
    if not documents:
        return []
    client = get_rerank_client()
    body = {
        "model": RERANK_MODEL,
        "query": query,
        "documents": documents,
        "top_n": top_n,
        "return_documents": False,
    }

    async def call() -> dict:
        response = await client.post("/rerank", json=body)
        # 429 和 5xx 是临时错误。其他 4xx 不重试。
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise _RetryableStatus(response.status_code)
        response.raise_for_status()
        return response.json()

    try:
        data = await retry_async(
            call,
            attempts=RERANK_MAX_ATTEMPTS,
            base_delay=RERANK_RETRY_BASE_DELAY,
            max_delay=RERANK_RETRY_MAX_DELAY,
            retry_on=(httpx.TransportError, _RetryableStatus),
            sleep=sleep,
            rand=rand,
        )
        results = [(int(r["index"]), float(r["relevance_score"])) for r in data["results"]]
        if any(not 0 <= index < len(documents) for index, _ in results):
            raise RerankError("重排结果下标越界")
    except RerankError:
        logger.exception("重排结果下标越界")
        raise
    except (httpx.HTTPError, _RetryableStatus, KeyError, TypeError, ValueError) as exc:
        logger.exception("重排失败")
        raise RerankError("重排失败") from exc
    return sorted(results, key=lambda result: result[1], reverse=True)[:top_n]
