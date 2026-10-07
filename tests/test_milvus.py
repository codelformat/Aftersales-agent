import pytest
from langchain_openai import OpenAIEmbeddings
from pymilvus import AsyncMilvusClient, DataType

from app.config import EMBED_DIM, EMBED_MODEL, get_settings
from app.knowledge import milvus as m
from app.knowledge.embeddings import build_embeddings, get_embeddings
from tests.fakes import FakeEmbeddings, blend, entity, unit

pytestmark = pytest.mark.anyio


async def test_ensure_collection_is_idempotent(milvus):
    await m.ensure_collection()
    desc = await milvus.describe_collection(m.get_collection())
    names = {f["name"] for f in desc["fields"]}
    assert names == {"id", "vector", "text", "sparse", "product_category", "content_type"}
    assert desc["consistency_level"] in (0, "Strong")  # 返回枚举值或名称。


async def test_upsert_same_id_twice_does_not_duplicate(milvus):
    await m.upsert_entities([entity(1, unit(0)), entity(2, unit(1))])
    await m.upsert_entities([entity(1, unit(2))])
    assert await m.count_vectors() == 2


async def test_search_returns_ids_by_similarity(milvus):
    await m.upsert_entities([entity(1, unit(0)), entity(2, blend(0, 1, 0.6)), entity(3, unit(5))])
    hits = await m.search_vectors(unit(0), 2)
    assert [i for i, _ in hits] == [1, 2]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-3)
    assert hits[1][1] == pytest.approx(0.6, abs=1e-3)


async def test_delete_vectors(milvus):
    await m.upsert_entities([entity(1, unit(0)), entity(2, unit(1))])
    await m.delete_vectors([1, 99])
    assert await m.count_vectors() == 1


async def test_delete_empty_list_is_noop(milvus):
    await m.delete_vectors([])
    assert await m.count_vectors() == 0


def test_build_embeddings_settings():
    e = build_embeddings(get_settings())
    assert isinstance(e, OpenAIEmbeddings)
    assert e.model == EMBED_MODEL
    assert e.check_embedding_ctx_length is False
    assert e.model_kwargs == {"encoding_format": "float"}
    assert e.max_retries == 2


async def test_milvus_access_without_fixture_fails_immediately():
    with pytest.raises(RuntimeError, match="测试未启用 fixture milvus"):
        await m.count_vectors()


async def test_empty_mutations_without_fixture_are_noops():
    await m.upsert_entities([])
    await m.delete_vectors([])


async def test_fake_embeddings_match_first_rule_and_record_calls():
    e = FakeEmbeddings([("邮费", unit(0)), ("多少", unit(1))])
    texts = ["邮费是多少", "多少"]
    assert await e.aembed_documents(texts) == [unit(0), unit(1)]
    texts.clear()
    assert e.embed_query("邮费是多少") == unit(0)
    assert await e.aembed_query("多少") == unit(1)
    assert e.calls == [["邮费是多少", "多少"], ["邮费是多少"], ["多少"]]


async def test_isolated_embeddings_are_deterministic_unit_vectors():
    e = get_embeddings()
    assert isinstance(e, FakeEmbeddings)
    v = await e.aembed_query("退货流程")
    assert len(v) == EMBED_DIM
    assert sum(x * x for x in v) == pytest.approx(1.0)
    assert v == FakeEmbeddings().embed_query("退货流程")
    other = e.embed_query("开发票")
    assert abs(sum(x * y for x, y in zip(v, other))) < 0.1


async def test_new_collection_has_six_fields(milvus):
    desc = await milvus.describe_collection(m.get_collection())
    assert [f["name"] for f in desc["fields"]] == [
        "id", "vector", "text", "sparse", "product_category", "content_type",
    ]


async def test_old_schema_collection_raises(milvus):
    name = m.get_collection()
    await milvus.drop_collection(name)
    schema = AsyncMilvusClient.create_schema(auto_id=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=1024)
    idx = AsyncMilvusClient.prepare_index_params()
    idx.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
    await milvus.create_collection(name, schema=schema, index_params=idx)
    with pytest.raises(m.CollectionSchemaError, match="build_kb.py --rebuild"):
        await m.ensure_collection()


async def test_recreate_collection_empties_and_fixes_schema(milvus):
    await m.upsert_entities([entity(1, unit(0))])
    await m.recreate_collection()
    assert await m.count_vectors() == 0
    await m.ensure_collection()


async def test_bm25_ranks_exact_model_terms(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "商品手册 > 蓝牙耳机\nX3 Pro 续航\n单次续航 8 小时", "蓝牙耳机", "manual"),
        entity(2, unit(1), "商品手册 > 蓝牙耳机\nX5 续航\n单次续航 6 小时", "蓝牙耳机", "manual"),
        entity(3, unit(2), "退货政策 > 退款\n退款时间\n原路退回", "通用", "policy"),
    ])
    hits = await m.search_bm25("X3 Pro 续航", 10)
    assert hits[0][0] == 1
    assert 3 not in [i for i, _ in hits]  # BM25 只返回含查询词项的文档


async def test_category_filter_keeps_general(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "耳机 七天无理由", "蓝牙耳机", "manual"),
        entity(2, unit(0), "七天无理由 退货条件", "通用", "policy"),
        entity(3, unit(0), "羊毛衫 七天无理由", "羊毛衫", "faq"),
    ])
    flt = 'product_category in ["蓝牙耳机", "通用"]'
    assert sorted(i for i, _ in await m.search_bm25("七天无理由", 10, flt)) == [1, 2]
    assert sorted(i for i, _ in await m.search_dense(unit(0), 10, flt)) == [1, 2]


async def test_exclude_mined_filter(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "快递 几天", "通用", "mined"),
        entity(2, unit(0), "快递 几天", "通用", "policy"),
    ])
    assert [i for i, _ in await m.search_bm25("快递", 10, 'content_type != "mined"')] == [2]


async def test_hybrid_search_fuses_both_legs(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "无关文字", "通用", "policy"),        # 只有 dense 命中
        entity(2, unit(5), "X3 Pro 续航", "蓝牙耳机", "manual"),  # 只有 BM25 命中
        entity(3, unit(9), "其他", "通用", "policy"),            # 都不命中
    ])
    hits = await m.search_hybrid(unit(0), "X3 Pro 续航", leg_limit=1, limit=10)
    assert sorted(i for i, _ in hits) == [1, 2]
    assert all(score > 0 for _, score in hits)


async def test_search_vectors_is_unfiltered_dense(milvus):
    await m.upsert_entities([entity(1, unit(0)), entity(2, unit(1), content_type="mined")])
    assert [i for i, _ in await m.search_vectors(unit(1), 1)] == [2]
