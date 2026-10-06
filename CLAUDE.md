# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

新建的空项目（Aftersales-agent，售后 agent）。尚无代码、构建、lint 或测试命令；技术栈落地后，在此补充常用命令（构建、lint、全量测试、运行单个测试）和整体架构说明。

- 远程仓库：https://github.com/codelformat/Aftersales-agent （默认分支 `main`，GitHub CLI `gh` 已登录）

## 模型与环境变量（`.env`，已 gitignore）

`.env` 在项目根目录，从 `~/mewhelp-src/.env` 拷贝而来，不得提交。代码读取配置时用下列变量名，不要硬编码密钥或地址。

| 用途 | 变量 | 上游与协议（2026-10-06 已实测可用） |
|---|---|---|
| 聊天 | `CHAT_BASE_URL` / `CHAT_MODEL` / `CHAT_API_KEY` | OpenAI 兼容 `/chat/completions`；当前为 DeepSeek `deepseek-v4-flash` |
| 思考强度 | `CHAT_THINKING=adaptive` | 映射为请求体 `"thinking": {"type": "adaptive"}`（可选 `disabled`）；思考内容在 `message.reasoning_content`，不在 `content` |
| 嵌入 | `EMBED_API_KEY`（`EMBED_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/embeddings`，模型 `BAAI/bge-m3`，**1024 维**（Milvus collection 维度按此设） |
| 重排 | `RERANK_API_KEY`（`RERANK_BASE_URL` 默认 `https://api.siliconflow.cn/v1`） | 硅基流动 `/rerank`，模型 `BAAI/bge-reranker-v2-m3`；Jina/Cohere 形状（`query` + `documents` → `results[].index/relevance_score`），不是 OpenAI 协议 |

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
    "<任务描述>" < /dev/null
  ```

- **任务描述要自包含**：Codex 看不到对话上下文。写清目标、涉及文件/模块、接口约定、验收标准（要通过的测试或命令）、不许改动的范围，以及相关库的正确 API 用法（来自 Context7 的查询结果）。
- 大任务按 plan 拆成小步依次交给 Codex。每步完成后由 Claude 检查 diff、跑测试；有问题把具体问题和修改要求反馈给 Codex 重做，不自己改代码。
- **检查和测试都通过后**，Claude 再 commit 并 push 到远程仓库。

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
