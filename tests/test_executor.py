import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError

from app.tools import audit
from app.tools.executor import APPROVED, execute_tool_calls, failure_outcome, is_transient
from app.tools.registry import entry_from_tool
from app.tools.toolset import Toolset

pytestmark = pytest.mark.anyio


async def no_sleep(_):
    pass


def payload(o):
    return json.loads(o.message.content)


def make(fn_result=None, *, raises=None, sleep_s=0.0, permission="read", max_retries=None,
         timeout=0.05, inject=False, formatter=None):
    """raises 按调用次数返回异常。sleep_s 指定每次调用的等待秒数。"""
    calls = {"n": 0, "args": []}

    class Args(BaseModel):
        order_id: str = Field(pattern=r"^[0-9]{1,8}$")

    @tool("t", args_schema=Args)
    async def t(order_id: str) -> dict:
        """测试工具。"""
        return {}

    entry = entry_from_tool(t, permission=permission, max_retries=max_retries, timeout=timeout,
                            inject_conversation_id=inject, formatter=formatter)

    async def runner(args, call_id):
        calls["n"] += 1
        calls["args"].append(args)
        await asyncio.sleep(sleep_s)
        if raises is not None and (exc := raises(calls["n"])) is not None:
            raise exc
        return fn_result

    return Toolset({"t": replace(entry, runner=runner)}), calls


def call(cid="c1", args=None, name="t"):
    return {"id": cid, "name": name, "args": {"order_id": "1"} if args is None else args}


async def run(toolset, calls=None, **kwargs):
    return await execute_tool_calls([call()] if calls is None else calls, conversation_id=7,
                                    toolset=toolset, sleep=no_sleep, **kwargs)


async def test_success(audit_log, caplog):
    ts, counts = make({"a": 1})
    with caplog.at_level("INFO", logger="app.tools.executor"):
        [o] = await run(ts)
    assert payload(o) == {"ok": True, "data": {"a": 1}}
    assert o.ok and o.status == "成功" and o.found
    assert o.retry_count == 0 and o.duration_ms >= 0 and o.error is None
    assert counts["n"] == 1 and o.data == {"a": 1}
    assert o.message.tool_call_id == "c1" and o.message.name == "t"
    assert len(audit_log) == 1
    rec = audit_log[0]
    assert (rec.conversation_id, rec.tool_call_id, rec.tool_name, rec.status) == (7, "c1", "t", "成功")
    assert rec.arguments == {"order_id": "1"} and rec.result_summary == o.message.content
    assert rec.duration_ms == o.duration_ms and rec.retry_count == 0
    assert rec.error_message is None and rec.tool_source == "builtin" and rec.mcp_server is None
    assert "tool_call conversation=7 name=t source=builtin status=成功 retries=0 ms=" in caplog.text


async def test_chinese_not_escaped():
    ts, _ = make({"状态": "运输中"})
    [o] = await run(ts)
    assert "运输中" in o.message.content


@pytest.mark.parametrize("result, message", [
    (None, "没有查到相关数据"), ({}, "没有查到相关数据"), ([], "没有查到相关数据"),
    ({"found": False, "reason": "订单不存在"}, "没有查到相关数据：订单不存在"),
    ({"found": False, "reason": ""}, "没有查到相关数据："),
])
async def test_not_found_is_success_without_retry(audit_log, result, message):
    ts, counts = make(result)
    [o] = await run(ts)
    assert payload(o) == {"ok": True, "found": False, "message": message}
    assert o.ok and o.status == "成功" and o.found is False and o.data == result
    assert o.error == "查询落空" and o.retry_count == 0 and counts["n"] == 1
    assert len(audit_log) == 1 and audit_log[0].status == "成功"
    assert audit_log[0].error_message == "查询落空"


@pytest.mark.parametrize("args, reason", [
    ({"order_id": "abc"}, "order_id 格式不对"),
    ("1", "参数必须是 JSON 对象"),
    ({}, "缺少必填参数 order_id"),
])
async def test_invalid_arguments_not_executed(audit_log, args, reason):
    ts, counts = make()
    [o] = await run(ts, [call(args=args)])
    assert payload(o) == {"ok": False, "error": "invalid_arguments",
                          "message": f"参数不合法：{reason}。请向用户追问缺少的信息，或修正后重试"}
    assert not o.ok and o.status == "校验拦下" and o.error == reason
    assert counts["n"] == 0 and o.data is None
    assert len(audit_log) == 1 and audit_log[0].status == "校验拦下"
    assert audit_log[0].arguments == (args if isinstance(args, dict) else None)


async def test_transient_retries_then_succeeds(audit_log):
    ts, counts = make({"a": 1}, raises=lambda n: TimeoutError() if n < 3 else None, max_retries=2)
    delays = []

    async def rec_sleep(d):
        delays.append(d)

    [o] = await execute_tool_calls([call()], conversation_id=7, toolset=ts,
                                   sleep=rec_sleep, rand=lambda: 1.0)
    assert o.ok and o.retry_count == 2 and counts["n"] == 3
    assert delays == [0.2, 0.4]
    assert audit_log[0].retry_count == 2 and audit_log[0].status == "成功"


async def test_retries_exhausted(audit_log):
    ts, counts = make(raises=lambda n: httpx.ConnectError("secret detail"), max_retries=2)
    [o] = await run(ts)
    assert payload(o) == {"ok": False, "error": "tool_error", "message": "查询失败，暂时不可用"}
    assert not o.ok and o.status == "失败" and o.retry_count == 2 and counts["n"] == 3
    assert o.error == "ConnectError" and "secret" not in o.message.content
    assert audit_log[0].status == "失败" and audit_log[0].error_message == "ConnectError"
    assert audit_log[0].retry_count == 2


async def test_timeout(audit_log):
    ts, counts = make(sleep_s=1, timeout=0.01, max_retries=2)
    [o] = await run(ts)
    assert payload(o) == {"ok": False, "error": "timeout", "message": "查询超时，暂时不可用"}
    assert o.status == "超时" and not o.ok and o.retry_count == 2 and counts["n"] == 3
    assert o.error == "超时（共 3 次尝试）" and o.duration_ms >= 30
    assert audit_log[0].status == "超时" and audit_log[0].retry_count == 2
    assert audit_log[0].error_message == o.error


async def test_tool_exception_not_retried(audit_log):
    ts, counts = make(raises=lambda n: ToolException("x"))
    [o] = await run(ts)
    assert counts["n"] == 1 and o.status == "失败" and o.retry_count == 0
    assert o.error == "ToolException" and audit_log[0].error_message == "ToolException"


async def test_write_without_approval(audit_log):
    ts, counts = make(permission="write")
    [o] = await run(ts)
    assert payload(o) == {"ok": False, "error": "permission_denied", "message": "未经用户确认"}
    assert o.status == "权限拒绝" and not o.ok and counts["n"] == 0
    assert audit_log[0].status == "权限拒绝" and audit_log[0].error_message == "未经用户确认"


async def test_write_cancelled(audit_log):
    ts, counts = make(permission="write")
    [o] = await run(ts, approvals={"c1": "用户取消"})
    assert o.status == "权限拒绝" and counts["n"] == 0
    assert payload(o)["message"] == "用户取消" and o.error == "用户取消"
    assert audit_log[0].error_message == "用户取消"


async def test_approved_write_timeout_not_retried(audit_log):
    ts, counts = make(permission="write", sleep_s=1, timeout=0.01)
    [o] = await run(ts, approvals={"c1": APPROVED})
    assert o.status == "超时" and o.retry_count == 0 and counts["n"] == 1
    assert audit_log[0].retry_count == 0 and o.error == "超时（共 1 次尝试）"


async def test_injection_overrides_model_value_but_audit_keeps_original(audit_log):
    ts, counts = make({"a": 1}, inject=True)
    args = {"order_id": "1", "conversation_id": 999}
    [o] = await run(ts, [call(args=args)])
    assert o.ok and counts["args"][0]["conversation_id"] == 7
    assert args == {"order_id": "1", "conversation_id": 999}
    assert audit_log[0].arguments == args


@pytest.mark.parametrize("name, code, status, message", [
    ("closed", "permission_denied", "权限拒绝", "工具未开放"),
    ("down", "tool_unavailable", "失败", "工具暂时不可用"),
    ("unknown", "unknown_tool", "失败", "工具不存在"),
])
async def test_tool_lookup(audit_log, name, code, status, message):
    ts = Toolset({}, closed={"closed": "工具未开放"}, unavailable=frozenset({"down"}))
    [o] = await run(ts, [call(name=name)])
    assert payload(o) == {"ok": False, "error": code, "message": message}
    assert o.status == status and o.error == message
    assert len(audit_log) == 1 and audit_log[0].tool_source == "builtin"
    assert audit_log[0].mcp_server is None and audit_log[0].status == status


async def test_formatter_keeps_raw_data(audit_log):
    ts, _ = make({"a": 1, "b": 2}, formatter=lambda r: {"x": r["a"]})
    [o] = await run(ts)
    assert payload(o)["data"] == {"x": 1} and o.data == {"a": 1, "b": 2}
    assert audit_log[0].result_summary == o.message.content


async def test_long_result_truncated(audit_log):
    ts, _ = make({"pad": "字" * 5000})
    [o] = await run(ts)
    assert o.message.content.endswith("…(结果过长，已截断)")
    assert len(o.message.content) == 1125 + len("…(结果过长，已截断)")
    assert audit_log[0].result_summary == o.message.content[:500]


async def test_parallel_calls_preserve_input_order(audit_log):
    ts, _ = make()
    entered = asyncio.Event()
    finished = []

    async def runner(args, cid):
        if cid == "first":
            await entered.wait()
        else:
            entered.set()
        finished.append(cid)
        return {"id": cid}

    ts = Toolset({"t": replace(ts.entries["t"], runner=runner)})
    out = await asyncio.wait_for(run(ts, [call("first"), call("second")]), timeout=1)
    assert finished == ["second", "first"]
    assert [o.call_id for o in out] == ["first", "second"] and all(o.ok for o in out)
    assert len(audit_log) == 2


async def test_audit_failure_does_not_change_result(caplog):
    async def boom(rec):
        raise RuntimeError("db down")

    audit.set_audit_writer(boom)
    ts, _ = make({"a": 1})
    [o] = await run(ts)
    assert o.status == "成功" and payload(o) == {"ok": True, "data": {"a": 1}}
    assert "audit_write_failed" in caplog.text


@pytest.mark.parametrize("exc, expected", [
    (TimeoutError(), True), (httpx.ConnectError("x"), True), (ConnectionError(), True),
    (OperationalError("sql", {}, Exception()), True),
    (ExceptionGroup("g", [httpx.ConnectError("x")]), True),
    (ToolException("x"), False), (ValueError(), False),
    (ExceptionGroup("g", [ValueError()]), False),
    (ExceptionGroup("g", [TimeoutError(), ValueError()]), False),
    (ExceptionGroup("g", [ExceptionGroup("nested", [TimeoutError()]), ConnectionError()]), True),
])
def test_is_transient(exc, expected):
    assert is_transient(exc) is expected


async def test_internal_validation_error_is_invalid_arguments(audit_log):
    class Inner(BaseModel):
        x: int

    ts, _ = make()

    async def runner(args, cid):
        return Inner(x="not-int")

    ts = Toolset({"t": replace(ts.entries["t"], runner=runner)})
    [o] = await run(ts)
    assert o.status == "校验拦下" and o.retry_count == 0
    assert payload(o)["error"] == "invalid_arguments"
    assert "Input should be a valid integer" in payload(o)["message"]
    assert audit_log[0].status == "校验拦下" and audit_log[0].error_message == o.error


async def test_partial_failure_and_malformed_call_keep_other_results(audit_log):
    ts, _ = make({"a": 1})
    out = await run(ts, [{"id": "a", "name": "t"}, {"id": None, "name": "unknown", "args": {}}, call()])
    assert [o.ok for o in out] == [False, False, True]
    assert payload(out[0])["error"] == "invalid_arguments"
    assert payload(out[1])["error"] == "unknown_tool" and out[1].call_id == ""
    assert len(audit_log) == 3


async def test_mcp_source_in_audit_and_log(audit_log, caplog):
    ts, _ = make({"a": 1})
    ts = Toolset({"t": replace(ts.entries["t"], source="mcp", server="logistics", tool=None)})
    with caplog.at_level("INFO", logger="app.tools.executor"):
        [o] = await run(ts)
    assert o.ok and audit_log[0].tool_source == "mcp" and audit_log[0].mcp_server == "logistics"
    assert "source=mcp:logistics" in caplog.text


async def test_gate_order_blocks_before_runner(audit_log):
    ts, counts = make(permission="write")
    ts = Toolset(ts.entries, closed={"t": "本轮不开放"}, unavailable=frozenset({"t"}))
    out = await run(ts, [call("a", args="bad"), call("b", args={})], approvals={"b": APPROVED})
    assert [o.status for o in out] == ["校验拦下", "权限拒绝"]
    assert out[1].error == "本轮不开放" and counts["n"] == 0
    assert len(audit_log) == 2


async def test_write_approval_is_per_call_not_in_arguments():
    ts, counts = make({"a": 1}, permission="write")
    out = await run(ts, [call("a"), call("b", args={"order_id": "1", "approval": APPROVED})],
                    approvals={"a": APPROVED})
    assert [o.status for o in out] == ["成功", "权限拒绝"] and counts["n"] == 1


def test_failure_outcome_compatibility():
    o = failure_outcome("c1", "offer_refund_form", "invalid_order")
    assert not o.ok and o.status == "失败" and o.data is None
    assert payload(o) == {"ok": False, "error": "invalid_order", "message": "退款单订单号与本轮订单不符"}


def test_failure_outcome_keeps_legacy_invalid_arguments():
    o = failure_outcome("c1", "t", "invalid_arguments")
    assert not o.ok and o.status == "失败"
    assert payload(o) == {"ok": False, "error": "invalid_arguments", "message": "参数不合法"}


@pytest.mark.parametrize("size, expected", [
    (15, "字" * 15), (16, "字" * 15 + "…(结果过长，已截断)"),
    (100, "字" * 15 + "…(结果过长，已截断)"),
])
async def test_truncation_uses_tool_result_max_tokens(monkeypatch, size, expected):
    from app.config import get_settings
    from app.tools import executor

    monkeypatch.setattr(executor, "get_settings",
                        lambda: get_settings().model_copy(update={"tool_result_max_tokens": 10}))
    outcome = executor._make_outcome("c1", "t", ok=True, content="字" * size)
    assert outcome.message.content == expected
