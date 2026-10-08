from dataclasses import asdict

import pytest

from app.config import GENERAL_CATEGORY
from app.knowledge import milvus as m
from app.knowledge import rerank as rr
from app.knowledge import retrieval as r
from app.knowledge.chunking import knowledge_text, product_category_of
from app.knowledge.embeddings import get_embeddings, set_embeddings
from app.knowledge.milvus import Entity
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk
from app.schemas import QueryPlan
from tests.fakes import FakeEmbeddings, blend, unit

pytestmark = pytest.mark.anyio


def spec(path, answer, content_type="manual", status="done"):
    parts = path.split(" > ")
    return dict(category=" > ".join(parts[:-1]) or parts[0], questions=parts[-1], answer=answer,
                section_path=path, content_type=content_type, status=status)


async def seed(db, specs, *, milvus_ids=None):
    """写 MySQL（按 status 标记 done），并把全部行写入 Milvus。返回 id 列表。"""
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk(x["category"], x["questions"], x["answer"], x["section_path"], x["content_type"], False)
            for x in specs
        ])
        await knowledge.mark_done(s, [row.id for row, x in zip(rows, specs) if x["status"] == "done"])
        await s.commit()
    emb = get_embeddings()
    ents = []
    for row, x in zip(rows, specs):
        txt = knowledge_text(x["category"], x["questions"], x["answer"])
        ents.append(Entity(row.id, await emb.aembed_query(txt), txt,
                           product_category_of(x["section_path"]), x["content_type"]))
    await m.upsert_entities(ents)
    return [row.id for row in rows]


def plan(text, category=None):
    return QueryPlan(standard_query=text, product_category=category)


@pytest.mark.parametrize("n, expected", [
    (0, []), (1, [1]), (2, [1, 2]), (3, [1, 3, 2]), (5, [1, 3, 5, 4, 2]), (6, [1, 3, 5, 6, 4, 2]),
])
def test_interleave_positions(n, expected):
    assert r.interleave(list(range(1, n + 1))) == expected


@pytest.mark.parametrize("category, exclude, expected", [
    (None, False, ""),
    ("蓝牙耳机", False, 'product_category in ["蓝牙耳机", "通用"]'),
    (None, True, 'content_type != "mined"'),
    ("台灯", True, 'product_category in ["台灯", "通用"] and content_type != "mined"'),
])
def test_build_filter(category, exclude, expected):
    assert r.build_filter(category, exclude) == expected


def test_source_key():
    assert r.source_key("退货政策 > 退款 > 退款时间", "退款时间") == "退货政策 > 退款 > 退款时间"
    assert r.source_key("常见问答 > 运费", "运费怎么算？") == "常见问答 > 运费 > 运费怎么算？"


async def test_bm25_hits_normalized_model_before_sibling(db, milvus):
    ids = await seed(db, [
        spec("商品手册 > 蓝牙耳机 > X3 续航", "单次续航 6 小时"),
        spec("商品手册 > 蓝牙耳机 > X3 Pro 续航", "单次续航 8 小时"),
        spec("退货政策 > 退款 > 退款时间", "原路退回", "policy"),
    ])
    res = await r.retrieve("x3pro续航", "bm25", plan=plan("X3 Pro 续航多久", "蓝牙耳机"))
    assert res.ranked[0].chunk_id == ids[1]


async def test_dense_orders_by_cosine_and_skips_pending_and_missing(db, milvus):
    set_embeddings(FakeEmbeddings([("运费", unit(0)), ("退款", unit(1))]))
    ids = await seed(db, [
        spec("退货政策 > 运费 > 运费说明", "运费 8 元", "policy"),
        spec("退货政策 > 退款 > 退款时间", "退款原路", "policy"),
        spec("退货政策 > 运费 > 偏远运费", "运费 20 元", "policy", status="pending"),
    ])
    await m.upsert_entities([Entity(999999, unit(0), "运费", GENERAL_CATEGORY, "policy")])
    res = await r.retrieve("q", "dense", plan=plan("运费"))
    got = [e.chunk_id for e in res.ranked]
    assert got[0] == ids[0] and ids[2] not in got and 999999 not in got


async def test_category_filter_and_exclude_mined(db, milvus):
    ids = await seed(db, [
        spec("商品手册 > 蓝牙耳机 > X3 保修", "保修 180 天"),
        spec("商品手册 > 羊毛衫 > W1 保修", "保修 90 天"),
        spec("退货政策 > 保修 > 保修期", "保修 从签收算", "policy"),
        spec("对话挖掘 > 售后维修 > 保修多久", "保修一年", "mined"),
    ])
    res = await r.retrieve("q", "bm25", plan=plan("保修", "蓝牙耳机"), exclude_mined=True)
    assert sorted(e.chunk_id for e in res.ranked) == sorted([ids[0], ids[2]])


async def test_hybrid_returns_rrf_without_rerank(db, milvus, monkeypatch):
    monkeypatch.setattr(rr, "rerank", lambda *a, **k: pytest.fail("hybrid 不应重排"))
    ids = await seed(db, [spec("退货政策 > 运费 > 运费说明", "运费 8 元", "policy")])
    res = await r.retrieve("q", "hybrid", plan=plan("运费"))
    assert [e.chunk_id for e in res.ranked] == ids
    assert res.evidence == res.ranked


async def test_hybrid_rerank_threshold_and_interleave(db, milvus, monkeypatch):
    ids = await seed(db, [
        spec("退货政策 > 运费 > A", "运费 a", "policy"),
        spec("退货政策 > 运费 > B", "运费 b", "policy"),
        spec("退货政策 > 运费 > C", "运费 c", "policy"),
        spec("退货政策 > 运费 > D", "运费 d", "policy"),
    ])
    seen = {}
    scores = {"运费 a": 0.9, "运费 b": 0.1, "运费 c": 0.8, "运费 d": 0.7}

    async def fake_rerank(query, documents, top_n, **kw):
        seen["query"], seen["docs"], seen["top_n"] = query, documents, top_n
        out = [(i, next(v for k, v in scores.items() if k in d)) for i, d in enumerate(documents)]
        return sorted(out, key=lambda x: x[1], reverse=True)[:top_n]

    monkeypatch.setattr(rr, "rerank", fake_rerank)
    res = await r.retrieve("q", "hybrid_rerank", plan=plan("邮费怎么算"), min_score=0.3)
    by_answer = {e.answer: e for e in res.ranked}
    assert [e.answer for e in res.ranked] == ["运费 a", "运费 c", "运费 d", "运费 b"]
    assert [e.answer for e in res.evidence] == ["运费 a", "运费 d", "运费 c"]
    assert by_answer["运费 a"].score == 0.9
    assert seen["query"] == "运费怎么算" and seen["top_n"] == 10
    assert all(d.startswith("退货政策 > 运费\n") for d in seen["docs"])  # knowledge_text 格式


async def test_hybrid_rerank_propagates_rerank_error(db, milvus, monkeypatch):
    await seed(db, [spec("退货政策 > 运费 > A", "运费 a", "policy")])

    async def boom(*a, **k):
        raise rr.RerankError("x")

    monkeypatch.setattr(rr, "rerank", boom)
    with pytest.raises(rr.RerankError):
        await r.retrieve("q", "hybrid_rerank", plan=plan("运费"))


async def test_retrieve_calls_understand_without_plan(db, milvus, monkeypatch):
    called = []

    async def fake_understand(question, **kw):
        called.append(question)
        return plan("运费")

    monkeypatch.setattr(r, "understand", fake_understand)
    await r.retrieve("邮费多少", "bm25")
    assert called == ["邮费多少"]


async def test_query_faq_returns_interleaved_evidence(monkeypatch):
    from app.tools.faq import query_faq
    item = r.EvidenceItem(7, "退货政策 > 运费 > A", "A", "运费 8 元", 0.9)

    async def fake_retrieve(question, *a, **k):
        assert question == "邮费多少"
        return r.Retrieval(plan("运费"), [item], [item])

    monkeypatch.setattr("app.tools.faq.retrieve", fake_retrieve)
    assert await query_faq.ainvoke({"question": "邮费多少"}) == {"evidence": [asdict(item)]}


def test_query_faq_contract():
    from app.tools.faq import query_faq
    schema = query_faq.args_schema.model_json_schema()
    assert list(schema["properties"]) == ["question"]
    assert schema["properties"]["question"]["maxLength"] == 200
    assert "不要改写" in schema["properties"]["question"]["description"]
    assert "商品型号" in query_faq.description


async def test_search_by_vector_orders_by_score_and_filters_threshold(db, milvus):
    set_embeddings(FakeEmbeddings([
        ("运费怎么算", unit(0)),
        ("退货的运费", blend(0, 1, 0.7)),
        ("发票", blend(0, 2, 0.3)),
    ]))
    ids = await seed(db, [
        spec("常见问答 > 发票", "在订单详情页申请。", "faq"),
        spec("常见问答 > 运费 > 退货的运费谁承担？", "质量问题由商家承担。", "faq"),
        spec("常见问答 > 运费 > 运费怎么算？", "满 99 元免运费，不满收 8 元。", "faq"),
    ])
    hits = await r.search_by_vector(unit(0), limit=3, min_score=0.5)
    assert [(row.id, round(score, 2)) for row, score in hits] == [(ids[2], 1.0), (ids[1], 0.7)]


async def test_search_by_vector_ignores_pending_rows(db, milvus):
    set_embeddings(FakeEmbeddings([("运费", unit(0))]))
    await seed(db, [spec("常见问答 > 运费", "运费 8 元", "faq", status="pending")])
    assert await r.search_by_vector(unit(0), limit=3, min_score=0.5) == []


async def test_search_by_vector_skips_ids_missing_in_mysql(db, milvus):
    set_embeddings(FakeEmbeddings([("运费", unit(0))]))
    ids = await seed(db, [spec("常见问答 > 运费", "运费 8 元", "faq")])
    await m.upsert_entities([Entity(999999, unit(0), "运费", GENERAL_CATEGORY, "policy")])
    hits = await r.search_by_vector(unit(0), limit=3, min_score=0.5)
    assert [row.id for row, _ in hits] == ids


async def test_retrieve_multi_merges_queries_and_reranks_once(db, milvus, monkeypatch):
    ids = await seed(db, [
        spec("退货政策 > 条件 > 拆封", "拆封后不影响二次销售可退", "policy"),
        spec("退货政策 > 运费 > 谁出", "质量问题商家承担运费", "policy"),
        spec("退货政策 > 时限 > 天数", "签收后 7 天内可退", "policy"),
    ])
    seen = []

    async def fake_rerank(query, documents, top_n, **kw):
        seen.append((query, len(documents)))
        return [(i, 0.9 - 0.1 * i) for i in range(len(documents))][:top_n]

    monkeypatch.setattr(rr, "rerank", fake_rerank)
    res = await r.retrieve_multi(["退货条件", "退货运费", "退货时限"], plan("耳机退货条件"))
    assert len(seen) == 1 and seen[0][0] == "耳机退货条件"
    assert seen[0][1] == len({e.chunk_id for e in res.ranked}) == 3
    assert sorted(e.chunk_id for e in res.ranked) == sorted(ids)
    assert res.plan.standard_query == "耳机退货条件"


async def test_retrieve_multi_respects_threshold_and_limit(db, milvus, monkeypatch):
    await seed(db, [spec(f"退货政策 > 条件 > 第{i}条", f"条件 {i}", "policy") for i in range(4)])
    monkeypatch.setattr(r, "MULTI_FUSED_LIMIT", 2)

    async def fake_rerank(query, documents, top_n, **kw):
        assert len(documents) == 2
        return [(0, 0.9), (1, 0.05)]

    monkeypatch.setattr(rr, "rerank", fake_rerank)
    res = await r.retrieve_multi(["条件", "退货条件"], plan("退货条件"))
    assert len(res.ranked) == 2 and len(res.evidence) == 1


async def test_retrieve_multi_rejects_empty_queries():
    with pytest.raises(ValueError):
        await r.retrieve_multi([], plan("q"))
