# ch08 即插即用的工具系统：设计规格

- 日期：2026-10-09
- 状态：待用户审阅
- 分支：`ch08`（从 `main` 拉出。`main` 已合并 PR #2，含 ch06 和 ch07）
- 前置：ch07（`docs/superpowers/specs/2026-10-08-ch07-context-management-design.md`）。本文只写新增和变化的部分。没有提到的 ch07 行为保持不变。

## 1. 目标与验收标准

**目标：** 把写死的内置工具升级为即插即用的工具系统。内置工具和 MCP 工具登记进同一份工具表，所有调用走同一个执行引擎。

1. 工具注册中心：每个工具带工具名、用途描述、JSON Schema 参数定义。内置工具服务启动时登记；MCP 工具每轮从 Server 动态发现。新工具注册后主力 Agent 就能用，不改核心代码。MCP 工具不重启服务。
2. 参数校验：执行前统一按 JSON Schema 校验。不合法时把错误说明包成工具结果回给模型，不抛异常。
3. 权限控制：工具分只读和写两类。写操作只有 `create_ticket`，只在用户明确要求时发起，执行前必须等前端确认。MCP 工具的权限只看我方策略文件，不看 Server 声明，不让模型判断。
4. 执行引擎：统一管超时、重试、错误处理、结果格式化。只对暂时性故障重试；查询落空不重试；写操作默认不重试。错误分 3 类：参数不合法、查询落空、真故障。结果只留回答用得上的字段，内部编码翻成中文，JSON 不转义中文。
5. 审计：`tool_audit_logs` 每次调用一条，被权限拒绝和被校验拦下的调用也记。不挂外键。写审计失败不影响工具执行。
6. MCP 接入：自建物流、售后两个 MCP Server，各自独立进程，返回 mock 数据。客服系统用 `MultiServerMCPClient` 接入。物流查询由物流 Server 接管，内置 `query_logistics` 从生产清单下线。
7. 建工单确认流：用户明确要求建工单时，Agent 先补齐信息，再发起 `create_ticket`。执行前用 LangGraph `interrupt` 把预览推给前端。确认后落 `tickets` 表，回复带工单号；取消时不执行，审计记"权限拒绝"。ch05 投诉流程的按钮建单不变。

**验收标准：**

1. 新写一个简单工具，只做注册动作、不动核心代码，Agent 就能在对话里用上它。
2. 两个 MCP Server 单独起进程。问物流轨迹，Agent 通过 MCP 查到数据并回答。
3. 在 MCP Server 侧新加一个工具，只重启该 Server。客服系统代码不动、服务不重启，再问对应问题，Agent 能用上新工具。
4. 说"帮我建个工单"但没讲清楚问题，Agent 先追问；补齐后聊天页出现工单预览卡片；点「确认提交」，`tickets` 表新增一条，回复带工单号。
5. 走到工单预览后点「取消」，工单没建，`tool_audit_logs` 中这条 `create_ticket` 状态为"权限拒绝"。
6. 让一个只读工具超时：能看到重试和错误兜底，审计中重试次数、超时状态、耗时齐全。让一个写操作超时：没有自动重试，审计中重试次数为 0。

**本章不做：** Skill 机制；仓储等更多外部系统；MCP 写工具（本章一律拒绝）；MCP Server 鉴权。

## 2. 技术栈与依赖

- MCP Server：MCP 官方 Python SDK，Streamable HTTP 传输（无状态、JSON 响应）。
- MCP Client：`langchain-mcp-adapters` 的 `MultiServerMCPClient`。
- 参数校验：`jsonschema`（Draft 2020-12）。它是校验工具库，不替换定死的选型。
- 新依赖：`mcp`、`langchain-mcp-adapters`、`jsonschema`。版本在计划阶段按 PyPI 和 Context7 确定：SDK 用 v1 `FastMCP` 还是 v2 `MCPServer`，以 `langchain-mcp-adapters` 兼容的版本为准。

Context7 核对结果（`/langchain-ai/langchain-mcp-adapters`、`/modelcontextprotocol/python-sdk`）：

- `MultiServerMCPClient(connections, *, tool_interceptors=None, tool_name_prefix=False, handle_tool_errors=True)`。连接写法 `{"transport": "http", "url": "http://127.0.0.1:8101/mcp"}`。
- 不传 session 时，`get_tools()` 返回的每个工具在**每次调用时新建会话**。所以每轮调 `get_tools()` 就能拿到 Server 的最新工具。
- 转换后的工具：`name`、`description`、`args_schema`（MCP `inputSchema`，dict）、`response_format="content_and_artifact"`；artifact 为 `{"structured_content": ...}`（Server 返回结构化内容时）。
- `handle_tool_errors=False` 时，MCP 执行错误（`CallToolResult(isError=True)`）抛 `ToolException`；传输和会话故障（连接拒绝、Server 崩溃）总是抛异常。
- Server 端：`mcp.run(transport="streamable-http", stateless_http=True, json_response=True)`，`host`、`port` 等传输参数传给 `run()`。

## 3. 数据

### 3.1 表结构

`db/schema_ch08.sql` 是用户 DDL（原 `ch08.sql`），逐字保存，是新表的唯一来源：`tool_audit_logs`，不挂外键，`conversation_id` 只建普通索引。`status` 枚举：`成功`、`失败`、`超时`、`校验拦下`、`权限拒绝`。

- `docker-compose.yml` 挂载为 `06-schema-ch08.sql`。
- `reset_db.sh` 增加 `tool_audit_logs` 存在性检查和中文枚举值的 `HEX()` 校验。
- ORM 只映射（`ToolAuditLog`），不 `create_all`。仓储 `app/repositories/audit.py` 只负责 SQL。

### 3.2 审计字段的取值

| 字段 | 取值 |
|---|---|
| `conversation_id` | 本轮会话 ID；评估脚本等无会话的调用为 NULL |
| `tool_call_id` | 模型给的 tool_call id；`fetch_order`、`POST /tickets` 用自生成 id |
| `tool_source` / `mcp_server` | `builtin` / NULL，或 `mcp` / Server 名 |
| `arguments` | 模型给的参数，不含执行引擎注入的 `conversation_id` |
| `result_summary` | 发给模型的格式化结果，超过 500 字截断 |
| `status` | 见 4.4 |
| `error_message` | 失败、拦下、拒绝的原因；查询落空写"查询落空"；超过 512 字截断 |
| `retry_count` | 实际重试次数 |
| `duration_ms` | 从进入执行引擎到出结果，含全部重试和回退等待 |

## 4. 工具注册中心与执行引擎

### 4.1 工具条目（`app/tools/registry.py`，重写）

`ToolEntry` 字段：`name`、`description`、`parameters`（发给模型的 JSON Schema）、`source`、`server`、`permission`（`read` / `write`）、`runner`（异步执行函数）、`formatter`、`timeout`、`max_retries`、`inject_conversation_id`、`agent`（是否给主力 Agent 绑定）。

- 发给模型的 Schema 不含注入字段（`conversation_id`）。
- `ToolEntry` 能转成 `bind_tools` 接受的 OpenAI 工具定义。

### 4.2 内置工具（`app/tools/builtin/`）

- 内置工具移到包 `app/tools/builtin/`，一个文件一个或一组工具。
- 注册方式：LangChain `@tool` 外面加 `@register(permission="read", formatter=..., agent=True, ...)`。JSON Schema 取自 `args_schema.model_json_schema()`。
- 服务启动时用 `pkgutil` 扫描整个包并导入，导入即登记。新增内置工具只需要放一个文件，再重启服务；核心代码不改。
- 内置工具的权限在登记时声明（我方代码）。
- 生产清单：`query_order`、`query_product`、`offer_human_options`、`offer_refund_form`（read）；`create_ticket`（write，`inject_conversation_id=True`）；`query_faq`（read，`agent=False`，只给 ch04 评估）。
- `query_logistics` 从生产包移出，放到 `app/tools/legacy/`（不扫描）。ch04 评估用 `build_ch04_registry()` 组装自己的基线清单（`CH04_CHAT_TOOLS` 不变），不跟随生产清单。

### 4.3 策略文件（`config/tools.json`）

```json
{
  "servers": {
    "logistics":  {"url": "http://127.0.0.1:8101/mcp", "tools": {"query_logistics": "read"}},
    "aftersales": {"url": "http://127.0.0.1:8102/mcp",
                   "tools": {"query_warranty": "read", "query_return_progress": "read"}}
  },
  "overrides": {
    "create_ticket": {"timeout_seconds": 5}
  }
}
```

- 每次取工具集时检查文件 mtime，变了就重读。不重启服务。
- MCP 工具的权限只看 `servers.<名>.tools`：`read` 放行；`write` 和 `deny` 拒绝；**没列出的工具一律拒绝**（用户裁定）。不读 Server 的 `annotations`（如 `readOnlyHint`）。
- `overrides` 按工具名覆盖 `timeout_seconds`、`max_retries`，内置和 MCP 工具都适用。
- 文件读失败或 JSON 不合法：沿用上一次成功读到的内容，打 warning；启动时就读失败则 MCP 工具集为空。

### 4.4 执行引擎（`app/tools/executor.py`）

入口不变：`execute_tool_calls(calls, *, conversation_id, toolset, approved_ids=frozenset())`，并行执行，按输入顺序返回 `ToolOutcome`。每个调用依次经过：

1. **查找**：名字在内置包、策略文件、本轮发现结果中都不存在 → `失败`（"工具不存在"）。策略文件列为 `read`、但本轮没发现到（Server 连不上）→ `失败`（"工具暂时不可用"）。存在但本轮没开放 → `权限拒绝`（"工具未开放"）。没开放包括：策略 `deny`、未列出的 MCP 工具、MCP 写工具、`ticket_request` 为假时的 `create_ticket`。
2. **权限**：`write` 工具的 call id 不在 `approved_ids` 中 → `权限拒绝`（"未经用户确认"或"用户取消"）。`approved_ids` 只来自确认节点（第 5 节）和 `POST /tickets`，不是工具参数，模型无法提供。
3. **校验**：注入 `conversation_id` 后，用 `jsonschema` 校验，收集全部错误，翻成短中文："缺少必填参数 description"、"order_id 格式不对"、"ticket_type 只能是 售后/投诉/咨询"。内置工具内部的 Pydantic 校验（如 `offer_human_options` 的跨字段规则）抛 `ValidationError` 时，同样按校验拦下处理。→ `校验拦下`。
4. **执行**：每次尝试用 `asyncio.wait_for(…, timeout)`。重试只用于 `read` 工具，只针对暂时性故障：`TimeoutError`、httpx 传输错误、MCP 会话或连接失败、`OperationalError`。`read` 默认 `max_retries=2`（共 3 次），`write` 默认 0。等待时间用现有 `retry_async`（指数回退 + 抖动），新增 `on_retry` 回调计数。MCP `isError`（`ToolException`）是 Server 给的业务错误，不重试。
5. **分诊**：

| 类别 | 判定 | 发给模型 | 审计 `status` |
|---|---|---|---|
| 参数不合法 | 第 3 步不通过 | `{"ok": false, "error": "invalid_arguments", "message": "参数不合法：…。请向用户追问缺少的信息，或修正后重试"}` | `校验拦下` |
| 查询落空 | 结果为空，或含 `"found": false` | `{"ok": true, "found": false, "message": "没有查到…"}` | `成功`，`error_message="查询落空"`（用户裁定） |
| 真故障 | 重试用尽仍超时或抛异常 | `{"ok": false, "error": "timeout" / "tool_error", "message": "…暂时不可用"}` | `超时` / `失败` |
| 权限 | 第 1、2 步拒绝 | `{"ok": false, "error": "permission_denied", "message": "…"}` | `权限拒绝` |

6. **格式化**：每个工具可带 formatter，只挑回答用得上的字段，把内部编码翻成中文（例如 `IN_TRANSIT` → 运输中）。内置工具的 formatter 随登记提供；MCP 工具的 formatter 在我方 `app/tools/formatters.py`，按（Server，工具名）登记。没有 formatter 的 MCP 工具用默认格式化：取结构化内容原样输出。全部用 `json.dumps(..., ensure_ascii=False)`，再按 `TOOL_RESULT_MAX_TOKENS` 截断（沿用 ch07）。
7. **审计**：每个调用结束后写一条，单独会话，超时 1 秒，包在 try/except 中。失败只打 `audit_write_failed`，不影响返回。

MCP 工具的 runner 用 ToolCall 形式调用转换后的工具，取 `artifact.structured_content`；没有结构化内容时解析文本块中的 JSON，解析不了就按文本处理。

### 4.5 MCP 发现（`app/tools/mcp.py`）

- `toolset_for_turn()` 在每轮 Agent 第一次调模型前执行一次：读策略文件，对每个 Server 调 `get_tools()`（单 Server 超时 2 秒，Server 之间并行），与内置条目合成本轮工具集，放在 `GraphContext` 上（不进 State）。同一轮的后续步骤复用。恢复（resume）时新建 `GraphContext`，重新发现。
- Server 连不上：本轮没有它的工具，打 `mcp_discovery_failed server=<名> error=<类型>`，本轮继续。
- 重名：内置优先，跳过 MCP 同名工具并打 warning；两个 MCP Server 重名时，策略文件中靠前的 Server 优先。
- 每轮打一行 `toolset conversation=<id> builtin=<n> mcp=<server:n,...> denied=<名,...>`。
- `fetch_order` 只用内置 `query_order`，不触发发现。

### 4.6 Agent 绑定（变化）

- 删除 `AGENT_TOOLS` 常量。Agent 绑定本轮工具集中 `agent=True` 且权限放行的全部条目。
- `offer_refund_form` 保持 ch06 条件（aftersales 出口且有订单号）。
- `create_ticket` 只在 `ticket_request` 为真时绑定。
- `agent_tools` 不再自己维护允许列表，统一由执行引擎判定。
- ch07 预算不变：`SYSTEM_RESERVE_TOKENS=1800` 仍是常量。每轮绑定时实测 System + 工具定义的 token，超出预留时打 `system_reserve_exceeded`。演示配置仍得 5650/3954/1695。

## 5. 建工单确认流

### 5.1 分流（用户裁定：方案 A）

- `resolve_reference` 的 JSON 输出增加 `ticket_request`（bool）。为真的情况：用户这句明确要求建工单、提交工单；或者 Agent 刚追问了工单信息，用户这句在补充问题描述。
- `after_intent` 中 `ticket_request` 优先于其他规则（含 `history_recall` 和投诉），直接走 business。8 类意图和意图评估集不变。

### 5.2 图的变化

```
agent_model ─(含写工具调用)→ confirm_write → agent_tools ─(本步有写决定)→ ticket_reply → finalize
            └─(只有读调用)─────────────────→ agent_tools ──────────────→ agent_model（同 ch05）
```

"含写工具调用"指本轮已开放的写工具，即 `ticket_request` 为真时的 `create_ticket`。没开放的写调用（MCP 写工具、未开放的 `create_ticket`）走 `agent_tools`，由执行引擎拒绝。

State 新增本轮字段：`ticket_request`、`approved_ids`、`write_decision`、`write_outcome`，由 `start_turn` 重置。

### 5.3 `confirm_write` 节点

1. `interrupt()` 之前不发事件、不写库、不调上游（恢复时节点从头执行），只做纯计算：
   - 权限：本轮开放了 `create_ticket`。
   - JSON Schema 校验参数。
2. 任一检查不通过 → 不中断，直接进 `agent_tools`，由执行引擎返回 `权限拒绝` 或 `校验拦下`，模型据此追问用户。所以只有参数合法时才出预览卡片。
3. 同一步有多个 `create_ticket` 调用时，只预览第一个；其余由执行引擎拒绝（"一次只能提交一张工单"）。
4. `interrupt({"type": "ticket_confirm", "call_id", "ticket_type", "description"})`。
5. 恢复值 `{"confirmed": bool}` → 写 `approved_ids`（确认时为该 call id，取消时为空）和 `write_decision`（`confirmed` / `cancelled`）。

`agent_tools` 把 `approved_ids` 传给执行引擎。取消时执行引擎记 `权限拒绝`，原因"用户取消"。

### 5.4 `ticket_reply` 节点（固定话术，不调模型）

| 结果 | 回复 |
|---|---|
| 已创建 | `TICKET_CREATED_NOTE`（带工单号和类型） |
| 已取消 | "好的，已取消，工单没有提交。还有其他问题可以随时告诉我。" |
| 超时 | "工单提交超时，暂时无法确认是否提交成功。为避免重复建单，系统没有自动重试，请稍后联系人工客服核实。" |
| 失败 | "工单提交失败，请稍后重试。" |

理由：工单号一定准确；恢复发生在另一个 HTTP 请求中，DeepSeek 按 tool_call id 缓存的思考内容可能已过期（未实测，可能 400）；少一次模型调用。回复追加到 `agent_messages` 末尾，`finalize` 照常写库。

### 5.5 接口与 SSE

- `stream_graph` 从 `__interrupt__` 中按 `type` 区分：`order_picker` 发 `order_picker`；`ticket_confirm` 发 `ticket_preview` `{call_id, ticket_type, description}`。之后 `done` 的 `finish_reason=interrupted`。
- `POST /chat/resume` 请求体增加 `ticket_confirm: bool | None`。`order_id` 和 `ticket_confirm` 必须恰好给一个，否则 422。预检按待处理 interrupt 的类型匹配：没有待处理的 `ticket_confirm` → 409 `no_pending_confirmation`；没有待处理的 `order_picker` → 409 `no_pending_selection`（不变）。恢复值 `Command(resume={"confirmed": ticket_confirm})`。
- 待确认时用户发新消息：从 START 开始，原 interrupt 作废（ch06 行为）。`prepare_chat_turn` 发现待处理的 `ticket_confirm` 时写一条审计：`权限拒绝`，原因"用户未确认，已被新消息取代"。未确认的 tool_call 只在本轮 `agent_messages` 中，不进历史。
- `POST /tickets`（投诉流程按钮）不变：点击即确认，执行引擎调用时 `approved_ids` 含该 call id，照常记审计。

### 5.6 Prompt（非可单测，用评估集验证）

Agent System Prompt 的工单规则改为：

1. 只有用户明确要求建工单时调用 `create_ticket`。
2. 调用前核对：`description` 必须概括用户说过的问题，`ticket_type` 按用户诉求选。用户没讲清问题时，先追问，不调用；不用"用户要求建工单"这类空话填 `description`，不编造细节。
3. 调用后系统会请用户确认，不说"已创建"。
4. 用户没有明确要求、但问题需要人工跟进时，仍用 `offer_human_options` 给建工单按钮（ch05 行为）。

指代消解 Prompt 增加 `ticket_request` 的定义和边界示例。

### 5.7 前端（Vibe Coding，由 Codex 实现）

收到 `ticket_preview` 时在回复下方渲染预览卡片：工单类型、问题描述、「确认提交」「取消」两个按钮。点击后两个按钮都置灰，调用 `/chat/resume`（`ticket_confirm: true/false`），接着渲染流式回复。

## 6. MCP Server

| Server | 端口 / 路径 | 工具 |
|---|---|---|
| `logistics` | 8101 `/mcp` | `query_logistics(order_id)` |
| `aftersales` | 8102 `/mcp` | `query_warranty(order_id)`、`query_return_progress(order_id)` |

- 代码在顶层包 `mcp_servers/`，不导入 `app`，不连数据库，不建表。
- 启动：`uv run python -m mcp_servers.logistics`、`uv run python -m mcp_servers.aftersales`。传输：Streamable HTTP，无状态，JSON 响应，监听 `127.0.0.1`。
- mock 数据按订单号做种子随机生成（照 ch02 `mock_data` 的做法），同一订单号结果固定。返回英文编码（`IN_TRANSIT`、`IN_WARRANTY`、`REFUND_PROCESSING`）和内部字段（`warehouse_id`、`route_id`），由我方 formatter 挑字段、翻译。
- 订单号以 `9` 开头时返回 `{"found": false}`，用于演示查询落空。
- 故障注入：每个 Server 读环境变量 `MOCK_DELAY_SECONDS`（Server 侧，不进我方 `.env`），每次调用先等待这么多秒。
- `aftersales` 启动时导入 `mcp_servers/aftersales/tools/` 下的全部模块。新增工具 = 放一个文件 + 重启该 Server。

## 7. 日志

- `toolset ...`（4.5）、`mcp_discovery_failed ...`、`audit_write_failed ...`、`system_reserve_exceeded ...`。
- 每次工具调用一行：`tool_call conversation=<id> name=<名> source=<builtin|mcp:server> status=<状态> retries=<n> ms=<耗时>`。
- `turn ...` 汇总行增加 `ticket_request=` 和 `write=<confirmed|cancelled|->`。

## 8. 错误处理

| 情况 | 行为 |
|---|---|
| MCP Server 连不上（发现时） | 本轮没有它的工具，`mcp_discovery_failed`，继续 |
| MCP Server 连不上（调用时） | 按暂时性故障重试，用尽 → `失败`，如实告诉模型 |
| 策略文件读失败 | 沿用上次内容；启动时读失败则 MCP 工具集为空 |
| 写审计失败 | `audit_write_failed`，工具结果照常返回 |
| 写工具超时 | 不重试，`超时`，`ticket_reply` 超时话术 |
| 恢复时没有待处理 interrupt | 409 |

## 9. 测试（TDD，不访问真实上游）

- `test_registry.py`：扫描 `builtin` 包登记；Schema 不含注入字段；新文件放进包即被登记（测试用临时包）；`query_logistics` 不在生产清单。
- `test_policy.py`：mtime 变化后重读；未列出、`deny`、`write` 的 MCP 工具被拒；`overrides` 生效；文件损坏时沿用上次内容。
- `test_executor.py`：各类校验错误的中文说明；无确认的写调用被拒；暂时性故障重试且 `retry_count` 正确；`isError` 不重试；查询落空不重试；写工具超时不重试；formatter 和 `ensure_ascii=False`；每种状态写一条审计；审计写失败不影响结果。
- `test_mcp.py`：用 fixture 以子进程启动两个 Server（测试端口）；发现、调用、格式化；Server 停掉时发现失败不中断；Server 新增工具后下一轮发现到它。
- `mcp_servers` 单测：直接调工具函数，结果固定、`9` 开头落空、延迟注入。
- 图测试（`memory_graph`）：`ticket_request` 路由优先；未设 `ticket_request` 时不绑定 `create_ticket`；`confirm_write` 确认、取消、参数不合法不中断；`ticket_reply` 四种话术；待确认时新消息写审计。
- 接口测试：`/chat/resume` 的 422（两个都给或都不给）、409（类型不匹配）；`ticket_preview` 事件。
- autouse fixture 默认拦截 MCP 发现（返回空），与 `BlockedMilvus` 同理；需要 MCP 的测试用 fixture `mcp_servers`。
- 更新现有用到 `query_logistics`、`AGENT_TOOLS` 的测试。

## 10. 评估（非可单测部分）

- `evals/run_ticket_eval.py`（新增，真实上游，只做第 1 次模型调用）：12 条。只说"帮我建个工单" → 不调 `create_ticket`，回复在追问；讲了问题 → 调 `create_ticket`，`description` 含关键事实、没有原话外的数字；生气且讲了问题 → `ticket_type=投诉`。全部通过才退出码 0。
- `run_multiturn_eval.py` 增加 `ticket_request` 组：明确要求、补充描述的下一轮、只抱怨不要求、要求转人工（不算建工单）。
- 回归：`run_intent_eval.py`、`run_multiturn_eval.py`、`run_tool_selection_eval.py`（ch04 基线）、`scripts/demo6.sh`、`scripts/demo7.sh`。

## 11. 验收脚本 `scripts/demo8.sh <日志目录>`

脚本自己启停两个 MCP Server 和客服服务（端口 8000、8101、8102 须空闲）。

1. 把 `scripts/demo8_assets/query_member_points.py` 复制进 `app/tools/builtin/`，重启客服服务，问"我有多少积分"：回复含积分，审计有 `query_member_points` 成功一条。最后删除该文件。
2. 问物流轨迹（订单 1001）：审计 `tool_source=mcp, mcp_server=logistics, status=成功`，回复含轨迹中的状态。
3. 记下客服服务 PID。把 `query_repair_progress.py` 复制进 `mcp_servers/aftersales/tools/`，在 `config/tools.json` 加一行 `read`，只重启 aftersales Server。问维修进度：审计有 `query_repair_progress`，客服服务 PID 不变。最后还原文件。
4. 发"帮我建个工单"：回复在追问，没有 `ticket_preview`。再发问题描述：收到 `ticket_preview`。调 `/chat/resume`（`ticket_confirm: true`）：回复含工单号，`tickets` 表有这一条。
5. 同样走到预览，`ticket_confirm: false`：`tickets` 表没有新增，审计 `create_ticket` 为 `权限拒绝`、原因"用户取消"。
6. 读超时：以 `MOCK_DELAY_SECONDS=5` 重启 logistics，策略 `overrides.query_logistics.timeout_seconds=2`，问物流：审计 `超时`、`retry_count=2`、`duration_ms` 有值，回复如实说查不到。写超时：策略 `overrides.create_ticket.timeout_seconds=0.001`，走确认流：审计 `超时`、`retry_count=0`，回复为超时话术。最后还原策略文件。

前端预览卡片在 Chrome 中手工验收：确认一次、取消一次。

## 12. CLAUDE.md 变更

- 项目状态加 ch08 一行；架构图和模块表加 `app/tools/builtin/`、`policy`、`mcp`、`formatters`、`mcp_servers/`、`repositories.audit`。
- 约束修改："Agent 只绑定 AGENT_TOOLS……`create_ticket` 只由 `POST /tickets` 调用" 改为"Agent 绑定本轮工具集；`create_ticket` 只在 `ticket_request` 为真时绑定，执行前必须经 `confirm_write` 确认或 `POST /tickets` 点击"。
- 约束新增："`schema_ch08.sql` 是用户 DDL"；"所有工具调用只走 `execute_tool_calls`"；"MCP 工具权限只看 `config/tools.json`，未列出即拒绝"；"写工具不重试"；"`confirm_write` 在 `interrupt()` 之前不发事件、不写库"；"审计写失败不影响工具执行"。
- 常用命令加两个 MCP Server 启动命令、`demo8.sh`、`run_ticket_eval.py`。

## 13. 已知限制

- 每轮调一次 `get_tools()`，每个 Server 多一次往返（本机约几十毫秒）。
- 没有 formatter 的新 MCP 工具，结果中的英文编码原样给模型。
- 写超时后工单可能已经落库；系统不自动核实，由话术提示用户找人工核实。
- 确认后用固定话术收尾，同一步中的读工具结果不进回复。
- MCP Server 不做鉴权，只监听 `127.0.0.1`。
