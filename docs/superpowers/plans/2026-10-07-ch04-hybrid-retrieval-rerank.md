# ch04 混合检索、重排与评估体系 Implementation Plan

> **For agentic workers:** 本项目的执行方式由 `CLAUDE.md` 规定：Claude 把每个代码任务交给 Codex 实现，Codex 完成后由 Claude 审查 diff、运行测试、补记 dev-notes、提交并推送到 `ch04` 分支。标为"Claude 执行"的步骤由 Claude 直接完成。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `query_faq` 升级为"Query 理解 → dense + BM25 混合召回 → RRF → 重排 → 门槛 → 首尾排列"的管线；回答带引用编号，证据不足时自评拒答并入池；建 300 题评估集，对比 4 种检索策略并评 Faithfulness；前端支持点击引用和 👍/👎。

**Architecture:** Milvus 集合 `knowledge` 改为 6 个字段（新增 BM25 源文本 `text`、`sparse` 和两个过滤字段），MySQL 仍是正文权威来源。检索管线集中在 `app/knowledge/retrieval.retrieve(question, strategy)`，线上用 `hybrid_rerank`，评估用全部 4 种。聊天服务在工具执行后、第 2 次调用前，经 `app/services/grounding.py` 合并证据、统一编号、自评，并在不足时替换证据和入池。

**Tech Stack:** Python 3.12、uv、FastAPI、SQLAlchemy 2.x（asyncio）+ asyncmy、MySQL 8、Milvus v2.6.22（BM25 Function、`hybrid_search`、`RRFRanker`）、pymilvus 3.0.2 `AsyncMilvusClient`、langchain-openai、httpx（重排）、pytest + anyio。

**Spec:** `docs/superpowers/specs/2026-10-07-ch04-hybrid-retrieval-rerank-design.md`

## 执行方式（每个任务）

1. Claude 把"Global Constraints"一节和该任务的全文作为任务描述，按 `CLAUDE.md` 中的命令交给 Codex。
2. Codex 按步骤实现，运行该任务的测试命令。Codex 不执行 `git commit`。
3. Claude 审查 diff，核对 spec 和本计划，运行 `uv run pytest -q`。
4. 有问题时，Claude 把具体问题交给 Codex 重做，不直接改代码。
5. 通过后，Claude 补记 `dev-notes/ch04.md`，提交并推送到 `ch04`。

## Global Constraints

- 所有工作在 `ch04` 分支上进行。
- 依赖只通过 `uv add` 添加。本章唯一的依赖变化：`httpx` 从 dev 依赖移到运行时依赖（`uv add httpx`，再从 dev 组删除）。
- `Settings` 新增读取 2 个环境变量：`RERANK_API_KEY`（必填，`SecretStr`）、`RERANK_BASE_URL`（默认 `https://api.siliconflow.cn/v1`）。不硬编码密钥或地址。
- `db/schema.sql`、`db/schema_ch03.sql`、`db/schema_ch04.sql` 是用户 DDL，**逐字保存，不许修改**。ORM 只映射，不调用 `create_all`。
- 执行 `.sql` 文件时用 `conn.exec_driver_sql(语句)`，不用 `text()`。提交后读数据库默认值列前先 `await session.refresh(obj)`。测试引擎用 `poolclass=NullPool`。
- `knowledge_chunks` 有自引用外键。删除行之前，先把 `prev_chunk_id`、`next_chunk_id` 更新为 NULL。
- 所有自写的重试一律用 `app.retry.retry_async`（指数回退加抖动）。不许手写重试循环。
- Milvus 集合 `knowledge` 的字段固定为 6 个：`id`（INT64 主键，`auto_id=False`）、`vector`（FLOAT_VECTOR 1024，`AUTOINDEX` + `COSINE`）、`text`（VARCHAR，`max_length=16384`，`enable_analyzer=True`，`analyzer_params={"type": "chinese"}`）、`sparse`（SPARSE_FLOAT_VECTOR，BM25 Function 输出，`SPARSE_INVERTED_INDEX` + `BM25`）、`product_category`（VARCHAR 64）、`content_type`（VARCHAR 16）。集合一致性 `Strong`。
- 正文和元数据的权威来源是 MySQL。在线检索按 id 从 MySQL 读 `done` 行。
- 向量化文本和 BM25 源文本都只由 `app.knowledge.chunking.knowledge_text(category, questions, answer)` 生成。
- 品类全集 `PRODUCT_CATEGORIES = ("蓝牙耳机", "羊毛衫", "扫地机器人", "电动牙刷", "台灯", "保温杯", "运动鞋", "手机壳")`，`GENERAL_CATEGORY = "通用"`。
- 24 个规范型号（知识文档、词表、测试共用，写法逐字一致）：

  | 品类 | 型号 |
  |---|---|
  | 蓝牙耳机 | `X3`、`X3 Pro`、`X5` |
  | 羊毛衫 | `W1`、`W1 Plus`、`W2` |
  | 扫地机器人 | `S10`、`S10 Max`、`S20` |
  | 电动牙刷 | `T3`、`T3 Pro`、`T5` |
  | 台灯 | `L1`、`L1 Pro`、`L2` |
  | 保温杯 | `C5`、`C5 Plus`、`C8` |
  | 运动鞋 | `R1`、`R1 Pro`、`R2` |
  | 手机壳 | `K1`、`K1 Pro`、`K2` |

- 拒答句常量 `REFUSAL_PREFIX = "抱歉，这个问题我没有在知识库中找到可靠依据。"`，定义在 `app/prompts.py`，其他模块只引用它。
- LLM 结构化调用（改写器、自评器、裁判）一律用 `build_extract_model(settings)`（关闭思考）+ `with_structured_output(Schema, method="function_calling", include_raw=True)`，与 ch03 的抽取器相同。结果取 `result["parsed"]`，为 `None` 时视为失败。
- 第 2 次调用不绑定工具，工具结果后追加 `SystemMessage(TOOL_ROUND_CLOSING)`（沿用）。
- SSE 事件用 `ServerSentEvent(raw_data=json.dumps(..., ensure_ascii=False))`（沿用）。
- 离线单测不访问网络、真实嵌入、真实重排、上游模型和生产集合。autouse fixture 提供 `FakeEmbeddings`、`BlockedMilvus`、`BlockedReranker`、被拦截的 LLM 工厂。需要数据库的测试用 fixture `db`；需要 Milvus 的测试用 fixture `milvus`。连不上时直接失败，不跳过。测试中的改写器、自评器、裁判用 `RunnableLambda`。
- `knowledge/docs/` 下的文档不许出现"邮"字（ch03 验收依赖"邮费"只能靠语义或同义词召回）。
- 对外错误信息只用固定文案。完整异常用 `logger.exception` 写日志。
- 代码注释和文档用中文，ASD-STE100 风格。
- Codex 不执行 `git commit`、`git push`，不修改 `.env`、`CLAUDE.md`、`docs/`、`dev-notes/`、`db/*.sql`。`knowledge/` 下的数据文件和 `evals/*.jsonl` 只在任务明确要求时修改。

## Review Focus

1. **模型写出越界的引用编号**（10 条证据却写 `[12]`）：服务端只记日志，本轮正常完成并写库；前端把越界编号显示为普通文字。→ Task 8 的 `test_out_of_range_citation_is_logged_not_blocked`。
2. **一轮中两次 `query_faq` 召回同一个块**：全局编号连续，重复块只出现一次，第 2 个工具消息中的编号接着第 1 个。→ Task 7 的 `test_collect_evidence_dedupes_and_numbers_across_calls`、Task 8 的 `test_two_faq_calls_number_continuously`。
3. **用户写小写或连写的型号**（`x3pro续航`、`X3-PRO`）：送入 BM25 前改写为 `X3 Pro`，并且真实 BM25 能命中 `X3 Pro` 的块，不命中 `X3` 的块排在前面。→ Task 4 的 `test_normalize_models_variants`、Task 6 的 `test_bm25_hits_normalized_model_before_sibling`。
4. **问题带品类、答案在通用政策中**（"耳机能七天无理由退吗"）：品类过滤保留 `通用` 块。→ Task 2 的 `test_category_filter_keeps_general`。
5. **重排接口返回 4xx**（密钥错误）：不重试，`query_faq` 返回 `ok=false`，不发 `citations`，不入池；模型如实说暂时查不到。→ Task 5 的 `test_4xx_is_not_retried`、Task 8 的 `test_rerank_failure_gives_tool_error_without_pool`。

---

## 文件结构

```
pyproject.toml / uv.lock  docker-compose.yml  scripts/reset_db.sh  app/config.py
  app/db/models.py  tests/conftest.py  tests/test_config.py  tests/test_db_models.py      Task 1
app/knowledge/milvus.py  app/knowledge/chunking.py  app/knowledge/vectorize.py
  app/knowledge/ingest.py  app/repositories/knowledge.py  scripts/build_kb.py
  tests/fakes.py  tests/test_milvus.py  tests/test_vectorize.py  tests/test_ingest.py
  tests/test_retrieval.py  tests/test_chunking.py                                        Task 2
knowledge/docs/manual/商品手册.md  knowledge/docs/policy/退货政策.md
  knowledge/docs/manual/售后手册.md  knowledge/lexicon.json  tests/test_knowledge_docs.py  Task 3（数据类）
app/knowledge/query.py  app/schemas.py  app/prompts.py  app/llm.py
  tests/conftest.py  tests/test_query.py                                                 Task 4
app/knowledge/rerank.py  app/main.py  tests/conftest.py  tests/test_rerank.py             Task 5
app/knowledge/retrieval.py  app/tools/faq.py  app/tools/registry.py
  app/tools/executor.py  app/config.py  删除 evals/run_retrieval_eval.py
  evals/run_tool_selection_eval.py  evals/tool_selection_samples.jsonl
  tests/test_retrieval.py  tests/test_tools.py  tests/test_executor.py  tests/test_config.py Task 6
app/services/grounding.py  app/repositories/low_confidence.py  app/schemas.py
  app/prompts.py  app/llm.py  tests/conftest.py  tests/test_grounding.py
  tests/test_repositories.py                                                             Task 7
app/services/chat.py  app/prompts.py  tests/test_chat_api.py  tests/test_prompts.py      Task 8
app/repositories/knowledge.py  app/repositories/faith_cases.py  app/api/knowledge.py
  app/api/faith_cases.py  app/api/web.py  app/web/faith_cases.html  app/main.py
  tests/test_knowledge_api.py  tests/test_faith_cases.py                                 Task 9
evals/rag_metrics.py  evals/rag_eval_set.py  evals/run_rag_eval.py
  evals/run_faith_judge_eval.py  app/schemas.py  app/prompts.py  app/llm.py
  tests/test_rag_metrics.py  tests/test_rag_eval_set.py  tests/test_run_rag_eval.py      Task 10
evals/rag_eval.jsonl  evals/faith_judge_samples.jsonl  删除 evals/retrieval_samples.jsonl Task 11（数据类）
app/config.py（RERANK_MIN_SCORE）  evals/reports/rag_eval_*.md  Prompt 调优                Task 12（验证类）
app/web/index.html  app/web/faith_cases.html                                             Task 13（Vibe Coding）
scripts/demo4.sh  CLAUDE.md（Claude）                                                     Task 14
```

**说明：** 任务编号与 spec 无关，只表示执行顺序。Task 3、11 是数据类任务，Task 12 是评估验证任务，按 CLAUDE.md 用评估集代替 TDD。Task 13 是 Vibe Coding，不走 TDD 和 code review。

---

### Task 1: 基础设施（建表挂载、ORM、配置、依赖）

**Files:**
- Modify: `pyproject.toml`、`uv.lock`（`uv add httpx`，删除 dev 组中的 `httpx`）
- Modify: `docker-compose.yml`
- Modify: `scripts/reset_db.sh`
- Modify: `tests/conftest.py`（`_reset_schema`、`_clear_runtime_tables`）
- Modify: `app/db/models.py`
- Modify: `app/config.py`
- Test: `tests/test_config.py`、`tests/test_db_models.py`

**Interfaces:**
- Produces:
  - ORM 类 `app.db.models.LowConfidenceQuestion`（字段：`id`、`conversation_id`、`raw_question`、`source`、`reason`、`created_at`）。
  - ORM 类 `app.db.models.FaithCase`（字段：`id`、`eval_id`、`bucket`、`query`、`strategy`、`answer`、`reason`、`citations`、`judge_model`、`status`、`seen_count`、`first_seen_at`、`last_seen_at`、`resolution`、`resolved_at`）。
  - `Settings.rerank_api_key: SecretStr`、`Settings.rerank_base_url: str`。
  - 常量：`RERANK_MODEL`、`RERANK_TIMEOUT_SECONDS`、`RERANK_MAX_ATTEMPTS`、`RERANK_RETRY_BASE_DELAY`、`RERANK_RETRY_MAX_DELAY`、`RECALL_LEG_LIMIT`、`RRF_K`、`FUSED_LIMIT`、`EVIDENCE_TOP_N`、`RERANK_MIN_SCORE`、`QUERY_FAQ_TIMEOUT_SECONDS`、`PRODUCT_CATEGORIES`、`GENERAL_CATEGORY`、`KNOWLEDGE_TEXT_MAX_BYTES`。
  - `FAQ_MIN_SCORE`、`FAQ_MAX_RESULTS` 本任务**保留**，Task 6 删除。

- [ ] **Step 1: 写失败的测试**

`tests/test_config.py`：在 `_set_required` 中加 `monkeypatch.setenv("RERANK_API_KEY", "r1")`；`test_unknown_env_file_keys_are_ignored` 的 `delenv` 列表和 `.env` 内容各加 `RERANK_API_KEY`。追加：

```python
def test_reads_rerank_variables(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("RERANK_BASE_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.rerank_api_key.get_secret_value() == "r1"
    assert s.rerank_base_url == "https://api.siliconflow.cn/v1"


def test_rerank_api_key_is_required(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("RERANK_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_ch04_constants():
    assert config.RERANK_MODEL == "BAAI/bge-reranker-v2-m3"
    assert config.RERANK_TIMEOUT_SECONDS == 10
    assert config.RERANK_MAX_ATTEMPTS == 3
    assert (config.RERANK_RETRY_BASE_DELAY, config.RERANK_RETRY_MAX_DELAY) == (0.5, 4.0)
    assert (config.RECALL_LEG_LIMIT, config.RRF_K, config.FUSED_LIMIT) == (50, 60, 50)
    assert config.EVIDENCE_TOP_N == 10
    assert config.RERANK_MIN_SCORE == 0.30
    assert config.QUERY_FAQ_TIMEOUT_SECONDS == 20
    assert config.PRODUCT_CATEGORIES == (
        "蓝牙耳机", "羊毛衫", "扫地机器人", "电动牙刷", "台灯", "保温杯", "运动鞋", "手机壳",
    )
    assert config.GENERAL_CATEGORY == "通用"
    assert config.KNOWLEDGE_TEXT_MAX_BYTES == 16384
```

文件头补 `import pytest` 和 `from pydantic import ValidationError`（已有则不重复）。

`tests/test_db_models.py` 追加（沿用该文件已有的 `pytestmark = pytest.mark.anyio` 和 fixture `db`）：

```python
from sqlalchemy import text as sql_text

from app.db.models import Conversation, FaithCase, LowConfidenceQuestion


async def test_low_confidence_question_defaults(db):
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.flush()
        row = LowConfidenceQuestion(
            conversation_id=conv.id, raw_question="X9 能无线充电吗", source="self_check", reason="证据没写",
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        assert row.id > 0 and row.created_at is not None
        assert row.source == "self_check"


async def test_faith_case_defaults_and_utf8_enum(db):
    async with db() as s:
        row = FaithCase(
            eval_id="A01", bucket="A_policy", query="q", answer="a", reason="r",
            citations=[{"n": 1, "chunk_id": 3, "section_path": "p", "question": "q", "answer": "a"}],
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        assert (row.strategy, row.status, row.seen_count) == ("hybrid_rerank", "未解决", 1)
        assert row.first_seen_at is not None and row.last_seen_at is not None
        assert row.citations[0]["chunk_id"] == 3
        # HEX 校验存储字节，避免双重编码被反向还原后漏检。
        hexed = await s.scalar(sql_text("SELECT HEX(status) FROM faith_cases WHERE id = :i"), {"i": row.id})
        assert hexed == "E69CAAE8A7A3E586B3"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_config.py tests/test_db_models.py -q`
Expected: FAIL（`ImportError: cannot import name 'FaithCase'`、`AttributeError: ... RERANK_MODEL`）。

- [ ] **Step 3: 实现**

1. 依赖：`uv add httpx`；从 `pyproject.toml` 的 `[dependency-groups].dev` 删除 `httpx`；`uv sync`。
2. `docker-compose.yml` 的 mysql `volumes`：在 `02-schema-ch03.sql` 一行后加入 `- ./db/schema_ch04.sql:/docker-entrypoint-initdb.d/03-schema-ch04.sql:ro`，把 seed 一行的目标改为 `04-seed.sql`。
3. `scripts/reset_db.sh`：在 `knowledge_chunks` 检查之后加：

```bash
for t in low_confidence_questions faith_cases; do
  docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SHOW TABLES LIKE '$t'" | grep -q "$t" || { echo "$t 未创建"; exit 1; }
done
```

4. `tests/conftest.py`：
   - `_reset_schema` 的 DROP 列表改为 `("faith_cases", "low_confidence_questions", "qa_extraction_staging", "knowledge_chunks", "messages", "tickets", "conversations", "faq")`；建表文件列表改为 `("schema.sql", "schema_ch03.sql", "schema_ch04.sql", "seed.sql")`。
   - `_clear_runtime_tables` 的 DELETE 列表改为 `("faith_cases", "low_confidence_questions", "qa_extraction_staging", "knowledge_chunks", "messages", "tickets", "conversations")`。原因：`low_confidence_questions` 外键引用 `conversations`，必须先删。
5. `app/db/models.py` 追加（`Integer` 从 `sqlalchemy` 导入）：

```python
class LowConfidenceQuestion(Base):
    __tablename__ = "low_confidence_questions"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int | None] = mapped_column(ID, ForeignKey("conversations.id"), nullable=True)
    raw_question: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Enum("retrieval_low_conf", "self_check", "user_feedback"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


class FaithCase(Base):
    __tablename__ = "faith_cases"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    eval_id: Mapped[str] = mapped_column(String(16))
    bucket: Mapped[str] = mapped_column(String(24))
    query: Mapped[str] = mapped_column(String(512))
    strategy: Mapped[str] = mapped_column(String(24), server_default=text("'hybrid_rerank'"))
    answer: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("未解决", "已解决", "无需解决"), server_default=text("'未解决'")
    )
    seen_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    resolution: Mapped[str | None] = mapped_column(String(300), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

6. `app/config.py`：在常量区追加（`FAQ_*` 保留到 Task 6）：

```python
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_TIMEOUT_SECONDS = 10
RERANK_MAX_ATTEMPTS = 3
RERANK_RETRY_BASE_DELAY = 0.5
RERANK_RETRY_MAX_DELAY = 4.0
RECALL_LEG_LIMIT = 50
RRF_K = 60
FUSED_LIMIT = 50
EVIDENCE_TOP_N = 10
# 初值。由评估集的门槛扫描校准（spec §8.2）。
RERANK_MIN_SCORE = 0.30
QUERY_FAQ_TIMEOUT_SECONDS = 20
PRODUCT_CATEGORIES = ("蓝牙耳机", "羊毛衫", "扫地机器人", "电动牙刷", "台灯", "保温杯", "运动鞋", "手机壳")
GENERAL_CATEGORY = "通用"
# Milvus VARCHAR 的 max_length 按字节计。
KNOWLEDGE_TEXT_MAX_BYTES = 16384
```

`Settings` 追加：

```python
    rerank_api_key: SecretStr
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。测试库由 `tests/conftest.py` 的 `_reset_schema` 按 4 个 SQL 文件重建，不需要 docker 操作。

- [ ] **Step 5: Claude 在生产库追加两张表（Codex 不做）**

**警告：** 不运行 `bash scripts/reset_db.sh`。它删除全部数据卷，生产库中的会话、消息和挖掘块会丢失。只追加新表：

```bash
docker exec -i aftersales-mysql mysql --default-character-set=utf8mb4 -uaftersales -paftersales aftersales < db/schema_ch04.sql
docker exec aftersales-mysql mysql -N -uaftersales -paftersales -e "SELECT HEX(COLUMN_TYPE) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='aftersales' AND TABLE_NAME='faith_cases' AND COLUMN_NAME='status'"
```

Expected: 第 2 条命令输出 `"enum('未解决','已解决','无需解决')"` 的 UTF-8 十六进制（Claude 用 `python3 -c "print(\"enum('未解决','已解决','无需解决')\".encode().hex().upper())"` 计算后比对）。不一致说明中文被双重编码，删除两张表后排查客户端字符集。

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml uv.lock docker-compose.yml scripts/reset_db.sh app/config.py app/db/models.py tests/conftest.py tests/test_config.py tests/test_db_models.py
git commit -m "feat(ch04): ch04 tables, ORM mappings, rerank settings and constants"
```

---

### Task 2: Milvus 新集合结构、检索原语与向量化

**Files:**
- Modify: `app/knowledge/milvus.py`（重写集合定义，新增检索原语）
- Modify: `app/knowledge/chunking.py`（新增 `product_category_of`）
- Modify: `app/knowledge/vectorize.py`
- Modify: `app/repositories/knowledge.py`（新增 `mark_pending_by_content_type`）
- Modify: `app/knowledge/ingest.py`
- Modify: `scripts/build_kb.py`
- Modify: `tests/fakes.py`（新增 `entity()`）
- Test: `tests/test_milvus.py`、`tests/test_chunking.py`、`tests/test_vectorize.py`、`tests/test_ingest.py`、`tests/test_retrieval.py`（只改 `upsert_vectors` 调用）

**Interfaces:**
- Consumes: Task 1 的 `KNOWLEDGE_TEXT_MAX_BYTES`、`RRF_K`、`PRODUCT_CATEGORIES`、`GENERAL_CATEGORY`。
- Produces（`app/knowledge/milvus.py`）：

```python
@dataclass(frozen=True)
class Entity:
    id: int
    vector: list[float]
    text: str
    product_category: str
    content_type: str

class CollectionSchemaError(RuntimeError): ...
REBUILD_HINT: str
async def ensure_collection() -> None          # 旧结构抛 CollectionSchemaError
async def recreate_collection() -> None        # 删除后按新结构创建并加载
async def upsert_entities(entities: list[Entity]) -> None
async def search_dense(vector: list[float], limit: int, filter: str = "") -> list[tuple[int, float]]
async def search_bm25(text: str, limit: int, filter: str = "") -> list[tuple[int, float]]
async def search_hybrid(vector: list[float], text: str, *, leg_limit: int, limit: int,
                        filter: str = "") -> list[tuple[int, float]]
async def search_vectors(vector: list[float], limit: int) -> list[tuple[int, float]]  # 保留，等于 search_dense(vector, limit)
async def delete_vectors(ids: list[int]) -> None   # 不变
async def count_vectors() -> int                   # 不变
```

  删除 `upsert_vectors`。
- Produces（`app/knowledge/chunking.py`）：`def product_category_of(section_path: str | None) -> str`
- Produces（`app/repositories/knowledge.py`）：`async def mark_pending_by_content_type(s: AsyncSession, content_type: str) -> int`
- Produces（`tests/fakes.py`）：`def entity(id: int, vector: list[float], text: str = "t", category: str = "通用", content_type: str = "policy") -> Entity`

- [ ] **Step 1: 写失败的测试**

`tests/fakes.py` 追加：

```python
from app.knowledge.milvus import Entity


def entity(id: int, vector: list[float], text: str = "t", category: str = "通用",
           content_type: str = "policy") -> Entity:
    return Entity(id=id, vector=vector, text=text, product_category=category, content_type=content_type)
```

`tests/test_milvus.py`：把所有 `upsert_vectors([(i, v), ...])` 改为 `upsert_entities([entity(i, v), ...])`，并追加：

```python
from pymilvus import AsyncMilvusClient, DataType

from app.knowledge import milvus as m
from tests.fakes import entity, unit


async def test_new_collection_has_six_fields(milvus):
    desc = await milvus.describe_collection(m.get_collection())
    assert [f["name"] for f in desc["fields"]] == [
        "id", "vector", "text", "sparse", "product_category", "content_type",
    ]


async def test_old_schema_collection_raises(milvus):
    name = m.get_collection()
    await milvus.drop_collection(name)
    schema = AsyncMilvusClient.create_schema(auto_id=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=1024)
    idx = AsyncMilvusClient.prepare_index_params()
    idx.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
    await milvus.create_collection(name, schema=schema, index_params=idx)
    with pytest.raises(m.CollectionSchemaError, match="build_kb.py --rebuild"):
        await m.ensure_collection()


async def test_recreate_collection_empties_and_fixes_schema(milvus):
    await m.upsert_entities([entity(1, unit(0))])
    await m.recreate_collection()
    assert await m.count_vectors() == 0
    await m.ensure_collection()


async def test_bm25_ranks_exact_model_terms(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "商品手册 > 蓝牙耳机\nX3 Pro 续航\n单次续航 8 小时", "蓝牙耳机", "manual"),
        entity(2, unit(1), "商品手册 > 蓝牙耳机\nX5 续航\n单次续航 6 小时", "蓝牙耳机", "manual"),
        entity(3, unit(2), "退货政策 > 退款\n退款时间\n原路退回", "通用", "policy"),
    ])
    hits = await m.search_bm25("X3 Pro 续航", 10)
    assert hits[0][0] == 1
    assert 3 not in [i for i, _ in hits]  # BM25 只返回含查询词项的文档


async def test_category_filter_keeps_general(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "耳机 七天无理由", "蓝牙耳机", "manual"),
        entity(2, unit(0), "七天无理由 退货条件", "通用", "policy"),
        entity(3, unit(0), "羊毛衫 七天无理由", "羊毛衫", "faq"),
    ])
    flt = 'product_category in ["蓝牙耳机", "通用"]'
    assert sorted(i for i, _ in await m.search_bm25("七天无理由", 10, flt)) == [1, 2]
    assert sorted(i for i, _ in await m.search_dense(unit(0), 10, flt)) == [1, 2]


async def test_exclude_mined_filter(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "快递 几天", "通用", "mined"),
        entity(2, unit(0), "快递 几天", "通用", "policy"),
    ])
    assert [i for i, _ in await m.search_bm25("快递", 10, 'content_type != "mined"')] == [2]


async def test_hybrid_search_fuses_both_legs(milvus):
    await m.upsert_entities([
        entity(1, unit(0), "无关文字", "通用", "policy"),        # 只有 dense 命中
        entity(2, unit(5), "X3 Pro 续航", "蓝牙耳机", "manual"),  # 只有 BM25 命中
        entity(3, unit(9), "其他", "通用", "policy"),            # 都不命中
    ])
    hits = await m.search_hybrid(unit(0), "X3 Pro 续航", leg_limit=1, limit=10)
    assert sorted(i for i, _ in hits) == [1, 2]
    assert all(score > 0 for _, score in hits)


async def test_search_vectors_is_unfiltered_dense(milvus):
    await m.upsert_entities([entity(1, unit(0)), entity(2, unit(1), content_type="mined")])
    assert [i for i, _ in await m.search_vectors(unit(1), 1)] == [2]
```

`tests/test_chunking.py` 追加：

```python
from app.knowledge.chunking import product_category_of


@pytest.mark.parametrize("path, expected", [
    ("商品FAQ > 蓝牙耳机 > 耳机怎么连手机？", "蓝牙耳机"),
    ("商品手册 > 扫地机器人 > S10 Max 续航", "扫地机器人"),
    ("退货政策 > 退款 > 退款时间", "通用"),
    ("对话挖掘 > 退换货", "通用"),
    ("常见问答 > 运费", "通用"),
    (None, "通用"),
    ("商品手册 > 蓝牙耳机配件", "通用"),  # 只认整段相等
])
def test_product_category_of(path, expected):
    assert product_category_of(path) == expected
```

`tests/test_vectorize.py` 追加（导入 `KnowledgeChunk`、`knowledge`、`NewChunk`、`knowledge_text`、`milvus as m`，已有的不重复）：

```python
async def _insert_two_chunks_for_fields(db):
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk("商品手册 > 蓝牙耳机", "X3 续航", "单次续航 6 小时", "商品手册 > 蓝牙耳机 > X3 续航", "manual", False),
            NewChunk("退货政策 > 退款", "退款时间", "原路退回", "退货政策 > 退款 > 退款时间", "policy", False),
        ])
        await s.commit()
    return [r.id for r in rows]


async def test_vectorize_writes_text_and_filter_fields(db, milvus):
    ids = await _insert_two_chunks_for_fields(db)
    await vectorize_pending()
    rows = await milvus.query(
        m.get_collection(), filter=f"id in {ids}",
        output_fields=["id", "text", "product_category", "content_type"],
    )
    got = {r["id"]: (r["product_category"], r["content_type"]) for r in rows}
    assert got == {ids[0]: ("蓝牙耳机", "manual"), ids[1]: ("通用", "policy")}
    async with db() as s:
        row = await s.get(KnowledgeChunk, ids[0])
    assert next(r["text"] for r in rows if r["id"] == ids[0]) == knowledge_text(
        row.category, row.questions, row.answer
    )
```

`tests/test_ingest.py` 追加：

```python
async def test_mark_pending_by_content_type_only_touches_mined(db):
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk("运费", "q1", "a1", "对话挖掘 > 运费", "mined", False),
            NewChunk("c", "q2", "a2", "退货政策 > x", "policy", False),
        ])
        await knowledge.mark_done(s, [r.id for r in rows])
        await s.commit()
    async with db() as s:
        assert await knowledge.mark_pending_by_content_type(s, "mined") == 1
        await s.commit()
    async with db() as s:
        statuses = {r.content_type: r.vectorize_status for r in await s.scalars(select(KnowledgeChunk))}
    assert statuses == {"mined": "pending", "policy": "done"}
```

`tests/test_ingest.py`、`tests/test_retrieval.py` 中已有的 `m.upsert_vectors([(i, v), ...])` 全部改为 `m.upsert_entities([entity(i, v), ...])`。

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_milvus.py tests/test_chunking.py tests/test_vectorize.py tests/test_ingest.py tests/test_retrieval.py -q`
Expected: FAIL（`ImportError: cannot import name 'Entity'`）。

- [ ] **Step 3: 实现**

`app/knowledge/milvus.py`（保留 `get_milvus`、`get_collection`、`set_milvus`、`_call`、`delete_vectors`、`count_vectors`、`close_milvus` 不变）：

```python
from dataclasses import asdict, dataclass
from typing import Any

from pymilvus import AnnSearchRequest, AsyncMilvusClient, DataType, Function, FunctionType, MilvusException, RRFRanker

from app.config import EMBED_DIM, KNOWLEDGE_TEXT_MAX_BYTES, RRF_K  # 另保留原有导入

FIELDS = ("id", "vector", "text", "sparse", "product_category", "content_type")
REBUILD_HINT = (
    "Milvus 集合结构过旧（缺少 sparse 字段）。请执行 bash scripts/reset_db.sh，"
    "或 uv run python scripts/build_kb.py --rebuild"
)


class CollectionSchemaError(RuntimeError):
    """集合存在，但结构不是本章的 6 个字段。"""


@dataclass(frozen=True)
class Entity:
    id: int
    vector: list[float]
    text: str
    product_category: str
    content_type: str


def _schema():
    schema = AsyncMilvusClient.create_schema(auto_id=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field(
        "text", DataType.VARCHAR, max_length=KNOWLEDGE_TEXT_MAX_BYTES,
        enable_analyzer=True, analyzer_params={"type": "chinese"},
    )
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("product_category", DataType.VARCHAR, max_length=64)
    schema.add_field("content_type", DataType.VARCHAR, max_length=16)
    # BM25 Function 由 Milvus 从 text 生成 sparse。写入时不提供 sparse。
    schema.add_function(Function(
        name="text_bm25", input_field_names=["text"], output_field_names=["sparse"],
        function_type=FunctionType.BM25,
    ))
    return schema


def _index_params():
    params = AsyncMilvusClient.prepare_index_params()
    params.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
    params.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    return params


async def ensure_collection() -> None:
    """集合不存在时创建并加载。已存在时检查结构，再加载。不自动删除集合。"""
    client, name = get_milvus(), get_collection()
    if await _call(lambda: client.has_collection(name)):
        desc = await _call(lambda: client.describe_collection(name))
        if "sparse" not in {f["name"] for f in desc["fields"]}:
            raise CollectionSchemaError(REBUILD_HINT)
        await _call(lambda: client.load_collection(name))
        return
    # 集合使用 Strong 一致性，写入后可立即检索。
    await _call(lambda: client.create_collection(
        name, schema=_schema(), index_params=_index_params(), consistency_level="Strong"
    ))


async def recreate_collection() -> None:
    client, name = get_milvus(), get_collection()
    if await _call(lambda: client.has_collection(name)):
        await _call(lambda: client.drop_collection(name))
    await ensure_collection()


async def upsert_entities(entities: list[Entity]) -> None:
    if not entities:
        return
    data = [asdict(e) for e in entities]
    await _call(lambda: get_milvus().upsert(get_collection(), data))


def _hits(result) -> list[tuple[int, float]]:
    return [(int(hit["id"]), float(hit["distance"])) for hit in result[0]]


async def search_dense(vector: list[float], limit: int, filter: str = "") -> list[tuple[int, float]]:
    """COSINE 的 distance 越大越相似。"""
    return _hits(await _call(lambda: get_milvus().search(
        get_collection(), data=[vector], anns_field="vector", limit=limit, filter=filter,
        search_params={"metric_type": "COSINE"},
    )))


async def search_bm25(text: str, limit: int, filter: str = "") -> list[tuple[int, float]]:
    """查询文本由 Milvus 用同一 analyzer 分词。只返回含查询词项的文档。"""
    return _hits(await _call(lambda: get_milvus().search(
        get_collection(), data=[text], anns_field="sparse", limit=limit, filter=filter,
        search_params={"metric_type": "BM25"},
    )))


async def search_hybrid(
    vector: list[float], text: str, *, leg_limit: int, limit: int, filter: str = ""
) -> list[tuple[int, float]]:
    """dense 和 BM25 各取 leg_limit 条，用 RRF 融合后取 limit 条。分数为 RRF 分数。"""
    reqs = [
        AnnSearchRequest(data=[vector], anns_field="vector", param={"metric_type": "COSINE"},
                         limit=leg_limit, filter=filter),
        AnnSearchRequest(data=[text], anns_field="sparse", param={"metric_type": "BM25"},
                         limit=leg_limit, filter=filter),
    ]
    return _hits(await _call(lambda: get_milvus().hybrid_search(
        get_collection(), reqs, RRFRanker(RRF_K), limit=limit,
    )))


async def search_vectors(vector: list[float], limit: int) -> list[tuple[int, float]]:
    """挖掘去重沿用：dense 单路，不过滤。"""
    return await search_dense(vector, limit)
```

`AnnSearchRequest` 的 `filter` 参数和 `expr` 互斥。`filter` 为空串时，pymilvus 视为无过滤（已在设计阶段实测）。

`app/knowledge/chunking.py` 追加（导入 `GENERAL_CATEGORY`、`PRODUCT_CATEGORIES`）：

```python
def product_category_of(section_path: str | None) -> str:
    """路径中第一个等于品类名的段就是品类。没有时为通用。"""
    for part in (section_path or "").split(PATH_SEP):
        if part in PRODUCT_CATEGORIES:
            return part
    return GENERAL_CATEGORY
```

`app/knowledge/vectorize.py`：把 `upsert_vectors([(r.id, v) ...])` 换成：

```python
        await upsert_entities([
            Entity(
                id=r.id,
                vector=v,
                text=knowledge_text(r.category, r.questions, r.answer),
                product_category=product_category_of(r.section_path),
                content_type=r.content_type or "",
            )
            for r, v in zip(rows, vectors)
        ])
```

`app/repositories/knowledge.py` 追加：

```python
async def mark_pending_by_content_type(s: AsyncSession, content_type: str) -> int:
    result = await s.execute(
        update(KnowledgeChunk)
        .where(KnowledgeChunk.content_type == content_type)
        .values(vectorize_status="pending", vector_id=None)
    )
    return result.rowcount
```

`app/knowledge/ingest.py` 的 `ingest_all`：`rebuild=True` 时，在逐个 `rebuild_source` 之后执行：

```python
        async with get_sessionmaker()() as s:
            # 集合已整体重建。挖掘块保留 MySQL 行，置回 pending，由同一次向量化重新写入。
            await knowledge.mark_pending_by_content_type(s, "mined")
            await s.commit()
```

`scripts/build_kb.py` 的 `run()`：开头改为

```python
        if args.rebuild:
            # 集合结构可能已变化，按 id 删除不够，整体重建。
            await recreate_collection()
        else:
            await ensure_collection()
```

`--rebuild` 的帮助文字改为"重建 Milvus 集合和全部文档来源；mined 块保留并重新向量化"。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: Claude 实测生产集合（Codex 不做）**

Run: `uv run python scripts/build_kb.py --rebuild && uv run python scripts/build_kb.py --check`
Expected: 退出码 0；Milvus 实体数等于 MySQL 行数。

- [ ] **Step 6: 提交**

```bash
git add app/knowledge app/repositories/knowledge.py scripts/build_kb.py tests
git commit -m "feat(ch04): Milvus collection with BM25 text, filter fields and hybrid search"
```

---

### Task 3: 知识文档扩容与词表（数据类，代替 TDD）

**Files:**
- Create: `knowledge/docs/manual/商品手册.md`
- Modify: `knowledge/docs/policy/退货政策.md`、`knowledge/docs/manual/售后手册.md`
- Create: `knowledge/lexicon.json`
- Modify: `tests/test_knowledge_docs.py`

**Interfaces:**
- Produces：`knowledge/lexicon.json`，格式 `{"models": {"品类": ["型号", ...]}, "synonyms": {"标准词": ["俗称", ...]}}`。`models` 的键顺序与 `PRODUCT_CATEGORIES` 一致，每个品类 3 个型号。Task 4 的 `load_lexicon` 读取它。
- Produces：`商品手册.md` 的二级标题为 8 个品类名，三级标题为"型号 + 小节"。Task 7 的 Milvus 测试和 Task 11 的评估集引用这些章节路径。

本任务由 Codex 编写数据文件和结构测试，Claude 审核内容。

- [ ] **Step 1: 写结构测试（先失败）**

`tests/test_knowledge_docs.py`：把 `test_docs_cover_required_structures` 中的 `["faq", "manual", "policy"]` 改为 `["faq", "manual", "manual", "policy"]`，并追加：

```python
import json
import re

from app.config import GENERAL_CATEGORY, PRODUCT_CATEGORIES
from app.knowledge.chunking import product_category_of

LEXICON = DOCS_DIR.parent / "lexicon.json"
MODELS = {
    "蓝牙耳机": ["X3", "X3 Pro", "X5"], "羊毛衫": ["W1", "W1 Plus", "W2"],
    "扫地机器人": ["S10", "S10 Max", "S20"], "电动牙刷": ["T3", "T3 Pro", "T5"],
    "台灯": ["L1", "L1 Pro", "L2"], "保温杯": ["C5", "C5 Plus", "C8"],
    "运动鞋": ["R1", "R1 Pro", "R2"], "手机壳": ["K1", "K1 Pro", "K2"],
}


def _manual():
    return next(s for s in load_doc_sources() if s.title == "商品手册")


def test_product_manual_sections():
    chunks = _manual().chunks
    cats = {c.section_path.split(" > ")[1] for c in chunks}
    assert cats == set(PRODUCT_CATEGORIES)
    assert all(product_category_of(c.section_path) != GENERAL_CATEGORY for c in chunks)
    for cat, models in MODELS.items():
        headings = [c.questions for c in chunks if c.section_path.split(" > ")[1] == cat]
        for model in models:
            # 每个型号 4–5 个小节，标题以型号开头，后接空格。
            n = len({h for h in headings if h.startswith(model + " ") and not any(
                h.startswith(o + " ") for o in models if o != model and o.startswith(model))})
            assert 4 <= n <= 5, (model, n)


def test_lexicon_models_match_manual_and_are_canonical():
    lex = json.loads(LEXICON.read_text(encoding="utf-8"))
    assert lex["models"] == MODELS
    assert list(lex["models"]) == list(PRODUCT_CATEGORIES)
    text = (DOCS_DIR / "manual" / "商品手册.md").read_text(encoding="utf-8")
    for model in [m for ms in lex["models"].values() for m in ms]:
        assert re.search(rf"(?<![A-Za-z0-9]){re.escape(model)}(?![A-Za-z0-9])", text), model
        if " " in model:
            # 文档只用规范写法：不出现连写或横线写法。
            assert model.replace(" ", "") not in text, model
            assert model.replace(" ", "-") not in text, model


def test_lexicon_synonyms_shape():
    syn = json.loads(LEXICON.read_text(encoding="utf-8"))["synonyms"]
    assert len(syn) >= 15
    assert syn["运费"] and "邮费" in syn["运费"]
    for key, aliases in syn.items():
        assert aliases and key not in aliases
        assert all(isinstance(a, str) and a for a in aliases)
    all_aliases = [a for aliases in syn.values() for a in aliases]
    assert len(all_aliases) == len(set(all_aliases)), "同一俗称只能对应一个标准词"


def test_chunk_count_supports_eval_set():
    # 240 道可答题，平均每个来源键不超过 3 题。
    assert len({c.section_path for s in load_doc_sources() for c in s.chunks}) >= 80
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_knowledge_docs.py -q`
Expected: FAIL（`StopIteration`：找不到商品手册）。

- [ ] **Step 3: 编写数据文件**

1. `knowledge/docs/manual/商品手册.md`：
   - 一级标题 `# 商品手册`，开头一段说明本手册覆盖 8 个品类、24 个型号的参数和售后规则。
   - 二级标题按 `PRODUCT_CATEGORIES` 顺序，每个二级标题下写该品类的 3 个型号（见 Global Constraints 的型号表）。
   - 每个型号 4–5 个三级标题，格式为"型号 + 空格 + 小节名"：`参数`、`续航`（或`容量`、`材质`，按品类选一个合适的）、`保修`、`退换规则`、`常见故障`。例：`### X3 Pro 续航`。
   - 相近型号的数字必须不同，例如 `X3` 续航 6 小时、`X3 Pro` 8 小时、`X5` 10 小时；保修天数分别为 180、365、365；`X5` 不支持七天无理由退货（拆封后）。每个型号至少有 1 个只属于它的事实（例如"`S10 Max` 的尘盒容量 450 毫升"）。
   - 每个小节 1–4 句，全部用规范型号写法。不出现"邮"字。不写知识库外的承诺（例如"保证""一定"）。
   - 至少 2 个小节含 `【关键条款】` 标记（例如保修的人为损坏除外条款）。
2. `退货政策.md` 新增二级标题 `## 特殊情况`，下设 `### 价格保护`、`### 赠品退回`、`### 部分退款`、`### 以旧换新`、`### 套装退货` 5 个小节，每节 2–4 句。`售后手册.md` 的 `## 发票` 下新增 `### 发票类型`（只开电子普通发票，不开增值税专用发票），`## 维修` 下新增 `### 上门取件`。
3. `knowledge/lexicon.json`：`models` 是"品类 → 3 个型号"的对象，按型号表填写；`synonyms` 至少 15 个标准词。必须包含：`"运费": ["邮费", "快递费"]`、`"退款": ["退钱", "钱退回来"]`、`"续航": ["能用多久", "电池能撑多久"]`、`"保修": ["质保", "包修"]`、`"无理由退货": ["七天无理由", "不想要了能退吗"]`、`"发票": ["开票", "收据"]`、`"换货": ["调换", "换一个"]`。同一俗称只出现在一个标准词下。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_knowledge_docs.py tests/test_ingest.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: Claude 审核与入库**

1. Claude 通读 `商品手册.md`，核对：相近型号的数字是否互不相同、是否有承诺性措辞、是否全部为规范写法。
2. Run: `uv run python scripts/build_kb.py --rebuild && uv run python scripts/build_kb.py --check`
   Expected: 退出码 0。

- [ ] **Step 6: 提交**

```bash
git add knowledge tests/test_knowledge_docs.py
git commit -m "feat(ch04): product manual with 24 models, policy details and lexicon"
```

---

### Task 4: Query 理解（改写、型号归一、同义词）

**Files:**
- Create: `app/knowledge/query.py`
- Modify: `app/schemas.py`（新增 `QueryPlan`）
- Modify: `app/prompts.py`（新增 `QUERY_REWRITE_SYSTEM_PROMPT`、`query_rewrite_prompt`）
- Modify: `app/llm.py`（新增 `get_query_rewriter`）
- Modify: `tests/conftest.py`（autouse 拦截 `get_query_rewriter`）
- Test: `tests/test_query.py`

**Interfaces:**
- Consumes: Task 3 的 `knowledge/lexicon.json`；Task 1 的 `PRODUCT_CATEGORIES`。
- Produces（`app/schemas.py`）：

```python
class QueryPlan(BaseModel):
    standard_query: str = Field(min_length=1, description="改写后的标准问法，保留型号、数字和限定条件")
    product_category: Literal[PRODUCT_CATEGORIES] | None = Field(
        default=None, description="用户明确提到的商品品类；没有提到时为 null")
```

- Produces（`app/knowledge/query.py`）：

```python
LEXICON_PATH: Path   # <项目根>/knowledge/lexicon.json
@dataclass(frozen=True)
class Lexicon:
    model_categories: dict[str, str]          # 规范型号 → 品类
    synonyms: dict[str, tuple[str, ...]]      # 标准词 → 俗称
def load_lexicon(path: Path = LEXICON_PATH) -> Lexicon
def get_lexicon() -> Lexicon                  # lru_cache
def normalize_models(text: str, lexicon: Lexicon) -> str
def model_category(text: str, lexicon: Lexicon) -> str | None
def dense_query(text: str, lexicon: Lexicon) -> str
def bm25_query(text: str, lexicon: Lexicon) -> str
async def understand(question: str, *, rewriter: Runnable | None = None,
                     lexicon: Lexicon | None = None) -> QueryPlan
```

- Produces（`app/llm.py`）：`get_query_rewriter() -> Runnable`（lru_cache），输入 `{"question": str}`，输出 `{"parsed": QueryPlan | None, "raw": ...}`。

- [ ] **Step 1: 写失败的测试**

`tests/test_query.py`：

```python
import pytest
from langchain_core.runnables import RunnableLambda

from app.knowledge import query as q
from app.schemas import QueryPlan

pytestmark = pytest.mark.anyio

LEX = q.Lexicon(
    model_categories={"X3": "蓝牙耳机", "X3 Pro": "蓝牙耳机", "X5": "蓝牙耳机",
                      "S10": "扫地机器人", "S10 Max": "扫地机器人"},
    synonyms={"运费": ("邮费", "快递费"), "无理由退货": ("七天无理由", "不想要了能退吗")},
)


@pytest.mark.parametrize("raw, expected", [
    ("x3pro续航多久", "X3 Pro续航多久"),
    ("X3-PRO 怎么样", "X3 Pro 怎么样"),
    ("x3 pro和x5哪个好", "X3 Pro和X5哪个好"),
    ("s10max尘盒多大", "S10 Max尘盒多大"),
    ("X3 Pro 续航", "X3 Pro 续航"),
    ("X30 是什么", "X30 是什么"),
    ("AX3 是什么", "AX3 是什么"),
    ("退款多久到账", "退款多久到账"),
])
def test_normalize_models_variants(raw, expected):
    assert q.normalize_models(raw, LEX) == expected


def test_model_category():
    assert q.model_category("X3 Pro续航", LEX) == "蓝牙耳机"
    assert q.model_category("S10 Max 尘盒", LEX) == "扫地机器人"
    assert q.model_category("退款多久", LEX) is None


def test_dense_query_replaces_aliases_once():
    assert q.dense_query("邮费多少钱", LEX) == "运费多少钱"
    assert q.dense_query("七天无理由吗", LEX) == "无理由退货吗"
    assert q.dense_query("运费多少钱", LEX) == "运费多少钱"


def test_bm25_query_appends_synonyms():
    assert q.bm25_query("邮费多少钱", LEX) == "邮费多少钱 运费 快递费"
    assert q.bm25_query("运费怎么算", LEX) == "运费怎么算 邮费 快递费"
    assert q.bm25_query("X3 Pro 续航", LEX) == "X3 Pro 续航"


def _rewriter(plan=None, exc=None, seen=None):
    def run(inputs):
        if seen is not None:
            seen.append(inputs["question"])
        if exc:
            raise exc
        return {"parsed": plan, "raw": "raw"}
    return RunnableLambda(run)


async def test_understand_normalizes_before_and_after_llm():
    seen = []
    plan = await q.understand(
        "x3pro能用几个小时啊气死了",
        rewriter=_rewriter(QueryPlan(standard_query="x3pro 续航时间", product_category=None), seen=seen),
        lexicon=LEX,
    )
    assert seen == ["X3 Pro能用几个小时啊气死了"]
    assert plan == QueryPlan(standard_query="X3 Pro 续航时间", product_category="蓝牙耳机")


async def test_model_category_overrides_llm_category():
    plan = await q.understand(
        "X3 续航", rewriter=_rewriter(QueryPlan(standard_query="X3 续航", product_category="羊毛衫")),
        lexicon=LEX,
    )
    assert plan.product_category == "蓝牙耳机"


async def test_llm_category_kept_without_model():
    plan = await q.understand(
        "耳机能退吗", rewriter=_rewriter(QueryPlan(standard_query="蓝牙耳机无理由退货", product_category="蓝牙耳机")),
        lexicon=LEX,
    )
    assert plan.product_category == "蓝牙耳机"


@pytest.mark.parametrize("rewriter", [
    _rewriter(exc=TimeoutError("slow")),
    _rewriter(plan=None),
])
async def test_understand_falls_back_on_failure(rewriter, caplog):
    plan = await q.understand("x3pro 续航", rewriter=rewriter, lexicon=LEX)
    assert plan == QueryPlan(standard_query="X3 Pro 续航", product_category="蓝牙耳机")
    assert "Query 改写失败" in caplog.text


async def test_understand_without_rewriter_uses_factory_and_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="get_query_rewriter"):
        await q.understand("运费", lexicon=LEX)


def test_real_lexicon_loads():
    lex = q.load_lexicon()
    assert len(lex.model_categories) == 24
    assert lex.model_categories["X3 Pro"] == "蓝牙耳机"
    assert "邮费" in lex.synonyms["运费"]


def test_query_plan_rejects_unknown_category():
    with pytest.raises(ValueError):
        QueryPlan(standard_query="x", product_category="冰箱")
```

`tests/conftest.py` 追加 autouse fixture：

```python
def _blocked_factory(name: str):
    def factory():
        raise RuntimeError(f"测试未替换 {name}")
    return factory


@pytest.fixture(autouse=True)
def _block_llm_runnables(monkeypatch):
    """测试不调用上游模型。需要时在测试中传入 RunnableLambda。"""
    from app.knowledge import query
    monkeypatch.setattr(query, "get_query_rewriter", _blocked_factory("get_query_rewriter"))
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_query.py -q`
Expected: FAIL（`ModuleNotFoundError: app.knowledge.query`）。

- [ ] **Step 3: 实现**

`app/schemas.py` 追加 `QueryPlan`（见 Interfaces；导入 `Literal`、`PRODUCT_CATEGORIES`）。`Literal[PRODUCT_CATEGORIES]` 在 Python 3.12 中等价于逐项列出。

`app/prompts.py` 追加：

```python
QUERY_REWRITE_SYSTEM_PROMPT = f"""你是售后知识库的检索改写器。把用户的问题改写成一句适合检索知识库的标准问法，并识别商品品类。

## 改写规则
1. 去掉情绪、寒暄和与问题无关的内容，保留用户真正想问的点。
2. 口语和俗称改为店铺常用说法。例如"钱什么时候退回来"改为"退款多久到账"，"不想要了能退吗"改为"无理由退货的条件"。
3. 原文中的型号、数字、时间和限定条件必须原样保留，例如"X3 Pro""签收第 8 天""拆封后"。
4. 不补充原文没有的信息，不回答问题。
5. 一句话问了几件事时，合并为一句，每件事都保留。

## 品类规则
品类只能从下列选项中选择：{"、".join(PRODUCT_CATEGORIES)}。只有用户明确提到某个品类时才填写，否则为 null。"""

query_rewrite_prompt = ChatPromptTemplate.from_messages([
    ("system", QUERY_REWRITE_SYSTEM_PROMPT),
    ("human", "{question}"),
])
```

注意：f-string 在导入时展开，模板中不能再有花括号。

`app/llm.py` 追加：

```python
@lru_cache
def get_query_rewriter() -> Runnable:
    # 与 /extract 一样关闭思考：强制 tool_choice 与 DeepSeek 思考模式冲突。
    model = build_extract_model(get_settings())
    return query_rewrite_prompt | model.with_structured_output(
        QueryPlan, method="function_calling", include_raw=True
    )
```

`app/knowledge/query.py`：

```python
"""Query 理解：LLM 改写、型号归一、同义词处理。同义词只在检索侧处理，不改入库文本。"""

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from langchain_core.runnables import Runnable

from app.llm import get_query_rewriter
from app.schemas import QueryPlan

logger = logging.getLogger(__name__)
LEXICON_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "lexicon.json"
_ALNUM = "A-Za-z0-9"


def _model_key(model: str) -> str:
    return re.sub(r"[\s\-]", "", model).lower()


@dataclass(frozen=True)
class Lexicon:
    model_categories: dict[str, str]
    synonyms: dict[str, tuple[str, ...]]
    _model_re: re.Pattern = field(init=False, repr=False, compare=False)
    _alias_re: re.Pattern | None = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        # 长型号优先，保证 "X3 Pro" 不被拆成 "X3"。型号字符之间允许空格或横线。
        keys = sorted({_model_key(m) for m in self.model_categories}, key=len, reverse=True)
        alts = [r"[\s\-]*".join(re.escape(ch) for ch in k) for k in keys]
        object.__setattr__(self, "_model_re", re.compile(
            rf"(?<![{_ALNUM}])(?:{'|'.join(alts)})(?![{_ALNUM}])", re.IGNORECASE))
        aliases = sorted((a for al in self.synonyms.values() for a in al), key=len, reverse=True)
        object.__setattr__(self, "_alias_re", re.compile("|".join(map(re.escape, aliases))) if aliases else None)

    @property
    def canonical(self) -> dict[str, str]:
        return {_model_key(m): m for m in self.model_categories}

    @property
    def alias_to_key(self) -> dict[str, str]:
        return {a: k for k, al in self.synonyms.items() for a in al}


def load_lexicon(path: Path = LEXICON_PATH) -> Lexicon:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Lexicon(
        model_categories={m: cat for cat, models in data["models"].items() for m in models},
        synonyms={k: tuple(v) for k, v in data["synonyms"].items()},
    )


@lru_cache
def get_lexicon() -> Lexicon:
    return load_lexicon()


def normalize_models(text: str, lexicon: Lexicon) -> str:
    canonical = lexicon.canonical
    return lexicon._model_re.sub(lambda m: canonical[_model_key(m.group(0))], text)


def model_category(text: str, lexicon: Lexicon) -> str | None:
    """返回文本中第一个规范型号所属的品类。文本须已做型号归一。"""
    m = lexicon._model_re.search(text)
    return lexicon.model_categories[lexicon.canonical[_model_key(m.group(0))]] if m else None


def dense_query(text: str, lexicon: Lexicon) -> str:
    """把俗称替换为标准词。只替换一遍，不追加。"""
    if lexicon._alias_re is None:
        return text
    mapping = lexicon.alias_to_key
    return lexicon._alias_re.sub(lambda m: mapping[m.group(0)], text)


def bm25_query(text: str, lexicon: Lexicon) -> str:
    """保留原文，追加命中的标准词和全部俗称。"""
    extra: list[str] = []
    for key, aliases in lexicon.synonyms.items():
        if key in text or any(a in text for a in aliases):
            extra += [t for t in (key, *aliases) if t not in text and t not in extra]
    return " ".join([text, *extra])


async def understand(
    question: str, *, rewriter: Runnable | None = None, lexicon: Lexicon | None = None
) -> QueryPlan:
    lex = lexicon or get_lexicon()
    normalized = normalize_models(question, lex)
    # 工厂在 try 之外调用：测试中未替换时立即暴露。
    rewriter = rewriter or get_query_rewriter()
    try:
        result = await rewriter.ainvoke({"question": normalized})
        parsed = result["parsed"]
        if parsed is None:
            raise ValueError(f"改写结果无效：raw={result.get('raw')!r}")
        standard, category = normalize_models(parsed.standard_query, lex), parsed.product_category
    except Exception:
        logger.exception("Query 改写失败，用原话检索")
        standard, category = normalized, None
    # 型号与品类的对应是确定的，覆盖 LLM 的结果。
    category = model_category(standard, lex) or model_category(normalized, lex) or category
    return QueryPlan(standard_query=standard, product_category=category)
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_query.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/knowledge/query.py app/schemas.py app/prompts.py app/llm.py tests/conftest.py tests/test_query.py
git commit -m "feat(ch04): query understanding with LLM rewrite, model normalization and synonyms"
```

---

### Task 5: 重排客户端

**Files:**
- Create: `app/knowledge/rerank.py`
- Modify: `app/main.py`（lifespan 关闭重排客户端）
- Modify: `tests/conftest.py`（autouse `BlockedReranker`）
- Test: `tests/test_rerank.py`

**Interfaces:**
- Consumes: Task 1 的 `Settings.rerank_api_key`、`Settings.rerank_base_url`、`RERANK_*` 常量。
- Produces（`app/knowledge/rerank.py`）：

```python
class RerankError(RuntimeError): ...
def build_rerank_client(settings: Settings) -> httpx.AsyncClient
def get_rerank_client() -> httpx.AsyncClient
def set_rerank_client(client) -> None
async def close_rerank() -> None
async def rerank(query: str, documents: list[str], top_n: int, *,
                 sleep=asyncio.sleep, rand=random.random) -> list[tuple[int, float]]
    # 返回 (documents 下标, relevance_score)，按分数降序，最多 top_n 条
```

- [ ] **Step 1: 写失败的测试**

`tests/test_rerank.py`：

```python
import json

import httpx
import pytest

from app.config import RERANK_MODEL, Settings
from app.knowledge import rerank as rr

pytestmark = pytest.mark.anyio


async def no_sleep(_):
    return None


def use_handler(handler):
    client = httpx.AsyncClient(base_url="https://rr.test/v1", transport=httpx.MockTransport(handler))
    rr.set_rerank_client(client)
    return client


def ok(results):
    return httpx.Response(200, json={"results": results})


async def test_request_shape_and_sorted_results():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return ok([{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.95}])

    use_handler(handler)
    assert await rr.rerank("q", ["a", "b", "c"], 2, sleep=no_sleep) == [(0, 0.95), (2, 0.9)]
    assert seen == [("/v1/rerank", {
        "model": RERANK_MODEL, "query": "q", "documents": ["a", "b", "c"], "top_n": 2,
        "return_documents": False,
    })]


async def test_empty_documents_skips_call():
    use_handler(lambda request: pytest.fail("不应发请求"))
    assert await rr.rerank("q", [], 10) == []


async def test_retries_429_and_5xx_then_succeeds():
    responses = [httpx.Response(429), httpx.Response(503), ok([{"index": 0, "relevance_score": 0.5}])]
    calls = []

    def handler(request):
        calls.append(1)
        return responses[len(calls) - 1]

    use_handler(handler)
    assert await rr.rerank("q", ["a"], 1, sleep=no_sleep) == [(0, 0.5)]
    assert len(calls) == 3


async def test_4xx_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"message": "invalid key"})

    use_handler(handler)
    with pytest.raises(rr.RerankError):
        await rr.rerank("q", ["a"], 1, sleep=no_sleep)
    assert len(calls) == 1


async def test_transport_error_retried_then_raises():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("down", request=request)

    use_handler(handler)
    with pytest.raises(rr.RerankError):
        await rr.rerank("q", ["a"], 1, sleep=no_sleep)
    assert len(calls) == 3


async def test_index_out_of_range_raises():
    use_handler(lambda request: ok([{"index": 5, "relevance_score": 0.5}]))
    with pytest.raises(rr.RerankError):
        await rr.rerank("q", ["a"], 1, sleep=no_sleep)


async def test_build_client_uses_settings(monkeypatch):
    monkeypatch.setenv("RERANK_API_KEY", "secret-r")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rr.example/v1")
    for k, v in {"CHAT_BASE_URL": "https://c/v1", "CHAT_MODEL": "m", "CHAT_API_KEY": "k",
                 "DATABASE_URL": "mysql+asyncmy://u:p@h:1/d", "EMBED_API_KEY": "e",
                 "MILVUS_URI": "http://m:1"}.items():
        monkeypatch.setenv(k, v)
    client = rr.build_rerank_client(Settings(_env_file=None))
    try:
        assert str(client.base_url) == "https://rr.example/v1/"
        assert client.headers["Authorization"] == "Bearer secret-r"
    finally:
        await client.aclose()


async def test_blocked_reranker_by_default():
    # conftest 的 autouse fixture 已设置 BlockedReranker。
    with pytest.raises(RuntimeError, match="重排"):
        await rr.rerank("q", ["a"], 1)
```

`tests/conftest.py`：

```python
class BlockedReranker:
    """测试访问真实重排接口时立即失败。"""

    def __getattr__(self, name):
        raise RuntimeError("测试未替换重排客户端")
```

并在 `_isolate_knowledge` 中加 `rerank_mod.set_rerank_client(BlockedReranker())`，teardown 中加 `rerank_mod.set_rerank_client(None)`（`from app.knowledge import rerank as rerank_mod`）。

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_rerank.py -q`
Expected: FAIL（`ModuleNotFoundError: app.knowledge.rerank`）。

- [ ] **Step 3: 实现**

`app/knowledge/rerank.py`：

```python
"""硅基流动 /rerank 客户端。请求 {model, query, documents, top_n}，响应 results[].index/relevance_score。"""

import asyncio
import random
from typing import Any

import httpx

from app.config import (
    RERANK_MAX_ATTEMPTS,
    RERANK_MODEL,
    RERANK_RETRY_BASE_DELAY,
    RERANK_RETRY_MAX_DELAY,
    RERANK_TIMEOUT_SECONDS,
    Settings,
    get_settings,
)
from app.retry import retry_async

_client: Any | None = None


class RerankError(RuntimeError):
    """重排失败（重试用尽、4xx、响应无效）。"""


class _RetryableStatus(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")


def build_rerank_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.rerank_base_url,
        headers={"Authorization": f"Bearer {settings.rerank_api_key.get_secret_value()}"},
        timeout=RERANK_TIMEOUT_SECONDS,
    )


def get_rerank_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = build_rerank_client(get_settings())
    return _client


def set_rerank_client(client) -> None:
    global _client
    _client = client


async def close_rerank() -> None:
    global _client
    if isinstance(_client, httpx.AsyncClient):
        await _client.aclose()
    _client = None


async def rerank(
    query: str, documents: list[str], top_n: int, *, sleep=asyncio.sleep, rand=random.random
) -> list[tuple[int, float]]:
    if not documents:
        return []
    client = get_rerank_client()
    body = {"model": RERANK_MODEL, "query": query, "documents": documents, "top_n": top_n,
            "return_documents": False}

    async def call() -> dict:
        resp = await client.post("/rerank", json=body)
        # 429 和 5xx 是临时错误，重试。其他 4xx 不重试。
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _RetryableStatus(resp.status_code)
        resp.raise_for_status()
        return resp.json()

    try:
        data = await retry_async(
            call,
            attempts=RERANK_MAX_ATTEMPTS,
            base_delay=RERANK_RETRY_BASE_DELAY,
            max_delay=RERANK_RETRY_MAX_DELAY,
            retry_on=(httpx.TransportError, _RetryableStatus),
            sleep=sleep,
            rand=rand,
        )
        results = [(int(r["index"]), float(r["relevance_score"])) for r in data["results"]]
    except (httpx.HTTPError, _RetryableStatus, KeyError, TypeError, ValueError) as exc:
        raise RerankError("重排失败") from exc
    if any(not 0 <= i < len(documents) for i, _ in results):
        raise RerankError("重排结果下标越界")
    return sorted(results, key=lambda r: r[1], reverse=True)[:top_n]
```

`httpx.TimeoutException` 是 `httpx.TransportError` 的子类，已在重试范围内。

`app/main.py` 的 lifespan `finally` 中，在 `close_milvus()` 之后调用 `close_rerank()`（两者都执行：用嵌套 `try/finally`）。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_rerank.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/knowledge/rerank.py app/main.py tests/conftest.py tests/test_rerank.py
git commit -m "feat(ch04): SiliconFlow rerank client with exponential backoff"
```

---

### Task 6: 检索管线与 `query_faq` 新契约

**Files:**
- Modify: `app/knowledge/retrieval.py`（重写在线检索；`search_by_vector` 保留给挖掘去重）
- Modify: `app/tools/faq.py`
- Modify: `app/tools/registry.py`
- Modify: `app/tools/executor.py`（`ToolOutcome.data`）
- Modify: `app/config.py`（删除 `FAQ_MIN_SCORE`、`FAQ_MAX_RESULTS`）
- Delete: `evals/run_retrieval_eval.py`
- Modify: `evals/run_tool_selection_eval.py`、`evals/tool_selection_samples.jsonl`
- Test: `tests/test_retrieval.py`（重写）、`tests/test_tools.py`、`tests/test_executor.py`、`tests/test_config.py`

**Interfaces:**
- Consumes: Task 2 的 `search_dense`、`search_bm25`、`search_hybrid`、`build` 后的集合；Task 4 的 `understand`、`get_lexicon`、`dense_query`、`bm25_query`、`QueryPlan`；Task 5 的 `rerank`、`RerankError`。
- Produces（`app/knowledge/retrieval.py`）：

```python
STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")

@dataclass(frozen=True)
class EvidenceItem:
    chunk_id: int
    section_path: str
    question: str
    answer: str
    score: float

@dataclass(frozen=True)
class Retrieval:
    plan: QueryPlan
    ranked: list[EvidenceItem]     # 门槛前、排列前，按分数降序，最多 EVIDENCE_TOP_N 条
    evidence: list[EvidenceItem]   # 门槛后、首尾排列后

def build_filter(category: str | None, exclude_mined: bool) -> str
def interleave(items: list) -> list
def source_key(section_path: str, questions: str) -> str
async def retrieve(question: str, strategy: str = "hybrid_rerank", *, plan: QueryPlan | None = None,
                   exclude_mined: bool = False, min_score: float = RERANK_MIN_SCORE) -> Retrieval
async def search_by_vector(vector, *, limit, min_score) -> list[tuple[KnowledgeChunk, float]]  # 不变
```

- Produces（`app/tools/executor.py`）：`ToolOutcome.data: Any = None`。成功时为工具原始返回值。
- Produces（`query_faq`）：入参 `question`；返回 `{"evidence": [asdict(EvidenceItem), ...]}`，顺序为首尾排列后的顺序。

- [ ] **Step 1: 写失败的测试**

`tests/test_retrieval.py`（整体重写；`search_by_vector` 的原有测试保留并改用 `upsert_entities`）：

```python
from dataclasses import asdict

import pytest

from app.config import GENERAL_CATEGORY
from app.knowledge import milvus as m
from app.knowledge import rerank as rr
from app.knowledge import retrieval as r
from app.knowledge.chunking import knowledge_text, product_category_of
from app.knowledge.embeddings import get_embeddings, set_embeddings
from app.knowledge.milvus import Entity
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk
from app.schemas import QueryPlan
from tests.fakes import FakeEmbeddings, unit

pytestmark = pytest.mark.anyio


def spec(path, answer, content_type="manual", status="done"):
    parts = path.split(" > ")
    return dict(category=" > ".join(parts[:-1]) or parts[0], questions=parts[-1], answer=answer,
                section_path=path, content_type=content_type, status=status)


async def seed(db, specs, *, milvus_ids=None):
    """写 MySQL（按 status 标记 done），并把全部行写入 Milvus。返回 id 列表。"""
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk(x["category"], x["questions"], x["answer"], x["section_path"], x["content_type"], False)
            for x in specs
        ])
        await knowledge.mark_done(s, [row.id for row, x in zip(rows, specs) if x["status"] == "done"])
        await s.commit()
    emb = get_embeddings()
    ents = []
    for row, x in zip(rows, specs):
        txt = knowledge_text(x["category"], x["questions"], x["answer"])
        ents.append(Entity(row.id, await emb.aembed_query(txt), txt,
                           product_category_of(x["section_path"]), x["content_type"]))
    await m.upsert_entities(ents)
    return [row.id for row in rows]


def plan(text, category=None):
    return QueryPlan(standard_query=text, product_category=category)


@pytest.mark.parametrize("n, expected", [
    (0, []), (1, [1]), (2, [1, 2]), (3, [1, 3, 2]), (5, [1, 3, 5, 4, 2]), (6, [1, 3, 5, 6, 4, 2]),
])
def test_interleave_positions(n, expected):
    assert r.interleave(list(range(1, n + 1))) == expected


@pytest.mark.parametrize("category, exclude, expected", [
    (None, False, ""),
    ("蓝牙耳机", False, 'product_category in ["蓝牙耳机", "通用"]'),
    (None, True, 'content_type != "mined"'),
    ("台灯", True, 'product_category in ["台灯", "通用"] and content_type != "mined"'),
])
def test_build_filter(category, exclude, expected):
    assert r.build_filter(category, exclude) == expected


def test_source_key():
    assert r.source_key("退货政策 > 退款 > 退款时间", "退款时间") == "退货政策 > 退款 > 退款时间"
    assert r.source_key("常见问答 > 运费", "运费怎么算？") == "常见问答 > 运费 > 运费怎么算？"


async def test_bm25_hits_normalized_model_before_sibling(db, milvus):
    ids = await seed(db, [
        spec("商品手册 > 蓝牙耳机 > X3 续航", "单次续航 6 小时"),
        spec("商品手册 > 蓝牙耳机 > X3 Pro 续航", "单次续航 8 小时"),
        spec("退货政策 > 退款 > 退款时间", "原路退回", "policy"),
    ])
    res = await r.retrieve("x3pro续航", "bm25", plan=plan("X3 Pro 续航多久", "蓝牙耳机"))
    assert res.ranked[0].chunk_id == ids[1]


async def test_dense_orders_by_cosine_and_skips_pending_and_missing(db, milvus):
    set_embeddings(FakeEmbeddings([("运费", unit(0)), ("退款", unit(1))]))
    ids = await seed(db, [
        spec("退货政策 > 运费 > 运费说明", "运费 8 元", "policy"),
        spec("退货政策 > 退款 > 退款时间", "退款原路", "policy"),
        spec("退货政策 > 运费 > 偏远运费", "运费 20 元", "policy", status="pending"),
    ])
    await m.upsert_entities([Entity(999999, unit(0), "运费", GENERAL_CATEGORY, "policy")])
    res = await r.retrieve("q", "dense", plan=plan("运费"))
    got = [e.chunk_id for e in res.ranked]
    assert got[0] == ids[0] and ids[2] not in got and 999999 not in got


async def test_category_filter_and_exclude_mined(db, milvus):
    ids = await seed(db, [
        spec("商品手册 > 蓝牙耳机 > X3 保修", "保修 180 天"),
        spec("商品手册 > 羊毛衫 > W1 保修", "保修 90 天"),
        spec("退货政策 > 保修 > 保修期", "保修 从签收算", "policy"),
        spec("对话挖掘 > 售后维修 > 保修多久", "保修一年", "mined"),
    ])
    res = await r.retrieve("q", "bm25", plan=plan("保修", "蓝牙耳机"), exclude_mined=True)
    assert sorted(e.chunk_id for e in res.ranked) == sorted([ids[0], ids[2]])


async def test_hybrid_returns_rrf_without_rerank(db, milvus, monkeypatch):
    monkeypatch.setattr(rr, "rerank", lambda *a, **k: pytest.fail("hybrid 不应重排"))
    ids = await seed(db, [spec("退货政策 > 运费 > 运费说明", "运费 8 元", "policy")])
    res = await r.retrieve("q", "hybrid", plan=plan("运费"))
    assert [e.chunk_id for e in res.ranked] == ids
    assert res.evidence == res.ranked


async def test_hybrid_rerank_threshold_and_interleave(db, milvus, monkeypatch):
    ids = await seed(db, [
        spec("退货政策 > 运费 > A", "运费 a", "policy"),
        spec("退货政策 > 运费 > B", "运费 b", "policy"),
        spec("退货政策 > 运费 > C", "运费 c", "policy"),
        spec("退货政策 > 运费 > D", "运费 d", "policy"),
    ])
    seen = {}
    scores = {"运费 a": 0.9, "运费 b": 0.1, "运费 c": 0.8, "运费 d": 0.7}

    async def fake_rerank(query, documents, top_n, **kw):
        seen["query"], seen["docs"], seen["top_n"] = query, documents, top_n
        out = [(i, next(v for k, v in scores.items() if k in d)) for i, d in enumerate(documents)]
        return sorted(out, key=lambda x: x[1], reverse=True)[:top_n]

    monkeypatch.setattr(rr, "rerank", fake_rerank)
    res = await r.retrieve("q", "hybrid_rerank", plan=plan("邮费怎么算"), min_score=0.3)
    by_answer = {e.answer: e for e in res.ranked}
    assert [e.answer for e in res.ranked] == ["运费 a", "运费 c", "运费 d", "运费 b"]
    assert [e.answer for e in res.evidence] == ["运费 a", "运费 d", "运费 c"]
    assert by_answer["运费 a"].score == 0.9
    assert seen["query"] == "邮费怎么算" and seen["top_n"] == 10
    assert all(d.startswith("退货政策 > 运费\n") for d in seen["docs"])  # knowledge_text 格式


async def test_hybrid_rerank_propagates_rerank_error(db, milvus, monkeypatch):
    await seed(db, [spec("退货政策 > 运费 > A", "运费 a", "policy")])

    async def boom(*a, **k):
        raise rr.RerankError("x")

    monkeypatch.setattr(rr, "rerank", boom)
    with pytest.raises(rr.RerankError):
        await r.retrieve("q", "hybrid_rerank", plan=plan("运费"))


async def test_retrieve_calls_understand_without_plan(db, milvus, monkeypatch):
    called = []

    async def fake_understand(question, **kw):
        called.append(question)
        return plan("运费")

    monkeypatch.setattr(r, "understand", fake_understand)
    await r.retrieve("邮费多少", "bm25")
    assert called == ["邮费多少"]


async def test_query_faq_returns_interleaved_evidence(monkeypatch):
    from app.tools.faq import query_faq
    item = r.EvidenceItem(7, "退货政策 > 运费 > A", "A", "运费 8 元", 0.9)

    async def fake_retrieve(question, *a, **k):
        assert question == "邮费多少"
        return r.Retrieval(plan("运费"), [item], [item])

    monkeypatch.setattr("app.tools.faq.retrieve", fake_retrieve)
    assert await query_faq.ainvoke({"question": "邮费多少"}) == {"evidence": [asdict(item)]}


def test_query_faq_contract():
    from app.tools.faq import query_faq
    schema = query_faq.args_schema.model_json_schema()
    assert list(schema["properties"]) == ["question"]
    assert schema["properties"]["question"]["maxLength"] == 200
    assert "不要改写" in schema["properties"]["question"]["description"]
    assert "商品型号" in query_faq.description
```

`tests/test_tools.py`：`test_registry_lists_five_tools_and_flags` 中 `query_faq` 的断言改为：

```python
    faq = reg.get("query_faq")
    assert faq.retryable is False and faq.timeout == 20
    assert reg.get("query_order").retryable is True and reg.get("query_order").timeout == 5
```

`tests/test_executor.py` 追加（使用该文件已有的 `make_registry`、`no_sleep`）：

```python
async def test_success_outcome_keeps_raw_data():
    reg, _ = make_registry()
    [out] = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                     conversation_id=7, registry=reg, sleep=no_sleep)
    assert out.ok and out.data == {"order_id": "1", "pad": ""}


async def test_failure_outcome_has_no_data():
    reg, _ = make_registry(fail=True, retryable=False)
    [out] = await execute_tool_calls([{"id": "a", "name": "echo", "args": {"order_id": "1"}}],
                                     conversation_id=7, registry=reg, sleep=no_sleep)
    assert out.ok is False and out.data is None
```

`tests/test_config.py`：删除 `FAQ_MAX_RESULTS`、`FAQ_MIN_SCORE` 的断言，追加 `assert not hasattr(config, "FAQ_MIN_SCORE") and not hasattr(config, "FAQ_MAX_RESULTS")`。

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_retrieval.py tests/test_tools.py tests/test_executor.py tests/test_config.py -q`
Expected: FAIL（`AttributeError: module 'app.knowledge.retrieval' has no attribute 'interleave'` 等）。

- [ ] **Step 3: 实现**

`app/knowledge/retrieval.py`（替换 `search_with_scores`、`search_faq`；保留 `search_by_vector`）：

```python
from dataclasses import dataclass, replace

from app.config import EVIDENCE_TOP_N, FUSED_LIMIT, GENERAL_CATEGORY, RECALL_LEG_LIMIT, RERANK_MIN_SCORE
from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge import rerank as rerank_mod
from app.knowledge.chunking import PATH_SEP, knowledge_text
from app.knowledge.embeddings import get_embeddings
from app.knowledge.milvus import search_bm25, search_dense, search_hybrid, search_vectors
from app.knowledge.query import bm25_query, dense_query, get_lexicon, understand
from app.repositories import knowledge
from app.schemas import QueryPlan

STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
FAQ_TABLE_SOURCE = "常见问答"


@dataclass(frozen=True)
class EvidenceItem:
    chunk_id: int
    section_path: str
    question: str
    answer: str
    score: float


@dataclass(frozen=True)
class Retrieval:
    plan: QueryPlan
    ranked: list[EvidenceItem]
    evidence: list[EvidenceItem]


def build_filter(category: str | None, exclude_mined: bool) -> str:
    parts = []
    if category:
        # 通用政策也必须能召回。
        parts.append(f'product_category in ["{category}", "{GENERAL_CATEGORY}"]')
    if exclude_mined:
        parts.append('content_type != "mined"')
    return " and ".join(parts)


def interleave(items: list) -> list:
    """按排名交替放首尾：第 1 名放首位，第 2 名放末位，依次向中间填。"""
    head, tail = [], []
    for i, item in enumerate(items):
        (head if i % 2 == 0 else tail).append(item)
    return head + tail[::-1]


def source_key(section_path: str, questions: str) -> str:
    """评估集的来源键。常见问答同一分类下有多行，需要加上问题文本。"""
    if section_path.startswith(FAQ_TABLE_SOURCE + PATH_SEP):
        return f"{section_path}{PATH_SEP}{questions}"
    return section_path


async def _done_rows(ids: list[int]) -> dict[int, KnowledgeChunk]:
    async with get_sessionmaker()() as s:
        return await knowledge.get_done_by_ids(s, ids)


async def retrieve(
    question: str,
    strategy: str = "hybrid_rerank",
    *,
    plan: QueryPlan | None = None,
    exclude_mined: bool = False,
    min_score: float = RERANK_MIN_SCORE,
) -> Retrieval:
    if strategy not in STRATEGIES:
        raise ValueError(f"未知的检索策略：{strategy}")
    plan = plan or await understand(question)
    lex = get_lexicon()
    flt = build_filter(plan.product_category, exclude_mined)
    text = bm25_query(plan.standard_query, lex)
    vector = None
    if strategy != "bm25":
        vector = await get_embeddings().aembed_query(dense_query(plan.standard_query, lex))
    if strategy == "dense":
        hits = await search_dense(vector, EVIDENCE_TOP_N, flt)
    elif strategy == "bm25":
        hits = await search_bm25(text, EVIDENCE_TOP_N, flt)
    else:
        limit = FUSED_LIMIT if strategy == "hybrid_rerank" else EVIDENCE_TOP_N
        hits = await search_hybrid(vector, text, leg_limit=RECALL_LEG_LIMIT, limit=limit, filter=flt)
    rows = await _done_rows([i for i, _ in hits])
    # Milvus 有、MySQL 没有（或仍为 pending）的 id 跳过。
    hits = [(i, sc) for i, sc in hits if i in rows]
    items = [EvidenceItem(i, rows[i].section_path or "", rows[i].questions, rows[i].answer, sc) for i, sc in hits]
    if strategy != "hybrid_rerank":
        ranked = items[:EVIDENCE_TOP_N]
        return Retrieval(plan, ranked, interleave(ranked))
    docs = [knowledge_text(rows[i].category, rows[i].questions, rows[i].answer) for i, _ in hits]
    # 重排用标准问法，不用追加了同义词的 BM25 查询。
    scored = await rerank_mod.rerank(plan.standard_query, docs, EVIDENCE_TOP_N)
    ranked = [replace(items[idx], score=score) for idx, score in scored]
    kept = [e for e in ranked if e.score >= min_score]
    return Retrieval(plan, ranked, interleave(kept))
```

`search_by_vector` 保持原样（用 `search_vectors`），文件中删除 `search_with_scores`、`search_faq` 和对 `FAQ_*` 的导入。

`app/tools/faq.py`：

```python
from dataclasses import asdict

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.knowledge.retrieval import retrieve


class QueryFaqArgs(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=200,
        description="用户关于店铺政策、商品型号参数或使用问题的原话，可以去掉订单号等个人信息，不要改写",
    )


@tool("query_faq", args_schema=QueryFaqArgs)
async def query_faq(question: str) -> dict:
    """查询店铺知识库，例如退换货政策、运费、发票、维修流程、商品型号的参数和常见故障。"""
    result = await retrieve(question)
    return {"evidence": [asdict(e) for e in result.evidence]}
```

`app/tools/registry.py`：`query_faq` 单独注册：

```python
    for registered_tool in (query_order, query_product, query_logistics):
        registry.register(ToolSpec(registered_tool, retryable=True, timeout=TOOL_TIMEOUT_SECONDS))
    # 嵌入、Milvus、重排、改写各自已有指数回退重试。外层不再重试，避免放大等待时间。
    registry.register(ToolSpec(query_faq, retryable=False, timeout=QUERY_FAQ_TIMEOUT_SECONDS))
```

`app/tools/executor.py`：`ToolOutcome` 增加 `data: Any = None`（`from typing import Any`）；`_make_outcome` 增加关键字参数 `data: Any = None` 并传入；成功分支调用 `_make_outcome(call_id, name, ok=True, content=content, data=result)`。

`app/config.py`：删除 `FAQ_MAX_RESULTS`、`FAQ_MIN_SCORE` 两行及其注释。

删除 `evals/run_retrieval_eval.py`（`evals/retrieval_samples.jsonl` 保留到 Task 11 合并）。

`evals/run_tool_selection_eval.py`：字段 `faq_keyword` 改为 `faq_terms`（`list[str] | None`）；判定改为"模型调用了 `query_faq`，且 `question` 参数包含 `faq_terms` 中的每一项"；`keywords` 变量和输出文字改为 `questions` / "query_faq question"；汇总行改为 `FAQ 原话包含率`。`evals/tool_selection_samples.jsonl`：每行 `"faq_keyword": "X"` 改为 `"faq_terms": ["X"]`，`null` 保持为 `null`。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app evals tests
git commit -m "feat(ch04): hybrid retrieval pipeline with rerank threshold and new query_faq contract"
```

---

### Task 7: 证据合并、自评与问题池

**Files:**
- Create: `app/services/grounding.py`
- Create: `app/repositories/low_confidence.py`
- Modify: `app/schemas.py`（新增 `SelfCheck`）
- Modify: `app/prompts.py`（新增 `REFUSAL_PREFIX`、`SELF_CHECK_SYSTEM_PROMPT`、`self_check_prompt`）
- Modify: `app/llm.py`（新增 `get_self_checker`）
- Modify: `tests/conftest.py`（`_block_llm_runnables` 增加 `get_self_checker`）
- Test: `tests/test_grounding.py`、`tests/test_repositories.py`

**Interfaces:**
- Consumes: Task 1 的 `LowConfidenceQuestion`；Task 6 的 `query_faq` 返回值格式 `{"evidence": [{"chunk_id", "section_path", "question", "answer", "score"}]}`。
- Produces（`app/prompts.py`）：`REFUSAL_PREFIX = "抱歉，这个问题我没有在知识库中找到可靠依据。"`。
- Produces（`app/schemas.py`）：

```python
class SelfCheck(BaseModel):
    useful: bool = Field(description="证据是否足以回答问题的全部要点")
    reason: str = Field(description="够用时写依据的证据编号；不够用时写缺了什么")
```

- Produces（`app/services/grounding.py`）：

```python
EMPTY_EVIDENCE_REASON = "检索证据低于置信度门槛"
SELF_CHECK_FAILED_REASON = "自评调用失败，按通过处理"
REFUSED_CONTENT: str   # {"ok": true, "data": {"evidence": [], "answerable": false}} 的 JSON 文本

@dataclass(frozen=True)
class Citation:
    n: int
    chunk_id: int
    section_path: str
    question: str
    answer: str
    def to_dict(self) -> dict

@dataclass
class Evidence:
    questions: list[str]
    citations: list[Citation]
    by_call: dict[str, list[Citation]]

def collect_evidence(calls: list[tuple[str, str, dict]]) -> Evidence   # (call_id, question, query_faq 返回值)
def render_evidence(citations: list[Citation]) -> str
def format_evidence(citations: list[Citation]) -> str                  # 自评和裁判的输入
def parse_citations(text: str) -> list[int]                           # 去重，保持出现顺序
async def self_check(questions: list[str], citations: list[Citation], *,
                     checker: Runnable | None = None) -> SelfCheck
async def record_low_confidence(conversation_id: int, raw_question: str, reason: str) -> None
```

- Produces（`app/repositories/low_confidence.py`）：`async def add(s, *, conversation_id: int | None, raw_question: str, source: str, reason: str | None) -> LowConfidenceQuestion`
- Produces（`app/llm.py`）：`get_self_checker() -> Runnable`，输入 `{"question": str, "evidence": str}`。

- [ ] **Step 1: 写失败的测试**

`tests/test_grounding.py`：

```python
import json

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import Conversation, LowConfidenceQuestion
from app.repositories import low_confidence
from app.schemas import SelfCheck
from app.services import grounding as g

pytestmark = pytest.mark.anyio


def item(cid, path="退货政策 > 运费 > 运费说明", q="运费说明", a="满 99 元免运费", score=0.9):
    return {"chunk_id": cid, "section_path": path, "question": q, "answer": a, "score": score}


def test_collect_evidence_dedupes_and_numbers_across_calls():
    ev = g.collect_evidence([
        ("c1", "运费", {"evidence": [item(1), item(2)]}),
        ("c2", "发票", {"evidence": [item(2), item(3)]}),
    ])
    assert ev.questions == ["运费", "发票"]
    assert [(c.n, c.chunk_id) for c in ev.citations] == [(1, 1), (2, 2), (3, 3)]
    assert [c.n for c in ev.by_call["c1"]] == [1, 2]
    assert [c.n for c in ev.by_call["c2"]] == [3]


def test_render_evidence_hides_ids_and_scores():
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    assert json.loads(g.render_evidence(ev.citations)) == {"ok": True, "data": {"evidence": [
        {"n": 1, "section_path": "退货政策 > 运费 > 运费说明", "content": "问：运费说明\n答：满 99 元免运费"},
    ]}}


def test_refused_content():
    assert json.loads(g.REFUSED_CONTENT) == {"ok": True, "data": {"evidence": [], "answerable": False}}


def test_citation_to_dict_matches_faith_cases_format():
    c = g.Citation(1, 7, "p", "q", "a")
    assert c.to_dict() == {"n": 1, "chunk_id": 7, "section_path": "p", "question": "q", "answer": "a"}


def test_format_evidence():
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7), item(8, path="p2", q="q2", a="a2")]})])
    assert g.format_evidence(ev.citations) == (
        "[1] 退货政策 > 运费 > 运费说明\n问：运费说明\n答：满 99 元免运费\n\n[2] p2\n问：q2\n答：a2"
    )


def test_parse_citations():
    assert g.parse_citations("可以退[1]。运费 8 元[3][1]。[x] 见[12]") == [1, 3, 12]
    assert g.parse_citations("没有引用") == []


def checker(result=None, exc=None, seen=None):
    def run(inputs):
        if seen is not None:
            seen.append(inputs)
        if exc:
            raise exc
        return {"parsed": result, "raw": "raw"}
    return RunnableLambda(run)


async def test_self_check_empty_evidence_skips_checker():
    res = await g.self_check(["X9 防水吗"], [], checker=checker(exc=AssertionError("不应调用")))
    assert res == SelfCheck(useful=False, reason=g.EMPTY_EVIDENCE_REASON)


async def test_self_check_passes_questions_and_evidence():
    seen = []
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    res = await g.self_check(["运费", "发票"], ev.citations,
                             checker=checker(SelfCheck(useful=False, reason="缺发票"), seen=seen))
    assert res.useful is False and res.reason == "缺发票"
    assert seen == [{"question": "运费\n发票", "evidence": g.format_evidence(ev.citations)}]


@pytest.mark.parametrize("bad", [checker(exc=TimeoutError("slow")), checker(result=None)])
async def test_self_check_failure_fails_open(bad, caplog):
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    res = await g.self_check(["运费"], ev.citations, checker=bad)
    assert res == SelfCheck(useful=True, reason=g.SELF_CHECK_FAILED_REASON)
    assert "自评调用失败" in caplog.text


async def test_self_check_factory_is_blocked_in_tests():
    ev = g.collect_evidence([("c1", "运费", {"evidence": [item(7)]})])
    with pytest.raises(RuntimeError, match="get_self_checker"):
        await g.self_check(["运费"], ev.citations)


async def test_record_low_confidence_writes_row(db):
    async with db() as s:
        conv = Conversation(user_id="u1")
        s.add(conv)
        await s.commit()
    await g.record_low_confidence(conv.id, "X9 能无线充电吗？急！", "证据没写无线充电")
    async with db() as s:
        rows = list(await s.scalars(select(LowConfidenceQuestion)))
    assert [(r.conversation_id, r.raw_question, r.source, r.reason) for r in rows] == [
        (conv.id, "X9 能无线充电吗？急！", "self_check", "证据没写无线充电"),
    ]


async def test_record_low_confidence_swallows_errors(db, monkeypatch, caplog):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(low_confidence, "add", boom)
    await g.record_low_confidence(1, "q", "r")
    assert "低置信度问题入池失败" in caplog.text
```

`tests/test_repositories.py` 追加：

```python
async def test_low_confidence_add(db):
    async with db() as s:
        row = await low_confidence.add(s, conversation_id=None, raw_question="q", source="self_check", reason=None)
        await s.commit()
        await s.refresh(row)
    assert row.id > 0 and row.created_at is not None and row.conversation_id is None
```

`tests/conftest.py` 的 `_block_llm_runnables` 增加：

```python
    from app.services import grounding
    monkeypatch.setattr(grounding, "get_self_checker", _blocked_factory("get_self_checker"))
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_grounding.py tests/test_repositories.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.grounding`）。

- [ ] **Step 3: 实现**

`app/prompts.py` 追加（放在 `CHAT_SYSTEM_TEMPLATE` 之前，Task 8 引用它）：

```python
# 拒答句。System Prompt、评估的拒答判定和验收脚本共用。
REFUSAL_PREFIX = "抱歉，这个问题我没有在知识库中找到可靠依据。"

SELF_CHECK_SYSTEM_PROMPT = """你是售后知识库的证据审核员。判断给定的知识库证据是否足以回答用户的问题。

## 判定规则
1. 证据直接写明了问题所需的事实（条件、时限、数字、步骤、型号参数），判为够用。
2. 问题有几个要点时，每个要点都有证据，才判为够用。
3. 证据只是同一话题，但没有写到用户问的那一点，判为不够用。例如问 X3 Pro 是否防水，证据只写了 X3 Pro 的续航。
4. 证据写的是另一个型号或另一个品类，判为不够用。
5. 用户要求承诺（例如"一定""保证""明天能到吗"），而证据给出了一般规则或时限，判为够用。回答阶段会按规则措辞。
6. 不使用证据以外的常识。

## reason
写一句话。够用时写依据的证据编号；不够用时写缺了什么。"""

self_check_prompt = ChatPromptTemplate.from_messages([
    ("system", SELF_CHECK_SYSTEM_PROMPT),
    ("human", "问题：\n{question}\n\n证据：\n{evidence}"),
])
```

`app/llm.py` 追加：

```python
@lru_cache
def get_self_checker() -> Runnable:
    model = build_extract_model(get_settings())
    return self_check_prompt | model.with_structured_output(
        SelfCheck, method="function_calling", include_raw=True
    )
```

`app/repositories/low_confidence.py`：

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LowConfidenceQuestion


async def add(
    s: AsyncSession, *, conversation_id: int | None, raw_question: str, source: str, reason: str | None
) -> LowConfidenceQuestion:
    row = LowConfidenceQuestion(
        conversation_id=conversation_id, raw_question=raw_question, source=source, reason=reason,
    )
    s.add(row)
    await s.flush()
    return row
```

`app/services/grounding.py`：

```python
"""证据合并、渲染、自评与入池。线上聊天和评估共用。"""

import json
import logging
import re
from dataclasses import asdict, dataclass

from langchain_core.runnables import Runnable

from app.db.engine import get_sessionmaker
from app.llm import get_self_checker
from app.repositories import low_confidence
from app.schemas import SelfCheck

logger = logging.getLogger(__name__)

EMPTY_EVIDENCE_REASON = "检索证据低于置信度门槛"
SELF_CHECK_FAILED_REASON = "自评调用失败，按通过处理"
REFUSED_CONTENT = json.dumps(
    {"ok": True, "data": {"evidence": [], "answerable": False}}, ensure_ascii=False
)
_CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Citation:
    n: int
    chunk_id: int
    section_path: str
    question: str
    answer: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Evidence:
    questions: list[str]
    citations: list[Citation]
    by_call: dict[str, list[Citation]]


def collect_evidence(calls: list[tuple[str, str, dict]]) -> Evidence:
    """按调用顺序拼接证据。chunk_id 重复时只保留第一次出现的那条。编号全局连续。"""
    seen: set[int] = set()
    citations: list[Citation] = []
    by_call: dict[str, list[Citation]] = {}
    for call_id, _, data in calls:
        mine = []
        for e in data.get("evidence", []):
            if e["chunk_id"] in seen:
                continue
            seen.add(e["chunk_id"])
            c = Citation(len(citations) + 1, e["chunk_id"], e["section_path"], e["question"], e["answer"])
            citations.append(c)
            mine.append(c)
        by_call[call_id] = mine
    return Evidence([q for _, q, _ in calls], citations, by_call)


def render_evidence(citations: list[Citation]) -> str:
    """模型看到的工具消息内容。不含 chunk_id 和分数。"""
    return json.dumps({"ok": True, "data": {"evidence": [
        {"n": c.n, "section_path": c.section_path, "content": f"问：{c.question}\n答：{c.answer}"}
        for c in citations
    ]}}, ensure_ascii=False)


def format_evidence(citations: list[Citation]) -> str:
    return "\n\n".join(f"[{c.n}] {c.section_path}\n问：{c.question}\n答：{c.answer}" for c in citations)


def parse_citations(text: str) -> list[int]:
    out: list[int] = []
    for m in _CITATION_RE.finditer(text):
        n = int(m.group(1))
        if n not in out:
            out.append(n)
    return out


async def self_check(
    questions: list[str], citations: list[Citation], *, checker: Runnable | None = None
) -> SelfCheck:
    if not citations:
        return SelfCheck(useful=False, reason=EMPTY_EVIDENCE_REASON)
    # 工厂在 try 之外调用：配置错误和测试中未替换时立即暴露。
    checker = checker or get_self_checker()
    try:
        result = await checker.ainvoke(
            {"question": "\n".join(questions), "evidence": format_evidence(citations)}
        )
        if result["parsed"] is None:
            raise ValueError(f"自评结果无效：raw={result.get('raw')!r}")
        return result["parsed"]
    except Exception:
        # 证据已通过重排门槛，按通过处理。
        logger.exception("自评调用失败，按通过处理")
        return SelfCheck(useful=True, reason=SELF_CHECK_FAILED_REASON)


async def record_low_confidence(conversation_id: int, raw_question: str, reason: str) -> None:
    """独立事务。失败只记日志，不中断本轮。"""
    try:
        async with get_sessionmaker()() as s:
            await low_confidence.add(
                s, conversation_id=conversation_id, raw_question=raw_question,
                source="self_check", reason=reason,
            )
            await s.commit()
    except Exception:
        logger.exception("低置信度问题入池失败")
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_grounding.py tests/test_repositories.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/grounding.py app/repositories/low_confidence.py app/schemas.py app/prompts.py app/llm.py tests
git commit -m "feat(ch04): evidence numbering, self-check and low-confidence pool"
```

---

### Task 8: 聊天服务接入证据与 System Prompt

**Files:**
- Modify: `app/services/chat.py`
- Modify: `app/prompts.py`（`CHAT_SYSTEM_TEMPLATE`）
- Test: `tests/test_chat_api.py`、`tests/test_prompts.py`

**Interfaces:**
- Consumes: Task 6 的 `ToolOutcome.data`、`Retrieval`、`EvidenceItem`、`RerankError`；Task 7 的 `collect_evidence`、`render_evidence`、`REFUSED_CONTENT`、`self_check`、`record_low_confidence`、`parse_citations`、`EMPTY_EVIDENCE_REASON`、`REFUSAL_PREFIX`。
- Produces：SSE 事件 `citations`，数据 `{"items": [Citation.to_dict()...], "refused": bool}`，位于 `tool_end` 之后、第 2 次调用的 `token` 之前。只在本轮至少一个 `query_faq` 成功时发送。

- [ ] **Step 1: 写失败的测试**

`tests/test_chat_api.py` 追加（文件中已有 `chat`、`rows`、`parse_sse`；第 205–265 行使用 `{"keyword": ...}` 的旧测试不改，它们走参数校验失败分支，仍然有效）：

```python
from langchain_core.runnables import RunnableLambda
from sqlalchemy import func

from app.db.models import LowConfidenceQuestion
from app.knowledge.rerank import RerankError
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.prompts import REFUSAL_PREFIX
from app.repositories import low_confidence
from app.schemas import QueryPlan, SelfCheck
from app.services import grounding


def ev_item(cid, path="商品手册 > 蓝牙耳机 > X3 Pro 续航", q="X3 Pro 续航", a="单次续航 8 小时"):
    return EvidenceItem(cid, path, q, a, 0.9)


@pytest.fixture
def kb(monkeypatch):
    state = {"evidence": [], "by_question": {}, "check": SelfCheck(useful=True, reason="[1]"),
             "check_calls": [], "retrieve_error": None}

    async def fake_retrieve(question, *a, **k):
        if state["retrieve_error"] is not None:
            raise state["retrieve_error"]
        items = state["by_question"].get(question, state["evidence"])
        return Retrieval(QueryPlan(standard_query=question), items, items)

    def factory():
        def run(inputs):
            state["check_calls"].append(inputs)
            if isinstance(state["check"], Exception):
                raise state["check"]
            return {"parsed": state["check"], "raw": None}
        return RunnableLambda(run)

    monkeypatch.setattr("app.tools.faq.retrieve", fake_retrieve)
    monkeypatch.setattr(grounding, "get_self_checker", factory)
    return state


async def pool_rows(db):
    async with db() as s:
        return list(await s.scalars(select(LowConfidenceQuestion).order_by(LowConfidenceQuestion.id)))


def event(ev, name):
    return next(d for n, d in ev if n == name)


def tool_contents(rec_call):
    return {m.tool_call_id: m.content for m in rec_call["messages"] if isinstance(m, ToolMessage)}


async def test_faq_useful_sends_citations_and_renders_evidence(client, db, use_script, kb):
    kb["evidence"] = [ev_item(11)]
    rec = use_script(tools(("c1", "query_faq", {"question": "X3 Pro 能用多久"})), text("约 8 小时[1]"))
    _, ev = await chat(client, "X3 Pro 能用多久")
    names = [n for n, _ in ev]
    assert names.index("tool_end") < names.index("citations") < names.index("done")
    assert event(ev, "citations") == {"items": [{
        "n": 1, "chunk_id": 11, "section_path": "商品手册 > 蓝牙耳机 > X3 Pro 续航",
        "question": "X3 Pro 续航", "answer": "单次续航 8 小时",
    }], "refused": False}
    content = tool_contents(rec[1])["c1"]
    assert json.loads(content) == {"ok": True, "data": {"evidence": [{
        "n": 1, "section_path": "商品手册 > 蓝牙耳机 > X3 Pro 续航", "content": "问：X3 Pro 续航\n答：单次续航 8 小时",
    }]}}
    assert [m.content for m in await rows(db) if m.role == "tool"] == [content]
    assert kb["check_calls"][0]["question"] == "X3 Pro 能用多久"
    assert await pool_rows(db) == []


async def test_faq_not_useful_records_pool_and_refuses(client, db, use_script, kb):
    kb["evidence"] = [ev_item(11)]
    kb["check"] = SelfCheck(useful=False, reason="证据没写防水")
    rec = use_script(tools(("c1", "query_faq", {"question": "X3 Pro 防水吗"})),
                     text(REFUSAL_PREFIX + "建议转人工。"))
    _, ev = await chat(client, "X3 Pro 防水吗？着急")
    assert event(ev, "citations") == {"items": [], "refused": True}
    assert tool_contents(rec[1])["c1"] == grounding.REFUSED_CONTENT
    session_id = int(ev[0][1]["session_id"])
    assert [(r.conversation_id, r.raw_question, r.source, r.reason) for r in await pool_rows(db)] == [
        (session_id, "X3 Pro 防水吗？着急", "self_check", "证据没写防水"),
    ]
    assert [m.content for m in await rows(db) if m.role == "tool"] == [grounding.REFUSED_CONTENT]


async def test_empty_evidence_skips_checker_and_pools(client, db, use_script, kb):
    kb["evidence"] = []
    kb["check"] = AssertionError("不应调用自评")
    use_script(tools(("c1", "query_faq", {"question": "X9 能无线充电吗"})), text(REFUSAL_PREFIX))
    _, ev = await chat(client, "X9 能无线充电吗")
    assert kb["check_calls"] == []
    assert event(ev, "citations") == {"items": [], "refused": True}
    assert [r.reason for r in await pool_rows(db)] == [grounding.EMPTY_EVIDENCE_REASON]


async def test_self_check_failure_fails_open(client, db, use_script, kb):
    kb["evidence"] = [ev_item(11)]
    kb["check"] = RuntimeError("upstream")
    use_script(tools(("c1", "query_faq", {"question": "X3 Pro 能用多久"})), text("约 8 小时[1]"))
    _, ev = await chat(client, "X3 Pro 能用多久")
    assert event(ev, "citations")["refused"] is False
    assert await pool_rows(db) == []
    assert ev[-1][0] == "done"


async def test_two_faq_calls_number_continuously(client, db, use_script, kb):
    a, b, c = ev_item(1, q="A"), ev_item(2, q="B"), ev_item(3, q="C")
    kb["by_question"] = {"运费": [a, b], "发票": [b, c]}
    rec = use_script(
        tools(("c1", "query_faq", {"question": "运费"}), ("c2", "query_faq", {"question": "发票"})),
        text("答[1][3]"),
    )
    _, ev = await chat(client, "运费和发票")
    assert [(i["n"], i["chunk_id"]) for i in event(ev, "citations")["items"]] == [(1, 1), (2, 2), (3, 3)]
    contents = tool_contents(rec[1])
    assert [e["n"] for e in json.loads(contents["c1"])["data"]["evidence"]] == [1, 2]
    assert [e["n"] for e in json.loads(contents["c2"])["data"]["evidence"]] == [3]
    assert kb["check_calls"][0]["question"] == "运费\n发票"


async def test_mixed_order_and_faq_refused_still_answers_order(client, db, use_script, kb):
    kb["evidence"] = []
    rec = use_script(
        tools(("c1", "query_order", {"order_id": "1001"}), ("c2", "query_faq", {"question": "X9 能无线充电吗"})),
        text("订单已发货。" + REFUSAL_PREFIX),
    )
    _, ev = await chat(client, "订单 1001 到哪了，另外 X9 能无线充电吗")
    contents = tool_contents(rec[1])
    assert json.loads(contents["c1"])["ok"] is True and "1001" in contents["c1"]
    assert contents["c2"] == grounding.REFUSED_CONTENT
    assert ev[-1][0] == "done"


async def test_rerank_failure_gives_tool_error_without_pool(client, db, use_script, kb):
    kb["retrieve_error"] = RerankError("x")
    rec = use_script(tools(("c1", "query_faq", {"question": "运费"})), text("暂时查不到"))
    _, ev = await chat(client, "运费多少")
    assert "citations" not in [n for n, _ in ev]
    assert json.loads(tool_contents(rec[1])["c1"])["ok"] is False
    assert await pool_rows(db) == []
    assert ev[-1][0] == "done"


async def test_pool_write_failure_does_not_break_turn(client, db, use_script, kb, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(low_confidence, "add", boom)
    kb["evidence"] = []
    use_script(tools(("c1", "query_faq", {"question": "X9"})), text(REFUSAL_PREFIX))
    _, ev = await chat(client, "X9 怎么样")
    assert ev[-1][0] == "done"
    assert [m.role for m in await rows(db)] == ["user", "assistant", "tool", "assistant"]


async def test_out_of_range_citation_is_logged_not_blocked(client, db, use_script, kb, caplog):
    kb["evidence"] = [ev_item(11)]
    use_script(tools(("c1", "query_faq", {"question": "X3 Pro 能用多久"})), text("约 8 小时[7]"))
    _, ev = await chat(client, "X3 Pro 能用多久")
    assert ev[-1][0] == "done"
    assert "越界引用编号" in caplog.text


async def test_turn_without_faq_has_no_citations_event(client, db, use_script, kb):
    use_script(tools(("c1", "query_logistics", {"order_id": "1001"})), text("运输中"))
    _, ev = await chat(client, "订单 1001 的物流到哪了")
    assert "citations" not in [n for n, _ in ev]
```

`turn_rows` 写库顺序为 `user`、`assistant`（带 tool_calls）、各 `tool`、`assistant`（已核对 `app/services/history.py`）。

`tests/test_prompts.py` 追加：

```python
from app.prompts import REFUSAL_PREFIX


def test_system_prompt_ch04_rules():
    s = render_chat_system(date(2026, 10, 7))
    assert REFUSAL_PREFIX in s
    assert "[2]" in s and "[1][3]" in s
    assert "question 填用户关于这一点的原话" in s
    for phrase in ("不承诺退款到账的具体日期", "一定审核通过", "不承诺赔偿", "不承诺具体的发货或送达时间",
                   "不承诺保修范围外免费维修"):
        assert phrase in s
    assert "300 字" in s and "200 字" not in s
    assert "keyword" not in s and "时限除外" not in s
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_chat_api.py tests/test_prompts.py -q`
Expected: FAIL（没有 `citations` 事件；Prompt 缺少新规则）。

- [ ] **Step 3: 实现**

`app/prompts.py`：`CHAT_SYSTEM_TEMPLATE` 整体替换为下列内容。`REFUSAL_PREFIX` 用字符串拼接嵌入（它不含花括号），模板变量仍只有 `{shop_name}`、`{today}`：

```python
CHAT_SYSTEM_TEMPLATE = """你是{shop_name}的售后客服助手。今天是{today}。

## 职责
帮助用户处理退货、换货、退款、维修、投诉和售后咨询，并解答店铺政策和商品使用问题（运费、发票、账户、支付、商品型号的参数和故障等）。

## 工具使用
1. 订单、商品、物流，以及退换货、运费、发票、账户、支付等店铺政策、商品型号参数和使用问题，一律调用工具查询。只根据工具返回的数据回答，不编造。
2. 需要查询时直接调用工具，调用前不输出文字。
3. 查询知识库时，question 填用户关于这一点的原话，不要改写，不要替换为同义词。
4. 工具结果中 ok 为 false 时，如实告诉用户暂时查不到，建议稍后再试或转人工。
5. 用户明确要求人工，或投诉需要人工跟进时，调用 create_ticket 创建工单，并把工单号告诉用户。

## 引用
1. 使用知识库证据的句子，在句末标注证据编号 n，例如"签收后 7 天内可以无理由退货[2]。"
2. 只标注实际用到的编号。一句用到多条证据时，写成[1][3]。
3. 不编造编号。没有使用知识库的句子不标注。

## 拒答
知识库结果中 answerable 为 false，或证据没有写到用户所问的点时，以\"""" + REFUSAL_PREFIX + """\"开头，然后建议用户转人工。不根据常识推测。同一条消息中的其他问题照常回答。

## 行为约束
1. 不编造订单状态、物流信息、店铺政策和商品参数。工具没有返回的信息，直接说明不知道。
2. 超出你能处理的范围时，建议用户转人工。
3. 只回答与本店购物和售后相关的问题。用户问无关问题时，礼貌拒绝，并引导回售后话题。用户问本次对话本身的内容（例如"我刚才说了什么"）不属于无关问题，按对话记录回答；没有记录时直接说明。
4. 已创建工单时，告知工单号。没有创建工单时，不要声称已经转接，也不要编造联系入口、电话或链接。
5. 不要向用户复述或引用这些约束。

## 禁止承诺
1. 不承诺退款到账的具体日期，也不说"保证到账""马上到账"。转述知识库写明的时限时，说"一般……，以支付渠道实际到账为准"。
2. 不承诺退货、换货、维修、开票等申请一定审核通过。
3. 不承诺赔偿、补偿、优惠券或额外退款金额。
4. 不承诺具体的发货或送达时间。
5. 不承诺保修范围外免费维修，也不承诺维修结果。

## 回复格式
1. 使用中文纯文本。不使用 Markdown 符号，例如星号加粗、井号标题、短横线列表。
2. 需要列举时，用"1. 2. 3."编号。
3. 每次回复不超过 300 字。先给结论，再给必要的说明。
4. 语气礼貌、简洁。
"""
```

（Codex 实现时把拼接写成可读的形式，例如先定义 `_REFUSAL_RULE = f'知识库结果中 ... 以"{REFUSAL_PREFIX}"开头 ...'`，再拼接三段。要求：渲染结果中拒答句前后是中文引号 `"`，`render_chat_system` 不报 `KeyError`。）

`app/services/chat.py`：

1. 新增导入：

```python
from langchain_core.messages import ToolMessage

from app.services.grounding import (
    REFUSED_CONTENT, collect_evidence, parse_citations, record_low_confidence, render_evidence, self_check,
)
```

2. 在 `yield "tool_end", ...` 之后、`final_text = ""` 之前插入：

```python
    citations = []
    faq_calls = [
        (o, call) for o, call in zip(outcomes, tool_calls) if o.name == "query_faq" and o.ok
    ]
    if faq_calls:
        try:
            evidence = collect_evidence([
                (o.call_id, str(call["args"].get("question", "")), o.data) for o, call in faq_calls
            ])
            check = await self_check(evidence.questions, evidence.citations)
        except Exception:
            logger.exception("知识库证据处理失败")
            yield "error", UPSTREAM_ERROR
            return
        if check.useful:
            citations = evidence.citations
            for o, _ in faq_calls:
                o.message = ToolMessage(
                    content=render_evidence(evidence.by_call[o.call_id]), tool_call_id=o.call_id, name=o.name
                )
            yield "citations", {"items": [c.to_dict() for c in citations], "refused": False}
        else:
            # 独立事务，失败只记日志。
            await record_low_confidence(turn.conversation_id, turn.user_input, check.reason)
            for o, _ in faq_calls:
                # 模型看不到不足的证据；写库时存模型实际看到的内容。
                o.message = ToolMessage(content=REFUSED_CONTENT, tool_call_id=o.call_id, name=o.name)
            yield "citations", {"items": [], "refused": True}
```

`execute_tool_calls` 按输入顺序返回结果，所以 `zip(outcomes, tool_calls)` 一一对应。

3. 在"工具结果回复流返回空回复"检查之后、写库之前插入：

```python
    out_of_range = [n for n in parse_citations(final_text) if not 1 <= n <= len(citations)]
    if out_of_range:
        logger.warning("回复含越界引用编号：%s", out_of_range)
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。如果 `tests/test_context.py` 因 System Prompt 变长而失败，只调整该测试中的预算数字，不改 `TOKEN_BUDGET`；在交付说明中写明。

- [ ] **Step 5: 提交**

```bash
git add app/services/chat.py app/prompts.py tests/test_chat_api.py tests/test_prompts.py
git commit -m "feat(ch04): citations event, self-check refusal and no-promise rules in chat"
```

---

### Task 9: 引用接口与台账接口

**Files:**
- Modify: `app/repositories/knowledge.py`（新增 `get_done`）
- Create: `app/repositories/faith_cases.py`
- Create: `app/api/knowledge.py`、`app/api/faith_cases.py`
- Modify: `app/api/web.py`（新增 `/admin/faith-cases`）
- Create: `app/web/faith_cases.html`（最小骨架，Task 13 用 Vibe Coding 完成）
- Modify: `app/main.py`（挂载两个新路由）
- Test: `tests/test_knowledge_api.py`、`tests/test_faith_cases.py`

**Interfaces:**
- Consumes: Task 1 的 `FaithCase`；Task 7 的 `parse_citations`。
- Produces（`app/repositories/faith_cases.py`）：

```python
async def upsert_case(s, *, eval_id: str, bucket: str, query: str, answer: str, reason: str,
                      citations: list[dict], judge_model: str | None,
                      strategy: str = "hybrid_rerank") -> FaithCase
async def list_cases(s, status: str | None = None) -> list[FaithCase]   # last_seen_at 降序，id 降序
async def resolve_case(s, case_id: int, status: str, resolution: str) -> FaithCase | None
```

- Produces（`app/repositories/knowledge.py`）：`async def get_done(s, chunk_id: int) -> KnowledgeChunk | None`
- Produces（HTTP）：spec §9.1 的 4 个路由。

- [ ] **Step 1: 写失败的测试**

`tests/test_faith_cases.py`：

```python
from datetime import datetime

import pytest
from sqlalchemy import text as sql_text

from app.db.models import FaithCase
from app.repositories import faith_cases as fc

pytestmark = pytest.mark.anyio
CITES = [{"n": 1, "chunk_id": 3, "section_path": "p", "question": "q", "answer": "a"}]


async def upsert(db, eval_id="A01", answer="a1", reason="r1"):
    async with db() as s:
        row = await fc.upsert_case(s, eval_id=eval_id, bucket="A_policy", query="q", answer=answer,
                                   reason=reason, citations=CITES, judge_model="m1")
        await s.commit()
        await s.refresh(row)
        return row


async def test_insert_then_repeat_increments_and_updates_snapshot(db):
    first = await upsert(db)
    async with db() as s:
        await s.execute(sql_text("UPDATE faith_cases SET last_seen_at = '2026-01-01 00:00:00'"))
        await s.commit()
    second = await upsert(db, answer="a2", reason="r2")
    assert second.id == first.id and second.seen_count == 2
    assert (second.answer, second.reason, second.status) == ("a2", "r2", "未解决")
    assert second.last_seen_at > datetime(2026, 1, 1)
    assert second.first_seen_at == first.first_seen_at


@pytest.mark.parametrize("status", ["已解决", "无需解决"])
async def test_recurrence_reopens_and_clears_resolution(db, status):
    row = await upsert(db)
    async with db() as s:
        await fc.resolve_case(s, row.id, status, "补了文档")
        await s.commit()
    again = await upsert(db)
    assert (again.status, again.resolution) == ("未解决", None)
    assert again.resolved_at is not None  # 保留，用来标「复发」


async def test_list_cases_filters_and_orders(db):
    a = await upsert(db, "A01")
    b = await upsert(db, "B01")
    async with db() as s:
        await fc.resolve_case(s, a.id, "已解决", "ok")
        await s.commit()
        assert [r.eval_id for r in await fc.list_cases(s, "未解决")] == ["B01"]
        assert {r.eval_id for r in await fc.list_cases(s)} == {"A01", "B01"}


async def test_resolve_missing_returns_none(db):
    async with db() as s:
        assert await fc.resolve_case(s, 999999, "已解决", "x") is None


async def test_api_list_and_resolve(client, db):
    row = await upsert(db, answer="运费 8 元[1]，包邮[3]")
    r = await client.get("/api/faith-cases", params={"status": "未解决"})
    assert r.status_code == 200
    [item] = r.json()
    assert item["eval_id"] == "A01" and item["cited"] == [1, 3] and item["citations"] == CITES
    r = await client.post(f"/api/faith-cases/{row.id}/resolve", json={"status": "已解决", "resolution": " 补了运费文档 "})
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["resolution"]) == ("已解决", "补了运费文档")
    assert r.json()["resolved_at"] is not None


@pytest.mark.parametrize("body", [
    {"status": "已解决", "resolution": "   "},
    {"status": "已解决", "resolution": "字" * 301},
    {"status": "未解决", "resolution": "x"},
    {"status": "已解决"},
])
async def test_api_resolve_validation(client, db, body):
    row = await upsert(db)
    assert (await client.post(f"/api/faith-cases/{row.id}/resolve", json=body)).status_code == 422


async def test_api_resolve_missing_is_404(client, db):
    r = await client.post("/api/faith-cases/999999/resolve", json={"status": "已解决", "resolution": "x"})
    assert r.status_code == 404


async def test_admin_page_served(client):
    r = await client.get("/admin/faith-cases")
    assert r.status_code == 200 and "编造个案台账" in r.text
```

`tests/test_knowledge_api.py`：

```python
import pytest

from app.repositories import knowledge
from app.repositories.knowledge import NewChunk

pytestmark = pytest.mark.anyio


async def seed(db):
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk("退货政策 > 退款", "退款时间", "原路退回", "退货政策 > 退款 > 退款时间", "policy", False),
            NewChunk("退货政策 > 退款", "退款方式", "原路", "退货政策 > 退款 > 退款方式", "policy", False),
        ])
        await knowledge.link_chain(s, [r.id for r in rows])
        await knowledge.mark_done(s, [rows[0].id])
        await s.commit()
        return [r.id for r in rows]


async def test_get_done_chunk(client, db):
    a, b = await seed(db)
    r = await client.get(f"/api/knowledge/chunks/{a}")
    assert r.status_code == 200
    assert r.json() == {
        "id": a, "section_path": "退货政策 > 退款 > 退款时间", "content_type": "policy",
        "questions": "退款时间", "answer": "原路退回", "prev_chunk_id": None, "next_chunk_id": b,
    }


async def test_pending_or_missing_chunk_is_404(client, db):
    a, b = await seed(db)
    assert (await client.get(f"/api/knowledge/chunks/{b}")).status_code == 404
    assert (await client.get("/api/knowledge/chunks/999999")).status_code == 404
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_faith_cases.py tests/test_knowledge_api.py -q`
Expected: FAIL（模块不存在、路由 404）。

- [ ] **Step 3: 实现**

`app/repositories/knowledge.py` 追加：

```python
async def get_done(s: AsyncSession, chunk_id: int) -> KnowledgeChunk | None:
    return await s.scalar(select(KnowledgeChunk).where(
        KnowledgeChunk.id == chunk_id, KnowledgeChunk.vectorize_status == "done",
    ))
```

`app/repositories/faith_cases.py`：

```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FaithCase

OPEN = "未解决"


async def upsert_case(
    s: AsyncSession, *, eval_id: str, bucket: str, query: str, answer: str, reason: str,
    citations: list[dict], judge_model: str | None, strategy: str = "hybrid_rerank",
) -> FaithCase:
    """一题一行。再次判出时更新快照并累计次数；已处置的题复发时退回未解决。"""
    row = await s.scalar(select(FaithCase).where(FaithCase.eval_id == eval_id).with_for_update())
    if row is None:
        row = FaithCase(eval_id=eval_id, bucket=bucket, query=query, strategy=strategy, answer=answer,
                        reason=reason, citations=citations, judge_model=judge_model)
        s.add(row)
        await s.flush()
        return row
    row.bucket, row.query, row.strategy = bucket, query, strategy
    row.answer, row.reason, row.citations, row.judge_model = answer, reason, citations, judge_model
    row.seen_count = FaithCase.seen_count + 1
    row.last_seen_at = func.now()
    if row.status != OPEN:
        # 复发：重新进入待处理列表。resolved_at 保留，用来标「复发」。
        row.status, row.resolution = OPEN, None
    await s.flush()
    return row


async def list_cases(s: AsyncSession, status: str | None = None) -> list[FaithCase]:
    stmt = select(FaithCase).order_by(FaithCase.last_seen_at.desc(), FaithCase.id.desc())
    if status is not None:
        stmt = stmt.where(FaithCase.status == status)
    return list(await s.scalars(stmt))


async def resolve_case(s: AsyncSession, case_id: int, status: str, resolution: str) -> FaithCase | None:
    row = await s.get(FaithCase, case_id)
    if row is None:
        return None
    row.status, row.resolution, row.resolved_at = status, resolution, func.now()
    await s.flush()
    return row
```

调用方提交后必须 `await s.refresh(row)` 再读取 `seen_count`、`last_seen_at`、`resolved_at`（SQL 表达式赋值）。

`app/api/knowledge.py`：

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.engine import get_sessionmaker
from app.repositories import knowledge

router = APIRouter()


class ChunkOut(BaseModel):
    id: int
    section_path: str | None
    content_type: str | None
    questions: str
    answer: str
    prev_chunk_id: int | None
    next_chunk_id: int | None


@router.get("/api/knowledge/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(chunk_id: int) -> ChunkOut:
    async with get_sessionmaker()() as s:
        row = await knowledge.get_done(s, chunk_id)
    if row is None:
        raise HTTPException(404, detail={"code": "chunk_not_found", "message": "知识块不存在"})
    return ChunkOut.model_validate(row, from_attributes=True)
```

`app/api/faith_cases.py`：

```python
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.db.engine import get_sessionmaker
from app.repositories import faith_cases
from app.services.grounding import parse_citations

router = APIRouter()


class FaithCaseOut(BaseModel):
    id: int
    eval_id: str
    bucket: str
    query: str
    strategy: str
    answer: str
    reason: str
    citations: list[dict[str, Any]] | None
    cited: list[int]
    judge_model: str | None
    status: str
    seen_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    resolution: str | None
    resolved_at: datetime | None


class ResolveRequest(BaseModel):
    status: Literal["已解决", "无需解决"]
    resolution: str = Field(max_length=300)

    @field_validator("resolution")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("处置说明不能为空")
        return v


def _out(row) -> FaithCaseOut:
    return FaithCaseOut.model_validate(
        {**{k: getattr(row, k) for k in FaithCaseOut.model_fields if k != "cited"},
         "cited": parse_citations(row.answer)}
    )


@router.get("/api/faith-cases", response_model=list[FaithCaseOut])
async def list_faith_cases(status: Literal["未解决", "已解决", "无需解决"] | None = None):
    async with get_sessionmaker()() as s:
        return [_out(r) for r in await faith_cases.list_cases(s, status)]


@router.post("/api/faith-cases/{case_id}/resolve", response_model=FaithCaseOut)
async def resolve_faith_case(case_id: int, req: ResolveRequest):
    async with get_sessionmaker()() as s:
        row = await faith_cases.resolve_case(s, case_id, req.status, req.resolution)
        if row is None:
            raise HTTPException(404, detail={"code": "case_not_found", "message": "个案不存在"})
        await s.commit()
        await s.refresh(row)
        return _out(row)
```

`ResolveRequest.resolution` 的 `max_length=300` 在去空白之前校验；`" " * 2 + "字" * 300` 这类输入按 302 字拒绝，可以接受。

`app/api/web.py` 追加：

```python
@router.get("/admin/faith-cases", response_class=FileResponse, include_in_schema=False)
async def faith_cases_page() -> Path:
    return Path(__file__).resolve().parent.parent / "web" / "faith_cases.html"
```

`app/web/faith_cases.html`：最小骨架，`<title>编造个案台账</title>`，`<h1>编造个案台账</h1>`，`<div id="cases"></div>`。完整页面在 Task 13 实现。

`app/main.py`：`from app.api import chat, extract, faith_cases, health, knowledge, web`，并 `include_router` 两个新路由。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app tests
git commit -m "feat(ch04): chunk lookup API and faith case ledger API"
```

---

### Task 10: 评估框架代码（指标、启动检查、检索段、生成段、裁判、报告）

**Files:**
- Create: `evals/rag_metrics.py`（纯函数）
- Create: `evals/rag_eval_set.py`（评估集读取与检查）
- Create: `evals/run_rag_eval.py`（命令行入口）
- Create: `evals/run_faith_judge_eval.py`
- Modify: `app/schemas.py`（新增 `FaithVerdict`）
- Modify: `app/prompts.py`（新增 `FAITH_JUDGE_SYSTEM_PROMPT`、`faith_judge_prompt`）
- Modify: `app/llm.py`（新增 `get_faith_judge`）
- Modify: `.gitignore`（不忽略 `evals/reports/`；如已有忽略规则则删除）
- Test: `tests/test_rag_metrics.py`、`tests/test_rag_eval_set.py`、`tests/test_run_rag_eval.py`

**Interfaces:**
- Consumes: Task 6 的 `retrieve`、`STRATEGIES`、`source_key`、`EvidenceItem`；Task 7 的 `collect_evidence`、`render_evidence`、`REFUSED_CONTENT`、`self_check`、`format_evidence`、`Citation`；Task 8 的 `REFUSAL_PREFIX`、`chat_prompt`、`TOOL_ROUND_CLOSING`；Task 9 的 `faith_cases.upsert_case`。
- Produces（`app/schemas.py`）：

```python
class FaithVerdict(BaseModel):
    faithful: bool = Field(description="unsupported_claims 为空时为 true")
    unsupported_claims: list[str] = Field(default_factory=list, description="证据中找不到依据的句子，原样摘录")
    reason: str = Field(description="一句话说明判定依据")
```

- Produces（`evals/rag_metrics.py`）：

```python
BUCKETS = ("A_policy", "B_model", "C_colloquial", "D_unanswerable", "E_multi")
ANSWERABLE = ("A_policy", "B_model", "C_colloquial", "E_multi")
DIFFICULTIES = ("easy", "medium", "hard")
KS = (1, 3, 5, 10)
THRESHOLDS = (0.05, 0.10, ..., 0.80)   # 16 个，步长 0.05
def is_refusal(answer: str) -> bool
def dedupe(keys: list[str]) -> list[str]
def recall_at_k(ranked_keys: list[str], relevant: list[str], k: int) -> float
def reciprocal_rank(ranked_keys: list[str], relevant: list[str]) -> float
@dataclass(frozen=True) class RetrievalScore: sample_id, bucket, difficulty, strategy, recall: dict[int, float], rr: float
def score_retrieval(sample_id, bucket, difficulty, strategy, ranked_keys, relevant) -> RetrievalScore
def summarize(scores: list[RetrievalScore], group: str) -> dict[tuple[str, str], dict[str, float]]
    # group 为 "bucket" 或 "difficulty"；键为 (strategy, 组名)，另含 (strategy, "ALL")；值含 "R@1" "R@3" "R@5" "R@10" "MRR" "n"
@dataclass(frozen=True) class ThresholdRow: bucket: str; top1_relevant: bool; top_score: float | None
def threshold_sweep(rows: list[ThresholdRow], thresholds=THRESHOLDS) -> list[tuple[float, float, float]]
    # (门槛, A/B/C/E 的 Top-1 保留率, D 的门槛拒答率)
def pick_threshold(sweep, min_keep: float = 0.95) -> float | None
@dataclass class GenResult: sample_id, bucket, difficulty, strategy, query, retrieved: bool, refused: bool,
    answer: str, citations: list[dict], faithful: bool | None, unsupported: list[str], reason: str
def generation_summary(results: list[GenResult]) -> dict[str, dict[str, float]]
    # 每个策略："faithfulness"、"false_refusal"、"d_refusal"、"no_retrieval"、"judge_failed"、"answered"
def render_report(*, retrieval_by_bucket, retrieval_by_difficulty, post_threshold, sweep, current_threshold,
                  generation, failures: list[str]) -> str   # Markdown；没有的部分传 None 时省略
```

- Produces（`evals/rag_eval_set.py`）：

```python
SAMPLES_PATH: Path   # evals/rag_eval.jsonl
BUCKET_SIZES = {"A_policy": 70, "B_model": 60, "C_colloquial": 60, "D_unanswerable": 60, "E_multi": 50}
DIFFICULTY_SIZES = {"A_policy": (25, 25, 20), "B_model": (20, 20, 20), "C_colloquial": (15, 25, 20),
                    "D_unanswerable": (20, 20, 20), "E_multi": (10, 20, 20)}   # easy, medium, hard
@dataclass(frozen=True) class EvalSample: id: str; bucket: str; difficulty: str; query: str; relevant: tuple[str, ...]
def load_samples(path: Path = SAMPLES_PATH) -> list[EvalSample]
def validate(samples: list[EvalSample], known_keys: set[str] | None, *, full: bool = True) -> list[str]   # 错误列表
async def known_source_keys() -> dict[str, str]   # 来源键 → 内容预览（MySQL done 行，不含 mined）
```

- Produces（`evals/run_rag_eval.py`）：

```python
async def generate_one(sample: EvalSample, plan: QueryPlan, strategy: str, *, model: BaseChatModel,
                       judge: Runnable, checker: Runnable | None = None, today: date) -> GenResult
async def write_faith_cases(results: list[GenResult], judge_model: str) -> int   # 返回写入条数
def main() -> int
```

  命令行参数：`--stage {retrieval,generation,all}`（默认 `all`）、`--check`、`--list-keys`、`--gen-strategies {hybrid_rerank,all}`（默认 `hybrid_rerank`）、`--concurrency N`（默认 8）、`--limit N`、`--bucket NAME`、`--no-write`。

- [ ] **Step 1: 写失败的测试**

`tests/test_rag_metrics.py`：

```python
import pytest

from app.prompts import REFUSAL_PREFIX
from evals import rag_metrics as rm


def test_is_refusal():
    assert rm.is_refusal("  " + REFUSAL_PREFIX + "建议转人工")
    assert not rm.is_refusal("可以退" + REFUSAL_PREFIX)


def test_dedupe_keeps_first():
    assert rm.dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_at_k_dedupes_by_source_key():
    ranked = ["x", "x", "a", "b"]          # 表格拆成两块，共用来源键 x
    assert rm.recall_at_k(ranked, ["a", "b"], 1) == 0.0
    assert rm.recall_at_k(ranked, ["a", "b"], 2) == 0.5
    assert rm.recall_at_k(ranked, ["a", "b"], 3) == 1.0


def test_reciprocal_rank():
    assert rm.reciprocal_rank(["x", "x", "a"], ["a"]) == 0.5
    assert rm.reciprocal_rank(["x"], ["a"]) == 0.0
    assert rm.reciprocal_rank([], ["a"]) == 0.0


def test_summarize_by_bucket_and_all():
    s = [
        rm.score_retrieval("A01", "A_policy", "easy", "bm25", ["a"], ["a"]),
        rm.score_retrieval("B01", "B_model", "hard", "bm25", ["x", "b"], ["b"]),
    ]
    out = rm.summarize(s, "bucket")
    assert out[("bm25", "A_policy")]["MRR"] == 1.0
    assert out[("bm25", "B_model")]["R@1"] == 0.0 and out[("bm25", "B_model")]["R@3"] == 1.0
    assert out[("bm25", "ALL")]["MRR"] == pytest.approx(0.75) and out[("bm25", "ALL")]["n"] == 2
    assert rm.summarize(s, "difficulty")[("bm25", "hard")]["n"] == 1


def test_threshold_sweep_and_pick():
    rows = [
        rm.ThresholdRow("A_policy", True, 0.9),
        rm.ThresholdRow("A_policy", True, 0.4),
        rm.ThresholdRow("B_model", False, 0.95),   # 第 1 名不相关，任何门槛都不算保留
        rm.ThresholdRow("D_unanswerable", False, 0.2),
        rm.ThresholdRow("D_unanswerable", False, None),
    ]
    sweep = dict((t, (keep, refuse)) for t, keep, refuse in rm.threshold_sweep(rows, (0.1, 0.3, 0.5)))
    assert sweep[0.1] == (pytest.approx(2 / 3), 0.5)
    assert sweep[0.3] == (pytest.approx(2 / 3), 1.0)
    assert sweep[0.5] == (pytest.approx(1 / 3), 1.0)
    assert rm.pick_threshold([(0.1, 0.96, 0.2), (0.2, 0.95, 0.5), (0.3, 0.90, 0.8)]) == 0.2
    assert rm.pick_threshold([(0.1, 0.5, 0.2)]) is None
    assert len(rm.THRESHOLDS) == 16 and rm.THRESHOLDS[0] == 0.05 and rm.THRESHOLDS[-1] == 0.8


def gen(bucket, *, retrieved=True, refused=False, faithful=True, strategy="hybrid_rerank"):
    return rm.GenResult("X01", bucket, "easy", strategy, "q", retrieved, refused, "a", [], faithful, [], "")


def test_generation_summary():
    out = rm.generation_summary([
        gen("A_policy"), gen("A_policy", faithful=False), gen("B_model", refused=True, faithful=None),
        gen("C_colloquial", retrieved=False, faithful=None),
        gen("D_unanswerable", refused=True, faithful=None), gen("D_unanswerable", faithful=False),
        gen("E_multi", faithful=None),   # 裁判失败
    ])["hybrid_rerank"]
    assert out["faithfulness"] == pytest.approx(1 / 2)     # 只算检索过、未拒答、裁判成功的 A/B/C/E
    assert out["false_refusal"] == pytest.approx(1 / 5)    # A/B/C/E 共 5 题，拒答 1 题
    assert out["d_refusal"] == pytest.approx(1 / 2)
    assert out["no_retrieval"] == 1 and out["judge_failed"] == 1


def test_render_report_sections():
    md = rm.render_report(
        retrieval_by_bucket={("bm25", "ALL"): {"R@1": 0.5, "R@3": 0.6, "R@5": 0.7, "R@10": 0.8, "MRR": 0.55, "n": 2}},
        retrieval_by_difficulty={("bm25", "easy"): {"R@1": 0.5, "R@3": 0.6, "R@5": 0.7, "R@10": 0.8, "MRR": 0.55, "n": 2}},
        post_threshold=None, sweep=[(0.3, 0.96, 0.8)], current_threshold=0.3,
        generation=None, failures=[],
    )
    assert "## 检索：策略 × 桶" in md and "## 检索：策略 × 难度" in md and "## 门槛扫描" in md
    assert "| bm25 | ALL | 0.500 |" in md
    assert "## 生成" not in md
```

`tests/test_rag_eval_set.py`：

```python
import json

from evals import rag_eval_set as es


def sample(id="A01", bucket="A_policy", difficulty="easy", query="q", relevant=("k1",)):
    return es.EvalSample(id, bucket, difficulty, query, tuple(relevant))


def test_load_samples(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"id": "A01", "bucket": "A_policy", "difficulty": "easy", "query": "q",
                             "relevant": ["k1"]}, ensure_ascii=False) + "\n\n", encoding="utf-8")
    assert es.load_samples(p) == [sample()]


def test_validate_partial_set_ok():
    assert es.validate([sample(), sample("D01", "D_unanswerable", relevant=())], {"k1"}, full=False) == []


def test_validate_errors():
    errors = es.validate([
        sample(), sample(),                                         # 题号重复
        sample("B01", "A_policy"),                                  # 前缀与桶不符
        sample("A02", difficulty="extreme"),                        # 难度无效
        sample("A03", relevant=()),                                 # 可答题没有来源键
        sample("D01", "D_unanswerable", relevant=("k1",)),           # D 必须为空
        sample("E01", "E_multi", relevant=("k1",)),                 # E 至少 2 个
        sample("A04", relevant=("nope",)),                          # 来源键不存在
        sample("A05", query=""),                                    # 问题为空
    ], {"k1"}, full=False)
    joined = "\n".join(errors)
    for word in ("A01", "B01", "A02", "A03", "D01", "E01", "nope", "A05"):
        assert word in joined
    assert len(errors) == 8


def test_validate_full_counts():
    errors = es.validate([sample()], None, full=True)
    assert any("A_policy" in e and "70" in e for e in errors)


def test_validate_skips_key_check_when_known_keys_none():
    assert es.validate([sample(relevant=("anything",))], None, full=False) == []
```

`tests/test_run_rag_eval.py`：

```python
import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import FaithCase
from app.knowledge.retrieval import EvidenceItem, Retrieval
from app.prompts import REFUSAL_PREFIX
from app.schemas import FaithVerdict, QueryPlan, SelfCheck
from app.services import grounding
from evals import rag_metrics as rm
from evals import run_rag_eval as rre
from evals.rag_eval_set import EvalSample
from tests.fakes import Recorder, ScriptedChatModel, text, tools

pytestmark = pytest.mark.anyio
PLAN = QueryPlan(standard_query="X3 Pro 续航", product_category="蓝牙耳机")
SAMPLE = EvalSample("B01", "B_model", "easy", "X3 Pro 能用多久", ("商品手册 > 蓝牙耳机 > X3 Pro 续航",))
ITEM = EvidenceItem(11, "商品手册 > 蓝牙耳机 > X3 Pro 续航", "X3 Pro 续航", "单次续航 8 小时", 0.9)


def fixed(result):
    calls = []

    def run(inputs):
        calls.append(inputs)
        return {"parsed": result, "raw": None}
    return RunnableLambda(run), calls


@pytest.fixture
def fake_retrieve(monkeypatch):
    seen = []

    async def run(question, strategy, **kw):
        seen.append((question, strategy, kw))
        return Retrieval(kw["plan"], [ITEM], [ITEM])

    monkeypatch.setattr(rre, "retrieve", run)
    return seen


async def test_generate_one_uses_model_tool_call_id(fake_retrieve):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[tools(("call_x", "query_faq", {"question": "别的说法"})),
                                       text("约 8 小时[1]")], recorder=rec)
    checker, _ = fixed(SelfCheck(useful=True, reason="[1]"))
    judge, judge_calls = fixed(FaithVerdict(faithful=True, unsupported_claims=[], reason="ok"))
    res = await rre.generate_one(SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
                                 checker=checker, today=date(2026, 10, 7))
    assert fake_retrieve == [("X3 Pro 能用多久", "hybrid_rerank", {"plan": PLAN, "exclude_mined": True})]
    msgs = rec[1]["messages"]
    ai = next(m for m in msgs if isinstance(m, AIMessage) and m.tool_calls)
    tool = next(m for m in msgs if isinstance(m, ToolMessage))
    assert ai.tool_calls[0]["id"] == "call_x" and tool.tool_call_id == "call_x"
    assert json.loads(tool.content)["data"]["evidence"][0]["n"] == 1
    assert (res.retrieved, res.refused, res.faithful, res.answer) == (True, False, True, "约 8 小时[1]")
    assert res.citations == [{"n": 1, "chunk_id": 11, "section_path": ITEM.section_path,
                              "question": ITEM.question, "answer": ITEM.answer}]
    assert judge_calls[0]["answer"] == "约 8 小时[1]" and "[1] 商品手册" in judge_calls[0]["evidence"]


async def test_generate_one_without_faq_call(fake_retrieve):
    model = ScriptedChatModel(scripts=[text("您好")])
    judge, judge_calls = fixed(None)
    res = await rre.generate_one(SAMPLE, PLAN, "bm25", model=model, judge=judge,
                                 checker=fixed(None)[0], today=date(2026, 10, 7))
    assert (res.retrieved, res.answer) == (False, "您好") and fake_retrieve == [] and judge_calls == []


async def test_generate_one_refused_skips_judge(fake_retrieve):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[tools(("c1", "query_faq", {"question": "q"})),
                                       text(REFUSAL_PREFIX + "建议转人工")], recorder=rec)
    judge, judge_calls = fixed(None)
    res = await rre.generate_one(SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
                                 checker=fixed(SelfCheck(useful=False, reason="缺"))[0], today=date(2026, 10, 7))
    tool = next(m for m in rec[1]["messages"] if isinstance(m, ToolMessage))
    assert tool.content == grounding.REFUSED_CONTENT
    assert res.refused is True and res.citations == [] and judge_calls == []


async def test_other_tool_calls_are_not_executed(fake_retrieve):
    rec = Recorder()
    model = ScriptedChatModel(scripts=[
        tools(("c1", "query_order", {"order_id": "1001"}), ("c2", "query_faq", {"question": "q"})),
        text("答[1]"),
    ], recorder=rec)
    judge, _ = fixed(FaithVerdict(faithful=True, unsupported_claims=[], reason="ok"))
    await rre.generate_one(SAMPLE, PLAN, "hybrid_rerank", model=model, judge=judge,
                           checker=fixed(SelfCheck(useful=True, reason="ok"))[0], today=date(2026, 10, 7))
    contents = {m.tool_call_id: m.content for m in rec[1]["messages"] if isinstance(m, ToolMessage)}
    assert json.loads(contents["c1"]) == {"ok": False, "error": "tool_error", "message": "查询失败"}


async def test_write_faith_cases_filters(db):
    def r(bucket, strategy="hybrid_rerank", faithful=False, refused=False, retrieved=True, sid="A01"):
        return rm.GenResult(sid, bucket, "easy", strategy, "q", retrieved, refused, "答[1]",
                            [{"n": 1, "chunk_id": 1, "section_path": "p", "question": "q", "answer": "a"}],
                            faithful, ["编的句子"], "编了")
    n = await rre.write_faith_cases([
        r("A_policy", sid="A01"),
        r("A_policy", strategy="bm25", sid="A02"),
        r("D_unanswerable", sid="D01"),
        r("B_model", faithful=True, sid="B01"),
        r("C_colloquial", faithful=None, sid="C01"),
        r("E_multi", refused=True, sid="E01"),
    ], "m1")
    assert n == 1
    async with db() as s:
        [row] = list(await s.scalars(select(FaithCase)))
    assert (row.eval_id, row.bucket, row.judge_model) == ("A01", "A_policy", "m1")
    assert "编了" in row.reason and "编的句子" in row.reason
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_rag_metrics.py tests/test_rag_eval_set.py tests/test_run_rag_eval.py -q`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

1. `app/schemas.py` 追加 `FaithVerdict`（见 Interfaces）。
2. `app/prompts.py` 追加：

```python
FAITH_JUDGE_SYSTEM_PROMPT = """你是客服回答的忠实度裁判。判断答案中的事实陈述是否都能在给定证据中找到依据。

## 判定规则
1. 逐句检查答案。事实陈述包括：条件、时限、金额、数字、步骤、型号参数、政策结论。
2. 一句事实陈述在证据中找不到依据，或与证据矛盾，列入 unsupported_claims，原样摘录这句话。
3. 数字、型号、条件必须与证据一致。把一个型号的参数说成另一个型号的，属于没有依据。
4. 礼貌用语、道歉、转人工建议、请用户补充信息，不算事实陈述。
5. 答案中的引用编号只是标注。标注的证据与句子内容不符时，这句列入 unsupported_claims。
6. unsupported_claims 为空时 faithful 为 true，否则为 false。

## reason
写一句话说明判定依据。"""

faith_judge_prompt = ChatPromptTemplate.from_messages([
    ("system", FAITH_JUDGE_SYSTEM_PROMPT),
    ("human", "证据：\n{evidence}\n\n答案：\n{answer}"),
])
```

3. `app/llm.py` 追加 `get_faith_judge()`（同 `get_self_checker` 写法，Schema 为 `FaithVerdict`，prompt 为 `faith_judge_prompt`）。
4. `evals/rag_metrics.py`：按 Interfaces 实现。要点：
   - `recall_at_k`：`hits = set(dedupe(ranked_keys)[:k]) & set(relevant)`，返回 `len(hits) / len(set(relevant))`。
   - `reciprocal_rank`：在 `dedupe(ranked_keys)` 中找第 1 个属于 `relevant` 的位置 `i`（从 1 开始），返回 `1 / i`；没有时返回 0。
   - `summarize`：对每个 `(strategy, 组名)` 和 `(strategy, "ALL")` 取各指标的算术平均，`n` 为题数。
   - `threshold_sweep`：保留率 = A/B/C/E 中 `top1_relevant and top_score is not None and top_score >= t` 的比例；拒答率 = D 中 `top_score is None or top_score < t` 的比例。分母为 0 时比例记为 0。
   - `pick_threshold`：保留率不低于 `min_keep` 的门槛中取最大值。
   - `generation_summary`：按策略分组。`faithfulness` 的分母为 A/B/C/E 中 `retrieved and not refused and faithful is not None` 的题；`false_refusal` 的分母为全部 A/B/C/E 题；`d_refusal` 的分母为全部 D 题；`no_retrieval`、`judge_failed`（`retrieved and not refused and faithful is None`，A–E 全部桶）、`answered` 为计数。
   - `render_report`：Markdown。表头固定为：

     ```
     ## 检索：策略 × 桶
     | 策略 | 桶 | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |
     ## 检索：策略 × 难度
     | 策略 | 难度 | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |
     ## hybrid_rerank 门槛后
     （同上表头，组名为桶）
     ## 门槛扫描
     | 门槛 | A/B/C/E Top-1 保留率 | D 门槛拒答率 |
     当前 RERANK_MIN_SCORE：0.30；按校准规则建议：0.xx
     ## 生成
     | 策略 | Faithfulness | 误拒率 | D 正确拒答率 | 未检索 | 裁判失败 |
     ## 编造个案与 D 误答
     - B12 [hybrid_rerank] 问题 → 未找到依据：……
     ## 执行失败
     ```

     数字保留 3 位小数，题数为整数。某部分的参数为 `None` 时省略该部分；`failures` 为空时省略"执行失败"。
5. `evals/rag_eval_set.py`：按 Interfaces 实现。`validate` 的检查项与错误文字：
   - 题号重复：`题号重复：A01`；题号格式 `^[A-E]\d{2,3}$`，首字母与桶名首字母一致：`题号与桶不符：B01（A_policy）`。
   - 桶不在 `BUCKET_SIZES` 中、难度不在 `easy/medium/hard` 中：`A02：难度无效 extreme`。
   - `query` 去空白后为空，或超过 512 字：`A05：问题为空或超过 512 字`。
   - D 桶 `relevant` 必须为空；其他桶必须非空；E 桶至少 2 个不同来源键。
   - `known_keys` 不为 `None` 时：`A04：来源键不存在 nope`。
   - `full=True` 时：每个桶的题数和每个难度的题数与表一致：`A_policy：题数 1，应为 70`、`A_policy/easy：题数 1，应为 25`。
   - `known_source_keys()`：读取 MySQL 中 `vectorize_status='done'` 且 `content_type != 'mined'` 的行，返回 `{source_key(section_path, questions): answer 前 60 字}`。
6. `evals/run_rag_eval.py`：
   - 结构仿照 `evals/run_chat_samples.py`（`sys.path` 处理、`main()` 中 `asyncio.run`、`finally` 中依次 `close_milvus()`、`close_rerank()`、`dispose_engine()`、日志级别 ERROR）。
   - 流程：`ensure_collection()` → `known_source_keys()` → `--list-keys` 时打印 `来源键\t预览` 后退出 0 → `load_samples()` + `validate(全集, keys, full=True)`，有错误时逐条打印并返回 1 → `--check` 时打印"评估集检查通过"并返回 0 → 按 `--bucket`、`--limit` 取子集 → 用信号量（`--concurrency`）并发调用 `understand(query)` 得到每题的 plan（缓存）。
   - 检索段：对每题、每个策略调用 `retrieve(query, strategy, plan=plan, exclude_mined=True)`。用 `source_key(e.section_path, e.question)` 取 `ranked` 的来源键算分（D 桶不算分）。`hybrid_rerank` 另外：用 `ranked` 中 `score >= RERANK_MIN_SCORE` 的部分算"门槛后"分数；生成 `ThresholdRow(bucket, ranked 非空且第 1 名相关, ranked[0].score 或 None)`。单题异常时 `logger.exception`，记入 `failures`（`"B12/bm25：RerankError"`），继续下一题。
   - 生成段：`model = get_chat_model()`，`judge = get_faith_judge()`，`checker` 用默认。对每题、每个所选策略调用 `generate_one`。然后（除非 `--no-write`）调用 `write_faith_cases(results, get_settings().chat_model)` 并打印写入条数。
   - `generate_one`：

```python
EVAL_SKIPPED_TOOL = json.dumps({"ok": False, "error": "tool_error", "message": "查询失败"}, ensure_ascii=False)


async def _stream(runnable, inputs):
    gathered = None
    async for chunk in runnable.astream(inputs):
        gathered = chunk if gathered is None else gathered + chunk
    return gathered


async def generate_one(sample, plan, strategy, *, model, judge, checker=None, today):
    """与线上相同：真实的第 1 次调用给出 tool_call id，第 2 次调用沿用它（DeepSeek 思考模式要求）。"""
    base = {**chat_prompt_vars(today), "history": [], "input": sample.query}
    first = await _stream(chat_prompt | model.bind_tools(get_registry().tools_for_model(), tool_choice="auto"), base)
    first_text = first.content if isinstance(first.content, str) else ""
    faq_calls = [c for c in first.tool_calls if c["name"] == "query_faq"]
    if not faq_calls:
        return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                         False, is_refusal(first_text), first_text, [], None, [], "")
    r = await retrieve(sample.query, strategy, plan=plan, exclude_mined=True)
    data = {"evidence": [asdict(e) for e in r.evidence]}
    evidence = collect_evidence([(c["id"], sample.query, data) for c in faq_calls])
    check = await self_check([sample.query], evidence.citations, checker=checker)
    messages = []
    for c in first.tool_calls:
        if c["name"] != "query_faq":
            content = EVAL_SKIPPED_TOOL
        elif check.useful:
            content = render_evidence(evidence.by_call[c["id"]])
        else:
            content = REFUSED_CONTENT
        messages.append(ToolMessage(content=content, tool_call_id=c["id"], name=c["name"]))
    request = AIMessage(content=first_text, tool_calls=first.tool_calls)
    second = await _stream(chat_prompt | model, {
        **base, "tool_round": [request, *messages, SystemMessage(TOOL_ROUND_CLOSING)],
    })
    answer = second.content if second is not None and isinstance(second.content, str) else ""
    citations = [c.to_dict() for c in evidence.citations] if check.useful else []
    refused = is_refusal(answer)
    if refused:
        return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                         True, True, answer, citations, None, [], check.reason)
    shown = evidence.citations if check.useful else []
    try:
        result = await judge.ainvoke({"evidence": format_evidence(shown) or "（无证据）", "answer": answer})
        verdict = result["parsed"]
        if verdict is None:
            raise ValueError("裁判结果无效")
    except Exception:
        logger.exception("裁判失败：%s/%s", sample.id, strategy)
        return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                         True, False, answer, citations, None, [], "裁判失败")
    return GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                     True, False, answer, citations, verdict.faithful, verdict.unsupported_claims, verdict.reason)
```

   - `write_faith_cases`：只处理 `strategy == "hybrid_rerank"`、`bucket in ANSWERABLE`、`retrieved and not refused and faithful is False` 的结果。`reason` 写 `f"{r.reason}\n未找到依据：{'；'.join(r.unsupported)}"`。每条一个事务：`upsert_case(...)` 后 `commit`。
   - 报告：`render_report(...)` 的结果打印到控制台，并写入 `evals/reports/rag_eval_<YYYYmmdd-HHMMSS>.md`（目录不存在时创建）。报告开头写运行参数和评估集题数。
   - 退出码：评估集检查失败或 `failures` 非空时为 1，否则为 0。
7. `evals/run_faith_judge_eval.py`：读取 `evals/faith_judge_samples.jsonl`（每行 `{"id", "evidence": [{"n", "section_path", "question", "answer"}], "answer", "faithful"}`），把证据转为 `Citation(n, 0, section_path, question, answer)` 后用 `format_evidence` 生成输入，调用 `get_faith_judge()`（并发 4）。逐条打印 `✅/❌ id 期望 实际 reason`，最后打印准确率。准确率低于 0.90 或有调用失败时退出码为 1。结构仿照 `evals/run_dedup_eval.py`。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app evals tests .gitignore
git commit -m "feat(ch04): RAG eval framework with four-strategy retrieval and faithfulness stages"
```

---

### Task 11: 评估集与裁判样例（数据类，代替 TDD）

**Files:**
- Create: `evals/rag_eval.jsonl`（300 题）
- Create: `evals/faith_judge_samples.jsonl`（30 条）
- Delete: `evals/retrieval_samples.jsonl`（相关样例并入 C 桶）
- Modify: `tests/test_rag_eval_set.py`（追加真实文件结构测试）

**Interfaces:**
- Consumes: Task 3 的知识文档；Task 10 的 `--list-keys`、`--check`、`validate`。
- Produces：spec §8.1 的评估集；spec §8.4 的裁判样例。

- [ ] **Step 1: Claude 准备环境**

Run: `uv run python scripts/build_kb.py --rebuild && uv run python scripts/build_kb.py --check`
Expected: 退出码 0。

- [ ] **Step 2: 写真实文件的结构测试（先失败）**

`tests/test_rag_eval_set.py` 追加：

```python
def test_real_eval_set_shape():
    samples = es.load_samples()
    assert es.validate(samples, None, full=True) == []


def test_real_faith_judge_samples_shape():
    path = es.SAMPLES_PATH.parent / "faith_judge_samples.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 30 and sum(r["faithful"] for r in rows) == 15
    assert len({r["id"] for r in rows}) == 30
    for r in rows:
        assert r["evidence"] and r["answer"]
        assert all({"n", "section_path", "question", "answer"} <= set(e) for e in r["evidence"])
```

Run: `uv run pytest tests/test_rag_eval_set.py -q`
Expected: FAIL（文件不存在）。

- [ ] **Step 3: 编写数据**

1. 运行 `uv run python evals/run_rag_eval.py --list-keys > /tmp/keys.tsv`，所有 `relevant` 只能从这份清单中逐字复制。
2. 按 spec §8.1 的表格编写 `evals/rag_eval.jsonl`（题号 `A01`–`A70`、`B01`–`B60`、`C01`–`C60`、`D01`–`D60`、`E01`–`E50`，按题号排序）：
   - `A_policy`：覆盖退货政策、售后手册、商品FAQ、常见问答的全部二级标题。easy 问法接近标题；medium 换说法或带一个条件；hard 带干扰信息或两个条件。
   - `B_model`：24 个型号每个至少 2 题。hard 题包含相近型号干扰（例如问 `X3` 时句中提到 `X3 Pro`）和非规范写法（`x3pro`、`S10-MAX`）。
   - `C_colloquial`：包含 `evals/retrieval_samples.jsonl` 中全部 `expected_questions` 非空的样例（`keyword` 作为 `query`，按清单把期望问题换成来源键），其余为口语、俗称、情绪化问法。
   - `D_unanswerable`：4 类各 15 题（easy/medium/hard 各 5 题）：不存在的型号（`X7`、`S30` 等）；存在的型号但问文档没写的属性（防水等级、产地等）；知识库没写的业务（增值税专用发票、分期免息等）；需要承诺才能回答的问题（"明天一定到账吗"）。编写前在清单和文档中搜索关键词，确认确实没有答案。第 4 类中，知识库写明了一般时限的，不能放进 D 桶。
   - `E_multi`：每题 2–3 个来源键，例如"X3 Pro 保修多久，过了保修期怎么修"。
3. 编写 `evals/faith_judge_samples.jsonl`：30 条，忠实 15 条、编造 15 条。证据从真实知识文档摘录 1–3 条。编造方式各 5 条：改数字；加证据外的条件或承诺；张冠李戴（把一个型号的参数说成相近型号的）。
4. 删除 `evals/retrieval_samples.jsonl`。

- [ ] **Step 4: 运行检查**

Run: `uv run pytest tests/test_rag_eval_set.py -q && uv run python evals/run_rag_eval.py --check`
Expected: 测试 PASS；脚本打印"评估集检查通过"，退出码 0。

- [ ] **Step 5: Claude 审核**

1. 每个桶抽查 10%（共 30 题），对照 `/tmp/keys.tsv` 和文档原文核对 `relevant` 是否完整、是否多标。
2. D 桶 60 题逐条确认：知识库中确实没有答案。
3. 发现问题时，把题号和修改要求交给 Codex 修正，重跑 Step 4。

- [ ] **Step 6: 提交**

```bash
git add evals tests/test_rag_eval_set.py
git commit -m "feat(ch04): 300-question RAG eval set and faithfulness judge samples"
```

---

### Task 12: 真实评估、门槛校准与 Prompt 调优（验证类）

**Files:**
- Modify: `app/config.py`（`RERANK_MIN_SCORE`）、`tests/test_config.py`
- Modify（按需）: `app/prompts.py`（改写、自评、裁判、System Prompt）
- Create: `evals/reports/rag_eval_<时间>.md`（最终报告）

本任务由 Claude 运行评估、分析结果；Codex 只按 Claude 给出的具体修改要求改常量和 Prompt。调优目标（不是验收门槛，未达到时向用户报告实际数字）：`hybrid_rerank` 全体 MRR 不低于其他 3 种策略；Faithfulness ≥ 0.90；D 正确拒答率 ≥ 0.85；误拒率 ≤ 0.10。

- [ ] **Step 1: 裁判自检**

Run: `uv run python evals/run_faith_judge_eval.py`
Expected: 准确率 ≥ 0.90，退出码 0。未达到时，Claude 分析错判样例，交给 Codex 修改 `FAITH_JUDGE_SYSTEM_PROMPT`，最多 2 轮。

- [ ] **Step 2: 检索段与门槛校准**

Run: `uv run python evals/run_rag_eval.py --stage retrieval`
Expected: 报告含 4 种策略 × 5 组的表、难度表、门槛扫描和建议门槛。

Claude 按校准规则（A/B/C/E Top-1 保留率不低于 95% 的最高门槛）确定新值，交给 Codex 修改 `RERANK_MIN_SCORE` 和 `tests/test_config.py` 中的断言。然后重跑本步，确认"门槛后"表使用新值。

- [ ] **Step 3: 工具选择评估**

Run: `uv run python evals/run_tool_selection_eval.py`
Expected: 工具集合完全匹配率 ≥ 90%，FAQ 原话包含率 100%，退出码 0。未达到时，修改 System Prompt 工具使用第 3 条，最多 2 轮。

- [ ] **Step 4: 生成段**

Run: `uv run python evals/run_rag_eval.py --stage generation`
Expected: 报告含生成表，`faith_cases` 写入条数打印在控制台。

如果未达到调优目标：Claude 按失败原因分类（改写错误 → 改写 Prompt；自评误判 → 自评 Prompt；回答编造或不按格式拒答 → System Prompt），每次只改一处，交给 Codex 修改，重跑本步。最多 2 轮。

- [ ] **Step 5: 最终全量运行**

Run: `uv run python evals/run_rag_eval.py --stage all --gen-strategies all`
Expected: 退出码 0；`evals/reports/` 下生成一份完整报告。

- [ ] **Step 6: 回归**

Run: `uv run pytest -q && uv run python evals/run_extract_eval.py && uv run python evals/run_chat_samples.py`
Expected: 全部通过（`run_chat_samples.py` 人工检查回答带引用、无承诺措辞）。

- [ ] **Step 7: 提交**

```bash
git add app/config.py app/prompts.py tests/test_config.py evals/reports
git commit -m "feat(ch04): calibrate rerank threshold and tune prompts on eval set"
```

---

### Task 13: 前端（Vibe Coding）

**Files:**
- Modify: `app/web/index.html`
- Modify: `app/web/faith_cases.html`

本任务按 CLAUDE.md 的例外规则执行：Claude 把效果描述转成任务交给 Codex，不走 brainstorm、TDD 和 code review。用户看页面后描述调整，Claude 再交给 Codex。

- [ ] **Step 1: 聊天页（交给 Codex 的效果描述）**

1. 处理 SSE 事件 `citations`：`{"items": [{n, chunk_id, section_path, question, answer}], "refused": bool}`。保存在当前回答上。
2. 回答正文中的 `[n]`（n 在 `items` 的编号范围内）渲染为上标样式的可点击角标；范围外的编号按普通文字显示。流式输出过程中同样处理（每次追加文字后重新渲染当前回答）。
3. 点击角标弹出卡片：标题为章节路径，正文为"问：…"和"答：…"，保留换行。卡片底部有"上一段""下一段"按钮：先调 `GET /api/knowledge/chunks/{chunk_id}` 取 `prev_chunk_id`、`next_chunk_id`，指针为空时隐藏对应按钮；点击后用同一接口取对应块，更新卡片内容。点击卡片外部或按 Esc 关闭卡片。接口失败时卡片显示"原文加载失败"。
4. 每条助手回答完成（收到 `done`）后，在回答左下角显示 👍 和 👎 两个按钮。点击其中一个：所选按钮高亮，旁边显示"已反馈"，两个按钮都禁用。记录追加到 `localStorage` 键 `aftersales_feedback`（数组，元素 `{session_id, answer_index, rating: "up"|"down", at: ISO 时间}`）。读写都包在 `try/catch` 中，存储不可用时页面照常工作。不调用后端。
5. 不改变现有的会话、流式输出、错误提示和"新对话"行为。

- [ ] **Step 2: 台账页（交给 Codex 的效果描述）**

1. 页面加载时调 `GET /api/faith-cases`。顶部有状态筛选（全部 / 未解决 / 已解决 / 无需解决），切换时带 `status` 参数重新加载。
2. 每行显示题号、桶、问题、`seen_count`、`last_seen_at`、状态。`resolved_at` 非空且状态为"未解决"时显示红色「复发」标签。
3. 点击一行展开：答案原文、裁判理由、证据全集（编号、章节路径、问、答）。`cited` 中的编号对应的证据高亮。
4. 每行有「已解决」「无需解决」按钮。点击后弹出输入框，必须填写处置说明（1–300 字）才能提交；调 `POST /api/faith-cases/{id}/resolve`；成功后刷新该行；失败时显示接口返回的错误。

- [ ] **Step 3: Claude 实测**

1. 确认旧进程已退出（`pgrep -f "uvicorn app.main:app"` 无输出），然后启动 `uv run uvicorn app.main:app --port 8000`。
2. 在浏览器打开 `http://127.0.0.1:8000/`：问"X3 Pro 耳机充满电能用多久？"，点击角标，检查章节路径、原文、上一段和下一段；点击 👍，检查锁定和"已反馈"。
3. 打开 `http://127.0.0.1:8000/admin/faith-cases`：检查列表、展开、筛选和处置。
4. 请用户查看页面并描述调整。按用户描述交给 Codex 修改，直到用户确认。

- [ ] **Step 4: 提交**

```bash
git add app/web
git commit -m "feat(ch04): clickable citations, feedback buttons and faith case ledger page"
```

---

### Task 14: 验收脚本与文档

**Files:**
- Create: `scripts/demo4.sh`（Codex）
- Modify: `CLAUDE.md`（Claude）

- [ ] **Step 1: 编写 `scripts/demo4.sh`（交给 Codex）**

```bash
#!/usr/bin/env bash
# ch04 验收。前置：MySQL、Milvus 已启动，已执行 build_kb.py --rebuild，服务已启动。
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
BASE="${BASE_URL:-http://127.0.0.1:8000}"

if ! curl --fail --silent --show-error --connect-timeout 5 --max-time 10 "$BASE/health" >/dev/null 2>&1; then
    printf '%s\n' '请先启动服务：uv run uvicorn app.main:app --port 8000' >&2
    exit 1
fi

mysql_q() {
    docker exec aftersales-mysql mysql -N --default-character-set=utf8mb4 \
        -uaftersales -paftersales aftersales -e "$1"
}

ask() {
    curl --fail --silent --show-error -N -X POST "$BASE/chat/stream" \
        -H 'Content-Type: application/json' \
        -d "{\"user_id\":\"demo4-$(date +%s)-$RANDOM\",\"message\":\"$1\"}"
}

printf '%s\n' '=== 验收 1：四策略对比报告 ==='
uv run python evals/run_rag_eval.py --stage retrieval

printf '%s\n' '=== 验收 2：BM25 单路命中型号 ==='
uv run python - <<'PY'
import asyncio
import sys

from app.db.engine import dispose_engine
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.rerank import close_rerank
from app.knowledge.retrieval import retrieve


async def main() -> int:
    try:
        await ensure_collection()
        r = await retrieve("X3 Pro 续航多久", "bm25")
        top = r.ranked[:3]
        for i, e in enumerate(top, 1):
            print(f"{i}. {e.section_path} | BM25 {e.score:.3f}")
        return 0 if any("X3 Pro" in e.section_path for e in top) else 1
    finally:
        await close_milvus()
        await close_rerank()
        await dispose_engine()


sys.exit(asyncio.run(main()))
PY

printf '%s\n' '=== 验收 3：引用编号定位原文 ==='
sse="$(ask 'X3 Pro 耳机充满电能用多久？')"
read -r chunk_id section_path < <(printf '%s\n' "$sse" | uv run python -c '
import json, re, sys
events, name = [], None
for line in sys.stdin.read().splitlines():
    if line.startswith("event: "):
        name = line[7:]
    elif line.startswith("data: "):
        events.append((name, json.loads(line[6:])))
reply = "".join(d["text"] for n, d in events if n == "token")
cites = next((d for n, d in events if n == "citations"), None)
print("回复：" + reply, file=sys.stderr)
if cites is None or cites["refused"] or not cites["items"] or not re.search(r"\[\d+\]", reply):
    print("验收失败：没有引用编号或引用列表", file=sys.stderr)
    sys.exit(1)
first = cites["items"][0]
print(first["chunk_id"], first["section_path"])
')
chunk_json="$(curl --fail --silent --show-error "$BASE/api/knowledge/chunks/$chunk_id")"
CHUNK_JSON="$chunk_json" EXPECTED="$section_path" uv run python -c '
import json, os, sys
got = json.loads(os.environ["CHUNK_JSON"])["section_path"]
print("接口返回章节路径：" + got)
sys.exit(0 if got == os.environ["EXPECTED"] else 1)
'
printf '%s\n' '页面部分：在聊天页点击角标，人工确认显示原文和章节路径。'

printf '%s\n' '=== 验收 4：拒答并进入低置信度池 ==='
before="$(mysql_q 'SELECT COUNT(*) FROM low_confidence_questions')"
sse="$(ask '你们的 X9 耳机支持无线充电吗？')"
printf '%s\n' "$sse" | uv run python -c '
import json, sys
from app.prompts import REFUSAL_PREFIX
events, name = [], None
for line in sys.stdin.read().splitlines():
    if line.startswith("event: "):
        name = line[7:]
    elif line.startswith("data: "):
        events.append((name, json.loads(line[6:])))
reply = "".join(d["text"] for n, d in events if n == "token")
cites = next((d for n, d in events if n == "citations"), None)
print("回复：" + reply)
ok = cites is not None and cites["refused"] and reply.strip().startswith(REFUSAL_PREFIX)
sys.exit(0 if ok else 1)
'
after="$(mysql_q 'SELECT COUNT(*) FROM low_confidence_questions')"
if [[ "$after" -ne $((before + 1)) ]]; then
    printf '%s\n' "验收失败：问题池行数 $before → $after" >&2
    exit 1
fi
mysql_q 'SELECT id, raw_question, source, reason FROM low_confidence_questions ORDER BY id DESC LIMIT 1'
printf '%s\n' 'ch04 验收通过'
```

`read -r chunk_id section_path`：`section_path` 含空格，`read` 把第 1 个字段以后的内容全部放进最后一个变量，所以完整保留。

- [ ] **Step 2: Claude 运行验收**

1. 确认旧进程已退出，启动服务。
2. Run: `bash scripts/demo4.sh`
   Expected: 4 项通过，最后打印"ch04 验收通过"。

- [ ] **Step 3: Claude 更新 `CLAUDE.md`**

按 spec §13 修改：项目状态加 ch04；常用命令加 `run_rag_eval.py`（含 `--check`、`--list-keys`、`--stage`）、`run_faith_judge_eval.py`、`demo4.sh`，删除 `run_retrieval_eval.py`；架构图和模块表加 `query`、`rerank`、`grounding`、`low_confidence`、`faith_cases`、两个新 API；设计约束中替换"集合只存 id + 向量"和"`query_faq` 契约不改"两条，并加入：型号规范写法与查询侧归一、首尾排列与全局编号、自评失败按通过处理、`useful=false` 时替换证据并入池、评估生成段必须使用第 1 次调用的真实 tool_call id、`RERANK_API_KEY` 已接入代码。

- [ ] **Step 4: 全量测试与提交**

Run: `uv run pytest -q`
Expected: 全部 PASS。

```bash
git add scripts/demo4.sh CLAUDE.md
git commit -m "docs(ch04): acceptance demo and project guide updates"
```
