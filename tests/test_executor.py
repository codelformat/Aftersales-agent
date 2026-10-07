import asyncio
import json

import pytest
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools.executor import execute_tool_calls
from app.tools.registry import ToolRegistry, ToolSpec

pytestmark = pytest.mark.anyio


class EchoArgs(BaseModel):
    order_id: str = Field(pattern=r"^[0-9]{1,8}$")


def make_registry(*, slow_times=0, fail=False, retryable=True, timeout=0.05, big=False, delay=0.0):
    state = {"calls": 0}

    @tool("echo", args_schema=EchoArgs)
    async def echo(order_id: str) -> dict:
        """回显订单号。"""
        state["calls"] += 1
        if state["calls"] <= slow_times:
            await asyncio.sleep(1)
        await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("secret detail")
        return {"order_id": order_id, "pad": "字" * (5000 if big else 0)}

    class InjArgs(BaseModel):
        note: str
        conversation_id: int

    @tool("inject", args_schema=InjArgs)
    async def inject(note: str, conversation_id: int) -> dict:
        """回显会话 ID。"""
        return {"conversation_id": conversation_id}

    reg = ToolRegistry()
    reg.register(ToolSpec(tool=echo, retryable=retryable, timeout=timeout))
    reg.register(ToolSpec(tool=inject, retryable=False, timeout=timeout, inject_conversation_id=True))
    return reg, state


async def no_sleep(d):
    pass


def payload(outcome):
    return json.loads(outcome.message.content)


async def test_success_and_order_preserved():
    reg, _ = make_registry()
    calls = [{"id": "a", "name": "echo", "args": {"order_id": "1"}},
             {"id": "b", "name": "echo", "args": {"order_id": "2"}}]
    out = await execute_tool_calls(calls, conversation_id=7, registry=reg, sleep=no_sleep)
    assert [o.call_id for o in out] == ["a", "b"]
    assert all(o.ok for o in out)
    assert payload(out[1])["data"]["order_id"] == "2"
    assert out[0].message.tool_call_id == "a"


async def test_invalid_arguments_not_executed():
    reg, state = make_registry()
    out = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1 OR 1=1"}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    assert payload(out[0]) == {"ok": False, "error": "invalid_arguments", "message": "参数不合法"}
    assert state["calls"] == 0


async def test_unknown_tool():
    reg, _ = make_registry()
    out = await execute_tool_calls([{"id": "a", "name": "nope", "args": {}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    assert payload(out[0])["error"] == "unknown_tool"
    assert out[0].ok is False


async def test_timeout_retried_with_backoff_then_succeeds():
    reg, state = make_registry(slow_times=2)
    delays = []

    async def rec_sleep(d):
        delays.append(d)

    out = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                   conversation_id=7, registry=reg, sleep=rec_sleep, rand=lambda: 1.0)
    assert out[0].ok and state["calls"] == 3
    assert delays == [0.2, 0.4]


async def test_timeout_exhausted():
    reg, state = make_registry(slow_times=10)
    out = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    assert payload(out[0])["error"] == "timeout"
    assert state["calls"] == 3


async def test_non_retryable_runs_once():
    reg, state = make_registry(slow_times=10, retryable=False)
    out = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    assert payload(out[0])["error"] == "timeout"
    assert state["calls"] == 1


async def test_tool_error_hides_detail():
    reg, _ = make_registry(fail=True)
    out = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    assert payload(out[0]) == {"ok": False, "error": "tool_error", "message": "查询失败"}
    assert "secret" not in out[0].message.content


async def test_partial_failure_keeps_other_results():
    reg, _ = make_registry()
    calls = [{"id": "a", "name": "echo", "args": {"order_id": "1"}},
             {"id": "b", "name": "nope", "args": {}}]
    out = await execute_tool_calls(calls, conversation_id=7, registry=reg, sleep=no_sleep)
    assert [o.ok for o in out] == [True, False]


async def test_injected_conversation_id_overrides_model_value():
    reg, _ = make_registry()
    out = await execute_tool_calls([{"id": "a", "name": "inject", "args": {"note": "x", "conversation_id": 999}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    assert payload(out[0])["data"] == {"conversation_id": 7}


async def test_long_result_truncated():
    reg, _ = make_registry(big=True)
    out = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    content = out[0].message.content
    assert content.endswith("…(结果过长，已截断)")
    assert len(content) == 1500 + len("…(结果过长，已截断)")


async def test_calls_run_in_parallel():
    # 5 个调用各耗时 0.2 秒；串行需要 1 秒，并行应小于 0.5 秒。
    reg, _ = make_registry(timeout=1.0, delay=0.2)
    calls = [{"id": str(i), "name": "echo", "args": {"order_id": str(i)}} for i in range(5)]
    loop = asyncio.get_running_loop()
    start = loop.time()
    out = await execute_tool_calls(calls, conversation_id=7, registry=reg, sleep=no_sleep)
    assert all(o.ok for o in out)
    assert loop.time() - start < 0.5


async def test_malformed_calls_do_not_crash_round():
    reg, _ = make_registry()
    calls = [{"id": "a", "name": "echo"}, {"id": None, "name": "nope", "args": {}},
             {"id": "c", "name": "echo", "args": {"order_id": "1"}}]
    out = await execute_tool_calls(calls, conversation_id=7, registry=reg, sleep=no_sleep)
    assert [o.ok for o in out] == [False, False, True]
    assert payload(out[0])["error"] == "invalid_arguments"
    assert payload(out[1])["error"] == "unknown_tool"
    assert out[1].call_id == ""


async def test_internal_validation_error_is_tool_error():
    class Inner(BaseModel):
        x: int

    @tool("inner", args_schema=EchoArgs)
    async def inner(order_id: str) -> dict:
        """工具内部的数据校验失败。"""
        Inner(x="not-int")
        return {}

    reg = ToolRegistry()
    reg.register(ToolSpec(tool=inner, retryable=False, timeout=1.0))
    out = await execute_tool_calls([{"id": "a", "name": "inner", "args": {"order_id": "1"}}],
                                   conversation_id=7, registry=reg, sleep=no_sleep)
    assert payload(out[0])["error"] == "tool_error"


async def test_success_outcome_keeps_raw_data():
    reg, _ = make_registry()
    [out] = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                     conversation_id=7, registry=reg, sleep=no_sleep)
    assert out.ok and out.data == {"order_id": "1", "pad": ""}


async def test_failure_outcome_has_no_data():
    reg, _ = make_registry(fail=True, retryable=False)
    [out] = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                     conversation_id=7, registry=reg, sleep=no_sleep)
    assert out.ok is False and out.data is None
