# ch02 Function Calling 工具链：设计规格

- 日期：2026-10-06
- 状态：待用户审阅
- 分支：`ch02`
- 前置：ch01（`docs/superpowers/specs/2026-10-06-ch01-pure-chat-design.md`）。本文只写新增和变化的部分；没有提到的 ch01 行为保持不变。

## 1. 目标与验收标准

**目标：** 让客服聊天能查数据。模型用 Function Calling 自己决定调用哪个工具，后端执行工具后把结果回灌给模型，模型据此回答。工具链接在现有的 `/chat/stream` 和聊天页上。

**验收标准：**

1. 浏览器打开聊天页，问"订单 1001 的物流到哪了"。气泡带工具徽章（`query_logistics`），回答与工具返回的数据一致。
2. 问"退货政策是什么"。`query_faq` 查到 FAQ 并据此作答。
3. 问"邮费是多少"。`query_faq` 用关键词"邮费"查表，查不到。这个漏召回是预期结果，记入 dev-notes，留给后续章节用向量检索解决。

**本章不做：** 多轮自动循环的 Agent Loop；向量检索和 RAG；真实的电商和物流接口；用户登录鉴权。

## 2. 技术栈

| 项 | 选择 |
|---|---|
| Web | FastAPI（沿用） |
| ORM | SQLAlchemy 2.x 异步：`create_async_engine`、`async_sessionmaker`、`AsyncSession`（已用 Context7 核对） |
| 数据库 | MySQL 8，Docker Compose 启动，宿主端口 3307 |
| 驱动 | asyncmy，URL 形如 `mysql+asyncmy://user:pass@host:port/db?charset=utf8mb4`（已用 Context7 核对） |
| 工具 | LangChain `@tool`、`InjectedToolArg`、`bind_tools`（已用 Context7 核对） |

## 3. 配置

- `.env` 新增 1 个变量（用户已同意）：`DATABASE_URL`，例如 `mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales?charset=utf8mb4`。
- 测试库地址由 `DATABASE_URL` 推导：库名替换为 `aftersales_test`。不新增变量。
- 新增代码常量：

| 常量 | 值 | 用途 |
|---|---|---|
| `TOOL_TIMEOUT_SECONDS` | `5` | 单次工具调用超时 |
| `TOOL_MAX_ATTEMPTS` | `3` | 可重试工具的最多尝试次数 |
| `TOOL_RETRY_BASE_DELAY` | `0.2` | 工具重试回退基数（秒） |
| `TOOL_RETRY_MAX_DELAY` | `2.0` | 工具重试回退上限（秒） |
| `TOOL_RESULT_MAX_CHARS` | `1500` | 单条工具结果的最大字符数 |
| `FAQ_MAX_RESULTS` | `3` | `query_faq` 最多返回条数 |

## 4. 数据库

### 4.1 Docker Compose

`docker-compose.yml` 定义一个服务：

- 镜像 `mysql:8`，容器名 `aftersales-mysql`，端口 `3307:3306`，命名数据卷保存数据。
- 环境变量：`MYSQL_ROOT_PASSWORD`、`MYSQL_DATABASE=aftersales`、`MYSQL_USER=aftersales`、`MYSQL_PASSWORD=aftersales`（仅本地开发）。
- 健康检查：`mysqladmin ping`。
- 初始化脚本挂载到 `/docker-entrypoint-initdb.d/`，按文件名顺序执行，只在数据卷为空时执行一次：
  1. `00-test-db.sql`：创建 `aftersales_test` 库，并把它的全部权限授予 `aftersales` 用户。
  2. `01-schema.sql`：挂载 `db/schema.sql`。
  3. `02-seed.sql`：挂载 `db/seed.sql`。

### 4.2 表结构

`db/schema.sql` 是用户提供的 DDL，**原样保存，是表结构的唯一来源**。4 张表：`conversations`、`messages`、`faq`、`tickets`。ENGINE=InnoDB，CHARSET=utf8mb4。

- SQLAlchemy 模型（`app/db/models.py`）只映射这 4 张表，**不执行 `create_all`**。
- 模型中的 ENUM 值、可空性、默认值必须与 DDL 一致。

### 4.3 测试数据

`db/seed.sql` 只灌入 FAQ（约 12 条）。约束：

- 覆盖分类：退换货、运费、发票、售后维修、账户、支付。
- 包含问题"退货政策是什么？"。
- 运费类条目只用"运费"一词。**全部 question 和 answer 中都不出现"邮费"和"邮"字**。原因：验收 3 要求"邮费"的 LIKE 查询真实落空。
- 用户审核数据后才进入实现。

`conversations`、`messages`、`tickets` 不灌数据，由运行时写入。

### 4.4 重建脚本

`scripts/reset_db.sh`：删除 Compose 数据卷并重新启动容器，使初始化脚本重新执行。

## 5. 架构与模块

```
docker-compose.yml
db/schema.sql  db/seed.sql  db/initdb/00-test-db.sql
scripts/reset_db.sh
app/
  config.py              新增 database_url 与工具常量
  retry.py               公共指数回退重试函数
  locks.py               按 conversation_id 的进程内锁
  db/engine.py           引擎与 sessionmaker；FastAPI 依赖 get_sessionmaker
  db/models.py           4 张表的 ORM 映射
  repositories/          conversations.py  messages.py  faq.py  tickets.py
  tools/
    mock_data.py         订单、商品、物流的确定性 mock 数据
    order.py product.py logistics.py faq.py ticket.py   5 个 @tool
    registry.py          工具注册表
    executor.py          执行一轮 tool_calls
  services/history.py    数据库消息 ↔ LangChain 消息；裁剪
  services/chat.py       一轮对话的编排
  api/chat.py            预检依赖改为读写数据库
  session.py             删除（由 locks.py 和数据库替代）
```

分层：

```
api → services → repositories → db
         ↘ tools (registry, executor) → repositories
         ↘ llm, prompts, history
```

- `repositories` 只负责 SQL，不依赖 LangChain。
- `tools` 只负责业务查询，不依赖 SSE。
- `services/chat.py` 是唯一了解"两次调用 + 工具执行"流程的模块。
- 每个数据库操作用 `async_sessionmaker` 开一个短事务。流式期间不持有数据库连接。

## 6. 接口变化

### 6.1 `POST /chat/stream` 请求体

```json
{"session_id": "可选，数字字符串", "user_id": "必填", "message": "必填，1–2000 字符"}
```

| 字段 | 校验 |
|---|---|
| `session_id` | 可选；`^\d{1,19}$` |
| `user_id` | 必填；`^[A-Za-z0-9_-]{1,64}$` |
| `message` | 同 ch01 |

### 6.2 SSE 事件

| 顺序 | event | data |
|---|---|---|
| 1 | `session` | `{"session_id": "123"}`（`conversations.id` 的字符串形式） |
| 2…n | `token` | `{"text": "..."}`（第 1 次调用的文字） |
| 有工具时 | `tool_start` | `{"tools": [{"id": "call_x", "name": "query_logistics", "args": {"order_id": "1001"}}]}` |
| 有工具时 | `tool_end` | `{"tools": [{"id": "call_x", "name": "query_logistics", "ok": true}]}` |
| 有工具时 | `token` … | 第 2 次调用的文字 |
| 最后 | `done` 或 `error` | 同 ch01 |

所有事件仍用 `ServerSentEvent(raw_data=json.dumps(..., ensure_ascii=False))`。

### 6.3 流开始前的错误

| 状态码 | `detail.code` | 条件 |
|---|---|---|
| 404 | `conversation_not_found` | `session_id` 不存在，或属于其他 `user_id` |
| 409 | `session_busy` | 同 ch01 |
| 422 | `budget_exceeded` / 校验错误 | 同 ch01 |

**为什么 404 也覆盖"属于他人"：** 防止猜中 ID 读到他人历史（ch01 遗留问题）。不区分"不存在"与"不属于你"，避免泄露 ID 是否存在。

## 7. 一轮对话的数据流

### 7.1 预检（yield 依赖 `prepare_chat_turn`）

1. 确定会话：
   - 有 `session_id`：按 `id` 和 `user_id` 查询。查不到时返回 404。
   - 没有 `session_id`：插入一行 `conversations(user_id)` 并提交，取得 id。
2. 如果该会话的锁已占用，返回 409。
3. 从 `messages` 表按 id 升序读取历史，转换为 LangChain 消息，用 ch01 的 `build_history` 按 `TOKEN_BUDGET` 裁剪。超出时返回 422。
4. 获取锁，`yield ChatTurn`，在 `finally` 中释放锁。锁语义沿用 ch01（`scope="request"`）。

### 7.2 流式阶段（`services/chat.py`）

1. 发送 `session` 事件。
2. **第 1 次调用**：`model.bind_tools(registry 中的全部工具, tool_choice="auto")`，调用 `astream`。
   - 每个 `content` 非空的 chunk 发送一个 `token` 事件，并累加到文字缓冲区。
   - 用 `+` 累积全部 chunk，得到完整的 `tool_calls`。
3. 如果 `tool_calls` 为空：
   - 文字缓冲区为空时，按 ch01 处理：发送 `error`，不写库。
   - 否则写库（`user`、`assistant`），发送 `done`。
4. 如果 `tool_calls` 不为空：
   1. 发送 `tool_start`。
   2. 用执行器并行执行全部 tool_calls，得到 `ToolMessage` 列表。
   3. 发送 `tool_end`。
   4. **第 2 次调用**：**不绑定工具**，输入为第 1 次的消息 + `AIMessage(content=第 1 次文字, tool_calls=...)` + 全部 `ToolMessage`。调用 `astream`，每个非空 chunk 发送一个 `token`。
   5. 第 2 次的回答为空时：发送 `error`，不写消息。
   6. 否则写库（4 类消息），发送 `done`。
5. 任何一次上游调用抛异常：发送 `error`，不写消息。

**为什么第 2 次调用不绑定工具：** 模型无法再发起工具调用，从结构上保证"一轮只调用一次工具就收敛"。已实测：DeepSeek 在不带 `tools` 参数时正常接受含 `tool_calls` 的历史。

**思考模式：** 两次调用都使用 `CHAT_THINKING` 的配置。已实测：DeepSeek 在 `adaptive` 下能正确选择工具，回灌时不回传 `reasoning_content` 也正常。

### 7.3 Prompt

`chat_prompt` 在 `("human", "{input}")` 之后加 `MessagesPlaceholder("tool_round", optional=True)`，第 2 次调用通过它传入 `AIMessage(tool_calls)` 和 `ToolMessage`。

`CHAT_SYSTEM_TEMPLATE` 的变化：

- 删除约束 1 中的"你无法查询订单和物流"。
- 新增"工具使用"一节：
  1. 订单、商品、物流、常见问题一律调用工具查询。只根据工具返回的数据回答，不编造。
  2. 需要查询时直接调用工具，调用前不输出文字。
  3. 工具返回 `ok` 为 false 时，告诉用户暂时查不到，建议稍后再试或转人工。
  4. 用户明确要求人工，或投诉需要跟进时，调用 `create_ticket`，并把工单号告诉用户。
- 约束 5（转人工）改为：已创建工单时告知工单号；没有创建工单时，不声称已经转接。

最终措辞以第 10.3 节验证通过的版本为准。

## 8. 持久化

### 8.1 写入规则

- 一轮对话的消息**在一个事务中一次写入，且只在本轮成功时写入**。

  | 情况 | 写入的消息（按顺序） |
  |---|---|
  | 没有工具 | `user`、`assistant` |
  | 有工具 | `user`、`assistant`（`content` 为第 1 次文字，为空时写 NULL；`tool_calls` 写 JSON）、每个工具一条 `tool`（`content` 为结果 JSON，`tool_call_id`）、`assistant`（最终回答） |

- 上游出错、回答为空、客户端断开时都不写入消息。
- `messages.tool_calls` 的 JSON 格式：`[{"id": "...", "name": "...", "args": {...}}]`，与 LangChain `AIMessage.tool_calls` 相同。

### 8.2 历史还原与裁剪

- `services/history.py` 把数据库行转换为 `HumanMessage`、`AIMessage(content, tool_calls)`、`ToolMessage(content, tool_call_id)`。
- 历史中保留工具消息，追问时模型能看到上一轮的工具结果。
- 裁剪沿用 `build_history`（`start_on="human"`），从一轮对话的开头切断。`assistant(tool_calls)` 和它的 `tool` 消息不会被拆开。

### 8.3 副作用与已知取舍

- `create_ticket` 在自己的事务中立即写入 `tickets`，并把会话状态改为"已转人工"。如果本轮后来失败，工单保留，本轮消息不写入。
- 预检新建的会话在本轮失败时，留下一个没有消息的会话。

## 9. 工具

### 9.1 工具清单

| 工具 | 模型可见参数 | 成功时的 `data` | 数据来源 | 可重试 |
|---|---|---|---|---|
| `query_order` | `order_id` | `order_id`、`status`（待付款/待发货/已发货/已签收/已取消）、`items`（`product_id`、`name`、`quantity`、`price`）、`total`、`created_at` | mock | 是 |
| `query_product` | `product_id` | `product_id`、`name`、`price`、`stock`、`warranty_days`、`no_reason_return`（是否支持 7 天无理由） | mock | 是 |
| `query_logistics` | `order_id` | `order_id`、`carrier`、`tracking_no`、`status`、`traces`（`time`、`location`、`description`）、`estimated_delivery` | mock | 是 |
| `query_faq` | `keyword` | `results`：最多 3 条 `{question, answer, category}`；没有命中时为空列表 | `faq` 表，`question LIKE %kw% OR answer LIKE %kw%` | 是 |
| `create_ticket` | `description`、`ticket_type`（售后/投诉/咨询） | `ticket_no`、`status` | 写入 `tickets`；`conversation_id` 用 `InjectedToolArg` 注入 | **否** |

### 9.2 参数 Schema

每个工具用显式的 Pydantic `args_schema`：

| 参数 | 约束 |
|---|---|
| `order_id`、`product_id` | `^[A-Za-z0-9-]{1,32}$` |
| `keyword` | 长度 1–20；描述中写明"取用户原话中的关键词，不要替换为同义词" |
| `description` | 长度 1–500 |
| `ticket_type` | 枚举：售后、投诉、咨询 |

**为什么 `keyword` 要保持原词：** 如果模型把"邮费"改成"运费"，会掩盖 LIKE 的漏召回，验收 3 失去意义。

### 9.3 mock 数据

- `mock_data.py` 用 `random.Random(种子)` 生成数据。同一订单号或商品号，每次结果相同。
- 订单与物流从同一个订单号种子生成，状态互相一致：

  | 订单状态 | 物流状态 |
  |---|---|
  | 待付款、待发货、已取消 | 未发货，轨迹为空 |
  | 已发货 | 运输中，轨迹最后一条不是签收 |
  | 已签收 | 已签收，轨迹最后一条是签收 |

- 订单 `1001` 的状态固定为"已发货"，保证验收 1 有物流轨迹。

### 9.4 工单号

- 格式：`T` + `YYYYMMDD` + 3 位当日序号，例如 `T20261006001`。
- 序号取当日已有最大序号加 1。
- 主键冲突（并发）时用公共重试函数重试：最多 3 次，回退基数 0.05 秒，上限 0.5 秒。

## 10. 工具基础设施

### 10.1 重试（`app/retry.py`）

- `retry_async(fn, *, attempts, base_delay, max_delay, retry_on, sleep=asyncio.sleep, rand=random.random)`。
- 第 n 次重试前等待 `min(base_delay × 2^(n−1), max_delay) × (0.5 + rand() / 2)`。
- 只重试 `retry_on` 中的异常类型。次数用完后抛出最后一个异常。
- `sleep` 和 `rand` 可注入，单测不真实等待。
- 项目规则（`CLAUDE.md`）：所有自写重试都用这个函数。

### 10.2 注册表（`tools/registry.py`）

- 每个工具登记：`@tool` 对象、`retryable`、`timeout`。
- `tools_for_model()`：返回给 `bind_tools` 的工具列表。
- `get(name)`：执行时查找。未注册的名字返回 None。

### 10.3 执行器（`tools/executor.py`）

1. 用 `asyncio.gather` 并行执行一轮的全部 tool_calls。
2. 每次尝试用 `asyncio.wait_for` 限时 `TOOL_TIMEOUT_SECONDS`。
3. 可重试工具遇到临时错误（`TimeoutError`、SQLAlchemy `OperationalError`）时，用 `retry_async` 重试：最多 3 次尝试，基数 0.2 秒，上限 2 秒。不可重试工具只执行 1 次。
4. 每个结果转换为 JSON 字符串，作为 `ToolMessage.content`，超过 `TOOL_RESULT_MAX_CHARS` 时截断：
   - 成功：`{"ok": true, "data": {...}}`
   - 失败：`{"ok": false, "error": "<code>", "message": "<固定文案>"}`

   | code | 条件 | message |
   |---|---|---|
   | `invalid_arguments` | 参数校验失败 | 参数不合法 |
   | `unknown_tool` | 工具未注册 | 工具不存在 |
   | `timeout` | 重试后仍超时 | 查询超时 |
   | `tool_error` | 其他异常 | 查询失败 |

5. 完整异常只写日志，不回灌给模型。
6. `create_ticket` 的 `conversation_id` 由执行器注入，模型提供的同名参数一律忽略。

## 11. 聊天页（Vibe Coding）

聊天页按 Vibe Coding 改造，不走 brainstorm、TDD、code review 流程。功能要求：

- 页面生成匿名 `user_id`，保存在 `localStorage`，每次请求都带上。
- 收到 `tool_start` 时，在当前助手气泡中显示工具徽章（工具名，进行中状态）；收到 `tool_end` 时，更新为成功或失败状态。
- 404 `conversation_not_found` 时，提示会话已失效，并自动开始新对话。

具体样式由用户描述后再改。

## 12. 测试与验证

### 12.1 离线单测（不连数据库，不调用上游）

| 模块 | 测试内容 |
|---|---|
| `retry` | 等待时间按 2 的幂增长，不超过上限，抖动在区间内；只重试指定异常；次数用完抛出最后一个异常 |
| `mock_data` | 同一 ID 结果相同；订单与物流状态符合 9.3 节的表；订单 1001 为"已发货" |
| `registry` | 5 个工具都已注册；`create_ticket` 的模型可见 schema 中没有 `conversation_id` |
| `executor` | `invalid_arguments`、`unknown_tool`；超时后按回退重试；`create_ticket` 不重试；结果截断；多个调用并行执行 |
| `history` | 4 种角色与 JSON `tool_calls` 的双向转换；裁剪不拆开工具调用对 |
| `services/chat` | 用自定义假模型（支持 `bind_tools`，按脚本流出文字或 tool_call chunk）：无工具时只调用 1 次；有工具时事件顺序为 `session → tool_start → tool_end → token… → done`；第 2 次调用没有绑定工具 |

### 12.2 数据库测试（MySQL `aftersales_test`）

- pytest 会话开始时，用 `db/schema.sql` 在 `aftersales_test` 中重建 4 张表，并灌入 `db/seed.sql`。每个测试结束后清空 `messages`、`tickets`、`conversations`。
- **数据库连不上时，需要数据库的测试直接失败，并提示"请先执行 docker compose up -d"。不跳过。**

| 范围 | 测试内容 |
|---|---|
| repositories | 会话新建与读取；`user_id` 不匹配时返回 None；4 种消息往返存取（含 JSON）；`query_faq` 中"退货政策"命中、"邮费"不命中；工单号生成与序号递增；主键冲突重试；建工单后会话状态为"已转人工" |
| API 端到端（假模型 + 真数据库） | 新建会话；他人会话返回 404；有工具的一轮写入 4 类消息；失败时不写消息；第二轮能读到第一轮的工具结果；ch01 的会话锁回归测试 |

### 12.3 数据与 Prompt 验证（代替 TDD）

- **FAQ 测试数据**：用户审核 `db/seed.sql`。脚本检查全文不含"邮"字。
- **工具选择样例集** `evals/tool_selection_samples.jsonl`：约 16 条问句，每条标注期望的工具集合；FAQ 类样例另标注 keyword 必须包含的原词。
  - `evals/run_tool_selection_eval.py` 调用真实上游，**只执行第 1 次调用**，不执行工具。
  - 通过标准：工具集合完全匹配率 ≥ 90%；FAQ 类样例 keyword 保持原词的比例为 100%。
  - 用户先审核标注。

### 12.4 验收

- `scripts/demo2.sh` 用 curl 跑 3 项验收，并用 `docker exec` 打印每轮写入的 `messages`。
- 聊天页在浏览器中实测 3 项验收。

## 13. 已知限制

- 会话锁是进程内锁，多进程部署时不生效。锁对象不清理。
- 失败的一轮会留下没有消息的会话；工单副作用不回滚。
- `query_faq` 的 LIKE 查询无法处理同义词（例如"邮费"与"运费"），由后续章节的向量检索解决。
- mock 数据与真实系统无关，订单号以外的输入（如不存在的订单）也会生成数据。
