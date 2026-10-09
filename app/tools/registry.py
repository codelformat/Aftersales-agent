from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from langchain_core.tools import BaseTool

from app.config import QUERY_FAQ_TIMEOUT_SECONDS, TOOL_TIMEOUT_SECONDS
from app.graph.control import offer_human_options, offer_refund_form
from app.tools.faq import query_faq
from app.tools.logistics import query_logistics
from app.tools.order import query_order
from app.tools.product import query_product
from app.tools.ticket import create_ticket


# ch04 聊天服务绑定的工具集。ch04 的评估脚本把它当基线。
CH04_CHAT_TOOLS = ("query_order", "query_product", "query_logistics", "query_faq", "create_ticket")


@dataclass(frozen=True)
class ToolSpec:
    tool: BaseTool
    retryable: bool
    timeout: float
    inject_conversation_id: bool = False


class ToolRegistry:
    """按工具名称保存工具及其执行配置。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.tool.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def tools_for_model(self, names: Sequence[str] | None = None) -> list[BaseTool]:
        if names is None:
            return [spec.tool for spec in self._specs.values()]
        return [self._specs[name].tool for name in names]

    def names(self) -> list[str]:
        return list(self._specs)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for registered_tool in (query_order, query_product, query_logistics):
        registry.register(ToolSpec(registered_tool, retryable=True, timeout=TOOL_TIMEOUT_SECONDS))
    # 嵌入、Milvus、重排、改写各自已有指数回退重试。外层不再重试，避免放大等待时间。
    registry.register(ToolSpec(query_faq, retryable=False, timeout=QUERY_FAQ_TIMEOUT_SECONDS))
    registry.register(ToolSpec(
        create_ticket,
        retryable=False,
        timeout=TOOL_TIMEOUT_SECONDS,
        inject_conversation_id=True,
    ))
    registry.register(ToolSpec(offer_human_options, retryable=False, timeout=TOOL_TIMEOUT_SECONDS))
    registry.register(ToolSpec(offer_refund_form, retryable=False, timeout=TOOL_TIMEOUT_SECONDS))
    return registry


@lru_cache
def get_registry() -> ToolRegistry:
    return build_default_registry()
