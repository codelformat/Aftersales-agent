# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

电商售后智能客服（Aftersales-agent）。ch01 已完成：SSE 流式多轮对话 + 售后描述结构化提取，纯对话，无工具调用和 Agent 循环。

- 远程仓库：https://github.com/codelformat/Aftersales-agent （默认分支 `main`，GitHub CLI `gh` 已登录）
- 每章的 spec 在 `docs/superpowers/specs/`，plan 在 `docs/superpowers/plans/`，开发记录在 `dev-notes/chNN.md`。

## 常用命令

```bash
uv sync                                              # 安装依赖（Python 3.12，由 uv 管理）
uv run uvicorn app.main:app --reload --port 8000     # 启动服务
uv run pytest -q                                     # 全量单测（不访问网络）
uv run pytest tests/test_chat_api.py::test_second_turn_sees_first_turn -q   # 单个测试
uv run python evals/run_extract_eval.py              # 提取 Prompt 样例集验证（调用真实上游；未达标退出码为 1）
uv run python evals/run_chat_samples.py              # 客服 Prompt 样例，打印回复供人工检查（调用真实上游）
bash scripts/demo.sh                                 # 3 项验收演示（需先启动服务；BASE_URL 可覆盖地址）
```

没有配置 lint 工具。

## 架构

分层：`app/api`（HTTP/SSE 适配）→ `app/services`（业务流程）→ `prompts`、`context`、`session`、`llm`。

| 模块 | 职责 |
|---|---|
| `app/config.py` | `Settings` 只读 4 个 `CHAT_*` 变量；`TOKEN_BUDGET`、`CHARS_PER_TOKEN` 等是代码常量，不从环境变量读取 |
| `app/llm.py` | 创建聊天模型和提取模型（`ChatOpenAI`），提供 FastAPI 依赖 `get_chat_model`、`get_extractor` |
| `app/prompts.py` | 客服模板和提取模板（`ChatPromptTemplate`）。模板文本中不许出现变量以外的花括号 |
| `app/session.py` | 进程内会话存储，每个会话一把 `asyncio.Lock` |
| `app/context.py` | `count_tokens`（2.0 字符/token）和 `build_history`（`trim_messages` 裁剪历史，固定部分超预算抛 `BudgetExceeded`） |

必须保持的设计约束（每条都有测试或实测依据，改动前先读 spec 和 `dev-notes/ch01.md`）：

- **提取模型必须关闭思考。** `with_structured_output(method="function_calling")` 强制 tool_choice，DeepSeek 思考模式下返回 400。`json_schema` 方式 DeepSeek 不支持。
- **`CHAT_THINKING` 未设置（或为空字符串）时，不发送 `thinking` 字段**，以便切换到 GPT、Ollama 等上游。**使用 DeepSeek 时必须设置 `CHAT_THINKING`**：DeepSeek 默认开启思考，未设置时提取模型不发送 `disabled`，`/extract` 一直返回 502。
- **SSE 预检放在 `Depends` 中。** 在 yield 型 SSE 端点函数体内抛 `HTTPException`，客户端收到 200 和空流。
- **会话锁在 yield 依赖 `prepare_chat_turn` 的 `finally` 中释放**（默认 `scope="request"`，响应发送完后执行）。不要改到端点生成器中释放，否则后续依赖出错时锁泄漏。
- **SSE 事件用 `ServerSentEvent(raw_data=json.dumps(..., ensure_ascii=False))`。** 用 `data=` 会把中文转义为 `\uXXXX`。
- **只有流正常结束且回复非空才写入历史。** 上游出错、返回空回复或客户端断开时，这一轮不写入。
- **预算计数和实际发送共用 `chat_prompt_vars()`。** 给客服模板加变量时只改这个函数。
- **测试中的模型一律用 `app.dependency_overrides` 替换**（`FakeListChatModel`、`RunnableGenerator`、`RunnableLambda`）；异步测试用 `@pytest.mark.anyio` + `httpx.AsyncClient(transport=ASGITransport(app=app))`。

## 模型与环境变量（`.env`，已 gitignore）

`.env` 在项目根目录，从 `~/mewhelp-src/.env` 拷贝而来，不得提交。代码读取配置时用下列变量名，不要硬编码密钥或地址。

| 用途 | 变量 | 上游与协议（2026-10-06 已实测可用） |
|---|---|---|
| 聊天 | `CHAT_BASE_URL` / `CHAT_MODEL` / `CHAT_API_KEY` | OpenAI 兼容 `/chat/completions`；当前为 DeepSeek `deepseek-v4-flash` |
| 思考强度 | `CHAT_THINKING=adaptive` | 映射为请求体 `"thinking": {"type": "adaptive"}`（可选 `disabled`）；思考内容在 `message.reasoning_content`，不在 `content` |
| 嵌入 | `EMBED_API_KEY`（`EMBED_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/embeddings`，模型 `BAAI/bge-m3`，**1024 维**（Milvus collection 维度按此设） |
| 重排 | `RERANK_API_KEY`（`RERANK_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/rerank`，模型 `BAAI/bge-reranker-v2-m3`；Jina/Cohere 形状（`query` + `documents` → `results[].index/relevance_score`），不是 OpenAI 协议 |

- ch01 只用到聊天这一组；嵌入、重排尚未接入代码。
- 嵌入、重排的 base URL 在代码里给默认值 `https://api.siliconflow.cn/v1`，`.env` 中可覆盖。
- `.env` 只含上表 6 个变量（`CHAT_BASE_URL`、`CHAT_MODEL`、`CHAT_API_KEY`、`CHAT_THINKING`、`EMBED_API_KEY`、`RERANK_API_KEY`）。需要新配置项时，由用户告知后再加入。
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
