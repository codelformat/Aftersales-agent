import asyncio
import json
import textwrap
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.tools.executor import execute_tool_calls
from app.tools.policy import ServerPolicy, ToolOverride, ToolPolicy
from app.tools.registry import builtin_registry
from tests.fakes import rt

pytestmark = pytest.mark.anyio
SERVER_TOOLS = Path(__file__).resolve().parents[1] / "mcp_servers/aftersales/tools"


def change_policy(servers, change):
    data = json.loads(servers.policy_path.read_text())
    change(data)
    servers.policy_path.write_text(json.dumps(data))


async def execute(ts, name="query_logistics", args=None):
    [outcome] = await execute_tool_calls(
        [{"id": "c1", "name": name, "args": {"order_id": "1001"} if args is None else args}],
        conversation_id=1, toolset=ts,
    )
    return outcome, json.loads(outcome.message.content)


@pytest.mark.mcp
async def test_adapter_toolcall_contract_and_fresh_sessions(mcp_servers):
    client = MultiServerMCPClient({"logistics": {"transport": "http", "url": "http://127.0.0.1:18101/mcp"}},
                                 handle_tool_errors=False)
    [converted] = await client.get_tools(server_name="logistics")
    assert converted.response_format == "content_and_artifact"
    msg = await converted.ainvoke({"type": "tool_call", "id": "probe", "name": "query_logistics",
                                  "args": {"order_id": "1001"}})
    assert isinstance(msg, ToolMessage)
    assert msg.artifact["structured_content"]["status_code"] == "IN_TRANSIT"
    mcp_servers.restart("logistics")
    msg = await converted.ainvoke({"type": "tool_call", "id": "probe2", "name": "query_logistics",
                                  "args": {"order_id": "1001"}})
    assert msg.artifact["structured_content"]["order_id"] == "1001"


@pytest.mark.mcp
async def test_discovers_mcp_entries_in_binding_order(mcp_servers, caplog):
    from app.tools.mcp import build_base_toolset

    with caplog.at_level("INFO", logger="app.tools.mcp"):
        ts = await build_base_toolset(1)
    assert ts.entries["query_logistics"].source == "mcp"
    assert ts.entries["query_logistics"].server == "logistics"
    assert {"query_warranty", "query_return_progress"} <= ts.entries.keys()
    assert "query_logistics" not in builtin_registry()
    assert [t["function"]["name"] for t in ts.agent_tools()] == [
        "query_order", "query_product", "offer_human_options", "offer_refund_form", "create_ticket",
        "query_logistics", "query_return_progress", "query_warranty",
    ]
    assert "toolset conversation=1 builtin=6 mcp=logistics:1,aftersales:2 denied=-" in caplog.text


@pytest.mark.mcp
async def test_calls_logistics_formats_and_audits(mcp_servers, audit_log):
    from app.tools.mcp import build_base_toolset

    ts = await build_base_toolset(1)
    outcome, payload = await execute(ts)
    assert outcome.status == "成功" and payload["data"]["status"] == "运输中"
    assert (audit_log[0].tool_source, audit_log[0].mcp_server) == ("mcp", "logistics")


@pytest.mark.mcp
async def test_not_found_is_success(mcp_servers, audit_log):
    from app.tools.mcp import build_base_toolset

    outcome, payload = await execute(await build_base_toolset(1), args={"order_id": "9001"})
    assert payload["found"] is False and outcome.retry_count == 0
    assert (audit_log[0].status, audit_log[0].error_message) == ("成功", "查询落空")


@pytest.mark.mcp
async def test_invalid_order_is_blocked_before_network(mcp_servers, audit_log):
    from app.tools.mcp import build_base_toolset

    ts = await build_base_toolset(1)
    mcp_servers.stop("logistics")
    outcome, payload = await execute(ts, args={"order_id": "订单"})
    assert outcome.status == "校验拦下" and payload["error"] == "invalid_arguments"
    assert audit_log[0].retry_count == 0


@pytest.mark.mcp
@pytest.mark.parametrize("permission", [None, "write", "deny"])
async def test_policy_closes_unlisted_write_and_denied_tools(mcp_servers, permission):
    from app.tools.mcp import build_base_toolset

    def change(data):
        tools = data["servers"]["aftersales"]["tools"]
        if permission is None:
            del tools["query_return_progress"]
        else:
            tools["query_return_progress"] = permission

    change_policy(mcp_servers, change)
    ts = await build_base_toolset(1)
    assert ts.closed["query_return_progress"] == "工具未开放"
    outcome, payload = await execute(ts, "query_return_progress")
    assert outcome.status == "权限拒绝" and payload["error"] == "permission_denied"


@pytest.mark.mcp
async def test_failed_server_is_unavailable_other_server_works(mcp_servers, caplog):
    from app.tools.mcp import build_base_toolset

    mcp_servers.stop("aftersales")
    ts = await build_base_toolset(1)
    assert {"query_warranty", "query_return_progress"} <= ts.unavailable
    assert "mcp_discovery_failed server=aftersales" in caplog.text
    outcome, _ = await execute(ts)
    assert outcome.status == "成功"
    mcp_servers.restart("aftersales")
    assert "query_warranty" in (await build_base_toolset(1)).entries


@pytest.mark.mcp
async def test_new_server_tool_is_discovered_without_formatter(mcp_servers):
    from app.tools.mcp import build_base_toolset

    path = SERVER_TOOLS / "task6_repair.py"
    try:
        path.write_text(textwrap.dedent('''
            from mcp_servers.common import OrderId

            def register(mcp):
                @mcp.tool()
                async def query_repair_progress(order_id: OrderId) -> dict:
                    """查维修进度。"""
                    return {"order_id": order_id, "status_code": "REPAIRING", "repair_id": "R1"}
        '''))
        change_policy(mcp_servers, lambda data: data["servers"]["aftersales"]["tools"].update(
            query_repair_progress="read"))
        mcp_servers.restart("aftersales", extra_tool_file=path)
        ts = await build_base_toolset(1)
        assert ts.entries["query_repair_progress"].formatter is None
        outcome, payload = await execute(ts, "query_repair_progress")
        assert outcome.status == "成功"
        assert payload["data"] == {"order_id": "1001", "status_code": "REPAIRING", "repair_id": "R1"}
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.mcp
async def test_server_stopped_after_discovery_retries_twice(mcp_servers, audit_log):
    from app.tools.mcp import build_base_toolset
    from app.tools.executor import is_transient

    ts = await build_base_toolset(1)
    mcp_servers.stop("logistics")
    with pytest.raises(Exception) as raised:
        await ts.entries["query_logistics"].runner({"order_id": "1001"}, "probe")
    print("stopped exception:", repr(raised.value))
    assert is_transient(raised.value)
    outcome, _ = await execute(ts)
    assert outcome.status == "失败" and outcome.retry_count == 2
    assert audit_log[0].retry_count == 2


@pytest.mark.mcp
async def test_plain_text_result_is_preserved(mcp_servers):
    from app.tools.mcp import build_base_toolset

    path = SERVER_TOOLS / "task6_echo.py"
    try:
        path.write_text(textwrap.dedent('''
            def register(mcp):
                @mcp.tool(structured_output=False)
                async def echo_text() -> str:
                    """返回文本。"""
                    return "原样文本"
        '''))
        change_policy(mcp_servers, lambda data: data["servers"]["aftersales"]["tools"].update(echo_text="read"))
        mcp_servers.restart("aftersales", extra_tool_file=path)
        outcome, payload = await execute(await build_base_toolset(1), "echo_text", {})
        assert outcome.status == "成功" and payload["data"] == "原样文本"
    finally:
        path.unlink(missing_ok=True)


async def test_discovery_exception_keeps_builtins(monkeypatch, caplog):
    from app.tools import mcp

    async def broken(policy):
        raise RuntimeError("discovery broken")

    monkeypatch.setattr(mcp, "discover", broken)
    ts = await mcp.build_base_toolset(1)
    assert "create_ticket" in ts.entries
    assert {"query_logistics", "query_warranty", "query_return_progress"} <= ts.unavailable
    assert "mcp_discovery_failed server=logistics" in caplog.text
    assert "mcp_discovery_failed server=aftersales" in caplog.text


async def test_merge_prefers_builtin_then_policy_server_order(monkeypatch, caplog):
    from app.tools import mcp

    @tool(args_schema={"type": "object", "properties": {}})
    async def shared() -> dict:
        """重名工具。"""
        return {}

    builtin = builtin_registry()["query_order"].tool
    policy = ToolPolicy({"second": ServerPolicy("http://second", {"shared": "read", "query_order": "read"}),
                         "first": ServerPolicy("http://first", {"shared": "read"})},
                        {"shared": ToolOverride(timeout_seconds=7, max_retries=1),
                         "query_order": ToolOverride(timeout_seconds=6)})

    async def discovered(policy):
        return {"first": [shared], "second": [builtin, shared]}, {}

    monkeypatch.setattr(mcp, "load_policy", lambda: policy)
    monkeypatch.setattr(mcp, "discover", discovered)
    ts = await mcp.build_base_toolset(1)
    assert ts.entries["query_order"].source == "builtin" and ts.entries["query_order"].timeout == 6
    assert ts.entries["shared"].server == "second"
    assert ts.entries["shared"].timeout == 7 and ts.entries["shared"].max_retries == 1
    assert "mcp_tool_shadowed server=second tool=query_order" in caplog.text
    assert "mcp_tool_shadowed server=first tool=shared" in caplog.text


async def test_discovery_runs_servers_in_parallel_with_individual_timeout(monkeypatch):
    from app.tools import mcp

    both_started = asyncio.Event()
    started = []

    class Client:
        def __init__(self, connections, *, handle_tool_errors):
            assert list(connections) == ["slow", "fast"]
            assert handle_tool_errors is False

        async def get_tools(self, *, server_name):
            started.append(server_name)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            if server_name == "slow":
                await asyncio.Event().wait()
            return [builtin_registry()["query_order"].tool]

    # 这里测试发现器本身，绕过默认的网络隔离。
    monkeypatch.setattr(mcp, "MultiServerMCPClient", Client)
    monkeypatch.setattr(mcp, "MCP_DISCOVERY_TIMEOUT_SECONDS", .05)
    policy = ToolPolicy({name: ServerPolicy(f"http://{name}", {}) for name in ("slow", "fast")}, {})
    from tests.conftest import REAL_DISCOVER
    discovered, failed = await REAL_DISCOVER(policy)
    assert list(discovered) == ["fast"] and failed == {"slow": "TimeoutError"}



async def test_context_caches_toolset_within_turn(monkeypatch):
    from app.tools import mcp

    calls = []

    async def discovered(policy):
        calls.append(policy)
        return {}, {}

    monkeypatch.setattr(mcp, "discover", discovered)
    ctx = rt().context
    first = await mcp.ensure_toolset(ctx)
    assert await mcp.ensure_toolset(ctx) is first
    assert len(calls) == 1
    assert await mcp.ensure_toolset(rt().context) is not first
    assert len(calls) == 2


@pytest.mark.parametrize("permission", ["deny", "write", None])
async def test_first_server_closed_tool_shadows_later_read_tool(monkeypatch, caplog, permission):
    from app.tools import mcp

    @tool(args_schema={"type": "object", "properties": {}})
    async def shared() -> dict:
        """查询共享工具。"""
        return {"server": "later"}

    first_tools = {} if permission is None else {"shared": permission}
    policy = ToolPolicy({"earlier": ServerPolicy("http://earlier", first_tools),
                         "later": ServerPolicy("http://later", {"shared": "read"})}, {})

    async def discovered(policy):
        return {"earlier": [shared], "later": [shared]}, {}

    monkeypatch.setattr(mcp, "load_policy", lambda: policy)
    monkeypatch.setattr(mcp, "discover", discovered)
    ts = await mcp.build_base_toolset(1)
    assert ts.get("shared") is None
    assert "shared" not in [t["function"]["name"] for t in ts.agent_tools()]
    assert ts.closed["shared"] == "工具未开放"
    assert "mcp_tool_shadowed server=later tool=shared" in caplog.text
    outcome, payload = await execute(ts, "shared", {})
    assert outcome.status == "权限拒绝" and payload["error"] == "permission_denied"
