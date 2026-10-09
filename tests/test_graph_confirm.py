import asyncio
import json
from datetime import date

import pytest
from langgraph.types import Command
from sqlalchemy import select

from app.db.models import Ticket, ToolAuditLog
from app.graph.builder import thread_config
from app.graph.state import GraphContext
from app.llm import get_chat_model
from app.main import app
from app.prompts import TICKET_CREATED_NOTE
from tests.fakes import text, tools
from tests.test_graph import new_cid, saved

pytestmark = pytest.mark.anyio

TICKET_CALL = ("t1", "create_ticket", {"description": "蓝牙耳机左耳没声音", "ticket_type": "售后"})
USER_INPUT = "蓝牙耳机左耳没声音，帮我建个工单"
PREVIEW = {"type": "ticket_confirm", "call_id": "t1", "ticket_type": "售后",
           "description": "蓝牙耳机左耳没声音"}


@pytest.fixture
def drive(memory_graph, use_script):
    async def run(cid, value):
        ctx = GraphContext(conversation_id=cid, today=date(2026, 10, 6),
                           model=app.dependency_overrides[get_chat_model](), user_id="u1")
        graph_input = value if isinstance(value, Command) else {"user_input": value}
        events = [chunk async for mode, chunk in memory_graph.astream(
            graph_input, thread_config(cid), context=ctx, stream_mode=["custom", "updates"],
        ) if mode == "custom" and chunk[0] != "saved"]
        return await memory_graph.aget_state(thread_config(cid)), events

    return run


async def ticket_rows(db, cid):
    async with db() as s:
        return (await s.scalars(select(Ticket).where(Ticket.conversation_id == cid))).all()


async def audit_rows(db, cid):
    async with db() as s:
        return (await s.scalars(select(ToolAuditLog).where(
            ToolAuditLog.conversation_id == cid, ToolAuditLog.tool_name == "create_ticket",
        ).order_by(ToolAuditLog.id))).all()


async def start(db, drive, use_script, use_resolver, use_intent, *calls):
    use_resolver({"ticket_request": True})
    use_intent("售后")
    rec = use_script(tools(*(calls or (TICKET_CALL,))), text("不应再调用模型"))
    cid = await new_cid(db)
    state, events = await drive(cid, USER_INPUT)
    assert len(state.interrupts) == 1
    assert state.interrupts[0].value == PREVIEW
    return cid, rec, state, events


async def test_ticket_preview_has_no_write_or_tool_events(db, db_audit, drive, use_script, use_resolver, use_intent):
    cid, rec, state, events = await start(db, drive, use_script, use_resolver, use_intent)
    assert state.next == ("confirm_write",)
    assert events == [("understood", {"resolved_input": USER_INPUT, "intent": "售后"})]
    assert len(rec) == 1
    assert await saved(db, cid) == []
    assert await ticket_rows(db, cid) == []
    assert await audit_rows(db, cid) == []


async def test_confirm_creates_ticket_with_fixed_reply(db, db_audit, drive, use_script, use_resolver, use_intent):
    cid, rec, _, _ = await start(db, drive, use_script, use_resolver, use_intent)
    state, events = await drive(cid, Command(resume={"confirmed": True}))
    [ticket] = await ticket_rows(db, cid)
    reply = TICKET_CREATED_NOTE.format(ticket_no=ticket.ticket_no, ticket_type="售后")
    assert (ticket.description, ticket.ticket_type) == ("蓝牙耳机左耳没声音", "售后")
    assert state.values["reply"] == reply
    assert state.values["agent_messages"][-1].content == reply
    assert state.values["write_decision"] == "confirmed"
    assert state.values["write_outcome"] == {"status": "成功", "ticket_no": ticket.ticket_no, "ticket_type": "售后"}
    assert state.values["trace"][-4:] == ["confirm_write", "agent_tools", "ticket_reply", "finalize"]
    assert events[-1] == ("token", {"text": reply})
    assert state.interrupts == () and len(rec) == 1
    [audit] = await audit_rows(db, cid)
    assert audit.status == "成功" and audit.retry_count == 0
    assert await saved(db, cid) == [("user", USER_INPUT), ("assistant", reply)]


@pytest.mark.parametrize("answer", [{"confirmed": False}, {"confirmed": 1}, {"confirmed": "true"}, {}, "cancel"])
async def test_cancel_does_not_create_ticket(db, db_audit, drive, use_script, use_resolver, use_intent, answer):
    from app.prompts import TICKET_CANCELLED_REPLY

    cid, rec, pending, _ = await start(db, drive, use_script, use_resolver, use_intent)
    # 空字典会被框架当成恢复映射，按 interrupt id 传入空答案。
    resume = {pending.interrupts[0].id: answer} if answer == {} else answer
    state, _ = await drive(cid, Command(resume=resume))
    assert await ticket_rows(db, cid) == []
    assert state.values["reply"] == TICKET_CANCELLED_REPLY
    assert state.values["write_decision"] == "cancelled"
    assert state.values["write_outcome"] == {"status": "权限拒绝", "ticket_no": None, "ticket_type": "售后"}
    [audit] = await audit_rows(db, cid)
    assert audit.status == "权限拒绝" and audit.error_message == "用户取消"
    assert len(rec) == 1
    assert await saved(db, cid) == [("user", USER_INPUT), ("assistant", TICKET_CANCELLED_REPLY)]


async def test_invalid_ticket_args_do_not_interrupt(db, db_audit, drive, use_script, use_resolver, use_intent):
    use_resolver({"ticket_request": True})
    use_intent("售后")
    rec = use_script(tools(("t1", "create_ticket", {"ticket_type": "售后"})), text("请描述一下遇到的问题"))
    cid = await new_cid(db)
    state, _ = await drive(cid, USER_INPUT)
    assert state.interrupts == ()
    assert state.values["reply"] == "请描述一下遇到的问题"
    assert state.values["write_decision"] is None and state.values["write_outcome"] is None
    assert state.values["approvals"].get("t1") != "approved"
    assert await ticket_rows(db, cid) == []
    [audit] = await audit_rows(db, cid)
    assert audit.status == "校验拦下"
    assert len(rec) == 2


async def test_two_ticket_calls_only_create_first(db, db_audit, drive, use_script, use_resolver, use_intent):
    second = ("t2", "create_ticket", {"description": "蓝牙耳机右耳断连", "ticket_type": "投诉"})
    cid, rec, _, _ = await start(db, drive, use_script, use_resolver, use_intent, TICKET_CALL, second)
    state, _ = await drive(cid, Command(resume={"confirmed": True}))
    [ticket] = await ticket_rows(db, cid)
    assert ticket.description == "蓝牙耳机左耳没声音"
    audits = {row.tool_call_id: row for row in await audit_rows(db, cid)}
    assert set(audits) == {"t1", "t2"}
    assert audits["t1"].status == "成功"
    assert audits["t2"].status == "权限拒绝" and audits["t2"].error_message == "一次只能提交一张工单"
    assert state.values["reply"] == TICKET_CREATED_NOTE.format(ticket_no=ticket.ticket_no, ticket_type="售后")
    assert state.values["write_outcome"]["status"] == "成功" and len(rec) == 1


async def test_ticket_timeout_does_not_retry(db, db_audit, drive, use_script, use_resolver, use_intent,
                                            tmp_path, monkeypatch):
    from app.prompts import TICKET_TIMEOUT_REPLY
    from app.repositories import tickets
    from app.tools import policy

    path = tmp_path / "tools.json"
    path.write_text(json.dumps({"servers": {}, "overrides": {"create_ticket": {"timeout_seconds": 0.01}}}))
    monkeypatch.setattr(policy, "TOOL_POLICY_PATH", path)
    calls = []

    async def slow(*args):
        calls.append(args)
        await asyncio.sleep(1)
        raise AssertionError("超时应取消仓储调用")

    monkeypatch.setattr(tickets, "create_ticket_record", slow)
    cid, rec, _, _ = await start(db, drive, use_script, use_resolver, use_intent)
    state, _ = await drive(cid, Command(resume={"confirmed": True}))
    assert state.values["reply"] == TICKET_TIMEOUT_REPLY
    assert state.values["write_outcome"] == {"status": "超时", "ticket_no": None, "ticket_type": "售后"}
    [audit] = await audit_rows(db, cid)
    assert audit.status == "超时" and audit.retry_count == 0
    assert len(calls) == len(rec) == 1 and await ticket_rows(db, cid) == []


async def test_ticket_without_request_is_not_open(db, db_audit, drive, use_script, use_resolver, use_intent):
    use_resolver({"ticket_request": False})
    use_intent("其他")
    rec = use_script(tools(TICKET_CALL), text("请点击按钮建工单"))
    cid = await new_cid(db)
    state, _ = await drive(cid, "耳机坏了")
    assert state.interrupts == () and "confirm_write" not in state.values["trace"]
    [audit] = await audit_rows(db, cid)
    assert audit.status == "权限拒绝" and audit.error_message == "工具未开放"
    assert state.values["reply"] == "请点击按钮建工单"
    assert len(rec) == 2 and await ticket_rows(db, cid) == []


async def test_confirm_survives_mcp_discovery_failure(db, db_audit, drive, use_script, use_resolver, use_intent,
                                                      monkeypatch):
    from app.tools import mcp

    cid, rec, _, _ = await start(db, drive, use_script, use_resolver, use_intent)
    discoveries = []

    async def broken(policy):
        discoveries.append(policy)
        raise RuntimeError("MCP 停止")

    monkeypatch.setattr(mcp, "discover", broken)
    state, _ = await drive(cid, Command(resume={"confirmed": True}))
    [ticket] = await ticket_rows(db, cid)
    assert state.values["reply"] == TICKET_CREATED_NOTE.format(ticket_no=ticket.ticket_no, ticket_type="售后")
    assert len(discoveries) == len(rec) == 1


async def test_turn_log_includes_ticket_confirmation(db, db_audit, drive, use_script, use_resolver, use_intent, caplog):
    caplog.set_level("INFO", logger="app.graph")
    cid, _, _, _ = await start(db, drive, use_script, use_resolver, use_intent)
    await drive(cid, Command(resume={"confirmed": True}))
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("turn "))
    assert "ticket_request=True" in line and "write=confirmed" in line


async def test_ticket_failure_uses_fixed_reply(db, db_audit, drive, use_script, use_resolver, use_intent, monkeypatch):
    from app.prompts import TICKET_FAILED_REPLY
    from app.repositories import tickets

    async def broken(*args):
        raise RuntimeError("建单失败")

    monkeypatch.setattr(tickets, "create_ticket_record", broken)
    cid, rec, _, _ = await start(db, drive, use_script, use_resolver, use_intent)
    state, _ = await drive(cid, Command(resume={"confirmed": True}))
    assert state.values["reply"] == TICKET_FAILED_REPLY
    assert await ticket_rows(db, cid) == [] and len(rec) == 1
    [audit] = await audit_rows(db, cid)
    assert audit.status == "失败" and audit.retry_count == 0


async def test_next_turn_resets_write_fields(db, db_audit, drive, use_script, use_resolver, use_intent):
    cid, _, _, _ = await start(db, drive, use_script, use_resolver, use_intent)
    await drive(cid, Command(resume={"confirmed": True}))
    use_resolver({"ticket_request": False})
    use_intent("其他")
    rec = use_script(text("还有什么需要帮助的"))
    state, _ = await drive(cid, "谢谢")
    assert state.values["approvals"] == {}
    assert state.values["write_decision"] is None and state.values["write_outcome"] is None
    assert state.values["reply"] == "还有什么需要帮助的" and len(rec) == 1
