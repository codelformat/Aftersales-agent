# ch09 可观测性与数据飞轮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目的执行方式（CLAUDE.md）：** Codex 写全部业务代码，Claude 逐任务写自包含的任务描述、检查 diff、跑测试、提交。涉及 docker 的步骤由 Claude 执行。Codex 额度用完时改派 Sonnet 子代理。

**Goal:** 给客服系统接上自部署 Langfuse 链路追踪和按意图的 token 统计，并建成"三入口落池 → 标准化查重 → 人工审核 → 入知识库"的数据飞轮和定期评估流水线。

**Architecture:** 编译图时挂一次 Langfuse 回调（`IntentCallbackHandler`，在 `classify_intent` 结束时把意图写到根 observation 和 trace 元数据）。置信度闸改为 `evidence_confidence` 加权分；检索节点把带分数的 Top-5 写进 State 的 `retrieval`，三个入口落池时带上快照。落池后交给进程内单 worker 的 `FlywheelRunner` 串行做标准化和查重；审核通过后写 `flywheel` 块并同步向量化。评估流水线复用 ch04 评估代码，每轮写一行 `eval_runs`。

**Tech Stack:** FastAPI、SQLAlchemy（asyncmy）、LangChain、LangGraph、Milvus、Langfuse 服务器 v4 + Python SDK `langfuse>=4.17,<5`。

**Spec:** `docs/superpowers/specs/2026-10-09-ch09-observability-flywheel-design.md`

## Global Constraints

- 表结构唯一来源是用户 DDL：`ch09.sql` 用 `git mv` 移到 `db/schema_ch09.sql`，逐字保存（SHA1 不变）。ORM 只映射，不 `create_all`。
- 执行 `.sql` 用 `exec_driver_sql`；校验中文存储字节用 `HEX()`。
- 测试不访问真实上游、生产集合和 Langfuse；`FlywheelRunner` 测试默认不启动。
- 所有重试用指数回退（`app/retry.py` 的 `retry_async`）；数据库写入不重试；聊天模型和嵌入由 SDK 重试。
- 新配置只有 3 个：`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL`（`Settings` 可选字段，用户授权写入 `.env`）。
- 本机 shell 有 `HTTP_PROXY`、没有 `NO_PROXY`：访问本机地址的 HTTP 客户端一律 `trust_env=False`；Langfuse SDK 的 OTLP 导出用 `NO_PROXY` 绕过（任务 3）。
- 本机已有 mewhelp 的 Langfuse v3（端口 3000、3030、5432、8123、9000、9090、9191、6380）。本项目的 Langfuse v4 端口：Web `3100`、worker `127.0.0.1:3130`、Postgres `127.0.0.1:5433`、ClickHouse `127.0.0.1:8124`/`127.0.0.1:9002`、MinIO `9092`/`127.0.0.1:9192`、Redis `127.0.0.1:6381`（用户裁定：本项目单独部署 v4）。
- 提取类模型（标准化器、查重判定器）关闭思考，用 `json_mode`，Prompt 必须写明 JSON 字段。
- SSE 事件用 `ServerSentEvent(raw_data=json.dumps(..., ensure_ascii=False))`。
- 文档和注释按 ASD-STE100 写；代码注释密度与周围代码一致。
- 每个任务完成后，Claude 当场在 `dev-notes/ch09.md` 补一段（四样）。

## Review Focus

1. 对没有 checkpoint 的旧回复（`reset_db.sh` 之后）或工单/退款提示消息点 👎：应当 201 落池、快照为 NULL，不报 500。测试在任务 11。
2. 两条相同问题几乎同时落池：只能建一行待审，第二条累加次数。测试在任务 8（`test_runner_serializes_duplicates`）。
3. 审核页双击"通过"：第二次 409，知识库只多一块。测试在任务 10（`test_approve_twice_conflicts`）。
4. Langfuse 配好了但服务器没启动，或意图打标抛异常：聊天照常完成。测试在任务 3（`test_tag_intent_failure_does_not_break_run`、`test_graph_runs_with_unreachable_langfuse`）。
5. `--limit 5` 调试跑出的一轮混进趋势：`--trend` 只比较与最新一轮 `dataset_size` 相同的轮次。测试在任务 12（`test_trend_only_compares_same_size`）。

---

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `docker-compose.langfuse.yml` | 本项目 Langfuse v4（6 容器） | 1 |
| `db/schema_ch09.sql` | 用户 DDL（`git mv`） | 2 |
| `app/db/models.py` | `ReviewItem`、`EvalRun`，`LowConfidenceQuestion` 两列 | 2 |
| `app/repositories/review_queue.py`、`eval_runs.py`、`low_confidence.py` | SQL | 2 |
| `app/observability.py` | Langfuse 客户端、`IntentCallbackHandler`、关闭 | 3 |
| `scripts/intent_cost.py` | 按意图汇总 token | 4 |
| `app/services/confidence.py` | `evidence_confidence` 纯函数 | 5 |
| `app/graph/nodes/knowledge.py`、`aftersales.py`、`turn.py`、`finalize.py` | 快照、闸、日志 | 6 |
| `evals/gate_calibration.py`、`evals/run_gate_calibration.py` | 校准搜索（纯函数）和脚本 | 7 |
| `app/flywheel/runner.py`、`app/flywheel/pipeline.py`、`scripts/run_flywheel.py` | 飞轮 | 8 |
| `app/prompts.py`、`app/schemas.py`、`app/llm.py` | 标准化、查重 Prompt 和工厂 | 8 |
| `evals/normalize_samples.jsonl`、`evals/review_dedup_samples.jsonl`、两个评估脚本 | Prompt 验证 | 9 |
| `app/api/review_queue.py` | 审核 API、入库 | 10 |
| `app/api/feedback.py`、`app/api/chat.py` | 👎、`done.message_id` | 11 |
| `evals/run_eval_pipeline.py`、`evals/eval_trend.py`、`app/api/eval_runs.py` | 评估流水线和趋势 | 12 |
| `app/web/review_queue.html`、`app/web/eval_runs.html`、`app/web/index.html`、`app/api/web.py` | 前端（Vibe Coding） | 13 |
| `scripts/demo9.sh`、`CLAUDE.md` | 验收和文档 | 14 |

---

### Task 1: 部署 Langfuse v4 并做 spike（意图元数据、查询接口、代理）

**Files:**
- Create: `docker-compose.langfuse.yml`
- Modify: `pyproject.toml`、`uv.lock`（`uv add "langfuse>=4.17,<5"`）
- Modify: `.env`（Claude 写入 3 个变量，不提交）
- Scratch: `<scratchpad>/spike_langfuse.py`（Claude 的一次性探针，不进仓库）

**Interfaces:**
- Produces: 本机 Langfuse v4 在 `http://127.0.0.1:3100`；`.env` 中 3 个 `LANGFUSE_*`；dev-notes 中记录的 spike 结论（意图写法是否可行、v2 observations 接口的字段名）。

- [ ] **Step 1（Codex）: 写 `docker-compose.langfuse.yml`**

  以官方 compose（`https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml`）为底稿，按下列要求改写。不引用 `${DATABASE_URL}` 等变量（`.env` 中的 `DATABASE_URL` 是本项目的 MySQL，会被 compose 插值误用），全部写字面值；只有 3 个初始化密钥从 `.env` 读。

  ```yaml
  # 本项目自部署的 Langfuse v4。与 docker-compose.yml 分开启停：
  #   docker compose -f docker-compose.langfuse.yml up -d --wait
  # 本机已有 mewhelp 的 Langfuse v3，端口全部错开。密码只用于本机开发。
  name: aftersales-langfuse
  services:
    langfuse-worker:
      image: docker.langfuse.com/langfuse/langfuse-worker:4
      restart: always
      depends_on: &langfuse-depends-on
        postgres: {condition: service_healthy}
        minio: {condition: service_healthy}
        redis: {condition: service_healthy}
        clickhouse: {condition: service_healthy}
      ports:
        - 127.0.0.1:3130:3030
      environment: &langfuse-worker-env
        NEXTAUTH_URL: http://localhost:3100
        DATABASE_URL: postgresql://postgres:postgres@postgres:5432/postgres
        SALT: aftersales-local-salt
        ENCRYPTION_KEY: "0000000000000000000000000000000000000000000000000000000000000000"
        TELEMETRY_ENABLED: "false"
        CLICKHOUSE_MIGRATION_URL: clickhouse://clickhouse:9000
        CLICKHOUSE_URL: http://clickhouse:8123
        CLICKHOUSE_USER: clickhouse
        CLICKHOUSE_PASSWORD: clickhouse
        CLICKHOUSE_CLUSTER_ENABLED: "false"
        LANGFUSE_S3_EVENT_UPLOAD_BUCKET: langfuse
        LANGFUSE_S3_EVENT_UPLOAD_REGION: auto
        LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: minio
        LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: miniosecret
        LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: http://minio:9000
        LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true"
        LANGFUSE_S3_EVENT_UPLOAD_PREFIX: events/
        LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: langfuse
        LANGFUSE_S3_MEDIA_UPLOAD_REGION: auto
        LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: minio
        LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: miniosecret
        LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: http://minio:9000
        LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true"
        LANGFUSE_S3_MEDIA_UPLOAD_PREFIX: media/
        REDIS_HOST: redis
        REDIS_PORT: "6379"
        REDIS_AUTH: aftersales-redis
    langfuse-web:
      image: docker.langfuse.com/langfuse/langfuse:4
      restart: always
      depends_on: *langfuse-depends-on
      ports:
        - 3100:3000
      environment:
        <<: *langfuse-worker-env
        NEXTAUTH_SECRET: aftersales-local-nextauth
        LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: http://localhost:9092
        LANGFUSE_S3_MEDIA_UPLOAD_INTERNAL_ENDPOINT: http://minio:9000
        LANGFUSE_INIT_ORG_ID: aftersales-org
        LANGFUSE_INIT_ORG_NAME: Aftersales
        LANGFUSE_INIT_PROJECT_ID: aftersales
        LANGFUSE_INIT_PROJECT_NAME: aftersales-agent
        LANGFUSE_INIT_PROJECT_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY:?在 .env 中设置 LANGFUSE_PUBLIC_KEY}
        LANGFUSE_INIT_PROJECT_SECRET_KEY: ${LANGFUSE_SECRET_KEY:?在 .env 中设置 LANGFUSE_SECRET_KEY}
        LANGFUSE_INIT_USER_EMAIL: admin@aftersales.local
        LANGFUSE_INIT_USER_NAME: admin
        LANGFUSE_INIT_USER_PASSWORD: aftersales-admin
    clickhouse:
      image: docker.io/clickhouse/clickhouse-server:25.12
      restart: always
      user: "101:101"
      environment:
        CLICKHOUSE_DB: default
        CLICKHOUSE_USER: clickhouse
        CLICKHOUSE_PASSWORD: clickhouse
      volumes:
        - aftersales-langfuse-clickhouse-data:/var/lib/clickhouse
        - aftersales-langfuse-clickhouse-logs:/var/log/clickhouse-server
      ports:
        - 127.0.0.1:8124:8123
        - 127.0.0.1:9002:9000
      healthcheck:
        test: wget --no-verbose --tries=1 --spider http://localhost:8123/ping || exit 1
        interval: 5s
        timeout: 5s
        retries: 10
        start_period: 1s
    minio:
      image: cgr.dev/chainguard/minio
      restart: always
      entrypoint: sh
      command: -c 'mkdir -p /data/langfuse && minio server --address ":9000" --console-address ":9001" /data'
      environment:
        MINIO_ROOT_USER: minio
        MINIO_ROOT_PASSWORD: miniosecret
      ports:
        - 9092:9000
        - 127.0.0.1:9192:9001
      volumes:
        - aftersales-langfuse-minio-data:/data
      healthcheck:
        test: ["CMD", "mc", "ready", "local"]
        interval: 1s
        timeout: 5s
        retries: 5
        start_period: 1s
    redis:
      image: docker.io/redis:7
      restart: always
      command: >
        --requirepass aftersales-redis
        --maxmemory-policy noeviction
      ports:
        - 127.0.0.1:6381:6379
      volumes:
        - aftersales-langfuse-redis-data:/data
      healthcheck:
        test: ["CMD", "redis-cli", "ping"]
        interval: 3s
        timeout: 10s
        retries: 10
    postgres:
      image: docker.io/postgres:17
      restart: always
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U postgres"]
        interval: 3s
        timeout: 3s
        retries: 10
      environment:
        POSTGRES_USER: postgres
        POSTGRES_PASSWORD: postgres
        POSTGRES_DB: postgres
        TZ: UTC
        PGTZ: UTC
      ports:
        - 127.0.0.1:5433:5432
      volumes:
        - aftersales-langfuse-postgres-data:/var/lib/postgresql/data
  volumes:
    aftersales-langfuse-postgres-data:
      name: aftersales-langfuse-postgres-data
    aftersales-langfuse-clickhouse-data:
      name: aftersales-langfuse-clickhouse-data
    aftersales-langfuse-clickhouse-logs:
      name: aftersales-langfuse-clickhouse-logs
    aftersales-langfuse-minio-data:
      name: aftersales-langfuse-minio-data
    aftersales-langfuse-redis-data:
      name: aftersales-langfuse-redis-data
  ```

- [ ] **Step 2（Codex）: 加依赖**

  Run: `uv add "langfuse>=4.17,<5"`
  Expected: `pyproject.toml` 出现 `langfuse>=4.17,<5`，`uv.lock` 更新；`uv run python -c "import langfuse, langfuse.langchain; print(langfuse.__version__)"` 打印 4.x。

- [ ] **Step 3（Claude）: 写 `.env` 并启动**

  1. 生成密钥：`pk-lf-` + `openssl rand -hex 16`，`sk-lf-` + `openssl rand -hex 16`。
  2. 在 `.env` 末尾追加 3 行：`LANGFUSE_PUBLIC_KEY=...`、`LANGFUSE_SECRET_KEY=...`、`LANGFUSE_BASE_URL=http://127.0.0.1:3100`。
  3. Run: `docker compose -f docker-compose.langfuse.yml up -d --wait`
  4. Run: `curl --noproxy '*' -s http://127.0.0.1:3100/api/public/health`
     Expected: `{"status":"OK","version":"4.…"}`。

- [ ] **Step 4（Claude）: spike 探针**

  在 scratchpad 写 `spike_langfuse.py`：一个 3 节点的 LangGraph（`start` → `classify_intent`（返回 `{"intent": "物流"}`）→ `answer`（用 `app.llm.build_extract_model(get_settings())` 真实调用一次 "说一个字"）），用任务 3 Step 3 的 `IntentCallbackHandler` 代码原样编译挂载，`thread_config` 带 `langfuse_session_id`、`langfuse_trace_name="chat_turn"`。用 `uv run python <scratchpad>/spike_langfuse.py` 运行，`flush()` 后等 15 秒，再用 `httpx.Client(trust_env=False, auth=(pk, sk))` 检查：

  | 检查项 | 请求 | 通过条件 |
  |---|---|---|
  | a. 代理 | 不设 `NO_PROXY` 运行一次，再设 `NO_PROXY=127.0.0.1,localhost` 运行一次 | 记录哪种情况下 trace 到达 |
  | b. 根 observation 带意图 | `GET /api/public/v2/observations?isRootObservation=true&fields=core,basic,metadata&fromStartTime=<5 分钟前>` | 根 observation 的 metadata 中 `intent == "物流"` |
  | c. trace 级元数据 | Langfuse 界面打开该 trace；以及 `GET /api/public/traces/<id>`（若 v4 仍提供） | 界面 trace 元数据中有 `intent`；可按 `intent` 筛选 |
  | d. token 字段名 | `GET /api/public/v2/observations?traceId=<id>&fields=core,basic,usage` | 记录 generation 上 token 字段的确切名称（例如 `usageDetails.input`/`output`/`total`） |
  | e. 按 trace 名过滤 | `filter=[{"type":"string","column":"traceName","operator":"=","value":"chat_turn"}]` | 只返回该 trace 的 observation |

- [ ] **Step 5（Claude）: 判定**

  - 如果 b 通过（根 observation 带意图），继续后续任务；c 的结论写进 dev-notes（界面可筛选即满足"打进 trace 元数据"）。
  - 如果 b、c 都不通过：**停下来问用户**，不自行换方案（spec 4.3）。
  - 把 a、d、e 的结论（确切字段名、是否需要 `NO_PROXY`）写进 dev-notes "任务 1"段；任务 3、4 按记录的字段名实现。

- [ ] **Step 6: 提交**

  ```bash
  git add docker-compose.langfuse.yml pyproject.toml uv.lock
  git commit -m "chore(ch09): self-hosted Langfuse v4 compose and SDK dependency"
  ```

---

### Task 2: 表结构、ORM、仓储

**Files:**
- Move: `ch09.sql` → `db/schema_ch09.sql`（Claude 用 `git mv`，校验 SHA1）
- Modify: `docker-compose.yml`（挂载 `./db/schema_ch09.sql:/docker-entrypoint-initdb.d/07-schema-ch09.sql:ro`）
- Modify: `scripts/reset_db.sh`、`tests/conftest.py`、`app/db/models.py`
- Create: `app/repositories/review_queue.py`、`app/repositories/eval_runs.py`
- Modify: `app/repositories/low_confidence.py`
- Test: `tests/test_ch09_repositories.py`

**Interfaces:**
- Produces:
  - ORM `ReviewItem`（`review_queue`）：`id`、`normalized_question`、`ai_suggested_answer`、`occurrence_count`、`review_status`、`approved_answer`、`created_at`、`updated_at`。
  - ORM `EvalRun`（`eval_runs`）：`id`、`triggered_by`、`dataset_size`、`metrics`、`created_at`。
  - `LowConfidenceQuestion` 新增 `retrieved_chunks: list[dict] | None`、`matched_review_id: int | None`。
  - `low_confidence.add(s, *, conversation_id, raw_question, source, reason, retrieved_chunks=None) -> LowConfidenceQuestion`
  - `low_confidence.get(s, lcq_id) -> LowConfidenceQuestion | None`
  - `low_confidence.set_matched(s, lcq_id, review_id) -> None`
  - `low_confidence.list_unmatched_ids(s, limit: int | None = None) -> list[int]`（id 升序）
  - `low_confidence.count_unmatched(s) -> int`
  - `low_confidence.list_for_review(s, review_id) -> list[LowConfidenceQuestion]`（`created_at`、`id` 升序）
  - `low_confidence.find_by_reason(s, *, conversation_id, source, reason) -> LowConfidenceQuestion | None`
  - `review_queue.add(s, *, normalized_question, suggested_answer) -> ReviewItem`
  - `review_queue.increment(s, review_id) -> None`
  - `review_queue.list_pending_questions(s) -> list[tuple[int, str]]`（`待审`，id 升序）
  - `review_queue.get(s, review_id, *, for_update=False) -> ReviewItem | None`
  - `review_queue.list_items(s, status: str | None) -> list[ReviewItem]`（`occurrence_count` 降序、`updated_at` 降序、`id` 降序）
  - `review_queue.set_status(s, review_id, status, approved_answer: str | None = None) -> None`
  - `eval_runs.add(s, *, triggered_by, dataset_size, metrics) -> EvalRun`
  - `eval_runs.list_recent(s, limit) -> list[EvalRun]`（返回按 `created_at`、`id` 升序的最近 `limit` 行）

- [ ] **Step 1（Claude）: 移动 DDL 并挂载**

  ```bash
  sha1sum ch09.sql
  git mv ch09.sql db/schema_ch09.sql
  sha1sum db/schema_ch09.sql   # 与上一行相同
  ```

- [ ] **Step 2: 写失败测试 `tests/test_ch09_repositories.py`**

  ```python
  from sqlalchemy import text

  from app.repositories import conversations, eval_runs, low_confidence, review_queue

  SNAP = [{"chunk_id": 1, "section_path": "退换货", "question": "q", "answer": "a", "score": 0.3123}]


  async def _cid(db):
      async with db() as s:
          c = await conversations.create(s, "u1")
          await s.commit()
          return c.id


  async def test_lcq_snapshot_and_match(db):
      cid = await _cid(db)
      async with db() as s:
          row = await low_confidence.add(s, conversation_id=cid, raw_question="保温杯能进洗碗机吗",
                                         source="retrieval_low_conf", reason="r", retrieved_chunks=SNAP)
          item = await review_queue.add(s, normalized_question="保温杯可以用洗碗机清洗吗？",
                                        suggested_answer="（待核实）不建议。")
          await low_confidence.set_matched(s, row.id, item.id)
          await s.commit()
      async with db() as s:
          got = await low_confidence.get(s, row.id)
          assert got.retrieved_chunks == SNAP and got.matched_review_id == item.id
          assert [r.id for r in await low_confidence.list_for_review(s, item.id)] == [row.id]
          assert await low_confidence.list_unmatched_ids(s) == []


  async def test_unmatched_and_find_by_reason(db):
      cid = await _cid(db)
      async with db() as s:
          a = await low_confidence.add(s, conversation_id=cid, raw_question="1", source="self_check", reason="x")
          b = await low_confidence.add(s, conversation_id=cid, raw_question="2", source="user_feedback",
                                       reason="用户反馈未解决（回复 msg-9）")
          await s.commit()
      async with db() as s:
          assert await low_confidence.list_unmatched_ids(s) == [a.id, b.id]
          assert await low_confidence.list_unmatched_ids(s, limit=1) == [a.id]
          assert await low_confidence.count_unmatched(s) == 2
          hit = await low_confidence.find_by_reason(s, conversation_id=cid, source="user_feedback",
                                                    reason="用户反馈未解决（回复 msg-9）")
          assert hit.id == b.id
          assert await low_confidence.find_by_reason(s, conversation_id=cid, source="user_feedback",
                                                     reason="用户反馈未解决（回复 msg-10）") is None


  async def test_review_queue_order_increment_status(db):
      async with db() as s:
          a = await review_queue.add(s, normalized_question="A？", suggested_answer="（待核实）a")
          b = await review_queue.add(s, normalized_question="B？", suggested_answer=None)
          await review_queue.increment(s, b.id)
          await s.commit()
      async with db() as s:
          items = await review_queue.list_items(s, "待审")
          assert [(i.id, i.occurrence_count) for i in items] == [(b.id, 2), (a.id, 1)]
          assert await review_queue.list_pending_questions(s) == [(a.id, "A？"), (b.id, "B？")]
          await review_queue.set_status(s, a.id, "通过", approved_answer="核准")
          await s.commit()
      async with db() as s:
          got = await review_queue.get(s, a.id)
          assert (got.review_status, got.approved_answer) == ("通过", "核准")
          assert [i.id for i in await review_queue.list_items(s, "待审")] == [b.id]
          assert len(await review_queue.list_items(s, None)) == 2


  async def test_eval_runs_recent_ascending(db):
      async with db() as s:
          for i in range(3):
              await eval_runs.add(s, triggered_by="手动", dataset_size=300, metrics={"mrr": i / 10})
          await s.commit()
      async with db() as s:
          rows = await eval_runs.list_recent(s, 2)
      assert [r.metrics["mrr"] for r in rows] == [0.1, 0.2]
      assert rows[0].triggered_by == "手动"


  async def test_chinese_enum_bytes(db):
      sql = ("SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS "
             "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{t}' AND COLUMN_NAME = '{c}'")
      async with db() as s:
          review = (await s.execute(text(sql.format(t="review_queue", c="review_status")))).scalar_one()
          trigger = (await s.execute(text(sql.format(t="eval_runs", c="triggered_by")))).scalar_one()
      assert "E5BE85E5AEA1" in review   # “待审”
      assert "E6898BE58AA8" in trigger  # “手动”
  ```

- [ ] **Step 3: 运行，确认失败**

  Run: `uv run pytest tests/test_ch09_repositories.py -q`
  Expected: FAIL（`ImportError: cannot import name 'ReviewItem'`）。

- [ ] **Step 4: 实现**

  `app/db/models.py`（`LowConfidenceQuestion` 加两列；新增两类，放在 `LowConfidenceQuestion` 之前，因为外键引用 `review_queue`）：

  ```python
  class ReviewItem(Base):
      __tablename__ = "review_queue"

      id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
      normalized_question: Mapped[str] = mapped_column(String(512))
      ai_suggested_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
      occurrence_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
      review_status: Mapped[str] = mapped_column(Enum("待审", "通过", "驳回"), server_default=text("'待审'"))
      approved_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
      created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
      updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


  class EvalRun(Base):
      __tablename__ = "eval_runs"

      id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
      triggered_by: Mapped[str] = mapped_column(Enum("定时", "手动"), server_default=text("'定时'"))
      dataset_size: Mapped[int] = mapped_column(Integer)
      metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
      created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
  ```

  `LowConfidenceQuestion` 增加：

  ```python
      retrieved_chunks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
      matched_review_id: Mapped[int | None] = mapped_column(ID, ForeignKey("review_queue.id"), nullable=True)
  ```

  `app/repositories/review_queue.py`：

  ```python
  from sqlalchemy import select, update
  from sqlalchemy.ext.asyncio import AsyncSession

  from app.db.models import ReviewItem


  async def add(s: AsyncSession, *, normalized_question: str, suggested_answer: str | None) -> ReviewItem:
      row = ReviewItem(normalized_question=normalized_question, ai_suggested_answer=suggested_answer)
      s.add(row)
      await s.flush()
      await s.refresh(row)
      return row


  async def increment(s: AsyncSession, review_id: int) -> None:
      await s.execute(update(ReviewItem).where(ReviewItem.id == review_id)
                      .values(occurrence_count=ReviewItem.occurrence_count + 1))


  async def list_pending_questions(s: AsyncSession) -> list[tuple[int, str]]:
      rows = await s.execute(select(ReviewItem.id, ReviewItem.normalized_question)
                             .where(ReviewItem.review_status == "待审").order_by(ReviewItem.id))
      return [(i, q) for i, q in rows]


  async def get(s: AsyncSession, review_id: int, *, for_update: bool = False) -> ReviewItem | None:
      stmt = select(ReviewItem).where(ReviewItem.id == review_id)
      if for_update:
          stmt = stmt.with_for_update()
      return (await s.execute(stmt)).scalar_one_or_none()


  async def list_items(s: AsyncSession, status: str | None) -> list[ReviewItem]:
      stmt = select(ReviewItem)
      if status is not None:
          stmt = stmt.where(ReviewItem.review_status == status)
      stmt = stmt.order_by(ReviewItem.occurrence_count.desc(), ReviewItem.updated_at.desc(), ReviewItem.id.desc())
      return list((await s.execute(stmt)).scalars())


  async def set_status(s: AsyncSession, review_id: int, status: str, approved_answer: str | None = None) -> None:
      values = {"review_status": status}
      if approved_answer is not None:
          values["approved_answer"] = approved_answer
      await s.execute(update(ReviewItem).where(ReviewItem.id == review_id).values(**values))
  ```

  `app/repositories/eval_runs.py`：

  ```python
  from sqlalchemy import select
  from sqlalchemy.ext.asyncio import AsyncSession

  from app.db.models import EvalRun


  async def add(s: AsyncSession, *, triggered_by: str, dataset_size: int, metrics: dict) -> EvalRun:
      row = EvalRun(triggered_by=triggered_by, dataset_size=dataset_size, metrics=metrics)
      s.add(row)
      await s.flush()
      await s.refresh(row)
      return row


  async def list_recent(s: AsyncSession, limit: int) -> list[EvalRun]:
      rows = await s.execute(select(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id.desc()).limit(limit))
      return list(reversed(list(rows.scalars())))
  ```

  `app/repositories/low_confidence.py`（替换整个文件）：

  ```python
  from sqlalchemy import func, select, update
  from sqlalchemy.ext.asyncio import AsyncSession

  from app.db.models import LowConfidenceQuestion


  async def add(
      s: AsyncSession, *, conversation_id: int | None, raw_question: str, source: str, reason: str | None,
      retrieved_chunks: list[dict] | None = None,
  ) -> LowConfidenceQuestion:
      row = LowConfidenceQuestion(
          conversation_id=conversation_id, raw_question=raw_question, source=source, reason=reason,
          retrieved_chunks=retrieved_chunks,
      )
      s.add(row)
      await s.flush()
      return row


  async def get(s: AsyncSession, lcq_id: int) -> LowConfidenceQuestion | None:
      return await s.get(LowConfidenceQuestion, lcq_id)


  async def set_matched(s: AsyncSession, lcq_id: int, review_id: int) -> None:
      await s.execute(update(LowConfidenceQuestion).where(LowConfidenceQuestion.id == lcq_id)
                      .values(matched_review_id=review_id))


  async def list_unmatched_ids(s: AsyncSession, limit: int | None = None) -> list[int]:
      stmt = (select(LowConfidenceQuestion.id).where(LowConfidenceQuestion.matched_review_id.is_(None))
              .order_by(LowConfidenceQuestion.id))
      if limit is not None:
          stmt = stmt.limit(limit)
      return list((await s.execute(stmt)).scalars())


  async def count_unmatched(s: AsyncSession) -> int:
      return (await s.execute(select(func.count()).select_from(LowConfidenceQuestion)
                              .where(LowConfidenceQuestion.matched_review_id.is_(None)))).scalar_one()


  async def list_for_review(s: AsyncSession, review_id: int) -> list[LowConfidenceQuestion]:
      rows = await s.execute(select(LowConfidenceQuestion).where(LowConfidenceQuestion.matched_review_id == review_id)
                             .order_by(LowConfidenceQuestion.created_at, LowConfidenceQuestion.id))
      return list(rows.scalars())


  async def find_by_reason(
      s: AsyncSession, *, conversation_id: int, source: str, reason: str
  ) -> LowConfidenceQuestion | None:
      rows = await s.execute(select(LowConfidenceQuestion).where(
          LowConfidenceQuestion.conversation_id == conversation_id,
          LowConfidenceQuestion.source == source, LowConfidenceQuestion.reason == reason).limit(1))
      return rows.scalar_one_or_none()
  ```

  `tests/conftest.py`：`_reset_schema` 的 DROP 列表在 `"low_confidence_questions"` 之后加 `"review_queue", "eval_runs"`；执行文件列表末尾加 `"schema_ch09.sql"`。`_clear_runtime_tables` 的 DELETE 列表在 `"low_confidence_questions"` 之后加 `"review_queue", "eval_runs"`。

  `docker-compose.yml` mysql volumes 末尾加 `- ./db/schema_ch09.sql:/docker-entrypoint-initdb.d/07-schema-ch09.sql:ro`。

  `scripts/reset_db.sh`：表检查循环加 `review_queue eval_runs`；在 `tool_audit_logs` 枚举校验之后加：

  ```bash
  review_hex=$(docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='aftersales' AND TABLE_NAME='review_queue' AND COLUMN_NAME='review_status'")
  # 校验“待审”的 utf8mb4 字节，排除双重编码。
  [[ "$review_hex" == *E5BE85E5AEA1* ]] || { echo "review_queue 中文枚举编码错误"; exit 1; }
  trigger_hex=$(docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='aftersales' AND TABLE_NAME='eval_runs' AND COLUMN_NAME='triggered_by'")
  # 校验“手动”的 utf8mb4 字节。
  [[ "$trigger_hex" == *E6898BE58AA8* ]] || { echo "eval_runs 中文枚举编码错误"; exit 1; }
  ```

- [ ] **Step 5: 运行，确认通过**

  Run: `uv run pytest tests/test_ch09_repositories.py -q` → PASS；`uv run pytest -q` → 全部通过。

- [ ] **Step 6（Claude）: 开发库建表**

  Run: `docker exec -i aftersales-mysql mysql --default-character-set=utf8mb4 -uaftersales -paftersales aftersales < db/schema_ch09.sql`
  然后用 Step 4 中两条 `HEX()` 查询在开发库校验。

- [ ] **Step 7: 提交**

  ```bash
  git add db/schema_ch09.sql docker-compose.yml scripts/reset_db.sh tests/conftest.py app/db/models.py \
    app/repositories/review_queue.py app/repositories/eval_runs.py app/repositories/low_confidence.py \
    tests/test_ch09_repositories.py
  git commit -m "feat(ch09): review_queue, eval_runs and pool snapshot columns"
  ```

---

### Task 3: Langfuse 挂载与意图打标

**Files:**
- Create: `app/observability.py`
- Modify: `app/config.py`（`Settings` 3 个字段）、`app/graph/builder.py`、`app/main.py`、`app/api/chat.py`、`tests/conftest.py`
- Test: `tests/test_observability.py`

**Interfaces:**
- Consumes: 任务 1 的 spike 结论（是否需要 `NO_PROXY`）。
- Produces:
  - `observability.TRACE_NAME = "chat_turn"`、`observability.INTENT_NODE = "classify_intent"`
  - `observability.get_langfuse_handler(settings: Settings | None = None) -> IntentCallbackHandler | None`
  - `observability.shutdown_langfuse() -> None`
  - `builder.build_graph(checkpointer, callbacks: list | None = None)`
  - `builder.thread_config(conversation_id: int, user_id: str | None = None) -> dict`（`metadata` 含 `langfuse_session_id`、`langfuse_trace_name`，`user_id` 有值时含 `langfuse_user_id`）

- [ ] **Step 1: 写失败测试 `tests/test_observability.py`**

  ```python
  import uuid
  from types import SimpleNamespace

  import pytest

  from app import observability
  from app.config import Settings
  from app.graph.builder import build_graph, thread_config

  # 模块导入时 autouse fixture _no_langfuse 尚未替换，这里保存原函数。
  REAL_GET_HANDLER = observability.get_langfuse_handler


  def _settings(**kw):
      base = dict(chat_base_url="http://x", chat_model="m", chat_api_key="k", database_url="mysql+asyncmy://a@b/c",
                  embed_api_key="e", milvus_uri="http://m", rerank_api_key="r",
                  langfuse_public_key=None, langfuse_secret_key=None, langfuse_base_url=None)
      return Settings(_env_file=None, **{**base, **kw})


  def test_handler_off_without_config(caplog):
      caplog.set_level("INFO")
      assert REAL_GET_HANDLER(_settings()) is None
      assert "langfuse=off" in caplog.text


  def test_thread_config_metadata():
      cfg = thread_config(7, "u1")
      assert cfg["configurable"] == {"thread_id": "7"}
      assert cfg["metadata"] == {"langfuse_session_id": "7", "langfuse_trace_name": "chat_turn",
                                 "langfuse_user_id": "u1"}
      assert "langfuse_user_id" not in thread_config(7)["metadata"]


  def test_build_graph_attaches_callbacks():
      from langgraph.checkpoint.memory import InMemorySaver
      marker = object()
      g = build_graph(InMemorySaver(), callbacks=[marker])
      assert g.config["callbacks"] == [marker]
      assert "callbacks" not in (build_graph(InMemorySaver()).config or {})


  class FakeRoot:
      def __init__(self):
          self.metadata = None
          self.attrs = {}
          self._otel_span = SimpleNamespace(set_attribute=lambda k, v: self.attrs.__setitem__(k, v))

      def update(self, **kw):
          self.metadata = kw.get("metadata")


  def _run_intent_node(handler, outputs):
      root, node = uuid.uuid4(), uuid.uuid4()
      handler.on_chain_start({}, {}, run_id=root, parent_run_id=None, metadata={}, name="LangGraph")
      fake = FakeRoot()
      handler._runs[root] = fake
      handler.on_chain_start({}, {}, run_id=node, parent_run_id=root,
                             metadata={"langgraph_node": "classify_intent"}, name="classify_intent")
      handler.on_chain_end(outputs, run_id=node, parent_run_id=root)
      return fake


  def test_intent_written_to_root_and_trace():
      handler = observability.IntentCallbackHandler()
      fake = _run_intent_node(handler, {"intent": "物流", "route": "business"})
      assert fake.metadata == {"intent": "物流"}
      assert fake.attrs == {"langfuse.trace.metadata.intent": "物流"}


  def test_missing_intent_written_as_dash():
      fake = _run_intent_node(observability.IntentCallbackHandler(), {"intent": None})
      assert fake.metadata == {"intent": "-"}


  def test_tag_intent_failure_does_not_break_run(monkeypatch, caplog):
      handler = observability.IntentCallbackHandler()

      def boom(*a, **k):
          raise RuntimeError("x")

      monkeypatch.setattr(handler, "_tag_intent", boom)
      _run_intent_node(handler, {"intent": "物流"})
      assert "langfuse_intent_tag_failed" in caplog.text


  def test_no_proxy_added_for_loopback(monkeypatch):
      monkeypatch.delenv("NO_PROXY", raising=False)
      monkeypatch.setenv("no_proxy", "example.com")
      observability._bypass_proxy("http://127.0.0.1:3100")
      import os
      assert set(os.environ["NO_PROXY"].split(",")) >= {"127.0.0.1", "localhost"}
      assert set(os.environ["no_proxy"].split(",")) >= {"example.com", "127.0.0.1", "localhost"}


  async def test_graph_runs_with_unreachable_langfuse(db, client, use_script, use_intent):
      """Langfuse 不可达时，带回调的图照常完成一轮。"""
      from langgraph.checkpoint.memory import InMemorySaver
      from app.graph.builder import set_graph
      handler = REAL_GET_HANDLER(_settings(
          langfuse_public_key="pk-lf-test", langfuse_secret_key="sk-lf-test",
          langfuse_base_url="http://127.0.0.1:9"))
      assert handler is not None
      set_graph(build_graph(InMemorySaver(), callbacks=[handler]))  # client 的 memory_graph 在结束时置回 None
      use_script()
      use_intent("闲聊")
      try:
          r = await client.post("/chat/stream", json={"user_id": "u1", "message": "你好"})
          assert "event: done" in r.text and "upstream_error" not in r.text
      finally:
          observability.shutdown_langfuse()
  ```

- [ ] **Step 2: 运行，确认失败**

  Run: `uv run pytest tests/test_observability.py -q`
  Expected: FAIL（`ImportError: cannot import name 'observability'`）。

- [ ] **Step 3: 实现 `app/observability.py`**

  ```python
  """Langfuse 链路追踪。编译图时挂一次回调；未配置时不挂。"""

  import logging
  import os
  from urllib.parse import urlparse
  from uuid import UUID

  from langfuse import Langfuse
  from langfuse.langchain import CallbackHandler

  from app.config import Settings, get_settings

  logger = logging.getLogger(__name__)
  TRACE_NAME = "chat_turn"
  INTENT_NODE = "classify_intent"
  INTENT_ATTRIBUTE = "langfuse.trace.metadata.intent"
  _LOOPBACK = ("127.0.0.1", "localhost")
  _client: Langfuse | None = None


  class IntentCallbackHandler(CallbackHandler):
      """classify_intent 结束时，把意图写到根 observation 的元数据和 trace 元数据。"""

      def __init__(self, **kwargs):
          super().__init__(**kwargs)
          self._intent_runs: set[UUID] = set()

      def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None,
                         **kwargs):
          if (parent_run_id is not None and kwargs.get("name") == INTENT_NODE
                  and (metadata or {}).get("langgraph_node") == INTENT_NODE):
              self._intent_runs.add(run_id)
          return super().on_chain_start(serialized, inputs, run_id=run_id, parent_run_id=parent_run_id,
                                        tags=tags, metadata=metadata, **kwargs)

      def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
          if run_id in self._intent_runs:
              self._intent_runs.discard(run_id)
              try:
                  self._tag_intent(run_id, outputs)
              except Exception:
                  # 打标失败只影响统计，不影响本轮。
                  logger.exception("langfuse_intent_tag_failed")
          return super().on_chain_end(outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs)

      def _tag_intent(self, run_id: UUID, outputs) -> None:
          intent = outputs.get("intent") if isinstance(outputs, dict) else None
          state = self._run_states.get(run_id)
          root = self._runs.get(state.root_run_id) if state is not None else None
          if root is None:
              return
          value = intent or "-"
          root.update(metadata={"intent": value})
          root._otel_span.set_attribute(INTENT_ATTRIBUTE, value)


  def _bypass_proxy(base_url: str) -> None:
      """本机 shell 有 HTTP_PROXY 没有 NO_PROXY。OTLP 导出访问本机 Langfuse 时绕过代理。"""
      if urlparse(base_url).hostname not in _LOOPBACK:
          return
      for var in ("NO_PROXY", "no_proxy"):
          hosts = [h for h in os.environ.get(var, "").split(",") if h]
          os.environ[var] = ",".join(dict.fromkeys([*hosts, *_LOOPBACK]))


  def get_langfuse_handler(settings: Settings | None = None) -> IntentCallbackHandler | None:
      global _client
      s = settings or get_settings()
      secret = s.langfuse_secret_key.get_secret_value() if s.langfuse_secret_key else None
      if not (s.langfuse_public_key and secret and s.langfuse_base_url):
          logger.info("langfuse=off")
          return None
      _bypass_proxy(s.langfuse_base_url)
      _client = Langfuse(public_key=s.langfuse_public_key, secret_key=secret, base_url=s.langfuse_base_url)
      logger.info("langfuse=on base_url=%s", s.langfuse_base_url)
      return IntentCallbackHandler(public_key=s.langfuse_public_key)


  def shutdown_langfuse() -> None:
      global _client
      if _client is None:
          return
      try:
          _client.flush()
          _client.shutdown()
      except Exception:
          logger.exception("langfuse_shutdown_failed")
      finally:
          _client = None
  ```

  如果任务 1 的 spike 记录了与上面不同的私有属性名（`_run_states`、`_runs`、`root_run_id`、`_otel_span`），按记录修改 `_tag_intent` 和测试中的 `handler._runs`。

  `app/config.py` 的 `Settings` 末尾加：

  ```python
      langfuse_public_key: str | None = None
      langfuse_secret_key: SecretStr | None = None
      langfuse_base_url: str | None = None
  ```

  `app/graph/builder.py`：

  ```python
  from app.observability import TRACE_NAME, get_langfuse_handler

  def build_graph(checkpointer, callbacks: list | None = None):
      ...  # 原有节点和边不变
      compiled = g.compile(checkpointer=checkpointer)
      # 编译时挂一次回调，不在每次请求时传入。
      return compiled.with_config({"callbacks": callbacks}) if callbacks else compiled


  def thread_config(conversation_id: int, user_id: str | None = None) -> dict:
      metadata = {"langfuse_session_id": str(conversation_id), "langfuse_trace_name": TRACE_NAME}
      if user_id:
          metadata["langfuse_user_id"] = user_id
      return {"configurable": {"thread_id": str(conversation_id)}, "recursion_limit": GRAPH_RECURSION_LIMIT,
              "metadata": metadata}


  @asynccontextmanager
  async def open_graph(path: str = CHECKPOINT_DB_PATH):
      Path(path).parent.mkdir(parents=True, exist_ok=True)
      handler = get_langfuse_handler()
      async with AsyncSqliteSaver.from_conn_string(path) as checkpointer:
          graph = build_graph(checkpointer, [handler] if handler else None)
          ...  # 其余不变
  ```

  `app/api/chat.py` 的 `stream_graph` 中 `graph.astream(graph_input, thread_config(turn.conversation_id, turn.user_id), ...)`。

  `app/main.py` 的 `lifespan` 的 `finally` 中最先调用 `shutdown_langfuse()`（`from app.observability import shutdown_langfuse`）。

  `tests/conftest.py` 加 autouse fixture：

  ```python
  @pytest.fixture(autouse=True)
  def _no_langfuse(monkeypatch):
      """测试不连 Langfuse。需要时在测试中直接构造 handler。"""
      from app import observability
      monkeypatch.setattr(observability, "get_langfuse_handler", lambda settings=None: None)
  ```

- [ ] **Step 4: 运行，确认通过**

  Run: `uv run pytest tests/test_observability.py -q` → PASS；`uv run pytest -q` → 全部通过。

- [ ] **Step 5（Claude）: 实测**

  1. 启动服务（`uv run uvicorn app.main:app --port 8000 > /tmp/ch09-server.log 2>&1`），日志有 `langfuse=on`。
  2. 在聊天页问"X3 Pro 续航多久"和"订单 1001 到哪了"。
  3. 在 `http://127.0.0.1:3100` 打开两条 trace：节点 span、generation 的 prompt 和 token、`retrieve` 输出的证据、`agent_tools` 输出都能看到；trace 元数据有 `intent`；同一会话归入同一 Session。
  4. 截图结论写进 dev-notes。

- [ ] **Step 6: 提交**

  ```bash
  git add app/observability.py app/config.py app/graph/builder.py app/main.py app/api/chat.py \
    tests/conftest.py tests/test_observability.py
  git commit -m "feat(ch09): Langfuse callback attached at compile time with intent metadata"
  ```

---

### Task 4: 按意图统计 token（`scripts/intent_cost.py`）

**Files:**
- Create: `scripts/intent_cost.py`
- Test: `tests/test_intent_cost.py`

**Interfaces:**
- Consumes: 任务 1 记录的 v2 observations 字段名；`observability.TRACE_NAME`。
- Produces:
  - `parse_observation(raw: dict) -> Obs`（`Obs(trace_id: str, is_root: bool, intent: str | None, input: int, output: int, total: int)`）
  - `aggregate(observations: list[Obs]) -> list[IntentCost]`（按 `total_tokens` 降序）
  - `render(rows: list[IntentCost], days: int) -> str`

- [ ] **Step 1: 写失败测试 `tests/test_intent_cost.py`**

  ```python
  from scripts.intent_cost import IntentCost, Obs, aggregate, parse_observation, render


  def test_parse_observation_root_and_generation():
      root = parse_observation({"traceId": "t1", "parentObservationId": None, "metadata": {"intent": "物流"}})
      gen = parse_observation({"traceId": "t1", "parentObservationId": "p",
                               "usageDetails": {"input": 100, "output": 20, "total": 120}})
      assert root == Obs("t1", True, "物流", 0, 0, 0)
      assert gen == Obs("t1", False, None, 100, 20, 120)


  def test_aggregate_groups_by_trace_intent():
      obs = [
          Obs("t1", True, "物流", 0, 0, 0), Obs("t1", False, None, 100, 20, 120), Obs("t1", False, None, 50, 5, 55),
          Obs("t2", True, "商品咨询", 0, 0, 0), Obs("t2", False, None, 900, 100, 1000),
          Obs("t3", True, "物流", 0, 0, 0), Obs("t3", False, None, 10, 0, 10),
          Obs("t4", False, None, 7, 1, 8),  # 没有根 observation：记为 "-"
      ]
      rows = aggregate(obs)
      assert [(r.intent, r.turns, r.total_tokens) for r in rows] == [
          ("商品咨询", 1, 1000), ("物流", 2, 185), ("-", 1, 8)]
      assert rows[1].avg_tokens == 92.5
      assert abs(sum(r.share for r in rows) - 1.0) < 1e-9


  def test_render_marks_top():
      rows = aggregate([Obs("t1", True, "物流", 0, 0, 0), Obs("t1", False, None, 1, 1, 2)])
      text = render(rows, 7)
      assert "最费 token" in text and "物流" in text
  ```

- [ ] **Step 2: 运行，确认失败**

  Run: `uv run pytest tests/test_intent_cost.py -q` → FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现 `scripts/intent_cost.py`**

  ```python
  """按意图汇总 Langfuse 中 chat_turn 的 token 用量。只统计 token，不统计金额。

  用法：uv run python scripts/intent_cost.py --days 7
  """

  import argparse
  import json
  import sys
  from collections import defaultdict
  from dataclasses import dataclass
  from datetime import datetime, timedelta, timezone
  from pathlib import Path

  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

  import httpx

  from app.config import get_settings
  from app.observability import TRACE_NAME

  PAGE_LIMIT = 1000
  NO_INTENT = "-"


  @dataclass(frozen=True)
  class Obs:
      trace_id: str
      is_root: bool
      intent: str | None
      input: int
      output: int
      total: int


  @dataclass(frozen=True)
  class IntentCost:
      intent: str
      turns: int
      input_tokens: int
      output_tokens: int
      total_tokens: int
      avg_tokens: float
      share: float


  def parse_observation(raw: dict) -> Obs:
      usage = raw.get("usageDetails") or {}
      metadata = raw.get("metadata") or {}
      return Obs(raw["traceId"], raw.get("parentObservationId") is None,
                 metadata.get("intent") if isinstance(metadata, dict) else None,
                 int(usage.get("input", 0)), int(usage.get("output", 0)), int(usage.get("total", 0)))


  def aggregate(observations: list[Obs]) -> list[IntentCost]:
      intent_of: dict[str, str] = {}
      tokens: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
      for o in observations:
          if o.is_root and o.intent:
              intent_of[o.trace_id] = o.intent
          t = tokens[o.trace_id]
          t[0] += o.input
          t[1] += o.output
          t[2] += o.total
      groups: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0])
      for trace_id, (i, out, tot) in tokens.items():
          g = groups[intent_of.get(trace_id, NO_INTENT)]
          g[0] += 1
          g[1] += i
          g[2] += out
          g[3] += tot
      grand = sum(g[3] for g in groups.values()) or 1
      rows = [IntentCost(k, g[0], g[1], g[2], g[3], g[3] / g[0], g[3] / grand) for k, g in groups.items()]
      return sorted(rows, key=lambda r: (-r.total_tokens, r.intent))


  def render(rows: list[IntentCost], days: int) -> str:
      lines = [f"# 最近 {days} 天按意图的 token 用量（trace 名 {TRACE_NAME}）", "",
               "| 意图 | 轮数 | 输入 token | 输出 token | 总 token | 每轮平均 | 占比 |", "|---|---|---|---|---|---|---|"]
      for n, r in enumerate(rows):
          mark = "（最费 token）" if n == 0 else ""
          lines.append(f"| {r.intent}{mark} | {r.turns} | {r.input_tokens} | {r.output_tokens} | {r.total_tokens} "
                       f"| {r.avg_tokens:.0f} | {r.share:.1%} |")
      return "\n".join(lines)


  def fetch(base_url: str, auth: tuple[str, str], days: int) -> list[Obs]:
      now = datetime.now(timezone.utc)
      params = {"fromStartTime": (now - timedelta(days=days)).isoformat(), "toStartTime": now.isoformat(),
                "limit": PAGE_LIMIT, "fields": "core,basic,usage,metadata",
                "filter": json.dumps([{"type": "string", "column": "traceName", "operator": "=", "value": TRACE_NAME}])}
      out: list[Obs] = []
      # 本机 shell 有 HTTP_PROXY，访问本机 Langfuse 不走代理。
      with httpx.Client(trust_env=False, auth=auth, timeout=30) as client:
          cursor = None
          while True:
              page = client.get(f"{base_url}/api/public/v2/observations",
                                params={**params, **({"cursor": cursor} if cursor else {})})
              page.raise_for_status()
              body = page.json()
              out += [parse_observation(o) for o in body.get("data", [])]
              cursor = (body.get("meta") or {}).get("cursor")
              if not cursor:
                  return out


  def main() -> int:
      parser = argparse.ArgumentParser(description="按意图汇总 token 用量（读 Langfuse）。")
      parser.add_argument("--days", type=int, default=7)
      args = parser.parse_args()
      s = get_settings()
      if not (s.langfuse_public_key and s.langfuse_secret_key and s.langfuse_base_url):
          print("未配置 Langfuse（LANGFUSE_PUBLIC_KEY、LANGFUSE_SECRET_KEY、LANGFUSE_BASE_URL）")
          return 1
      rows = aggregate(fetch(s.langfuse_base_url, (s.langfuse_public_key, s.langfuse_secret_key.get_secret_value()),
                             args.days))
      print(render(rows, args.days) if rows else "时间窗内没有 chat_turn 数据")
      return 0


  if __name__ == "__main__":
      sys.exit(main())
  ```

  如果任务 1 记录的字段名不同（例如 token 在 `inputUsage`/`outputUsage`/`totalUsage`，或根判定字段不是 `parentObservationId`），只改 `parse_observation` 和测试中的原始字典。

- [ ] **Step 4: 运行测试** → PASS。

- [ ] **Step 5（Claude）: 实测** `uv run python scripts/intent_cost.py --days 1`，输出表格，第 1 行带"（最费 token）"。

- [ ] **Step 6: 提交**

  ```bash
  git add scripts/intent_cost.py tests/test_intent_cost.py
  git commit -m "feat(ch09): intent_cost script aggregates token usage by intent"
  ```

---

### Task 5: `evidence_confidence` 纯函数

**Files:**
- Create: `app/services/confidence.py`
- Modify: `app/config.py`
- Test: `tests/test_confidence.py`

**Interfaces:**
- Produces:
  - 常量：`SNAPSHOT_TOP_N = 5`、`GATE_WEIGHTS: tuple[float, float, float] = (1.0, 0.0, 0.0)`、`GATE_EFFECTIVE_N = 3`、`GATE_CONF_THRESHOLD = 0.20`（校准前的初值等价于旧规则 Top-1 ≥ 0.20；任务 7 替换）。删除 `GATE_MIN_SCORE`。
  - `Confidence(top1: float, effective: float, margin: float, score: float)`，`to_dict() -> dict`（4 位小数）
  - `evidence_confidence(scores: Sequence[float], *, weights=GATE_WEIGHTS, effective_n=GATE_EFFECTIVE_N, min_score=RERANK_MIN_SCORE) -> Confidence`
  - `gate_passes(scores: Sequence[float], *, weights=..., effective_n=..., threshold=GATE_CONF_THRESHOLD, min_score=...) -> tuple[bool, Confidence]`（没有 ≥ `min_score` 的分数时不通过）

- [ ] **Step 1: 写失败测试 `tests/test_confidence.py`**

  ```python
  import pytest

  from app.services.confidence import Confidence, evidence_confidence, gate_passes


  def test_empty():
      c = evidence_confidence([], weights=(0.5, 0.3, 0.2), effective_n=3, min_score=0.2)
      assert c == Confidence(0.0, 0.0, 0.0, 0.0)


  def test_single():
      c = evidence_confidence([0.6], weights=(0.5, 0.3, 0.2), effective_n=3, min_score=0.2)
      assert (c.top1, c.margin) == (0.6, 0.6)
      assert c.effective == pytest.approx(1 / 3)
      assert c.score == pytest.approx(0.5 * 0.6 + 0.3 / 3 + 0.2 * 0.6)


  def test_effective_caps_and_ignores_low():
      c = evidence_confidence([0.9, 0.5, 0.3, 0.25, 0.1], weights=(0, 1, 0), effective_n=3, min_score=0.2)
      assert c.effective == 1.0 and c.score == 1.0
      c2 = evidence_confidence([0.9, 0.1], weights=(0, 1, 0), effective_n=2, min_score=0.2)
      assert c2.effective == 0.5


  def test_ties_margin_zero():
      c = evidence_confidence([0.4, 0.4], weights=(0, 0, 1), effective_n=3, min_score=0.2)
      assert c.margin == 0.0 and c.score == 0.0


  def test_unsorted_input_is_sorted():
      c = evidence_confidence([0.1, 0.7, 0.3], weights=(1, 0, 0), effective_n=3, min_score=0.2)
      assert (c.top1, c.margin) == (0.7, pytest.approx(0.4))


  def test_gate_passes_needs_evidence_above_min_score():
      ok, _ = gate_passes([0.15], weights=(0, 0, 1), effective_n=3, threshold=0.1, min_score=0.2)
      assert ok is False
      ok, c = gate_passes([0.5, 0.1], weights=(1, 0, 0), effective_n=3, threshold=0.2, min_score=0.2)
      assert ok is True and c.score == 0.5


  def test_default_params_match_old_rule():
      assert gate_passes([0.2])[0] is True
      assert gate_passes([0.19])[0] is False


  def test_to_dict_rounds():
      assert Confidence(0.123456, 1 / 3, 0.0, 0.5).to_dict() == {
          "top1": 0.1235, "effective": 0.3333, "margin": 0.0, "score": 0.5}
  ```

- [ ] **Step 2: 运行，确认失败** → FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现**

  `app/config.py`：删除 `GATE_MIN_SCORE` 及其注释，加：

  ```python
  # 召回快照条数。不小于 GATE_EFFECTIVE_N 的搜索上限 5。
  SNAPSHOT_TOP_N = 5
  # evidence_confidence = w1·Top-1 + w2·有效证据占比 + w3·Top-1 与 Top-2 的分差。
  # 校准前的初值等价于旧规则（Top-1 ≥ 0.20）。校准报告：evals/reports/gate_calibration_*.md。
  GATE_WEIGHTS = (1.0, 0.0, 0.0)
  GATE_EFFECTIVE_N = 3
  GATE_CONF_THRESHOLD = 0.20
  ```

  `app/services/confidence.py`：

  ```python
  """置信度闸的信号与分数。纯函数，线上闸和校准脚本共用。"""

  from collections.abc import Sequence
  from dataclasses import dataclass

  from app.config import GATE_CONF_THRESHOLD, GATE_EFFECTIVE_N, GATE_WEIGHTS, RERANK_MIN_SCORE


  @dataclass(frozen=True)
  class Confidence:
      top1: float
      effective: float
      margin: float
      score: float

      def to_dict(self) -> dict:
          return {k: round(getattr(self, k), 4) for k in ("top1", "effective", "margin", "score")}


  def evidence_confidence(
      scores: Sequence[float], *, weights: tuple[float, float, float] = GATE_WEIGHTS,
      effective_n: int = GATE_EFFECTIVE_N, min_score: float = RERANK_MIN_SCORE,
  ) -> Confidence:
      ranked = sorted(scores, reverse=True)
      if not ranked:
          return Confidence(0.0, 0.0, 0.0, 0.0)
      top1 = ranked[0]
      top2 = ranked[1] if len(ranked) > 1 else 0.0
      effective = min(sum(s >= min_score for s in ranked) / effective_n, 1.0)
      margin = top1 - top2
      w1, w2, w3 = weights
      return Confidence(top1, effective, margin, w1 * top1 + w2 * effective + w3 * margin)


  def gate_passes(
      scores: Sequence[float], *, weights: tuple[float, float, float] = GATE_WEIGHTS,
      effective_n: int = GATE_EFFECTIVE_N, threshold: float = GATE_CONF_THRESHOLD,
      min_score: float = RERANK_MIN_SCORE,
  ) -> tuple[bool, Confidence]:
      conf = evidence_confidence(scores, weights=weights, effective_n=effective_n, min_score=min_score)
      # 没有过检索门槛的证据时，Agent 拿不到任何证据。
      has_evidence = any(s >= min_score for s in scores)
      return has_evidence and conf.score >= threshold, conf
  ```

- [ ] **Step 4: 运行测试** → PASS（`GATE_MIN_SCORE` 的引用在任务 6 中删除；本任务先让 `app/graph/nodes/knowledge.py` 暂时 `from app.config import GATE_CONF_THRESHOLD as GATE_MIN_SCORE`，保证全量测试通过；任务 6 删除这行别名）。

  Run: `uv run pytest -q` → 全部通过。

- [ ] **Step 5: 提交**

  ```bash
  git add app/services/confidence.py app/config.py app/graph/nodes/knowledge.py tests/test_confidence.py
  git commit -m "feat(ch09): evidence_confidence signals and gate rule"
  ```

---

### Task 6: 检索快照、正式置信度闸、落池带快照

**Files:**
- Modify: `app/graph/state.py`、`app/graph/nodes/knowledge.py`、`app/graph/nodes/aftersales.py`、`app/graph/nodes/turn.py`、`app/graph/nodes/finalize.py`、`app/services/grounding.py`
- Test: `tests/test_graph_nodes.py`、`tests/test_graph_aftersales.py`、`tests/test_grounding.py`

**Interfaces:**
- Consumes: `confidence.gate_passes`、`SNAPSHOT_TOP_N`、`low_confidence.add(..., retrieved_chunks=)`。
- Produces:
  - State 本轮字段 `retrieval: list[dict] | None`（`start_turn` 重置为 None）
  - `knowledge.snapshot(ranked: list[EvidenceItem]) -> list[dict]`（前 `SNAPSHOT_TOP_N` 条，`{"chunk_id","section_path","question","answer","score"}`，分数 4 位小数）
  - `evidence_update(question, result, trace)` 额外返回 `"retrieval": snapshot(result.ranked)`
  - `gate` 字典：`{"passed", "top_score", "confidence", "signals", "reason", "source"}`，`signals = {"top1","effective","margin"}`
  - `grounding.record_low_confidence(conversation_id, raw_question, reason, source="self_check", retrieved_chunks=None) -> int | None`（返回新行 id；失败返回 None）。写入成功后调用 `app.flywheel.runner.get_runner().submit(id)`——本任务中 `app/flywheel/runner.py` 还不存在，因此先只返回 id，任务 8 加提交。

- [ ] **Step 1: 改测试（先失败）**

  `tests/test_graph_nodes.py`：

  1. `test_retrieve_numbers_evidence_and_records_top_score` 末尾加：

     ```python
     # 快照取门槛过滤前的 ranked，包含 0.1 这条低分证据。
     assert [(r["chunk_id"], r["score"]) for r in out["retrieval"]] == [(100, 0.9), (101, 0.5), (102, 0.1)]
     assert set(out["retrieval"][0]) == {"chunk_id", "section_path", "question", "answer", "score"}
     ```

     （chunk_id 以该文件 `fake_retrieval` 的实际编号为准。）

  2. `test_retrieve_with_no_hits` 末尾加 `assert out["retrieval"] == []`。
  3. 替换 `gate_state` 和受影响的测试：

     ```python
     def gate_state(evidence, scores):
         snap = [{"chunk_id": 100 + i, "section_path": "p", "question": "q", "answer": "a", "score": sc}
                 for i, sc in enumerate(scores)]
         return {"user_input": "原话", "resolved_input": "原话", "evidence": evidence, "retrieval": snap,
                 "gate": {"passed": False, "top_score": scores[0] if scores else None, "reason": "", "source": None},
                 "trace": []}


     def gate_view(gate):
         return {k: gate[k] for k in ("passed", "reason", "source")}


     async def test_gate_passes_and_emits_citations(db, monkeypatch, emitted):
         calls = use_checker(monkeypatch)
         out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, [0.8]), rt(await new_conversation(db)))
         assert gate_view(out["gate"]) == {"passed": True, "reason": "依据[1]", "source": None}
         assert out["gate"]["confidence"] == 0.8 and out["gate"]["signals"]["top1"] == 0.8
         assert out["gate"]["top_score"] == 0.8
         assert "[1] 退换货 > 运费" in calls[0]["evidence"]
         assert emitted == [("citations", {"items": EVIDENCE, "refused": False})]


     @pytest.mark.parametrize("evidence,scores", [([], []), (EVIDENCE, [0.1])])
     async def test_gate_low_score_pools_without_self_check(db, monkeypatch, emitted, evidence, scores):
         cid = await new_conversation(db)
         state = gate_state(evidence, scores)
         out = await knowledge_nodes.confidence_gate(state, rt(cid))
         assert out["gate"]["passed"] is False and out["gate"]["source"] == "retrieval_low_conf"
         assert emitted == []
         async with db() as s:
             row = (await s.execute(select(LowConfidenceQuestion))).scalar_one()
         assert (row.conversation_id, row.raw_question, row.source) == (cid, "原话", "retrieval_low_conf")
         assert row.retrieved_chunks == state["retrieval"]


     async def test_gate_uses_weighted_confidence(db, monkeypatch, emitted):
         monkeypatch.setattr(knowledge_nodes, "GATE_WEIGHTS", (0.0, 0.0, 1.0))
         monkeypatch.setattr(knowledge_nodes, "GATE_CONF_THRESHOLD", 0.3)
         cid = await new_conversation(db)
         # Top-1 很高，但与 Top-2 几乎相同，分差 0.01 < 0.3。
         out = await knowledge_nodes.confidence_gate(gate_state(EVIDENCE, [0.9, 0.89]), rt(cid))
         assert out["gate"]["passed"] is False and out["gate"]["source"] == "retrieval_low_conf"
         assert out["gate"]["signals"]["margin"] == pytest.approx(0.01, abs=1e-4)


     async def test_gate_self_check_not_useful_pools_with_snapshot(db, monkeypatch, emitted):
         use_checker(monkeypatch, useful=False, reason="没写到防水")
         cid = await new_conversation(db)
         state = gate_state(EVIDENCE, [0.8])
         out = await knowledge_nodes.confidence_gate(state, rt(cid))
         assert gate_view(out["gate"]) == {"passed": False, "reason": "没写到防水", "source": "self_check"}
         async with db() as s:
             row = (await s.execute(select(LowConfidenceQuestion))).scalar_one()
         assert (row.source, row.reason, row.retrieved_chunks) == ("self_check", "没写到防水", state["retrieval"])
     ```

     其余引用 `gate_state(EVIDENCE, 0.8)` 的测试改为 `gate_state(EVIDENCE, [0.8])`，`gate_state(EVIDENCE, 0.05)` 改为 `[0.05]`；`test_gate_aftersales_passes_on_score_without_self_check` 的断言改为 `gate_view(out["gate"]) == {"passed": True, "reason": "", "source": None}`；删除原 `test_gate_self_check_not_useful_pools`（被上面的新测试替代）和 `monkeypatch.setattr(knowledge_nodes, "GATE_MIN_SCORE", 0.2)`。

  4. `test_start_turn_*` 中重置字段断言加 `"retrieval": None`。

  `tests/test_graph_aftersales.py` 第 89 行附近：加 `assert [r["score"] for r in out["retrieval"]] == [0.8, ...]`（按该测试 fake 的分数写全）。

  `tests/test_grounding.py` 加：

  ```python
  async def test_record_low_confidence_returns_id_and_snapshot(db):
      async with db() as s:
          conv = await conversations.create(s, "u1")
          await s.commit()
      snap = [{"chunk_id": 1, "section_path": "p", "question": "q", "answer": "a", "score": 0.1}]
      lcq_id = await g.record_low_confidence(conv.id, "问", "低", source="retrieval_low_conf", retrieved_chunks=snap)
      async with db() as s:
          row = await s.get(LowConfidenceQuestion, lcq_id)
      assert row.retrieved_chunks == snap


  async def test_record_low_confidence_failure_returns_none(db, monkeypatch):
      async def boom(*a, **k):
          raise RuntimeError("db down")
      monkeypatch.setattr(g.low_confidence, "add", boom)
      assert await g.record_low_confidence(1, "q", "r") is None
  ```

- [ ] **Step 2: 运行，确认失败**

  Run: `uv run pytest tests/test_graph_nodes.py tests/test_graph_aftersales.py tests/test_grounding.py -q` → FAIL（`KeyError: 'retrieval'` 等）。

- [ ] **Step 3: 实现**

  `app/graph/state.py` 的 `ChatState` 在 `evidence` 下加 `retrieval: list[dict] | None`。

  `app/graph/nodes/turn.py` 的 `start_turn` 返回值加 `"retrieval": None`。

  `app/graph/nodes/knowledge.py`（替换整个文件）：

  ```python
  """知识类出口：强制检索，再过置信度闸。"""

  from dataclasses import asdict

  from app.config import GATE_CONF_THRESHOLD, GATE_EFFECTIVE_N, GATE_WEIGHTS, SNAPSHOT_TOP_N, get_settings
  from app.graph import events
  from app.knowledge.retrieval import EvidenceItem, Retrieval, retrieve
  from app.schemas import QueryPlan
  from app.services.confidence import gate_passes
  from app.services.grounding import (
      EMPTY_EVIDENCE_REASON,
      Citation,
      collect_evidence,
      record_low_confidence,
      self_check,
  )


  def snapshot(ranked: list[EvidenceItem]) -> list[dict]:
      """召回快照：重排后、门槛过滤前的前几条，审核人员据此判断是真缺知识还是没检到。"""
      return [{"chunk_id": e.chunk_id, "section_path": e.section_path, "question": e.question,
               "answer": e.answer, "score": round(e.score, 4)} for e in ranked[:SNAPSHOT_TOP_N]]


  def evidence_update(question: str, result: Retrieval, trace: list[str]) -> dict:
      evidence = collect_evidence([("retrieve", question, {"evidence": [asdict(e) for e in result.evidence]})])
      top = result.ranked[0].score if result.ranked else None
      return {
          "evidence": [c.to_dict() for c in evidence.citations],
          "retrieval": snapshot(result.ranked),
          "gate": {"passed": False, "top_score": top, "reason": "", "source": None},
          "trace": trace,
      }


  async def retrieve_evidence(state, runtime):
      trace = events.enter("retrieve", state, runtime)
      plan = QueryPlan(standard_query=state["standard_query"], product_category=state.get("product_category"))
      result = await retrieve(state["resolved_input"], plan=plan, top_n=get_settings().rerank_top_k)
      return evidence_update(state["resolved_input"], result, trace)


  async def confidence_gate(state, runtime):
      trace = events.enter("confidence_gate", state, runtime)
      citations = [Citation(**c) for c in state["evidence"]]
      snap = state.get("retrieval") or []
      ok, conf = gate_passes([c["score"] for c in snap], weights=GATE_WEIGHTS, effective_n=GATE_EFFECTIVE_N,
                             threshold=GATE_CONF_THRESHOLD)
      base = {"top_score": state["gate"]["top_score"], "confidence": round(conf.score, 4),
              "signals": {k: v for k, v in conf.to_dict().items() if k != "score"}}
      if not citations or not ok:
          gate = {**base, "passed": False, "reason": EMPTY_EVIDENCE_REASON, "source": "retrieval_low_conf"}
      elif state.get("route") == "aftersales":
          # 子流程中缺的信息由 Agent 追问，自评会挡在 Agent 之前。
          gate = {**base, "passed": True, "reason": "", "source": None}
      else:
          check = await self_check([state["resolved_input"]], citations)
          gate = {**base, "passed": check.useful, "reason": check.reason,
                  "source": None if check.useful else "self_check"}
      if gate["passed"]:
          events.emit("citations", {"items": state["evidence"], "refused": False})
      else:
          # 独立事务，失败只记日志。
          await record_low_confidence(runtime.context.conversation_id, state["user_input"], gate["reason"],
                                      source=gate["source"], retrieved_chunks=snap)
      return {"gate": gate, "trace": trace}
  ```

  `EMPTY_EVIDENCE_REASON` 的文字改为 `"检索证据置信度低于门槛"`（`app/services/grounding.py`）。

  `app/services/grounding.py` 的 `record_low_confidence`：

  ```python
  async def record_low_confidence(
      conversation_id: int, raw_question: str, reason: str, source: str = "self_check",
      retrieved_chunks: list[dict] | None = None,
  ) -> int | None:
      """独立事务。失败只记日志，不中断本轮。返回新行 id。"""
      try:
          async with get_sessionmaker()() as s:
              row = await low_confidence.add(
                  s, conversation_id=conversation_id, raw_question=raw_question,
                  source=source, reason=reason, retrieved_chunks=retrieved_chunks,
              )
              await s.commit()
              return row.id
      except Exception:
          logger.exception("低置信度问题入池失败")
          return None
  ```

  `app/graph/nodes/finalize.py` 的 `turn` 日志中 `gate=` 的取值改为：

  ```python
  f"{gate.get('passed')}/{gate.get('confidence')}/{gate.get('source')}" if gate else "-",
  ```

- [ ] **Step 4: 运行测试**

  Run: `uv run pytest -q` → 全部通过（`tests/test_graph.py` 的端到端测试经 fake retrieve 得到的分数决定是否走兜底；初值等价旧规则，结果不变）。

- [ ] **Step 5: 提交**

  ```bash
  git add app/graph app/services/grounding.py tests/test_graph_nodes.py tests/test_graph_aftersales.py tests/test_grounding.py
  git commit -m "feat(ch09): confidence gate on evidence_confidence with retrieval snapshot"
  ```

---

### Task 7: 置信度参数校准

**Files:**
- Create: `evals/gate_calibration.py`（纯函数）、`evals/run_gate_calibration.py`（脚本）
- Modify: `app/config.py`（Claude 按报告给出数值，Codex 改常量）
- Test: `tests/test_gate_calibration.py`

**Interfaces:**
- Consumes: `confidence.gate_passes`、`evals.rag_eval_set.load_samples`、`retrieve`、`understand`、`source_key`、`ANSWERABLE`。
- Produces:
  - `SignalRow(sample_id: str, bucket: str, top1_relevant: bool, scores: list[float])`
  - `Params(weights: tuple[float, float, float], effective_n: int, threshold: float)`
  - `Outcome(params: Params, keep: float, d_reject: float)`
  - `weight_grid(step=0.1) -> list[tuple[float, float, float]]`（和为 1，四舍五入到 1 位小数）
  - `evaluate(rows, params) -> Outcome`
  - `search(rows, *, min_keep=0.95, ns=(2, 3, 4, 5), thresholds=...) -> Outcome | None`
  - `stratified_folds(rows, k=5, seed=0) -> list[list[SignalRow]]`
  - `cross_validate(rows, k=5, seed=0, min_keep=0.95) -> list[tuple[Outcome, Outcome]]`（每折 (训练最优, 该参数在验证折的结果)）
  - `render_report(full: Outcome, folds, old: Outcome, n_rows: int) -> str`

- [ ] **Step 1: 写失败测试 `tests/test_gate_calibration.py`**

  ```python
  from evals.gate_calibration import (
      Params, SignalRow, cross_validate, evaluate, search, stratified_folds, weight_grid,
  )


  def rows():
      out = []
      for i in range(20):
          out.append(SignalRow(f"A{i}", "A_policy", True, [0.8, 0.3, 0.25]))
      for i in range(10):
          out.append(SignalRow(f"D{i}", "D_unanswerable", False, [0.45, 0.44]))  # 分数高但分差小
      return out


  def test_weight_grid():
      grid = weight_grid(0.5)
      assert set(grid) == {(1.0, 0.0, 0.0), (0.5, 0.5, 0.0), (0.5, 0.0, 0.5), (0.0, 1.0, 0.0), (0.0, 0.5, 0.5),
                           (0.0, 0.0, 1.0)}
      assert len(weight_grid(0.1)) == 66


  def test_evaluate_old_rule_cannot_reject_d():
      o = evaluate(rows(), Params((1.0, 0.0, 0.0), 3, 0.20))
      assert (o.keep, o.d_reject) == (1.0, 0.0)


  def test_search_finds_margin_weight():
      best = search(rows(), ns=(3,), thresholds=[i / 100 for i in range(0, 101)])
      assert best.keep >= 0.95 and best.d_reject == 1.0
      assert best.params.weights[2] > 0


  def test_unrelevant_top1_not_kept():
      r = [SignalRow("A1", "A_policy", False, [0.9])]
      assert evaluate(r, Params((1, 0, 0), 3, 0.1)).keep == 0.0


  def test_stratified_folds_cover_all_once():
      folds = stratified_folds(rows(), k=5, seed=0)
      ids = [r.sample_id for f in folds for r in f]
      assert sorted(ids) == sorted(r.sample_id for r in rows())
      assert all(sum(r.bucket == "D_unanswerable" for r in f) == 2 for f in folds)


  def test_cross_validate_shapes():
      res = cross_validate(rows(), k=5, seed=0)
      assert len(res) == 5 and all(train.params == test.params for train, test in res)
  ```

- [ ] **Step 2: 运行，确认失败** → FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现 `evals/gate_calibration.py`**

  ```python
  """置信度闸参数的网格搜索与交叉验证。纯函数，不访问外部服务。"""

  import random
  from collections import defaultdict
  from dataclasses import dataclass

  from app.services.confidence import gate_passes
  from evals.rag_metrics import ANSWERABLE

  D_BUCKET = "D_unanswerable"
  THRESHOLDS = tuple(i / 100 for i in range(0, 101))


  @dataclass(frozen=True)
  class SignalRow:
      sample_id: str
      bucket: str
      top1_relevant: bool
      scores: list[float]


  @dataclass(frozen=True)
  class Params:
      weights: tuple[float, float, float]
      effective_n: int
      threshold: float


  @dataclass(frozen=True)
  class Outcome:
      params: Params
      keep: float
      d_reject: float


  def weight_grid(step: float = 0.1) -> list[tuple[float, float, float]]:
      n = round(1 / step)
      return [(round(a * step, 1), round(b * step, 1), round((n - a - b) * step, 1))
              for a in range(n + 1) for b in range(n + 1 - a)]


  def evaluate(rows: list[SignalRow], params: Params) -> Outcome:
      answerable = [r for r in rows if r.bucket in ANSWERABLE]
      unanswerable = [r for r in rows if r.bucket == D_BUCKET]

      def passed(r):
          return gate_passes(r.scores, weights=params.weights, effective_n=params.effective_n,
                             threshold=params.threshold)[0]

      keep = sum(r.top1_relevant and passed(r) for r in answerable) / len(answerable) if answerable else 0.0
      d_reject = sum(not passed(r) for r in unanswerable) / len(unanswerable) if unanswerable else 0.0
      return Outcome(params, keep, d_reject)


  def _key(o: Outcome):
      # D 拒答率最高；相同时保留率更高；再相同时取门槛低、N 小、w1 大的参数，结果可复现。
      return (-o.d_reject, -o.keep, o.params.threshold, o.params.effective_n, tuple(-w for w in o.params.weights))


  def search(rows, *, min_keep: float = 0.95, ns=(2, 3, 4, 5), thresholds=THRESHOLDS) -> Outcome | None:
      best = None
      for w in weight_grid():
          for n in ns:
              for t in thresholds:
                  o = evaluate(rows, Params(w, n, t))
                  if o.keep >= min_keep and (best is None or _key(o) < _key(best)):
                      best = o
      return best


  def stratified_folds(rows, k: int = 5, seed: int = 0) -> list[list[SignalRow]]:
      by_bucket = defaultdict(list)
      for r in rows:
          by_bucket[r.bucket].append(r)
      folds = [[] for _ in range(k)]
      rng = random.Random(seed)
      for bucket in sorted(by_bucket):
          items = sorted(by_bucket[bucket], key=lambda r: r.sample_id)
          rng.shuffle(items)
          for i, r in enumerate(items):
              folds[i % k].append(r)
      return folds


  def cross_validate(rows, k: int = 5, seed: int = 0, min_keep: float = 0.95) -> list[tuple[Outcome, Outcome]]:
      folds = stratified_folds(rows, k, seed)
      out = []
      for i, test in enumerate(folds):
          train = [r for j, f in enumerate(folds) if j != i for r in f]
          best = search(train, min_keep=min_keep)
          if best is None:
              continue
          out.append((best, evaluate(test, best.params)))
      return out


  def render_report(full: Outcome | None, folds, old: Outcome, n_rows: int) -> str:
      lines = ["# 置信度闸校准报告", "", f"样本数：{n_rows}；规则：可答题保留率 ≥ 95% 时 D 桶拒答率最高。", ""]
      lines += ["## 全量最优", ""]
      if full is None:
          lines.append("没有满足保留率约束的参数。")
      else:
          p = full.params
          lines.append(f"- GATE_WEIGHTS = {p.weights}\n- GATE_EFFECTIVE_N = {p.effective_n}\n"
                       f"- GATE_CONF_THRESHOLD = {p.threshold:.2f}\n- 保留率 {full.keep:.3f}，D 拒答率 {full.d_reject:.3f}")
      lines += ["", "## 旧规则（Top-1 ≥ 0.20）", "", f"- 保留率 {old.keep:.3f}，D 拒答率 {old.d_reject:.3f}", "",
                "## 5 折交叉验证", "", "| 折 | 参数 | 训练保留率 | 训练 D 拒答率 | 验证保留率 | 验证 D 拒答率 |",
                "|---|---|---|---|---|---|"]
      for i, (train, test) in enumerate(folds, 1):
          p = train.params
          lines.append(f"| {i} | {p.weights}/N={p.effective_n}/t={p.threshold:.2f} | {train.keep:.3f} | "
                       f"{train.d_reject:.3f} | {test.keep:.3f} | {test.d_reject:.3f} |")
      return "\n".join(lines)
  ```

  `evals/run_gate_calibration.py`：

  ```python
  """置信度闸参数校准。对评估集每题调一次生产检索，缓存信号，再离线网格搜索。

  用法：
    uv run python evals/run_gate_calibration.py                 # 调上游采集信号并搜索
    uv run python evals/run_gate_calibration.py --from-cache <jsonl>   # 只用缓存搜索
  """

  import argparse
  import asyncio
  import json
  import logging
  import sys
  from dataclasses import asdict
  from datetime import datetime
  from pathlib import Path

  SCRIPT_DIR = Path(__file__).resolve().parent
  sys.path.insert(0, str(SCRIPT_DIR.parent))

  from app.db.engine import dispose_engine
  from app.knowledge.milvus import close_milvus, ensure_collection
  from app.knowledge.query import understand
  from app.knowledge.rerank import close_rerank
  from app.knowledge.retrieval import retrieve, source_key
  from evals.gate_calibration import Params, SignalRow, cross_validate, evaluate, render_report, search
  from evals.rag_eval_set import load_samples

  logger = logging.getLogger(__name__)
  REPORTS_DIR = SCRIPT_DIR / "reports"


  async def collect(concurrency: int) -> list[SignalRow]:
      sem = asyncio.Semaphore(concurrency)
      rows: list[SignalRow] = []

      async def one(sample):
          async with sem:
              try:
                  # 与 ch04 门槛扫描一致：排除挖掘块和飞轮块，保证相关性标注有效。
                  r = await retrieve(sample.query, plan=await understand(sample.query), exclude_mined=True)
              except Exception:
                  logger.exception("检索失败：%s", sample.id)
                  return
              ranked = [source_key(e.section_path, e.question) for e in r.ranked]
              relevant = bool(ranked) and any(ranked[0] in g for g in sample.relevant)
              rows.append(SignalRow(sample.id, sample.bucket, relevant, [e.score for e in r.ranked[:5]]))

      try:
          await ensure_collection()
          await asyncio.gather(*(one(s) for s in load_samples()))
      finally:
          try:
              await close_milvus()
          finally:
              try:
                  await close_rerank()
              finally:
                  await dispose_engine()
      return sorted(rows, key=lambda r: r.sample_id)


  def main() -> int:
      parser = argparse.ArgumentParser(description="置信度闸参数校准。")
      parser.add_argument("--from-cache", type=Path)
      parser.add_argument("--concurrency", type=int, default=3)
      args = parser.parse_args()
      stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
      REPORTS_DIR.mkdir(parents=True, exist_ok=True)
      if args.from_cache:
          rows = [SignalRow(**json.loads(line)) for line in args.from_cache.read_text(encoding="utf-8").splitlines()]
      else:
          rows = asyncio.run(collect(args.concurrency))
          cache = REPORTS_DIR / f"gate_signals_{stamp}.jsonl"
          cache.write_text("\n".join(json.dumps(asdict(r), ensure_ascii=False) for r in rows), encoding="utf-8")
          print(f"信号缓存：{cache}")
      full = search(rows)
      report = render_report(full, cross_validate(rows), evaluate(rows, Params((1.0, 0.0, 0.0), 3, 0.20)), len(rows))
      path = REPORTS_DIR / f"gate_calibration_{stamp}.md"
      path.write_text(report, encoding="utf-8")
      print(report)
      print(f"报告：{path}")
      return 0 if full is not None else 1


  if __name__ == "__main__":
      sys.exit(main())
  ```

- [ ] **Step 4: 运行测试** → PASS。

- [ ] **Step 5（Claude）: 真实校准**

  1. Run: `uv run python evals/run_gate_calibration.py`（真实上游 + 生产集合，约 5 分钟）。
  2. 读报告：全量最优、旧规则对比、5 折验证。如果验证折 D 拒答率比训练折低 0.15 以上，判为过拟合，在 dev-notes 记录，并改用 5 折中出现次数最多的 (weights, N) 组合、在全量上重新搜索门槛。
  3. 把最终数值交给 Codex 写进 `app/config.py` 的 3 个常量，注释改为报告文件名。

- [ ] **Step 6: 提交**

  ```bash
  git add evals/gate_calibration.py evals/run_gate_calibration.py tests/test_gate_calibration.py app/config.py \
    evals/reports/gate_calibration_*.md evals/reports/gate_signals_*.jsonl
  git commit -m "feat(ch09): calibrate evidence_confidence on the ch04 eval set"
  ```

  提交后运行 `uv run pytest -q`。如果 `tests/test_graph.py` 的端到端测试因新参数改变走向，按新参数修正 fake 分数（例如给 fake retrieve 足够的分差），不改断言的意图。

---

### Task 8: 飞轮流水线（Runner、标准化、查重、补跑脚本）

**Files:**
- Create: `app/flywheel/__init__.py`、`app/flywheel/runner.py`、`app/flywheel/pipeline.py`、`scripts/run_flywheel.py`
- Modify: `app/schemas.py`、`app/prompts.py`、`app/llm.py`、`app/config.py`、`app/services/grounding.py`、`app/main.py`、`tests/conftest.py`
- Test: `tests/test_flywheel.py`

**Interfaces:**
- Consumes: 任务 2 的仓储；`get_embeddings()`；`record_low_confidence` 返回 id。
- Produces:
  - 常量：`FLYWHEEL_LLM_TIMEOUT_SECONDS = 20`、`REVIEW_DEDUP_MIN_SCORE = 0.75`、`REVIEW_DEDUP_TOP_K = 5`
  - `schemas.NormalizedQuestion{normalized_question: str(2–100), suggested_answer: str(1–500)}`、`schemas.ReviewDedup{duplicate_of: int | None}`
  - `llm.get_question_normalizer() -> Runnable`、`llm.get_review_dedup_judge() -> Runnable`（关闭思考，`json_mode`，`include_raw=True`）
  - `pipeline.format_chunks(chunks: list[dict] | None) -> str`
  - `pipeline.format_candidates(cands: list[tuple[int, str]]) -> str`
  - `pipeline.process(lcq_id: int) -> ProcessResult | None`（`ProcessResult(review_id: int, merged: bool, candidates: int)`；跳过或失败返回 None）
  - `runner.FlywheelRunner`：`start()`、`submit(lcq_id) -> bool`、`drain()`、`stop()`；`runner.get_runner()`、`runner.set_runner()`
  - fixture `use_flywheel(normalized=[...], dedup=[...])`；fixture `flywheel_runner`（启动真 Runner，结束时 `stop()`）

- [ ] **Step 1: 写失败测试 `tests/test_flywheel.py`**

  ```python
  import asyncio

  from app.flywheel import pipeline
  from app.flywheel.runner import FlywheelRunner
  from app.repositories import conversations, low_confidence, review_queue
  from app.services import grounding
  from tests.fakes import FakeEmbeddings

  SNAP = [{"chunk_id": 1, "section_path": "保温杯 > 清洗", "question": "怎么清洗", "answer": "手洗", "score": 0.31}]


  async def _lcq(db, raw, snap=SNAP, source="retrieval_low_conf"):
      async with db() as s:
          c = await conversations.create(s, "u1")
          row = await low_confidence.add(s, conversation_id=c.id, raw_question=raw, source=source, reason="r",
                                         retrieved_chunks=snap)
          await s.commit()
          return row.id


  async def test_new_gap_creates_review_row(db, use_flywheel):
      calls = use_flywheel(normalized=[("保温杯可以用洗碗机清洗吗？", "（待核实）不建议。")], dedup=[])
      lcq = await _lcq(db, "杯子能扔洗碗机吗？？急")
      res = await pipeline.process(lcq)
      assert res.merged is False and res.candidates == 0
      async with db() as s:
          item = await review_queue.get(s, res.review_id)
          assert (item.normalized_question, item.ai_suggested_answer, item.occurrence_count) == (
              "保温杯可以用洗碗机清洗吗？", "（待核实）不建议。", 1)
          assert (await low_confidence.get(s, lcq)).matched_review_id == res.review_id
      assert "怎么清洗" in calls["normalize"][0]["chunks"] and calls["dedup"] == []


  async def test_duplicate_increments(db, use_flywheel, monkeypatch):
      emb = FakeEmbeddings(rules=[("洗碗机", [1.0] + [0.0] * 1023)])
      monkeypatch.setattr(pipeline, "get_embeddings", lambda: emb)
      calls = use_flywheel(normalized=[("保温杯可以用洗碗机清洗吗？", "（待核实）a"),
                                       ("保温杯能放洗碗机洗吗？", "（待核实）b")], dedup=[1])
      first = await pipeline.process(await _lcq(db, "杯子能扔洗碗机吗"))
      second = await pipeline.process(await _lcq(db, "保温杯洗碗机能洗不"))
      assert second.merged is True and second.review_id == first.review_id and second.candidates == 1
      assert "1. 保温杯可以用洗碗机清洗吗？" in calls["dedup"][0]["candidates"]
      async with db() as s:
          assert (await review_queue.get(s, first.review_id)).occurrence_count == 2


  async def test_already_matched_is_skipped(db, use_flywheel):
      calls = use_flywheel(normalized=[("Q？", "（待核实）a")], dedup=[])
      lcq = await _lcq(db, "q")
      await pipeline.process(lcq)
      assert await pipeline.process(lcq) is None
      assert len(calls["normalize"]) == 1


  async def test_normalize_failure_keeps_null(db, use_flywheel, caplog):
      use_flywheel(normalized=[None], dedup=[])
      lcq = await _lcq(db, "q", snap=None)
      assert await pipeline.process(lcq) is None
      assert "flywheel_failed" in caplog.text and "step=normalize" in caplog.text
      async with db() as s:
          assert (await low_confidence.get(s, lcq)).matched_review_id is None


  async def test_dedup_out_of_range_keeps_null(db, use_flywheel, monkeypatch):
      emb = FakeEmbeddings(rules=[("洗碗机", [1.0] + [0.0] * 1023)])
      monkeypatch.setattr(pipeline, "get_embeddings", lambda: emb)
      use_flywheel(normalized=[("保温杯洗碗机A？", "（待核实）"), ("保温杯洗碗机B？", "（待核实）")], dedup=[3])
      await pipeline.process(await _lcq(db, "a"))
      lcq = await _lcq(db, "b")
      assert await pipeline.process(lcq) is None
      async with db() as s:
          assert (await low_confidence.get(s, lcq)).matched_review_id is None


  def test_format_chunks():
      assert pipeline.format_chunks(None) == "（无）"
      assert pipeline.format_chunks(SNAP) == "[1] 保温杯 > 清洗（分数 0.31）\n问：怎么清洗\n答：手洗"


  async def test_runner_serializes_duplicates(db, use_flywheel, monkeypatch, flywheel_runner):
      emb = FakeEmbeddings(rules=[("洗碗机", [1.0] + [0.0] * 1023)])
      monkeypatch.setattr(pipeline, "get_embeddings", lambda: emb)
      use_flywheel(normalized=[("保温杯洗碗机？", "（待核实）"), ("保温杯洗碗机？", "（待核实）")], dedup=[1])
      a, b = await _lcq(db, "x"), await _lcq(db, "y")
      assert flywheel_runner.submit(a) and flywheel_runner.submit(b)
      await flywheel_runner.drain()
      async with db() as s:
          items = await review_queue.list_items(s, None)
      assert [(i.occurrence_count) for i in items] == [2]


  async def test_record_low_confidence_submits(db, monkeypatch):
      submitted = []
      runner = FlywheelRunner()
      monkeypatch.setattr(runner, "submit", submitted.append)
      monkeypatch.setattr(grounding, "get_flywheel_runner", lambda: runner)
      async with db() as s:
          c = await conversations.create(s, "u1")
          await s.commit()
      lcq = await grounding.record_low_confidence(c.id, "q", "r")
      assert submitted == [lcq]


  def test_submit_when_not_started_logs(caplog):
      assert FlywheelRunner().submit(1) is False
      assert "flywheel_runner_off" in caplog.text
  ```

  `tests/conftest.py` 加：

  ```python
  @pytest.fixture(autouse=True)
  def _isolate_flywheel(monkeypatch):
      """测试默认不启动飞轮 Runner，不调上游。"""
      from app.flywheel import pipeline, runner
      monkeypatch.setattr(pipeline, "get_question_normalizer", _blocked_factory("get_question_normalizer"))
      monkeypatch.setattr(pipeline, "get_review_dedup_judge", _blocked_factory("get_review_dedup_judge"))
      pipeline.clear_vector_cache()
      runner.set_runner(runner.FlywheelRunner())


  @pytest.fixture
  def use_flywheel(monkeypatch):
      """用法：use_flywheel(normalized=[(问题, 答案) 或 None 或异常], dedup=[序号 或 None 或异常])。"""
      from langchain_core.runnables import RunnableLambda
      from app.flywheel import pipeline
      from app.schemas import NormalizedQuestion, ReviewDedup

      def _use(normalized, dedup):
          nq, dq = list(normalized), list(dedup)
          calls = {"normalize": [], "dedup": []}

          async def norm(inputs):
              calls["normalize"].append(inputs)
              v = nq.pop(0)
              if isinstance(v, BaseException):
                  raise v
              parsed = None if v is None else NormalizedQuestion(normalized_question=v[0], suggested_answer=v[1])
              return {"parsed": parsed, "raw": None}

          async def judge(inputs):
              calls["dedup"].append(inputs)
              v = dq.pop(0)
              if isinstance(v, BaseException):
                  raise v
              return {"parsed": ReviewDedup(duplicate_of=v), "raw": None}

          monkeypatch.setattr(pipeline, "get_question_normalizer", lambda: RunnableLambda(norm))
          monkeypatch.setattr(pipeline, "get_review_dedup_judge", lambda: RunnableLambda(judge))
          return calls

      return _use


  @pytest.fixture
  async def flywheel_runner():
      from app.flywheel import runner
      r = runner.FlywheelRunner()
      r.start()
      runner.set_runner(r)
      yield r
      await r.stop()
  ```

- [ ] **Step 2: 运行，确认失败** → FAIL（`ModuleNotFoundError: app.flywheel`）。

- [ ] **Step 3: 实现**

  `app/config.py` 加：

  ```python
  # 飞轮的标准化和查重调用等待时间。超时后该行留给补跑脚本。
  FLYWHEEL_LLM_TIMEOUT_SECONDS = 20
  # 待审问题查重的余弦相似度门槛和候选上限。初值同 DEDUP_STAGING_MIN_SCORE，用查重样例集检查。
  REVIEW_DEDUP_MIN_SCORE = 0.75
  REVIEW_DEDUP_TOP_K = 5
  ```

  `app/schemas.py` 加（复用文件中已有的 `_null_like`）：

  ```python
  class NormalizedQuestion(BaseModel):
      """飞轮标准化结果。"""

      normalized_question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
      suggested_answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


  class ReviewDedup(BaseModel):
      """待审问题查重结果。"""

      duplicate_of: int | None = Field(description="同一个意思时填候选序号（从 1 开始）；否则为 null")

      @field_validator("duplicate_of", mode="before")
      @classmethod
      def _null_like_to_none(cls, value):
          return _null_like(value)
  ```

  `app/prompts.py` 加：

  ```python
  NORMALIZE_SYSTEM_PROMPT = """你是售后知识库的问题整理员。把用户的一句原话整理成知识库的标准问题，并写一条示例答案，供人工审核参考。

  ## normalized_question
  1. 写成一句完整、中性的问句，例如"X3 Pro 耳机可以戴着游泳吗？"。
  2. 去掉情绪词、寒暄、催促和抱怨。
  3. 去掉订单号、手机号、姓名、地址等个人信息。
  4. 型号、数字、时间和限定条件原样保留，例如"X3 Pro""签收第 8 天""拆封后"。
  5. 一句话问了几件事时，合并为一句，每件事都保留。
  6. 不超过 60 字。

  ## suggested_answer
  1. 以"（待核实）"开头。
  2. 参考片段中有相关内容时，只依据片段回答，不编造片段中没有的数字、期限和政策。
  3. 参考片段中没有相关内容时，按电商售后的通常做法写一条示例答案，不写具体金额、天数等数字。
  4. 不超过 200 字。

  ## 输出
  只输出 JSON：{"normalized_question": "...", "suggested_answer": "..."}"""

  normalize_prompt = ChatPromptTemplate.from_messages([
      ("system", NORMALIZE_SYSTEM_PROMPT),
      ("human", "用户原话：{question}\n\n参考片段：\n{chunks}"),
  ])


  REVIEW_DEDUP_SYSTEM_PROMPT = """你是售后知识库的查重员。判断新问题与候选问题中的哪一条是同一个意思。

  ## 规则
  1. 同一个意思：问的是同一件事，一条答案能同时回答两者。说法、语序、口语和书面语不同，都算同一个意思。
  2. 不是同一个意思：商品品类或型号不同；条件不同（例如"拆封后"和"未拆封"、"7 天内"和"超过 7 天"）；问的环节不同（例如"退款多久到账"和"怎么申请退款"）。
  3. 有同一个意思的候选时，duplicate_of 填它的序号（从 1 开始）；有多条时填序号最小的一条。没有时填 null。

  ## 输出
  只输出 JSON：{"duplicate_of": 序号或 null}"""

  review_dedup_prompt = ChatPromptTemplate.from_messages([
      ("system", REVIEW_DEDUP_SYSTEM_PROMPT),
      ("human", "新问题：{question}\n\n候选：\n{candidates}"),
  ])
  ```

  `app/llm.py` 加：

  ```python
  @lru_cache
  def get_question_normalizer() -> Runnable:
      model = build_extract_model(get_settings())
      return normalize_prompt | model.with_structured_output(
          NormalizedQuestion, method="json_mode", include_raw=True
      )


  @lru_cache
  def get_review_dedup_judge() -> Runnable:
      model = build_extract_model(get_settings())
      return review_dedup_prompt | model.with_structured_output(
          ReviewDedup, method="json_mode", include_raw=True
      )
  ```

  `app/flywheel/__init__.py`：空文件，首行 docstring `"""数据飞轮：落池问题的标准化、查重和入待审队列。"""`。

  `app/flywheel/pipeline.py`：

  ```python
  """处理一条落池问题：标准化 → 召回待审候选 → 查重判定 → 累加或新建。"""

  import asyncio
  import logging
  import math
  from dataclasses import dataclass

  from app.config import FLYWHEEL_LLM_TIMEOUT_SECONDS, REVIEW_DEDUP_MIN_SCORE, REVIEW_DEDUP_TOP_K
  from app.db.engine import get_sessionmaker
  from app.knowledge.embeddings import get_embeddings
  from app.llm import get_question_normalizer, get_review_dedup_judge
  from app.repositories import low_confidence, review_queue

  logger = logging.getLogger(__name__)
  # 待审问题的向量缓存，键为 (id, 问题文本)。问题文本不变时不重复嵌入。
  _vectors: dict[tuple[int, str], list[float]] = {}


  @dataclass(frozen=True)
  class ProcessResult:
      review_id: int
      merged: bool
      candidates: int


  class StepFailed(Exception):
      def __init__(self, step: str):
          super().__init__(step)
          self.step = step


  def clear_vector_cache() -> None:
      _vectors.clear()


  def format_chunks(chunks: list[dict] | None) -> str:
      if not chunks:
          return "（无）"
      return "\n\n".join(f"[{i}] {c['section_path']}（分数 {c['score']:.2f}）\n问：{c['question']}\n答：{c['answer']}"
                         for i, c in enumerate(chunks, 1))


  def format_candidates(cands: list[tuple[int, str]]) -> str:
      return "\n".join(f"{i}. {q}" for i, (_, q) in enumerate(cands, 1))


  def _cosine(a: list[float], b: list[float]) -> float:
      dot = sum(x * y for x, y in zip(a, b))
      return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


  async def _call(runnable, inputs: dict, step: str):
      try:
          result = await asyncio.wait_for(runnable.ainvoke(inputs), FLYWHEEL_LLM_TIMEOUT_SECONDS)
      except Exception as exc:
          raise StepFailed(step) from exc
      if result["parsed"] is None:
          logger.error("flywheel_invalid step=%s raw=%r", step, result.get("raw"))
          raise StepFailed(step)
      return result["parsed"]


  async def _candidates(question: str) -> tuple[list[float], list[tuple[int, str]]]:
      emb = get_embeddings()
      async with get_sessionmaker()() as s:
          pending = await review_queue.list_pending_questions(s)
      missing = [(i, q) for i, q in pending if (i, q) not in _vectors]
      vectors = await emb.aembed_documents([question, *(q for _, q in missing)])
      for key, v in zip(missing, vectors[1:]):
          _vectors[key] = v
      scored = [(_cosine(vectors[0], _vectors[(i, q)]), i, q) for i, q in pending]
      top = sorted((x for x in scored if x[0] >= REVIEW_DEDUP_MIN_SCORE), key=lambda x: (-x[0], x[1]))
      return vectors[0], [(i, q) for _, i, q in top[:REVIEW_DEDUP_TOP_K]]


  async def process(lcq_id: int) -> ProcessResult | None:
      step = "load"
      try:
          async with get_sessionmaker()() as s:
              row = await low_confidence.get(s, lcq_id)
          if row is None or row.matched_review_id is not None:
              return None
          step = "normalize"
          norm = await _call(get_question_normalizer(),
                             {"question": row.raw_question, "chunks": format_chunks(row.retrieved_chunks)}, step)
          step = "candidates"
          try:
              _, cands = await _candidates(norm.normalized_question)
          except Exception as exc:
              raise StepFailed(step) from exc
          dup_id = None
          if cands:
              step = "judge"
              verdict = await _call(get_review_dedup_judge(),
                                    {"question": norm.normalized_question, "candidates": format_candidates(cands)},
                                    step)
              if verdict.duplicate_of is not None:
                  if not 1 <= verdict.duplicate_of <= len(cands):
                      logger.error("flywheel_invalid step=judge duplicate_of=%s candidates=%s",
                                   verdict.duplicate_of, len(cands))
                      raise StepFailed(step)
                  dup_id = cands[verdict.duplicate_of - 1][0]
          step = "save"
          async with get_sessionmaker()() as s:
              if dup_id is not None:
                  await review_queue.increment(s, dup_id)
                  review_id = dup_id
              else:
                  item = await review_queue.add(s, normalized_question=norm.normalized_question,
                                                suggested_answer=norm.suggested_answer)
                  review_id = item.id
              await low_confidence.set_matched(s, lcq_id, review_id)
              await s.commit()
      except StepFailed as exc:
          logger.exception("flywheel_failed lcq=%s step=%s", lcq_id, exc.step)
          return None
      except Exception:
          logger.exception("flywheel_failed lcq=%s step=%s", lcq_id, step)
          return None
      logger.info("flywheel lcq=%s review=%s merged=%s candidates=%s", lcq_id, review_id, dup_id is not None,
                  len(cands))
      return ProcessResult(review_id, dup_id is not None, len(cands))
  ```

  `app/flywheel/runner.py`：

  ```python
  """进程内飞轮队列。单 worker 串行处理，防止并发查重把同一缺口建成两行。"""

  import asyncio
  import logging

  from app.flywheel import pipeline

  logger = logging.getLogger(__name__)


  class FlywheelRunner:
      def __init__(self):
          self._queue: asyncio.Queue[int] | None = None
          self._worker: asyncio.Task | None = None

      def start(self) -> None:
          self._queue = asyncio.Queue()
          self._worker = asyncio.create_task(self._loop())

      def submit(self, lcq_id: int) -> bool:
          if self._worker is None or self._worker.done():
              logger.info("flywheel_runner_off lcq=%s", lcq_id)
              return False
          self._queue.put_nowait(lcq_id)
          return True

      async def _loop(self) -> None:
          while True:
              lcq_id = await self._queue.get()
              try:
                  await pipeline.process(lcq_id)
              except Exception:
                  logger.exception("flywheel_failed lcq=%s step=runner", lcq_id)
              finally:
                  self._queue.task_done()

      async def drain(self) -> None:
          if self._queue is not None:
              await self._queue.join()

      async def stop(self) -> None:
          # 不等队列清空；未处理的行由 scripts/run_flywheel.py 补跑。
          if self._worker is not None:
              self._worker.cancel()
              await asyncio.gather(self._worker, return_exceptions=True)
          self._worker = None


  _runner = FlywheelRunner()


  def get_runner() -> FlywheelRunner:
      return _runner


  def set_runner(runner: FlywheelRunner) -> None:
      global _runner
      _runner = runner
  ```

  `app/services/grounding.py`：`from app.flywheel.runner import get_runner as get_flywheel_runner`；`record_low_confidence` 在 `return row.id` 之前（`commit` 之后）调用 `get_flywheel_runner().submit(row.id)`。

  `app/main.py` 的 `lifespan`：`setup_file_logging` 之后 `get_flywheel_runner().start()`；`finally` 中 `await get_flywheel_runner().stop()`（在 `get_runner().cancel_all()` 之后）。

  `scripts/run_flywheel.py`：

  ```python
  """补跑飞轮：处理所有 matched_review_id 为空的落池问题。

  用法：
    uv run python scripts/run_flywheel.py            # 全部处理
    uv run python scripts/run_flywheel.py --limit 20
    uv run python scripts/run_flywheel.py --status   # 只看统计
  crontab 示例（每 30 分钟补跑一次）：
    */30 * * * * cd /path/to/Aftersales-agent && uv run python scripts/run_flywheel.py >> log/flywheel.log 2>&1
  """

  import argparse
  import asyncio
  import sys
  from pathlib import Path

  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

  from app.db.engine import dispose_engine, get_sessionmaker
  from app.flywheel.pipeline import process
  from app.repositories import low_confidence, review_queue


  async def run(limit: int | None, status_only: bool) -> int:
      try:
          async with get_sessionmaker()() as s:
              pending = await low_confidence.count_unmatched(s)
              waiting = len(await review_queue.list_items(s, "待审"))
              ids = await low_confidence.list_unmatched_ids(s, limit)
          print(f"待处理落池问题：{pending}；待审缺口：{waiting}")
          if status_only:
              return 0
          failed = 0
          for lcq_id in ids:
              if await process(lcq_id) is None:
                  failed += 1
          print(f"本次处理：{len(ids)}；失败：{failed}")
          return 1 if failed else 0
      finally:
          await dispose_engine()


  def main() -> int:
      parser = argparse.ArgumentParser(description="补跑飞轮流水线。")
      parser.add_argument("--limit", type=int)
      parser.add_argument("--status", action="store_true")
      args = parser.parse_args()
      return asyncio.run(run(args.limit, args.status))


  if __name__ == "__main__":
      sys.exit(main())
  ```

  注意：`process()` 在"已处理"时也返回 None。补跑脚本只取 `matched_review_id IS NULL` 的行，因此 None 只表示失败。

- [ ] **Step 4: 运行测试**

  Run: `uv run pytest tests/test_flywheel.py -q` → PASS；`uv run pytest -q` → 全部通过。

- [ ] **Step 5: 提交**

  ```bash
  git add app/flywheel scripts/run_flywheel.py app/schemas.py app/prompts.py app/llm.py app/config.py \
    app/services/grounding.py app/main.py tests/conftest.py tests/test_flywheel.py
  git commit -m "feat(ch09): flywheel runner normalizes and dedups pooled questions"
  ```

---

### Task 9: 标准化和查重 Prompt 的样例验证（非可单测）

**Files:**
- Create: `evals/normalize_samples.jsonl`、`evals/review_dedup_samples.jsonl`、`evals/run_normalize_eval.py`、`evals/run_review_dedup_eval.py`

**Interfaces:**
- Consumes: `get_question_normalizer()`、`get_review_dedup_judge()`、`pipeline.format_chunks`、`pipeline.format_candidates`。

- [ ] **Step 1: 写样例 `evals/normalize_samples.jsonl`（15 条）**

  字段：`question`（原话）、`chunks`（快照，可为 null）、`must_keep`（标准化问题必须原样包含的词）、`must_drop`（不得出现的词）。

  ```jsonl
  {"question": "我那个 X3 Pro 耳机能不能戴着游泳啊？？急急急", "chunks": null, "must_keep": ["X3 Pro", "游泳"], "must_drop": ["急", "？？"]}
  {"question": "订单 20261001 的保温杯能扔洗碗机里洗吗", "chunks": [{"chunk_id": 1, "section_path": "商品手册 > 保温杯 > 清洗", "question": "保温杯怎么清洗", "answer": "建议用软毛刷手洗。", "score": 0.31}], "must_keep": ["保温杯", "洗碗机"], "must_drop": ["20261001", "订单"]}
  {"question": "你们客服太慢了！！S10 Max 扫地机能不能连小米米家", "chunks": null, "must_keep": ["S10 Max", "米家"], "must_drop": ["太慢", "！！"]}
  {"question": "签收第 8 天了还能七天无理由退吗 气死了", "chunks": null, "must_keep": ["第 8 天", "无理由"], "must_drop": ["气死"]}
  {"question": "羊毛衫 W1 Plus 能不能机洗，手机号 13800138000", "chunks": null, "must_keep": ["W1 Plus", "机洗"], "must_drop": ["13800138000"]}
  {"question": "T3 Pro 牙刷头多久换一次 顺便问下有没有替换装卖", "chunks": null, "must_keep": ["T3 Pro"], "must_drop": []}
  {"question": "台灯 L2 晚上开着会不会伤眼睛啊 我家孩子用", "chunks": null, "must_keep": ["L2"], "must_drop": ["我家孩子"]}
  {"question": "拆封后的手机壳 K1 还能退吗", "chunks": [{"chunk_id": 2, "section_path": "退货政策 > 无理由退货", "question": "无理由退货的条件", "answer": "签收 7 天内、商品完好可退。", "score": 0.42}], "must_keep": ["拆封后", "K1"], "must_drop": []}
  {"question": "R2 跑鞋 42 码偏大还是偏小，张三收", "chunks": null, "must_keep": ["R2", "42 码"], "must_drop": ["张三"]}
  {"question": "C8 杯子装热水能保温几个小时？？", "chunks": null, "must_keep": ["C8", "保温"], "must_drop": ["？？"]}
  {"question": "退款都三天了还没到 你们是不是骗子", "chunks": [{"chunk_id": 3, "section_path": "退货政策 > 退款时效", "question": "退款多久到账", "answer": "商家确认收货后 1 到 3 个工作日原路退款。", "score": 0.55}], "must_keep": ["退款"], "must_drop": ["骗子"]}
  {"question": "X5 耳机能连两台手机吗 地址北京市朝阳区xx路", "chunks": null, "must_keep": ["X5"], "must_drop": ["朝阳区"]}
  {"question": "能开专票吗 公司报销要用", "chunks": null, "must_keep": ["专票"], "must_drop": []}
  {"question": "S20 扫地机器人能爬多高的门槛", "chunks": null, "must_keep": ["S20", "门槛"], "must_drop": []}
  {"question": "唉 能不能货到付款啊", "chunks": null, "must_keep": ["货到付款"], "must_drop": ["唉"]}
  ```

  通过条件：JSON 解析率 100%；每条 `normalized_question` 包含全部 `must_keep`、不含任何 `must_drop`、以"？"结尾、≤ 60 字；`suggested_answer` 以"（待核实）"开头；有 `chunks` 的样例，答案中的数字都出现在 chunks 的 `answer` 中（与 `check_summary` 的数字规则相同：4 位以上数字串不检查之外，1–3 位数字也要求出现在片段中）。

- [ ] **Step 2: 写样例 `evals/review_dedup_samples.jsonl`（15 条）**

  字段：`question`、`candidates`（文本列表）、`expected`（序号或 null）、`kind`（同义 / 近义不同 / 不同品类）。

  ```jsonl
  {"question": "保温杯可以放进洗碗机清洗吗？", "candidates": ["保温杯能用洗碗机洗吗？"], "expected": 1, "kind": "同义"}
  {"question": "X3 Pro 耳机可以戴着游泳吗？", "candidates": ["X3 Pro 耳机防水等级是多少？", "X3 Pro 耳机支持游泳时佩戴吗？"], "expected": 2, "kind": "同义"}
  {"question": "退款多久到账？", "candidates": ["退款一般几天能退回来？"], "expected": 1, "kind": "同义"}
  {"question": "可以开增值税专用发票吗？", "candidates": ["能开专票吗？"], "expected": 1, "kind": "同义"}
  {"question": "S10 Max 扫地机器人能接入米家 App 吗？", "candidates": ["S10 Max 支持小米米家联动吗？"], "expected": 1, "kind": "同义"}
  {"question": "签收超过 7 天还能无理由退货吗？", "candidates": ["签收 7 天内可以无理由退货吗？"], "expected": null, "kind": "近义不同"}
  {"question": "拆封后的手机壳能退吗？", "candidates": ["未拆封的手机壳能退吗？"], "expected": null, "kind": "近义不同"}
  {"question": "退款多久到账？", "candidates": ["怎么申请退款？"], "expected": null, "kind": "近义不同"}
  {"question": "C8 保温杯保温时间多长？", "candidates": ["C8 保温杯容量多大？"], "expected": null, "kind": "近义不同"}
  {"question": "T3 Pro 牙刷头多久更换一次？", "candidates": ["T3 Pro 牙刷充一次电能用多久？"], "expected": null, "kind": "近义不同"}
  {"question": "W1 羊毛衫可以机洗吗？", "candidates": ["R1 运动鞋可以机洗吗？"], "expected": null, "kind": "不同品类"}
  {"question": "X5 耳机能同时连接两台手机吗？", "candidates": ["X3 耳机能同时连接两台手机吗？"], "expected": null, "kind": "不同品类"}
  {"question": "L2 台灯有蓝光危害吗？", "candidates": ["L1 Pro 台灯有蓝光危害吗？", "L2 台灯长时间使用伤眼睛吗？"], "expected": 2, "kind": "不同品类"}
  {"question": "K1 手机壳适配哪些机型？", "candidates": ["K2 手机壳适配哪些机型？"], "expected": null, "kind": "不同品类"}
  {"question": "支持货到付款吗？", "candidates": ["可以货到付款吗？", "支持分期付款吗？"], "expected": 1, "kind": "同义"}
  ```

  通过条件：解析率 100%，15 条判定全部与 `expected` 一致。

- [ ] **Step 3: 写两个评估脚本**

  结构仿照 `evals/run_dedup_eval.py`：读样例 → 并发 3 调用工厂 → 逐条打印 `✅/❌` 和原因 → 汇总 → 全部通过退出码 0，否则 1。`run_normalize_eval.py` 用 `pipeline.format_chunks(sample["chunks"])` 组装输入；`run_review_dedup_eval.py` 用 `pipeline.format_candidates([(0, c) for c in sample["candidates"]])`。

- [ ] **Step 4（Claude）: 运行并迭代 Prompt**

  Run: `uv run python evals/run_normalize_eval.py`、`uv run python evals/run_review_dedup_eval.py`（真实上游）。
  不通过时：Claude 分析失败样例，给 Codex 具体的 Prompt 修改要求（不改样例的期望值，除非样例本身标错，并在 dev-notes 写明原因），重跑直到两个脚本退出码均为 0。再用查重样例的问题对比 `REVIEW_DEDUP_MIN_SCORE=0.75`：用真实嵌入算每条样例中 `expected` 候选的余弦相似度；如果有同义候选低于 0.75，按最低值向下取到 0.05 的整数倍，改常量并在 dev-notes 记录。

- [ ] **Step 5: 提交**

  ```bash
  git add evals/normalize_samples.jsonl evals/review_dedup_samples.jsonl evals/run_normalize_eval.py \
    evals/run_review_dedup_eval.py app/prompts.py app/config.py
  git commit -m "test(ch09): sample sets for flywheel normalize and dedup prompts"
  ```

---

### Task 10: 审核 API 与入库

**Files:**
- Create: `app/api/review_queue.py`
- Modify: `app/main.py`（`include_router`）、`app/knowledge/ingest.py`（`FLYWHEEL_SOURCE`、`--rebuild` 保留）、`app/knowledge/retrieval.py`（`build_filter` 排除飞轮块）、`scripts/build_kb.py`（帮助文字）
- Test: `tests/test_review_queue_api.py`、`tests/test_retrieval.py`（或现有 `build_filter` 测试所在文件）、`tests/test_ingest.py`

**Interfaces:**
- Consumes: 任务 2 仓储；`knowledge.insert_chunks`、`NewChunk`；`vectorize_pending`。
- Produces:
  - `ingest.FLYWHEEL_SOURCE = "飞轮补充"`；`FLYWHEEL_CONTENT_TYPE = "flywheel"`
  - `GET /api/review-queue?status=`、`GET /api/review-queue/{id}`、`POST /api/review-queue/{id}/approve`、`POST /api/review-queue/{id}/reject`
  - `build_filter(category, exclude_mined)`：`exclude_mined=True` 时过滤 `content_type not in ["mined", "flywheel"]`（评估集的相关性标注只覆盖文档来源，计划阶段补充，已同步 spec）

- [ ] **Step 1: 写失败测试 `tests/test_review_queue_api.py`**

  ```python
  from sqlalchemy import select

  from app.api import review_queue as api
  from app.db.models import KnowledgeChunk
  from app.repositories import conversations, low_confidence, review_queue

  SNAP = [{"chunk_id": 1, "section_path": "p", "question": "q", "answer": "a", "score": 0.3}]


  async def _seed(db, raws=("杯子能扔洗碗机吗", "保温杯洗碗机能洗不")):
      async with db() as s:
          c = await conversations.create(s, "u1")
          item = await review_queue.add(s, normalized_question="保温杯可以用洗碗机清洗吗？",
                                        suggested_answer="（待核实）不建议。")
          for raw in raws:
              row = await low_confidence.add(s, conversation_id=c.id, raw_question=raw, source="retrieval_low_conf",
                                             reason="r", retrieved_chunks=SNAP)
              await low_confidence.set_matched(s, row.id, item.id)
          await s.commit()
          return item.id


  async def test_list_and_detail(db, client):
      rid = await _seed(db)
      r = await client.get("/api/review-queue", params={"status": "待审"})
      assert r.status_code == 200 and r.json()[0]["id"] == rid and r.json()[0]["occurrence_count"] == 1
      d = (await client.get(f"/api/review-queue/{rid}")).json()
      assert [x["raw_question"] for x in d["sources"]] == ["杯子能扔洗碗机吗", "保温杯洗碗机能洗不"]
      assert d["sources"][0]["retrieved_chunks"] == SNAP
      assert (await client.get("/api/review-queue/999999")).status_code == 404


  async def test_approve_writes_flywheel_chunk_and_vectorizes(db, client, monkeypatch):
      calls = []

      async def fake_vectorize():
          calls.append(1)
          return 1

      monkeypatch.setattr(api, "vectorize_pending", fake_vectorize)
      rid = await _seed(db)
      r = await client.post(f"/api/review-queue/{rid}/approve",
                            json={"approved_answer": "  不建议放入洗碗机，请手洗。 ", "product_category": "保温杯"})
      assert r.status_code == 200 and r.json()["vectorized"] == 1 and calls == [1]
      async with db() as s:
          chunk = (await s.execute(select(KnowledgeChunk))).scalar_one()
          item = await review_queue.get(s, rid)
      assert (chunk.category, chunk.section_path, chunk.content_type, chunk.answer) == (
          "保温杯", "飞轮补充 > 保温杯", "flywheel", "不建议放入洗碗机，请手洗。")
      assert chunk.questions.split("\n") == ["保温杯可以用洗碗机清洗吗？", "杯子能扔洗碗机吗", "保温杯洗碗机能洗不"]
      assert (item.review_status, item.approved_answer) == ("通过", "不建议放入洗碗机，请手洗。")
      assert r.json()["chunk_id"] == chunk.id


  async def test_approve_general_category_path(db, client, monkeypatch):
      async def fake_vectorize():
          return 1
      monkeypatch.setattr(api, "vectorize_pending", fake_vectorize)
      rid = await _seed(db, raws=("a", "b", "c", "d", "a"))
      await client.post(f"/api/review-queue/{rid}/approve", json={"approved_answer": "答", "product_category": "通用"})
      async with db() as s:
          chunk = (await s.execute(select(KnowledgeChunk))).scalar_one()
      assert chunk.section_path == "飞轮补充"
      assert chunk.questions.split("\n") == ["保温杯可以用洗碗机清洗吗？", "a", "b", "c"]


  async def test_approve_twice_conflicts(db, client, monkeypatch):
      async def fake_vectorize():
          return 1
      monkeypatch.setattr(api, "vectorize_pending", fake_vectorize)
      rid = await _seed(db)
      body = {"approved_answer": "答", "product_category": "通用"}
      assert (await client.post(f"/api/review-queue/{rid}/approve", json=body)).status_code == 200
      assert (await client.post(f"/api/review-queue/{rid}/approve", json=body)).status_code == 409
      async with db() as s:
          assert len((await s.execute(select(KnowledgeChunk))).scalars().all()) == 1


  async def test_approve_validation(db, client):
      rid = await _seed(db)
      assert (await client.post(f"/api/review-queue/{rid}/approve",
                                json={"approved_answer": "  ", "product_category": "通用"})).status_code == 422
      assert (await client.post(f"/api/review-queue/{rid}/approve",
                                json={"approved_answer": "a", "product_category": "冰箱"})).status_code == 422
      assert (await client.post("/api/review-queue/999999/approve",
                                json={"approved_answer": "a", "product_category": "通用"})).status_code == 404


  async def test_vectorize_failure_returns_502_but_keeps_status(db, client, monkeypatch):
      async def boom():
          raise RuntimeError("milvus down")
      monkeypatch.setattr(api, "vectorize_pending", boom)
      rid = await _seed(db)
      r = await client.post(f"/api/review-queue/{rid}/approve", json={"approved_answer": "答", "product_category": "通用"})
      assert r.status_code == 502
      async with db() as s:
          assert (await review_queue.get(s, rid)).review_status == "通过"


  async def test_reject(db, client):
      rid = await _seed(db)
      assert (await client.post(f"/api/review-queue/{rid}/reject")).status_code == 200
      assert (await client.post(f"/api/review-queue/{rid}/reject")).status_code == 409
      async with db() as s:
          assert (await review_queue.get(s, rid)).review_status == "驳回"
  ```

  另改两处现有测试：
  - `tests/test_retrieval.py` 第 59–60 行的期望值改为 `'content_type not in ["mined", "flywheel"]'` 和 `'product_category in ["台灯", "通用"] and content_type not in ["mined", "flywheel"]'`。
  - `tests/test_ingest.py` 的 `test_ingest_all_rebuild_keeps_mined_chunks` 再插入一行 `knowledge.NewChunk("保温杯", "保温杯能进洗碗机吗", "不能。", "飞轮补充 > 保温杯", "flywheel", False)`，断言重建后 `content_type == "flywheel"` 的行仍为 1 行，并已重新向量化（与该测试对 mined 行的断言一致）。

- [ ] **Step 2: 运行，确认失败** → FAIL。

- [ ] **Step 3: 实现**

  `app/knowledge/ingest.py`：加 `FLYWHEEL_SOURCE = "飞轮补充"`、`FLYWHEEL_CONTENT_TYPE = "flywheel"`；`load_doc_sources` 中跳过的标题加入 `FLYWHEEL_SOURCE`；`ingest_all` 的 rebuild 分支在 `mark_pending_by_content_type(s, "mined")` 之后加 `await knowledge.mark_pending_by_content_type(s, FLYWHEEL_CONTENT_TYPE)`，注释改为"挖掘块和飞轮块保留 MySQL 行……"。`scripts/build_kb.py` 的 `--rebuild` 帮助文字改为"mined 块和 flywheel 块保留并重新向量化"。

  `app/knowledge/retrieval.py` 的 `build_filter`：

  ```python
      if exclude_mined:
          # 评估集只标注文档来源，挖掘块和飞轮块不参与评估。
          parts.append('content_type not in ["mined", "flywheel"]')
  ```

  `app/api/review_queue.py`：

  ```python
  import logging
  from datetime import datetime
  from typing import Any, Literal

  from fastapi import APIRouter, HTTPException
  from pydantic import BaseModel, field_validator

  from app.config import GENERAL_CATEGORY, PRODUCT_CATEGORIES
  from app.db.engine import get_sessionmaker
  from app.knowledge.chunking import PATH_SEP
  from app.knowledge.ingest import FLYWHEEL_CONTENT_TYPE, FLYWHEEL_SOURCE
  from app.knowledge.vectorize import vectorize_pending
  from app.repositories import knowledge, low_confidence, review_queue

  logger = logging.getLogger(__name__)
  router = APIRouter()
  EXTRA_QUESTIONS = 3
  NOT_FOUND = {"code": "review_not_found", "message": "待审问题不存在"}
  NOT_PENDING = {"code": "review_not_pending", "message": "该问题已审核"}
  CATEGORIES = (*PRODUCT_CATEGORIES, GENERAL_CATEGORY)


  class ReviewOut(BaseModel):
      id: int
      normalized_question: str
      ai_suggested_answer: str | None
      occurrence_count: int
      review_status: str
      approved_answer: str | None
      created_at: datetime
      updated_at: datetime


  class SourceOut(BaseModel):
      id: int
      raw_question: str
      source: str
      reason: str | None
      created_at: datetime
      retrieved_chunks: list[dict[str, Any]] | None


  class ReviewDetail(ReviewOut):
      sources: list[SourceOut]


  class ApproveRequest(BaseModel):
      approved_answer: str
      product_category: str = GENERAL_CATEGORY

      @field_validator("approved_answer")
      @classmethod
      def not_blank(cls, v: str) -> str:
          v = v.strip()
          if not v:
              raise ValueError("核准答案不能为空")
          return v

      @field_validator("product_category")
      @classmethod
      def known_category(cls, v: str) -> str:
          if v not in CATEGORIES:
              raise ValueError("品类不在可选列表中")
          return v


  def _out(row) -> dict:
      return {k: getattr(row, k) for k in ReviewOut.model_fields}


  @router.get("/api/review-queue", response_model=list[ReviewOut])
  async def list_reviews(status: Literal["待审", "通过", "驳回"] | None = None) -> list[dict]:
      async with get_sessionmaker()() as s:
          return [_out(r) for r in await review_queue.list_items(s, status)]


  @router.get("/api/review-queue/{review_id}", response_model=ReviewDetail)
  async def review_detail(review_id: int) -> dict:
      async with get_sessionmaker()() as s:
          row = await review_queue.get(s, review_id)
          if row is None:
              raise HTTPException(404, detail=NOT_FOUND)
          sources = await low_confidence.list_for_review(s, review_id)
      return {**_out(row), "sources": [{k: getattr(x, k) for k in SourceOut.model_fields} for x in sources]}


  def _questions(normalized: str, raws: list[str]) -> str:
      extra = [q for q in dict.fromkeys(r.strip() for r in raws) if q and q != normalized][:EXTRA_QUESTIONS]
      return "\n".join([normalized, *extra])


  @router.post("/api/review-queue/{review_id}/approve")
  async def approve(review_id: int, req: ApproveRequest) -> dict:
      async with get_sessionmaker()() as s:
          row = await review_queue.get(s, review_id, for_update=True)
          if row is None:
              raise HTTPException(404, detail=NOT_FOUND)
          if row.review_status != "待审":
              raise HTTPException(409, detail=NOT_PENDING)
          raws = [x.raw_question for x in await low_confidence.list_for_review(s, review_id)]
          path = FLYWHEEL_SOURCE if req.product_category == GENERAL_CATEGORY else \
              f"{FLYWHEEL_SOURCE}{PATH_SEP}{req.product_category}"
          [chunk] = await knowledge.insert_chunks(s, [knowledge.NewChunk(
              req.product_category, _questions(row.normalized_question, raws), req.approved_answer,
              path, FLYWHEEL_CONTENT_TYPE, False)])
          await review_queue.set_status(s, review_id, "通过", approved_answer=req.approved_answer)
          await s.commit()
          chunk_id = chunk.id
      try:
          # 同步向量化，返回后同一个问题马上能检索到。
          vectorized = await vectorize_pending()
      except Exception:
          logger.exception("review_vectorize_failed review=%s chunk=%s", review_id, chunk_id)
          raise HTTPException(502, detail={"code": "vectorize_failed",
                                           "message": "已通过，向量化失败，请运行 build_kb.py 补齐"})
      logger.info("review approve review=%s chunk=%s vectorized=%s", review_id, chunk_id, vectorized)
      return {"chunk_id": chunk_id, "vectorized": vectorized}


  @router.post("/api/review-queue/{review_id}/reject")
  async def reject(review_id: int) -> dict:
      async with get_sessionmaker()() as s:
          row = await review_queue.get(s, review_id, for_update=True)
          if row is None:
              raise HTTPException(404, detail=NOT_FOUND)
          if row.review_status != "待审":
              raise HTTPException(409, detail=NOT_PENDING)
          await review_queue.set_status(s, review_id, "驳回")
          await s.commit()
      return {"id": review_id, "review_status": "驳回"}
  ```

  `knowledge.NewChunk` 的字段顺序：`(category, questions, answer, section_path, content_type, is_key_clause)`；`PATH_SEP = " > "`。

  `app/main.py`：`app.include_router(review_queue.router)`。

- [ ] **Step 4: 运行测试** → PASS；`uv run pytest -q` → 全部通过。

- [ ] **Step 5: 提交**

  ```bash
  git add app/api/review_queue.py app/main.py app/knowledge/ingest.py app/knowledge/retrieval.py scripts/build_kb.py tests/
  git commit -m "feat(ch09): review queue API writes approved answers into the knowledge base"
  ```

---

### Task 11: 👎 接后端与 `done.message_id`

**Files:**
- Create: `app/api/feedback.py`
- Modify: `app/graph/nodes/finalize.py`、`app/api/chat.py`、`app/main.py`
- Test: `tests/test_feedback_api.py`、`tests/test_chat_api.py`

**Interfaces:**
- Consumes: `record_low_confidence(...)`、`low_confidence.find_by_reason`、`thread_config`、`msg_id`、`get_graph`。
- Produces:
  - `finalize` 写库后 `events.emit("saved", {"message_id": reply_row.id})`
  - SSE `done`：`{"finish_reason": "stop", "message_id": <int>}`；中断或失败时没有 `message_id`
  - `POST /api/feedback`，请求体 `{conversation_id, message_id, rating, user_id}`
  - `feedback.FEEDBACK_REASON = "用户反馈未解决（回复 msg-{message_id}）"`
  - `feedback.find_snapshot(graph, conversation_id, message_id) -> list[dict] | None`

- [ ] **Step 1: 写失败测试**

  `tests/test_feedback_api.py`：

  ```python
  from sqlalchemy import select

  from app.db.models import LowConfidenceQuestion
  from app.repositories import conversations, messages
  from app.repositories.messages import NewMessage


  async def _turn(db, user="X3 Pro 能游泳吗", reply="抱歉，暂时无法回答。"):
      async with db() as s:
          c = await conversations.create(s, "u1")
          u, a = await messages.add_turn(s, c.id, [NewMessage("user", user), NewMessage("assistant", reply)])
          await s.commit()
          return c.id, u.id, a.id


  async def test_up_returns_204(db, client):
      cid, _, aid = await _turn(db)
      r = await client.post("/api/feedback", json={"conversation_id": cid, "message_id": aid, "rating": "up",
                                                   "user_id": "u1"})
      assert r.status_code == 204


  async def test_down_pools_once_with_null_snapshot_without_checkpoint(db, client):
      cid, _, aid = await _turn(db)
      body = {"conversation_id": cid, "message_id": aid, "rating": "down", "user_id": "u1"}
      r1 = await client.post("/api/feedback", json=body)
      r2 = await client.post("/api/feedback", json=body)
      assert (r1.status_code, r1.json()["duplicate"]) == (201, False)
      assert (r2.status_code, r2.json()["duplicate"]) == (200, True)
      async with db() as s:
          rows = (await s.execute(select(LowConfidenceQuestion))).scalars().all()
      assert len(rows) == 1
      assert (rows[0].raw_question, rows[0].source, rows[0].retrieved_chunks) == (
          "X3 Pro 能游泳吗", "user_feedback", None)
      assert rows[0].reason == f"用户反馈未解决（回复 msg-{aid}）"


  async def test_down_errors(db, client):
      cid, uid, aid = await _turn(db)
      base = {"conversation_id": cid, "rating": "down", "user_id": "u1"}
      assert (await client.post("/api/feedback", json={**base, "message_id": 999999})).status_code == 404
      assert (await client.post("/api/feedback", json={**base, "message_id": uid})).status_code == 422
      other, _, _ = await _turn(db)
      assert (await client.post("/api/feedback", json={**base, "conversation_id": other,
                                                       "message_id": aid})).status_code == 404
      assert (await client.post("/api/feedback", json={**base, "message_id": aid, "user_id": "u2"})).status_code == 404
  ```

  另加一个经真实图的测试（放在 `tests/test_graph.py`，复用该文件的 knowledge 轮辅助函数和 fake retrieve）：跑一轮被闸拦下的 knowledge 问题 → 从 `done` 取 `message_id` → 再跑一轮闲聊（`retrieval` 被重置为 None）→ 对第 1 轮回复点 👎 → 落池行的 `retrieved_chunks` 等于第 1 轮的快照（分数列表与 fake retrieve 一致）；对第 2 轮回复点 👎 → `retrieved_chunks` 为 None。

  `tests/test_chat_api.py` 加：一轮正常对话的 `done` 事件含整数 `message_id`，且等于 `messages` 表中该轮助手消息的 id；一轮 `order_picker` 中断的 `done` 不含 `message_id`。

- [ ] **Step 2: 运行，确认失败** → FAIL（404 Not Found 路由不存在等）。

- [ ] **Step 3: 实现**

  `app/graph/nodes/finalize.py`：`await s.commit()` 之后加 `events.emit("saved", {"message_id": reply_row.id})`。

  `app/api/chat.py` 的 `stream_graph`：

  ```python
      saved_id = None
      ...
              if mode == "custom":
                  name, data = chunk
                  if name == "saved":
                      saved_id = data["message_id"]
                      continue
                  yield sse(name, data)
      ...
      done = {"finish_reason": "interrupted" if interrupted else "stop"}
      if saved_id is not None and not interrupted:
          done["message_id"] = saved_id
      yield sse("done", done)
  ```

  `app/api/feedback.py`：

  ```python
  import logging
  from typing import Annotated, Literal

  from fastapi import APIRouter, Depends, HTTPException, Response
  from fastapi.responses import JSONResponse
  from pydantic import BaseModel
  from sqlalchemy import select

  from app.db.engine import get_sessionmaker
  from app.db.models import Message
  from app.graph.builder import get_graph, thread_config
  from app.repositories import conversations, low_confidence
  from app.schemas import UserId
  from app.services.grounding import record_low_confidence
  from app.services.history import msg_id

  logger = logging.getLogger(__name__)
  router = APIRouter()
  FEEDBACK_SOURCE = "user_feedback"
  FEEDBACK_REASON = "用户反馈未解决（回复 msg-{message_id}）"
  NOT_FOUND = {"code": "message_not_found", "message": "消息不存在"}
  NOT_REPLY = {"code": "not_a_reply", "message": "只能对客服回复反馈"}


  class FeedbackRequest(BaseModel):
      conversation_id: int
      message_id: int
      rating: Literal["up", "down"]
      user_id: UserId


  async def find_snapshot(graph, conversation_id: int, message_id: int) -> list[dict] | None:
      """回捞该回复所在轮的召回快照：取最后一条消息为该回复的最早 checkpoint。"""
      target, found = msg_id(message_id), None
      async for snap in graph.aget_state_history(thread_config(conversation_id)):
          msgs = snap.values.get("messages") or []
          if msgs and msgs[-1].id == target:
              found = snap.values.get("retrieval")
      return found


  @router.post("/api/feedback")
  async def feedback(req: FeedbackRequest, graph: Annotated[object, Depends(get_graph)]):
      async with get_sessionmaker()() as s:
          if await conversations.get_for_user(s, req.conversation_id, req.user_id) is None:
              raise HTTPException(404, detail=NOT_FOUND)
          reply = await s.get(Message, req.message_id)
          if reply is None or reply.conversation_id != req.conversation_id:
              raise HTTPException(404, detail=NOT_FOUND)
          if reply.role != "assistant":
              raise HTTPException(422, detail=NOT_REPLY)
          if req.rating == "up":
              return Response(status_code=204)
          reason = FEEDBACK_REASON.format(message_id=req.message_id)
          existing = await low_confidence.find_by_reason(s, conversation_id=req.conversation_id,
                                                         source=FEEDBACK_SOURCE, reason=reason)
          if existing is not None:
              return {"id": existing.id, "duplicate": True}
          question = (await s.execute(
              select(Message.content).where(Message.conversation_id == req.conversation_id,
                                            Message.role == "user", Message.id < req.message_id)
              .order_by(Message.id.desc()).limit(1))).scalar_one_or_none()
      if not question:
          raise HTTPException(422, detail=NOT_REPLY)
      try:
          snap = await find_snapshot(graph, req.conversation_id, req.message_id)
      except Exception:
          # 回捞失败不影响落池。
          logger.exception("feedback_snapshot_failed conversation=%s message=%s", req.conversation_id, req.message_id)
          snap = None
      lcq_id = await record_low_confidence(req.conversation_id, question, reason, source=FEEDBACK_SOURCE,
                                           retrieved_chunks=snap)
      if lcq_id is None:
          raise HTTPException(500, detail={"code": "feedback_failed", "message": "反馈保存失败，请稍后重试"})
      logger.info("feedback down conversation=%s message=%s lcq=%s snapshot=%s", req.conversation_id,
                  req.message_id, lcq_id, "-" if snap is None else len(snap))
      return JSONResponse(status_code=201, content={"id": lcq_id, "duplicate": False})
  ```

  `app/main.py`：`app.include_router(feedback.router)`。

- [ ] **Step 4: 运行测试** → PASS；`uv run pytest -q` → 全部通过（现有测试中断言完整 `done` 事件的地方需要加上 `message_id`；只改这些断言，不改其他意图）。

- [ ] **Step 5: 提交**

  ```bash
  git add app/api/feedback.py app/api/chat.py app/graph/nodes/finalize.py app/main.py tests/
  git commit -m "feat(ch09): thumbs-down feedback pools the turn with its retrieval snapshot"
  ```

---

### Task 12: 自动化评估流水线与趋势

**Files:**
- Modify: `evals/run_rag_eval.py`（把 `run_eval` 中的采集部分提取为 `collect()`，行为不变）
- Create: `evals/eval_trend.py`（纯函数）、`evals/run_eval_pipeline.py`、`app/api/eval_runs.py`
- Modify: `app/main.py`
- Test: `tests/test_eval_trend.py`、`tests/test_eval_pipeline.py`、`tests/test_eval_runs_api.py`

**Interfaces:**
- Consumes: `eval_runs` 仓储；`rag_metrics.summarize`、`generation_summary`；`GATE_CONF_THRESHOLD`。
- Produces:
  - `run_rag_eval.collect(samples, *, strategies, gen_strategies, do_retrieval, do_generation, concurrency, write_cases) -> Collected`（`Collected(plans, scores, post_scores, rows, results, failures)`）
  - `eval_trend.METRIC_KEYS = ("recall_at_1","recall_at_3","recall_at_5","recall_at_10","mrr","faithfulness","false_refusal","d_refusal")`、`LOWER_IS_BETTER = {"false_refusal"}`
  - `eval_trend.build_metrics(scores, results, failures: int) -> dict`
  - `eval_trend.trend(runs: list[RunPoint], last: int = 10, tolerance: float = EVAL_DROP_TOLERANCE) -> Trend`（`RunPoint(id, created_at, triggered_by, dataset_size, metrics)`；`Trend(points, deltas: list[dict[str, float | None]], dropped: list[str])`）
  - `eval_trend.render_trend(t: Trend) -> str`
  - 常量 `EVAL_DROP_TOLERANCE = 0.02`（`app/config.py`）
  - `GET /api/eval-runs?limit=N` → `[{id, triggered_by, dataset_size, metrics, created_at}]`（升序）

- [ ] **Step 1: 写失败测试**

  `tests/test_eval_trend.py`：

  ```python
  from datetime import datetime, timedelta

  from evals.eval_trend import RunPoint, render_trend, trend

  T0 = datetime(2026, 10, 9, 10)


  def m(**kw):
      base = {"recall_at_1": 0.8, "recall_at_3": 0.9, "recall_at_5": 0.92, "recall_at_10": 0.95, "mrr": 0.85,
              "faithfulness": 0.97, "false_refusal": 0.06, "d_refusal": 0.9}
      return {**base, **kw}


  def test_trend_marks_drops_both_directions():
      runs = [RunPoint(1, T0, "手动", 300, m()), RunPoint(2, T0 + timedelta(hours=1), "定时", 300,
                                                         m(mrr=0.80, false_refusal=0.10, faithfulness=0.98))]
      t = trend(runs, tolerance=0.02)
      assert t.deltas[0] == {k: None for k in t.deltas[0]}
      assert round(t.deltas[1]["mrr"], 2) == -0.05
      assert t.dropped == ["mrr", "false_refusal"]


  def test_trend_only_compares_same_size():
      runs = [RunPoint(1, T0, "手动", 300, m()), RunPoint(2, T0 + timedelta(hours=1), "手动", 5, m(mrr=0.1)),
              RunPoint(3, T0 + timedelta(hours=2), "手动", 300, m(mrr=0.84))]
      t = trend(runs)
      assert [p.id for p in t.points] == [1, 3] and t.dropped == []


  def test_trend_last_n():
      runs = [RunPoint(i, T0 + timedelta(hours=i), "定时", 300, m()) for i in range(12)]
      assert [p.id for p in trend(runs, last=3).points] == [9, 10, 11]


  def test_render_trend():
      runs = [RunPoint(1, T0, "手动", 300, m()), RunPoint(2, T0 + timedelta(hours=1), "定时", 300, m(mrr=0.7))]
      text = render_trend(trend(runs))
      assert "↓" in text and "下滑指标：mrr" in text
  ```

  `tests/test_eval_pipeline.py`：

  ```python
  from evals import run_eval_pipeline as pipe
  from evals.rag_metrics import GenResult, RetrievalScore


  def test_build_metrics():
      scores = [RetrievalScore("A1", "A_policy", "easy", "hybrid_rerank", {1: 1.0, 3: 1.0, 5: 1.0, 10: 1.0}, 1.0),
                RetrievalScore("A2", "A_policy", "easy", "hybrid_rerank", {1: 0.0, 3: 1.0, 5: 1.0, 10: 1.0}, 0.5)]
      results = [GenResult("A1", "A_policy", "easy", "hybrid_rerank", "q", True, False, "a", [], True, [], ""),
                 GenResult("D1", "D_unanswerable", "easy", "hybrid_rerank", "q", True, True, "抱歉", [], None, [], "")]
      got = pipe.build_metrics(scores, results, failures=2)
      assert got["recall_at_1"] == 0.5 and got["mrr"] == 0.75 and got["faithfulness"] == 1.0
      assert got["d_refusal"] == 1.0 and got["false_refusal"] == 0.0 and got["failures"] == 2
      assert got["strategy"] == "hybrid_rerank" and "gate_conf_threshold" in got


  async def test_save_run_writes_row(db):
      run_id = await pipe.save_run("定时", 300, {"mrr": 0.8})
      from app.repositories import eval_runs
      async with db() as s:
          [row] = await eval_runs.list_recent(s, 5)
      assert (row.id, row.triggered_by, row.dataset_size, row.metrics) == (run_id, "定时", 300, {"mrr": 0.8})


  def test_stage_failure_detection():
      assert pipe.whole_stage_failed(scores=[], results=[1]) is True
      assert pipe.whole_stage_failed(scores=[1], results=[]) is True
      assert pipe.whole_stage_failed(scores=[1], results=[1]) is False
  ```

  （`build_metrics` 位于 `evals/eval_trend.py`，`run_eval_pipeline` 重新导出；测试按 Interfaces 中的名称导入。）

  `tests/test_eval_runs_api.py`：写 3 行后 `GET /api/eval-runs?limit=2` 返回最后 2 行、升序、`metrics` 原样。

- [ ] **Step 2: 运行，确认失败** → FAIL。

- [ ] **Step 3: 实现**

  `evals/run_rag_eval.py`：把 `run_eval` 中从 `semaphore = ...` 到 `write_faith_cases` 的部分移进：

  ```python
  @dataclass
  class Collected:
      plans: dict
      scores: list
      post_scores: list
      rows: list
      results: list
      failures: list[str]


  async def collect(samples, *, strategies, gen_strategies, do_retrieval: bool, do_generation: bool,
                    concurrency: int, write_cases: bool) -> Collected:
      ...  # 原代码，把 STRATEGIES 换成 strategies，把 args.gen_strategies 判断换成 gen_strategies，
           # 把 args.no_write 换成 not write_cases，把 args.concurrency 换成 concurrency
  ```

  `run_eval` 改为调用 `collect(samples, strategies=STRATEGIES, gen_strategies=STRATEGIES if args.gen_strategies == "all" else ("hybrid_rerank",), do_retrieval=..., do_generation=..., concurrency=args.concurrency, write_cases=not args.no_write)`，报告部分不变。现有 `run_rag_eval` 测试必须全部通过。

  `evals/eval_trend.py`：

  ```python
  """评估轮次的指标组装与趋势。纯函数。"""

  from dataclasses import dataclass
  from datetime import datetime

  from app.config import EVAL_DROP_TOLERANCE, GATE_CONF_THRESHOLD
  from evals.rag_metrics import generation_summary, summarize

  STRATEGY = "hybrid_rerank"
  METRIC_KEYS = ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "faithfulness",
                 "false_refusal", "d_refusal")
  LOWER_IS_BETTER = {"false_refusal"}


  def build_metrics(scores, results, failures: int) -> dict:
      retrieval = summarize(scores, "bucket")[(STRATEGY, "ALL")]
      gen = generation_summary(results)[STRATEGY]
      return {
          "strategy": STRATEGY,
          **{f"recall_at_{k}": round(retrieval[f"R@{k}"], 4) for k in (1, 3, 5, 10)},
          "mrr": round(retrieval["MRR"], 4),
          "faithfulness": round(gen["faithfulness"], 4),
          "false_refusal": round(gen["false_refusal"], 4),
          "d_refusal": round(gen["d_refusal"], 4),
          "judge_failed": gen["judge_failed"],
          "failures": failures,
          "gate_conf_threshold": GATE_CONF_THRESHOLD,
      }


  @dataclass(frozen=True)
  class RunPoint:
      id: int
      created_at: datetime
      triggered_by: str
      dataset_size: int
      metrics: dict


  @dataclass(frozen=True)
  class Trend:
      points: list[RunPoint]
      deltas: list[dict[str, float | None]]
      dropped: list[str]


  def trend(runs: list[RunPoint], last: int = 10, tolerance: float = EVAL_DROP_TOLERANCE) -> Trend:
      if not runs:
          return Trend([], [], [])
      size = runs[-1].dataset_size
      points = [r for r in runs if r.dataset_size == size][-last:]
      deltas = [{k: None for k in METRIC_KEYS}]
      for prev, cur in zip(points, points[1:]):
          deltas.append({k: (cur.metrics[k] - prev.metrics[k]) if k in cur.metrics and k in prev.metrics else None
                         for k in METRIC_KEYS})
      dropped = []
      if len(points) > 1:
          for k in METRIC_KEYS:
              d = deltas[-1][k]
              if d is not None and (d > tolerance if k in LOWER_IS_BETTER else d < -tolerance):
                  dropped.append(k)
      return Trend(points, deltas, dropped)


  def render_trend(t: Trend) -> str:
      if not t.points:
          return "eval_runs 中没有数据"
      head = "| 轮次 | 时间 | 触发 | 题数 | " + " | ".join(METRIC_KEYS) + " |"
      lines = [head, "|" + "---|" * (4 + len(METRIC_KEYS))]
      for p, d in zip(t.points, t.deltas):
          cells = []
          for k in METRIC_KEYS:
              v, dv = p.metrics.get(k), d[k]
              if v is None:
                  cells.append("-")
                  continue
              mark = ""
              if dv is not None:
                  worse = dv > EVAL_DROP_TOLERANCE if k in LOWER_IS_BETTER else dv < -EVAL_DROP_TOLERANCE
                  mark = f" ({dv:+.3f}{' ↓' if worse else ''})"
              cells.append(f"{v:.3f}{mark}")
          lines.append(f"| {p.id} | {p.created_at:%Y-%m-%d %H:%M} | {p.triggered_by} | {p.dataset_size} | "
                       + " | ".join(cells) + " |")
      lines.append("")
      lines.append("下滑指标：" + ("、".join(t.dropped) if t.dropped else "无"))
      return "\n".join(lines)
  ```

  `app/config.py` 加 `EVAL_DROP_TOLERANCE = 0.02`（注释：相对上一轮下降超过这个值时标为下滑）。

  `evals/run_eval_pipeline.py`：

  ```python
  """自动化评估流水线：生产策略 hybrid_rerank 跑全量评估集，每轮写一行 eval_runs。

  用法：
    uv run python evals/run_eval_pipeline.py --trigger 手动
    uv run python evals/run_eval_pipeline.py --trend [--last 10]
  crontab 示例（每天 03:00）：
    0 3 * * * cd /path/to/Aftersales-agent && uv run python evals/run_eval_pipeline.py --trigger 定时 >> log/eval_pipeline.log 2>&1
  """

  import argparse
  import asyncio
  import sys
  from pathlib import Path

  SCRIPT_DIR = Path(__file__).resolve().parent
  sys.path.insert(0, str(SCRIPT_DIR.parent))

  from app.db.engine import dispose_engine, get_sessionmaker
  from app.knowledge.milvus import close_milvus, ensure_collection
  from app.knowledge.rerank import close_rerank
  from app.repositories import eval_runs
  from evals.eval_trend import RunPoint, build_metrics, render_trend, trend
  from evals.rag_eval_set import known_source_keys, load_samples, validate
  from evals.run_rag_eval import collect

  STRATEGIES = ("hybrid_rerank",)


  def whole_stage_failed(*, scores, results) -> bool:
      return not scores or not results


  async def save_run(triggered_by: str, dataset_size: int, metrics: dict) -> int:
      async with get_sessionmaker()() as s:
          row = await eval_runs.add(s, triggered_by=triggered_by, dataset_size=dataset_size, metrics=metrics)
          await s.commit()
          return row.id


  async def load_points(limit: int) -> list[RunPoint]:
      async with get_sessionmaker()() as s:
          rows = await eval_runs.list_recent(s, limit)
      return [RunPoint(r.id, r.created_at, r.triggered_by, r.dataset_size, r.metrics) for r in rows]


  async def run_once(trigger: str, concurrency: int, limit: int | None) -> int:
      try:
          await ensure_collection()
          samples = load_samples()
          errors = validate(samples, set(await known_source_keys()), full=True)
          if errors:
              print("\n".join(errors))
              return 1
          if limit is not None:
              samples = samples[:limit]
          got = await collect(samples, strategies=STRATEGIES, gen_strategies=STRATEGIES, do_retrieval=True,
                              do_generation=True, concurrency=concurrency, write_cases=True)
          if whole_stage_failed(scores=got.scores, results=got.results):
              print("检索段或生成段整体失败，不写 eval_runs")
              return 1
          metrics = build_metrics(got.scores, got.results, len(got.failures))
          run_id = await save_run(trigger, len(samples), metrics)
          print(f"eval_runs 新增第 {run_id} 轮：{metrics}")
          print(render_trend(trend(await load_points(50))))
          return 1 if got.failures else 0
      finally:
          try:
              await close_milvus()
          finally:
              try:
                  await close_rerank()
              finally:
                  await dispose_engine()


  async def show_trend(last: int) -> int:
      try:
          print(render_trend(trend(await load_points(max(last * 5, 50)), last=last)))
          return 0
      finally:
          await dispose_engine()


  def main() -> int:
      parser = argparse.ArgumentParser(description="自动化评估流水线。正常运行会调用上游模型。")
      parser.add_argument("--trigger", choices=("定时", "手动"), default="手动")
      parser.add_argument("--concurrency", type=int, default=3)
      parser.add_argument("--limit", type=int, help="只用于调试")
      parser.add_argument("--trend", action="store_true")
      parser.add_argument("--last", type=int, default=10)
      args = parser.parse_args()
      if args.trend:
          return asyncio.run(show_trend(args.last))
      return asyncio.run(run_once(args.trigger, args.concurrency, args.limit))


  if __name__ == "__main__":
      sys.exit(main())
  ```

  `app/api/eval_runs.py`：

  ```python
  from datetime import datetime
  from typing import Any

  from fastapi import APIRouter, Query
  from pydantic import BaseModel

  from app.db.engine import get_sessionmaker
  from app.repositories import eval_runs

  router = APIRouter()


  class EvalRunOut(BaseModel):
      id: int
      triggered_by: str
      dataset_size: int
      metrics: dict[str, Any]
      created_at: datetime


  @router.get("/api/eval-runs", response_model=list[EvalRunOut])
  async def list_eval_runs(limit: int = Query(30, ge=1, le=200)) -> list[dict]:
      async with get_sessionmaker()() as s:
          rows = await eval_runs.list_recent(s, limit)
      return [{k: getattr(r, k) for k in EvalRunOut.model_fields} for r in rows]
  ```

  `app/main.py`：`app.include_router(eval_runs.router)`（模块名与仓储同名，导入时写 `from app.api import eval_runs as eval_runs_api`）。

- [ ] **Step 4: 运行测试** → PASS；`uv run pytest -q` → 全部通过。

- [ ] **Step 5（Claude）: 冒烟**

  Run: `uv run python evals/run_eval_pipeline.py --trigger 手动 --limit 5`（真实上游），确认写入一行；`--trend` 能打印。冒烟行的 `dataset_size=5`，不影响 300 题的趋势。

- [ ] **Step 6: 提交**

  ```bash
  git add evals/run_rag_eval.py evals/eval_trend.py evals/run_eval_pipeline.py app/api/eval_runs.py app/main.py \
    app/config.py tests/
  git commit -m "feat(ch09): scheduled eval pipeline writes eval_runs with trend report"
  ```

---

### Task 13: 前端（Vibe Coding，不走 TDD 和 code review）

**Files:**
- Create: `app/web/review_queue.html`、`app/web/eval_runs.html`
- Modify: `app/api/web.py`（`/admin/review-queue`、`/admin/eval-runs`）、`app/web/index.html`

- [ ] **Step 1（Claude → Codex）: 按用户已描述的效果下发任务**

  1. `/admin/review-queue`：风格沿用 `app/web/faith_cases.html`。顶部状态筛选（待审 / 通过 / 驳回 / 全部）。列表列：标准化问题、出现次数、示例答案、状态。每行可展开详情：归并进来的用户原话（时间、入口 `source` 的中文名：检索证据低 / 自评不足 / 用户反馈）以及每条原话的召回片段（`section_path`、问、答、分数，分数保留 2 位）；快照为空时显示"该轮没有走检索"。`待审` 行有"通过"和"驳回"：通过时弹出表单，答案默认填示例答案并去掉开头"（待核实）"，品类下拉（8 个品类 + 通用，默认通用），提交调用 approve；409、422、502 显示接口返回的 `message`。
  2. `/admin/eval-runs`：调用 `GET /api/eval-runs?limit=30`，只画与最新一轮 `dataset_size` 相同的轮次。每个指标一条折线（R@1/3/5/10、MRR、Faithfulness、误拒率、D 拒答率），横轴为时间。最新一轮相对上一轮下降超过 0.02 的指标（误拒率为上升）标红并在顶部列出。不引入新的前端依赖，用内联 SVG 画线。
  3. 聊天页 `index.html`：只在 `done` 带 `message_id` 时显示 👍/👎；点 👎 调 `POST /api/feedback`（`conversation_id`=当前会话、`message_id`、`rating`、`user_id`），成功显示"已反馈，我们会改进"，失败显示"反馈失败"；👍 同样调用接口。保留现有 localStorage 记录。

- [ ] **Step 2（Claude）: 在浏览器中检查效果**，把结果描述给用户，用户提出修改时再转给 Codex。

- [ ] **Step 3: 提交**

  ```bash
  git add app/web app/api/web.py
  git commit -m "feat(ch09): review queue and eval trend admin pages, feedback wiring in chat"
  ```

---

### Task 14: 验收脚本、文档、两轮评估

**Files:**
- Create: `scripts/demo9.sh`
- Modify: `CLAUDE.md`、`docs/superpowers/specs/2026-10-09-ch09-observability-flywheel-design.md`（状态）

- [ ] **Step 1（Codex）: 写 `scripts/demo9.sh <日志目录>`**

  结构仿照 `scripts/demo8.sh`（`port_open`、`kill_wait`、`trap cleanup`、`fail`）。前置检查：MySQL、Milvus 健康；`$LANGFUSE_BASE_URL/api/public/health` 返回 OK（`curl --noproxy '*'`）；端口 8000、8101、8102 空闲。启动两个 MCP Server 和服务（日志写 `$DIR`）。SSE 请求用 `curl --noproxy '*' -N`，用 `.venv/bin/python` 解析事件。

  1. 验收 2：新会话问 `C8 保温杯可以放进洗碗机清洗吗？`；断言回复等于 `GATE_FALLBACK_REPLY`；最多等 60 秒轮询 `GET /api/review-queue?status=待审`，找到一行，其详情 `sources` 中有该原话且 `retrieved_chunks` 非空；打印标准化问题、示例答案和片段。
  2. 验收 3：对该行 `POST approve`，答案 `C8 保温杯不能放进洗碗机清洗。洗碗机的高温和强力水流会损坏真空层和杯盖密封圈，请用软毛刷加温水手洗。`，品类 `保温杯`；新会话再问同一个问题；断言回复不是兜底话术且包含"洗碗机"和"手洗"。
  3. 验收 4：新会话问 `X3 Pro 耳机的续航是多久？`；取 `done.message_id`；`POST /api/feedback` rating=down；最多等 60 秒，断言待审队列中某行详情的 `sources` 含该原话、`source` 为 `user_feedback`。
  4. 验收 1：打印 `$LANGFUSE_BASE_URL/project/aftersales/sessions` 和本次 3 个会话 ID，提示在界面打开。
  5. 验收 5：等待 10 秒后运行 `uv run python scripts/intent_cost.py --days 1`。
  6. 验收 6：运行 `uv run python evals/run_eval_pipeline.py --trend`（两轮评估另行运行，见 Step 4）。

  脚本开头注释写明：需要先 `docker compose up -d --wait`、`docker compose -f docker-compose.langfuse.yml up -d --wait`、`build_kb`；验收问题 `C8 保温杯可以放进洗碗机清洗吗？` 在知识库中没有答案（Claude 在 Step 3 前用 `grep -rn 洗碗机 knowledge/docs` 和一次实际提问确认；如果知识库已有，换成同类的另一个问题并同步脚本）。

- [ ] **Step 2（Codex）: 更新 `CLAUDE.md`**（spec 第 13 节）：项目状态加 ch09 一段；常用命令加 `docker compose -f docker-compose.langfuse.yml up -d --wait`、`run_gate_calibration.py`、`run_flywheel.py`、`intent_cost.py`、`run_eval_pipeline.py`（含 `--trend`）、`run_normalize_eval.py`、`run_review_dedup_eval.py`、`demo9.sh`；架构图加 `graph → observability`、`services.grounding → flywheel.runner → flywheel.pipeline`、`api.review_queue → knowledge.vectorize`；模块表加 `app/observability.py`、`app/flywheel/`、`app/services/confidence.py`、3 个新 API；设计约束加：`schema_ch09.sql` 逐字保存；`evidence_confidence` 参数来自校准报告；飞轮单 worker 串行、失败留 NULL 由补跑脚本处理；`flywheel` 块在 `--rebuild` 时保留、评估时与 `mined` 一起排除；测试默认不启动 Runner、不连 Langfuse（`_no_langfuse`、`_isolate_flywheel`）；`done` 只在成功轮带 `message_id`；环境变量表加 Langfuse 3 个变量（`LANGFUSE_BASE_URL=http://127.0.0.1:3100`）；把置信度闸约束中的"Top-1 重排分 < `GATE_MIN_SCORE`"改为 `evidence_confidence`。

- [ ] **Step 3（Claude）: 运行验收**

  1. `bash scripts/demo9.sh /tmp/ch09-demo`，全部通过。
  2. 在 Langfuse 界面打开 demo 的 trace，确认链路完整（验收 1），截图结论写 dev-notes。

- [ ] **Step 4（Claude）: 两轮评估**

  1. `uv run python evals/run_eval_pipeline.py --trigger 手动`（约 20 分钟）。
  2. 再运行一次。
  3. `uv run python evals/run_eval_pipeline.py --trend` 显示两轮 300 题的对比；打开 `/admin/eval-runs` 看折线。

- [ ] **Step 5: 全量测试与提交**

  Run: `uv run pytest -q` → 全部通过。

  ```bash
  git add scripts/demo9.sh CLAUDE.md docs/superpowers/specs/2026-10-09-ch09-observability-flywheel-design.md
  git commit -m "docs(ch09): demo9 acceptance script and CLAUDE.md update"
  ```
