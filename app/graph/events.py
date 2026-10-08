import logging

from langgraph.config import get_stream_writer

logger = logging.getLogger("app.graph")


def emit(name: str, data: dict) -> None:
    """发出一个 SSE 事件。API 层用 stream_mode="custom" 接收 (name, data)。"""
    get_stream_writer()((name, data))


def enter(node: str, state: dict, runtime) -> list[str]:
    logger.info("node=%s conversation=%s", node, runtime.context.conversation_id)
    return [*state.get("trace", []), node]
