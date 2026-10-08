from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.prompts import (
    INTENT_SYSTEM_PROMPT, REFUSAL_PREFIX, chat_prompt, extract_prompt, intent_prompt, render_chat_system,
)
from app.schemas import INTENTS


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
    assert INTENTS == ("物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊", "其他")
    msgs = intent_prompt.invoke({"text": "订单 1001 到哪了"}).to_messages()
    assert msgs[-1].content == "订单 1001 到哪了"


def test_agent_system_prompt():
    from datetime import date
    from app.prompts import render_agent_system
    plain = render_agent_system(date(2026, 10, 6))
    assert "示例商城" in plain and "2026-10-06" in plain
    assert "offer_human_options" in plain and "create_ticket" not in plain and "query_faq" not in plain
    assert "## 知识库证据" not in plain and "{" not in plain
    with_evidence = render_agent_system(date(2026, 10, 6), "[1] 退换货 > 运费\n问：q\n答：a {x}")
    assert "## 知识库证据\n[1] 退换货 > 运费" in with_evidence and "{x}" in with_evidence
    assert "要不要查取决于前一个的结果时" in plain


def test_intent_prompt_four_parts():
    from app.prompts import INTENT_SYSTEM_PROMPT
    from app.schemas import INTENTS
    for label in INTENTS:
        assert label in INTENT_SYSTEM_PROMPT
    for letter in "ABCDEFGH":
        assert f"{letter}. " in INTENT_SYSTEM_PROMPT
    assert '"intent"' in INTENT_SYSTEM_PROMPT and '"confidence"' in INTENT_SYSTEM_PROMPT
    assert "JSON" in INTENT_SYSTEM_PROMPT and "## 示例" in INTENT_SYSTEM_PROMPT


def test_resolve_prompt_rules_and_json():
    from app.prompts import RESOLVE_SYSTEM_PROMPT, resolve_prompt
    for key in ('"resolved_input"', '"standard_query"', '"product_category"', '"order_scoped"', '"order_id"'):
        assert key in RESOLVE_SYSTEM_PROMPT
    assert "一字不差" in RESOLVE_SYSTEM_PROMPT and "JSON" in RESOLVE_SYSTEM_PROMPT
    assert set(resolve_prompt.input_variables) == {"history", "question"}


def test_expand_prompt_json():
    from app.prompts import EXPAND_SYSTEM_PROMPT, expand_prompt
    assert '"queries"' in EXPAND_SYSTEM_PROMPT and "JSON" in EXPAND_SYSTEM_PROMPT
    assert set(expand_prompt.input_variables) == {"question", "products"}
