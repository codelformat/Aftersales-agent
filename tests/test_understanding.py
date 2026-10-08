import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import config
from app.knowledge.query import Lexicon
from app.services import understanding

pytestmark = pytest.mark.anyio

LEX = Lexicon(model_categories={"X3 Pro": "蓝牙耳机"}, synonyms={})


async def test_classify_uses_large_model_by_default(use_intent):
    calls = use_intent(("退款退货", 0.95))
    d = await understanding.classify("蓝牙耳机能退吗")
    assert (d.intent, d.confidence, d.model) == ("退款退货", 0.95, "large")
    assert calls == [{"text": "蓝牙耳机能退吗"}]


@pytest.mark.parametrize("value", [None, ValueError("boom")])
async def test_classify_failure_returns_none(use_intent, value, caplog):
    use_intent(value)
    d = await understanding.classify("x")
    assert (d.intent, d.confidence, d.model) == (None, None, "-")
    assert "意图识别失败" in caplog.text


async def test_classify_timeout(monkeypatch, use_intent):
    from langchain_core.runnables import RunnableLambda
    monkeypatch.setattr(config, "INTENT_TIMEOUT_SECONDS", 0.01)

    async def slow(_):
        await asyncio.sleep(1)

    monkeypatch.setattr(understanding, "get_intent_classifier", lambda: RunnableLambda(slow))
    assert (await understanding.classify("x")).intent is None


async def test_cascade_keeps_confident_small_result(monkeypatch, use_intent, use_small_intent):
    monkeypatch.setattr(config, "INTENT_SMALL_MODEL", "small-model")
    large = use_intent()
    use_small_intent(("物流", 0.8))
    d = await understanding.classify("到哪了")
    assert (d.intent, d.model) == ("物流", "small") and large == []


async def test_cascade_escalates_low_confidence(monkeypatch, use_intent, use_small_intent):
    monkeypatch.setattr(config, "INTENT_SMALL_MODEL", "small-model")
    use_small_intent(("闲聊", 0.4))
    use_intent(("其他", 0.6))
    d = await understanding.classify("那个东西怎么弄")
    assert (d.intent, d.confidence, d.model) == ("其他", 0.6, "large")


async def test_cascade_small_failure_goes_large(monkeypatch, use_intent, use_small_intent):
    monkeypatch.setattr(config, "INTENT_SMALL_MODEL", "small-model")
    use_small_intent(ValueError("down"))
    use_intent(("订单", 0.9))
    assert (await understanding.classify("订单 1001 付款了吗")).model == "large"


def test_history_text_keeps_dialogue_only():
    msgs = [HumanMessage("订单 1001 到哪了"), AIMessage("", tool_calls=[{"id": "c1", "name": "query_logistics", "args": {}}]),
            ToolMessage("{}", tool_call_id="c1"), AIMessage("运输中" + "很" * 300)]
    text = understanding.history_text(msgs, limit=6, max_chars=10)
    assert text == "用户：订单 1001 到哪\n客服：运输中很很很很很很很"
    assert understanding.history_text([]) == ""
    assert understanding.history_text(msgs, limit=1, max_chars=200).startswith("客服：运输中")


async def test_resolve_fills_reference(use_resolver):
    calls = use_resolver({"resolved_input": "订单 1001 的蓝牙耳机能退吗", "standard_query": "蓝牙耳机退货条件",
                          "order_scoped": True, "order_id": "1001"})
    r = await understanding.resolve("这个能退吗", "用户：订单 1001 到哪了\n客服：运输中", lexicon=LEX)
    assert r == understanding.Resolution("订单 1001 的蓝牙耳机能退吗", "蓝牙耳机退货条件", None, True, "1001")
    assert calls == [{"history": "用户：订单 1001 到哪了\n客服：运输中", "question": "这个能退吗"}]


async def test_resolve_without_history_keeps_input(use_resolver):
    use_resolver({"resolved_input": "被改写的问题", "standard_query": "退货条件"})
    r = await understanding.resolve("拆封了还能退吗", "", lexicon=LEX)
    assert r.resolved_input == "拆封了还能退吗" and r.standard_query == "退货条件"


async def test_resolve_drops_invented_order_id(use_resolver, caplog):
    caplog.set_level("INFO")
    use_resolver({"resolved_input": "订单 2002 能退吗", "standard_query": "退货条件",
                  "order_scoped": True, "order_id": "2002"})
    r = await understanding.resolve("这个能退吗", "用户：蓝牙耳机坏了", lexicon=LEX)
    assert r.order_id is None and r.order_scoped is True
    assert "order_id_dropped" in caplog.text


async def test_resolve_order_id_forces_order_scoped(use_resolver):
    use_resolver({"resolved_input": "订单 1001 退货运费谁出", "standard_query": "退货运费",
                  "order_scoped": False, "order_id": "1001"})
    r = await understanding.resolve("订单 1001 退货运费谁出", "", lexicon=LEX)
    assert r.order_scoped is True and r.order_id == "1001"


async def test_resolve_model_decides_category(use_resolver):
    use_resolver({"resolved_input": "x3pro 能退吗", "standard_query": "x3pro 退货条件", "product_category": "扫地机器人"})
    r = await understanding.resolve("x3pro 能退吗", "", lexicon=LEX)
    assert r.standard_query == "X3 Pro 退货条件" and r.product_category == "蓝牙耳机"


@pytest.mark.parametrize("value", [None, ValueError("boom")])
async def test_resolve_failure_passes_through(use_resolver, value, caplog):
    use_resolver(value)
    r = await understanding.resolve("x3pro 能退吗", "用户：你好", lexicon=LEX)
    assert r == understanding.Resolution("x3pro 能退吗", "X3 Pro 能退吗", "蓝牙耳机", False, None)
    assert "指代消解失败" in caplog.text


async def test_resolve_drops_partial_order_id(use_resolver):
    use_resolver({"resolved_input": "订单 100 能退吗", "standard_query": "退货条件",
                  "order_scoped": True, "order_id": "100"})
    r = await understanding.resolve("这个能退吗", "用户：订单 1001 到哪了", lexicon=LEX)
    assert r.order_id is None

    use_resolver({"resolved_input": "订单 1001 能退吗", "standard_query": "退货条件",
                  "order_scoped": True, "order_id": "1001"})
    r = await understanding.resolve("这个能退吗", "用户：订单1001到哪了", lexicon=LEX)
    assert r.order_id == "1001"
