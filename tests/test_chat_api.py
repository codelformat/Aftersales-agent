import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.api.chat import get_token_budget
from app.db.models import LowConfidenceQuestion, Message, Ticket
from app.knowledge.rerank import RerankError
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.llm import get_chat_model
from app.main import app
from app.prompts import REFUSAL_PREFIX
from app.repositories import low_confidence
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import text, tools

pytestmark = pytest.mark.anyio
UPSTREAM_ERROR = ("error", {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"})


def parse_sse(body):
    events = []
    for block in body.strip().split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        events.append((name, data))
    return events


async def chat(client, message, session_id=None, user_id="u1"):
    body = {"user_id": user_id, "message": message}
    if session_id is not None:
        body["session_id"] = session_id
    r = await client.post("/chat/stream", json=body)
    return r, (parse_sse(r.text) if r.status_code == 200 else None)


async def rows(db):
    async with db() as s:
        return (await s.execute(select(Message).order_by(Message.id))).scalars().all()


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_plain_answer_single_call(client, db, use_script):
    rec = use_script(text("您好"))
    r, ev = await chat(client, "你好")
    assert ev[0][0] == "session" and ev[0][1]["session_id"].isdigit()
    assert ev[1:] == [("token", {"text": "您"}), ("token", {"text": "好"}), ("done", {"finish_reason": "stop"})]
    assert len(rec) == 1 and len(rec[0]["tools"]) == 5
    assert [(m.role, m.content) for m in await rows(db)] == [("user", "你好"), ("assistant", "您好")]
    assert "\\u" not in r.text


async def test_tool_round_events_and_persistence(client, db, use_script):
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    names = [e for e, _ in ev]
    assert names == ["session", "tool_start", "tool_end", "token", "token", "token", "done"]
    assert ev[1][1] == {"tools": [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]}
    assert ev[2][1] == {"tools": [{"id": "c1", "name": "query_logistics", "ok": True}]}
    assert rec[1]["tools"] == []
    second_input = rec[1]["messages"]
    assert isinstance(second_input[-3], AIMessage) and isinstance(second_input[-2], ToolMessage)
    assert isinstance(second_input[-1], SystemMessage) and "本轮不能再调用任何工具" in second_input[-1].content
    assert json.loads(second_input[-2].content)["data"]["status"] == "运输中"
    saved = await rows(db)
    assert [m.role for m in saved] == ["user", "assistant", "tool", "assistant"]
    assert saved[1].content is None and saved[1].tool_calls[0]["name"] == "query_logistics"
    assert saved[2].tool_call_id == "c1" and saved[3].content == "运输中"


async def test_parallel_tools_in_one_round(client, db, use_script):
    use_script(tools(("c1", "query_order", {"order_id": "1001"}), ("c2", "query_logistics", {"order_id": "1001"})),
               text("好"))
    _, ev = await chat(client, "订单 1001 买了什么、到哪了")
    assert [t["name"] for t in ev[1][1]["tools"]] == ["query_order", "query_logistics"]
    assert [m.role for m in await rows(db)] == ["user", "assistant", "tool", "tool", "assistant"]


async def test_create_ticket_injects_conversation(client, db, use_script):
    use_script(tools(("c1", "create_ticket", {"description": "要人工", "ticket_type": "投诉"})), text("已建单"))
    _, ev = await chat(client, "我要投诉，转人工")
    cid = int(ev[0][1]["session_id"])
    assert ev[2][1]["tools"][0]["ok"] is True
    async with db() as s:
        ticket = (await s.execute(select(Ticket))).scalar_one()
    assert ticket.conversation_id == cid


async def test_second_turn_sees_previous_tool_result(client, db, use_script):
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"), text("明天到"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    sid = ev[0][1]["session_id"]
    await chat(client, "那哪天到？", session_id=sid)
    third = rec[2]["messages"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in third)


async def test_other_users_conversation_is_404(client, db, use_script):
    use_script(text("好"))
    _, ev = await chat(client, "你好", user_id="alice")
    r, _ = await chat(client, "你好", session_id=ev[0][1]["session_id"], user_id="bob")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversation_not_found"


async def test_unknown_session_is_404(client, db, use_script):
    use_script(text("好"))
    r, _ = await chat(client, "你好", session_id="999999")
    assert r.status_code == 404


async def test_upstream_error_in_first_call_writes_nothing(client, db, use_script):
    use_script([*text("您"), RuntimeError("boom")])
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_upstream_error_in_second_call_writes_nothing(client, db, use_script):
    use_script(tools(("c1", "query_order", {"order_id": "1001"})), [RuntimeError("boom")])
    _, ev = await chat(client, "订单 1001")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_empty_reply_is_error(client, db, use_script):
    use_script([AIMessageChunk(content="")])
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_budget_exceeded(client, db, use_script):
    use_script(text("x"))
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "你好")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "budget_exceeded"


async def test_validation(client, db, use_script):
    use_script(text("x"))
    for body in ({"user_id": "u1", "message": "  "}, {"message": "hi"},
                 {"user_id": "u 1", "message": "hi"}, {"user_id": "u1", "message": "hi", "session_id": "abc"}):
        assert (await client.post("/chat/stream", json=body)).status_code == 422


async def test_lock_held_during_stream_and_released_after(client, db, use_script, locks):
    gate = asyncio.Event()
    use_script([*text("a"), gate, *text("b")])
    task = asyncio.create_task(chat(client, "hi"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if any(l.locked() for l in locks._locks.values()):
            break
    cid = next(k for k, l in locks._locks.items() if l.locked())
    busy, _ = await chat(client, "again", session_id=str(cid))
    assert busy.status_code == 409
    gate.set()
    _, ev = await task
    assert ev[-1] == ("done", {"finish_reason": "stop"})
    assert not locks.get(cid).locked()


async def test_lock_released_when_model_dependency_fails(client, db, locks):
    def boom():
        raise RuntimeError("config error")

    app.dependency_overrides[get_chat_model] = boom
    with pytest.raises(RuntimeError):
        await chat(client, "你好")
    assert not any(l.locked() for l in locks._locks.values())


async def test_disconnect_after_tools_writes_no_messages(db, locks):
    from app.services.chat import ChatTurn, stream_reply
    from app.repositories import conversations
    from tests.fakes import ScriptedChatModel
    from datetime import date

    async with db() as s:
        conv = await conversations.create(s, "u1")
        await s.commit()
    model = ScriptedChatModel(scripts=[tools(("c1", "create_ticket", {"description": "人工", "ticket_type": "投诉"})),
                                       text("已建单")])
    gen = stream_reply(ChatTurn(conversation_id=conv.id, history=[], user_input="转人工", today=date(2026, 10, 6)), model)
    names = []
    async for name, _ in gen:
        names.append(name)
        if name == "tool_end":
            break
    await gen.aclose()
    assert names == ["session", "tool_start", "tool_end"]
    assert await rows(db) == []
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalar_one().conversation_id == conv.id


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_tool_markup_at_start_of_second_call_is_error(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "邮费"})),
               text('<｜｜DSML｜｜ invoke name="query_faq">'))
    _, ev = await chat(client, "邮费是多少")
    assert [e for e, _ in ev] == ["session", "tool_start", "tool_end", "error"]
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_tool_markup_later_in_second_call_is_not_saved(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "邮费"})),
               text('没查到。<｜｜DSML｜｜ invoke name="query_faq">'))
    _, ev = await chat(client, "邮费是多少")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_normal_second_call_text_still_streams_per_chunk(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "退货"})), text("可以退"))
    _, ev = await chat(client, "能退吗")
    assert [d["text"] for e, d in ev if e == "token"] == ["可", "以", "退"]
    assert ev[-1] == ("done", {"finish_reason": "stop"})


async def test_persist_failure_sends_error(client, db, use_script, monkeypatch):
    from app.services import chat as chat_service

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service.messages, "add_turn", boom)
    use_script(text("您好"))
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR


async def test_tool_execution_crash_sends_error(db, locks):
    from datetime import date

    from app.repositories import conversations
    from app.services.chat import ChatTurn, stream_reply
    from tests.fakes import ScriptedChatModel

    async with db() as s:
        conv = await conversations.create(s, "u1")
        await s.commit()

    async def boom(*args, **kwargs):
        raise RuntimeError("executor crashed")

    model = ScriptedChatModel(scripts=[tools(("c1", "query_order", {"order_id": "1001"}))])
    turn = ChatTurn(conversation_id=conv.id, history=[], user_input="订单", today=date(2026, 10, 6))
    events = [e async for e in stream_reply(turn, model, execute=boom)]
    assert events[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


# ch03 起 query_faq 走向量检索；本测试未启用 fixture milvus，工具返回 tool_error，测试只验证事件和写库行为
async def test_tool_markup_after_leading_whitespace_is_not_streamed(client, db, use_script):
    use_script(tools(("c1", "query_faq", {"keyword": "邮费"})),
               text('\n <｜｜DSML｜｜ invoke name="query_faq">'))
    _, ev = await chat(client, "邮费是多少")
    assert [e for e, _ in ev] == ["session", "tool_start", "tool_end", "error"]


async def test_budget_exceeded_on_new_session_creates_no_conversation(client, db, use_script):
    from app.db.models import Conversation

    use_script(text("x"))
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "你好")
    assert r.status_code == 422
    async with db() as s:
        assert (await s.execute(select(Conversation))).scalars().all() == []


async def test_budget_exceeded_on_existing_session_releases_lock(client, db, use_script, locks):
    use_script(text("好"))
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "再问一句", session_id=sid)
    assert r.status_code == 422
    assert not locks.get(int(sid)).locked()


async def test_tool_args_split_across_chunks_and_tool_choice_auto(client, db, use_script):
    from langchain_core.messages.tool import tool_call_chunk

    split = [
        AIMessageChunk(content="", tool_call_chunks=[tool_call_chunk(name="query_logistics", args='{"order_', id="c1", index=0)]),
        AIMessageChunk(content="", tool_call_chunks=[tool_call_chunk(name=None, args='id": "1001"}', id=None, index=0)]),
    ]
    rec = use_script(split, text("好"))
    _, ev = await chat(client, "物流")
    assert ev[1][1]["tools"][0]["args"] == {"order_id": "1001"}
    assert rec[0]["tool_choice"] == "auto"
    assert rec[1]["tool_choice"] is None


async def test_tool_round_persist_failure_sends_error(client, db, use_script, monkeypatch):
    from app.services import chat as chat_service

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service.messages, "add_turn", boom)
    use_script(tools(("c1", "query_order", {"order_id": "1001"})), text("好"))
    _, ev = await chat(client, "订单")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_busy_session_does_not_read_history(client, db, use_script, locks, monkeypatch):
    from app.repositories import messages

    use_script(text("好"))
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    reads = []

    async def record_read(*args, **kwargs):
        reads.append(args)
        return []

    monkeypatch.setattr(messages, "list_for_conversation", record_read)
    lock = locks.get(int(sid))
    await lock.acquire()
    try:
        r, _ = await chat(client, "再问一句", session_id=sid)
        assert r.status_code == 409
        assert reads == []
    finally:
        lock.release()


async def test_existing_session_holds_lock_while_reading_history(client, db, use_script, locks, monkeypatch):
    from app.repositories import messages

    use_script(text("好"))
    _, ev = await chat(client, "你好")
    sid = ev[0][1]["session_id"]
    observed = []
    original = messages.list_for_conversation

    async def record_read(session, conversation_id):
        observed.append(locks.get(conversation_id).locked())
        return await original(session, conversation_id)

    monkeypatch.setattr(messages, "list_for_conversation", record_read)
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "再问一句", session_id=sid)
    assert r.status_code == 422
    assert observed == [True]
    assert not locks.get(int(sid)).locked()


def ev_item(cid, path="商品手册 > 蓝牙耳机 > X3 Pro 续航", q="X3 Pro 续航", a="单次续航 8 小时"):
    return EvidenceItem(cid, path, q, a, 0.9)


@pytest.fixture
def kb(monkeypatch):
    state = {"evidence": [], "by_question": {}, "check": SelfCheck(useful=True, reason="[1]"),
             "check_calls": [], "retrieve_error": None}

    async def fake_retrieve(question, *a, **k):
        if state["retrieve_error"] is not None:
            raise state["retrieve_error"]
        items = state["by_question"].get(question, state["evidence"])
        return Retrieval(QueryPlan(standard_query=question), items, items)

    def factory():
        def run(inputs):
            state["check_calls"].append(inputs)
            if isinstance(state["check"], Exception):
                raise state["check"]
            return {"parsed": state["check"], "raw": None}
        return RunnableLambda(run)

    monkeypatch.setattr("app.tools.faq.retrieve", fake_retrieve)
    monkeypatch.setattr(grounding, "get_self_checker", factory)
    return state


async def pool_rows(db):
    async with db() as s:
        return list(await s.scalars(select(LowConfidenceQuestion).order_by(LowConfidenceQuestion.id)))


def event(ev, name):
    return next(d for n, d in ev if n == name)


def tool_contents(rec_call):
    return {m.tool_call_id: m.content for m in rec_call["messages"] if isinstance(m, ToolMessage)}


async def test_faq_useful_sends_citations_and_renders_evidence(client, db, use_script, kb):
    kb["evidence"] = [ev_item(11)]
    rec = use_script(tools(("c1", "query_faq", {"question": "X3 Pro 能用多久"})), text("约 8 小时[1]"))
    _, ev = await chat(client, "X3 Pro 能用多久")
    names = [n for n, _ in ev]
    assert names.index("tool_end") < names.index("citations") < names.index("token") < names.index("done")
    assert event(ev, "citations") == {"items": [{
        "n": 1, "chunk_id": 11, "section_path": "商品手册 > 蓝牙耳机 > X3 Pro 续航",
        "question": "X3 Pro 续航", "answer": "单次续航 8 小时",
    }], "refused": False}
    content = tool_contents(rec[1])["c1"]
    assert json.loads(content) == {"ok": True, "data": {"evidence": [{
        "n": 1, "section_path": "商品手册 > 蓝牙耳机 > X3 Pro 续航", "content": "问：X3 Pro 续航\n答：单次续航 8 小时",
    }]}}
    assert [m.content for m in await rows(db) if m.role == "tool"] == [content]
    assert kb["check_calls"][0]["question"] == "X3 Pro 能用多久"
    assert await pool_rows(db) == []


async def test_faq_not_useful_records_pool_and_refuses(client, db, use_script, kb):
    kb["evidence"] = [ev_item(11)]
    kb["check"] = SelfCheck(useful=False, reason="证据没写防水")
    rec = use_script(tools(("c1", "query_faq", {"question": "X3 Pro 防水吗"})),
                     text(REFUSAL_PREFIX + "建议转人工。"))
    _, ev = await chat(client, "X3 Pro 防水吗？着急")
    assert event(ev, "citations") == {"items": [], "refused": True}
    assert tool_contents(rec[1])["c1"] == grounding.REFUSED_CONTENT
    session_id = int(ev[0][1]["session_id"])
    assert [(r.conversation_id, r.raw_question, r.source, r.reason) for r in await pool_rows(db)] == [
        (session_id, "X3 Pro 防水吗？着急", "self_check", "证据没写防水"),
    ]
    assert [m.content for m in await rows(db) if m.role == "tool"] == [grounding.REFUSED_CONTENT]


async def test_empty_evidence_skips_checker_and_pools(client, db, use_script, kb):
    kb["evidence"] = []
    kb["check"] = AssertionError("不应调用自评")
    use_script(tools(("c1", "query_faq", {"question": "X9 能无线充电吗"})), text(REFUSAL_PREFIX))
    _, ev = await chat(client, "X9 能无线充电吗")
    assert kb["check_calls"] == []
    assert event(ev, "citations") == {"items": [], "refused": True}
    assert [r.reason for r in await pool_rows(db)] == [grounding.EMPTY_EVIDENCE_REASON]


async def test_self_check_failure_fails_open(client, db, use_script, kb):
    kb["evidence"] = [ev_item(11)]
    kb["check"] = RuntimeError("upstream")
    use_script(tools(("c1", "query_faq", {"question": "X3 Pro 能用多久"})), text("约 8 小时[1]"))
    _, ev = await chat(client, "X3 Pro 能用多久")
    assert event(ev, "citations")["refused"] is False
    assert await pool_rows(db) == []
    assert ev[-1][0] == "done"


async def test_two_faq_calls_number_continuously(client, db, use_script, kb):
    a, b, c = ev_item(1, q="A"), ev_item(2, q="B"), ev_item(3, q="C")
    kb["by_question"] = {"运费": [a, b], "发票": [b, c]}
    rec = use_script(
        tools(("c1", "query_faq", {"question": "运费"}), ("c2", "query_faq", {"question": "发票"})),
        text("答[1][3]"),
    )
    _, ev = await chat(client, "运费和发票")
    assert [(i["n"], i["chunk_id"]) for i in event(ev, "citations")["items"]] == [(1, 1), (2, 2), (3, 3)]
    contents = tool_contents(rec[1])
    assert [e["n"] for e in json.loads(contents["c1"])["data"]["evidence"]] == [1, 2]
    assert [e["n"] for e in json.loads(contents["c2"])["data"]["evidence"]] == [3]
    assert kb["check_calls"][0]["question"] == "运费\n发票"


async def test_mixed_order_and_faq_refused_still_answers_order(client, db, use_script, kb):
    kb["evidence"] = []
    rec = use_script(
        tools(("c1", "query_order", {"order_id": "1001"}), ("c2", "query_faq", {"question": "X9 能无线充电吗"})),
        text("订单已发货。" + REFUSAL_PREFIX),
    )
    _, ev = await chat(client, "订单 1001 到哪了，另外 X9 能无线充电吗")
    contents = tool_contents(rec[1])
    assert json.loads(contents["c1"])["ok"] is True and "1001" in contents["c1"]
    assert contents["c2"] == grounding.REFUSED_CONTENT
    assert ev[-1][0] == "done"


async def test_rerank_failure_gives_tool_error_without_pool(client, db, use_script, kb):
    kb["retrieve_error"] = RerankError("x")
    rec = use_script(tools(("c1", "query_faq", {"question": "运费"})), text("暂时查不到"))
    _, ev = await chat(client, "运费多少")
    assert "citations" not in [n for n, _ in ev]
    assert json.loads(tool_contents(rec[1])["c1"])["ok"] is False
    assert await pool_rows(db) == []
    assert ev[-1][0] == "done"


async def test_pool_write_failure_does_not_break_turn(client, db, use_script, kb, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(low_confidence, "add", boom)
    kb["evidence"] = []
    use_script(tools(("c1", "query_faq", {"question": "X9"})), text(REFUSAL_PREFIX))
    _, ev = await chat(client, "X9 怎么样")
    assert ev[-1][0] == "done"
    assert [m.role for m in await rows(db)] == ["user", "assistant", "tool", "assistant"]


async def test_out_of_range_citation_is_logged_not_blocked(client, db, use_script, kb, caplog):
    kb["evidence"] = [ev_item(11)]
    use_script(tools(("c1", "query_faq", {"question": "X3 Pro 能用多久"})), text("约 8 小时[7]"))
    _, ev = await chat(client, "X3 Pro 能用多久")
    assert ev[-1][0] == "done"
    assert "越界引用编号" in caplog.text
    assert (await rows(db))[-1].content == "约 8 小时[7]"


async def test_turn_without_faq_has_no_citations_event(client, db, use_script, kb):
    use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    assert "citations" not in [n for n, _ in ev]
