from datetime import date

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import KnowledgeChunk, QaExtractionStaging
from app.knowledge import mining
from app.knowledge.embeddings import set_embeddings
from app.knowledge.vectorize import vectorize_pending
from app.repositories import knowledge, staging
from app.repositories.knowledge import NewChunk
from app.repositories.staging import NewStaging
from app.schemas import DedupVerdict
from tests.fakes import FakeEmbeddings, blend, unit

pytestmark = pytest.mark.anyio


def _judge(decide, seen: list | None = None):
    """decide(inputs) 返回 DedupVerdict 或 Exception。"""
    async def run(inputs):
        if seen is not None:
            seen.append(inputs)
        out = decide(inputs)
        if isinstance(out, Exception):
            raise out
        return {"raw": None, "parsing_error": None, "parsed": out}
    return RunnableLambda(run)


async def _stage(db, *pairs):
    async with db() as s:
        await staging.add_rows(s, [
            NewStaging("20261005-01", f"conversation:{i}", q, a)
            for i, (q, a) in enumerate(pairs)
        ])
        await s.commit()


async def _staging_status(db):
    async with db() as s:
        return [(r.question, r.status) for r in (
            await s.execute(select(QaExtractionStaging).order_by(QaExtractionStaging.id))
        ).scalars()]


async def _mined(db):
    async with db() as s:
        return list((await s.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.content_type == "mined")
            .order_by(KnowledgeChunk.id)
        )).scalars())


def test_format_candidates():
    assert mining.format_candidates([]) == "（无）"
    assert mining.format_candidates([("运费怎么算？", "满 99 免。")]) == "1. 问：运费怎么算？\n   答：满 99 免。"


async def test_duplicate_of_kb_is_discarded(db, milvus):
    set_embeddings(FakeEmbeddings([("运费怎么算", unit(0)), ("邮费多少", blend(0, 1, 0.8))]))
    async with db() as s:
        await knowledge.insert_chunks(s, [
            NewChunk("运费", "运费怎么算？", "满 99 元免运费。", "常见问答 > 运费", "faq", False)
        ])
        await s.commit()
    await vectorize_pending()
    await _stage(db, ("邮费多少", "满 99 包邮。"))
    seen: list = []
    stats = await mining.dedup_pending(_judge(lambda i: DedupVerdict(duplicate_of=1, category="运费"), seen))
    assert "运费怎么算？" in seen[0]["candidates"]
    assert await _staging_status(db) == [("邮费多少", "discarded")]
    assert await _mined(db) == []
    assert (stats.kept, stats.discarded) == (0, 1)


async def test_new_pair_is_kept_as_mined_chunk(db, milvus):
    await _stage(db, ("能开专票吗", "可以开增值税专用发票。"))
    seen: list = []
    stats = await mining.dedup_pending(_judge(lambda i: DedupVerdict(duplicate_of=None, category="发票"), seen))
    assert seen[0]["candidates"] == "（无）"
    [chunk] = await _mined(db)
    assert (chunk.questions, chunk.answer, chunk.category, chunk.section_path, chunk.vectorize_status) == (
        "能开专票吗", "可以开增值税专用发票。", "发票", "对话挖掘 > 发票", "pending")
    assert await _staging_status(db) == [("能开专票吗", "kept")]
    assert stats.kept == 1


async def test_kept_pairs_in_same_run_are_candidates(db, milvus):
    set_embeddings(FakeEmbeddings([("改地址", unit(3)), ("换收货地址", blend(3, 4, 0.9))]))
    await _stage(db, ("能改地址吗", "发货前可以改。"), ("怎么换收货地址", "发货前在订单页修改。"))

    def decide(i):
        return DedupVerdict(duplicate_of=1 if i["candidates"] != "（无）" else None, category="物流")

    seen: list = []
    await mining.dedup_pending(_judge(decide, seen))
    assert "能改地址吗" in seen[1]["candidates"]
    assert await _staging_status(db) == [("能改地址吗", "kept"), ("怎么换收货地址", "discarded")]


async def test_dedup_out_of_range_index_keeps_extracted(db, milvus):
    await _stage(db, ("能开专票吗", "可以。"))
    stats = await mining.dedup_pending(_judge(lambda i: DedupVerdict(duplicate_of=5, category="发票")))
    assert await _staging_status(db) == [("能开专票吗", "extracted")]
    assert await _mined(db) == []
    assert stats.failed == 1


async def test_judge_failure_keeps_extracted_and_continues(db, milvus):
    await _stage(db, ("甲", "甲答。"), ("乙", "乙答。"))

    def decide(i):
        return RuntimeError("上游失败") if i["question"] == "甲" else DedupVerdict(duplicate_of=None, category="其他")

    stats = await mining.dedup_pending(_judge(decide))
    assert await _staging_status(db) == [("甲", "extracted"), ("乙", "kept")]
    assert (stats.kept, stats.failed) == (1, 1)


async def test_run_mining_end_to_end(db, milvus):
    from datetime import datetime
    from app.db.models import Conversation, Message
    from app.schemas import QaPair, QaPairs

    day = date(2026, 10, 5)
    async with db() as s:
        conv = Conversation(user_id="u1", created_at=datetime(2026, 10, 5, 10))
        s.add(conv)
        await s.flush()
        s.add_all([
            Message(conversation_id=conv.id, role="user", content="能开专票吗"),
            Message(conversation_id=conv.id, role="assistant", content="可以开专票。"),
        ])
        await s.commit()
    extractor = RunnableLambda(lambda _: {
        "raw": None, "parsing_error": None,
        "parsed": QaPairs(pairs=[QaPair(question="能开专票吗", answer="可以开专票。")]),
    })
    stats = await mining.run_mining(day, extractor, _judge(lambda i: DedupVerdict(duplicate_of=None, category="发票")))
    assert (stats.extract.pairs, stats.dedup.kept, stats.vectorized) == (1, 1, 1)
    [chunk] = await _mined(db)
    assert chunk.vectorize_status == "done"
