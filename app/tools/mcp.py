import asyncio
import json
import logging
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import MCP_DISCOVERY_TIMEOUT_SECONDS, TOOL_MAX_ATTEMPTS
from app.tools.formatters import formatter_for
from app.tools.policy import ToolPolicy, apply_override, load_policy
from app.tools.registry import ToolEntry, builtin_registry
from app.tools.toolset import Toolset

logger = logging.getLogger(__name__)


def _local_client_factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth,
                             follow_redirects=True, trust_env=False)


async def discover(policy: ToolPolicy) -> tuple[dict[str, list[BaseTool]], dict[str, str]]:
    connections = {}
    for name, server in policy.servers.items():
        connection = {"transport": "http", "url": server.url}
        if urlparse(server.url).hostname in {"127.0.0.1", "localhost", "::1"}:
            # 本机 MCP 直连，避免环境代理拦截请求。
            connection["httpx_client_factory"] = _local_client_factory
        connections[name] = connection
    client = MultiServerMCPClient(
        connections,
        handle_tool_errors=False,
    )

    async def get_tools(name):
        try:
            tools = await asyncio.wait_for(client.get_tools(server_name=name), MCP_DISCOVERY_TIMEOUT_SECONDS)
            return name, tools, None
        except Exception as exc:
            return name, None, type(exc).__name__

    results = await asyncio.gather(*(get_tools(name) for name in policy.servers))
    discovered, failed = {}, {}
    for name, tools, error in results:
        if error is None:
            discovered[name] = tools
        else:
            failed[name] = error
    return discovered, failed


def entry_from_mcp(tool: BaseTool, server: str, policy: ToolPolicy) -> ToolEntry:
    async def runner(args, call_id):
        msg = await tool.ainvoke({"type": "tool_call", "id": call_id or "mcp", "name": tool.name, "args": args})
        artifact = getattr(msg, "artifact", None) or {}
        if isinstance(artifact, dict) and artifact.get("structured_content") is not None:
            return artifact["structured_content"]
        text = msg.content if isinstance(msg.content, str) else "".join(
            block.get("text", "") for block in msg.content if isinstance(block, dict))
        try:
            return json.loads(text)
        except ValueError:
            return text

    return apply_override(ToolEntry(
        name=tool.name, description=tool.description, parameters=tool.args_schema,
        source="mcp", server=server, permission="read", runner=runner,
        max_retries=TOOL_MAX_ATTEMPTS - 1, formatter=formatter_for(server, tool.name),
    ), policy)


async def build_base_toolset(conversation_id: int | None) -> Toolset:
    policy = load_policy()
    entries = {name: apply_override(entry, policy) for name, entry in builtin_registry().items()}
    builtin_count = len(entries)
    seen = set(entries)
    closed, unavailable, mcp_counts = {}, set(), {}
    try:
        discovered, failed = await discover(policy)
    except Exception as exc:
        discovered, failed = {}, {name: type(exc).__name__ for name in policy.servers}
    for name, server in policy.servers.items():
        mcp_counts[name] = 0
        if name in failed or name not in discovered:
            unavailable.update(tool for tool, permission in server.tools.items() if permission == "read")
            logger.warning("mcp_discovery_failed server=%s error=%s", name, failed.get(name, "Unavailable"))
            continue
        for tool in discovered[name]:
            if tool.name in seen:
                logger.warning("mcp_tool_shadowed server=%s tool=%s", name, tool.name)
                continue
            seen.add(tool.name)
            if policy.mcp_permission(name, tool.name) == "read":
                entries[tool.name] = entry_from_mcp(tool, name, policy)
                mcp_counts[name] += 1
            else:
                closed[tool.name] = "工具未开放"
    logger.info("toolset conversation=%s builtin=%s mcp=%s denied=%s", conversation_id, builtin_count,
                ",".join(f"{name}:{count}" for name, count in mcp_counts.items()) or "-",
                ",".join(closed) or "-")
    return Toolset(entries, closed, frozenset(unavailable - entries.keys()))


async def ensure_toolset(ctx) -> Toolset:
    if ctx.base_toolset is None:
        ctx.base_toolset = await build_base_toolset(ctx.conversation_id)
    return ctx.base_toolset
