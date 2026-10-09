from dataclasses import dataclass, field

from app.tools.registry import BUILTIN_AGENT_ORDER, ToolEntry, builtin_registry


@dataclass(frozen=True)
class Toolset:
    entries: dict[str, ToolEntry]
    closed: dict[str, str] = field(default_factory=dict)
    unavailable: frozenset[str] = frozenset()

    def agent_tools(self) -> list[dict]:
        builtins = [entry for entry in self.entries.values() if entry.agent and entry.source == "builtin"]
        order = {name: index for index, name in enumerate(BUILTIN_AGENT_ORDER)}
        builtins.sort(key=lambda entry: order.get(entry.name, len(order)))
        mcp = [entry for entry in self.entries.values() if entry.agent and entry.source == "mcp"]
        return [entry.openai_tool() for entry in (*builtins, *mcp)]

    def get(self, name: str) -> ToolEntry | None:
        return self.entries.get(name)


def builtin_toolset() -> Toolset:
    return Toolset(builtin_registry())
