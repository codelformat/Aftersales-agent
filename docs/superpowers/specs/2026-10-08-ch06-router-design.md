# ch06 正式版分流器：设计规格

- 日期：2026-10-08
- 状态：待用户审阅
- 分支：`ch06`
- 前置：ch05（`docs/superpowers/specs/2026-10-07-ch05-workflow-agent-design.md`）。本文只写新增和变化的部分。没有提到的 ch05 行为保持不变。

## 1. 目标与验收标准

**目标：** 把 ch05 占位的指代消解和意图识别做成正式版，并把退款退货、售后改成确定性子流程。

1. 指代消解：用 LLM 结合对话历史，把带指代的问题补全成独立可读的完整问题。问题已完整时原样透传。
2. Query 改写：口语问法归一成标准问法，和指代消解在同一次调用中完成。
3. Query 扩写：把一句问题泛化成多条侧重点不同的检索查询，强制 JSON 输出 `{"queries": [...]}`。几条一起检索，再去重合并。只在退款退货、售后子流程中扩写。扩写只在检索侧做，库里知识只存一份。
4. 意图识别：LLM Prompt 四件套（枚举选择题、强制 JSON `{intent, confidence}`、边界 few-shot、「其他」兜底类）。
5. 分流精化：退款退货和售后走确定性子流程：拿订单 → 扩写 → 强制检索政策 → 主力 Agent 只判断"这一单能不能办"。
6. 槽位处理：缺订单号时不让模型猜，图暂停并弹出订单选择器，用户点选后回填并继续。退款原因不追问，用户在退款单中从固定类目自选。需求澄清留在主力 Agent 中做。
7. 前端：聊天流中可点选的订单卡片；退款单是一个简单表单，原因用下拉框选。

**验收标准：**

1. 多轮对话用例：用户从问物流切到要退款，再聊回物流。每一轮意图都判对，指代都补全对。
2. 意图输出的 JSON 稳定可解析。拿不准的怪问题落到「其他」。
3. 问"这个能退吗"，能看到先补全指代，再走退款子流程拿订单和政策。
4. 浏览器中不带订单号问退款，聊天页弹出订单选择器。点选后子流程接着走完。

**本章不做：** 微调小模型或 BERT 做意图分类、跨会话记忆、退款单落库、接入真实小模型（需要新环境变量）。

## 2. 技术栈

| 项 | 选择 |
|---|---|
| 暂停与恢复 | `langgraph.types.interrupt(value)` 在节点内暂停，checkpoint 保存；同一 `thread_id` 用 `Command(resume=value)` 恢复，`interrupt()` 返回该值。恢复时节点从头重新执行（Context7 已核对，`langgraph==1.2.14`） |
| 结构化输出 | `ChatOpenAI.with_structured_output(Schema, method="json_mode", include_raw=True)`。Prompt 必须写明 JSON 字段。返回 `{"raw", "parsed", "parsing_error"}`（Context7 已核对） |
| 其他 | 沿用 FastAPI、SQLAlchemy、LangChain、LangGraph、Milvus。无新增组件和依赖 |

## 3. 配置

- `.env` 不新增变量。不新增依赖。
- 新增代码常量（`app/config.py`）：

| 常量 | 值 | 用途 |
|---|---|---|
| `RESOLVE_TIMEOUT_SECONDS` | `8` | 指代消解的等待上限 |
| `RESOLVE_HISTORY_MESSAGES` | `6` | 指代消解读取的历史消息条数 |
| `RESOLVE_MESSAGE_MAX_CHARS` | `200` | 每条历史消息的截断长度 |
| `EXPAND_TIMEOUT_SECONDS` | `8` | 扩写的等待上限 |
| `EXPAND_MAX_QUERIES` | `3` | 扩写返回的查询条数上限（不含原查询） |
| `MULTI_FUSED_LIMIT` | `50` | 多查询合并后送重排的候选上限 |
| `INTENT_SMALL_MODEL` | `None` | 降级路的小模型名。`None` 时关闭降级路 |
| `INTENT_ESCALATE_BELOW` | `0.7` | 小模型置信度低于此值时升级大模型重判 |
| `USER_ORDER_COUNT` | `3` | mock 用户订单列表的条数 |

- 指代消解、意图识别、扩写都用 `build_extract_model`（关闭思考）。降级路开启时，小模型用同一组 `CHAT_BASE_URL` / `CHAT_API_KEY`，只换模型名。

## 4. 架构

```
START → start_turn → resolve_reference → classify_intent ─┬─ knowledge(商品咨询) → retrieve → confidence_gate ─┬→ agent_model
                                                          │                                                   └→ fallback_reply
                                                          ├─ business(物流/订单/其他) → agent_model
                                                          ├─ aftersales(退款退货/售后) ─┬─ order_scoped=true  → ensure_order → fetch_order ─┐
                                                          │                             └─ order_scoped=false ──────────────────────────────┤
                                                          │        expand_query → retrieve_multi → confidence_gate → agent_model | fallback_reply
                                                          ├─ complaint → complaint_reply
                                                          └─ chitchat  → chitchat_reply
agent_model ⇄ agent_tools；所有出口 → finalize → END
```

| 模块 | 变化 |
|---|---|
| `app/graph/nodes/turn.py` | `resolve_reference` 改为 LLM 指代消解 + 改写 |
| `app/graph/nodes/intent.py` | 8 选 1 + confidence；降级路 |
| `app/graph/nodes/aftersales.py`（新） | `ensure_order`、`fetch_order`、`expand_query`、`retrieve_multi_evidence` |
| `app/graph/nodes/knowledge.py` | `retrieve` 改用 State 中的 `QueryPlan`，不再调 `understand()` |
| `app/graph/nodes/agent.py` | aftersales 出口的订单段和任务指令；绑定 `offer_refund_form` |
| `app/graph/routing.py` | 分流表新增 aftersales 出口和「其他」；`after_aftersales` 条件边 |
| `app/graph/control.py` | 新增控制工具 `offer_refund_form` |
| `app/knowledge/retrieval.py` | 新增 `retrieve_multi` |
| `app/tools/mock_data.py` | 新增 `user_orders(user_id, today)` |
| `app/api/chat.py` | 抽出 `stream_graph`；处理 interrupt；新增 `POST /chat/resume` |
| `app/api/refunds.py`（新） | `POST /refunds`（mock，不落库） |
| `app/prompts.py`、`app/schemas.py`、`app/llm.py` | 新 Prompt、Schema 和工厂 |
| `app/web/index.html` | 订单卡片、退款单表单、`understood` 灰字（Vibe Coding） |

## 5. State 与 GraphContext

`ChatState` 新增本轮字段，由 `start_turn` 重置：

| 字段 | 类型 | 写入节点 | 说明 |
|---|---|---|---|
| `standard_query` | `str` | `resolve_reference` | 检索用标准问法 |
| `product_category` | `str \| None` | `resolve_reference` | 品类；型号决定品类 |
| `order_scoped` | `bool` | `resolve_reference` | 问题是否指向用户自己的某个订单 |
| `order_id` | `str \| None` | `resolve_reference`、`ensure_order` | 本轮订单号 |
| `order` | `dict \| None` | `fetch_order` | 订单数据；查询失败为 `None` |
| `queries` | `list[str]` | `expand_query` | 原查询 + 扩写查询 |
| `intent_confidence` | `float \| None` | `classify_intent` | 意图置信度 |

`resolved_input` 的含义改为"补全指代后的问题"，给意图识别和 Agent 用。`standard_query` 只给检索用。

`GraphContext` 新增 `user_id: str`，`ensure_order` 用它列出用户订单。

## 6. 节点

### 6.1 `resolve_reference`

- 输入：State `messages` 最后 `RESOLVE_HISTORY_MESSAGES` 条（每条截断到 `RESOLVE_MESSAGE_MAX_CHARS` 字，只取用户和助手的文字），以及 `user_input`。
- 调用：`get_reference_resolver()`，即 `resolve_prompt | model.with_structured_output(ResolvedQuery, method="json_mode", include_raw=True)`，超时 `RESOLVE_TIMEOUT_SECONDS`。
- 输出 Schema `ResolvedQuery`：`resolved_input: str`（1 至 300 字）、`standard_query: str`（1 至 300 字）、`product_category: PRODUCT_CATEGORIES | None`、`order_scoped: bool`、`order_id: str | None`（`^[A-Za-z0-9-]{1,32}$`）。
- 代码后处理：
  1. `standard_query` 做 `normalize_models`；问题中出现型号时，型号决定 `product_category`（与 `understand()` 相同）。
  2. `order_id` 不是 `user_input` 或所读历史文本的子串时，置为 `None`，日志记 `order_id_dropped`。
- 失败（超时、`parsed is None`、异常）：`resolved_input = standard_query = user_input`（`standard_query` 做型号归一），`product_category` 由型号决定，`order_scoped=False`，`order_id=None`，日志记 warning。

### 6.2 `classify_intent`

- 输入：`resolved_input`，不带历史。
- 输出 Schema `IntentResult`：`intent: Literal[INTENTS]`，`confidence: float`（0 至 1）。`INTENTS` 增加「其他」，共 8 类。
- 降级路：`INTENT_SMALL_MODEL` 不为 `None` 时，先用小模型判；`confidence < INTENT_ESCALATE_BELOW` 或小模型失败时，用大模型重判一次，以第 2 次结果为准。日志记 `intent_model=small|large`。
- 失败：`intent=None`，`intent_confidence=None`，出口 `business`（不变）。
- 节点结束时发 SSE 事件 `understood {resolved_input, intent}`。

### 6.3 分流表

| 意图 | 出口 |
|---|---|
| 商品咨询 | knowledge |
| 物流、订单、其他 | business |
| 退款退货、售后 | aftersales |
| 投诉 | complaint |
| 闲聊 | chitchat |
| 识别失败 | business |

「其他」进 Agent，由 Agent 做需求澄清。aftersales 出口的条件边 `after_aftersales`：`order_scoped` 为真 → `ensure_order`，否则 → `expand_query`。

### 6.4 `ensure_order`

1. 如果 `order_id` 已有值，直接返回。
2. 否则调 `mock_data.user_orders(ctx.user_id, ctx.today)`，得到订单卡片列表。
3. 调 `interrupt({"type": "order_picker", "orders": cards})`。
4. 恢复后，返回值必须是列表中的某个 `order_id`，否则抛 `ValueError`（`/chat/resume` 已预检，这里是防线）。写入 State `order_id`。

注意：恢复时节点从头重新执行。`interrupt()` 之前只做确定性的读操作，不发 SSE 事件，不写库。卡片由 API 层发出（见 7.1）。

卡片字段：`order_id`、`title`（第 1 个商品名，多件时加"等 N 件"）、`total`、`created_at`、`status`。

### 6.5 `fetch_order`

- 代码直接用执行器调用 `query_order(order_id)`（`ctx.execute`），不经模型。
- 成功：`order = outcome.data`。失败：`order = None`，日志记 warning。

### 6.6 `expand_query`

- 输入：`standard_query`，以及 `order` 中的商品名（有订单时）。
- 调用：`get_query_expander()`，`QueryExpansion` Schema：`queries: list[str]`（1 至 4 条，每条 1 至 100 字）。超时 `EXPAND_TIMEOUT_SECONDS`。
- Prompt 规则：每条侧重点不同（是否符合条件 / 流程和费用 / 时限和例外）；保留型号、数字和限定条件；不编造事实。
- 代码后处理：每条做 `normalize_models`；结果 = `[standard_query, *queries[:EXPAND_MAX_QUERIES]]`，按文本去重，保持顺序。
- 失败：`queries = [standard_query]`。

### 6.7 `retrieve_multi`（`app/knowledge/retrieval.py`）

签名：`async def retrieve_multi(queries: list[str], plan: QueryPlan, *, min_score: float = RERANK_MIN_SCORE) -> Retrieval`。

1. 对每条查询并行（`asyncio.gather`）做混合召回：`dense_query` 嵌入 + `bm25_query`，`search_hybrid(leg_limit=RECALL_LEG_LIMIT, limit=FUSED_LIMIT, filter=build_filter(plan.product_category, False))`。
2. 按 `chunk_id` 合并。同一块取最好的 RRF 名次（名次相同取较高 RRF 分）。按名次排序，取前 `MULTI_FUSED_LIMIT` 条。
3. 从 MySQL 读 `done` 行，跳过缺失的 id。
4. 用 `dense_query(plan.standard_query)` 重排一次，取 Top-`EVIDENCE_TOP_N`，过门槛 `min_score`，首尾排列。
5. 返回 `Retrieval(plan, ranked, interleaved)`，与 `retrieve()` 相同。

理由：每条查询的重排分只对自己的查询有意义，混排不可比。对标准问法重排一次，`GATE_MIN_SCORE` 的含义不变。

节点 `retrieve_multi_evidence`（图中名 `retrieve_multi`）把结果写成 `evidence` 和 `gate` 初值，与 `retrieve` 节点相同，之后接 `confidence_gate`。

### 6.8 `retrieve`（knowledge 出口）

用 State 中的 `standard_query` 和 `product_category` 组成 `QueryPlan`，调 `retrieve(question, plan=plan)`。不再调 `understand()`。ch04 评估脚本仍调 `understand()`，基线不变。

### 6.9 主力 Agent（aftersales 出口）

- System Prompt 新增「订单数据」段：订单号、状态、商品（名称、数量、单价）、金额、下单时间。`order_scoped` 为真但 `order is None` 时写"订单数据暂不可用"。`order_scoped` 为假时不出现此段。
- 任务指令（只在 aftersales 出口）：
  - 有订单：判断这一单能否办理用户要的退货、退款或售后。只依据知识库证据和订单数据下结论，引用 `[n]`。条件不全时说明还缺什么，不猜。能退货或退款时调用 `offer_refund_form`。需要维修、补发等时用 `offer_human_options` 给工单选项。
  - 无订单：回答政策问题，引用 `[n]`。
- 绑定工具：`AGENT_TOOLS`；aftersales 出口且 `order_id` 有值时，再加 `offer_refund_form`。`agent_tools` 的允许列表随之变化。

### 6.10 控制工具 `offer_refund_form`

- 参数：`order_id: str`。只写 `actions=[{"type": "refund", "order_id": order_id}]`，不做业务动作。
- `agent_tools` 校验：`order_id` 不等于 State `order_id` 时，按工具失败处理（`failure_outcome(..., "invalid_order")`），不写 `actions`。
- `Action.type` 增加 `"refund"`，新增字段 `order_id`。

### 6.11 `finalize`

`turn` 日志行新增：`resolved=<resolved_input>`、`confidence=<intent_confidence>`、`order=<order_id 或 ->`、`queries=<条数>`。

## 7. 接口

### 7.1 `POST /chat/stream`（变化）

- 把流式主体抽成 `stream_graph(graph, graph_input, config, ctx)`，`/chat/stream` 和 `/chat/resume` 共用。
- `graph.astream(..., stream_mode=["custom", "updates"])`：custom 事件原样转 SSE；updates 中只处理 `__interrupt__`。出现 interrupt 时发 `order_picker {orders}`，再发 `done {"finish_reason": "interrupted"}`，然后结束。具体字段名在 plan 阶段用 Context7 核对。
- 会话有未完成的 interrupt 时，新消息直接以 `{"user_input": ...}` 输入，图从 START 重新开始，原 interrupt 作废。plan 阶段先写测试确认此行为。**如果 LangGraph 不是这样，停下来问用户。**

### 7.2 `POST /chat/resume`（新增，TDD）

- 请求体：`{session_id, user_id, order_id}`。
- 预检（yield 依赖，会话锁在 `finally` 释放）：
  1. 会话不属于该用户 → 404 `conversation_not_found`。
  2. 会话锁被占用 → 409 `session_busy`。
  3. `aget_state` 没有待处理的 `order_picker` interrupt → 409 `no_pending_selection`。
  4. `order_id` 不在该 interrupt 的订单列表中 → 422 `invalid_order`。
- 执行：`stream_graph(graph, Command(resume=order_id), ...)`。SSE 事件集与 `/chat/stream` 相同。不做 token 预算检查（没有新输入）。

### 7.3 `POST /refunds`（新增，TDD）

- 请求体：`{session_id, user_id, order_id, reason, note?}`。`reason` 为 `Literal["七天无理由", "质量问题", "商品与描述不符", "发错货或漏发", "物流损坏", "其他"]`；`note` 可选，不超过 200 字。
- 流程：
  1. 会话不属于该用户 → 404。
  2. 会话锁被占用 → 409 `session_busy`。
  3. mock 生成 `refund_no`（`R` + 日期 + 随机数字），`status="待审核"`。
  4. 写 `REFUND_CREATED_NOTE` 到 `messages` 表和 State（`aupdate_state(..., as_node="finalize")`）。这两步失败只记日志，不回滚。
- 返回：`{refund_no, status}`。

### 7.4 SSE 事件变化

| 事件 | 数据 | 说明 |
|---|---|---|
| `understood`（新） | `{resolved_input, intent}` | `classify_intent` 结束时发 |
| `order_picker`（新） | `{orders: [...]}` | 图暂停时由 API 发 |
| `actions` | 新增 `{"type": "refund", "order_id"}` | |
| `done` | `finish_reason` 新增 `"interrupted"` | |

### 7.5 前端（Vibe Coding，由 Codex 实现）

- `understood`：在助手气泡上方显示灰字"已理解为：……（意图）"。
- `order_picker`：在聊天流中显示订单卡片。点选后所有卡片置灰，调 `/chat/resume`，回复流入新的助手气泡。409 或 422 时提示，卡片保持置灰。
- `actions` 的 `refund`：显示「提交退款单」按钮。点击后弹出表单：订单号只读，原因下拉框（6 个固定类目），备注可选。提交成功后显示退款单号。

## 8. Prompt

所有新 Prompt 写明 JSON 格式和字段取值（`json_mode` 要求）。

### 8.1 指代消解 `RESOLVE_SYSTEM_PROMPT`

规则：
1. 只用历史中出现过的实体补全"它""这个""那单"等指代。
2. 问题已完整，或历史为空时，`resolved_input` 必须与原文一字不差。
3. 话题切换时（例如退款之后问物流），不把前文实体带进来。
4. 不补充历史中没有的信息，不回答问题。
5. `standard_query` 沿用 `QUERY_REWRITE_SYSTEM_PROMPT` 的改写规则（口语归一、保留型号和数字）。
6. `order_scoped`：问题指向用户自己的某个订单或某件已买商品时为 true；只问政策和规则时为 false。
7. `order_id`：只有当前输入或历史中原样出现订单号，且本问题针对该订单时才填，否则为 null。

### 8.2 意图识别 `INTENT_SYSTEM_PROMPT`（四件套）

1. **选择题：** A 物流 / B 订单 / C 商品咨询 / D 退款退货 / E 售后 / F 投诉 / G 闲聊 / H 其他。每项一行定义。输出中文类名。
2. **强制 JSON：** `{"intent": "<8 类之一>", "confidence": <0 到 1 的小数>}`，不输出其他内容。
3. **边界 few-shot（约 8 条）：** 物流 vs 投诉、退款退货 vs 售后、商品咨询 vs 售后、闲聊 vs 其他、乱码 → 其他（低置信度）。
4. **「其他」兜底：** 目的不明或不属于前 7 类时选「其他」，不硬塞业务意图。闲聊只包括问候、感谢、告别和明确与购物无关的话。

### 8.3 扩写 `EXPAND_SYSTEM_PROMPT`

见 6.6。输出 `{"queries": [...]}`。

## 9. 错误处理

| 位置 | 故障 | 处理 |
|---|---|---|
| `resolve_reference` | 超时、JSON 无法解析、校验失败 | 透传（见 6.1），日志 warning |
| `resolve_reference` | `order_id` 不在原文和历史中 | 置为 `None`，日志 `order_id_dropped` |
| `classify_intent` | 失败 | 出口 `business`；降级路中小模型失败时直接用大模型 |
| `fetch_order` | 工具失败 | `order=None`，Agent 提示"暂不可用" |
| `expand_query` | 失败 | `queries=[standard_query]` |
| `retrieve_multi` | Milvus 或重排异常 | 与 `retrieve` 相同：抛出 → `error` 事件，不写库 |
| `confidence_gate` | 不通过 | `GATE_FALLBACK_REPLY`（复用） |
| `offer_refund_form` | `order_id` 不符 | 工具失败 `invalid_order`，不写 `actions` |
| `/chat/resume` | 无待处理选择、订单不在列表 | 409 / 422，不执行图 |

## 10. 测试

TDD，用假模型和 `RunnableLambda`，不访问真实上游：

- `resolve_reference`：透传、补全、编造的 `order_id` 被丢弃、失败降级、型号决定品类。
- `classify_intent`：8 类；降级路升级、不升级、小模型失败。
- 分流表：退款退货、售后 → aftersales；其他 → business；`INTENT_ROUTES` 覆盖全部 `INTENTS`。
- `ensure_order`：有订单号不暂停；无订单号 `interrupt`；有效恢复值继续；订单列表确定。
- `expand_query`：合并、去重、失败降级。
- `retrieve_multi`（fixture `milvus`）：多查询合并、只重排一次、门槛。
- 图测试（`memory_graph`）：aftersales 全链路；`order_scoped=false` 跳过订单节点；interrupt → resume 走完且 `finalize` 只写一次库；有待处理 interrupt 时新消息从 START 开始。
- API：`/chat/stream` 发 `order_picker` + `done(interrupted)`；`/chat/resume` 的 200、404、409、422；`/refunds` 的 200、404、409、422，以及提示写入 `messages` 表和 State。
- `offer_refund_form`：参数校验；`order_id` 不符时失败。
- 不新建 autouse 拦截：新增的三个工厂（`get_reference_resolver`、`get_query_expander`、小模型分类器）加入现有 autouse fixture 的拦截列表。

Prompt 验证（真实上游，替代 TDD）：

| 脚本 | 样例 | 达标线 |
|---|---|---|
| `evals/run_intent_eval.py` | `intent_samples.jsonl` 扩到约 60 条（新增「其他」和边界样例） | 准确率 ≥ 90%，JSON 解析率 100%，「其他」样例全部判对；未达标退出码 1 |
| `evals/run_multiturn_eval.py`（新） | `multiturn_samples.jsonl`，约 6 组对话，每组物流 → 退款 → 物流 | 每轮意图正确，指代用 `must_contain` 或 `unchanged` 判定；全部通过，否则退出码 1 |
| `evals/run_expand_eval.py`（新） | `expand_samples.jsonl`，约 15 条 | 解析率 100%，条数 1 至 4，型号保留 |

## 11. 验收脚本 `scripts/demo6.sh <服务日志路径>`

前置：服务、MySQL、Milvus 已启动，已 `build_kb`。

1. 跑 `run_multiturn_eval.py`，全部通过。
2. 跑 `run_intent_eval.py`，检查解析率和「其他」样例。
3. 发"订单 1001 到哪了"，再发"这个能退吗"。检查 SSE `understood` 补全了指代且 intent=退款退货；服务日志 trace 含 `ensure_order,fetch_order,expand_query,retrieve_multi,confidence_gate`。
4. 发"我要退货"（不带订单号）。检查出现 `order_picker` 和 `done(interrupted)`；用第 1 张卡片的订单号调 `/chat/resume`，回复正常结束。浏览器点选卡片的验证由人工完成。

## 12. 已知限制

1. interrupt 作废后，原问题不进历史。
2. 订单列表是 mock 数据；用户手输的订单号不校验归属（与 ch02 相同）。
3. 降级路的小模型未接入，需要新环境变量。
4. 自评"全有或全无"的问题仍在 backlog（`docs/backlog/2026-10-07-self-check-partial-answer.md`）。
