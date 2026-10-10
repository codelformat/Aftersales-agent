# ch10 作品展示前端：设计规格

- 日期：2026-10-09
- 状态：用户已审阅通过（2026-10-09）
- 分支：`ch10`（从 `main` 拉出，`main` 含 ch09）
- 前置：ch09（`docs/superpowers/specs/2026-10-09-ch09-observability-flywheel-design.md`）。后端行为除本文列出的增量外保持不变。
- 后续：README 与作品集接入另起一份 spec（依赖本章的截图和 Pages 地址）。

## 1. 目标与验收标准

**背景：** 仓库已公开，要成为作者求职（LLM / Agent 应用工程）的代表作。读者分三类：HR（30 秒）、面试官（5 分钟）、工程师（本地运行）。

**目标：** 用一套 React 前端替换现有 4 个独立 HTML 页面。同一套界面接两种数据源：本地连真实后端，GitHub Pages 上回放录制的真实会话。把系统内部的决策过程（指代消解、意图、检索、置信度闸、工具、token）在界面上展示出来。视觉克制，像真实电商客服工作台，不做炫技效果。

**验收标准：**

1. 本地 `uv run uvicorn app.main:app` 后，`http://127.0.0.1:8000/` 打开新工作台；客户视角下对话、订单选择、退款单、工单预览、转人工、👍/👎 全部可用，行为与 ch09 一致。
2. 切到工程视角后，每轮对话在透视面板中逐节点展示：节点瀑布、指代消解差异、意图与置信度、检索 Top-5 与分数、置信度闸的三段堆叠条与门槛线、工具调用（含重试、耗时、审计状态）、token 和上下文预算。
3. 运营台（待审队列、评估趋势与四策略对比、编造台账、工具审计）在新前端中可用；旧路径 `/admin/*` 重定向到新路由。
4. GitHub Pages 上的回放站点不依赖任何后端：首页是场景库，7 个场景都能播放、暂停、拖动、变速，支持深链接；页脚标明录制日期、commit 和模型。
5. `cp .env.example .env`、填 3 组密钥、`docker compose --profile full up` 三步后，系统在一台干净机器上可用（含建库）。
6. CI：后端全量 pytest、前端类型检查 + Vitest + 构建 + Playwright（跑回放版）、Pages 自动部署，三条 workflow 都通过。

**本章不做：** 登录与权限；移动端适配（只保证窄屏不崩）；界面国际化（界面中文，仅回放剧场解说中英切换）；公网在线部署；README 与作品集（下一份 spec）。

## 2. 技术栈（用户裁定）

| 项 | 选择 | 理由 |
|---|---|---|
| 前端框架 | React + TypeScript + Vite | 作品集工具箱已列 TS/React；类型化事件协议；静态产物同时服务本地和 Pages |
| 路由 | hash 路由 | GitHub Pages 无服务端重写 |
| 样式 | CSS Modules + 设计令牌（CSS 变量） | 体积小、外观不模板化 |
| 无障碍组件 | Radix UI primitives（Dialog、Tooltip、Tabs、Toggle） | 键盘与读屏支持 |
| 图表 | 手写 SVG | 不引图表库 |
| 测试 | Vitest + Testing Library；Playwright | 单元与端到端 |
| 演示方式 | 回放剧场（Pages）+ 一键本地运行（Docker Compose） | 无服务器费用、不会被滥用 |

前端框架不在 CLAUDE.md「定死」的技术选型清单内，属于新增选型，用户已拍板。后端选型不变。

## 3. 仓库结构与构建

- `web/`：Vite 工程。`VITE_DATA_SOURCE=live|replay` 在构建时选择数据源。
  - `live` 构建输出到 `app/web/dist`，FastAPI 用 `StaticFiles` 提供，`/` 返回 `index.html`。
  - `replay` 构建由 GitHub Actions 部署到 Pages。
- `web/public/replays/<scene>.jsonl`：录制的场景；`web/public/snapshots/*.json`：运营台快照。均提交进仓库。
- 旧页面 `app/web/*.html` 迁移完成后删除；`/admin/review-queue`、`/admin/eval-runs`、`/admin/faith-cases` 返回 307 到对应 hash 路由。
- `app/web/dist` 不提交；本地运行前执行 `npm --prefix web run build`（Docker 镜像在构建阶段完成）。未构建时访问 `/` 返回一页说明如何构建。

## 4. 页面与信息架构

| 路由 | 页面 | live | replay |
|---|---|---|---|
| `#/` | 客服工作台：左会话列表 + 订单卡片 / 中对话 / 右透视面板 | 真实 SSE | 重定向到 `#/theater` |
| `#/desk` | 客服工作台 | 同上 | 选择一段录制会话回放 |
| `#/ops/review` | 待审队列 | 真实 API | 快照，通过和驳回按钮禁用并提示「本地运行可操作」 |
| `#/ops/evals` | 评估趋势 + 四策略对比 | 真实 API | 快照 |
| `#/ops/faith` | 编造台账 | 真实 API | 快照 |
| `#/ops/tools` | 工具审计 | 真实 API（新增） | 快照 |
| `#/theater` | 场景库 | 可用 | 首页 |
| `#/theater/:scene` | 场景播放器 | 可用 | 同左 |

- 顶栏：品牌名（示例商城客服）、工作台 / 运营台 / 回放剧场三个入口、客户视角 ⇄ 工程视角切换（只在工作台和播放器出现）。
- replay 模式顶部一条横幅：「这是录制的真实会话回放 · 在本地运行完整系统 →」，链接到 README 的本地运行章节。

## 5. 事件协议

### 5.1 业务事件（不变）

`session`、`understood`、`token`、`tool_start`、`tool_end`、`citations`、`actions`、`order_picker`、`ticket_preview`、`error`、`done`（含 `message_id`）。字段保持 ch09 现状。客户视角只消费这些。

### 5.2 调试通道（新增）

- `ChatRequest`、`ResumeRequest` 新增 `debug: bool = False`。`GraphContext` 新增 `debug: bool = False`。
- `debug` 为真时，后端另发事件名 `trace`，数据 `{kind, node, t_ms, data}`。`t_ms` 为相对本轮开始的毫秒数。为假时不发，行为与 ch09 完全一致。

| kind | data | 发出位置 |
|---|---|---|
| `node_start` / `node_end` | `{node}` / `{node, ms}` | `events.enter` 及节点返回时（统一封装，节点代码不逐个改） |
| `resolve` | `{original, resolved_input, standard_query, order_id, order_scoped, ticket_request, status_query, history_recall}` | `resolve_reference` |
| `intent` | `{intent, confidence, route, escalated}` | `classify_intent` |
| `retrieval` | `{queries, top: [快照条目], kept}` | `retrieve`、`retrieve_multi` |
| `gate` | `{passed, confidence, signals, weights, threshold, source, reason, self_check}` | `confidence_gate` |
| `tool` | `{call_id, name, source, mcp_server, status, retry_count, duration_ms, error_message}` | 执行引擎写审计记录处（同一份 `AuditRecord`） |
| `llm` | `{node, model, input_tokens, output_tokens, cache_read_tokens, reasoning_tokens, ms}` | 每请求一个回调收集器（LangChain callback），挂在本轮 config 上 |
| `context` | `{layer1_tokens, layer1_budget, layer2_tokens, layer2_budget, summary_triggered}` | `agent_model` 组装上下文时、`finalize` 维护后 |

- 不发 Prompt 原文、密钥、Langfuse 地址。快照条目沿用 ch09 `retrieved_chunks` 的格式。
- `llm` 事件的收集器与 Langfuse 回调并存；它把用量放进一个本轮队列，由 `stream_graph` 合并发出，避免在回调线程中调用 `get_stream_writer`。

### 5.3 契约

- `web/src/protocol/events.schema.json`（JSON Schema）定义全部业务事件和 `trace` 事件。
- TypeScript 类型与 schema 一一对应（手写判别联合，加一个 Vitest 断言二者字段集合一致）。
- pytest：调试模式下的端到端测试收集到的全部事件，逐条通过 schema 校验。
- Vitest：每个录制文件逐条通过 schema 校验，且 reducer 能从头回放到尾不抛异常。

## 6. 数据源与状态

```ts
interface DataSource {
  mode: "live" | "replay"
  chat(req: ChatRequest): AsyncIterable<ChatEvent>
  resume(req: ResumeRequest): AsyncIterable<ChatEvent>
  conversations(userId: string): Promise<ConversationSummary[]>
  messages(conversationId: number): Promise<StoredMessage[]>
  feedback(req: FeedbackRequest): Promise<void>
  refunds / tickets: 提交接口
  review: { list, detail, approve, reject }
  evalRuns(): Promise<EvalRun[]>
  strategyComparison(): Promise<StrategyComparison>
  faithCases(): Promise<FaithCase[]>
  toolAudit(limit: number): Promise<ToolAuditRow[]>
}
```

- `LiveDataSource`：`fetch` 读 SSE（`POST` 请求，不用 `EventSource`），解析为事件；其余调用现有 REST 接口。
- `ReplayDataSource`：读 `replays/*.jsonl` 和 `snapshots/*.json`；写操作返回「回放模式不可用」。
- **界面状态 = `reduce(事件序列)`，纯函数。** live 与 replay 共用同一个 reducer。回放拖动 = 对事件 0..t 重新 reduce。
- 回放时钟：播放、暂停、拖动、0.5×–4× 变速；按原始相对时间戳发事件。相邻事件间隔超过 3 秒时压缩为 1.5 秒，并在时间轴标记「⏩ 实际 N s」。

## 7. 客服工作台

- 左栏：会话列表（`GET /api/conversations`）；当前会话有订单时显示订单卡片。
- 中栏：对话。业务卡片：订单选择、退款单表单、工单预览（确认 / 取消）、转人工和建工单按钮、引用卡片、👍/👎（只在 `done` 带 `message_id` 时出现）。回复中的 `[n]` 悬停显示证据原文。
- 右栏：透视面板（第 8 节），客户视角下隐藏。
- 视角切换写入 `localStorage`；工程视角发送 `debug: true`。

## 8. 透视面板

- 宽 380 px，可折叠。每轮一条节点瀑布：状态点（进行中 / 完成 / 跳过 / 失败）、中文名 + 代码名、按本轮总时长比例的耗时条。点开节点看详情卡：

| 节点 | 详情 |
|---|---|
| 指代消解 | 原句与改写句的文字差异（补全的实体高亮）；`standard_query`；标记 chips |
| 意图 | 意图 + 置信度条，0.7 升级门槛刻度；路由 chip |
| 检索 | 扩写查询 chips；Top-5（路径、分数条），低于 `RERANK_MIN_SCORE` 的置灰 |
| 置信度闸 | 三段堆叠条（`w1·Top1`、`w2·有效证据`、`w3·分差`）与门槛竖线；自评结论（若调用） |
| Agent | 步数；每次工具调用一行：名称、内置 / MCP 徽标、状态、重试、耗时；引用映射 |
| 收尾 | 层 1、层 2 token 条对预算；摘要触发标记 |

- 底部汇总：本轮总耗时、LLM 调用次数、输入 / 输出 token（缓存、思考分列）、意图。live 模式带「Langfuse ↗」链接到该会话（会话 ID 即 Langfuse session）。
- 悬停回复中的 `[n]` 高亮检索行；点击任一条客服回复切换到那一轮。没有调试数据的历史轮显示「该轮没有调试数据」。

## 9. 运营台

- 待审队列：功能与 ch09 页面一致（筛选、详情展开看原话与快照、通过表单、驳回）。
- 评估趋势：ch09 的同题数折线 + 下滑标红；新增「四策略对比」区块，数据来自最近一次 `run_rag_eval.py --gen-strategies all` 报告的导出 JSON。
- 编造台账：ch04 页面的功能迁移（列表、详情、处置）。
- 工具审计（新增）：`GET /api/tool-audit?limit=N&status=`，按时间倒序列出 `tool_audit_logs`（工具、来源、状态、重试、耗时、错误）。只读。

## 10. 回放剧场

### 10.1 场景

| # | 场景 id | 流程 | 证明什么 |
|---|---|---|---|
| 1 | `flywheel` | 分屏：客户问知识库没有的问题 → 闸拦下、兜底 → 运营侧待审出现（带快照）→ 核准 → 向量化 → 客户再问答对 | 系统会自我改进 |
| 2 | `refund` | 「我上周买的耳机想退」→ 订单选择 interrupt → 选订单 → 恢复 → 扩写、多查询检索 → Agent 给退款单按钮 → 填写提交 | interrupt / resume、结构化卡片 |
| 3 | `multi-turn` | 「X3 Pro 续航多久」→「那它防水吗」→「它能退吗」→「我刚才问了什么」 | 指代消解、分层上下文、回顾 |
| 4 | `ticket` | 「帮我建个工单」→ 追问 → 预览卡 → 确认 → 工单号；审计记录 | 写操作人工确认 |
| 5 | `mcp-timeout` | 物流 MCP Server 注入延迟 → 超时 → 指数回退重试 → 降级回复；审计「超时」 | 超时、重试、降级 |
| 6 | `boundaries` | 超范围问题（宠物保险）→ 兜底；投诉 → 安抚 + 按钮；闲聊 → 固定话术 | 业务边界 |
| 7 | `strategies` | 3 道代表题在 dense / BM25 / RRF / 重排下的 Top-5 并排，标出正确答案；下方真实指标表 | 选型有数据支撑 |

### 10.2 播放器

- 顶部：场景标题 + 解说横幅（中 / EN 切换）。解说锚定到事件（如「第 1 个 `gate` 事件出现时」），不锚定到时间。
- 中部：复用工作台组件，默认工程视角；场景 1 为左右分屏（客户 | 运营）。
- 底部：带章节刻度的时间轴、播放 / 暂停、变速、跳过等待。
- 深链接：`#/theater/<scene>?t=<秒>&view=eng|cust`。
- 页脚：「录制于 <日期> · commit <hash> · <模型>」，hash 链接到 GitHub。

### 10.3 录制

- `scripts/scenes/<scene>.yaml` 声明步骤：`chat`（消息）、`wait_done`、`pick_order`（序号）、`confirm_ticket`、`feedback`、`poll_review`（直到包含某原话）、`approve`（答案、品类）、`snapshot`（API 路径）、`inject`（如 MCP 延迟，需脚本自己重启对应 Server）。
- `scripts/record_scene.py <scene>`：对本地完整系统按步骤执行，记录全部 SSE 事件（`debug: true`）和 API 响应，写 `web/public/replays/<scene>.jsonl`。
  - 首行：`{scene, title_zh, title_en, recorded_at, git_commit, model}`。
  - 之后每行：`{t_ms, lane: "customer"|"ops", channel: "sse"|"api", event, data}`。
- 场景 7 由 `evals/export_strategy_compare.py` 从评估结果导出（需要评估脚本保存逐题排序结果，见第 13 节）。
- `scripts/export_snapshots.py`：导出运营台快照。
- 解说文本在 `web/src/theater/scenes.ts` 中维护，不进录制文件。

## 11. 视觉

- 颜色：深蓝 `#0F2D50`（标题、主文字强调）、`#173F6B`（交互）、琥珀 `#F2B544`（强调，来自作品集 banner）；背景 `#F2F5F7`，边框浅灰；状态色只用于状态（绿通过、琥珀拦下 / 警告、红错误）。不做暗色模式。
- 字体：系统中文字体栈（PingFang SC / Microsoft YaHei）；字号 13 / 14 / 16 / 20；数字 `tabular-nums`；8 px 栅格，客服工作台密度。
- 动效只有节点行淡入和进度推进；尊重 `prefers-reduced-motion`。
- 无障碍：瀑布可键盘导航；状态变化 `aria-live`；颜色之外有文字或图标表示状态。

## 12. 一键本地运行

- `Dockerfile`（多阶段）：Node 阶段构建 `web`（live）→ Python 阶段 `uv sync --frozen`，复制 `dist`。
- `docker-compose.yml` 新增 `full` profile（默认 `docker compose up -d` 行为不变，仍只起 MySQL + Milvus）：
  - `init`：等数据库健康后执行 `build_kb.py`（已建库跳过）。
  - `mcp-logistics`、`mcp-aftersales`：两个 MCP Server。
  - `app`：主服务，8000 端口，`depends_on` 上述服务完成或健康。
- `.env.example`：必填 `CHAT_*`、`EMBED_API_KEY`、`RERANK_API_KEY`；可选 `LANGFUSE_*`。`DATABASE_URL`、`MILVUS_URI` 在 compose 中按容器网络设置。
- **新增配置项 `TOOL_POLICY_PATH`（用户同意）：** `Settings` 可选字段，默认 `config/tools.json`。compose 中指向 `config/tools.docker.json`（MCP 地址为容器服务名）。
- Langfuse 仍可选：`-f docker-compose.langfuse.yml`。

## 13. 后端增量汇总

| 变更 | 位置 |
|---|---|
| `ChatRequest`、`ResumeRequest` 加 `debug`；`GraphContext` 加 `debug` | `app/schemas.py`、`app/graph/state.py`、`app/api/chat.py` |
| `events.trace(kind, data)`：只在 `debug` 时发 | `app/graph/events.py` 及第 5.2 节各处 |
| 每请求 token 收集器，由 `stream_graph` 合并发出 | `app/observability.py` 或新模块、`app/api/chat.py` |
| `GET /api/tool-audit` | 新 `app/api/tool_audit.py`、`app/repositories/audit.py` |
| `GET /api/strategy-comparison`（读导出文件） | 新接口 |
| `run_rag_eval.py` 保存逐题排序结果 JSON（`--save-rankings`） | `evals/run_rag_eval.py` |
| 静态资源服务、`/admin/*` 重定向、未构建提示 | `app/api/web.py` |
| `TOOL_POLICY_PATH` 读 `Settings` | `app/config.py`、`app/tools/policy.py` |

## 14. 测试与 CI

- 后端（TDD）：`debug=False` 时事件序列与 ch09 完全相同；`debug=True` 时每类 `trace` 至少在一个测试中出现并通过 schema；工具审计接口；`TOOL_POLICY_PATH`；静态服务与重定向。
- 前端（TDD）：reducer 覆盖全部事件类型和拖动；回放时钟（变速、暂停、压缩）；回放数据源；关键组件（闸堆叠条数值、无调试数据的历史轮、引用悬停联动）。
- 端到端：Playwright 对 replay 构建打开场景库 → 播放 `flywheel` → 拖到末尾 → 断言出现「通过」和带引用的回复；每个场景能打开并播放到结束。同时截取各场景关键帧到 `docs/media/`，供 README 使用。
- GitHub Actions：
  1. `backend.yml`：docker compose 起 MySQL + Milvus，`uv run pytest -q`。
  2. `web.yml`：`npm ci`、`tsc --noEmit`、`vitest run`、`vite build`（两种模式）、Playwright。
  3. `pages.yml`：推送 `main` 时构建 replay 版并部署 Pages。
- **用户确认：** 本章前端不按 CLAUDE.md 的 Vibe Coding 例外处理，按完整流程（TDD、code review）开发；视觉细节仍可由用户描述效果后调整。

## 15. 已知限制

- 回放访客不能自己提问；要提问须本地运行。
- 录制依赖真实上游，重录结果会因模型输出不同而变化；解说锚定事件以减小影响。
- CI 中后端测试依赖 Milvus 容器，运行时间较长（预计 5–8 分钟）。
- 历史会话（ch10 之前）没有调试数据。
