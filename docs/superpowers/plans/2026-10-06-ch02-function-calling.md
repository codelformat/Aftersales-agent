# ch02 Function Calling 工具链 Implementation Plan

> **For agentic workers:** 本项目的执行方式由 `CLAUDE.md` 规定：Claude 把每个任务交给 Codex 实现，Codex 完成后由 Claude 审查 diff、运行测试、补记 dev-notes、提交并推送到 `ch02` 分支。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给客服聊天接上 Function Calling：模型选择工具 → 后端执行 → 结果回灌 → 流式作答；聊天记录落 MySQL。

**Architecture:** MySQL 由本项目的 Docker Compose 启动（端口 3307），表结构以 `db/schema.sql`（用户 DDL 原样）为唯一来源。SQLAlchemy 2.x 异步 + asyncmy。5 个 LangChain `@tool` 由注册表管理，执行器负责参数校验、超时、指数回退重试和错误转换。`/chat/stream` 的一轮：第 1 次调用绑定工具并流式输出；有工具时并行执行，再做第 2 次调用（不绑定工具）流式输出。

**Tech Stack:** Python 3.12、uv、FastAPI、SQLAlchemy ≥ 2.0（asyncio）、asyncmy、cryptography、MySQL 8（Docker）、langchain-core / langchain-openai ≥ 1.6、pytest + anyio + httpx。

**Spec:** `docs/superpowers/specs/2026-10-06-ch02-function-calling-design.md`

## 执行方式（每个任务）

1. Claude 把"Global Constraints"一节和该任务的全文作为任务描述，按 `CLAUDE.md` 中的命令交给 Codex。
2. Codex 按步骤实现，运行该任务的测试命令。Codex 不执行 `git commit`。
3. Claude 审查 diff，核对 spec 和本计划，运行 `uv run pytest -q`。
4. 有问题时，Claude 把具体问题交给 Codex 重做，不直接改代码。
5. 通过后，Claude 补记 `dev-notes/ch02.md`，提交并推送到 `ch02`。

## Global Constraints

- 所有工作在 `ch02` 分支上进行。
- 依赖只通过 `uv add` 添加。本章新增运行时依赖仅限：`sqlalchemy[asyncio]>=2.0`、`asyncmy`、`cryptography`。
- 配置读取 5 个环境变量：`CHAT_BASE_URL`、`CHAT_MODEL`、`CHAT_API_KEY`、`CHAT_THINKING`（可选）、`DATABASE_URL`。测试库地址由 `app.config.test_database_url()` 推导，不新增变量。
- `db/schema.sql` 是用户提供的 DDL，**逐字保存，不许修改**。ORM 只映射，不调用 `create_all`。
- 执行 `.sql` 文件时用 `conn.exec_driver_sql(语句)`，不用 `text()`。原因：`text()` 会把 `:xxx` 解析为绑定参数，DDL 注释中有冒号。
- 异步 ORM：提交后要读取由数据库默认值填充的列（如 `status`、`created_at`）时，必须先 `await session.refresh(obj)`，否则抛 `MissingGreenlet`（已实测）。`async_sessionmaker(..., expire_on_commit=False)`。
- 测试中的数据库引擎一律用 `poolclass=NullPool`。原因：pytest 的 anyio 测试各自使用独立事件循环，连接池跨循环复用会报 "attached to a different loop"（已实测）。
- 所有自写的重试一律用 `app.retry.retry_async`（指数回退加抖动）。不许手写重试循环，不许固定间隔。
- SSE 事件一律用 `ServerSentEvent(raw_data=json.dumps(payload, ensure_ascii=False), event=<name>)`。
- 对外错误信息和回灌给模型的工具错误只用固定文案。完整异常用 `logger.exception` 写日志。
- 离线单测不访问网络和数据库。数据库测试使用 fixture `db`，连不上时直接失败，不跳过。
- 测试中的聊天模型一律用 `tests/fakes.py` 的 `ScriptedChatModel`，通过 `app.dependency_overrides[get_chat_model]` 替换。
- 代码注释和文档用中文，ASD-STE100 风格。
- Codex 不执行 `git commit`、`git push`，不修改 `.env`、`CLAUDE.md`、`docs/`、`dev-notes/`。

## Review Focus

1. **用户换个说法问同一件事**（"邮费"与"运费"）：LIKE 查不到，模型应如实说没查到，不编造运费规则。→ Task 8 样例集 + Task 9 验收 3。
2. **一轮中的某个工具失败而另一个成功**：用户仍得到成功部分的回答，失败部分说明暂时查不到。→ Task 5 的 `test_partial_failure_keeps_other_results`。
3. **模型给出非法参数**（例如订单号含空格或 SQL 字符）：不执行查询，返回 `invalid_arguments`，不报 500。→ Task 5 的 `test_invalid_arguments_not_executed`。
4. **关键词含 `%` 或 `_`**：LIKE 把它们当普通字符，不变成通配符。→ Task 3 的 `test_faq_search_escapes_wildcards`。
5. **客户端在工具执行期间断开**：工单可能已创建，但本轮消息不写入，锁被释放。→ Task 7 的 `test_disconnect_after_tools_writes_no_messages`。

---

## 文件结构

```
docker-compose.yml  db/schema.sql  db/seed.sql  db/initdb/00-test-db.sql   Task 1
scripts/reset_db.sh                                                        Task 1
app/config.py（修改）  app/db/__init__.py  app/db/engine.py  app/db/models.py  Task 1
tests/conftest.py（修改）  tests/test_db_models.py  tests/test_config.py、tests/test_llm.py（修改）  Task 1
app/retry.py  tests/test_retry.py                                          Task 2
app/repositories/{__init__,conversations,messages,faq,tickets}.py  tests/test_repositories.py   Task 3
app/tools/{__init__,mock_data,order,product,logistics,faq,ticket,registry}.py  tests/test_tools.py   Task 4
app/tools/executor.py  tests/test_executor.py                              Task 5
app/services/history.py  app/prompts.py（修改）  tests/test_history.py     Task 6
app/locks.py  app/services/chat.py（重写）  app/api/chat.py（重写）  app/schemas.py（修改）
  删除 app/session.py、tests/test_session.py、tests/test_chat_service.py
  tests/fakes.py  tests/test_chat_api.py（重写）                            Task 7
evals/tool_selection_samples.jsonl  evals/run_tool_selection_eval.py       Task 8
scripts/demo.sh（修改）  scripts/demo2.sh  CLAUDE.md（Claude 修改）          Task 9
app/web/index.html（Vibe Coding）                                           Task 10
```

---

### Task 1: 数据库基建

**Files:**
- Create: `docker-compose.yml`、`db/schema.sql`、`db/seed.sql`、`db/initdb/00-test-db.sql`、`scripts/reset_db.sh`
- Create: `app/db/__init__.py`（空）、`app/db/engine.py`、`app/db/models.py`
- Modify: `app/config.py`、`tests/conftest.py`、`tests/test_config.py`、`tests/test_llm.py`
- Test: `tests/test_db_models.py`
- Claude（不是 Codex）在 `.env` 中加入：`DATABASE_URL=mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales?charset=utf8mb4`

**Interfaces:**
- Produces:
  - `app.config.Settings.database_url: str`（必填）
  - `app.config.test_database_url(url: str) -> str`：库名替换为 `aftersales_test`
  - 常量：`TOOL_TIMEOUT_SECONDS = 5`、`TOOL_MAX_ATTEMPTS = 3`、`TOOL_RETRY_BASE_DELAY = 0.2`、`TOOL_RETRY_MAX_DELAY = 2.0`、`TOOL_RESULT_MAX_CHARS = 1500`、`FAQ_MAX_RESULTS = 3`
  - `app.db.engine.get_sessionmaker() -> async_sessionmaker[AsyncSession]`；`app.db.engine.set_sessionmaker(sm | None) -> None`
  - `app.db.models`：`Base`、`Conversation`、`Message`、`Faq`、`Ticket`
  - fixture `db`（返回测试库的 `async_sessionmaker`，并已通过 `set_sessionmaker` 设为全局）

- [ ] **Step 1: 安装依赖**

```bash
uv add "sqlalchemy[asyncio]>=2.0" asyncmy cryptography
```

`cryptography` 是必需的：MySQL 8.4 默认认证方式 `caching_sha2_password` 需要它，否则 asyncmy 连接时抛 `RuntimeError: 'cryptography' package is required`（已实测）。

- [ ] **Step 2: 写 `db/schema.sql`**

逐字写入下面的 DDL（用户提供，不许修改任何字符）：

```sql
-- =============================================================
-- ch02 · Function Calling 工具链 · 建表 DDL
-- 本章新建:faq / conversations / messages / tickets 四张表
-- 商品、订单、物流走工具内 mock,不建表
-- 全库统一 ENGINE=InnoDB、CHARSET=utf8mb4
-- 建表顺序:先 conversations,再依赖它的 messages / tickets
-- =============================================================

-- 会话壳:一通对话的统一身份,messages / tickets 都引用它
CREATE TABLE conversations (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '会话主键',
  user_id     VARCHAR(64)     NOT NULL                COMMENT '用户标识',
  status      ENUM('进行中','已转人工','已结束') NOT NULL DEFAULT '进行中' COMMENT '处理状态',
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开启时间',
  updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  KEY idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客服会话';

-- 消息流水:一通会话底下挂 N 条,role 对齐 Chat Completions 协议
CREATE TABLE messages (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '消息主键',
  conversation_id BIGINT UNSIGNED NOT NULL                COMMENT '所属会话',
  role            ENUM('user','assistant','tool') NOT NULL COMMENT '角色:用户/助手/工具结果',
  content         TEXT            NULL                     COMMENT '消息正文,assistant 纯工具调用时可为空',
  tool_calls      JSON            NULL                     COMMENT 'assistant 消息带的工具调用申请单',
  tool_call_id    VARCHAR(64)     NULL                     COMMENT 'tool 消息对应的申请单 id,回灌时对号入座',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '产生时间',
  PRIMARY KEY (id),
  KEY idx_conversation_id (conversation_id),
  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话消息流水';

-- FAQ 问答对:query_faq 的数据源;ch03 起检索改走向量库,这张表退居原始录入
CREATE TABLE faq (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'FAQ 主键',
  question    VARCHAR(512)    NOT NULL                COMMENT '问题',
  answer      TEXT            NOT NULL                COMMENT '答案',
  category    VARCHAR(64)     NOT NULL                COMMENT '分类',
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  KEY idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='常见问答';

-- 人工工单:create_ticket 落地,工单号当业务主键
CREATE TABLE tickets (
  ticket_no       VARCHAR(32)     NOT NULL                COMMENT '工单号,如 T20260701008',
  conversation_id BIGINT UNSIGNED NOT NULL                COMMENT '关联会话,可倒查当时聊了什么',
  description     TEXT            NOT NULL                COMMENT '问题描述',
  ticket_type     ENUM('售后','投诉','咨询') NOT NULL     COMMENT '工单类型',
  status          ENUM('待处理','已处理') NOT NULL DEFAULT '待处理' COMMENT '处理状态',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (ticket_no),
  KEY idx_conversation_id (conversation_id),
  CONSTRAINT fk_tickets_conversation FOREIGN KEY (conversation_id) REFERENCES conversations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人工工单';
```

- [ ] **Step 3: 写 `db/seed.sql`（FAQ 测试数据，用户审核）**

约束：全文不出现"邮"字；ASCII 分号 `;` 只出现在语句末尾（分号用于切分语句）。逐字写入：

```sql
-- ch02 FAQ 测试数据。运费类条目只用“运费”一词，全文不出现“邮”字（验收 3 依赖此约束）。
INSERT INTO faq (question, answer, category) VALUES
('退货政策是什么？', '签收后 7 天内，商品完好且不影响二次销售的，可以申请无理由退货。质量问题的商品，签收后 15 天内可以退货。', '退换货'),
('怎么申请换货？', '在订单详情页点击“申请售后”，选择“换货”并填写原因。审核通过后按页面提示寄回商品，商家收到后寄出新商品。', '退换货'),
('退款多久到账？', '商家确认收到退货后，1 到 3 个工作日内原路退款。银行卡的到账时间以银行为准。', '退换货'),
('运费怎么算？', '单笔订单实付满 99 元免运费，不满 99 元收取 8 元运费。偏远地区另加 10 元。', '运费'),
('退货的运费谁承担？', '质量问题退货的运费由商家承担。无理由退货的运费由买家承担。', '运费'),
('怎么开发票？', '在订单详情页点击“申请开票”，填写抬头和税号。电子发票在订单完成后 3 个工作日内发送到账户。', '发票'),
('发票抬头开错了能改吗？', '电子发票开具后 30 天内，可以申请换开一次。', '发票'),
('商品坏了怎么保修？', '保修期内的质量问题可以申请免费维修。在订单详情页点击“申请售后”，选择“维修”。', '售后维修'),
('维修需要多长时间？', '商家收到商品后，7 个工作日内完成检测和维修。', '售后维修'),
('账户密码忘了怎么办？', '在登录页点击“忘记密码”，通过绑定的手机号验证后重设密码。', '账户'),
('支持哪些支付方式？', '支持微信支付、支付宝和银行卡支付。', '支付'),
('订单可以取消吗？', '未发货的订单可以在订单详情页直接取消，货款原路退回。已发货的订单需要在签收后申请退货。', '支付');
```

- [ ] **Step 4: 写 `db/initdb/00-test-db.sql`、`docker-compose.yml`、`scripts/reset_db.sh`**

`db/initdb/00-test-db.sql`：

```sql
CREATE DATABASE IF NOT EXISTS aftersales_test DEFAULT CHARACTER SET utf8mb4;
GRANT ALL PRIVILEGES ON aftersales_test.* TO 'aftersales'@'%';
FLUSH PRIVILEGES;
```

`docker-compose.yml` 要求：
- 服务 `mysql`，镜像 `mysql:8`，`container_name: aftersales-mysql`，端口 `"3307:3306"`。
- 环境变量：`MYSQL_ROOT_PASSWORD: aftersales-root`、`MYSQL_DATABASE: aftersales`、`MYSQL_USER: aftersales`、`MYSQL_PASSWORD: aftersales`。
- 命名数据卷 `aftersales-mysql-data` 挂载到 `/var/lib/mysql`。
- 挂载：`./db/initdb/00-test-db.sql:/docker-entrypoint-initdb.d/00-test-db.sql:ro`、`./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro`、`./db/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro`。
- 健康检查：`mysqladmin ping -h 127.0.0.1 -uroot -paftersales-root --silent`，间隔 5 秒，重试 20 次。
- 命令参数：`--character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci`。

`scripts/reset_db.sh`：`set -euo pipefail`；执行 `docker compose down -v`，再 `docker compose up -d --wait`；最后打印 `aftersales` 库中 faq 的行数（应为 12）。

- [ ] **Step 5: 启动数据库**

```bash
docker compose up -d --wait
docker exec aftersales-mysql mysql -uaftersales -paftersales -e "SELECT COUNT(*) FROM aftersales.faq; SHOW DATABASES LIKE 'aftersales_test';"
```

Expected：faq 计数为 12；`aftersales_test` 存在。

- [ ] **Step 6: 修改配置**

`app/config.py`：
- `Settings` 新增必填字段 `database_url: str`。
- 新增函数：

```python
from sqlalchemy.engine import make_url


def test_database_url(url: str) -> str:
    """把库名替换为 aftersales_test，其余部分不变。"""
    return make_url(url).set(database="aftersales_test").render_as_string(hide_password=False)
```

- 新增 Interfaces 中列出的 6 个常量。

修改已有测试，使其在没有真实 `.env` 时仍能构造 `Settings`：
- `tests/test_config.py` 的 `_set_required` 增加 `monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@h:3307/aftersales")`；`test_unknown_env_file_keys_are_ignored` 的 `delenv` 循环加入 `"DATABASE_URL"`，env 文件内容增加一行 `DATABASE_URL=mysql+asyncmy://u:p@h:3307/aftersales`。
- `tests/test_llm.py` 的 `_settings` 增加参数 `database_url="mysql+asyncmy://u:p@h:3307/aftersales"`。

在 `tests/test_config.py` 末尾加入：

```python
def test_test_database_url_replaces_db_name():
    from app.config import test_database_url

    url = "mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales?charset=utf8mb4"
    assert test_database_url(url) == (
        "mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales_test?charset=utf8mb4"
    )


def test_tool_constants():
    from app import config

    assert config.TOOL_TIMEOUT_SECONDS == 5
    assert config.TOOL_MAX_ATTEMPTS == 3
    assert config.TOOL_RETRY_BASE_DELAY == 0.2
    assert config.TOOL_RETRY_MAX_DELAY == 2.0
    assert config.TOOL_RESULT_MAX_CHARS == 1500
    assert config.FAQ_MAX_RESULTS == 3
```

- [ ] **Step 7: 写 `app/db/engine.py` 和 `app/db/models.py`**

`app/db/engine.py`：

```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """返回全局 sessionmaker。第一次调用时按 DATABASE_URL 创建引擎。"""
    ...


def set_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None:
    """替换全局 sessionmaker。测试用它指向测试库；传 None 恢复为按配置创建。"""
    ...
```

`get_sessionmaker` 用 `create_async_engine(settings.database_url, pool_pre_ping=True)` 和 `async_sessionmaker(engine, expire_on_commit=False)`。

`app/db/models.py`（已在 MySQL 8.4 上实测这种映射）：

```python
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, text
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ID = BIGINT(unsigned=True)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        Enum("进行中", "已转人工", "已结束"), server_default=text("'进行中'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ID, ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(Enum("user", "assistant", "tool"))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


class Faq(Base):
    __tablename__ = "faq"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(512))
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))


class Ticket(Base):
    __tablename__ = "tickets"

    ticket_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ID, ForeignKey("conversations.id"))
    description: Mapped[str] = mapped_column(Text)
    ticket_type: Mapped[str] = mapped_column(Enum("售后", "投诉", "咨询"))
    status: Mapped[str] = mapped_column(Enum("待处理", "已处理"), server_default=text("'待处理'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
```

- [ ] **Step 8: 在 `tests/conftest.py` 中加入数据库 fixture**

在现有内容之后追加（保留现有 fixture）：

```python
import asyncio
from pathlib import Path

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings, test_database_url
from app.db.engine import set_sessionmaker

ROOT = Path(__file__).resolve().parent.parent


def split_sql(sql: str) -> list[str]:
    """去掉 -- 注释行，按分号切分语句。"""
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for table in ("messages", "tickets", "conversations", "faq"):
                await conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
            for name in ("schema.sql", "seed.sql"):
                for stmt in split_sql((ROOT / "db" / name).read_text(encoding="utf-8")):
                    await conn.exec_driver_sql(stmt)
    finally:
        await engine.dispose()


async def _clear_runtime_tables(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for table in ("messages", "tickets", "conversations"):
                await conn.exec_driver_sql(f"DELETE FROM {table}")
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_db_url():
    return test_database_url(get_settings().database_url)


@pytest.fixture(scope="session")
def _test_schema(test_db_url):
    # 连不上测试库时直接失败，不跳过。
    try:
        asyncio.run(_reset_schema(test_db_url))
    except OperationalError as exc:
        pytest.fail(
            f"无法连接测试库 aftersales_test，请先执行 docker compose up -d --wait（{exc.orig}）",
            pytrace=False,
        )


@pytest.fixture
def db(_test_schema, test_db_url):
    """测试库的 sessionmaker。每个测试前清空运行时表，并设为全局 sessionmaker。"""
    asyncio.run(_clear_runtime_tables(test_db_url))
    engine = create_async_engine(test_db_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_sessionmaker(sm)
    yield sm
    set_sessionmaker(None)
    asyncio.run(engine.dispose())
```

- [ ] **Step 9: 写数据库测试 `tests/test_db_models.py`**

```python
import pytest
from sqlalchemy import select

from app.db.models import Conversation, Faq, Message
from tests.conftest import ROOT

pytestmark = pytest.mark.anyio


async def test_conversation_defaults(db):
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.commit()
        await s.refresh(conv)
    assert conv.id > 0
    assert conv.status == "进行中"


async def test_message_json_roundtrip(db):
    calls = [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.flush()
        s.add_all([
            Message(conversation_id=conv.id, role="assistant", content=None, tool_calls=calls),
            Message(conversation_id=conv.id, role="tool", content='{"ok": true}', tool_call_id="c1"),
        ])
        await s.commit()
    async with db() as s:
        rows = (await s.execute(select(Message).order_by(Message.id))).scalars().all()
    assert [(r.role, r.content, r.tool_calls, r.tool_call_id) for r in rows] == [
        ("assistant", None, calls, None),
        ("tool", '{"ok": true}', None, "c1"),
    ]


async def test_seed_faq_loaded(db):
    async with db() as s:
        questions = (await s.execute(select(Faq.question))).scalars().all()
    assert len(questions) == 12
    assert "退货政策是什么？" in questions


def test_seed_has_no_you_char_and_no_ascii_semicolon_in_values():
    body = (ROOT / "db" / "seed.sql").read_text(encoding="utf-8")
    values = "\n".join(l for l in body.splitlines() if not l.strip().startswith("--"))
    assert "邮" not in values
    assert values.rstrip().endswith(";")
    assert values.rstrip().rstrip(";").count(";") == 0
```

- [ ] **Step 10: 运行测试**

Run: `uv run pytest -q`
Expected: 全部通过（ch01 原有测试 + 本任务新增测试）。再执行 `docker compose stop`，运行 `uv run pytest tests/test_db_models.py -q`，Expected：失败并显示"无法连接测试库 aftersales_test，请先执行 docker compose up -d --wait"。最后执行 `docker compose up -d --wait` 恢复。

- [ ] **Step 11: 提交（Claude 执行）**

```bash
git add docker-compose.yml db scripts/reset_db.sh app tests pyproject.toml uv.lock
git commit -m "feat(ch02): MySQL via Docker, schema, seed and async ORM"
```

---

### Task 2: 公共指数回退重试

**Files:**
- Create: `app/retry.py`
- Test: `tests/test_retry.py`

**Interfaces:**
- Produces:
  - `backoff_delay(retry_number: int, base_delay: float, max_delay: float, rand_value: float) -> float`：`min(base_delay * 2 ** (retry_number - 1), max_delay) * (0.5 + rand_value / 2)`
  - `async retry_async(fn: Callable[[], Awaitable[T]], *, attempts: int, base_delay: float, max_delay: float, retry_on: tuple[type[BaseException], ...], sleep=asyncio.sleep, rand=random.random) -> T`

- [ ] **Step 1: 写失败的测试 `tests/test_retry.py`**

```python
import pytest

from app.retry import backoff_delay, retry_async

pytestmark = pytest.mark.anyio


def test_backoff_doubles_and_caps():
    assert [backoff_delay(n, 0.2, 2.0, 1.0) for n in (1, 2, 3, 4, 5)] == [0.2, 0.4, 0.8, 1.6, 2.0]


def test_backoff_jitter_range():
    assert backoff_delay(1, 1.0, 10.0, 0.0) == 0.5
    assert backoff_delay(1, 1.0, 10.0, 1.0) == 1.0


class Flaky:
    def __init__(self, failures, exc=TimeoutError):
        self.failures, self.exc, self.calls = failures, exc, 0

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc("boom")
        return "ok"


async def test_retries_then_succeeds_with_backoff():
    delays = []

    async def fake_sleep(d):
        delays.append(d)

    fn = Flaky(2)
    result = await retry_async(
        fn, attempts=3, base_delay=0.2, max_delay=2.0, retry_on=(TimeoutError,),
        sleep=fake_sleep, rand=lambda: 1.0,
    )
    assert result == "ok"
    assert fn.calls == 3
    assert delays == [0.2, 0.4]


async def test_raises_last_error_when_exhausted():
    async def fake_sleep(d):
        pass

    fn = Flaky(5)
    with pytest.raises(TimeoutError):
        await retry_async(fn, attempts=3, base_delay=0.1, max_delay=1.0,
                          retry_on=(TimeoutError,), sleep=fake_sleep)
    assert fn.calls == 3


async def test_does_not_retry_other_errors():
    async def fake_sleep(d):
        raise AssertionError("must not sleep")

    fn = Flaky(1, exc=ValueError)
    with pytest.raises(ValueError):
        await retry_async(fn, attempts=3, base_delay=0.1, max_delay=1.0,
                          retry_on=(TimeoutError,), sleep=fake_sleep)
    assert fn.calls == 1


async def test_single_attempt_never_sleeps():
    async def fake_sleep(d):
        raise AssertionError("must not sleep")

    fn = Flaky(1)
    with pytest.raises(TimeoutError):
        await retry_async(fn, attempts=1, base_delay=0.1, max_delay=1.0,
                          retry_on=(TimeoutError,), sleep=fake_sleep)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_retry.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.retry'`。

- [ ] **Step 3: 实现 `app/retry.py`**

按 Interfaces 的公式实现。`attempts` 小于 1 时抛 `ValueError`。第 n 次失败（n < attempts）后等待 `backoff_delay(n, ...)`，用注入的 `rand()` 取随机值。不在 `retry_on` 中的异常立即抛出。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_retry.py -q`
Expected: 6 passed。

- [ ] **Step 5: 提交（Claude 执行）**

```bash
git add app/retry.py tests/test_retry.py
git commit -m "feat(ch02): shared exponential-backoff retry helper"
```

---

### Task 3: Repositories

**Files:**
- Create: `app/repositories/__init__.py`（空）、`conversations.py`、`messages.py`、`faq.py`、`tickets.py`
- Test: `tests/test_repositories.py`

**Interfaces:**
- Consumes: `app.db.models`（Task 1）；`retry_async`（Task 2）
- Produces（函数都接收 `AsyncSession`，不自行提交，除非注明）：
  - `conversations.create(session, user_id: str) -> Conversation`：`add` + `flush`，返回带 id 的对象
  - `conversations.get_for_user(session, conversation_id: int, user_id: str) -> Conversation | None`
  - `conversations.set_status(session, conversation_id: int, status: str) -> None`
  - `messages.NewMessage`（dataclass：`role: str`、`content: str | None = None`、`tool_calls: list[dict] | None = None`、`tool_call_id: str | None = None`）
  - `messages.list_for_conversation(session, conversation_id: int) -> list[Message]`：按 id 升序
  - `messages.add_turn(session, conversation_id: int, rows: list[NewMessage]) -> None`：按顺序 `add_all`
  - `faq.search(session, keyword: str, limit: int) -> list[Faq]`：`question LIKE %kw% OR answer LIKE %kw%`，转义 `%`、`_`、`\`（`escape="\\"`），按 id 升序
  - `tickets.next_ticket_no(session, today: date) -> str`：`T` + `YYYYMMDD` + 3 位序号；当日最大序号加 1
  - `async tickets.create_ticket_record(sm: async_sessionmaker, conversation_id: int, description: str, ticket_type: str, today: date, *, sleep=asyncio.sleep, rand=random.random, next_no=next_ticket_no) -> Ticket`：每次尝试开一个新会话事务：生成工单号 → 插入工单 → 把会话状态改为"已转人工" → 提交 → `refresh`。主键冲突（`IntegrityError`）时用 `retry_async(attempts=3, base_delay=0.05, max_delay=0.5)` 重试。

- [ ] **Step 1: 写失败的测试 `tests/test_repositories.py`**

```python
from datetime import date

import pytest

from app.repositories import conversations, faq, messages, tickets
from app.repositories.messages import NewMessage

pytestmark = pytest.mark.anyio


async def _new_conversation(db, user_id="u1"):
    async with db() as s:
        conv = await conversations.create(s, user_id)
        await s.commit()
        return conv.id


async def test_get_for_user_checks_owner(db):
    cid = await _new_conversation(db, "u1")
    async with db() as s:
        assert (await conversations.get_for_user(s, cid, "u1")).id == cid
        assert await conversations.get_for_user(s, cid, "u2") is None
        assert await conversations.get_for_user(s, cid + 999, "u1") is None


async def test_add_turn_and_list_in_order(db):
    cid = await _new_conversation(db)
    calls = [{"id": "c1", "name": "query_order", "args": {"order_id": "1001"}}]
    async with db() as s:
        await messages.add_turn(s, cid, [
            NewMessage(role="user", content="订单 1001"),
            NewMessage(role="assistant", content=None, tool_calls=calls),
            NewMessage(role="tool", content='{"ok": true}', tool_call_id="c1"),
            NewMessage(role="assistant", content="已发货"),
        ])
        await s.commit()
    async with db() as s:
        rows = await messages.list_for_conversation(s, cid)
    assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1].tool_calls == calls
    assert rows[2].tool_call_id == "c1"


async def test_faq_search_hits_and_misses(db):
    async with db() as s:
        hit = await faq.search(s, "退货政策", 3)
        miss = await faq.search(s, "邮费", 3)
        many = await faq.search(s, "退货", 3)
    assert [f.question for f in hit] == ["退货政策是什么？"]
    assert miss == []
    assert len(many) == 3


async def test_faq_search_escapes_wildcards(db):
    async with db() as s:
        assert await faq.search(s, "%", 3) == []
        assert await faq.search(s, "_", 3) == []


async def test_ticket_numbers_increment(db):
    cid = await _new_conversation(db)
    today = date(2026, 10, 6)
    t1 = await tickets.create_ticket_record(db, cid, "耳机坏了", "售后", today)
    t2 = await tickets.create_ticket_record(db, cid, "快递员态度差", "投诉", today)
    assert (t1.ticket_no, t2.ticket_no) == ("T20261006001", "T20261006002")
    assert t1.status == "待处理"
    async with db() as s:
        conv = await conversations.get_for_user(s, cid, "u1")
    assert conv.status == "已转人工"


async def test_ticket_number_conflict_retries_with_backoff(db):
    cid = await _new_conversation(db)
    today = date(2026, 10, 6)
    await tickets.create_ticket_record(db, cid, "第一单", "售后", today)
    calls, delays = [], []

    async def stale_then_fresh(session, day):
        calls.append(day)
        if len(calls) == 1:
            return "T20261006001"  # 模拟并发：拿到已被占用的号
        return await tickets.next_ticket_no(session, day)

    async def fake_sleep(d):
        delays.append(d)

    t = await tickets.create_ticket_record(
        db, cid, "第二单", "投诉", today, sleep=fake_sleep, rand=lambda: 1.0, next_no=stale_then_fresh,
    )
    assert t.ticket_no == "T20261006002"
    assert delays == [0.05]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_repositories.py -q`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 4 个 repository 模块**

按 Interfaces 实现。要点：
- `faq.search` 的转义：先把 `\` 替换为 `\\`，再把 `%`、`_` 分别替换为 `\%`、`\_`，然后用 `column.like(pattern, escape="\\")`。
- `next_ticket_no` 用 `select(func.max(Ticket.ticket_no)).where(Ticket.ticket_no.like(f"T{today:%Y%m%d}%"))`；没有时序号为 1。
- `create_ticket_record` 中，冲突后的回滚通过结束该次 `async with sm() as s` 实现；每次尝试都用新会话。提交后 `await s.refresh(ticket)` 再返回。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_repositories.py -q`
Expected: 6 passed。

- [ ] **Step 5: 提交（Claude 执行）**

```bash
git add app/repositories tests/test_repositories.py
git commit -m "feat(ch02): repositories for conversations, messages, faq and tickets"
```

---

### Task 4: 5 个工具与注册表

**Files:**
- Create: `app/tools/__init__.py`（空）、`mock_data.py`、`order.py`、`product.py`、`logistics.py`、`faq.py`、`ticket.py`、`registry.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `get_sessionmaker()`（Task 1）；`faq.search`、`tickets.create_ticket_record`（Task 3）；`FAQ_MAX_RESULTS`、`TOOL_TIMEOUT_SECONDS`（Task 1）
- Produces:
  - `mock_data.order(order_id: str, today: date) -> dict`、`mock_data.product(product_id: str) -> dict`、`mock_data.logistics(order_id: str, today: date) -> dict`
  - 5 个 `@tool` 对象：`query_order`、`query_product`、`query_logistics`、`query_faq`、`create_ticket`（名称与变量名相同）
  - `registry.ToolSpec`（frozen dataclass：`tool: BaseTool`、`retryable: bool`、`timeout: float`、`inject_conversation_id: bool = False`）
  - `registry.ToolRegistry`：`register(spec)`、`get(name) -> ToolSpec | None`、`tools_for_model() -> list[BaseTool]`、`names() -> list[str]`
  - `registry.build_default_registry() -> ToolRegistry`；`registry.get_registry() -> ToolRegistry`（`lru_cache`）

**`@tool` 写法（已在 langchain-core 1.6 上实测）：**

```python
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolArg, tool
from pydantic import BaseModel, Field


class CreateTicketArgs(BaseModel):
    description: str = Field(min_length=1, max_length=500, description="问题描述，概括用户的诉求")
    ticket_type: Literal["售后", "投诉", "咨询"] = Field(description="工单类型")
    conversation_id: Annotated[int, InjectedToolArg]


@tool("create_ticket", args_schema=CreateTicketArgs)
async def create_ticket(description: str, ticket_type: str, conversation_id: Annotated[int, InjectedToolArg]) -> dict:
    """创建人工工单。用户明确要求人工，或投诉需要人工跟进时调用。"""
    ...
```

实测结果：`InjectedToolArg` 参数不出现在 `convert_to_openai_tool(create_ticket)` 和 `tool_call_schema` 中；`await tool.ainvoke(dict)` 返回函数返回值；参数不合法时抛 `pydantic.ValidationError`。

**工具定义要求：**

| 工具 | args_schema 字段 | 工具描述（docstring） |
|---|---|---|
| `query_order` | `order_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$", description="订单号")` | 按订单号查询订单状态、商品和金额。 |
| `query_product` | `product_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$", description="商品号，例如 P001")` | 按商品号查询价格、库存、保修天数和是否支持 7 天无理由退货。 |
| `query_logistics` | `order_id`（同上） | 按订单号查询承运商、运单号、物流状态、轨迹和预计送达时间。 |
| `query_faq` | `keyword: str = Field(min_length=1, max_length=20, description="取用户原话中的关键词，不要替换为同义词")` | 按关键词查询常见问题，例如退货政策、运费、发票。 |
| `create_ticket` | 见上方代码 | 见上方代码 |

- 3 个 mock 工具调用 `mock_data`，`today` 取 `date.today()`。
- `query_faq` 用 `get_sessionmaker()` 开会话，调用 `faq.search(s, keyword, FAQ_MAX_RESULTS)`，返回 `{"results": [{"question", "answer", "category"}, ...]}`。
- `create_ticket` 调用 `tickets.create_ticket_record(get_sessionmaker(), conversation_id, description, ticket_type, date.today())`，返回 `{"ticket_no": ..., "status": ...}`。
- 所有工具返回 `dict`，由执行器负责序列化。

**`mock_data` 要求：**
- 商品目录（固定）：`P001` 蓝牙耳机 299、`P002` 羊毛衫 459、`P003` 扫地机器人 1999、`P004` 电动牙刷 199、`P005` 台灯 129、`P006` 保温杯 89、`P007` 运动鞋 599、`P008` 手机壳 39（单位：元）。
- `product(product_id)`：用 `random.Random(f"product:{product_id}")`。目录内的商品用目录中的名称和价格；目录外的商品从目录中随机选一个名称，价格随机。`stock` 0–200，`warranty_days` 取 0、180、365 之一，`no_reason_return` 为布尔值。
- `order(order_id, today)`：用 `random.Random(f"order:{order_id}")`。状态从 5 种中随机选；**订单 `1001` 固定为"已发货"**。商品 1–2 件（从目录选，数量 1–2），`total` 为合计金额，`created_at` 为 `today` 前 2–20 天（格式 `YYYY-MM-DD HH:MM`）。
- `logistics(order_id, today)`：先调用 `order(order_id, today)` 得到状态，再用 `random.Random(f"logistics:{order_id}")` 生成：

  | 订单状态 | `status` | `traces` | `estimated_delivery` |
  |---|---|---|---|
  | 待付款、待发货、已取消 | 未发货 | 空列表 | null |
  | 已发货 | 运输中 | 2–4 条，最后一条不是签收 | 晚于 `today` 的日期 |
  | 已签收 | 已签收 | 3–5 条，最后一条描述含"已签收" | null |

  `carrier` 从顺丰、中通、圆通、京东物流中选；未发货时 `carrier` 和 `tracking_no` 为 null。

**注册表：**

| 工具 | retryable | timeout | inject_conversation_id |
|---|---|---|---|
| `query_order`、`query_product`、`query_logistics`、`query_faq` | True | `TOOL_TIMEOUT_SECONDS` | False |
| `create_ticket` | **False** | `TOOL_TIMEOUT_SECONDS` | **True** |

- [ ] **Step 1: 写失败的测试 `tests/test_tools.py`**

```python
from datetime import date

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import Ticket
from app.repositories import conversations
from app.tools import mock_data
from app.tools.registry import get_registry

TODAY = date(2026, 10, 6)


def test_mock_is_deterministic():
    assert mock_data.order("2002", TODAY) == mock_data.order("2002", TODAY)
    assert mock_data.product("P009") == mock_data.product("P009")
    assert mock_data.logistics("2002", TODAY) == mock_data.logistics("2002", TODAY)


def test_order_1001_is_shipped_with_traces():
    assert mock_data.order("1001", TODAY)["status"] == "已发货"
    lg = mock_data.logistics("1001", TODAY)
    assert lg["status"] == "运输中"
    assert 2 <= len(lg["traces"]) <= 4
    assert "已签收" not in lg["traces"][-1]["description"]


def test_order_and_logistics_are_consistent():
    expected = {"待付款": "未发货", "待发货": "未发货", "已取消": "未发货", "已发货": "运输中", "已签收": "已签收"}
    seen = set()
    for i in range(2000, 2200):
        oid = str(i)
        status = mock_data.order(oid, TODAY)["status"]
        lg = mock_data.logistics(oid, TODAY)
        seen.add(status)
        assert lg["status"] == expected[status]
        if lg["status"] == "未发货":
            assert lg["traces"] == [] and lg["carrier"] is None
        if lg["status"] == "已签收":
            assert "已签收" in lg["traces"][-1]["description"]
    assert seen == set(expected)


def test_catalog_product():
    p = mock_data.product("P001")
    assert (p["name"], p["price"]) == ("蓝牙耳机", 299)


def test_registry_lists_five_tools_and_flags():
    reg = get_registry()
    assert sorted(reg.names()) == ["create_ticket", "query_faq", "query_logistics", "query_order", "query_product"]
    assert reg.get("create_ticket").retryable is False
    assert reg.get("create_ticket").inject_conversation_id is True
    assert reg.get("query_faq").retryable is True
    assert reg.get("nope") is None


def test_create_ticket_hides_conversation_id_from_model():
    schema = convert_to_openai_tool(get_registry().get("create_ticket").tool)
    assert set(schema["function"]["parameters"]["properties"]) == {"description", "ticket_type"}


@pytest.mark.anyio
async def test_order_id_pattern_rejected():
    with pytest.raises(ValidationError):
        await get_registry().get("query_order").tool.ainvoke({"order_id": "1001; DROP TABLE"})


@pytest.mark.anyio
async def test_query_faq_tool(db):
    tool = get_registry().get("query_faq").tool
    hit = await tool.ainvoke({"keyword": "退货政策"})
    miss = await tool.ainvoke({"keyword": "邮费"})
    assert hit["results"][0]["question"] == "退货政策是什么？"
    assert miss == {"results": []}


@pytest.mark.anyio
async def test_create_ticket_tool_writes_row(db):
    async with db() as s:
        conv = await conversations.create(s, "u1")
        await s.commit()
    tool = get_registry().get("create_ticket").tool
    out = await tool.ainvoke({"description": "耳机坏了要人工", "ticket_type": "售后", "conversation_id": conv.id})
    assert out["ticket_no"].startswith("T") and out["status"] == "待处理"
    async with db() as s:
        row = (await s.execute(select(Ticket))).scalar_one()
    assert row.conversation_id == conv.id
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_tools.py -q`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `mock_data.py`、5 个工具模块、`registry.py`**

按上面的要求实现。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_tools.py -q`
Expected: 9 passed。

- [ ] **Step 5: 提交（Claude 执行）**

```bash
git add app/tools tests/test_tools.py
git commit -m "feat(ch02): five LangChain tools and tool registry"
```

---

### Task 5: 工具执行器

**Files:**
- Create: `app/tools/executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `ToolRegistry`、`ToolSpec`（Task 4）；`retry_async`（Task 2）；`TOOL_MAX_ATTEMPTS`、`TOOL_RETRY_BASE_DELAY`、`TOOL_RETRY_MAX_DELAY`、`TOOL_RESULT_MAX_CHARS`（Task 1）
- Produces:
  - `ToolOutcome`（dataclass：`call_id: str`、`name: str`、`ok: bool`、`message: ToolMessage`）
  - `async execute_tool_calls(tool_calls: list[dict], *, conversation_id: int, registry: ToolRegistry | None = None, sleep=asyncio.sleep, rand=random.random) -> list[ToolOutcome]`：输入是 LangChain `AIMessage.tool_calls` 格式（`{"id", "name", "args"}`），输出顺序与输入相同

**执行规则（spec 第 10.3 节）：**
1. `registry` 为 None 时用 `get_registry()`。
2. 用 `asyncio.gather` 并行执行全部调用。
3. 单个调用：
   - 名称未注册 → `unknown_tool`。
   - `args` 不是 dict → `invalid_arguments`。
   - `inject_conversation_id` 为 True 时，`args = {**args, "conversation_id": conversation_id}`（覆盖模型提供的同名参数）。
   - 每次尝试：`await asyncio.wait_for(spec.tool.ainvoke(args), spec.timeout)`。
   - `retryable` 为 True 时用 `retry_async(attempts=TOOL_MAX_ATTEMPTS, base_delay=TOOL_RETRY_BASE_DELAY, max_delay=TOOL_RETRY_MAX_DELAY, retry_on=(TimeoutError, OperationalError), sleep=sleep, rand=rand)`；False 时只尝试 1 次。
   - `pydantic.ValidationError` → `invalid_arguments`（不重试）。
   - `TimeoutError`（重试用完）→ `timeout`。
   - 其他 `Exception` → `tool_error`，`logger.exception` 记录。
4. 成功：`content = json.dumps({"ok": True, "data": result}, ensure_ascii=False, default=str)`。
5. 失败：`content = json.dumps({"ok": False, "error": code, "message": 文案}, ensure_ascii=False)`。文案：`invalid_arguments` → 参数不合法；`unknown_tool` → 工具不存在；`timeout` → 查询超时；`tool_error` → 查询失败。
6. `content` 超过 `TOOL_RESULT_MAX_CHARS` 时，截取前 `TOOL_RESULT_MAX_CHARS` 个字符，再追加 `…(结果过长，已截断)`。
7. `ToolMessage(content=content, tool_call_id=call["id"], name=call["name"])`。

- [ ] **Step 1: 写失败的测试 `tests/test_executor.py`**

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_executor.py -q`
Expected: FAIL，`ModuleNotFoundError`。

- [ ] **Step 3: 实现 `app/tools/executor.py`**

按"执行规则"实现。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_executor.py -q`
Expected: 11 passed。

- [ ] **Step 5: 提交（Claude 执行）**

```bash
git add app/tools/executor.py tests/test_executor.py
git commit -m "feat(ch02): tool executor with validation, timeout and backoff retry"
```

---

### Task 6: 历史转换与 Prompt

**Files:**
- Create: `app/services/history.py`
- Modify: `app/prompts.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `Message`（Task 1）；`NewMessage`（Task 3）；`build_history`（ch01）
- Produces:
  - `history.to_langchain(rows: Sequence[Message]) -> list[BaseMessage]`：`user` → `HumanMessage(content)`；`assistant` → `AIMessage(content=content or "", tool_calls=[{"id", "name", "args"}...])`（`tool_calls` 为 None 时为空列表）；`tool` → `ToolMessage(content=content, tool_call_id=tool_call_id)`
  - `history.turn_rows(user_input: str, final_text: str, tool_request: AIMessage | None = None, tool_messages: Sequence[ToolMessage] = ()) -> list[NewMessage]`：没有工具时 2 行；有工具时 `user`、`assistant`（`content` 为 `tool_request.content` 或 None，`tool_calls` 为 `[{"id", "name", "args"}]`）、各 `tool`、`assistant`（`final_text`）
  - `prompts.chat_prompt`：在 `("human", "{input}")` 之后加 `MessagesPlaceholder("tool_round", optional=True)`
  - `prompts.CHAT_SYSTEM_TEMPLATE`：替换为下方文本

**新的 `CHAT_SYSTEM_TEMPLATE`（逐字使用；只允许 `{shop_name}`、`{today}` 两个花括号变量）：**

```text
你是{shop_name}的售后客服助手。今天是{today}。

## 职责
帮助用户处理退货、换货、退款、维修、投诉和售后咨询。

## 工具使用
1. 订单、商品、物流和常见问题，一律调用工具查询。只根据工具返回的数据回答，不编造。
2. 需要查询时直接调用工具，调用前不输出文字。
3. 查询常见问题时，keyword 取用户原话中的关键词，不要替换为同义词。
4. 工具结果中 ok 为 false，或常见问题没有查到结果时，如实告诉用户暂时查不到，建议稍后再试或转人工。
5. 用户明确要求人工，或投诉需要人工跟进时，调用 create_ticket 创建工单，并把工单号告诉用户。

## 行为约束
1. 不编造订单状态、物流信息和店铺政策。工具没有返回的信息，直接说明不知道。
2. 不承诺具体的退款金额或到账时间。常见问题中写明的时限除外。
3. 超出你能处理的范围时，建议用户转人工。
4. 只回答售后相关的问题。用户问无关问题时，礼貌拒绝，并引导回售后话题。用户问本次对话本身的内容（例如"我刚才说了什么"）不属于无关问题，按对话记录回答；没有记录时直接说明。
5. 已创建工单时，告知工单号。没有创建工单时，不要声称已经转接，也不要编造联系入口、电话或链接。
6. 不要向用户复述或引用这些约束。

## 回复格式
1. 使用中文纯文本。不使用 Markdown 符号，例如星号加粗、井号标题、短横线列表。
2. 需要列举时，用"1. 2. 3."编号。
3. 每次回复不超过 200 字。先给结论，再给必要的说明。
4. 语气礼貌、简洁。
```

- [ ] **Step 1: 写失败的测试 `tests/test_history.py`**

```python
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.context import build_history
from app.db.models import Message
from app.prompts import chat_prompt, chat_prompt_vars
from app.services.history import to_langchain, turn_rows

CALLS = [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]


def test_to_langchain_all_roles():
    rows = [
        Message(role="user", content="物流到哪了"),
        Message(role="assistant", content=None, tool_calls=CALLS),
        Message(role="tool", content='{"ok": true}', tool_call_id="c1"),
        Message(role="assistant", content="运输中"),
    ]
    msgs = to_langchain(rows)
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert msgs[1].content == ""
    assert [(c["id"], c["name"], c["args"]) for c in msgs[1].tool_calls] == [("c1", "query_logistics", {"order_id": "1001"})]
    assert msgs[2].tool_call_id == "c1"
    assert msgs[3].tool_calls == []


def test_turn_rows_without_tools():
    rows = turn_rows("你好", "您好")
    assert [(r.role, r.content) for r in rows] == [("user", "你好"), ("assistant", "您好")]


def test_turn_rows_with_tools_roundtrip():
    request = AIMessage(content="", tool_calls=CALLS)
    tool_msgs = [ToolMessage(content='{"ok": true}', tool_call_id="c1")]
    rows = turn_rows("物流到哪了", "运输中", request, tool_msgs)
    assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1].content is None and rows[1].tool_calls == CALLS
    assert rows[2].tool_call_id == "c1"
    back = to_langchain([Message(role=r.role, content=r.content, tool_calls=r.tool_calls,
                                 tool_call_id=r.tool_call_id) for r in rows])
    assert isinstance(back[2], ToolMessage)


def test_trim_keeps_tool_pairs_together():
    def turn(i):
        return [HumanMessage(f"问{i}" * 40), AIMessage(content="", tool_calls=[{"id": f"c{i}", "name": "query_order", "args": {}}]),
                ToolMessage(content=f"结果{i}" * 40, tool_call_id=f"c{i}"), AIMessage(f"答{i}" * 40)]
    history = turn(1) + turn(2) + turn(3)
    for budget in range(200, 1200, 37):
        out = build_history(history, "系统", "新问题", budget)
        if out:
            assert isinstance(out[0], HumanMessage)
        ids = {m.tool_call_id for m in out if isinstance(m, ToolMessage)}
        requested = {c["id"] for m in out if isinstance(m, AIMessage) for c in m.tool_calls}
        assert ids == requested


def test_prompt_tool_round_placeholder_and_rules():
    msgs = chat_prompt.invoke({**chat_prompt_vars(date(2026, 10, 6)), "history": [],
                               "input": "q", "tool_round": [AIMessage(content="", tool_calls=CALLS),
                                                            ToolMessage(content="{}", tool_call_id="c1")]}).to_messages()
    assert [type(m) for m in msgs] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    assert "create_ticket" in msgs[0].content
    assert "不要替换为同义词" in msgs[0].content
    without = chat_prompt.invoke({**chat_prompt_vars(date(2026, 10, 6)),
                                  "history": [], "input": "q"}).to_messages()
    assert [type(m) for m in without] == [SystemMessage, HumanMessage]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_history.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.services.history'`。

- [ ] **Step 3: 实现 `app/services/history.py`，修改 `app/prompts.py`**

按 Interfaces 实现。`EXTRACT_SYSTEM_PROMPT` 不改。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest -q`
Expected: 全部通过（含 ch01 的 `tests/test_prompts.py`）。

- [ ] **Step 5: 提交（Claude 执行）**

```bash
git add app/services/history.py app/prompts.py tests/test_history.py
git commit -m "feat(ch02): message history conversion and tool-aware prompt"
```

---

### Task 7: 聊天编排与接口改造

**Files:**
- Create: `app/locks.py`、`tests/fakes.py`
- Rewrite: `app/services/chat.py`、`app/api/chat.py`、`tests/test_chat_api.py`
- Modify: `app/schemas.py`（`ChatRequest`）、`tests/conftest.py`（删除 `store` 和 `use_model` fixture 及其依赖的导入）
- Delete: `app/session.py`、`tests/test_session.py`、`tests/test_chat_service.py`

**Interfaces:**
- Consumes: Task 1–6 的全部接口；`get_chat_model`（ch01）；`build_history`、`BudgetExceeded`（ch01）
- Produces:
  - `app.locks.LockRegistry`：`get(conversation_id: int) -> asyncio.Lock`；`app.locks.get_lock_registry() -> LockRegistry`（FastAPI 依赖，返回模块单例）
  - `app.schemas.ChatRequest`：`session_id: str | None`（`^\d{1,19}$`）、`user_id: str`（`^[A-Za-z0-9_-]{1,64}$`）、`message`（同 ch01）
  - `app.services.chat.ChatTurn`（dataclass：`conversation_id: int`、`history: list[BaseMessage]`、`user_input: str`、`today: date`）
  - `app.services.chat.stream_reply(turn, model: BaseChatModel, *, execute=execute_tool_calls) -> AsyncIterator[tuple[str, dict]]`
  - `app.api.chat.prepare_chat_turn`（yield 依赖）；`get_token_budget`、`get_today`（沿用）

**`prepare_chat_turn` 流程（spec 7.1）：**
1. `sm = get_sessionmaker()`，开会话：
   - 有 `session_id`：`conversations.get_for_user(s, int(session_id), user_id)`；None 时抛 `HTTPException(404, detail={"code": "conversation_not_found", "message": "会话不存在或已失效"})`。
   - 没有：`conversations.create(s, user_id)` 并提交。
   - 读取 `messages.list_for_conversation`，用 `history.to_langchain` 转换。
2. `lock = locks.get(conversation_id)`；`lock.locked()` 时抛 409（文案同 ch01）。
3. `build_history(...)`；`BudgetExceeded` 时抛 422（同 ch01）。
4. `await lock.acquire()`；`try: yield ChatTurn(...)` `finally: lock.release()`。依赖保持默认 `scope="request"`。

**`stream_reply` 流程（spec 7.2）：**
1. 产出 `("session", {"session_id": str(turn.conversation_id)})`。
2. `vars = {**chat_prompt_vars(turn.today), "history": turn.history, "input": turn.user_input}`。
3. 第 1 次：`async for chunk in (chat_prompt | model.bind_tools(get_registry().tools_for_model(), tool_choice="auto")).astream(vars)`：
   - `chunk.content` 是非空字符串时：产出 `("token", {"text": ...})`，累加到 `first_text`。
   - `gathered = chunk if gathered is None else gathered + chunk`。
4. `tool_calls = gathered.tool_calls if gathered else []`。
5. 没有工具：`first_text` 为空 → `logger.warning` + 产出 error；否则写库（`turn_rows(user_input, first_text)`）后产出 `("done", {"finish_reason": "stop"})`。
6. 有工具：
   1. `request = AIMessage(content=first_text, tool_calls=tool_calls)`。
   2. 产出 `("tool_start", {"tools": [{"id", "name", "args"}...]})`。
   3. `outcomes = await execute(tool_calls, conversation_id=turn.conversation_id)`。
   4. 产出 `("tool_end", {"tools": [{"id": o.call_id, "name": o.name, "ok": o.ok}...]})`。
   5. 第 2 次：`async for chunk in (chat_prompt | model).astream({**vars, "tool_round": [request, *[o.message for o in outcomes]]})`，非空 content 产出 token 并累加到 `final_text`。
   6. `final_text` 为空 → warning + error；否则写库（`turn_rows(user_input, final_text, request, [o.message ...])`），产出 done。
7. 写库：`async with get_sessionmaker()() as s: await messages.add_turn(s, conversation_id, rows); await s.commit()`。
8. 第 1 次或第 2 次调用抛 `Exception`：`logger.exception`，产出 `("error", UPSTREAM_ERROR)`，不写库。不捕获 `BaseException`。

**`tests/fakes.py`（逐字写入；已在 langchain-core 1.6 上实测：`bind_tools` 返回的副本与原对象共享 `scripts` 和 `recorder`，chunk 用 `+` 累积出完整 `tool_calls`）：**

```python
"""测试用的脚本化聊天模型。"""

import asyncio
import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGenerationChunk


class Recorder(list):
    """记录每次模型调用：{"messages": [...], "tools": [工具名...]}。"""


class ScriptedChatModel(BaseChatModel):
    """每次调用消费 scripts 中的一个列表。

    列表元素：AIMessageChunk 依次流出；Exception 在该位置抛出；asyncio.Event 在该位置等待。
    """

    scripts: list
    recorder: Any = None
    bound_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("只支持流式调用")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        if self.recorder is not None:
            self.recorder.append({"messages": list(messages), "tools": [t.name for t in self.bound_tools]})
        for item in self.scripts.pop(0):
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, asyncio.Event):
                await item.wait()
                continue
            yield ChatGenerationChunk(message=item)


def text(s: str) -> list:
    """把文字拆成逐字 chunk。"""
    return [AIMessageChunk(content=ch) for ch in s]


def tools(*calls: tuple[str, str, dict]) -> list:
    """生成一轮 tool_call chunk。每个参数为 (id, name, args)。"""
    return [
        AIMessageChunk(content="", tool_call_chunks=[tool_call_chunk(
            name=name, args=json.dumps(args, ensure_ascii=False), id=cid, index=i)])
        for i, (cid, name, args) in enumerate(calls)
    ]
```

**`tests/conftest.py` 新增 fixture（替换 ch01 的 `store`、`use_model`）：**

```python
from app.locks import LockRegistry, get_lock_registry
from tests.fakes import Recorder, ScriptedChatModel


@pytest.fixture
def locks():
    reg = LockRegistry()
    app.dependency_overrides[get_lock_registry] = lambda: reg
    app.dependency_overrides[get_today] = lambda: date(2026, 10, 6)
    yield reg
    app.dependency_overrides.clear()


@pytest.fixture
def use_script(locks):
    """用法：rec = use_script([第1次调用的chunks], [第2次调用的chunks], ...)。"""

    def _use(*scripts):
        rec = Recorder()
        model = ScriptedChatModel(scripts=list(scripts), recorder=rec)
        app.dependency_overrides[get_chat_model] = lambda: model
        return rec

    return _use
```

- [ ] **Step 1: 写失败的测试 `tests/test_chat_api.py`（整体替换 ch01 版本）**

```python
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from sqlalchemy import select

from app.api.chat import get_token_budget
from app.db.models import Message, Ticket
from app.llm import get_chat_model
from app.main import app
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


async def test_plain_answer_single_call(client, db, use_script):
    rec = use_script(text("您好"))
    r, ev = await chat(client, "你好")
    assert ev[0][0] == "session" and ev[0][1]["session_id"].isdigit()
    assert ev[1:] == [("token", {"text": "您"}), ("token", {"text": "好"}), ("done", {"finish_reason": "stop"})]
    assert len(rec) == 1 and len(rec[0]["tools"]) == 5
    assert [(m.role, m.content) for m in await rows(db)] == [("user", "你好"), ("assistant", "您好")]
    assert "\\u" not in r.text


async def test_tool_round_events_and_persistence(client, db, use_script):
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    names = [e for e, _ in ev]
    assert names == ["session", "tool_start", "tool_end", "token", "token", "token", "done"]
    assert ev[1][1] == {"tools": [{"id": "c1", "name": "query_logistics", "args": {"order_id": "1001"}}]}
    assert ev[2][1] == {"tools": [{"id": "c1", "name": "query_logistics", "ok": True}]}
    assert rec[1]["tools"] == []
    second_input = rec[1]["messages"]
    assert isinstance(second_input[-2], AIMessage) and isinstance(second_input[-1], ToolMessage)
    assert json.loads(second_input[-1].content)["data"]["status"] == "运输中"
    saved = await rows(db)
    assert [m.role for m in saved] == ["user", "assistant", "tool", "assistant"]
    assert saved[1].content is None and saved[1].tool_calls[0]["name"] == "query_logistics"
    assert saved[2].tool_call_id == "c1" and saved[3].content == "运输中"


async def test_parallel_tools_in_one_round(client, db, use_script):
    use_script(tools(("c1", "query_order", {"order_id": "1001"}), ("c2", "query_logistics", {"order_id": "1001"})),
               text("好"))
    _, ev = await chat(client, "订单 1001 买了什么、到哪了")
    assert [t["name"] for t in ev[1][1]["tools"]] == ["query_order", "query_logistics"]
    assert [m.role for m in await rows(db)] == ["user", "assistant", "tool", "tool", "assistant"]


async def test_create_ticket_injects_conversation(client, db, use_script):
    use_script(tools(("c1", "create_ticket", {"description": "要人工", "ticket_type": "投诉"})), text("已建单"))
    _, ev = await chat(client, "我要投诉，转人工")
    cid = int(ev[0][1]["session_id"])
    assert ev[2][1]["tools"][0]["ok"] is True
    async with db() as s:
        ticket = (await s.execute(select(Ticket))).scalar_one()
    assert ticket.conversation_id == cid


async def test_second_turn_sees_previous_tool_result(client, db, use_script):
    rec = use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"), text("明天到"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    sid = ev[0][1]["session_id"]
    await chat(client, "那哪天到？", session_id=sid)
    third = rec[2]["messages"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in third)


async def test_other_users_conversation_is_404(client, db, use_script):
    use_script(text("好"))
    _, ev = await chat(client, "你好", user_id="alice")
    r, _ = await chat(client, "你好", session_id=ev[0][1]["session_id"], user_id="bob")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conversation_not_found"


async def test_unknown_session_is_404(client, db, use_script):
    use_script(text("好"))
    r, _ = await chat(client, "你好", session_id="999999")
    assert r.status_code == 404


async def test_upstream_error_in_first_call_writes_nothing(client, db, use_script):
    use_script([*text("您"), RuntimeError("boom")])
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_upstream_error_in_second_call_writes_nothing(client, db, use_script):
    use_script(tools(("c1", "query_order", {"order_id": "1001"})), [RuntimeError("boom")])
    _, ev = await chat(client, "订单 1001")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_empty_reply_is_error(client, db, use_script):
    use_script([AIMessageChunk(content="")])
    _, ev = await chat(client, "你好")
    assert ev[-1] == UPSTREAM_ERROR
    assert await rows(db) == []


async def test_budget_exceeded(client, db, use_script):
    use_script(text("x"))
    app.dependency_overrides[get_token_budget] = lambda: 10
    r, _ = await chat(client, "你好")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "budget_exceeded"


async def test_validation(client, db, use_script):
    use_script(text("x"))
    for body in ({"user_id": "u1", "message": "  "}, {"message": "hi"},
                 {"user_id": "u 1", "message": "hi"}, {"user_id": "u1", "message": "hi", "session_id": "abc"}):
        assert (await client.post("/chat/stream", json=body)).status_code == 422


async def test_lock_held_during_stream_and_released_after(client, db, use_script, locks):
    gate = asyncio.Event()
    use_script([*text("a"), gate, *text("b")])
    task = asyncio.create_task(chat(client, "hi"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if any(l.locked() for l in locks._locks.values()):
            break
    cid = next(k for k, l in locks._locks.items() if l.locked())
    busy, _ = await chat(client, "again", session_id=str(cid))
    assert busy.status_code == 409
    gate.set()
    _, ev = await task
    assert ev[-1] == ("done", {"finish_reason": "stop"})
    assert not locks.get(cid).locked()


async def test_lock_released_when_model_dependency_fails(client, db, locks):
    def boom():
        raise RuntimeError("config error")

    app.dependency_overrides[get_chat_model] = boom
    with pytest.raises(RuntimeError):
        await chat(client, "你好")
    assert not any(l.locked() for l in locks._locks.values())


async def test_disconnect_after_tools_writes_no_messages(db, locks):
    from app.services.chat import ChatTurn, stream_reply
    from app.repositories import conversations
    from tests.fakes import ScriptedChatModel
    from datetime import date

    async with db() as s:
        conv = await conversations.create(s, "u1")
        await s.commit()
    model = ScriptedChatModel(scripts=[tools(("c1", "create_ticket", {"description": "人工", "ticket_type": "投诉"})),
                                       text("已建单")])
    gen = stream_reply(ChatTurn(conversation_id=conv.id, history=[], user_input="转人工", today=date(2026, 10, 6)), model)
    names = []
    async for name, _ in gen:
        names.append(name)
        if name == "tool_end":
            break
    await gen.aclose()
    assert names == ["session", "tool_start", "tool_end"]
    assert await rows(db) == []
    async with db() as s:
        assert (await s.execute(select(Ticket))).scalar_one().conversation_id == conv.id
```

**注意：** `LockRegistry` 内部用属性 `_locks: dict[int, asyncio.Lock]` 保存锁（测试直接读取）。

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_chat_api.py -q`
Expected: FAIL（`app.locks` 不存在、请求体缺少 `user_id` 等）。

- [ ] **Step 3: 实现**

1. 新建 `app/locks.py`、`tests/fakes.py`。
2. 修改 `app/schemas.py` 的 `ChatRequest`。
3. 重写 `app/services/chat.py`、`app/api/chat.py`。
4. 修改 `tests/conftest.py`：删除 `store`、`use_model` fixture 和 `SessionStore`、`get_session_store` 的导入；加入 `locks`、`use_script` fixture。
5. 删除 `app/session.py`、`tests/test_session.py`、`tests/test_chat_service.py`。
6. `evals/run_chat_samples.py` 不改（它不使用会话）。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest -q`
Expected: 全部通过。

- [ ] **Step 5: 提交（Claude 执行）**

```bash
git add -A app tests
git commit -m "feat(ch02): tool-calling chat orchestration persisted to MySQL"
```

---

### Task 7.5（Claude 执行）：真实上游冒烟

Claude 启动服务，用 curl 走一遍"订单 1001 的物流到哪了"和"退货政策是什么"，检查事件流和 `messages` 表。发现问题时交 Codex 修复。

---

### Task 8: 工具选择样例集验证（代替 TDD）

**Files:**
- Create: `evals/tool_selection_samples.jsonl`、`evals/run_tool_selection_eval.py`
- Modify（仅在验证不达标时）: `app/prompts.py` 的 `CHAT_SYSTEM_TEMPLATE`、`app/tools/*.py` 的工具描述

**Interfaces:**
- Consumes: `get_chat_model()`、`chat_prompt`、`chat_prompt_vars`、`get_registry()`
- Produces: `uv run python evals/run_tool_selection_eval.py`：打印每条结果和两个指标；未达标时退出码为 1

- [ ] **Step 1: 写样例集（用户审核）**

`evals/tool_selection_samples.jsonl`，每行 `{"text", "expected_tools", "faq_keyword"}`。`expected_tools` 是工具名集合（列表，顺序无关）；`faq_keyword` 只在期望调用 `query_faq` 时给出，表示 keyword 必须包含的原词。逐字写入：

```jsonl
{"text": "订单 1001 的物流到哪了", "expected_tools": ["query_logistics"], "faq_keyword": null}
{"text": "订单 1001 买的是什么？物流到哪了？", "expected_tools": ["query_order", "query_logistics"], "faq_keyword": null}
{"text": "帮我看下订单 2002 现在什么状态", "expected_tools": ["query_order"], "faq_keyword": null}
{"text": "订单 3003 的快递单号是多少", "expected_tools": ["query_logistics"], "faq_keyword": null}
{"text": "订单 1001 什么时候能到", "expected_tools": ["query_logistics"], "faq_keyword": null}
{"text": "商品 P003 支持七天无理由吗", "expected_tools": ["query_product"], "faq_keyword": null}
{"text": "P001 这款耳机保修多久", "expected_tools": ["query_product"], "faq_keyword": null}
{"text": "退货政策是什么", "expected_tools": ["query_faq"], "faq_keyword": "退货"}
{"text": "邮费是多少", "expected_tools": ["query_faq"], "faq_keyword": "邮费"}
{"text": "怎么开发票", "expected_tools": ["query_faq"], "faq_keyword": "发票"}
{"text": "账户密码忘了怎么办", "expected_tools": ["query_faq"], "faq_keyword": "密码"}
{"text": "你好", "expected_tools": [], "faq_keyword": null}
{"text": "谢谢，没有别的问题了", "expected_tools": [], "faq_keyword": null}
{"text": "帮我写一首关于秋天的诗", "expected_tools": [], "faq_keyword": null}
{"text": "我要投诉你们的快递员，给我转人工", "expected_tools": ["create_ticket"], "faq_keyword": null}
{"text": "我不想跟机器人说话，转人工处理", "expected_tools": ["create_ticket"], "faq_keyword": null}
```

- [ ] **Step 2: 写 `evals/run_tool_selection_eval.py`**

要求：
1. 参照 `evals/run_extract_eval.py` 处理 `sys.path` 和路径。
2. 对每条样例只执行第 1 次调用：`await (chat_prompt | get_chat_model().bind_tools(get_registry().tools_for_model(), tool_choice="auto")).ainvoke({**chat_prompt_vars(date.today()), "history": [], "input": text})`。**不执行工具。**
3. `asyncio.Semaphore(4)` 限制并发。
4. 每条打印：序号、✅/❌、期望工具 → 实际工具、`query_faq` 的 keyword（如有）。
5. 指标 1：工具集合完全匹配率。指标 2：`faq_keyword` 不为 null 的样例中，实际 `query_faq` 的 keyword 包含 `faq_keyword` 的比例。
6. 通过标准：指标 1 ≥ 0.9，且指标 2 == 1.0。

- [ ] **Step 3: Claude 运行验证 3 轮**

Run: `uv run python evals/run_tool_selection_eval.py`（3 次）
Expected: 3 轮都达标。

- [ ] **Step 4: 如果不达标**

Claude 分析错例，写出 Prompt 或工具描述的修改方案；Codex 修改；重跑。每轮结果记入 dev-notes。不许改标注来通过。

- [ ] **Step 5: 提交（Claude 执行）**

```bash
git add evals app/prompts.py app/tools
git commit -m "feat(ch02): tool selection eval set"
```

---

### Task 9: 验收脚本与文档

**Files:**
- Modify: `scripts/demo.sh`（适配新请求体：带 `user_id`，不再自定义 `session_id`，从 `session` 事件取 ID）
- Create: `scripts/demo2.sh`
- Modify（Claude）: `CLAUDE.md`

- [ ] **Step 1: 改 `scripts/demo.sh`，写 `scripts/demo2.sh`**

`scripts/demo.sh`：`user_id` 取 `demo-$(date +%s)`；第 1 轮不传 `session_id`，用 `sed` 或 `python3 -c` 从输出的 `event: session` 下一行解析 `session_id`，第 2 轮带上。其余不变。

`scripts/demo2.sh`：
1. 健康检查失败时提示启动命令并退出 1。
2. 3 项验收，每项新开会话（不传 `session_id`），`user_id` 为 `demo2-<时间戳>`：
   - 验收 1：`订单 1001 的物流到哪了`
   - 验收 2：`退货政策是什么`
   - 验收 3：`邮费是多少`
3. 每项打印完整 SSE 输出，再用 `docker exec aftersales-mysql mysql -uaftersales -paftersales aftersales -e "SELECT role, LEFT(content, 60), tool_calls, tool_call_id FROM messages WHERE conversation_id=<id> ORDER BY id"` 打印本会话写入的消息。

- [ ] **Step 2: Claude 运行验收**

1. `docker compose up -d --wait`
2. `uv run uvicorn app.main:app --port 8000`
3. `bash scripts/demo2.sh`、`bash scripts/demo.sh`

Expected：
- 验收 1：`tool_start` 中有 `query_logistics`，回答中的物流状态与工具结果一致；数据库有 4 条消息。
- 验收 2：`query_faq` 的 keyword 含"退货"，回答引用 7 天、15 天的政策。
- 验收 3：`query_faq` 的 keyword 为"邮费"，工具结果为空，回答说明暂时查不到，不编造运费规则。
- `demo.sh`：ch01 的 3 项验收仍通过。

- [ ] **Step 3: Claude 更新 `CLAUDE.md`**

- 常用命令：`docker compose up -d --wait`、`bash scripts/reset_db.sh`、`bash scripts/demo2.sh`、`uv run python evals/run_tool_selection_eval.py`；说明 `uv run pytest` 需要先启动数据库。
- 架构：数据库层、工具层、两次调用编排；必须保持的约束（DDL 唯一来源、`exec_driver_sql`、`refresh`、测试 `NullPool`、`create_ticket` 不重试、第 2 次调用不绑定工具）。

- [ ] **Step 4: 提交（Claude 执行）**

```bash
git add scripts CLAUDE.md
git commit -m "docs(ch02): acceptance demo and project commands"
```

---

### Task 10: 聊天页工具徽章（Vibe Coding）

不走 TDD 和 code review。Claude 按 spec 第 11 节和用户的描述，把需求交给 Codex：
- 页面生成匿名 `user_id` 存 `localStorage`，每次请求带上。
- `tool_start` → 在当前助手气泡顶部显示工具徽章（工具中文名 + 进行中动画）；`tool_end` → 更新为成功或失败样式。
- 404 `conversation_not_found` → 提示会话已失效，自动开始新对话。

Claude 用 Playwright 驱动本机 Chrome 实测 3 项验收并截图，然后提交。用户描述效果后继续调整。
