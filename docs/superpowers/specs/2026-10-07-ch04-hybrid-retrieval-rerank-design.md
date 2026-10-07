# ch04 混合检索、重排与评估体系：设计规格

- 日期：2026-10-07
- 状态：待用户审阅
- 分支：`ch04`
- 前置：ch03（`docs/superpowers/specs/2026-10-06-ch03-knowledge-base-design.md`）。本文只写新增和变化的部分。没有提到的 ch03 行为保持不变。

## 1. 目标与验收标准

**目标：** 提高客服系统的检索质量和生成质量，并建立可重复的评估体系。

1. 混合检索：Milvus 原生 BM25（内置 `chinese` analyzer）和 dense 向量各召回 Top-50，用 `hybrid_search` + `RRFRanker` 融合。
2. 重排：`BAAI/bge-reranker-v2-m3` 精排出 Top-10。组装 Prompt 时把最相关的证据放在首尾。
3. 元数据过滤：按品类先过滤再检索。
4. Query 理解：LLM 把口语问法改写为标准问法，并识别品类；词表负责型号归一和同义词处理。同义词只在检索侧处理，不在入库侧拆存。
5. 生成质量控制：回答带引用编号 `[n]`；证据不足时显式拒答；模型自评知识是否足够，不足时拒答并把问题写入 `low_confidence_questions`；System Prompt 列出禁止承诺的内容。
6. 评估体系：300 题评估集，分 5 个桶、3 个难度，标注来源键。检索段评 Recall@K 和 MRR，生成段评 Faithfulness。报告对比 4 种策略，按桶和难度分组。编造个案写入 `faith_cases` 台账。
7. 前端：聊天页上点击引用编号，显示原文和章节路径；每条回答左下角有 👍/👎，只在前端采集。台账页可处置编造个案。

**验收标准：**

1. 四策略对比报告能跑出数字。
2. 问带具体型号的问题，BM25 那一路能命中该型号的章节。
3. 答案的引用编号能定位回原文；在聊天页上点击引用，能看到原文和章节路径。
4. 问知识库没有的内容，得到明确拒答，且问题写入低置信度问题池。

**本章不做：** 指代消解、多轮改写、Langfuse 接入、👍/👎 落库（`user_feedback` 入口留给 ch09）、台账页鉴权、Agent Loop。

## 2. 技术栈

| 项 | 选择 |
|---|---|
| 关键词召回 | Milvus v2.6.22 原生 BM25：`text` 字段 `enable_analyzer=True`、`analyzer_params={"type": "chinese"}`；`Function(function_type=FunctionType.BM25)` 输出到 `sparse` 字段；索引 `SPARSE_INVERTED_INDEX`，`metric_type="BM25"`（已用 Context7 核对） |
| 融合 | `AsyncMilvusClient.hybrid_search(collection_name, reqs, ranker, limit, output_fields)`；`AnnSearchRequest(data, anns_field, param, limit, filter)`；`RRFRanker(k=60)`（已用 Context7 核对，并在 pymilvus 3.0.2 上核对签名） |
| 重排 | 硅基流动 `/rerank`，模型 `BAAI/bge-reranker-v2-m3`。请求 `{model, query, documents, top_n}`，响应 `results[].index`、`results[].relevance_score`。用 `httpx.AsyncClient` 直接调用 |
| Query 理解、自评、裁判 | 聊天模型（沿用 `CHAT_*`），关闭思考，`with_structured_output(method="function_calling")`，与 ch03 的抽取器相同 |
| 其他 | 沿用 FastAPI、SQLAlchemy、LangChain、MySQL 8、Milvus |

**设计阶段实测**（一次性探测，不进仓库）：本地 Milvus 的 `run_analyzer(text, {"type": "chinese"})` 结果如下。

| 输入 | 词项 |
|---|---|
| `X3 Pro耳机只有一边有声音` | `X3`、`Pro`、`耳机`、`只有`、`一边`、`有`、`声音` |
| `x3pro 续航多久` | `x3pro`、`续航`、`多久` |
| `BT-X3 Pro和X5有什么区别` | `BT`、`X3`、`Pro`、`和`、`X5`、… |

结论：analyzer 保留字母和数字词项，但**区分大小写，并且不拆分连写的型号**。所以查询侧必须把型号改写成文档中的规范写法（§6.1）。

## 3. 配置

- `.env` 不新增变量。`RERANK_API_KEY` 已在 `.env` 中（CLAUDE.md 变量表）。
- `Settings` 新增读取：`rerank_api_key`（必填）、`rerank_base_url`（默认 `https://api.siliconflow.cn/v1`）。
- 删除常量 `FAQ_MIN_SCORE`、`FAQ_MAX_RESULTS`（由下表取代）。`DEDUP_*` 常量不变，挖掘去重仍走 dense 单路。
- 新增代码常量：

| 常量 | 值 | 用途 |
|---|---|---|
| `RERANK_MODEL` | `"BAAI/bge-reranker-v2-m3"` | 重排模型 |
| `RERANK_TIMEOUT_SECONDS` | `10` | 单次重排请求超时 |
| `RERANK_MAX_ATTEMPTS` | `3` | 重排最多尝试次数（`retry_async`） |
| `RERANK_RETRY_BASE_DELAY` | `0.5` | 重排重试基准等待（秒） |
| `RERANK_RETRY_MAX_DELAY` | `4.0` | 重排重试最大等待（秒） |
| `RECALL_LEG_LIMIT` | `50` | dense 和 BM25 每一路的召回数 |
| `RRF_K` | `60` | RRF 参数 |
| `FUSED_LIMIT` | `50` | 融合后送去重排的候选数 |
| `EVIDENCE_TOP_N` | `10` | 重排后保留的证据数；非重排策略也取前 10 |
| `RERANK_MIN_SCORE` | `0.30`（初值） | 置信度门槛。由评估集的门槛扫描校准（§8.3） |
| `QUERY_FAQ_TIMEOUT_SECONDS` | `20` | `query_faq` 的执行器超时 |
| `PRODUCT_CATEGORIES` | 蓝牙耳机、羊毛衫、扫地机器人、电动牙刷、台灯、保温杯、运动鞋、手机壳 | 品类全集，与 `mock_data.CATALOG` 一致 |
| `GENERAL_CATEGORY` | `"通用"` | 不属于任何品类的块 |
| `KNOWLEDGE_TEXT_MAX_BYTES` | `16384` | Milvus `text` 字段的 `max_length` |

## 4. 部署与数据

### 4.1 表结构

- 用户提供的 `ch04.sql` 逐字移到 `db/schema_ch04.sql`，是两张新表的唯一来源：
  - `low_confidence_questions`：低置信度问题池。
  - `faith_cases`：忠实度编造个案台账。
- `docker-compose.yml` 把它挂为 `/docker-entrypoint-initdb.d/03-schema-ch04.sql`，`seed.sql` 顺延为 `04-seed.sql`。测试库初始化同样包含这两张表（沿用 ch03 的做法）。
- `reset_db.sh` 增加检查：两张表都存在。
- ORM 增加两个映射类，不 `create_all`。读数据库默认值列前先 `await session.refresh()`。

### 4.2 Milvus 集合 `knowledge`（结构变化，需要重建）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INT64 主键，`auto_id=False` | 等于 `knowledge_chunks.id`（不变） |
| `vector` | FLOAT_VECTOR，1024 维 | `AUTOINDEX`，COSINE（不变） |
| `text` | VARCHAR，`max_length=KNOWLEDGE_TEXT_MAX_BYTES`，`enable_analyzer=True`，`analyzer_params={"type": "chinese"}` | 内容等于 `knowledge_text(category, questions, answer)`，与嵌入的输入相同 |
| `sparse` | SPARSE_FLOAT_VECTOR | BM25 Function 的输出。写入时不提供 |
| `product_category` | VARCHAR(64) | 过滤字段 |
| `content_type` | VARCHAR(16) | 过滤字段：`policy`、`faq`、`manual`、`mined` |

- 集合级 `Strong` 一致性（不变）。
- MySQL 仍是正文和元数据的权威来源。在线检索仍按 id 从 MySQL 读取 `done` 行。Milvus 中的 `text`、`product_category`、`content_type` 都是派生副本。
- `ensure_collection()`：如果集合存在但没有 `sparse` 字段，抛出错误，并提示执行 `bash scripts/reset_db.sh` 或 `uv run python scripts/build_kb.py --rebuild`。它不自动删除集合。
- `build_kb.py --rebuild` 改为先删除并重建集合，再重建全部文档来源。原因：集合结构变化后，不能只按 id 删除向量。mined 块的 MySQL 行保留，并置回 `pending`，由同一次 `vectorize_pending()` 重新写入。

### 4.3 品类推导

`product_category_of(section_path)`：按 `PATH_SEP` 拆分路径，依次检查每一段。第一个等于 `PRODUCT_CATEGORIES` 中某一项的段就是品类。没有任何一段匹配时，返回 `GENERAL_CATEGORY`。

- 例：`商品FAQ > 蓝牙耳机 > 耳机怎么连手机？` → `蓝牙耳机`；`退货政策 > 退款 > 退款时间` → `通用`；`对话挖掘 > 退换货` → `通用`。
- MySQL 不加列。`vectorize_pending()` 写 Milvus 时计算。

### 4.4 知识文档扩容

为支撑 240 道可答题，并让 4 种策略的差距可以测出，扩充知识文档：

1. 新增 `knowledge/docs/manual/商品手册.md`：8 个品类，每个品类 3 个型号，共 24 个型号。每个型号 4–5 个小节：参数、续航或容量、保修天数、退换规则、常见故障。相近型号（例如 `X3` 和 `X3 Pro`）的数字刻意不同。
   - 二级标题为品类名，三级标题为"型号 + 小节"，例如 `### X3 Pro 续航`。品类推导依赖二级标题。
   - 型号的规范写法为"字母和数字连写，修饰词用空格隔开"，例如 `X3 Pro`、`S10 Max`。全文统一使用规范写法。
2. 扩充 `退货政策.md` 和 `售后手册.md`：价保、赠品退回、部分退款、以旧换新等细则。
3. 新增 `knowledge/lexicon.json`：

```json
{
  "models": ["X3", "X3 Pro", "X5", "..."],
  "synonyms": {"运费": ["邮费", "快递费"], "退款": ["退钱", "钱退回来"], "...": []}
}
```

- `models` 是 24 个规范型号。`synonyms` 的键是标准词，值是俗称列表。
- 文档和词表由 Codex 编写，Claude 审核。

## 5. 架构

```
api/chat → services/chat ──→ tools/executor ──→ tools/faq(query_faq)
              │                                     ↓
              │                              knowledge/retrieval.retrieve(question, strategy)
              │                                 ├─ knowledge/query      (改写、型号归一、同义词)
              │                                 ├─ knowledge/milvus     (dense / bm25 / hybrid_search)
              │                                 ├─ knowledge/rerank     (/rerank)
              │                                 └─ repositories/knowledge (读 done 行)
              ├─ services/grounding  (合并编号、渲染证据、自评、入池)
              │       └─ repositories/low_confidence
              └─ prompts
api/knowledge   → repositories/knowledge       (GET /api/knowledge/chunks/{id})
api/faith_cases → repositories/faith_cases     (台账接口)
evals/run_rag_eval.py → knowledge/retrieval, services/grounding, evals/rag_metrics, repositories/faith_cases
```

| 模块 | 职责 |
|---|---|
| `app/knowledge/query.py` | `understand(question) -> QueryPlan`；`normalize_models`；`dense_query`、`bm25_query`；`load_lexicon` |
| `app/knowledge/rerank.py` | `rerank(query, documents, top_n) -> list[(index, score)]`；测试可替换客户端 |
| `app/knowledge/milvus.py` | 新集合结构；`upsert_entities`；`search_dense`、`search_bm25`、`search_hybrid`；`search_vectors` 保留给挖掘去重 |
| `app/knowledge/retrieval.py` | `retrieve(question, strategy, plan=None, *, exclude_mined=False) -> Retrieval`；`interleave` 首尾排列；`source_key` |
| `app/services/grounding.py` | `collect_evidence`（跨调用合并和编号）；`render_evidence`；`self_check`；`record_low_confidence` |
| `app/repositories/low_confidence.py` | 写入问题池 |
| `app/repositories/faith_cases.py` | upsert、列表、处置 |
| `app/api/knowledge.py`、`app/api/faith_cases.py` | 新接口 |
| `evals/rag_metrics.py` | Recall@K、MRR、分组汇总（纯函数，可单测） |

## 6. 检索管线

`retrieve(question, strategy)` 按顺序执行 5 步。线上固定使用 `hybrid_rerank`。

### 6.1 Query 理解

1. **LLM 改写**：`understand(question)` 做一次结构化调用（关闭思考），输出：

   ```python
   class QueryPlan(BaseModel):
       standard_query: str                       # 标准问法，保留型号和数字
       product_category: Literal[<8 个品类>] | None
   ```

   Prompt 要求：把口语、情绪化表达改写为一句标准问法；保留原文中的型号、数字和限定条件；不补充原文没有的信息；只有原文明确涉及某个品类或该品类的型号时，才填 `product_category`。
2. **型号归一**：`normalize_models(text)`。对 `lexicon.models` 中的每个型号，构造"忽略大小写、型号内空格和横线可有可无"的正则，按型号长度从长到短匹配，替换为规范写法。例：`x3pro`、`X3-PRO`、`x3 pro` → `X3 Pro`。作用于 `standard_query`。
3. **同义词**：
   - `dense_query(q)`：把俗称**替换**为标准词。
   - `bm25_query(q)`：保留原文，在末尾**追加**命中键的全部同义词和标准词，用空格分隔。
   - 理由：向量表示整句语义，追加词会让查询向量偏离；BM25 按词项打分，追加词只增加命中机会。
4. **改写失败**（超时、上游错误、解析错误）：`standard_query` 取原话，`product_category=None`，记日志，继续检索。型号归一和同义词照常执行。

### 6.2 召回

- **过滤表达式**：
  - 有品类时：`product_category in ["<品类>", "通用"]`。原因：通用政策也必须能召回。
  - `exclude_mined=True` 时（只在评估中使用）：追加 `content_type != "mined"`。
  - 两个条件同时存在时，用 `and` 连接。没有条件时为空串。
- **4 种策略**：

| 策略 | 做法 | 排序依据 |
|---|---|---|
| `dense` | `search` `vector`，limit `EVIDENCE_TOP_N` | COSINE |
| `bm25` | `search` `sparse`，`data=[bm25_query]`，limit `EVIDENCE_TOP_N` | BM25 |
| `hybrid` | `hybrid_search`：dense 一路和 BM25 一路各 limit `RECALL_LEG_LIMIT`，同一过滤，`RRFRanker(RRF_K)`，limit `EVIDENCE_TOP_N` | RRF |
| `hybrid_rerank` | 同 `hybrid`，但融合 limit `FUSED_LIMIT`，然后重排 | 重排分数 |

- 所有 Milvus 调用经 `retry_async` 重试（沿用 ch03 的 `_call`）。

### 6.3 重排（仅 `hybrid_rerank`）

1. 按候选 id 从 MySQL 读 `done` 行。Milvus 有、MySQL 没有（或仍为 `pending`）的 id 跳过（沿用 ch03）。
2. documents 用 `knowledge_text(...)`。query 用 `standard_query`，不用追加了同义词的版本。
3. 调用 `/rerank`，`top_n=EVIDENCE_TOP_N`。失败时经 `retry_async` 重试，重试条件为 `httpx.TransportError`、`httpx.TimeoutException`、HTTP 429 和 5xx。
4. 重试用尽后抛出异常，`query_faq` 失败，执行器返回 `ok=false`。模型如实告诉用户暂时查不到。理由：重排分数就是置信度门槛，没有它就无法判断证据是否可信。

### 6.4 置信度门槛（仅 `hybrid_rerank`）

丢弃 `relevance_score < RERANK_MIN_SCORE` 的结果。门槛后为空时，证据集为空，后续进入拒答（§7.3）。

### 6.5 首尾排列

`interleave(ranked)`：输入按分数降序，输出位置为：第 1 名放在首位，第 2 名放在末位，第 3 名放在第 2 位，第 4 名放在倒数第 2 位，依次向中间填。例：排名 `[1,2,3,4,5]` → 位置 `[1,3,5,4,2]`。

- 引用编号 `[n]` 等于排列后的位置序号（从 1 开始）。
- 4 种策略都做首尾排列。评估指标按排列前的排名计算（§8.2）。

### 6.6 返回值

```python
@dataclass
class EvidenceItem:
    chunk_id: int
    section_path: str
    question: str      # knowledge_chunks.questions
    answer: str
    score: float       # 该策略的排序分数

@dataclass
class Retrieval:
    plan: QueryPlan
    ranked: list[EvidenceItem]   # 门槛后、排列前，按分数降序
    evidence: list[EvidenceItem] # 首尾排列后
```

## 7. 生成质量控制

### 7.1 `query_faq` 新契约

- **入参**：`question: str`，长度 1–200。描述："用户关于店铺政策、商品型号参数或使用问题的原话，可以去掉订单号等个人信息，不要改写"。
- **工具描述**："查询店铺知识库，例如退换货政策、运费、发票、维修流程、商品型号的参数和常见故障。"
- **工具返回值**（Python 对象，执行器不直接展示给模型）：

  ```python
  {"evidence": [{"chunk_id", "section_path", "question", "answer", "score"}, ...]}
  ```

  列表顺序为首尾排列后的顺序。
- **执行器改动**：`ToolOutcome` 增加 `data` 字段，保存工具成功时的原始返回值，失败时为 `None`。`query_faq` 的 `ToolSpec` 设 `retryable=False`、`timeout=QUERY_FAQ_TIMEOUT_SECONDS`。理由：嵌入、Milvus、重排、改写各自已有指数回退重试，外层再重试会放大等待时间。

### 7.2 证据合并与渲染（`services/grounding.py`）

工具执行完后，聊天服务处理本轮所有 `ok=true` 的 `query_faq` 结果：

1. `collect_evidence(outcomes)`：按工具调用顺序拼接证据；`chunk_id` 重复时只保留第一次出现的那条；按拼接顺序重新编号为 `1..N`。
2. `render_evidence(items)` 生成模型看到的工具消息内容：

   ```json
   {"ok": true, "data": {"evidence": [{"n": 1, "section_path": "...", "content": "问：...\n答：..."}]}}
   ```

   模型看不到 `chunk_id` 和分数。每个 `query_faq` 调用的工具消息只包含它自己的证据，但编号使用全局编号。
3. 渲染后的内容替换 `ToolOutcome.message.content`，不受 `TOOL_RESULT_MAX_CHARS` 截断。10 条证据最多约 5000 字。
4. 引用列表 `citations = [{n, chunk_id, section_path, question, answer}]`。格式与 `faith_cases.citations` 相同。

### 7.3 自评

- **时机**：工具执行完、第 2 次调用之前。只在本轮至少有一个 `ok=true` 的 `query_faq` 时执行。一轮最多自评 1 次。
- **输入**：本轮所有 `query_faq` 的 `question` 参数（换行拼接）和合并后的证据。不使用整句用户输入。理由：混合问句中，只有一部分归知识库回答。
- **输出**：

  ```python
  class SelfCheck(BaseModel):
      useful: bool   # 证据是否足以回答问题的全部要点
      reason: str    # 判断理由；useful=false 时写缺了什么
  ```

  Prompt 要求：只有证据直接写明了问题所需的事实时，才判 `useful=true`；证据只是同一话题但没写到所问的点，判 `false`；问题要求承诺（例如"一定""保证"）而证据只给出一般规则时，判 `true`，由回答阶段按"禁止承诺"清单处理。
- **证据为空**：不调用 LLM，直接得到 `useful=false`，`reason="检索证据低于置信度门槛"`。
- **自评调用失败**：按 `useful=true` 处理，记日志，不入池。理由：证据已经通过重排门槛。

### 7.4 `useful=false` 的处理

1. **入池**：写一行 `low_confidence_questions`，字段如下。这一步在独立事务中提交，与工单副作用相同，不受本轮成败影响。入池失败只记日志，不中断本轮。

   | 字段 | 值 |
   |---|---|
   | `conversation_id` | 本轮会话 |
   | `raw_question` | 本轮用户原话 `turn.user_input` |
   | `source` | `self_check` |
   | `reason` | 自评理由 |

2. **替换证据**：本轮每个 `query_faq` 的工具消息内容替换为：

   ```json
   {"ok": true, "data": {"evidence": [], "answerable": false}}
   ```

   模型看不到不足的证据。写库时存替换后的版本，即模型实际看到的内容。
3. 发送 SSE 事件 `citations`，`{"items": [], "refused": true}`。
4. 第 2 次调用照常进行。同一轮中其他工具（例如订单）的结果照常回答。

**说明：** 本章只按自评结果入池（DDL 注释："本章只靠 useful 自评判入池"）。证据为空也视为自评的确定性结论，所以写 `self_check`，`reason` 写明原因。`retrieval_low_conf` 和 `user_feedback` 本章不写入。

### 7.5 `useful=true` 的处理

1. 发送 SSE 事件 `citations`，`{"items": citations, "refused": false}`。
2. 第 2 次调用使用渲染后的证据。
3. 回复完成后，服务端解析正文中的 `[n]`。越界编号只记日志，不拦截。

### 7.6 SSE 事件顺序

`session` → (`token`…) → `tool_start` → `tool_end` → `citations`（仅当本轮调用了 `query_faq` 且至少一个成功）→ `token`… → `done` 或 `error`。

### 7.7 System Prompt 变更

1. **工具使用第 3 条**改为："查询知识库时，`question` 填用户关于这一点的原话，不要改写，不要替换为同义词。"
2. **新增"引用"一节**：
   1. 使用知识库证据的句子，在句末标注证据编号，例如"签收后 7 天内可以无理由退货[2]。"
   2. 只标注实际用到的编号。一句用到多条证据时，写成 `[1][3]`。
   3. 不编造编号。没有使用知识库的句子不标注。
3. **新增"拒答"一节**：知识库结果中 `answerable` 为 `false`，或证据没有写到用户所问的点时，以"抱歉，这个问题我没有在知识库中找到可靠依据。"开头，然后建议用户转人工。不根据常识推测。
4. **"禁止承诺"清单**取代现有"行为约束"第 2 条：
   1. 不承诺退款到账的具体日期，也不说"保证到账""马上到账"。转述知识库写明的时限时，说"一般……，以支付渠道实际到账为准"。
   2. 不承诺退货、换货、维修、开票等申请一定审核通过。
   3. 不承诺赔偿、补偿、优惠券或额外退款金额。
   4. 不承诺具体的发货或送达时间。
   5. 不承诺保修范围外免费维修，也不承诺维修结果。
5. 回复字数上限从 200 字放宽到 300 字。理由：引用编号占用字数。
6. `TOOL_ROUND_CLOSING` 不变。

### 7.8 历史消息

数据库中已有的 ch03 工具消息（`keyword` 入参、旧结果格式）照常回放，不迁移。模型只把它们当作历史上下文。

## 8. 评估体系

### 8.1 评估集 `evals/rag_eval.jsonl`

- 300 题。每行：

  ```json
  {"id": "A01", "bucket": "A_policy", "difficulty": "easy", "query": "...", "relevant": ["退货政策 > 退货条件 > 无理由退货"]}
  ```

- 分桶与难度：

| 桶 | 题数 | easy / medium / hard | 内容 |
|---|---|---|---|
| `A_policy` | 70 | 25 / 25 / 20 | 标准问法的政策和流程题 |
| `B_model` | 60 | 20 / 20 / 20 | 带型号的题，含相近型号干扰和非规范写法（`x3pro`） |
| `C_colloquial` | 60 | 15 / 25 / 20 | 口语、俗称、情绪化问法；吸收 ch03 `retrieval_samples.jsonl` 的相关样例 |
| `D_unanswerable` | 60 | 20 / 20 / 20 | 知识库没有答案，`relevant` 为空列表 |
| `E_multi` | 50 | 10 / 20 / 20 | 需要 2 个及以上来源键才能答全 |

- D 桶分 4 类，每类 15 题：不存在的型号；存在的型号但问知识库没写的属性；听起来相关但知识库没写的业务（例如增值税专用发票）；需要承诺才能回答的问题（例如"明天一定能到账吗"）。
- 难度定义：easy 为问法与标题接近；medium 为换了说法或带一个限定条件；hard 为口语重、带干扰信息，或需要区分相近型号。
- **来源键** `source_key(chunk)`：
  - `content_type` 为 `policy`、`manual` 的文档块，以及 `faq` 文档块：`section_path`。
  - `常见问答` 行（`section_path` 以 `常见问答 > ` 开头）：`section_path + " > " + questions`。原因：同一分类下有多行。
  - 表格被切成多块时，多块共享同一个来源键，命中任意一块都算命中。
- 评估集由 Codex 编写，Claude 逐桶审核，并抽查至少 10% 的标注。
- **启动检查**：评估脚本先检查每个来源键都能在 MySQL `done` 行中找到，题号唯一，桶和难度的计数与上表一致。任一项不满足，退出码为 1。

### 8.2 检索段（`evals/run_rag_eval.py --stage retrieval`）

1. 每题调用 1 次 `understand()`，结果缓存，4 种策略共用。这样差异只来自检索方式。
2. 每种策略都用 `exclude_mined=True`，评估结果不受挖掘任务影响。
3. 指标（只对 A、B、C、E 桶计算）：
   - Recall@K（K = 1、3、5、10）：Top-K 中命中的来源键数 ÷ 该题的来源键数。
   - MRR：Top-10 中第 1 个相关结果排名的倒数。没有相关结果时为 0。
   - 排名用 `Retrieval.ranked`（排列前）。去重按来源键：同一来源键的多块只按排名最前的那块计算。
4. `hybrid_rerank` 的指标在门槛**之前**计算。另外单独报告门槛之后的结果。
5. **门槛扫描**：对 `hybrid_rerank`，在 0.05 到 0.80 之间每隔 0.05 取一个门槛，分别输出：
   - A/B/C/E 的 Top-1 保留率：第 1 名相关且分数不低于门槛的比例；
   - D 的门槛拒答率：门槛后为空的比例。
   - **校准规则**：选 A/B/C/E 保留率不低于 95% 的最高门槛，写入 `RERANK_MIN_SCORE`。

### 8.3 生成段（`--stage generation`）

1. 默认只跑 `hybrid_rerank`。`--gen-strategies all` 跑全部 4 种。
2. 每题流程：`retrieve` → `collect_evidence` / `render_evidence` → `self_check` → 按线上 System Prompt 和工具轮消息构造第 2 次调用，生成答案。工具轮消息由 `AIMessage`（一个 `query_faq` 调用）、工具消息和 `SystemMessage(TOOL_ROUND_CLOSING)` 组成。评估不写 `low_confidence_questions`。
3. **拒答判定**：答案以拒答句开头。
4. **裁判**（只对未拒答的答案）：一次结构化调用，关闭思考。

   ```python
   class FaithVerdict(BaseModel):
       faithful: bool
       unsupported_claims: list[str]  # 证据中找不到依据的句子
       reason: str
   ```

   输入为证据全集（带编号）和答案。Prompt 要求：逐句检查答案中的事实陈述，证据中找不到依据的句子列入 `unsupported_claims`；礼貌用语、转人工建议不算事实陈述。
5. **指标**：
   - Faithfulness = 未拒答答案中 `faithful=true` 的比例（A、B、C、E 桶）。
   - 误拒率 = A、B、C、E 中拒答的比例。
   - D 正确拒答率 = D 中拒答的比例。D 中未拒答的题列入报告，不进 `faith_cases`。
6. **`faith_cases` 写入**（只为 `hybrid_rerank`，桶为 A、B、C、E）：`faithful=false` 时按 `eval_id` upsert：
   - 新题：插入一行，`seen_count=1`，`status='未解决'`。
   - 已有行：`seen_count` 加 1；`answer`、`reason`、`citations`、`judge_model`、`query`、`bucket` 更新为本次；`last_seen_at` 更新为当前时间。
   - 已有行的状态为 `已解决` 或 `无需解决`：状态退回 `未解决`，`resolution` 置 NULL，`resolved_at` 保留。
   - `reason` 写裁判的 `reason` 加 `unsupported_claims`；`citations` 写证据全集；`judge_model` 写 `CHAT_MODEL`。
7. **并发与子集**：`--concurrency`（默认 8）、`--limit N`、`--bucket A_policy`。

### 8.4 裁判自检

- `evals/faith_judge_samples.jsonl`：约 30 条人工标注样例，每条为 `{evidence, answer, faithful}`，忠实和编造各占一半。编造样例包括改数字、加证据外的条件和张冠李戴（相近型号）。
- `evals/run_faith_judge_eval.py`：输出裁判准确率。低于 90% 时退出码为 1。

### 8.5 报告

控制台打印表格，同时写入 `evals/reports/rag_eval_<YYYYmmdd-HHMMSS>.md`。报告包含：

1. 策略 × 桶：Recall@1/3/5/10、MRR。
2. 策略 × 难度：同上。
3. 门槛扫描表和当前 `RERANK_MIN_SCORE`。
4. 生成段：每个策略的 Faithfulness、误拒率、D 正确拒答率；编造个案和 D 误答清单。

finish 时提交一份完整报告作为交付物。

## 9. 接口与前端

### 9.1 新接口（TDD）

| 方法与路径 | 说明 |
|---|---|
| `GET /api/knowledge/chunks/{id}` | 返回 `{id, section_path, content_type, questions, answer, prev_chunk_id, next_chunk_id}`。块不存在或不是 `done` 时返回 404 |
| `GET /api/faith-cases?status=未解决` | `status` 可选。按 `last_seen_at` 降序返回个案列表。每条附 `cited`：从 `answer` 解析出的引用编号 |
| `POST /api/faith-cases/{id}/resolve` | 请求体 `{status: "已解决"|"无需解决", resolution}`。`resolution` 去空白后为空或超过 300 字时返回 422。成功时写 `status`、`resolution`，`resolved_at` 设为当前时间。id 不存在时返回 404 |
| `GET /admin/faith-cases` | 返回台账页 HTML |

### 9.2 聊天页（Vibe Coding）

1. 收到 `citations` 事件后，把正文中的 `[n]` 渲染为可点击角标。正文流式到达时同样处理。
2. 点击角标后弹出卡片，显示章节路径和原文（问题和答案）。
3. 卡片有"上一段 / 下一段"按钮，经 `prev_chunk_id` / `next_chunk_id` 调用 `GET /api/knowledge/chunks/{id}` 翻看上下文。指针为空时隐藏对应按钮。
4. 每条回答左下角显示 👍/👎。点击后高亮所选按钮，显示"已反馈"，两个按钮都锁定。记录写入 `localStorage` 键 `aftersales_feedback`，格式 `{session_id, answer_index, rating, at}`。读写包在 `try/catch` 中。不调用后端。

### 9.3 台账页 `/admin/faith-cases`（Vibe Coding）

1. 按状态筛选。每行显示题号、桶、问题、`seen_count`、`last_seen_at`。`resolved_at` 非空且状态为 `未解决` 的行显示「复发」标签。
2. 展开一行，显示答案、裁判理由和证据全集，其中被引用的证据高亮。
3. 每行有「已解决」「无需解决」两个按钮。点击后弹出输入框，必须填写处置说明，然后调用 resolve 接口。

## 10. 错误处理

| 情况 | 行为 |
|---|---|
| 改写调用失败 | 用原话检索，不加品类过滤，记日志 |
| 嵌入或 Milvus 重试用尽 | `query_faq` 失败，`ok=false`（沿用 ch03） |
| 重排重试用尽 | `query_faq` 失败，`ok=false` |
| `query_faq` 超过 20 秒 | 执行器返回 `timeout` |
| 门槛后证据为空 | 自评直接判 `useful=false`，入池，拒答 |
| 自评调用失败 | 按 `useful=true` 处理，记日志 |
| 入池写库失败 | 记日志，本轮继续 |
| 回复中有越界编号 | 记日志，不拦截 |
| 旧结构的 Milvus 集合 | `ensure_collection()` 报错并提示重建命令 |

## 11. 测试

- **单测**：
  - `query`：型号归一（大小写、连写、横线、长型号优先）、`dense_query` 替换、`bm25_query` 追加、改写失败时的回退。
  - `product_category_of`、`interleave`、`source_key`、`collect_evidence`（跨调用编号、去重）、`render_evidence`。
  - `evals/rag_metrics.py`：Recall@K、MRR、按来源键去重、分组汇总。
  - `rerank`：用 `httpx.MockTransport` 测请求格式、结果解析、429 和 5xx 重试、4xx 不重试。
  - 仓库（fixture `db`）：问题池写入；`faith_cases` 插入、累计、复发退回、`resolution` 清空。
  - 接口：chunk 接口 200 和 404；台账列表和 resolve 的 200、404、422。
  - 聊天服务（`ScriptedChatModel` + 替换后的检索与自评）：`useful=true` 时发 `citations` 且工具消息为渲染后的内容；`useful=false` 时入池、替换证据、发 `refused=true`；自评失败时按通过处理；重排失败时工具 `ok=false`；跨两次 `query_faq` 的编号连续。
  - 执行器：`ToolOutcome.data` 在成功时为原始返回值；`query_faq` 不重试，超时为 20 秒。
- **Milvus 测试**（fixture `milvus`，按新结构重建 `knowledge_test`）：真实 BM25 下，型号查询 `X3 Pro` 命中含该型号的块；品类过滤排除其他品类，并保留 `通用`；`exclude_mined` 生效；旧结构集合触发报错。
- **测试隔离**：autouse fixture 用 `BlockedReranker` 拦截真实重排，与 `FakeEmbeddings`、`BlockedMilvus` 并列。改写器、自评器、裁判在测试中用 `RunnableLambda`。
- **纯 Prompt 与数据任务**（用评估集验证，不写单测）：改写 Prompt 看检索段报告；自评 Prompt 看 D 正确拒答率和误拒率；裁判 Prompt 看 `run_faith_judge_eval.py`；知识文档、词表和评估集看启动检查和人工抽查。

## 12. 验收脚本 `scripts/demo4.sh`

前置：MySQL、Milvus 已启动，已执行 `build_kb.py --rebuild`，服务已启动。

1. 运行 `uv run python evals/run_rag_eval.py --stage retrieval`，输出四策略对比表（验收 1）。
2. 用 `bm25` 策略检索"X3 Pro 续航多久"，检查 Top-3 中有 `section_path` 含 `X3 Pro` 的块（验收 2）。
3. 发一条聊天"X3 Pro 耳机充满电能用多久？"，检查回复含 `[n]`；用 `citations` 事件中第 1 条的 `chunk_id` 调用 `GET /api/knowledge/chunks/{id}`，返回的 `section_path` 与事件中一致（验收 3 的接口部分；页面部分人工点击确认）。
4. 记录 `low_confidence_questions` 行数；发一条聊天"你们的 X9 耳机支持无线充电吗？"；检查回复以拒答句开头、`citations` 事件 `refused=true`、行数加 1（验收 4）。

## 13. 文档更新

- CLAUDE.md：项目状态加 ch04；常用命令加 `run_rag_eval.py`、`run_faith_judge_eval.py`、`demo4.sh`，删除 `run_retrieval_eval.py`；架构表加新模块；设计约束中：
  - "集合只存 id + 向量"改为 §4.2 的 6 个字段，正文权威来源仍是 MySQL；
  - "`query_faq` 契约不改"改为 §7.1 的新契约；
  - 加入：型号规范写法、首尾排列与编号、自评失败按通过处理、`useful=false` 时替换证据。
- `evals/run_retrieval_eval.py` 和 `evals/retrieval_samples.jsonl` 删除，样例并入 C 桶。
- `evals/tool_selection_samples.jsonl`、`run_tool_selection_eval.py` 按 `query_faq` 新入参更新。

## 14. 已知限制

1. 裁判和被测模型都是 DeepSeek，裁判存在自我偏好。
2. 评估集由同一团队编写，与知识文档的措辞可能偏近，线上真实问法的分数会更低。
3. 品类由路径推导。文档结构不按"二级标题为品类"编写时，品类为 `通用`。
4. 台账页和接口不鉴权，只供本地使用。
5. 👍/👎 只存在浏览器 `localStorage` 中，换浏览器或清除数据后丢失。
6. 服务端不拦截越界的引用编号。
7. 一轮最多自评 1 次。一轮中多次 `query_faq` 时，任何一部分证据不足，整轮的知识库证据都被替换。
