import logging
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.prompts import render_agent_system
from tests.test_layers import turn


def prompt(**kw):
    from app.context.assemble import build_agent_prompt
    args = dict(history=[*turn(1, 2, reply="好" * 100), *turn(3, 4)], summary_upto=None, layer1_from=2,
                summary=None, user_input="现在呢", reference="参考", agent_messages=[])
    return build_agent_prompt(**{**args, **kw})


def test_order_system_layer2_layer1_user_reference_agent():
    tail = [AIMessage("", tool_calls=[{"id": "c9", "name": "query_order", "args": {"order_id": "1"}}])]
    p = prompt(agent_messages=tail)
    m = p.messages
    assert isinstance(m[0], SystemMessage) and m[0].content == render_agent_system()
    assert m[2].content == "好" * 60 + "…"          # 层 2 截短
    assert m[3].id == "msg-3"                        # 层 1 原样
    assert isinstance(m[5], HumanMessage) and m[5].content == "现在呢"
    assert m[6].content == "参考"
    assert m[7] is tail[0]
    assert (p.layer2, p.layer1) == (2, 2)


def test_system_is_identical_across_turns():
    from app.prompts import render_reference
    a = render_reference(date(2026, 10, 6), summary="梗概A", order_section="订单A", evidence_text="[1] 证据A")
    b = render_reference(date(2026, 10, 7), evidence_text="[1] 证据B")
    assert prompt(reference=a).messages[0].content == prompt(reference=b).messages[0].content


def test_summary_never_in_system_message():
    from app.prompts import render_reference
    ref = render_reference(date(2026, 10, 6), summary="第1段：订单 1001 要换货")
    p = prompt(reference=ref, summary="第1段：订单 1001 要换货")
    assert all("订单 1001 要换货" not in m.content for m in p.messages if isinstance(m, SystemMessage))
    assert "订单 1001 要换货" in p.messages[-1].content


def test_reference_has_only_non_empty_sections():
    from app.prompts import REFERENCE_HEADER, render_reference
    ref = render_reference(date(2026, 10, 6), evidence_text="[1] 证据")
    assert ref.startswith(REFERENCE_HEADER)
    assert "## 今天\n2026-10-06" in ref and "## 知识库证据\n[1] 证据" in ref
    assert "## 早期对话梗概" not in ref and "## 订单数据" not in ref and "本轮任务" not in ref


def test_system_has_no_variable_sections():
    s = render_agent_system()
    assert "今天是" not in s and "## 知识库证据\n" not in s and "## 订单数据\n" not in s


def test_log_model_ctx(caplog):
    from app.context.assemble import log_model_ctx
    caplog.set_level(logging.INFO)
    log_model_ctx(12, 0, prompt(summary="第1段：订单 1001"))
    assert "model_ctx conversation=12 step=0 window=4 tokens≈" in caplog.text
    assert "summary=第1段：订单 1001" in caplog.text
    assert "  [L2] user: 问" in caplog.text and "  [L1] assistant: 答" in caplog.text


def test_summary_anchor_excludes_covered_history_without_mutation():
    history = [*turn(1, 2), *turn(3, 4, reply="好" * 100), *turn(5, 6)]
    p = prompt(history=history, summary_upto=2, layer1_from=4)
    assert [m.id for m in p.messages[1:5]] == ["msg-3", "msg-4", "msg-5", "msg-6"]
    assert p.messages[2].content == "好" * 60 + "…"
    assert history[3].content == "好" * 100
    assert p.messages[3] is history[4]


def test_log_model_ctx_labels_tools_and_empty_window(caplog):
    from app.context.assemble import log_model_ctx

    caplog.set_level(logging.INFO)
    log_model_ctx(12, 1, prompt(history=turn(1, 2, tool="长" * 100)))
    assert "[L2] assistant(tool_calls=query_order):" in caplog.text
    assert "[L2] tool: 〔query_order 结果已省略，约 100 字〕" in caplog.text
    log_model_ctx(12, 2, prompt(history=[]))
    assert "step=2 window=0 tokens≈" in caplog.text
    assert "summary=-\n  （无）" in caplog.text
