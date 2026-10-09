# ch07 会话上下文管理：设计规格

- 日期：2026-10-08
- 状态：待用户审阅
- 分支：`ch07`（从 `ch06` 拉出。ch06 的 PR #2 未合并）
- 前置：ch06（`docs/superpowers/specs/2026-10-08-ch06-router-design.md`）。本文只写新增和变化的部分。没有提到的 ch06 行为保持不变。

## 1. 目标与验收标准

**目标：** 把 ch01 起的简单裁剪（`build_history` + `TOKEN_BUDGET=2000`）升级为分层上下文管理，让客服在一通会话中记住前面聊过的内容。只管当前会话。

1. 三层结构：层 1 原文；层 2 按规则截短；层 3 后台异步压成分段梗概。两个锚点 `summary_upto_msg_id`、`layer1_from_msg_id` 划边界，降级只挪锚点，不搬数据。梗概存 `conversation_summaries`。
2. 摘要后台异步：层 2 超预算时起摘要任务，追加新的一段，边界追到层 1 起点，不阻塞当前回复。梗概只追加、不重写。触发看 token 用量，不数条数。
3. 上下文按固定顺序拼装：固定 System → 层 2 → 层 1 → 用户当前这句 → 一条参考资料消息（梗概 + 证据等）。梗概不单独占 System。
4. token 预算从模型窗口倒推。启动时自检，连一轮都装不下就报警。字符/token 折算口径和预算一起校准。
5. 完整历史在 State `messages`（`add_messages` + checkpoint）；调模型前另外整理精简版，两者互不影响。
6. 可观测：`model_ctx`、`history_ctx` 每次调模型前打到 `log/app.log`；摘要任务的触发、开始、完成、跳过、失败都打日志，带覆盖边界和耗时。
7. 前端会话侧栏 + 两个只读接口。

**验收标准：**

1. 连续聊 20 轮以上，token 不超限，系统不崩。
2. 演示配置 `MODEL_CONTEXT_WINDOW=18000 MAX_OUTPUT_TOKENS=2000 MAX_USER_INPUT_TOKENS=2000 MAX_AGENT_STEPS=3 TOOL_RESULT_MAX_TOKENS=1200 RERANK_TOP_K=5` 算出滑窗 5650、层 1 预算 3954、层 2 预算 1695。日志能看到完整级联：`层1 降级 X→Y`、`summary trigger 层2 约 N token > 预算 M`、`summary done 第N段`。再问"最开始那个订单后来怎么说"，回复能答对订单号和诉求。只改窗口（其余默认）时，滑窗归零并报"上下文预算不足"。
3. 默认配置聊 20 轮，不触发任何降级和摘要。
4. 日志能看到后台摘要压缩早期轮次，且摘要没有阻塞该轮回复。`grep model_ctx` / `grep history_ctx` 能看到每轮发给模型的摘要和滑窗。
5. 前端侧栏能开多个会话对照，切回旧会话时历史完整回载，能接着聊。

**本章不做：** 语义检索捞历史、按主题重要度保留关键事实、跨会话长期记忆、用户画像、回载时恢复引用卡片和按钮。

## 2. 技术栈

LangGraph State（`add_messages` + `AsyncSqliteSaver`）、LangChain `trim_messages`、MySQL（`conversations`、`messages`、`conversation_summaries`）。

Context7 核对结果（langchain-core 1.6.6、langgraph 1.2.14）：

- `trim_messages(messages, max_tokens=, token_counter=, strategy="last", start_on="human")` 保留末尾不超过 `max_tokens` 的消息，结果从 HumanMessage 开始。
- `add_messages` 给没有 id 的消息分配 UUID；新消息与已有消息 id 相同时替换。

## 3. 数据

### 3.1 表结构

`db/schema_ch07.sql` 是用户 DDL（原 `ch07.sql`），逐字保存，是新列和新表的唯一来源：

- `conversations` 加 `summary`（最近几段梗概拼成的投影）、`summary_upto_msg_id`、`layer1_from_msg_id`。
- 新表 `conversation_summaries`：`(conversation_id, seq)` 唯一，`from_msg_id`、`upto_msg_id` 闭区间，`content`，只追加。

`docker-compose.yml` 挂载为 `05-schema-ch07.sql`。`reset_db.sh` 增加 `conversation_summaries` 存在性检查。ORM 只映射，不 `create_all`。

### 3.2 `messages` 表写入（变化，用户裁定）

- `finalize` 只写用户消息和最终回复，不再写 `role=tool` 行和只含工具调用的 assistant 行。工具调用和结果只留在 State。
- `finalize` 写库 `flush` 后取得主键，给 State 中的 `HumanMessage` 和最终 `AIMessage` 设 `id="msg-<主键>"`，再返回给 `add_messages`。
- `POST /tickets`、`POST /refunds` 的提示消息同样先写库，再以 `msg-<主键>` 写 State。
- 旧 checkpoint 中的消息没有 `msg-` id。升级后执行 `bash scripts/reset_db.sh`。

### 3.3 层的划分（`app/context/layers.py`）

每条 State 消息的**有效 id**：自身 id 为 `msg-<n>` 时取 n；否则（工具调用、工具结果）取前一条消息的有效 id。

| 条件 | 层 |
|---|---|
| 有效 id ≤ `summary_upto_msg_id` | 已进摘要，不发给模型 |
| `summary_upto` < 有效 id ≤ `layer1_from_msg_id` | 层 2 |
| 有效 id > `layer1_from_msg_id` | 层 1 |

锚点为 NULL 表示该边界还没设。两个都为 NULL 时全部是层 1。`layer1_from` 只会设在一轮的最后一个有 DB id 的消息上，同一轮的消息不会被拆到两层。

## 4. 配置与预算

### 4.1 新配置

环境变量（`Settings` 字段，带默认值；用户在需求中给出名称）：

| 变量 | 默认 | 用途 |
|---|---|---|
| `MODEL_CONTEXT_WINDOW` | 128000 | 窗口 W |
| `MAX_OUTPUT_TOKENS` | 8192 | 输出预留 O。只进预算，不作为 `max_tokens` 发给上游（思考 token 计入，太小会返回空 content） |
| `MAX_USER_INPUT_TOKENS` | 1000 | 用户输入上限 U。`/chat/stream` 预检，超出返回 422 `budget_exceeded`。`MAX_INPUT_CHARS=2000` 的 schema 校验保留 |
| `MAX_AGENT_STEPS` | 4 | 取代 `AGENT_MAX_STEPS` |
| `TOOL_RESULT_MAX_TOKENS` | 750 | 执行器截断长度 = `int(T × CHARS_PER_TOKEN)` 字符，取代 `TOOL_RESULT_MAX_CHARS` |
| `RERANK_TOP_K` | 10 | 生产链路证据条数 K。`retrieve`、`retrieve_multi` 增加 `top_n` 参数，生产节点传 K；ch04 评估仍用 `EVIDENCE_TOP_N=10` |

代码常量（`app/config.py`）：

| 常量 | 值 | 含义 |
|---|---|---|
| `SYSTEM_RESERVE_TOKENS` (SYS) | 1800 | System Prompt + 工具定义的预留 |
| `EVIDENCE_ITEM_TOKENS` (EV) | 250 | 每条证据的预留 |
| `SUMMARY_RESERVE_TOKENS` (SUM) | 500 | 注入梗概的预留，也是投影长度上限 |
| `SAFETY_MARGIN_RATIO` | 0.05 | 安全余量 = W × 0.05 |
| `STEP_OVERHEAD_TOKENS` (STEP) | 100 | 每步 AI 工具调用消息的开销 |
| `KEEP_TURNS` | 30 | 想留住的轮数 |
| `TURN_TOKENS` | 实测定值（初值 800） | 每轮稳态占用 |
| `LAYER1_RATIO` | 0.7 | 层 1 份额；层 2 = 0.3 |
| `LAYER1_LOW_WATER` | 0.6 | 层 1 降级后的目标水位（× L1 预算） |
| `LAYER2_REPLY_CHARS` | 60 | 层 2 客服答复保留字数 |
| `LAYER2_TOOL_MAX_CHARS` | 80 | 层 2 工具结果超过此长度换成一行标识 |
| `SUMMARY_TIMEOUT_SECONDS` | 30 | 摘要调用超时 |
| `SUMMARY_MAX_CHARS` | 300 | 摘要输出硬上限 |
| `SUMMARY_TOOL_RESULT_CHARS` | 200 | 摘要输入中工具结果的截断长度 |

删除 `TOKEN_BUDGET`、`AGENT_MAX_STEPS`、`TOOL_RESULT_MAX_CHARS`，删除 `app/context.py` 的 `build_history` 和 `BudgetExceeded`。

### 4.2 公式（`app/context/budget.py`）

```
fixed   = O + int(W × SAFETY_MARGIN_RATIO) + SYS + K × EV + SUM
peak    = U + steps × (T + STEP)
avail   = W − fixed − peak
history = max(0, min(KEEP_TURNS × TURN_TOKENS, avail))
L1 = int(history × 0.7)
L2 = int(history × 0.3)
```

演示配置：`18000 − 2000 − 900 − 1800 − 5×250 − 500 − (2000 + 3×(1200+100)) = 5650`，L1 = 3954，L2 = 1695（Python `int(5650*0.7)` 为 3954）。

只改窗口为 18000：`18000 − 8192 − 900 − 1800 − 2500 − 500 − (1000 + 4×850) = −292` → history = 0。

`ContextBudget` 是 frozen dataclass，带全部输入项和结果，由 `get_budget()` 按 `Settings` 计算并缓存。

### 4.3 启动自检

`lifespan` 启动时：

1. 打一行 `budget window= output= margin= system= evidence= summary= peak= avail= history= layer1= layer2=`。
2. 实测 System Prompt + 工具定义的 token。超过 SYS 时 warning `system_reserve_exceeded measured= reserve=`。
3. `history ≤ 0` 或 `L1 < TURN_TOKENS` 时 warning `上下文预算不足 history= layer1= turn_tokens=`。不阻止启动。

### 4.4 口径校准

`evals/run_token_calibration.py`（真实上游）：

1. 用约 20 条样本（中文对话、证据段、工具结果 JSON、System Prompt）各发一次请求，取 `usage.prompt_tokens`。
2. 对比 `count_tokens` 的估算，输出每条的字符/token 比值和建议的 `CHARS_PER_TOKEN`（取估算不低于真实值的保守值）。
3. 更新 `CHARS_PER_TOKEN` 后，用同一口径重新实测 SYS、EV、`TURN_TOKENS`。

只改折算比、不改预留，预算的净效果会反向。所以两者在同一个任务中一起改。改后如果演示配置不再得到 5650，调整预留项并在本文记录原因。

**校准结果（2026-10-09，DeepSeek `deepseek-v4-flash`）：**

| 项 | 实测（真实 token） | 处理 |
|---|---|---|
| 中文正文（System、证据、用户话） | 1.54–1.67 字符/token；极短句受基线 ±1 token 影响低至 1.36 | `CHARS_PER_TOKEN` 2.0 → 1.5（估算略高于真实值） |
| 工具定义和工具结果 JSON | 2.36–2.38 字符/token | 新增 `TOOL_SCHEMA_CHARS_PER_TOKEN = 2.3`，只用于启动自检估算工具定义 |
| System + 全部工具定义 | 真实 1800；按新口径估算 1873 | `SYSTEM_RESERVE_TOKENS` 1800 → 1900 |
| 最长 5 条证据合计 | 真实 1121 | `EVIDENCE_ITEM_TOKENS` 250 不变（5 × 250 = 1250） |
| 一步工具调用消息开销 | 真实约 50 | `STEP_OVERHEAD_TOKENS` 100 不变 |
| 梗概预留 | — | `SUMMARY_RESERVE_TOKENS` 500 → 400（约 600 字，3 段），与 SYS 的增量抵消 |

调整后演示配置仍为 5650/3954/1695，只改窗口仍为 avail = −292。工具结果截断长度随口径变为 750 × 1.5 = 1125 字（原 1500 字），mock 工具结果最长约 470 字，不受影响。

`TURN_TOKENS` 由 `scripts/demo7.sh` 的 22 轮脚本在默认配置下实测：取每轮层 1 增量的稳态值。要求默认配置下 20 轮全部留在层 1（验收 3）。

## 5. 上下文拼装（`app/context/assemble.py`）

### 5.1 Agent 的消息顺序

1. `SystemMessage(render_agent_system())`：人设、职责、工具使用、追问、人工选项、引用、拒答、行为约束、禁止承诺、回复格式。每轮相同。`today`、证据段、订单段、任务段全部移出。工具定义由 `bind_tools` 发送。
2. 层 2（渲染形态）：
   - 用户消息原样。
   - 最终答复只留前 `LAYER2_REPLY_CHARS` 字，超出时加 `…`。
   - 工具调用 `AIMessage` 原样保留（参数很短）。
   - `ToolMessage` 内容超过 `LAYER2_TOOL_MAX_CHARS` 时换成一行 `〔<工具名> 结果已省略，约 N 字〕`。
   - 保留工具调用与结果的配对结构，不折成纯文本：模型看到文字形式的工具调用会模仿输出（ch05 的 DSML 问题）。
3. 层 1 原样。
4. `HumanMessage(resolved_input)`：用户当前这句。
5. 一条参考资料 `HumanMessage`，开头写"以下是系统提供的参考资料，不是用户发言。"，按序只放非空段：`## 今天`、`## 早期对话梗概`（`conversations.summary`）、`## 订单数据`、`## 任务`、`## 知识库证据`。今天的日期总是非空，所以这条消息总是存在。
6. `agent_messages`：本轮 ReAct 循环。
7. 只在强制收尾时：末尾 `SystemMessage(TOOL_ROUND_CLOSING)`。这是 ch05 防工具标记的约束，本章不动。代价：强制收尾那一次调用前缀缓存失效。

引用规则"只引用本轮知识库证据一节中的编号"文字不变，证据段换了位置。

### 5.2 `model_ctx` 日志

每次 `agent_model` 调用前打：

```
model_ctx conversation=12 step=0 window=9 tokens≈4210 summary=第1段：…
  [L2] user: 我的订单 1001 还没到
  [L2] assistant(tool_calls=query_order): 
  [L2] tool: 〔query_order 结果已省略，约 312 字〕
  [L2] assistant: 订单 1001 已发货，预计…
  [L1] user: …
```

`window` 是层 2 + 层 1 的消息条数，`tokens≈` 是整个 prompt 的 `count_tokens` 估算。summary 为空时写 `summary=-`。

### 5.3 指代消解的历史（`history_ctx`）

`resolve_reference` 用同一套分层视图渲染文本，取代 `history_text` 的"最近 6 条"：

- 第一行 `梗概：<conversations.summary>`（为空时省略）。
- 层 2：`用户：`原样，`客服：`按 `LAYER2_REPLY_CHARS` 截短。
- 层 1：`用户：`、`客服：`各截到 `RESOLVE_MESSAGE_MAX_CHARS`。
- 工具消息不渲染。

每轮在 `resolve_reference` 中打 `history_ctx conversation= lines= summary=` 加逐行内容。`resolve_reference` 在分流前执行，所以闲聊兜底轮也有。意图识别仍只用 `resolved_input`（ch06 设计），历史经指代消解进入意图识别。`evals/run_multiturn_eval.py` 改用同一渲染函数（锚点为空）。

### 5.4 State 字段

`ChatState` 新增本轮字段，由 `start_turn` 从 `conversations` 读取后写入：`summary: str | None`、`summary_upto: int | None`、`layer1_from: int | None`。恢复（`/chat/resume`）时沿用 interrupt 前的值。精简视图不写入 State。

## 6. 降级与摘要

### 6.1 层 1 降级（`finalize` 末尾）

在写库、设 id 之后，用"State 历史 + 本轮消息"计算：

1. 层 1 token（原文，`count_tokens`）≤ L1：不动。
2. 超出：`trim_messages(层1, max_tokens=int(L1 × LAYER1_LOW_WATER), strategy="last", start_on="human", token_counter=count_tokens)`。保留部分之前最后一个有 DB id 的消息作为新的 `layer1_from`。保留部分为空时，`layer1_from` 取层 1 最后一个有 DB id 的消息（全部降级）。
3. `UPDATE conversations SET layer1_from_msg_id=:new WHERE id=:cid AND (layer1_from_msg_id IS NULL OR layer1_from_msg_id < :new)`。
4. 日志 `层1 降级 conversation= X→Y 层1 约 N token > 预算 M`（X 为 NULL 时写 `-`）。

水位 0.6 让层 1 一次降一批，不会每轮降一条。

### 6.2 摘要触发

降级后计算层 2（渲染形态）token：

- ≤ L2：不动。
- 超出且该会话没有运行中的任务：日志 `summary trigger conversation= 层2 约 N token > 预算 M range=a..b`，起后台任务。批次为有效 id 在 `(summary_upto, layer1_from]` 的消息快照，`a`、`b` 为批次首尾 DB id，`b` = 当时的 `layer1_from`。
- 超出但已有运行中的任务：日志 `summary skip conversation= reason=running`。

降级和触发包在 `try` 中。失败时打 `context_maintain_failed`（带堆栈），本轮照常成功。

### 6.3 后台任务（`app/context/summarizer.py`）

`SummaryRunner`（进程内单例）：`asyncio.create_task` 起任务，持有强引用，每个会话最多一个运行中任务。`lifespan` 退出时取消运行中任务，日志 `summary cancel conversation=`。测试可调 `runner.drain()` 等待。

任务步骤：

1. 日志 `summary start conversation= range=a..b msgs=n`。
2. 读该会话已有的全部段落，作为背景放进输入。旧段落只给模型看，不参与合并，不重写。
3. 批次渲染成文本：`用户：`原文；`客服：`全文；工具调用写成 `调用 query_order(order_id=1001)`；工具结果截到 `SUMMARY_TOOL_RESULT_CHARS`。
4. 调摘要模型：`get_summarizer()` 工厂，关闭思考，纯文本输出，超时 `SUMMARY_TIMEOUT_SECONDS`，重试交给 SDK 的 `max_retries`（指数回退）。
5. 校验：非空；不超过 `SUMMARY_MAX_CHARS`；输出中每个 4 位以上的数字串都必须出现在批次文本或旧段落中，否则失败 `unsupported_number`。
6. 同一事务写入：`seq = max(seq) + 1` 插入一段；更新 `conversations.summary_upto_msg_id = b` 和 `summary = 投影`，条件 `summary_upto_msg_id IS NULL OR summary_upto_msg_id < b`。
7. 日志 `summary done conversation= 第N段 range=a..b chars= elapsed=1.2s`。

失败（超时、上游错误、校验失败、写库失败）：日志 `summary fail conversation= range=a..b elapsed= error=`，锚点不动。层 2 仍超预算，下一轮再触发（每轮最多一次，不是重试循环）。

**投影：** 从最新一段往前取，总 token 不超过 SUM 的若干段，按时间顺序拼成 `第1段：…\n第2段：…`。更早的段落留在表中，不进 prompt。

### 6.4 摘要 Prompt（`SUMMARY_SYSTEM_PROMPT`）

- 只提炼事实与诉求：问过哪款商品、报过的订单号和手机号、明确诉求、还没解决的问题。
- 对话中没出现的内容一个字不许写。数字、型号、订单号原样照抄。
- 寒暄、闲聊、客服的安抚话不留。
- 已有梗概只作背景：不复述、不改写，只写本批新增的事实。
- 长度 30 至 200 字，纯文本，一段。

## 7. 并发与一致性

- 一轮在会话锁内执行。摘要任务在锁外，只写 `summary_upto_msg_id`、`summary` 和 `conversation_summaries`。降级只写 `layer1_from_msg_id`。两个 UPDATE 都带"只增不减"条件。
- `summary_upto` 取触发时的 `layer1_from`，`layer1_from` 只增，所以 `summary_upto ≤ layer1_from` 恒成立。
- 摘要运行期间，下一轮的层 2 仍可超预算，模型照常看到渲染形态。
- 进程重启丢失运行中的任务，下一轮重新触发。`(conversation_id, seq)` 唯一键防止重复段号。

## 8. 接口

### 8.1 `GET /api/conversations?user_id=`（新增，TDD）

返回该用户的会话，按 `id` 倒序，最多 50 条：`[{session_id, created_at, updated_at, preview, summarized}]`。`preview` 为首条用户消息前 30 字（没有时为空串），`summarized` 为 `summary_upto_msg_id IS NOT NULL`。

### 8.2 `GET /api/conversations/{id}/messages?user_id=`（新增，TDD）

会话不属于该用户时 404 `conversation_not_found`。返回 `[{id, role, content, created_at}]`，只含 `user` 和 `assistant` 且 `content` 非空的行。

### 8.3 `POST /chat/stream`（变化）

预检改为：用户消息 `count_tokens` 超过 `MAX_USER_INPUT_TOKENS` 时 422 `budget_exceeded`。不再读 checkpoint 做历史预算检查。

### 8.4 前端（Vibe Coding，由 Codex 实现）

- 聊天页左侧加会话侧栏：新在前，显示首问预览和"已摘要"标记。
- 点击会话：调 8.2 回载历史原文，设 `sessionId`，可接着聊。
- "新对话"只清空当前 `sessionId` 和消息区，旧会话仍在侧栏。
- `session` 事件和 `done` 事件后刷新侧栏。
- 侧栏请求失败时静默降级（`console.warn`），不影响聊天。
- 回载只显示文本，不恢复引用卡片、按钮、订单选择器。

## 9. 日志

`app/main.py` 给根 logger 加 `FileHandler("log/app.log", encoding="utf-8")`，保留控制台输出。`log/` 加入 `.gitignore`。启动时目录不存在则创建。

日志行汇总：`budget`、`上下文预算不足`、`system_reserve_exceeded`、`model_ctx`、`history_ctx`、`context_usage`、`层1 降级`、`summary trigger|skip|start|done|fail|cancel`、`context_maintain_failed`。

`context_usage conversation= layer1=<token>/<预算> layer2=<token>/<预算>`：`finalize` 每轮维护后打一行（计划阶段补充，用于实测 `TURN_TOKENS` 和观察水位）。

## 10. 错误处理

| 情况 | 行为 |
|---|---|
| 启动时预算不足 | warning，服务照常启动；history = 0 时模型只看到梗概和当前这句 |
| 读 `conversations` 锚点失败（`start_turn`） | 抛出，本轮 `error` 事件（与其他写库失败一致） |
| 降级或触发失败 | `context_maintain_failed`，本轮成功 |
| 摘要失败 | `summary fail`，锚点不动，下轮再触发 |
| 侧栏接口失败 | 前端静默降级 |

## 11. 测试

- `test_budget.py`：演示配置得到 5650/3954/1695；只改窗口得到 0；`KEEP_TURNS × TURN_TOKENS` 较小时取它；启动自检的两条 warning。
- `test_layers.py`：有效 id 继承；锚点为 NULL 的各种组合；层 2 渲染（答复截短、长工具结果换标识、短工具结果原样、配对结构保留）。
- `test_assemble.py`：消息顺序；不同证据、订单、梗概下 System 文本相同；梗概不在任何 `SystemMessage` 中；参考资料消息只含非空段；`model_ctx` 日志内容。
- `test_history_ctx.py`：`resolve_reference` 收到的历史含梗概行和两层；闲聊轮也打 `history_ctx`。
- `test_finalize_context.py`：不写 `role=tool` 行；State 消息带 `msg-` id；层 1 超预算按水位降级并写库；层 2 超预算触发、运行中跳过；降级失败不影响本轮。
- `test_summarizer.py`：完成时写段落、锚点和投影；编造数字失败；超长失败；超时失败且锚点不动；旧段落进输入但不被改写；投影按 SUM 截取；摘要被 `asyncio.Event` 卡住时本轮先发出 `done`。
- `test_conversations_api.py`：两个接口的排序、预览、标记、过滤和 404。
- 工单、退款：提示消息带 `msg-` id。
- 预检：超 `MAX_USER_INPUT_TOKENS` 返回 422。
- autouse fixture 拦截 `get_summarizer`（`RunnableLambda`），测试结束 `drain` 后台任务。

## 12. 评估（非可单测部分）

- `evals/run_summary_eval.py`（新增，真实上游）：约 10 个标注批次。每条检查：必留事实（订单号、手机号、商品、诉求）都在；没有源文本外的数字串；不含寒暄；长度 30 至 200 字。全部通过才退出码 0。
- `evals/run_token_calibration.py`（新增，真实上游）：见 4.4。
- 回归：`run_multiturn_eval.py`、`run_intent_eval.py`、`run_expand_eval.py`、`run_chat_samples.py`、`scripts/demo6.sh`。证据、订单段、任务段移到用户消息后，确认 ch06 行为不变。

## 13. 验收脚本 `scripts/demo7.sh <日志目录>`

脚本自己按配置启停服务（端口 8000），每种配置一个日志文件。

1. **默认配置**：跑 22 轮脚本对话（业务、知识、闲聊混合，前几轮报订单号和诉求）。每轮收到 `done`；日志中没有 `层1 降级` 和 `summary trigger`。
2. **只改窗口**（`MODEL_CONTEXT_WINDOW=18000`）：启动日志有 `上下文预算不足`。
3. **演示配置**：启动日志 `budget ... history=5650 layer1=3954 layer2=1695`；跑同一脚本；日志依次出现 `层1 降级`、`summary trigger`、`summary done 第1段`；每段 `summary done` 之前已有触发它那一轮的 `turn` 行；等待后台任务完成后问"最开始那个订单后来怎么说"，回复包含首轮的订单号。
4. `grep -c model_ctx`、`grep -c history_ctx` 大于 0，并打印最后一条。

前端在 Chrome 中手工验收：开两个会话，切回旧会话，历史完整，继续聊。

## 14. CLAUDE.md 变更

- 项目状态加 ch07 一行。
- 约束更新："证据渲染进 Agent System Prompt" 改为"证据、订单段、任务段、梗概在用户这句之后的参考资料消息中，System 每轮相同"；新增"`messages` 表不写工具行"、"State 消息 id 为 `msg-<主键>`"、"梗概只追加不重写"、"锚点只增不减"、"`schema_ch07.sql` 是用户 DDL"。
- 常用命令加 `demo7.sh`、`run_summary_eval.py`、`run_token_calibration.py`。

## 15. 已知限制

- 投影只放 SUM 预留内的最新几段，更早的段落不进 prompt。会话非常长时，最早的事实可能看不到。
- 摘要任务在进程内，重启时丢失，靠下一轮重新触发。
- 强制收尾调用的 `TOOL_ROUND_CLOSING` 仍是末尾 System，那一次调用前缀缓存失效。
- 任务指令从 System 移到用户角色消息，模型遵循度可能下降，由回归评估和 `demo6.sh` 把关。
