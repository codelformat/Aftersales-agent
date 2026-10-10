import pytest

from app.db.models import ToolAuditLog

pytestmark = pytest.mark.anyio


async def test_audit_fields_descending_limit_and_status(client, db):
    async with db() as session:
        rows = [ToolAuditLog(tool_name="query_order", tool_source="builtin", status=status,
                             tool_call_id=f"call-{i}", conversation_id=None, mcp_server=None,
                             arguments={"private": "hidden"}, result_summary="订单结果",
                             error_message="查询落空" if i == 2 else None,
                             retry_count=i, duration_ms=10 + i)
                for i, status in enumerate(("成功", "超时", "成功"))]
        session.add_all(rows)
        await session.commit()
        ids = [r.id for r in rows]
    response = await client.get("/api/tool-audit?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert [r["id"] for r in data] == ids[:0:-1]
    assert set(data[0]) == {"id", "created_at", "conversation_id", "tool_call_id", "tool_name",
                           "tool_source", "mcp_server", "status", "retry_count", "duration_ms",
                           "error_message", "result_summary"}
    assert data[0]["created_at"] and data[0]["conversation_id"] is None
    assert (data[0]["retry_count"], data[0]["duration_ms"], data[0]["error_message"],
            data[0]["result_summary"]) == (2, 12, "查询落空", "订单结果")
    filtered = await client.get("/api/tool-audit?status=成功")
    assert [r["id"] for r in filtered.json()] == [ids[2], ids[0]]
    assert (await client.get("/api/tool-audit?status=失败")).json() == []
    assert (await client.get("/api/tool-audit?status=&limit=500")).status_code == 200


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "status=unknown"])
async def test_audit_rejects_invalid_filter(client, query):
    assert (await client.get(f"/api/tool-audit?{query}")).status_code == 422
