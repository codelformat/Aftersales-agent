# ch03 知识库与向量检索：设计规格

- 日期：2026-10-06
- 状态：待用户审阅
- 分支：`ch03`
- 前置：ch02（`docs/superpowers/specs/2026-10-06-ch02-function-calling-design.md`）。本文只写新增和变化的部分；没有提到的 ch02 行为保持不变。

## 1. 目标与验收标准

**目标：** 给客服系统建知识库。`query_faq` 的内部实现从关键词查表（LIKE）升级为向量语义检索。工具的入参和出参契约保持不变。

**验收标准：**

1. 问「邮费是多少」这类换了说法的问题，`query_faq` 能召回运费说明，回答正确（包含"99 元"和"8 元"）。
2. 故意中断建库任务，再重跑。漏向量化的块被捡起补齐：MySQL 中 `pending` 为 0，Milvus 实体数等于 MySQL 行数，没有重复。

**本章不做：** 关键词召回、混合检索、重排。本章只跑 dense 向量单路。另外不做：Agent Loop、多进程并发建库、Langfuse 接入。

## 2. 技术栈

| 项 | 选择 |
|---|---|
| 向量库 | Milvus Standalone `milvusdb/milvus:v2.6.22`（Docker Compose，含 etcd、minio） |
| Milvus SDK | `pymilvus` 的 `AsyncMilvusClient`：`create_schema`、`prepare_index_params`、`create_collection`、`upsert`、`search`、`query`、`delete`（已用 Context7 核对） |
| 嵌入 | 硅基流动 `BAAI/bge-m3`，1024 维，向量已归一化。用 LangChain `OpenAIEmbeddings`，`check_embedding_ctx_length=False`（非 OpenAI 服务必须设置，否则发送 token id；已用 Context7 核对） |
| 切分 | `langchain-text-splitters`：`MarkdownHeaderTextSplitter`（`headers_to_split_on`、`strip_headers`）、`RecursiveCharacterTextSplitter`（`separators`、`keep_separator`）（已用 Context7 核对） |
| 原文权威源 | MySQL 8（沿用），新增 `knowledge_chunks`、`qa_extraction_staging` |
| 抽取与裁定 | 聊天模型（沿用 `CHAT_*`），关闭思考，`with_structured_output(method="function_calling")` |

新增依赖：`pymilvus`、`langchain-text-splitters`。

**设计阶段实测**（一次性探测，不进仓库）：真实 BGE-M3 返回 1024 维、模长 1.0 的向量。「邮费」「邮费是多少」「快递费」的第 1 名都是运费条目（0.649、0.686、0.682）。无关问题「天气怎么样」「红烧肉怎么做」的最高分为 0.425、0.342。

## 3. 配置

- `.env` 新增 1 个变量（用户已同意）：`MILVUS_URI=http://127.0.0.1:19530`。
- `Settings` 新增读取：`embed_api_key`（必填）、`embed_base_url`（默认 `https://api.siliconflow.cn/v1`）、`milvus_uri`（必填）。
- 新增代码常量：

| 常量 | 值 | 用途 |
|---|---|---|
| `EMBED_MODEL` | `"BAAI/bge-m3"` | 嵌入模型 |
| `EMBED_DIM` | `1024` | 向量维度 |
| `EMBED_TIMEOUT_SECONDS` | `10` | 单次嵌入请求超时 |
| `EMBED_MAX_RETRIES` | `2` | 交给 OpenAI SDK 的重试次数（SDK 自带指数回退） |
| `KNOWLEDGE_COLLECTION` | `"knowledge"` | 生产集合 |
| `KNOWLEDGE_TEST_COLLECTION` | `"knowledge_test"` | 测试集合 |
| `CHUNK_MAX_CHARS` | `400` | 块正文最大字符数 |
| `OVERLAP_MAX_CHARS` | `100` | 重叠段最大字符数 |
| `VECTORIZE_BATCH_SIZE` | `16` | 每批向量化的行数 |
| `MILVUS_MAX_ATTEMPTS` | `3` | Milvus 调用最多尝试次数（`retry_async`） |
| `FAQ_MIN_SCORE` | `0.50` | 在线检索的最低相似度，用评估集校准 |
| `FAQ_MAX_RESULTS` | `3` | 沿用 |
| `MINE_BATCH_SIZE` | `20` | 每批会话数 |
| `MINE_CONCURRENCY` | `4` | 一批内的最大并发 LLM 调用数 |
| `DEDUP_KB_MIN_SCORE` | `0.55` | 去重时知识库候选的最低相似度 |
| `DEDUP_STAGING_MIN_SCORE` | `0.75` | 去重时本次保留项候选的最低相似度 |
| `MINED_CATEGORIES` | 退换货、运费、发票、售后维修、账户、支付、物流、其他 | 挖掘问答对的分类 |

## 4. 部署与数据

### 4.1 Docker Compose

- 在 `docker-compose.yml` 加 3 个服务：`etcd`、`minio`、`milvus-standalone`。容器名为 `aftersales-milvus-etcd`、`aftersales-milvus-minio`、`aftersales-milvus`；数据卷为 `aftersales-milvus-etcd`、`aftersales-milvus-minio`、`aftersales-milvus-data`。
- 宿主只暴露 19530（gRPC）和 9091（健康检查）。minio 不暴露端口，原因：本机 9000 已被 Langfuse 的 minio 占用。
- `milvus-standalone` 带健康检查（`curl -f http://localhost:9091/healthz`），所以 `docker compose up -d --wait` 会等到 Milvus 可用。

### 4.2 表结构

- 用户 DDL `ch03.sql` 逐字移到 `db/schema_ch03.sql`，是这两张表结构的唯一来源。`db/schema.sql` 不改。
- 容器初始化顺序：`00-test-db.sql` → `01-schema.sql` → `02-schema-ch03.sql` → `03-seed.sql`。
- ORM 新增 `KnowledgeChunk`、`QaExtractionStaging` 映射。只映射，不 `create_all`。
- `vectorize_status` 取值 `pending`（待向量化）、`done`（已向量化）。`qa_extraction_staging.status` 取值 `extracted`、`kept`、`discarded`。

### 4.3 Milvus 集合

- 字段：`id` INT64 主键（`auto_id=False`，等于 `knowledge_chunks.id`）；`vector` FLOAT_VECTOR，`dim=1024`。不存正文和元数据。
- 索引：`AUTOINDEX`，`metric_type="COSINE"`。
- 一致性级别：集合级 `Strong`。写入后立即可检索；默认的 Bounded 会让刚写入的向量短时间查不到。数据量小，额外延迟可以忽略。（计划阶段修订：原设计按调用区分一致性。）
- `ensure_collection(client, name)`：集合不存在时创建，存在时不动。集合定义写在代码中（`app/knowledge/milvus.py`）。
- `vector_id` 回填为 `str(id)`。

### 4.4 重建脚本

`scripts/reset_db.sh` 用 `docker compose down -v`，一起清空 MySQL 和 Milvus 的卷。重建后保留原有的 FAQ HEX 校验，并检查 `knowledge_chunks` 表存在、Milvus 健康检查返回正常。

## 5. 架构与模块

```
api → services → repositories → db
         ↘ tools ──→ knowledge.retrieval → knowledge.embeddings, knowledge.milvus, repositories.knowledge
scripts/build_kb.py → knowledge.ingest, knowledge.vectorize
scripts/mine_qa.py  → knowledge.mining, knowledge.vectorize
```

| 模块 | 职责 |
|---|---|
| `app/knowledge/embeddings.py` | `OpenAIEmbeddings` 工厂；get/set（测试替换为假嵌入） |
| `app/knowledge/milvus.py` | `AsyncMilvusClient` 和集合名的 get/set；`ensure_collection`；`upsert`、`search`、`delete`、`count` 封装，Milvus 调用经 `retry_async` 重试 |
| `app/knowledge/chunking.py` | 纯函数：Markdown → 块列表（第 6 节） |
| `app/knowledge/ingest.py` | 文档和 `faq` 表入库（写 MySQL `pending`），`rebuild` |
| `app/knowledge/vectorize.py` | `vectorize_pending()`（第 8 节） |
| `app/knowledge/mining.py` | 抽取、去重（第 7 节） |
| `app/knowledge/retrieval.py` | 在线检索（第 9 节） |
| `app/repositories/knowledge.py` | `knowledge_chunks` 的 SQL |
| `app/repositories/staging.py` | `qa_extraction_staging` 的 SQL |
| `app/prompts.py` | 新增抽取 Prompt、裁定 Prompt |
| `app/tools/faq.py` | `query_faq` 改为调用 `retrieval.search_faq`；入参、出参、描述不变 |

- Milvus 客户端和嵌入对象用 get/set 模式，和 `get_sessionmaker`/`set_sessionmaker` 相同。测试中每个用例新建客户端，原因：pytest 每个异步测试用一个新的事件循环。
- 删除 `app/repositories/faq.py`（LIKE 查询）。`faq` 表保留，作为原始录入来源（第 6.5 节）。
- 向量化文本统一由一个函数生成：`f"{category}\n{questions}\n{answer}"`。

## 6. 文档切分（离线建库·文档处理）

### 6.1 文档

- 位置：`knowledge/docs/<content_type>/*.md`。子目录名就是 `content_type`：`policy/退货政策.md`、`faq/商品FAQ.md`、`manual/售后手册.md`。
- 每份文档的一级标题（`#`）是文档名，全库唯一。
- 内容由 Claude 编写、用户审核。内容和 `db/seed.sql` 一致，不矛盾。必须包含：多级标题；至少 1 个超过 400 字的小节（触发递归切）；至少 1 张超过 400 字、15 行以上的表格（触发按行切）；至少 2 处 `【关键条款】` 标记。3 份文档和 `faq` 表合计至少 49 块（多于 3 批），保证 10.4 节演示中断时还有未处理的批次。
- 运费只在 `faq` 表中出现，用"运费"一词。3 份文档全文不出现"邮"字（验收 1 依赖此约束：召回必须来自语义，不来自字面）。

### 6.2 切分步骤

1. 用 `MarkdownHeaderTextSplitter` 按 `#`、`##`、`###` 切成小节，`strip_headers=True`。
2. 小节正文中的 Markdown 表格（以 `|` 开头、第 2 行是分隔行的连续行）单独处理：
   - 表格不超过 `CHUNK_MAX_CHARS` 时，整张表是一块。
   - 表格超过时，按行切。每块累积若干数据行，总长不超过 `CHUNK_MAX_CHARS`，每块开头复制表头行和分隔行。表格块之间不加重叠。
3. 表格以外的正文不超过 `CHUNK_MAX_CHARS` 时，是一块。
4. 超长正文用 `RecursiveCharacterTextSplitter` 递归切：`separators=["\n\n", "\n", "。", "！", "？", "；", "，", ""]`，`keep_separator="end"`（句末标点留在前一块末尾），`chunk_size=CHUNK_MAX_CHARS`，`chunk_overlap=0`。
5. 同一小节内相邻的正文块加重叠：取前一块末尾最多 `OVERLAP_MAX_CHARS` 字；从这段里第一个句末标点（`。！？`）之后开始截取，加到后一块开头。这段里没有句末标点、或截取结果为空时，不加重叠。所以重叠段总是从完整句子开始，不留半截话。
6. 不同小节之间以标题为边界，不加重叠。

### 6.3 字段映射

三类文档用同一规则：

| 字段 | 取值 |
|---|---|
| `questions` | 最后一级标题。商品 FAQ 文档把问法写成最后一级标题，所以这里是真实问法；政策和手册是章节标题 |
| `category` | 最后一级标题之上的标题路径，用 ` > ` 连接，例如「退货政策 > 退货条件」 |
| `section_path` | 完整标题路径，例如「退货政策 > 退货条件 > 无理由退货」 |
| `answer` | 块正文（含重叠段） |
| `content_type` | 子目录名 |
| `is_key_clause` | 块正文含 `【关键条款】` 时为 1，并从正文中删除该标记；否则为 0 |
| `prev_chunk_id` / `next_chunk_id` | 同一文档内按文档顺序串成链表，跨小节相连；首块的 prev、末块的 next 为空 |

只有一级标题的小节：`questions` 和 `category` 都取文档名。

### 6.4 入库与幂等（按文档）

- 一份文档的全部块在一个 MySQL 事务中写入：先插入全部块（`pending`），得到 id 后再更新前后指针，然后提交。
- 入库前检查：已有 `section_path` 等于文档名或以「文档名 > 」开头的块时，跳过这份文档。
- `rebuild`：对每个文档来源，先从 Milvus 按 id 删除向量，再在一个事务中删除 MySQL 的块，然后重新入库。只处理 `policy`、`faq`、`manual` 和 6.5 节的「常见问答」，不处理 `mined`。删除顺序保证：中途中断后重跑 `rebuild`，结果仍然正确（Milvus 删除不存在的 id 不报错）。

### 6.5 `faq` 表导入

- `faq` 表的记录作为一个虚拟文档「常见问答」入库：`questions`=`question`，`category`=`category`，`answer`=`answer`，`section_path`=「常见问答 > 分类」，`content_type=faq`，`is_key_clause=0`，前后指针为空（条目互相独立）。
- 幂等规则同 6.4 节。

## 7. 对话挖掘（离线建库·对话挖知识）

### 7.1 入口与调度

- `scripts/mine_qa.py --date YYYY-MM-DD`，默认昨天。跑一次就退出。
- 调度交给系统 crontab。README 给出示例：`0 2 * * * cd /path/to/Aftersales-agent && uv run python scripts/mine_qa.py`。

### 7.2 总流程

1. **抽取**（7.3 节）。
2. **补齐向量**：调用 `vectorize_pending()`，保证去重时知识库已有内容都能从 Milvus 召回。
3. **整体去重**（7.4 节）。
4. **向量化**：调用 `vectorize_pending()`。
5. 打印本次统计：会话数、抽出数、`kept` 数、`discarded` 数。

### 7.3 抽取

- 选会话：`conversations.created_at` 在指定日期内，且 `qa_extraction_staging` 中没有 `source_ref = "conversation:{id}"` 的行。按 id 升序。
- 分批：每 `MINE_BATCH_SIZE` 个会话为一批，`batch_no` 为 `YYYYMMDD-NN`（NN 从本日已有的最大批号加 1 开始）。
- 一个会话调用一次 LLM，避免多个会话串味。一批内最多 `MINE_CONCURRENCY` 个并发。
- 对话文本只取 `user` 和 `assistant` 消息的正文，按时间顺序，标注"用户："和"客服："。不取 `tool` 消息和 `tool_calls`。没有 `assistant` 正文的会话跳过。
- 输出结构：`QaPairs { pairs: list[QaPair{question, answer}] }`，0 到 N 个。
- Prompt 规则：
  1. 只抽通用、可复用的店铺知识：政策、流程、规则、时限。
  2. 不抽订单号、物流状态、金额等个人订单信息，不抽闲聊。
  3. 客服回答"查不到""不知道"或只建议转人工的，不抽。
  4. `question` 保留用户的真实问法，去掉订单号等个人信息。
  5. `answer` 只用客服说过的内容，不补充、不编造。
- 一批的全部暂存行在一个事务中写入，`status=extracted`。
- 单个会话的 LLM 调用失败（SDK 已重试）或输出无法解析时：记日志，跳过这个会话。它没有暂存行，下次运行会重新抽取。没抽出问答对的会话也没有暂存行，下次运行也会重新抽取（已知取舍，见第 12 节）。

### 7.4 整体去重

按 id 升序处理所有 `status=extracted` 的暂存行（包括之前中断的运行留下的行）。每一行：

1. 把 `question` 向量化（`aembed_query`）。
2. 召回候选：
   - 知识库候选：到 Milvus 检索 Top 5，相似度 ≥ `DEDUP_KB_MIN_SCORE` 的，从 MySQL 读出 `questions` 和 `answer`。
   - 本次保留项候选：本次运行中已保留的问答对，问题向量的余弦相似度 ≥ `DEDUP_STAGING_MIN_SCORE` 的。在内存中比较，原因：它们还没有进 Milvus。
3. LLM 裁定（每行都调用，候选为空时也调用，用于确定分类）。输出结构：`DedupVerdict { duplicate_of: int | None, category: Literal[MINED_CATEGORIES] }`。`duplicate_of` 是候选序号。规则：候选已经回答了同一个问题（问法不同但意图相同）时，判为重复；以知识库为准，新答案和候选矛盾时也判为重复。
4. 重复：暂存行改为 `discarded`。
5. 不重复：在一个事务中插入 `knowledge_chunks`（`questions`=问法，`answer`=答案，`category`=裁定分类，`section_path`=「对话挖掘 > 分类」，`content_type=mined`，`is_key_clause=0`，指针为空，`pending`），并把暂存行改为 `kept`。
6. `duplicate_of` 超出候选范围，或裁定调用失败时：这一行保持 `extracted`，记日志，处理下一行。下次运行再处理。

### 7.5 演示数据

- `knowledge/history/conversations.jsonl`：约 30 通历史对话，Claude 编写、用户审核。每行一通：`{"messages": [{"role": "user"|"assistant", "content": "..."}]}`。
- 必须覆盖：和文档或 `faq` 表重复的问题（期望丢弃）；同一个新问题的 3 种问法（期望只保留 1 条）；文档没有的新知识，例如"能开专票吗""能改收货地址吗"（期望保留）；闲聊；订单个人信息（期望不抽出）。
- `scripts/seed_history.py --date YYYY-MM-DD`（默认昨天）：把对话写入 `conversations`（`user_id=history-seed`）和 `messages`，`created_at` 设为该日期。

## 8. 双写与向量化

### 8.1 `vectorize_pending(*, crash_after_batches=None) -> int`

按 id 升序，每批取 `VECTORIZE_BATCH_SIZE` 行 `pending`（取 id 大于上一批最大 id 的行，避免同一批反复失败时死循环）。每批：

1. 生成向量化文本，调用 `aembed_documents`。
2. Milvus `upsert`：`[{"id": id, "vector": v}]`。
3. MySQL 一个事务：`vector_id = str(id)`，`vectorize_status = 'done'`。
4. 没有更多 `pending` 行时结束，返回本次处理的行数。

`crash_after_batches=N`：故障注入，只给 `build_kb.py` 演示和测试使用。完成 N 批后，第 N+1 批执行完第 2 步（Milvus 已写入），在第 3 步之前抛出 `SimulatedCrash`。

### 8.2 故障与重跑

| 中断位置 | 中断后状态 | 重跑结果 |
|---|---|---|
| 嵌入或 Milvus 写入前 | MySQL `pending`，Milvus 没有 | 正常处理 |
| Milvus 写入后、MySQL 回填前 | MySQL `pending`，Milvus 已有 | 再次 `upsert` 同一主键，覆盖，不产生重复 |
| MySQL 回填后 | MySQL `done`，Milvus 已有 | 跳过 |

- 嵌入调用的重试交给 OpenAI SDK（`max_retries=EMBED_MAX_RETRIES`，自带指数回退）。
- Milvus 调用用 `retry_async`（指数回退加抖动，`MILVUS_MAX_ATTEMPTS` 次），只重试 `MilvusException`。
- 重试用完后抛出异常，脚本以非 0 退出码结束。已完成的批次保持 `done`。

### 8.3 建库入口 `scripts/build_kb.py`

步骤：
1. 确保 Milvus 集合存在。
2. 如果带 `--rebuild`，执行 6.4 节的重建。
3. 切分 `knowledge/docs/` 下的文档入库，已入库的跳过。
4. 导入 `faq` 表，已导入则跳过。
5. 调用 `vectorize_pending()`。
6. 打印状态。

参数：
- `--rebuild`：重建全部文档来源（不含 `mined`）。
- `--crash-after-batches N`：传给 `vectorize_pending()`。捕获 `SimulatedCrash` 后打印状态，以退出码 1 结束。
- `--status`：只打印状态，不建库。

状态输出：MySQL 按 `content_type` 和 `vectorize_status` 分组的行数；Milvus 实体数（`query` 输出 `count(*)`，`consistency_level="Strong"`）。

## 9. 在线检索

### 9.1 契约（不变）

- 入参：`keyword`，1 到 20 字，描述"取用户原话中的关键词，不要替换为同义词"。
- 出参：`{"results": [{"question": str, "answer": str, "category": str}]}`。
- 工具描述、注册配置（可重试，5 秒超时）、System Prompt 都不改。

### 9.2 `search_faq(keyword) -> list[dict]`

1. `aembed_query(keyword)`。
2. Milvus `search` 集合 `knowledge`，`limit=FAQ_MAX_RESULTS`。
3. 丢弃相似度低于 `FAQ_MIN_SCORE` 的结果。
4. 按 id 从 MySQL 读 `knowledge_chunks`，只取 `done` 的行。按 Milvus 的相似度顺序排列。Milvus 有、MySQL 没有的 id 跳过。
5. 映射：`question`=`questions`，`answer`=`answer`，`category`=`category`。

- 嵌入或 Milvus 失败时，异常交给执行器：执行器返回 `tool_error`，模型如实告诉用户暂时查不到。
- 执行器的 5 秒超时包含嵌入请求和 Milvus 检索。

## 10. 测试与验证

### 10.1 离线单测（不连数据库，不调用上游）

| 模块 | 测试内容 |
|---|---|
| `chunking` | 标题路径和 3 个字段的映射；只有一级标题的小节；超长小节递归切且每块不超过上限；重叠段从完整句子开始、无句末标点时不加重叠；不同小节之间无重叠；小表整块；大表按行切且每块含表头；关键条款标记识别和删除；文档内块顺序 |
| 向量化文本 | 拼接格式 |
| `mining` 纯逻辑 | 对话文本格式化（排除工具消息）；分批和批号 |

### 10.2 数据库 + Milvus 测试（`aftersales_test` + `knowledge_test`）

- `tests/conftest.py` 的库重置增加 `db/schema_ch03.sql`，并先删除 `qa_extraction_staging`、`knowledge_chunks`。每个测试前清空这两张表，并删除重建 Milvus 测试集合。
- **Milvus 连不上时直接失败，提示"请先执行 docker compose up -d --wait"。不跳过。**
- 假嵌入：`tests/fakes.py` 新增确定性假嵌入。指定文本映射到指定的 1024 维单位向量，其余文本按哈希生成，用于控制相似度。不调用上游。

| 范围 | 测试内容 |
|---|---|
| `ingest` | 文档入库后块数、字段、指针正确，状态 `pending`；重跑跳过；`rebuild` 删除 Milvus 和 MySQL 后重建；`faq` 表导入 |
| `vectorize` | 全部 `pending` 变 `done`，`vector_id` 回填，Milvus 实体数相等；`crash_after_batches` 中断后 Milvus 比 `done` 多 1 批；重跑后 `pending` 为 0、Milvus 实体数等于行数 |
| `retrieval` / `query_faq` | 按相似度顺序返回 MySQL 正文；低于阈值的结果丢弃；`pending` 行不返回；出参结构不变 |
| `mining` | 选会话（日期过滤、跳过已抽取）；一批写入暂存表；单会话失败不影响其他会话；去重：裁定重复 → `discarded`；不重复 → 插入 `mined` 块并 `kept`；本次保留项作为候选；裁定失败保持 `extracted` |

ch02 中断言"邮费查不到"的测试（`tests/test_repositories.py`、`tests/test_tools.py`）删除或改为新行为。`tests/test_db_models.py` 中检查种子数据不含"邮"字的测试保留。

### 10.3 数据与 Prompt 验证（代替 TDD，真实上游）

- **知识文档**：用户审核 3 份 Markdown。脚本检查全文不含"邮"字。切分后人工标注 10 个块的期望字段，跑一遍核对。
- **检索评估集** `evals/retrieval_samples.jsonl`：约 20 条。每条为关键词和期望命中块的 `section_path`；另含约 5 条无关问题，期望无结果。`evals/run_retrieval_eval.py` 用真实嵌入和 Milvus 生产集合。通过标准：Top 3 命中率 ≥ 90%；无关问题无结果的比例 ≥ 80%。用它校准 `FAQ_MIN_SCORE`。
- **抽取评估集** `evals/mine_extract_samples.jsonl`：约 10 通对话，每通标注期望的问答对数量和不许出现的内容（订单号等）。
- **裁定评估集** `evals/dedup_samples.jsonl`：约 12 条（新问答对 + 候选），标注期望是否重复。通过标准：准确率 ≥ 90%。
- **工具选择评估集**：重跑 `evals/run_tool_selection_eval.py`，确认没有退化。
- 用户先审核各评估集的标注。

### 10.4 验收

`scripts/demo3.sh`（需要先启动服务、MySQL 和 Milvus）：

1. 建库与中断恢复（验收 2）：`build_kb.py --rebuild --crash-after-batches 2` → 打印状态（退出码 1，`done` = 32，Milvus 实体数 = 48）→ `build_kb.py` → 打印状态（`pending` = 0，Milvus 实体数 = MySQL 总行数）。
2. 挖掘：`seed_history.py` → `mine_qa.py` → 打印 `kept`、`discarded` 数量和新入库的问答对。
3. 语义召回（验收 1）：通过 `/chat/stream` 问「邮费是多少」→ 打印 `query_faq` 的工具结果（含运费条目）和回复（含"99 元"和"8 元"）。

## 11. 文档更新

- `CLAUDE.md`：项目状态加 ch03；常用命令加 `build_kb.py`、`mine_qa.py`、`seed_history.py`、`demo3.sh`、新评估脚本；架构表加 `app/knowledge/`；"`query_faq` 取原词、邮费查不到是预期"的约束改为向量检索的约束；`.env` 说明加 `MILVUS_URI`。
- `README` 或 `dev-notes/ch03.md` 给出 crontab 示例。

## 12. 已知限制

- 不处理两个建库或挖掘进程同时运行。只支持单实例运行。
- 没抽出问答对、或抽取失败的会话没有暂存行，每次运行同一日期时会被重新抽取（多花 LLM 调用，结果不变）。
- MySQL 为 `done`、Milvus 却没有向量的不一致（例如只清空了 Milvus 卷）不会自动发现。处理方法：`reset_db.sh` 一起清空，或 `build_kb.py --rebuild`。`mined` 块不在 `--rebuild` 范围内。
- 只有 dense 单路：精确型号、编号这类字面匹配的查询召回可能不如关键词检索。由后续章节的混合检索解决。
- `keyword` 最长 20 字，长问题只取关键词检索，语义信息比整句少。
- （code review 补记）在线检索时，嵌入请求的 SDK 超时（10 秒）和重试（2 次）落在执行器的 5 秒超时之内，SDK 超时实际不生效；执行器超时后整体重试最多 3 次，最坏情况用户等待超过 15 秒。
- （code review 补记）表头中含 `【关键条款】` 时，表头复制到每一块，每一块都会被标为关键条款。现有文档没有这种情况。
- （code review 补记）选会话时，`source_ref = CONCAT('conversation:', id)` 的 `NOT EXISTS` 没有索引可用，代价随会话数 × 暂存行数增长。演示规模没有影响。
- （code review 补记）检索评估集按 `questions` 标注期望结果，而不是 10.3 节写的 `section_path`。同一小节递归切出的多块 `questions` 相同，按 `questions` 判定可能把同名的另一块算作命中。
