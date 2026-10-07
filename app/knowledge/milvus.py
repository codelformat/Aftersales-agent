from dataclasses import asdict, dataclass
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    AsyncMilvusClient,
    DataType,
    Function,
    FunctionType,
    MilvusException,
    RRFRanker,
)

from app.config import (
    EMBED_DIM,
    KNOWLEDGE_COLLECTION,
    KNOWLEDGE_TEXT_MAX_BYTES,
    MILVUS_MAX_ATTEMPTS,
    MILVUS_RETRY_BASE_DELAY,
    MILVUS_RETRY_MAX_DELAY,
    RRF_K,
    get_settings,
)
from app.retry import retry_async

FIELDS = ("id", "vector", "text", "sparse", "product_category", "content_type")
REBUILD_HINT = (
    "Milvus 集合结构过旧（缺少 sparse 字段）。请执行 bash scripts/reset_db.sh，"
    "或 uv run python scripts/build_kb.py --rebuild"
)


class CollectionSchemaError(RuntimeError):
    """集合存在，但结构不是本章的 6 个字段。"""


@dataclass(frozen=True)
class Entity:
    id: int
    vector: list[float]
    text: str
    product_category: str
    content_type: str


def _schema():
    schema = AsyncMilvusClient.create_schema(auto_id=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field(
        "text", DataType.VARCHAR, max_length=KNOWLEDGE_TEXT_MAX_BYTES,
        enable_analyzer=True, analyzer_params={"type": "chinese"},
    )
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("product_category", DataType.VARCHAR, max_length=64)
    schema.add_field("content_type", DataType.VARCHAR, max_length=16)
    # BM25 Function 由 Milvus 从 text 生成 sparse。写入时不提供 sparse。
    schema.add_function(Function(
        name="text_bm25", input_field_names=["text"], output_field_names=["sparse"],
        function_type=FunctionType.BM25,
    ))
    return schema


def _index_params():
    params = AsyncMilvusClient.prepare_index_params()
    params.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
    params.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    return params


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
    """集合不存在时创建并加载。已存在时检查结构，再加载。不自动删除集合。"""
    client, name = get_milvus(), get_collection()
    if await _call(lambda: client.has_collection(name)):
        desc = await _call(lambda: client.describe_collection(name))
        if "sparse" not in {f["name"] for f in desc["fields"]}:
            raise CollectionSchemaError(REBUILD_HINT)
        await _call(lambda: client.load_collection(name))
        return
    # 集合使用 Strong 一致性，写入后可立即检索。
    await _call(lambda: client.create_collection(
        name, schema=_schema(), index_params=_index_params(), consistency_level="Strong"
    ))


async def recreate_collection() -> None:
    client, name = get_milvus(), get_collection()
    if await _call(lambda: client.has_collection(name)):
        await _call(lambda: client.drop_collection(name))
    await ensure_collection()


async def upsert_entities(entities: list[Entity]) -> None:
    if not entities:
        return
    data = [asdict(e) for e in entities]
    await _call(lambda: get_milvus().upsert(get_collection(), data))


def _hits(result) -> list[tuple[int, float]]:
    return [(int(hit["id"]), float(hit["distance"])) for hit in result[0]]


async def search_dense(vector: list[float], limit: int, filter: str = "") -> list[tuple[int, float]]:
    """COSINE 的 distance 越大越相似。"""
    return _hits(await _call(lambda: get_milvus().search(
        get_collection(), data=[vector], anns_field="vector", limit=limit, filter=filter,
        search_params={"metric_type": "COSINE"},
    )))


async def search_bm25(text: str, limit: int, filter: str = "") -> list[tuple[int, float]]:
    """查询文本由 Milvus 用同一 analyzer 分词。只返回含查询词项的文档。"""
    return _hits(await _call(lambda: get_milvus().search(
        get_collection(), data=[text], anns_field="sparse", limit=limit, filter=filter,
        search_params={"metric_type": "BM25"},
    )))


async def search_hybrid(
    vector: list[float], text: str, *, leg_limit: int, limit: int, filter: str = ""
) -> list[tuple[int, float]]:
    """dense 和 BM25 各取 leg_limit 条，用 RRF 融合后取 limit 条。分数为 RRF 分数。"""
    reqs = [
        AnnSearchRequest(data=[vector], anns_field="vector", param={"metric_type": "COSINE"},
                         limit=leg_limit, filter=filter),
        AnnSearchRequest(data=[text], anns_field="sparse", param={"metric_type": "BM25"},
                         limit=leg_limit, filter=filter),
    ]
    return _hits(await _call(lambda: get_milvus().hybrid_search(
        get_collection(), reqs, RRFRanker(RRF_K), limit=limit,
    )))


async def search_vectors(vector: list[float], limit: int) -> list[tuple[int, float]]:
    """挖掘去重沿用：dense 单路，不过滤。"""
    return await search_dense(vector, limit)


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
