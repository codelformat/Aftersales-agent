# ch10 作品展示前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目的执行方式（CLAUDE.md + 用户要求）：** Codex 写全部业务代码并自测（"Entrust as much work as possible to codex"）；Claude 写自包含任务描述、审查 diff、跑全量测试、做 docker 步骤、提交。Codex 额度用完时改派 Sonnet 子代理。Codex 不修改 dev-notes。

**Goal:** 用一套 React + TS + Vite 前端替换 4 个旧 HTML 页面，同一界面接 live（SSE）和 replay（录制）两种数据源，展示 Agent 内部决策过程，并提供 GitHub Pages 回放剧场、一键 Docker 运行和三条 CI。

**Architecture:** 后端新增可选的 `trace` 调试事件通道（`debug=true` 时才发）和两个只读接口；前端状态是事件序列的纯函数 reduce，live 与 replay 共用；录制脚本驱动真实系统生成 `.jsonl` 场景；Playwright 跑回放版并截图。

**Tech Stack:** FastAPI、LangGraph（后端不变）；React 18+、TypeScript、Vite、CSS Modules、Radix UI primitives、Vitest + Testing Library、Playwright；Docker Compose；GitHub Actions + Pages。

**Spec:** `docs/superpowers/specs/2026-10-09-ch10-showcase-frontend-design.md`

## Global Constraints

- `debug=False`（默认）时，SSE 事件序列和 ch09 完全相同；不发 `trace`。
- `trace` 事件不含 Prompt 原文、密钥、Langfuse 地址。
- 界面文字中文；回放剧场解说中英切换；README 不在本章。
- 颜色：`#0F2D50`、`#173F6B`、琥珀 `#F2B544`、背景 `#F2F5F7`；状态色只用于状态；无暗色模式；尊重 `prefers-reduced-motion`。
- 不引入图表库、全量 UI 组件库；只用 Radix primitives。
- 新配置项只有 `TOOL_POLICY_PATH`（用户已同意），`Settings` 可选字段。
- 库用法先查 Context7（React、Vite、Vitest、Playwright、Radix、FastAPI StaticFiles、LangGraph `get_runtime`）。
- 现有后端测试必须保持通过；ch09 的设计约束全部保持。
- 本机 shell 有 `HTTP_PROXY`：本机 HTTP 客户端 `trust_env=False`，curl 用 `--noproxy '*'`。
- 每个任务完成后 Claude 在 `dev-notes/ch10.md` 补一段（四样）。

## Review Focus

1. 客户视角（`debug=false`）事件流被调试代码改变（多了事件、顺序变化、异常吞掉）——任务 1 的 `test_debug_off_stream_identical` 用 ch09 的端到端测试序列逐事件比对。
2. SSE 字节流在网络分块处被截断（一个事件跨两个 chunk、`\r\n` 换行、多行 data）——任务 7 的 `parseSse` 分块测试。
3. 回放向后拖动、重复播放、变速中拖动后状态错乱——任务 7 的 reducer 纯函数性测试与时钟测试。
4. 没有调试数据的历史轮、没有 checkpoint 的旧会话在工作台中显示异常——任务 9 的组件测试。
5. replay 构建意外请求后端（`/api/...`、`/chat/...`）——任务 14 的 Playwright 拦截所有非静态请求并断言为 0。

---

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `app/graph/events.py`、`app/graph/state.py`、`app/schemas.py`、`app/api/chat.py` | `debug` 开关、`trace()`、节点计时包装 | 1 |
| `web/src/protocol/events.schema.json` | 事件契约（前后端共享） | 1 |
| `app/graph/nodes/*.py`、`app/tools/executor.py` | 各类 `trace` 事件 | 2 |
| `app/observability.py`（或 `app/tracing.py`） | 每请求 LLM 用量收集器 | 3 |
| `app/api/tool_audit.py`、`app/api/strategy.py`、`app/api/web.py`、`evals/run_rag_eval.py`、`evals/export_strategy_compare.py`、`app/config.py`、`app/tools/policy.py` | 只读接口、静态服务、配置 | 4 |
| `web/`（脚手架、令牌、外壳、路由） | 前端工程 | 5 |
| `web/src/protocol/`、`web/src/state/` | 类型、reducer | 6 |
| `web/src/data/` | Live/Replay 数据源、SSE 解析、回放时钟 | 7 |
| `web/src/desk/` | 客服工作台 | 8 |
| `web/src/xray/` | 透视面板 | 9 |
| `web/src/ops/` | 运营台 | 10 |
| `scripts/record_scene.py`、`scripts/scenes/*.yaml`、`scripts/export_snapshots.py`、`web/public/replays/`、`web/public/snapshots/` | 录制 | 11 |
| `web/src/theater/` | 回放剧场 | 12 |
| `Dockerfile`、`docker-compose.yml`、`config/tools.docker.json`、`.env.example` | 一键运行 | 13 |
| `web/e2e/`、`.github/workflows/*.yml`、`docs/media/` | 端到端、CI、截图 | 14 |

---

### Task 1: 调试开关、`trace` 基础设施、事件契约

**Files:**
- Modify: `app/schemas.py`（`ChatRequest`、`ResumeRequest` 加 `debug: bool = False`）、`app/graph/state.py`（`GraphContext.debug: bool = False`）、`app/graph/events.py`、`app/graph/builder.py`（节点计时包装）、`app/api/chat.py`（`ChatTurn.debug`，传进 `GraphContext`）
- Create: `web/src/protocol/events.schema.json`、`tests/protocol.py`（校验辅助）、`tests/test_trace_events.py`

**Interfaces:**
- Produces:
  - `events.trace(kind: str, data: dict, node: str | None = None) -> None`：只在当前 run 的 `GraphContext.debug` 为真时 `emit("trace", {"kind", "node", "t_ms", "data"})`。`t_ms` 从本轮开始计时（`ChatTurn` 创建时记录 `time.monotonic()`，放进 `GraphContext.started_at`）。读取当前 runtime 用 LangGraph 的 `get_runtime()`（先查 Context7 确认 1.2.x 的导入路径和签名）；读不到 runtime 时什么也不做。
  - `builder` 中每个节点函数用 `timed(name, fn)` 包装：进入发 `trace("node_start", {}, node=name)`，返回发 `trace("node_end", {"ms": ...}, node=name)`。`GraphInterrupt` 异常要原样抛出且先发 `node_end`（`data.interrupted = true`）。
  - `tests/protocol.py`：`validate_event(name: str, data: dict) -> None`，用 `jsonschema` 按 `web/src/protocol/events.schema.json` 校验，失败抛 `AssertionError` 带路径。
- schema 结构：顶层 `oneOf` 按事件名分支；业务事件按 ch09 实际字段写（`session{session_id}`、`understood{resolved_input,intent}`、`token{text}`、`tool_start{tools[{id,name,args}]}`、`tool_end{tools[{id,name,ok}]}`、`citations{items[],refused}`、`actions{options[]}`、`order_picker{orders[]}`、`ticket_preview{call_id,ticket_type,description}`、`error{code,message}`、`done{finish_reason,message_id?}`）；`trace` 分支按 spec 5.2 的 8 种 `kind`，`data` 各自定义。先读 `app/graph/nodes/*.py`、`app/api/chat.py` 核对字段，以代码为准。

- [ ] **Step 1: 写失败测试 `tests/test_trace_events.py`**

  - `test_debug_off_stream_identical`：用 `client` + `use_script` + `use_intent` 跑与 `tests/test_chat_api.py::test_knowledge_events` 相同的场景，请求不带 `debug`，收集事件名序列，断言与该测试的期望序列完全相同且没有 `trace`。
  - `test_debug_on_emits_node_events`：同场景带 `"debug": true`，断言出现 `trace`，`kind` 为 `node_start`/`node_end` 的节点名覆盖 `start_turn`、`resolve_reference`、`classify_intent`、`retrieve`、`confidence_gate`、`agent_model`、`finalize`；每个 `node_start` 都有对应 `node_end`；`t_ms` 单调不减。
  - `test_debug_events_match_schema`：上一测试收集的全部事件逐条 `validate_event`。
  - `test_interrupt_emits_node_end`：订单选择 interrupt 场景（复用 `tests/test_resume_api.py` 的辅助）带 debug，`ensure_order` 有 `node_end` 且 `data.interrupted is True`。
  - `test_resume_accepts_debug`：`/chat/resume` 带 `debug: true` 时恢复轮也有 `trace`。

- [ ] **Step 2: 运行确认失败。** `uv run pytest tests/test_trace_events.py -q` → FAIL。
- [ ] **Step 3: 实现**（按 Interfaces）。`trace()` 的实现要点：

  ```python
  def trace(kind: str, data: dict, node: str | None = None) -> None:
      try:
          ctx = get_runtime(GraphContext).context
      except Exception:
          return
      if not getattr(ctx, "debug", False):
          return
      emit("trace", {"kind": kind, "node": node, "t_ms": int((time.monotonic() - ctx.started_at) * 1000),
                     "data": data})
  ```
  （`get_runtime` 的确切导入和参数以 Context7 为准；若 1.2.x 不支持，在节点中把 `runtime` 显式传给 `trace`，并在报告中说明。）
- [ ] **Step 4: 运行** 新测试和 `uv run pytest -q` 全部通过。
- [ ] **Step 5: 提交** `feat(ch10): debug trace channel and event schema`。

---

### Task 2: 领域 `trace` 事件

**Files:**
- Modify: `app/graph/nodes/turn.py`（`resolve`）、`app/graph/nodes/intent.py`（`intent`）、`app/graph/nodes/knowledge.py`、`app/graph/nodes/aftersales.py`（`retrieval`、`gate`）、`app/graph/nodes/agent.py`（`context`）、`app/graph/nodes/finalize.py`（`context`）、`app/tools/audit.py` 或 `app/tools/executor.py`（`tool`）
- Test: `tests/test_trace_events.py`

**Interfaces:**
- `resolve`：`{original, resolved_input, standard_query, order_id, order_scoped, ticket_request, status_query, history_recall}`。
- `intent`：`{intent, confidence, route, escalated}`（`escalated` 取自 `understanding.classify` 的降级路是否使用大模型；没有该信息时为 `false`，读代码确认）。
- `retrieval`：`{queries, top, kept}`，`top` = State `retrieval` 快照，`kept` = `len(evidence)`；`queries` 在 knowledge 出口为 `[standard_query]`，aftersales 为 `state["queries"]`。
- `gate`：`{passed, confidence, signals, weights, threshold, source, reason, self_check}`；`weights` 与 `threshold` 取 config 常量；`self_check` 为是否调用了自评。
- `tool`：`{call_id, name, source, mcp_server, status, retry_count, duration_ms, error_message}`，在 `audit.record(rec)` 被调用的同一处发（执行引擎内）。
- `context`：`agent_model` 组装后发 `{layer1_tokens: prompt.layer1, layer1_budget, layer2_tokens: prompt.layer2, layer2_budget, summary_triggered: false}`；`finalize` 在 `maintain()` 后发一次 `summary_triggered` 实际值（让 `maintain` 返回是否触发摘要，或用 `SummaryRunner.running(cid)` 判断，读代码选简单的）。

- [ ] **Step 1: 写失败测试**：knowledge 轮（闸放行）、knowledge 轮（闸拦下）、aftersales 轮（含 interrupt/resume）、业务轮（带一次工具调用，用 `ScriptedChatModel` 产生 tool_call）、闲聊轮；分别断言对应 `kind` 出现、字段值与 State/审计一致（例如 `gate.confidence` 等于 State `gate.confidence`，`tool.status` 等于 `audit_log` 中记录）、全部通过 schema。
- [ ] **Step 2: 运行确认失败。**
- [ ] **Step 3: 实现。** 字段名同时补进 `events.schema.json`。
- [ ] **Step 4: 运行** 新测试与全量通过。
- [ ] **Step 5: 提交** `feat(ch10): domain trace events for resolve, intent, retrieval, gate, tools, context`。

---

### Task 3: 每请求 LLM 用量收集器

**Files:**
- Create: `app/tracing.py`
- Modify: `app/api/chat.py`（`stream_graph` 合并）
- Test: `tests/test_trace_events.py`

**Interfaces:**
- `class UsageCollector(BaseCallbackHandler)`：`on_llm_end` 读取 `LLMResult` 的 usage（`llm_output["token_usage"]` 或 generation message 的 `usage_metadata`，两者都兼容；`input_token_details.cache_read`、`output_token_details.reasoning`），以及 run 的 `metadata["langgraph_node"]`（在 `on_chat_model_start` 记下 run_id → node、开始时间）。把 `{"kind": "llm", "node", "data": {...}}` 放进 `asyncio.Queue`（线程安全：用 `loop.call_soon_threadsafe`）。
- `stream_graph`：`debug` 为真时创建 collector，以 `config["callbacks"] = [collector]` 合并进本次调用（与编译时挂的 Langfuse 回调并存，先查 Context7 确认 LangGraph 运行时 callbacks 与 `with_config` callbacks 的合并行为）；在 astream 循环中每产出一个事件后、以及循环结束前，把队列里的 `llm` 事件按 `t_ms` 发成 `trace`。

- [ ] **Step 1: 写失败测试**：带 debug 的 knowledge 轮（`ScriptedChatModel` 设定 usage），断言 `llm` 事件的 `node == "agent_model"`、token 数等于脚本设定；不带 debug 时无 `llm` 事件；schema 通过。（若 `ScriptedChatModel` 不支持 usage，先给它加 `usage_metadata` 选项并写测试。）
- [ ] **Step 2–4:** 失败 → 实现 → 通过（含全量）。
- [ ] **Step 5: 提交** `feat(ch10): per-request LLM usage trace`。

---

### Task 4: 只读接口、静态服务、`TOOL_POLICY_PATH`

**Files:**
- Create: `app/api/tool_audit.py`、`app/api/strategy.py`、`evals/export_strategy_compare.py`
- Modify: `app/repositories/audit.py`（`list_recent`）、`app/main.py`、`app/api/web.py`、`evals/run_rag_eval.py`（`--save-rankings <path>`）、`app/config.py`（`Settings.tool_policy_path: str | None = None`）、`app/tools/policy.py`
- Test: `tests/test_tool_audit_api.py`、`tests/test_strategy_api.py`、`tests/test_web.py`、`tests/test_policy.py`（补用例）、`tests/test_run_rag_eval.py`（补用例）

**Interfaces:**
- `GET /api/tool-audit?limit=50&status=` → `[{id, created_at, conversation_id, tool_call_id, tool_name, tool_source, mcp_server, status, retry_count, duration_ms, error_message, result_summary}]`，按 `id` 倒序；`limit` 1–500；`status` 取 5 个中文状态之一。
- `run_rag_eval.py --save-rankings evals/reports/rankings_<时间>.json`：保存 `{strategy: {sample_id: [source_key, ...前 5]}}` 和每题的 `relevant`、`bucket`、`query`。
- `evals/export_strategy_compare.py --rankings <json> --report <md> --out web/public/snapshots/strategy-comparison.json`：输出 `{generated_from: {report, rankings, git_commit}, metrics: {strategy: {R@1,R@3,R@5,R@10,MRR,faithfulness,false_refusal,d_refusal}}, cases: [3 道代表题：{id, query, bucket, relevant, rankings: {strategy: [{key, relevant: bool}]}}]}`。代表题选择规则：C 桶中 BM25 Top-1 不相关而重排 Top-1 相关的第一题；B 桶中 dense Top-1 不相关而重排相关的第一题；A 桶中四种都相关的第一题。没有满足条件的题时退而取同桶第一题，并在 JSON 中注明。
- `GET /api/strategy-comparison`：读 `web/public/snapshots/strategy-comparison.json`；不存在时 404 `{code: "not_exported"}`。
- `app/api/web.py`：
  - `/assets/*` 等用 `StaticFiles(directory=app/web/dist)` 挂载（`html=False`），`/` 返回 `dist/index.html`；`dist` 不存在时 `/` 返回 200 的说明页（含 `npm --prefix web ci && npm --prefix web run build`）。
  - `/admin/review-queue` → 307 `/#/ops/review`；`/admin/eval-runs` → `/#/ops/evals`；`/admin/faith-cases` → `/#/ops/faith`。
  - 旧 HTML 文件在任务 14 删除；本任务先让新路由生效，旧文件不再被引用。
- `TOOL_POLICY_PATH`：`policy.load_policy()` 的默认路径 = `Settings.tool_policy_path or config.TOOL_POLICY_PATH`。

- [ ] **Step 1: 写失败测试**（接口字段与排序、`limit` 边界 422、`status` 过滤；strategy 404 与正常返回；`/` 在无 dist 时返回说明、有 dist（tmp 目录 monkeypatch）时返回 index；3 条重定向；`TOOL_POLICY_PATH` 指向 tmp 文件时生效；`--save-rankings` 写出预期结构（用现有 run_rag_eval 测试的 fake）；export 脚本的代表题选择规则（纯函数测试））。
- [ ] **Step 2–4:** 失败 → 实现 → 通过（含全量）。
- [ ] **Step 5（Claude）:** 运行一次 `uv run python evals/run_rag_eval.py --stage retrieval --save-rankings evals/reports/rankings_ch10.json --no-write`（只检索段，约 3 分钟），再运行 export 脚本，生成 `web/public/snapshots/strategy-comparison.json`，生成段指标取 `evals/reports/rag_eval_20261009-193611.md`。
- [ ] **Step 6: 提交** `feat(ch10): tool audit and strategy comparison APIs, static web serving, TOOL_POLICY_PATH`。

---

### Task 5: 前端脚手架、设计令牌、外壳

**Files:**
- Create: `web/package.json`、`web/vite.config.ts`、`web/tsconfig*.json`、`web/index.html`、`web/src/main.tsx`、`web/src/app/App.tsx`、`web/src/app/router.tsx`、`web/src/app/TopBar.tsx`、`web/src/styles/tokens.css`、`web/src/styles/base.css`、`web/src/app/ViewModeContext.tsx`、`web/src/config.ts`、`web/vitest.config.ts`、`web/src/test/setup.ts`、`web/.gitignore`、`web/README.md`（开发命令）
- Modify: `.gitignore`（`app/web/dist`、`web/node_modules`）

**Interfaces:**
- `config.ts`：`export const DATA_SOURCE: "live" | "replay" = import.meta.env.VITE_DATA_SOURCE ?? "live"`；`export const REPO_URL = "https://github.com/codelformat/Aftersales-agent"`。
- npm scripts：`dev`（代理 `/api`、`/chat`、`/tickets`、`/refunds` 到 `127.0.0.1:8000`）、`build`（live，`outDir: ../app/web/dist`）、`build:replay`（`--mode replay`，`outDir: dist-replay`，`base: "/Aftersales-agent/"`）、`typecheck`、`test`（vitest run）、`e2e`（任务 14）。
- 路由（hash）：`#/` → live 为 `DeskPage`，replay 为重定向 `#/theater`；`#/desk`、`#/ops/review|evals|faith|tools`、`#/theater`、`#/theater/:scene`。先用占位组件。
- 顶栏：品牌名、三个入口、视角切换（Radix Toggle Group，只在 desk 和 theater 播放页显示，值存 `localStorage("view_mode")`，默认 `eng`）；replay 模式顶部横幅（文案见 spec 第 4 节，链接 `${REPO_URL}#run-locally`）。
- 令牌：spec 第 11 节的颜色、字号、间距、圆角、阴影（只一档），全部为 CSS 变量。

- [ ] **Step 1: 写失败测试**（Vitest + Testing Library）：路由渲染对应占位页；replay 模式下 `#/` 重定向到 `#/theater` 且显示横幅；视角切换写入 localStorage 并在刷新后恢复；顶栏在 ops 页不显示切换。
- [ ] **Step 2–4:** 失败 → 实现 → 通过；`npm --prefix web run typecheck`、`build`、`build:replay` 均成功。
- [ ] **Step 5（Claude）:** 启动后端，浏览器打开 `http://127.0.0.1:8000/`，确认新外壳显示。
- [ ] **Step 6: 提交** `feat(ch10): web scaffold with design tokens and app shell`（`package-lock.json` 一并提交）。

---

### Task 6: 协议类型与 reducer

**Files:**
- Create: `web/src/protocol/events.ts`、`web/src/protocol/schema.test.ts`、`web/src/state/conversation.ts`、`web/src/state/conversation.test.ts`

**Interfaces:**
- `events.ts`：`type ChatEvent = | {event:"session", data:{session_id:string}} | ... | {event:"trace", data: TraceEvent}`；`TraceEvent` 为 8 种 `kind` 的判别联合。
- `schema.test.ts`：读取 `events.schema.json`，断言 TS 类型中每个事件名、每个 `trace.kind` 在 schema 中存在且反之亦然（用一个手写的 `EVENT_NAMES`、`TRACE_KINDS` 常量数组与 schema 比对，类型用 `satisfies` 绑定到常量，保证三者一致）。
- `conversation.ts`：
  ```ts
  export interface Turn { id: string; userText: string; replyText: string; status: "streaming"|"done"|"interrupted"|"error";
    messageId?: number; understood?: {...}; citations?: Citation[]; actions?: Action[]; orderPicker?: Order[];
    ticketPreview?: TicketPreview; tools: ToolCall[]; trace: TraceEvent[]; error?: {code:string; message:string} }
  export interface DeskState { sessionId?: string; turns: Turn[]; activeTurnId?: string }
  export type DeskAction = { type: "user_message"; text: string; turnId: string } | { type: "resume"; turnId: string }
    | { type: "event"; turnId: string; event: ChatEvent } | { type: "select_turn"; turnId: string } | { type: "load_history"; ... }
  export function reduce(state: DeskState, action: DeskAction): DeskState   // 纯函数，不改入参
  export function replay(actions: DeskAction[], upTo?: number): DeskState   // 从初始状态依次 reduce
  ```
- 规则：`token` 追加到当前轮 `replyText`；`done` 设置 `status` 与 `messageId`；`order_picker`/`ticket_preview` 设置卡片并 `status="interrupted"`；`trace` 追加到当前轮 `trace`；`error` 设置错误；`resume` 让同一轮继续接收事件（订单选择后的回复追加到同一轮）。

- [ ] **Step 1: 写失败测试**：每种事件一个用例；一段完整 knowledge 轮（含 trace）reduce 后的状态快照；interrupt + resume 合并到同一轮；`reduce` 不修改入参（`Object.freeze` 深冻结输入）；`replay(actions, k)` 对任意 k 等于前 k 个依次 reduce（性质测试：k 取 0..n）。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: 提交** `feat(ch10): typed event protocol and pure conversation reducer`。

---

### Task 7: 数据源、SSE 解析、回放时钟

**Files:**
- Create: `web/src/data/DataSource.ts`、`web/src/data/sse.ts`、`web/src/data/live.ts`、`web/src/data/replay.ts`、`web/src/data/clock.ts`、`web/src/data/index.ts` 及各 `*.test.ts`；`web/src/data/fixtures/*.jsonl`（测试用的小录制）

**Interfaces:**
- `DataSource`：按 spec 第 6 节（`chat`、`resume`、`conversations`、`messages`、`feedback`、`submitRefund`、`submitTicket`、`review.{list,detail,approve,reject}`、`evalRuns`、`strategyComparison`、`faithCases`、`resolveFaithCase`、`toolAudit`）。`index.ts` 按 `DATA_SOURCE` 导出单例。
- `sse.ts`：`async function* parseSse(stream: ReadableStream<Uint8Array>): AsyncGenerator<{event: string; data: string}>`，按 SSE 规范处理 `\n`/`\r\n`、多行 `data:`、注释行、跨 chunk 的事件和多字节 UTF-8 截断（`TextDecoder` stream 模式）。
- `live.ts`：`chat()` 用 `fetch("/chat/stream", {method:"POST", body})`，非 2xx 时抛 `ApiError{status, code, message}`（读 FastAPI `detail`）；REST 调用同理。
- `replay.ts`：`loadScene(id): Promise<Scene>`（`Scene = {header, lines: SceneLine[]}`）；运营台读 `snapshots/*.json`；写操作抛 `ReplayReadOnlyError`。
- `clock.ts`：
  ```ts
  export class ReplayClock {
    constructor(lines: SceneLine[], opts?: { gapThresholdMs?: number /*3000*/, compressedGapMs?: number /*1500*/ })
    readonly durationMs: number               // 压缩后的总时长
    readonly gaps: { atMs: number; realMs: number }[]
    position(): number                        // 当前压缩后时间
    play(speed?: number): void; pause(): void; seek(ms: number): void; setSpeed(s: number): void
    onTick(cb: (indexInclusive: number, positionMs: number) => void): () => void
  }
  ```
  时钟只计算「到当前时间为止应已发出的事件下标」；界面状态由 `replay(actions, index)` 得到。测试中注入可控时间源（`now: () => number`、`schedule`）。

- [ ] **Step 1: 写失败测试**：`parseSse` 的分块（每字节一个 chunk）、`\r\n`、多行 data、中文被截断在 chunk 边界；`live` 的错误映射（mock fetch）；`replay` 读取 fixture、写操作报错；时钟：间隔压缩与 `gaps` 标记、2× 速度、暂停后不前进、向后 seek 后下标回退、播放到末尾自动暂停。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: 提交** `feat(ch10): live and replay data sources with SSE parser and replay clock`。

---

### Task 8: 客服工作台

**Files:**
- Create: `web/src/desk/DeskPage.tsx`、`SessionList.tsx`、`OrderCard.tsx`、`ChatPane.tsx`、`MessageBubble.tsx`、`Composer.tsx`、`cards/OrderPicker.tsx`、`cards/RefundForm.tsx`、`cards/TicketPreview.tsx`、`cards/ActionButtons.tsx`、`cards/Citations.tsx`、`cards/Feedback.tsx`、`useDeskSession.ts` 及测试

**Interfaces:**
- 行为与 ch09 聊天页一致（先通读 `app/web/index.html` 的交互：会话侧栏、`user_id` 生成与存储、`understood` 灰字、订单卡片与 `/chat/resume`、退款单 `/refunds`、工单确认 `ticket_confirm`、`/tickets` 按钮建单、引用卡片 `/api/knowledge/chunks/{id}`、👍/👎 `/api/feedback`、错误提示、发送中禁用）。
- `useDeskSession(dataSource)`：管理 `DeskState`（任务 6 的 reducer）、发送、恢复、加载历史；工程视角时请求带 `debug: true`。
- 回复中的 `[n]` 渲染为可悬停的引用标记（Radix Tooltip 显示证据原文），并通过回调通知透视面板高亮（任务 9 接入）。
- 历史会话加载：`messages()` 转成 `Turn`（无 trace）。

- [ ] **Step 1: 写失败测试**（用 fake DataSource 产生事件序列）：发送消息后流式显示；订单选择点击后调用 `resume` 且回复并入同一轮；退款单提交与成功提示；工单预览确认 / 取消；转人工按钮；👍/👎 只在有 `messageId` 时显示、点击后调用 `feedback`、失败可重试；错误事件显示文案；发送中输入框禁用；加载历史会话。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5（Claude）:** 启动完整系统，浏览器逐项走一遍 ch09 的客户视角功能（含 `demo9.sh` 的 3 个问题），与旧页面行为对照。
- [ ] **Step 6: 提交** `feat(ch10): service desk with business cards`。

---

### Task 9: 透视面板

**Files:**
- Create: `web/src/xray/XrayPanel.tsx`、`Waterfall.tsx`、`details/ResolveDetail.tsx`、`IntentDetail.tsx`、`RetrievalDetail.tsx`、`GateDetail.tsx`、`AgentDetail.tsx`、`ContextDetail.tsx`、`TurnSummary.tsx`、`textDiff.ts`、`buildTimeline.ts` 及测试

**Interfaces:**
- `buildTimeline(trace: TraceEvent[]): TimelineNode[]`（纯函数）：按 `node_start`/`node_end` 配对，附上同节点的领域事件和 `llm` 事件；未结束的节点为 `running`；`interrupted` 标记；`totalMs`。
- `textDiff(a: string, b: string): {text: string; added: boolean}[]`：字符级 LCS，给指代消解高亮。
- `GateDetail`：SVG 堆叠条，三段宽度 = `weights[i] * signal[i]`，总长 = `confidence`，门槛竖线在 `threshold`；文字同时给出数值（颜色之外的表达）。
- 汇总：总耗时、LLM 次数、输入 / 输出 token（缓存、思考分列）、意图；live 模式「Langfuse ↗」链接到 `${LANGFUSE_UI}/project/aftersales/sessions/<sessionId>`（`LANGFUSE_UI` 由 `VITE_LANGFUSE_URL` 提供，未设置时不显示链接）。
- 无 trace 的轮：显示「该轮没有调试数据」。

- [ ] **Step 1: 写失败测试**：`buildTimeline` 配对、嵌套领域事件、未结束节点、interrupt；`textDiff` 用例（无变化、插入实体、整句替换）；`GateDetail` 三段宽度与门槛线位置（读 SVG 属性）；悬停引用 `[n]` 高亮对应检索行；无 trace 的提示；键盘上下键在瀑布行间移动、Enter 展开。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5（Claude）:** 浏览器工程视角跑 knowledge、aftersales（interrupt）、业务（MCP 工具）各一轮，截图检查。
- [ ] **Step 6: 提交** `feat(ch10): x-ray panel with waterfall and gate visualisation`。

---

### Task 10: 运营台

**Files:**
- Create: `web/src/ops/OpsLayout.tsx`、`ReviewPage.tsx`、`EvalsPage.tsx`、`StrategyComparison.tsx`、`FaithPage.tsx`、`ToolAuditPage.tsx`、`charts/LineChart.tsx` 及测试

**Interfaces:**
- 功能迁移自 ch09 `review_queue.html`、`eval_runs.html` 和 ch04 `faith_cases.html`（先通读三个文件的交互与接口）。
- `EvalsPage`：同题数轮次折线（8 指标）+ 下滑标红；下方 `StrategyComparison`：指标表（4 策略 × 8 指标，每列最优加粗）+ 3 道代表题的 Top-5 并排，相关项标记。
- `ToolAuditPage`：表格（时间、工具、来源、状态徽标、重试、耗时、错误），状态筛选。
- replay 模式：写操作按钮禁用并提示「本地运行可操作」。

- [ ] **Step 1: 写失败测试**：审核通过表单默认值（去掉「（待核实）」、品类默认通用）、409/422/502 文案；评估只画同题数轮次、下滑标红；策略表最优加粗；工具审计筛选；replay 模式按钮禁用。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: 提交** `feat(ch10): ops console for review, evals, faith cases and tool audit`。

---

### Task 11: 场景录制与快照导出

**Files:**
- Create: `scripts/record_scene.py`、`scripts/scenes/{flywheel,refund,multi-turn,ticket,mcp-timeout,boundaries}.yaml`、`scripts/export_snapshots.py`、`tests/test_record_scene.py`
- Generate（Claude 运行）：`web/public/replays/*.jsonl`、`web/public/snapshots/{review-queue,eval-runs,faith-cases,tool-audit}.json`

**Interfaces:**
- YAML 步骤（spec 10.3）：`chat {lane, text}`、`wait_done`、`pick_order {index}`、`confirm_ticket {confirmed}`、`feedback {rating}`、`poll_review {contains, timeout_s}`、`approve {answer, category}`、`snapshot {lane, path}`、`inject {server, delay_seconds}`（脚本重启对应 MCP Server 并设 `MOCK_DELAY_SECONDS`，结束后恢复）、`reset_demo_chunk {contains}`（复用 demo9 的清理逻辑）。
- 输出：首行 header（`scene, title_zh, title_en, recorded_at, git_commit, model`），之后 `{t_ms, lane, channel, event, data}`；所有 SSE 请求带 `debug: true`；HTTP 客户端 `trust_env=False`。
- `export_snapshots.py`：调用 4 个运营台接口写 JSON。

- [ ] **Step 1: 写失败测试**（`tests/test_record_scene.py`）：YAML 解析与校验（未知步骤报错）；用 fake HTTP 传输（`httpx.MockTransport`）跑一个 3 步场景，断言输出行结构、`t_ms` 单调、header 字段；输出逐条通过 `validate_event`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5（Claude）:** 启动完整系统（MySQL、Milvus、Langfuse、两个 MCP Server、服务），依次录制 6 个场景并导出快照；人工检查每个文件（事件完整、回复合理、无密钥）；`flywheel` 场景的知识缺口问题沿用 demo9 的「L2 台灯可以用小爱同学语音控制吗？」。
- [ ] **Step 6: 提交** `feat(ch10): scene recorder and recorded replays`。

---

### Task 12: 回放剧场

**Files:**
- Create: `web/src/theater/scenes.ts`、`GalleryPage.tsx`、`PlayerPage.tsx`、`Timeline.tsx`、`Narration.tsx`、`SplitStage.tsx`、`StrategyStage.tsx`、`deepLink.ts`、`narration.ts` 及测试

**Interfaces:**
- `scenes.ts`：7 个场景的元数据 `{id, titleZh, titleEn, proves, tags, layout: "single"|"split"|"strategies", narration: NarrationCue[]}`；`NarrationCue = {anchor: {event: string; kind?: string; nth?: number; lane?: string}, zh: string, en: string}`。
- `narration.ts`：`resolveCues(lines, cues) → {index, cue}[]`（把锚点解析为事件下标，找不到锚点的 cue 在测试中报错）；`currentCue(resolved, index)`。
- `PlayerPage`：时钟（任务 7）驱动 `replay()`；单屏复用 `DeskPage` 的展示组件（只读）；`split` 布局左客户、右运营（运营侧渲染 `api` 通道的待审快照与核准结果）；`strategies` 布局渲染 `StrategyComparison`。时间轴：章节刻度（cue 位置）、⏩ 间隔标记、播放 / 暂停、0.5/1/2/4×、跳过等待（跳到下一个 cue）。页脚：录制日期、commit 链接（`${REPO_URL}/commit/<hash>`）、模型。
- `deepLink.ts`：解析与生成 `#/theater/<scene>?t=<秒>&view=eng|cust`；拖动和切换视角时更新 URL（`history.replaceState`）。

- [ ] **Step 1: 写失败测试**：每个场景的 cue 都能在对应录制文件中解析到（读取 `web/public/replays/*.jsonl`）；深链接解析与生成往返；播放器打开深链接时定位到对应时间和视角；`split` 布局两侧都有内容；跳过等待跳到下一个 cue；页脚显示 commit 链接。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5（Claude）:** `npm --prefix web run build:replay && npx --prefix web vite preview` 浏览器逐个场景观看，记录需要调整的解说文案或节奏，交给 Codex 修改。
- [ ] **Step 6: 提交** `feat(ch10): replay theater with scenes, timeline and narration`。

---

### Task 13: 一键本地运行（Docker）

**Files:**
- Create: `Dockerfile`、`.dockerignore`、`config/tools.docker.json`、`.env.example`、`scripts/docker_init.sh`
- Modify: `docker-compose.yml`（`full` profile 服务）、`app/knowledge/milvus.py` 或配置（如容器内 Milvus 地址需要，按 `MILVUS_URI` 即可，确认无硬编码）

**Interfaces:**
- `Dockerfile`：`node:22-slim` 构建 `web`（`npm ci && npm run build`）→ `python:3.12-slim` + uv，`uv sync --frozen --no-dev`，复制代码和 `app/web/dist`；默认命令 `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`。
- `docker-compose.yml` 新服务（`profiles: ["full"]`）：`init`（同一镜像，`scripts/docker_init.sh`：等 MySQL、Milvus 健康，`build_kb.py --status` 显示未建库时执行 `build_kb.py`）、`mcp-logistics`、`mcp-aftersales`（同一镜像，`--host 0.0.0.0`；若 MCP Server 不支持 host 参数则加上并测试）、`app`（`depends_on: init: service_completed_successfully`，MCP 健康后启动）。环境：`DATABASE_URL=mysql+asyncmy://aftersales:aftersales@mysql:3306/aftersales?charset=utf8mb4`、`MILVUS_URI=http://milvus-standalone:19530`、`TOOL_POLICY_PATH=/app/config/tools.docker.json`，其余从 `.env`。
- `.env.example`：列出全部变量与说明，必填项标注。

- [ ] **Step 1: 写失败测试**：MCP Server `--host` 参数（若新增）；`tools.docker.json` 能被策略加载器解析且工具集与 `tools.json` 相同（只有 URL 不同）。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5（Claude，docker）:** 在全新卷上验证：`docker compose --profile full down -v`（警告：会删除本项目 MySQL、Milvus 数据卷；先确认 `reset_db.sh` 可恢复，并在执行前提示用户）→ `cp .env.example /tmp/...` 填入现有密钥 → `docker compose --profile full up -d --wait` → 浏览器问 3 个问题。记录首次启动耗时。验证后恢复开发环境（`reset_db.sh`、`build_kb.py`）。
- [ ] **Step 6: 提交** `feat(ch10): one-command local run with docker compose`。

---

### Task 14: 端到端、截图、CI、清理

**Files:**
- Create: `web/playwright.config.ts`、`web/e2e/theater.spec.ts`、`web/e2e/screenshots.spec.ts`、`.github/workflows/backend.yml`、`.github/workflows/web.yml`、`.github/workflows/pages.yml`
- Delete: `app/web/index.html`、`app/web/review_queue.html`、`app/web/eval_runs.html`、`app/web/faith_cases.html`
- Generate: `docs/media/*.png`
- Modify: `CLAUDE.md`

**Interfaces:**
- Playwright 对 `vite preview` 的 replay 构建运行；`page.route("**/*")` 拦截：非 `dist-replay` 静态资源的请求全部记录，测试结束断言为 0（Review Focus 5）。
- `theater.spec.ts`：场景库显示 7 张卡片；`flywheel` 播放、拖到末尾、断言出现「通过」和带引用的回复；其余 6 个场景依次打开并以 4× 播放到结束无报错（监听 `pageerror`）；深链接打开定位正确。
- `screenshots.spec.ts`：固定视口 1440×900，输出 `docs/media/{gallery,desk-xray,gate,flywheel-split,strategies,ops-review}.png`。
- `backend.yml`：`ubuntu-latest`；`docker compose up -d --wait`（MySQL、Milvus；minio 镜像需构建，开启缓存）；`astral-sh/setup-uv`；`uv sync`；`uv run pytest -q`；`.env` 由 workflow 生成（测试不访问上游，填占位值）。
- `web.yml`：`setup-node`（22）、`npm ci`、`typecheck`、`test`、`build`、`build:replay`、`npx playwright install --with-deps chromium`、`npm run e2e`。
- `pages.yml`：推送 `main` 时 `build:replay` 并用 `actions/upload-pages-artifact` + `actions/deploy-pages` 部署。
- `CLAUDE.md`：项目状态加 ch10；常用命令加前端开发、构建、录制、e2e、docker full profile；架构与模块表加 `web/`、`app/tracing.py`、新接口；设计约束加「`debug=false` 时事件流与 ch09 一致」「录制文件必须来自真实系统」「前端状态 = 事件序列 reduce」；删除「聊天页面用 Vibe Coding」的例外说明中关于本章前端的部分（按用户确认）。

- [ ] **Step 1: 写 e2e 测试并运行确认失败**（在旧文件删除前，先让测试指向新构建）。
- [ ] **Step 2–4:** 实现 → 通过；本地 `npm --prefix web run e2e` 全部通过；`uv run pytest -q` 全部通过。
- [ ] **Step 5（Claude，外部操作，执行前向用户确认）:** 推送 `ch10` 分支到远程触发 CI；确认 3 条 workflow 通过；在仓库设置中启用 Pages（Source: GitHub Actions，`gh api` 设置前先问用户）。
- [ ] **Step 6: 提交** `test(ch10): e2e, screenshots and CI workflows; remove legacy pages`。
