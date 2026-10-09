from dataclasses import dataclass, field

from app.tools.registry import ToolEntry, builtin_registry


@dataclass(frozen=True)
class Toolset:
    entries: dict[str, ToolEntry]
    closed: dict[str, str] = field(default_factory=dict)
    unavailable: frozenset[str] = frozenset()

    def agent_tools(self) -> list[dict]:
        return [entry.openai_tool() for entry in self.entries.values() if entry.agent]

    def get(self, name: str) -> ToolEntry | None:
        return self.entries.get(name)


def builtin_toolset() -> Toolset:
    return Toolset(builtin_registry())
