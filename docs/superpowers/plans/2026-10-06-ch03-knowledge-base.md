# ch03 知识库与向量检索 Implementation Plan

> **For agentic workers:** 本项目的执行方式由 `CLAUDE.md` 规定：Claude 把每个代码任务交给 Codex 实现，Codex 完成后由 Claude 审查 diff、运行测试、补记 dev-notes、提交并推送到 `ch03` 分支。标为"Claude 执行"的步骤（数据文件、`.env`、`CLAUDE.md`）由 Claude 直接完成。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建知识库（Markdown 文档切分 + 历史对话挖掘），MySQL 与 Milvus 双写，`query_faq` 的内部实现换成 dense 向量检索，契约不变。

**Architecture:** 三个入库来源（文档、`faq` 表、挖掘保留项）只写 MySQL `knowledge_chunks`，状态 `pending`。公共函数 `vectorize_pending()` 把 `pending` 行嵌入后 `upsert` 到 Milvus 集合 `knowledge`（主键 = MySQL 主键），再回填 `vector_id`、改为 `done`。在线检索：关键词嵌入 → Milvus Top-K → 按 id 读 MySQL 正文。

**Tech Stack:** Python 3.12、uv、FastAPI、SQLAlchemy 2.x（asyncio）+ asyncmy、MySQL 8、Milvus Standalone v2.6.22、pymilvus `AsyncMilvusClient`、langchain-openai `OpenAIEmbeddings`（硅基流动 BGE-M3，1024 维）、langchain-text-splitters、pytest + anyio。

**Spec:** `docs/superpowers/specs/2026-10-06-ch03-knowledge-base-design.md`

## 执行方式（每个任务）

1. Claude 把"Global Constraints"一节和该任务的全文作为任务描述，按 `CLAUDE.md` 中的命令交给 Codex。
2. Codex 按步骤实现，运行该任务的测试命令。Codex 不执行 `git commit`。
3. Claude 审查 diff，核对 spec 和本计划，运行 `uv run pytest -q`。
4. 有问题时，Claude 把具体问题交给 Codex 重做，不直接改代码。
5. 通过后，Claude 补记 `dev-notes/ch03.md`，提交并推送到 `ch03`。

## Global Constraints

- 所有工作在 `ch03` 分支上进行。
- 依赖只通过 `uv add` 添加。本章新增运行时依赖仅限：`pymilvus`、`langchain-text-splitters`。
- 配置新增读取 3 个环境变量：`EMBED_API_KEY`（必填）、`EMBED_BASE_URL`（默认 `https://api.siliconflow.cn/v1`）、`MILVUS_URI`（必填）。不硬编码密钥或地址。
- `db/schema.sql` 和 `db/schema_ch03.sql` 是用户 DDL，**逐字保存，不许修改**。ORM 只映射，不调用 `create_all`。
- 执行 `.sql` 文件时用 `conn.exec_driver_sql(语句)`，不用 `text()`。
- 异步 ORM：提交后要读取数据库默认值列时，先 `await session.refresh(obj)`。`async_sessionmaker(..., expire_on_commit=False)`。
- 测试中的数据库引擎一律用 `poolclass=NullPool`。测试中的 `AsyncMilvusClient` 在每个测试的事件循环内新建、测试结束时关闭。原因：pytest 的 anyio 测试各自使用独立事件循环，gRPC 通道和连接池跨循环复用会报错。
- `knowledge_chunks` 有自引用外键（`prev_chunk_id`、`next_chunk_id`）。删除行之前，先把这些行的两个指针更新为 NULL，再 `DELETE`。
- 所有自写的重试一律用 `app.retry.retry_async`（指数回退加抖动）。嵌入调用的重试交给 `OpenAIEmbeddings(max_retries=...)`（OpenAI SDK 自带指数回退）。不许手写重试循环。
- `OpenAIEmbeddings` 必须设置 `check_embedding_ctx_length=False`（硅基流动不接受 token id 输入）和 `model_kwargs={"encoding_format": "float"}`。
- Milvus 集合只存 `id`（INT64 主键，`auto_id=False`）和 `vector`（FLOAT_VECTOR，`dim=1024`），索引 `AUTOINDEX` + `COSINE`，集合一致性级别 `Strong`（写入后立即可检索；默认的 Bounded 会让刚写入的向量短时间查不到）。正文和元数据只存 MySQL。
- 向量化文本只由 `app.knowledge.chunking.knowledge_text(category, questions, answer)` 生成，格式 `f"{category}\n{questions}\n{answer}"`。
- `query_faq` 的入参 schema、工具描述、出参结构 `{"results": [{"question", "answer", "category"}]}`、注册配置都不许改。`app/prompts.py` 中的 `CHAT_SYSTEM_TEMPLATE` 不许改。
- 离线单测不访问网络、数据库和 Milvus。需要数据库的测试用 fixture `db`；需要 Milvus 的测试用 fixture `milvus`。连不上时直接失败，不跳过。
- 测试中一律用 `tests/fakes.py` 的 `FakeEmbeddings`，不调用真实嵌入接口。测试中的抽取器和裁定器用 `RunnableLambda`，不调用上游模型。
- 对外错误信息只用固定文案。完整异常用 `logger.exception` 写日志。
- 代码注释和文档用中文，ASD-STE100 风格。
- Codex 不执行 `git commit`、`git push`，不修改 `.env`、`CLAUDE.md`、`docs/`、`dev-notes/`、`knowledge/` 下的数据文件、`evals/*.jsonl`。

## Review Focus

1. **Milvus 有向量、MySQL 已删除该行**（`--rebuild` 中途中断，或检索与重建同时发生）：`query_faq` 跳过该 id，不报错，不返回空正文。→ Task 6 的 `test_search_skips_ids_missing_in_mysql`。
2. **MySQL 行还是 `pending`、Milvus 已有向量**（中断位置在两次写之间）：在线检索不返回该行，重跑后补齐且不产生重复。→ Task 5 的 `test_crash_between_milvus_and_mysql_then_resume`、Task 6 的 `test_search_ignores_pending_rows`。
3. **文档中的小节只有表格，或表格中某一行本身超过 400 字**：切分不丢行，每块都带表头。→ Task 3 的 `test_table_only_section_and_oversized_row`。
4. **LLM 裁定返回的候选序号越界**（例如只有 2 个候选却返回 5）：这一行保持 `extracted`，不误删、不误入库。→ Task 9 的 `test_dedup_out_of_range_index_keeps_extracted`。
5. **同一日期的挖掘任务跑两次**：已有暂存行的会话不重复抽取，批号不重复。→ Task 8 的 `test_extract_day_twice_skips_mined_and_continues_batch_numbers`。

---

## 文件结构

```
docker-compose.yml（修改）  scripts/reset_db.sh（修改）  pyproject.toml / uv.lock     Task 1
app/config.py（修改）  app/db/models.py（修改）  app/db/engine.py（修改）           Task 1
tests/conftest.py（修改）  tests/test_config.py（修改）  tests/test_db_models.py（修改）  Task 1
app/knowledge/__init__.py  app/knowledge/milvus.py  app/knowledge/embeddings.py     Task 2
tests/fakes.py（修改）  tests/conftest.py（修改）  tests/test_milvus.py              Task 2
app/knowledge/chunking.py  tests/test_chunking.py                                   Task 3
app/repositories/knowledge.py  app/knowledge/ingest.py  tests/test_ingest.py        Task 4
app/knowledge/vectorize.py  scripts/build_kb.py  tests/test_vectorize.py            Task 5
app/knowledge/retrieval.py  app/tools/faq.py（修改）  删除 app/repositories/faq.py
  tests/test_retrieval.py  tests/test_tools.py / tests/test_repositories.py（修改）  Task 6
knowledge/docs/{policy,faq,manual}/*.md（Claude）  evals/retrieval_samples.jsonl（Claude）
  evals/run_retrieval_eval.py（Codex）  tests/test_knowledge_docs.py（Codex）       Task 7
app/schemas.py / app/prompts.py / app/llm.py（修改）  app/repositories/staging.py
  app/knowledge/mining.py  app/knowledge/history_seed.py  scripts/seed_history.py
  tests/test_mining_extract.py                                                      Task 8
app/knowledge/mining.py（续）  scripts/mine_qa.py  tests/test_mining_dedup.py        Task 9
knowledge/history/conversations.jsonl（Claude）  evals/mine_extract_samples.jsonl、
  evals/dedup_samples.jsonl（Claude）  evals/run_mine_extract_eval.py、
  evals/run_dedup_eval.py（Codex）                                                  Task 10
scripts/demo3.sh（Codex）  CLAUDE.md（Claude）                                      Task 11
```

---

### Task 1: 基础设施（Milvus 容器、建表、配置、ORM）

**Files:**
- Modify: `docker-compose.yml`、`scripts/reset_db.sh`、`app/config.py`、`app/db/models.py`、`app/db/engine.py`、`tests/conftest.py`、`tests/test_config.py`、`tests/test_db_models.py`
- 依赖：`uv add pymilvus langchain-text-splitters`
- Claude（不是 Codex）先在 `.env` 末尾加入：`MILVUS_URI=http://127.0.0.1:19530`

**Interfaces:**
- Produces:
  - `Settings.embed_api_key: SecretStr`、`Settings.embed_base_url: str`、`Settings.milvus_uri: str`
  - 常量（`app/config.py`）：见 Step 3
  - ORM：`KnowledgeChunk`、`QaExtractionStaging`（`app/db/models.py`）
  - `app.db.engine.dispose_engine() -> Awaitable[None]`：关闭全局引擎（脚本退出前调用）

- [ ] **Step 1（Claude 执行）：在 `.env` 加入 `MILVUS_URI`**

```bash
printf '\n# --- 向量库：本项目 Docker Compose 的 Milvus ---\nMILVUS_URI=http://127.0.0.1:19530\n' >> .env
```

- [ ] **Step 2: 写失败的测试**

`tests/test_config.py`：`_set_required` 增加两个必填变量；`test_unknown_env_file_keys_are_ignored` 的 `.env` 内容和 `delenv` 列表也加上它们。新增测试：

```python
def _set_required(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CHAT_MODEL", "m1")
    monkeypatch.setenv("CHAT_API_KEY", "k1")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@h:3307/aftersales")
    monkeypatch.setenv("EMBED_API_KEY", "e1")
    monkeypatch.setenv("MILVUS_URI", "http://m:19530")


def test_reads_knowledge_variables(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.embed_api_key.get_secret_value() == "e1"
    assert s.embed_base_url == "https://api.siliconflow.cn/v1"
    assert s.milvus_uri == "http://m:19530"


def test_knowledge_constants():
    from app import config

    assert config.EMBED_MODEL == "BAAI/bge-m3"
    assert config.EMBED_DIM == 1024
    assert config.KNOWLEDGE_COLLECTION == "knowledge"
    assert config.KNOWLEDGE_TEST_COLLECTION == "knowledge_test"
    assert config.CHUNK_MAX_CHARS == 400
    assert config.OVERLAP_MAX_CHARS == 100
    assert config.VECTORIZE_BATCH_SIZE == 16
    assert config.FAQ_MIN_SCORE == 0.50
    assert config.MINE_BATCH_SIZE == 20
    assert config.MINE_CONCURRENCY == 4
    assert config.DEDUP_KB_MIN_SCORE == 0.55
    assert config.DEDUP_STAGING_MIN_SCORE == 0.75
    assert config.MINED_CATEGORIES == ("退换货", "运费", "发票", "售后维修", "账户", "支付", "物流", "其他")
```

`tests/test_db_models.py` 追加：

```python
from app.db.models import KnowledgeChunk, QaExtractionStaging


async def test_knowledge_chunk_defaults_and_self_pointers(db):
    async with db() as s:
        a = KnowledgeChunk(category="退货政策", questions="退货条件", answer="七天内可退。")
        b = KnowledgeChunk(category="退货政策", questions="退货流程", answer="先申请。")
        s.add_all([a, b])
        await s.flush()
        a.next_chunk_id, b.prev_chunk_id = b.id, a.id
        await s.commit()
        await s.refresh(a)
    assert a.vectorize_status == "pending"
    assert a.is_key_clause is False
    assert a.vector_id is None
    assert a.next_chunk_id == b.id


async def test_staging_defaults(db):
    async with db() as s:
        row = QaExtractionStaging(batch_no="20261005-01", source_ref="conversation:1", question="能开专票吗", answer="可以。")
        s.add(row)
        await s.commit()
        await s.refresh(row)
    assert row.status == "extracted"
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/test_config.py tests/test_db_models.py -q`
Expected: FAIL（`ImportError: KnowledgeChunk`、`AttributeError: EMBED_MODEL`）

- [ ] **Step 4: 实现**

`app/config.py` 新增常量和字段：

```python
EMBED_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024
EMBED_TIMEOUT_SECONDS = 10
EMBED_MAX_RETRIES = 2
KNOWLEDGE_COLLECTION = "knowledge"
KNOWLEDGE_TEST_COLLECTION = "knowledge_test"
CHUNK_MAX_CHARS = 400
OVERLAP_MAX_CHARS = 100
VECTORIZE_BATCH_SIZE = 16
MILVUS_MAX_ATTEMPTS = 3
MILVUS_RETRY_BASE_DELAY = 0.5
MILVUS_RETRY_MAX_DELAY = 4.0
# 设计阶段实测：相关问题第 1 名约 0.65，无关问题最高 0.43。由检索评估集校准。
FAQ_MIN_SCORE = 0.50
MINE_BATCH_SIZE = 20
MINE_CONCURRENCY = 4
DEDUP_KB_MIN_SCORE = 0.55
DEDUP_STAGING_MIN_SCORE = 0.75
MINED_CATEGORIES = ("退换货", "运费", "发票", "售后维修", "账户", "支付", "物流", "其他")


class Settings(BaseSettings):
    ...  # 原有字段不变
    embed_api_key: SecretStr
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    milvus_uri: str
```

`app/db/models.py` 新增（`Boolean` 映射 `TINYINT(1)`，读出为 `bool`）：

```python
from sqlalchemy import Boolean


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(255))
    questions: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    section_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_key_clause: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    prev_chunk_id: Mapped[int | None] = mapped_column(ID, ForeignKey("knowledge_chunks.id"), nullable=True)
    next_chunk_id: Mapped[int | None] = mapped_column(ID, ForeignKey("knowledge_chunks.id"), nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vectorize_status: Mapped[str] = mapped_column(Enum("pending", "done"), server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))


class QaExtractionStaging(Base):
    __tablename__ = "qa_extraction_staging"

    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum("extracted", "kept", "discarded"), server_default=text("'extracted'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
```

`app/db/engine.py` 新增：

```python
async def dispose_engine() -> None:
    """关闭全局引擎。脚本在 asyncio.run 结束前调用，避免连接在事件循环关闭后回收。"""
    global _sessionmaker
    if _sessionmaker is not None:
        await _sessionmaker.kw["bind"].dispose()
        _sessionmaker = None
```

`tests/conftest.py`：
- `_reset_schema`：先 `DROP TABLE IF EXISTS qa_extraction_staging`、`knowledge_chunks`，再删原有 4 张表；按 `schema.sql`、`schema_ch03.sql`、`seed.sql` 的顺序执行。
- `_clear_runtime_tables`：先执行 `UPDATE knowledge_chunks SET prev_chunk_id = NULL, next_chunk_id = NULL`，再按 `qa_extraction_staging`、`knowledge_chunks`、`messages`、`tickets`、`conversations` 的顺序 `DELETE`。

`docker-compose.yml`：
- `mysql` 的卷挂载改为：`./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro`、`./db/schema_ch03.sql:/docker-entrypoint-initdb.d/02-schema-ch03.sql:ro`、`./db/seed.sql:/docker-entrypoint-initdb.d/03-seed.sql:ro`。
- 新增服务（照搬 `~/mewhelp-src/docker-compose.yml` 已验证的配置，只改名称和端口）：

```yaml
  milvus-etcd:
    image: quay.io/coreos/etcd:v3.5.16
    container_name: aftersales-milvus-etcd
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - aftersales-milvus-etcd:/etcd
    command: etcd -advertise-client-urls=http://milvus-etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
    healthcheck:
      test: ["CMD", "etcdctl", "endpoint", "health"]
      interval: 10s
      timeout: 20s
      retries: 10

  milvus-minio:
    image: minio/minio:RELEASE.2024-12-18T13-15-44Z
    container_name: aftersales-milvus-minio
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    volumes:
      - aftersales-milvus-minio:/minio_data
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 20s
      retries: 10

  milvus-standalone:
    image: milvusdb/milvus:v2.6.22
    container_name: aftersales-milvus
    command: ["milvus", "run", "standalone"]
    environment:
      ETCD_ENDPOINTS: milvus-etcd:2379
      MINIO_ADDRESS: milvus-minio:9000
    volumes:
      - aftersales-milvus-data:/var/lib/milvus
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 10s
      start_period: 90s
      timeout: 20s
      retries: 10
    ports:
      - "19530:19530"
      - "9091:9091"
    depends_on:
      - milvus-etcd
      - milvus-minio
```

  `volumes:` 增加 `aftersales-milvus-etcd`、`aftersales-milvus-minio`、`aftersales-milvus-data`，每个都写 `name:` 与键名相同。minio 的镜像标签以 `~/mewhelp-src/docker-compose.yml` 中实际使用的为准；minio 不映射宿主端口（本机 9000 已被占用）。

`scripts/reset_db.sh`：保留原有 HEX 校验；末尾追加两项检查，失败时 `exit 1`：

```bash
docker exec aftersales-mysql mysql -N -uaftersales -paftersales aftersales -e "SHOW TABLES LIKE 'knowledge_chunks'" | grep -q knowledge_chunks || { echo "knowledge_chunks 未创建"; exit 1; }
curl --fail --silent http://127.0.0.1:9091/healthz >/dev/null || { echo "Milvus 健康检查失败"; exit 1; }
echo "Milvus 健康检查正常"
```

- [ ] **Step 5: 重建容器并运行测试**

Run: `bash scripts/reset_db.sh && uv run pytest -q`
Expected: 输出"FAQ 中文编码正确""Milvus 健康检查正常"；全部测试 PASS。

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml scripts/reset_db.sh pyproject.toml uv.lock app/config.py app/db/models.py app/db/engine.py tests/conftest.py tests/test_config.py tests/test_db_models.py
git commit -m "feat(ch03): Milvus in compose, knowledge tables, config and ORM"
```

---

### Task 2: Milvus 与嵌入适配层、测试替身

**Files:**
- Create: `app/knowledge/__init__.py`（空）、`app/knowledge/milvus.py`、`app/knowledge/embeddings.py`、`tests/test_milvus.py`
- Modify: `tests/fakes.py`、`tests/conftest.py`

**Interfaces:**
- Consumes: Task 1 的常量和 `Settings`。
- Produces（`app/knowledge/milvus.py`）：
  - `get_milvus() -> AsyncMilvusClient`、`get_collection() -> str`
  - `set_milvus(client: Any | None, collection: str = KNOWLEDGE_COLLECTION) -> None`
  - `async ensure_collection() -> None`
  - `async upsert_vectors(rows: list[tuple[int, list[float]]]) -> None`
  - `async search_vectors(vector: list[float], limit: int) -> list[tuple[int, float]]`（按相似度降序，`(id, score)`）
  - `async delete_vectors(ids: list[int]) -> None`
  - `async count_vectors() -> int`（Strong 一致性）
  - `async close_milvus() -> None`
- Produces（`app/knowledge/embeddings.py`）：`get_embeddings() -> Embeddings`、`set_embeddings(e: Embeddings | None) -> None`
- Produces（`tests/fakes.py`）：`FakeEmbeddings(rules: list[tuple[str, list[float]]] | None = None)`、`unit(i: int) -> list[float]`、`blend(i: int, j: int, cos: float) -> list[float]`
- Produces（`tests/conftest.py`）：autouse fixture `_isolate_knowledge`；fixture `milvus`

- [ ] **Step 1: 写测试替身**

`tests/fakes.py` 追加：

```python
import math
import random

from langchain_core.embeddings import Embeddings

from app.config import EMBED_DIM


def unit(i: int) -> list[float]:
    """第 i 维为 1 的单位向量。"""
    v = [0.0] * EMBED_DIM
    v[i] = 1.0
    return v


def blend(i: int, j: int, cos: float) -> list[float]:
    """和 unit(i) 的余弦相似度为 cos 的单位向量。"""
    v = [0.0] * EMBED_DIM
    v[i] = cos
    v[j] = math.sqrt(1 - cos * cos)
    return v


class FakeEmbeddings(Embeddings):
    """确定性假嵌入。文本包含 rules 中某个子串时，返回对应向量（按 rules 顺序取第一个）；
    否则按文本哈希生成随机单位向量（两两余弦接近 0）。calls 记录每次调用的文本列表。"""

    def __init__(self, rules: list[tuple[str, list[float]]] | None = None):
        self.rules = rules or []
        self.calls: list[list[str]] = []

    def _vec(self, text: str) -> list[float]:
        for sub, v in self.rules:
            if sub in text:
                return v
        rng = random.Random(text)
        v = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.calls.append([text])
        return self._vec(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)
```

- [ ] **Step 2: 写 fixture**

`tests/conftest.py` 追加：

```python
from pymilvus import AsyncMilvusClient

from app.config import KNOWLEDGE_TEST_COLLECTION
from app.knowledge import milvus as milvus_mod
from app.knowledge.embeddings import set_embeddings
from tests.fakes import FakeEmbeddings


class BlockedMilvus:
    """没有使用 fixture milvus 的测试，访问 Milvus 时立即失败，避免误连生产集合。"""

    def __getattr__(self, name):
        raise RuntimeError("测试未启用 fixture milvus")


@pytest.fixture(autouse=True)
def _isolate_knowledge():
    set_embeddings(FakeEmbeddings())
    milvus_mod.set_milvus(BlockedMilvus(), KNOWLEDGE_TEST_COLLECTION)
    yield
    set_embeddings(None)
    milvus_mod.set_milvus(None)


@pytest.fixture
async def milvus():
    """每个测试重建 Milvus 测试集合。连不上时直接失败，不跳过。"""
    client = AsyncMilvusClient(uri=get_settings().milvus_uri, timeout=5)
    try:
        if await client.has_collection(KNOWLEDGE_TEST_COLLECTION, timeout=5):
            await client.drop_collection(KNOWLEDGE_TEST_COLLECTION, timeout=5)
    except Exception as exc:
        await client.close()
        pytest.fail(f"无法连接 Milvus，请先执行 docker compose up -d --wait（{exc}）", pytrace=False)
    milvus_mod.set_milvus(client, KNOWLEDGE_TEST_COLLECTION)
    await milvus_mod.ensure_collection()
    yield client
    milvus_mod.set_milvus(BlockedMilvus(), KNOWLEDGE_TEST_COLLECTION)
    await client.close()
```

  `BlockedMilvus` 抛出 `RuntimeError`（不是 `MilvusException`），所以 `retry_async` 不重试，工具立即返回 `tool_error`。

- [ ] **Step 3: 写失败的测试** `tests/test_milvus.py`

```python
import pytest
from langchain_openai import OpenAIEmbeddings

from app.config import EMBED_DIM, EMBED_MODEL
from app.knowledge import milvus as m
from app.knowledge.embeddings import build_embeddings
from app.config import get_settings
from tests.fakes import blend, unit

pytestmark = pytest.mark.anyio


async def test_ensure_collection_is_idempotent(milvus):
    await m.ensure_collection()
    desc = await milvus.describe_collection(m.get_collection())
    names = {f["name"] for f in desc["fields"]}
    assert names == {"id", "vector"}
    assert desc["consistency_level"] in (0, "Strong")  # pymilvus 返回枚举值 0 或名称


async def test_upsert_same_id_twice_does_not_duplicate(milvus):
    await m.upsert_vectors([(1, unit(0)), (2, unit(1))])
    await m.upsert_vectors([(1, unit(2))])
    assert await m.count_vectors() == 2


async def test_search_returns_ids_by_similarity(milvus):
    await m.upsert_vectors([(1, unit(0)), (2, blend(0, 1, 0.6)), (3, unit(5))])
    hits = await m.search_vectors(unit(0), 2)
    assert [i for i, _ in hits] == [1, 2]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-3)
    assert hits[1][1] == pytest.approx(0.6, abs=1e-3)


async def test_delete_vectors(milvus):
    await m.upsert_vectors([(1, unit(0)), (2, unit(1))])
    await m.delete_vectors([1, 99])
    assert await m.count_vectors() == 1


async def test_delete_empty_list_is_noop(milvus):
    await m.delete_vectors([])
    assert await m.count_vectors() == 0


def test_build_embeddings_settings():
    e = build_embeddings(get_settings())
    assert isinstance(e, OpenAIEmbeddings)
    assert e.model == EMBED_MODEL
    assert e.check_embedding_ctx_length is False
    assert e.model_kwargs == {"encoding_format": "float"}
    assert e.max_retries == 2
```

- [ ] **Step 4: 运行测试，确认失败**

Run: `uv run pytest tests/test_milvus.py -q`
Expected: FAIL（`ModuleNotFoundError: app.knowledge.milvus`）

- [ ] **Step 5: 实现** `app/knowledge/milvus.py`

```python
from typing import Any

from pymilvus import AsyncMilvusClient, DataType, MilvusException

from app.config import (
    EMBED_DIM,
    KNOWLEDGE_COLLECTION,
    MILVUS_MAX_ATTEMPTS,
    MILVUS_RETRY_BASE_DELAY,
    MILVUS_RETRY_MAX_DELAY,
    get_settings,
)
from app.retry import retry_async

_client: Any | None = None
_collection: str = KNOWLEDGE_COLLECTION


def get_milvus() -> Any:
    """返回全局 Milvus 客户端。第一次调用时按 MILVUS_URI 创建。"""
    global _client
    if _client is None:
        _client = AsyncMilvusClient(uri=get_settings().milvus_uri)
    return _client


def get_collection() -> str:
    return _collection


def set_milvus(client: Any | None, collection: str = KNOWLEDGE_COLLECTION) -> None:
    """替换全局客户端和集合名。测试用它指向测试集合；传 None 恢复为按配置创建。"""
    global _client, _collection
    _client, _collection = client, collection


async def _call(fn):
    return await retry_async(
        fn,
        attempts=MILVUS_MAX_ATTEMPTS,
        base_delay=MILVUS_RETRY_BASE_DELAY,
        max_delay=MILVUS_RETRY_MAX_DELAY,
        retry_on=(MilvusException,),
    )


async def ensure_collection() -> None:
    """集合不存在时创建并加载；已存在时只加载。"""
    client, name = get_milvus(), get_collection()
    if await _call(lambda: client.has_collection(name)):
        await _call(lambda: client.load_collection(name))
        return
    schema = AsyncMilvusClient.create_schema(auto_id=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    index_params = AsyncMilvusClient.prepare_index_params()
    index_params.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
    # 集合级 Strong：写入后立即可检索。数据量小，额外延迟可以忽略。
    await _call(lambda: client.create_collection(
        name, schema=schema, index_params=index_params, consistency_level="Strong"
    ))


async def upsert_vectors(rows: list[tuple[int, list[float]]]) -> None:
    if not rows:
        return
    data = [{"id": i, "vector": v} for i, v in rows]
    await _call(lambda: get_milvus().upsert(get_collection(), data))


async def search_vectors(vector: list[float], limit: int) -> list[tuple[int, float]]:
    """返回 (id, 相似度)，按相似度降序。COSINE 的 distance 越大越相似。"""
    result = await _call(lambda: get_milvus().search(
        get_collection(), data=[vector], limit=limit, search_params={"metric_type": "COSINE"},
    ))
    return [(int(hit["id"]), float(hit["distance"])) for hit in result[0]]


async def delete_vectors(ids: list[int]) -> None:
    if not ids:
        return
    await _call(lambda: get_milvus().delete(get_collection(), ids=ids))


async def count_vectors() -> int:
    rows = await _call(lambda: get_milvus().query(
        get_collection(), filter="", output_fields=["count(*)"], consistency_level="Strong",
    ))
    return int(rows[0]["count(*)"])


async def close_milvus() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
```

  如果 `create_schema` / `prepare_index_params` 在 `AsyncMilvusClient` 上不可用，改用 `MilvusClient.create_schema()`（二者都是 `BaseMilvusClient` 的类方法，已用 Context7 核对）。`load_collection` 在已加载的集合上调用是安全的。

`app/knowledge/embeddings.py`：

```python
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.config import EMBED_MAX_RETRIES, EMBED_MODEL, EMBED_TIMEOUT_SECONDS, VECTORIZE_BATCH_SIZE, Settings, get_settings

_embeddings: Embeddings | None = None


def build_embeddings(settings: Settings) -> OpenAIEmbeddings:
    # 硅基流动不接受 token id 输入，必须关闭长度检查，直接发送原文。
    return OpenAIEmbeddings(
        model=EMBED_MODEL,
        base_url=settings.embed_base_url,
        api_key=settings.embed_api_key,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
        chunk_size=VECTORIZE_BATCH_SIZE,
        max_retries=EMBED_MAX_RETRIES,
        timeout=EMBED_TIMEOUT_SECONDS,
    )


def get_embeddings() -> Embeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = build_embeddings(get_settings())
    return _embeddings


def set_embeddings(e: Embeddings | None) -> None:
    """替换全局嵌入对象。测试用它换成假嵌入；传 None 恢复为按配置创建。"""
    global _embeddings
    _embeddings = e
```

- [ ] **Step 6: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 7（Claude 执行）：真实上游冒烟**

```bash
uv run python -c "
import asyncio
from app.knowledge.embeddings import get_embeddings
v = asyncio.run(get_embeddings().aembed_query('邮费是多少'))
print(len(v), round(sum(x*x for x in v) ** 0.5, 3))
"
```
Expected: `1024 1.0`

- [ ] **Step 8: Commit**

```bash
git add app/knowledge/__init__.py app/knowledge/milvus.py app/knowledge/embeddings.py tests/fakes.py tests/conftest.py tests/test_milvus.py
git commit -m "feat(ch03): Milvus and embedding adapters with test doubles"
```

---

### Task 3: 文档切分

**Files:**
- Create: `app/knowledge/chunking.py`、`tests/test_chunking.py`

**Interfaces:**
- Consumes: `CHUNK_MAX_CHARS`、`OVERLAP_MAX_CHARS`。
- Produces:
  - `Chunk(category: str, questions: str, answer: str, section_path: str, is_key_clause: bool)`（frozen dataclass）
  - `ParsedDoc(title: str, chunks: list[Chunk])`
  - `parse_markdown(markdown: str) -> ParsedDoc`
  - `knowledge_text(category: str, questions: str, answer: str) -> str`
  - 常量 `PATH_SEP = " > "`、`KEY_CLAUSE_MARK = "【关键条款】"`

切分规则（spec 6.2、6.3）的补充约定：
- `MarkdownHeaderTextSplitter` 实测会丢掉空行，用 `"  \n"` 连接段落，并丢弃没有正文的小节。所以先把 `"  \n"` 替换为 `"\n"`。没有正文的小节不产生块。
- 块长度：不含重叠段时不超过 `CHUNK_MAX_CHARS`；含重叠段时不超过 `CHUNK_MAX_CHARS + OVERLAP_MAX_CHARS`。例外：单个表格数据行本身超过上限时，该行和表头单独成块。
- `is_key_clause` 只看块自身的正文（不含重叠段），避免标记随重叠传到下一块。标记在加完重叠后从全文删除。

- [ ] **Step 1: 写失败的测试** `tests/test_chunking.py`

```python
import pytest

from app.config import CHUNK_MAX_CHARS, OVERLAP_MAX_CHARS
from app.knowledge.chunking import KEY_CLAUSE_MARK, knowledge_text, parse_markdown

SENT = "这是一句用来凑长度的完整句子，内容没有实际含义。"  # 25 字


def test_header_paths_map_to_fields():
    doc = parse_markdown(
        "# 退货政策\n\n总则。\n\n## 退货条件\n\n### 无理由退货\n\n签收后七天内可退。\n"
    )
    assert doc.title == "退货政策"
    first, second = doc.chunks
    assert (first.questions, first.category, first.section_path) == ("退货政策", "退货政策", "退货政策")
    assert first.answer == "总则。"
    assert second.questions == "无理由退货"
    assert second.category == "退货政策 > 退货条件"
    assert second.section_path == "退货政策 > 退货条件 > 无理由退货"
    assert second.answer == "签收后七天内可退。"


def test_faq_heading_is_real_question():
    doc = parse_markdown("# 商品FAQ\n\n## 蓝牙耳机\n\n### 耳机怎么配对？\n\n长按电源键三秒。\n")
    assert doc.chunks[0].questions == "耳机怎么配对？"
    assert doc.chunks[0].category == "商品FAQ > 蓝牙耳机"


def test_paragraphs_keep_line_breaks():
    doc = parse_markdown("# 手册\n\n## 维修\n\n第一段。\n\n第二段。\n")
    assert doc.chunks[0].answer == "第一段。\n第二段。"


def test_empty_section_produces_no_chunk():
    doc = parse_markdown("# 手册\n\n## 空节\n\n## 有内容\n\n内容。\n")
    assert [c.questions for c in doc.chunks] == ["有内容"]


def test_requires_single_h1():
    with pytest.raises(ValueError):
        parse_markdown("## 没有一级标题\n\n内容。\n")
    with pytest.raises(ValueError):
        parse_markdown("# 甲\n\n内容。\n\n# 乙\n\n内容。\n")


def _sentences(n: int) -> list[str]:
    return [f"第{i:03d}句用来凑长度的完整内容。" for i in range(n)]  # 每句 16 字，内容互不相同


def test_long_section_split_recursively_with_sentence_overlap():
    sents = _sentences(50)  # 800 字
    doc = parse_markdown("# 手册\n\n## 长节\n\n" + "".join(sents) + "\n")
    chunks = doc.chunks
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.answer) <= CHUNK_MAX_CHARS + OVERLAP_MAX_CHARS
        assert c.questions == "长节"
        # 不留半截话：每块从句子开头开始，以句号结束。
        assert c.answer.startswith("第") and c.answer.endswith("。")
    for prev, cur in zip(chunks, chunks[1:]):
        # 有重叠：后一块的第一句出现在前一块中。
        first = cur.answer[: cur.answer.index("。") + 1]
        assert first in prev.answer
    # 不丢内容：每一句至少出现在一个块中。
    assert all(any(s in c.answer for c in chunks) for s in sents)


def test_no_overlap_when_tail_has_no_sentence_end():
    body = "，".join(["没有句号的分句内容"] * 60)  # 只有逗号，没有句末标点
    doc = parse_markdown(f"# 手册\n\n## 长节\n\n{body}\n")
    chunks = doc.chunks
    assert len(chunks) >= 2
    joined = "".join(c.answer for c in chunks)
    assert len(joined) == len(body)  # 没有重叠，内容不重复也不丢失


def test_no_overlap_across_sections():
    long = SENT * 20
    doc = parse_markdown(f"# 手册\n\n## 甲\n\n{long}\n\n## 乙\n\n短内容。\n")
    assert doc.chunks[-1].answer == "短内容。"


def _table(rows: int) -> str:
    lines = ["| 品类 | 退货期限 | 说明 |", "|---|---|---|"]
    lines += [f"| 品类{i:02d} | {i} 天 | 这一行是第 {i} 行的说明文字，用来凑长度。 |" for i in range(rows)]
    return "\n".join(lines)


def test_small_table_stays_whole():
    table = _table(3)
    doc = parse_markdown(f"# 政策\n\n## 期限表\n\n{table}\n")
    assert doc.chunks[0].answer == table


def test_large_table_split_by_rows_with_header_copied():
    table = _table(20)
    doc = parse_markdown(f"# 政策\n\n## 期限表\n\n说明文字。\n\n{table}\n")
    table_chunks = [c for c in doc.chunks if c.answer.startswith("| 品类 |")]
    assert len(table_chunks) >= 2
    data_rows = []
    for c in table_chunks:
        lines = c.answer.split("\n")
        assert lines[0] == "| 品类 | 退货期限 | 说明 |"
        assert lines[1] == "|---|---|---|"
        assert len(c.answer) <= CHUNK_MAX_CHARS
        data_rows += lines[2:]
    assert data_rows == table.split("\n")[2:]  # 不丢行、不重复、顺序不变


def test_table_only_section_and_oversized_row():
    big_row = "| 超长 | 1 天 | " + "很长的说明" * 100 + " |"
    table = "\n".join(["| 品类 | 退货期限 | 说明 |", "|---|---|---|", "| 甲 | 7 天 | 短 |", big_row, "| 乙 | 15 天 | 短 |"])
    doc = parse_markdown(f"# 政策\n\n## 期限表\n\n{table}\n")
    rows = []
    for c in doc.chunks:
        lines = c.answer.split("\n")
        assert lines[:2] == ["| 品类 | 退货期限 | 说明 |", "|---|---|---|"]
        rows += lines[2:]
    assert rows == table.split("\n")[2:]


def test_key_clause_flag_and_mark_removed():
    doc = parse_markdown(f"# 政策\n\n## 甲\n\n{KEY_CLAUSE_MARK}签收前不能退货。\n\n## 乙\n\n普通内容。\n")
    assert doc.chunks[0].is_key_clause is True
    assert KEY_CLAUSE_MARK not in doc.chunks[0].answer
    assert doc.chunks[0].answer == "签收前不能退货。"
    assert doc.chunks[1].is_key_clause is False


def test_key_clause_not_propagated_by_overlap():
    body = SENT * 14 + f"{KEY_CLAUSE_MARK}关键句。" + SENT * 14
    doc = parse_markdown(f"# 手册\n\n## 长节\n\n{body}\n")
    flags = [c.is_key_clause for c in doc.chunks]
    assert flags.count(True) == 1
    assert all(KEY_CLAUSE_MARK not in c.answer for c in doc.chunks)


def test_knowledge_text_format():
    assert knowledge_text("运费", "运费怎么算？", "满 99 元免运费。") == "运费\n运费怎么算？\n满 99 元免运费。"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_chunking.py -q`
Expected: FAIL（`ModuleNotFoundError: app.knowledge.chunking`）

- [ ] **Step 3: 实现** `app/knowledge/chunking.py`

```python
import re
from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.config import CHUNK_MAX_CHARS, OVERLAP_MAX_CHARS

PATH_SEP = " > "
KEY_CLAUSE_MARK = "【关键条款】"
_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
_SENTENCE_ENDS = "。！？"
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", ""]
_TABLE_DIVIDER = re.compile(r"^\|?\s*:?-{3,}")


@dataclass(frozen=True)
class Chunk:
    category: str
    questions: str
    answer: str
    section_path: str
    is_key_clause: bool


@dataclass(frozen=True)
class ParsedDoc:
    title: str
    chunks: list[Chunk]


def knowledge_text(category: str, questions: str, answer: str) -> str:
    """向量化文本。全项目只用这一个函数拼接。"""
    return f"{category}\n{questions}\n{answer}"


def parse_markdown(markdown: str) -> ParsedDoc:
    """按标题层级切分一份 Markdown 文档。文档必须有且只有一个一级标题。"""
    sections = MarkdownHeaderTextSplitter(_HEADERS, strip_headers=True).split_text(markdown)
    if not sections or "h1" not in sections[0].metadata:
        raise ValueError("文档必须以一级标题开头")
    title = sections[0].metadata["h1"]
    chunks: list[Chunk] = []
    for section in sections:
        headers = [section.metadata[k] for k in ("h1", "h2", "h3") if k in section.metadata]
        if not headers or headers[0] != title:
            raise ValueError("一份文档只能有一个一级标题")
        # 切分器用两个空格加换行连接段落，还原为普通换行。
        body = section.page_content.replace("  \n", "\n").strip()
        if not body:
            continue
        category = PATH_SEP.join(headers[:-1]) or title
        section_path = PATH_SEP.join(headers)
        for text, is_key in _split_body(body):
            answer = text.replace(KEY_CLAUSE_MARK, "").strip()
            if answer:
                chunks.append(Chunk(category, headers[-1], answer, section_path, is_key))
    return ParsedDoc(title, chunks)


def _split_body(body: str) -> list[tuple[str, bool]]:
    """返回 (块文本, 是否关键条款)。块文本可能仍含标记，由调用方删除。"""
    if len(body) <= CHUNK_MAX_CHARS:
        return [(body, KEY_CLAUSE_MARK in body)]
    result: list[tuple[str, bool]] = []
    for kind, block in _blocks(body):
        if kind == "table":
            result += [(t, KEY_CLAUSE_MARK in t) for t in _split_table(block)]
        else:
            result += _with_overlap(_split_text(block))
    return result


def _blocks(body: str) -> list[tuple[str, str]]:
    """把正文拆成按顺序排列的 ("text" | "table", 内容) 块。"""
    lines = body.split("\n")
    blocks: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(lines):
        is_table_start = (
            lines[i].startswith("|") and i + 1 < len(lines) and _TABLE_DIVIDER.match(lines[i + 1])
        )
        if is_table_start:
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            blocks.append(("table", lines[i:j]))
            i = j
        else:
            if not blocks or blocks[-1][0] != "text":
                blocks.append(("text", []))
            blocks[-1][1].append(lines[i])
            i += 1
    return [(kind, "\n".join(ls).strip()) for kind, ls in blocks if "\n".join(ls).strip()]


def _split_table(table: str) -> list[str]:
    if len(table) <= CHUNK_MAX_CHARS:
        return [table]
    lines = table.split("\n")
    header, rows = lines[:2], lines[2:]
    pieces: list[str] = []
    current = list(header)
    for row in rows:
        if len(current) > 2 and len("\n".join(current + [row])) > CHUNK_MAX_CHARS:
            pieces.append("\n".join(current))
            current = list(header)
        current.append(row)
    if len(current) > 2:
        pieces.append("\n".join(current))
    return pieces


def _split_text(text: str) -> list[str]:
    if len(text) <= CHUNK_MAX_CHARS:
        return [text]
    splitter = RecursiveCharacterTextSplitter(
        separators=_SEPARATORS,
        keep_separator="end",
        chunk_size=CHUNK_MAX_CHARS,
        chunk_overlap=0,
    )
    return splitter.split_text(text)


def _overlap_prefix(prev: str) -> str:
    """取前一块末尾最多 OVERLAP_MAX_CHARS 字，从其中第一个句末标点之后开始。没有句末标点时返回空串。"""
    tail = prev[-OVERLAP_MAX_CHARS:]
    for idx, ch in enumerate(tail):
        if ch in _SENTENCE_ENDS:
            return tail[idx + 1:].strip()
    return ""


def _with_overlap(pieces: list[str]) -> list[tuple[str, bool]]:
    result = []
    for i, piece in enumerate(pieces):
        prefix = _overlap_prefix(pieces[i - 1]) if i > 0 else ""
        text = f"{prefix}{piece}" if prefix else piece
        result.append((text, KEY_CLAUSE_MARK in piece))
    return result
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_chunking.py -q`
Expected: 全部 PASS。如果某个测试因 `RecursiveCharacterTextSplitter` 的合并行为失败，先打印实际块内容核对规则，再改实现；不许为了通过而放宽断言。

- [ ] **Step 5: Commit**

```bash
git add app/knowledge/chunking.py tests/test_chunking.py
git commit -m "feat(ch03): structure-aware Markdown chunking with sentence-aligned overlap"
```

---

### Task 4: 知识块仓储与文档入库

**Files:**
- Create: `app/repositories/knowledge.py`、`app/knowledge/ingest.py`、`tests/test_ingest.py`

**Interfaces:**
- Consumes: `KnowledgeChunk`、`Faq`；`Chunk`、`ParsedDoc`、`parse_markdown`、`PATH_SEP`；`delete_vectors`、`upsert_vectors`、`count_vectors`。
- Produces（`app/repositories/knowledge.py`，只负责 SQL，调用方提交）：
  - `NewChunk(category, questions, answer, section_path, content_type, is_key_clause)`（dataclass）
  - `async has_source(s, root: str) -> bool`
  - `async ids_for_source(s, root: str) -> list[int]`
  - `async insert_chunks(s, chunks: list[NewChunk]) -> list[KnowledgeChunk]`（flush 后带 id，顺序与输入一致）
  - `async link_chain(s, ids: list[int]) -> None`
  - `async delete_ids(s, ids: list[int]) -> None`（先清指针，再删除）
  - `async next_pending(s, after_id: int, limit: int) -> list[KnowledgeChunk]`
  - `async mark_done(s, ids: list[int]) -> None`
  - `async get_done_by_ids(s, ids: list[int]) -> dict[int, KnowledgeChunk]`
  - `async status_counts(s) -> list[tuple[str | None, str, int]]`（content_type, status, 行数）
- Produces（`app/knowledge/ingest.py`）：
  - `DOCS_DIR: Path`（`<项目根>/knowledge/docs`）、`FAQ_SOURCE = "常见问答"`、`CONTENT_TYPES = ("policy", "faq", "manual")`
  - `SourceDoc(title: str, content_type: str, chunks: list[Chunk], linked: bool)`
  - `load_doc_sources(docs_dir: Path = DOCS_DIR) -> list[SourceDoc]`
  - `async load_faq_source() -> SourceDoc`
  - `async ingest_source(src: SourceDoc) -> int`（跳过时返回 0）
  - `async rebuild_source(title: str) -> int`（返回删除的行数）
  - `async ingest_all(docs_dir: Path = DOCS_DIR, *, rebuild: bool = False) -> dict[str, int]`

- [ ] **Step 1: 写失败的测试** `tests/test_ingest.py`

```python
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import KnowledgeChunk
from app.knowledge import ingest
from app.knowledge import milvus as m
from tests.fakes import unit

pytestmark = pytest.mark.anyio

DOC = "# 退货政策\n\n总则。\n\n## 退货条件\n\n【关键条款】签收后七天内可退。\n\n## 退款\n\n原路退回。\n"


def _write_docs(tmp_path: Path, content_type: str = "policy", name: str = "退货政策.md", text: str = DOC) -> Path:
    d = tmp_path / content_type
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return tmp_path


async def _rows(db):
    async with db() as s:
        return list((await s.execute(select(KnowledgeChunk).order_by(KnowledgeChunk.id))).scalars())


def test_load_doc_sources_uses_folder_as_content_type(tmp_path):
    srcs = ingest.load_doc_sources(_write_docs(tmp_path))
    assert [(s.title, s.content_type, s.linked, len(s.chunks)) for s in srcs] == [("退货政策", "policy", True, 3)]


def test_load_doc_sources_rejects_unknown_folder(tmp_path):
    with pytest.raises(ValueError):
        ingest.load_doc_sources(_write_docs(tmp_path, content_type="blog"))


async def test_ingest_source_writes_pending_chunks_with_chain(db, tmp_path):
    [src] = ingest.load_doc_sources(_write_docs(tmp_path))
    assert await ingest.ingest_source(src) == 3
    rows = await _rows(db)
    assert [r.vectorize_status for r in rows] == ["pending"] * 3
    assert [r.content_type for r in rows] == ["policy"] * 3
    assert [r.is_key_clause for r in rows] == [False, True, False]
    assert rows[1].section_path == "退货政策 > 退货条件"
    assert [r.prev_chunk_id for r in rows] == [None, rows[0].id, rows[1].id]
    assert [r.next_chunk_id for r in rows] == [rows[1].id, rows[2].id, None]


async def test_ingest_source_skips_existing_document(db, tmp_path):
    [src] = ingest.load_doc_sources(_write_docs(tmp_path))
    await ingest.ingest_source(src)
    assert await ingest.ingest_source(src) == 0
    assert len(await _rows(db)) == 3


async def test_source_match_does_not_hit_prefix_titles(db, tmp_path):
    # 「退货政策补充」不能被当成「退货政策」的一部分。
    _write_docs(tmp_path, name="补充.md", text="# 退货政策补充\n\n补充内容。\n")
    srcs = {s.title: s for s in ingest.load_doc_sources(tmp_path)}
    await ingest.ingest_source(srcs["退货政策补充"])
    [src] = ingest.load_doc_sources(_write_docs(tmp_path / "x"))
    assert await ingest.ingest_source(src) == 3


async def test_faq_source_from_table(db):
    src = await ingest.load_faq_source()
    assert src.title == "常见问答" and src.linked is False
    await ingest.ingest_source(src)
    rows = await _rows(db)
    assert len(rows) == 12
    freight = next(r for r in rows if r.questions == "运费怎么算？")
    assert freight.category == "运费"
    assert freight.section_path == "常见问答 > 运费"
    assert freight.content_type == "faq"
    assert freight.prev_chunk_id is None and freight.next_chunk_id is None


async def test_rebuild_source_deletes_milvus_and_mysql(db, milvus, tmp_path):
    [src] = ingest.load_doc_sources(_write_docs(tmp_path))
    await ingest.ingest_source(src)
    ids = [r.id for r in await _rows(db)]
    await m.upsert_vectors([(i, unit(n)) for n, i in enumerate(ids)])
    assert await ingest.rebuild_source("退货政策") == 3
    assert await _rows(db) == []
    assert await m.count_vectors() == 0


async def test_ingest_all_rebuild_keeps_mined_chunks(db, milvus, tmp_path):
    from app.repositories import knowledge

    async with db() as s:
        await knowledge.insert_chunks(s, [knowledge.NewChunk("其他", "能开专票吗", "可以。", "对话挖掘 > 其他", "mined", False)])
        await s.commit()
    docs = _write_docs(tmp_path)
    first = await ingest.ingest_all(docs)
    assert first == {"退货政策": 3, "常见问答": 12}
    assert await ingest.ingest_all(docs) == {"退货政策": 0, "常见问答": 0}
    assert await ingest.ingest_all(docs, rebuild=True) == {"退货政策": 3, "常见问答": 12}
    rows = await _rows(db)
    assert sum(r.content_type == "mined" for r in rows) == 1
    assert len(rows) == 16


def test_duplicate_titles_rejected(tmp_path):
    _write_docs(tmp_path, content_type="policy", name="a.md")
    _write_docs(tmp_path, content_type="manual", name="b.md")
    with pytest.raises(ValueError):
        ingest.load_doc_sources(tmp_path)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现** `app/repositories/knowledge.py`

```python
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeChunk

_SEP = " > "


@dataclass(frozen=True)
class NewChunk:
    category: str
    questions: str
    answer: str
    section_path: str
    content_type: str
    is_key_clause: bool


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _source_filter(root: str):
    return or_(
        KnowledgeChunk.section_path == root,
        KnowledgeChunk.section_path.like(f"{_escape_like(root + _SEP)}%", escape="\\"),
    )


async def has_source(s: AsyncSession, root: str) -> bool:
    return (await s.scalar(select(KnowledgeChunk.id).where(_source_filter(root)).limit(1))) is not None


async def ids_for_source(s: AsyncSession, root: str) -> list[int]:
    return list(await s.scalars(select(KnowledgeChunk.id).where(_source_filter(root)).order_by(KnowledgeChunk.id)))


async def insert_chunks(s: AsyncSession, chunks: list[NewChunk]) -> list[KnowledgeChunk]:
    rows = [KnowledgeChunk(**vars(c)) for c in chunks]
    for row in rows:
        s.add(row)
        await s.flush()  # 逐行 flush，保证 id 按文档顺序递增
    return rows


async def link_chain(s: AsyncSession, ids: list[int]) -> None:
    for i, cid in enumerate(ids):
        await s.execute(update(KnowledgeChunk).where(KnowledgeChunk.id == cid).values(
            prev_chunk_id=ids[i - 1] if i > 0 else None,
            next_chunk_id=ids[i + 1] if i + 1 < len(ids) else None,
        ))


async def delete_ids(s: AsyncSession, ids: list[int]) -> None:
    if not ids:
        return
    # 自引用外键：先清指针，再删除。
    await s.execute(update(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)).values(prev_chunk_id=None, next_chunk_id=None))
    await s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))


async def next_pending(s: AsyncSession, after_id: int, limit: int) -> list[KnowledgeChunk]:
    return list(await s.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.vectorize_status == "pending", KnowledgeChunk.id > after_id)
        .order_by(KnowledgeChunk.id).limit(limit)
    ))


async def mark_done(s: AsyncSession, ids: list[int]) -> None:
    for cid in ids:
        await s.execute(update(KnowledgeChunk).where(KnowledgeChunk.id == cid).values(
            vector_id=str(cid), vectorize_status="done"
        ))


async def get_done_by_ids(s: AsyncSession, ids: list[int]) -> dict[int, KnowledgeChunk]:
    if not ids:
        return {}
    rows = await s.scalars(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids), KnowledgeChunk.vectorize_status == "done"))
    return {r.id: r for r in rows}


async def status_counts(s: AsyncSession) -> list[tuple[str | None, str, int]]:
    rows = await s.execute(
        select(KnowledgeChunk.content_type, KnowledgeChunk.vectorize_status, func.count())
        .group_by(KnowledgeChunk.content_type, KnowledgeChunk.vectorize_status)
        .order_by(KnowledgeChunk.content_type, KnowledgeChunk.vectorize_status)
    )
    return [(ct, st, int(n)) for ct, st, n in rows]
```

`app/knowledge/ingest.py`：

```python
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import Faq
from app.knowledge.chunking import PATH_SEP, Chunk, parse_markdown
from app.knowledge.milvus import delete_vectors
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "docs"
FAQ_SOURCE = "常见问答"
CONTENT_TYPES = ("policy", "faq", "manual")


@dataclass(frozen=True)
class SourceDoc:
    title: str
    content_type: str
    chunks: list[Chunk]
    linked: bool


def load_doc_sources(docs_dir: Path = DOCS_DIR) -> list[SourceDoc]:
    """读取 docs_dir/<content_type>/*.md。子目录名必须是 policy、faq、manual 之一，文档名全库唯一。"""
    sources = []
    for path in sorted(docs_dir.glob("*/*.md")):
        content_type = path.parent.name
        if content_type not in CONTENT_TYPES:
            raise ValueError(f"未知的内容类型目录：{content_type}")
        doc = parse_markdown(path.read_text(encoding="utf-8"))
        sources.append(SourceDoc(doc.title, content_type, doc.chunks, linked=True))
    titles = [s.title for s in sources] + [FAQ_SOURCE]
    if len(titles) != len(set(titles)):
        raise ValueError("文档名重复")
    return sources


async def load_faq_source() -> SourceDoc:
    async with get_sessionmaker()() as s:
        rows = list(await s.scalars(select(Faq).order_by(Faq.id)))
    chunks = [
        Chunk(category=r.category, questions=r.question, answer=r.answer,
              section_path=f"{FAQ_SOURCE}{PATH_SEP}{r.category}", is_key_clause=False)
        for r in rows
    ]
    return SourceDoc(FAQ_SOURCE, "faq", chunks, linked=False)


async def ingest_source(src: SourceDoc) -> int:
    """一份文档在一个事务中写入。已入库时跳过，返回 0。"""
    async with get_sessionmaker()() as s:
        if await knowledge.has_source(s, src.title):
            return 0
        rows = await knowledge.insert_chunks(s, [
            NewChunk(c.category, c.questions, c.answer, c.section_path, src.content_type, c.is_key_clause)
            for c in src.chunks
        ])
        if src.linked:
            await knowledge.link_chain(s, [r.id for r in rows])
        await s.commit()
        return len(rows)


async def rebuild_source(title: str) -> int:
    """先删 Milvus 向量，再删 MySQL 块。中途中断后重跑，结果仍然正确。"""
    async with get_sessionmaker()() as s:
        ids = await knowledge.ids_for_source(s, title)
    await delete_vectors(ids)
    async with get_sessionmaker()() as s:
        await knowledge.delete_ids(s, ids)
        await s.commit()
    return len(ids)


async def ingest_all(docs_dir: Path = DOCS_DIR, *, rebuild: bool = False) -> dict[str, int]:
    sources = load_doc_sources(docs_dir) + [await load_faq_source()]
    if rebuild:
        for src in sources:
            await rebuild_source(src.title)
    return {src.title: await ingest_source(src) for src in sources}
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_ingest.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/repositories/knowledge.py app/knowledge/ingest.py tests/test_ingest.py
git commit -m "feat(ch03): knowledge chunk repository and per-document idempotent ingest"
```

---

### Task 5: 双写向量化与建库脚本

**Files:**
- Create: `app/knowledge/vectorize.py`、`scripts/build_kb.py`、`tests/test_vectorize.py`

**Interfaces:**
- Consumes: `knowledge.next_pending`、`knowledge.mark_done`、`knowledge.status_counts`；`upsert_vectors`、`count_vectors`、`ensure_collection`、`close_milvus`；`get_embeddings`；`knowledge_text`；`ingest_all`；`dispose_engine`。
- Produces:
  - `class SimulatedCrash(Exception)`
  - `async vectorize_pending(*, batch_size: int = VECTORIZE_BATCH_SIZE, crash_after_batches: int | None = None) -> int`
  - `KbStatus(groups: list[tuple[str | None, str, int]], total: int, pending: int, done: int, milvus: int)`
  - `async kb_status() -> KbStatus`、`format_status(st: KbStatus) -> str`
  - `scripts/build_kb.py`：参数 `--rebuild`、`--crash-after-batches N`、`--status`、`--check`

- [ ] **Step 1: 写失败的测试** `tests/test_vectorize.py`

```python
import pytest
from sqlalchemy import select

from app.db.models import KnowledgeChunk
from app.knowledge import milvus as m
from app.knowledge.embeddings import get_embeddings
from app.knowledge.vectorize import SimulatedCrash, kb_status, vectorize_pending
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk

pytestmark = pytest.mark.anyio


async def _seed(db, n: int) -> list[int]:
    async with db() as s:
        rows = await knowledge.insert_chunks(s, [
            NewChunk(f"分类{i}", f"问题{i}", f"答案{i}。", f"文档 > 分类{i}", "policy", False) for i in range(n)
        ])
        await s.commit()
        return [r.id for r in rows]


async def _statuses(db):
    async with db() as s:
        return list((await s.execute(select(KnowledgeChunk.vectorize_status, KnowledgeChunk.vector_id).order_by(KnowledgeChunk.id))).all())


async def test_vectorize_all_pending(db, milvus):
    ids = await _seed(db, 5)
    assert await vectorize_pending(batch_size=2) == 5
    assert await _statuses(db) == [("done", str(i)) for i in ids]
    assert await m.count_vectors() == 5
    assert get_embeddings().calls[0] == ["分类0\n问题0\n答案0。", "分类1\n问题1\n答案1。"]


async def test_vectorize_skips_done_rows(db, milvus):
    await _seed(db, 3)
    await vectorize_pending()
    get_embeddings().calls.clear()
    assert await vectorize_pending() == 0
    assert get_embeddings().calls == []


async def test_crash_between_milvus_and_mysql_then_resume(db, milvus):
    await _seed(db, 7)
    with pytest.raises(SimulatedCrash):
        await vectorize_pending(batch_size=2, crash_after_batches=2)
    st = await kb_status()
    assert (st.done, st.pending) == (4, 3)
    assert st.milvus == 6  # 第 3 批已写入 Milvus，MySQL 未回填
    assert await vectorize_pending(batch_size=2) == 3
    st = await kb_status()
    assert (st.done, st.pending, st.total, st.milvus) == (7, 0, 7, 7)


async def test_embedding_failure_leaves_rows_pending(db, milvus):
    await _seed(db, 3)

    class Boom(type(get_embeddings())):
        async def aembed_documents(self, texts):
            raise RuntimeError("上游失败")

    from app.knowledge.embeddings import set_embeddings
    set_embeddings(Boom())
    with pytest.raises(RuntimeError):
        await vectorize_pending()
    st = await kb_status()
    assert (st.pending, st.milvus) == (3, 0)


async def test_status_groups(db, milvus):
    await _seed(db, 2)
    st = await kb_status()
    assert st.groups == [("policy", "pending", 2)]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_vectorize.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现** `app/knowledge/vectorize.py`

```python
import logging
from dataclasses import dataclass

from app.config import VECTORIZE_BATCH_SIZE
from app.db.engine import get_sessionmaker
from app.knowledge.chunking import knowledge_text
from app.knowledge.embeddings import get_embeddings
from app.knowledge.milvus import count_vectors, upsert_vectors
from app.repositories import knowledge

logger = logging.getLogger(__name__)


class SimulatedCrash(Exception):
    """故障注入：Milvus 已写入、MySQL 未回填时中断。只用于演示和测试。"""


async def vectorize_pending(
    *, batch_size: int = VECTORIZE_BATCH_SIZE, crash_after_batches: int | None = None
) -> int:
    """把 pending 行嵌入后 upsert 到 Milvus，再回填 MySQL。返回本次处理的行数。

    Milvus 主键等于 MySQL 主键，所以中断后重跑会覆盖已写入的向量，不产生重复。
    """
    sm = get_sessionmaker()
    embeddings = get_embeddings()
    after_id = processed = batches = 0
    while True:
        async with sm() as s:
            rows = await knowledge.next_pending(s, after_id, batch_size)
        if not rows:
            return processed
        vectors = await embeddings.aembed_documents(
            [knowledge_text(r.category, r.questions, r.answer) for r in rows]
        )
        await upsert_vectors([(r.id, v) for r, v in zip(rows, vectors)])
        if crash_after_batches is not None and batches == crash_after_batches:
            raise SimulatedCrash(f"第 {batches + 1} 批已写入 Milvus，未回填 MySQL")
        async with sm() as s:
            await knowledge.mark_done(s, [r.id for r in rows])
            await s.commit()
        processed += len(rows)
        batches += 1
        after_id = rows[-1].id
        logger.info("已向量化 %d 行", processed)


@dataclass(frozen=True)
class KbStatus:
    groups: list[tuple[str | None, str, int]]
    total: int
    pending: int
    done: int
    milvus: int


async def kb_status() -> KbStatus:
    async with get_sessionmaker()() as s:
        groups = await knowledge.status_counts(s)
    pending = sum(n for _, st, n in groups if st == "pending")
    done = sum(n for _, st, n in groups if st == "done")
    return KbStatus(groups, pending + done, pending, done, await count_vectors())


def format_status(st: KbStatus) -> str:
    lines = ["知识库状态："]
    lines += [f"  {ct or '-'} / {status}: {n}" for ct, status, n in st.groups]
    lines.append(f"  MySQL 合计 {st.total}（pending {st.pending}，done {st.done}）；Milvus 实体数 {st.milvus}")
    return "\n".join(lines)
```

`scripts/build_kb.py`：

```python
"""离线建库：文档切分和 faq 表入库（MySQL pending），再向量化写入 Milvus。"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.engine import dispose_engine
from app.knowledge.ingest import ingest_all
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.vectorize import SimulatedCrash, format_status, kb_status, vectorize_pending

logger = logging.getLogger(__name__)


async def run(args) -> int:
    try:
        await ensure_collection()
        if args.status or args.check:
            st = await kb_status()
            print(format_status(st))
            if args.check and (st.pending != 0 or st.milvus != st.total):
                print("检查失败：存在 pending 行，或 Milvus 实体数不等于 MySQL 行数")
                return 1
            return 0
        for title, n in (await ingest_all(rebuild=args.rebuild)).items():
            print(f"入库 {title}：{n} 块" if n else f"跳过 {title}：已入库")
        try:
            n = await vectorize_pending(crash_after_batches=args.crash_after_batches)
        except SimulatedCrash as exc:
            print(f"模拟中断：{exc}")
            print(format_status(await kb_status()))
            return 1
        print(f"本次向量化 {n} 块")
        print(format_status(await kb_status()))
        return 0
    finally:
        await close_milvus()
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="离线建库")
    parser.add_argument("--rebuild", action="store_true", help="重建全部文档来源（不含 mined）")
    parser.add_argument("--crash-after-batches", type=int, default=None, help="故障注入，只用于演示")
    parser.add_argument("--status", action="store_true", help="只打印状态")
    parser.add_argument("--check", action="store_true", help="打印状态；有 pending 行或 Milvus 数量不一致时退出码为 1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args))
    except Exception:
        logger.exception("建库失败")
        print("建库失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_vectorize.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/knowledge/vectorize.py scripts/build_kb.py tests/test_vectorize.py
git commit -m "feat(ch03): idempotent MySQL-to-Milvus vectorization and build_kb script"
```

---

### Task 6: 在线检索替换 `query_faq`

**Files:**
- Create: `app/knowledge/retrieval.py`、`tests/test_retrieval.py`
- Modify: `app/tools/faq.py`、`tests/test_tools.py`、`tests/test_repositories.py`
- Delete: `app/repositories/faq.py`

**Interfaces:**
- Consumes: `get_embeddings`、`search_vectors`、`knowledge.get_done_by_ids`。
- Produces:
  - `async search_by_vector(vector: list[float], *, limit: int, min_score: float) -> list[tuple[KnowledgeChunk, float]]`
  - `async search_with_scores(keyword: str, *, limit: int = FAQ_MAX_RESULTS, min_score: float = FAQ_MIN_SCORE) -> list[tuple[KnowledgeChunk, float]]`
  - `async search_faq(keyword: str) -> list[dict]`（元素为 `{"question", "answer", "category"}`）

- [ ] **Step 1: 写失败的测试** `tests/test_retrieval.py`

```python
import pytest

from app.knowledge import milvus as m
from app.knowledge.embeddings import set_embeddings
from app.knowledge.retrieval import search_faq, search_with_scores
from app.knowledge.vectorize import vectorize_pending
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk
from app.tools.registry import get_registry
from tests.fakes import FakeEmbeddings, blend, unit

pytestmark = pytest.mark.anyio

FREIGHT = NewChunk("运费", "运费怎么算？", "满 99 元免运费，不满收 8 元。", "常见问答 > 运费", "faq", False)
RETURN_FREIGHT = NewChunk("运费", "退货的运费谁承担？", "质量问题由商家承担。", "常见问答 > 运费", "faq", False)
INVOICE = NewChunk("发票", "怎么开发票？", "在订单详情页申请。", "常见问答 > 发票", "faq", False)


def _fake():
    return FakeEmbeddings([
        ("邮费", unit(0)),
        ("运费怎么算", unit(0)),            # 余弦 1.0
        ("退货的运费", blend(0, 1, 0.7)),   # 余弦 0.7
        ("发票", blend(0, 2, 0.3)),         # 余弦 0.3，低于阈值
    ])


async def _seed(db, chunks):
    async with db() as s:
        rows = await knowledge.insert_chunks(s, chunks)
        await s.commit()
        return [r.id for r in rows]


async def test_search_orders_by_score_and_filters_threshold(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [INVOICE, RETURN_FREIGHT, FREIGHT])
    await vectorize_pending()
    hits = await search_with_scores("邮费")
    assert [(r.questions, round(sc, 2)) for r, sc in hits] == [("运费怎么算？", 1.0), ("退货的运费谁承担？", 0.7)]


async def test_search_faq_output_shape(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [FREIGHT])
    await vectorize_pending()
    assert await search_faq("邮费") == [
        {"question": "运费怎么算？", "answer": "满 99 元免运费，不满收 8 元。", "category": "运费"}
    ]


async def test_search_ignores_pending_rows(db, milvus):
    set_embeddings(_fake())
    [cid] = await _seed(db, [FREIGHT])
    await m.upsert_vectors([(cid, unit(0))])  # Milvus 有向量，MySQL 仍为 pending
    assert await search_faq("邮费") == []


async def test_search_skips_ids_missing_in_mysql(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [FREIGHT])
    await vectorize_pending()
    await m.upsert_vectors([(999999, unit(0))])
    assert [r["question"] for r in await search_faq("邮费")] == ["运费怎么算？"]


async def test_query_faq_tool_uses_vector_search(db, milvus):
    set_embeddings(_fake())
    await _seed(db, [FREIGHT])
    await vectorize_pending()
    tool = get_registry().get("query_faq").tool
    result = await tool.ainvoke({"keyword": "邮费"})
    assert result == {"results": [{"question": "运费怎么算？", "answer": "满 99 元免运费，不满收 8 元。", "category": "运费"}]}


async def test_query_faq_contract_unchanged():
    from langchain_core.utils.function_calling import convert_to_openai_tool

    schema = convert_to_openai_tool(get_registry().get("query_faq").tool)
    assert schema["function"]["description"] == "按关键词查询常见问题，例如退货政策、运费、发票、账户、支付。"
    assert schema["function"]["parameters"]["properties"]["keyword"]["maxLength"] == 20
```

- [ ] **Step 2: 修改旧测试**

- `tests/test_repositories.py`：删除 `test_faq_search_hits_and_misses`、`test_faq_search_escapes_wildcards`，`import` 中去掉 `faq`。
- `tests/test_tools.py`：删除 `test_query_faq_tool`（新行为由 `tests/test_retrieval.py::test_query_faq_tool_uses_vector_search` 覆盖）。
- `tests/test_chat_api.py` 不改。其中执行 `query_faq` 的测试在 `BlockedMilvus` 下得到 `tool_error`，断言只看事件和写库，不受影响。

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/test_retrieval.py -q`
Expected: FAIL（`ModuleNotFoundError: app.knowledge.retrieval`）

- [ ] **Step 4: 实现** `app/knowledge/retrieval.py`

```python
from app.config import FAQ_MAX_RESULTS, FAQ_MIN_SCORE
from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.embeddings import get_embeddings
from app.knowledge.milvus import search_vectors
from app.repositories import knowledge


async def search_by_vector(
    vector: list[float], *, limit: int, min_score: float
) -> list[tuple[KnowledgeChunk, float]]:
    """Milvus 取 Top-K，丢弃低于阈值的结果，正文从 MySQL 读（只取 done 行），保持相似度顺序。"""
    hits = [(i, sc) for i, sc in await search_vectors(vector, limit) if sc >= min_score]
    if not hits:
        return []
    async with get_sessionmaker()() as s:
        rows = await knowledge.get_done_by_ids(s, [i for i, _ in hits])
    # Milvus 有、MySQL 没有（或仍为 pending）的 id 跳过。
    return [(rows[i], sc) for i, sc in hits if i in rows]


async def search_with_scores(
    keyword: str, *, limit: int = FAQ_MAX_RESULTS, min_score: float = FAQ_MIN_SCORE
) -> list[tuple[KnowledgeChunk, float]]:
    vector = await get_embeddings().aembed_query(keyword)
    return await search_by_vector(vector, limit=limit, min_score=min_score)


async def search_faq(keyword: str) -> list[dict]:
    return [
        {"question": r.questions, "answer": r.answer, "category": r.category}
        for r, _ in await search_with_scores(keyword)
    ]
```

`app/tools/faq.py`：`QueryFaqArgs`、`@tool` 装饰器参数、docstring 保持原样，只改函数体：

```python
from app.knowledge.retrieval import search_faq


@tool("query_faq", args_schema=QueryFaqArgs)
async def query_faq(keyword: str) -> dict:
    """按关键词查询常见问题，例如退货政策、运费、发票、账户、支付。"""
    return {"results": await search_faq(keyword)}
```

  删除 `app/repositories/faq.py`，并删除 `app/tools/faq.py` 中不再使用的 import（`FAQ_MAX_RESULTS`、`get_sessionmaker`、`faq`）。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add -A app/knowledge/retrieval.py app/tools/faq.py app/repositories/faq.py tests/test_retrieval.py tests/test_tools.py tests/test_repositories.py
git commit -m "feat(ch03): query_faq uses dense vector retrieval; drop LIKE search"
```

---

### Task 7: 知识文档、真实建库与检索评估（数据类，代替 TDD）

**Files:**
- Create（Claude 执行）：`knowledge/docs/policy/退货政策.md`、`knowledge/docs/faq/商品FAQ.md`、`knowledge/docs/manual/售后手册.md`、`evals/retrieval_samples.jsonl`
- Create（Codex 执行）：`tests/test_knowledge_docs.py`、`evals/run_retrieval_eval.py`

**Interfaces:**
- Consumes: `load_doc_sources`、`search_with_scores`、`ensure_collection`、`close_milvus`、`dispose_engine`。

- [ ] **Step 1（Claude 执行）：写 3 份文档**

  约束（spec 6.1）：
  - 内容和 `db/seed.sql` 一致：7 天无理由退货、质量问题 15 天、退款 1 到 3 个工作日原路退回、维修 7 个工作日等。
  - 不写运费的具体标准（运费只在 `faq` 表中）。3 份文档全文不出现"邮"字。
  - `退货政策.md`：多级标题；1 张 15 行以上、超过 400 字的"品类退换货规则"表；至少 2 处 `【关键条款】`。
  - `商品FAQ.md`：按商品分 `##`（蓝牙耳机、羊毛衫、扫地机器人、电动牙刷等，和 `app/tools/mock_data.py` 的 `CATALOG` 一致），每个问题是 `###` 标题，用口语问法。
  - `售后手册.md`：维修、换货、投诉、发票换开等流程，至少 1 个超过 400 字的小节。
  - 3 份文档和 `faq` 表合计至少 49 块。

- [ ] **Step 2（用户审核）：** 把 3 份文档交给用户审核，按意见修改。

- [ ] **Step 3: 文档检查测试（Codex）** `tests/test_knowledge_docs.py`

```python
from app.config import CHUNK_MAX_CHARS, OVERLAP_MAX_CHARS
from app.knowledge.ingest import DOCS_DIR, load_doc_sources


def test_docs_have_no_you_char():
    for path in DOCS_DIR.glob("*/*.md"):
        assert "邮" not in path.read_text(encoding="utf-8"), path.name


def test_docs_cover_required_structures():
    sources = load_doc_sources()
    assert sorted(s.content_type for s in sources) == ["faq", "manual", "policy"]
    chunks = [c for s in sources for c in s.chunks]
    assert len(chunks) + 12 >= 49
    assert sum(c.is_key_clause for c in chunks) >= 2
    table_chunks = [c for c in chunks if c.answer.startswith("|")]
    assert len(table_chunks) >= 2  # 大表被按行切成多块
    assert all(len(c.answer) <= CHUNK_MAX_CHARS + OVERLAP_MAX_CHARS for c in chunks)
```

  Run: `uv run pytest tests/test_knowledge_docs.py -q`，Expected: PASS。

- [ ] **Step 4（Claude 执行）：标注样例核对切分结果**

  用下面的命令打印全部块。Claude 选 10 个块（含表格块、递归切块、关键条款块、FAQ 块），先写下期望的 `questions`、`category`、`is_key_clause` 和正文首尾，再与输出核对，结果记入 dev-notes。

```bash
uv run python -c "
from app.knowledge.ingest import load_doc_sources
for s in load_doc_sources():
    for i, c in enumerate(s.chunks):
        print(f'[{s.content_type}] #{i} {c.section_path} | Q={c.questions} | C={c.category} | key={c.is_key_clause} | {len(c.answer)}字')
        print('   ', c.answer[:40].replace(chr(10), '/'), '…', c.answer[-20:].replace(chr(10), '/'))
"
```

- [ ] **Step 5（Claude 执行）：写检索评估集** `evals/retrieval_samples.jsonl`

  约 20 条相关样例 + 约 5 条无关样例。格式：

```json
{"keyword": "邮费", "expected_questions": ["运费怎么算？"]}
{"keyword": "天气怎么样", "expected_questions": []}
```

  `expected_questions` 为空表示期望无结果。相关样例以换说法为主（例如"邮费""快递费""钱什么时候退回来""耳机连不上手机"），每条可列多个可接受的 `questions`。用户审核标注。

- [ ] **Step 6: 评估脚本（Codex）** `evals/run_retrieval_eval.py`

  要求：
  - 结构参考 `evals/run_tool_selection_eval.py`（`sys.path` 处理、`logging`、`main()` 返回退出码）。
  - 先 `await ensure_collection()`。对每条样例调用 `search_with_scores(keyword, limit=3, min_score=0.0)`，打印 Top 3 的 `questions` 和相似度（保留 3 位小数）。
  - 判定（按当前 `FAQ_MIN_SCORE` 过滤后）：相关样例在 Top 3 中出现任一 `expected_questions` 为命中；无关样例过滤后无结果为通过。
  - 输出 Top 3 命中率、无关样例通过率。通过标准：命中率 ≥ 90% 且无关通过率 ≥ 80%，否则退出码 1。
  - 另外打印"阈值扫描"：对 0.40 到 0.65、步长 0.05 的每个阈值，打印两项比率，供校准用。
  - 结束前 `await close_milvus()`、`await dispose_engine()`。

- [ ] **Step 7（Claude 执行）：真实建库并评估**

```bash
docker compose up -d --wait
uv run python scripts/build_kb.py
uv run python evals/run_retrieval_eval.py
```

  Expected: `build_kb.py` 打印 `pending 0`，Milvus 实体数等于 MySQL 合计；评估达标。如果需要调整 `FAQ_MIN_SCORE`，Claude 按阈值扫描结果选值，交给 Codex 修改 `app/config.py` 和 `tests/test_config.py`，再重跑评估。结果记入 dev-notes。

- [ ] **Step 8: Commit**

```bash
git add knowledge/docs evals/retrieval_samples.jsonl evals/run_retrieval_eval.py tests/test_knowledge_docs.py app/config.py tests/test_config.py
git commit -m "feat(ch03): knowledge documents and retrieval eval set"
```

---

### Task 8: 对话挖掘——抽取进暂存表

**Files:**
- Create: `app/repositories/staging.py`、`app/knowledge/mining.py`、`app/knowledge/history_seed.py`、`scripts/seed_history.py`、`tests/test_mining_extract.py`
- Modify: `app/schemas.py`、`app/prompts.py`、`app/llm.py`

**Interfaces:**
- Consumes: `Conversation`、`Message`、`QaExtractionStaging`；`messages.list_for_conversation`；`build_extract_model`。
- Produces:
  - `app/schemas.py`：`QaPair(question: str, answer: str)`、`QaPairs(pairs: list[QaPair])`、`MinedCategory = Literal[MINED_CATEGORIES]`、`DedupVerdict(duplicate_of: int | None, category: MinedCategory)`
  - `app/prompts.py`：`QA_EXTRACT_SYSTEM_PROMPT`、`qa_extract_prompt`（变量 `transcript`）、`DEDUP_JUDGE_SYSTEM_PROMPT`、`dedup_judge_prompt`（变量 `question`、`answer`、`candidates`）
  - `app/llm.py`：`get_qa_extractor() -> Runnable`、`get_dedup_judge() -> Runnable`（都是 `include_raw=True`，输出 `{"raw", "parsed", "parsing_error"}`）
  - `app/repositories/staging.py`：`NewStaging(batch_no, source_ref, question, answer)`、`async unmined_conversation_ids(s, day: date) -> list[int]`、`async max_batch_seq(s, day: date) -> int`、`async add_rows(s, rows: list[NewStaging]) -> None`、`async list_extracted(s) -> list[QaExtractionStaging]`、`async set_status(s, row_id: int, status: str) -> None`、`async status_counts(s) -> dict[str, int]`
  - `app/knowledge/mining.py`：`source_ref(conversation_id: int) -> str`、`format_transcript(rows: list[Message]) -> str | None`、`batch_no(day: date, seq: int) -> str`、`ExtractStats`、`async extract_day(day: date, extractor: Runnable, *, batch_size: int = MINE_BATCH_SIZE, concurrency: int = MINE_CONCURRENCY) -> ExtractStats`
  - `app/knowledge/history_seed.py`：`HISTORY_FILE: Path`、`HISTORY_USER_ID = "history-seed"`、`async seed_history(day: date, path: Path = HISTORY_FILE) -> int`（已有该日期的种子会话时返回 0）

- [ ] **Step 1: 写失败的测试** `tests/test_mining_extract.py`

```python
from datetime import date, datetime
import json

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import Conversation, Message, QaExtractionStaging
from app.knowledge import mining
from app.knowledge.history_seed import seed_history
from app.schemas import QaPair, QaPairs

pytestmark = pytest.mark.anyio
DAY = date(2026, 10, 5)


async def _conv(db, day: date, turns: list[tuple[str, str | None]]) -> int:
    async with db() as s:
        conv = Conversation(user_id="u1", created_at=datetime.combine(day, datetime.min.time()).replace(hour=10))
        s.add(conv)
        await s.flush()
        for role, content in turns:
            s.add(Message(conversation_id=conv.id, role=role, content=content))
        await s.commit()
        return conv.id


def _extractor(answers: dict[str, list[tuple[str, str]] | Exception], seen: list | None = None):
    """按对话中的第一句用户话选择输出。"""
    async def run(inputs):
        first = inputs["transcript"].split("\n")[0]
        if seen is not None:
            seen.append(inputs["transcript"])
        result = answers[first]
        if isinstance(result, Exception):
            raise result
        return {"raw": None, "parsing_error": None,
                "parsed": QaPairs(pairs=[QaPair(question=q, answer=a) for q, a in result])}
    return RunnableLambda(run)


async def _staging(db):
    async with db() as s:
        return list((await s.execute(select(QaExtractionStaging).order_by(QaExtractionStaging.id))).scalars())


def test_format_transcript_excludes_tool_messages():
    rows = [
        Message(role="user", content="能开专票吗"),
        Message(role="assistant", content=None, tool_calls=[{"id": "c1", "name": "query_faq", "args": {}}]),
        Message(role="tool", content='{"ok": true}', tool_call_id="c1"),
        Message(role="assistant", content="可以开专票。"),
    ]
    assert mining.format_transcript(rows) == "用户：能开专票吗\n客服：可以开专票。"


def test_format_transcript_without_reply_is_none():
    assert mining.format_transcript([Message(role="user", content="在吗")]) is None


def test_batch_no_format():
    assert mining.batch_no(DAY, 3) == "20261005-03"


async def test_extract_day_writes_staging_by_batch(db):
    c1 = await _conv(db, DAY, [("user", "能开专票吗"), ("assistant", "可以开专票。")])
    c2 = await _conv(db, DAY, [("user", "你好"), ("assistant", "您好。")])
    c3 = await _conv(db, DAY, [("user", "能改地址吗"), ("assistant", "发货前可以改。")])
    await _conv(db, date(2026, 10, 4), [("user", "能开专票吗"), ("assistant", "可以。")])  # 其他日期
    ex = _extractor({
        "用户：能开专票吗": [("能开专票吗", "可以开专票。")],
        "用户：你好": [],
        "用户：能改地址吗": [("能改地址吗", "发货前可以改。")],
    })
    stats = await mining.extract_day(DAY, ex, batch_size=2)
    rows = await _staging(db)
    assert [(r.batch_no, r.source_ref, r.question, r.status) for r in rows] == [
        ("20261005-01", f"conversation:{c1}", "能开专票吗", "extracted"),
        ("20261005-02", f"conversation:{c3}", "能改地址吗", "extracted"),
    ]
    assert (stats.conversations, stats.pairs, stats.failed) == (3, 2, 0)


async def test_one_conversation_failure_does_not_block_others(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    await _conv(db, DAY, [("user", "乙"), ("assistant", "乙答。")])
    ex = _extractor({"用户：甲": RuntimeError("上游失败"), "用户：乙": [("乙", "乙答。")]})
    stats = await mining.extract_day(DAY, ex)
    assert [r.question for r in await _staging(db)] == ["乙"]
    assert stats.failed == 1


async def test_unparsed_output_counts_as_failure(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    ex = RunnableLambda(lambda _: {"raw": "x", "parsed": None, "parsing_error": "bad"})
    stats = await mining.extract_day(DAY, ex)
    assert stats.failed == 1 and await _staging(db) == []


async def test_extract_day_twice_skips_mined_and_continues_batch_numbers(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    seen: list = []
    ex = _extractor({"用户：甲": [("甲", "甲答。")], "用户：乙": [("乙", "乙答。")]}, seen)
    await mining.extract_day(DAY, ex)
    await _conv(db, DAY, [("user", "乙"), ("assistant", "乙答。")])
    await mining.extract_day(DAY, ex)
    rows = await _staging(db)
    assert [(r.batch_no, r.question) for r in rows] == [("20261005-01", "甲"), ("20261005-02", "乙")]
    assert len(seen) == 2  # 第二次只抽取新会话


async def test_blank_pairs_are_dropped(db):
    await _conv(db, DAY, [("user", "甲"), ("assistant", "甲答。")])
    ex = _extractor({"用户：甲": [("  ", "答"), ("甲", " ")]})
    await mining.extract_day(DAY, ex)
    assert await _staging(db) == []


async def test_seed_history_is_idempotent(db, tmp_path):
    f = tmp_path / "h.jsonl"
    f.write_text("\n".join(json.dumps({"messages": [
        {"role": "user", "content": f"问题{i}"}, {"role": "assistant", "content": f"回答{i}"}
    ]}, ensure_ascii=False) for i in range(3)), encoding="utf-8")
    assert await seed_history(DAY, f) == 3
    assert await seed_history(DAY, f) == 0
    async with db() as s:
        convs = list((await s.execute(select(Conversation).where(Conversation.user_id == "history-seed"))).scalars())
    assert len(convs) == 3
    assert all(c.created_at.date() == DAY for c in convs)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_mining_extract.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 schemas、prompts、llm**

`app/schemas.py` 追加：

```python
from typing import Literal

from app.config import MINED_CATEGORIES


class QaPair(BaseModel):
    question: str = Field(description="用户的真实问法，去掉订单号等个人信息")
    answer: str = Field(description="客服在对话中给出的答案，不补充、不编造")


class QaPairs(BaseModel):
    """从一通历史对话中抽出的可复用问答对。"""

    pairs: list[QaPair] = Field(description="问答对列表；没有可抽的内容时为空列表")


MinedCategory = Literal[MINED_CATEGORIES]


class DedupVerdict(BaseModel):
    """去重裁定结果。"""

    duplicate_of: int | None = Field(description="重复时填候选序号（从 1 开始）；不重复时为 null")
    category: MinedCategory = Field(description="新问答对的分类")
```

`app/prompts.py` 追加（Prompt 原文，不许改写）：

```python
QA_EXTRACT_SYSTEM_PROMPT = """你是客服知识整理员。从一通历史客服对话中抽取可以复用的问答对，用于充实店铺知识库。

## 抽取规则
1. 只抽通用的店铺知识：政策、规则、流程、时限、费用标准。换一个用户来问，答案仍然成立。
2. 不抽个人订单信息：订单号、物流状态和轨迹、具体订单金额、收货地址、手机号。
3. 不抽闲聊、问候，以及与购物和售后无关的内容。
4. 客服回答"查不到""不知道""暂时无法确认"，或只建议转人工的，不抽。
5. question 用用户的真实问法，保留口语。去掉订单号等个人信息后，问题仍要完整可读。
6. answer 只用客服在对话中说过的内容，可以合并同一话题的多句回复。不补充、不编造、不改变数字。
7. 一通对话可以抽出 0 到多个问答对。同一个问题只抽一次。没有可抽的内容时，返回空列表。"""

qa_extract_prompt = ChatPromptTemplate.from_messages([
    ("system", QA_EXTRACT_SYSTEM_PROMPT),
    ("human", "{transcript}"),
])

DEDUP_JUDGE_SYSTEM_PROMPT = """你是知识库去重审核员。判断一条新问答对是否和已有候选重复，并给新问答对选择分类。

## 重复判定
1. 某个候选已经回答了新问答对的问题（问法不同，但用户想知道的是同一件事），判为重复。duplicate_of 填该候选的序号。
2. 以候选为准：新答案和候选答案的说法或数字不同，也判为重复。
3. 新问题比候选更具体，候选没有给出新问题的答案时，不算重复。
4. 没有候选，或没有候选回答了同一个问题时，duplicate_of 为 null。

## 分类
从下列分类中选一个最贴切的：退换货、运费、发票、售后维修、账户、支付、物流、其他。"""

dedup_judge_prompt = ChatPromptTemplate.from_messages([
    ("system", DEDUP_JUDGE_SYSTEM_PROMPT),
    ("human", "新问答对：\n问：{question}\n答：{answer}\n\n候选：\n{candidates}"),
])
```

`app/llm.py` 追加：

```python
from app.prompts import dedup_judge_prompt, qa_extract_prompt
from app.schemas import DedupVerdict, QaPairs


@lru_cache
def get_qa_extractor() -> Runnable:
    # 和 /extract 一样关闭思考：强制 tool_choice 与 DeepSeek 思考模式冲突。
    model = build_extract_model(get_settings())
    return qa_extract_prompt | model.with_structured_output(QaPairs, method="function_calling", include_raw=True)


@lru_cache
def get_dedup_judge() -> Runnable:
    model = build_extract_model(get_settings())
    return dedup_judge_prompt | model.with_structured_output(DedupVerdict, method="function_calling", include_raw=True)
```

- [ ] **Step 4: 实现 staging 仓储、抽取、种子脚本**

`app/repositories/staging.py`：

```python
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, QaExtractionStaging


@dataclass(frozen=True)
class NewStaging:
    batch_no: str
    source_ref: str
    question: str
    answer: str


async def unmined_conversation_ids(s: AsyncSession, day: date) -> list[int]:
    start = datetime.combine(day, time.min)
    ref = func.concat("conversation:", Conversation.id)
    mined = exists().where(QaExtractionStaging.source_ref == ref)
    rows = await s.scalars(
        select(Conversation.id)
        .where(Conversation.created_at >= start, Conversation.created_at < start + timedelta(days=1), ~mined)
        .order_by(Conversation.id)
    )
    return list(rows)


async def max_batch_seq(s: AsyncSession, day: date) -> int:
    prefix = f"{day:%Y%m%d}-"
    values = await s.scalars(select(QaExtractionStaging.batch_no).where(QaExtractionStaging.batch_no.like(f"{prefix}%")))
    return max((int(v[len(prefix):]) for v in values), default=0)


async def add_rows(s: AsyncSession, rows: list[NewStaging]) -> None:
    s.add_all([QaExtractionStaging(**vars(r)) for r in rows])


async def list_extracted(s: AsyncSession) -> list[QaExtractionStaging]:
    return list(await s.scalars(
        select(QaExtractionStaging).where(QaExtractionStaging.status == "extracted").order_by(QaExtractionStaging.id)
    ))


async def set_status(s: AsyncSession, row_id: int, status: str) -> None:
    await s.execute(update(QaExtractionStaging).where(QaExtractionStaging.id == row_id).values(status=status))


async def status_counts(s: AsyncSession) -> dict[str, int]:
    rows = await s.execute(select(QaExtractionStaging.status, func.count()).group_by(QaExtractionStaging.status))
    return {st: int(n) for st, n in rows}
```

  注意：`max_batch_seq` 只统计有暂存行的批号。一批全部会话都没抽出问答对时，该批号不留痕迹，下次可能复用。这不影响追溯（该批没有行）。

`app/knowledge/mining.py`（本任务部分）：

```python
import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from langchain_core.runnables import Runnable

from app.config import MINE_BATCH_SIZE, MINE_CONCURRENCY
from app.db.engine import get_sessionmaker
from app.db.models import Message
from app.repositories import messages, staging
from app.repositories.staging import NewStaging

logger = logging.getLogger(__name__)


def source_ref(conversation_id: int) -> str:
    return f"conversation:{conversation_id}"


def batch_no(day: date, seq: int) -> str:
    return f"{day:%Y%m%d}-{seq:02d}"


def format_transcript(rows: list[Message]) -> str | None:
    """只取用户和客服的正文。没有客服正文时返回 None。"""
    lines, has_reply = [], False
    for m in rows:
        if m.role == "user" and m.content:
            lines.append(f"用户：{m.content}")
        elif m.role == "assistant" and m.content:
            lines.append(f"客服：{m.content}")
            has_reply = True
    return "\n".join(lines) if has_reply else None


@dataclass
class ExtractStats:
    conversations: int = 0
    skipped: int = 0
    failed: int = 0
    pairs: int = 0
    batches: int = 0


async def _extract_one(cid: int, extractor: Runnable, sem: asyncio.Semaphore, stats: ExtractStats) -> list[tuple[str, str]]:
    async with get_sessionmaker()() as s:
        text = format_transcript(await messages.list_for_conversation(s, cid))
    if text is None:
        stats.skipped += 1
        return []
    async with sem:
        try:
            result = await extractor.ainvoke({"transcript": text})
        except Exception:
            logger.exception("会话 %d 抽取失败", cid)
            stats.failed += 1
            return []
    parsed = result["parsed"]
    if parsed is None:
        logger.error("会话 %d 抽取结果无法解析：raw=%r，parsing_error=%r", cid, result["raw"], result["parsing_error"])
        stats.failed += 1
        return []
    return [(p.question.strip(), p.answer.strip()) for p in parsed.pairs if p.question.strip() and p.answer.strip()]


async def extract_day(
    day: date, extractor: Runnable, *, batch_size: int = MINE_BATCH_SIZE, concurrency: int = MINE_CONCURRENCY
) -> ExtractStats:
    """抽取指定日期的会话。一个会话一次 LLM 调用；一批的暂存行在一个事务中写入。"""
    stats = ExtractStats()
    async with get_sessionmaker()() as s:
        ids = await staging.unmined_conversation_ids(s, day)
        seq = await staging.max_batch_seq(s, day)
    stats.conversations = len(ids)
    sem = asyncio.Semaphore(concurrency)
    for start in range(0, len(ids), batch_size):
        batch_ids = ids[start:start + batch_size]
        results = await asyncio.gather(*(_extract_one(cid, extractor, sem, stats) for cid in batch_ids))
        pairs = [(cid, q, a) for cid, found in zip(batch_ids, results) for q, a in found]
        if not pairs:
            continue
        seq += 1
        bno = batch_no(day, seq)
        rows = [NewStaging(bno, source_ref(cid), q, a) for cid, q, a in pairs]
        async with get_sessionmaker()() as s:
            await staging.add_rows(s, rows)
            await s.commit()
        stats.pairs += len(rows)
        stats.batches += 1
    return stats
```

  说明：批号只在该批有暂存行时分配，所以测试 `test_extract_day_writes_staging_by_batch` 中第 1 批（c1、c2）为 `-01`，第 2 批（c3）为 `-02`。

`app/repositories/messages.py` 的 `list_for_conversation` 若签名不是 `(session, conversation_id)`，按实际签名调用，不改它。

`app/knowledge/history_seed.py`：

```python
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import Conversation, Message

HISTORY_FILE = Path(__file__).resolve().parents[2] / "knowledge" / "history" / "conversations.jsonl"
HISTORY_USER_ID = "history-seed"


async def seed_history(day: date, path: Path = HISTORY_FILE) -> int:
    """把样例历史对话写入 conversations 和 messages，created_at 设为 day。该日期已有种子会话时返回 0。"""
    start = datetime.combine(day, time(9, 0))
    async with get_sessionmaker()() as s:
        existing = await s.scalar(select(Conversation.id).where(
            Conversation.user_id == HISTORY_USER_ID,
            Conversation.created_at >= datetime.combine(day, time.min),
            Conversation.created_at < datetime.combine(day, time.min) + timedelta(days=1),
        ).limit(1))
        if existing is not None:
            return 0
        lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for i, item in enumerate(lines):
            at = start + timedelta(minutes=10 * i)
            conv = Conversation(user_id=HISTORY_USER_ID, created_at=at, updated_at=at)
            s.add(conv)
            await s.flush()
            for j, msg in enumerate(item["messages"]):
                s.add(Message(conversation_id=conv.id, role=msg["role"], content=msg["content"],
                              created_at=at + timedelta(seconds=j)))
        await s.commit()
        return len(lines)
```

`scripts/seed_history.py`：参数 `--date`（默认昨天，格式 `YYYY-MM-DD`）、`--file`（默认 `HISTORY_FILE`）。打印"写入 N 通历史对话"或"该日期已有种子会话，跳过"。结束前 `await dispose_engine()`。结构参考 `scripts/build_kb.py`。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/test_mining_extract.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add app/schemas.py app/prompts.py app/llm.py app/repositories/staging.py app/knowledge/mining.py app/knowledge/history_seed.py scripts/seed_history.py tests/test_mining_extract.py
git commit -m "feat(ch03): mine QA pairs from conversations into staging by batch"
```

---

### Task 9: 对话挖掘——整体去重入库与定时任务入口

**Files:**
- Modify: `app/knowledge/mining.py`
- Create: `scripts/mine_qa.py`、`tests/test_mining_dedup.py`

**Interfaces:**
- Consumes: `staging.list_extracted`、`staging.set_status`、`staging.status_counts`；`knowledge.insert_chunks`、`NewChunk`；`search_by_vector`；`get_embeddings`；`vectorize_pending`；`extract_day`。
- Produces（`app/knowledge/mining.py`）：
  - `format_candidates(cands: list[tuple[str, str]]) -> str`
  - `DedupStats(kept: int, discarded: int, failed: int)`
  - `async dedup_pending(judge: Runnable) -> DedupStats`
  - `MiningStats(extract: ExtractStats, dedup: DedupStats, vectorized: int)`
  - `async run_mining(day: date, extractor: Runnable, judge: Runnable) -> MiningStats`
- `scripts/mine_qa.py`：参数 `--date`（默认昨天）。

- [ ] **Step 1: 写失败的测试** `tests/test_mining_dedup.py`

```python
from datetime import date

import pytest
from langchain_core.runnables import RunnableLambda
from sqlalchemy import select

from app.db.models import KnowledgeChunk, QaExtractionStaging
from app.knowledge import mining
from app.knowledge.embeddings import set_embeddings
from app.knowledge.vectorize import vectorize_pending
from app.repositories import knowledge, staging
from app.repositories.knowledge import NewChunk
from app.repositories.staging import NewStaging
from app.schemas import DedupVerdict
from tests.fakes import FakeEmbeddings, blend, unit

pytestmark = pytest.mark.anyio


def _judge(decide, seen: list | None = None):
    """decide(inputs) -> DedupVerdict | Exception。"""
    async def run(inputs):
        if seen is not None:
            seen.append(inputs)
        out = decide(inputs)
        if isinstance(out, Exception):
            raise out
        return {"raw": None, "parsing_error": None, "parsed": out}
    return RunnableLambda(run)


async def _stage(db, *pairs):
    async with db() as s:
        await staging.add_rows(s, [NewStaging("20261005-01", f"conversation:{i}", q, a) for i, (q, a) in enumerate(pairs)])
        await s.commit()


async def _staging_status(db):
    async with db() as s:
        return [(r.question, r.status) for r in (await s.execute(select(QaExtractionStaging).order_by(QaExtractionStaging.id))).scalars()]


async def _mined(db):
    async with db() as s:
        return list((await s.execute(select(KnowledgeChunk).where(KnowledgeChunk.content_type == "mined").order_by(KnowledgeChunk.id))).scalars())


def test_format_candidates():
    assert mining.format_candidates([]) == "（无）"
    assert mining.format_candidates([("运费怎么算？", "满 99 免。")]) == "1. 问：运费怎么算？\n   答：满 99 免。"


async def test_duplicate_of_kb_is_discarded(db, milvus):
    set_embeddings(FakeEmbeddings([("运费怎么算", unit(0)), ("邮费多少", blend(0, 1, 0.8))]))
    async with db() as s:
        await knowledge.insert_chunks(s, [NewChunk("运费", "运费怎么算？", "满 99 元免运费。", "常见问答 > 运费", "faq", False)])
        await s.commit()
    await vectorize_pending()
    await _stage(db, ("邮费多少", "满 99 包邮。"))
    seen: list = []
    stats = await mining.dedup_pending(_judge(lambda i: DedupVerdict(duplicate_of=1, category="运费"), seen))
    assert "运费怎么算？" in seen[0]["candidates"]
    assert await _staging_status(db) == [("邮费多少", "discarded")]
    assert await _mined(db) == []
    assert (stats.kept, stats.discarded) == (0, 1)


async def test_new_pair_is_kept_as_mined_chunk(db, milvus):
    await _stage(db, ("能开专票吗", "可以开增值税专用发票。"))
    seen: list = []
    stats = await mining.dedup_pending(_judge(lambda i: DedupVerdict(duplicate_of=None, category="发票"), seen))
    assert seen[0]["candidates"] == "（无）"
    [chunk] = await _mined(db)
    assert (chunk.questions, chunk.answer, chunk.category, chunk.section_path, chunk.vectorize_status) == (
        "能开专票吗", "可以开增值税专用发票。", "发票", "对话挖掘 > 发票", "pending")
    assert await _staging_status(db) == [("能开专票吗", "kept")]
    assert stats.kept == 1


async def test_kept_pairs_in_same_run_are_candidates(db, milvus):
    set_embeddings(FakeEmbeddings([("改地址", unit(3)), ("换收货地址", blend(3, 4, 0.9))]))
    await _stage(db, ("能改地址吗", "发货前可以改。"), ("怎么换收货地址", "发货前在订单页修改。"))

    def decide(i):
        return DedupVerdict(duplicate_of=1 if i["candidates"] != "（无）" else None, category="物流")

    seen: list = []
    await mining.dedup_pending(_judge(decide, seen))
    assert "能改地址吗" in seen[1]["candidates"]
    assert await _staging_status(db) == [("能改地址吗", "kept"), ("怎么换收货地址", "discarded")]


async def test_dedup_out_of_range_index_keeps_extracted(db, milvus):
    await _stage(db, ("能开专票吗", "可以。"))
    stats = await mining.dedup_pending(_judge(lambda i: DedupVerdict(duplicate_of=5, category="发票")))
    assert await _staging_status(db) == [("能开专票吗", "extracted")]
    assert await _mined(db) == []
    assert stats.failed == 1


async def test_judge_failure_keeps_extracted_and_continues(db, milvus):
    await _stage(db, ("甲", "甲答。"), ("乙", "乙答。"))

    def decide(i):
        return RuntimeError("上游失败") if i["question"] == "甲" else DedupVerdict(duplicate_of=None, category="其他")

    stats = await mining.dedup_pending(_judge(decide))
    assert await _staging_status(db) == [("甲", "extracted"), ("乙", "kept")]
    assert (stats.kept, stats.failed) == (1, 1)


async def test_run_mining_end_to_end(db, milvus):
    from datetime import datetime
    from app.db.models import Conversation, Message
    from app.schemas import QaPair, QaPairs

    day = date(2026, 10, 5)
    async with db() as s:
        conv = Conversation(user_id="u1", created_at=datetime(2026, 10, 5, 10))
        s.add(conv)
        await s.flush()
        s.add_all([Message(conversation_id=conv.id, role="user", content="能开专票吗"),
                   Message(conversation_id=conv.id, role="assistant", content="可以开专票。")])
        await s.commit()
    extractor = RunnableLambda(lambda _: {"raw": None, "parsing_error": None,
                                          "parsed": QaPairs(pairs=[QaPair(question="能开专票吗", answer="可以开专票。")])})
    stats = await mining.run_mining(day, extractor, _judge(lambda i: DedupVerdict(duplicate_of=None, category="发票")))
    assert (stats.extract.pairs, stats.dedup.kept, stats.vectorized) == (1, 1, 1)
    [chunk] = await _mined(db)
    assert chunk.vectorize_status == "done"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_mining_dedup.py -q`
Expected: FAIL（`AttributeError: module 'app.knowledge.mining' has no attribute 'format_candidates'`）

- [ ] **Step 3: 实现**（追加到 `app/knowledge/mining.py`）

```python
import math

from app.config import DEDUP_KB_MIN_SCORE, DEDUP_STAGING_MIN_SCORE
from app.knowledge.chunking import PATH_SEP
from app.knowledge.embeddings import get_embeddings
from app.knowledge.retrieval import search_by_vector
from app.knowledge.vectorize import vectorize_pending
from app.repositories import knowledge
from app.repositories.knowledge import NewChunk

MINED_SOURCE = "对话挖掘"
DEDUP_KB_TOP_K = 5


def format_candidates(cands: list[tuple[str, str]]) -> str:
    if not cands:
        return "（无）"
    return "\n".join(f"{i}. 问：{q}\n   答：{a}" for i, (q, a) in enumerate(cands, start=1))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@dataclass
class DedupStats:
    kept: int = 0
    discarded: int = 0
    failed: int = 0


async def dedup_pending(judge: Runnable) -> DedupStats:
    """逐行处理 extracted 暂存行：向量召回候选，LLM 裁定，重复则丢弃，否则写入 knowledge_chunks。"""
    stats = DedupStats()
    embeddings = get_embeddings()
    async with get_sessionmaker()() as s:
        rows = await staging.list_extracted(s)
    kept: list[tuple[list[float], str, str]] = []  # 本次保留项，还不在 Milvus 中
    for row in rows:
        try:
            vec = await embeddings.aembed_query(row.question)
            kb = await search_by_vector(vec, limit=DEDUP_KB_TOP_K, min_score=DEDUP_KB_MIN_SCORE)
            cands = [(c.questions, c.answer) for c, _ in kb]
            cands += [(q, a) for v, q, a in kept if _cosine(v, vec) >= DEDUP_STAGING_MIN_SCORE]
            result = await judge.ainvoke({
                "question": row.question, "answer": row.answer, "candidates": format_candidates(cands),
            })
        except Exception:
            logger.exception("暂存行 %d 去重失败", row.id)
            stats.failed += 1
            continue
        verdict = result["parsed"]
        if verdict is None or (verdict.duplicate_of is not None and not 1 <= verdict.duplicate_of <= len(cands)):
            logger.error("暂存行 %d 裁定结果无效：raw=%r", row.id, result["raw"])
            stats.failed += 1
            continue
        async with get_sessionmaker()() as s:
            if verdict.duplicate_of is not None:
                await staging.set_status(s, row.id, "discarded")
                stats.discarded += 1
            else:
                await knowledge.insert_chunks(s, [NewChunk(
                    verdict.category, row.question, row.answer,
                    f"{MINED_SOURCE}{PATH_SEP}{verdict.category}", "mined", False,
                )])
                await staging.set_status(s, row.id, "kept")
                kept.append((vec, row.question, row.answer))
                stats.kept += 1
            await s.commit()
    return stats


@dataclass
class MiningStats:
    extract: ExtractStats
    dedup: DedupStats
    vectorized: int


async def run_mining(day: date, extractor: Runnable, judge: Runnable) -> MiningStats:
    """抽取 → 补齐向量 → 整体去重 → 向量化。"""
    extract = await extract_day(day, extractor)
    vectorized = await vectorize_pending()  # 保证去重时知识库已有内容都能从 Milvus 召回
    dedup = await dedup_pending(judge)
    vectorized += await vectorize_pending()
    return MiningStats(extract, dedup, vectorized)
```

  import 放到文件顶部，和 Task 8 的 import 合并。注意 `retrieval` → `vectorize` 之间没有循环 import。

`scripts/mine_qa.py`：

```python
"""定时任务：从指定日期的历史客服对话中挖掘问答对，去重后入库。跑一次就退出。

crontab 示例（每天 02:00 处理前一天）：
0 2 * * * cd /path/to/Aftersales-agent && uv run python scripts/mine_qa.py >> log/mine_qa.log 2>&1
"""

import argparse
import asyncio
from datetime import date, timedelta
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.engine import dispose_engine, get_sessionmaker
from app.knowledge.milvus import close_milvus, ensure_collection
from app.knowledge.mining import run_mining
from app.llm import get_dedup_judge, get_qa_extractor
from app.repositories import staging

logger = logging.getLogger(__name__)


async def run(day: date) -> int:
    try:
        await ensure_collection()
        st = await run_mining(day, get_qa_extractor(), get_dedup_judge())
        print(f"日期 {day}：会话 {st.extract.conversations}，跳过 {st.extract.skipped}，"
              f"抽取失败 {st.extract.failed}，抽出 {st.extract.pairs} 对（{st.extract.batches} 批）")
        print(f"去重：保留 {st.dedup.kept}，丢弃 {st.dedup.discarded}，裁定失败 {st.dedup.failed}；向量化 {st.vectorized} 块")
        async with get_sessionmaker()() as s:
            print(f"暂存表状态：{await staging.status_counts(s)}")
        return 0
    finally:
        await close_milvus()
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="从历史对话挖掘问答对")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        return asyncio.run(run(args.date))
    except Exception:
        logger.exception("挖掘任务失败")
        print("挖掘任务失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_mining_dedup.py -q && uv run pytest -q`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/knowledge/mining.py scripts/mine_qa.py tests/test_mining_dedup.py
git commit -m "feat(ch03): vector-recall + LLM dedup of mined QA and mine_qa job"
```

---

### Task 10: 挖掘演示数据与 Prompt 评估（数据类，代替 TDD）

**Files:**
- Create（Claude 执行）：`knowledge/history/conversations.jsonl`、`evals/mine_extract_samples.jsonl`、`evals/dedup_samples.jsonl`
- Create（Codex 执行）：`evals/run_mine_extract_eval.py`、`evals/run_dedup_eval.py`

- [ ] **Step 1（Claude 执行）：写约 30 通历史对话** `knowledge/history/conversations.jsonl`

  每行 `{"messages": [{"role": "user"|"assistant", "content": "..."}]}`。覆盖（spec 7.5）：
  - 和文档或 `faq` 表重复的问题，约 8 通（期望丢弃）。
  - 同一个新问题的 3 种问法，2 组共 6 通（期望每组只保留 1 条）。
  - 文档没有的新知识，约 6 通，例如"能开专票吗""能改收货地址吗""可以指定快递吗"（期望保留）。
  - 闲聊，约 4 通（期望不抽出）。
  - 订单个人信息，约 4 通（含订单号、物流状态；期望不抽出个人信息）。
  - 客服回答"查不到"或只建议转人工，约 2 通（期望不抽出）。
  - 全文不出现"邮"字以外的约束不需要（挖掘数据允许"包邮"等说法，用来测试去重）。

- [ ] **Step 2（Claude 执行）：写评估集**

  `evals/mine_extract_samples.jsonl`：约 10 条，每条 `{"messages": [...], "min_pairs": 0, "max_pairs": 1, "forbidden": ["1001"]}`。
  `evals/dedup_samples.jsonl`：约 12 条，每条 `{"question": "...", "answer": "...", "candidates": [["问", "答"], ...], "expected_duplicate": true}`。重复与不重复约各一半，含"新问题比候选更具体"的反例。

- [ ] **Step 3（用户审核）：** 把对话数据和两个评估集交给用户审核，按意见修改。

- [ ] **Step 4: 评估脚本（Codex）**

  `evals/run_mine_extract_eval.py`：结构参考 `evals/run_tool_selection_eval.py`。对每条样例，用 `mining.format_transcript` 的同样格式把 `messages` 拼成文本（构造 `Message(role=..., content=...)` 对象后调用它），调用 `get_qa_extractor().ainvoke({"transcript": text})`，并发 4。打印每条的问答对。通过条件：`parsed` 不为空、问答对数量在 `[min_pairs, max_pairs]` 内、问答对文本中不含任一 `forbidden` 子串。通过率 ≥ 90% 时退出码 0，否则 1。

  `evals/run_dedup_eval.py`：对每条样例调用 `get_dedup_judge().ainvoke({"question", "answer", "candidates": mining.format_candidates(候选)})`。判定：`(parsed.duplicate_of is not None) == expected_duplicate`；重复时序号必须在范围内。打印每条的裁定和分类。准确率 ≥ 90% 时退出码 0，否则 1。

- [ ] **Step 5（Claude 执行）：真实上游运行**

```bash
uv run python evals/run_mine_extract_eval.py
uv run python evals/run_dedup_eval.py
uv run python scripts/seed_history.py --date 2026-10-05
uv run python scripts/mine_qa.py --date 2026-10-05
```

  Expected: 两个评估达标；`mine_qa.py` 的保留数接近期望（约 8 条：6 通新知识中 2 组各留 1 条 + 其余新知识），丢弃数覆盖重复项。逐条核对新入库的 `mined` 块，结果记入 dev-notes。未达标时，Claude 分析失败样例，修改 Prompt 文本（交给 Codex 写入 `app/prompts.py`），重跑。

- [ ] **Step 6: Commit**

```bash
git add knowledge/history/conversations.jsonl evals/mine_extract_samples.jsonl evals/dedup_samples.jsonl evals/run_mine_extract_eval.py evals/run_dedup_eval.py app/prompts.py
git commit -m "feat(ch03): sample history conversations and mining prompt evals"
```

---

### Task 11: 验收脚本与文档

**Files:**
- Create（Codex 执行）：`scripts/demo3.sh`
- Modify（Claude 执行）：`CLAUDE.md`

- [ ] **Step 1: 验收脚本（Codex）** `scripts/demo3.sh`

  要求（`set -euo pipefail`，脚本开头 `cd` 到项目根）：
  1. 检查服务健康（同 `scripts/demo2.sh`），不健康时提示启动命令并退出 1。
  2. `=== 验收 2：建库中断与重跑 ===`：
     - 运行 `uv run python scripts/build_kb.py --rebuild --crash-after-batches 2`，捕获退出码；不是 1 时报错退出。
     - 运行 `uv run python scripts/build_kb.py`（打印补齐结果）。
     - 运行 `uv run python scripts/build_kb.py --check`；退出码不是 0 时报错退出。
  3. `=== 对话挖掘 ===`：`DAY=$(date -v-1d +%F)`（macOS）；运行 `seed_history.py --date "$DAY"` 和 `mine_qa.py --date "$DAY"`；用 `docker exec aftersales-mysql mysql --default-character-set=utf8mb4 ...` 打印 `content_type='mined'` 的 `questions`、`category`、`vectorize_status`。
  4. `=== 验收 1：邮费是多少 ===`：用 curl 调 `/chat/stream`（`user_id=demo3-<时间戳>`），用 python 拼接全部 `token` 事件的 `text`，打印回复；用 `docker exec` 打印本会话 `role='tool'` 的消息内容（`query_faq` 的检索结果）。回复不含 `99` 或不匹配正则 `8\s*元` 时报错退出 1。
  5. 全部通过时打印 `ch03 验收全部通过`。

- [ ] **Step 2（Claude 执行）：更新 `CLAUDE.md`**
  - 项目状态加 ch03 一行。
  - 常用命令加：`build_kb.py`（含 `--rebuild`、`--status`、`--check`）、`seed_history.py`、`mine_qa.py`、`run_retrieval_eval.py`、`run_mine_extract_eval.py`、`run_dedup_eval.py`、`demo3.sh`；`docker compose up -d --wait` 的说明改为"启动 MySQL 和 Milvus"。
  - 架构图和模块表加 `app/knowledge/`、`repositories/knowledge.py`、`repositories/staging.py`。
  - 设计约束：删除"`query_faq` 的 keyword 取用户原词……留给向量检索"；新增：Milvus 主键 = MySQL 主键、只存 id + 向量；入库只写 `pending`，统一由 `vectorize_pending()` 写 Milvus；`OpenAIEmbeddings` 的两个必设参数；测试的 `BlockedMilvus` / `milvus` fixture / `FakeEmbeddings`；`knowledge_chunks` 删除前先清指针。
  - 环境变量表和说明加 `MILVUS_URI`；"ch01、ch02 只用到聊天这一组"改为包含 ch03 的嵌入。

- [ ] **Step 3（Claude 执行）：完整验收**

```bash
uv run pytest -q
pgrep -f "uvicorn app.main:app" || true   # 确认没有旧进程
uv run uvicorn app.main:app --port 8000 &  # 另开终端
bash scripts/demo3.sh
uv run python evals/run_tool_selection_eval.py
```

  Expected: 全部测试 PASS；`demo3.sh` 打印"ch03 验收全部通过"；工具选择评估不退化（达标退出码 0）。

- [ ] **Step 4: Commit**

```bash
git add scripts/demo3.sh CLAUDE.md
git commit -m "docs(ch03): acceptance demo and project guide updates"
```
