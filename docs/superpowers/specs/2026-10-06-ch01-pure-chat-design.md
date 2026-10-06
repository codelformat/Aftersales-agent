# ch01 纯对话：设计规格

- 日期：2026-10-06
- 状态：待用户审阅
- 范围：电商智能客服系统第 1 章。只做纯对话，不做工具调用和 Agent 循环。

## 1. 目标与验收标准

**目标：** 跑通一个售后客服对话服务。它提供 SSE 流式多轮对话和售后描述的结构化提取。

**验收标准：**

1. 用 curl 调用对话接口，能看到逐 token 的流式回复。
2. 在同一个 `session_id` 下连续问两轮，第二轮能接上第一轮的上下文。
3. 发送一段售后描述，能得到结构化 JSON。

**本章不做：** 工具调用、Agent 循环、聊天页面、持久化存储、可观测性。

## 2. 技术栈与运行环境

| 项 | 选择 |
|---|---|
| 语言 | Python 3.12（由 `uv` 安装和管理） |
| Web 框架 | FastAPI（实测版本 0.142.2，使用 `fastapi.sse`） |
| LLM 框架 | LangChain：`langchain-core`、`langchain-openai`（实测版本 1.6.x） |
| 配置 | `pydantic-settings` |
| 测试 | `pytest`、`httpx`、anyio pytest 插件（FastAPI 官方文档的异步测试写法） |

**模型接入：** 应用侧只使用 OpenAI 协议（`ChatOpenAI`），直连上游，不经过网关。更换上游（GPT、Claude 兼容层、DeepSeek、Ollama）时，只改 `.env`。

## 3. 配置

`app/config.py` 用 `BaseSettings`，设置 `env_file=".env"` 和 `extra="ignore"`。

**只读取以下变量：**

| 变量 | 必填 | 说明 |
|---|---|---|
| `CHAT_BASE_URL` | 是 | OpenAI 兼容地址 |
| `CHAT_MODEL` | 是 | 上游的真实模型名 |
| `CHAT_API_KEY` | 是 | 密钥 |
| `CHAT_THINKING` | 否 | 思考模式，例如 `adaptive`、`disabled`；不设置则不发送 `thinking` 字段 |

**代码常量（不从环境变量读取）：**

| 常量 | 值 |
|---|---|
| `TOKEN_BUDGET` | `2000` |
| `CHARS_PER_TOKEN` | `2.0`（实测 DeepSeek：55 个中文字符约 30 token；默认值 4.0 会把中文少算约一半） |
| `SHOP_NAME` | `"示例商城"` |
| `UPSTREAM_TIMEOUT_SECONDS` | `60` |
| `UPSTREAM_MAX_RETRIES` | `1` |
| `MAX_INPUT_CHARS` | `2000` |

`.env` 中的其他变量（例如 `TOKEN_BUDGET`、`DATABASE_URL`）一律忽略。`TOKEN_BUDGET` 是代码常量，不是 `Settings` 字段，因此 `.env` 中的同名变量不生效。

## 4. 架构与模块

```
app/
  main.py         FastAPI 应用入口，注册路由
  config.py       Settings 与代码常量
  llm.py          创建聊天模型和提取模型；提供 FastAPI 依赖
  prompts.py      客服模板与提取模板
  session.py      SessionStore：内存会话存储
  context.py      裁剪历史，检查预算
  schemas.py      API 请求/响应模型，AfterSalesRequest
  services/
    chat.py       流式对话
    extract.py    结构化提取
  api/
    chat.py       POST /chat/stream
    extract.py    POST /extract
    health.py     GET /health
tests/            pytest 单元测试
evals/            样例集与验证脚本
scripts/demo.sh   验收演示脚本
```

| 模块 | 职责 | 依赖 |
|---|---|---|
| `config` | 提供只读配置 | pydantic-settings |
| `llm` | 根据配置创建 `ChatOpenAI` 实例，处理思考参数 | `config` |
| `prompts` | 定义模板，不调用模型 | langchain-core |
| `session` | 存取会话历史，不关心 token | 无 |
| `context` | 输入完整历史和新消息，输出裁剪后的历史；不关心存储 | `prompts`、`trim_messages` |
| `services/*` | 组合以上模块，完成一次业务调用 | 以上全部 |
| `api/*` | HTTP 与 SSE 适配，不含业务逻辑 | `services` |

## 5. 模型与思考参数

`app/llm.py` 创建两个 `ChatOpenAI` 实例。两者共用 `base_url`、`model`、`api_key`、`timeout=60`、`max_retries=1`。

| 实例 | `CHAT_THINKING` 已设置 | `CHAT_THINKING` 未设置 |
|---|---|---|
| 聊天模型 | `extra_body={"thinking": {"type": <CHAT_THINKING>}}` | 不设置 `extra_body` |
| 提取模型 | `extra_body={"thinking": {"type": "disabled"}}` | 不设置 `extra_body` |

**为什么提取模型必须关闭思考：** `with_structured_output(method="function_calling")` 会强制指定 tool_choice。DeepSeek 在思考模式下拒绝强制 tool_choice，返回 400 "Thinking mode does not support this tool_choice"。2026-10-06 已实测确认。

**为什么未设置时不发送：** GPT、Ollama 等上游不认识 `thinking` 字段，发送后可能报错。

**注意：** DeepSeek 默认开启思考。使用 DeepSeek 时必须设置 `CHAT_THINKING`，否则提取模型不发送 `disabled`，强制 tool_choice 返回 400，`/extract` 一直返回 502（2026-10-06 实测）。`CHAT_THINKING` 为空字符串时视为未设置（`env_ignore_empty=True`）。

**FastAPI 依赖：** `get_chat_model()` 返回聊天模型；`get_extractor()` 返回提取用的 Runnable。测试时用 `app.dependency_overrides` 替换这两个依赖。

**思考内容的处理：** 思考内容（`reasoning_content`）不推送给客户端，也不写入历史。

## 6. Prompt 模板

### 6.1 客服对话模板

```python
ChatPromptTemplate.from_messages([
    ("system", CHAT_SYSTEM_TEMPLATE),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])
```

`CHAT_SYSTEM_TEMPLATE` 有两个变量：

- `{shop_name}`：店铺名，取值为 `SHOP_NAME`。
- `{today}`：当前日期，格式为 `YYYY-MM-DD`。模型用它判断"7 天无理由"等时限。

System Prompt 包含 3 部分：

1. **角色设定**：`{shop_name}` 的售后客服。
2. **行为约束**：
   - 不编造订单状态、物流信息和店铺政策。不知道时直接说明，并请用户提供信息或转人工。
   - 不承诺具体的退款金额或到账时间。
   - 超出能力范围时，引导用户转人工客服。
   - 只回答售后相关问题。用户问无关问题时，礼貌拒绝，并引导回售后话题。
3. **回复风格**：使用中文，简洁、礼貌，每次回复不超过 200 字。

**注意：** 最终措辞以 `app/prompts.py` 中经 10.3 节人工检查通过的版本为准。执行时修订：加入"询问本次对话内容不属于无关问题""不编造联系入口""不复述约束"，并要求纯文本输出、不使用 Markdown、先给结论。

### 6.2 提取模板

```python
ChatPromptTemplate.from_messages([
    ("system", EXTRACT_SYSTEM_PROMPT),
    ("human", "{text}"),
])
```

`EXTRACT_SYSTEM_PROMPT` 定义以下规则：

- **诉求类型判定：**
  - `退货`：用户要退回商品，并退钱。
  - `换货`：用户要退回商品，换一件同款或其他款。
  - `退款`：用户只要钱，不涉及退回商品。例如未发货、少发、仅退款。
  - `维修`：用户要修好商品。
  - `投诉`：用户对服务、物流或商家表达不满，主要诉求是追责或要求改进。
  - `咨询`：用户询问政策、流程或进度，没有明确的售后要求。
- **订单号**：只提取原文中出现的订单号，保持原样。原文没有订单号时为 `null`，不许编造。
- **期望方案**：写用户想要的结果，例如"更换新耳机"。不复述问题本身。用户没有说明期望时，写"未说明"。

**注意：** 上述判定规则的最终措辞，以样例集验证通过的版本为准（见 10.2 节）。

## 7. 数据模型

```python
class RequestType(str, Enum):
    RETURN = "退货"
    EXCHANGE = "换货"
    REFUND = "退款"
    REPAIR = "维修"
    COMPLAINT = "投诉"
    INQUIRY = "咨询"

class AfterSalesRequest(BaseModel):
    """从用户的售后描述中提取的结构化信息。"""
    order_id: str | None = Field(None, description="订单号；原文没有则为 null")
    request_type: RequestType = Field(description="诉求类型")
    expected_solution: str = Field(description="用户期望的解决方案")
```

`Field` 的 description 会进入 function schema，因此要与 6.2 节的规则一致。

## 8. 接口

### 8.1 `POST /chat/stream`

**请求体：**

```json
{"session_id": "可选，字符串", "message": "必填，1–2000 字符"}
```

- 如果不传 `session_id`，服务端生成一个 UUID4。
- 如果传入的 `session_id` 不存在，服务端用这个 ID 新建会话。

**响应：** `text/event-stream`。每个事件的 data 是单行 JSON，用 `ServerSentEvent(raw_data=json.dumps(payload, ensure_ascii=False), event=...)` 生成。

**为什么不用 `ServerSentEvent(data=...)`：** 它会把中文转义成 `\uXXXX`，curl 输出无法阅读。已用 FastAPI 0.142.2 实测确认。

| 顺序 | event | data |
|---|---|---|
| 1 | `session` | `{"session_id": "..."}` |
| 2…n | `token` | `{"text": "增量文本"}`；每个非空 chunk 一个事件 |
| 最后 | `done` | `{"finish_reason": "stop"}` |
| 出错时代替 `done` | `error` | `{"code": "upstream_error", "message": "服务暂时不可用，请稍后重试"}` |

**流开始前的错误**返回普通 HTTP 响应：

| 状态码 | `detail.code` | 条件 |
|---|---|---|
| 422 | （FastAPI 默认） | 请求体校验失败 |
| 422 | `budget_exceeded` | System Prompt 加当前消息超过 `TOKEN_BUDGET` |
| 409 | `session_busy` | 同一会话已有请求正在处理 |

### 8.2 `POST /extract`

**请求体：**

```json
{"text": "必填，1–2000 字符"}
```

**响应 200：**

```json
{"order_id": "A12345", "request_type": "换货", "expected_solution": "更换新耳机"}
```

**错误：**

| 状态码 | `detail.code` | 条件 |
|---|---|---|
| 422 | （FastAPI 默认） | 请求体校验失败 |
| 502 | `extraction_failed` | 上游调用失败，或输出无法解析为 `AfterSalesRequest` |

这个接口无状态，不读写会话。

### 8.3 `GET /health`

返回 `{"status": "ok"}`，不调用上游。

## 9. 数据流与错误处理

### 9.1 会话存储

`SessionStore` 是进程内单例，内部为 `dict[str, Session]`。每个 `Session` 包含：

- `messages: list[BaseMessage]`：完整历史，不裁剪。
- `lock: asyncio.Lock`：保证同一会话同一时刻只处理一个请求。

进程重启后，所有会话清空。

### 9.2 对话流程

**为什么用依赖做预检：** FastAPI 的 yield 型 SSE 端点在函数体内抛出 `HTTPException` 时，客户端收到的是 200 和空流，错误丢失。在 `Depends` 依赖中抛出时，客户端能收到正常的 422/409。已用 FastAPI 0.142.2 实测确认。

**阶段 A：预检。** 依赖函数 `prepare_chat_turn` 在流开始前执行：

1. 取得或新建会话。如果没有 `session_id`，生成 UUID4。
2. 如果 `session.lock.locked()` 为真，返回 409 `session_busy`。
3. 渲染 System Prompt，用 `count_tokens_approximately(..., chars_per_token=CHARS_PER_TOKEN)` 计算"System Prompt + 当前 human 消息"的 token 数 `fixed`。
4. 如果 `fixed > TOKEN_BUDGET`，返回 422 `budget_exceeded`。
5. 裁剪历史：

   ```python
   trim_messages(
       session.messages,
       max_tokens=TOKEN_BUDGET - fixed,
       strategy="last",
       token_counter=count_tokens,  # chars_per_token=CHARS_PER_TOKEN
       start_on="human",
   )
   ```

6. 获取 `session.lock`。第 2 步到本步之间没有 `await`，所以检查和获取之间不会切换协程；预算超限时也无需释放锁。
7. `yield` 一个 `ChatTurn`（会话、裁剪后的历史、当前消息），并在 `finally` 中释放 `session.lock`。

**为什么由 yield 依赖释放锁（执行时修订）：** `prepare_chat_turn` 是 yield 依赖，默认 `scope="request"`。FastAPI ≥ 0.118 在响应（包括流式响应）发送完之后才执行它的退出代码，所以锁覆盖整个流。如果在端点生成器中释放锁，预检之后、生成器开始之前出错时（例如模型依赖抛异常），锁永远不释放。不要改为 `scope="function"`，否则锁会在流开始前释放。回归测试：`test_lock_released_when_model_dependency_fails`、`test_lock_held_during_stream_and_released_after`。

**阶段 B：流式输出。** 端点函数是异步生成器：

1. 发送 `session` 事件。
2. 调用 `(chat_prompt | chat_model).astream({...})`。每收到一个 `content` 非空的 chunk，发送一个 `token` 事件，并把文本累加到缓冲区。
3. 流正常结束且缓冲区非空时，把 `HumanMessage(当前消息)` 和 `AIMessage(完整回复)` 追加到 `session.messages`，然后发送 `done` 事件。
4. 流正常结束但缓冲区为空时（例如思考 token 耗尽），不写历史，发送 `error` 事件 `upstream_error`。原因：空 assistant 消息写入历史后，部分上游会拒绝下一轮请求。

**历史写入规则：** 只有流正常结束才写入。出错或客户端断开时，用户消息和回复都不写入。这样会话中不会出现半截回复。

### 9.3 提取流程

1. 校验请求体。
2. 调用：

   ```python
   (extract_prompt | extract_model.with_structured_output(
       AfterSalesRequest, method="function_calling", include_raw=True
   )).ainvoke({"text": text})
   ```

3. 如果 `parsed` 不为空，返回 200 和 `parsed` 的 JSON。
4. 如果 `parsing_error` 不为空，或调用抛出异常，记录日志，返回 502 `extraction_failed`。

### 9.4 错误处理汇总

| 情况 | 时机 | 处理 |
|---|---|---|
| 请求体不合法 | 预检 | 422 |
| 超出预算 | 预检 | 422 `budget_exceeded` |
| 会话正忙 | 预检 | 409 `session_busy` |
| 上游报错或超时 | 流中 | 发送 `error` 事件后结束流；不写历史；记录异常日志 |
| 上游返回空回复 | 流结束 | 发送 `error` 事件 `upstream_error`；不写历史；记录警告日志 |
| 客户端断开 | 流中 | 生成器被取消；不写历史；`prepare_chat_turn` 依赖释放锁 |
| 提取失败 | `/extract` | 502 `extraction_failed`；记录原始输出 |

**错误信息不外泄：** `error` 事件和 502 响应只返回固定文案。完整异常只写入服务端日志，以免泄露上游地址或密钥。

**重试：** `max_retries=1` 只在请求失败时重试。流已经开始输出后，失败不重试。

## 10. 测试与验证

### 10.1 单元测试（不调用上游）

**模型替身：**

- 聊天：`langchain_core` 的 `FakeListChatModel` 或 `GenericFakeChatModel`。用 `FakeListChatModel(error_on_chunk_number=N)` 模拟流中途出错。
- 提取：一个返回固定 `{"raw": ..., "parsed": ..., "parsing_error": ...}` 的 Runnable。

**测试清单：**

| 模块 | 测试内容 |
|---|---|
| `config` | 只读取指定变量；`CHAT_THINKING` 未设置时为 `None`；`TOKEN_BUDGET == 2000` |
| `llm` | 设置 `CHAT_THINKING` 时，聊天模型 `extra_body` 为该值，提取模型为 `disabled`；未设置时两者都没有 `thinking` |
| `prompts` | 渲染结果包含 `shop_name`、`today`；历史位于 system 与 human 之间 |
| `session` | 新建、读取、追加；锁占用可检测 |
| `context` | ① 未超预算时历史全部保留；② 超预算时丢弃最旧消息；③ 裁剪后首条为 human；④ 固定部分超预算时抛 `BudgetExceeded` |
| `api/chat` | ① 事件顺序为 session → token… → done；② 第二轮的模型输入包含第一轮问答；③ 流中途出错时发送 error 且不写历史；④ 超预算返回 422；⑤ 会话正忙返回 409；⑥ SSE 中的中文不转义 |
| `api/extract` | 解析成功返回 200；`parsing_error` 不为空返回 502；请求体为空返回 422 |

### 10.2 提取 Prompt 样例集验证（代替 TDD）

- **文件：** `evals/extract_samples.jsonl`。每行一个样例：`{"text": ..., "expected": {"order_id": ..., "request_type": ...}}`。
- **规模：** 20 条。6 种诉求类型都覆盖，每种至少 3 条。
- **必须包含：**
  - 设计阶段实测中的 2 条问题样例："羊毛衫缩水……直接退钱吧，不想要了"；"快递太慢了……我要投诉"。
  - 无订单号的样例。
  - 多种订单号格式：纯数字、带横线、带字母。
  - 易混淆的样例：退货与退款，换货与维修。
- **判分方法：** `order_id` 与 `request_type` 必须完全匹配。脚本打印 `expected_solution`，由人检查是否复述了问题。
- **脚本：** `evals/run_extract_eval.py`。它调用真实上游，输出每条结果和准确率。
- **通过标准：** `request_type` 准确率 ≥ 90%（20 条中最多错 2 条）；`order_id` 准确率为 100%。
- **流程：**
  1. 写样例集，交用户审核标注。
  2. 写提取 Prompt。
  3. 运行脚本。
  4. 如果不达标，修改 Prompt 后重跑。每一轮结果记入 `dev-notes/ch01.md`。

### 10.3 客服 System Prompt 人工检查

`evals/chat_samples.md` 包含 7 条对话样例（第 6、7 条在执行时加入）：

1. 正常售后咨询。
2. 询问订单状态。检查模型不编造订单状态。
3. 要求承诺退款金额。检查模型不做承诺。
4. 询问与售后无关的问题。检查模型拒绝并引导回售后话题。
5. 要求转人工。
6. 询问对话本身（"我上一句问了什么？"）。检查模型不当作无关问题拒绝。
7. 比较退货与换货。检查回复为纯文本、不超过 200 字。

`evals/run_chat_samples.py` 批量调用真实上游并打印回复，由人判断是否合格。

### 10.4 验收演示

`scripts/demo.sh` 用 curl 依次执行 3 项验收：

1. `curl -N` 调用 `/chat/stream`，观察逐 token 输出。
2. 在同一个 `session_id` 下问两轮，第二轮的问题依赖第一轮内容。
3. 调用 `/extract`，返回结构化 JSON。

## 11. 已知限制

- 会话存储在进程内存中。进程重启后会话丢失；多进程部署时会话不共享。
- `count_tokens_approximately` 是近似计数，与上游真实 token 数有偏差。
- 会话数量没有上限，也没有过期清理。本章只用于演示。
