from datetime import datetime

import pytest
from sqlalchemy import update

from app.db.models import Conversation
from app.repositories import conversations, messages
from app.repositories.messages import NewMessage

pytestmark = pytest.mark.anyio


async def seed(db, user_id, rows, **cols):
    async with db() as s:
        cid = (await conversations.create(s, user_id)).id
        await messages.add_turn(s, cid, [NewMessage(**r) for r in rows])
        if cols:
            await s.execute(update(Conversation).where(Conversation.id == cid).values(**cols))
        await s.commit()
    return cid


async def test_list_conversations_newest_first(client, db):
    a = await seed(db, "u1", [
        {"role": "user", "content": "订单 1001 到哪了" * 5},
        {"role": "assistant", "content": "已发货"},
    ], updated_at=datetime(2030, 1, 1))
    b = await seed(db, "u1", [{"role": "user", "content": "运费谁出"}], summary_upto_msg_id=1)
    await seed(db, "u2", [{"role": "user", "content": "别人的"}])
    r = await client.get("/api/conversations", params={"user_id": "u1"})
    assert r.status_code == 200
    items = r.json()
    assert [i["session_id"] for i in items] == [str(b), str(a)]
    assert items[0]["summarized"] is True and items[1]["summarized"] is False
    assert items[1]["preview"] == ("订单 1001 到哪了" * 5)[:30]
    for item in items:
        assert set(item) == {"session_id", "created_at", "updated_at", "preview", "summarized"}
        datetime.fromisoformat(item["created_at"])
        datetime.fromisoformat(item["updated_at"])


async def test_empty_conversation_has_empty_preview(client, db):
    await seed(db, "u1", [])
    r = await client.get("/api/conversations", params={"user_id": "u1"})
    assert r.status_code == 200
    assert r.json()[0]["preview"] == ""


async def test_messages_returns_user_and_assistant_text_only(client, db):
    cid = await seed(db, "u1", [
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "name": "query_order", "args": {}}]},
        {"role": "tool", "content": "{}", "tool_call_id": "c1"},
        {"role": "assistant", "content": "答"},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": None},
    ])
    r = await client.get(f"/api/conversations/{cid}/messages", params={"user_id": "u1"})
    assert r.status_code == 200
    items = r.json()
    assert [(m["role"], m["content"]) for m in items] == [("user", "问"), ("assistant", "答")]
    assert items[0]["id"] < items[1]["id"]
    for item in items:
        assert set(item) == {"id", "role", "content", "created_at"}
        assert isinstance(item["id"], int)
        datetime.fromisoformat(item["created_at"])


async def test_messages_of_other_user_is_404(client, db):
    cid = await seed(db, "u2", [{"role": "user", "content": "x"}])
    r = await client.get(f"/api/conversations/{cid}/messages", params={"user_id": "u1"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversation_not_found"


async def test_list_conversations_limits_to_latest_50(client, db):
    async with db() as s:
        ids = [(await conversations.create(s, "u1")).id for _ in range(51)]
        await s.commit()
    r = await client.get("/api/conversations", params={"user_id": "u1"})
    assert r.status_code == 200
    assert [item["session_id"] for item in r.json()] == [str(cid) for cid in reversed(ids[1:])]


async def test_preview_uses_first_user_message(client, db):
    await seed(db, "u1", [
        {"role": "assistant", "content": "开场白"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "答"},
        {"role": "user", "content": "第二问"},
    ])
    r = await client.get("/api/conversations", params={"user_id": "u1"})
    assert r.status_code == 200
    assert r.json()[0]["preview"] == "第一问"


async def test_conversation_without_user_messages_has_empty_preview(client, db):
    await seed(db, "u1", [{"role": "assistant", "content": "已创建工单"}])
    r = await client.get("/api/conversations", params={"user_id": "u1"})
    assert r.status_code == 200
    assert r.json()[0]["preview"] == ""


async def test_list_for_user_honors_custom_limit(db):
    a = await seed(db, "u1", [{"role": "user", "content": "第一问"}])
    b = await seed(db, "u1", [])
    await seed(db, "u2", [{"role": "user", "content": "别人的"}])
    async with db() as s:
        rows = await conversations.list_for_user(s, "u1", limit=1)
        assert [(c.id, p) for c, p in rows] == [(b, None)]
        rows = await conversations.list_for_user(s, "u1", limit=2)
        assert [(c.id, p) for c, p in rows] == [(b, None), (a, "第一问")]


async def test_unknown_user_has_no_conversations(client, db):
    await seed(db, "u2", [])
    r = await client.get("/api/conversations", params={"user_id": "u1"})
    assert r.status_code == 200 and r.json() == []


async def test_missing_conversation_is_404(client, db):
    cid = await seed(db, "u1", [])
    r = await client.get(f"/api/conversations/{cid + 1}/messages", params={"user_id": "u1"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversation_not_found"


async def test_empty_conversation_has_no_messages(client, db):
    cid = await seed(db, "u1", [])
    r = await client.get(f"/api/conversations/{cid}/messages", params={"user_id": "u1"})
    assert r.status_code == 200 and r.json() == []


@pytest.mark.parametrize("path", ["/api/conversations", "/api/conversations/1/messages"])
@pytest.mark.parametrize("params", [{}, {"user_id": ""}, {"user_id": "bad user"}, {"user_id": "x" * 65}])
async def test_user_id_validation_is_422(client, path, params):
    r = await client.get(path, params=params)
    assert r.status_code == 422
