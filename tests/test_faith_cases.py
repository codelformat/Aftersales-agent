from datetime import datetime

import pytest
from sqlalchemy import text as sql_text

from app.repositories import faith_cases as fc

pytestmark = pytest.mark.anyio
CITES = [{"n": 1, "chunk_id": 3, "section_path": "p", "question": "q", "answer": "a"}]


async def upsert(db, eval_id="A01", answer="a1", reason="r1"):
    async with db() as s:
        row = await fc.upsert_case(
            s, eval_id=eval_id, bucket="A_policy", query="q", answer=answer,
            reason=reason, citations=CITES, judge_model="m1",
        )
        await s.commit()
        await s.refresh(row)
        return row


async def test_insert_then_repeat_increments_and_updates_snapshot(db):
    first = await upsert(db)
    async with db() as s:
        await s.execute(sql_text("UPDATE faith_cases SET last_seen_at = '2026-01-01 00:00:00'"))
        await s.commit()
    second = await upsert(db, answer="a2", reason="r2")
    assert second.id == first.id and second.seen_count == 2
    assert (second.answer, second.reason, second.status) == ("a2", "r2", "未解决")
    assert second.last_seen_at > datetime(2026, 1, 1)
    assert second.first_seen_at == first.first_seen_at


@pytest.mark.parametrize("status", ["已解决", "无需解决"])
async def test_recurrence_reopens_and_clears_resolution(db, status):
    row = await upsert(db)
    async with db() as s:
        await fc.resolve_case(s, row.id, status, "补了文档")
        await s.commit()
    again = await upsert(db)
    assert (again.status, again.resolution) == ("未解决", None)
    assert again.resolved_at is not None  # 保留，用来标「复发」。


async def test_list_cases_filters_and_orders(db):
    a = await upsert(db, "A01")
    await upsert(db, "B01")
    async with db() as s:
        await fc.resolve_case(s, a.id, "已解决", "ok")
        await s.execute(sql_text(
            "UPDATE faith_cases SET last_seen_at = CASE "
            "WHEN eval_id = 'A01' THEN '2026-01-02 00:00:00' "
            "ELSE '2026-01-01 00:00:00' END"
        ))
        await s.commit()
    async with db() as s:
        assert [r.eval_id for r in await fc.list_cases(s, "未解决")] == ["B01"]
        assert [r.eval_id for r in await fc.list_cases(s)] == ["A01", "B01"]
        await s.execute(sql_text("UPDATE faith_cases SET last_seen_at = '2026-01-01 00:00:00'"))
        await s.commit()
    async with db() as s:
        assert [r.eval_id for r in await fc.list_cases(s)] == ["B01", "A01"]


async def test_resolve_missing_returns_none(db):
    async with db() as s:
        assert await fc.resolve_case(s, 999999, "已解决", "x") is None


async def test_api_list_and_resolve(client, db):
    row = await upsert(db, answer="运费 8 元[1]，包邮[3]")
    r = await client.get("/api/faith-cases", params={"status": "未解决"})
    assert r.status_code == 200
    [item] = r.json()
    assert item["eval_id"] == "A01" and item["cited"] == [1, 3] and item["citations"] == CITES
    r = await client.post(
        f"/api/faith-cases/{row.id}/resolve",
        json={"status": "已解决", "resolution": " 补了运费文档 "},
    )
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["resolution"]) == ("已解决", "补了运费文档")
    assert r.json()["resolved_at"] is not None


@pytest.mark.parametrize("body", [
    {"status": "已解决", "resolution": "   "},
    {"status": "已解决", "resolution": "字" * 301},
    {"status": "未解决", "resolution": "x"},
    {"status": "已解决"},
])
async def test_api_resolve_validation(client, db, body):
    row = await upsert(db)
    assert (await client.post(f"/api/faith-cases/{row.id}/resolve", json=body)).status_code == 422


async def test_api_resolve_missing_is_404(client, db):
    r = await client.post(
        "/api/faith-cases/999999/resolve", json={"status": "已解决", "resolution": "x"},
    )
    assert r.status_code == 404


async def test_admin_page_served(client):
    r = await client.get("/admin/faith-cases")
    assert r.status_code == 200 and "编造个案台账" in r.text
