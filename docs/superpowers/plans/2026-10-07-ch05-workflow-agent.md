# ch05 Workflow 骨架与主力 Agent 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目的执行方式（CLAUDE.md 规定，优先于技能默认值）：** Claude 把每个任务整段（含 Global Constraints）交给 Codex 编写代码；Claude 检查 diff、跑测试、核对 spec，有问题把具体问题反馈给 Codex 重做；通过后 Claude 提交、推送，并当场追记 `dev-notes/ch05.md`。Task 10（聊天页）按 CLAUDE.md 的例外条款走 Vibe Coding。

**Goal:** 把 `/chat/stream` 从 ch02 的单轮工具调用换成 LangGraph 图：确定性骨架（指代消解 → 意图识别 → 分流 → 检索 → 置信度闸）+ 主力 ReAct Agent + 日志记录，并加上「转人工」「建工单」两个用户自选按钮。

**Architecture:** `app/graph/` 中每个节点是一个 `async def node(state, runtime)`。节点用 `events.emit()`（封装 `get_stream_writer()`）发 SSE 事件，API 层用 `astream(stream_mode="custom")` 转发。跨轮历史放在 State 的 `messages`（`add_messages` reducer），由 `AsyncSqliteSaver` 持久化；本轮字段由 `start_turn` 重置。每轮的运行时依赖（会话 ID、日期、聊天模型、工具执行函数）通过 `context=GraphContext(...)` 传入。

**Tech Stack:** Python 3.12、FastAPI、LangGraph 1.2.x、langgraph-checkpoint-sqlite 3.1.x、LangChain、SQLAlchemy（异步）、MySQL 8、Milvus、`openai` SDK（仅热身）。

**Spec:** `docs/superpowers/specs/2026-10-07-ch05-workflow-agent-design.md`

## Global Constraints

- 技术选型定死：FastAPI、SQLAlchemy、LangChain、LangGraph、Milvus、Langfuse。走不通时停下来问用户。
- 新增依赖只有 `langgraph` 和 `langgraph-checkpoint-sqlite`，用 `uv add` 添加。
- `.env` 不新增变量。不硬编码密钥和地址。
- 常量值：`CHECKPOINT_DB_PATH` = 项目根目录下 `data/checkpoints.sqlite`；`INTENT_TIMEOUT_SECONDS = 8`；`AGENT_MAX_STEPS = 4`；`AGENT_TOKEN_BUDGET = 16000`；`GRAPH_RECURSION_LIMIT = 25`；`GATE_MIN_SCORE = 0.20`。
- 意图 7 类，字面值固定：`物流`、`订单`、`商品咨询`、`退款退货`、`售后`、`投诉`、`闲聊`。
- 出口名固定：`knowledge`、`business`、`complaint`、`chitchat`。
- 节点名固定：`start_turn`、`resolve_reference`、`classify_intent`、`retrieve`、`confidence_gate`、`agent_model`、`agent_tools`、`fallback_reply`、`complaint_reply`、`chitchat_reply`、`finalize`。
- 每个节点进入时打 INFO 日志 `node=<节点名> conversation=<id>`。
- SSE 事件名：`session`、`token`、`tool_start`、`tool_end`、`citations`、`actions`、`done`、`error`。SSE 用 `ServerSentEvent(raw_data=json.dumps(..., ensure_ascii=False))`。
- `actions` 事件数据：`{"options": [{"type": "handoff"}, {"type": "ticket", "description": str, "ticket_type": str}]}`。
- 所有重试用 `app.retry.retry_async`（指数回退）。不新写重试循环。
- 测试不访问真实上游、嵌入、重排和生产 Milvus 集合。聊天模型用 `tests/fakes.py` 的 `ScriptedChatModel`；LLM runnable 用 `RunnableLambda`；数据库测试用 fixture `db`。
- `db/*.sql` 不改。ORM 不 `create_all`。
- 注释、文档、日志文字用中文，按 ASD-STE100 原则（短句、主动语态、一词一义）。
- 代码风格跟随周围代码：注释密度低，命名和现有模块一致。

## Review Focus

1. **上一轮中途失败后用户继续发消息**：checkpoint 的 `next` 停在失败节点。新一轮必须从 START 重新开始，历史中没有失败的那一轮。→ Task 7 `test_failed_turn_leaves_history_and_next_turn_restarts`。
2. **ch04 及以前的老会话继续聊天**：没有 checkpoint，`aget_state` 返回空值。必须按空历史正常回答，不报错。→ Task 8 `test_old_conversation_without_checkpoint_continues`。
3. **两个会话同时聊天，共用一个 sqlite 文件**：AsyncSqliteSaver 只有一个连接。两轮并发时都必须成功，历史不串。→ Task 8 `test_sqlite_checkpointer_concurrent_conversations`。
4. **Agent 调用 `offer_human_options` 时参数不全**（选了 ticket 但没填描述）：执行器返回 `invalid_arguments`，不发 `actions` 事件，模型可以改正。→ Task 6 `test_invalid_offer_args_emit_no_actions`。
5. **流式输出中途点「建工单」**：同一会话正在流式输出时，`POST /tickets` 返回 409，不写 `tickets` 表。→ Task 9 `test_ticket_while_streaming_is_409`。

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/agent/__init__.py`、`app/agent/bare_loop.py` | 新建 | 热身裸循环 |
| `scripts/bare_agent.py` | 新建 | 裸循环演示脚本 |
| `app/config.py` | 改 | 新常量 |
| `app/graph/__init__.py` | 新建 | 空文件 |
| `app/graph/control.py` | 新建 | 控制工具 `offer_human_options`、`actions_from_args` |
| `app/tools/registry.py` | 改 | `tools_for_model(names)`、注册控制工具、`CH04_CHAT_TOOLS` |
| `app/services/grounding.py` | 改 | `record_low_confidence(..., source=)` |
| `app/services/history.py` | 改 | 新增 `turn_messages_rows` |
| `app/schemas.py` | 改 | `INTENTS`、`IntentResult`、`TicketRequest` |
| `app/prompts.py` | 改 | `intent_prompt`、`AGENT_SYSTEM_TEMPLATE`、`render_agent_system`、3 句固定话术、`TICKET_CREATED_NOTE` |
| `app/llm.py` | 改 | `get_intent_classifier` |
| `app/graph/state.py` | 新建 | `ChatState`、`GraphContext`、`Action` |
| `app/graph/events.py` | 新建 | `emit`、`enter` |
| `app/graph/routing.py` | 新建 | 分流表和条件边函数 |
| `app/graph/nodes/__init__.py` | 新建 | 空文件 |
| `app/graph/nodes/turn.py` | 新建 | `start_turn`、`resolve_reference` |
| `app/graph/nodes/intent.py` | 新建 | `classify_intent` |
| `app/graph/nodes/replies.py` | 新建 | 3 个固定话术节点 |
| `app/graph/nodes/knowledge.py` | 新建 | `retrieve_evidence`、`confidence_gate` |
| `app/graph/nodes/agent.py` | 新建 | `agent_model`、`agent_tools` |
| `app/graph/nodes/finalize.py` | 新建 | `finalize` |
| `app/graph/builder.py` | 新建 | `build_graph`、`get_graph`、`set_graph`、`thread_config`、`open_graph` |
| `app/api/chat.py` | 改 | 用图替代 `stream_reply` |
| `app/api/tickets.py` | 新建 | `POST /tickets` |
| `app/main.py` | 改 | lifespan 打开 checkpointer；挂 tickets 路由 |
| `app/services/chat.py` | 删除 | 由图替代 |
| `app/web/index.html` | 改 | 按钮和确认框（Vibe Coding） |
| `evals/intent_samples.jsonl`、`evals/run_intent_eval.py` | 新建 | 意图样例集 |
| `evals/run_chat_samples.py`、`evals/chat_samples.md` | 改 | 适配图 |
| `evals/run_tool_selection_eval.py`、`evals/run_rag_eval.py` | 改 1 行 | 用 `CH04_CHAT_TOOLS` 保持 ch04 基线工具集 |
| `scripts/reset_db.sh` | 改 | 删除 sqlite 文件 |
| `scripts/demo5.sh` | 新建 | 验收脚本 |
| `.gitignore` | 改 | `data/` |
| `tests/conftest.py` | 改 | 意图识别器拦截、内存图、`use_intent` |
| `tests/test_bare_loop.py`、`tests/test_graph_nodes.py`、`tests/test_graph_agent.py`、`tests/test_graph.py`、`tests/test_tickets_api.py` | 新建 | 测试 |
| `tests/test_chat_api.py` | 重写 | 按图重写 |
| `tests/test_tools.py`、`tests/test_grounding.py`、`tests/test_history.py`、`tests/test_prompts.py` | 改 | 增加用例 |
| `CLAUDE.md` | 改 | 项目状态、命令、架构、约束（Claude 改） |

---

### Task 1: 热身——裸 Agent 循环

**Files:**
- Create: `app/agent/__init__.py`（空）、`app/agent/bare_loop.py`、`scripts/bare_agent.py`
- Test: `tests/test_bare_loop.py`

**Interfaces:**
- Consumes: `app.tools.mock_data.order(order_id, today)`、`mock_data.logistics(order_id, today)`；`app.config.get_settings()`。
- Produces: `run_bare_agent(client, model: str, question: str, *, today: date, max_steps: int = 4, extra_body: dict | None = None) -> BareResult`；`BareResult(answer: str, steps: int, calls: list[list[str]])`。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_bare_loop.py
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
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_bare_loop.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 3: 实现**

```python
# app/agent/bare_loop.py
"""热身：不用框架的 Agent 循环。调用 LLM；有工具调用就执行并把结果喂回去；没有就返回答案。"""

import json
from dataclasses import dataclass, field
from datetime import date

from app.tools import mock_data

SYSTEM = "你是售后客服助手。订单和物流问题调用工具查询，只根据工具结果回答，不编造。"

TOOLS = [
    {"type": "function", "function": {
        "name": "query_order",
        "description": "按订单号查询订单状态、商品和金额。",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string", "description": "订单号"}},
                       "required": ["order_id"]},
    }},
    {"type": "function", "function": {
        "name": "query_logistics",
        "description": "按订单号查询承运商、物流状态和轨迹。",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string", "description": "订单号"}},
                       "required": ["order_id"]},
    }},
]


@dataclass
class BareResult:
    answer: str
    steps: int
    calls: list[list[str]] = field(default_factory=list)


def _run_tool(name: str, arguments: str, today: date) -> dict:
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        return {"error": "invalid_arguments"}
    if name == "query_order":
        return mock_data.order(str(args.get("order_id", "")), today)
    if name == "query_logistics":
        return mock_data.logistics(str(args.get("order_id", "")), today)
    return {"error": "unknown_tool"}


async def run_bare_agent(client, model: str, question: str, *, today: date,
                         max_steps: int = 4, extra_body: dict | None = None) -> BareResult:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    extra = {"extra_body": extra_body} if extra_body else {}
    steps, calls = 0, []
    while True:
        if steps >= max_steps:
            # 达到步数上限：不再提供工具，让模型用文字收尾。
            resp = await client.chat.completions.create(model=model, messages=messages, **extra)
            return BareResult(resp.choices[0].message.content or "", steps, calls)
        resp = await client.chat.completions.create(model=model, messages=messages, tools=TOOLS, **extra)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return BareResult(msg.content or "", steps, calls)
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in msg.tool_calls
        ]})
        for c in msg.tool_calls:
            result = _run_tool(c.function.name, c.function.arguments, today)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result, ensure_ascii=False)})
        steps += 1
        calls.append([c.function.name for c in msg.tool_calls])
```

```python
# scripts/bare_agent.py
"""运行一次裸 Agent 循环，打印每一步。用法：uv run python scripts/bare_agent.py "订单 1001 到哪了" """

import asyncio
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI

from app.agent.bare_loop import run_bare_agent
from app.config import UPSTREAM_TIMEOUT_SECONDS, get_settings


async def main(question: str) -> int:
    s = get_settings()
    client = AsyncOpenAI(base_url=s.chat_base_url, api_key=s.chat_api_key.get_secret_value(),
                         timeout=UPSTREAM_TIMEOUT_SECONDS)
    extra = {"thinking": {"type": s.chat_thinking}} if s.chat_thinking else None
    result = await run_bare_agent(client, s.chat_model, question, today=date.today(), extra_body=extra)
    for i, names in enumerate(result.calls, 1):
        print(f"第 {i} 步：调用 {', '.join(names)}")
    print(f"共 {result.steps} 步。答案：\n{result.answer}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('用法：uv run python scripts/bare_agent.py "<问题>"')
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_bare_loop.py -q`
Expected: 6 passed

- [ ] **Step 5: 真实上游跑一次演示（Claude 执行）**

Run: `uv run python scripts/bare_agent.py "帮我看下订单 1001 买了什么，物流到哪了"`
Expected: 打印至少 1 步工具调用和一段中文答案。把输出摘要记入 dev-notes。

- [ ] **Step 6: 提交**

```bash
git add app/agent tests/test_bare_loop.py scripts/bare_agent.py
git commit -m "feat(ch05): bare agent loop warm-up without frameworks"
```

---

### Task 2: 基础件——依赖、常量、控制工具、注册表筛选、入池来源、轮次记录

**Files:**
- Modify: `pyproject.toml`、`uv.lock`（`uv add`）、`.gitignore`、`app/config.py`、`app/tools/registry.py`、`app/services/grounding.py:record_low_confidence`、`app/services/history.py`、`evals/run_tool_selection_eval.py`、`evals/run_rag_eval.py`
- Create: `app/graph/__init__.py`（空）、`app/graph/control.py`
- Test: `tests/test_tools.py`、`tests/test_grounding.py`、`tests/test_history.py`

**Interfaces:**
- Produces:
  - `app.config`：`CHECKPOINT_DB_PATH: str`、`INTENT_TIMEOUT_SECONDS`、`AGENT_MAX_STEPS`、`AGENT_TOKEN_BUDGET`、`GRAPH_RECURSION_LIMIT`、`GATE_MIN_SCORE`。
  - `app.graph.control.offer_human_options`（`BaseTool`，名称 `"offer_human_options"`）、`OfferHumanOptionsArgs`、`actions_from_args(args: dict) -> list[dict]`。
  - `ToolRegistry.tools_for_model(names: Sequence[str] | None = None) -> list[BaseTool]`；`app.tools.registry.CH04_CHAT_TOOLS: tuple[str, ...]`。
  - `record_low_confidence(conversation_id: int, raw_question: str, reason: str, source: str = "self_check") -> None`。
  - `app.services.history.turn_messages_rows(user_input: str, turn: Sequence[BaseMessage]) -> list[NewMessage]`。

- [ ] **Step 1: 添加依赖**

Run: `uv add langgraph langgraph-checkpoint-sqlite`
Expected: `pyproject.toml` 出现两个依赖。`uv run python -c "import langgraph.checkpoint.sqlite.aio"` 无报错。

- [ ] **Step 2: 写失败的测试**

追加到 `tests/test_tools.py`：

```python
from app.graph.control import OfferHumanOptionsArgs, actions_from_args
from app.tools.registry import CH04_CHAT_TOOLS


def test_tools_for_model_filters_by_name_in_given_order():
    reg = get_registry()
    names = [t.name for t in reg.tools_for_model(("query_logistics", "offer_human_options"))]
    assert names == ["query_logistics", "offer_human_options"]
    assert "offer_human_options" in [t.name for t in reg.tools_for_model()]
    with pytest.raises(KeyError):
        reg.tools_for_model(("nope",))


def test_ch04_tool_set_is_unchanged():
    assert CH04_CHAT_TOOLS == ("query_order", "query_product", "query_logistics", "query_faq", "create_ticket")


@pytest.mark.parametrize("args", [
    {"options": []},
    {"options": ["handoff", "handoff"]},
    {"options": ["ticket"]},
    {"options": ["ticket"], "ticket_description": "坏了"},
    {"options": ["call"]},
])
def test_offer_args_rejects_invalid(args):
    with pytest.raises(ValidationError):
        OfferHumanOptionsArgs.model_validate(args)


def test_actions_from_args():
    assert actions_from_args({"options": ["handoff"]}) == [{"type": "handoff"}]
    assert actions_from_args({"options": ["ticket", "handoff"], "ticket_description": "耳机坏了",
                              "ticket_type": "售后"}) == [
        {"type": "ticket", "description": "耳机坏了", "ticket_type": "售后"}, {"type": "handoff"}]


@pytest.mark.anyio
async def test_offer_tool_has_no_side_effect():
    spec = get_registry().get("offer_human_options")
    assert spec.retryable is False and spec.inject_conversation_id is False
    assert await spec.tool.ainvoke({"options": ["handoff"]}) == {"shown": ["handoff"]}
```

追加到 `tests/test_grounding.py`：

```python
@pytest.mark.anyio
async def test_record_low_confidence_source(db):
    from app.db.models import LowConfidenceQuestion
    from app.repositories import conversations
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.commit()
    await grounding.record_low_confidence(cid, "问", "分数低", source="retrieval_low_conf")
    await grounding.record_low_confidence(cid, "问2", "不够")
    async with db() as s:
        rows = (await s.execute(select(LowConfidenceQuestion).order_by(LowConfidenceQuestion.id))).scalars().all()
    assert [(r.source, r.reason) for r in rows] == [("retrieval_low_conf", "分数低"), ("self_check", "不够")]
```

（如果 `tests/test_grounding.py` 没有导入 `select` 和 `grounding`，在文件头补上 `from sqlalchemy import select` 和 `from app.services import grounding`。）

追加到 `tests/test_history.py`：

```python
from langchain_core.messages import AIMessage, ToolMessage

from app.services.history import turn_messages_rows


def test_turn_messages_rows_multi_round():
    turn = [
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "query_order", "args": {"order_id": "1"}}]),
        ToolMessage(content="{}", tool_call_id="c1"),
        AIMessage(content="我查一下物流", tool_calls=[{"id": "c2", "name": "query_logistics", "args": {"order_id": "1"}}]),
        ToolMessage(content="{}", tool_call_id="c2"),
        AIMessage(content="好了"),
    ]
    rows = turn_messages_rows("问", turn)
    assert [(r.role, r.content, r.tool_call_id) for r in rows] == [
        ("user", "问", None), ("assistant", None, None), ("tool", "{}", "c1"),
        ("assistant", "我查一下物流", None), ("tool", "{}", "c2"), ("assistant", "好了", None)]
    assert rows[1].tool_calls == [{"id": "c1", "name": "query_order", "args": {"order_id": "1"}}]
    assert rows[5].tool_calls is None


def test_turn_messages_rows_fixed_reply():
    rows = turn_messages_rows("你好", [AIMessage(content="您好")])
    assert [(r.role, r.content) for r in rows] == [("user", "你好"), ("assistant", "您好")]
```

- [ ] **Step 3: 运行，确认失败**

Run: `uv run pytest tests/test_tools.py tests/test_grounding.py tests/test_history.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.graph'` 等

- [ ] **Step 4: 实现**

`app/config.py` 追加（文件头加 `from pathlib import Path`）：

```python
CHECKPOINT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "checkpoints.sqlite")
# 限制意图识别等待时间，超时后走 business 出口。
INTENT_TIMEOUT_SECONDS = 8
AGENT_MAX_STEPS = 4
# 一轮中 Agent 累计 token（输入 + 输出，含思考）。
AGENT_TOKEN_BUDGET = 16000
GRAPH_RECURSION_LIMIT = 25
# 置信度闸的 Top-1 重排分门槛。与检索门槛分开设置。
GATE_MIN_SCORE = 0.20
```

`.gitignore` 追加一行 `data/`。

```python
# app/graph/control.py
"""控制工具：只把可选项交给用户，不做业务动作。"""

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator


class OfferHumanOptionsArgs(BaseModel):
    options: list[Literal["handoff", "ticket"]] = Field(
        min_length=1, max_length=2,
        description="给用户的可选项：handoff 为转人工，ticket 为建工单。可以只给一个",
    )
    ticket_description: str | None = Field(
        None, min_length=1, max_length=500, description="options 含 ticket 时必填，概括用户诉求")
    ticket_type: Literal["售后", "投诉", "咨询"] | None = Field(
        None, description="options 含 ticket 时必填")

    @model_validator(mode="after")
    def _check(self):
        if len(set(self.options)) != len(self.options):
            raise ValueError("options 不能重复")
        if "ticket" in self.options and (not self.ticket_description or not self.ticket_type):
            raise ValueError("options 含 ticket 时必须填写 ticket_description 和 ticket_type")
        return self


@tool("offer_human_options", args_schema=OfferHumanOptionsArgs)
async def offer_human_options(
    options: list[str], ticket_description: str | None = None, ticket_type: str | None = None
) -> dict:
    """在回复下方给用户展示「转人工」「建工单」按钮，由用户自己选择。只展示选项，不会转接，也不会创建工单。"""
    return {"shown": list(options)}


def actions_from_args(args: dict) -> list[dict]:
    parsed = OfferHumanOptionsArgs.model_validate(args)
    actions = []
    for option in parsed.options:
        if option == "handoff":
            actions.append({"type": "handoff"})
        else:
            actions.append({"type": "ticket", "description": parsed.ticket_description,
                            "ticket_type": parsed.ticket_type})
    return actions
```

`app/tools/registry.py`：

```python
# 新增导入
from collections.abc import Sequence
from app.graph.control import offer_human_options

# ch04 聊天服务绑定的工具集。ch04 的评估脚本把它当基线。
CH04_CHAT_TOOLS = ("query_order", "query_product", "query_logistics", "query_faq", "create_ticket")

# ToolRegistry.tools_for_model 改为：
    def tools_for_model(self, names: Sequence[str] | None = None) -> list[BaseTool]:
        if names is None:
            return [spec.tool for spec in self._specs.values()]
        return [self._specs[name].tool for name in names]

# build_default_registry 在 create_ticket 之后追加：
    registry.register(ToolSpec(offer_human_options, retryable=False, timeout=TOOL_TIMEOUT_SECONDS))
```

`evals/run_tool_selection_eval.py`、`evals/run_rag_eval.py` 和 `app/services/chat.py`：把 `get_registry().tools_for_model()` 改为 `get_registry().tools_for_model(CH04_CHAT_TOOLS)`，并导入 `CH04_CHAT_TOOLS`。其余不改。`app/services/chat.py` 在 Task 8 删除；本任务改它，是为了在删除前保持 ch04 行为和测试不变。

`app/services/grounding.py`：

```python
async def record_low_confidence(
    conversation_id: int, raw_question: str, reason: str, source: str = "self_check"
) -> None:
    """独立事务。失败只记日志，不中断本轮。"""
    try:
        async with get_sessionmaker()() as s:
            await low_confidence.add(
                s, conversation_id=conversation_id, raw_question=raw_question,
                source=source, reason=reason,
            )
            await s.commit()
    except Exception:
        logger.exception("低置信度问题入池失败")
```

`app/services/history.py` 追加：

```python
def turn_messages_rows(user_input: str, turn: Sequence[BaseMessage]) -> list[NewMessage]:
    """一轮的记录：用户消息，然后按顺序写 AI 工具请求、工具结果和最终回复。"""
    rows = [NewMessage(role="user", content=user_input)]
    for message in turn:
        if isinstance(message, ToolMessage):
            rows.append(NewMessage(role="tool", content=message.content, tool_call_id=message.tool_call_id))
        elif message.tool_calls:
            rows.append(NewMessage(
                role="assistant",
                content=message.content or None,
                tool_calls=[{"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls],
            ))
        else:
            rows.append(NewMessage(role="assistant", content=message.content))
    return rows
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/test_tools.py tests/test_grounding.py tests/test_history.py -q`
Expected: 全部通过

- [ ] **Step 6: 全量回归**

Run: `uv run pytest -q`
Expected: 全部通过（`app/services/chat.py` 用 `CH04_CHAT_TOOLS`，仍绑定 5 个工具）。

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml uv.lock .gitignore app/config.py app/graph app/tools/registry.py app/services/chat.py \
  app/services/grounding.py app/services/history.py evals/run_tool_selection_eval.py evals/run_rag_eval.py \
  tests/test_tools.py tests/test_grounding.py tests/test_history.py
git commit -m "feat(ch05): langgraph deps, agent constants, offer_human_options control tool"
```

---

### Task 3: 意图识别器（Prompt 任务，用标注样例集验证）

**Files:**
- Modify: `app/schemas.py`、`app/prompts.py`、`app/llm.py`
- Create: `evals/intent_samples.jsonl`、`evals/run_intent_eval.py`
- Test: `tests/test_prompts.py`（结构检查）

**Interfaces:**
- Produces: `app.schemas.INTENTS: tuple[str, ...]`、`IntentResult(intent: Literal[INTENTS])`；`app.prompts.intent_prompt`（变量 `text`）；`app.llm.get_intent_classifier() -> Runnable`，`ainvoke({"text": str})` 返回 `{"parsed": IntentResult | None, "raw": ..., "parsing_error": ...}`。

- [ ] **Step 1: 写结构测试**

追加到 `tests/test_prompts.py`：

```python
def test_intent_prompt_lists_all_intents():
    from app.prompts import INTENT_SYSTEM_PROMPT, intent_prompt
    from app.schemas import INTENTS
    assert INTENTS == ("物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊")
    for intent in INTENTS:
        assert f"- {intent}：" in INTENT_SYSTEM_PROMPT
    msgs = intent_prompt.invoke({"text": "订单 1001 到哪了"}).to_messages()
    assert msgs[-1].content == "订单 1001 到哪了"
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: FAIL，`ImportError: cannot import name 'INTENT_SYSTEM_PROMPT'`

- [ ] **Step 3: 实现**

`app/schemas.py` 追加：

```python
INTENTS = ("物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊")


class IntentResult(BaseModel):
    """用户这一句话的意图。"""

    intent: Literal[INTENTS] = Field(description="七类意图之一")
```

`app/prompts.py` 追加：

```python
INTENT_SYSTEM_PROMPT = """你是售后客服的意图分类器。把用户这一句话归入下列 7 类之一。只看这一句，不回答问题。

## 类别
- 物流：问包裹到哪了、什么时候到、快递单号、催发货、派送问题。例如"订单 1001 到哪了""怎么还没发货"。
- 订单：查订单状态、订单里买了什么、金额、修改或取消订单、改地址。例如"我的订单 A123 付款成功了吗"。
- 商品咨询：商品型号的参数、功能、使用方法、常见故障排查、保修政策。例如"X3 Pro 续航多久""扫地机器人不回充怎么办"。
- 退款退货：退货、换货、退款的政策、条件、运费、到账时间和流程。例如"拆封了还能退吗""退款多久到账"。
- 售后：针对自己某个订单发起或查询售后处理，例如申请维修、补发、查售后进度、要求转人工。例如"订单 1001 的耳机坏了要修""我的售后处理到哪了"。
- 投诉：对服务、物流、商家表达强烈不满，要求追责、赔偿、要说法，或明确说"投诉"。例如"我要投诉""你们客服太差了，我要个说法"。
- 闲聊：问候、感谢、告别，以及和购物售后无关的话。例如"你好""谢谢""今天天气怎么样"。

## 判定规则
1. 一句话里有多个诉求时，按用户最主要的诉求归类。
2. 明确说"投诉"，或带强烈不满并要求追责时，归投诉，即使同时提到物流或订单。
3. 只问政策和规则、没有指向自己的具体订单时：退换货问题归退款退货，商品参数和故障归商品咨询。
4. 要求转人工但没有表达不满时，归售后。"""

intent_prompt = ChatPromptTemplate.from_messages([
    ("system", INTENT_SYSTEM_PROMPT),
    ("human", "{text}"),
])
```

`app/llm.py`：导入 `intent_prompt` 和 `IntentResult`，追加：

```python
@lru_cache
def get_intent_classifier() -> Runnable:
    # 与改写器一样关闭思考：强制 tool_choice 与 DeepSeek 思考模式冲突。
    model = build_extract_model(get_settings())
    return intent_prompt | model.with_structured_output(
        IntentResult, method="function_calling", include_raw=True
    )
```

`evals/intent_samples.jsonl`（42 行，每类 6 条，逐字写入）：

```jsonl
{"text": "订单 1001 的物流到哪了", "intent": "物流"}
{"text": "我的快递怎么三天了还没动", "intent": "物流"}
{"text": "A20261003 什么时候能送到", "intent": "物流"}
{"text": "能帮我催一下发货吗，下单两天了", "intent": "物流"}
{"text": "快递单号是多少，我想自己查", "intent": "物流"}
{"text": "显示派送中但是我没收到", "intent": "物流"}
{"text": "帮我查下订单 1002 的状态", "intent": "订单"}
{"text": "我那单买了哪几样东西", "intent": "订单"}
{"text": "订单 A12345 付款成功了吗", "intent": "订单"}
{"text": "我想取消刚下的订单 2003", "intent": "订单"}
{"text": "订单 1005 的收货地址能改吗", "intent": "订单"}
{"text": "订单 3001 一共花了多少钱", "intent": "订单"}
{"text": "X3 Pro 耳机充满电能用多久", "intent": "商品咨询"}
{"text": "扫地机器人不回充怎么办", "intent": "商品咨询"}
{"text": "S10 Max 牙刷防水吗", "intent": "商品咨询"}
{"text": "羊毛衫能机洗吗", "intent": "商品咨询"}
{"text": "保温杯能装碳酸饮料吗", "intent": "商品咨询"}
{"text": "台灯的保修期是多久", "intent": "商品咨询"}
{"text": "退货运费谁出", "intent": "退款退货"}
{"text": "拆封了还能七天无理由退吗", "intent": "退款退货"}
{"text": "退款多久能到账", "intent": "退款退货"}
{"text": "衣服尺码不合适可以换吗", "intent": "退款退货"}
{"text": "我想退货，流程是怎样的", "intent": "退款退货"}
{"text": "钱什么时候退回来啊", "intent": "退款退货"}
{"text": "订单 1001 的耳机左耳没声音了，要修", "intent": "售后"}
{"text": "我的售后申请处理到哪一步了", "intent": "售后"}
{"text": "收到的保温杯少了个杯盖，给我补发", "intent": "售后"}
{"text": "订单 2002 的扫地机坏了，帮我安排维修", "intent": "售后"}
{"text": "给我转人工", "intent": "售后"}
{"text": "订单 1003 的鞋开胶了怎么处理", "intent": "售后"}
{"text": "我要投诉", "intent": "投诉"}
{"text": "你们快递员态度太差了，我要投诉", "intent": "投诉"}
{"text": "等了十天还没到，你们必须给我个说法", "intent": "投诉"}
{"text": "客服一直不回复，太差劲了，我要找你们领导", "intent": "投诉"}
{"text": "卖假货是吧？我要求赔偿", "intent": "投诉"}
{"text": "订单 1001 物流慢成这样，我要投诉你们", "intent": "投诉"}
{"text": "你好", "intent": "闲聊"}
{"text": "谢谢你啊", "intent": "闲聊"}
{"text": "今天天气怎么样", "intent": "闲聊"}
{"text": "你是机器人吗", "intent": "闲聊"}
{"text": "帮我写一首秋天的诗", "intent": "闲聊"}
{"text": "好的，没别的事了，再见", "intent": "闲聊"}
```

```python
# evals/run_intent_eval.py
"""用标注样例评估意图识别。准确率低于 90% 时退出码为 1。"""

import asyncio
from collections import Counter
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_intent_classifier
from app.schemas import INTENTS

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "intent_samples.jsonl"
PASS_RATE = 0.90


async def classify(sample: dict, semaphore: asyncio.Semaphore) -> str:
    async with semaphore:
        try:
            result = await get_intent_classifier().ainvoke({"text": sample["text"]})
            if result["parsed"] is None:
                raise ValueError(f"解析失败：{result.get('raw')!r}")
            return result["parsed"].intent
        except Exception:
            logger.exception("意图识别失败：%s", sample["text"])
            return "失败"


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(4)
    actual = await asyncio.gather(*(classify(s, semaphore) for s in samples))
    confusion = Counter()
    correct = 0
    for i, (sample, got) in enumerate(zip(samples, actual), 1):
        ok = got == sample["intent"]
        correct += ok
        confusion[(sample["intent"], got)] += 1
        print(f"{i:02d} {'✅' if ok else '❌'} {sample['text']} | 期望 {sample['intent']} → {got}")
    labels = [*INTENTS, "失败"]
    print("\n混淆矩阵（行：期望，列：实际）")
    print("\t" + "\t".join(labels))
    for expected in INTENTS:
        print(expected + "\t" + "\t".join(str(confusion[(expected, got)]) for got in labels))
    rate = correct / len(samples)
    print(f"\n准确率：{correct}/{len(samples)} = {rate:.1%}（门槛 {PASS_RATE:.0%}）")
    return 0 if rate >= PASS_RATE else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run_eval()))
```

- [ ] **Step 4: 运行结构测试，确认通过**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: 全部通过

- [ ] **Step 5: 跑标注样例集（替代 TDD 的验证步骤，真实上游，Claude 执行）**

Run: `uv run python evals/run_intent_eval.py`
Expected: 准确率 ≥ 90%，退出码 0。未达标时，把错判样例和混淆矩阵交给 Codex 只改 `INTENT_SYSTEM_PROMPT`（不改样例），重跑。把结果和改动次数记入 dev-notes。

- [ ] **Step 6: 提交**

```bash
git add app/schemas.py app/prompts.py app/llm.py evals/intent_samples.jsonl evals/run_intent_eval.py tests/test_prompts.py
git commit -m "feat(ch05): intent classifier prompt with 42 labeled samples"
```

---

### Task 4: 图的 State、事件、分流和简单节点

**Files:**
- Create: `app/graph/state.py`、`app/graph/events.py`、`app/graph/routing.py`、`app/graph/nodes/__init__.py`（空）、`app/graph/nodes/turn.py`、`app/graph/nodes/intent.py`、`app/graph/nodes/replies.py`
- Modify: `app/prompts.py`（3 句固定话术）、`tests/conftest.py`
- Test: `tests/test_graph_nodes.py`

**Interfaces:**
- Consumes: `get_intent_classifier`、`IntentResult`（Task 3）；`INTENT_TIMEOUT_SECONDS`（Task 2）。
- Produces:
  - `app.graph.state`：`ChatState`、`Action`、`GraphContext(conversation_id: int, today: date, model: BaseChatModel, execute: Callable = execute_tool_calls)`。
  - `app.graph.events`：`emit(name: str, data: dict) -> None`；`enter(node: str, state: dict, runtime) -> list[str]`（打日志并返回新的 trace）。测试中 monkeypatch `app.graph.events.get_stream_writer`。
  - `app.graph.routing`：`INTENT_ROUTES`、`FALLBACK_ROUTE`、`route_for(intent) -> str`、`after_intent(state) -> str`、`after_gate(state) -> str`、`after_agent(state) -> str`。
  - 节点：`start_turn`、`resolve_reference`、`classify_intent`、`chitchat_reply`、`complaint_reply`、`fallback_reply`。
  - `app.prompts`：`CHITCHAT_REPLY`、`COMPLAINT_REPLY`、`GATE_FALLBACK_REPLY`。
  - `tests/conftest.py`：autouse 拦截 `app.graph.nodes.intent.get_intent_classifier`；fixture `use_intent(*values)`（按调用顺序消费，值为意图字符串、`None` 表示 parsed 为空、异常实例表示抛出）；fixture `emitted`（收集 `emit` 的事件列表）；helper `rt(**kw)` 生成假 runtime。

- [ ] **Step 1: 写失败的测试**

`tests/conftest.py` 追加：

```python
@pytest.fixture(autouse=True)
def _block_intent_classifier(monkeypatch):
    from app.graph.nodes import intent
    monkeypatch.setattr(intent, "get_intent_classifier", _blocked_factory("get_intent_classifier"))


@pytest.fixture
def use_intent(monkeypatch):
    """用法：use_intent("物流", "闲聊")。每次识别消费一个值；None 表示解析失败；异常实例表示抛出。"""
    from langchain_core.runnables import RunnableLambda
    from app.graph.nodes import intent
    from app.schemas import IntentResult

    def _use(*values):
        queue = list(values)
        calls = []

        async def classify(inputs):
            calls.append(inputs)
            value = queue.pop(0)
            if isinstance(value, BaseException):
                raise value
            return {"parsed": None if value is None else IntentResult(intent=value), "raw": None}

        monkeypatch.setattr(intent, "get_intent_classifier", lambda: RunnableLambda(classify))
        return calls

    return _use


@pytest.fixture
def emitted(monkeypatch):
    """直接调用节点时，收集节点发出的事件。"""
    from app.graph import events
    out = []
    monkeypatch.setattr(events, "get_stream_writer", lambda: out.append)
    return out
```

`tests/fakes.py` 追加：

```python
def rt(conversation_id=1, model=None, execute=None, today=None):
    """直接调用节点时使用的假 runtime。"""
    from datetime import date
    from types import SimpleNamespace
    from app.graph.state import GraphContext
    from app.tools.executor import execute_tool_calls
    ctx = GraphContext(conversation_id=conversation_id, today=today or date(2026, 10, 6),
                       model=model, execute=execute or execute_tool_calls)
    return SimpleNamespace(context=ctx)
```

```python
# tests/test_graph_nodes.py
import asyncio

import pytest

from app.graph import routing
from app.graph.nodes.intent import classify_intent
from app.graph.nodes.replies import chitchat_reply, complaint_reply, fallback_reply
from app.graph.nodes.turn import resolve_reference, start_turn
from app.prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, GATE_FALLBACK_REPLY, REFUSAL_PREFIX
from tests.fakes import rt

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("intent,route", [
    ("商品咨询", "knowledge"), ("退款退货", "knowledge"),
    ("物流", "business"), ("订单", "business"), ("售后", "business"),
    ("投诉", "complaint"), ("闲聊", "chitchat"), (None, "business"),
])
def test_route_for(intent, route):
    assert routing.route_for(intent) == route


def test_every_intent_has_a_route():
    from app.schemas import INTENTS
    assert set(routing.INTENT_ROUTES) == set(INTENTS)


def test_after_gate_and_after_agent():
    from langchain_core.messages import AIMessage
    assert routing.after_gate({"gate": {"passed": True}}) == "agent_model"
    assert routing.after_gate({"gate": {"passed": False}}) == "fallback_reply"
    call = AIMessage(content="", tool_calls=[{"id": "c1", "name": "query_order", "args": {}}])
    assert routing.after_agent({"agent_messages": [call], "force_final": False}) == "agent_tools"
    assert routing.after_agent({"agent_messages": [call], "force_final": True}) == "finalize"
    assert routing.after_agent({"agent_messages": [AIMessage(content="好")]}) == "finalize"


async def test_start_turn_resets_turn_fields(caplog):
    caplog.set_level("INFO")
    stale = {"intent": "投诉", "route": "complaint", "evidence": [{"n": 1}], "gate": {"passed": False},
             "agent_messages": ["x"], "steps": 3, "tokens_used": 999, "force_final": True,
             "reply": "旧", "actions": [{"type": "handoff"}], "trace": ["a", "b"]}
    out = await start_turn(stale, rt(conversation_id=7))
    assert out == {"resolved_input": "", "intent": None, "route": "", "evidence": [], "gate": None,
                   "agent_messages": [], "steps": 0, "tokens_used": 0, "force_final": False,
                   "reply": "", "actions": [], "trace": ["start_turn"]}
    assert "node=start_turn conversation=7" in caplog.text


async def test_resolve_reference_passes_through():
    out = await resolve_reference({"user_input": "那它呢", "trace": ["start_turn"]}, rt())
    assert out == {"resolved_input": "那它呢", "trace": ["start_turn", "resolve_reference"]}


async def test_classify_intent_sets_route(use_intent):
    calls = use_intent("物流")
    out = await classify_intent({"resolved_input": "到哪了", "trace": []}, rt())
    assert (out["intent"], out["route"]) == ("物流", "business")
    assert calls == [{"text": "到哪了"}]


@pytest.mark.parametrize("value", [None, RuntimeError("boom"), asyncio.TimeoutError()])
async def test_classify_intent_failure_falls_back(use_intent, value, caplog):
    use_intent(value)
    out = await classify_intent({"resolved_input": "q", "trace": []}, rt())
    assert (out["intent"], out["route"]) == (None, "business")
    assert "意图识别失败" in caplog.text


async def test_classify_intent_timeout(monkeypatch, use_intent):
    from langchain_core.runnables import RunnableLambda
    from app.graph.nodes import intent as intent_mod
    monkeypatch.setattr(intent_mod, "INTENT_TIMEOUT_SECONDS", 0.01)

    async def slow(_):
        await asyncio.sleep(1)

    monkeypatch.setattr(intent_mod, "get_intent_classifier", lambda: RunnableLambda(slow))
    out = await classify_intent({"resolved_input": "q", "trace": []}, rt())
    assert out["route"] == "business"


async def test_chitchat_reply(emitted):
    out = await chitchat_reply({"trace": []}, rt())
    assert out == {"reply": CHITCHAT_REPLY, "trace": ["chitchat_reply"]}
    assert emitted == [("token", {"text": CHITCHAT_REPLY})]


async def test_complaint_reply_offers_two_independent_options(emitted):
    out = await complaint_reply({"user_input": "我要投诉" + "很" * 600, "trace": []}, rt())
    assert out["reply"] == COMPLAINT_REPLY
    handoff, ticket = out["actions"]
    assert handoff == {"type": "handoff"}
    assert ticket["type"] == "ticket" and ticket["ticket_type"] == "投诉" and len(ticket["description"]) == 500
    assert emitted == [("token", {"text": COMPLAINT_REPLY}), ("actions", {"options": out["actions"]})]


async def test_fallback_reply(emitted):
    out = await fallback_reply({"trace": []}, rt())
    assert out["reply"] == GATE_FALLBACK_REPLY and GATE_FALLBACK_REPLY.startswith(REFUSAL_PREFIX)
    assert emitted == [("token", {"text": GATE_FALLBACK_REPLY})]
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_graph_nodes.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.graph.routing'`

- [ ] **Step 3: 实现**

`app/prompts.py` 追加（放在 `REFUSAL_PREFIX` 定义之后）：

```python
CHITCHAT_REPLY = "您好，我是示例商城的售后客服助手。订单、物流、退换货和商品使用问题都可以问我。"
COMPLAINT_REPLY = "非常抱歉给您带来不好的体验，您的反馈我们很重视。如果需要，您可以选择转人工客服，或者提交一张投诉工单，我们会尽快跟进。"
GATE_FALLBACK_REPLY = REFUSAL_PREFIX + "您可以换个问法再试，或者补充商品型号等具体信息。"
```

```python
# app/graph/state.py
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.tools.executor import execute_tool_calls


class Action(TypedDict, total=False):
    type: Literal["handoff", "ticket"]
    description: str
    ticket_type: str


class ChatState(TypedDict, total=False):
    # 跨轮字段：checkpointer 持久化，只由 finalize 和 POST /tickets 追加。
    messages: Annotated[list[AnyMessage], add_messages]
    # 本轮字段：start_turn 每轮重置，节点整体覆盖。
    user_input: str
    resolved_input: str
    intent: str | None
    route: str
    evidence: list[dict]
    gate: dict | None
    agent_messages: list[AnyMessage]
    steps: int
    tokens_used: int
    force_final: bool
    reply: str
    actions: list[Action]
    trace: list[str]


@dataclass
class GraphContext:
    """每轮的运行时依赖，不持久化。"""

    conversation_id: int
    today: date
    model: BaseChatModel
    execute: Callable[..., Any] = execute_tool_calls
```

```python
# app/graph/events.py
import logging

from langgraph.config import get_stream_writer

logger = logging.getLogger("app.graph")


def emit(name: str, data: dict) -> None:
    """发出一个 SSE 事件。API 层用 stream_mode="custom" 接收 (name, data)。"""
    get_stream_writer()((name, data))


def enter(node: str, state: dict, runtime) -> list[str]:
    logger.info("node=%s conversation=%s", node, runtime.context.conversation_id)
    return [*state.get("trace", []), node]
```

```python
# app/graph/routing.py
"""分流规则写死在代码中。"""

INTENT_ROUTES = {
    "商品咨询": "knowledge",
    "退款退货": "knowledge",
    "物流": "business",
    "订单": "business",
    "售后": "business",
    "投诉": "complaint",
    "闲聊": "chitchat",
}
# 意图识别失败时走 business：Agent 有工具，能处理大多数问题。
FALLBACK_ROUTE = "business"


def route_for(intent: str | None) -> str:
    return INTENT_ROUTES.get(intent, FALLBACK_ROUTE) if intent else FALLBACK_ROUTE


def after_intent(state: dict) -> str:
    return state["route"]


def after_gate(state: dict) -> str:
    return "agent_model" if state["gate"]["passed"] else "fallback_reply"


def after_agent(state: dict) -> str:
    last = state["agent_messages"][-1]
    if getattr(last, "tool_calls", None) and not state.get("force_final"):
        return "agent_tools"
    return "finalize"
```

```python
# app/graph/nodes/turn.py
from app.graph import events


async def start_turn(state, runtime):
    return {
        "resolved_input": "", "intent": None, "route": "", "evidence": [], "gate": None,
        "agent_messages": [], "steps": 0, "tokens_used": 0, "force_final": False,
        "reply": "", "actions": [], "trace": events.enter("start_turn", {}, runtime),
    }


async def resolve_reference(state, runtime):
    # 最简版：原样透传。正式的指代消解放在下一章。
    return {"resolved_input": state["user_input"],
            "trace": events.enter("resolve_reference", state, runtime)}
```

```python
# app/graph/nodes/intent.py
import asyncio
import logging

from app.config import INTENT_TIMEOUT_SECONDS
from app.graph import events
from app.graph.routing import route_for
from app.llm import get_intent_classifier

logger = logging.getLogger(__name__)


async def classify_intent(state, runtime):
    trace = events.enter("classify_intent", state, runtime)
    # 工厂在 try 之外调用：配置错误和测试中未替换时立即暴露。
    classifier = get_intent_classifier()
    intent = None
    try:
        result = await asyncio.wait_for(
            classifier.ainvoke({"text": state["resolved_input"]}), INTENT_TIMEOUT_SECONDS)
        if result["parsed"] is None:
            raise ValueError(f"意图结果无效：raw={result.get('raw')!r}")
        intent = result["parsed"].intent
    except Exception:
        logger.warning("意图识别失败，走兜底出口", exc_info=True)
    route = route_for(intent)
    logger.info("intent=%s route=%s conversation=%s", intent, route, runtime.context.conversation_id)
    return {"intent": intent, "route": route, "trace": trace}
```

```python
# app/graph/nodes/replies.py
"""固定话术出口。不调用回答模型。"""

from app.graph import events
from app.prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, GATE_FALLBACK_REPLY

TICKET_DESCRIPTION_MAX_CHARS = 500


async def chitchat_reply(state, runtime):
    trace = events.enter("chitchat_reply", state, runtime)
    events.emit("token", {"text": CHITCHAT_REPLY})
    return {"reply": CHITCHAT_REPLY, "trace": trace}


async def complaint_reply(state, runtime):
    trace = events.enter("complaint_reply", state, runtime)
    actions = [
        {"type": "handoff"},
        {"type": "ticket", "description": state["user_input"][:TICKET_DESCRIPTION_MAX_CHARS],
         "ticket_type": "投诉"},
    ]
    events.emit("token", {"text": COMPLAINT_REPLY})
    events.emit("actions", {"options": actions})
    return {"reply": COMPLAINT_REPLY, "actions": actions, "trace": trace}


async def fallback_reply(state, runtime):
    trace = events.enter("fallback_reply", state, runtime)
    events.emit("token", {"text": GATE_FALLBACK_REPLY})
    return {"reply": GATE_FALLBACK_REPLY, "trace": trace}
```

注意：`classify_intent` 的超时测试用 monkeypatch 改 `app.graph.nodes.intent.INTENT_TIMEOUT_SECONDS`，所以该模块必须用 `from app.config import INTENT_TIMEOUT_SECONDS`，并在函数内按模块全局名读取。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_graph_nodes.py -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add app/graph app/prompts.py tests/conftest.py tests/fakes.py tests/test_graph_nodes.py
git commit -m "feat(ch05): graph state, routing table and fixed-reply nodes"
```

---

### Task 5: 知识检索节点和置信度闸

**Files:**
- Create: `app/graph/nodes/knowledge.py`
- Test: `tests/test_graph_nodes.py`（追加）

**Interfaces:**
- Consumes: `app.knowledge.retrieval.retrieve(question) -> Retrieval(plan, ranked, evidence)`；`app.services.grounding.collect_evidence`、`Citation`、`self_check`、`record_low_confidence(..., source=)`、`EMPTY_EVIDENCE_REASON`；`GATE_MIN_SCORE`。
- Produces: `retrieve_evidence(state, runtime) -> {"evidence": list[dict], "gate": {"passed": False, "top_score": float | None, "reason": "", "source": None}, "trace"}`；`confidence_gate(state, runtime) -> {"gate": {"passed": bool, "top_score", "reason": str, "source": str | None}, "trace"}`。`evidence` 每项为 `Citation.to_dict()`：`{"n", "chunk_id", "section_path", "question", "answer"}`。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_graph_nodes.py`：

```python
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import LowConfidenceQuestion
from app.graph.nodes import knowledge as knowledge_nodes
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.repositories import conversations
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding


def fake_retrieval(scores):
    items = [EvidenceItem(100 + i, f"退换货 > 规则{i}", f"问{i}", f"答{i}", s) for i, s in enumerate(scores)]
    kept = [e for e in items if e.score >= 0.20]
    return Retrieval(QueryPlan(standard_query="q"), items, kept)


async def new_conversation(db):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.commit()
    return cid


def use_checker(monkeypatch, useful=True, reason="依据[1]"):
    calls = []

    async def check(inputs):
        calls.append(inputs)
        return {"parsed": SelfCheck(useful=useful, reason=reason), "raw": None}

    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))
    return calls


async def test_retrieve_numbers_evidence_and_records_top_score(monkeypatch, caplog):
    caplog.set_level("INFO")
    seen = []

    async def fake(question):
        seen.append(question)
        return fake_retrieval([0.9, 0.5, 0.1])

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    out = await knowledge_nodes.retrieve_evidence({"resolved_input": "退货运费谁出", "trace": []}, rt())
    assert seen == ["退货运费谁出"]
    assert [(e["n"], e["chunk_id"]) for e in out["evidence"]] == [(1, 100), (2, 101)]
    assert set(out["evidence"][0]) == {"n", "chunk_id", "section_path", "question", "answer"}
    assert out["gate"]["top_score"] == 0.9 and out["trace"] == ["retrieve"]
    assert "node=retrieve" in caplog.text


async def test_retrieve_with_no_hits(monkeypatch):
    async def fake(question):
        return Retrieval(QueryPlan(standard_query="q"), [], [])

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    out = await knowledge_nodes.retrieve_evidence({"resolved_input": "q", "trace": []}, rt())
    assert out["evidence"] == [] and out["gate"]["top_score"] is None


def gate_state(evidence, top):
    return {"user_input": "原话", "resolved_input": "原话", "evidence": evidence,
            "gate": {"passed": False, "top_score": top, "reason": "", "source": None}, "trace": []}


EVIDENCE = [{"n": 1, "chunk_id": 100, "section_path": "退换货 > 运费", "question": "退货运费谁出",
             "answer": "质量问题商家承担"}]


async def test_gate_passes_and_emits_citations(db, monkeypatch, emitted):
    calls = use_checker(monkeypatch)
    out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, 0.8), rt(await new_conversation(db)))
    assert out["gate"] == {"passed": True, "top_score": 0.8, "reason": "依据[1]", "source": None}
    assert "[1] 退换货 > 运费" in calls[0]["evidence"]
    assert emitted == [("citations", {"items": EVIDENCE, "refused": False})]


@pytest.mark.parametrize("evidence,top", [([], None), (EVIDENCE, 0.1)])
async def test_gate_low_score_pools_without_self_check(db, monkeypatch, emitted, evidence, top):
    monkeypatch.setattr(knowledge_nodes, "GATE_MIN_SCORE", 0.2)
    cid = await new_conversation(db)
    out = await knowledge_nodes.confidence_gate(gate_state(evidence, top), rt(cid))
    assert out["gate"]["passed"] is False and out["gate"]["source"] == "retrieval_low_conf"
    assert emitted == []
    async with db() as s:
        row = (await s.execute(select(LowConfidenceQuestion))).scalar_one()
    assert (row.conversation_id, row.raw_question, row.source) == (cid, "原话", "retrieval_low_conf")


async def test_gate_self_check_not_useful_pools(db, monkeypatch, emitted):
    use_checker(monkeypatch, useful=False, reason="没写到防水")
    cid = await new_conversation(db)
    out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, 0.8), rt(cid))
    assert out["gate"] == {"passed": False, "top_score": 0.8, "reason": "没写到防水", "source": "self_check"}
    assert emitted == []
    async with db() as s:
        row = (await s.execute(select(LowConfidenceQuestion))).scalar_one()
    assert (row.source, row.reason) == ("self_check", "没写到防水")


async def test_gate_self_check_failure_fails_open(db, monkeypatch, emitted):
    async def boom(_):
        raise RuntimeError("upstream")

    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(boom))
    out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, 0.8), rt(await new_conversation(db)))
    assert out["gate"]["passed"] is True
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_graph_nodes.py -q -k "retrieve or gate"`
Expected: FAIL，`ImportError: cannot import name 'knowledge' from 'app.graph.nodes'`

- [ ] **Step 3: 实现**

```python
# app/graph/nodes/knowledge.py
"""知识类出口：强制检索，再过置信度闸。"""

from dataclasses import asdict

from app.config import GATE_MIN_SCORE
from app.graph import events
from app.knowledge.retrieval import retrieve
from app.services.grounding import (
    EMPTY_EVIDENCE_REASON,
    Citation,
    collect_evidence,
    record_low_confidence,
    self_check,
)


async def retrieve_evidence(state, runtime):
    trace = events.enter("retrieve", state, runtime)
    question = state["resolved_input"]
    result = await retrieve(question)
    evidence = collect_evidence([("retrieve", question, {"evidence": [asdict(e) for e in result.evidence]})])
    top = result.ranked[0].score if result.ranked else None
    return {
        "evidence": [c.to_dict() for c in evidence.citations],
        "gate": {"passed": False, "top_score": top, "reason": "", "source": None},
        "trace": trace,
    }


async def confidence_gate(state, runtime):
    trace = events.enter("confidence_gate", state, runtime)
    citations = [Citation(**c) for c in state["evidence"]]
    top = state["gate"]["top_score"]
    if not citations or top is None or top < GATE_MIN_SCORE:
        gate = {"passed": False, "top_score": top, "reason": EMPTY_EVIDENCE_REASON,
                "source": "retrieval_low_conf"}
    else:
        check = await self_check([state["resolved_input"]], citations)
        gate = {"passed": check.useful, "top_score": top, "reason": check.reason,
                "source": None if check.useful else "self_check"}
    if gate["passed"]:
        events.emit("citations", {"items": state["evidence"], "refused": False})
    else:
        # 独立事务，失败只记日志。
        await record_low_confidence(
            runtime.context.conversation_id, state["user_input"], gate["reason"], source=gate["source"])
    return {"gate": gate, "trace": trace}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_graph_nodes.py -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add app/graph/nodes/knowledge.py tests/test_graph_nodes.py
git commit -m "feat(ch05): forced retrieval node and confidence gate with low-confidence pooling"
```

---

### Task 6: 主力 Agent 节点（ReAct 的两个节点）和 Agent System Prompt

**Files:**
- Create: `app/graph/nodes/agent.py`
- Modify: `app/prompts.py`（`AGENT_SYSTEM_TEMPLATE`、`render_agent_system`）
- Test: `tests/test_graph_agent.py`、`tests/test_prompts.py`（追加）

**Interfaces:**
- Consumes: `tools_for_model(names)`、`actions_from_args`（Task 2）；`events`（Task 4）；`format_evidence`、`Citation`（ch04）；`build_history`、`count_tokens`（`app/context.py`）；`TOKEN_BUDGET`、`AGENT_MAX_STEPS`、`AGENT_TOKEN_BUDGET`；`TOOL_ROUND_CLOSING`。
- Produces:
  - `app.prompts.AGENT_SYSTEM_TEMPLATE`（变量 `shop_name`、`today`、`evidence_section`）、`render_agent_system(today: date, evidence_text: str = "") -> str`。
  - `app.graph.nodes.agent`：`AGENT_TOOLS`、`AgentOutputError(RuntimeError)`、`agent_model(state, runtime)`、`agent_tools(state, runtime)`。
  - `agent_model` 返回 `{"agent_messages": [...旧, AIMessage], "tokens_used": int, "trace", 以及无工具调用时 "reply": str}`。
  - `agent_tools` 返回 `{"agent_messages": [...旧, *ToolMessage], "steps": int, "trace", 可选 "actions", 可选 "force_final": True}`。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_prompts.py`：

```python
def test_agent_system_prompt():
    from datetime import date
    from app.prompts import render_agent_system
    plain = render_agent_system(date(2026, 10, 6))
    assert "示例商城" in plain and "2026-10-06" in plain
    assert "offer_human_options" in plain and "create_ticket" not in plain and "query_faq" not in plain
    assert "## 知识库证据" not in plain and "{" not in plain
    with_evidence = render_agent_system(date(2026, 10, 6), "[1] 退换货 > 运费\n问：q\n答：a {x}")
    assert "## 知识库证据\n[1] 退换货 > 运费" in with_evidence and "{x}" in with_evidence
```

```python
# tests/test_graph_agent.py
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from app.graph.nodes import agent as agent_mod
from app.graph.nodes.agent import AGENT_TOOLS, AgentOutputError, agent_model, agent_tools
from app.tools.executor import ToolOutcome
from tests.fakes import Recorder, ScriptedChatModel, rt, text, tools

pytestmark = pytest.mark.anyio


def model(*scripts):
    rec = Recorder()
    return ScriptedChatModel(scripts=list(scripts), recorder=rec), rec


def state(**kw):
    base = {"messages": [], "resolved_input": "订单 1001 到哪了", "user_input": "订单 1001 到哪了",
            "evidence": [], "agent_messages": [], "steps": 0, "tokens_used": 0, "force_final": False,
            "trace": []}
    return {**base, **kw}


async def test_binds_agent_tools_and_streams_answer(emitted):
    m, rec = model(text("您好"))
    out = await agent_model(state(), rt(model=m))
    assert rec[0]["tools"] == list(AGENT_TOOLS) and rec[0]["tool_choice"] == "auto"
    assert AGENT_TOOLS == ("query_order", "query_logistics", "query_product", "offer_human_options")
    assert out["reply"] == "您好" and out["agent_messages"][-1].content == "您好"
    assert emitted == [("token", {"text": "您"}), ("token", {"text": "好"})]
    sent = rec[0]["messages"]
    assert isinstance(sent[0], SystemMessage) and isinstance(sent[-1], HumanMessage)
    assert out["tokens_used"] > 0 and out["trace"] == ["agent_model"]


async def test_tool_call_has_no_reply():
    m, _ = model(tools(("c1", "query_logistics", {"order_id": "1001"})))
    out = await agent_model(state(), rt(model=m))
    assert "reply" not in out
    assert out["agent_messages"][-1].tool_calls[0]["name"] == "query_logistics"


async def test_history_and_turn_messages_are_sent_in_order():
    history = [HumanMessage("上一句"), AIMessage("上一答")]
    prior = [AIMessage(content="", tool_calls=[{"id": "c1", "name": "query_order", "args": {"order_id": "1"}}]),
             ToolMessage(content="{}", tool_call_id="c1")]
    m, rec = model(text("好"))
    await agent_model(state(messages=history, agent_messages=prior), rt(model=m))
    sent = rec[0]["messages"]
    assert [type(x).__name__ for x in sent] == [
        "SystemMessage", "HumanMessage", "AIMessage", "HumanMessage", "AIMessage", "ToolMessage"]


async def test_evidence_goes_into_system_prompt():
    evidence = [{"n": 1, "chunk_id": 9, "section_path": "退换货 > 运费", "question": "运费谁出", "answer": "商家"}]
    m, rec = model(text("商家承担[1]"))
    await agent_model(state(evidence=evidence), rt(model=m))
    system = rec[0]["messages"][0].content
    assert "## 知识库证据\n[1] 退换货 > 运费\n问：运费谁出\n答：商家" in system


async def test_force_final_unbinds_tools_and_appends_closing():
    m, rec = model(text("只能查到这些"))
    out = await agent_model(state(force_final=True), rt(model=m))
    assert rec[0]["tools"] == []
    assert isinstance(rec[0]["messages"][-1], SystemMessage) and "本轮不能再调用任何工具" in rec[0]["messages"][-1].content
    assert out["reply"] == "只能查到这些"


async def test_usage_metadata_is_preferred_for_tokens():
    chunk = AIMessageChunk(content="", usage_metadata={"input_tokens": 1000, "output_tokens": 234,
                                                       "total_tokens": 1234})
    m, _ = model([*text("好"), chunk])
    out = await agent_model(state(tokens_used=100), rt(model=m))
    assert out["tokens_used"] == 1334


@pytest.mark.parametrize("script", [
    [],
    text("<｜DSML｜invoke"),
    text("好的 invoke name=query_order"),
])
async def test_bad_output_raises(script, emitted):
    m, _ = model(script)
    with pytest.raises(AgentOutputError):
        await agent_model(state(), rt(model=m))
    assert not any(e[1]["text"].startswith("<") for e in emitted if e[0] == "token")


async def test_upstream_exception_propagates():
    m, _ = model([RuntimeError("upstream")])
    with pytest.raises(RuntimeError):
        await agent_model(state(), rt(model=m))


def call_state(*calls, **kw):
    msg = AIMessage(content="", tool_calls=[{"id": c, "name": n, "args": a} for c, n, a in calls])
    return state(agent_messages=[msg], **kw)


async def test_agent_tools_executes_and_counts_step(emitted):
    seen = []

    async def execute(calls, *, conversation_id):
        seen.append((calls, conversation_id))
        return [ToolOutcome("c1", "query_logistics", True,
                            ToolMessage(content='{"ok": true}', tool_call_id="c1", name="query_logistics"),
                            data={})]

    out = await agent_tools(call_state(("c1", "query_logistics", {"order_id": "1001"})),
                            rt(conversation_id=5, execute=execute))
    assert seen[0][1] == 5 and out["steps"] == 1 and "force_final" not in out
    assert isinstance(out["agent_messages"][-1], ToolMessage)
    assert emitted == [
        ("tool_start", {"tools": [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]}),
        ("tool_end", {"tools": [{"id": "c1", "name": "query_logistics", "ok": True}]}),
    ]


async def test_offer_human_options_emits_actions(emitted):
    args = {"options": ["handoff", "ticket"], "ticket_description": "耳机坏了", "ticket_type": "售后"}
    out = await agent_tools(call_state(("c1", "offer_human_options", args)), rt())
    assert out["actions"] == [{"type": "handoff"},
                              {"type": "ticket", "description": "耳机坏了", "ticket_type": "售后"}]
    assert emitted[-1] == ("actions", {"options": out["actions"]})
    assert json.loads(out["agent_messages"][-1].content) == {"ok": True, "data": {"shown": ["handoff", "ticket"]}}


async def test_invalid_offer_args_emit_no_actions(emitted):
    out = await agent_tools(call_state(("c1", "offer_human_options", {"options": ["ticket"]})), rt())
    assert "actions" not in out
    assert [e[0] for e in emitted] == ["tool_start", "tool_end"]
    assert json.loads(out["agent_messages"][-1].content)["error"] == "invalid_arguments"


async def test_step_limit_sets_force_final(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(agent_mod, "AGENT_MAX_STEPS", 2)
    out = await agent_tools(call_state(("c1", "query_order", {"order_id": "1"}), steps=1), rt())
    assert out["steps"] == 2 and out["force_final"] is True
    assert "agent_limit reason=steps" in caplog.text


async def test_token_limit_sets_force_final(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(agent_mod, "AGENT_TOKEN_BUDGET", 500)
    out = await agent_tools(call_state(("c1", "query_order", {"order_id": "1"}), tokens_used=600), rt())
    assert out["force_final"] is True and "agent_limit reason=tokens" in caplog.text
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_graph_agent.py tests/test_prompts.py -q`
Expected: FAIL，`ImportError`

- [ ] **Step 3: 实现 Prompt**

`app/prompts.py` 追加（放在 `render_chat_system` 之后）。`CHAT_SYSTEM_TEMPLATE` 和 `chat_prompt` 不改：

```python
_AGENT_REFUSAL_RULE = (
    '店铺政策或商品型号问题，知识库证据没有写到用户所问的点时，'
    f'以"{REFUSAL_PREFIX}"开头（一字不差），然后可以调用 offer_human_options 给出转人工选项。'
    '不根据常识推测。同一条消息中的其他问题照常回答。'
)

AGENT_SYSTEM_TEMPLATE = """你是{shop_name}的售后客服助手。今天是{today}。

## 职责
帮助用户处理退货、换货、退款、维修、投诉和售后咨询，并解答店铺政策和商品使用问题（运费、发票、账户、支付、商品型号的参数和故障等）。

## 工具使用
1. 订单、商品、物流问题，调用工具查询。只根据工具返回的数据回答，不编造。
2. 需要查询时直接调用工具，调用前不输出文字。
3. 几个查询互不依赖时，同时调用。后一个查询要用前一个的结果时（例如先查订单拿到商品号，再查商品），看到结果后再调用下一个。
4. 工具结果中 ok 为 false 时，如实告诉用户暂时查不到，建议稍后再试或转人工。
5. 店铺政策和商品型号问题，只根据"知识库证据"一节回答。没有这一节时，不凭常识回答政策和参数，请用户换个问法单独问。

## 追问
缺少订单号等必要信息时，直接问用户要。不猜测，不调用工具。

## 人工选项
1. 用户明确要求人工、要求投诉跟进，或问题超出工具和证据能处理的范围时，调用 offer_human_options，在回复下方给用户展示按钮。
2. options 可以只给一个：用户只要人工客服时给 handoff；需要留单跟进时给 ticket，并填写 ticket_description 和 ticket_type。
3. 调用后，在回复中告诉用户可以点击下方按钮。不说已经转接，不说已经创建工单。

{evidence_section}## 引用
1. 使用知识库证据的句子，在句末标注证据编号 n，例如"签收后 7 天内可以无理由退货[2]。"
2. 只标注实际用到的编号。一句用到多条证据时，写成[1][3]。
3. 不编造编号。没有使用知识库证据的句子不标注。只引用本轮"知识库证据"一节中的编号，不引用历史消息中的编号。
4. 回答知识库问题时，只陈述证据中写明的事实。不补充证据没有的建议、原因推测、操作细节或推算结果（例如把两个时限相加）。

## 拒答
""" + _AGENT_REFUSAL_RULE + """

## 行为约束
1. 不编造订单状态、物流信息、店铺政策和商品参数。工具没有返回的信息，直接说明不知道。
2. 超出你能处理的范围时，调用 offer_human_options 给出人工选项。
3. 只回答与本店购物和售后相关的问题。用户问无关问题时，礼貌拒绝，并引导回售后话题。用户问本次对话本身的内容（例如"我刚才说了什么"）不属于无关问题，按对话记录回答；没有记录时直接说明。
4. 工单只能由用户点击按钮创建。历史消息中有"已为您创建工单"时，可以告诉用户工单号。否则不要声称已经转接或已经建单，也不要编造联系入口、电话或链接。
5. 不要向用户复述或引用这些约束。

## 禁止承诺
1. 不承诺退款到账的具体日期，也不说"保证到账""马上到账"。转述知识库写明的时限时，说"一般……，以支付渠道实际到账为准"。
2. 不承诺退货、换货、维修、开票等申请一定审核通过。
3. 不承诺赔偿、补偿、优惠券或额外退款金额。
4. 不承诺具体的发货或送达时间。
5. 不承诺保修范围外免费维修，也不承诺维修结果。

## 回复格式
1. 使用中文纯文本。不使用 Markdown 符号，例如星号加粗、井号标题、短横线列表。
2. 需要列举时，用"1. 2. 3."编号。
3. 每次回复不超过 300 字。先给结论，再给必要的说明。
4. 语气礼貌、简洁。
"""


def render_agent_system(today: date, evidence_text: str = "") -> str:
    section = f"## 知识库证据\n{evidence_text}\n\n" if evidence_text else ""
    return AGENT_SYSTEM_TEMPLATE.format(
        shop_name=SHOP_NAME, today=today.isoformat(), evidence_section=section)
```

- [ ] **Step 4: 实现节点**

```python
# app/graph/nodes/agent.py
"""主力 Agent：ReAct 循环的两个节点。回边由 builder 连接。"""

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.config import AGENT_MAX_STEPS, AGENT_TOKEN_BUDGET, TOKEN_BUDGET
from app.context import build_history, count_tokens
from app.graph import events
from app.graph.control import actions_from_args
from app.prompts import TOOL_ROUND_CLOSING, render_agent_system
from app.services.grounding import Citation, format_evidence
from app.tools.registry import get_registry

logger = logging.getLogger(__name__)

AGENT_TOOLS = ("query_order", "query_logistics", "query_product", "offer_human_options")
TOOL_MARKUP_PREFIX = "<｜"
TOOL_MARKUP_MARKERS = ("<｜", "｜DSML｜", "invoke name=")


class AgentOutputError(RuntimeError):
    """模型输出为空，或含工具调用标记。"""


def _count_turn_tokens(gathered, prompt) -> int:
    usage = getattr(gathered, "usage_metadata", None) or {}
    if usage.get("total_tokens"):
        return usage["total_tokens"]
    output = AIMessage(content=gathered.content or "", tool_calls=gathered.tool_calls) if gathered else AIMessage("")
    return count_tokens([*prompt, output])


async def agent_model(state, runtime):
    trace = events.enter("agent_model", state, runtime)
    ctx = runtime.context
    citations = [Citation(**c) for c in state.get("evidence", [])]
    system = render_agent_system(ctx.today, format_evidence(citations) if citations else "")
    # 历史预算不计证据段，与 ch04 不计工具结果一致。
    history = build_history(
        state.get("messages", []), render_agent_system(ctx.today), state["resolved_input"], TOKEN_BUDGET)
    prompt = [SystemMessage(system), *history, HumanMessage(state["resolved_input"]),
              *state.get("agent_messages", [])]
    force = state.get("force_final", False)
    if force:
        runnable = ctx.model
        prompt = [*prompt, SystemMessage(TOOL_ROUND_CLOSING)]
    else:
        runnable = ctx.model.bind_tools(get_registry().tools_for_model(AGENT_TOOLS), tool_choice="auto")

    text = ""
    gathered = None
    pending: list[str] = []
    prefix_checked = False
    async for chunk in runnable.astream(prompt):
        gathered = chunk if gathered is None else gathered + chunk
        if not (isinstance(chunk.content, str) and chunk.content):
            continue
        text += chunk.content
        if prefix_checked:
            events.emit("token", {"text": chunk.content})
            continue
        # 先缓冲前两个非空白字符，以工具调用标记开头时不流出。
        pending.append(chunk.content)
        if len(text.lstrip()) < 2:
            continue
        if text.lstrip().startswith(TOOL_MARKUP_PREFIX):
            break
        prefix_checked = True
        for token in pending:
            events.emit("token", {"text": token})
        pending.clear()

    if text.lstrip().startswith(TOOL_MARKUP_PREFIX) or any(m in text for m in TOOL_MARKUP_MARKERS):
        raise AgentOutputError("模型输出含工具调用标记")
    for token in pending:
        events.emit("token", {"text": token})
    tool_calls = gathered.tool_calls if gathered is not None else []
    if force and tool_calls:
        raise AgentOutputError("强制收尾时出现工具调用")
    update = {
        "agent_messages": [*state.get("agent_messages", []), AIMessage(content=text, tool_calls=tool_calls)],
        "tokens_used": state.get("tokens_used", 0) + _count_turn_tokens(gathered, prompt),
        "trace": trace,
    }
    if not tool_calls:
        if not text:
            raise AgentOutputError("模型返回空回复")
        update["reply"] = text
    return update


async def agent_tools(state, runtime):
    trace = events.enter("agent_tools", state, runtime)
    ctx = runtime.context
    calls = state["agent_messages"][-1].tool_calls
    events.emit("tool_start", {"tools": [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in calls]})
    outcomes = await ctx.execute(calls, conversation_id=ctx.conversation_id)
    events.emit("tool_end", {"tools": [{"id": o.call_id, "name": o.name, "ok": o.ok} for o in outcomes]})
    steps = state.get("steps", 0) + 1
    update = {
        "agent_messages": [*state["agent_messages"], *(o.message for o in outcomes)],
        "steps": steps,
        "trace": trace,
    }
    actions = None
    for outcome, call in zip(outcomes, calls):
        if outcome.name == "offer_human_options" and outcome.ok:
            actions = actions_from_args(call["args"])
    if actions is not None:
        update["actions"] = actions
        events.emit("actions", {"options": actions})
    reason = None
    if steps >= AGENT_MAX_STEPS:
        reason = "steps"
    elif state.get("tokens_used", 0) >= AGENT_TOKEN_BUDGET:
        reason = "tokens"
    if reason:
        logger.info("agent_limit reason=%s conversation=%s steps=%s tokens=%s",
                    reason, ctx.conversation_id, steps, state.get("tokens_used", 0))
        update["force_final"] = True
    return update
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/test_graph_agent.py tests/test_prompts.py -q`
Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add app/graph/nodes/agent.py app/prompts.py tests/test_graph_agent.py tests/test_prompts.py
git commit -m "feat(ch05): ReAct agent nodes with step and token limits"
```

---

### Task 7: finalize 节点、图的组装和整图测试

**Files:**
- Create: `app/graph/nodes/finalize.py`、`app/graph/builder.py`
- Modify: `tests/conftest.py`（fixture `memory_graph`）
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: Task 4、5、6 的全部节点和 `routing`；`turn_messages_rows`（Task 2）；`messages.add_turn`；`GRAPH_RECURSION_LIMIT`、`CHECKPOINT_DB_PATH`。
- Produces:
  - `finalize(state, runtime) -> {"messages": [HumanMessage(user_input), *本轮消息], "trace"}`。
  - `app.graph.builder`：`build_graph(checkpointer) -> CompiledStateGraph`；`get_graph()`（未设置时抛 `RuntimeError("图未初始化")`）；`set_graph(graph | None)`；`thread_config(conversation_id: int) -> dict`；`open_graph(path: str = CHECKPOINT_DB_PATH)`（异步上下文管理器：创建目录、打开 `AsyncSqliteSaver`、编译、`set_graph`，退出时 `set_graph(None)`）。
  - `tests/conftest.py`：fixture `memory_graph`（`InMemorySaver` 编译的图，`set_graph` 后返回，结束时 `set_graph(None)`）。

- [ ] **Step 1: 写失败的测试**

`tests/conftest.py` 追加：

```python
@pytest.fixture
def memory_graph():
    from langgraph.checkpoint.memory import InMemorySaver
    from app.graph.builder import build_graph, set_graph
    graph = build_graph(InMemorySaver())
    set_graph(graph)
    yield graph
    set_graph(None)
```

```python
# tests/test_graph.py
from datetime import date

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import LowConfidenceQuestion, Message
from app.graph.builder import get_graph, set_graph, thread_config
from app.graph.nodes import agent as agent_mod
from app.graph.nodes import knowledge as knowledge_nodes
from app.graph.state import GraphContext
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, GATE_FALLBACK_REPLY
from app.repositories import conversations
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import Recorder, ScriptedChatModel, text, tools

pytestmark = pytest.mark.anyio


class Turn:
    def __init__(self, events, state):
        self.events, self.state = events, state

    @property
    def trace(self):
        return self.state.values["trace"]


async def run(graph, cid, message, *scripts):
    rec = Recorder()
    model = ScriptedChatModel(scripts=list(scripts), recorder=rec)
    ctx = GraphContext(conversation_id=cid, today=date(2026, 10, 6), model=model)
    events = [e async for e in graph.astream({"user_input": message}, thread_config(cid),
                                             context=ctx, stream_mode="custom")]
    return Turn(events, await graph.aget_state(thread_config(cid))), rec


async def new_cid(db):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.commit()
    return cid


async def saved(db, cid):
    async with db() as s:
        rows = (await s.execute(select(Message).where(Message.conversation_id == cid).order_by(Message.id))).scalars()
        return [(m.role, m.content) for m in rows]


def kb(monkeypatch, scores=(0.9,), useful=True):
    async def fake(question):
        items = [EvidenceItem(100 + i, "退换货 > 运费", "退货运费谁出", "质量问题商家承担", s)
                 for i, s in enumerate(scores)]
        return Retrieval(QueryPlan(standard_query=question), items, [e for e in items if e.score >= 0.2])

    async def check(_):
        return {"parsed": SelfCheck(useful=useful, reason="r"), "raw": None}

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))


async def test_chitchat_route_uses_no_chat_model(db, memory_graph, use_intent):
    use_intent("闲聊")
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "你好")
    assert turn.trace == ["start_turn", "resolve_reference", "classify_intent", "chitchat_reply", "finalize"]
    assert turn.events == [("token", {"text": CHITCHAT_REPLY})] and rec == []
    assert await saved(db, cid) == [("user", "你好"), ("assistant", CHITCHAT_REPLY)]
    assert [m.content for m in turn.state.values["messages"]] == ["你好", CHITCHAT_REPLY]


async def test_complaint_route_offers_actions_and_writes_no_ticket(db, memory_graph, use_intent):
    from app.db.models import Ticket
    use_intent("投诉")
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "我要投诉")
    assert turn.trace[-2:] == ["complaint_reply", "finalize"] and rec == []
    assert turn.events[0] == ("token", {"text": COMPLAINT_REPLY})
    assert [o["type"] for o in turn.events[1][1]["options"]] == ["handoff", "ticket"]
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalars().all() == []


async def test_knowledge_route_passes_gate_into_agent(db, memory_graph, use_intent, monkeypatch, caplog):
    caplog.set_level("INFO")
    use_intent("退款退货")
    kb(monkeypatch)
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "退货运费谁出", text("商家承担[1]"))
    assert turn.trace == ["start_turn", "resolve_reference", "classify_intent", "retrieve",
                          "confidence_gate", "agent_model", "finalize"]
    assert "node=retrieve" in caplog.text
    assert [e[0] for e in turn.events][0] == "citations"
    assert "## 知识库证据" in rec[0]["messages"][0].content


async def test_knowledge_route_weak_evidence_falls_back(db, memory_graph, use_intent, monkeypatch):
    use_intent("商品咨询")
    kb(monkeypatch, scores=(0.05,))
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "X9 防水吗")
    assert turn.trace[-3:] == ["confidence_gate", "fallback_reply", "finalize"] and rec == []
    assert turn.events == [("token", {"text": GATE_FALLBACK_REPLY})]
    async with db() as s:
        assert (await s.execute(select(LowConfidenceQuestion))).scalar_one().source == "retrieval_low_conf"


async def test_business_route_multi_step_react(db, memory_graph, use_intent, caplog):
    caplog.set_level("INFO")
    use_intent("订单")
    cid = await new_cid(db)
    turn, rec = await run(
        memory_graph, cid, "订单 1001 第一件商品保修多久，物流到哪了",
        tools(("c1", "query_order", {"order_id": "1001"})),
        tools(("c2", "query_product", {"product_id": "P002"}), ("c3", "query_logistics", {"order_id": "1001"})),
        text("保修 180 天，运输中"),
    )
    assert turn.trace[3:] == ["agent_model", "agent_tools", "agent_model", "agent_tools", "agent_model", "finalize"]
    assert turn.state.values["steps"] == 2
    assert [e[0] for e in turn.events].count("tool_start") == 2
    assert [r for r, _ in await saved(db, cid)] == ["user", "assistant", "tool", "assistant", "tool", "tool", "assistant"]
    assert "steps=2" in caplog.text and "route=business" in caplog.text


async def test_step_limit_forces_text_answer(db, memory_graph, use_intent, monkeypatch):
    monkeypatch.setattr(agent_mod, "AGENT_MAX_STEPS", 1)
    use_intent("物流")
    cid = await new_cid(db)
    turn, rec = await run(memory_graph, cid, "到哪了",
                          tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    assert rec[1]["tools"] == [] and turn.state.values["force_final"] is True
    assert turn.state.values["reply"] == "运输中"


async def test_second_turn_sees_first_turn_history(db, memory_graph, use_intent):
    use_intent("物流", "物流")
    cid = await new_cid(db)
    await run(memory_graph, cid, "订单 1001 到哪了",
              tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, rec = await run(memory_graph, cid, "那哪天到？", text("明天"))
    sent = rec[0]["messages"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in sent)
    assert sent[-1].content == "那哪天到？"


async def test_failed_turn_leaves_history_and_next_turn_restarts(db, memory_graph, use_intent):
    use_intent("物流", "闲聊")
    cid = await new_cid(db)
    with pytest.raises(RuntimeError):
        await run(memory_graph, cid, "到哪了",
                  tools(("c1", "query_logistics", {"order_id": "1001"})), [RuntimeError("upstream")])
    state = await memory_graph.aget_state(thread_config(cid))
    assert state.values.get("messages", []) == [] and state.next == ("agent_model",)
    assert await saved(db, cid) == []
    turn, _ = await run(memory_graph, cid, "你好")
    assert turn.trace[0] == "start_turn" and turn.state.values["steps"] == 0
    assert [m.content for m in turn.state.values["messages"]] == ["你好", CHITCHAT_REPLY]


async def test_finalize_db_failure_raises_and_keeps_history(db, memory_graph, use_intent, monkeypatch):
    from app.repositories import messages as messages_repo

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(messages_repo, "add_turn", boom)
    use_intent("闲聊")
    cid = await new_cid(db)
    with pytest.raises(RuntimeError):
        await run(memory_graph, cid, "你好")
    assert (await memory_graph.aget_state(thread_config(cid))).values.get("messages", []) == []


async def test_turn_log_line(db, memory_graph, use_intent, caplog):
    caplog.set_level("INFO")
    use_intent("闲聊")
    cid = await new_cid(db)
    await run(memory_graph, cid, "你好")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("turn "))
    assert f"conversation={cid}" in line and "intent=闲聊" in line and "route=chitchat" in line
    assert "trace=" in line and "steps=0" in line


def test_get_graph_requires_initialisation():
    set_graph(None)
    with pytest.raises(RuntimeError):
        get_graph()


async def test_open_graph_creates_sqlite_file(tmp_path):
    from app.graph.builder import open_graph
    path = tmp_path / "sub" / "cp.sqlite"
    async with open_graph(str(path)) as graph:
        assert get_graph() is graph
    assert path.exists()
    with pytest.raises(RuntimeError):
        get_graph()
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_graph.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.graph.builder'`

- [ ] **Step 3: 实现**

```python
# app/graph/nodes/finalize.py
"""日志记录节点：写结构化日志和 messages 表，再把本轮并入 State 历史。"""

import logging

from langchain_core.messages import AIMessage, HumanMessage

from app.db.engine import get_sessionmaker
from app.graph import events
from app.repositories import messages
from app.services.history import turn_messages_rows

logger = logging.getLogger("app.graph")


async def finalize(state, runtime):
    trace = events.enter("finalize", state, runtime)
    cid = runtime.context.conversation_id
    turn = state.get("agent_messages") or [AIMessage(content=state["reply"])]
    async with get_sessionmaker()() as s:
        await messages.add_turn(s, cid, turn_messages_rows(state["user_input"], turn))
        await s.commit()
    gate = state.get("gate") or {}
    logger.info(
        "turn conversation=%s intent=%s route=%s trace=%s gate=%s steps=%s tokens=%s actions=%s",
        cid, state.get("intent"), state.get("route"), ",".join(trace),
        f"{gate.get('passed')}/{gate.get('top_score')}/{gate.get('source')}" if gate else "-",
        state.get("steps", 0), state.get("tokens_used", 0),
        ",".join(a["type"] for a in state.get("actions", [])) or "-",
    )
    return {"messages": [HumanMessage(state["user_input"]), *turn], "trace": trace}
```

注意：`messages.add_turn` 必须通过模块属性调用（`messages.add_turn`），测试会 monkeypatch `app.repositories.messages.add_turn`。

```python
# app/graph/builder.py
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import CHECKPOINT_DB_PATH, GRAPH_RECURSION_LIMIT
from app.graph.nodes.agent import agent_model, agent_tools
from app.graph.nodes.finalize import finalize
from app.graph.nodes.intent import classify_intent
from app.graph.nodes.knowledge import confidence_gate, retrieve_evidence
from app.graph.nodes.replies import chitchat_reply, complaint_reply, fallback_reply
from app.graph.nodes.turn import resolve_reference, start_turn
from app.graph.routing import after_agent, after_gate, after_intent
from app.graph.state import ChatState, GraphContext

_graph = None


def build_graph(checkpointer):
    g = StateGraph(ChatState, context_schema=GraphContext)
    for name, fn in (
        ("start_turn", start_turn), ("resolve_reference", resolve_reference),
        ("classify_intent", classify_intent), ("retrieve", retrieve_evidence),
        ("confidence_gate", confidence_gate), ("agent_model", agent_model),
        ("agent_tools", agent_tools), ("fallback_reply", fallback_reply),
        ("complaint_reply", complaint_reply), ("chitchat_reply", chitchat_reply),
        ("finalize", finalize),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "resolve_reference")
    g.add_edge("resolve_reference", "classify_intent")
    g.add_conditional_edges("classify_intent", after_intent, {
        "knowledge": "retrieve", "business": "agent_model",
        "complaint": "complaint_reply", "chitchat": "chitchat_reply",
    })
    g.add_edge("retrieve", "confidence_gate")
    g.add_conditional_edges("confidence_gate", after_gate, ["agent_model", "fallback_reply"])
    g.add_conditional_edges("agent_model", after_agent, ["agent_tools", "finalize"])
    g.add_edge("agent_tools", "agent_model")
    for name in ("fallback_reply", "complaint_reply", "chitchat_reply"):
        g.add_edge(name, "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def get_graph():
    if _graph is None:
        raise RuntimeError("图未初始化")
    return _graph


def set_graph(graph) -> None:
    global _graph
    _graph = graph


def thread_config(conversation_id: int) -> dict:
    return {"configurable": {"thread_id": str(conversation_id)}, "recursion_limit": GRAPH_RECURSION_LIMIT}


@asynccontextmanager
async def open_graph(path: str = CHECKPOINT_DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(path) as checkpointer:
        graph = build_graph(checkpointer)
        set_graph(graph)
        try:
            yield graph
        finally:
            set_graph(None)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_graph.py tests/test_graph_nodes.py tests/test_graph_agent.py -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add app/graph tests/conftest.py tests/test_graph.py
git commit -m "feat(ch05): finalize node and assembled workflow graph with sqlite checkpointer"
```

---

### Task 8: `/chat/stream` 接入图，删除旧聊天服务

**Files:**
- Modify: `app/api/chat.py`、`app/main.py`、`tests/conftest.py`、`evals/run_chat_samples.py`、`evals/chat_samples.md`、`scripts/reset_db.sh`
- Delete: `app/services/chat.py`
- Rewrite: `tests/test_chat_api.py`

**Interfaces:**
- Consumes: `get_graph`、`thread_config`、`open_graph`、`GraphContext`（Task 7）；`render_agent_system`（Task 6）。
- Produces: `app.api.chat.ChatTurn(conversation_id: int, user_input: str, today: date)`；`UPSTREAM_ERROR` 移到 `app/api/chat.py`；`tests/conftest.py` 的 `client` fixture 自动使用 `memory_graph`。

- [ ] **Step 1: 重写 `tests/test_chat_api.py`**

保留原文件的 `parse_sse`、`chat`、`rows` 帮助函数和下列用例（逐个改为图的语义）。整文件内容：

```python
import asyncio
import json

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.api.chat import get_token_budget
from app.db.models import Message
from app.graph.builder import thread_config
from app.graph.nodes import knowledge as knowledge_nodes
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.llm import get_chat_model
from app.main import app
from app.prompts import CHITCHAT_REPLY
from app.repositories import conversations, messages as messages_repo
from app.repositories.messages import NewMessage
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding
from tests.fakes import text, tools

pytestmark = pytest.mark.anyio
UPSTREAM_ERROR = ("error", {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"})


def parse_sse(body):
    events = []
    for block in body.strip().split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        events.append((name, data))
    return events


async def chat(client, message, session_id=None, user_id="u1"):
    body = {"user_id": user_id, "message": message}
    if session_id is not None:
        body["session_id"] = session_id
    r = await client.post("/chat/stream", json=body)
    return r, (parse_sse(r.text) if r.status_code == 200 else None)


async def rows(db):
    async with db() as s:
        return (await s.execute(select(Message).order_by(Message.id))).scalars().all()


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_chitchat_events(client, db, use_script, use_intent):
    use_intent("闲聊")
    rec = use_script()
    r, ev = await chat(client, "你好")
    assert ev[0][0] == "session" and ev[0][1]["session_id"].isdigit()
    assert ev[1:] == [("token", {"text": CHITCHAT_REPLY}), ("done", {"finish_reason": "stop"})]
    assert rec == [] and "\\u" not in r.text


async def test_business_tool_round_events(client, db, use_script, use_intent):
    use_intent("物流")
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    assert [e for e, _ in ev] == ["session", "tool_start", "tool_end", "token", "token", "token", "done"]
    assert ev[1][1] == {"tools": [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]}
    assert rec[1]["tools"] == ["query_order", "query_logistics", "query_product", "offer_human_options"]
    assert [m.role for m in await rows(db)] == ["user", "assistant", "tool", "assistant"]


async def test_knowledge_events(client, db, use_script, use_intent, monkeypatch):
    use_intent("退款退货")

    async def fake(q):
        item = EvidenceItem(7, "退换货 > 运费", "退货运费谁出", "商家承担", 0.9)
        return Retrieval(QueryPlan(standard_query=q), [item], [item])

    async def check(_):
        return {"parsed": SelfCheck(useful=True, reason="r"), "raw": None}

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    monkeypatch.setattr(grounding, "get_self_checker", lambda: RunnableLambda(check))
    use_script(text("商家承担[1]"))
    _, ev = await chat(client, "退货运费谁出")
    assert ev[1] == ("citations", {"items": [{"n": 1, "chunk_id": 7, "section_path": "退换货 > 运费",
                                              "question": "退货运费谁出", "answer": "商家承担"}],
                                   "refused": False})
    assert ev[-1] == ("done", {"finish_reason": "stop"})


async def test_complaint_actions_event(client, db, use_script, use_intent):
    use_intent("投诉")
    use_script()
    _, ev = await chat(client, "我要投诉")
    assert [e for e, _ in ev] == ["session", "token", "actions", "done"]
    assert [o["type"] for o in ev[2][1]["options"]] == ["handoff", "ticket"]


async def test_second_turn_uses_checkpoint_history(client, db, use_script, use_intent):
    use_intent("物流", "物流")
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"), text("明天到"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    await chat(client, "那哪天到？", session_id=ev[0][1]["session_id"])
    assert any(getattr(m, "tool_call_id", None) == "c1" for m in rec[2]["messages"])


async def test_old_conversation_without_checkpoint_continues(client, db, use_script, use_intent):
    use_intent("闲聊")
    use_script()
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await messages_repo.add_turn(s, cid, [NewMessage(role="user", content="旧"),
                                              NewMessage(role="assistant", content="旧答")])
        await s.commit()
    r, ev = await chat(client, "你好", session_id=str(cid))
    assert r.status_code == 200 and ev[-1] == ("done", {"finish_reason": "stop"})


async def test_other_users_conversation_is_404(client, db, use_script, use_intent):
    use_intent("闲聊")
    use_script()
    _, ev = await chat(client, "你好", user_id="alice")
    r, _ = await chat(client, "你好", session_id=ev[0][1]["session_id"], user_id="bob")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversation_not_found"


async def test_unknown_session_is_404(client, db, use_script):
    use_script()
    r, _ = await chat(client, "你好", session_id="999999")
    assert r.status_code == 404


async def test_upstream_error_writes_nothing(client, db, use_script, use_intent):
    use_intent("物流")
    use_script([RuntimeError("boom")])
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR and await rows(db) == []


async def test_error_after_tools_writes_nothing(client, db, use_script, use_intent):
    use_intent("物流")
    use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), [RuntimeError("boom")])
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR and await rows(db) == []


async def test_empty_reply_is_error(client, db, use_script, use_intent):
    use_intent("物流")
    use_script([])
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR


async def test_tool_markup_is_error_and_not_streamed(client, db, use_script, use_intent):
    use_intent("物流")
    use_script(text("  <｜DSML｜invoke"))
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR
    assert not any(e == "token" and "<" in d["text"] for e, d in ev[:-1])
    assert await rows(db) == []


async def test_recursion_limit_is_error(client, db, use_script, use_intent, monkeypatch):
    from app.graph import builder
    monkeypatch.setattr(builder, "GRAPH_RECURSION_LIMIT", 4)
    use_intent("物流")
    use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("好"))
    _, ev = await chat(client, "到哪了")
    assert ev[-1] == UPSTREAM_ERROR


async def test_budget_exceeded(client, db, use_script):
    use_script()
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "你好")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "budget_exceeded"


async def test_budget_exceeded_on_new_session_creates_no_conversation(client, db, use_script):
    from app.db.models import Conversation
    use_script()
    app.dependency_overrides[get_token_budget] = lambda: 10
    await chat(client, "你好")
    async with db() as s:
        assert (await s.execute(select(Conversation))).scalars().all() == []


async def test_validation(client, db, use_script):
    use_script()
    assert (await client.post("/chat/stream", json={"user_id": "u1", "message": "  "})).status_code == 422


async def test_lock_held_during_stream_and_released_after(client, db, use_script, use_intent, locks):
    use_intent("物流", "闲聊")
    gate = asyncio.Event()
    use_script([gate, *text("好")])
    first = asyncio.create_task(chat(client, "到哪了"))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(lock.locked() for lock in locks._locks.values()):
            break
    cid = next(iter(locks._locks))
    r, _ = await chat(client, "你好", session_id=str(cid))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_busy"
    gate.set()
    await first
    assert not locks.get(cid).locked()
```

说明（写给实现者）：
- `use_script()` 不传参数时，聊天模型被调用会因为 `scripts` 为空而抛 `IndexError`。固定话术用例借此证明没有调用聊天模型。
- `test_lock_held_during_stream_and_released_after` 中第 1 个请求的会话 ID 只能从锁注册表拿到，因为 SSE 响应在流结束前不返回。

- [ ] **Step 2: 写 sqlite 并发测试**

追加到 `tests/test_graph.py`：

```python
async def test_sqlite_checkpointer_concurrent_conversations(db, tmp_path, use_intent):
    import asyncio
    from app.graph.builder import open_graph
    use_intent("物流", "物流")
    a, b = await new_cid(db), await new_cid(db)
    async with open_graph(str(tmp_path / "cp.sqlite")) as graph:
        await asyncio.gather(
            run(graph, a, "订单 1 到哪了", tools(("a1", "query_logistics", {"order_id": "1"})), text("A")),
            run(graph, b, "订单 2 到哪了", tools(("b1", "query_logistics", {"order_id": "2"})), text("B")),
        )
        sa = await graph.aget_state(thread_config(a))
        sb = await graph.aget_state(thread_config(b))
    assert [m.content for m in sa.values["messages"]][0] == "订单 1 到哪了"
    assert [m.content for m in sb.values["messages"]][-1] == "B"
```

- [ ] **Step 3: 修改 `tests/conftest.py` 的 `client` fixture**

```python
@pytest.fixture
async def client(memory_graph):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

- [ ] **Step 4: 运行，确认失败**

Run: `uv run pytest tests/test_chat_api.py -q`
Expected: FAIL（`/chat/stream` 仍走旧的 `stream_reply`，`use_intent` 未生效、事件不符）

- [ ] **Step 5: 实现 `app/api/chat.py`**

```python
import json
import logging
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import TOKEN_BUDGET
from app.context import BudgetExceeded, build_history
from app.db.engine import get_sessionmaker
from app.graph.builder import get_graph, thread_config
from app.graph.state import GraphContext
from app.llm import get_chat_model
from app.locks import LockRegistry, get_lock_registry
from app.prompts import render_agent_system
from app.repositories import conversations
from app.schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()
UPSTREAM_ERROR = {"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"}


@dataclass
class ChatTurn:
    conversation_id: int
    user_input: str
    today: date


def get_token_budget() -> int:
    return TOKEN_BUDGET


def get_today() -> date:
    return date.today()


async def prepare_chat_turn(
    req: ChatRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    budget: Annotated[int, Depends(get_token_budget)],
    today: Annotated[date, Depends(get_today)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterator[ChatTurn]:
    def check_budget(previous) -> None:
        try:
            build_history(previous, render_agent_system(today), req.message, budget)
        except BudgetExceeded:
            raise HTTPException(422, detail={"code": "budget_exceeded", "message": "消息过长，请缩短后重试"})

    sm = get_sessionmaker()
    # 新会话先校验预算，避免拒绝请求时留下空会话。
    if req.session_id is None:
        check_budget([])
    async with sm() as s:
        if req.session_id is not None:
            conversation = await conversations.get_for_user(s, int(req.session_id), req.user_id)
            if conversation is None:
                raise HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})
        else:
            conversation = await conversations.create(s, req.user_id)
            await s.commit()
        conversation_id = conversation.id

    lock = locks.get(conversation_id)
    if lock.locked():
        raise HTTPException(409, detail={"code": "session_busy", "message": "该会话正在处理上一条消息，请稍后重试"})
    await lock.acquire()
    # 使用 yield 依赖在响应结束或后续依赖出错时释放锁。
    try:
        if req.session_id is not None:
            # 历史从 checkpoint 读取。老会话没有 checkpoint，按空历史处理。
            state = await graph.aget_state(thread_config(conversation_id))
            check_budget(state.values.get("messages", []))
        yield ChatTurn(conversation_id=conversation_id, user_input=req.message, today=today)
    finally:
        lock.release()


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    turn: Annotated[ChatTurn, Depends(prepare_chat_turn)],
    model: Annotated[BaseChatModel, Depends(get_chat_model)],
    graph: Annotated[object, Depends(get_graph)],
) -> AsyncIterable[ServerSentEvent]:
    def sse(name: str, data: dict) -> ServerSentEvent:
        return ServerSentEvent(raw_data=json.dumps(data, ensure_ascii=False), event=name)

    yield sse("session", {"session_id": str(turn.conversation_id)})
    ctx = GraphContext(conversation_id=turn.conversation_id, today=turn.today, model=model)
    try:
        async for name, data in graph.astream(
            {"user_input": turn.user_input}, thread_config(turn.conversation_id),
            context=ctx, stream_mode="custom",
        ):
            yield sse(name, data)
    except Exception:
        logger.exception("对话图执行失败")
        yield sse("error", UPSTREAM_ERROR)
        return
    yield sse("done", {"finish_reason": "stop"})
```

注意：`thread_config` 在 `app.graph.builder` 中按模块全局读取 `GRAPH_RECURSION_LIMIT`，测试 `test_recursion_limit_is_error` 依赖这一点。

- [ ] **Step 6: 修改 `app/main.py`**

```python
from app.api import chat, extract, faith_cases, health, knowledge, web
from app.graph.builder import open_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with open_graph():
            yield
    finally:
        try:
            await close_milvus()
        finally:
            await close_rerank()
```

- [ ] **Step 7: 删除旧服务并修正引用**

Run: `git rm app/services/chat.py`，然后 `grep -rn "services.chat\|stream_reply" app evals scripts tests`
Expected: 无输出

- [ ] **Step 8: 修改 `scripts/reset_db.sh`**

在 `docker compose down -v` 一行之后插入：

```bash
# MySQL 重建后会话 ID 从 1 开始。删除 checkpoint，避免新会话读到旧会话的 State。
rm -f data/checkpoints.sqlite data/checkpoints.sqlite-wal data/checkpoints.sqlite-shm
```

- [ ] **Step 9: 修改 `evals/run_chat_samples.py` 和 `evals/chat_samples.md`**

`run_chat_samples.py`：
- 导入 `from app.graph.builder import open_graph`。
- `run_samples()` 中把 `async with httpx.AsyncClient(...)` 包在 `async with open_graph():` 内（ASGI transport 不触发 lifespan）。
- SSE 解析循环中收集 `actions` 事件：`elif event == "actions": actions = [o["type"] for o in data["options"]]`，打印 `print(f"人工选项：{actions or '无'}")`。

`chat_samples.md` 第 5 条的检查点改为：

```markdown
- 检查点：出现「转人工」选项（actions 事件含 handoff）；回复提示用户点击按钮；不声称已经转接，不声称已经建单。
```

第 2 条改为：`- 检查点：调用 query_order；回答与工具返回的订单状态一致，不编造工具没有返回的信息。`（不变）。在文件头说明后追加一句：`ch05 起，样例走 LangGraph 图（意图识别 → 分流 → Agent）。`

- [ ] **Step 10: 运行测试，确认通过**

Run: `uv run pytest -q`
Expected: 全部通过

- [ ] **Step 11: 服务冒烟（Claude 执行）**

1. 确认 `pgrep -f "uvicorn app.main:app"` 无输出。
2. Run: `uv run uvicorn app.main:app --port 8000 > /tmp/ch05-server.log 2>&1 &`
3. Run: `curl -sN -X POST http://127.0.0.1:8000/chat/stream -H 'Content-Type: application/json' -d '{"user_id":"smoke","message":"你好"}'`
Expected: `session` → `token`（闲聊固定话术）→ `done`；`data/checkpoints.sqlite` 已创建。
4. 停止服务。

- [ ] **Step 12: 提交**

```bash
git add -A app/api/chat.py app/main.py app/services tests evals/run_chat_samples.py evals/chat_samples.md scripts/reset_db.sh
git commit -m "feat(ch05): serve /chat/stream from the workflow graph and remove the ch02 chat service"
```

---

### Task 9: `POST /tickets`

**Files:**
- Create: `app/api/tickets.py`
- Modify: `app/main.py`（挂路由）、`app/schemas.py`（`TicketRequest`）、`app/prompts.py`（`TICKET_CREATED_NOTE`）
- Test: `tests/test_tickets_api.py`

**Interfaces:**
- Consumes: `execute_tool_calls`（ch02）、`get_graph`、`thread_config`、`get_lock_registry`、`conversations.get_for_user`、`messages.add_turn`。
- Produces: `POST /tickets` → 200 `{"ticket_no": str, "status": str}`；404 `conversation_not_found`；409 `session_busy`；422（校验）；502 `ticket_failed`。`app.prompts.TICKET_CREATED_NOTE = "已为您创建工单 {ticket_no}，类型：{ticket_type}，我们会尽快处理。"`。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_tickets_api.py
import asyncio

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.db.models import Message, Ticket
from app.graph.builder import thread_config
from app.repositories import conversations
from tests.fakes import text

pytestmark = pytest.mark.anyio


async def new_cid(db, user_id="u1"):
    async with db() as s:
        cid = (await conversations.create(s, user_id)).id
        await s.commit()
    return cid


def body(cid, **kw):
    return {"session_id": str(cid), "user_id": "u1", "description": "快递员态度差", "ticket_type": "投诉", **kw}


async def test_create_ticket_writes_table_message_and_state(client, db, locks, memory_graph):
    cid = await new_cid(db)
    r = await client.post("/tickets", json=body(cid))
    assert r.status_code == 200
    ticket_no = r.json()["ticket_no"]
    assert r.json()["status"] == "待处理"
    async with db() as s:
        ticket = (await s.execute(select(Ticket))).scalar_one()
        msg = (await s.execute(select(Message))).scalar_one()
    assert (ticket.ticket_no, ticket.conversation_id, ticket.ticket_type) == (ticket_no, cid, "投诉")
    note = f"已为您创建工单 {ticket_no}，类型：投诉，我们会尽快处理。"
    assert (msg.role, msg.content) == ("assistant", note)
    state = await memory_graph.aget_state(thread_config(cid))
    assert [m.content for m in state.values["messages"]] == [note]
    assert isinstance(state.values["messages"][0], AIMessage) and state.next == ()


async def test_other_user_is_404(client, db, locks):
    cid = await new_cid(db, user_id="alice")
    r = await client.post("/tickets", json=body(cid))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversation_not_found"


@pytest.mark.parametrize("patch", [{"ticket_type": "退款"}, {"description": ""}, {"description": "x" * 501},
                                   {"session_id": "abc"}])
async def test_validation(client, db, locks, patch):
    cid = await new_cid(db)
    assert (await client.post("/tickets", json=body(cid, **patch))).status_code == 422


async def test_ticket_while_streaming_is_409(client, db, locks):
    cid = await new_cid(db)
    await locks.get(cid).acquire()
    try:
        r = await client.post("/tickets", json=body(cid))
    finally:
        locks.get(cid).release()
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_busy"
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalars().all() == []


async def test_tool_failure_is_502(client, db, locks, monkeypatch):
    from app.api import tickets as tickets_api
    from app.tools.executor import ToolOutcome
    from langchain_core.messages import ToolMessage

    async def fail(calls, *, conversation_id):
        return [ToolOutcome(calls[0]["id"], "create_ticket", False,
                            ToolMessage(content="{}", tool_call_id=calls[0]["id"]))]

    monkeypatch.setattr(tickets_api, "execute_tool_calls", fail)
    r = await client.post("/tickets", json=body(await new_cid(db)))
    assert r.status_code == 502 and r.json()["detail"]["code"] == "ticket_failed"


async def test_state_update_failure_still_returns_ticket(client, db, locks, memory_graph, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("checkpoint down")

    monkeypatch.setattr(memory_graph, "aupdate_state", boom)
    r = await client.post("/tickets", json=body(await new_cid(db)))
    assert r.status_code == 200
    async with db() as s:
        assert len((await s.execute(select(Ticket))).scalars().all()) == 1


async def test_next_turn_sees_ticket_note(client, db, locks, memory_graph, use_script, use_intent):
    # 真实流程：先有一轮投诉，再点建工单。build_history 要求历史从用户消息开始。
    use_intent("投诉", "售后")
    rec = use_script(text("您的工单已创建"))
    r = await client.post("/chat/stream", json={"user_id": "u1", "message": "我要投诉"})
    cid = int(r.text.split('"session_id": "')[1].split('"')[0])
    ticket_no = (await client.post("/tickets", json=body(cid))).json()["ticket_no"]
    await client.post("/chat/stream", json={"session_id": str(cid), "user_id": "u1", "message": "工单号多少"})
    assert any(ticket_no in str(m.content) for m in rec[0]["messages"])
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_tickets_api.py -q`
Expected: FAIL，404 Not Found（路由不存在）

- [ ] **Step 3: 实现**

`app/schemas.py` 追加：

```python
class TicketRequest(BaseModel):
    session_id: SessionId
    user_id: UserId
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    ticket_type: Literal["售后", "投诉", "咨询"]
```

`app/prompts.py` 追加：

```python
TICKET_CREATED_NOTE = "已为您创建工单 {ticket_no}，类型：{ticket_type}，我们会尽快处理。"
```

```python
# app/api/tickets.py
"""用户点击「建工单」后调用。后端不自动建单。"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage

from app.db.engine import get_sessionmaker
from app.graph.builder import get_graph, thread_config
from app.locks import LockRegistry, get_lock_registry
from app.prompts import TICKET_CREATED_NOTE
from app.repositories import conversations, messages
from app.repositories.messages import NewMessage
from app.schemas import TicketRequest
from app.tools.executor import execute_tool_calls

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/tickets")
async def create_ticket(
    req: TicketRequest,
    locks: Annotated[LockRegistry, Depends(get_lock_registry)],
    graph: Annotated[object, Depends(get_graph)],
) -> dict:
    cid = int(req.session_id)
    async with get_sessionmaker()() as s:
        if await conversations.get_for_user(s, cid, req.user_id) is None:
            raise HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})
    lock = locks.get(cid)
    if lock.locked():
        raise HTTPException(409, detail={"code": "session_busy", "message": "该会话正在处理上一条消息，请稍后重试"})
    async with lock:
        call = {"id": f"ticket-{uuid.uuid4().hex[:8]}", "name": "create_ticket",
                "args": {"description": req.description, "ticket_type": req.ticket_type}}
        outcome = (await execute_tool_calls([call], conversation_id=cid))[0]
        if not outcome.ok:
            raise HTTPException(502, detail={"code": "ticket_failed", "message": "工单创建失败，请稍后重试"})
        ticket = outcome.data
        note = TICKET_CREATED_NOTE.format(ticket_no=ticket["ticket_no"], ticket_type=req.ticket_type)
        # 工单已提交，不回滚。下面两步失败只记日志。
        try:
            async with get_sessionmaker()() as s:
                await messages.add_turn(s, cid, [NewMessage(role="assistant", content=note)])
                await s.commit()
        except Exception:
            logger.exception("工单提示写入 messages 表失败")
        try:
            await graph.aupdate_state(thread_config(cid), {"messages": [AIMessage(content=note)]}, as_node="finalize")
        except Exception:
            logger.exception("工单提示写入 State 失败")
    return {"ticket_no": ticket["ticket_no"], "status": ticket["status"]}
```

`app/main.py`：导入 `tickets` 并 `app.include_router(tickets.router)`。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_tickets_api.py -q && uv run pytest -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add app/api/tickets.py app/main.py app/schemas.py app/prompts.py tests/test_tickets_api.py
git commit -m "feat(ch05): POST /tickets creates a ticket only when the user clicks"
```

---

### Task 10: 聊天页按钮（Vibe Coding）

按 CLAUDE.md 的例外条款：不套 TDD 和 code review；代码仍由 Codex 写；Claude 用浏览器检查效果。

**Files:**
- Modify: `app/web/index.html`

- [ ] **Step 1: 交给 Codex 的任务描述（逐字）**

```
修改 app/web/index.html（原生 HTML + CSS + JavaScript，不引入任何库），只做下面这些改动：

1. SSE 新事件 actions：数据为 {"options": [{"type": "handoff"}, {"type": "ticket", "description": "...", "ticket_type": "投诉"}]}。
   收到后，在当前这条助手回复气泡下方渲染按钮：type=handoff 渲染「转人工」，type=ticket 渲染「建工单」。
   两个按钮相互独立，可以只有一个。同一条回复多次收到 actions 时，以最后一次为准重新渲染。
2. 点「转人工」：弹出确认框（用原生 <dialog>，风格与现有引用卡片一致），文字"确认转接人工客服吗？"。
   确认后：在对话中追加一行居中的系统提示"已转接人工客服"；然后追加一条助手气泡，发送者显示为"客服小猫"，
   内容"您好，我是客服小猫，请问有什么可以帮您的"。不调用任何后端接口。按钮置灰。
3. 点「建工单」：弹出确认框，显示工单类型（只读）和描述（<textarea>，预填 description，最多 500 字）。
   确认后 POST /tickets，JSON 体 {"session_id": 当前会话 ID, "user_id": 当前用户 ID, "description": 描述, "ticket_type": 类型}。
   成功（200）：在对话中追加系统提示"工单已创建：<ticket_no>"，按钮置灰。
   失败：在确认框内显示后端返回的 detail.message（没有时显示"工单创建失败，请稍后重试"），按钮保持可点。
   409 时显示"当前正在回复中，请稍后再试"。
4. 两个按钮互不影响：点了一个，另一个仍可点。用户不点、继续发消息时，对话照常进行，按钮保留。
5. 工具名映射 toolNames 中加入 ['offer_human_options', '推荐人工选项']，保留其他项。
6. 不改动现有的 token、tool_start、tool_end、citations、done、error 处理和引用卡片、👍/👎 逻辑。
验收：uv run pytest -q 全部通过（本任务不新增测试）。
```

- [ ] **Step 2: 浏览器检查（Claude 执行，用户确认）**

1. 启动服务（先确认旧进程已退出）。
2. 打开 `http://127.0.0.1:8000/`，发"我要投诉"。检查：出现两个独立按钮。
3. 点「转人工」并确认。检查：出现"已转接人工客服"和客服小猫的问候。
4. 点「建工单」，改描述后确认。检查：出现工单号；`docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT ticket_no, ticket_type FROM tickets ORDER BY created_at DESC LIMIT 1"` 返回该工单。
5. 新开会话发"我要投诉"，不点按钮，接着发"订单 1001 到哪了"。检查：正常回答，`tickets` 表无新增。
6. 请用户在浏览器中确认效果。

- [ ] **Step 3: 提交**

```bash
git add app/web/index.html
git commit -m "feat(ch05): handoff and ticket buttons with confirmation dialogs in chat page"
```

---

### Task 11: 验收脚本、文档和完整验收

**Files:**
- Create: `scripts/demo5.sh`
- Modify: `CLAUDE.md`（Claude 改）

- [ ] **Step 1: 写 `scripts/demo5.sh`（交给 Codex，内容逐字）**

```bash
#!/usr/bin/env bash
# ch05 验收。前置：MySQL、Milvus 已启动，已 build_kb，服务已启动且日志写到文件。
# 用法：bash scripts/demo5.sh <服务日志路径>
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
BASE="${BASE_URL:-http://127.0.0.1:8000}"
LOG="${1:?用法：bash scripts/demo5.sh <服务日志路径>}"
USER_ID="demo5-$(date +%s)-$RANDOM"

if ! curl --fail --silent --connect-timeout 5 --max-time 10 "$BASE/health" >/dev/null 2>&1; then
    echo '请先启动服务：uv run uvicorn app.main:app --port 8000 > <日志> 2>&1' >&2
    exit 1
fi

mysql_q() {
    docker exec aftersales-mysql mysql -N --default-character-set=utf8mb4 -uaftersales -paftersales aftersales -e "$1"
}

# ask <消息> [会话ID]：打印 SSE 原文
ask() {
    local body
    body=$(uv run python -c 'import json,sys; d={"user_id":sys.argv[1],"message":sys.argv[2]}; sys.argv[3:] and d.update(session_id=sys.argv[3]); print(json.dumps(d, ensure_ascii=False))' "$USER_ID" "$@")
    curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" -H 'Content-Type: application/json' -d "$body"
}

# sse_get <SSE 原文> <python 表达式>：events 为 [(name, data)]
sse_get() {
    printf '%s\n' "$1" | uv run python -c '
import json, sys
events, name = [], None
for line in sys.stdin.read().splitlines():
    if line.startswith("event: "): name = line[7:]
    elif line.startswith("data: "): events.append((name, json.loads(line[6:])))
print(eval(sys.argv[1]))' "$2"
}

fail() { echo "❌ $1"; exit 1; }

echo '=== 验收 1：政策问题走强制检索 ==='
sse=$(ask '退货运费谁出？')
cid=$(sse_get "$sse" 'events[0][1]["session_id"]')
grep -q "node=retrieve conversation=$cid" "$LOG" || fail "日志中没有 node=retrieve conversation=$cid"
echo "日志：$(grep "node=retrieve conversation=$cid" "$LOG" | tail -1)"
echo "回复：$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')"
echo '✅ 验收 1 通过'

echo '=== 验收 2：Agent 自己调工具查物流 ==='
sse=$(ask '订单 1001 的物流到哪了')
tools=$(sse_get "$sse" '[t["name"] for n, d in events if n == "tool_start" for t in d["tools"]]')
echo "调用的工具：$tools"
[[ "$tools" == *query_logistics* ]] || fail '没有调用 query_logistics'
echo "回复：$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')"
echo '✅ 验收 2 通过'

echo '=== 验收 3：投诉给出两个独立选项，点了才建单 ==='
sse=$(ask '我要投诉，你们快递员态度太差了')
cid=$(sse_get "$sse" 'events[0][1]["session_id"]')
options=$(sse_get "$sse" '[o["type"] for n, d in events if n == "actions" for o in d["options"]]')
echo "选项：$options"
[[ "$options" == "['handoff', 'ticket']" ]] || fail '投诉没有给出 handoff 和 ticket 两个选项'
before=$(mysql_q "SELECT COUNT(*) FROM tickets WHERE conversation_id = $cid")
ask '那算了，先帮我查下订单 1001' "$cid" >/dev/null
after_chat=$(mysql_q "SELECT COUNT(*) FROM tickets WHERE conversation_id = $cid")
[[ "$before" == "$after_chat" ]] || fail '用户没点按钮，tickets 表却有新增'
echo "不点按钮继续对话：tickets 行数 $before → $after_chat"
resp=$(curl --fail --silent --show-error -X POST "$BASE/tickets" -H 'Content-Type: application/json' \
    -d "{\"session_id\":\"$cid\",\"user_id\":\"$USER_ID\",\"description\":\"快递员态度太差\",\"ticket_type\":\"投诉\"}")
echo "点「建工单」：$resp"
after_click=$(mysql_q "SELECT COUNT(*) FROM tickets WHERE conversation_id = $cid")
[[ "$after_click" == "$((before + 1))" ]] || fail '点击建工单后 tickets 表没有新增 1 行'
echo '「转人工」只在前端模拟，请在浏览器中确认。'
echo '✅ 验收 3 通过（后端部分）'

echo '=== 验收 4：闲聊回固定话术 ==='
sse=$(ask '你好呀')
reply=$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')
expected=$(uv run python -c 'from app.prompts import CHITCHAT_REPLY; print(CHITCHAT_REPLY)')
echo "回复：$reply"
[[ "$reply" == "$expected" ]] || fail '闲聊回复不是固定话术'
echo '✅ 验收 4 通过'

echo '=== 验收 5：复杂问题 ReAct 走多步 ==='
sse=$(ask '帮我看下订单 1001 里第一件商品的保修多久，还有这单物流到哪了')
cid=$(sse_get "$sse" 'events[0][1]["session_id"]')
line=$(grep "turn conversation=$cid " "$LOG" | tail -1)
echo "日志：$line"
steps=$(printf '%s' "$line" | sed -E 's/.* steps=([0-9]+).*/\1/')
[[ "$steps" -ge 2 ]] || fail "ReAct 只走了 $steps 步"
echo "回复：$(sse_get "$sse" '"".join(d["text"] for n, d in events if n == "token")')"
echo '✅ 验收 5 通过'

echo '全部验收通过。'
```

- [ ] **Step 2: 运行验收（Claude 执行）**

1. 确认 `pgrep -f "uvicorn app.main:app"` 无输出。
2. Run: `uv run uvicorn app.main:app --port 8000 > /tmp/ch05-server.log 2>&1 &`
3. Run: `bash scripts/demo5.sh /tmp/ch05-server.log`
Expected: 5 项全部通过。如果验收 5 只走了 1 步（模型一次并行调用了全部工具），把日志交给用户决定是否改问题或 Prompt；不自行改验收标准。
4. Run: `uv run python scripts/bare_agent.py "订单 1001 到哪了"`、`uv run python evals/run_intent_eval.py`、`uv run python evals/run_chat_samples.py`，把输出摘要记入 dev-notes。
5. Run: `uv run pytest -q`，记录通过数。

- [ ] **Step 3: 更新 `CLAUDE.md`（Claude 改）**

- 项目状态加一条 ch05：LangGraph Workflow（指代消解透传 → 意图识别 7 类 → 写死分流 4 出口 → 强制检索 + 置信度闸 → ReAct Agent → finalize 日志记录）；State + AsyncSqliteSaver；转人工、建工单由用户在前端自选。
- 常用命令加：`bash scripts/demo5.sh <服务日志>`、`uv run python scripts/bare_agent.py "<问题>"`、`uv run python evals/run_intent_eval.py`；启动服务命令注明日志重定向。
- 架构图把 `api → services` 改为 `api.chat → graph → (tools.executor, knowledge.retrieval, services.grounding, llm, repositories.messages)`，加 `api.tickets`；模块表删除 `app/services/chat.py`，加 `app/agent/`、`app/graph/`、`app/api/tickets.py`。
- 设计约束加：
  - State `messages` 只由 `finalize` 和 `POST /tickets` 追加；本轮字段由 `start_turn` 重置；一轮失败历史不变，下一轮从 START 重新开始。
  - 节点用 `events.emit` 发 SSE 事件，API 用 `stream_mode="custom"`；每轮依赖走 `context=GraphContext`，不进 State。
  - 知识证据渲染进 Agent System Prompt，不伪造 `query_faq` 调用（DeepSeek 思考模式下当前轮自造 tool_call id 返回 400）。
  - Agent 只绑定 `AGENT_TOOLS`；`create_ticket` 只由 `POST /tickets` 调用；`offer_human_options` 只写 `actions`。
  - `reset_db.sh` 必须同时删除 `data/checkpoints.sqlite`。
  - ch04 评估脚本用 `CH04_CHAT_TOOLS` 和 `chat_prompt` 作基线，不跟随 Agent 变化。
  - 测试用 `memory_graph`（InMemorySaver）和 `use_intent`。

- [ ] **Step 4: 提交**

```bash
git add scripts/demo5.sh CLAUDE.md
git commit -m "docs(ch05): acceptance demo and project guide updates"
```

---

## 收尾（计划之外的流程步骤）

1. 全分支 code review（superpowers:requesting-code-review），结论记入 dev-notes。
2. finish（superpowers:finishing-a-development-branch）：推送 `ch05`，提 PR。
3. 交付：功能演示命令、测试结果、`dev-notes/ch05.md`。
