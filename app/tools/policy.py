import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from app.config import TOOL_POLICY_PATH
from app.tools.registry import ToolEntry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerPolicy:
    url: str
    tools: dict[str, str]


@dataclass(frozen=True)
class ToolOverride:
    timeout_seconds: float | None = None
    max_retries: int | None = None


@dataclass(frozen=True)
class ToolPolicy:
    servers: dict[str, ServerPolicy]
    overrides: dict[str, ToolOverride]

    def mcp_permission(self, server: str, tool: str) -> Literal["read", "write", "deny", "unlisted"]:
        server_policy = self.servers.get(server)
        if server_policy is None:
            return "unlisted"
        return server_policy.tools.get(tool, "unlisted")


EMPTY_POLICY = ToolPolicy({}, {})
_cache: dict[Path, tuple[int, ToolPolicy]] = {}


def _mapping(value) -> dict:
    if not isinstance(value, dict):
        raise TypeError("策略字段必须是对象")
    return value


def _parse_policy(data) -> ToolPolicy:
    data = _mapping(data)
    servers = {}
    for name, raw_server in _mapping(data["servers"]).items():
        raw_server = _mapping(raw_server)
        url = raw_server["url"]
        if not isinstance(url, str):
            raise TypeError("Server URL 必须是字符串")
        tools = {
            tool: permission if permission in ("read", "write", "deny") else "deny"
            for tool, permission in _mapping(raw_server["tools"]).items()
        }
        servers[name] = ServerPolicy(url=url, tools=tools)

    overrides = {}
    for name, raw_override in _mapping(data["overrides"]).items():
        raw_override = _mapping(raw_override)
        timeout = raw_override.get("timeout_seconds")
        retries = raw_override.get("max_retries")
        if timeout is not None and type(timeout) not in (int, float):
            raise TypeError("timeout_seconds 必须是数字")
        if retries is not None and type(retries) is not int:
            raise TypeError("max_retries 必须是整数")
        overrides[name] = ToolOverride(timeout_seconds=timeout, max_retries=retries)
    return ToolPolicy(servers=servers, overrides=overrides)


def load_policy(path: Path | None = None) -> ToolPolicy:
    path = path or TOOL_POLICY_PATH
    cached = _cache.get(path)
    try:
        mtime = path.stat().st_mtime_ns
        if cached is not None and cached[0] == mtime:
            return cached[1]
        policy = _parse_policy(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("tool_policy_invalid path=%s error=%s", path, exc)
        return cached[1] if cached is not None else EMPTY_POLICY
    _cache[path] = (mtime, policy)
    return policy


def apply_override(entry: ToolEntry, policy: ToolPolicy) -> ToolEntry:
    override = policy.overrides.get(entry.name)
    if override is None:
        return entry
    return replace(
        entry,
        timeout=entry.timeout if override.timeout_seconds is None else override.timeout_seconds,
        max_retries=entry.max_retries if override.max_retries is None else override.max_retries,
    )
