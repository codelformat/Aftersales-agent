# ch07 会话上下文管理实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目的执行方式（CLAUDE.md 规定，优先于技能默认值）：** Claude 把每个任务整段（含 Global Constraints 和 Review Focus 中归属该任务的测试）交给 Codex 编写代码；Claude 检查 diff、跑测试、核对 spec，有问题把具体问题反馈给 Codex 重做；通过后 Claude 提交、推送，并当场追记 `dev-notes/ch07.md`。Task 12（聊天页）按 CLAUDE.md 的例外条款走 Vibe Coding。Task 10（摘要 Prompt）和 Task 13（口径校准）用评估集跑真实上游验证，替代 TDD。

**Goal:** 把 ch01 的简单裁剪升级为三层上下文管理：层 1 原文、层 2 规则截短、层 3 后台异步分段摘要；预算从模型窗口倒推；上下文按固定顺序拼装；日志可观测；聊天页加会话侧栏。

**Architecture:** 新包 `app/context/`（`budget`、`layers`、`assemble`、`maintain`、`summarizer`）放全部上下文逻辑。`start_turn` 从 `conversations` 读两个锚点和梗概投影；`resolve_reference` 和 `agent_model` 从 State 完整历史现场构造精简视图，不写回 State；`finalize` 写库后给消息设 `msg-<主键>` id，再做层 1 降级和摘要触发；摘要由进程内 `SummaryRunner` 用 `asyncio` 后台执行，只追加段落、锚点只增。

**Tech Stack:** Python 3.12、FastAPI 0.142、LangGraph 1.2.14（`add_messages`、`AsyncSqliteSaver`）、langchain-core 1.6.6（`trim_messages`）、SQLAlchemy 2.1（异步）、MySQL 8。

**Spec:** `docs/superpowers/specs/2026-10-08-ch07-context-management-design.md`

## Global Constraints

- 技术选型定死：FastAPI、SQLAlchemy、LangChain、LangGraph、Milvus、Langfuse。走不通时停下来问用户。
- 不新增依赖。`.env` 不新增变量（新环境变量只作 `Settings` 字段，带默认值）。不硬编码密钥和地址。
- `db/schema.sql`、`db/schema_ch03.sql`、`db/schema_ch04.sql`、`db/schema_ch07.sql` 是用户 DDL，逐字保存，不改。ORM 只映射，不 `create_all`。
- 环境变量与默认值：`MODEL_CONTEXT_WINDOW=128000`、`MAX_OUTPUT_TOKENS=8192`、`MAX_USER_INPUT_TOKENS=1000`、`MAX_AGENT_STEPS=4`、`TOOL_RESULT_MAX_TOKENS=750`、`RERANK_TOP_K=10`。
- 常量（`app/config.py`）：`SYSTEM_RESERVE_TOKENS = 1800`、`EVIDENCE_ITEM_TOKENS = 250`、`SUMMARY_RESERVE_TOKENS = 500`、`SAFETY_MARGIN_RATIO = 0.05`、`STEP_OVERHEAD_TOKENS = 100`、`KEEP_TURNS = 30`、`TURN_TOKENS = 800`（Task 13 实测后更新）、`LAYER1_RATIO = 0.7`、`LAYER2_RATIO = 0.3`、`LAYER1_LOW_WATER = 0.6`、`LAYER2_REPLY_CHARS = 60`、`LAYER2_TOOL_MAX_CHARS = 80`、`SUMMARY_TIMEOUT_SECONDS = 30`、`SUMMARY_MAX_CHARS = 300`、`SUMMARY_TOOL_RESULT_CHARS = 200`。
- 演示配置 `MODEL_CONTEXT_WINDOW=18000 MAX_OUTPUT_TOKENS=2000 MAX_USER_INPUT_TOKENS=2000 MAX_AGENT_STEPS=3 TOOL_RESULT_MAX_TOKENS=1200 RERANK_TOP_K=5` 必须得到 history 5650、layer1 3954、layer2 1695。
- State 中有数据库行的消息 id 固定为 `msg-<主键>`。工具调用和工具结果消息不写 `messages` 表。
- 锚点只增不减。梗概段只追加，不重写。
- System Prompt 每轮相同：不含日期、证据、订单段、任务段、梗概。梗概不进任何 `SystemMessage`。`TOOL_ROUND_CLOSING` 保持现状。
- 日志关键字固定：`budget`、`上下文预算不足`、`system_reserve_exceeded`、`model_ctx`、`history_ctx`、`context_usage`、`层1 降级`、`summary trigger`、`summary skip`、`summary start`、`summary done`、`summary fail`、`summary cancel`、`context_maintain_failed`。
- 摘要模型用 `build_extract_model`（关闭思考）。上游重试只用 SDK 的 `max_retries`。自写重试一律用 `app.retry.retry_async`（本章不需要新的重试）。
- 测试不访问真实上游、嵌入、重排和生产 Milvus 集合。LLM runnable 用 `RunnableLambda`；聊天模型用 `tests/fakes.py` 的 `ScriptedChatModel`；数据库测试用 fixture `db`。
- ch04 评估脚本（`evals/run_rag_eval.py`）仍用 `EVIDENCE_TOP_N=10`、`CH04_CHAT_TOOLS`、`chat_prompt`，不改。
- 注释、文档、日志文字用中文，按 ASD-STE100 原则（短句、主动语态、一词一义）。
- 代码风格跟随周围代码：注释密度低，命名和现有模块一致。

## Review Focus

1. **摘要在下一轮进行中完成**（用户连续快速发消息）：`finalize` 用 State 中的旧锚点会把已摘要的消息再次算进层 2，重复摘要同一批事实。期望：`maintain` 开头从数据库重新读锚点。→ Task 9 `test_maintain_reads_fresh_anchors`。
2. **单轮超大**（一轮的工具结果就超过层 1 水位）：`trim_messages` 返回空列表。期望：整个层 1 降级，`layer1_from` 取层 1 最后一个 DB id，下一轮 prompt 合法（层 1 不以 `ToolMessage` 开头）。→ Task 9 `test_degrade_all_when_single_turn_exceeds`，Task 5 `test_layer_boundary_never_splits_turn`。
3. **本轮失败**（Agent 抛异常，`error` 事件）：不写 `messages` 表，锚点不动，不触发摘要。→ Task 9 `test_failed_turn_does_not_maintain`。
4. **工单提示写库失败**：State 中的提示消息没有 `msg-` id，继承前一条的有效 id，分层不出错。→ Task 4 `test_ticket_note_without_db_id_inherits_previous`，Task 5 `test_effective_ids_inherit_previous`。
5. **旧 checkpoint 消息没有 `msg-` id**（升级前的会话）：有效 id 按 0 处理，锚点为空时全部在层 1，不抛异常。→ Task 5 `test_messages_without_db_id_count_as_zero`。

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `db/schema_ch07.sql` | 新建（用户 DDL 原样） | 新列和 `conversation_summaries` |
| `docker-compose.yml`、`scripts/reset_db.sh` | 改 | 挂载 `05-schema-ch07.sql`；检查新表 |
| `app/db/models.py` | 改 | `Conversation` 新列；`ConversationSummary` |
| `tests/conftest.py` | 改 | 测试库应用 ch07 DDL；摘要器和 runner 隔离；`use_summarizer`、`use_budget` |
| `app/config.py` | 改 | 新常量和 6 个 `Settings` 字段；删 `TOKEN_BUDGET`、`AGENT_MAX_STEPS`、`TOOL_RESULT_MAX_CHARS`、`RESOLVE_HISTORY_MESSAGES` |
| `app/context.py` → `app/context/__init__.py` | 移动 | `count_tokens`；删 `build_history`、`BudgetExceeded` |
| `app/context/budget.py` | 新建 | `ContextBudget`、`compute_budget`、`get_budget`/`set_budget`、`startup_check` |
| `app/context/layers.py` | 新建 | 有效 id、`split_layers`、`render_layer2`、`history_lines` |
| `app/context/assemble.py` | 新建 | `build_agent_prompt`、`log_model_ctx` |
| `app/context/summarizer.py` | 新建 | `SummaryRunner`、`run_summary`、`batch_text`、`check_summary`、`projection` |
| `app/context/maintain.py` | 新建 | `maintain`：层 1 降级 + 摘要触发 |
| `app/repositories/conversations.py` | 改 | `get_context`、`advance_layer1`、`set_summary`、`list_for_user` |
| `app/repositories/summaries.py` | 新建 | `list_for_conversation`、`append` |
| `app/repositories/messages.py` | 改 | `add_turn` 返回写入的行 |
| `app/services/history.py` | 改 | `msg_id`、`db_id`、`final_rows`；删 `turn_messages_rows` |
| `app/services/understanding.py` | 改 | 删 `history_text` |
| `app/prompts.py` | 改 | System 去掉可变段；`render_reference`；`SUMMARY_SYSTEM_PROMPT`、`summary_prompt` |
| `app/llm.py` | 改 | `get_summarizer` |
| `app/graph/state.py` | 改 | 本轮字段 `summary`、`summary_upto`、`layer1_from` |
| `app/graph/nodes/turn.py` | 改 | `start_turn` 读锚点；`resolve_reference` 用 `history_lines` 并打 `history_ctx` |
| `app/graph/nodes/agent.py` | 改 | 用 `build_agent_prompt`；`max_agent_steps`；`measure_system_tokens` |
| `app/graph/nodes/finalize.py` | 改 | 只写两行、设 id、调 `maintain` |
| `app/graph/nodes/knowledge.py`、`aftersales.py` | 改 | 传 `top_n=rerank_top_k` |
| `app/knowledge/retrieval.py` | 改 | `retrieve`、`retrieve_multi`、`_rerank_hits` 加 `top_n` |
| `app/tools/executor.py` | 改 | 截断长度来自 `tool_result_max_tokens` |
| `app/api/chat.py` | 改 | 预检改为 `MAX_USER_INPUT_TOKENS` |
| `app/api/tickets.py`、`app/api/refunds.py` | 改 | 提示消息设 `msg-` id |
| `app/api/conversations.py` | 新建 | 两个只读接口 |
| `app/main.py` | 改 | `log/app.log`、启动自检、注册路由、退出时取消摘要任务 |
| `app/web/index.html` | 改（Codex，Vibe Coding） | 会话侧栏 |
| `evals/run_multiturn_eval.py` | 改 | 用 `history_lines` |
| `evals/summary_samples.jsonl`、`evals/run_summary_eval.py` | 新建 | 摘要 Prompt 评估 |
| `evals/run_token_calibration.py` | 新建 | 字符/token 校准 |
| `scripts/demo7.sh`、`scripts/demo7_chat.py`、`scripts/demo7_dialog.json` | 新建 | 验收 |
| `.gitignore` | 改 | `log/` |
| `CLAUDE.md` | 改 | ch07 状态、约束、命令 |

---

### Task 1: DDL、ORM 和测试库

**Files:**
- Create: `db/schema_ch07.sql`（Claude 执行：`git mv` 不适用，`mv ch07.sql db/schema_ch07.sql`，内容不改）
- Modify: `docker-compose.yml`、`scripts/reset_db.sh`、`app/db/models.py`、`tests/conftest.py`
- Test: `tests/test_db_models.py`

**Interfaces:**
- Produces: `Conversation.summary: str | None`、`Conversation.summary_upto_msg_id: int | None`、`Conversation.layer1_from_msg_id: int | None`；`ConversationSummary(id, conversation_id, seq, from_msg_id, upto_msg_id, content, created_at)`。

- [ ] **Step 1（Claude）：保存 DDL 并应用到开发库**

```bash
mv ch07.sql db/schema_ch07.sql
docker exec -i aftersales-mysql mysql --default-character-set=utf8mb4 -uaftersales -paftersales aftersales < db/schema_ch07.sql
rm -f data/checkpoints.sqlite data/checkpoints.sqlite-wal data/checkpoints.sqlite-shm
docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SHOW TABLES LIKE 'conversation_summaries'"
```

Expected：最后一条输出 `conversation_summaries`。删除 checkpoint 的原因：旧 State 消息没有 `msg-` id。

- [ ] **Step 2：写失败测试**（追加到 `tests/test_db_models.py`）

```python
async def test_conversation_context_columns_and_summaries(db):
    from app.db.models import Conversation, ConversationSummary
    async with db() as s:
        c = Conversation(user_id="u1")
        s.add(c)
        await s.flush()
        s.add(ConversationSummary(conversation_id=c.id, seq=1, from_msg_id=1, upto_msg_id=4, content="用户报订单 1001"))
        await s.commit()
        await s.refresh(c)
        assert (c.summary, c.summary_upto_msg_id, c.layer1_from_msg_id) == (None, None, None)
        rows = (await s.scalars(select(ConversationSummary))).all()
        assert [(r.seq, r.from_msg_id, r.upto_msg_id, r.content) for r in rows] == [(1, 1, 4, "用户报订单 1001")]
```

- [ ] **Step 3：运行，确认失败**

Run: `uv run pytest tests/test_db_models.py -q`
Expected: FAIL（`ImportError: ConversationSummary` 或缺列）。

- [ ] **Step 4：实现**

`app/db/models.py`：

```python
class Conversation(Base):
    ...
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_upto_msg_id: Mapped[int | None] = mapped_column(ID, nullable=True)
    layer1_from_msg_id: Mapped[int | None] = mapped_column(ID, nullable=True)
    ...


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ID)
    seq: Mapped[int] = mapped_column(Integer)
    from_msg_id: Mapped[int] = mapped_column(ID)
    upto_msg_id: Mapped[int] = mapped_column(ID)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
```

`tests/conftest.py`：
- `_reset_schema` 的 DROP 列表最前面加 `"conversation_summaries"`；DDL 文件列表改为 `("schema.sql", "schema_ch03.sql", "schema_ch04.sql", "seed.sql", "schema_ch07.sql")`。
- `_clear_runtime_tables` 的 DELETE 列表最前面加 `"conversation_summaries"`。

`docker-compose.yml` 的 mysql volumes 在 seed 之后加：

```yaml
      - ./db/schema_ch07.sql:/docker-entrypoint-initdb.d/05-schema-ch07.sql:ro
```

`scripts/reset_db.sh`：`for t in low_confidence_questions faith_cases conversation_summaries; do`。

- [ ] **Step 5：运行全量测试**

Run: `uv run pytest -q`
Expected: 全部 PASS（之前 548 条 + 新增 1 条）。

- [ ] **Step 6：提交**

```bash
git add db/schema_ch07.sql docker-compose.yml scripts/reset_db.sh app/db/models.py tests/conftest.py tests/test_db_models.py
git commit -m "feat(ch07): conversation context columns and summaries table"
```

---

### Task 2: 配置、预算、启动自检、日志文件

**Files:**
- Move: `app/context.py` → `app/context/__init__.py`（内容先不变）
- Create: `app/context/budget.py`
- Modify: `app/config.py`、`app/main.py`、`app/graph/nodes/agent.py`（只加 `measure_system_tokens`）、`.gitignore`
- Test: `tests/test_budget.py`、`tests/test_main.py`

**Interfaces:**
- Produces:
  - `Settings.model_context_window: int = 128000`、`max_output_tokens: int = 8192`、`max_user_input_tokens: int = 1000`、`max_agent_steps: int = 4`、`tool_result_max_tokens: int = 750`、`rerank_top_k: int = 10`。
  - `ContextBudget`（frozen dataclass：`window, output, margin, system, evidence, summary, peak, avail, history, layer1, layer2`，全为 `int`）。
  - `compute_budget(*, window: int, max_output: int, max_user_input: int, max_agent_steps: int, tool_result_max: int, top_k: int) -> ContextBudget`
  - `budget_from_settings(settings: Settings) -> ContextBudget`、`get_budget() -> ContextBudget`、`set_budget(b: ContextBudget | None) -> None`
  - `startup_check(budget: ContextBudget, measured_system: int) -> None`
  - `app.graph.nodes.agent.measure_system_tokens() -> int`
  - `app.main.setup_file_logging(path: Path = LOG_PATH) -> None`

- [ ] **Step 1：写失败测试** `tests/test_budget.py`

```python
import logging

from app.config import Settings
from app.context.budget import budget_from_settings, compute_budget, startup_check

DEMO = dict(window=18000, max_output=2000, max_user_input=2000, max_agent_steps=3, tool_result_max=1200, top_k=5)
DEFAULTS = dict(max_output=8192, max_user_input=1000, max_agent_steps=4, tool_result_max=750, top_k=10)


def test_demo_config_gives_5650():
    b = compute_budget(**DEMO)
    assert (b.margin, b.evidence, b.peak) == (900, 1250, 5900)
    assert (b.history, b.layer1, b.layer2) == (5650, 3954, 1695)


def test_window_only_change_gives_zero():
    b = compute_budget(window=18000, **DEFAULTS)
    assert b.avail == -292
    assert (b.history, b.layer1, b.layer2) == (0, 0, 0)


def test_default_window_is_capped_by_keep_turns(monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "TURN_TOKENS", 800)
    b = compute_budget(window=128000, **DEFAULTS)
    assert b.avail == 104208
    assert (b.history, b.layer1, b.layer2) == (24000, 16800, 7200)


def test_budget_from_settings_reads_env(monkeypatch):
    for k, v in {"MODEL_CONTEXT_WINDOW": "18000", "MAX_OUTPUT_TOKENS": "2000", "MAX_USER_INPUT_TOKENS": "2000",
                 "MAX_AGENT_STEPS": "3", "TOOL_RESULT_MAX_TOKENS": "1200", "RERANK_TOP_K": "5"}.items():
        monkeypatch.setenv(k, v)
    assert budget_from_settings(Settings()).history == 5650


def test_startup_check_warns_when_budget_is_short(caplog):
    caplog.set_level(logging.INFO)
    startup_check(compute_budget(window=18000, **DEFAULTS), measured_system=1000)
    assert "budget window=18000" in caplog.text
    assert "上下文预算不足" in caplog.text


def test_startup_check_warns_when_system_reserve_exceeded(caplog):
    caplog.set_level(logging.INFO)
    startup_check(compute_budget(**DEMO), measured_system=2500)
    assert "system_reserve_exceeded measured=2500 reserve=1800" in caplog.text
    assert "上下文预算不足" not in caplog.text
```

追加到 `tests/test_main.py`：

```python
@pytest.fixture(autouse=True)
def _tmp_log(monkeypatch, tmp_path):
    """lifespan 的日志写到临时文件，测试结束移除新增的 handler。"""
    import logging
    monkeypatch.setattr(main, "LOG_PATH", tmp_path / "app.log")
    before = list(logging.getLogger().handlers)
    yield
    for h in logging.getLogger().handlers[:]:
        if h not in before:
            logging.getLogger().removeHandler(h)
            h.close()


async def test_lifespan_logs_budget(caplog, tmp_path):
    import logging
    caplog.set_level(logging.INFO)
    async with main.app.router.lifespan_context(main.app):
        pass
    assert "budget window=" in caplog.text
    assert "budget window=" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_measure_system_tokens_is_positive():
    from app.graph.nodes.agent import measure_system_tokens
    assert measure_system_tokens() > 500
```

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_budget.py tests/test_main.py -q`
Expected: FAIL（`ModuleNotFoundError: app.context.budget`）。

- [ ] **Step 3：实现**

`git mv app/context.py app/context/__init__.py`。

`app/config.py` 加常量（Global Constraints 中的值）和 `Settings` 字段：

```python
    model_context_window: int = 128000
    max_output_tokens: int = 8192
    max_user_input_tokens: int = 1000
    max_agent_steps: int = 4
    tool_result_max_tokens: int = 750
    rerank_top_k: int = 10
```

`app/context/budget.py`：

```python
"""从模型窗口倒推历史预算。"""

import logging
from dataclasses import dataclass

import app.config as config
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextBudget:
    window: int
    output: int
    margin: int
    system: int
    evidence: int
    summary: int
    peak: int
    avail: int
    history: int
    layer1: int
    layer2: int


def compute_budget(*, window: int, max_output: int, max_user_input: int, max_agent_steps: int,
                   tool_result_max: int, top_k: int) -> ContextBudget:
    margin = int(window * config.SAFETY_MARGIN_RATIO)
    evidence = top_k * config.EVIDENCE_ITEM_TOKENS
    # 单轮峰值：用户输入 + 每步工具结果和工具调用消息。
    peak = max_user_input + max_agent_steps * (tool_result_max + config.STEP_OVERHEAD_TOKENS)
    avail = (window - max_output - margin - config.SYSTEM_RESERVE_TOKENS - evidence
             - config.SUMMARY_RESERVE_TOKENS - peak)
    history = max(0, min(config.KEEP_TURNS * config.TURN_TOKENS, avail))
    return ContextBudget(
        window=window, output=max_output, margin=margin, system=config.SYSTEM_RESERVE_TOKENS,
        evidence=evidence, summary=config.SUMMARY_RESERVE_TOKENS, peak=peak, avail=avail, history=history,
        layer1=int(history * config.LAYER1_RATIO), layer2=int(history * config.LAYER2_RATIO))


def budget_from_settings(settings: Settings) -> ContextBudget:
    return compute_budget(
        window=settings.model_context_window, max_output=settings.max_output_tokens,
        max_user_input=settings.max_user_input_tokens, max_agent_steps=settings.max_agent_steps,
        tool_result_max=settings.tool_result_max_tokens, top_k=settings.rerank_top_k)


_budget: ContextBudget | None = None


def get_budget() -> ContextBudget:
    global _budget
    if _budget is None:
        _budget = budget_from_settings(get_settings())
    return _budget


def set_budget(budget: ContextBudget | None) -> None:
    global _budget
    _budget = budget


def startup_check(budget: ContextBudget, measured_system: int) -> None:
    b = budget
    logger.info(
        "budget window=%s output=%s margin=%s system=%s evidence=%s summary=%s peak=%s avail=%s "
        "history=%s layer1=%s layer2=%s",
        b.window, b.output, b.margin, b.system, b.evidence, b.summary, b.peak, b.avail,
        b.history, b.layer1, b.layer2)
    if measured_system > b.system:
        logger.warning("system_reserve_exceeded measured=%s reserve=%s", measured_system, b.system)
    if b.history <= 0 or b.layer1 < config.TURN_TOKENS:
        logger.warning("上下文预算不足 history=%s layer1=%s turn_tokens=%s", b.history, b.layer1, config.TURN_TOKENS)
```

`app/graph/nodes/agent.py` 加：

```python
import json
import math

from langchain_core.utils.function_calling import convert_to_openai_tool

from app.config import CHARS_PER_TOKEN


def measure_system_tokens() -> int:
    """System Prompt 加全部 Agent 工具定义的估算 token。"""
    tools = get_registry().tools_for_model((*AGENT_TOOLS, REFUND_FORM_TOOL))
    schema = json.dumps([convert_to_openai_tool(t) for t in tools], ensure_ascii=False)
    return count_tokens([SystemMessage(render_agent_system(date.today()))]) + math.ceil(len(schema) / CHARS_PER_TOKEN)
```

（Task 6 把 `render_agent_system` 改成无参后，这里同步改为 `render_agent_system()`。）

`app/main.py`：

```python
from pathlib import Path

from app.context.budget import get_budget, startup_check
from app.graph.nodes.agent import measure_system_tokens

LOG_PATH = Path(__file__).resolve().parent.parent / "log" / "app.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_file_logging(path: Path | None = None) -> None:
    """给根 logger 加文件输出。重复调用不重复添加。"""
    path = path or LOG_PATH
    root = logging.getLogger()
    if any(isinstance(h, logging.FileHandler) and Path(h.baseFilename) == path for h in root.handlers):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_file_logging(LOG_PATH)
    startup_check(get_budget(), measure_system_tokens())
    ...（原有内容不变）
```

`logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)`。`.gitignore` 加 `log/`。

- [ ] **Step 4：运行**

Run: `uv run pytest tests/test_budget.py tests/test_main.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add app/context app/config.py app/main.py app/graph/nodes/agent.py .gitignore tests/test_budget.py tests/test_main.py
git commit -m "feat(ch07): context budget from model window and startup self-check"
```

---

### Task 3: 运行时限值接入（步数、截断、证据条数、预检）

**Files:**
- Modify: `app/graph/nodes/agent.py`、`app/tools/executor.py`、`app/knowledge/retrieval.py`、`app/graph/nodes/knowledge.py`、`app/graph/nodes/aftersales.py`、`app/api/chat.py`、`app/config.py`（删 `AGENT_MAX_STEPS`、`TOOL_RESULT_MAX_CHARS`）
- Test: `tests/test_executor.py`、`tests/test_graph_agent.py`、`tests/test_retrieval.py`、`tests/test_chat_api.py`、`tests/test_graph_nodes.py`（或 `test_graph_aftersales.py`）

**Interfaces:**
- Consumes: Task 2 的 `Settings` 字段。
- Produces:
  - `executor.tool_result_max_chars() -> int` = `int(get_settings().tool_result_max_tokens * CHARS_PER_TOKEN)`。
  - `retrieve(..., top_n: int = EVIDENCE_TOP_N)`、`retrieve_multi(queries, plan, *, min_score=..., top_n: int = EVIDENCE_TOP_N)`；`_rerank_hits(plan, hits, min_score, top_n)`。
  - `chat.get_input_token_limit() -> int`（依赖，测试可覆盖）。
  - Agent 停止条件 `steps >= get_settings().max_agent_steps`。

- [ ] **Step 1：写失败测试**

`tests/test_executor.py`：

```python
async def test_truncation_uses_tool_result_max_tokens(monkeypatch):
    from app.config import get_settings
    from app.tools import executor
    monkeypatch.setattr(executor, "get_settings", lambda: get_settings().model_copy(update={"tool_result_max_tokens": 10}))
    outcome = executor._make_outcome("c1", "query_order", ok=True, content="字" * 100)
    assert outcome.message.content == "字" * 20 + "…(结果过长，已截断)"
```

`tests/test_graph_agent.py`：把现有 `test_step_limit_sets_force_final` 中的 `monkeypatch.setattr(agent_mod, "AGENT_MAX_STEPS", 2)` 改为：

```python
    from app.config import get_settings
    monkeypatch.setattr(agent_mod, "get_settings",
                        lambda: get_settings().model_copy(update={"max_agent_steps": 2}))
```

其余断言不变（`out["steps"] == 2 and out["force_final"] is True`）。`tests/test_graph.py` 的 `test_step_limit_forces_text_answer` 同样改为覆盖 `get_settings`（`max_agent_steps=1`）。

`tests/test_retrieval.py`：沿用该文件中现有 `retrieve_multi` 测试的 Milvus 和重排替换方式，加一条 `retrieve_multi(..., top_n=3)` 的测试，断言传给 `rerank_mod.rerank` 的第 3 个参数为 3。

`tests/test_graph.py`：

```python
async def test_retrieve_nodes_pass_rerank_top_k(db, memory_graph, use_intent, monkeypatch):
    from app.config import get_settings
    seen = {}

    async def fake(question, plan=None, top_n=None):
        seen["top_n"] = top_n
        return Retrieval(QueryPlan(standard_query=question), [], [])

    monkeypatch.setattr(knowledge_nodes, "retrieve", fake)
    monkeypatch.setattr(knowledge_nodes, "get_settings", lambda: get_settings().model_copy(update={"rerank_top_k": 5}))
    use_intent("商品咨询")
    cid = await new_cid(db)
    await run(memory_graph, cid, "X3 Pro 续航多久")
    assert seen["top_n"] == 5
```

`aftersales_nodes.retrieve_multi` 同样加一条（沿用 `test_aftersales_policy_only_skips_order` 的设置，`fake(queries, plan, top_n=None)` 记录 `top_n`）。注意：`kb()` 中的 `fake(question, plan=None)` 要加 `top_n=None` 参数，否则现有测试会报参数错误。

`tests/test_chat_api.py`：

```python
async def test_message_over_input_token_limit_is_422(client, db, locks):
    from app.api.chat import get_input_token_limit
    from app.main import app
    app.dependency_overrides[get_input_token_limit] = lambda: 10
    r = await client.post("/chat/stream", json={"user_id": "u1", "message": "字" * 30})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "budget_exceeded"
```

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_executor.py tests/test_graph_agent.py tests/test_retrieval.py tests/test_chat_api.py -q`
Expected: 新测试 FAIL。

- [ ] **Step 3：实现**

- `executor.py`：`from app.config import CHARS_PER_TOKEN, get_settings`；新增 `tool_result_max_chars()`；`_make_outcome` 用 `limit = tool_result_max_chars()` 替换 `TOOL_RESULT_MAX_CHARS`。
- `retrieval.py`：`retrieve` 中所有 `EVIDENCE_TOP_N` 换成参数 `top_n`；`_rerank_hits` 多一个 `top_n` 参数传给 `rerank_mod.rerank`；`retrieve_multi` 加 `top_n` 并传下去。默认值 `EVIDENCE_TOP_N`，ch04 评估行为不变。
- `knowledge.py`：`await retrieve(state["resolved_input"], plan=plan, top_n=get_settings().rerank_top_k)`；`aftersales.py`：`await retrieve_multi(state["queries"], plan, top_n=get_settings().rerank_top_k)`。
- `agent.py`：`if steps >= get_settings().max_agent_steps:`；import `get_settings`。
- `chat.py`：删 `get_token_budget`、`build_history`、`BudgetExceeded`、`render_agent_system`、`TOKEN_BUDGET` 的使用；新增

```python
def get_input_token_limit() -> int:
    return get_settings().max_user_input_tokens
```

  `prepare_chat_turn` 参数 `budget` 换成 `limit: Annotated[int, Depends(get_input_token_limit)]`，开头：

```python
    if count_tokens([HumanMessage(req.message)]) > limit:
        raise HTTPException(422, detail={"code": "budget_exceeded", "message": "消息过长，请缩短后重试"})
```

  删除 `if req.session_id is not None:` 里读 checkpoint 做预算检查的两行（`aget_state` + `check_budget`）。
- 删 `config.AGENT_MAX_STEPS`、`config.TOOL_RESULT_MAX_CHARS`。更新引用它们的测试（`tests/test_config.py` 等）和 `get_token_budget` 的旧测试（改为覆盖 `get_input_token_limit`）。

- [ ] **Step 4：运行全量**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add app tests
git commit -m "feat(ch07): runtime limits from settings and input token precheck"
```

---

### Task 4: `messages` 表只写两行，State 消息带 `msg-` id

**Files:**
- Modify: `app/repositories/messages.py`、`app/services/history.py`、`app/graph/nodes/finalize.py`、`app/api/tickets.py`、`app/api/refunds.py`
- Test: `tests/test_history.py`、`tests/test_graph.py`、`tests/test_chat_api.py`、`tests/test_repositories.py`、`tests/test_tickets_api.py`、`tests/test_refunds_api.py`

**Interfaces:**
- Produces:
  - `messages.add_turn(session, conversation_id, rows) -> list[Message]`（`flush` 后返回，主键已有值）。
  - `history.msg_id(n: int) -> str`（`f"msg-{n}"`）、`history.db_id(message: BaseMessage) -> int | None`（id 不是 `msg-<数字>` 时为 None）、`history.final_rows(user_input: str, reply: str) -> list[NewMessage]`。
  - `finalize` 返回的 `messages`：`[HumanMessage(user_input, id=msg-u), *工具消息（原 id）, 最终 AIMessage(id=msg-a)]`。

- [ ] **Step 1：写失败测试**

`tests/test_history.py`：

```python
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from app.services.history import db_id, final_rows, msg_id


def test_msg_id_round_trip():
    assert msg_id(42) == "msg-42"
    assert db_id(HumanMessage("x", id="msg-42")) == 42
    assert db_id(HumanMessage("x", id="5f1c-uuid")) is None
    assert db_id(HumanMessage("x")) is None
    assert db_id(ToolMessage("{}", tool_call_id="c1", id="msg-x")) is None


def test_final_rows_has_user_and_reply_only():
    rows = final_rows("问", "答")
    assert [(r.role, r.content) for r in rows] == [("user", "问"), ("assistant", "答")]
```

`tests/test_graph.py`、`tests/test_chat_api.py`、`tests/test_repositories.py`：把断言 `["user", "assistant", "tool", "assistant", ...]` 改为 `["user", "assistant"]`（`test_business_route_multi_step_react` 改为 `["user", "assistant"]`）。`tests/test_graph.py` 加：

```python
async def test_finalize_stamps_db_ids_on_state_messages(db, memory_graph, use_intent):
    use_intent("订单")
    cid = await new_cid(db)
    turn, _ = await run(memory_graph, cid, "订单 1001 到哪了",
                        tools(("c1", "query_order", {"order_id": "1001"})), text("已发货"))
    async with db() as s:
        ids = [m.id for m in (await s.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.id))).scalars()]
    msgs = turn.state.values["messages"]
    assert [type(m).__name__ for m in msgs] == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert len(ids) == 2
    assert msgs[0].id == f"msg-{ids[0]}" and msgs[-1].id == f"msg-{ids[1]}"
    assert not msgs[1].id.startswith("msg-") and not msgs[2].id.startswith("msg-")
    assert msgs[-1].content == "已发货"
```

`tests/test_tickets_api.py`（`test_refunds_api.py` 同样写一条）：

```python
async def test_ticket_note_state_message_has_db_id(client, db, locks, memory_graph):
    cid = await new_cid(db)
    await client.post("/tickets", json=body(cid))
    async with db() as s:
        row = (await s.scalars(select(Message).where(Message.conversation_id == cid))).one()
    state = await memory_graph.aget_state(thread_config(cid))
    assert state.values["messages"][-1].id == f"msg-{row.id}"


async def test_ticket_note_without_db_id_inherits_previous(client, db, locks, memory_graph, monkeypatch):
    from app.api import tickets
    async def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(tickets.messages, "add_turn", broken)
    cid = await new_cid(db)
    r = await client.post("/tickets", json=body(cid))
    assert r.status_code == 200
    state = await memory_graph.aget_state(thread_config(cid))
    assert not state.values["messages"][-1].id.startswith("msg-")
```

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_history.py tests/test_graph.py tests/test_chat_api.py tests/test_tickets_api.py tests/test_refunds_api.py -q`
Expected: FAIL。

- [ ] **Step 3：实现**

`messages.add_turn`：

```python
async def add_turn(session, conversation_id, rows) -> list[Message]:
    objs = [Message(conversation_id=conversation_id, role=r.role, content=r.content,
                    tool_calls=r.tool_calls, tool_call_id=r.tool_call_id) for r in rows]
    session.add_all(objs)
    await session.flush()
    return objs
```

`history.py`：删除 `turn_messages_rows`，新增：

```python
MSG_ID_PREFIX = "msg-"


def msg_id(n: int) -> str:
    return f"{MSG_ID_PREFIX}{n}"


def db_id(message: BaseMessage) -> int | None:
    """State 消息对应的 messages 表主键。工具消息和旧消息没有。"""
    mid = message.id or ""
    if mid.startswith(MSG_ID_PREFIX) and mid[len(MSG_ID_PREFIX):].isdigit():
        return int(mid[len(MSG_ID_PREFIX):])
    return None


def final_rows(user_input: str, reply: str) -> list[NewMessage]:
    """一轮写入 messages 表的两行。工具调用和结果只留在 State。"""
    return [NewMessage(role="user", content=user_input), NewMessage(role="assistant", content=reply)]
```

`finalize.py`：

```python
    turn = state.get("agent_messages") or [AIMessage(content=state["reply"])]
    final = turn[-1]
    async with get_sessionmaker()() as s:
        user_row, reply_row = await messages.add_turn(s, cid, final_rows(state["user_input"], final.content))
        await s.commit()
    new = [HumanMessage(state["user_input"], id=msg_id(user_row.id)), *turn[:-1],
           final.model_copy(update={"id": msg_id(reply_row.id)})]
    ...（日志不变）
    return {"messages": new, "trace": trace}
```

`tickets.py` / `refunds.py`：

```python
        note_id = None
        try:
            async with get_sessionmaker()() as s:
                (row,) = await messages.add_turn(s, cid, [NewMessage(role="assistant", content=note)])
                await s.commit()
                note_id = msg_id(row.id)
        except Exception:
            logger.exception(...)
        try:
            await graph.aupdate_state(thread_config(cid), {"messages": [AIMessage(content=note, id=note_id)]},
                                      as_node="finalize")
```

（`id=None` 时 `add_messages` 分配 UUID。）

更新其余依赖旧行为的测试（`tests/test_history.py` 中 `turn_messages_rows` 的测试删除；`test_repositories.py` 的 `add_turn` 测试改为检查返回值带主键）。

- [ ] **Step 4：运行全量**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add app tests
git commit -m "feat(ch07): store only user and reply rows, stamp msg ids on state messages"
```

---

### Task 5: 分层与渲染（纯函数）

**Files:**
- Create: `app/context/layers.py`
- Test: `tests/test_layers.py`

**Interfaces:**
- Consumes: `history.db_id`。
- Produces:
  - `effective_ids(messages: Sequence[BaseMessage]) -> list[int]`：自身 `db_id`，否则前一条的有效 id；最前面没有 DB id 的消息为 0。
  - `Layers`（frozen dataclass）：`layer2: list[BaseMessage]`、`layer1: list[BaseMessage]`、`ids2: list[int]`、`ids1: list[int]`。
  - `split_layers(messages, summary_upto: int | None, layer1_from: int | None) -> Layers`。
  - `render_layer2(messages: Sequence[BaseMessage]) -> list[BaseMessage]`（返回新对象，不改原消息）。
  - `history_lines(summary: str | None, layers: Layers, max_chars: int = RESOLVE_MESSAGE_MAX_CHARS) -> list[str]`。

- [ ] **Step 1：写失败测试** `tests/test_layers.py`

```python
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.context.layers import effective_ids, history_lines, render_layer2, split_layers


def turn(u, a, user="问", reply="答", tool=None):
    """一轮消息：用户 msg-u，可选工具调用和结果，回复 msg-a。"""
    msgs = [HumanMessage(user, id=f"msg-{u}")]
    if tool:
        msgs += [AIMessage("", tool_calls=[{"id": f"c{u}", "name": "query_order", "args": {"order_id": "1001"}}]),
                 ToolMessage(tool, tool_call_id=f"c{u}", name="query_order")]
    msgs.append(AIMessage(reply, id=f"msg-{a}"))
    return msgs


def test_effective_ids_inherit_previous():
    msgs = turn(1, 2, tool='{"ok": true}')
    assert effective_ids(msgs) == [1, 1, 1, 2]


def test_messages_without_db_id_count_as_zero():
    msgs = [HumanMessage("旧", id="uuid-1"), AIMessage("旧答"), *turn(5, 6)]
    assert effective_ids(msgs) == [0, 0, 5, 6]
    layers = split_layers(msgs, None, None)
    assert layers.layer1 == msgs and layers.layer2 == []


def test_split_by_anchors():
    msgs = [*turn(1, 2), *turn(3, 4, tool="{}"), *turn(5, 6)]
    layers = split_layers(msgs, summary_upto=2, layer1_from=4)
    assert [m.content for m in layers.layer2 if isinstance(m, HumanMessage)] == ["问"]
    assert layers.ids2 == [3, 3, 3, 4]
    assert layers.ids1 == [5, 6]


def test_null_anchors():
    msgs = [*turn(1, 2), *turn(3, 4)]
    assert split_layers(msgs, None, None).layer2 == []
    assert len(split_layers(msgs, 2, None).layer1) == 2
    assert len(split_layers(msgs, None, 2).layer2) == 2


def test_layer_boundary_never_splits_turn():
    msgs = [*turn(1, 2, tool="{}"), *turn(3, 4, tool="{}")]
    layers = split_layers(msgs, None, 2)
    assert isinstance(layers.layer1[0], HumanMessage)


def test_render_layer2_truncates_reply_and_long_tool_result():
    long_reply = "好" * 100
    msgs = turn(1, 2, user="订单 1001 到哪了", reply=long_reply, tool="x" * 300)
    out = render_layer2(msgs)
    assert out[0].content == "订单 1001 到哪了"
    assert out[1].tool_calls[0]["name"] == "query_order"
    assert out[2].content == "〔query_order 结果已省略，约 300 字〕"
    assert out[2].tool_call_id == "c1"
    assert out[3].content == "好" * 60 + "…"
    assert msgs[3].content == long_reply  # 原消息不变


def test_render_layer2_keeps_short_tool_result():
    out = render_layer2(turn(1, 2, tool='{"ok": true}'))
    assert out[2].content == '{"ok": true}'


def test_history_lines():
    msgs = [*turn(1, 2, user="我要退 1001", reply="好" * 100), *turn(3, 4, user="那运费呢", reply="运费由商家承担", tool="{}")]
    lines = history_lines("第1段：用户报订单 1001", split_layers(msgs, None, 2))
    assert lines == ["梗概：第1段：用户报订单 1001", "用户：我要退 1001", "客服：" + "好" * 60 + "…",
                     "用户：那运费呢", "客服：运费由商家承担"]


def test_history_lines_without_summary():
    assert history_lines(None, split_layers(turn(1, 2), None, None)) == ["用户：问", "客服：答"]
```

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_layers.py -q`
Expected: FAIL（模块不存在）。

- [ ] **Step 3：实现** `app/context/layers.py`

```python
"""按两个锚点把 State 历史分成层 2 和层 1，并渲染层 2 的截短形态。"""

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.config import LAYER2_REPLY_CHARS, LAYER2_TOOL_MAX_CHARS, RESOLVE_MESSAGE_MAX_CHARS
from app.services.history import db_id


def effective_ids(messages: Sequence[BaseMessage]) -> list[int]:
    out, current = [], 0
    for m in messages:
        n = db_id(m)
        if n is not None:
            current = n
        out.append(current)
    return out


@dataclass(frozen=True)
class Layers:
    layer2: list[BaseMessage]
    layer1: list[BaseMessage]
    ids2: list[int]
    ids1: list[int]


def split_layers(messages: Sequence[BaseMessage], summary_upto: int | None, layer1_from: int | None) -> Layers:
    l2, l1, i2, i1 = [], [], [], []
    for m, n in zip(messages, effective_ids(messages)):
        if summary_upto is not None and n <= summary_upto:
            continue
        if layer1_from is not None and n <= layer1_from:
            l2.append(m)
            i2.append(n)
        else:
            l1.append(m)
            i1.append(n)
    return Layers(l2, l1, i2, i1)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def render_layer2(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """用户原话不动；客服答复截短；长工具结果换成一行标识。保留工具调用与结果的配对。"""
    out: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, ToolMessage) and len(str(m.content)) > LAYER2_TOOL_MAX_CHARS:
            out.append(m.model_copy(update={
                "content": f"〔{m.name or '工具'} 结果已省略，约 {len(str(m.content))} 字〕"}))
        elif isinstance(m, AIMessage) and not m.tool_calls and isinstance(m.content, str):
            out.append(m.model_copy(update={"content": _cut(m.content, LAYER2_REPLY_CHARS)}))
        else:
            out.append(m)
    return out


def _dialog_lines(messages: Sequence[BaseMessage], *, layer2: bool, max_chars: int) -> list[str]:
    lines = []
    for m in messages:
        if isinstance(m, HumanMessage):
            # 层 2 用户原话不动；层 1 与 ch06 一致，截到 max_chars。
            lines.append("用户：" + (m.content if layer2 else m.content[:max_chars]))
        elif isinstance(m, AIMessage) and not m.tool_calls and isinstance(m.content, str) and m.content:
            lines.append("客服：" + (_cut(m.content, LAYER2_REPLY_CHARS) if layer2 else m.content[:max_chars]))
    return lines


def history_lines(summary: str | None, layers: Layers, max_chars: int = RESOLVE_MESSAGE_MAX_CHARS) -> list[str]:
    """指代消解用的历史文本：梗概行 + 层 2（规则截短）+ 层 1。工具消息不渲染。"""
    lines = [f"梗概：{summary}"] if summary else []
    lines += _dialog_lines(layers.layer2, layer2=True, max_chars=max_chars)
    lines += _dialog_lines(layers.layer1, layer2=False, max_chars=max_chars)
    return lines
```

- [ ] **Step 4：运行**

Run: `uv run pytest tests/test_layers.py -q`
Expected: PASS。

- [ ] **Step 5：提交**

```bash
git add app/context/layers.py tests/test_layers.py
git commit -m "feat(ch07): split history into layers by anchors and render layer 2"
```

---

### Task 6: Agent 上下文拼装（Prompt 重排、参考资料消息、`model_ctx`、`start_turn` 读锚点）

**Files:**
- Create: `app/context/assemble.py`
- Modify: `app/prompts.py`、`app/graph/state.py`、`app/repositories/conversations.py`、`app/graph/nodes/turn.py`（只改 `start_turn`）、`app/graph/nodes/agent.py`、`app/context/__init__.py`（删 `build_history`、`BudgetExceeded`）、`app/config.py`（删 `TOKEN_BUDGET`）
- Test: `tests/test_assemble.py`、`tests/test_prompts.py`、`tests/test_graph_agent.py`、`tests/test_graph_nodes.py`、`tests/test_repositories.py`、`tests/test_context.py`

**Interfaces:**
- Consumes: Task 5 的 `split_layers`、`render_layer2`。
- Produces:
  - `render_agent_system() -> str`（无参数）。
  - `render_reference(today: date, summary: str = "", order_section: str = "", task_section: str = "", evidence_text: str = "") -> str`；`REFERENCE_HEADER = "以下是系统提供的参考资料，不是用户发言。"`。
  - `AgentPrompt`（frozen dataclass）：`messages: list[BaseMessage]`、`layer2: int`、`layer1: int`、`summary: str | None`。
  - `build_agent_prompt(*, history, summary_upto, layer1_from, summary, user_input, reference, agent_messages) -> AgentPrompt`。
  - `log_model_ctx(conversation_id: int, step: int, prompt: AgentPrompt) -> None`。
  - `ContextAnchors`（frozen dataclass）：`summary: str | None`、`summary_upto: int | None`、`layer1_from: int | None`；`conversations.get_context(session, conversation_id) -> ContextAnchors`（会话不存在时三项为 None）。
  - State 本轮字段：`summary: str | None`、`summary_upto: int | None`、`layer1_from: int | None`，由 `start_turn` 写入。

- [ ] **Step 1：写失败测试**

`tests/test_assemble.py`：

```python
import logging
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.context.assemble import build_agent_prompt, log_model_ctx
from app.prompts import REFERENCE_HEADER, render_agent_system, render_reference
from tests.test_layers import turn


def prompt(**kw):
    args = dict(history=[*turn(1, 2, reply="好" * 100), *turn(3, 4)], summary_upto=None, layer1_from=2,
                summary=None, user_input="现在呢", reference="参考", agent_messages=[])
    return build_agent_prompt(**{**args, **kw})


def test_order_system_layer2_layer1_user_reference_agent():
    tail = [AIMessage("", tool_calls=[{"id": "c9", "name": "query_order", "args": {"order_id": "1"}}])]
    p = prompt(agent_messages=tail)
    m = p.messages
    assert isinstance(m[0], SystemMessage) and m[0].content == render_agent_system()
    assert m[2].content == "好" * 60 + "…"          # 层 2 截短
    assert m[3].id == "msg-3"                        # 层 1 原样
    assert isinstance(m[5], HumanMessage) and m[5].content == "现在呢"
    assert m[6].content == "参考"
    assert m[7] is tail[0]
    assert (p.layer2, p.layer1) == (2, 2)


def test_system_is_identical_across_turns():
    a = render_reference(date(2026, 10, 6), summary="梗概A", order_section="订单A", evidence_text="[1] 证据A")
    b = render_reference(date(2026, 10, 7), evidence_text="[1] 证据B")
    assert prompt(reference=a).messages[0].content == prompt(reference=b).messages[0].content


def test_summary_never_in_system_message():
    ref = render_reference(date(2026, 10, 6), summary="第1段：订单 1001 要换货")
    p = prompt(reference=ref, summary="第1段：订单 1001 要换货")
    assert all("订单 1001 要换货" not in m.content for m in p.messages if isinstance(m, SystemMessage))
    assert "订单 1001 要换货" in p.messages[-1].content


def test_reference_has_only_non_empty_sections():
    ref = render_reference(date(2026, 10, 6), evidence_text="[1] 证据")
    assert ref.startswith(REFERENCE_HEADER)
    assert "## 今天\n2026-10-06" in ref and "## 知识库证据\n[1] 证据" in ref
    assert "## 早期对话梗概" not in ref and "## 订单数据" not in ref and "本轮任务" not in ref


def test_system_has_no_variable_sections():
    s = render_agent_system()
    assert "今天是" not in s and "## 知识库证据\n" not in s and "## 订单数据\n" not in s


def test_log_model_ctx(caplog):
    caplog.set_level(logging.INFO)
    log_model_ctx(12, 0, prompt(summary="第1段：订单 1001"))
    assert "model_ctx conversation=12 step=0 window=4 tokens≈" in caplog.text
    assert "summary=第1段：订单 1001" in caplog.text
    assert "  [L2] user: 问" in caplog.text and "  [L1] assistant: 答" in caplog.text
```

`tests/test_repositories.py`：`get_context` 读到写入的三个值；会话不存在时返回三项 None。

`tests/test_graph_nodes.py`：

```python
async def test_start_turn_loads_anchors(db, emitted):
    from sqlalchemy import update
    from app.db.models import Conversation
    from app.graph.nodes.turn import start_turn
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.execute(update(Conversation).where(Conversation.id == cid)
                        .values(summary="第1段：订单 1001", summary_upto_msg_id=4, layer1_from_msg_id=8))
        await s.commit()
    out = await start_turn({}, rt(conversation_id=cid))
    assert (out["summary"], out["summary_upto"], out["layer1_from"]) == ("第1段：订单 1001", 4, 8)
```

`tests/test_graph_agent.py`：
- `test_history_and_turn_messages_are_sent_in_order` 的期望改为 `["SystemMessage", "HumanMessage", "AIMessage", "HumanMessage", "HumanMessage", "AIMessage", "ToolMessage"]`（第 4、5 条为用户这句和参考资料）。
- `test_binds_agent_tools_and_streams_answer` 中 `isinstance(sent[-1], HumanMessage)` 保持成立（最后一条是参考资料）。
- `test_evidence_goes_into_system_prompt` 改名并改为：

```python
async def test_evidence_goes_into_reference_message():
    from app.prompts import REFERENCE_HEADER
    evidence = [{"n": 1, "chunk_id": 9, "section_path": "退换货 > 运费", "question": "运费谁出", "answer": "商家"}]
    m, rec = model(text("商家承担[1]"))
    await agent_model(state(evidence=evidence, summary="第1段：订单 1001"), rt(model=m))
    sent = rec[0]["messages"]
    assert "退换货 > 运费" not in sent[0].content and "订单 1001" not in sent[0].content
    assert sent[-2].content == "订单 1001 到哪了"
    assert sent[-1].content.startswith(REFERENCE_HEADER)
    assert "## 早期对话梗概\n第1段：订单 1001" in sent[-1].content and "[1] 退换货 > 运费" in sent[-1].content
```

- 售后模式中断言订单段、任务段在 System 中的旧测试，改为断言它们在 `sent[-1]`（参考资料）中。

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_assemble.py tests/test_graph_agent.py tests/test_graph_nodes.py tests/test_repositories.py -q`
Expected: FAIL。

- [ ] **Step 3：实现**

`app/prompts.py`：
- `AGENT_SYSTEM_TEMPLATE` 第一行改为 `你是{shop_name}的售后客服助手。`（去掉日期）；删除 `{task_section}{order_section}{evidence_section}` 占位（`## 引用` 段直接接在 `## 人工选项` 之后）。
- 提到"知识库证据"一节和"订单数据"的规则，改为指向参考资料：工具使用第 5 条改为 `店铺政策和商品型号问题，只根据参考资料中"知识库证据"一节回答。没有这一节时，……`；引用第 3 条改为 `只引用本轮参考资料中"知识库证据"一节的编号，不引用历史消息中的编号。`。其他文字不变。
- `AFTERSALES_TASK_WITH_ORDER` 第 1 条中 `"订单数据"和"知识库证据"` 保持（两段都在参考资料中）。

```python
def render_agent_system() -> str:
    return AGENT_SYSTEM_TEMPLATE.format(shop_name=SHOP_NAME)


REFERENCE_HEADER = "以下是系统提供的参考资料，不是用户发言。"


def render_reference(today: date, summary: str = "", order_section: str = "", task_section: str = "",
                     evidence_text: str = "") -> str:
    """挂在用户这句之后的一条消息：只放非空段。"""
    parts = [REFERENCE_HEADER, f"## 今天\n{today.isoformat()}"]
    if summary:
        parts.append(f"## 早期对话梗概\n{summary}")
    if order_section:
        parts.append(f"## 订单数据\n{order_section}")
    if task_section:
        parts.append(task_section.strip())  # 自带"## 本轮任务"标题
    if evidence_text:
        parts.append(f"## 知识库证据\n{evidence_text}")
    return "\n\n".join(parts)
```

`app/context/assemble.py`：

```python
"""主力 Agent 的上下文拼装：固定 System → 层 2 → 层 1 → 用户这句 → 参考资料 → 本轮循环。"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.context import count_tokens
from app.context.layers import render_layer2, split_layers
from app.prompts import render_agent_system

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentPrompt:
    messages: list[BaseMessage]
    layer2: int
    layer1: int
    summary: str | None


def build_agent_prompt(*, history: Sequence[BaseMessage], summary_upto: int | None, layer1_from: int | None,
                       summary: str | None, user_input: str, reference: str,
                       agent_messages: Sequence[BaseMessage]) -> AgentPrompt:
    layers = split_layers(history, summary_upto, layer1_from)
    layer2 = render_layer2(layers.layer2)
    messages = [SystemMessage(render_agent_system()), *layer2, *layers.layer1,
                HumanMessage(user_input), HumanMessage(reference), *agent_messages]
    return AgentPrompt(messages, len(layer2), len(layers.layer1), summary)


def _line(tag: str, m: BaseMessage) -> str:
    if isinstance(m, HumanMessage):
        role = "user"
    elif isinstance(m, ToolMessage):
        role = "tool"
    elif isinstance(m, AIMessage) and m.tool_calls:
        role = "assistant(tool_calls=" + ",".join(c["name"] for c in m.tool_calls) + ")"
    else:
        role = "assistant"
    return f"  [{tag}] {role}: {m.content}"


def log_model_ctx(conversation_id: int, step: int, prompt: AgentPrompt) -> None:
    window = prompt.messages[1:1 + prompt.layer2 + prompt.layer1]
    lines = [_line("L2" if i < prompt.layer2 else "L1", m) for i, m in enumerate(window)]
    logger.info("model_ctx conversation=%s step=%s window=%s tokens≈%s summary=%s\n%s",
                conversation_id, step, len(window), count_tokens(prompt.messages), prompt.summary or "-",
                "\n".join(lines) or "  （无）")
```

`app/repositories/conversations.py`：

```python
@dataclass(frozen=True)
class ContextAnchors:
    summary: str | None
    summary_upto: int | None
    layer1_from: int | None


async def get_context(session, conversation_id: int) -> ContextAnchors:
    row = (await session.execute(
        select(Conversation.summary, Conversation.summary_upto_msg_id, Conversation.layer1_from_msg_id)
        .where(Conversation.id == conversation_id))).first()
    return ContextAnchors(*row) if row else ContextAnchors(None, None, None)
```

`app/graph/state.py`：`ChatState` 本轮字段加 `summary: str | None`、`summary_upto: int | None`、`layer1_from: int | None`。

`start_turn`：

```python
async def start_turn(state, runtime):
    async with get_sessionmaker()() as s:
        anchors = await conversations.get_context(s, runtime.context.conversation_id)
    return {
        ...（原字段不变）,
        "summary": anchors.summary, "summary_upto": anchors.summary_upto, "layer1_from": anchors.layer1_from,
    }
```

`agent_model`：替换 `system = ...`、`history = build_history(...)`、`prompt = [...]` 三段：

```python
    order_section, task_section = _aftersales_sections(state)
    reference = render_reference(ctx.today, state.get("summary") or "", order_section, task_section,
                                 format_evidence(citations) if citations else "")
    built = build_agent_prompt(
        history=state.get("messages", []), summary_upto=state.get("summary_upto"),
        layer1_from=state.get("layer1_from"), summary=state.get("summary"),
        user_input=state["resolved_input"], reference=reference, agent_messages=state.get("agent_messages", []))
    log_model_ctx(ctx.conversation_id, state.get("steps", 0), built)
    prompt = built.messages
```

`measure_system_tokens` 改用 `render_agent_system()`。删除 `app/context/__init__.py` 中的 `build_history`、`BudgetExceeded` 和 `config.TOKEN_BUDGET`；删除 `tests/test_context.py` 中对应测试，保留 `test_count_tokens_uses_chars_per_token`。直接调用 `start_turn` 的旧测试加 fixture `db`。

- [ ] **Step 4：运行全量**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add app tests
git commit -m "feat(ch07): fixed system prompt, reference message after user input, model_ctx log"
```

---

### Task 7: 指代消解的分层历史与 `history_ctx`

**Files:**
- Modify: `app/graph/nodes/turn.py`（`resolve_reference`）、`app/services/understanding.py`（删 `history_text`）、`app/config.py`（删 `RESOLVE_HISTORY_MESSAGES`）、`evals/run_multiturn_eval.py`
- Test: `tests/test_graph_nodes.py`、`tests/test_understanding.py`、`tests/test_graph.py`

**Interfaces:**
- Consumes: `split_layers`、`history_lines`；State 的 `summary`、`summary_upto`、`layer1_from`。

- [ ] **Step 1：写失败测试**

`tests/test_graph_nodes.py`：

```python
async def test_resolve_reference_uses_layered_history(use_resolver, caplog):
    import logging
    caplog.set_level(logging.INFO)
    calls = use_resolver({})
    state = {"user_input": "那个呢", "trace": [], "summary": "第1段：订单 1001 要换货",
             "summary_upto": 2, "layer1_from": 4,
             "messages": [*turn(1, 2, user="很早的话"), *turn(3, 4, user="订单 1002 呢", reply="好" * 100),
                          *turn(5, 6, user="运费呢", reply="商家承担")]}
    await resolve_reference(state, rt(conversation_id=7))
    assert calls[0]["history"] == ("梗概：第1段：订单 1001 要换货\n用户：订单 1002 呢\n客服：" + "好" * 60 + "…"
                                   "\n用户：运费呢\n客服：商家承担")
    assert "history_ctx conversation=7 lines=5 summary=第1段：订单 1001 要换货" in caplog.text
    assert "很早的话" not in calls[0]["history"]
```

`tests/test_graph.py`：

```python
async def test_history_ctx_logged_on_chitchat_turn(db, memory_graph, use_intent, caplog):
    caplog.set_level("INFO")
    use_intent("闲聊", "闲聊")
    cid = await new_cid(db)
    await run(memory_graph, cid, "你好")
    await run(memory_graph, cid, "在吗")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("history_ctx")]
    assert len(lines) == 2
    assert f"history_ctx conversation={cid} lines=2 summary=-" in lines[1] and "用户：你好" in lines[1]
```

（`test_layers.py` 中的 `turn` 辅助函数在 `tests/test_graph_nodes.py` 中用 `from tests.test_layers import turn` 引入。）

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_graph_nodes.py tests/test_graph.py -q`
Expected: FAIL。

- [ ] **Step 3：实现**

```python
async def resolve_reference(state, runtime):
    trace = events.enter("resolve_reference", state, runtime)
    cid = runtime.context.conversation_id
    layers = split_layers(state.get("messages", []), state.get("summary_upto"), state.get("layer1_from"))
    lines = history_lines(state.get("summary"), layers)
    history = "\n".join(lines)
    logger.info("history_ctx conversation=%s lines=%s summary=%s\n%s", cid, len(lines),
                state.get("summary") or "-", "\n".join(f"  {line}" for line in lines) or "  （无）")
    r = await understanding.resolve(state["user_input"], history)
    ...（其余不变）
```

删 `understanding.history_text` 和 `config.RESOLVE_HISTORY_MESSAGES`，删除或改写 `tests/test_understanding.py` 中 `history_text` 的测试。`evals/run_multiturn_eval.py`：

```python
from app.context.layers import history_lines, split_layers
...
                history = "\n".join(history_lines(None, split_layers(messages, None, None)))
```

- [ ] **Step 4：运行全量 + 多轮评估（真实上游）**

Run: `uv run pytest -q && uv run python evals/run_multiturn_eval.py`
Expected: 测试全部 PASS；多轮评估退出码 0。

- [ ] **Step 5：提交**

```bash
git add app evals tests
git commit -m "feat(ch07): resolver reads layered history with summary, history_ctx log"
```

---

### Task 8: 摘要器（runner、Prompt、仓储、投影）

**Files:**
- Create: `app/context/summarizer.py`、`app/repositories/summaries.py`
- Modify: `app/repositories/conversations.py`（`set_summary`、`advance_layer1`）、`app/prompts.py`、`app/llm.py`、`app/main.py`（退出时 `cancel_all`）、`tests/conftest.py`
- Test: `tests/test_summarizer.py`、`tests/test_repositories.py`

**Interfaces:**
- Produces:
  - `summaries.list_for_conversation(session, cid) -> list[ConversationSummary]`（按 `seq`）；`summaries.append(session, cid, from_id, upto_id, content) -> int`（返回 seq = 已有最大 seq + 1）。
  - `conversations.set_summary(session, cid, upto: int, projection: str) -> bool`（条件 `summary_upto_msg_id IS NULL OR < upto`，返回是否更新）；`conversations.advance_layer1(session, cid, new_from: int) -> bool`（条件 `layer1_from_msg_id IS NULL OR < new_from`）。
  - `SUMMARY_SYSTEM_PROMPT`、`summary_prompt`（输入变量 `previous`、`dialog`）；`llm.get_summarizer() -> Runnable`（`summary_prompt | build_extract_model(get_settings()) | StrOutputParser()`，`lru_cache`）。
  - `batch_text(batch: Sequence[BaseMessage]) -> str`、`check_summary(text: str, source: str) -> None`（不通过抛 `SummaryRejected(code)`，code 为 `empty`/`too_long`/`unsupported_number`/`stale_range`）、`projection(contents: Sequence[str]) -> str`。
  - `run_summary(cid: int, batch: list[BaseMessage], from_id: int, upto_id: int) -> None`。
  - `SummaryRunner.running(cid) -> bool`、`.start(cid, batch, from_id, upto_id, tokens: int, budget: int) -> bool`、`async .drain()`、`async .cancel_all()`；`get_runner()`、`set_runner(r)`。
  - conftest：autouse `_isolate_summarizer`（`summarizer.get_summarizer` 换成立即失败的工厂；`set_runner(SummaryRunner())`）；fixture `use_summarizer(*values)`（每次调用消费一个值：字符串为输出，异常实例为抛出，`asyncio.Event` 为先等待再消费下一个值；返回调用输入列表）。

- [ ] **Step 1：写失败测试** `tests/test_summarizer.py`

```python
import asyncio
import logging

import pytest
from sqlalchemy import select

from app.context.summarizer import (SummaryRejected, SummaryRunner, batch_text, check_summary, get_runner,
                                    projection, run_summary)
from app.db.models import Conversation, ConversationSummary
from app.repositories import conversations
from tests.test_layers import turn

pytestmark = pytest.mark.anyio


async def new_cid(db):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        await s.commit()
    return cid


def test_batch_text():
    text = batch_text(turn(1, 2, user="订单 1001 没到", reply="已发货", tool="x" * 500))
    assert text == ("用户：订单 1001 没到\n调用 query_order(order_id=1001)\n工具结果：" + "x" * 200 + "\n客服：已发货")


def test_check_summary():
    check_summary("用户订单 1001 要换货", "用户：订单 1001 坏了")
    for bad, code in (("", "empty"), ("字" * 301, "too_long"), ("订单 9999 要换货", "unsupported_number")):
        with pytest.raises(SummaryRejected) as e:
            check_summary(bad, "用户：订单 1001 坏了")
        assert e.value.code == code


def test_projection_keeps_newest_within_reserve():
    assert projection(["甲", "乙"]) == "第1段：甲\n第2段：乙"
    long = ["字" * 600, "字" * 600, "新"]
    out = projection(long)
    assert out.endswith("第3段：新") and "第1段" not in out


async def test_run_summary_appends_segment_and_moves_anchor(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    calls = use_summarizer("用户报订单 1001，左耳没声音，想换货。")
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2, user="订单 1001 左耳没声音，想换货"), 1, 2)
    async with db() as s:
        seg = (await s.scalars(select(ConversationSummary))).one()
        conv = await s.get(Conversation, cid)
    assert (seg.seq, seg.from_msg_id, seg.upto_msg_id) == (1, 1, 2)
    assert conv.summary_upto_msg_id == 2 and conv.summary == "第1段：用户报订单 1001，左耳没声音，想换货。"
    assert calls[0]["previous"] == "（无）"
    assert "summary start conversation=%s range=1..2 msgs=2" % cid in caplog.text
    assert "summary done conversation=%s 第1段 range=1..2" % cid in caplog.text


async def test_previous_segments_are_background_only(db, use_summarizer):
    calls = use_summarizer("第一批", "第二批 1002")
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2), 1, 2)
    await run_summary(cid, turn(3, 4, user="订单 1002"), 3, 4)
    async with db() as s:
        segs = (await s.scalars(select(ConversationSummary).order_by(ConversationSummary.seq))).all()
        conv = await s.get(Conversation, cid)
    assert [s.content for s in segs] == ["第一批", "第二批 1002"]   # 旧段不变
    assert calls[1]["previous"] == "第1段：第一批"
    assert conv.summary == "第1段：第一批\n第2段：第二批 1002"


@pytest.mark.parametrize("value,code", [("订单 8888", "unsupported_number"), (TimeoutError(), "TimeoutError")])
async def test_run_summary_failure_keeps_anchor(db, use_summarizer, caplog, value, code):
    caplog.set_level(logging.INFO)
    use_summarizer(value)
    cid = await new_cid(db)
    await run_summary(cid, turn(1, 2), 1, 2)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id is None
        assert (await s.scalars(select(ConversationSummary))).all() == []
    assert "summary fail conversation=%s range=1..2" % cid in caplog.text and code in caplog.text


async def test_summary_does_not_move_anchor_backwards(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    use_summarizer("后一批", "前一批")
    cid = await new_cid(db)
    await run_summary(cid, turn(3, 4), 3, 4)
    await run_summary(cid, turn(1, 2), 1, 2)
    async with db() as s:
        assert (await s.get(Conversation, cid)).summary_upto_msg_id == 4
        assert len((await s.scalars(select(ConversationSummary))).all()) == 1
    assert "stale_range" in caplog.text


async def test_runner_skips_when_running(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    gate = asyncio.Event()
    use_summarizer(gate, "完成")
    cid = await new_cid(db)
    runner = get_runner()
    assert runner.start(cid, turn(1, 2), 1, 2, tokens=2000, budget=1695) is True
    assert runner.start(cid, turn(1, 2), 1, 2, tokens=2000, budget=1695) is False
    assert "summary trigger conversation=%s 层2 约 2000 token > 预算 1695 range=1..2" % cid in caplog.text
    assert "summary skip conversation=%s reason=running" % cid in caplog.text
    gate.set()
    await runner.drain()
    assert runner.running(cid) is False


async def test_cancel_all_logs(db, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    use_summarizer(asyncio.Event())
    cid = await new_cid(db)
    runner = get_runner()
    runner.start(cid, turn(1, 2), 1, 2, tokens=1, budget=0)
    await asyncio.sleep(0)
    await runner.cancel_all()
    assert "summary cancel conversation=%s" % cid in caplog.text
```

`tests/test_repositories.py`：`summaries.append` 两次返回 1、2；`set_summary` 和 `advance_layer1` 对更小的值返回 False 且不改列。

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_summarizer.py tests/test_repositories.py -q`
Expected: FAIL。

- [ ] **Step 3：实现**

`app/prompts.py`：

```python
SUMMARY_SYSTEM_PROMPT = """你是客服对话的记录员。把"本批对话"压成一段梗概，供客服后续接待时回看。

## 只记录
1. 用户问过的商品和型号。
2. 用户报过的订单号、手机号。
3. 用户明确提出的诉求（退货、换货、退款、维修、投诉、查物流等）和期望的结果。
4. 还没解决的问题。
5. 客服给出的关键结论（例如订单状态、能否办理）。

## 规则
1. 只写本批对话中出现的内容，不推测，不补充。对话中没有的内容一个字也不许写。
2. 数字、型号、订单号、手机号原样照抄。
3. 寒暄、感谢、闲聊、客服的道歉和安抚话不写。
4. "已有梗概"只作背景，帮助你理解指代。不复述、不改写已有梗概，只写本批新增的事实。
5. 写成一段中文纯文本，30 至 200 字。不分点，不用 Markdown。"""

summary_prompt = ChatPromptTemplate.from_messages([
    ("system", SUMMARY_SYSTEM_PROMPT),
    ("human", "已有梗概（只作背景）：\n{previous}\n\n本批对话：\n{dialog}"),
])
```

`app/llm.py`：

```python
@lru_cache
def get_summarizer() -> Runnable:
    return summary_prompt | build_extract_model(get_settings()) | StrOutputParser()
```

`app/repositories/summaries.py`：

```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationSummary


async def list_for_conversation(session: AsyncSession, conversation_id: int) -> list[ConversationSummary]:
    rows = await session.scalars(select(ConversationSummary)
                                 .where(ConversationSummary.conversation_id == conversation_id)
                                 .order_by(ConversationSummary.seq))
    return list(rows)


async def append(session: AsyncSession, conversation_id: int, from_id: int, upto_id: int, content: str) -> int:
    current = await session.scalar(select(func.max(ConversationSummary.seq))
                                   .where(ConversationSummary.conversation_id == conversation_id))
    seq = (current or 0) + 1
    session.add(ConversationSummary(conversation_id=conversation_id, seq=seq, from_msg_id=from_id,
                                    upto_msg_id=upto_id, content=content))
    await session.flush()
    return seq
```

`app/repositories/conversations.py`：

```python
async def set_summary(session, conversation_id: int, upto: int, projection: str) -> bool:
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id,
               or_(Conversation.summary_upto_msg_id.is_(None), Conversation.summary_upto_msg_id < upto))
        .values(summary_upto_msg_id=upto, summary=projection))
    return result.rowcount > 0


async def advance_layer1(session, conversation_id: int, new_from: int) -> bool:
    result = await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id,
               or_(Conversation.layer1_from_msg_id.is_(None), Conversation.layer1_from_msg_id < new_from))
        .values(layer1_from_msg_id=new_from))
    return result.rowcount > 0
```

`app/context/summarizer.py`：

```python
"""后台摘要：把层 2 的一批消息压成新的一段，只追加，不重写旧段。"""

import asyncio
import logging
import re
import time
from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.config import (SUMMARY_MAX_CHARS, SUMMARY_RESERVE_TOKENS, SUMMARY_TIMEOUT_SECONDS,
                        SUMMARY_TOOL_RESULT_CHARS)
from app.context import count_tokens
from app.db.engine import get_sessionmaker
from app.llm import get_summarizer
from app.repositories import conversations, summaries

logger = logging.getLogger(__name__)


class SummaryRejected(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def batch_text(batch: Sequence[BaseMessage]) -> str:
    lines = []
    for m in batch:
        if isinstance(m, HumanMessage):
            lines.append(f"用户：{m.content}")
        elif isinstance(m, ToolMessage):
            lines.append(f"工具结果：{str(m.content)[:SUMMARY_TOOL_RESULT_CHARS]}")
        elif isinstance(m, AIMessage):
            for c in m.tool_calls:
                args = ", ".join(f"{k}={v}" for k, v in c["args"].items())
                lines.append(f"调用 {c['name']}({args})")
            if isinstance(m.content, str) and m.content:
                lines.append(f"客服：{m.content}")
    return "\n".join(lines)


def check_summary(text: str, source: str) -> None:
    if not text:
        raise SummaryRejected("empty")
    if len(text) > SUMMARY_MAX_CHARS:
        raise SummaryRejected("too_long")
    # 订单号、手机号等数字串必须来自原文。
    if any(num not in source for num in re.findall(r"\d{4,}", text)):
        raise SummaryRejected("unsupported_number")


def projection(contents: Sequence[str]) -> str:
    """从最新一段往前取，总量不超过梗概预留；至少保留最新一段。"""
    lines = [f"第{i}段：{c}" for i, c in enumerate(contents, 1)]
    kept: list[str] = []
    for line in reversed(lines):
        if kept and count_tokens([HumanMessage("\n".join([line, *kept]))]) > SUMMARY_RESERVE_TOKENS:
            break
        kept.insert(0, line)
    return "\n".join(kept)


async def run_summary(cid: int, batch: list[BaseMessage], from_id: int, upto_id: int) -> None:
    started = time.monotonic()
    logger.info("summary start conversation=%s range=%s..%s msgs=%s", cid, from_id, upto_id, len(batch))
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            previous = [r.content for r in await summaries.list_for_conversation(s, cid)]
        dialog = batch_text(batch)
        prev_text = "\n".join(f"第{i}段：{c}" for i, c in enumerate(previous, 1)) or "（无）"
        text = (await asyncio.wait_for(get_summarizer().ainvoke({"previous": prev_text, "dialog": dialog}),
                                       SUMMARY_TIMEOUT_SECONDS)).strip()
        check_summary(text, dialog + "\n" + prev_text)
        async with sm() as s:
            seq = await summaries.append(s, cid, from_id, upto_id, text)
            if not await conversations.set_summary(s, cid, upto_id, projection([*previous, text])):
                await s.rollback()
                raise SummaryRejected("stale_range")
            await s.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = exc.code if isinstance(exc, SummaryRejected) else type(exc).__name__
        logger.warning("summary fail conversation=%s range=%s..%s elapsed=%.1fs error=%s",
                       cid, from_id, upto_id, time.monotonic() - started, code, exc_info=not isinstance(exc, SummaryRejected))
        return
    logger.info("summary done conversation=%s 第%s段 range=%s..%s chars=%s elapsed=%.1fs",
                cid, seq, from_id, upto_id, len(text), time.monotonic() - started)


class SummaryRunner:
    """每个会话最多一个运行中的摘要任务。持有任务引用，避免被回收。"""

    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}

    def running(self, cid: int) -> bool:
        task = self._tasks.get(cid)
        return task is not None and not task.done()

    def start(self, cid: int, batch: Sequence[BaseMessage], from_id: int, upto_id: int,
              tokens: int, budget: int) -> bool:
        if self.running(cid):
            logger.info("summary skip conversation=%s reason=running", cid)
            return False
        logger.info("summary trigger conversation=%s 层2 约 %s token > 预算 %s range=%s..%s",
                    cid, tokens, budget, from_id, upto_id)
        task = asyncio.create_task(run_summary(cid, list(batch), from_id, upto_id))
        self._tasks[cid] = task
        task.add_done_callback(lambda t, c=cid: self._tasks.pop(c, None) if self._tasks.get(c) is t else None)
        return True

    async def drain(self) -> None:
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def cancel_all(self) -> None:
        for cid, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                logger.info("summary cancel conversation=%s", cid)
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


_runner = SummaryRunner()


def get_runner() -> SummaryRunner:
    return _runner


def set_runner(runner: SummaryRunner) -> None:
    global _runner
    _runner = runner
```

`app/main.py` lifespan 的 `finally` 最前面加 `await get_runner().cancel_all()`。

`tests/conftest.py`：

```python
@pytest.fixture(autouse=True)
def _isolate_summarizer(monkeypatch):
    from app.context import summarizer
    monkeypatch.setattr(summarizer, "get_summarizer", _blocked_factory("get_summarizer"))
    summarizer.set_runner(summarizer.SummaryRunner())


@pytest.fixture
def use_summarizer(monkeypatch):
    """用法：use_summarizer("梗概", TimeoutError(), asyncio.Event(), "梗概2")。Event 表示先等待再消费下一个值。"""
    from langchain_core.runnables import RunnableLambda
    from app.context import summarizer

    def _use(*values):
        queue, calls = list(values), []

        async def run(inputs):
            calls.append(inputs)
            value = queue.pop(0)
            if isinstance(value, asyncio.Event):
                await value.wait()
                value = queue.pop(0) if queue else "完成"
            if isinstance(value, BaseException):
                raise value
            return value

        monkeypatch.setattr(summarizer, "get_summarizer", lambda: RunnableLambda(run))
        return calls

    return _use
```

（`run_summary` 内用 `wait_for` 包住调用，`TimeoutError()` 值模拟超时失败。）

- [ ] **Step 4：运行全量**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add app tests
git commit -m "feat(ch07): background summary runner with append-only segments"
```

---

### Task 9: `finalize` 降级与摘要触发

**Files:**
- Create: `app/context/maintain.py`
- Modify: `app/graph/nodes/finalize.py`、`tests/conftest.py`（fixture `use_budget`）
- Test: `tests/test_maintain.py`、`tests/test_graph.py`

**Interfaces:**
- Consumes: `split_layers`、`render_layer2`、`count_tokens`、`get_budget`、`get_runner`、`conversations.get_context`、`conversations.advance_layer1`。
- Produces: `async maintain(cid: int, messages: Sequence[BaseMessage]) -> None`；fixture `use_budget(layer1: int, layer2: int)`（`set_budget` 一个只改 `layer1`、`layer2` 的 `ContextBudget`，测试结束恢复 None）。

- [ ] **Step 1：写失败测试** `tests/test_maintain.py`

```python
import logging

import pytest
from sqlalchemy import update

from app.context.maintain import maintain
from app.context.summarizer import get_runner
from app.db.models import Conversation
from app.repositories import conversations
from tests.test_layers import turn

pytestmark = pytest.mark.anyio


async def new_cid(db, **cols):
    async with db() as s:
        cid = (await conversations.create(s, "u1")).id
        if cols:
            await s.execute(update(Conversation).where(Conversation.id == cid).values(**cols))
        await s.commit()
    return cid


def history(n, size=40):
    msgs = []
    for i in range(n):
        msgs += turn(2 * i + 1, 2 * i + 2, user="问" * size, reply="答" * size)
    return msgs


async def anchors(db, cid):
    async with db() as s:
        return await conversations.get_context(s, cid)


async def test_under_budget_does_nothing(db, use_budget, caplog):
    caplog.set_level(logging.INFO)
    use_budget(layer1=100000, layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, history(20))
    a = await anchors(db, cid)
    assert (a.layer1_from, a.summary_upto) == (None, None)
    assert "层1 降级" not in caplog.text and "summary trigger" not in caplog.text
    assert "context_usage conversation=%s" % cid in caplog.text


async def test_layer1_over_budget_degrades_a_batch(db, use_budget, caplog):
    from app.config import LAYER1_LOW_WATER
    from app.context import count_tokens
    caplog.set_level(logging.INFO)
    msgs = history(10)
    per_turn = count_tokens(msgs[:2])
    use_budget(layer1=per_turn * 5, layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, msgs)
    # 水位 0.6：大约保留 3 轮，其余整轮降级。
    keep = int(per_turn * 5 * LAYER1_LOW_WATER) // per_turn
    expected = 2 * (10 - keep)
    assert (await anchors(db, cid)).layer1_from == expected
    assert f"层1 降级 conversation={cid} -→{expected}" in caplog.text


async def test_degrade_all_when_single_turn_exceeds(db, use_budget):
    use_budget(layer1=10, layer2=100000)
    cid = await new_cid(db)
    await maintain(cid, turn(1, 2, tool="x" * 2000))
    assert (await anchors(db, cid)).layer1_from == 2


async def test_layer2_over_budget_triggers_summary(db, use_budget, use_summarizer, caplog):
    caplog.set_level(logging.INFO)
    use_summarizer("梗概")
    use_budget(layer1=100000, layer2=10)
    cid = await new_cid(db, layer1_from_msg_id=4)
    await maintain(cid, history(4))
    assert "summary trigger conversation=%s" % cid in caplog.text and "range=1..4" in caplog.text
    await get_runner().drain()
    assert (await anchors(db, cid)).summary_upto == 4


async def test_maintain_reads_fresh_anchors(db, use_budget, use_summarizer, caplog):
    # 数据库中已摘要到 4：即使调用方拿的是旧锚点，也不再摘要 1..4。
    caplog.set_level(logging.INFO)
    use_budget(layer1=100000, layer2=10)
    cid = await new_cid(db, layer1_from_msg_id=4, summary_upto_msg_id=4)
    await maintain(cid, history(4))
    assert "summary trigger" not in caplog.text
```

`tests/test_graph.py`：

```python
async def test_failed_turn_does_not_maintain(db, memory_graph, use_intent, use_budget, caplog):
    caplog.set_level("INFO")
    use_budget(layer1=0, layer2=0)
    use_intent("物流")
    cid = await new_cid(db)
    with pytest.raises(RuntimeError):
        await run(memory_graph, cid, "到哪了", [RuntimeError("upstream")])
    assert await saved(db, cid) == []
    assert "层1 降级" not in caplog.text and "summary trigger" not in caplog.text


async def test_maintain_failure_does_not_break_turn(db, memory_graph, use_intent, monkeypatch, caplog):
    from app.graph.nodes import finalize as finalize_mod

    async def broken(*a, **k):
        raise RuntimeError("x")

    caplog.set_level("INFO")
    monkeypatch.setattr(finalize_mod, "maintain", broken)
    use_intent("闲聊")
    cid = await new_cid(db)
    turn, _ = await run(memory_graph, cid, "你好")
    assert turn.state.values["reply"] == CHITCHAT_REPLY
    assert [r for r, _ in await saved(db, cid)] == ["user", "assistant"]
    assert f"context_maintain_failed conversation={cid}" in caplog.text


async def test_summary_does_not_block_reply(db, memory_graph, use_intent, use_budget, use_summarizer):
    import asyncio
    from app.context.summarizer import get_runner
    from app.repositories import conversations as conv_repo
    gate = asyncio.Event()
    use_summarizer(gate, "用户打招呼")
    use_budget(layer1=0, layer2=0)
    use_intent("闲聊")
    cid = await new_cid(db)
    turn, _ = await run(memory_graph, cid, "你好")
    # 图已经跑完，本轮回复已产生；摘要仍卡在 Event 上。
    assert turn.state.values["reply"] == CHITCHAT_REPLY
    assert get_runner().running(cid) is True
    gate.set()
    await get_runner().drain()
    async with db() as s:
        a = await conv_repo.get_context(s, cid)
    assert a.summary_upto == a.layer1_from and a.summary == "第1段：用户打招呼"
```

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_maintain.py tests/test_graph.py -q`
Expected: FAIL。

- [ ] **Step 3：实现**

`tests/conftest.py`：

```python
@pytest.fixture
def use_budget():
    """用法：use_budget(layer1=100, layer2=50)。只替换层 1、层 2 预算。"""
    from dataclasses import replace
    from app.context import budget as budget_mod

    def _use(layer1: int, layer2: int):
        budget_mod.set_budget(replace(budget_mod.compute_budget(
            window=128000, max_output=8192, max_user_input=1000, max_agent_steps=4, tool_result_max=750, top_k=10),
            layer1=layer1, layer2=layer2))

    yield _use
    budget_mod.set_budget(None)
```

`app/context/maintain.py`：

```python
"""每轮结束后维护层的边界：层 1 超预算降级一批，层 2 超预算起后台摘要。"""

import logging
from collections.abc import Sequence

from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import trim_messages

from app.config import LAYER1_LOW_WATER
from app.context import count_tokens
from app.context.budget import get_budget
from app.context.layers import render_layer2, split_layers
from app.context.summarizer import get_runner
from app.db.engine import get_sessionmaker
from app.repositories import conversations

logger = logging.getLogger(__name__)


def _tokens(messages: Sequence[BaseMessage]) -> int:
    return count_tokens(messages) if messages else 0


async def maintain(cid: int, messages: Sequence[BaseMessage]) -> None:
    budget = get_budget()
    sm = get_sessionmaker()
    # 锚点从数据库读：本轮进行中，后台摘要可能已经推进了边界。
    async with sm() as s:
        anchors = await conversations.get_context(s, cid)
    layer1_from = anchors.layer1_from
    layers = split_layers(messages, anchors.summary_upto, layer1_from)
    l1 = _tokens(layers.layer1)
    if l1 > budget.layer1:
        kept = trim_messages(layers.layer1, max_tokens=int(budget.layer1 * LAYER1_LOW_WATER), strategy="last",
                             start_on="human", token_counter=count_tokens)
        dropped = len(layers.layer1) - len(kept)
        new_from = max(layers.ids1[:dropped]) if dropped else None
        if new_from is not None:
            async with sm() as s:
                await conversations.advance_layer1(s, cid, new_from)
                await s.commit()
            logger.info("层1 降级 conversation=%s %s→%s 层1 约 %s token > 预算 %s",
                        cid, layer1_from if layer1_from is not None else "-", new_from, l1, budget.layer1)
            layer1_from = new_from
            layers = split_layers(messages, anchors.summary_upto, layer1_from)
    l2 = _tokens(render_layer2(layers.layer2))
    logger.info("context_usage conversation=%s layer1=%s/%s layer2=%s/%s",
                cid, _tokens(layers.layer1), budget.layer1, l2, budget.layer2)
    if layers.layer2 and l2 > budget.layer2:
        get_runner().start(cid, layers.layer2, layers.ids2[0], layer1_from, l2, budget.layer2)
```

注意：`layers.ids2[0]` 是层 2 第一条消息（用户消息）的 DB id；`layer1_from` 是层 2 最后一条有 DB id 的消息。

`finalize.py`：在 `logger.info("turn ...")` 之后、`return` 之前：

```python
    try:
        await maintain(cid, [*state.get("messages", []), *new])
    except Exception:
        logger.exception("context_maintain_failed conversation=%s", cid)
```

- [ ] **Step 4：运行全量**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add app tests
git commit -m "feat(ch07): degrade layer 1 and trigger background summary in finalize"
```

---

### Task 10: 摘要 Prompt 评估（非 TDD，真实上游）

**Files:**
- Create: `evals/summary_samples.jsonl`、`evals/run_summary_eval.py`
- Test: `tests/test_summary_samples.py`（只校验样例文件格式，不调上游）

**Interfaces:**
- Consumes: `batch_text`、`check_summary`、`get_summarizer`（`app.llm`）。

- [ ] **Step 1：写样例** `evals/summary_samples.jsonl`，10 行，每行：

```json
{"id": "s01", "previous": [], "batch": [{"role": "user", "content": "你好，我的订单 1001 耳机左耳没声音"}, {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "query_order", "args": {"order_id": "1001"}}]}, {"role": "tool", "content": "{\"ok\": true, \"data\": {\"order_id\": \"1001\", \"status\": \"已签收\"}}", "tool_call_id": "c1"}, {"role": "assistant", "content": "订单 1001 已签收。左耳没声音属于质量问题，可以申请换货。"}, {"role": "user", "content": "那就换货吧，我手机 13800001234"}, {"role": "assistant", "content": "好的，请点击下方按钮提交。"}], "must_contain": ["1001", "13800001234", "换货"], "must_not_contain": ["你好", "好的"]}
```

覆盖：订单号 + 手机号；多个订单；商品型号（`X3 Pro`）；只有闲聊的一批（`must_contain` 为空，检查不编造）；有 `previous` 的一批（`must_not_contain` 含 `previous` 中独有的事实，检查不复述）；工具结果很长；用户反复改口（以最后诉求为准）；投诉；纯政策咨询；退款进度。

- [ ] **Step 2：写格式测试** `tests/test_summary_samples.py`：每行能解析；`id` 唯一；`batch` 中 `role` 只有 `user`/`assistant`/`tool`；`must_contain` 中每个词都出现在 batch 文本中。

- [ ] **Step 3：写评估脚本** `evals/run_summary_eval.py`

样例中闲聊批次加字段 `"chitchat": true`，长度下限不检查。

```python
"""评估摘要 Prompt：必留事实、无编造数字、不复述、长度全部通过才达标。"""

import asyncio
import json
import logging
from pathlib import Path
import sys

# 用脚本位置定位项目和样例文件。
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.config import SUMMARY_TIMEOUT_SECONDS
from app.context.summarizer import SummaryRejected, batch_text, check_summary
from app.llm import get_summarizer

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "summary_samples.jsonl"
MIN_CHARS, MAX_CHARS = 30, 200


def to_messages(batch: list[dict]) -> list:
    out = []
    for m in batch:
        if m["role"] == "user":
            out.append(HumanMessage(m["content"]))
        elif m["role"] == "tool":
            out.append(ToolMessage(m["content"], tool_call_id=m["tool_call_id"]))
        else:
            out.append(AIMessage(m.get("content") or "", tool_calls=[
                {"id": c["id"], "name": c["name"], "args": c["args"]} for c in m.get("tool_calls", [])]))
    return out


async def summarize(sample: dict, semaphore: asyncio.Semaphore) -> tuple[str | None, str, str]:
    dialog = batch_text(to_messages(sample["batch"]))
    previous = "\n".join(f"第{i}段：{c}" for i, c in enumerate(sample["previous"], 1)) or "（无）"
    async with semaphore:
        try:
            text = await asyncio.wait_for(
                get_summarizer().ainvoke({"previous": previous, "dialog": dialog}), SUMMARY_TIMEOUT_SECONDS)
            return text.strip(), dialog, previous
        except Exception:
            logger.exception("摘要失败：%s", sample["id"])
            return None, dialog, previous


def problems(sample: dict, text: str | None, dialog: str, previous: str) -> list[str]:
    if text is None:
        return ["调用失败"]
    found = []
    try:
        check_summary(text, dialog + "\n" + previous)
    except SummaryRejected as exc:
        found.append(f"校验失败：{exc.code}")
    found += [f"缺少：{w}" for w in sample["must_contain"] if w not in text]
    found += [f"不应出现：{w}" for w in sample["must_not_contain"] if w in text]
    if len(text) > MAX_CHARS or (not sample.get("chitchat") and len(text) < MIN_CHARS):
        found.append(f"长度 {len(text)} 不在 {MIN_CHARS}–{MAX_CHARS}")
    return found


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(3)
    results = await asyncio.gather(*(summarize(s, semaphore) for s in samples))
    passed = 0
    for sample, (text, dialog, previous) in zip(samples, results):
        found = problems(sample, text, dialog, previous)
        passed += not found
        print(f"{sample['id']} {'✅' if not found else '❌'} {text}")
        for p in found:
            print(f"  {p}")
    print(f"\n全部检查通过：{passed}/{len(samples)}（门槛 100%）")
    return 0 if passed == len(samples) else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run_eval()))
```

- [ ] **Step 4：运行评估**

Run: `uv run pytest tests/test_summary_samples.py -q && uv run python evals/run_summary_eval.py`
Expected: 测试 PASS；评估退出码 0。没通过时，Claude 分析失败样例，给 Codex 具体的 Prompt 修改要求，重跑直到通过。不许改样例迁就输出。

- [ ] **Step 5：提交**

```bash
git add evals/summary_samples.jsonl evals/run_summary_eval.py tests/test_summary_samples.py app/prompts.py
git commit -m "feat(ch07): summary prompt eval set"
```

---

### Task 11: 会话只读接口

**Files:**
- Create: `app/api/conversations.py`
- Modify: `app/repositories/conversations.py`（`list_for_user`）、`app/main.py`（注册路由）
- Test: `tests/test_conversations_api.py`

**Interfaces:**
- Produces:
  - `conversations.list_for_user(session, user_id: str, limit: int = 50) -> list[tuple[Conversation, str | None]]`（会话 + 首条用户消息内容）。
  - `GET /api/conversations?user_id=` → `[{"session_id": str, "created_at": str, "updated_at": str, "preview": str, "summarized": bool}]`。
  - `GET /api/conversations/{id}/messages?user_id=` → `[{"id": int, "role": "user"|"assistant", "content": str, "created_at": str}]`；不属于该用户 → 404 `conversation_not_found`。

- [ ] **Step 1：写失败测试** `tests/test_conversations_api.py`

```python
import pytest
from sqlalchemy import update

from app.db.models import Conversation
from app.repositories import conversations, messages
from app.repositories.messages import NewMessage

pytestmark = pytest.mark.anyio


async def seed(db, user_id, rows, **cols):
    async with db() as s:
        cid = (await conversations.create(s, user_id)).id
        await messages.add_turn(s, cid, [NewMessage(**r) for r in rows])
        if cols:
            await s.execute(update(Conversation).where(Conversation.id == cid).values(**cols))
        await s.commit()
    return cid


async def test_list_conversations_newest_first(client, db):
    a = await seed(db, "u1", [{"role": "user", "content": "订单 1001 到哪了" * 5}, {"role": "assistant", "content": "已发货"}])
    b = await seed(db, "u1", [{"role": "user", "content": "运费谁出"}], summary_upto_msg_id=1)
    await seed(db, "u2", [{"role": "user", "content": "别人的"}])
    r = await client.get("/api/conversations", params={"user_id": "u1"})
    assert r.status_code == 200
    items = r.json()
    assert [i["session_id"] for i in items] == [str(b), str(a)]
    assert items[0]["summarized"] is True and items[1]["summarized"] is False
    assert items[1]["preview"] == ("订单 1001 到哪了" * 5)[:30]


async def test_empty_conversation_has_empty_preview(client, db):
    await seed(db, "u1", [])
    assert (await client.get("/api/conversations", params={"user_id": "u1"})).json()[0]["preview"] == ""


async def test_messages_returns_user_and_assistant_text_only(client, db):
    cid = await seed(db, "u1", [
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "name": "query_order", "args": {}}]},
        {"role": "tool", "content": "{}", "tool_call_id": "c1"},
        {"role": "assistant", "content": "答"}])
    r = await client.get(f"/api/conversations/{cid}/messages", params={"user_id": "u1"})
    assert [(m["role"], m["content"]) for m in r.json()] == [("user", "问"), ("assistant", "答")]


async def test_messages_of_other_user_is_404(client, db):
    cid = await seed(db, "u2", [{"role": "user", "content": "x"}])
    r = await client.get(f"/api/conversations/{cid}/messages", params={"user_id": "u1"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversation_not_found"
```

- [ ] **Step 2：运行，确认失败**

Run: `uv run pytest tests/test_conversations_api.py -q`
Expected: FAIL（404 路由不存在）。

- [ ] **Step 3：实现**

`app/repositories/conversations.py`：

```python
async def list_for_user(session, user_id: str, limit: int = 50) -> list[tuple[Conversation, str | None]]:
    first = (select(Message.content)
             .where(Message.conversation_id == Conversation.id, Message.role == "user")
             .order_by(Message.id).limit(1).correlate(Conversation).scalar_subquery())
    rows = await session.execute(select(Conversation, first).where(Conversation.user_id == user_id)
                                 .order_by(Conversation.id.desc()).limit(limit))
    return [(c, p) for c, p in rows.all()]
```

`app/api/conversations.py`：

```python
"""会话侧栏的两个只读接口。"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.db.engine import get_sessionmaker
from app.repositories import conversations, messages
from app.schemas import UserId

router = APIRouter(prefix="/api/conversations")
PREVIEW_CHARS = 30


@router.get("")
async def list_conversations(user_id: Annotated[UserId, Query()]) -> list[dict]:
    async with get_sessionmaker()() as s:
        rows = await conversations.list_for_user(s, user_id)
    return [{"session_id": str(c.id), "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat(),
             "preview": (p or "")[:PREVIEW_CHARS], "summarized": c.summary_upto_msg_id is not None} for c, p in rows]


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: int, user_id: Annotated[UserId, Query()]) -> list[dict]:
    async with get_sessionmaker()() as s:
        if await conversations.get_for_user(s, conversation_id, user_id) is None:
            raise HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})
        rows = await messages.list_for_conversation(s, conversation_id)
    return [{"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in rows if m.role in ("user", "assistant") and m.content]
```

`app/main.py`：`app.include_router(conversations.router)`（`from app.api import conversations`）。

- [ ] **Step 4：运行全量**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add app tests
git commit -m "feat(ch07): read-only conversation list and history APIs"
```

---

### Task 12: 聊天页会话侧栏（Vibe Coding）

**Files:**
- Modify: `app/web/index.html`

按 CLAUDE.md 例外条款：Claude 把下面的效果描述交给 Codex，不走 TDD 和 code review；Claude 在 Chrome 中验收。

- [ ] **Step 1：交给 Codex 的效果描述**

1. 聊天页左侧加会话侧栏，宽 240px；窄屏（< 720px）时侧栏收起，标题栏加一个按钮展开。
2. 侧栏调用 `GET /api/conversations?user_id=<当前 userId>`，按返回顺序（新在前）列出会话：首问预览（为空时显示"新会话"）、更新时间（`MM-DD HH:mm`）、`summarized` 为真时显示小标签"已摘要"。当前会话高亮。
3. 点击一个会话：调用 `GET /api/conversations/{id}/messages?user_id=`，清空消息区，按顺序渲染用户和客服的文本气泡，`sessionId` 设为该会话，之后发消息继续这个会话。回载只渲染文本，不渲染引用卡片和按钮。
4. "新对话"按钮只清空当前 `sessionId` 和消息区。旧会话仍在侧栏。
5. 收到 `session` 事件后、收到 `done` 事件后，重新加载侧栏。
6. 侧栏请求失败（网络错误、非 200）：只 `console.warn`，侧栏保持原样，聊天功能不受影响。正在流式回复时禁止切换会话（点击无效）。
7. 不改 SSE 处理、工单、退款、订单选择器的现有逻辑。

- [ ] **Step 2：Claude 验收（Chrome）**

1. 启动服务，打开 `http://127.0.0.1:8000/`。
2. 会话 A 问"订单 1001 到哪了"；点"新对话"；会话 B 问"运费谁出"。侧栏有两条，B 在上。
3. 点 A：消息区回载 A 的两条消息；再问"那能退吗"，回复针对订单 1001。
4. 停掉服务后点侧栏：控制台有 warning，页面不崩。

- [ ] **Step 3：提交**

```bash
git add app/web/index.html
git commit -m "feat(ch07): chat page conversation sidebar"
```

---

### Task 13: 口径校准、验收脚本、`TURN_TOKENS` 实测

**Files:**
- Create: `evals/run_token_calibration.py`、`scripts/demo7.sh`、`scripts/demo7_chat.py`、`scripts/demo7_dialog.json`
- Modify: `app/config.py`（`CHARS_PER_TOKEN`、`TURN_TOKENS`，必要时调整预留常量）、`tests/test_budget.py`（如预留常量变化）、spec 4.4 节

**Interfaces:**
- `scripts/demo7_chat.py --base URL --user USER --dialog FILE [--session ID] [--ask TEXT]`：顺序发送每轮，每轮读完 SSE 才发下一轮；打印 `turn=<n> session=<id> finish=<reason> reply=<前 80 字>`；最后一行打印 JSON `{"session_id": ..., "turns": n, "errors": n, "last_reply": ...}`。任一轮没有以 `done` 结束时退出码 1。

- [ ] **Step 1：校准脚本** `evals/run_token_calibration.py`

```python
"""字符/token 口径校准：对比 count_tokens 估算和上游 usage。只打印建议值，不改代码。"""

import asyncio
import json
import logging
import math
import statistics
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage

from app.config import CHARS_PER_TOKEN, get_settings
from app.context import count_tokens
from app.llm import build_extract_model
from app.prompts import render_agent_system
from app.tools import mock_data

logger = logging.getLogger(__name__)


def samples() -> list[tuple[str, str]]:
    today = date(2026, 10, 8)
    out = [("system", render_agent_system())]
    for path in sorted((ROOT / "knowledge" / "docs").glob("*/*.md"))[:5]:
        out.append(("evidence", path.read_text(encoding="utf-8")[:400]))
    for oid in ("1001", "1002"):
        out.append(("tool", json.dumps({"ok": True, "data": mock_data.order(oid, today)}, ensure_ascii=False)))
    out.append(("tool", json.dumps({"ok": True, "data": mock_data.logistics("1001", today)}, ensure_ascii=False)))
    dialog = json.loads((ROOT / "scripts" / "demo7_dialog.json").read_text(encoding="utf-8"))
    out += [("user", line) for line in dialog[:10]]
    return out


async def input_tokens(model, text: str) -> int:
    msg = await model.ainvoke([HumanMessage(text)], max_tokens=16)
    return msg.usage_metadata["input_tokens"]


async def run() -> int:
    model = build_extract_model(get_settings())
    # 基线：一条最短消息，扣掉上游模板的固定开销。
    base = await input_tokens(model, "好")
    rows = []
    for kind, text in samples():
        real = await input_tokens(model, text) - base + 1
        estimate = count_tokens([HumanMessage(text)]) - count_tokens([HumanMessage("好")]) + 1
        rows.append((kind, len(text), estimate, real, len(text) / real))
        print(f"{kind:8s} 字数={len(text):5d} 估算={estimate:5d} 真实={real:5d} 字/token={len(text) / real:.2f}")
    ratios = [r[4] for r in rows]
    suggest = math.floor(min(ratios) * 10) / 10
    print(f"\n当前 CHARS_PER_TOKEN={CHARS_PER_TOKEN}；最小 {min(ratios):.2f}，中位数 {statistics.median(ratios):.2f}")
    print(f"建议 CHARS_PER_TOKEN={suggest}（取最小值向下到 0.1，估算不低于真实值）")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(asyncio.run(run()))
```

注意：`max_tokens=16` 只限制输出，不影响 `input_tokens`；提取模型已关闭思考，不会因思考 token 返回空内容。

- [ ] **Step 2（Claude）：运行校准并决定常量**

前置：校准样本读取 `scripts/demo7_dialog.json`，所以先让 Codex 完成 Step 3 的对话文件，再运行本步。

Run: `uv run python evals/run_token_calibration.py`

1. 把 `CHARS_PER_TOKEN` 改成建议值（交给 Codex 改 `app/config.py` 和 `tests/test_context.py` 中的注释数值）。
2. 重新运行 `uv run python -c "from app.graph.nodes.agent import measure_system_tokens; print(measure_system_tokens())"`。如果超过 `SYSTEM_RESERVE_TOKENS`，按实测值上调 SYS（取整到 50），并下调 `SUMMARY_RESERVE_TOKENS` 或 `STEP_OVERHEAD_TOKENS`，使演示配置仍得 5650；保持 `tests/test_budget.py` 的演示断言不变。调不平时停下来问用户。
3. 用同一口径估算一条证据的 token（取 Milvus 中最长 10 个块的平均），如超过 `EVIDENCE_ITEM_TOKENS` 同样处理。
4. 在 spec 4.4 节和 `dev-notes/ch07.md` 记录：校准数据、新旧值、调整理由。

- [ ] **Step 3：对话脚本** `scripts/demo7_dialog.json`（22 轮，字符串数组）

要求：第 1 至 3 轮报订单 1001、手机号 13800001234、诉求"耳机左耳没声音，想换货"；之后的轮次不再出现 1001 和这个手机号（避免层 1 原文帮忙答对）；包含订单 1002、1003 查询、物流、运费、发票、保修政策、X3 Pro 参数、2 轮闲聊、1 轮投诉；每轮一句。

`scripts/demo7_chat.py`：

```python
"""ch07 验收用的对话客户端：按顺序发送，每轮读完 SSE 再发下一轮。"""

import argparse
import asyncio
import json
import sys

import httpx


async def ask(client: httpx.AsyncClient, base: str, user: str, message: str, session: str | None) -> dict:
    body = {"user_id": user, "message": message, **({"session_id": session} if session else {})}
    events, name = [], None
    async with client.stream("POST", f"{base}/chat/stream", json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    sid = next((d["session_id"] for n, d in events if n == "session"), session)
    reply = "".join(d["text"] for n, d in events if n == "token")
    finish = events[-1][1].get("finish_reason") if events and events[-1][0] == "done" else None
    return {"session_id": sid, "reply": reply, "finish": finish}


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--user", required=True)
    p.add_argument("--dialog")
    p.add_argument("--session")
    p.add_argument("--ask")
    a = p.parse_args()
    lines = json.loads(open(a.dialog, encoding="utf-8").read()) if a.dialog else []
    if a.ask:
        lines.append(a.ask)
    session, errors, last = a.session, 0, ""
    async with httpx.AsyncClient(timeout=180) as client:
        for i, message in enumerate(lines, 1):
            r = await ask(client, a.base, a.user, message, session)
            session, last = r["session_id"], r["reply"]
            errors += r["finish"] != "stop"
            print(f"turn={i} session={session} finish={r['finish']} reply={last[:80]}")
    print(json.dumps({"session_id": session, "turns": len(lines), "errors": errors, "last_reply": last},
                     ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4：验收脚本** `scripts/demo7.sh <日志目录>`

```bash
#!/usr/bin/env bash
# ch07 验收。前置：MySQL、Milvus 已启动，已 build_kb，端口 8000 空闲。脚本自己按配置启停服务。
# 用法：bash scripts/demo7.sh <日志目录>
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
DIR="${1:?用法：bash scripts/demo7.sh <日志目录>}"
mkdir -p "$DIR" log
APP_LOG=log/app.log
touch "$APP_LOG"
DEMO_ENV=(MODEL_CONTEXT_WINDOW=18000 MAX_OUTPUT_TOKENS=2000 MAX_USER_INPUT_TOKENS=2000 MAX_AGENT_STEPS=3
          TOOL_RESULT_MAX_TOKENS=1200 RERANK_TOP_K=5)
PID=""

if pgrep -f "uvicorn app.main:app" >/dev/null; then
    echo '端口 8000 上已有服务在运行，请先停止。' >&2
    exit 1
fi

stop() {
    [[ -n "$PID" ]] || return 0
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    PID=""
    for _ in $(seq 20); do pgrep -f "uvicorn app.main:app" >/dev/null || return 0; sleep 0.5; done
    echo '旧服务进程没有退出' >&2
    return 1
}
fail() { echo "❌ $1"; stop || true; exit 1; }
trap 'stop || true' EXIT

# start <名称> [环境变量...]：记下 app.log 当前长度，启动服务，等 /health 最多 30 秒。
start() {
    local name=$1
    shift
    OFFSET=$(wc -c < "$APP_LOG")
    env "$@" uv run uvicorn app.main:app --port 8000 > "$DIR/$name.out" 2>&1 &
    PID=$!
    for _ in $(seq 60); do
        curl --fail --silent http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    fail "服务启动超时（$name），见 $DIR/$name.out"
}
# section：本次启动后新增的 app.log 内容。
section() { tail -c +"$((OFFSET + 1))" "$APP_LOG"; }
count() { section | grep -c "$1" || true; }

echo '=== [1/4] 默认配置 22 轮：不降级、不摘要 ==='
start default
uv run python scripts/demo7_chat.py --user "demo7-a-$RANDOM" --dialog scripts/demo7_dialog.json \
    || fail '[1/4] 有轮次没有正常结束'
sleep 2
section > "$DIR/default.log"
stop
if grep -q '层1 降级\|summary trigger' "$DIR/default.log"; then fail '[1/4] 默认配置出现了降级或摘要'; fi
echo '✅ [1/4] 默认配置 22 轮没有降级和摘要'

echo '=== [2/4] 只改窗口：上下文预算不足 ==='
start window-only MODEL_CONTEXT_WINDOW=18000
section > "$DIR/window-only.log"
stop
grep -q '上下文预算不足' "$DIR/window-only.log" || fail '[2/4] 没有报上下文预算不足'
echo '✅ [2/4] 只改窗口时报上下文预算不足'

echo '=== [3/4] 演示配置：完整级联，靠梗概答对最早的订单 ==='
start demo "${DEMO_ENV[@]}"
section | grep -q 'history=5650 layer1=3954 layer2=1695' || fail '[3/4] 预算不是 5650/3954/1695'
USER_C="demo7-c-$RANDOM"
out=$(uv run python scripts/demo7_chat.py --user "$USER_C" --dialog scripts/demo7_dialog.json) \
    || fail '[3/4] 有轮次没有正常结束'
sid=$(echo "$out" | tail -1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
# 等后台摘要结束：最多 60 秒，直到 trigger 数等于 done 数加 fail 数。
for _ in $(seq 60); do
    [[ $(count 'summary trigger') -eq $(( $(count 'summary done') + $(count 'summary fail') )) ]] && break
    sleep 1
done
ans=$(uv run python scripts/demo7_chat.py --user "$USER_C" --session "$sid" --ask '最开始那个订单后来怎么说') \
    || fail '[3/4] 最后一问没有正常结束'
section > "$DIR/demo.log"
stop
grep -q '层1 降级' "$DIR/demo.log" || fail '[3/4] 没有层1 降级'
grep -q 'summary trigger' "$DIR/demo.log" || fail '[3/4] 没有 summary trigger'
grep -q 'summary done conversation=.* 第1段' "$DIR/demo.log" || fail '[3/4] 没有 summary done 第1段'
# 不阻塞：第一条 summary trigger 前面已有本轮的 turn 行；summary done 在 trigger 之后。
python3 - "$DIR/demo.log" <<'EOF' || fail '[3/4] 摘要顺序不对'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
t = next(i for i, l in enumerate(lines) if "summary trigger" in l)
d = next(i for i, l in enumerate(lines) if "summary done" in l)
assert any(" turn conversation=" in l for l in lines[:t]), "trigger 之前没有 turn 行"
assert d > t, "done 早于 trigger"
print(lines[t]); print(lines[d])
EOF
echo "$ans" | tail -1 | python3 -c 'import json,sys; r=json.load(sys.stdin)["last_reply"]; print(r); sys.exit(0 if "1001" in r else 1)' \
    || fail '[3/4] 回复里没有最早的订单号 1001'
echo '✅ [3/4] 降级、摘要级联完整，靠梗概答对了最早的订单'

echo '=== [4/4] model_ctx / history_ctx ==='
for k in model_ctx history_ctx; do
    n=$(grep -c "$k" "$DIR/demo.log" || true)
    [[ "$n" -gt 0 ]] || fail "[4/4] 没有 $k"
    echo "$k：$n 条，最后一条："
    grep -A8 "$k" "$DIR/demo.log" | tail -9
done
echo '✅ [4/4] 每轮发给模型的摘要和滑窗都在日志中'
```

说明：`turn` 汇总行由 `logger.info("turn conversation=...")` 打出，文件格式 `%(asctime)s %(levelname)s %(name)s %(message)s` 中以 `" turn conversation="` 出现。trigger 由 `finalize` 在本轮 `turn` 行之后打出，所以 trigger 之前一定有 turn 行；`summary done` 晚于本轮 `turn` 行，说明摘要没有阻塞本轮回复。

- [ ] **Step 5（Claude）：实测 `TURN_TOKENS`**

1. 运行 `bash scripts/demo7.sh /tmp/ch07-demo`。
2. 从 `default.log` 取每轮的 `context_usage ... layer1=N/...`，计算第 3 至 22 轮的平均增量，向上取整到 50，乘 1.2 作为 `TURN_TOKENS`。
3. 检查：默认配置下 `int(KEEP_TURNS × TURN_TOKENS × 0.7)` 大于第 22 轮的 layer1 实测值。
4. 交给 Codex 更新 `TURN_TOKENS` 和 `tests/test_budget.py` 中依赖它的断言（`test_default_window_is_capped_by_keep_turns` 用 monkeypatch 固定 800，不受影响）。
5. 重新运行 `bash scripts/demo7.sh /tmp/ch07-demo`，4 项全部 ✅。

- [ ] **Step 6：提交**

```bash
git add evals/run_token_calibration.py scripts/demo7.sh scripts/demo7_chat.py scripts/demo7_dialog.json app/config.py tests docs/superpowers/specs/2026-10-08-ch07-context-management-design.md
git commit -m "feat(ch07): token calibration, demo7 acceptance script, measured turn tokens"
```

---

### Task 14: 回归、文档、完整验收

**Files:**
- Modify: `CLAUDE.md`、`dev-notes/ch07.md`

- [ ] **Step 1：全量测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。记录条数。

- [ ] **Step 2：回归评估（真实上游）**

```bash
uv run python evals/run_multiturn_eval.py
uv run python evals/run_intent_eval.py
uv run python evals/run_expand_eval.py
uv run python evals/run_summary_eval.py
uv run python evals/run_chat_samples.py
```

Expected: 前 4 个退出码 0；`run_chat_samples.py` 的回复人工抽查：售后任务指令（退款单按钮、引用编号）仍被遵循。

- [ ] **Step 3：ch06 验收回归**

Run: 启动服务（`> /tmp/ch07-server.log 2>&1`），`bash scripts/demo6.sh /tmp/ch07-server.log`
Expected: 4 项 ✅。不通过时按 systematic-debugging 排查（重点：任务指令移到用户角色消息后的遵循度）。

- [ ] **Step 4：ch07 验收**

Run: `bash scripts/demo7.sh /tmp/ch07-demo`
Expected: 4 项 ✅。另按 Task 12 Step 2 在 Chrome 中复验侧栏。

- [ ] **Step 5：更新 CLAUDE.md**

1. 项目状态加 ch07 一行：三层上下文（锚点 `summary_upto_msg_id`/`layer1_from_msg_id`、`conversation_summaries` 只追加）、预算从窗口倒推、System 固定 + 参考资料消息、`model_ctx`/`history_ctx`、会话侧栏。
2. 常用命令加：`bash scripts/demo7.sh /tmp/ch07-demo`、`uv run python evals/run_summary_eval.py`、`uv run python evals/run_token_calibration.py`；`reset_db.sh` 说明加"升级到 ch07 后必须执行"。
3. 约束：
   - 把"证据全局编号后渲染进 Agent System Prompt 的'知识库证据'段"改为"证据、订单段、任务段、梗概和日期放在用户这句之后的一条参考资料 `HumanMessage` 中；System 每轮相同；梗概不进任何 `SystemMessage`"。
   - 新增："`messages` 表只写用户消息、最终回复和工单/退款提示；工具调用和结果只留 State"；"State 中有数据库行的消息 id 为 `msg-<主键>`，工具消息继承前一条的有效 id"；"梗概只追加不重写，旧段只作背景"；"两个锚点只增不减；`maintain` 从数据库读锚点"；"`db/schema_ch07.sql` 是用户 DDL"；"摘要中 4 位以上数字串必须出现在源文本中"。
   - 架构表加 `app/context/` 各模块和 `app/api/conversations.py`；删除 `app/context.py` 一行中的 `build_history`。
4. 环境变量段：注明 6 个 `Settings` 字段有默认值，不写入 `.env`。

- [ ] **Step 6：提交并推送**

```bash
git add CLAUDE.md dev-notes/ch07.md
git commit -m "docs(ch07): CLAUDE.md update and acceptance notes"
git push -u origin ch07
```
