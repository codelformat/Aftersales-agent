import importlib
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.config import TOOL_MAX_ATTEMPTS, TOOL_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from app.tools.toolset import Toolset

Permission = Literal["read", "write"]
BUILTIN_PACKAGE = "app.tools.builtin"
CH04_CHAT_TOOLS = ("query_order", "query_product", "query_logistics", "query_faq", "create_ticket")


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    parameters: dict
    source: Literal["builtin", "mcp"]
    permission: Permission
    runner: Callable[[dict, str], Awaitable[Any]]
    server: str | None = None
    formatter: Callable[[Any], Any] | None = None
    timeout: float = TOOL_TIMEOUT_SECONDS
    max_retries: int = 0
    inject_conversation_id: bool = False
    agent: bool = True
    tool: BaseTool | None = None

    def openai_tool(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters,
        }}


def entry_from_tool(
    tool: BaseTool, *, permission: Permission = "read", formatter=None,
    timeout: float = TOOL_TIMEOUT_SECONDS, max_retries: int | None = None,
    inject_conversation_id: bool = False, agent: bool = True,
) -> ToolEntry:
    parameters = convert_to_openai_tool(tool)["function"]["parameters"]
    if max_retries is None:
        max_retries = TOOL_MAX_ATTEMPTS - 1 if permission == "read" else 0
    return ToolEntry(
        name=tool.name, description=tool.description, parameters=parameters,
        source="builtin", permission=permission, runner=lambda args, call_id: tool.ainvoke(args),
        formatter=formatter, timeout=timeout, max_retries=max_retries,
        inject_conversation_id=inject_conversation_id, agent=agent, tool=tool,
    )


_ENTRIES: dict[str, ToolEntry] = {}
_LOADED = False
_LOADING = False


def register(tool: BaseTool | None = None, **opts):
    def decorate(registered_tool: BaseTool):
        entry = entry_from_tool(registered_tool, **opts)
        if entry.name in _ENTRIES:
            raise ValueError(f"工具重名：{entry.name}")
        _ENTRIES[entry.name] = entry
        return registered_tool

    return decorate if tool is None else decorate(tool)


def load_package(package: str) -> None:
    pkg = importlib.import_module(package)
    for module in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{package}.{module.name}")


def builtin_registry() -> dict[str, ToolEntry]:
    global _LOADED, _LOADING
    if not _LOADED and not _LOADING:
        _LOADING = True
        try:
            load_package(BUILTIN_PACKAGE)
            _LOADED = True
        finally:
            _LOADING = False
    return dict(_ENTRIES)


def _unregister(name: str) -> None:
    _ENTRIES.pop(name, None)


def ch04_tools() -> list[BaseTool]:
    entries = builtin_registry()
    return [entries[name].tool for name in CH04_CHAT_TOOLS]


def ch04_toolset() -> "Toolset":
    from app.tools.toolset import Toolset

    entries = builtin_registry()
    return Toolset({name: entries[name] for name in CH04_CHAT_TOOLS})
