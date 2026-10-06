from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.prompts import chat_prompt, extract_prompt, render_chat_system


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
