"""分流前的 Query 理解：指代消解与改写、意图识别、扩写。节点和评估脚本共用。"""

import asyncio
import logging
from dataclasses import dataclass

import app.config as config
from app.llm import get_intent_classifier, get_small_intent_classifier
from app.schemas import IntentResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntentDecision:
    intent: str | None
    confidence: float | None
    model: str


async def _classify_once(classifier, text: str) -> IntentResult:
    result = await asyncio.wait_for(classifier.ainvoke({"text": text}), config.INTENT_TIMEOUT_SECONDS)
    if result["parsed"] is None:
        raise ValueError(f"意图结果无效：raw={result.get('raw')!r}")
    return result["parsed"]


async def classify(text: str) -> IntentDecision:
    # 工厂在 try 之外调用：配置错误和测试中未替换时立即暴露。
    large = get_intent_classifier()
    if config.INTENT_SMALL_MODEL:
        small = get_small_intent_classifier()
        try:
            r = await _classify_once(small, text)
            if r.confidence >= config.INTENT_ESCALATE_BELOW:
                return IntentDecision(r.intent, r.confidence, "small")
        except Exception:
            logger.warning("小模型意图识别失败，升级大模型", exc_info=True)
    try:
        r = await _classify_once(large, text)
        return IntentDecision(r.intent, r.confidence, "large")
    except Exception:
        logger.warning("意图识别失败，走兜底出口", exc_info=True)
        return IntentDecision(None, None, "-")
