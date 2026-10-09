import pytest
from langchain_core.runnables import RunnableLambda

from app.graph.nodes import aftersales as aftersales_nodes
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.schemas import SelfCheck
from app.services import grounding
from tests.fakes import text
from tests.test_chat_api import chat, parse_sse, rows

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
    assert ev2[-1][1] == {"finish_reason": "stop"}
    assert [(m.role, m.content) for m in await rows(db)] == [("user", "我要退货"), ("assistant", "可以退[1]")]


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
    assert ev2[-1] == ("done", {"finish_reason": "stop"})
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
