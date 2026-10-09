"""分流前的 Query 理解：指代消解与改写、意图识别、扩写。节点和评估脚本共用。"""

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import app.config as config
from app.knowledge.query import Lexicon, get_lexicon, model_category, normalize_models
from app.llm import get_intent_classifier, get_query_expander, get_reference_resolver, get_small_intent_classifier
from app.schemas import IntentResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolution:
    resolved_input: str
    standard_query: str
    product_category: str | None
    order_scoped: bool
    order_id: str | None


def _category(lex: Lexicon, *texts: str, fallback: str | None = None) -> str | None:
    # 型号与品类的对应是确定的，覆盖 LLM 的结果。
    for t in texts:
        if category := model_category(t, lex):
            return category
    return fallback


def _mentions(text: str, order_id: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(order_id)}(?![A-Za-z0-9])", text) is not None


async def resolve(user_input: str, history: str, *, resolver=None, lexicon: Lexicon | None = None) -> Resolution:
    lex = lexicon or get_lexicon()
    normalized = normalize_models(user_input, lex)
    # 工厂在 try 之外调用：测试中未替换时立即暴露。
    resolver = resolver or get_reference_resolver()
    try:
        result = await asyncio.wait_for(
            resolver.ainvoke({"history": history or "（无）", "question": user_input}),
            config.RESOLVE_TIMEOUT_SECONDS)
        parsed = result["parsed"]
        if parsed is None:
            raise ValueError(f"指代消解结果无效：raw={result.get('raw')!r}")
    except Exception:
        logger.warning("指代消解失败，原样透传", exc_info=True)
        return Resolution(user_input, normalized, _category(lex, normalized), False, None)
    resolved = parsed.resolved_input if history else user_input
    standard = normalize_models(parsed.standard_query, lex)
    order_id = parsed.order_id
    if order_id and not _mentions(user_input, order_id) and not _mentions(history, order_id):
        logger.info("order_id_dropped order_id=%s", order_id)
        order_id = None
    return Resolution(
        resolved_input=resolved,
        standard_query=standard,
        product_category=_category(lex, standard, normalize_models(resolved, lex), fallback=parsed.product_category),
        order_scoped=parsed.order_scoped or order_id is not None,
        order_id=order_id,
    )


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


async def expand(standard_query: str, product_names: Sequence[str], *, expander=None,
                 lexicon: Lexicon | None = None) -> list[str]:
    lex = lexicon or get_lexicon()
    expander = expander or get_query_expander()
    extra: list[str] = []
    try:
        result = await asyncio.wait_for(
            expander.ainvoke({"question": standard_query, "products": "、".join(product_names) or "（无）"}),
            config.EXPAND_TIMEOUT_SECONDS)
        parsed = result["parsed"]
        if parsed is None:
            raise ValueError(f"扩写结果无效：raw={result.get('raw')!r}")
        extra = [normalize_models(q.strip(), lex) for q in parsed.queries[:config.EXPAND_MAX_QUERIES]]
    except Exception:
        logger.warning("扩写失败，只用标准问法检索", exc_info=True)
    queries: list[str] = []
    for q in (standard_query, *extra):
        if q and q not in queries:
            queries.append(q)
    return queries
