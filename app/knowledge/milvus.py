from typing import Any

from pymilvus import AsyncMilvusClient, DataType, MilvusException

from app.config import (
    EMBED_DIM,
    KNOWLEDGE_COLLECTION,
    MILVUS_MAX_ATTEMPTS,
    MILVUS_RETRY_BASE_DELAY,
    MILVUS_RETRY_MAX_DELAY,
    get_settings,
)
from app.retry import retry_async

_client: Any | None = None
_collection: str = KNOWLEDGE_COLLECTION


def get_milvus() -> AsyncMilvusClient:
    """返回全局 Milvus 客户端。第一次调用时按 MILVUS_URI 创建。"""
    global _client
    if _client is None:
        _client = AsyncMilvusClient(uri=get_settings().milvus_uri)
    return _client


def get_collection() -> str:
    return _collection


def set_milvus(client: Any | None, collection: str = KNOWLEDGE_COLLECTION) -> None:
    """替换客户端和集合名。传 None 后，下次调用按配置创建客户端。"""
    global _client, _collection
    _client, _collection = client, collection


async def _call(fn):
    return await retry_async(
        fn,
        attempts=MILVUS_MAX_ATTEMPTS,
        base_delay=MILVUS_RETRY_BASE_DELAY,
        max_delay=MILVUS_RETRY_MAX_DELAY,
        retry_on=(MilvusException,),
    )


async def ensure_collection() -> None:
    """集合不存在时创建并加载；已存在时只加载。"""
    client, name = get_milvus(), get_collection()
    if await _call(lambda: client.has_collection(name)):
        await _call(lambda: client.load_collection(name))
        return
    schema = AsyncMilvusClient.create_schema(auto_id=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    index_params = AsyncMilvusClient.prepare_index_params()
    index_params.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
    # 集合使用 Strong 一致性，写入后可立即检索。
    await _call(lambda: client.create_collection(
        name, schema=schema, index_params=index_params, consistency_level="Strong"
    ))


async def upsert_vectors(rows: list[tuple[int, list[float]]]) -> None:
    if not rows:
        return
    data = [{"id": i, "vector": v} for i, v in rows]
    await _call(lambda: get_milvus().upsert(get_collection(), data))


async def search_vectors(vector: list[float], limit: int) -> list[tuple[int, float]]:
    """返回 (id, 相似度)，按相似度降序。COSINE 的 distance 越大越相似。"""
    result = await _call(lambda: get_milvus().search(
        get_collection(),
        data=[vector],
        limit=limit,
        search_params={"metric_type": "COSINE"},
    ))
    return [(int(hit["id"]), float(hit["distance"])) for hit in result[0]]


async def delete_vectors(ids: list[int]) -> None:
    if not ids:
        return
    await _call(lambda: get_milvus().delete(get_collection(), ids=ids))


async def count_vectors() -> int:
    rows = await _call(lambda: get_milvus().query(
        get_collection(),
        filter="",
        output_fields=["count(*)"],
        consistency_level="Strong",
    ))
    return int(rows[0]["count(*)"])


async def close_milvus() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
