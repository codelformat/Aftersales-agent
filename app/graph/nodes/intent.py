import asyncio
import logging

from app.config import INTENT_TIMEOUT_SECONDS
from app.graph import events
from app.graph.routing import route_for
from app.llm import get_intent_classifier

logger = logging.getLogger(__name__)


async def classify_intent(state, runtime):
    trace = events.enter("classify_intent", state, runtime)
    # 工厂在 try 之外调用：配置错误和测试中未替换时立即暴露。
    classifier = get_intent_classifier()
    intent = None
    try:
        result = await asyncio.wait_for(
            classifier.ainvoke({"text": state["resolved_input"]}), INTENT_TIMEOUT_SECONDS)
        if result["parsed"] is None:
            raise ValueError(f"意图结果无效：raw={result.get('raw')!r}")
        intent = result["parsed"].intent
    except Exception:
        logger.warning("意图识别失败，走兜底出口", exc_info=True)
    route = route_for(intent)
    logger.info("intent=%s route=%s conversation=%s", intent, route, runtime.context.conversation_id)
    return {"intent": intent, "route": route, "trace": trace}
