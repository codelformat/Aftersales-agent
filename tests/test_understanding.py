import asyncio

import pytest

from app import config
from app.services import understanding

pytestmark = pytest.mark.anyio


async def test_classify_uses_large_model_by_default(use_intent):
    calls = use_intent(("退款退货", 0.95))
    d = await understanding.classify("耳机能退吗")
    assert (d.intent, d.confidence, d.model) == ("退款退货", 0.95, "large")
    assert calls == [{"text": "耳机能退吗"}]


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
