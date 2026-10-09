from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.context.layers import render_layer2, split_layers
from app.db.models import Message
from app.prompts import chat_prompt, chat_prompt_vars
from app.services.history import to_langchain, turn_rows

CALLS = [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]


def test_to_langchain_all_roles():
    rows = [
        Message(role="user", content="物流到哪了"),
        Message(role="assistant", content=None, tool_calls=CALLS),
        Message(role="tool", content='{"ok": true}', tool_call_id="c1"),
        Message(role="assistant", content="运输中"),
    ]
    msgs = to_langchain(rows)
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert msgs[1].content == ""
    assert [(c["id"], c["name"], c["args"]) for c in msgs[1].tool_calls] == [("c1", "query_logistics", {"order_id": "1001"})]
    assert msgs[2].tool_call_id == "c1"
    assert msgs[3].tool_calls == []


def test_turn_rows_without_tools():
    rows = turn_rows("你好", "您好")
    assert [(r.role, r.content) for r in rows] == [("user", "你好"), ("assistant", "您好")]


def test_turn_rows_with_tools_roundtrip():
    request = AIMessage(content="", tool_calls=CALLS)
    tool_msgs = [ToolMessage(content='{"ok": true}', tool_call_id="c1")]
    rows = turn_rows("物流到哪了", "运输中", request, tool_msgs)
    assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1].content is None and rows[1].tool_calls == CALLS
    assert rows[2].tool_call_id == "c1"
    back = to_langchain([Message(role=r.role, content=r.content, tool_calls=r.tool_calls,
                                 tool_call_id=r.tool_call_id) for r in rows])
    assert isinstance(back[2], ToolMessage)


def test_layers_keep_tool_pairs_together():
    def turn(i):
        return [HumanMessage(f"问{i}" * 40, id=f"msg-{2 * i - 1}"),
                AIMessage(content="", tool_calls=[{"id": f"c{i}", "name": "query_order", "args": {}}]),
                ToolMessage(content=f"结果{i}" * 40, tool_call_id=f"c{i}"),
                AIMessage(f"答{i}" * 40, id=f"msg-{2 * i}")]
    history = turn(1) + turn(2) + turn(3)
    for summary_upto, layer1_from in ((None, None), (None, 2), (2, 4), (4, 6)):
        layers = split_layers(history, summary_upto, layer1_from)
        for out in (render_layer2(layers.layer2), layers.layer1):
            if out:
                assert isinstance(out[0], HumanMessage)
            ids = {m.tool_call_id for m in out if isinstance(m, ToolMessage)}
            requested = {c["id"] for m in out if isinstance(m, AIMessage) for c in m.tool_calls}
            assert ids == requested


def test_prompt_tool_round_placeholder_and_rules():
    msgs = chat_prompt.invoke({**chat_prompt_vars(date(2026, 10, 6)), "history": [],
                               "input": "q", "tool_round": [AIMessage(content="", tool_calls=CALLS),
                                                            ToolMessage(content="{}", tool_call_id="c1")]}).to_messages()
    assert [type(m) for m in msgs] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    assert "create_ticket" in msgs[0].content
    assert "不要替换为同义词" in msgs[0].content
    without = chat_prompt.invoke({**chat_prompt_vars(date(2026, 10, 6)),
                                  "history": [], "input": "q"}).to_messages()
    assert [type(m) for m in without] == [SystemMessage, HumanMessage]


def test_msg_id_round_trip():
    from app.services.history import db_id, msg_id

    assert msg_id(42) == "msg-42"
    assert db_id(HumanMessage("x", id="msg-42")) == 42
    assert db_id(HumanMessage("x", id="5f1c-uuid")) is None
    assert db_id(HumanMessage("x")) is None
    assert db_id(ToolMessage("{}", tool_call_id="c1", id="msg-x")) is None
    assert db_id(HumanMessage("x", id="msg-")) is None
    assert db_id(HumanMessage("x", id="msg-42-extra")) is None


def test_final_rows_has_user_and_reply_only():
    from app.services.history import final_rows

    rows = final_rows("问", "答")
    assert [(r.role, r.content) for r in rows] == [("user", "问"), ("assistant", "答")]
    assert all(r.tool_calls is None and r.tool_call_id is None for r in rows)


def test_db_id_rejects_non_decimal_digit():
    from app.services.history import db_id

    assert db_id(HumanMessage("x", id="msg-²")) is None
