import pytest
from sqlalchemy import select

from app.db.models import LowConfidenceQuestion
from app.repositories import conversations, messages
from app.repositories.messages import NewMessage

pytestmark = pytest.mark.anyio


async def _turn(db, user="X3 Pro 能游泳吗", reply="抱歉，暂时无法回答。"):
    async with db() as s:
        c = await conversations.create(s, "u1")
        u, a = await messages.add_turn(
            s, c.id, [NewMessage("user", user), NewMessage("assistant", reply)])
        await s.commit()
        return c.id, u.id, a.id


async def test_up_returns_204(db, client):
    cid, _, aid = await _turn(db)
    r = await client.post("/api/feedback", json={
        "conversation_id": cid, "message_id": aid, "rating": "up", "user_id": "u1"})
    assert r.status_code == 204


async def test_down_pools_once_with_null_snapshot_without_checkpoint(db, client):
    cid, _, aid = await _turn(db)
    body = {"conversation_id": cid, "message_id": aid, "rating": "down", "user_id": "u1"}
    r1 = await client.post("/api/feedback", json=body)
    r2 = await client.post("/api/feedback", json=body)
    assert r1.status_code == 201
    assert r1.json()["duplicate"] is False
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True
    async with db() as s:
        rows = (await s.execute(select(LowConfidenceQuestion))).scalars().all()
    assert len(rows) == 1
    assert r1.json()["id"] == r2.json()["id"] == rows[0].id
    assert (rows[0].raw_question, rows[0].source, rows[0].retrieved_chunks) == (
        "X3 Pro 能游泳吗", "user_feedback", None)
    assert rows[0].reason == f"用户反馈未解决（回复 msg-{aid}）"


async def test_down_errors(db, client):
    cid, uid, aid = await _turn(db)
    base = {"conversation_id": cid, "rating": "down", "user_id": "u1"}
    assert (await client.post("/api/feedback", json={**base, "message_id": 999999})).status_code == 404
    assert (await client.post("/api/feedback", json={**base, "message_id": uid})).status_code == 422
    other, _, _ = await _turn(db)
    assert (await client.post("/api/feedback", json={
        **base, "conversation_id": other, "message_id": aid})).status_code == 404
    assert (await client.post("/api/feedback", json={
        **base, "message_id": aid, "user_id": "u2"})).status_code == 404
