import pytest
from sqlalchemy import select

from app.db.models import KnowledgeChunk
from app.knowledge import milvus as m
from app.knowledge.embeddings import get_embeddings
from app.knowledge.vectorize import SimulatedCrash, kb_status, vectorize_pending
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk

pytestmark = pytest.mark.anyio


async def _seed(db, n: int) -> list[int]:
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk(f"分类{i}", f"问题{i}", f"答案{i}。", f"文档 > 分类{i}", "policy", False)
            for i in range(n)
        ])
        await s.commit()
        for row in rows:
            await s.refresh(row)
        return [r.id for r in rows]


async def _statuses(db):
    async with db() as s:
        return list((await s.execute(select(
            KnowledgeChunk.vectorize_status, KnowledgeChunk.vector_id
        ).order_by(KnowledgeChunk.id))).all())


async def test_vectorize_all_pending(db, milvus):
    ids = await _seed(db, 5)
    assert await vectorize_pending(batch_size=2) == 5
    assert await _statuses(db) == [("done", str(i)) for i in ids]
    assert await m.count_vectors() == 5
    assert get_embeddings().calls[0] == ["分类0\n问题0\n答案0。", "分类1\n问题1\n答案1。"]


async def test_vectorize_skips_done_rows(db, milvus):
    await _seed(db, 3)
    await vectorize_pending()
    get_embeddings().calls.clear()
    assert await vectorize_pending() == 0
    assert get_embeddings().calls == []


async def test_crash_between_milvus_and_mysql_then_resume(db, milvus):
    await _seed(db, 7)
    with pytest.raises(SimulatedCrash):
        await vectorize_pending(batch_size=2, crash_after_batches=2)
    st = await kb_status()
    assert (st.done, st.pending) == (4, 3)
    assert st.milvus == 6  # 第 3 批已写入 Milvus，MySQL 未回填。
    assert await vectorize_pending(batch_size=2) == 3
    st = await kb_status()
    assert (st.done, st.pending, st.total, st.milvus) == (7, 0, 7, 7)


async def test_embedding_failure_leaves_rows_pending(db, milvus):
    await _seed(db, 3)

    class Boom(type(get_embeddings())):
        async def aembed_documents(self, texts):
            raise RuntimeError("上游失败")

    from app.knowledge.embeddings import set_embeddings
    set_embeddings(Boom())
    with pytest.raises(RuntimeError):
        await vectorize_pending()
    st = await kb_status()
    assert (st.pending, st.milvus) == (3, 0)


async def test_status_groups(db, milvus):
    await _seed(db, 2)
    st = await kb_status()
    assert st.groups == [("policy", "pending", 2)]


async def test_short_embedding_response_leaves_rows_pending(db, milvus, monkeypatch):
    await _seed(db, 3)
    embeddings = get_embeddings()
    original = embeddings.aembed_documents

    async def short_embedding(texts):
        return (await original(texts))[:-1]

    monkeypatch.setattr(embeddings, "aembed_documents", short_embedding)
    with pytest.raises(ValueError, match="嵌入返回数量与输入不一致"):
        await vectorize_pending()
    assert await _statuses(db) == [("pending", None)] * 3
    assert await m.count_vectors() == 0
