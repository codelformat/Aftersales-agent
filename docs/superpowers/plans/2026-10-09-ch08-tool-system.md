# ch08 即插即用的工具系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目的执行方式（CLAUDE.md）：** Claude 把每个任务写成自包含的描述交给 Codex（`gpt-6.1-sol`，`high`）编码；Claude 检查 diff、跑测试、提交并推送。Claude 不直接改业务代码。任务 11（前端）用 Vibe Coding。

**Goal:** 内置工具和 MCP 工具登记进同一份工具表，所有调用走同一个执行引擎（权限、JSON Schema 校验、超时重试、分诊、格式化、审计），`create_ticket` 经 interrupt 预览卡片确认后才执行。

**Architecture:** `app/tools/` 拆为注册表（`registry`）、本轮工具集（`toolset`）、策略文件（`policy`）、MCP 发现（`mcp`）、参数校验（`validation`）、格式化（`formatters`）、审计（`audit`）和执行引擎（`executor`）。内置工具放 `app/tools/builtin/`，启动时扫描登记。两个 MCP Server 在顶层包 `mcp_servers/`，独立进程。图中新增 `confirm_write`、`ticket_reply` 两个节点。

**Tech Stack:** `mcp` 1.x（`FastMCP`，`langchain-mcp-adapters` 0.3.2 要求 `mcp<2`）、`langchain-mcp-adapters` 0.3.2、`jsonschema` 4.26、LangGraph `interrupt`、SQLAlchemy 异步、FastAPI SSE。

**Spec:** `docs/superpowers/specs/2026-10-09-ch08-tool-system-design.md`

## Global Constraints

- `db/schema_ch08.sql` 是用户 DDL，从 `ch08.sql` 逐字复制，不改一个字节；ORM 只映射，不 `create_all`。
- 执行 `.sql` 文件用 `exec_driver_sql`；容器初始化 SQL 必须用 utf8mb4 读取；校验中文存储用 `HEX()`。
- 审计 `status` 只取 5 个值：`成功`、`失败`、`超时`、`校验拦下`、`权限拒绝`。查询落空记 `成功`，`error_message="查询落空"`。
- 所有重试用 `app/retry.py` 的 `retry_async`（指数回退 + 抖动）。只读工具默认 `max_retries=2`；写工具默认 0。
- 工具结果 JSON 一律 `json.dumps(..., ensure_ascii=False)`，再按 `TOOL_RESULT_MAX_TOKENS × CHARS_PER_TOKEN` 截断（沿用 `tool_result_max_chars()`）。
- MCP 工具权限只看 `config/tools.json`：`read` 放行；`write`、`deny`、未列出一律拒绝；不读 Server annotations。
- `approvals` 只来自 `confirm_write` 节点和 `POST /tickets`，不是工具参数。
- `confirm_write` 在 `interrupt()` 之前不发事件、不写库、不调上游。
- 写审计失败只打 `audit_write_failed`，不影响工具结果。
- 测试不访问真实上游、生产集合和开发库；MCP 发现默认被 autouse fixture 拦截；审计默认写进内存列表。
- `mcp_servers/` 不导入 `app`，不连数据库。
- `.env` 不加新变量。新常量写在 `app/config.py`。
- 演示配置预算仍为 5650/3954/1695（`tests/test_budget.py` 不改）。
- 中文注释、日志、文档按 ASD-STE100 原则：短句、主动语态、一词一义。

## 计划阶段对 spec 的补充（已同步改 spec）

1. 确认结果用 `approvals: dict[str, str]`（call id → `"approved"` 或拒绝原因），代替 spec 中的 `approved_ids`。这样一个字段同时表达"确认""用户取消""一次只能提交一张工单"三种情况。
2. 参数校验对**发给模型的 Schema**（不含注入字段）做，校验通过后再注入 `conversation_id`，注入值覆盖模型给的同名参数。
3. `offer_refund_form` 的本轮 Schema 把 `order_id` 收窄为 `{"enum": [本轮订单号]}`。订单号不符由 JSON Schema 校验拦下（`校验拦下`），删除 `agent_tools` 中的 `invalid_order` 分支。
4. 测试默认把审计写进内存列表（fixture `audit_log`）。原因：不用 `db` fixture 时 `get_sessionmaker()` 会连开发库。
5. MCP runner 用 ToolCall 形式调用（`tool.ainvoke({"type": "tool_call", ...})`），从返回的 `ToolMessage.artifact["structured_content"]` 取结构化结果。

## Review Focus

1. 测试没用 `db` fixture 时，执行引擎写审计不能连到开发库 → 审计默认写内存列表；任务 1 的测试断言执行一次工具后 `app.db.engine._sessionmaker` 仍为 None。
2. 预览卡片待确认期间 MCP Server 停了，用户点确认 → 恢复时重新发现失败，但 `create_ticket` 是内置工具，照常建单 → 任务 8 的测试让发现抛异常后恢复，`tickets` 有一条。
3. 模型在 `create_ticket` 参数中自带 `conversation_id`（指向别的会话）→ 执行引擎注入值覆盖 → 任务 3 的测试断言工具收到的是本轮会话 ID。
4. 用户连点两次「确认提交」，或发了新消息后再点旧卡片 → 第二次 `/chat/resume` 返回 409 `no_pending_confirmation`，只建一张工单 → 任务 9 的测试。
5. MCP 工具只返回文本、没有结构化内容，或文本不是 JSON → 按文本原样处理，不报错 → 任务 6 的测试。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `db/schema_ch08.sql` | 用户 DDL（`git mv ch08.sql`） |
| `app/db/models.py` | 加 `ToolAuditLog` |
| `app/repositories/audit.py` | `add_audit(session, AuditRecord)` |
| `app/tools/audit.py` | `AuditRecord`、`record()`（超时 + 吞异常）、`set_audit_writer()` |
| `app/tools/registry.py`（重写） | `ToolEntry`、`register()`、`builtin_registry()`、`ch04_tools()`、`ch04_toolset()` |
| `app/tools/builtin/*.py` | `order.py`、`product.py`、`ticket.py`、`faq.py`、`control.py`（登记 `offer_human_options`、`offer_refund_form`） |
| `app/tools/legacy/logistics.py` | 原 `query_logistics`，只给 ch04 基线（任务 6 移入） |
| `app/tools/toolset.py` | `Toolset`、`builtin_toolset()`、`narrow_for_turn()` |
| `app/tools/validation.py` | `validate_args(schema, args) -> list[str]`（jsonschema → 中文） |
| `app/tools/formatters.py` | MCP 工具的 formatter 表、默认格式化 |
| `app/tools/policy.py` | `ToolPolicy`、`load_policy()`（mtime 缓存） |
| `app/tools/mcp.py` | `discover()`、`build_base_toolset()`、MCP runner |
| `app/tools/executor.py`（重写） | `execute_tool_calls()`：查找 → 权限 → 校验 → 执行 → 分诊 → 格式化 → 审计 |
| `config/tools.json` | 策略文件 |
| `mcp_servers/{common,logistics,aftersales}` | 两个 MCP Server |
| `app/graph/nodes/confirm.py` | `confirm_write`、`ticket_reply` |
| `scripts/demo8.sh`、`scripts/demo8_assets/` | 验收脚本和演示用的新工具文件 |
| `evals/run_ticket_eval.py`、`evals/ticket_samples.jsonl` | 工单 Prompt 评估 |

---

### Task 1: 审计表、ORM、仓储和审计写入器

**Files:**
- Move: `ch08.sql` → `db/schema_ch08.sql`（`git mv` 前先 `git add ch08.sql`；内容不改）
- Modify: `docker-compose.yml`（加挂载）、`scripts/reset_db.sh`、`tests/conftest.py`（`_reset_schema`、`_clear_runtime_tables`、新 fixture）
- Modify: `app/db/models.py`
- Create: `app/repositories/audit.py`、`app/tools/audit.py`
- Modify: `app/config.py`（加常量）
- Test: `tests/test_audit.py`

**Interfaces:**
- Produces:
  - `app.config`: `AUDIT_WRITE_TIMEOUT_SECONDS = 1`、`AUDIT_SUMMARY_MAX_CHARS = 500`、`AUDIT_ERROR_MAX_CHARS = 512`
  - `app.tools.audit.AuditRecord`（frozen dataclass）：`conversation_id: int | None`、`tool_call_id: str | None`、`tool_name: str`、`tool_source: Literal["builtin", "mcp"]`、`mcp_server: str | None`、`arguments: dict | None`、`result_summary: str | None`、`status: Literal["成功","失败","超时","校验拦下","权限拒绝"]`、`error_message: str | None`、`retry_count: int`、`duration_ms: int | None`
  - `async def record(rec: AuditRecord) -> None`：截断 `result_summary`（500）和 `error_message`（512）后调当前写入器；`asyncio.wait_for(..., AUDIT_WRITE_TIMEOUT_SECONDS)`；任何异常只打 `logger.warning("audit_write_failed tool=%s call=%s error=%s", ...)`。
  - `def set_audit_writer(writer: Callable[[AuditRecord], Awaitable[None]] | None) -> None`；`None` 恢复为数据库写入器 `write_db`。
  - `app.repositories.audit.add_audit(session, rec) -> ToolAuditLog`
  - conftest：autouse fixture `audit_log`（写入器替换为追加到列表，返回该列表）；fixture `db_audit`（依赖 `db`，把写入器恢复为 `write_db`）。

- [ ] **Step 1: 移动 DDL 并挂载**

```bash
git add ch08.sql && git mv ch08.sql db/schema_ch08.sql
```

`docker-compose.yml` 的 mysql volumes 在 `05-schema-ch07.sql` 一行后加：

```yaml
      - ./db/schema_ch08.sql:/docker-entrypoint-initdb.d/06-schema-ch08.sql:ro
```

`scripts/reset_db.sh` 的表检查循环加 `tool_audit_logs`，并在循环后加枚举中文校验：

```bash
enum_hex=$(docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='aftersales' AND TABLE_NAME='tool_audit_logs' AND COLUMN_NAME='status'")
# "权限拒绝" 的 utf8mb4 字节。双重编码时这一串不会出现。
[[ "$enum_hex" == *E69D83E99990E68B92E7BB9D* ]] || { echo "tool_audit_logs 中文枚举编码错误"; exit 1; }
```

`tests/conftest.py`：`_reset_schema` 的 DROP 列表首位加 `"tool_audit_logs"`，执行文件列表末尾加 `"schema_ch08.sql"`；`_clear_runtime_tables` 的 DELETE 列表首位加 `"tool_audit_logs"`。

- [ ] **Step 2: 写失败的测试**

```python
# tests/test_audit.py
import asyncio

import pytest
from sqlalchemy import func, select

from app.db import engine as engine_mod
from app.db.models import ToolAuditLog
from app.tools import audit

pytestmark = pytest.mark.anyio


def rec(**kw):
    base = dict(conversation_id=None, tool_call_id="c1", tool_name="query_order", tool_source="builtin",
                mcp_server=None, arguments={"order_id": "1001"}, result_summary="ok", status="成功",
                error_message=None, retry_count=0, duration_ms=12)
    return audit.AuditRecord(**{**base, **kw})


async def test_default_fixture_captures_in_memory(audit_log):
    await audit.record(rec())
    assert audit_log == [rec()]
    assert engine_mod._sessionmaker is None  # 没有连开发库


async def test_record_truncates(audit_log):
    await audit.record(rec(result_summary="字" * 600, error_message="错" * 600))
    assert len(audit_log[0].result_summary) == 500 and len(audit_log[0].error_message) == 512


async def test_writer_failure_is_swallowed(caplog):
    async def boom(_):
        raise RuntimeError("db down")
    audit.set_audit_writer(boom)
    await audit.record(rec())
    assert "audit_write_failed" in caplog.text


async def test_writer_timeout_is_swallowed(caplog, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_WRITE_TIMEOUT_SECONDS", 0.01)

    async def slow(_):
        await asyncio.sleep(1)
    audit.set_audit_writer(slow)
    await audit.record(rec())
    assert "audit_write_failed" in caplog.text


async def test_db_writer_persists_chinese_status(db, db_audit):
    await audit.record(rec(status="权限拒绝", error_message="用户取消", conversation_id=None))
    async with db() as s:
        row = (await s.execute(select(ToolAuditLog))).scalar_one()
    assert (row.status, row.error_message, row.arguments, row.retry_count) == ("权限拒绝", "用户取消", {"order_id": "1001"}, 0)
    async with db() as s:
        hexed = (await s.execute(select(func.hex(ToolAuditLog.status)))).scalar_one()
    assert hexed == "E69D83E99990E68B92E7BB9D"  # 按字节校验，避免双重编码被客户端还原后漏检
```

- [ ] **Step 3: 运行，确认失败**

Run: `uv run pytest tests/test_audit.py -q`
Expected: FAIL（`ImportError: cannot import name 'audit'`）

- [ ] **Step 4: 实现**

`app/db/models.py`（`Enum` 值与 DDL 一致；`arguments` 用 `JSON`）：

```python
class ToolAuditLog(Base):
    __tablename__ = "tool_audit_logs"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int | None] = mapped_column(ID, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_source: Mapped[str] = mapped_column(Enum("builtin", "mcp"))
    mcp_server: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arguments: Mapped[Any] = mapped_column(JSON, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Enum("成功", "失败", "超时", "校验拦下", "权限拒绝"))
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
```

`app/repositories/audit.py`：

```python
from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ToolAuditLog


async def add_audit(session: AsyncSession, rec) -> ToolAuditLog:
    row = ToolAuditLog(**asdict(rec))
    session.add(row)
    await session.flush()
    return row
```

`app/tools/audit.py`：

```python
"""工具调用审计。写入失败只记日志，不影响工具执行。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from app.config import AUDIT_ERROR_MAX_CHARS, AUDIT_SUMMARY_MAX_CHARS, AUDIT_WRITE_TIMEOUT_SECONDS
from app.db.engine import get_sessionmaker
from app.repositories.audit import add_audit

logger = logging.getLogger(__name__)
AuditStatus = Literal["成功", "失败", "超时", "校验拦下", "权限拒绝"]


@dataclass(frozen=True)
class AuditRecord:
    conversation_id: int | None
    tool_call_id: str | None
    tool_name: str
    tool_source: Literal["builtin", "mcp"]
    mcp_server: str | None
    arguments: dict | None
    result_summary: str | None
    status: AuditStatus
    error_message: str | None
    retry_count: int
    duration_ms: int | None


async def write_db(rec: AuditRecord) -> None:
    async with get_sessionmaker()() as s:
        await add_audit(s, rec)
        await s.commit()


_writer: Callable[[AuditRecord], Awaitable[None]] = write_db


def set_audit_writer(writer: Callable[[AuditRecord], Awaitable[None]] | None) -> None:
    global _writer
    _writer = writer or write_db


def _cut(value: str | None, limit: int) -> str | None:
    return value if value is None or len(value) <= limit else value[:limit]


async def record(rec: AuditRecord) -> None:
    rec = replace(rec, result_summary=_cut(rec.result_summary, AUDIT_SUMMARY_MAX_CHARS),
                  error_message=_cut(rec.error_message, AUDIT_ERROR_MAX_CHARS))
    try:
        await asyncio.wait_for(_writer(rec), AUDIT_WRITE_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("audit_write_failed tool=%s call=%s error=%s", rec.tool_name, rec.tool_call_id,
                       type(exc).__name__)
```

注意：`record` 中的 `AUDIT_WRITE_TIMEOUT_SECONDS` 必须按模块属性读取（测试用 monkeypatch 改它）。写法：`import app.tools.audit` 内部用全局名即可，monkeypatch `audit.AUDIT_WRITE_TIMEOUT_SECONDS` 会生效。

`tests/conftest.py` 末尾：

```python
@pytest.fixture(autouse=True)
def audit_log():
    """默认把审计写进内存列表，不连数据库。"""
    from app.tools import audit
    rows = []

    async def capture(rec):
        rows.append(rec)

    audit.set_audit_writer(capture)
    yield rows
    audit.set_audit_writer(None)


@pytest.fixture
def db_audit(db, audit_log):
    """审计写进测试库。"""
    from app.tools import audit
    audit.set_audit_writer(None)
    yield
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/test_audit.py -q && uv run pytest -q`
Expected: 新测试全部通过；全量通过（数量 = 680 + 新增）。

- [ ] **Step 6: 开发库建表（Claude 执行，不经 Codex）**

```bash
docker exec -i aftersales-mysql mysql --default-character-set=utf8mb4 -uaftersales -paftersales aftersales < db/schema_ch08.sql
docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='aftersales' AND TABLE_NAME='tool_audit_logs' AND COLUMN_NAME='status'" | grep -q E69D83E99990E68B92E7BB9D && echo OK
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add db/schema_ch08.sql docker-compose.yml scripts/reset_db.sh tests/conftest.py tests/test_audit.py app/db/models.py app/repositories/audit.py app/tools/audit.py app/config.py
git commit -m "feat(ch08): tool_audit_logs table, ORM and audit writer"
```

---

### Task 2: 注册表、内置工具包、本轮工具集

**Files:**
- Rewrite: `app/tools/registry.py`
- Create: `app/tools/builtin/__init__.py`、`order.py`、`product.py`、`logistics.py`、`ticket.py`、`faq.py`、`control.py`
- Delete: `app/tools/order.py`、`product.py`、`logistics.py`、`ticket.py`、`faq.py`（内容移入 `builtin/`，工具代码不变）
- Create: `app/tools/toolset.py`
- Modify: `app/tools/executor.py`（改用 `Toolset` 查找；其余行为本任务不变）、`app/graph/state.py`、`app/graph/nodes/agent.py`、`app/graph/nodes/aftersales.py`、`app/api/tickets.py`、`evals/run_rag_eval.py`、`evals/run_tool_selection_eval.py`、`tests/fakes.py`
- Test: `tests/test_registry.py`；更新 `tests/test_tools.py`、`tests/test_executor.py`、`tests/test_graph_agent.py`、`tests/test_chat_api.py`

**Interfaces:**
- Consumes: 无
- Produces（后续任务都依赖）：

```python
# app/tools/registry.py
Permission = Literal["read", "write"]

@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    parameters: dict                       # 发给模型的 JSON Schema，不含注入字段
    source: Literal["builtin", "mcp"]
    permission: Permission
    runner: Callable[[dict, str], Awaitable[Any]]   # (参数, call_id) -> 原始结果
    server: str | None = None
    formatter: Callable[[Any], Any] | None = None
    timeout: float = TOOL_TIMEOUT_SECONDS
    max_retries: int = 0
    inject_conversation_id: bool = False
    agent: bool = True
    tool: BaseTool | None = None           # 内置工具的 LangChain 对象（ch04 基线用）

    def openai_tool(self) -> dict: ...     # {"type": "function", "function": {"name", "description", "parameters"}}

def entry_from_tool(tool: BaseTool, *, permission: Permission = "read", formatter=None,
                    timeout: float = TOOL_TIMEOUT_SECONDS, max_retries: int | None = None,
                    inject_conversation_id: bool = False, agent: bool = True) -> ToolEntry
def register(tool: BaseTool | None = None, **opts)   # 可作装饰器 @register(...) 或直接调用 register(tool, ...)
def builtin_registry() -> dict[str, ToolEntry]       # 首次调用时扫描 BUILTIN_PACKAGE；lru 缓存
def load_package(package: str) -> None               # pkgutil 导入包内全部模块
BUILTIN_PACKAGE = "app.tools.builtin"
CH04_CHAT_TOOLS = ("query_order", "query_product", "query_logistics", "query_faq", "create_ticket")
def ch04_tools() -> list[BaseTool]                   # 按 CH04_CHAT_TOOLS 顺序
def ch04_toolset() -> Toolset                        # 同上的条目，全部开放

# app/tools/toolset.py
@dataclass(frozen=True)
class Toolset:
    entries: dict[str, ToolEntry]              # 本轮开放
    closed: dict[str, str] = field(default_factory=dict)   # 存在但没开放：名 -> 原因
    unavailable: frozenset[str] = frozenset()  # 策略列为 read、本轮没发现到
    def agent_tools(self) -> list[dict]        # entries 中 agent=True 的 openai_tool()，按登记顺序
    def get(self, name) -> ToolEntry | None

def builtin_toolset() -> Toolset               # 全部内置条目开放（含 agent=False）
```

`max_retries` 缺省时：`read` 取 `TOOL_MAX_ATTEMPTS - 1`（2），`write` 取 0。`parameters` 取 `convert_to_openai_tool(tool)["function"]["parameters"]`（自动去掉 `InjectedToolArg` 字段）。内置 runner：`lambda args, call_id: tool.ainvoke(args)`。

内置工具登记：

| 文件 | 工具 | 选项 |
|---|---|---|
| `builtin/order.py` | `query_order` | 默认 |
| `builtin/product.py` | `query_product` | 默认 |
| `builtin/logistics.py` | `query_logistics` | 默认（任务 6 移到 `legacy/`） |
| `builtin/faq.py` | `query_faq` | `timeout=QUERY_FAQ_TIMEOUT_SECONDS, max_retries=0, agent=False` |
| `builtin/ticket.py` | `create_ticket` | `permission="write", inject_conversation_id=True` |
| `builtin/control.py` | `offer_human_options`、`offer_refund_form` | `max_retries=0`；对 `app.graph.control` 中已有的工具对象调用 `register(tool, ...)` |

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_registry.py
import sys
import textwrap

import pytest

from app.tools import registry
from app.tools.toolset import builtin_toolset


def test_builtin_scan_registers_production_tools():
    names = list(registry.builtin_registry())
    assert set(names) == {"query_order", "query_product", "query_logistics", "query_faq", "create_ticket",
                          "offer_human_options", "offer_refund_form"}


def test_entry_fields():
    reg = registry.builtin_registry()
    t = reg["create_ticket"]
    assert (t.permission, t.max_retries, t.inject_conversation_id, t.source) == ("write", 0, True, "builtin")
    assert "conversation_id" not in t.parameters["properties"]
    assert set(t.parameters["required"]) == {"description", "ticket_type"}
    q = reg["query_order"]
    assert (q.permission, q.max_retries, q.agent) == ("read", 2, True)
    assert reg["query_faq"].agent is False
    assert q.openai_tool()["function"]["name"] == "query_order"


def test_new_file_in_package_is_registered(tmp_path, monkeypatch):
    pkg = tmp_path / "plugpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "points.py").write_text(textwrap.dedent('''
        from langchain_core.tools import tool
        from app.tools.registry import register

        @register()
        @tool("query_points_test")
        async def query_points_test(phone: str) -> dict:
            """查积分。"""
            return {"points": 1}
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    before = set(registry.builtin_registry())
    registry.load_package("plugpkg")
    assert set(registry.builtin_registry()) - before == {"query_points_test"}
    registry._unregister("query_points_test")


def test_builtin_toolset_opens_everything():
    ts = builtin_toolset()
    assert "query_faq" in ts.entries and ts.closed == {}
    assert "query_faq" not in [t["function"]["name"] for t in ts.agent_tools()]


def test_ch04_tools_unchanged():
    assert [t.name for t in registry.ch04_tools()] == list(registry.CH04_CHAT_TOOLS)
```

`registry._unregister(name)` 是测试用的辅助函数（从内部表删除一个条目），一并实现。

- [ ] **Step 2: 运行，确认失败**

Run: `uv run pytest tests/test_registry.py -q`
Expected: FAIL（`AttributeError: module 'app.tools.registry' has no attribute 'builtin_registry'`）

- [ ] **Step 3: 实现注册表和工具集**

`register` 写入模块级 `_ENTRIES: dict[str, ToolEntry]`，重名时抛 `ValueError(f"工具重名：{name}")`。`builtin_registry()` 在第一次调用时执行 `load_package(BUILTIN_PACKAGE)`（用模块级标志防重入），返回 `_ENTRIES` 的副本（`dict(_ENTRIES)`，保持插入顺序）。`load_package` 用 `importlib.import_module(package)` 和 `pkgutil.iter_modules(pkg.__path__)` 逐个 `import_module(f"{package}.{name}")`。

`ch04_tools()` 从 `builtin_registry()` 取 `.tool`；任务 6 之后 `query_logistics` 从 `app.tools.legacy.logistics` 取（本任务先从注册表取）。

- [ ] **Step 4: 迁移调用方**

1. `app/tools/executor.py`：签名改为 `execute_tool_calls(calls, *, conversation_id: int | None, toolset: Toolset | None = None, sleep=..., rand=...)`；`toolset` 为 None 时用 `builtin_toolset()`。查找改为：`entry = toolset.get(name)`；不在 `entries` → 现有的 `unknown_tool` 失败。执行改为 `entry.runner(args, call_id)`；超时 `entry.timeout`；`retryable` 改为 `entry.max_retries > 0`，`attempts = entry.max_retries + 1`。参数校验本任务仍用 `entry.tool.args_schema.model_validate(args)`（只有内置工具），任务 3 换成 JSON Schema。
2. `app/graph/state.py`：`GraphContext` 加 `base_toolset: Toolset | None = None`（不进 State）。
3. `app/graph/nodes/agent.py`：删除对 `get_registry` 的引用。新函数：

```python
def turn_toolset(state, base: Toolset) -> Toolset:
    """按本轮状态收窄工具集。"""
    entries = dict(base.entries)
    closed = dict(base.closed)
    for name, entry in list(entries.items()):
        if not entry.agent or name not in AGENT_TOOLS + (REFUND_FORM_TOOL,):
            closed[name] = "工具未开放"
            del entries[name]
    if not (state.get("route") == "aftersales" and state.get("order_id")):
        if REFUND_FORM_TOOL in entries:
            closed[REFUND_FORM_TOOL] = "工具未开放"
            del entries[REFUND_FORM_TOOL]
    return Toolset(entries, closed, base.unavailable)
```

本任务暂时保留 `AGENT_TOOLS` 白名单（任务 6 删除），这样本任务行为不变。`agent_model` 用 `turn_toolset(state, builtin_toolset()).agent_tools()` 绑定（字典形式）；`agent_tools` 把同一个工具集传给 `ctx.execute(..., toolset=...)`，删除自己的允许列表检查（不在工具集的调用由执行引擎返回 `unknown_tool`，行为与现在相同）；`invalid_order` 分支本任务保留。`measure_system_tokens` 改为用 `builtin_toolset()` 中 `AGENT_TOOLS + (REFUND_FORM_TOOL,)` 的 `openai_tool()`。
4. `app/graph/nodes/aftersales.py`、`app/api/tickets.py`：调用不变（`toolset` 用默认）。
5. `evals/run_rag_eval.py`、`evals/run_tool_selection_eval.py`：`get_registry().tools_for_model(CH04_CHAT_TOOLS)` 改为 `ch04_tools()`；`execute_tool_calls(read_calls, conversation_id=0)` 改为 `execute_tool_calls(read_calls, conversation_id=None, toolset=ch04_toolset())`。
6. `tests/fakes.py`：`Recorder` 记录工具名时兼容字典：

```python
"tools": [t["function"]["name"] if isinstance(t, dict) else t.name for t in self.bound_tools],
```

7. 更新 `tests/test_tools.py`（`get_registry`、`ToolSpec`、`tools_for_model` 相关的测试改用新接口，断言内容不变）和 `tests/test_executor.py`（`make_registry` 改为构造 `Toolset`，条目用 `entry_from_tool(echo, max_retries=2 if retryable else 0, timeout=timeout)`）。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest -q`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add -A app/tools app/graph app/api/tickets.py evals/run_rag_eval.py evals/run_tool_selection_eval.py tests
git commit -m "refactor(ch08): tool registry with builtin package scan and per-turn toolset"
```

---

### Task 3: 执行引擎（权限、JSON Schema 校验、重试计数、分诊、格式化、审计）

**Files:**
- Create: `app/tools/validation.py`
- Rewrite: `app/tools/executor.py`
- Modify: `app/retry.py`（加 `on_retry`、`should_retry`）
- Modify: `app/api/tickets.py`（传 `approvals`）
- Modify: `pyproject.toml`（`uv add jsonschema`）
- Test: `tests/test_validation.py`、`tests/test_executor.py`（重写）、`tests/test_retry.py`（若无则新建）

**Interfaces:**
- Consumes: 任务 1 `audit.record`、`AuditRecord`；任务 2 `ToolEntry`、`Toolset`、`builtin_toolset`
- Produces:

```python
# app/tools/validation.py
def validate_args(schema: dict, args: Any) -> list[str]   # 空列表 = 通过

# app/retry.py
async def retry_async(fn, *, attempts, base_delay, max_delay, retry_on=(), should_retry=None,
                      on_retry: Callable[[int, BaseException], None] | None = None, sleep=..., rand=...)

# app/tools/executor.py
APPROVED = "approved"

@dataclass
class ToolOutcome:
    call_id: str
    name: str
    ok: bool                 # status == "成功"
    message: ToolMessage
    data: Any = None         # 原始结果（格式化前）
    status: str = "成功"      # 审计状态
    found: bool = True       # 查询落空时为 False
    retry_count: int = 0
    duration_ms: int = 0
    error: str | None = None # error_message

async def execute_tool_calls(calls, *, conversation_id: int | None, toolset: Toolset | None = None,
                             approvals: Mapping[str, str] | None = None,
                             sleep=asyncio.sleep, rand=random.random) -> list[ToolOutcome]
def is_transient(exc: BaseException) -> bool
```

发给模型的内容（`ToolMessage.content`，均为 `json.dumps(..., ensure_ascii=False)`）：

| 情况 | content | status | error |
|---|---|---|---|
| 成功 | `{"ok": true, "data": <格式化结果>}` | 成功 | None |
| 查询落空 | `{"ok": true, "found": false, "message": "没有查到相关数据" + ("：" + reason if 结果含 reason)}` | 成功 | `查询落空` |
| 参数不合法 | `{"ok": false, "error": "invalid_arguments", "message": "参数不合法：" + "；".join(errors) + "。请向用户追问缺少的信息，或修正后重试"}` | 校验拦下 | `"；".join(errors)` |
| 权限 | `{"ok": false, "error": "permission_denied", "message": <原因>}` | 权限拒绝 | 原因 |
| 工具不存在 | `{"ok": false, "error": "unknown_tool", "message": "工具不存在"}` | 失败 | 工具不存在 |
| 暂时不可用 | `{"ok": false, "error": "tool_unavailable", "message": "工具暂时不可用"}` | 失败 | 工具暂时不可用 |
| 超时 | `{"ok": false, "error": "timeout", "message": "查询超时，暂时不可用"}` | 超时 | `超时（共 N 次尝试）` |
| 故障 | `{"ok": false, "error": "tool_error", "message": "查询失败，暂时不可用"}` | 失败 | 异常类型名 |

步骤顺序（每个调用）：
1. `args` 不是 dict → 校验拦下（`参数必须是 JSON 对象`）。
2. `name in toolset.closed` → 权限拒绝（原因取 `closed[name]`）；`name in toolset.unavailable` → 暂时不可用；不在 `entries` → 工具不存在。
3. `entry.permission == "write"`：`approvals.get(call_id)` 为 `APPROVED` 才继续；为其他字符串 → 权限拒绝（原因即该字符串）；缺省 → 权限拒绝（`未经用户确认`）。
4. `validate_args(entry.parameters, args)` 非空 → 校验拦下。
5. 注入：`entry.inject_conversation_id` 时 `args = {**args, "conversation_id": conversation_id}`（覆盖模型给的值）。
6. 执行：`asyncio.wait_for(entry.runner(args, call_id), entry.timeout)`。`entry.max_retries > 0` 时用 `retry_async(attempts=max_retries+1, should_retry=is_transient, on_retry=计数)`。`pydantic.ValidationError` → 校验拦下（错误文本取 `e.errors()` 的 `msg`）。
7. 分诊：结果为 `None`、`{}`、`[]`，或 dict 且 `result.get("found") is False` → 查询落空。
8. 格式化：`entry.formatter(result)`（有 formatter 时），再 dumps、截断（`tool_result_max_chars()`）。
9. 审计：`audit.record(AuditRecord(...))`；`arguments` 用模型给的原参数（注入前）；`result_summary` = content；`tool_source=entry.source`，`mcp_server=entry.server`（查不到条目时 `builtin`/None）。
10. 日志：`logger.info("tool_call conversation=%s name=%s source=%s status=%s retries=%s ms=%s", ...)`，`source` 写 `builtin` 或 `mcp:<server>`。

`is_transient`：`TimeoutError`、`sqlalchemy.exc.OperationalError`、`httpx.TransportError`、`ConnectionError` 返回 True；`BaseExceptionGroup` 时，全部子异常 transient 才 True；`langchain_core.tools.ToolException` 返回 False；其余 False。

- [ ] **Step 1: 写失败的测试（校验）**

```python
# tests/test_validation.py
from app.tools.registry import builtin_registry
from app.tools.validation import validate_args


def schema(name):
    return builtin_registry()[name].parameters


def test_missing_required():
    assert validate_args(schema("create_ticket"), {"ticket_type": "售后"}) == ["缺少必填参数 description"]


def test_enum_and_pattern_and_length():
    errs = validate_args(schema("create_ticket"), {"description": "", "ticket_type": "退货"})
    assert "ticket_type 只能是 售后/投诉/咨询" in errs
    assert "description 长度不能少于 1 个字" in errs
    assert validate_args(schema("query_order"), {"order_id": "订单一"}) == ["order_id 格式不对"]


def test_type():
    assert validate_args(schema("query_order"), {"order_id": 1001}) == ["order_id 类型应为字符串"]


def test_not_object():
    assert validate_args(schema("query_order"), "1001") == ["参数必须是 JSON 对象"]


def test_ok():
    assert validate_args(schema("query_order"), {"order_id": "1001"}) == []
```

- [ ] **Step 2: 实现 `validate_args`**

```python
"""JSON Schema 校验，错误翻成给模型看的短中文。"""

from typing import Any

from jsonschema import Draft202012Validator

_TYPES = {"string": "字符串", "integer": "整数", "number": "数字", "boolean": "布尔值",
          "array": "列表", "object": "对象", "null": "空"}


def _where(error) -> str:
    return ".".join(str(p) for p in error.absolute_path) or "参数"


def _message(error) -> list[str]:
    v, val, where = error.validator, error.validator_value, _where(error)
    if v == "required":
        return [f"缺少必填参数 {name}" for name in val if name not in (error.instance or {})]
    if v == "type":
        types = val if isinstance(val, list) else [val]
        return [f"{where} 类型应为{'或'.join(_TYPES.get(t, t) for t in types)}"]
    if v in ("enum", "const"):
        values = val if v == "enum" else [val]
        return [f"{where} 只能是 {'/'.join(str(x) for x in values)}"]
    if v == "pattern":
        return [f"{where} 格式不对"]
    if v == "minLength":
        return [f"{where} 长度不能少于 {val} 个字"]
    if v == "maxLength":
        return [f"{where} 长度不能超过 {val} 个字"]
    if v in ("minimum", "exclusiveMinimum"):
        return [f"{where} 不能小于 {val}"]
    if v in ("maximum", "exclusiveMaximum"):
        return [f"{where} 不能大于 {val}"]
    if v == "minItems":
        return [f"{where} 至少 {val} 项"]
    if v == "maxItems":
        return [f"{where} 最多 {val} 项"]
    if v == "uniqueItems":
        return [f"{where} 不能有重复项"]
    if v == "additionalProperties":
        return [f"不认识的参数：{error.message}"]
    return [f"{where} 取值不合法"]


def validate_args(schema: dict, args: Any) -> list[str]:
    if not isinstance(args, dict):
        return ["参数必须是 JSON 对象"]
    errors = sorted(Draft202012Validator(schema).iter_errors(args), key=lambda e: list(map(str, e.absolute_path)))
    out: list[str] = []
    for error in errors:
        for msg in _message(error):
            if msg not in out:
                out.append(msg)
    return out
```

Run: `uv add jsonschema && uv run pytest tests/test_validation.py -q` → PASS。

- [ ] **Step 3: 写失败的测试（引擎）**

`tests/test_executor.py` 重写。辅助函数：

```python
import asyncio
import json

import httpx
import pytest
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field

from app.tools.executor import APPROVED, execute_tool_calls, is_transient
from app.tools.registry import entry_from_tool
from app.tools.toolset import Toolset

pytestmark = pytest.mark.anyio


async def no_sleep(_):
    pass


def payload(o):
    return json.loads(o.message.content)


def make(fn_result=None, *, raises=None, sleep_s=0.0, permission="read", max_retries=None, timeout=0.05,
         inject=False, formatter=None):
    """raises：None，或 n -> 异常或 None 的函数（n 为第几次调用）。sleep_s：每次调用先等待的秒数。"""
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

    from dataclasses import replace
    return Toolset({"t": replace(entry, runner=runner)}), calls


def call(cid="c1", args=None, name="t"):
    return {"id": cid, "name": name, "args": {"order_id": "1"} if args is None else args}
```

测试用例（每条一个 `async def test_...`）：

1. 成功：`{"ok": true, "data": {...}}`，`status="成功"`，`audit_log[0].status == "成功"`，`retry_count == 0`，`duration_ms >= 0`。
2. 中文不转义：结果 `{"状态": "运输中"}` → `"运输中" in o.message.content`。
3. 查询落空（`None`、`{}`、`{"found": False, "reason": "订单不存在"}` 三种）：`payload == {"ok": True, "found": False, "message": ...}`，`o.found is False`，`calls["n"] == 1`（不重试），审计 `成功`、`error_message == "查询落空"`。
4. 校验拦下：`args={"order_id": "abc"}` → `error == "invalid_arguments"`，message 含 `order_id 格式不对`，runner 未调用，审计 `校验拦下`。
5. 暂时性故障重试：`raises=lambda n: TimeoutError() if n < 3 else None`、`max_retries=2` → 成功，`retry_count == 2`，审计 `retry_count == 2`。
6. 重试用尽：始终 `httpx.ConnectError("x")` → `status="失败"`，`retry_count == 2`，`calls["n"] == 3`。
7. 超时：runner `await asyncio.sleep(1)`，`timeout=0.01`，`max_retries=2` → `status="超时"`，`retry_count == 2`，审计 `超时`。
8. `ToolException` 不重试：`calls["n"] == 1`，`status="失败"`。
9. 写工具无确认：`permission="write"` → `权限拒绝`、`未经用户确认`，runner 未调用。
10. 写工具取消：`approvals={"c1": "用户取消"}` → `权限拒绝`，审计 `error_message == "用户取消"`。
11. 写工具确认后超时：`approvals={"c1": APPROVED}`、runner 睡 1 秒、`timeout=0.01` → `超时`、`retry_count == 0`、`calls["n"] == 1`。
12. 注入覆盖（Review Focus 3）：`inject=True`，模型参数 `{"order_id": "1", "conversation_id": 999}`，`conversation_id=7` → `calls["args"][0]["conversation_id"] == 7`；审计 `arguments == {"order_id": "1", "conversation_id": 999}`（原参数）。
13. `closed` → 权限拒绝（原因取表中值）；`unavailable` → `tool_unavailable`；未知名 → `unknown_tool`，审计 `tool_source == "builtin"`。
14. formatter：`formatter=lambda r: {"x": r["a"]}` → `payload["data"] == {"x": 1}`，`o.data == {"a": 1, "b": 2}`。
15. 截断：结果 `{"pad": "字" * 5000}` → content 以 `…(结果过长，已截断)` 结尾。
16. 并行且按输入顺序返回：两个调用 id 顺序不变。
17. 审计写失败不影响结果：`audit.set_audit_writer(boom)` → 结果照常 `成功`。
18. `is_transient`：`TimeoutError()`、`httpx.ConnectError("x")`、`ExceptionGroup("g", [httpx.ConnectError("x")])` 为 True；`ToolException("x")`、`ValueError()`、`ExceptionGroup("g", [ValueError()])` 为 False。

`tests/test_retry.py` 加：`on_retry` 被调用的次数等于实际重试次数；`should_retry` 返回 False 时立即抛出。

- [ ] **Step 4: 运行，确认失败**

Run: `uv run pytest tests/test_executor.py tests/test_retry.py -q`
Expected: FAIL

- [ ] **Step 5: 实现 `retry_async` 扩展和执行引擎**

`retry_async`：保留 `retry_on` 元组参数；新增 `should_retry`（给了就捕获 `Exception`，`should_retry(exc)` 为 False 时直接抛）；每次重试等待前调用 `on_retry(retry_number, exc)`。

引擎按上面的步骤表实现。耗时用 `time.monotonic()`，从进入 `execute_call` 到出结果。所有分支经同一个 `_finish(...)` 构造 `ToolOutcome`、写审计、打 `tool_call` 日志。保留 `tool_result_max_chars()`。删除 `failure_outcome`（调用方在任务 6、8 改掉；本任务先保留一个兼容函数，内部改成 `status="失败"`）。

`app/api/tickets.py`：

```python
outcome = (await execute_tool_calls([call], conversation_id=cid, approvals={call["id"]: APPROVED}))[0]
```

- [ ] **Step 6: 运行测试**

Run: `uv run pytest -q`
Expected: 全部通过。`tests/test_tickets_api.py` 中工单仍能创建；`audit_log` 中有一条 `create_ticket` `成功`。

- [ ] **Step 7: Commit**

```bash
git add app/tools/validation.py app/tools/executor.py app/retry.py app/api/tickets.py pyproject.toml uv.lock tests
git commit -m "feat(ch08): unified execution engine with permission gate, JSON Schema validation, triage and audit"
```

---

### Task 4: 策略文件

**Files:**
- Create: `app/tools/policy.py`、`config/tools.json`
- Modify: `app/config.py`（`TOOL_POLICY_PATH`）
- Test: `tests/test_policy.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ServerPolicy:
    url: str
    tools: dict[str, str]          # 工具名 -> "read" | "write" | "deny"

@dataclass(frozen=True)
class ToolOverride:
    timeout_seconds: float | None = None
    max_retries: int | None = None

@dataclass(frozen=True)
class ToolPolicy:
    servers: dict[str, ServerPolicy]       # 保持文件中的顺序
    overrides: dict[str, ToolOverride]
    def mcp_permission(self, server: str, tool: str) -> Literal["read", "write", "deny", "unlisted"]

EMPTY_POLICY = ToolPolicy({}, {})
def load_policy(path: Path | None = None) -> ToolPolicy    # 默认 TOOL_POLICY_PATH；按 (path, mtime_ns) 缓存
def apply_override(entry: ToolEntry, policy: ToolPolicy) -> ToolEntry
```

`app/config.py`：`TOOL_POLICY_PATH = Path(__file__).resolve().parent.parent / "config" / "tools.json"`。

`config/tools.json` 内容与 spec 4.3 相同（`overrides` 中只放 `"create_ticket": {"timeout_seconds": 5}`）。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_policy.py
import json
import os

from app.tools import policy as pol
from app.tools.registry import builtin_registry


def write(path, data, mtime=None):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if mtime:
        os.utime(path, ns=(mtime, mtime))


DATA = {"servers": {"logistics": {"url": "http://x/mcp", "tools": {"query_logistics": "read", "bad": "deny"}}},
        "overrides": {"query_order": {"timeout_seconds": 2, "max_retries": 1}}}


def test_permissions(tmp_path):
    p = tmp_path / "tools.json"
    write(p, DATA)
    policy = pol.load_policy(p)
    assert policy.mcp_permission("logistics", "query_logistics") == "read"
    assert policy.mcp_permission("logistics", "bad") == "deny"
    assert policy.mcp_permission("logistics", "new_tool") == "unlisted"
    assert policy.mcp_permission("other", "x") == "unlisted"


def test_reload_on_mtime_change(tmp_path):
    p = tmp_path / "tools.json"
    write(p, DATA, mtime=1_000_000_000)
    assert pol.load_policy(p).mcp_permission("logistics", "new_tool") == "unlisted"
    data = json.loads(json.dumps(DATA))
    data["servers"]["logistics"]["tools"]["new_tool"] = "read"
    write(p, data, mtime=2_000_000_000)
    assert pol.load_policy(p).mcp_permission("logistics", "new_tool") == "read"


def test_broken_file_keeps_last_good(tmp_path, caplog):
    p = tmp_path / "tools.json"
    write(p, DATA, mtime=1_000_000_000)
    good = pol.load_policy(p)
    p.write_text("{oops", encoding="utf-8")
    os.utime(p, ns=(2_000_000_000, 2_000_000_000))
    assert pol.load_policy(p) == good
    assert "tool_policy_invalid" in caplog.text


def test_missing_file_first_time_is_empty(tmp_path):
    assert pol.load_policy(tmp_path / "none.json") == pol.EMPTY_POLICY


def test_override_applies_to_builtin(tmp_path):
    p = tmp_path / "tools.json"
    write(p, DATA)
    entry = pol.apply_override(builtin_registry()["query_order"], pol.load_policy(p))
    assert (entry.timeout, entry.max_retries) == (2, 1)


def test_repo_policy_file_is_valid():
    policy = pol.load_policy()
    assert set(policy.servers) == {"logistics", "aftersales"}
    assert policy.mcp_permission("aftersales", "query_warranty") == "read"
```

- [ ] **Step 2: 运行，确认失败** — `uv run pytest tests/test_policy.py -q` → FAIL

- [ ] **Step 3: 实现**

默认路径在函数内读取：`path = path or TOOL_POLICY_PATH`（不要写成默认参数值），这样测试 monkeypatch `app.tools.policy.TOOL_POLICY_PATH` 才生效。缓存：模块级 `_cache: dict[Path, tuple[int, ToolPolicy]]`。每次调用 `path.stat().st_mtime_ns`；与缓存相同则返回缓存。解析失败（`OSError`、`json.JSONDecodeError`、结构不对的 `KeyError/TypeError/ValueError`）：有缓存则返回缓存并打 `logger.warning("tool_policy_invalid path=%s error=%s", ...)`；没有缓存则返回 `EMPTY_POLICY` 并打同一条 warning。权限值不是 `read/write/deny` 时按 `deny` 处理。

- [ ] **Step 4: 运行测试** — `uv run pytest tests/test_policy.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/policy.py config/tools.json app/config.py tests/test_policy.py
git commit -m "feat(ch08): tool policy file with mtime reload and default deny for MCP tools"
```

---

### Task 5: 两个 MCP Server

**Files:**
- Create: `mcp_servers/__init__.py`、`mcp_servers/common.py`
- Create: `mcp_servers/logistics/__init__.py`、`__main__.py`、`server.py`
- Create: `mcp_servers/aftersales/__init__.py`、`__main__.py`、`server.py`、`tools/__init__.py`、`tools/warranty.py`、`tools/returns.py`
- Modify: `pyproject.toml`（`uv add "mcp>=1.24,<2" "langchain-mcp-adapters>=0.3.2"`）
- Test: `tests/test_mcp_servers.py`

**Interfaces:**
- Produces:
  - `mcp_servers.common`：`delay() -> float`（读 `MOCK_DELAY_SECONDS`，默认 0）、`async def maybe_delay()`、`rng(kind, order_id) -> random.Random`、`not_found(order_id) -> dict | None`（订单号以 `9` 开头返回 `{"found": False, "order_id": ..., "reason": "订单不存在"}`）、`PRODUCTS`（8 个商品名，与 `app/tools/mock_data.CATALOG` 的名称一致，复制一份，不导入 `app`）、`OrderId = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{1,32}$", description="订单号")]`
  - `mcp_servers.logistics.server.build_server(host="127.0.0.1", port=8101) -> FastMCP`；工具 `async def query_logistics(order_id: OrderId) -> dict`
  - `mcp_servers.aftersales.server.build_server(host="127.0.0.1", port=8102) -> FastMCP`；启动时用 `pkgutil` 导入 `mcp_servers.aftersales.tools` 下全部模块，对每个模块调用 `module.register(mcp)`。
  - `tools/warranty.py`：`register(mcp)` 定义 `query_warranty(order_id: OrderId) -> dict`；`tools/returns.py`：`register(mcp)` 定义 `query_return_progress(order_id: OrderId) -> dict`。
  - `__main__.py`：`argparse` 读 `--port`（默认 8101 / 8102），`build_server(port=args.port).run(transport="streamable-http")`。
  - `FastMCP(name, host=host, port=port, stateless_http=True, json_response=True)`（v1 写法：传输参数在构造函数上；默认路径 `/mcp`）。

返回结构（字段名固定，任务 6 的 formatter 依赖）：

```python
# query_logistics（order_id == "1001" 时 status_code 固定 IN_TRANSIT）
{"found": True, "order_id": "1001", "carrier_code": "SF" | "ZTO" | "YTO" | "JD", "tracking_no": "SF1234567890",
 "status_code": "PICKED_UP" | "IN_TRANSIT" | "OUT_FOR_DELIVERY" | "DELIVERED",
 "eta": "2026-10-11", "warehouse_id": "WH-07", "route_id": "R-3321",
 "traces": [{"time": "2026-10-08 09:12", "node_code": "N01", "city": "杭州", "desc": "快件已揽收"}, ...]}
# query_warranty
{"found": True, "order_id": "...", "policy_code": "STD_365",
 "items": [{"sku_id": "P001", "name": "蓝牙耳机", "warranty_status": "IN_WARRANTY" | "EXPIRED" | "NO_WARRANTY",
            "warranty_end": "2027-10-01"}]}
# query_return_progress
{"found": True, "order_id": "...", "rma_no": "RMA20261008001",
 "status_code": "REQUESTED" | "APPROVED" | "RETURN_IN_TRANSIT" | "RETURN_RECEIVED" | "REFUND_PROCESSING" | "REFUNDED" | "REJECTED",
 "updated_at": "2026-10-08 15:30", "refund_amount": 299, "internal_note": "仓库复检通过"}
```

日期按 `date.today()` 推算；轨迹时间递增；`DELIVERED` 时最后一条为签收。

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_mcp_servers.py
import pytest

from mcp_servers.aftersales import server as aftersales
from mcp_servers.common import not_found
from mcp_servers.logistics import server as logistics

pytestmark = pytest.mark.anyio


async def tool_names(srv):
    return sorted(t.name for t in await srv.list_tools())


async def test_tool_lists():
    assert await tool_names(logistics.build_server(port=0)) == ["query_logistics"]
    assert await tool_names(aftersales.build_server(port=0)) == ["query_return_progress", "query_warranty"]


async def test_input_schema_has_pattern():
    (t,) = await logistics.build_server(port=0).list_tools()
    assert t.inputSchema["properties"]["order_id"]["pattern"] == r"^[A-Za-z0-9-]{1,32}$"


async def test_logistics_deterministic_and_1001_in_transit():
    a = await logistics.query_logistics("1001")
    assert a == await logistics.query_logistics("1001")
    assert a["status_code"] == "IN_TRANSIT" and {"warehouse_id", "route_id"} <= set(a)
    times = [t["time"] for t in a["traces"]]
    assert times == sorted(times)


async def test_not_found_prefix_9():
    assert (await logistics.query_logistics("9001"))["found"] is False
    assert not_found("1001") is None


async def test_delay(monkeypatch):
    import time
    monkeypatch.setenv("MOCK_DELAY_SECONDS", "0.2")
    t0 = time.monotonic()
    await logistics.query_logistics("1001")
    assert time.monotonic() - t0 >= 0.2


def test_servers_do_not_import_app():
    import subprocess, sys
    code = ("import sys, mcp_servers.logistics.server, mcp_servers.aftersales.server; "
            "print(any(m == 'app' or m.startswith('app.') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
```

`query_logistics`、`query_warranty`、`query_return_progress` 必须是模块级可直接调用的异步函数（`logistics.query_logistics`），`build_server` 用 `mcp.tool()(query_logistics)` 登记。aftersales 的工具函数在 `tools/` 模块中同样定义在模块级，`register(mcp)` 只做 `mcp.tool()(fn)`。`FastMCP.list_tools()` 是异步方法，返回 `mcp.types.Tool` 列表。

- [ ] **Step 2: 运行，确认失败** — `uv run pytest tests/test_mcp_servers.py -q` → FAIL

- [ ] **Step 3: 实现并安装依赖**

```bash
uv add "mcp>=1.24,<2" "langchain-mcp-adapters>=0.3.2"
```

- [ ] **Step 4: 运行测试，并手动起进程确认** 

Run: `uv run pytest tests/test_mcp_servers.py -q` → PASS

Run（Claude 执行）：

```bash
uv run python -m mcp_servers.logistics --port 8101 & sleep 3
curl -s -X POST http://127.0.0.1:8101/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head -c 300; kill %1
```

Expected: 返回含 `query_logistics` 的 JSON。

- [ ] **Step 5: Commit**

```bash
git add mcp_servers pyproject.toml uv.lock tests/test_mcp_servers.py
git commit -m "feat(ch08): logistics and aftersales MCP servers with mock data"
```

---

### Task 6: MCP 发现、合并工具集、Agent 绑定；`query_logistics` 下线

**Files:**
- Create: `app/tools/mcp.py`、`app/tools/formatters.py`、`app/tools/legacy/__init__.py`、`app/tools/legacy/logistics.py`
- Delete: `app/tools/builtin/logistics.py`
- Modify: `app/tools/registry.py`（`ch04_tools`/`ch04_toolset` 从 `legacy` 取 `query_logistics`）、`app/graph/nodes/agent.py`、`app/config.py`（`MCP_DISCOVERY_TIMEOUT_SECONDS = 2`）、`tests/conftest.py`
- Test: `tests/test_mcp.py`、`tests/test_formatters.py`；更新所有用 `query_logistics`、`AGENT_TOOLS`、`invalid_order` 的测试

**Interfaces:**
- Consumes: 任务 2–5 的全部接口
- Produces:

```python
# app/tools/mcp.py
async def discover(policy: ToolPolicy) -> tuple[dict[str, list[BaseTool]], dict[str, str]]
    # 返回 (server -> 工具列表, server -> 失败原因)。每个 Server get_tools 用 wait_for(MCP_DISCOVERY_TIMEOUT_SECONDS)，并行。
def entry_from_mcp(tool: BaseTool, server: str, policy: ToolPolicy) -> ToolEntry
async def build_base_toolset(conversation_id: int | None) -> Toolset
    # 内置条目（apply_override）+ 发现到的 MCP 条目；打 toolset 日志
async def ensure_toolset(ctx) -> Toolset   # ctx.base_toolset 为 None 时构建并缓存

# app/tools/formatters.py
MCP_FORMATTERS: dict[tuple[str, str], Callable[[Any], Any]]
def formatter_for(server: str, tool: str) -> Callable[[Any], Any] | None

# app/graph/nodes/agent.py
def turn_toolset(state, base: Toolset) -> Toolset
```

`build_base_toolset` 合并规则：
0. `discover(policy)` 整体抛异常时（Review Focus 2），按全部 Server 发现失败处理，不向外抛。
1. 内置条目全部放入 `entries`（先 `apply_override`）。
2. 对策略文件中每个 Server（按文件顺序）：发现失败 → 该 Server 中权限为 `read` 的工具名加入 `unavailable`；打 `logger.warning("mcp_discovery_failed server=%s error=%s", ...)`。
3. 发现到的每个工具：名字已在 `entries` → 跳过，`logger.warning("mcp_tool_shadowed server=%s tool=%s", ...)`；`mcp_permission` 为 `read` → `entries`；其余（`write`、`deny`、`unlisted`）→ `closed[name] = "工具未开放"`。
4. 打一行 `logger.info("toolset conversation=%s builtin=%s mcp=%s denied=%s", cid, 内置数, "logistics:1,aftersales:2", ",".join(closed) or "-")`。

`entry_from_mcp`：`description=tool.description`、`parameters=tool.args_schema`（dict）、`source="mcp"`、`server=server`、`permission="read"`、`max_retries=TOOL_MAX_ATTEMPTS - 1`、`formatter=formatter_for(server, tool.name)`，再 `apply_override`。runner：

```python
async def runner(args, call_id):
    msg = await tool.ainvoke({"type": "tool_call", "id": call_id or "mcp", "name": tool.name, "args": args})
    artifact = getattr(msg, "artifact", None) or {}
    if isinstance(artifact, dict) and artifact.get("structured_content") is not None:
        return artifact["structured_content"]
    text = msg.content if isinstance(msg.content, str) else "".join(
        b.get("text", "") for b in msg.content if isinstance(b, dict))
    try:
        return json.loads(text)
    except ValueError:
        return text
```

`MultiServerMCPClient` 的构造：`MultiServerMCPClient({name: {"transport": "http", "url": s.url} for name, s in policy.servers.items()}, handle_tool_errors=False)`；`discover` 中 `await asyncio.wait_for(client.get_tools(server_name=name), MCP_DISCOVERY_TIMEOUT_SECONDS)`。实现前用 Context7（`/langchain-ai/langchain-mcp-adapters`）确认 0.3.2 的 `get_tools(server_name=)` 和 `handle_tool_errors` 参数。

formatter（`app/tools/formatters.py`）：

```python
CARRIERS = {"SF": "顺丰", "ZTO": "中通", "YTO": "圆通", "JD": "京东物流"}
LOGISTICS_STATUS = {"PICKED_UP": "已揽收", "IN_TRANSIT": "运输中", "OUT_FOR_DELIVERY": "派送中", "DELIVERED": "已签收"}
WARRANTY_STATUS = {"IN_WARRANTY": "在保", "EXPIRED": "已过保", "NO_WARRANTY": "不保修"}
RETURN_STATUS = {"REQUESTED": "已申请", "APPROVED": "已同意退货", "RETURN_IN_TRANSIT": "退货寄回中",
                 "RETURN_RECEIVED": "商家已收货", "REFUND_PROCESSING": "退款处理中", "REFUNDED": "已退款", "REJECTED": "已拒绝"}

def logistics(r):  # 去掉 warehouse_id、route_id、node_code
    return {"order_id": r["order_id"], "carrier": CARRIERS.get(r["carrier_code"], r["carrier_code"]),
            "tracking_no": r["tracking_no"], "status": LOGISTICS_STATUS.get(r["status_code"], r["status_code"]),
            "eta": r["eta"], "traces": [{"time": t["time"], "location": t["city"], "desc": t["desc"]} for t in r["traces"]]}
# warranty：去掉 policy_code、sku_id；items -> {"name", "warranty": 中文, "warranty_end"}
# return_progress：去掉 internal_note；status_code -> 中文；保留 rma_no、updated_at、refund_amount
MCP_FORMATTERS = {("logistics", "query_logistics"): logistics, ("aftersales", "query_warranty"): warranty,
                  ("aftersales", "query_return_progress"): return_progress}
```

formatter 只在 `found` 不为 False 时调用（引擎先分诊，再格式化）。

Agent 绑定（替换任务 2 的临时版本）：

```python
REFUND_FORM_TOOL = "offer_refund_form"
TICKET_TOOL = "create_ticket"


def turn_toolset(state, base: Toolset) -> Toolset:
    entries, closed = dict(base.entries), dict(base.closed)

    def close(name):
        if name in entries:
            del entries[name]
            closed[name] = "工具未开放"

    for name, entry in list(entries.items()):
        if not entry.agent:
            close(name)
    order_id = state.get("order_id")
    if state.get("route") == "aftersales" and order_id:
        e = entries[REFUND_FORM_TOOL]
        params = {**e.parameters, "properties": {**e.parameters["properties"],
                  "order_id": {**e.parameters["properties"]["order_id"], "enum": [order_id]}}}
        entries[REFUND_FORM_TOOL] = replace(e, parameters=params)
    else:
        close(REFUND_FORM_TOOL)
    if not state.get("ticket_request"):
        close(TICKET_TOOL)
    return Toolset(entries, closed, base.unavailable)
```

`agent_model`：`base = await ensure_toolset(ctx)`；`ts = turn_toolset(state, base)`；`ctx.model.bind_tools(ts.agent_tools(), tool_choice="auto")`。绑定前实测 System + 工具定义 token：

```python
measured = count_tokens([SystemMessage(render_agent_system())]) + math.ceil(
    len(json.dumps(ts.agent_tools(), ensure_ascii=False)) / TOOL_SCHEMA_CHARS_PER_TOKEN)
if measured > SYSTEM_RESERVE_TOKENS:
    logger.warning("system_reserve_exceeded measured=%s reserve=%s conversation=%s", measured, SYSTEM_RESERVE_TOKENS, ctx.conversation_id)
```

`agent_tools`：`ts = turn_toolset(state, await ensure_toolset(ctx))`，`ctx.execute(calls, conversation_id=..., toolset=ts, approvals=state.get("approvals") or {})`。删除 `AGENT_TOOLS` 常量和 `invalid_order` 分支。`measure_system_tokens()`（启动自检）改为内置工具集中 `agent=True` 的全部条目（含 `create_ticket`、`offer_refund_form`）。

`tests/conftest.py` 加：

```python
@pytest.fixture(autouse=True)
def _block_mcp(monkeypatch):
    """默认不连 MCP Server。需要时用 fixture mcp_servers。"""
    from app.tools import mcp

    async def none(policy):
        return {}, {}
    monkeypatch.setattr(mcp, "discover", none)


@pytest.fixture
def mcp_servers(monkeypatch, tmp_path):
    """以子进程启动两个 MCP Server（测试端口），并写一份指向它们的策略文件。"""
    # 端口 18101、18102；等端口可连再 yield；结束时 terminate 并 wait。
    # 用 monkeypatch 恢复真实 discover，并把 app.tools.policy 的默认路径指向 tmp_path/tools.json。
    # yield 一个对象：.policy_path、.restart(name, env=None, extra_tool_file=None)
```

`_block_mcp` 拦截后，`config/tools.json` 中列为 `read` 的 MCP 工具都会进 `unavailable`；不影响内置工具。

- [ ] **Step 1: 写失败的测试**

`tests/test_formatters.py`：用 `mcp_servers.logistics.server.query_logistics("1001")` 的结果做输入，断言 `status == "运输中"`、`carrier` 为中文、输出中没有 `warehouse_id`、`route_id`、`node_code`；warranty 和 return_progress 同理（`internal_note` 不在输出中）。

`tests/test_mcp.py`（用 `mcp_servers` fixture 的测试标记 `pytest.mark.mcp`，默认也运行）：

1. 发现两个 Server：`build_base_toolset(1)` 的 `entries` 含 `query_logistics`（`source="mcp"`、`server="logistics"`）、`query_warranty`、`query_return_progress`，不含内置 `query_logistics`。
2. 调用：`execute_tool_calls([{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}], conversation_id=1, toolset=ts)` → `成功`，`payload["data"]["status"] == "运输中"`，审计 `tool_source == "mcp"`、`mcp_server == "logistics"`。
3. 查询落空：`order_id="9001"` → `found is False`，审计 `成功`、`查询落空`。
4. 校验拦下：`order_id="订单"` → `校验拦下`（我方先拦，不发到 Server）。
5. 未列出的工具被拒：策略文件删掉 `query_return_progress` → 它在 `closed`，调用 → `权限拒绝`。
6. Server 停掉：`restart` 前先停 aftersales → 发现时它的工具进 `unavailable`，日志有 `mcp_discovery_failed server=aftersales`；`query_logistics` 照常可用。
7. Server 新增工具：把一个含 `register(mcp)` 定义 `query_repair_progress` 的文件写进 `mcp_servers/aftersales/tools/`（测试结束删除），策略文件加 `"query_repair_progress": "read"`，只重启 aftersales → 下一次 `build_base_toolset` 发现到它，调用 `成功`，结果为未翻译的原始字段（没有 formatter）。
8. 调用时 Server 已停（发现后再停）：`失败`，`retry_count == 2`。先在这一步实测异常类型：如果 `is_transient` 没覆盖到（结果为 `retry_count == 0`），把实测到的异常类型加进 `is_transient` 并在 `tests/test_executor.py` 补一条用例。
9. 只有文本的结果（Review Focus 5）：在测试 Server 文件中加一个返回 `str`（非 JSON）的工具 `echo_text`（`structured_output=False`），策略列为 `read` → 调用 `成功`，`payload["data"] == "原样文本"`。

`tests/test_graph_agent.py`、`tests/test_chat_api.py`、`tests/test_graph.py`：

- `AGENT_TOOLS` 断言改为：`rec[0]["tools"] == ["query_order", "query_product", "offer_human_options"]`（`_block_mcp` 下没有 MCP 工具）。
- 原 `query_logistics` 的脚本调用改为 `query_order`（断言同步改）。
- `test_refund_form_with_other_order_fails`：断言 `error == "invalid_arguments"`，message 含 `order_id 只能是 1001`。
- `test_refund_form_not_allowed_outside_aftersales`：断言 `error == "permission_denied"`。
- 新增：`ticket_request` 为假时 `create_ticket` 不在绑定列表；为真时在。
- 新增：`system_reserve_exceeded` 在 `SYSTEM_RESERVE_TOKENS` 被 monkeypatch 为 10 时出现在日志中。

- [ ] **Step 2: 运行，确认失败** — `uv run pytest tests/test_mcp.py tests/test_formatters.py tests/test_graph_agent.py -q` → FAIL

- [ ] **Step 3: 实现**（按上面的接口；`query_logistics` 移到 `app/tools/legacy/logistics.py`，`legacy` 包不被扫描）

- [ ] **Step 4: 运行测试** — `uv run pytest -q` → 全部通过

- [ ] **Step 5: Commit**

```bash
git add -A app/tools app/graph app/config.py tests
git commit -m "feat(ch08): MCP discovery merged into per-turn toolset; logistics served by MCP"
```

---

### Task 7: `ticket_request` 分流

**Files:**
- Modify: `app/schemas.py`（`ResolvedQuery.ticket_request: bool = False`）、`app/services/understanding.py`（`Resolution.ticket_request`）、`app/prompts.py`（`RESOLVE_SYSTEM_PROMPT` 加一节、输出格式加字段）、`app/graph/nodes/turn.py`、`app/graph/state.py`（`ticket_request: bool`）、`app/graph/routing.py`
- Modify: `evals/multiturn_samples.jsonl`、`evals/run_multiturn_eval.py`（检查 `ticket_request`）
- Test: `tests/test_understanding.py`、`tests/test_graph.py`、`tests/test_multiturn_samples.py`

**Interfaces:**
- Produces: State `ticket_request: bool`（`start_turn` 重置为 False）；`after_intent` 第一条规则 `if state.get("ticket_request"): return "business"`。

`RESOLVE_SYSTEM_PROMPT` 在 `## history_recall` 一节之后加：

```
## ticket_request
用户这句话明确要求建工单、提交工单、留单时为 true，例如"帮我建个工单""给我提个投诉工单""我要提交一个售后工单"。客服刚请用户补充工单信息，用户这句在补充问题描述时也为 true，例如客服问"请描述一下遇到的问题"，用户答"耳机左耳没声音"。
只抱怨、只要求转人工、只问能不能建工单而没有要求建时为 false，例如"太差了""给我转人工""你们能建工单吗"。
```

输出格式行改为 `{"resolved_input": "...", ..., "history_recall": false, "ticket_request": false}`。`understanding.resolve`：`ticket_request=parsed.ticket_request`（历史为空也取，不像 `history_recall`）；解析失败时 False。`resolve_reference` 的日志行加 `ticket_request=%s`；返回值加 `"ticket_request"`。

`evals/multiturn_samples.jsonl` 加 2 组（每组每轮可带 `expect_ticket_request`）：

```json
{"group": "ticket-1", "turns": [
  {"user": "帮我建个工单", "expect_ticket_request": true},
  {"assistant": "好的，请描述一下您遇到的问题。", "user": "蓝牙耳机左耳没声音，买了一周", "expect_ticket_request": true},
  {"assistant": "已为您创建工单 T20261009001。", "user": "谢谢", "expect_ticket_request": false}]}
{"group": "ticket-2", "turns": [
  {"user": "你们的服务太差了", "expect_ticket_request": false},
  {"assistant": "非常抱歉……", "user": "给我转人工", "expect_ticket_request": false},
  {"assistant": "您可以点击下方按钮转人工。", "user": "算了，直接给我提个投诉工单，快递把箱子摔坏了", "expect_ticket_request": true}]}
```

（字段名以现有 `multiturn_samples.jsonl` 的格式为准，执行前先读该文件和 `run_multiturn_eval.py`，按现有键名补 `expect_ticket_request`；未写该键的轮不检查。）

- [ ] **Step 1: 写失败的测试**

- `tests/test_understanding.py`：解析结果 `ticket_request=True`、历史为空 → `Resolution.ticket_request is True`；解析失败 → False。
- `tests/test_graph.py`：`use_resolver({"ticket_request": True})` + `use_intent("投诉")` → trace 含 `agent_model`、不含 `complaint_reply`；`use_resolver({"ticket_request": True, "history_recall": True})` 同样走 business。
- `tests/test_multiturn_samples.py`：新组格式合法；`expect_ticket_request` 只能是 bool。
- `tests/test_prompts.py`：`RESOLVE_SYSTEM_PROMPT` 含 `ticket_request`。

- [ ] **Step 2: 运行，确认失败** — `uv run pytest tests/test_understanding.py tests/test_graph.py tests/test_multiturn_samples.py tests/test_prompts.py -q` → FAIL

- [ ] **Step 3: 实现** — 按上面的改动。

- [ ] **Step 4: 运行测试** — `uv run pytest -q` → 全部通过

- [ ] **Step 5: 评估（真实上游，Claude 执行）**

Run: `uv run python evals/run_multiturn_eval.py && uv run python evals/run_intent_eval.py`
Expected: 两个都退出码 0（多轮全部通过，含新组；意图评估不回退）。不通过时调整 Prompt 的定义和示例后重跑，翻车记 dev-notes。

- [ ] **Step 6: Commit**

```bash
git add app/schemas.py app/services/understanding.py app/prompts.py app/graph evals/multiturn_samples.jsonl evals/run_multiturn_eval.py tests
git commit -m "feat(ch08): resolver ticket_request flag routes explicit ticket requests to the agent"
```

---

### Task 8: `confirm_write`、`ticket_reply` 和 Agent 工单规则

**Files:**
- Create: `app/graph/nodes/confirm.py`
- Modify: `app/graph/builder.py`、`app/graph/routing.py`、`app/graph/state.py`、`app/graph/nodes/turn.py`（重置新字段）、`app/graph/nodes/agent.py`（`agent_tools` 写 `write_outcome`）、`app/graph/nodes/finalize.py`（`turn` 日志加字段）、`app/prompts.py`
- Test: `tests/test_graph_confirm.py`、`tests/test_prompts.py`

**Interfaces:**
- Consumes: `validate_args`、`builtin_registry()["create_ticket"].parameters`、`APPROVED`、`turn_toolset`、`TICKET_TOOL`
- Produces:
  - State：`approvals: dict[str, str]`、`write_decision: Literal["confirmed", "cancelled"] | None`、`write_outcome: dict | None`（`{"status", "ticket_no", "ticket_type"}`）。`start_turn` 重置为 `{}`、None、None。
  - `routing.after_agent`：`force_final` 或无工具调用 → `finalize`；有 `create_ticket` 调用且 `ticket_request` → `confirm_write`；否则 `agent_tools`。
  - `routing.after_tools(state)`：`write_decision` 非 None → `ticket_reply`；否则 `agent_model`。
  - interrupt 值：`{"type": "ticket_confirm", "call_id": str, "ticket_type": str, "description": str}`；恢复值 `{"confirmed": bool}`。
  - `prompts`：`TICKET_CANCELLED_REPLY`、`TICKET_TIMEOUT_REPLY`、`TICKET_FAILED_REPLY`（文字见 spec 5.4）。

`confirm_write`：

```python
async def confirm_write(state, runtime):
    trace = events.enter("confirm_write", state, runtime)
    # 恢复时本节点从头执行。interrupt 之前只做纯计算，不发事件、不写库、不调上游。
    calls = [c for c in state["agent_messages"][-1].tool_calls if c["name"] == TICKET_TOOL]
    first, extras = calls[0], calls[1:]
    approvals = {c["id"]: "一次只能提交一张工单" for c in extras}
    if validate_args(builtin_registry()[TICKET_TOOL].parameters, first["args"]):
        return {"approvals": approvals, "write_decision": None, "trace": trace}
    logger.info("interrupt=ticket_confirm conversation=%s call=%s", runtime.context.conversation_id, first["id"])
    answer = interrupt({"type": "ticket_confirm", "call_id": first["id"],
                        "ticket_type": first["args"]["ticket_type"], "description": first["args"]["description"]})
    confirmed = isinstance(answer, dict) and answer.get("confirmed") is True
    approvals[first["id"]] = APPROVED if confirmed else "用户取消"
    return {"approvals": approvals, "write_decision": "confirmed" if confirmed else "cancelled", "trace": trace}
```

`agent_tools` 在得到结果后：如果本步有 `create_ticket` 的结果且 `state.get("write_decision")`，写 `write_outcome = {"status": o.status, "ticket_no": (o.data or {}).get("ticket_no") if o.ok else None, "ticket_type": call["args"].get("ticket_type")}`。

`ticket_reply`：

```python
async def ticket_reply(state, runtime):
    trace = events.enter("ticket_reply", state, runtime)
    outcome = state.get("write_outcome") or {}
    if state.get("write_decision") == "cancelled":
        reply = TICKET_CANCELLED_REPLY
    elif outcome.get("status") == "成功":
        reply = TICKET_CREATED_NOTE.format(ticket_no=outcome["ticket_no"], ticket_type=outcome["ticket_type"])
    elif outcome.get("status") == "超时":
        reply = TICKET_TIMEOUT_REPLY
    else:
        reply = TICKET_FAILED_REPLY
    events.emit("token", {"text": reply})
    return {"reply": reply, "agent_messages": [*state["agent_messages"], AIMessage(content=reply)], "trace": trace}
```

builder：加节点 `confirm_write`、`ticket_reply`；`agent_model` 的条件边目标加 `confirm_write`；`confirm_write → agent_tools`；`agent_tools` 改为条件边 `after_tools`（`agent_model`、`ticket_reply`）；`ticket_reply → finalize`。

Agent System Prompt（`AGENT_SYSTEM_TEMPLATE`）：
- `## 工具使用` 第 4 条改为："工具结果中 ok 为 false 时，按 message 处理：参数不合法时向用户追问缺少的信息或修正后重试；查询失败时如实告诉用户暂时查不到，建议稍后再试或转人工。found 为 false 时，如实告诉用户没有查到。"
- 新增一节：

```
## 建工单
1. 只有用户明确要求建工单时，才调用 create_ticket。
2. 调用前核对：description 概括用户说过的问题，ticket_type 按用户诉求选（售后、投诉、咨询）。用户没讲清遇到什么问题时，先追问，不调用。不用"用户要求建工单"这类空话填 description，不编造用户没说过的细节。
3. 调用后，系统会请用户在页面上确认。不说已经创建工单。
4. 用户没有要求建工单、但问题需要人工跟进时，用 offer_human_options 给出建工单按钮。
```

- `## 行为约束` 第 4 条改为："工单只能在用户确认或点击按钮后创建。历史消息中有"已为您创建工单"时，可以告诉用户工单号。否则不要声称已经转接或已经建单，也不要编造联系入口、电话或链接。"

- [ ] **Step 1: 写失败的测试**

`tests/test_graph_confirm.py`（用 `memory_graph`、`use_script`、`use_resolver`、`use_intent`、`db`、`db_audit`；Agent 走 `ticket_request=True`）：

```python
TICKET_CALL = ("t1", "create_ticket", {"description": "蓝牙耳机左耳没声音", "ticket_type": "售后"})
```

1. 发起：脚本 `tools(TICKET_CALL)` → SSE 事件依次为 `session`、`understood`、`ticket_preview`、`done`；`ticket_preview` 数据 `{"call_id": "t1", "ticket_type": "售后", "description": "蓝牙耳机左耳没声音"}`；`done.finish_reason == "interrupted"`；`messages` 表无行；`tickets` 无行；审计无 `create_ticket` 行。（`ticket_preview` 由任务 9 的 API 发出；本任务先在图层断言：`graph.aget_state(...).interrupts[0].value == {"type": "ticket_confirm", ...}`，SSE 断言放任务 9。）
2. 确认：`graph.astream(Command(resume={"confirmed": True}), ...)` → `tickets` 一条；回复 == `TICKET_CREATED_NOTE.format(...)`；审计 `create_ticket` `成功`、`retry_count == 0`；`messages` 表两行（用户原话、回复）；模型只被调用 1 次（`len(rec) == 1`）。
3. 取消：恢复 `{"confirmed": False}` → `tickets` 无行；回复 == `TICKET_CANCELLED_REPLY`；审计 `create_ticket` `权限拒绝`、`error_message == "用户取消"`。
4. 参数不合法不中断：脚本 `tools(("t1", "create_ticket", {"ticket_type": "售后"}))`、`text("请描述一下遇到的问题")` → 没有 interrupt；审计 `校验拦下`；回复为第 2 段脚本文字。
5. 同一步两个 `create_ticket`：只预览 `t1`；确认后第二个审计 `权限拒绝`、`一次只能提交一张工单`；`tickets` 只有一条。
6. 写超时：在 `tmp_path` 写一份策略文件 `{"servers": {}, "overrides": {"create_ticket": {"timeout_seconds": 0.01}}}`，monkeypatch `app.tools.policy.TOOL_POLICY_PATH` 指向它；monkeypatch `app.repositories.tickets.create_ticket_record` 为先 `await asyncio.sleep(1)` 的函数 → 回复 == `TICKET_TIMEOUT_REPLY`；审计 `超时`、`retry_count == 0`；被 monkeypatch 的函数只被调用 1 次。
7. `ticket_request` 为假：脚本仍给 `create_ticket` 调用 → 不进 `confirm_write`；审计 `权限拒绝`、`工具未开放`；第 2 段脚本回复。
8. Review Focus 2：发起后让 `app.tools.mcp.discover` 抛 `RuntimeError`，再恢复确认 → `tickets` 一条。
9. `turn` 日志行含 `ticket_request=True` 和 `write=confirmed`。

`tests/test_prompts.py`：Agent System 含 `## 建工单`，不含"工单只能由用户点击按钮创建"。

- [ ] **Step 2: 运行，确认失败** — `uv run pytest tests/test_graph_confirm.py tests/test_prompts.py -q` → FAIL

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行测试** — `uv run pytest -q` → 全部通过

- [ ] **Step 5: Commit**

```bash
git add app/graph app/prompts.py tests
git commit -m "feat(ch08): confirm_write interrupt and fixed ticket replies for agent-initiated tickets"
```

---

### Task 9: SSE 预览事件、`/chat/resume` 确认、作废审计

**Files:**
- Modify: `app/api/chat.py`、`app/schemas.py`（`ResumeRequest`）
- Test: `tests/test_resume_api.py`

**Interfaces:**
- Consumes: 任务 8 的 interrupt 值和恢复值；任务 1 `audit.record`
- Produces:
  - SSE `ticket_preview` `{"call_id", "ticket_type", "description"}`
  - `ResumeRequest`：`order_id: OrderId | None = None`、`ticket_confirm: bool | None = None`；`model_validator(mode="after")` 要求恰好一个非 None，否则 `ValueError("order_id 和 ticket_confirm 必须恰好提供一个")`（FastAPI 返回 422）。
  - 409 `{"code": "no_pending_confirmation", "message": "没有待确认的工单，请重新提问"}`
  - `ChatTurn.resume_value: Any = None`（替换 `resume_order_id`）

`stream_graph` 的 `__interrupt__` 分支：

```python
value = chunk["__interrupt__"][0].value
if value.get("type") == "ticket_confirm":
    yield sse("ticket_preview", {k: value[k] for k in ("call_id", "ticket_type", "description")})
else:
    yield sse("order_picker", {"orders": value["orders"]})
interrupted = True
```

`_pending(state, kind)` 取类型为 `kind` 的 interrupt 值。`prepare_resume`：`order_id` 给了 → 需要 `order_picker`（原逻辑）；`ticket_confirm` 给了 → 需要 `ticket_confirm`，否则 409 `no_pending_confirmation`；`resume_value = {"confirmed": req.ticket_confirm}`。

`prepare_chat_turn`：取得锁之后、yield 之前，若 `req.session_id` 不为 None：

```python
pending = _pending(await graph.aget_state(thread_config(conversation_id)), "ticket_confirm")
if pending is not None:
    await audit.record(AuditRecord(
        conversation_id=conversation_id, tool_call_id=pending["call_id"], tool_name="create_ticket",
        tool_source="builtin", mcp_server=None,
        arguments={"description": pending["description"], "ticket_type": pending["ticket_type"]},
        result_summary=None, status="权限拒绝", error_message="用户未确认，已被新消息取代",
        retry_count=0, duration_ms=None))
```

- [ ] **Step 1: 写失败的测试**

`tests/test_resume_api.py` 新增（`start_ticket` 辅助函数：`use_resolver({"ticket_request": True})`、`use_intent("售后")`、脚本 `tools(TICKET_CALL)`）：

1. 发起后 SSE 为 `["session", "understood", "ticket_preview", "done"]`，预览数据正确，`finish_reason == "interrupted"`。
2. `/chat/resume` `ticket_confirm: true` → 200；事件中有 `token`，拼起来 == 工单提示；`done.finish_reason == "stop"`；`tickets` 一条。
3. `ticket_confirm: false` → 回复 == `TICKET_CANCELLED_REPLY`；`tickets` 无行。
4. 两个字段都给 / 都不给 → 422。
5. 待确认的是工单，却传 `order_id` → 409 `no_pending_selection`；待选的是订单，却传 `ticket_confirm` → 409 `no_pending_confirmation`。
6. Review Focus 4：确认成功后再调一次 `ticket_confirm: true` → 409 `no_pending_confirmation`；`tickets` 仍一条。
7. Review Focus 4：发起后用户发新消息（`use_intent("闲聊")`）→ 审计有一条 `create_ticket` `权限拒绝`、`用户未确认，已被新消息取代`；随后再调 `ticket_confirm: true` → 409；`tickets` 无行。
8. 原订单选择器测试（`order_id`）全部仍通过。

- [ ] **Step 2: 运行，确认失败** — `uv run pytest tests/test_resume_api.py -q` → FAIL

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行测试** — `uv run pytest -q` → 全部通过

- [ ] **Step 5: Commit**

```bash
git add app/api/chat.py app/schemas.py tests/test_resume_api.py
git commit -m "feat(ch08): ticket_preview SSE event and ticket confirmation via /chat/resume"
```

---

### Task 10: 工单 Prompt 评估集（非可单测，用评估集验证）

**Files:**
- Create: `evals/ticket_samples.jsonl`、`evals/run_ticket_eval.py`
- Test: `tests/test_ticket_samples.py`（只校验样例格式和判分函数，不调上游）

**Interfaces:**
- Consumes: `render_agent_system`、`build_agent_prompt`（或与 `agent_model` 相同的拼装函数）、`turn_toolset`、`builtin_toolset`、`get_chat_model`
- Produces: `evals/run_ticket_eval.py`：读样例，按生产拼装方式构造 Agent 第 1 次调用（`ticket_request=True`，绑定 `turn_toolset(...)` 的工具），只做这 1 次调用，判分，打印每条结果和汇总；全部通过才退出码 0。

样例格式（12 条）：

```json
{"id": "t01", "history": [], "user": "帮我建个工单", "expect": "ask"}
{"id": "t02", "history": [], "user": "帮我建个工单，蓝牙耳机左耳没声音，买了一周", "expect": "call", "ticket_type": "售后", "must_include": ["耳机", "左耳"]}
{"id": "t03", "history": [], "user": "快递把箱子摔坏了，商品也裂了，我要投诉，给我建个工单", "expect": "call", "ticket_type": "投诉", "must_include": ["摔坏"]}
{"id": "t04", "history": [["user", "帮我建个工单"], ["assistant", "好的，请描述一下您遇到的问题。"]], "user": "扫地机器人充不进电", "expect": "call", "ticket_type": "售后", "must_include": ["充"]}
```

另外 8 条覆盖：只说"提个工单"（ask）；"我要建工单，我的订单有问题"（问题不具体，ask）；带订单号和具体问题（call，`must_include` 含订单号）；咨询类（"想咨询发票怎么开，帮我建个工单跟进"→ call，`咨询`）；生气但问题具体（call，`投诉`）；补充描述在历史第 3 轮（call）；描述里含数字金额（call，检查无编造数字）；用户只说"建工单，快点"（ask）。

判分：
- `expect == "ask"`：没有 `create_ticket` 调用，回复文字非空。
- `expect == "call"`：恰好一个 `create_ticket` 调用；`ticket_type` 相等；`must_include` 每项都在 `description` 中；`description` 中 4 位以上数字串都出现在用户原话或历史中；`description` 不等于"用户要求建工单"这类空话（长度 ≥ 4 且不含"要求建工单"）。

- [ ] **Step 1: 写样例格式和判分函数的测试**（`tests/test_ticket_samples.py`：12 条、id 唯一、`expect` 取值合法、call 类必有 `ticket_type`；判分函数对构造的 AIMessage 给出预期结果，含"编造数字"和"空话描述"两个反例）

- [ ] **Step 2: 运行，确认失败** — `uv run pytest tests/test_ticket_samples.py -q` → FAIL

- [ ] **Step 3: 实现样例和脚本** — `uv run pytest tests/test_ticket_samples.py -q` → PASS

- [ ] **Step 4: 真实上游评估（Claude 执行）**

Run: `uv run python evals/run_ticket_eval.py`
Expected: 12/12，退出码 0。不通过时只改 `## 建工单` 一节的 Prompt 文字，重跑；每次翻车记 dev-notes。

- [ ] **Step 5: Commit**

```bash
git add evals/ticket_samples.jsonl evals/run_ticket_eval.py tests/test_ticket_samples.py app/prompts.py
git commit -m "eval(ch08): ticket prompt evaluation set"
```

---

### Task 11: 前端工单预览卡片（Vibe Coding）

**Files:**
- Modify: `app/web/index.html`

由 Claude 把效果描述交给 Codex，不走 TDD 和 code review：

- 收到 SSE `ticket_preview` 时，在当前回复气泡下方渲染一张卡片：标题"请确认工单信息"，两行"工单类型：…""问题描述：…"，按钮「确认提交」「取消」。
- 点击任一按钮：两个按钮置灰不可点；调用 `POST /chat/resume`，body `{"session_id", "user_id", "ticket_confirm": true|false}`；按现有订单选择器恢复时的方式读取 SSE 流，把回复渲染成新的助手消息。
- 409 时在卡片下显示"该工单已失效，请重新发起"；其他错误沿用现有错误提示。
- 样式与订单选择器卡片一致。

- [ ] **Step 1: Codex 修改 `index.html`**
- [ ] **Step 2: Claude 在 Chrome 中验收**：说"帮我建个工单"→ 追问 → 补充描述 → 卡片出现 → 点确认 → 回复带工单号；再走一次点取消 → 回复为取消话术；刷新后点旧卡片不会出现（回载不恢复卡片，ch07 已知限制）。
- [ ] **Step 3: Commit**

```bash
git add app/web/index.html
git commit -m "feat(ch08): ticket preview card on chat page"
```

---

### Task 12: 验收脚本、演示素材、CLAUDE.md、回归

**Files:**
- Create: `scripts/demo8.sh`、`scripts/demo8_chat.py`（发消息、读 SSE、调 resume 的小工具，参照 `scripts/demo7_chat.py`）、`scripts/demo8_assets/query_member_points.py`、`scripts/demo8_assets/query_repair_progress.py`
- Modify: `CLAUDE.md`、`scripts/demo5.sh`、`scripts/demo6.sh`、`scripts/demo7.sh`（物流问题需要 logistics Server：脚本开头检查 8101 端口可连，否则提示先启动）
- Test: `tests/test_demo8_files.py`（脚本语法 `bash -n`、素材文件可导入且登记名正确、不含 `set -u` 下 EXIT trap 的 `$?` 写法问题，参照 `tests/test_demo7_files.py`）

`query_member_points.py`（内置演示工具）：

```python
"""演示用：查询会员积分。复制到 app/tools/builtin/ 后重启服务即可使用。"""

import random

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.tools.registry import register


class QueryMemberPointsArgs(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$", description="会员手机号，11 位")


@register()
@tool("query_member_points", args_schema=QueryMemberPointsArgs)
async def query_member_points(phone: str) -> dict:
    """按会员手机号查询当前积分和即将过期的积分。"""
    rng = random.Random(f"points:{phone}")
    return {"phone": phone, "points": rng.randint(100, 5000), "expiring": rng.randint(0, 300)}
```

`query_repair_progress.py`（MCP 演示工具，复制到 `mcp_servers/aftersales/tools/`）：定义模块级 `async def query_repair_progress(order_id: OrderId) -> dict`（返回 `found`、`order_id`、`repair_no`、`status_code`（`RECEIVED`/`DIAGNOSING`/`REPAIRING`/`SHIPPED_BACK`）、`updated_at`），以及 `register(mcp)`。

`demo8.sh <日志目录>` 按 spec 第 11 节的 6 项实现。要点：
- 开头检查端口 8000、8101、8102 空闲；`trap` 中停掉全部子进程，还原 `config/tools.json`（先 `cp` 备份）、删除复制进去的两个素材文件。
- 每项用 `demo8_chat.py` 发消息并把 SSE 事件写进 `$DIR/itemN.jsonl`；用 `docker exec aftersales-mysql mysql -N ...` 查 `tool_audit_logs` 和 `tickets`，只看本次开始后的行（记下开始时的 `MAX(id)`）。
- 第 3 项断言客服服务 PID 在重启 aftersales 前后相同。
- 第 6 项读超时：`MOCK_DELAY_SECONDS=5` 重启 logistics，策略 `overrides.query_logistics = {"timeout_seconds": 2}`；断言审计 `超时`、`retry_count=2`、`duration_ms` 非空。写超时：策略 `overrides.create_ticket = {"timeout_seconds": 0.001}`；断言 `超时`、`retry_count=0`，回复为超时话术。
- 每项打印 `✅ [N/6] ...` 或 `❌ ...` 并退出码 1。

CLAUDE.md 按 spec 第 12 节改。

- [ ] **Step 1: 写 `tests/test_demo8_files.py` 并确认失败**
- [ ] **Step 2: 实现脚本和素材** — `uv run pytest tests/test_demo8_files.py -q` → PASS；`uv run pytest -q` → 全部通过
- [ ] **Step 3: 验收（Claude 执行）**

```bash
bash scripts/demo8.sh /tmp/ch08-demo
```

Expected: 6/6 ✅。

- [ ] **Step 4: 回归（Claude 执行，两个 MCP Server 在 8101、8102 运行）**

```bash
uv run python evals/run_tool_selection_eval.py
uv run python evals/run_intent_eval.py
uv run python evals/run_multiturn_eval.py
uv run python evals/run_ticket_eval.py
bash scripts/demo6.sh /tmp/ch08-regress-6.log    # 先启动服务
bash scripts/demo7.sh /tmp/ch08-regress-7
```

Expected: 全部退出码 0。

- [ ] **Step 5: Commit**

```bash
git add scripts CLAUDE.md tests/test_demo8_files.py
git commit -m "docs(ch08): demo8 acceptance script, demo assets and CLAUDE.md update"
```

---

## 自查记录

- spec 覆盖：1（目标）→ 任务 2–9；3 数据 → 任务 1；4.1–4.2 → 任务 2；4.3 → 任务 4；4.4 → 任务 3；4.5–4.6 → 任务 6；5.1 → 任务 7；5.2–5.4、5.6 → 任务 8、10；5.5 → 任务 9；5.7 → 任务 11；6 → 任务 5；7 日志 → 任务 3、6、8；8 错误处理 → 任务 3、4、6；9 测试 → 各任务；10 评估 → 任务 7、10、12；11 → 任务 12；12 CLAUDE.md → 任务 12。
- 类型一致：`ToolEntry`、`Toolset`、`approvals: Mapping[str, str]`、`APPROVED`、`AuditRecord`、`turn_toolset`、`TICKET_TOOL`、`ensure_toolset` 在定义处和使用处同名。
- Review Focus 5 条各有测试：1 → 任务 1；2 → 任务 8 第 8 条；3 → 任务 3 第 12 条；4 → 任务 9 第 6、7 条；5 → 任务 6 第 9 条。
