import pytest
from langchain_core.runnables import RunnableLambda

from app.graph.builder import thread_config
from app.graph.nodes import aftersales as aftersales_nodes
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.prompts import TICKET_CANCELLED_REPLY, TICKET_CREATED_NOTE
from app.schemas import SelfCheck
from app.services import grounding
from tests.fakes import text, tools
from tests.test_chat_api import chat, parse_sse, rows
from tests.test_graph_confirm import TICKET_CALL, USER_INPUT, audit_rows, ticket_rows

pytestmark = pytest.mark.anyio


def kb_multi(monkeypatch):
    async def fake(queries, plan, top_n=None):
        item = EvidenceItem(7, "退货政策 > 条件", "能退吗", "签收 7 天内可退", 0.9)
        return Retrieval(plan, [item], [item])

    async def check(_):
        return {"parsed": SelfCheck(useful=True, reason="r"), "raw": None}

    monkeypatch.setattr(aftersales_nodes, "retrieve_multi", fake)
    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))


async def resume(client, session_id, order_id, user_id="u1"):
    r = await client.post("/chat/resume", json={"session_id": session_id, "user_id": user_id, "order_id": order_id})
    return r, (parse_sse(r.text) if r.status_code == 200 else None)


async def start_picker(client, use_resolver, use_intent):
    use_resolver({"resolved_input": "我要退货", "standard_query": "退货流程", "order_scoped": True})
    use_intent("退款退货")
    r, ev = await chat(client, "我要退货")
    return ev[0][1]["session_id"], ev


async def start_ticket(client, use_script, use_resolver, use_intent):
    use_resolver({"ticket_request": True})
    use_intent("售后")
    use_script(tools(TICKET_CALL))
    r, ev = await chat(client, USER_INPUT)
    assert r.status_code == 200
    return ev[0][1]["session_id"], ev


async def resume_ticket(client, session_id, confirmed):
    r = await client.post("/chat/resume", json={
        "session_id": session_id, "user_id": "u1", "ticket_confirm": confirmed,
    })
    return r, (parse_sse(r.text) if r.status_code == 200 else None)


async def test_stream_emits_order_picker_and_interrupted(client, db, use_script, use_intent, use_resolver):
    use_script()
    sid, ev = await start_picker(client, use_resolver, use_intent)
    assert [e for e, _ in ev] == ["session", "understood", "order_picker", "done"]
    assert ev[-1][1] == {"finish_reason": "interrupted"}
    orders = ev[2][1]["orders"]
    assert len(orders) == 3 and set(orders[0]) == {"order_id", "title", "total", "created_at", "status"}
    assert await rows(db) == []


async def test_resume_finishes_subflow(client, db, use_script, use_intent, use_resolver, use_expander, monkeypatch):
    use_script(text("可以退[1]"))
    use_expander(["退货运费"])
    kb_multi(monkeypatch)
    sid, ev = await start_picker(client, use_resolver, use_intent)
    r, ev2 = await resume(client, sid, ev[2][1]["orders"][0]["order_id"])
    assert r.status_code == 200
    names = [e for e, _ in ev2]
    assert names[0] == "session" and "citations" in names and names[-1] == "done"
    assert ev2[-1][1] == {"finish_reason": "stop", "message_id": (await rows(db))[-1].id}
    assert [(m.role, m.content) for m in await rows(db)] == [("user", "我要退货"), ("assistant", "可以退[1]")]


@pytest.mark.parametrize("kind, expected_intent", [("order", "退款退货"), ("ticket", "售后")])
async def test_resume_stream_config_carries_state_intent(
    client, db, memory_graph, use_script, use_intent, use_resolver, use_expander, monkeypatch,
    kind, expected_intent,
):
    if kind == "order":
        use_script(text("可以退[1]"))
        use_expander(["退货运费"])
        kb_multi(monkeypatch)
        sid, events = await start_picker(client, use_resolver, use_intent)
    else:
        sid, events = await start_ticket(client, use_script, use_resolver, use_intent)
    state = await memory_graph.aget_state(thread_config(int(sid)))
    assert state.values["intent"] == expected_intent
    configs = []
    real_astream = memory_graph.astream

    async def capture(graph_input, config, **kwargs):
        configs.append(config)
        async for event in real_astream(graph_input, config, **kwargs):
            yield event

    monkeypatch.setattr(memory_graph, "astream", capture)
    if kind == "order":
        response, resumed = await resume(client, sid, events[2][1]["orders"][0]["order_id"])
    else:
        response, resumed = await resume_ticket(client, sid, True)
    assert response.status_code == 200
    assert resumed[-1][0] == "done"
    assert len(configs) == 1
    assert configs[0]["metadata"].get("intent") == expected_intent


async def test_resume_twice_is_409(client, db, use_script, use_intent, use_resolver, use_expander, monkeypatch):
    use_script(text("可以退[1]"))
    use_expander(["退货运费"])
    kb_multi(monkeypatch)
    sid, ev = await start_picker(client, use_resolver, use_intent)
    oid = ev[2][1]["orders"][0]["order_id"]
    await resume(client, sid, oid)
    r, _ = await resume(client, sid, oid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_pending_selection"
    assert len(await rows(db)) == 2


async def test_resume_with_unknown_order_is_422(client, db, use_script, use_intent, use_resolver):
    use_script()
    sid, _ = await start_picker(client, use_resolver, use_intent)
    r, _ = await resume(client, sid, "999")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_order"


async def test_resume_without_picker_is_409(client, db, use_script, use_intent, use_resolver):
    use_resolver({})
    use_intent("闲聊")
    use_script()
    _, ev = await chat(client, "你好")
    r, _ = await resume(client, ev[0][1]["session_id"], "1001")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_pending_selection"


async def test_resume_other_users_session_is_404(client, db, use_script, use_intent, use_resolver):
    use_script()
    sid, ev = await start_picker(client, use_resolver, use_intent)
    r, _ = await resume(client, sid, ev[2][1]["orders"][0]["order_id"], user_id="u2")
    assert r.status_code == 404


async def test_resume_while_busy_is_409(client, db, use_script, use_intent, use_resolver, locks):
    use_script()
    sid, ev = await start_picker(client, use_resolver, use_intent)
    lock = locks.get(int(sid))
    await lock.acquire()
    try:
        r, _ = await resume(client, sid, ev[2][1]["orders"][0]["order_id"])
        assert r.status_code == 409 and r.json()["detail"]["code"] == "session_busy"
    finally:
        lock.release()


async def test_new_message_discards_pending_picker(client, db, use_script, use_intent, use_resolver):
    use_script()
    sid, ev = await start_picker(client, use_resolver, use_intent)
    use_resolver({})
    use_intent("闲聊")
    _, ev2 = await chat(client, "算了，你好", session_id=sid)
    assert ev2[-1] == ("done", {"finish_reason": "stop", "message_id": (await rows(db))[-1].id})
    r, _ = await resume(client, sid, ev[2][1]["orders"][0]["order_id"])
    assert r.status_code == 409


async def test_resume_failure_writes_nothing(client, db, use_script, use_intent, use_resolver, use_expander,
                                             monkeypatch):
    use_script([RuntimeError("upstream down")])
    use_expander(["退货运费"])
    kb_multi(monkeypatch)
    sid, ev = await start_picker(client, use_resolver, use_intent)
    oid = ev[2][1]["orders"][0]["order_id"]
    r, ev2 = await resume(client, sid, oid)
    assert ev2[-1] == ("error", {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"})
    assert await rows(db) == []
    r, _ = await resume(client, sid, oid)
    assert r.status_code == 409


async def test_stream_emits_ticket_preview_and_interrupted(client, db, db_audit, use_script,
                                                         use_resolver, use_intent):
    sid, ev = await start_ticket(client, use_script, use_resolver, use_intent)
    assert [e for e, _ in ev] == ["session", "understood", "ticket_preview", "done"]
    assert ev[2][1] == {"call_id": "t1", "ticket_type": "售后", "description": "蓝牙耳机左耳没声音"}
    assert ev[-1] == ("done", {"finish_reason": "interrupted"})
    assert await ticket_rows(db, int(sid)) == []
    assert await audit_rows(db, int(sid)) == []
    assert await rows(db) == []


async def test_resume_confirm_creates_ticket(client, db, use_script, use_resolver, use_intent):
    sid, _ = await start_ticket(client, use_script, use_resolver, use_intent)
    r, ev = await resume_ticket(client, sid, True)
    assert r.status_code == 200
    [ticket] = await ticket_rows(db, int(sid))
    reply = TICKET_CREATED_NOTE.format(ticket_no=ticket.ticket_no, ticket_type="售后")
    assert (ticket.description, ticket.ticket_type) == ("蓝牙耳机左耳没声音", "售后")
    assert "token" in [e for e, _ in ev]
    assert "".join(d["text"] for e, d in ev if e == "token") == reply
    assert ev[-1] == ("done", {"finish_reason": "stop", "message_id": (await rows(db))[-1].id})
    assert [(m.role, m.content) for m in await rows(db)] == [("user", USER_INPUT), ("assistant", reply)]


async def test_resume_cancel_does_not_create_ticket(client, db, use_script, use_resolver, use_intent):
    sid, _ = await start_ticket(client, use_script, use_resolver, use_intent)
    r, ev = await resume_ticket(client, sid, False)
    assert r.status_code == 200
    assert "".join(d["text"] for e, d in ev if e == "token") == TICKET_CANCELLED_REPLY
    assert ev[-1] == ("done", {"finish_reason": "stop", "message_id": (await rows(db))[-1].id})
    assert await ticket_rows(db, int(sid)) == []


@pytest.mark.parametrize("fields", [
    {}, {"order_id": None, "ticket_confirm": None},
    {"order_id": "1001", "ticket_confirm": True},
    {"order_id": "1001", "ticket_confirm": False},
])
async def test_resume_requires_exactly_one_choice(client, db, use_script, use_resolver, use_intent, fields):
    use_script()
    sid, _ = await start_picker(client, use_resolver, use_intent)
    r = await client.post("/chat/resume", json={"session_id": sid, "user_id": "u1", **fields})
    assert r.status_code == 422
    assert "order_id 和 ticket_confirm 必须恰好提供一个" in r.json()["detail"][0]["msg"]


async def test_order_resume_rejects_pending_ticket(client, db, use_script, use_resolver, use_intent):
    sid, _ = await start_ticket(client, use_script, use_resolver, use_intent)
    r, _ = await resume(client, sid, "1001")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_pending_selection"
    assert await ticket_rows(db, int(sid)) == []


async def test_ticket_resume_rejects_pending_picker(client, db, use_script, use_resolver, use_intent):
    use_script()
    sid, _ = await start_picker(client, use_resolver, use_intent)
    r, _ = await resume_ticket(client, sid, True)
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "no_pending_confirmation", "message": "没有待确认的工单，请重新提问",
    }


async def test_ticket_resume_twice_is_409(client, db, use_script, use_resolver, use_intent):
    sid, _ = await start_ticket(client, use_script, use_resolver, use_intent)
    r, _ = await resume_ticket(client, sid, True)
    assert r.status_code == 200
    r, _ = await resume_ticket(client, sid, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_pending_confirmation"
    assert len(await ticket_rows(db, int(sid))) == 1


async def test_new_message_audits_discarded_ticket(client, db, db_audit, use_script,
                                                use_resolver, use_intent):
    sid, _ = await start_ticket(client, use_script, use_resolver, use_intent)
    use_resolver({})
    use_intent("闲聊")
    r, ev = await chat(client, "算了，你好", session_id=sid)
    assert r.status_code == 200 and ev[-1] == ("done", {"finish_reason": "stop", "message_id": (await rows(db))[-1].id})
    [audit] = await audit_rows(db, int(sid))
    assert audit.tool_call_id == "t1" and audit.tool_source == "builtin" and audit.mcp_server is None
    assert audit.arguments == {"description": "蓝牙耳机左耳没声音", "ticket_type": "售后"}
    assert audit.status == "权限拒绝" and audit.error_message == "用户未确认，已被新消息取代"
    assert audit.result_summary is None and audit.retry_count == 0 and audit.duration_ms is None
    r, _ = await resume_ticket(client, sid, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_pending_confirmation"
    assert await ticket_rows(db, int(sid)) == []


async def test_discard_audit_failure_does_not_block_new_message(client, db, use_script,
                                                             use_resolver, use_intent, caplog):
    from app.tools import audit

    sid, _ = await start_ticket(client, use_script, use_resolver, use_intent)

    async def broken(rec):
        raise RuntimeError("审计不可用")

    audit.set_audit_writer(broken)
    use_resolver({})
    use_intent("闲聊")
    r, ev = await chat(client, "算了，你好", session_id=sid)
    assert r.status_code == 200 and ev[-1] == ("done", {"finish_reason": "stop", "message_id": (await rows(db))[-1].id})
    assert "audit_write_failed" in caplog.text
    r, _ = await resume_ticket(client, sid, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_pending_confirmation"
    assert await ticket_rows(db, int(sid)) == []
