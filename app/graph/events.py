import logging
import time

from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from app.graph.state import GraphContext

logger = logging.getLogger("app.graph")


def emit(name: str, data: dict) -> None:
    """发出一个 SSE 事件。API 层用 stream_mode="custom" 接收 (name, data)。"""
    get_stream_writer()((name, data))


def trace(kind: str, data: dict, node: str | None = None) -> None:
    """仅在本轮打开 debug 时发出调试事件。图外调用不发事件。"""
    try:
        ctx = get_runtime(GraphContext).context
    except Exception:
        return
    if not getattr(ctx, "debug", False):
        return
    emit("trace", {"kind": kind, "node": node,
                   "t_ms": int((time.monotonic() - ctx.started_at) * 1000), "data": data})


def enter(node: str, state: dict, runtime) -> list[str]:
    logger.info("node=%s conversation=%s", node, runtime.context.conversation_id)
    return [*state.get("trace", []), node]
