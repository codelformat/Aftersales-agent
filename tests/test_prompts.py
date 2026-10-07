from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.prompts import REFUSAL_PREFIX, chat_prompt, extract_prompt, render_chat_system


def test_system_prompt_contains_shop_and_date():
    text = render_chat_system(date(2026, 10, 6))
    assert "示例商城" in text
    assert "2026-10-06" in text


def test_chat_prompt_places_history_between_system_and_input():
    msgs = chat_prompt.invoke({
        "shop_name": "示例商城",
        "today": "2026-10-06",
        "history": [HumanMessage("q1"), AIMessage("a1")],
        "input": "q2",
    }).to_messages()
    assert [type(m) for m in msgs] == [SystemMessage, HumanMessage, AIMessage, HumanMessage]
    assert msgs[-1].content == "q2"


def test_input_with_braces_is_not_a_template_variable():
    msgs = chat_prompt.invoke({
        "shop_name": "示例商城", "today": "2026-10-06", "history": [], "input": "订单{A1}",
    }).to_messages()
    assert msgs[-1].content == "订单{A1}"


def test_extract_prompt_puts_text_last():
    msgs = extract_prompt.invoke({"text": "耳机坏了"}).to_messages()
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[-1].content == "耳机坏了"


def test_chat_prompt_vars():
    from app.prompts import chat_prompt_vars

    assert chat_prompt_vars(date(2026, 10, 6)) == {"shop_name": "示例商城", "today": "2026-10-06"}


def test_system_prompt_ch04_rules():
    s = render_chat_system(date(2026, 10, 7))
    assert REFUSAL_PREFIX in s
    assert "[2]" in s and "[1][3]" in s
    assert "question 填用户关于这一点的原话" in s
    for phrase in ("不承诺退款到账的具体日期", "一定审核通过", "不承诺赔偿", "不承诺具体的发货或送达时间",
                   "不承诺保修范围外免费维修"):
        assert phrase in s
    assert "300 字" in s and "200 字" not in s
    assert "keyword" not in s and "时限除外" not in s


def test_system_prompt_evidence_only_rule():
    s = render_chat_system(date(2026, 10, 7))
    assert "只陈述证据中写明的事实" in s and "一字不差" in s


def test_system_prompt_citations_only_use_current_round():
    s = render_chat_system(date(2026, 10, 7))
    assert "只引用本轮知识库结果中的编号" in s
    assert "不引用历史消息中的编号" in s


def test_faith_judge_prompt_conservative_wording():
    from app.prompts import FAITH_JUDGE_SYSTEM_PROMPT

    assert "以审核结果为准" in FAITH_JUDGE_SYSTEM_PROMPT and "本店" in FAITH_JUDGE_SYSTEM_PROMPT


def test_intent_prompt_lists_all_intents():
    from app.prompts import INTENT_SYSTEM_PROMPT, intent_prompt
    from app.schemas import INTENTS
    assert INTENTS == ("物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊")
    for intent in INTENTS:
        assert f"- {intent}：" in INTENT_SYSTEM_PROMPT
    msgs = intent_prompt.invoke({"text": "订单 1001 到哪了"}).to_messages()
    assert msgs[-1].content == "订单 1001 到哪了"
