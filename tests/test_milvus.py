import pytest
from langchain_openai import OpenAIEmbeddings

from app.config import EMBED_DIM, EMBED_MODEL, get_settings
from app.knowledge import milvus as m
from app.knowledge.embeddings import build_embeddings, get_embeddings
from tests.fakes import FakeEmbeddings, blend, unit

pytestmark = pytest.mark.anyio


async def test_ensure_collection_is_idempotent(milvus):
    await m.ensure_collection()
    desc = await milvus.describe_collection(m.get_collection())
    names = {f["name"] for f in desc["fields"]}
    assert names == {"id", "vector"}
    assert desc["consistency_level"] in (0, "Strong")  # 返回枚举值或名称。


async def test_upsert_same_id_twice_does_not_duplicate(milvus):
    await m.upsert_vectors([(1, unit(0)), (2, unit(1))])
    await m.upsert_vectors([(1, unit(2))])
    assert await m.count_vectors() == 2


async def test_search_returns_ids_by_similarity(milvus):
    await m.upsert_vectors([(1, unit(0)), (2, blend(0, 1, 0.6)), (3, unit(5))])
    hits = await m.search_vectors(unit(0), 2)
    assert [i for i, _ in hits] == [1, 2]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-3)
    assert hits[1][1] == pytest.approx(0.6, abs=1e-3)


async def test_delete_vectors(milvus):
    await m.upsert_vectors([(1, unit(0)), (2, unit(1))])
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
    await m.upsert_vectors([])
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
