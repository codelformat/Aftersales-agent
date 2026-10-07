import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.agent.bare_loop import TOOLS, run_bare_agent

pytestmark = pytest.mark.anyio
TODAY = date(2026, 10, 6)


def reply(content=None, calls=()):
    tool_calls = [
        SimpleNamespace(id=cid, type="function",
                        function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for cid, name, args in calls
    ] or None
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """按顺序返回预设的回复，记录每次请求。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.replies.pop(0)


async def test_direct_answer_has_no_steps():
    client = FakeClient([reply("您好")])
    result = await run_bare_agent(client, "m", "你好", today=TODAY)
    assert (result.answer, result.steps, result.calls) == ("您好", 0, [])
    assert client.requests[0]["tools"] == TOOLS


async def test_one_tool_step_feeds_result_back():
    client = FakeClient([reply(calls=[("c1", "query_logistics", {"order_id": "1001"})]), reply("运输中")])
    result = await run_bare_agent(client, "m", "1001 到哪了", today=TODAY)
    assert (result.answer, result.steps, result.calls) == ("运输中", 1, [["query_logistics"]])
    second = client.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
    assert json.loads(second[-1]["content"])["status"] == "运输中"


async def test_two_steps():
    client = FakeClient([
        reply(calls=[("c1", "query_order", {"order_id": "1001"})]),
        reply(calls=[("c2", "query_logistics", {"order_id": "1001"})]),
        reply("好了"),
    ])
    result = await run_bare_agent(client, "m", "q", today=TODAY)
    assert result.steps == 2 and result.calls == [["query_order"], ["query_logistics"]]


async def test_max_steps_forces_final_call_without_tools():
    client = FakeClient([
        reply(calls=[("c1", "query_order", {"order_id": "1001"})]),
        reply(calls=[("c2", "query_order", {"order_id": "1002"})]),
        reply("只能先查到这些"),
    ])
    result = await run_bare_agent(client, "m", "q", today=TODAY, max_steps=2)
    assert result.answer == "只能先查到这些" and result.steps == 2
    assert "tools" not in client.requests[2]


async def test_unknown_tool_returns_error_content():
    client = FakeClient([reply(calls=[("c1", "nope", {})]), reply("抱歉")])
    await run_bare_agent(client, "m", "q", today=TODAY)
    assert json.loads(client.requests[1]["messages"][-1]["content"]) == {"error": "unknown_tool"}


async def test_extra_body_is_forwarded():
    client = FakeClient([reply("好")])
    await run_bare_agent(client, "m", "q", today=TODAY, extra_body={"thinking": {"type": "adaptive"}})
    assert client.requests[0]["extra_body"] == {"thinking": {"type": "adaptive"}}
