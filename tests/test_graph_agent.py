import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from app.graph.nodes import agent as agent_mod
from app.graph.nodes.agent import AGENT_TOOLS, AgentOutputError, agent_model, agent_tools
from app.tools.executor import ToolOutcome
from tests.fakes import Recorder, ScriptedChatModel, rt, text, tools

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("emitted")]


def model(*scripts):
    rec = Recorder()
    return ScriptedChatModel(scripts=list(scripts), recorder=rec), rec


def state(**kw):
    base = {"messages": [], "resolved_input": "订单 1001 到哪了", "user_input": "订单 1001 到哪了",
            "evidence": [], "agent_messages": [], "steps": 0, "tokens_used": 0, "force_final": False,
            "trace": []}
    return {**base, **kw}


async def test_binds_agent_tools_and_streams_answer(emitted):
    m, rec = model(text("您好"))
    out = await agent_model(state(), rt(model=m))
    assert rec[0]["tools"] == list(AGENT_TOOLS) and rec[0]["tool_choice"] == "auto"
    assert AGENT_TOOLS == ("query_order", "query_logistics", "query_product", "offer_human_options")
    assert out["reply"] == "您好" and out["agent_messages"][-1].content == "您好"
    assert emitted == [("token", {"text": "您"}), ("token", {"text": "好"})]
    sent = rec[0]["messages"]
    assert isinstance(sent[0], SystemMessage) and isinstance(sent[-1], HumanMessage)
    assert out["tokens_used"] > 0 and out["trace"] == ["agent_model"]


async def test_tool_call_has_no_reply():
    m, _ = model(tools(("c1", "query_logistics", {"order_id": "1001"})))
    out = await agent_model(state(), rt(model=m))
    assert "reply" not in out
    assert out["agent_messages"][-1].tool_calls[0]["name"] == "query_logistics"


async def test_history_and_turn_messages_are_sent_in_order():
    history = [HumanMessage("上一句"), AIMessage("上一答")]
    prior = [AIMessage(content="", tool_calls=[{"id": "c1", "name": "query_order", "args": {"order_id": "1"}}]),
             ToolMessage(content="{}", tool_call_id="c1")]
    m, rec = model(text("好"))
    await agent_model(state(messages=history, agent_messages=prior), rt(model=m))
    sent = rec[0]["messages"]
    assert [type(x).__name__ for x in sent] == [
        "SystemMessage", "HumanMessage", "AIMessage", "HumanMessage", "AIMessage", "ToolMessage"]


async def test_evidence_goes_into_system_prompt():
    evidence = [{"n": 1, "chunk_id": 9, "section_path": "退换货 > 运费", "question": "运费谁出", "answer": "商家"}]
    m, rec = model(text("商家承担[1]"))
    await agent_model(state(evidence=evidence), rt(model=m))
    system = rec[0]["messages"][0].content
    assert "## 知识库证据\n[1] 退换货 > 运费\n问：运费谁出\n答：商家" in system


@pytest.mark.parametrize("reply, warning", [
    ("答[1][3]", "回复含越界引用编号：[3]"),
    ("答[1]", None),
])
async def test_final_reply_logs_out_of_range_citations(reply, warning, caplog, emitted):
    evidence = [{"n": 1, "chunk_id": 9, "section_path": "退换货 > 运费", "question": "运费谁出", "answer": "商家"}]
    m, _ = model(text(reply))
    out = await agent_model(state(evidence=evidence), rt(model=m))
    assert out["reply"] == reply and out["agent_messages"][-1].content == reply
    assert "".join(data["text"] for name, data in emitted if name == "token") == reply
    warnings = [r.getMessage() for r in caplog.records
                if r.levelname == "WARNING" and r.getMessage().startswith("回复含越界引用编号：")]
    assert warnings == ([warning] if warning else [])


async def test_force_final_unbinds_tools_and_appends_closing():
    m, rec = model(text("只能查到这些"))
    out = await agent_model(state(force_final=True), rt(model=m))
    assert rec[0]["tools"] == []
    assert isinstance(rec[0]["messages"][-1], SystemMessage) and "本轮不能再调用任何工具" in rec[0]["messages"][-1].content
    assert out["reply"] == "只能查到这些"


async def test_usage_metadata_is_preferred_for_tokens():
    chunk = AIMessageChunk(content="", usage_metadata={"input_tokens": 1000, "output_tokens": 234,
                                                       "total_tokens": 1234})
    m, _ = model([*text("好"), chunk])
    out = await agent_model(state(tokens_used=100), rt(model=m))
    assert out["tokens_used"] == 1334


@pytest.mark.parametrize("script", [
    [],
    text("<｜DSML｜invoke"),
    text("好的 invoke name=query_order"),
])
async def test_bad_output_raises(script, emitted):
    m, _ = model(script)
    with pytest.raises(AgentOutputError):
        await agent_model(state(), rt(model=m))
    assert not any(e[1]["text"].startswith("<") for e in emitted if e[0] == "token")


@pytest.mark.parametrize("error", [RuntimeError("upstream"), ValueError("upstream")])
async def test_upstream_exception_propagates(error):
    m, _ = model([error])
    with pytest.raises(type(error), match="upstream"):
        await agent_model(state(), rt(model=m))


def call_state(*calls, **kw):
    msg = AIMessage(content="", tool_calls=[{"id": c, "name": n, "args": a} for c, n, a in calls])
    return state(agent_messages=[msg], **kw)


async def test_agent_tools_executes_and_counts_step(emitted):
    seen = []

    async def execute(calls, *, conversation_id):
        seen.append((calls, conversation_id))
        return [ToolOutcome("c1", "query_logistics", True,
                            ToolMessage(content='{"ok": true}', tool_call_id="c1", name="query_logistics"),
                            data={})]

    out = await agent_tools(call_state(("c1", "query_logistics", {"order_id": "1001"})),
                            rt(conversation_id=5, execute=execute))
    assert seen[0][1] == 5 and out["steps"] == 1 and "force_final" not in out
    assert isinstance(out["agent_messages"][-1], ToolMessage)
    assert emitted == [
        ("tool_start", {"tools": [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]}),
        ("tool_end", {"tools": [{"id": "c1", "name": "query_logistics", "ok": True}]}),
    ]


async def test_agent_tools_rejects_unbound_tool(emitted, caplog):
    seen = []

    async def execute(calls, *, conversation_id):
        seen.append((list(calls), conversation_id))
        return [ToolOutcome(c["id"], c["name"], True,
                            ToolMessage(content='{"ok": true}', tool_call_id=c["id"], name=c["name"]))
                for c in calls]

    out = await agent_tools(
        call_state(("c1", "query_order", {"order_id": "1001"}),
                   ("c2", "create_ticket", {"description": "x", "ticket_type": "投诉"})),
        rt(conversation_id=5, execute=execute),
    )
    assert len(seen) == 1 and seen[0][1] == 5
    assert seen[0][0] == [{"id": "c1", "name": "query_order", "args": {"order_id": "1001"},
                          "type": "tool_call"}]
    messages = out["agent_messages"][1:]
    assert [m.tool_call_id for m in messages] == ["c1", "c2"]
    assert json.loads(messages[1].content) == {
        "ok": False, "error": "unknown_tool", "message": "工具不存在"}
    assert emitted[-1] == ("tool_end", {"tools": [
        {"id": "c1", "name": "query_order", "ok": True},
        {"id": "c2", "name": "create_ticket", "ok": False},
    ]})
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "create_ticket" in warnings[0].getMessage() and "5" in warnings[0].getMessage()


async def test_offer_human_options_emits_actions(emitted):
    args = {"options": ["handoff", "ticket"], "ticket_description": "耳机坏了", "ticket_type": "售后"}
    out = await agent_tools(call_state(("c1", "offer_human_options", args)), rt())
    assert out["actions"] == [{"type": "handoff"},
                              {"type": "ticket", "description": "耳机坏了", "ticket_type": "售后"}]
    assert emitted[-1] == ("actions", {"options": out["actions"]})
    assert json.loads(out["agent_messages"][-1].content) == {"ok": True, "data": {"shown": ["handoff", "ticket"]}}


async def test_invalid_offer_args_emit_no_actions(emitted):
    out = await agent_tools(call_state(("c1", "offer_human_options", {"options": ["ticket"]})), rt())
    assert "actions" not in out
    assert [e[0] for e in emitted] == ["tool_start", "tool_end"]
    assert json.loads(out["agent_messages"][-1].content)["error"] == "invalid_arguments"


async def test_step_limit_sets_force_final(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(agent_mod, "AGENT_MAX_STEPS", 2)
    out = await agent_tools(call_state(("c1", "query_order", {"order_id": "1"}), steps=1), rt())
    assert out["steps"] == 2 and out["force_final"] is True
    assert "agent_limit reason=steps" in caplog.text


async def test_token_limit_sets_force_final(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(agent_mod, "AGENT_TOKEN_BUDGET", 500)
    out = await agent_tools(call_state(("c1", "query_order", {"order_id": "1"}), tokens_used=600), rt())
    assert out["force_final"] is True and "agent_limit reason=tokens" in caplog.text


def aftersales_state(**kw):
    state = {"user_input": "这个能退吗", "resolved_input": "订单 1001 的蓝牙耳机能退吗", "route": "aftersales",
             "order_scoped": True, "order_id": "1001",
             "order": {"order_id": "1001", "status": "已签收", "created_at": "2026-10-01 10:00", "total": 299,
                       "items": [{"product_id": "P001", "name": "蓝牙耳机", "price": 299, "quantity": 1}]},
             "evidence": [], "messages": [], "agent_messages": [], "trace": []}
    state.update(kw)
    return state


def test_agent_tool_names():
    from app.graph.nodes.agent import agent_tool_names

    assert agent_tool_names(aftersales_state()) == (*AGENT_TOOLS, "offer_refund_form")
    assert agent_tool_names(aftersales_state(order_id=None)) == AGENT_TOOLS
    assert agent_tool_names({"route": "business", "order_id": "1001"}) == AGENT_TOOLS


async def test_aftersales_prompt_has_order_and_refund_tool(emitted):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[text("可以退[1]")], recorder=rec)
    await agent_mod.agent_model(aftersales_state(), rt(model=model))
    system = rec[0]["messages"][0].content
    assert "## 订单数据" in system and "订单号：1001" in system and "## 本轮任务" in system
    assert rec[0]["tools"][-1] == "offer_refund_form"


async def test_policy_only_aftersales_prompt(emitted):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[text("一般 7 天")], recorder=rec)
    await agent_mod.agent_model(aftersales_state(order_scoped=False, order_id=None, order=None), rt(model=model))
    system = rec[0]["messages"][0].content
    assert "## 订单数据" not in system and "## 本轮任务" in system
    assert "offer_refund_form" not in rec[0]["tools"]


async def test_order_unavailable_prompt(emitted):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[text("暂时查不到")], recorder=rec)
    await agent_mod.agent_model(aftersales_state(order=None), rt(model=model))
    assert "订单数据暂不可用。" in rec[0]["messages"][0].content


async def test_refund_form_emits_refund_action(emitted):
    msg = AIMessage(content="", tool_calls=[{"id": "c1", "name": "offer_refund_form", "args": {"order_id": "1001"}}])
    out = await agent_mod.agent_tools(aftersales_state(agent_messages=[msg]), rt())
    assert out["actions"] == [{"type": "refund", "order_id": "1001"}]
    assert ("actions", {"options": [{"type": "refund", "order_id": "1001"}]}) in emitted


async def test_refund_form_with_other_order_fails(emitted):
    msg = AIMessage(content="", tool_calls=[{"id": "c1", "name": "offer_refund_form", "args": {"order_id": "2002"}}])
    out = await agent_mod.agent_tools(aftersales_state(agent_messages=[msg]), rt())
    assert "actions" not in out
    assert not any(name == "actions" for name, _ in emitted)
    assert json.loads(out["agent_messages"][-1].content)["error"] == "invalid_order"


async def test_refund_form_not_allowed_outside_aftersales(emitted):
    msg = AIMessage(content="", tool_calls=[{"id": "c1", "name": "offer_refund_form", "args": {"order_id": "1001"}}])
    out = await agent_mod.agent_tools(aftersales_state(route="business", agent_messages=[msg]), rt())
    assert "actions" not in out
    assert json.loads(out["agent_messages"][-1].content)["error"] == "unknown_tool"


async def test_refund_and_human_actions_merge(emitted):
    msg = AIMessage(content="", tool_calls=[
        {"id": "c1", "name": "offer_refund_form", "args": {"order_id": "1001"}},
        {"id": "c2", "name": "offer_human_options", "args": {"options": ["handoff"]}},
    ])
    out = await agent_mod.agent_tools(aftersales_state(agent_messages=[msg]), rt())
    assert out["actions"] == [{"type": "refund", "order_id": "1001"}, {"type": "handoff"}]
    assert [name for name, _ in emitted].count("actions") == 1
