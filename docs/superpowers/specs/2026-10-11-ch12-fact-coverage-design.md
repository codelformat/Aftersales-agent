# ch12 补充：v2 题集生成评估与事实覆盖率

日期：2026-10-11。分支 `ch12`（PR #4）。本文是 `2026-10-11-ch12-harder-eval-set-design.md` 的补充，不改原 spec。

## 0. 背景与目标

ch12 只在新知识库上跑了 v2 题集的检索段。用户要求补跑生成段：

- 用户原话："V2题集在新知识库上再跑一遍faithfulness和答案覆盖率吧"。
- "答案覆盖率"指**事实覆盖率**：回答是否说出了题目需要的每一组事实。现有指标没有这一项，需要新增一个裁判（用户选定）。
- 只跑 `hybrid_rerank`（用户选定）。

Faithfulness 只检查回答有没有编造，不检查回答是否完整。拒答、只答一半的回答，Faithfulness 都可以是 1。事实覆盖率补上这一项。

### 0.1 方法约束

- v2 题集冻结，不改题、不改标注（`evals/v2/manifest.json`）。
- 生成段沿用 ch04 基线（`generate_one`：`chat_prompt` + `ch04_tools`、真实第 1 次调用、自评、第 2 次调用、忠实度裁判），不改。
- 覆盖率裁判先用标注样例验证，准确率达标后才跑全量。
- 3 条预期写在本文 §4.2，在全量运行之前提交。只跑一次，结果照实报告，不按结果改裁判 Prompt 或预期。

### 0.2 不做的事

- 不改 `app/` 中生产链路的任何行为。新增的裁判 Prompt、工厂、Schema 只给评估脚本使用。
- 不跑 dense、bm25、hybrid 的生成段。
- 不在旧知识库上跑生成段。
- 不写 `faith_cases`（见 §3.3）。

## 1. 指标定义

### 1.1 逐题判定

对可回答桶（A、B、C、E）的每道题，裁判逐组判断回答是否**正确说出**该组事实：

- **组**：题目 `relevant` 的一个外层元素。E 桶一题有 2–3 组，其他桶一题 1 组。
- **参考原文**：组内第 1 个来源键对应的知识块正文。一个来源键对应多个知识块时（实测 `售后手册 > 维修 > 维修流程` 2 块、`退货政策 > 品类退换货规则` 4 块），按主键升序拼接。查知识块时排除 `mined` 和 `flywheel` 来源，与检索评估一致。
- **覆盖**：回答说出了参考原文中回答这道题所需的全部要点，数字、条件、型号与原文一致。
- **未覆盖**：没有说到；只说到一部分要点；数值或条件与原文不一致；把形近型号的参数当作本型号的参数。

### 1.2 不调裁判的情况

- 回答为拒答（`is_refusal` 为真）：所有组记为未覆盖，不调裁判。
- 回答未检索（模型没有调用 `query_faq`）但没有拒答：照常调裁判。
- D 桶：不算覆盖率。D 桶仍报告"D 正确拒答率"。

### 1.3 汇总

按桶和全部可回答题（ALL）分别汇总：

| 列 | 定义 |
|---|---|
| 题数 | 该桶的题数 |
| 拒答 | 拒答题数 |
| 裁判失败 | 裁判调用失败或输出无效的题数，不进入下面的分母 |
| 组覆盖率（全部） | 覆盖组数 ÷ 总组数。拒答题的组计入分母 |
| 全覆盖率（全部） | 所有组都覆盖的题数 ÷ 题数。拒答题计入分母 |
| 组覆盖率（已回答） | 同上，只算没有拒答的题 |
| 全覆盖率（已回答） | 同上，只算没有拒答的题 |

"全部"口径反映用户实际得到的信息量。"已回答"口径排除拒答，只看回答本身的完整度。

## 2. 覆盖率裁判

### 2.1 组件

| 组件 | 位置 | 说明 |
|---|---|---|
| `COVERAGE_JUDGE_SYSTEM_PROMPT`、`coverage_judge_prompt` | `app/prompts.py` | 规则见 §2.2 |
| `GroupCoverage`、`CoverageVerdict` | `app/schemas.py` | 见 §2.3 |
| `get_coverage_judge()` | `app/llm.py` | 与 `get_faith_judge()` 相同：`build_extract_model`（关闭思考）+ `with_structured_output(CoverageVerdict, method="function_calling", include_raw=True)`，`@lru_cache` |

Human 消息格式：

```
问题：{question}

参考事实：
{references}

答案：
{answer}
```

`references` 每组一段，格式为 `[n] <来源键>\n<参考原文>`，n 从 1 开始，各段之间空一行。

### 2.2 判定规则（写进 System Prompt）

1. 逐条检查参考事实 [n]。先找出参考原文中回答这道题所需的部分，参考原文中与题目无关的内容不要求出现在答案中。
2. 答案说出了这部分的全部要点，并且数字、条件、型号、结论与参考原文一致，判为覆盖。
3. 换一种说法可以，例如"两周"与"14 天"、"质量问题"与"非人为损坏"在原文含义相同时。
4. 下列情况判为未覆盖：没有提到；只说了一部分要点；数字或条件与参考原文不一致；把另一个型号的参数说成本型号的参数；只建议联系客服或说无法确认。
5. 答案中参考事实之外的内容不影响判定。是否编造由另一个裁判检查。
6. 引用编号 `[n]` 只是标注，不影响判定。
7. 每条参考事实输出一项，编号与输入一致。

### 2.3 输出结构

```python
class GroupCoverage(BaseModel):
    n: int            # 参考事实编号，从 1 开始
    covered: bool
    reason: str       # 一句话：覆盖时写对应的答案内容，未覆盖时写缺了什么或错在哪里

class CoverageVerdict(BaseModel):
    groups: list[GroupCoverage]
```

`groups` 的编号集合必须正好是 1..k（k 为组数），每个编号出现 1 次；否则记为裁判失败。（设计讨论时写的是 `covered: list[bool]`；改为带编号和理由的对象列表，便于对齐和查错。）

### 2.4 裁判验证

- 样例 `evals/coverage_judge_samples.jsonl`，30 条，每行：`id`（`V01`–`V30`）、`question`、`references`（`[{"key", "text"}]`）、`answer`、`covered`（`list[bool]`，与 `references` 等长）。
- 合计不少于 60 组。必须包含下列情形，每种至少 3 条：
  1. 单组完全覆盖；
  2. 单组未提到；
  3. 多组部分覆盖（至少 1 组覆盖、至少 1 组未覆盖）；
  4. 形近型号的数值错配（例如把 X5 的参数说成 X5 Pro 的）；
  5. 只说一部分要点；
  6. 换一种说法的等价表述（应判覆盖）；
  7. 答案带有参考事实之外的内容（不影响判定）；
  8. 只建议联系客服或说无法确认。
- 参考原文从当前知识库原文复制（`knowledge/docs/` 和 `db/seed.sql`）。题目不得复制 v2 题集（`evals/rag_eval_v2.jsonl`）的原题，避免按 v2 题集调裁判。
- Codex 编写样例；Claude 逐条审核标注，审核结论写入 dev-notes。
- `evals/run_coverage_judge_eval.py`：并发 4，按组计准确率。准确率 ≥ 90% 且没有调用失败时退出码 0，否则 1。打印每条的期望、实际和理由。
- 未达标时：可以改 Prompt 和重跑验证，最多 3 轮。每轮记入 dev-notes。改 Prompt 不改样例标注；如果发现标注错误，单独记录理由后再改。3 轮仍不达标，停下来问用户。

## 3. 评估脚本改动 `evals/run_rag_eval.py`

### 3.1 `--coverage`

- 新增参数 `--coverage`（store_true）。只在生成段（`--stage generation` 或 `all`）生效。
- 不加 `--coverage` 时，报告和退出码与现在逐字一致。
- 加 `--coverage` 时：
  1. 生成开始前，从 MySQL 一次读出所有被引用来源键的参考原文（§1.1）。某个来源键查不到知识块时，记入执行失败，该题不评覆盖率。
  2. 每道可回答题在 `generate_one` 返回后调用覆盖率裁判（同一信号量，并发上限不变）。
  3. 报告"生成"一节之后加"事实覆盖率"表（§1.3）和"未覆盖个案"列表（每行：题号、组编号、来源键、裁判理由；拒答题只写一行"拒答"）。
  4. 裁判失败的题记入执行失败，退出码 1（与忠实度裁判失败相同）。

### 3.2 `--save-generation <path>`

把每题生成结果写成 JSON（问题、回答、引用、忠实度判定、每组覆盖判定和理由），供汇总报告分析。不加时不写。

### 3.3 `--set v2` 不写 `faith_cases`

`faith_cases` 是生产台账，记录基础题集上的编造个案。v2 题集的结果只进报告，不写库。`--set v2` 时跳过 `write_faith_cases`，与 `--no-write` 无关。`--set base` 行为不变。

### 3.4 生成段预期

`--set v2` 且加 `--coverage` 时，报告末尾加"冻结前写定的生成预期"表，编号接检索预期（7–9），格式与 `render_expectations` 相同。见 §4.2。

## 4. 运行与报告

### 4.1 运行

1. 运行 `uv run python evals/run_coverage_judge_eval.py`，确认退出码 0。
2. 运行：

   ```bash
   uv run python evals/run_rag_eval.py --set v2 --stage generation \
     --gen-strategies hybrid_rerank --coverage --concurrency 3 \
     --save-generation evals/reports/ch12/v2_newkb_generation.json
   ```

   调用量：300 次改写、300 次第 1 次调用、约 300 次自评、约 300 次第 2 次调用、约 240 次忠实度裁判、约 240 次覆盖率裁判，合计约 1,700 次 DeepSeek 调用。

3. 检查报告头部"Query 改写退回原话"题数。超过 3 题时退出码 1，结果作废，查明原因（例如余额）后重跑。
4. 报告复制到 `evals/reports/ch12/v2_newkb_generation.md`。

### 4.2 事先写定的预期（全量运行之前提交）

| 编号 | 预期 |
|---|---|
| 7 | hybrid_rerank 的 Faithfulness ≥ 0.90 |
| 8 | E_multi 的组覆盖率（全部）是 4 个可回答桶中最低的 |
| 9 | 可回答桶误拒率 ≤ 0.10 |

预期只用于对照，不是要达到的目标。不符合时照实报告。

### 4.3 噪声说明

生成结果每次运行不同。ch10 曾观察到同一份代码两次运行的 Faithfulness 相差约 4 个百分点。本次只跑一次，报告中写明。

### 4.4 汇总报告

在 `evals/reports/ch12_comparison.md` 末尾加一节"v2 生成评估"：

- 生成表（Faithfulness、误拒率、D 正确拒答率）；
- 事实覆盖率表（按桶）；
- 预期 7–9 的对照；
- E 桶未覆盖的原因分类（检索没召回该组、召回了但回答漏说、回答说错），用 `--save-generation` 的数据和 v2 检索 rankings 统计；
- 噪声说明。

## 5. 测试

测试不调真实上游。裁判用 `RunnableLambda`；`tests/conftest.py` 在 `run_rag_eval` 和 `run_coverage_judge_eval` 中拦截 `get_coverage_judge`。

- Schema：`CoverageVerdict` 解析；编号缺失、重复、超出范围时判为无效。
- 参考原文：一个来源键多个知识块时按主键拼接；排除 `mined`、`flywheel`；查不到时报失败。
- 逐题：拒答不调裁判，所有组未覆盖；未检索但回答时调裁判；裁判异常记失败。
- 汇总：两种口径的分子分母；裁判失败不进分母；按桶和 ALL。
- 运行脚本：不加 `--coverage` 时报告与现在一致（已有测试保持通过）；`--set v2` 不调用 `write_faith_cases`；`--set base` 仍调用；`--save-generation` 写出的 JSON 字段。
- 预期 7–9：符合、不符合、数据不足。
- `run_coverage_judge_eval.py`：按组计准确率；调用失败时退出码 1。
