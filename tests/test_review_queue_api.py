import pytest
from sqlalchemy import select

from app.api import review_queue as api
from app.db.models import KnowledgeChunk
from app.repositories import conversations, low_confidence, review_queue

pytestmark = pytest.mark.anyio

SNAP = [{"chunk_id": 1, "section_path": "p", "question": "q", "answer": "a", "score": 0.3}]


async def _seed(db, raws=("杯子能扔洗碗机吗", "保温杯洗碗机能洗不")):
    async with db() as s:
        c = await conversations.create(s, "u1")
        item = await review_queue.add(s, normalized_question="保温杯可以用洗碗机清洗吗？",
                                      suggested_answer="（待核实）不建议。")
        for raw in raws:
            row = await low_confidence.add(s, conversation_id=c.id, raw_question=raw, source="retrieval_low_conf",
                                           reason="r", retrieved_chunks=SNAP)
            await low_confidence.set_matched(s, row.id, item.id)
        await s.commit()
        return item.id


async def test_list_and_detail(db, client):
    rid = await _seed(db)
    r = await client.get("/api/review-queue", params={"status": "待审"})
    assert r.status_code == 200 and r.json()[0]["id"] == rid and r.json()[0]["occurrence_count"] == 1
    d = (await client.get(f"/api/review-queue/{rid}")).json()
    assert [x["raw_question"] for x in d["sources"]] == ["杯子能扔洗碗机吗", "保温杯洗碗机能洗不"]
    assert d["sources"][0]["retrieved_chunks"] == SNAP
    async with db() as s:
        sources = await low_confidence.list_for_review(s, rid)
    assert [x.get("conversation_id") for x in d["sources"]] == [x.conversation_id for x in sources]
    assert (await client.get("/api/review-queue/999999")).status_code == 404


async def test_approve_writes_flywheel_chunk_and_vectorizes(db, client, monkeypatch):
    calls = []

    async def fake_vectorize():
        calls.append(1)
        return 1

    monkeypatch.setattr(api, "vectorize_pending", fake_vectorize)
    rid = await _seed(db)
    r = await client.post(f"/api/review-queue/{rid}/approve",
                          json={"approved_answer": "  不建议放入洗碗机，请手洗。 ", "product_category": "保温杯"})
    assert r.status_code == 200 and r.json()["vectorized"] == 1 and calls == [1]
    async with db() as s:
        chunk = (await s.execute(select(KnowledgeChunk))).scalar_one()
        item = await review_queue.get(s, rid)
    assert (chunk.category, chunk.section_path, chunk.content_type, chunk.answer) == (
        "保温杯", "飞轮补充 > 保温杯", "flywheel", "不建议放入洗碗机，请手洗。")
    assert chunk.questions.split("\n") == ["保温杯可以用洗碗机清洗吗？", "杯子能扔洗碗机吗", "保温杯洗碗机能洗不"]
    assert (item.review_status, item.approved_answer) == ("通过", "不建议放入洗碗机，请手洗。")
    assert r.json()["chunk_id"] == chunk.id


async def test_approve_general_category_path(db, client, monkeypatch):
    async def fake_vectorize():
        return 1
    monkeypatch.setattr(api, "vectorize_pending", fake_vectorize)
    rid = await _seed(db, raws=("a", "b", "c", "d", "a"))
    await client.post(f"/api/review-queue/{rid}/approve", json={"approved_answer": "答", "product_category": "通用"})
    async with db() as s:
        chunk = (await s.execute(select(KnowledgeChunk))).scalar_one()
    assert chunk.section_path == "飞轮补充"
    assert chunk.questions.split("\n") == ["保温杯可以用洗碗机清洗吗？", "a", "b", "c"]


async def test_approve_twice_conflicts(db, client, monkeypatch):
    async def fake_vectorize():
        return 1
    monkeypatch.setattr(api, "vectorize_pending", fake_vectorize)
    rid = await _seed(db)
    body = {"approved_answer": "答", "product_category": "通用"}
    assert (await client.post(f"/api/review-queue/{rid}/approve", json=body)).status_code == 200
    assert (await client.post(f"/api/review-queue/{rid}/approve", json=body)).status_code == 409
    async with db() as s:
        assert len((await s.execute(select(KnowledgeChunk))).scalars().all()) == 1


async def test_approve_validation(db, client):
    rid = await _seed(db)
    assert (await client.post(f"/api/review-queue/{rid}/approve",
                              json={"approved_answer": "  ", "product_category": "通用"})).status_code == 422
    assert (await client.post(f"/api/review-queue/{rid}/approve",
                              json={"approved_answer": "a", "product_category": "冰箱"})).status_code == 422
    assert (await client.post("/api/review-queue/999999/approve",
                              json={"approved_answer": "a", "product_category": "通用"})).status_code == 404


async def test_vectorize_failure_returns_502_but_keeps_status(db, client, monkeypatch):
    async def boom():
        raise RuntimeError("milvus down")
    monkeypatch.setattr(api, "vectorize_pending", boom)
    rid = await _seed(db)
    r = await client.post(f"/api/review-queue/{rid}/approve", json={"approved_answer": "答", "product_category": "通用"})
    assert r.status_code == 502
    async with db() as s:
        assert (await review_queue.get(s, rid)).review_status == "通过"


async def test_reject(db, client):
    rid = await _seed(db)
    assert (await client.post(f"/api/review-queue/{rid}/reject")).status_code == 200
    assert (await client.post(f"/api/review-queue/{rid}/reject")).status_code == 409
    async with db() as s:
        assert (await review_queue.get(s, rid)).review_status == "驳回"
