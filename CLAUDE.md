# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

电商售后智能客服（Aftersales-agent）。
- ch01：SSE 流式多轮对话 + 售后描述结构化提取。
- ch02：Function Calling 工具链（5 个工具，单轮调用），会话和消息落 MySQL。不做 Agent Loop。
- ch03：知识库。Markdown 文档结构感知切分 + 历史对话挖掘问答对，MySQL `knowledge_chunks` 与 Milvus 集合 `knowledge` 双写；`query_faq` 内部改为 dense 向量检索。
- ch04：混合检索与评估。Query 理解（LLM 改写、型号归一、同义词）→ Milvus dense + BM25 各 Top-50 → RRF → `bge-reranker-v2-m3` Top-10 → 门槛 → 首尾排列；回答带引用编号，自评不足时拒答并写入 `low_confidence_questions`；300 题评估集对比 4 种检索策略并评 Faithfulness，编造个案写入 `faith_cases`；聊天页引用卡片和 👍/👎，台账页 `/admin/faith-cases`。不做指代消解、多轮改写。
- ch05：LangGraph Workflow 骨架 + 主力 ReAct Agent。`start_turn → resolve_reference`（透传）`→ classify_intent`（7 类）`→` 写死分流 4 出口：knowledge（强制检索 → 置信度闸 → Agent 或兜底话术）、business（直接进 Agent）、complaint（安抚话术 + 按钮）、chitchat（固定话术）`→ finalize`（日志记录、写库）。State + `AsyncSqliteSaver`（`data/checkpoints.sqlite`）。「转人工」「建工单」由用户在前端自选，`POST /tickets` 才建单。热身裸循环 `app/agent/bare_loop.py`。
- ch06：正式版分流器。`resolve_reference` 用 LLM 结合历史补全指代并改写（`resolved_input` 给意图和 Agent，`standard_query` 给检索，另出 `order_scoped`、`order_id`），生产链路不再调 `understand()`；意图 8 类四件套（选择题、json_mode `{intent, confidence}`、边界 few-shot、「其他」兜底，降级路默认关闭）；退款退货、售后走 aftersales 出口：`ensure_order`（缺订单号时 `interrupt` 弹订单选择器）→ `fetch_order` → `expand_query`（`{queries}`）→ `retrieve_multi`（多查询召回、只重排一次）→ 置信度闸（只看分数）→ Agent（订单段 + 任务指令，可给「提交退款单」按钮）；`POST /chat/resume` 恢复、`POST /refunds` mock 不落库；聊天页订单卡片、退款单表单、`understood` 灰字。
- ch07：会话上下文管理。三层：层 1 原文、层 2 规则截短（用户原话不动、答复留 60 字、长工具结果换一行标识）、层 3 后台异步分段梗概（`conversation_summaries`，只追加）；`conversations.summary_upto_msg_id`、`layer1_from_msg_id` 两个锚点划边界，只增不减。预算从模型窗口倒推（`app/context/budget.py`，启动自检），层 1 七成、层 2 三成；`finalize` 末尾层 1 超预算按 0.6 水位降级、层 2 超预算起后台摘要。Agent 上下文：固定 System → 层 2 → 层 1 → 用户这句 → 参考资料消息（日期、梗概、订单段、任务段、证据）。回顾本次对话的问题（`history_recall`）直达 Agent。日志 `model_ctx`、`history_ctx`、`context_usage`、`层1 降级`、`summary trigger/done/...` 写 `log/app.log`。聊天页会话侧栏 + `GET /api/conversations`、`GET /api/conversations/{id}/messages`。
- ch08：即插即用的工具系统。内置工具放 `app/tools/builtin/`，启动时扫描登记；MCP 工具每轮从 `config/tools.json` 列出的 Server 动态发现（`MultiServerMCPClient`，Streamable HTTP），与内置工具合成一份本轮工具集。所有调用只走执行引擎 `execute_tool_calls`：查找 → JSON Schema 校验 → 写工具确认 → 超时/重试（只读工具、暂时性故障）→ 分诊（参数不合法、查询落空、真故障）→ 格式化 → 审计（`tool_audit_logs`）。自建两个 MCP Server：物流 8101（`query_logistics`，内置版下线）、售后 8102（`query_warranty`、`query_return_progress`）。用户明确要求建工单时，解析器设 `ticket_request`，Agent 补齐信息后发起 `create_ticket`，`confirm_write` 用 interrupt 推预览卡片，`/chat/resume`（`ticket_confirm`）确认或取消。
- 开发中搁置的问题记录在 `docs/backlog/`。

- 远程仓库：https://github.com/codelformat/Aftersales-agent （默认分支 `main`，GitHub CLI `gh` 已登录）。每章在 `chNN` 分支开发，finish 时提 PR。
- 每章的 spec 在 `docs/superpowers/specs/`，plan 在 `docs/superpowers/plans/`，开发记录在 `dev-notes/chNN.md`。

## 常用命令

```bash
uv sync                                              # 安装依赖（Python 3.12，由 uv 管理）
docker compose up -d --wait                          # 启动 MySQL（宿主端口 3307）和 Milvus（19530，健康检查 9091）
bash scripts/reset_db.sh                             # 删除 MySQL 和 Milvus 数据卷并重建，校验 FAQ 中文编码和 Milvus 健康
uv run python scripts/build_kb.py                    # 离线建库：文档和 faq 表入库（pending），再向量化写 Milvus；已入库的文档跳过
uv run python scripts/build_kb.py --rebuild          # 删除并重建 Milvus 集合和全部文档来源，mined 块保留并重新向量化；--status 只看状态；--check 不一致时退出码 1
uv run python scripts/seed_history.py --date 2026-10-05   # 导入 30 通样例历史对话（默认昨天）
uv run python scripts/mine_qa.py --date 2026-10-05   # 挖掘任务：抽取 → 暂存 → 去重 → 入库 → 向量化（默认昨天；crontab 示例见脚本头）
uv run uvicorn app.main:app --reload --port 8000     # 启动服务；聊天页 http://127.0.0.1:8000/（验收时重定向日志：> /tmp/ch05-server.log 2>&1）
uv run pytest -q                                     # 全量测试（需要 MySQL；连不上时数据库测试直接失败，不跳过）
uv run pytest tests/test_chat_api.py::test_business_tool_round_events -q   # 单个测试
uv run python evals/run_tool_selection_eval.py       # 工具选择样例集（真实上游，只执行第 1 次调用；未达标退出码 1）
uv run python evals/run_extract_eval.py              # 提取 Prompt 样例集（真实上游）
uv run python evals/run_intent_eval.py               # 意图识别样例集（60 条，真实上游；准确率 ≥ 90%、JSON 解析率 100%、「其他」召回 100% 才退出码 0）
uv run python evals/run_multiturn_eval.py            # 多轮指代消解 + 意图 + 回顾标记（7 组 24 轮，真实上游；全部通过才退出码 0）
uv run python -m mcp_servers.logistics              # 物流 MCP Server（端口 8101；--port 可改；MOCK_DELAY_SECONDS 注入延迟）
uv run python -m mcp_servers.aftersales             # 售后 MCP Server（端口 8102；启动时导入 mcp_servers/aftersales/tools/ 下全部模块）
uv run python evals/run_ticket_eval.py               # 工单 Prompt（12 条，真实上游，只做第 1 次调用；全部通过才退出码 0）
uv run python evals/run_summary_eval.py              # 摘要 Prompt（10 条，真实上游；必留事实、无编造数字、不复述、长度全部通过才退出码 0）
uv run python evals/run_token_calibration.py         # 字符/token 口径校准（真实上游，只打印建议值）
uv run python evals/run_expand_eval.py               # Query 扩写（15 条，真实上游；解析、条数、型号数字保留全部通过才退出码 0）
uv run python scripts/bare_agent.py "订单 1001 到哪了"   # 热身裸 Agent 循环（只用 openai SDK，真实上游）
uv run python evals/run_chat_samples.py              # 客服样例，走完整生产链路并写库（真实上游 + MySQL）
uv run python evals/run_rag_eval.py --check          # 检查 300 题评估集（来源键、题号、桶和难度计数）；--list-keys 列出来源键
uv run python evals/run_rag_eval.py --stage retrieval # 四策略检索对比 + 门槛扫描（真实上游 + 生产集合）；--stage generation 评 Faithfulness 并写 faith_cases
uv run python evals/run_rag_eval.py --stage all --gen-strategies all --concurrency 3   # 完整报告，写入 evals/reports/（并发高于 4 会触发上游限流）
uv run python evals/run_faith_judge_eval.py          # 忠实度裁判自检（30 条，准确率 < 90% 退出码 1）
uv run python evals/run_mine_extract_eval.py         # 问答抽取 Prompt 评估（真实上游）
uv run python evals/run_dedup_eval.py                # 去重裁定 Prompt 评估（真实上游）
bash scripts/demo8.sh /tmp/ch08-demo                 # ch08 六项验收（脚本自己启停两个 MCP Server 和服务，端口 8000、8101、8102 须空闲；需 MySQL、Milvus，已 build_kb）
bash scripts/demo7.sh /tmp/ch07-demo                 # ch07 四项验收（脚本自己按 3 种配置启停服务，端口 8000 须空闲；需 MySQL、Milvus，已 build_kb；约 12 分钟）
bash scripts/demo6.sh /tmp/ch06-server.log           # ch06 四项验收（参数为服务日志路径；需先启动服务、MySQL 和 Milvus，已 build_kb）
bash scripts/demo5.sh /tmp/ch05-server.log           # ch05 五项验收（参数为服务日志路径；需先启动服务、MySQL 和 Milvus，已 build_kb）
bash scripts/demo4.sh                                # ch04 四项验收（需先启动服务、MySQL 和 Milvus，已 build_kb）
bash scripts/demo3.sh                                # ch03 验收（需先启动服务、MySQL 和 Milvus）
bash scripts/demo2.sh                                # ch02 三项验收（需先启动服务和 MySQL）
bash scripts/demo.sh                                 # ch01 三项验收
```

重启服务时，先确认旧进程已退出（`pgrep -f "uvicorn app.main:app"` 无输出）再启动，否则请求可能落到旧进程。没有配置 lint 工具。

## 架构

```
api.chat → graph (builder, nodes) → repositories → db
               ↘ tools (registry, toolset, policy, mcp, executor, validation, formatters, audit) → repositories
               ↘ tools.mcp → MultiServerMCPClient → mcp_servers (logistics 8101, aftersales 8102，独立进程)
               ↘ knowledge.retrieval → knowledge.query, knowledge.embeddings, knowledge.milvus, knowledge.rerank
               ↘ services.grounding → repositories.low_confidence
               ↘ llm, prompts, services.history, context
api.chat (/chat/resume) → graph (Command(resume=order_id 或 {"confirmed": bool}))
api.tickets, api.refunds → tools.executor (create_ticket) / mock, graph.aupdate_state, repositories.messages
graph.nodes (turn, intent, aftersales) → services.understanding → llm, knowledge.query
api.knowledge, api.faith_cases → repositories
main.lifespan → graph.builder.open_graph (AsyncSqliteSaver)
evals/run_rag_eval.py → knowledge.retrieval, services.grounding, repositories.faith_cases
scripts/build_kb.py → knowledge.ingest, knowledge.vectorize
scripts/mine_qa.py  → knowledge.mining → knowledge.vectorize
graph.nodes (turn, agent, finalize) → context.layers, context.assemble, context.maintain → context.summarizer → llm, repositories.summaries
api.conversations → repositories.conversations, repositories.messages
```

| 模块 | 职责 |
|---|---|
| `app/config.py` | `Settings` 读 4 个 `CHAT_*`、`DATABASE_URL`、`EMBED_API_KEY`、`EMBED_BASE_URL`、`MILVUS_URI`、`RERANK_API_KEY`、`RERANK_BASE_URL`，以及 ch07 的 6 个带默认值的预算字段；`test_database_url()` 推导测试库；其余为代码常量 |
| `app/db/` | 异步引擎（`get_sessionmaker` / `set_sessionmaker` / `dispose_engine`）与 9 张表的 ORM 映射 |
| `app/repositories/` | 只负责 SQL：会话、消息、工单（指数回退重试主键冲突）、知识块 `knowledge`、挖掘暂存 `staging`、问题池 `low_confidence`、编造台账 `faith_cases`、分段梗概 `summaries` |
| `app/knowledge/` | `milvus`（客户端 get/set、集合定义、经 `retry_async` 重试）、`embeddings`（`OpenAIEmbeddings` 工厂）、`chunking`（Markdown 切分、`knowledge_text`）、`ingest`（文档和 faq 表入库）、`vectorize`（`vectorize_pending`、状态）、`query`（改写、型号归一、同义词，词表 `knowledge/lexicon.json`）、`rerank`（硅基流动 `/rerank`）、`retrieval`（4 种策略、门槛、首尾排列、`source_key`）、`mining`（抽取、去重）、`history_seed` |
| `app/tools/` | `builtin/`（内置工具，`@register` 登记，启动时扫描）、`legacy/`（ch04 基线用的 `query_logistics`）、`registry`（`ToolEntry`、`CH04_CHAT_TOOLS`）、`toolset`（本轮工具集）、`policy`（`config/tools.json`，mtime 变化即重读）、`mcp`（发现与合并）、`validation`（jsonschema → 中文）、`formatters`（MCP 结果挑字段、翻译编码）、`executor`（执行引擎）、`audit`（审计写入，失败只记日志）、`mock_data` |
| `mcp_servers/` | 两个独立进程的 MCP Server（FastMCP 1.x，无状态 Streamable HTTP），不导入 `app`、不连数据库 |
| `app/graph/nodes/confirm.py` | `confirm_write`（interrupt 工单预览）、`ticket_reply`（固定话术） |
| `app/agent/bare_loop.py` | 热身裸循环（openai SDK + 手写 schema，不用框架） |
| `app/graph/` | `state`（`ChatState`、`GraphContext`）、`events`（`emit`、`enter`）、`routing`（分流表、条件边）、`nodes/`（turn、intent、knowledge、agent、replies、finalize）、`builder`（`build_graph`、`get_graph`/`set_graph`、`thread_config`、`open_graph`） |
| `app/api/chat.py` | 预检、会话锁；`graph.astream(stream_mode="custom")` 转 SSE |
| `app/api/tickets.py` | `POST /tickets`：用户点击才建单，写 `messages` 表并 `aupdate_state` |
| `app/services/understanding.py` | 分流前的 Query 理解：`resolve`（指代消解 + 改写 + 订单槽位 + `history_recall`）、`classify`（意图 + 降级路）、`expand`（扩写）；节点和评估脚本共用 |
| `app/graph/nodes/aftersales.py` | 售后子流程：`ensure_order`（interrupt 订单选择器）、`fetch_order`、`expand_query`、`retrieve_multi_evidence` |
| `app/api/refunds.py` | `POST /refunds`：mock 退款单号，提示写 `messages` 表和 State，不落库 |
| `app/services/grounding.py` | 证据合并与全局编号、渲染、自评、入池 |
| `app/services/history.py` | 数据库消息 ↔ LangChain 消息 |
| `app/locks.py` | 按会话 ID 的进程内锁 |
| `app/context/` | `count_tokens`（1.5 字符/token）；`budget`（窗口倒推预算、启动自检）；`layers`（有效 id、按锚点分层、层 2 渲染、指代消解历史文本）；`assemble`（Agent 消息拼装、`model_ctx`）；`maintain`（层 1 降级、摘要触发、`context_usage`）；`summarizer`（`SummaryRunner` 后台任务、`check_summary` 防编造、投影） |
| `app/api/conversations.py` | 会话侧栏的两个只读接口 |

必须保持的设计约束（每条都有测试或实测依据，改动前先读 spec 和 `dev-notes/`）：

- **`db/schema.sql`、`db/schema_ch03.sql`、`db/schema_ch04.sql`、`db/schema_ch07.sql` 是用户 DDL，逐字保存，是表结构唯一来源。** ORM 只映射，不 `create_all`。
- **容器初始化 SQL 必须用 utf8mb4 读取**（`db/mysql-client.cnf` 挂到 `/etc/mysql/conf.d/`），否则中文双重编码。校验存储字节要用 `HEX()`，字符串比较会被 latin1 客户端"还原"而漏检。
- **执行 `.sql` 文件用 `exec_driver_sql`，不用 `text()`。** 异步 ORM 提交后读数据库默认值列前先 `await session.refresh()`。测试引擎用 `NullPool`（pytest 每个异步测试一个事件循环）。
- **不绑定工具的调用（Agent 强制收尾、ch04 评估的第 2 次调用）在末尾追加 `SystemMessage(TOOL_ROUND_CLOSING)`**（不写库）。去掉它，模型会把工具调用标记 `<｜｜DSML｜｜ ...>` 写进正文。`agent_model` 另有标记防线（缓冲前 2 个字符）：命中时抛 `AgentOutputError` → `error` 事件、不写库。
- **`create_ticket` 不重试**（非幂等）；`conversation_id` 由执行器用 `InjectedToolArg` 注入，模型看不到。写工具一律不重试。
- **`db/schema_ch08.sql` 是用户 DDL，逐字保存。** `tool_audit_logs` 不挂外键；状态只有 成功、失败、超时、校验拦下、权限拒绝；查询落空记 成功 + `error_message=查询落空`。
- **所有工具调用只走 `execute_tool_calls`。** 顺序：查找（`closed` → 权限拒绝，`unavailable` → 失败）→ JSON Schema 校验（发给模型的 Schema，不含注入字段）→ 写工具查 `approvals`（只来自 `confirm_write` 和 `POST /tickets`）→ 注入 → 执行 → 分诊 → 格式化 → 审计。写审计失败不影响工具结果。
- **MCP 工具权限只看 `config/tools.json`**：`read` 放行；`write`、`deny`、未列出一律拒绝；不读 Server 的 annotations。本机地址的 MCP Server 用 `trust_env=False` 的 httpx 客户端（本机 shell 有 `HTTP_PROXY`，没有 `NO_PROXY`）。
- **测试默认拦截 MCP 发现（`_block_mcp`），审计默认写内存列表（`audit_log`）**；需要时用 fixture `mcp_servers`（子进程起测试端口 18101、18102）和 `db_audit`。
- **ch05 起 Agent 不绑定 `query_faq`；知识检索只走 `retrieve` 节点**，证据全局编号后渲染进参考资料消息的"知识库证据"段（模型看不到 `chunk_id` 和分数），不伪造 `query_faq` 调用（DeepSeek 思考模式下当前轮自造 tool_call id 返回 400）。`query_faq` 契约保留给 ch04 评估：入参 `question`（≤ 200 字），返回 `{"evidence": [...]}`，不重试，超时 20 秒。
- **Milvus 集合 6 个字段：`id`（= MySQL 主键）、`vector`、`text`（BM25 源文本，`chinese` analyzer）、`sparse`（BM25 Function 输出）、`product_category`、`content_type`，集合级 `Strong` 一致性。** 正文和元数据的权威来源是 MySQL；结构变化后用 `build_kb.py --rebuild` 整体重建集合。
- **`chinese` analyzer 区分大小写、不拆连写型号**：文档只用规范型号写法（`X3 Pro`），查询侧用词表归一（`x3pro` → `X3 Pro`）；问题中出现型号时，品类由型号决定。
- **重排 query 用 `dense_query(standard_query)`**（俗称替换为标准词、不追加同义词）；门槛 `RERANK_MIN_SCORE=0.20` 只挡明显无关的证据，能否回答由自评判断。
- **置信度闸（`confidence_gate`）：证据为空或 Top-1 重排分 < `GATE_MIN_SCORE` → 入池 `retrieval_low_conf`、不调自评；否则调自评，`useful=false` → 入池 `self_check`。不通过时回 `GATE_FALLBACK_REPLY`，不进 Agent。自评失败按通过处理。** 自评要求证据覆盖问题的全部要点，部分可答的问题整句兜底（见 `docs/backlog/`）。 引用编号 `[n]` 为首尾排列后的全局序号；越界编号只记日志。
- **`query_product` 只接受 `^P\d{3,8}$` 商品号**（模型曾把型号当商品号，mock 返回随机品类）。
- **评估生成段必须做真实的第 1 次调用**，沿用模型给出的 tool_call id（DeepSeek 思考模式下自造 id 返回 400）；评估集 `relevant` 为分组格式，Recall 按组计算。
- **入库只写 MySQL `pending`，统一由 `vectorize_pending()` 写 Milvus 并回填 `done`。** 按主键 `upsert`，中断后重跑不产生重复。
- **`OpenAIEmbeddings` 必须设 `check_embedding_ctx_length=False` 和 `model_kwargs={"encoding_format": "float"}`**（硅基流动不接受 token id）。
- **`knowledge_chunks` 有自引用外键，删除行之前先把 `prev_chunk_id`、`next_chunk_id` 置 NULL。**
- **测试不访问真实嵌入、重排、上游模型和生产集合**：autouse fixture 用 `FakeEmbeddings`、`BlockedMilvus`、`BlockedReranker`，并拦截改写器和自评器工厂；需要 Milvus 的测试用 fixture `milvus`（每个测试重建 `knowledge_test`）。抽取器、裁定器、改写器、自评器、裁判用 `RunnableLambda`。
- **提取模型必须关闭思考**；**使用 DeepSeek 时必须设置 `CHAT_THINKING`**；`CHAT_THINKING` 未设置或为空时不发送 `thinking` 字段。
- **DeepSeek 思考模式下，第 2 次调用依赖服务端按 tool_call id 缓存的思考内容**（`ChatOpenAI` 不回传 `reasoning_content`）。自造 tool_call id 放在当前轮会 400。
- **SSE 预检放在 yield 依赖 `prepare_chat_turn` 中，会话锁在其 `finally` 释放**（默认 `scope="request"`）。
- **SSE 事件用 `ServerSentEvent(raw_data=json.dumps(..., ensure_ascii=False))`。**
- **一轮成功（回复非空、无工具标记）才写消息**；工单副作用独立提交，不回滚。
- **ch07 起 `messages` 表只写用户消息、最终回复和工单/退款提示**；工具调用和结果只留 State。State 中有数据库行的消息 id 为 `msg-<主键>`，其他消息的有效 id 继承前一条。升级后旧 checkpoint 没有 `msg-` id，必须执行 `reset_db.sh`（或删除 `data/checkpoints.sqlite`）。
- **Agent System Prompt 每轮相同**：日期、梗概、订单段、任务段、证据放在用户这句之后的一条参考资料 `HumanMessage` 中；梗概不进任何 `SystemMessage`（上游模板会上提合并所有 System，前缀缓存失效）。`TOOL_ROUND_CLOSING` 例外。
- **梗概段只追加不重写**，旧段只作背景；摘要中 4 位以上数字串必须出现在源文本中（`unsupported_number`）。两个锚点只增不减；`maintain` 每轮从数据库重读锚点。
- **预算常量与 `CHARS_PER_TOKEN` 同一口径，一起校准**（`run_token_calibration.py`，spec 4.4）。演示配置必须得 5150/3604/1545（`tests/test_budget.py`；ch08 接入 MCP 后 `SYSTEM_RESERVE_TOKENS` 1900 → 2400，原为 5650/3954/1695）。
- **`history_recall` 为真时 `after_intent` 直接走 business**，不检索、不过置信度闸（spec 5.5，用户裁定）。
- **State `messages` 只由 `finalize` 和 `POST /tickets` 追加；本轮字段由 `start_turn` 重置。** 一轮失败时历史不变，checkpoint 的 `next` 停在失败节点，下一轮新输入从 START 重新开始。
- **节点用 `events.emit(name, data)` 发 SSE 事件，API 用 `stream_mode="custom"`；每轮依赖（会话 ID、日期、聊天模型、执行函数）走 `context=GraphContext(...)`，不进 State。** 每个节点进入时打 `node=<名> conversation=<id>`，`finalize` 打一行 `turn ...` 汇总日志。
- **Agent 绑定本轮工具集（`turn_toolset`）：内置按 `BUILTIN_AGENT_ORDER` 在前，MCP 按策略文件顺序在后。`offer_refund_form` 只在 aftersales 且有订单号时开放，`order_id` 用 `enum` 收窄；`create_ticket` 只在 `ticket_request` 为真时开放，执行前必须经 `confirm_write` 确认（或 `POST /tickets` 点击）。`confirm_write` 在 `interrupt()` 之前不发事件、不写库；确认或取消后由 `ticket_reply` 用固定话术收尾，不再调模型。`offer_human_options` 只写 `actions`，不做业务动作。** 停止条件：无工具调用收敛；`AGENT_MAX_STEPS=4` 或 `AGENT_TOKEN_BUDGET=16000` 触发强制收尾；`GRAPH_RECURSION_LIMIT=25` 兜底。
- **`ensure_order` 在 `interrupt()` 之前不发事件、不写库、不调上游**（恢复时节点从头重新执行）；订单卡片由 API 从 `stream_mode=["custom","updates"]` 的 `__interrupt__` 中取出发 `order_picker`。
- **`/chat/resume` 必须预检待处理的 `order_picker`**：没有待处理 interrupt 时 `Command(resume=)` 静默不执行（设计阶段实测）→ 409 `no_pending_selection`；订单不在卡片列表 → 422 `invalid_order`。有待处理 interrupt 时新消息直接从 START 开始，原 interrupt 作废（实测）。
- **指代消解丢弃不在原文和历史中的订单号**（整词匹配，前后不能是字母或数字）；`order_id` 有值时 `order_scoped` 为真；历史为空时 `resolved_input` 等于原话。
- **aftersales 出口的置信度闸只看分数，不调自评**（spec 6.12，用户裁定）：子流程中缺的信息由 Agent 追问。knowledge 出口仍是分数 + 自评。
- **多查询检索只对 `standard_query` 重排一次**；扩写只在检索侧，库里知识只存一份。生产链路不再调 `understand()`，ch04 评估仍用它。
- **`offer_refund_form` 只在 aftersales 出口且有订单号时绑定**；`order_id` 与 State 不符时按工具失败 `invalid_order` 处理。
- **`reset_db.sh` 必须同时删除 `data/checkpoints.sqlite`**（MySQL 重建后会话 ID 复用，旧 State 会串到新会话）。
- **ch04 评估脚本用 `CH04_CHAT_TOOLS` 和 `chat_prompt` 作基线，不跟随 Agent 变化。**
- **测试 autouse 默认透传指代消解器**（等价 ch05 行为）；需要时用 `use_resolver`、`use_expander`、`use_small_intent`；`use_intent` 的值可为 `(意图, 置信度)`。
- **图测试用 fixture `memory_graph`（`InMemorySaver`）、`use_intent`（替换意图识别器）、`emitted`（直接调节点时收集事件）和 `tests/fakes.rt()`**；`client` fixture 自动使用 `memory_graph`。
- **测试中的聊天模型用 `tests/fakes.py` 的 `ScriptedChatModel`**（支持 `bind_tools`、tool_call chunk、注入异常和等待）；数据库测试用 fixture `db`。

## 模型与环境变量（`.env`，已 gitignore）

`.env` 在项目根目录，从 `~/mewhelp-src/.env` 拷贝而来，不得提交。代码读取配置时用下列变量名，不要硬编码密钥或地址。

| 用途 | 变量 | 上游与协议（2026-10-06 已实测可用） |
|---|---|---|
| 聊天 | `CHAT_BASE_URL` / `CHAT_MODEL` / `CHAT_API_KEY` | OpenAI 兼容 `/chat/completions`；当前为 DeepSeek `deepseek-v4-flash` |
| 思考强度 | `CHAT_THINKING=adaptive` | 映射为请求体 `"thinking": {"type": "adaptive"}`（可选 `disabled`）；思考内容在 `message.reasoning_content`，不在 `content` |
| 嵌入 | `EMBED_API_KEY`（`EMBED_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/embeddings`，模型 `BAAI/bge-m3`，**1024 维**（Milvus collection 维度按此设） |
| 重排 | `RERANK_API_KEY`（`RERANK_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/rerank`，模型 `BAAI/bge-reranker-v2-m3`；Jina/Cohere 形状（`query` + `documents` → `results[].index/relevance_score`），不是 OpenAI 协议 |

- ch01、ch02 只用到聊天这一组和 `DATABASE_URL`；ch03 接入嵌入和 `MILVUS_URI`；ch04 接入重排。
- `DATABASE_URL`：`mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales?charset=utf8mb4`（本项目 Docker Compose 的 MySQL）。
- 嵌入、重排的 base URL 在代码里给默认值 `https://api.siliconflow.cn/v1`，`.env` 中可覆盖。
- `MILVUS_URI`：`http://127.0.0.1:19530`（本项目 Docker Compose 的 Milvus，ch03 经用户同意加入）。
- `.env` 只含上表 6 个变量、`DATABASE_URL` 和 `MILVUS_URI`。需要新配置项时，由用户告知后再加入。
- ch07 的 `MODEL_CONTEXT_WINDOW`、`MAX_OUTPUT_TOKENS`、`MAX_USER_INPUT_TOKENS`、`MAX_AGENT_STEPS`、`TOOL_RESULT_MAX_TOKENS`、`RERANK_TOP_K` 是 `Settings` 字段，有默认值，不写入 `.env`；演示时用环境变量覆盖。
- 验证聊天时 `max_tokens` 别设太小：思考 token 计入其中，太小会 `finish_reason=length` 且 `content` 为空。

## 技术选型（定死，不得更换）

FastAPI、SQLAlchemy、LangChain、LangGraph、Milvus、Langfuse。

实现中发现选型之间有矛盾或走不通，**停下来问用户**，不要自行换库、换方案或绕过。

## 开发流程：Superpowers

- 全程走 Superpowers 流程（brainstorm → spec → plan → 计划评审 → 实现 → code review → finish），相关技能自动触发。
- **非可单测代码的任务**（纯 Prompt、数据类）：把 TDD 那步换成用标注样例或评估集跑一遍验证，其余步骤照走。
- **例外：聊天页面**用 Vibe Coding 方式做：用户描述效果，Claude 转成任务交给 Codex 改，不套 brainstorm、TDD、code review 流程（代码仍由 Codex 写）。

## 分工：Claude 规划，Codex 编码

- **Claude 负责**：理解需求、读代码、plan 阶段的思考与方案设计、拆分任务、审查 Codex 的产出（读 diff、跑测试、核对是否符合 spec/plan）、git 提交与推送。
- **Codex 负责**：所有实际的代码编写与修改。Claude 不直接用 Edit/Write 改业务代码（CLAUDE.md、spec、plan、dev-notes 等文档除外）。
- **调用方式**：模型 `gpt-6.1-sol`，推理强度 `high`（`< /dev/null` 避免 codex 等待 stdin）：

  ```bash
  codex exec -C /Users/harry/Aftersales-agent \
    -m gpt-6.1-sol -c model_reasoning_effort="high" \
    -s workspace-write \
    -c sandbox_workspace_write.network_access=true \
    -c 'sandbox_workspace_write.writable_roots=["/Users/harry/.cache/uv","/Users/harry/.local/share/uv"]' \
    "<任务描述>" < /dev/null
  ```

  后两个 `-c` 是必需的。原因：默认沙箱禁止网络，也不能写 uv 的缓存目录，`uv add` / `uv sync` 会失败。

  不开启 Codex 的 fast 模式（不加 `-c service_tier="priority"`）。用户 2026-10-06 要求开启，2026-10-07 因额度用完改为关闭。

  **Codex 额度用完时，改派 Sonnet 子代理（用户 2026-10-09 要求）。** 每次 `codex exec` 结束后，先查日志中的额度错误（quota、usage limit、429）。如果额度用完：
  1. 告诉用户，引用错误原文。
  2. 用 Agent 工具（`model: "sonnet"`）执行同一份自包含任务描述。
  3. Claude 照常检查它的 diff、跑测试、提交，与 Codex 的产出同样对待。

  Codex 的沙箱不能执行 `docker build`（不能写 `~/.docker/buildx/`）。涉及 docker 的步骤由 Claude 执行。

- **任务描述要自包含**：Codex 看不到对话上下文。写清目标、涉及文件/模块、接口约定、验收标准（要通过的测试或命令）、不许改动的范围，以及相关库的正确 API 用法（来自 Context7 的查询结果）。
- 大任务按 plan 拆成小步依次交给 Codex。每步完成后由 Claude 检查 diff、跑测试；有问题把具体问题和修改要求反馈给 Codex 重做，不自己改代码。
- **检查和测试都通过后**，Claude 再 commit 并 push 到远程仓库。

## 重试：一律指数回退

项目中所有重试都用指数回退（exponential backoff）计算等待时间：第 n 次重试前等待 `min(base × 2^(n−1), max_delay)`，并加随机抖动。不许用固定间隔重试，也不许无等待立即重试。自写的重试统一用一个公共工具函数实现，不在各处手写循环。

- 上游模型调用由 `ChatOpenAI` 的 `max_retries` 交给 OpenAI SDK 重试。SDK 自带指数回退（初始 0.5 秒，上限 8 秒），满足本规则。

## 库/框架用法：先查 Context7

涉及具体库、框架、API 的用法（FastAPI、SQLAlchemy、LangChain、LangGraph、Milvus、Langfuse 等），**一律先用 Context7 MCP 查最新官方文档和接口定义**再动手（包括写给 Codex 的任务描述），不凭记忆写。版本对不上的 API 是返工重灾区。

## 过程留痕：dev-notes

- 在仓库 `dev-notes/chNN.md`（`ch01.md`、`ch02.md`……）里追记开发过程。
- 每完成一个阶段就**当场**补一段：brainstorm 定稿、计划评审通过、每个任务完成、code review 结论、finish。**不许收尾时一次性补记。**
- 每段记四样：
  1. 用户这一步发的关键原话
  2. Claude 的关键产出（spec / plan 路径、评审结论）
  3. 用户拒绝或纠偏了什么
  4. 翻车与返工

## 写作规范：ASD-STE100

技术文档（spec、plan、dev-notes、README、代码注释）和给用户的回复、汇报，都按 ASD-STE100（Simplified Technical English）的规则写。用中文写时，按同样的原则执行：

- **短句**：操作步骤每句不超过 20 词（中文约 30 字），说明性句子不超过 25 词（中文约 40 字）。一句只讲一件事。
- **一段一个主题**：每段不超过 6 句。
- **操作步骤用祈使句**：一步一个动作，按执行顺序编号。条件写在动作前面（"如果 X，执行 Y"）。
- **用主动语态**：写清楚谁做什么。不用"被……"句式，除非动作的主体不明。
- **一词一义**：同一个对象始终用同一个名称，不用同义词替换。用 STE 词表中的批准词义；词表之外的技术名词（库名、API、变量名）原样保留。
- **不用模糊词**：不写"一些""大概""可能会"等，给出具体数字、名称、路径。
- **警告和注意放在相关步骤之前**，并说明原因。

## 完结交付

每个功能完结时给出：功能演示命令、测试结果、dev-notes 路径。
