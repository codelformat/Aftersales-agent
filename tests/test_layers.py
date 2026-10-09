import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.context.layers import effective_ids, history_lines, render_layer2, split_layers


def turn(u, a, user="问", reply="答", tool=None):
    """一轮消息：用户、可选工具调用和结果、最终答复。"""
    msgs = [HumanMessage(user, id=f"msg-{u}")]
    if tool is not None:
        msgs += [
            AIMessage("", tool_calls=[{
                "id": f"c{u}", "name": "query_order", "args": {"order_id": "1001"},
            }]),
            ToolMessage(tool, tool_call_id=f"c{u}", name="query_order"),
        ]
    msgs.append(AIMessage(reply, id=f"msg-{a}"))
    return msgs


def test_effective_ids_inherit_previous():
    msgs = turn(1, 2, tool='{"ok": true}')
    assert effective_ids(msgs) == [1, 1, 1, 2]


def test_messages_without_db_id_count_as_zero():
    msgs = [HumanMessage("旧", id="uuid-1"), AIMessage("旧答"), *turn(5, 6)]
    assert effective_ids(msgs) == [0, 0, 5, 6]
    layers = split_layers(msgs, None, None)
    assert layers.layer1 == msgs and layers.layer2 == []
    assert layers.ids1 == [0, 0, 5, 6]


def test_split_by_anchors():
    msgs = [*turn(1, 2), *turn(3, 4, tool="{}"), *turn(5, 6)]
    layers = split_layers(msgs, summary_upto=2, layer1_from=4)
    assert layers.layer2 == msgs[2:6]
    assert layers.layer1 == msgs[6:]
    assert layers.ids2 == [3, 3, 3, 4]
    assert layers.ids1 == [5, 6]


def test_null_anchors():
    msgs = [*turn(1, 2), *turn(3, 4)]
    assert split_layers(msgs, None, None).layer2 == []
    assert split_layers(msgs, 2, None).layer1 == msgs[2:]
    assert split_layers(msgs, None, 2).layer2 == msgs[:2]


def test_layer_boundary_never_splits_turn():
    msgs = [*turn(1, 2, tool="{}"), *turn(3, 4, tool="{}")]
    layers = split_layers(msgs, None, 2)
    assert layers.layer2 == msgs[:4]
    assert layers.layer1 == msgs[4:]
    assert isinstance(layers.layer1[0], HumanMessage)


def test_degrading_entire_history_keeps_tool_pairs():
    msgs = turn(1, 2, tool="x" * 300)
    layers = split_layers(msgs, None, 2)
    assert layers.layer1 == [] and layers.ids1 == []
    assert layers.layer2 == msgs
    assert layers.ids2 == [1, 1, 1, 2]


def test_note_without_db_id_inherits_final_reply():
    msgs = [*turn(1, 2), AIMessage("工单已创建", id="uuid-note"), *turn(3, 4)]
    layers = split_layers(msgs, None, 2)
    assert layers.layer2 == msgs[:3]
    assert layers.ids2 == [1, 2, 2]
    assert split_layers(msgs, 2, 2).layer1 == msgs[3:]
    assert split_layers(msgs, 2, 2).layer2 == []


def test_render_layer2_truncates_reply_and_long_tool_result():
    long_reply = "好" * 100
    msgs = turn(1, 2, user="订单 1001 到哪了", reply=long_reply, tool="x" * 300)
    out = render_layer2(msgs)
    assert out[0].content == "订单 1001 到哪了"
    assert out[1].tool_calls == msgs[1].tool_calls
    assert out[2].content == "〔query_order 结果已省略，约 300 字〕"
    assert out[2].tool_call_id == out[1].tool_calls[0]["id"] == "c1"
    assert out[3].content == "好" * 60 + "…"
    assert out[3].id == "msg-2"
    assert msgs[2].content == "x" * 300
    assert msgs[3].content == long_reply


def test_render_layer2_keeps_short_tool_result():
    out = render_layer2(turn(1, 2, tool='{"ok": true}'))
    assert out[2].content == '{"ok": true}'


@pytest.mark.parametrize("reply_chars, tool_chars", [(60, 80), (61, 81)])
def test_render_layer2_length_boundaries(reply_chars, tool_chars):
    out = render_layer2(turn(1, 2, reply="好" * reply_chars, tool="x" * tool_chars))
    assert out[2].content == (
        "x" * 80 if tool_chars == 80 else "〔query_order 结果已省略，约 81 字〕"
    )
    assert out[3].content == ("好" * 60 if reply_chars == 60 else "好" * 60 + "…")


def test_render_layer2_returns_independent_messages():
    msgs = turn(1, 2, tool="{}")
    out = render_layer2(msgs)
    assert out == msgs
    assert all(rendered is not original for rendered, original in zip(out, msgs))
    out[0].content = "已修改"
    out[1].tool_calls[0]["args"]["order_id"] = "9999"
    out[2].content = "已修改"
    out[3].content = "已修改"
    assert msgs[0].content == "问"
    assert msgs[1].tool_calls[0]["args"] == {"order_id": "1001"}
    assert msgs[2].content == "{}"
    assert msgs[3].content == "答"


def test_history_lines():
    msgs = [
        *turn(1, 2, user="我要退 1001", reply="好" * 100),
        *turn(3, 4, user="那运费呢", reply="运费由商家承担", tool="{}"),
    ]
    lines = history_lines("第1段：用户报订单 1001", split_layers(msgs, None, 2))
    assert lines == [
        "梗概：第1段：用户报订单 1001", "用户：我要退 1001", "客服：" + "好" * 60 + "…",
        "用户：那运费呢", "客服：运费由商家承担",
    ]


@pytest.mark.parametrize("summary", [None, ""])
def test_history_lines_without_summary(summary):
    assert history_lines(summary, split_layers(turn(1, 2), None, None)) == ["用户：问", "客服：答"]


def test_history_lines_truncates_only_layer1_to_max_chars():
    msgs = [
        *turn(1, 2, user="旧" * 250, reply="好" * 100),
        *turn(3, 4, user="新" * 250, reply="答" * 250),
    ]
    layers = split_layers(msgs, None, 2)
    assert history_lines(None, layers) == [
        "用户：" + "旧" * 250, "客服：" + "好" * 60 + "…",
        "用户：" + "新" * 200, "客服：" + "答" * 200,
    ]
    assert history_lines(None, layers, max_chars=3) == [
        "用户：" + "旧" * 250, "客服：" + "好" * 60 + "…", "用户：新新新", "客服：答答答",
    ]


def test_history_lines_omits_tool_calls_and_empty_reply():
    assert history_lines(None, split_layers(turn(1, 2, reply="", tool="{}"), None, None)) == ["用户：问"]


def test_empty_history():
    layers = split_layers([], None, None)
    assert effective_ids([]) == []
    assert layers.layer1 == layers.layer2 == layers.ids1 == layers.ids2 == []
    assert render_layer2([]) == []
    assert history_lines(None, layers) == []
    assert history_lines("早期梗概", layers) == ["梗概：早期梗概"]
