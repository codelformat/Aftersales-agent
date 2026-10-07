import pytest

from app.repositories import knowledge
from app.repositories.knowledge import NewChunk

pytestmark = pytest.mark.anyio


async def seed(db):
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk("退货政策 > 退款", "退款时间", "原路退回", "退货政策 > 退款 > 退款时间", "policy", False),
            NewChunk("退货政策 > 退款", "退款方式", "原路", "退货政策 > 退款 > 退款方式", "policy", False),
        ])
        await knowledge.link_chain(s, [r.id for r in rows])
        await knowledge.mark_done(s, [rows[0].id])
        await s.commit()
        return [r.id for r in rows]


async def test_get_done_chunk(client, db):
    a, b = await seed(db)
    r = await client.get(f"/api/knowledge/chunks/{a}")
    assert r.status_code == 200
    assert r.json() == {
        "id": a, "section_path": "退货政策 > 退款 > 退款时间", "content_type": "policy",
        "questions": "退款时间", "answer": "原路退回", "prev_chunk_id": None, "next_chunk_id": b,
    }


async def test_pending_or_missing_chunk_is_404(client, db):
    a, b = await seed(db)
    assert (await client.get(f"/api/knowledge/chunks/{b}")).status_code == 404
    assert (await client.get("/api/knowledge/chunks/999999")).status_code == 404
