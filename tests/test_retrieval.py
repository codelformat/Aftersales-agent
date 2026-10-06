import pytest

from app.knowledge import milvus as m
from app.knowledge.embeddings import set_embeddings
from app.knowledge.retrieval import search_faq, search_with_scores
from app.knowledge.vectorize import vectorize_pending
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk
from app.tools.registry import get_registry
from tests.fakes import FakeEmbeddings, blend, unit

pytestmark = pytest.mark.anyio

FREIGHT = NewChunk("运费", "运费怎么算？", "满 99 元免运费，不满收 8 元。", "常见问答 > 运费", "faq", False)
RETURN_FREIGHT = NewChunk("运费", "退货的运费谁承担？", "质量问题由商家承担。", "常见问答 > 运费", "faq", False)
INVOICE = NewChunk("发票", "怎么开发票？", "在订单详情页申请。", "常见问答 > 发票", "faq", False)


def _fake():
    return FakeEmbeddings([
        ("邮费", unit(0)),
        ("运费怎么算", unit(0)),            # 余弦 1.0
        ("退货的运费", blend(0, 1, 0.7)),   # 余弦 0.7
        ("发票", blend(0, 2, 0.3)),         # 余弦 0.3，低于阈值
    ])


async def _seed(db, chunks):
    async with db() as s:
        rows = await knowledge.insert_chunks(s, chunks)
        await s.commit()
        return [r.id for r in rows]


async def test_search_orders_by_score_and_filters_threshold(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [INVOICE, RETURN_FREIGHT, FREIGHT])
    await vectorize_pending()
    hits = await search_with_scores("邮费")
    assert [(r.questions, round(sc, 2)) for r, sc in hits] == [("运费怎么算？", 1.0), ("退货的运费谁承担？", 0.7)]


async def test_search_faq_output_shape(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [FREIGHT])
    await vectorize_pending()
    assert await search_faq("邮费") == [
        {"question": "运费怎么算？", "answer": "满 99 元免运费，不满收 8 元。", "category": "运费"}
    ]


async def test_search_ignores_pending_rows(db, milvus):
    set_embeddings(_fake())
    [cid] = await _seed(db, [FREIGHT])
    await m.upsert_vectors([(cid, unit(0))])  # Milvus 有向量，MySQL 仍为 pending
    assert await search_faq("邮费") == []


async def test_search_skips_ids_missing_in_mysql(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [FREIGHT])
    await vectorize_pending()
    await m.upsert_vectors([(999999, unit(0))])
    assert [r["question"] for r in await search_faq("邮费")] == ["运费怎么算？"]


async def test_query_faq_tool_uses_vector_search(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [FREIGHT])
    await vectorize_pending()
    tool = get_registry().get("query_faq").tool
    result = await tool.ainvoke({"keyword": "邮费"})
    assert result == {"results": [{"question": "运费怎么算？", "answer": "满 99 元免运费，不满收 8 元。", "category": "运费"}]}


async def test_query_faq_contract_unchanged():
    from langchain_core.utils.function_calling import convert_to_openai_tool

    schema = convert_to_openai_tool(get_registry().get("query_faq").tool)
    assert schema["function"]["description"] == "按关键词查询常见问题，例如退货政策、运费、发票、账户、支付。"
    assert schema["function"]["parameters"]["properties"]["keyword"]["maxLength"] == 20
