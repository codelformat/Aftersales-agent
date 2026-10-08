# ch05 Workflow 骨架与主力 Agent：设计规格

- 日期：2026-10-07
- 状态：待用户审阅
- 分支：`ch05`
- 前置：ch04（`docs/superpowers/specs/2026-10-07-ch04-hybrid-retrieval-rerank-design.md`）。本文只写新增和变化的部分。没有提到的 ch04 行为保持不变。

## 1. 目标与验收标准

**目标：** 把客服系统升级为"确定性 Workflow 做骨架、主力 Agent 做核心节点"的架构。

1. 热身：不用框架，手写一个最裸的 Agent 循环。再用 LangGraph 重构。
2. 图骨架：指代消解 → 意图识别 → 按意图分流 → 知识检索 → 置信度闸 → 主力 Agent → 日志记录。
3. 分流规则写死在代码中。7 类意图归到 4 个出口：知识类、业务数据类、投诉、闲聊。
4. 主力 Agent 用 ReAct 循环。有停止条件和 token 消耗控制。缺信息时追问用户。工具复用 ch02 的工具，不新写业务工具。
5. 会话状态用 LangGraph 的 State 贯穿整个图，用 checkpointer 持久化。
6. 指代消解和意图识别本章只做最简实现。
7. 置信度闸放在知识类检索之后、Agent 之前。证据弱时直接回兜底话术，不进 Agent，并把问题写入问题池。
8. 转人工和建工单是两件事，都由用户在前端自选。后端只在回复中给出可选项，不自动执行。

**验收标准：**

1. 问政策类问题，日志中能看到强制检索节点 `retrieve` 被执行。
2. 问"订单 1001 的物流到哪了"，Agent 自己调用工具作答。
3. 说"我要投诉"，前端出现「转人工」「建工单」两个独立按钮。点「转人工」，前端显示"已转接人工客服"和客服小猫的问候。点「建工单」，才写 `tickets` 表。两个按钮互不影响。都不点时，接着正常对话，后端不做任何动作。
4. 闲聊得到固定话术。
5. 一个要先查订单再查物流的复杂问题，ReAct 走了 2 步以上。

**本章不做：** 意图识别和指代消解的正式版、上下文管理策略升级、MCP 接入、飞轮入库、正式的置信度检查（留给可观测那章）、Langfuse 接入、真人客服系统对接、`conversations.status` 状态变更。

## 2. 技术栈

| 项 | 选择 |
|---|---|
| 图编排 | `langgraph`：`StateGraph(ChatState, context_schema=GraphContext)`、`add_conditional_edges`、`add_messages` reducer、`Runtime[GraphContext]`、`get_stream_writer()`、`astream(stream_mode="custom")`、`aget_state` / `aupdate_state`（已用 Context7 核对） |
| 持久化 | `langgraph-checkpoint-sqlite` 的 `AsyncSqliteSaver.from_conn_string(path)`（用户选定）。LangGraph 官方没有 MySQL checkpointer |
| 测试用 checkpointer | `langgraph` 自带的 `InMemorySaver` |
| 热身裸循环 | `openai` SDK 的 `AsyncOpenAI`（`langchain-openai` 已依赖该包）。不用 LangChain 和 LangGraph |
| 其他 | 沿用 FastAPI、SQLAlchemy、LangChain、MySQL 8、Milvus |

**设计阶段实测**（一次性探测，不进仓库）：DeepSeek `deepseek-v4-flash`，`CHAT_THINKING=adaptive`，绑定 `query_order`、`query_logistics`、`query_product`。

| 步 | 结果 |
|---|---|
| 第 1 次调用 | `query_order(1001)` |
| 第 2 次调用 | 并行调用 `query_product(P002)` 和 `query_logistics(1001)` |
| 第 3 次调用 | 文字收尾，无工具调用 |
| 强制收尾（不绑定工具 + `TOOL_ROUND_CLOSING`） | 正常文字回答 |

结论：
1. 思考模式下连续 3 次带工具调用，没有返回 400。
2. 流式调用的合并结果带 `usage_metadata`，其中 `output_token_details.reasoning` 为思考 token。
3. 模型会在一步中并行调用多个工具。执行器已支持并行。

## 3. 配置

- `.env` 不新增变量。
- 新增依赖：`langgraph`、`langgraph-checkpoint-sqlite`。
- `.gitignore` 新增 `data/`。
- 新增代码常量（`app/config.py`）：

| 常量 | 值 | 用途 |
|---|---|---|
| `CHECKPOINT_DB_PATH` | `"data/checkpoints.sqlite"` | AsyncSqliteSaver 文件路径 |
| `INTENT_TIMEOUT_SECONDS` | `8` | 意图识别的等待上限，超时后走兜底出口 |
| `AGENT_MAX_STEPS` | `4` | 一轮中工具轮次的上限 |
| `AGENT_TOKEN_BUDGET` | `16000` | 一轮中 Agent 累计 token 上限（输入 + 输出，含思考） |
| `GRAPH_RECURSION_LIMIT` | `25` | 图的 `recursion_limit`，最后防线 |
| `GATE_MIN_SCORE` | `0.20` | 置信度闸的 Top-1 重排分门槛。初值等于 `RERANK_MIN_SCORE` |

`GATE_MIN_SCORE` 单独设为常量，是为了让可观测那章能独立调整闸门，不影响检索门槛。

## 4. 热身：裸 Agent 循环

**文件：** `app/agent/bare_loop.py`、`scripts/bare_agent.py`。

`run_bare_agent(client, model, question, *, max_steps=4) -> BareResult`：

1. 消息列表 = `[system, user]`。
2. 调用 `client.chat.completions.create(model, messages, tools=TOOLS)`。
3. 如果返回的消息没有 `tool_calls`，返回 `content`。循环结束。
4. 否则把 assistant 消息追加到列表。逐个执行工具，把结果作为 `role="tool"` 消息追加。
5. 步数加 1。如果步数达到 `max_steps`，不带 `tools` 再调用一次，返回 `content`。
6. 回到第 2 步。

- `TOOLS` 是手写的 JSON schema：`query_order`、`query_logistics`，各一个 `order_id` 参数。
- 工具执行直接调用 `app.tools.mock_data.order` 和 `mock_data.logistics`。
- `BareResult` 记录 `answer`、`steps` 和每步的工具名，供演示打印。
- 思考字段：如果 `CHAT_THINKING` 有值，用 `extra_body={"thinking": {"type": ...}}` 发送，和 `app/llm.py` 一致。
- `scripts/bare_agent.py "<问题>"` 用 `.env` 中的 `CHAT_*` 运行一次，打印每一步。

dev-notes 中记一张对照表：裸循环的每一步对应 LangGraph 图中的哪个节点或哪条边（§6.6）。

## 5. 架构

```
api.chat ──→ graph（app/graph/）──→ tools.executor → tools
   │            ├─ knowledge.retrieval（retrieve 节点）
   │            ├─ services.grounding（confidence_gate 节点）
   │            ├─ llm（意图识别器、聊天模型）
   │            └─ repositories.messages（finalize 节点）
api.tickets ──→ tools.executor（create_ticket）、graph.aupdate_state、repositories.messages
main.lifespan ──→ AsyncSqliteSaver、build_graph
```

| 模块 | 职责 |
|---|---|
| `app/agent/bare_loop.py` | 热身裸循环（§4） |
| `app/graph/state.py` | `ChatState`、`GraphContext`、`Action` |
| `app/graph/routing.py` | 意图 → 出口的分流表，条件边函数 |
| `app/graph/nodes/` | 每个节点一个函数，按主题分文件：`turn.py`（start_turn、resolve_reference）、`intent.py`、`knowledge.py`（retrieve、confidence_gate）、`agent.py`（agent_model、agent_tools）、`replies.py`（3 个固定话术节点）、`finalize.py` |
| `app/graph/builder.py` | `build_graph(checkpointer) -> CompiledStateGraph`；`get_graph` / `set_graph` |
| `app/graph/control.py` | 控制工具 `offer_human_options` |
| `app/api/chat.py` | 预检、会话锁不变；调用图并把 custom 事件转成 SSE |
| `app/api/tickets.py` | `POST /tickets` |
| `app/services/chat.py` | **删除**。由图替代 |

### 5.1 依赖注入

- 图在 `main.lifespan` 中编译一次：打开 `AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB_PATH)`，调用 `build_graph(checkpointer)`，再调用 `set_graph(graph)`。启动前先创建 `data/` 目录。
- 路由用依赖 `get_graph()` 取图。测试中用 `InMemorySaver` 编译的图覆盖该依赖。
- 每轮的运行时依赖通过 `context=GraphContext(...)` 传入，不放进 State（不需要持久化）：

```python
@dataclass
class GraphContext:
    conversation_id: int
    today: date
    model: BaseChatModel           # 聊天模型，路由依赖 get_chat_model 提供
    execute: Callable = execute_tool_calls
```

- `config = {"configurable": {"thread_id": str(conversation_id)}, "recursion_limit": GRAPH_RECURSION_LIMIT}`。

## 6. 图

### 6.1 State

```python
class Action(TypedDict, total=False):
    type: Literal["handoff", "ticket"]
    description: str          # 仅 ticket
    ticket_type: str          # 仅 ticket："售后" / "投诉" / "咨询"

class ChatState(TypedDict, total=False):
    # 跨轮字段：checkpointer 持久化
    messages: Annotated[list[AnyMessage], add_messages]
    # 本轮字段：start_turn 每轮重置，无 reducer，节点整体覆盖
    user_input: str
    resolved_input: str
    intent: str | None
    route: str
    evidence: list[dict]          # Citation.to_dict() 列表
    gate: dict | None             # {"passed", "top_score", "reason", "source"}
    agent_messages: list[AnyMessage]
    steps: int
    tokens_used: int
    force_final: bool
    reply: str
    actions: list[Action]
    trace: list[str]
```

- `messages` 只由 `finalize` 和 `POST /tickets` 追加。一轮中途失败时，`messages` 不变。这与 ch02"一轮成功才写库"的规则一致。
- 本轮字段在 checkpoint 中也会保存。下一轮由 `start_turn` 覆盖，所以上一轮失败留下的值不影响下一轮。
- 图的输入为 `{"user_input": req.message}`。

### 6.2 节点与边

```
START → start_turn → resolve_reference → classify_intent ─┬─ knowledge → retrieve → confidence_gate ─┬─ 通过 → agent_model
                                                           │                                         └─ 不通过 → fallback_reply
                                                           ├─ business ─────────────────────────────────────────→ agent_model
                                                           ├─ complaint → complaint_reply
                                                           └─ chitchat  → chitchat_reply
agent_model ─┬─ 有工具调用 且 force_final 为假 → agent_tools → agent_model
             └─ 其他 → finalize
fallback_reply / complaint_reply / chitchat_reply → finalize → END
```

每个节点进入时打一行 INFO 日志：`node=<节点名> conversation=<id>`，并把节点名追加到 `trace`。验收 1 检查 `node=retrieve`。

| 节点 | 行为 |
|---|---|
| `start_turn` | 重置全部本轮字段：`steps=0`、`tokens_used=0`、`force_final=False`、列表置空、其余置 `None` 或空串 |
| `resolve_reference` | `resolved_input = user_input`（最简版，原样透传） |
| `classify_intent` | §6.3 |
| `retrieve` | `retrieve(resolved_input)`（ch04 的 `hybrid_rerank`），结果转为 `Citation` 列表（全局编号 1..n，沿用 `collect_evidence`），写入 `evidence` |
| `confidence_gate` | §6.4 |
| `agent_model` | §6.5 |
| `agent_tools` | §6.5 |
| `fallback_reply` | `reply = GATE_FALLBACK_REPLY`，用 `token` 事件发出 |
| `complaint_reply` | `reply = COMPLAINT_REPLY`；`actions = [handoff, ticket(description=user_input[:500], ticket_type="投诉")]`；发 `token` 和 `actions` 事件 |
| `chitchat_reply` | `reply = CHITCHAT_REPLY`，用 `token` 事件发出 |
| `finalize` | §6.7 |

固定话术（常量，放在 `app/prompts.py`）：

| 常量 | 内容 |
|---|---|
| `CHITCHAT_REPLY` | "您好，我是示例商城的售后客服助手。订单、物流、退换货和商品使用问题都可以问我。" |
| `COMPLAINT_REPLY` | "非常抱歉给您带来不好的体验，您的反馈我们很重视。如果需要，您可以选择转人工客服，或者提交一张投诉工单，我们会尽快跟进。" |
| `GATE_FALLBACK_REPLY` | `REFUSAL_PREFIX + "您可以换个问法再试，或者补充商品型号等具体信息。"` |

固定话术节点不调用回答模型。

### 6.3 意图识别（最简版）

- `IntentResult(BaseModel)`：`intent: Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊"]`。
- `get_intent_classifier()`（`app/llm.py`）：`intent_prompt | build_extract_model(...).with_structured_output(IntentResult, method="function_calling", include_raw=True)`。与 ch04 改写器相同，关闭思考。
- `intent_prompt`：一个 system prompt，列出 7 类的定义和 1 至 2 个例子，human 为 `resolved_input`。只看当前这句话，不看历史。
- 调用用 `asyncio.wait_for(..., INTENT_TIMEOUT_SECONDS)`。超时、异常或 `parsed is None` 时，记 WARNING 日志，`intent = None`。
- 测试中由 autouse fixture 拦截 `get_intent_classifier`（沿用 ch04 拦截改写器的做法），需要时用 `RunnableLambda` 替换。

### 6.4 分流与置信度闸

**分流表**（`app/graph/routing.py`，写死）：

```python
INTENT_ROUTES = {
    "商品咨询": "knowledge", "退款退货": "knowledge",
    "物流": "business", "订单": "business", "售后": "business",
    "投诉": "complaint",
    "闲聊": "chitchat",
}
FALLBACK_ROUTE = "business"   # intent 为 None 时
```

`route` 写入 State。条件边函数只读 `route`。

**置信度闸**（`confidence_gate`，最简版，用户选定"得分门槛 + 复用自评"）：

1. 如果 `evidence` 为空，或 Top-1 重排分 < `GATE_MIN_SCORE`，判不通过，`source="retrieval_low_conf"`，`reason="检索证据低于置信度门槛"`。
2. 否则调用 ch04 的 `self_check([resolved_input], citations)`。`useful=false` 时判不通过，`source="self_check"`，`reason` 取自评理由。自评调用失败按通过处理（沿用 ch04）。
3. 不通过时：用 `record_low_confidence` 写 `low_confidence_questions`（独立事务，失败只记日志）。`record_low_confidence` 增加参数 `source`，默认 `"self_check"`。然后流向 `fallback_reply`。
4. 通过时：发 `citations` 事件 `{"items": evidence, "refused": false}`，流向 `agent_model`。

说明：`retrieve` 返回的证据已经过首尾排列，Top-1 重排分取 `Retrieval.ranked[0].score`。`retrieve` 节点把它写入 `gate.top_score` 供闸门使用。

业务数据类不经过这道闸。

### 6.5 主力 Agent（ReAct）

**工具：** `query_order`、`query_logistics`、`query_product`（ch02），加控制工具 `offer_human_options`。

- `ToolRegistry` 新增 `tools_for_model(names: Sequence[str] | None = None)`：传入名称时只返回这些工具，顺序按传入顺序。
- `offer_human_options` 注册到执行器使用的注册表中（`retryable=False`，超时 `TOOL_TIMEOUT_SECONDS`）。它不是业务工具：只返回 `{"shown": options}`，不访问数据库。
- Agent 绑定的工具名常量：`AGENT_TOOLS = ("query_order", "query_logistics", "query_product", "offer_human_options")`。
- `create_ticket` 和 `query_faq` 不绑定给 Agent。`create_ticket` 只由 `POST /tickets` 调用。知识检索只走 `retrieve` 节点。

`offer_human_options` 的参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `options` | `list[Literal["handoff", "ticket"]]`，1 至 2 项，不重复 | 建议给用户的选项 |
| `ticket_description` | `str \| None`，≤ 500 字 | `options` 含 `ticket` 时必填，概括用户诉求 |
| `ticket_type` | `Literal["售后", "投诉", "咨询"] \| None` | `options` 含 `ticket` 时必填 |

参数校验失败时，执行器按 ch02 规则返回 `invalid_arguments`，模型可以在下一步改正。

**`agent_model` 节点：**

1. 组装消息：`AGENT_SYSTEM_TEMPLATE`（§6.8，知识类时带证据段）+ 裁剪后的历史（`build_history(state.messages, ...)`，沿用 ch02 预算）+ `HumanMessage(resolved_input)` + `agent_messages`。
2. 如果 `force_final` 为真：模型不绑定工具，并在末尾追加 `SystemMessage(TOOL_ROUND_CLOSING)`（不写入 State）。
3. 否则：`model.bind_tools(tools_for_model(AGENT_TOOLS), tool_choice="auto")`。
4. 流式调用。正文 token 用 `writer(("token", {"text": ...}))` 发出。如果是强制收尾调用，沿用 ch02 第 2 次调用的前缀缓冲和工具标记防线。
5. 累加 `tokens_used`：优先用合并结果的 `usage_metadata["total_tokens"]`；没有时用 `count_tokens` 估算输入和输出。
6. 结果 `AIMessage(content, tool_calls)` 追加到 `agent_messages`。
7. 如果没有工具调用：正文为空时抛出异常（发 `error`）；正文含工具标记时抛出异常；否则 `reply = 正文`。
8. 如果有工具调用但 `force_final` 为真，按第 7 步的异常处理（不绑定工具时不应出现工具调用）。

**`agent_tools` 节点：**

1. 发 `tool_start` 事件（格式沿用 ch02）。
2. `context.execute(tool_calls, conversation_id=...)`。
3. 发 `tool_end` 事件。
4. `ToolMessage` 追加到 `agent_messages`。`steps += 1`。
5. 如果某个成功的调用是 `offer_human_options`，按参数生成 `actions`（覆盖为最近一次），发 `actions` 事件。
6. 如果 `steps >= AGENT_MAX_STEPS` 或 `tokens_used >= AGENT_TOKEN_BUDGET`，设 `force_final = True`，记 INFO 日志 `agent_limit reason=steps|tokens`。

**停止条件汇总：**

| 条件 | 结果 |
|---|---|
| 模型不调用工具 | 正常收敛 → `finalize` |
| `steps >= AGENT_MAX_STEPS` | 下一次调用不绑定工具，强制文字收尾 |
| `tokens_used >= AGENT_TOKEN_BUDGET` | 同上 |
| 超过 `GRAPH_RECURSION_LIMIT` | `GraphRecursionError` → `error` 事件 |

**追问：** System Prompt 规定"缺少订单号等必要信息时，直接问用户，不猜，不调用工具"。追问是一轮普通文字回复，正常 `finalize`。

### 6.6 裸循环与图的对照

| 裸循环（§4） | LangGraph |
|---|---|
| 消息列表 | `ChatState.messages`（跨轮）+ `agent_messages`（本轮） |
| 调用 LLM | `agent_model` 节点 |
| 有 `tool_calls` 就执行 | 条件边 `agent_model → agent_tools` + `agent_tools` 节点 |
| 把结果喂回去 | 边 `agent_tools → agent_model` |
| 没有 `tool_calls` 就返回 | 条件边 `agent_model → finalize` |
| `max_steps` | `steps` + `force_final` |
| 进程退出即丢失 | checkpointer 按 `thread_id` 持久化 |

### 6.7 finalize（日志记录）

1. 写一行 INFO 结构化日志：`turn conversation=<id> intent=<> route=<> trace=<> gate=<passed/top_score/source> steps=<> tokens=<> actions=<类型列表>`。
2. 写 `messages` 表（一个事务）：
   - 用户消息。
   - Agent 出口：`agent_messages` 中每条带工具调用的 AI 消息和对应的工具消息，按顺序写入。
   - 最终回复。
   - 写入失败时抛出异常（发 `error`，State 历史不追加）。
3. 返回 `{"messages": [HumanMessage(user_input), *agent_messages]}`。固定话术出口追加 `[HumanMessage(user_input), AIMessage(reply)]`。Agent 出口中 `agent_messages` 的最后一条就是最终回复。

`services/history.turn_rows` 扩展为接受多组"工具请求 + 工具结果"。

**写入顺序说明：** MySQL 先写，State 后写（节点返回值由 checkpointer 在节点成功后保存）。如果 checkpointer 写失败，两边会不一致：MySQL 有这一轮，State 没有。本章接受这个限制（§12）。

### 6.8 Agent System Prompt

`AGENT_SYSTEM_TEMPLATE` 从 `CHAT_SYSTEM_TEMPLATE` 改写。变量：`shop_name`、`today`、`evidence_section`。

| 段落 | 相对 `CHAT_SYSTEM_TEMPLATE` 的变化 |
|---|---|
| 职责 | 不变 |
| 工具使用 | 删除第 3 条（query_faq 原话）和第 5 条（create_ticket）。新增：一步能查的就并行调用；需要上一步结果才能查的，看到结果后再查下一步 |
| 追问 | 新增：缺少订单号等必要信息时，直接问用户，不猜，不调用工具 |
| 人工选项 | 新增：用户明确要人工、要投诉跟进、或问题超出工具能力时，调用 `offer_human_options`。调用后在回复中告诉用户"可以点击下方按钮"。不说已经转接，不说已经建单 |
| 知识库证据 | `evidence_section` 不为空时，插入"## 知识库证据"段，内容为 `format_evidence(citations)`。引用和拒答规则沿用 ch04，"本轮知识库结果"改为"知识库证据段" |
| 行为约束第 4 条 | 改为：工单只能由用户点击按钮创建。历史中有"已为您创建工单"时，可以告诉用户工单号 |
| 其余 | 不变 |

`CHAT_SYSTEM_TEMPLATE` 和 `chat_prompt` 保留不变：ch04 的 RAG 评估和工具选择评估把它们当基线。

## 7. 接口

### 7.1 `POST /chat/stream`（变化）

- 请求体、预检、会话锁、404、409、422 不变。
- 预算预检：历史改为从 `graph.aget_state(config).values.get("messages", [])` 读取，再用 `build_history` 裁剪。ch04 及以前的老会话没有 checkpoint，按空历史处理。
- 运行：`graph.astream({"user_input": req.message}, config, context=GraphContext(...), stream_mode="custom")`。每个 custom 事件为 `(name, data)` 元组，转成 `ServerSentEvent(raw_data=json.dumps(data, ensure_ascii=False), event=name)`。
- 事件顺序：`session` → [`citations`] → `token`… / `tool_start` → `tool_end` → [`actions`] → `token`… → [`actions`] → `done`。
- 图抛出任何异常（含 `GraphRecursionError`）时，记 ERROR 日志，发 `error` 事件 `UPSTREAM_ERROR`，不发 `done`。

### 7.2 `POST /tickets`（新增，TDD）

请求：

```json
{"session_id": "12", "user_id": "u1", "description": "快递员态度差……", "ticket_type": "投诉"}
```

- `description`：1 至 500 字。`ticket_type`：`售后` / `投诉` / `咨询`。
- 步骤：
  1. 校验会话属于该用户。不属于时返回 404 `conversation_not_found`。
  2. 拿会话锁。锁被占用时返回 409 `session_busy`。
  3. 用 `execute_tool_calls([{"id": "ticket-<uuid4 前 8 位>", "name": "create_ticket", "args": {...}}], conversation_id=...)` 创建工单。执行器注入 `conversation_id`，不重试。
  4. 结果 `ok=false` 时返回 502 `ticket_failed`。
  5. 写 `messages` 表一行 assistant 消息："已为您创建工单 <ticket_no>，类型：<ticket_type>，我们会尽快处理。"
  6. `graph.aupdate_state(config, {"messages": [AIMessage(同上文字)]}, as_node="finalize")`。
  7. 返回 200 `{"ticket_no": ..., "status": ...}`。
- 第 5、6 步失败时记 ERROR 日志，仍返回 200（工单已写入，不回滚，沿用 ch02"工单副作用独立提交"）。

### 7.3 前端（Vibe Coding，由 Codex 实现）

- 收到 `actions` 事件时，在该条回复下方渲染按钮。`handoff` → 「转人工」，`ticket` → 「建工单」。两个按钮相互独立。
- 「转人工」：弹确认框。确认后，在对话中显示系统提示"已转接人工客服"，再以"客服小猫"身份显示"您好，我是客服小猫，请问有什么可以帮您的"。不调用后端。
- 「建工单」：弹确认框，显示工单类型和可编辑的描述（预填 `description`）。确认后调用 `POST /tickets`。成功时显示工单号；失败时显示错误信息，按钮恢复可点。
- 按钮点过并成功后置灰。用户继续发消息时，对话照常进行。
- 工具名映射加入 `offer_human_options`（显示为"推荐人工选项"）。

## 8. 迁移与清理

- 删除 `app/services/chat.py`。`tests/test_chat_api.py` 按图重写。
- `reset_db.sh` 在 `docker compose down -v` 之后删除 `data/checkpoints.sqlite`。原因：MySQL 重建后会话 ID 从 1 开始，旧的 checkpoint 会被新会话读到。
- `evals/run_chat_samples.py`：用 ASGI transport 调用 app 时不触发 lifespan。脚本自行打开 AsyncSqliteSaver 并 `set_graph`。`evals/chat_samples.md` 中依赖 `create_ticket` 的检查点改为检查 `actions` 事件。
- `evals/run_tool_selection_eval.py` 和 `evals/run_rag_eval.py` 不改（ch04 基线）。

## 9. 错误处理

| 情况 | 处理 |
|---|---|
| 意图识别超时、异常、解析失败 | `intent=None`，走 `business` 出口，记 WARNING |
| 检索异常 | 节点抛出 → `error` 事件 |
| 自评失败 | 按通过处理（ch04） |
| 问题池写入失败 | 只记日志，本轮继续 |
| 工具失败 | 执行器转换为 `ok=false` 结果，模型据此回答（ch02） |
| 模型调用异常、空回复、含工具标记 | 节点抛出 → `error` 事件，不写库，State 历史不变 |
| 步数或 token 超限 | 强制文字收尾 |
| 递归超限 | `error` 事件 |
| `messages` 表写入失败 | `finalize` 抛出 → `error` 事件 |

## 10. 测试

所有图测试用 `InMemorySaver` 编译的图、`ScriptedChatModel`、假检索函数（monkeypatch `retrieve`）和 `RunnableLambda` 意图识别器。

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_bare_loop.py` | 假 `AsyncOpenAI` 客户端：直接回答；1 步工具；2 步工具；达到 `max_steps` 后不带 tools 收尾 |
| `tests/test_graph_routing.py` | 7 类意图 + `None` 的出口；条件边函数 |
| `tests/test_graph.py` | 4 个出口的节点序列（`trace`）；闲聊和投诉不调用聊天模型；闸门两种不通过（入池 `source` 正确，不调用聊天模型）；闸门通过时证据进入 System Prompt；多步 ReAct（`steps=2`）；步数超限和 token 超限后不绑定工具；`offer_human_options` 生成 `actions`；Agent 失败时 `messages` 不变；第 2 轮能看到第 1 轮历史；`start_turn` 重置上一轮失败留下的字段 |
| `tests/test_chat_api.py` | SSE 事件顺序；`error` 事件；预检和锁（保留 ch02 已有用例）；`messages` 表写入 |
| `tests/test_tickets_api.py` | 成功写 `tickets` 表和 `messages` 表，State 追加消息；404；409；`ticket_type` 非法时 422 |
| `tests/test_tools.py` | `offer_human_options` 参数校验；`tools_for_model(names)` |

**不能单测的部分：**
- 意图 Prompt：标注样例集 `evals/intent_samples.jsonl`，7 类各 6 条，共 42 条。`evals/run_intent_eval.py` 跑真实上游，打印混淆矩阵。准确率 < 90% 时退出码 1。
- Agent System Prompt：`evals/run_chat_samples.py` 走完整链路，人工检查。

## 11. 验收脚本 `scripts/demo5.sh`

前置：服务、MySQL、Milvus 已启动，已 `build_kb`。服务日志重定向到文件，脚本参数传入日志路径。

1. 发"退货运费谁出？"。检查服务日志中有 `node=retrieve`，SSE 有 `citations` 事件。
2. 发"订单 1001 的物流到哪了"。检查 SSE 有 `tool_start`，其中有 `query_logistics`。
3. 发"我要投诉"。检查 SSE 有 `actions` 事件，含 `handoff` 和 `ticket`。查 `tickets` 表行数 N。在同一会话再发一句"那算了，先帮我查下订单 1001"，检查 `tickets` 表行数仍为 N。调用 `POST /tickets`，检查 `tickets` 表行数为 N+1。
4. 发"你好呀"。检查回复等于 `CHITCHAT_REPLY`。
5. 发"帮我查下订单 1001 买的是什么，如果已经发货了，再看看物流到哪了"。检查服务日志中该会话 `steps>=2`。（2026-10-08 实施时修订：原问句含"保修"，意图识别会在商品咨询和物流之间摇摆，用户决定换成本句，并收紧 Agent Prompt 的条件依赖规则。）

验收 3 的前端按钮效果由用户在浏览器中手动确认。

## 12. 已知限制

- 意图识别只看当前一句话。多轮对话中，"那退货呢"这类省略句可能判错。正式版放在下一步。
- 一句话里同时有知识类和业务类问题时，只走一个出口。业务出口的 Agent 没有知识检索工具。
- `finalize` 先写 MySQL 再由 checkpointer 写 State。checkpointer 写失败时两边不一致。
- AsyncSqliteSaver 只适合单进程部署。多进程或多机部署时，需要换成服务端数据库的 checkpointer。
- checkpoint 每个超步保存一次，sqlite 文件会持续增长。本章不清理。
- 转人工只在前端模拟。刷新页面后，按钮和转人工提示不保留。
