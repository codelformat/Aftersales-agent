# ch09 可观测性与数据飞轮：设计规格

- 日期：2026-10-09
- 状态：用户已审阅通过（2026-10-09）；计划阶段补充已同步（Langfuse 端口、意图写法、标准化不出品类、评估排除飞轮块、`failures` 字段）
- 分支：`ch09`（从 `main` 拉出，`main` 含 ch08）
- 前置：ch08（`docs/superpowers/specs/2026-10-09-ch08-tool-system-design.md`）。本文只写新增和变化的部分。没有提到的 ch08 行为保持不变。

## 1. 目标与验收标准

**目标：** 给客服系统装上链路追踪，并把答不上的问题变成知识库的补充来源。

1. 接 Langfuse（自部署）：LangGraph 编译时挂一次回调。每条请求的 trace 树展示每个节点的输入输出、模型 prompt、工具调用、检索结果、token 和耗时。
2. 成本控制：意图写进 trace 元数据，按意图统计 token。
3. 三个入口把问题送进 `low_confidence_questions`，各自标好 `source`，同时存召回快照 `retrieved_chunks`：
   - 检索证据置信度低：最简版闸升级为 `evidence_confidence`（Top-1 分、有效证据数、Top-1 与 Top-2 的分差），参数用 ch04 评估集校准。闸的位置不变。
   - 模型自评知识不够答：沿用 ch04/ch05 的自评，接入飞轮。
   - 用户 👎：接到后端，把该轮用户问题落池；快照从该轮检索结果中回捞，没走检索时为 NULL。
4. 飞轮流水线：标准化（FAQ 式问题 + 示例答案）→ 与待审队列查重（命中则累加次数）→ `review_queue`。归并落点写回 `matched_review_id`。人工审核通过后走 ch03 入库流程写入知识库。
5. 自动化评估流水线：复用 ch04 评估集和指标，定期跑，每轮写一行 `eval_runs`，能看出趋势和下滑指标。
6. 前端：待审队列后台页（列表、通过、驳回、详情看原话和召回片段）；评估趋势页。用 Vibe Coding 做。

**验收标准：**

1. Langfuse 中能点开任意一条请求，看到完整链路。
2. 问一个知识库没有的问题，得到兜底话术，该问题出现在待审队列中；在审核页点开这条，能看到用户原话和当时的召回片段。
3. 在审核页点通过后，同一个问题再问能答对。
4. 在聊天页对一条回答点 👎，这条用户问题落池，标准化查重后出现在待审队列中。
5. 能拿出按意图汇总的 token 统计，看得出哪类意图最费 token。
6. 评估流水线至少跑两轮，能看到指标趋势对比。

**本章不做：** 低置信度问题按主题归类的微调分类器；金额统计（只统计 token，用户裁定）；Langfuse 的 Prompt 管理和在线评估。

## 2. 技术栈与依赖

| 项 | 选择 |
|---|---|
| 链路追踪 | Langfuse 自部署（服务器 v4，Python SDK v4）；版本兼容由计划第 1 个任务实测确认 |
| 部署 | 新文件 `docker-compose.langfuse.yml`（用户裁定），6 个容器：langfuse-web、langfuse-worker、Postgres、ClickHouse、Redis、MinIO |
| 新依赖 | `langfuse`（Python SDK） |
| 新配置 | `Settings` 增加 3 个可选字段：`langfuse_public_key`、`langfuse_secret_key`、`langfuse_base_url`；`.env` 增加同名 3 个变量（用户授权） |

端口（计划阶段补充）：本机已有 mewhelp 项目的 Langfuse v3（3000、3030、5432、8123、9000、9090、9191、6380）。用户裁定本项目单独部署 v4，端口为 Web `3100`、worker `127.0.0.1:3130`、Postgres `127.0.0.1:5433`、ClickHouse `127.0.0.1:8124`/`127.0.0.1:9002`、MinIO `9092`/`127.0.0.1:9192`、Redis `127.0.0.1:6381`。`LANGFUSE_BASE_URL=http://127.0.0.1:3100`。compose 文件不引用 `.env` 中的 `DATABASE_URL` 等变量，只读 3 个 `LANGFUSE_*` 作为初始化密钥。

## 3. 数据

### 3.1 表结构

- 用户 DDL `ch09.sql` 用 `git mv` 移到 `db/schema_ch09.sql`，逐字保存（校验 SHA1 不变）。compose 挂载为 `/docker-entrypoint-initdb.d/07-schema-ch09.sql`。
- 新表：`review_queue`、`eval_runs`。`low_confidence_questions` 新增两列：`retrieved_chunks`（JSON）、`matched_review_id`（外键 → `review_queue.id`，`ON DELETE SET NULL`）。
- ORM 只映射，不 `create_all`。测试库同样执行 `schema_ch09.sql`。
- `reset_db.sh` 增加两张表的存在检查，并用 `HEX()` 校验 `review_queue.review_status`、`eval_runs.triggered_by` 的中文枚举。

### 3.2 `retrieved_chunks` 格式

```json
[{"chunk_id": 12, "section_path": "退换货政策/七天无理由", "question": "...", "answer": "...", "score": 0.4312}]
```

- 取重排后、门槛过滤前的 `ranked` 的前 `SNAPSHOT_TOP_N=5` 条，按分数降序。分数保留 4 位小数。
- 没走检索的入口为 NULL。检索结果为空时为 `[]`。

### 3.3 `eval_runs.metrics` 格式

```json
{"strategy": "hybrid_rerank", "recall_at_1": 0.0, "recall_at_3": 0.0, "recall_at_5": 0.0, "recall_at_10": 0.0,
 "mrr": 0.0, "faithfulness": 0.0, "false_refusal": 0.0, "d_refusal": 0.0, "judge_failed": 0,
 "failures": 0, "gate_conf_threshold": 0.0}
```

## 4. 可观测性

### 4.1 挂载（`app/observability.py`，新建）

- `get_langfuse_handler()`：3 个 Langfuse 配置都有值时，返回 `langfuse.langchain.CallbackHandler`；否则返回 None。启动日志打印 `langfuse=on` 或 `langfuse=off`。
- `build_graph` 编译后，如果 handler 不为 None，执行一次 `.with_config({"callbacks": [handler]})`。不在每次请求时传 callbacks。
- `thread_config(conversation_id)` 的 `metadata` 增加：`langfuse_session_id=str(conversation_id)`、`langfuse_user_id`（`GraphContext.user_id`）、`langfuse_trace_name="chat_turn"`。`/chat`、`/chat/resume` 都经过 `thread_config`。
- `lifespan` 退出时调用 Langfuse 客户端的 `flush()` 和 `shutdown()`。
- 测试：没有配置时不挂回调；有配置时用假 handler 验证 `with_config` 和 metadata。测试不连 Langfuse。

### 4.2 链路内容

| 内容 | 来源 |
|---|---|
| 节点 span，输入输出 | LangGraph 节点 = LangChain run，State 更新即输出 |
| 模型 prompt、token、耗时 | 所有 `ChatOpenAI` 调用（解析、意图、扩写、自评、Agent、标准化、查重） |
| 检索结果、置信度信号 | `retrieve`、`retrieve_multi`、`confidence_gate` 的输出（`retrieval`、`gate`） |
| 工具调用 | `agent_model` 输出中的 tool_calls，`agent_tools` 输出中的工具结果 |

嵌入、重排、Milvus 不经过 LangChain，不单独成为 span。它们的结果在节点输出中。飞轮后台任务和评估脚本不挂回调。

### 4.3 意图元数据（用户裁定：先 spike）

- 目标：每条 trace 带元数据 `intent`（8 类意图之一，或 `-` 表示未识别），可在 Langfuse 界面筛选，可通过公开 API 按 `intent` 汇总 token。
- 难点：SDK v4 的 `propagate_attributes` 只作用于作用域内新建的 observation，而意图在 `classify_intent` 之后才确定。
- 计划第 1 个任务做 spike：在本机部署的 Langfuse 上实测写法，确认能在意图确定后把 `intent` 写成 trace 级元数据，并能按它汇总。
- 候选写法（计划阶段补充）：`IntentCallbackHandler` 继承 `CallbackHandler`，在 `classify_intent` 节点的 `on_chain_end` 中找到根 observation，执行 `update(metadata={"intent": ...})`，并在其 OTel span 上设置 `langfuse.trace.metadata.intent`。统计按根 observation 的 metadata 归类。
- 本机 shell 有 `HTTP_PROXY` 没有 `NO_PROXY`：`LANGFUSE_BASE_URL` 为本机地址时，进程把 `127.0.0.1`、`localhost` 加入 `NO_PROXY`。
- spike 走不通时，停下来问用户，不自行换方案。

### 4.4 按意图统计 token（`scripts/intent_cost.py`）

- 参数：`--days N`（默认 7）。
- 调用 `GET /api/public/v2/observations`（按 `traceName=chat_turn` 过滤，字段组 `core,basic,usage,metadata`，游标分页；字段名由 spike 确认），按 `intent` 汇总：轮数、输入 token、输出 token、总 token、每轮平均 token、总 token 占比。按总 token 降序排列，第 1 行标"最费 token"。
- 只统计 token，不统计金额（用户裁定）。
- Langfuse 未配置时，打印说明并以退出码 1 退出。

## 5. 置信度检查

### 5.1 `evidence_confidence`（`app/services/confidence.py`，新建，纯函数）

输入：`ranked`（重排后、门槛过滤前，按分数降序）。输出：

| 信号 | 定义 |
|---|---|
| `top1` | 第 1 名重排分；没有证据时为 0 |
| `effective` | `min(分数 ≥ RERANK_MIN_SCORE 的条数 / GATE_EFFECTIVE_N, 1)` |
| `margin` | `top1 − top2`；只有 1 条时 `top2` 记 0；没有证据时为 0 |
| `conf` | `w1·top1 + w2·effective + w3·margin`，`w1 + w2 + w3 = 1` |

- 常量：`GATE_WEIGHTS`（3 元组）、`GATE_EFFECTIVE_N`、`GATE_CONF_THRESHOLD`，放在 `app/config.py`，注释写明校准报告路径。删除 `GATE_MIN_SCORE`。
- 通过条件：`conf ≥ GATE_CONF_THRESHOLD`。

### 5.2 校准（`evals/run_gate_calibration.py`）

1. 对评估集 300 题各调一次生产检索（`retrieve`，`hybrid_rerank`），记录每题的桶、`ranked` 的分数列表、Top-1 是否相关。结果缓存到 `evals/reports/gate_signals_<时间>.jsonl`。
2. 网格搜索：权重步长 0.1（和为 1），`GATE_EFFECTIVE_N ∈ {2, 3, 4, 5}`，门槛步长 0.01。
3. 规则（沿用 ch04）：可答题（A/B/C/E）保留率 ≥ 95%，在此前提下 D 桶拒答率最高。保留率 = Top-1 相关且 `conf ≥ 门槛` 的比例。D 桶拒答率相同时，取可答题保留率较高者。
4. 5 折交叉验证（按桶分层），报告各折的训练和验证指标。
5. 报告 `evals/reports/gate_calibration_<时间>.md`：最优参数、交叉验证结果、与旧规则（Top-1 ≥ 0.20）的对比。
6. `--from-cache <路径>`：用缓存重跑搜索，不调上游。
7. 参数由 Claude 写进 `app/config.py`，脚本不自动修改代码。

### 5.3 闸（`confidence_gate`，位置不变）

| 出口 | 规则 | 不通过时的 `source` |
|---|---|---|
| knowledge | 先看 `conf`；通过后调自评，`useful=false` 不通过 | `retrieval_low_conf` 或 `self_check` |
| aftersales | 只看 `conf`，不调自评（spec 6.12） | `retrieval_low_conf` |

- `gate` 增加 `confidence`、`signals`（`top1`、`effective`、`margin`）。保留 `top_score`。
- 不通过时：回 `GATE_FALLBACK_REPLY`，不进 Agent，落池（第 6 章）。
- `finalize` 的 `turn` 日志中 `gate=` 改为 `passed/conf/source`。
- `history_recall`、`status_query`、`ticket_request` 直达 business 的规则不变，不过闸。

### 5.4 State 快照

- 新的本轮字段 `retrieval: list[dict] | None`，格式见 3.2。`retrieve` 和 `retrieve_multi` 写入；`start_turn` 重置为 None。
- `confidence_gate` 用 `retrieval` 计算信号，不再从 `gate.top_score` 读分数。

## 6. 问题池入口

### 6.1 统一写入

- `record_low_confidence(conversation_id, raw_question, reason, source, retrieved_chunks=None)`：独立事务，失败只记日志。写入成功后，把新行 id 提交给 `FlywheelRunner`（7.1）。
- `low_confidence.add` 增加 `retrieved_chunks` 参数。

### 6.2 闸和自评

`confidence_gate` 不通过时，传入 State 的 `retrieval` 作为快照。

### 6.3 👎（`POST /api/feedback`，新建 `app/api/feedback.py`）

- 请求体：`{conversation_id: int, message_id: int, rating: "up" | "down", user_id: str}`。会话不属于该用户时返回 404。
- `up`：返回 204，不写库。
- `down`：
  1. 读取 `messages` 中的 `message_id`。如果不存在或不属于该会话，返回 404；如果不是助手回复，返回 422。
  2. 取该会话中 id 小于 `message_id` 的最近一条用户消息作为 `raw_question`。
  3. 回捞快照：遍历 `graph.aget_state_history(thread_config(cid))`，找到 `messages` 最后一条 id 为 `msg-<message_id>` 的最早 checkpoint，取其 `retrieval`。没有找到时为 NULL。
  4. 幂等：`reason="用户反馈未解决（回复 msg-<message_id>）"`。同一会话已有相同 `source=user_feedback` 和 `reason` 的行时，返回 200 `{"duplicate": true}`，不再写入。
  5. 落池，`source=user_feedback`，返回 201。
- 工单和退款的提示消息也是助手消息，允许点 👎。

### 6.4 SSE `done` 事件

- `finalize` 写库后发 `events.emit("saved", {"message_id": reply_row.id})`。API 把它合并进 `done`：`{"finish_reason": "stop", "message_id": <int>}`。
- 中断（`interrupted`）和失败的轮次没有 `message_id`。前端只在有 `message_id` 时显示反馈按钮。
- 工单确认后 `ticket_reply` → `finalize` 的路径同样带 `message_id`。`POST /tickets`、`POST /refunds` 不改。

## 7. 飞轮流水线

### 7.1 `FlywheelRunner`（`app/flywheel/runner.py`）

- 进程内 `asyncio.Queue`，1 个 worker，串行处理（防止并发查重把同一缺口建成两行）。
- `submit(lcq_id)`：放入队列，不阻塞。Runner 未启动时只记日志 `flywheel_runner_off`。
- `lifespan` 启动时 `start()`，退出时 `stop()`（取消 worker，不等队列清空；未处理的行由补跑脚本处理）。
- 测试提供 `drain()`，等待队列处理完。
- `get_runner()` / `set_runner()` 沿用 `SummaryRunner` 的模式。

### 7.2 `process(lcq_id)`（`app/flywheel/pipeline.py`）

1. 读取该行。`matched_review_id` 已有值时跳过。
2. 标准化：调用标准化器（json_mode，关闭思考，超时 `FLYWHEEL_LLM_TIMEOUT_SECONDS`）。输入原话和快照片段（有时），输出 `NormalizedQuestion{normalized_question, suggested_answer}`。`review_queue` 没有品类列，品类由审核人员在通过时选择（计划阶段补充）。
   - `normalized_question`：FAQ 式问法，去掉情绪词、订单号、个人信息，≤ 100 字。
   - `suggested_answer`：只依据快照片段和通用售后常识，开头写"（待核实）"。
3. 召回候选：嵌入 `normalized_question`，与所有 `待审` 行的 `normalized_question` 计算余弦相似度，取 ≥ `REVIEW_DEDUP_MIN_SCORE` 的前 `REVIEW_DEDUP_TOP_K=5` 条。待审行向量按 `(id, normalized_question)` 缓存在进程内。
4. 查重判定：有候选时调用判定器（json_mode，关闭思考），输出 `ReviewDedup{duplicate_of: int | None}`（候选序号）。序号越界视为失败。没有候选时不调用，判为新缺口。
5. 一个事务内：命中时 `occurrence_count + 1`；否则插入 `review_queue`（`待审`，次数 1）。回填 `matched_review_id`。
6. 任何一步失败：记日志 `flywheel_failed lcq=<id> step=<步骤>`，该行保持 NULL。

- `REVIEW_DEDUP_MIN_SCORE` 初值 0.75（同 `DEDUP_STAGING_MIN_SCORE`），用查重样例集检查。
- 日志：`flywheel lcq=<id> review=<id> merged=<bool> candidates=<n>`。

### 7.3 补跑脚本 `scripts/run_flywheel.py`

- 按 id 升序处理所有 `matched_review_id IS NULL` 的行，直接调用 `process()`（不经 Runner）。
- `--limit N`；`--status` 只打印待处理条数和待审队列统计。
- 脚本头部给出 crontab 示例。退出码：有失败行时为 1。

### 7.4 Prompt（非可单测，用标注样例验证）

| 样例集 | 条数 | 通过条件 |
|---|---|---|
| `evals/normalize_samples.jsonl` | 15 | JSON 解析率 100%；品类合法且与标注一致；问题不含情绪词和订单号；示例答案以"（待核实）"开头 |
| `evals/review_dedup_samples.jsonl` | 15 | 同义、近义不同问题、不同品类同问法 3 类，判定全部正确 |

脚本：`evals/run_normalize_eval.py`、`evals/run_review_dedup_eval.py`，全部通过时退出码 0。

## 8. 审核与入库

### 8.1 API（`app/api/review_queue.py`）

| 接口 | 行为 |
|---|---|
| `GET /api/review-queue?status=待审` | 按 `occurrence_count` 降序、`updated_at` 降序；`status` 省略时返回全部 |
| `GET /api/review-queue/{id}` | 该行 + 归并进来的原话列表（`raw_question`、`source`、`reason`、`created_at`、`retrieved_chunks`），按 `created_at` 升序；不存在时 404 |
| `POST /api/review-queue/{id}/approve` | 请求体 `{approved_answer, product_category}`（品类默认"通用"）；见 8.2 |
| `POST /api/review-queue/{id}/reject` | 只允许 `待审` → `驳回`，否则 409 |

### 8.2 通过 → 入库

1. 状态不是 `待审` 时返回 409。`approved_answer` 去空白后为空时返回 422。品类不合法时返回 422。
2. 一个事务内：
   - 插入 `knowledge_chunks`：`category`=品类；`questions`=标准化问题 + 最多 3 条不重复的原话（换行分隔，去掉与标准化问题相同的）；`answer`=`approved_answer`；`section_path`=`飞轮补充/<品类>`（品类为"通用"时为 `飞轮补充`）；`content_type="flywheel"`；`vectorize_status="pending"`。
   - `review_queue` 设为 `通过`，写 `approved_answer`。
3. 提交后同步调用 `vectorize_pending()`。成功返回 200 `{chunk_id, vectorized}`。失败返回 502，审核状态保持 `通过`，`pending` 行由 `build_kb.py` 补齐。
4. 飞轮块的 Milvus `product_category` 由 `product_category_of(section_path)` 得出，与现有规则一致。

### 8.3 与现有约束的衔接

- `build_kb.py --rebuild`：`flywheel` 块与 `mined` 块一样保留，并重新向量化。
- 评估（`exclude_mined=True`）同时排除 `flywheel` 块：`content_type not in ["mined", "flywheel"]`。评估集只标注文档来源（计划阶段补充）。
- 编造台账、挖掘流程不变。

### 8.4 前端（Vibe Coding，由 Codex 实现）

- `/admin/review-queue`：列表（标准化问题、出现次数、示例答案、状态）；通过（可编辑答案和品类，答案默认填示例答案并去掉"（待核实）"，品类默认"通用"）、驳回；每行可展开详情，显示原话和召回片段（片段带分数）。
- `/admin/eval-runs`：各指标折线，标出下滑点。
- 聊天页：👎 调 `POST /api/feedback`，保留 localStorage 记录；只在 `done` 带 `message_id` 时显示反馈按钮。

## 9. 评估流水线（`evals/run_eval_pipeline.py`）

- 复用 `run_rag_eval.py` 的检索段和生成段函数，只跑 `hybrid_rerank`，全量 300 题，默认并发 3。
- 跑完写一行 `eval_runs`：`triggered_by` 由 `--trigger 定时|手动` 指定（默认 `手动`）；`dataset_size` 为实际题数；`metrics` 见 3.3。
- 生成段继续写 `faith_cases`（ch04 行为）。
- 任一段整体失败（检索段没有得分或生成段没有结果）：不写 `eval_runs`，退出码 1。部分题目失败时照常写入，`failures` 记失败条数，退出码 1。
- `--limit N` 只用于调试。
- `--trend [--last N]`：打印最近 N 轮（默认 10），只比较与最新一轮 `dataset_size` 相同的轮次。每个指标显示数值和与上一轮的差值。下降超过 `EVAL_DROP_TOLERANCE=0.02` 标 `↓`；`false_refusal` 上升超过容差标 `↓`。末行列出下滑指标。
- `GET /api/eval-runs?limit=N`（新建 `app/api/eval_runs.py`）：按 `created_at` 升序返回，给趋势页用。
- 脚本头部给出 crontab 示例（`--trigger 定时`）。

## 10. 错误处理

| 场景 | 行为 |
|---|---|
| Langfuse 未配置 | 不挂回调，服务照常运行 |
| Langfuse 不可达 | SDK 后台上报失败，不影响回复 |
| 落池写库失败 | 记日志，不中断本轮 |
| 飞轮上游失败或超时 | 该行 `matched_review_id` 保持 NULL，补跑脚本处理 |
| 审核通过后向量化失败 | 502，`pending` 行留给 `build_kb.py` |
| 评估某段失败 | 不写 `eval_runs`，退出码 1 |

重试规则：聊天模型、嵌入由 SDK 重试；Milvus、重排沿用 `retry_async`；数据库写入不重试。

## 11. 测试（TDD，不访问真实上游）

- `confidence`：空证据、1 条、分数相同、权重边界。
- `confidence_gate`：knowledge 和 aftersales 的拦截与放行；`retrieval` 作为快照传给 `record_low_confidence`。
- `retrieve` / `retrieve_multi` 写 `retrieval`；`start_turn` 重置。
- `POST /api/feedback`：up 204；down 201；重复 200；404；422；从 checkpoint 回捞快照；没走检索时为 NULL。用 `memory_graph`。
- SSE `done` 带 `message_id`；中断时不带。
- 飞轮：新缺口、命中累加、已处理跳过、标准化失败保持 NULL、判定序号越界、Runner 串行（两条相同问题只建一行）。标准化器、判定器用 `RunnableLambda`。
- 测试默认 `FlywheelRunner` 不启动（autouse），需要时用 fixture 启动并 `drain()`。
- 审核 API：通过写入 `flywheel` 块并调用向量化（替换为假函数）；409；404；422。
- 评估：`metrics` 组装、`eval_runs` 写入、趋势计算（纯函数）。
- Langfuse：未配置时不挂回调；有配置时 `with_config` 和 metadata 正确（假 handler）。
- `build_kb --rebuild` 保留 `flywheel` 块。

## 12. 验收

- `scripts/demo9.sh <日志目录>`：前提为 MySQL、Milvus、Langfuse 已启动，已 `build_kb`，端口 8000、8101、8102 空闲。脚本自己启停 2 个 MCP Server 和服务，依次验证验收 2、3、4，再打印验收 1 的 trace 链接和验收 5 的意图统计。
- 验收 6：单独运行两轮 `uv run python evals/run_eval_pipeline.py --trigger 手动`（每轮约 20 分钟），再运行 `--trend`。

## 13. CLAUDE.md 变更

- 项目状态加 ch09 一段；常用命令加 Langfuse compose、`run_gate_calibration.py`、`run_flywheel.py`、`intent_cost.py`、`run_eval_pipeline.py`、两个 Prompt 评估、`demo9.sh`。
- 设计约束加：`schema_ch09.sql` 逐字保存；`evidence_confidence` 参数来源；飞轮单 worker 串行；`flywheel` 块在 `--rebuild` 时保留；测试默认不启动 Runner、不连 Langfuse。
- 环境变量表加 Langfuse 3 个变量。

## 14. 已知限制

- 查重只对比 `待审` 行。已通过或已驳回的缺口再次出现时，会新建一行待审。
- 👎 的快照依赖 checkpoint。`reset_db.sh` 删除 `data/checkpoints.sqlite` 后，旧回复的快照为 NULL。
- 嵌入、重排、Milvus 调用不单独成为 span。
- 进程重启时，队列中未处理的行由补跑脚本处理，不自动恢复。
