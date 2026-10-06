# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

电商售后智能客服（Aftersales-agent）。
- ch01：SSE 流式多轮对话 + 售后描述结构化提取。
- ch02：Function Calling 工具链（5 个工具，单轮调用），会话和消息落 MySQL。不做 Agent Loop。
- ch03：知识库。Markdown 文档结构感知切分 + 历史对话挖掘问答对，MySQL `knowledge_chunks` 与 Milvus 集合 `knowledge` 双写；`query_faq` 内部改为 dense 向量检索（契约不变）。不做关键词召回、混合检索、重排。

- 远程仓库：https://github.com/codelformat/Aftersales-agent （默认分支 `main`，GitHub CLI `gh` 已登录）。每章在 `chNN` 分支开发，finish 时提 PR。
- 每章的 spec 在 `docs/superpowers/specs/`，plan 在 `docs/superpowers/plans/`，开发记录在 `dev-notes/chNN.md`。

## 常用命令

```bash
uv sync                                              # 安装依赖（Python 3.12，由 uv 管理）
docker compose up -d --wait                          # 启动 MySQL（宿主端口 3307）和 Milvus（19530，健康检查 9091）
bash scripts/reset_db.sh                             # 删除 MySQL 和 Milvus 数据卷并重建，校验 FAQ 中文编码和 Milvus 健康
uv run python scripts/build_kb.py                    # 离线建库：文档和 faq 表入库（pending），再向量化写 Milvus；已入库的文档跳过
uv run python scripts/build_kb.py --rebuild          # 重建全部文档来源（不含 mined）；--status 只看状态；--check 不一致时退出码 1
uv run python scripts/seed_history.py --date 2026-10-05   # 导入 30 通样例历史对话（默认昨天）
uv run python scripts/mine_qa.py --date 2026-10-05   # 挖掘任务：抽取 → 暂存 → 去重 → 入库 → 向量化（默认昨天；crontab 示例见脚本头）
uv run uvicorn app.main:app --reload --port 8000     # 启动服务；聊天页 http://127.0.0.1:8000/
uv run pytest -q                                     # 全量测试（需要 MySQL；连不上时数据库测试直接失败，不跳过）
uv run pytest tests/test_chat_api.py::test_tool_round_events_and_persistence -q   # 单个测试
uv run python evals/run_tool_selection_eval.py       # 工具选择样例集（真实上游，只执行第 1 次调用；未达标退出码 1）
uv run python evals/run_extract_eval.py              # 提取 Prompt 样例集（真实上游）
uv run python evals/run_chat_samples.py              # 客服样例，走完整生产链路并写库（真实上游 + MySQL）
uv run python evals/run_retrieval_eval.py            # 检索评估集（真实嵌入 + 生产集合；先 build_kb）
uv run python evals/run_mine_extract_eval.py         # 问答抽取 Prompt 评估（真实上游）
uv run python evals/run_dedup_eval.py                # 去重裁定 Prompt 评估（真实上游）
bash scripts/demo3.sh                                # ch03 验收（需先启动服务、MySQL 和 Milvus）
bash scripts/demo2.sh                                # ch02 三项验收（需先启动服务和 MySQL）
bash scripts/demo.sh                                 # ch01 三项验收
```

重启服务时，先确认旧进程已退出（`pgrep -f "uvicorn app.main:app"` 无输出）再启动，否则请求可能落到旧进程。没有配置 lint 工具。

## 架构

```
api → services → repositories → db
         ↘ tools (registry, executor) → repositories
                  ↘ knowledge.retrieval → knowledge.embeddings, knowledge.milvus
         ↘ llm, prompts, history, context
scripts/build_kb.py → knowledge.ingest, knowledge.vectorize
scripts/mine_qa.py  → knowledge.mining → knowledge.vectorize
```

| 模块 | 职责 |
|---|---|
| `app/config.py` | `Settings` 读 4 个 `CHAT_*`、`DATABASE_URL`、`EMBED_API_KEY`、`EMBED_BASE_URL`、`MILVUS_URI`；`test_database_url()` 推导测试库；其余为代码常量 |
| `app/db/` | 异步引擎（`get_sessionmaker` / `set_sessionmaker` / `dispose_engine`）与 6 张表的 ORM 映射 |
| `app/repositories/` | 只负责 SQL：会话、消息、工单（指数回退重试主键冲突）、知识块 `knowledge`、挖掘暂存 `staging` |
| `app/knowledge/` | `milvus`（客户端 get/set、集合定义、经 `retry_async` 重试）、`embeddings`（`OpenAIEmbeddings` 工厂）、`chunking`（Markdown 切分、`knowledge_text`）、`ingest`（文档和 faq 表入库）、`vectorize`（`vectorize_pending`、状态）、`retrieval`（在线检索）、`mining`（抽取、去重）、`history_seed` |
| `app/tools/` | 5 个 `@tool`、`mock_data`（确定性 mock）、注册表、执行器（校验、超时、重试、错误转换、截断、并行） |
| `app/services/chat.py` | 一轮对话：第 1 次调用绑定工具并流式输出 → 并行执行工具 → 第 2 次调用（不绑定工具）流式输出 → 一个事务写库 |
| `app/services/history.py` | 数据库消息 ↔ LangChain 消息 |
| `app/locks.py` | 按会话 ID 的进程内锁 |
| `app/context.py` | `count_tokens`（2.0 字符/token）和 `build_history`（裁剪历史） |

必须保持的设计约束（每条都有测试或实测依据，改动前先读 spec 和 `dev-notes/`）：

- **`db/schema.sql`、`db/schema_ch03.sql` 是用户 DDL，逐字保存，是表结构唯一来源。** ORM 只映射，不 `create_all`。
- **容器初始化 SQL 必须用 utf8mb4 读取**（`db/mysql-client.cnf` 挂到 `/etc/mysql/conf.d/`），否则中文双重编码。校验存储字节要用 `HEX()`，字符串比较会被 latin1 客户端"还原"而漏检。
- **执行 `.sql` 文件用 `exec_driver_sql`，不用 `text()`。** 异步 ORM 提交后读数据库默认值列前先 `await session.refresh()`。测试引擎用 `NullPool`（pytest 每个异步测试一个事件循环）。
- **第 2 次调用不绑定工具，并在工具结果后追加 `SystemMessage(TOOL_ROUND_CLOSING)`**（不写库）。去掉它，模型会把工具调用标记 `<｜｜DSML｜｜ ...>` 写进正文。服务端另有标记防线：命中时发 `error`、不写库。
- **`create_ticket` 不重试**（非幂等）；`conversation_id` 由执行器用 `InjectedToolArg` 注入，模型看不到。
- **`query_faq` 的入参、描述、出参不改**；内部是向量检索：关键词嵌入 → Milvus Top 3 → 丢弃低于 `FAQ_MIN_SCORE=0.50`（检索评估集校准）→ 按 id 读 MySQL 的 `done` 行。
- **Milvus 主键 = MySQL `knowledge_chunks.id`，集合只存 id + 向量，集合级 `Strong` 一致性。** 正文和元数据只在 MySQL。
- **入库只写 MySQL `pending`，统一由 `vectorize_pending()` 写 Milvus 并回填 `done`。** 按主键 `upsert`，中断后重跑不产生重复。
- **`OpenAIEmbeddings` 必须设 `check_embedding_ctx_length=False` 和 `model_kwargs={"encoding_format": "float"}`**（硅基流动不接受 token id）。
- **`knowledge_chunks` 有自引用外键，删除行之前先把 `prev_chunk_id`、`next_chunk_id` 置 NULL。**
- **测试不访问真实嵌入和生产集合**：autouse fixture 用 `FakeEmbeddings` 和 `BlockedMilvus`；需要 Milvus 的测试用 fixture `milvus`（每个测试重建 `knowledge_test`）。抽取器和裁定器用 `RunnableLambda`。
- **提取模型必须关闭思考**；**使用 DeepSeek 时必须设置 `CHAT_THINKING`**；`CHAT_THINKING` 未设置或为空时不发送 `thinking` 字段。
- **DeepSeek 思考模式下，第 2 次调用依赖服务端按 tool_call id 缓存的思考内容**（`ChatOpenAI` 不回传 `reasoning_content`）。自造 tool_call id 放在当前轮会 400。
- **SSE 预检放在 yield 依赖 `prepare_chat_turn` 中，会话锁在其 `finally` 释放**（默认 `scope="request"`）。
- **SSE 事件用 `ServerSentEvent(raw_data=json.dumps(..., ensure_ascii=False))`。**
- **一轮成功（回复非空、无工具标记）才写消息**；工单副作用独立提交，不回滚。
- **测试中的聊天模型用 `tests/fakes.py` 的 `ScriptedChatModel`**（支持 `bind_tools`、tool_call chunk、注入异常和等待）；数据库测试用 fixture `db`。

## 模型与环境变量（`.env`，已 gitignore）

`.env` 在项目根目录，从 `~/mewhelp-src/.env` 拷贝而来，不得提交。代码读取配置时用下列变量名，不要硬编码密钥或地址。

| 用途 | 变量 | 上游与协议（2026-10-06 已实测可用） |
|---|---|---|
| 聊天 | `CHAT_BASE_URL` / `CHAT_MODEL` / `CHAT_API_KEY` | OpenAI 兼容 `/chat/completions`；当前为 DeepSeek `deepseek-v4-flash` |
| 思考强度 | `CHAT_THINKING=adaptive` | 映射为请求体 `"thinking": {"type": "adaptive"}`（可选 `disabled`）；思考内容在 `message.reasoning_content`，不在 `content` |
| 嵌入 | `EMBED_API_KEY`（`EMBED_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/embeddings`，模型 `BAAI/bge-m3`，**1024 维**（Milvus collection 维度按此设） |
| 重排 | `RERANK_API_KEY`（`RERANK_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/rerank`，模型 `BAAI/bge-reranker-v2-m3`；Jina/Cohere 形状（`query` + `documents` → `results[].index/relevance_score`），不是 OpenAI 协议 |

- ch01、ch02 只用到聊天这一组和 `DATABASE_URL`；ch03 接入嵌入和 `MILVUS_URI`；重排尚未接入代码。
- `DATABASE_URL`：`mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales?charset=utf8mb4`（本项目 Docker Compose 的 MySQL）。
- 嵌入、重排的 base URL 在代码里给默认值 `https://api.siliconflow.cn/v1`，`.env` 中可覆盖。
- `MILVUS_URI`：`http://127.0.0.1:19530`（本项目 Docker Compose 的 Milvus，ch03 经用户同意加入）。
- `.env` 只含上表 6 个变量、`DATABASE_URL` 和 `MILVUS_URI`。需要新配置项时，由用户告知后再加入。
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
    -c service_tier="priority" \
    -s workspace-write \
    -c sandbox_workspace_write.network_access=true \
    -c 'sandbox_workspace_write.writable_roots=["/Users/harry/.cache/uv","/Users/harry/.local/share/uv"]' \
    "<任务描述>" < /dev/null
  ```

  后两个 `-c` 是必需的。原因：默认沙箱禁止网络，也不能写 uv 的缓存目录，`uv add` / `uv sync` 会失败。

  `service_tier="priority"` 开启 Codex 的 fast 模式（用户要求，2026-10-06）。`~/.codex/config.toml` 默认为 `default`，所以必须在命令中覆盖。

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
