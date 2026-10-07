# 自评"全有或全无"：部分可答的问题整条被拒答

- 状态：搁置
- 发现：ch04 Task 12 回归（`evals/run_chat_samples.py`），2026-10-07
- 相关 spec：`docs/superpowers/specs/2026-10-07-ch04-hybrid-retrieval-rerank-design.md` §7.3、§7.4

## 1. 现象

用户一句话问了两个要点，知识库只能回答其中一个时，系统撤掉全部证据并拒答，能回答的要点也不回答。

| 用户问题 | 知识库能答 | 知识库不能答 | 实际回复 |
|---|---|---|---|
| 我退货的话能退多少钱？什么时候到账？ | 到账时间（退款 1–3 个工作日，银行卡再 1–5 个工作日） | 能退多少钱（取决于具体订单） | 只有拒答句，建议转人工 |
| 耳机坏了，退货和换货有什么区别？我该选哪个？ | 退货条件和时限；换货流程 | 两者的对比和"该选哪个" | 只有拒答句，建议转人工 |

自评理由（`low_confidence_questions.reason`）：
- "证据[1][2]写明了退款到账时限，但都没有写退款金额（能退多少钱），缺该要点。"
- "证据[1]只写了退货的时限与条件，证据[2]只写了换货的流程；两者都没有说明退货与换货的区别……"

ch03 时这两条都能部分回答。这是 ch04 引入的退化。

## 2. 根因

自评只有两档（spec §7.3）：

- Prompt 规则 2："问题有几个要点时，每个要点都有证据，才判为够用。"
- `useful=false` 时（spec §7.4），聊天服务把本轮所有 `query_faq` 的工具消息替换为 `{"evidence": [], "answerable": false}`，模型看不到任何证据。

所以只要缺一个要点，整条知识库证据就被撤掉。

## 3. 当时的决定

用户选择方案 B：本章只把重排门槛从 0.40 降到 0.20（修复另一个问题：宽泛口语问题的正确证据被门槛过滤）。本问题搁置，记录方案 A 供后续章节实施。

理由：方案 A 要改 spec 的自评设计、聊天服务、评估代码和自评 Prompt，并重跑生成段评估。本章已接近收尾，改动范围较大。

## 4. 建议方案（方案 A）

### 4.1 自评改为三档

```python
class SelfCheck(BaseModel):
    coverage: Literal["full", "partial", "none"]
    answerable_points: list[str]   # 证据能回答的要点
    missing_points: list[str]      # 证据不能回答的要点
    reason: str
```

- `full`：全部要点都有证据。处理同现在的 `useful=true`。
- `partial`：部分要点有证据。保留证据，模型回答能答的部分，对缺的部分明确说明。
- `none`：没有要点有证据。处理同现在的 `useful=false`。
- 证据为空时直接判 `none`（不调用 LLM），与现在相同。

### 4.2 `partial` 的处理

1. 工具消息保留渲染后的证据，并追加缺失要点：`{"ok": true, "data": {"evidence": [...], "missing_points": ["能退多少钱"]}}`。
2. System Prompt"拒答"一节增加：结果中有 `missing_points` 时，先回答有证据的部分并标注引用，再对每个缺失要点说"这一点我没有在知识库中找到可靠依据"，建议转人工。
3. 入池：写一行 `low_confidence_questions`，`source='self_check'`，`reason` 写缺失要点。DDL 不变。
4. SSE `citations` 事件增加字段 `partial: true` 和 `missing_points`，前端可以选择性展示。

### 4.3 改动范围

| 位置 | 改动 |
|---|---|
| spec §7.3、§7.4、§7.6 | 三档定义、`partial` 处理、SSE 字段 |
| `app/schemas.py` | `SelfCheck` 改为三档 |
| `app/prompts.py` | 自评 Prompt 规则 2 改为分要点判断；System Prompt 拒答一节 |
| `app/services/grounding.py` | `self_check` 返回三档；`render_evidence` 支持 `missing_points` |
| `app/services/chat.py` | 按三档分支处理 |
| `evals/run_rag_eval.py`、`evals/rag_metrics.py` | 生成段识别部分拒答；新增"部分回答率"指标；部分回答的答案照常送裁判（只判有证据的部分） |
| 测试 | `tests/test_grounding.py`、`tests/test_chat_api.py`、`tests/test_run_rag_eval.py` |

### 4.4 风险

- 自评需要正确拆分要点。拆分错误时，`partial` 可能把本该拒答的问题变成"答一半"。
- 部分回答的忠实度依赖模型遵守"只答有证据的部分"。需要用 Faithfulness 验证。

## 5. 验收方法

1. 上表 2 条问题：回复先回答有证据的部分（带引用），再对缺失要点说明查不到；问题池各新增 1 行，`reason` 写明缺失要点。
2. `evals/run_rag_eval.py --stage generation`：Faithfulness 不低于修改前（ch04 最终报告 `hybrid_rerank` 0.906）；误拒率下降；D 正确拒答率不低于 0.95。
3. 在 `evals/rag_eval.jsonl` 中增加"部分可答"样例（例如 E 桶中一问可答、一问不可答的题），统计部分回答率。
