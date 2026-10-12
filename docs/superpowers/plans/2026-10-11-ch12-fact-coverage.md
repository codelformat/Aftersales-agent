# ch12 补充：v2 生成评估与事实覆盖率 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增覆盖率裁判，在新知识库上对 v2 题集跑一次 hybrid_rerank 生成评估，报告 Faithfulness 和事实覆盖率。

**Architecture:** 覆盖率裁判与忠实度裁判同构（Prompt + Schema + `@lru_cache` 工厂，只给评估脚本用）。新模块 `evals/coverage.py` 负责参考原文、裁判调用、汇总和渲染；`run_rag_eval.py` 只加参数和接线。裁判先用 30 条标注样例验证，再跑全量。

**Tech Stack:** Python 3.12、LangChain（`with_structured_output(method="function_calling", include_raw=True)`）、SQLAlchemy 异步、pytest + anyio。

**Spec:** `docs/superpowers/specs/2026-10-11-ch12-fact-coverage-design.md`

## Global Constraints

- Codex 写所有代码和样例；Claude 只改 spec、plan、dev-notes、汇总报告，负责审核、运行、提交。
- 不改 `app/` 中生产链路的行为。新增的 Prompt、Schema、工厂只给评估脚本使用。
- v2 题集冻结（`evals/v2/manifest.json`），不改 `evals/rag_eval_v2.jsonl`。
- 不加 `--coverage` 时，`run_rag_eval.py` 的报告和退出码与现在逐字一致。
- 对上游的并发不超过 4；全量运行用 `--concurrency 3`。
- 测试不访问真实上游、嵌入、重排和生产集合；裁判用 `RunnableLambda`。
- 预期 7–9 在全量运行前提交；全量只跑一次，不按结果改 Prompt 或预期。
- 覆盖率裁判验证：按组准确率 ≥ 90% 且没有调用失败。改 Prompt 最多 3 轮，3 轮不达标停下来问用户。
- 提交信息结尾：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## Review Focus

1. 不加 `--coverage` 跑 `--set base`：报告"运行参数"一行不能多出 `coverage`、`save_generation` 两个键（Task 3 测试 `test_params_line_unchanged_without_coverage`）。
2. 裁判返回的编号乱序、重复、缺失或越界：乱序按编号重排，其余记为裁判失败（Task 1 测试 `test_parse_verdict_*`）。
3. 常见问答表的来源键带问题文本（`常见问答 > 分类 > 问题`）：参考原文必须能按这种键查到（Task 1 测试 `test_load_reference_texts_*`）。
4. E 桶多组题被拒答：每组都记未覆盖，不调裁判；分母计入"全部"口径，不计入"已回答"口径（Task 1 测试 `test_judge_coverage_refused_*`、`test_coverage_summary_*`）。
5. `--coverage` 与 `--stage retrieval` 同用：不读参考原文、不调裁判、报告无覆盖率一节（Task 3 测试 `test_coverage_ignored_for_retrieval_stage`）。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/schemas.py` | 改 | 加 `GroupCoverage`、`CoverageVerdict` |
| `app/prompts.py` | 改 | 加 `COVERAGE_JUDGE_SYSTEM_PROMPT`、`coverage_judge_prompt` |
| `app/llm.py` | 改 | 加 `get_coverage_judge()` |
| `evals/coverage.py` | 新建 | 参考原文、`format_references`、`parse_verdict`、`judge_coverage`、`coverage_summary`、`render_coverage` |
| `evals/v2/expectations.py` | 改 | 加 `check_generation_expectations`；`render_expectations` 加 `title` 参数 |
| `evals/run_coverage_judge_eval.py` | 新建 | 裁判验证脚本 |
| `evals/coverage_judge_samples.jsonl` | 新建 | 30 条标注样例 |
| `evals/run_rag_eval.py` | 改 | `--coverage`、`--save-generation`、v2 不写 `faith_cases` |
| `tests/conftest.py` | 改 | 拦截 `get_coverage_judge` |
| `tests/test_coverage.py` | 新建 | `evals/coverage.py` 与 Schema、Prompt |
| `tests/test_coverage_judge_eval.py` | 新建 | 验证脚本与样例结构 |
| `tests/test_v2_expectations.py` | 改 | 预期 7–9 |
| `tests/test_run_rag_eval.py` | 改 | 运行脚本接线 |

---

### Task 1: 覆盖率裁判组件与 `evals/coverage.py`

**Files:**
- Modify: `app/schemas.py`（`FaithVerdict` 之后）、`app/prompts.py`（`faith_judge_prompt` 之后）、`app/llm.py`（`get_faith_judge` 之后）
- Create: `evals/coverage.py`、`tests/test_coverage.py`

**Interfaces:**
- Consumes: `evals.rag_eval_set.EvalSample`（`relevant: tuple[tuple[str, ...], ...]`）、`evals.rag_metrics.ANSWERABLE`、`evals.rag_metrics._cell`、`app.knowledge.retrieval.source_key`
- Produces:
  - `app.schemas.GroupCoverage(n: int, covered: bool, reason: str)`、`app.schemas.CoverageVerdict(groups: list[GroupCoverage])`
  - `app.prompts.coverage_judge_prompt`（变量 `question`、`references`、`answer`）
  - `app.llm.get_coverage_judge() -> Runnable`
  - `evals.coverage.CoverageResult`（字段见下）
  - `async load_reference_texts(keys: Iterable[str]) -> dict[str, str]`
  - `format_references(keys: list[str], texts: dict[str, str]) -> str`
  - `parse_verdict(parsed, k: int) -> tuple[list[bool], list[str]]`
  - `async judge_coverage(sample, strategy, answer, refused, texts, judge) -> CoverageResult`
  - `coverage_summary(results) -> dict[tuple[str, str], dict]`
  - `render_coverage(results) -> str`

- [ ] **Step 0（Claude）：查 Context7**

查 LangChain `with_structured_output(..., method="function_calling", include_raw=True)` 的返回结构（`{"raw", "parsed", "parsing_error"}`，`parsed` 为 Pydantic 实例或 None），把结论写进给 Codex 的任务描述。

- [ ] **Step 1: 写失败的测试 `tests/test_coverage.py`**

```python
import pytest
from langchain_core.runnables import RunnableLambda

from app.db.models import KnowledgeChunk
from app.prompts import coverage_judge_prompt
from app.schemas import CoverageVerdict, GroupCoverage
from evals import coverage as cv
from evals.rag_eval_set import EvalSample

E = EvalSample("E01", "E_multi", "hard", "X5 Pro 续航和保修？", (("k1", "k1b"), ("k2",)))
TEXTS = {"k1": "续航 12 小时", "k2": "保修 365 天"}


def verdict(*items):
    return CoverageVerdict(groups=[GroupCoverage(n=n, covered=c, reason=f"r{n}") for n, c in items])


def judge(result=None, exc=None):
    calls = []

    def run(inputs):
        calls.append(inputs)
        if exc:
            raise exc
        return {"parsed": result, "raw": None}
    return RunnableLambda(run), calls


def test_prompt_renders_three_parts():
    msgs = coverage_judge_prompt.format_messages(question="Q", references="[1] k\nT", answer="A")
    assert msgs[-1].content == "问题：Q\n\n参考事实：\n[1] k\nT\n\n答案：\nA"


def test_format_references_numbers_from_one():
    assert cv.format_references(["k1", "k2"], TEXTS) == "[1] k1\n续航 12 小时\n\n[2] k2\n保修 365 天"


def test_parse_verdict_reorders_by_number():
    assert cv.parse_verdict(verdict((2, False), (1, True)), 2) == ([True, False], ["r1", "r2"])


@pytest.mark.parametrize("items", [[(1, True)], [(1, True), (1, False)], [(1, True), (3, False)],
                                   [(0, True), (1, False)]])
def test_parse_verdict_rejects_bad_numbers(items):
    with pytest.raises(ValueError):
        cv.parse_verdict(verdict(*items), 2)


@pytest.mark.anyio
async def test_judge_coverage_calls_judge_with_first_keys():
    runnable, calls = judge(verdict((1, True), (2, False)))
    r = await cv.judge_coverage(E, "hybrid_rerank", "答", False, TEXTS, runnable)
    assert r.covered == [True, False] and r.reasons == ["r1", "r2"] and r.error is None
    assert r.keys == ["k1", "k2"] and r.strategy == "hybrid_rerank" and r.bucket == "E_multi"
    assert calls == [{"question": E.query, "references": cv.format_references(["k1", "k2"], TEXTS),
                      "answer": "答"}]


@pytest.mark.anyio
async def test_judge_coverage_refused_skips_judge():
    runnable, calls = judge(verdict((1, True), (2, True)))
    r = await cv.judge_coverage(E, "hybrid_rerank", "抱歉", True, TEXTS, runnable)
    assert r.covered == [False, False] and r.reasons == ["拒答", "拒答"] and not calls and r.refused


@pytest.mark.anyio
async def test_judge_coverage_missing_reference_is_error():
    runnable, calls = judge(verdict((1, True), (2, True)))
    r = await cv.judge_coverage(E, "hybrid_rerank", "答", False, {"k1": "x"}, runnable)
    assert r.covered is None and r.error == "参考原文缺失：k2" and not calls


@pytest.mark.anyio
@pytest.mark.parametrize("result, exc", [(None, None), (None, RuntimeError("x")),
                                         (verdict((1, True)), None)])
async def test_judge_coverage_failures(result, exc):
    runnable, _ = judge(result, exc)
    r = await cv.judge_coverage(E, "hybrid_rerank", "答", False, TEXTS, runnable)
    assert r.covered is None and r.error == "覆盖率裁判失败"


def result(sid, bucket, covered, refused=False):
    return cv.CoverageResult(sid, bucket, "hybrid_rerank", ["k"] * (len(covered) if covered else 1),
                             refused, covered, ["r"] * len(covered or []))


def test_coverage_summary_two_scopes():
    rows = [result("E1", "E_multi", [True, False]), result("E2", "E_multi", [True, True]),
            result("E3", "E_multi", [False, False], refused=True), result("E4", "E_multi", None),
            result("B1", "B_model", [True])]
    s = cv.coverage_summary(rows)
    e = s[("hybrid_rerank", "E_multi")]
    assert (e["n"], e["refused"], e["failed"]) == (4, 1, 1)
    assert e["group_all"] == pytest.approx(3 / 6) and e["full_all"] == pytest.approx(1 / 3)
    assert e["group_answered"] == pytest.approx(3 / 4) and e["full_answered"] == pytest.approx(1 / 2)
    a = s[("hybrid_rerank", "ALL")]
    assert a["n"] == 5 and a["group_all"] == pytest.approx(4 / 7)
    assert list(s) == [("hybrid_rerank", "B_model"), ("hybrid_rerank", "E_multi"), ("hybrid_rerank", "ALL")]


def test_coverage_summary_empty_denominator_is_none():
    s = cv.coverage_summary([result("E1", "E_multi", [False], refused=True)])
    assert s[("hybrid_rerank", "E_multi")]["group_answered"] is None


def test_render_coverage_table_and_cases():
    rows = [cv.CoverageResult("E1", "E_multi", "hybrid_rerank", ["k1", "k2"], False, [True, False],
                              ["ok", "漏了|保修"]),
            cv.CoverageResult("E2", "E_multi", "hybrid_rerank", ["k1"], True, [False], ["拒答"]),
            cv.CoverageResult("E3", "E_multi", "hybrid_rerank", ["k1"], False, None, [], "覆盖率裁判失败")]
    md = cv.render_coverage(rows)
    assert md.startswith("## 事实覆盖率") and "## 未覆盖个案" in md
    assert "- E1 [hybrid_rerank] 组 2 k2 → 漏了\\|保修" in md and "- E2 [hybrid_rerank] 拒答" in md
    assert "E3 [hybrid_rerank]" not in md
    assert "| hybrid_rerank | E_multi | 3 | 1 | 1 | 0.333 | 0.000 | 0.500 | 0.000 |" in md


@pytest.mark.anyio
async def test_load_reference_texts_joins_and_filters(db):
    async with db() as s:
        for path, q, kind, status, ans in [
            ("退货政策 > 品类退换货规则", "品类退换货规则", "policy", "done", "第一块"),
            ("退货政策 > 品类退换货规则", "品类退换货规则", "policy", "done", "第二块"),
            ("常见问答 > 运费", "怎么算", "faq", "done", "包邮"),
            ("挖掘问答 > 运费", "怎么算", "mined", "done", "挖掘"),
            ("飞轮 > 运费", "怎么算", "flywheel", "done", "飞轮"),
            ("商品手册 > X5", "续航", "manual", "pending", "未向量化"),
            ("商品手册 > X3", "续航", "manual", "done", "不需要"),
        ]:
            s.add(KnowledgeChunk(category="c", section_path=path, questions=q, answer=ans,
                                 content_type=kind, vectorize_status=status))
        await s.commit()
    got = await cv.load_reference_texts(["退货政策 > 品类退换货规则", "常见问答 > 运费 > 怎么算",
                                         "挖掘问答 > 运费", "飞轮 > 运费", "商品手册 > X5"])
    assert got == {"退货政策 > 品类退换货规则": "第一块\n第二块", "常见问答 > 运费 > 怎么算": "包邮"}
```

注意：`test_render_coverage_table_and_cases` 中 E_multi 行：题数 3、拒答 1、失败 1；全部口径 judged = E1、E2，组 1/3 = 0.333，全覆盖 0/2；已回答口径只有 E1，组 1/2，全覆盖 0/1。

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: FAIL（`ImportError: cannot import name 'coverage_judge_prompt'`）

- [ ] **Step 3: 实现 Schema、Prompt、工厂**

`app/schemas.py`（`FaithVerdict` 之后）：

```python
class GroupCoverage(BaseModel):
    n: int = Field(description="参考事实编号，从 1 开始")
    covered: bool = Field(description="答案是否正确说出这条参考事实中回答问题所需的全部要点")
    reason: str = Field(description="覆盖时写答案中对应的内容；未覆盖时写缺了什么或错在哪里")


class CoverageVerdict(BaseModel):
    groups: list[GroupCoverage] = Field(description="每条参考事实一项，编号与输入一致")
```

`app/prompts.py`（`faith_judge_prompt` 之后）：

```python
COVERAGE_JUDGE_SYSTEM_PROMPT = """你是客服回答的事实覆盖裁判。判断答案是否正确说出了每条参考事实中回答问题所需的内容。

## 判定规则
1. 逐条检查参考事实 [n]。先找出参考原文中回答这道题所需的部分。参考原文中与题目无关的内容，不要求出现在答案中。
2. 答案说出了这部分的全部要点，并且数字、条件、型号、结论与参考原文一致，covered 为 true。
3. 换一种说法可以，例如"两周"与"14 天"。
4. 下列情况 covered 为 false：没有提到；只说了一部分要点；数字或条件与参考原文不一致；把另一个型号的参数说成本型号的参数；只建议联系客服或说无法确认。
5. 答案中参考事实之外的内容不影响判定。是否编造由另一个裁判检查。
6. 答案中的引用编号只是标注，不影响判定。
7. 每条参考事实输出一项，n 与输入编号一致。

## reason
covered 为 true 时，写答案中对应的内容；为 false 时，写缺了什么或错在哪里。"""

coverage_judge_prompt = ChatPromptTemplate.from_messages([
    ("system", COVERAGE_JUDGE_SYSTEM_PROMPT),
    ("human", "问题：{question}\n\n参考事实：\n{references}\n\n答案：\n{answer}"),
])
```

`app/llm.py`（`get_faith_judge` 之后；在顶部 import 中加入 `coverage_judge_prompt`、`CoverageVerdict`）：

```python
@lru_cache
def get_coverage_judge() -> Runnable:
    model = build_extract_model(get_settings())
    return coverage_judge_prompt | model.with_structured_output(
        CoverageVerdict, method="function_calling", include_raw=True
    )
```

本任务不改 `tests/conftest.py`：`run_rag_eval` 还没有导入 `get_coverage_judge`。Task 2、Task 3 各自在 `_block_faith_judge` 中加入拦截。

- [ ] **Step 4: 实现 `evals/coverage.py`**

```python
"""事实覆盖率：参考原文、裁判调用、汇总和报告。"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
import logging

from langchain_core.runnables import Runnable
from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.retrieval import source_key
from app.schemas import CoverageVerdict
from evals.rag_eval_set import EvalSample
from evals.rag_metrics import ANSWERABLE, _cell

logger = logging.getLogger(__name__)
EXCLUDED_CONTENT_TYPES = ("mined", "flywheel")
BUCKET_ORDER = (*ANSWERABLE, "ALL")


@dataclass
class CoverageResult:
    sample_id: str
    bucket: str
    strategy: str
    keys: list[str]
    refused: bool
    covered: list[bool] | None
    reasons: list[str] = field(default_factory=list)
    error: str | None = None


async def load_reference_texts(keys: Iterable[str]) -> dict[str, str]:
    """一个来源键对应多个知识块时，按主键升序拼接。"""
    wanted = set(keys)
    parts = defaultdict(list)
    async with get_sessionmaker()() as s:
        rows = await s.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.vectorize_status == "done",
            KnowledgeChunk.content_type.notin_(EXCLUDED_CONTENT_TYPES),
        ).order_by(KnowledgeChunk.id))
        for row in rows:
            key = source_key(row.section_path or "", row.questions)
            if key in wanted:
                parts[key].append(row.answer)
    return {key: "\n".join(texts) for key, texts in parts.items()}


def format_references(keys: list[str], texts: dict[str, str]) -> str:
    return "\n\n".join(f"[{n}] {key}\n{texts[key]}" for n, key in enumerate(keys, 1))


def parse_verdict(parsed, k: int) -> tuple[list[bool], list[str]]:
    verdict = CoverageVerdict.model_validate(parsed)
    numbers = [g.n for g in verdict.groups]
    if sorted(numbers) != list(range(1, k + 1)):
        raise ValueError(f"覆盖率裁判编号无效：{numbers}")
    ordered = sorted(verdict.groups, key=lambda g: g.n)
    return [g.covered for g in ordered], [g.reason for g in ordered]


async def judge_coverage(sample: EvalSample, strategy: str, answer: str, refused: bool,
                         texts: dict[str, str], judge: Runnable) -> CoverageResult:
    """拒答时每组记未覆盖，不调裁判。参考原文取每组第 1 个来源键。"""
    keys = [group[0] for group in sample.relevant]
    result = CoverageResult(sample.id, sample.bucket, strategy, keys, refused, None)
    missing = [key for key in keys if key not in texts]
    if missing:
        result.error = f"参考原文缺失：{'、'.join(missing)}"
        return result
    if refused:
        result.covered, result.reasons = [False] * len(keys), ["拒答"] * len(keys)
        return result
    try:
        raw = await judge.ainvoke({"question": sample.query,
                                   "references": format_references(keys, texts), "answer": answer})
        if raw["parsed"] is None:
            raise ValueError("覆盖率裁判结果无效")
        result.covered, result.reasons = parse_verdict(raw["parsed"], len(keys))
    except Exception:
        logger.exception("覆盖率裁判失败：%s/%s", sample.id, strategy)
        result.error = "覆盖率裁判失败"
    return result


def _rates(items: list[CoverageResult]) -> tuple[float | None, float | None]:
    judged = [r for r in items if r.covered is not None]
    groups = sum(len(r.covered) for r in judged)
    return (sum(sum(r.covered) for r in judged) / groups if groups else None,
            sum(all(r.covered) for r in judged) / len(judged) if judged else None)


def coverage_summary(results: list[CoverageResult]) -> dict[tuple[str, str], dict]:
    """全部口径计入拒答题；已回答口径排除拒答题。裁判失败的题不进分母。"""
    grouped = defaultdict(list)
    for r in results:
        grouped[(r.strategy, r.bucket)].append(r)
        grouped[(r.strategy, "ALL")].append(r)
    out = {}
    for strategy, bucket in sorted(grouped, key=lambda k: (k[0], BUCKET_ORDER.index(k[1]))):
        items = grouped[(strategy, bucket)]
        group_all, full_all = _rates(items)
        group_answered, full_answered = _rates([r for r in items if not r.refused])
        out[(strategy, bucket)] = {
            "n": len(items), "refused": sum(r.refused for r in items),
            "failed": sum(r.covered is None for r in items),
            "group_all": group_all, "full_all": full_all,
            "group_answered": group_answered, "full_answered": full_answered,
        }
    return out


def _rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_coverage(results: list[CoverageResult]) -> str:
    lines = ["## 事实覆盖率", "",
             "| 策略 | 桶 | 题数 | 拒答 | 裁判失败 | 组覆盖率（全部） | 全覆盖率（全部） | 组覆盖率（已回答） | 全覆盖率（已回答） |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for (strategy, bucket), v in coverage_summary(results).items():
        lines.append(f"| {strategy} | {bucket} | {v['n']} | {v['refused']} | {v['failed']} | "
                     f"{_rate(v['group_all'])} | {_rate(v['full_all'])} | "
                     f"{_rate(v['group_answered'])} | {_rate(v['full_answered'])} |")
    cases = []
    for r in sorted(results, key=lambda r: (r.strategy, r.sample_id)):
        if r.covered is None:
            continue
        if r.refused:
            cases.append(f"- {r.sample_id} [{r.strategy}] 拒答")
            continue
        for n, (key, ok, reason) in enumerate(zip(r.keys, r.covered, r.reasons), 1):
            if not ok:
                cases.append(f"- {r.sample_id} [{r.strategy}] 组 {n} {_cell(key)} → {_cell(reason)}")
    return "\n".join(lines) + "\n\n## 未覆盖个案\n\n" + ("\n".join(cases) or "无") + "\n"
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/test_coverage.py -q`
Expected: 全部 PASS

- [ ] **Step 6: 跑全量，确认没有回归**

Run: `uv run pytest -q`
Expected: 1347 + 新增测试全部 PASS

- [ ] **Step 7: 提交**

```bash
git add app/schemas.py app/prompts.py app/llm.py evals/coverage.py tests/test_coverage.py
git commit -m "feat(ch12): fact coverage judge and evals/coverage.py"
```

---

### Task 2: 裁判验证脚本、30 条标注样例、真实验证

**Files:**
- Create: `evals/run_coverage_judge_eval.py`、`evals/coverage_judge_samples.jsonl`、`tests/test_coverage_judge_eval.py`
- Modify: `tests/conftest.py`（拦截 `run_coverage_judge_eval.get_coverage_judge`）

**Interfaces:**
- Consumes: Task 1 的 `get_coverage_judge`、`format_references`、`parse_verdict`
- Produces: `evals.run_coverage_judge_eval.run_eval() -> int`、`SAMPLES_PATH`、`CASE_LABELS`

样例每行字段：`id`（`V01`–`V30`）、`question`、`references`（`[{"key", "text"}]`，键不重复）、`answer`、`covered`（`list[bool]`，与 `references` 等长）、`cases`（`list[str]`，取自 `CASE_LABELS`）。`cases` 字段是 spec §2.4 之外新增的，用于测试检查 8 种情形各至少 3 条。

- [ ] **Step 1: 写失败的测试 `tests/test_coverage_judge_eval.py`**

```python
import json
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda

from app.schemas import CoverageVerdict, GroupCoverage
from evals import run_coverage_judge_eval as cje

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path, samples):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in samples), encoding="utf-8")
    return path


def sample(i, covered):
    return {"id": f"V{i:02d}", "question": str(i), "answer": str(i), "covered": covered, "cases": [],
            "references": [{"key": f"k{n}", "text": f"t{n}"} for n in range(1, len(covered) + 1)]}


@pytest.mark.anyio
@pytest.mark.parametrize("wrong, fail_one, bad_numbers, expected", [
    (0, False, False, 0), (1, False, False, 0), (3, False, False, 1),
    (0, True, False, 1), (0, False, True, 1),
])
async def test_group_accuracy_and_failures(monkeypatch, tmp_path, capsys, wrong, fail_one, bad_numbers, expected):
    # 10 题共 20 组。错 1 组为 95%，错 3 组为 85%。
    samples = [sample(i, [True, True]) for i in range(10)]
    monkeypatch.setattr(cje, "SAMPLES_PATH", write(tmp_path, samples))
    calls = []

    def run(inputs):
        calls.append(inputs)
        i = int(inputs["answer"])
        if fail_one and i == 0:
            raise RuntimeError("x")
        if bad_numbers and i == 0:
            return {"parsed": CoverageVerdict(groups=[GroupCoverage(n=1, covered=True, reason="r")])}
        groups = [GroupCoverage(n=n, covered=not (i < wrong and n == 1), reason="r") for n in (1, 2)]
        return {"parsed": CoverageVerdict(groups=groups)}

    monkeypatch.setattr(cje, "get_coverage_judge", lambda: RunnableLambda(run))
    assert await cje.run_eval() == expected and len(calls) == 10
    assert calls[0]["references"] == "[1] k1\nt1\n\n[2] k2\nt2"
    assert "覆盖率裁判准确率" in capsys.readouterr().out


def corpus() -> str:
    parts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "knowledge/docs").rglob("*.md"))]
    parts.append((ROOT / "db/seed.sql").read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_samples_file_shape():
    rows = [json.loads(line) for line in cje.SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [r["id"] for r in rows] == [f"V{i:02d}" for i in range(1, 31)]
    assert sum(len(r["covered"]) for r in rows) >= 60
    for r in rows:
        keys = [ref["key"] for ref in r["references"]]
        assert len(keys) == len(set(keys)) == len(r["covered"]) >= 1
        assert all(isinstance(c, bool) for c in r["covered"])
        assert set(r["cases"]) <= set(cje.CASE_LABELS) and r["cases"]
    for label in cje.CASE_LABELS:
        assert sum(label in r["cases"] for r in rows) >= 3, label


def test_samples_do_not_copy_v2_questions():
    v2 = {json.loads(line)["query"] for line in (ROOT / "evals/rag_eval_v2.jsonl").read_text(encoding="utf-8").splitlines()
          if line.strip()}
    rows = [json.loads(line) for line in cje.SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not [r["id"] for r in rows if r["question"] in v2]


def test_reference_lines_come_from_knowledge_sources():
    text = corpus()
    rows = [json.loads(line) for line in cje.SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    missing = [(r["id"], line) for r in rows for ref in r["references"]
               for line in ref["text"].splitlines() if line.strip() and line.strip() not in text]
    assert not missing
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_coverage_judge_eval.py -q`
Expected: FAIL（`ImportError: cannot import name 'run_coverage_judge_eval'`）

- [ ] **Step 3: 实现 `evals/run_coverage_judge_eval.py`**

```python
"""用人工标注样例检查覆盖率裁判，按组计准确率。"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from app.llm import get_coverage_judge
from evals.coverage import format_references, parse_verdict

logger = logging.getLogger(__name__)
SAMPLES_PATH = SCRIPT_DIR / "coverage_judge_samples.jsonl"
CASE_LABELS = ("完全覆盖", "未提到", "部分组覆盖", "形近型号错配", "要点不全", "等价表述", "额外内容",
               "建议联系客服")


async def evaluate_sample(sample: dict, semaphore: asyncio.Semaphore) -> tuple[int, int, bool, str]:
    expected = sample["covered"]
    keys = [ref["key"] for ref in sample["references"]]
    texts = {ref["key"]: ref["text"] for ref in sample["references"]}
    async with semaphore:
        try:
            raw = await get_coverage_judge().ainvoke({
                "question": sample["question"], "references": format_references(keys, texts),
                "answer": sample["answer"],
            })
            if raw["parsed"] is None:
                raise ValueError("裁判结果无效")
            actual, reasons = parse_verdict(raw["parsed"], len(expected))
        except Exception:
            logger.exception("覆盖率裁判评估失败：%s", sample["id"])
            return 0, len(expected), True, f"❌ {sample['id']} 期望 {expected} 实际 调用失败"
    correct = sum(a == e for a, e in zip(actual, expected))
    marker = "✅" if correct == len(expected) else "❌"
    return correct, len(expected), False, (f"{marker} {sample['id']} 期望 {expected} 实际 {actual} "
                                           f"{'；'.join(reasons)}")


async def run_eval() -> int:
    samples = [json.loads(line) for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("样例集为空")
    semaphore = asyncio.Semaphore(4)
    results = await asyncio.gather(*(evaluate_sample(sample, semaphore) for sample in samples))
    for _, _, _, line in results:
        print(line)
    correct = sum(r[0] for r in results)
    total = sum(r[1] for r in results)
    rate = correct / total
    print(f"覆盖率裁判准确率：{rate:.2%} ({correct}/{total} 组)")
    return 0 if rate >= 0.90 and not any(r[2] for r in results) else 1


def main() -> int:
    argparse.ArgumentParser(description="评估覆盖率裁判，正常运行会调用上游模型。").parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        return asyncio.run(run_eval())
    except Exception:
        logger.exception("覆盖率裁判评估执行失败")
        print("覆盖率裁判评估执行失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

`tests/conftest.py` 的 `_block_faith_judge` 末尾加：

```python
    from evals import run_coverage_judge_eval
    monkeypatch.setattr(run_coverage_judge_eval, "get_coverage_judge", _blocked_factory("get_coverage_judge"))
```

- [ ] **Step 4: 编写 `evals/coverage_judge_samples.jsonl`（Codex）**

规则（写进任务描述）：

1. 30 行，`id` 为 `V01`–`V30`，合计不少于 60 组。
2. `references[].text` 从 `knowledge/docs/**/*.md` 或 `db/seed.sql` 逐字复制，每个非空行都必须能在原文中找到。`key` 用知识库的来源键写法（`商品手册 > 蓝牙耳机 > X5 Pro 续航`、`常见问答 > 分类 > 问题`）。
3. `question` 不得与 `evals/rag_eval_v2.jsonl` 的任何 `query` 相同，也不要改写其中的题。
4. `answer` 模仿客服回答（可带引用编号 `[1]`）。
5. `cases` 从 `CASE_LABELS` 中选，一行可多个；8 种情形各至少 3 行。"形近型号错配"用 X5 / X5 Pro、S10 / S10 Pro 这类真实存在的形近型号。"等价表述"必须是原文含义完全相同的换说法。
6. `covered` 按 spec §2.2 的规则标注。

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/test_coverage_judge_eval.py tests/test_coverage.py -q`
Expected: 全部 PASS

- [ ] **Step 6（Claude）：逐条审核 30 条标注**

逐条读 `question`、`references`、`answer`，按 spec §2.2 独立判断每组。与 `covered` 不一致的，写明理由，退回 Codex 改。审核结论记入 dev-notes。

- [ ] **Step 7（Claude）：真实验证**

Run: `uv run python evals/run_coverage_judge_eval.py > .superpowers/sdd/2026-10-11-ch12-fact-coverage/judge_eval_1.log 2>&1; echo exit=$?`
Expected: `exit=0`，准确率 ≥ 90%。
如果不达标：Claude 读错判的条目，写 Prompt 修改要求交 Codex；不改标注。每轮记入 dev-notes，最多 3 轮。

- [ ] **Step 8: 提交**

```bash
git add evals/run_coverage_judge_eval.py evals/coverage_judge_samples.jsonl tests/test_coverage_judge_eval.py tests/conftest.py
git commit -m "eval(ch12): coverage judge samples and validation script"
```

---

### Task 3: 运行脚本接线与生成预期 7–9

**Files:**
- Modify: `evals/run_rag_eval.py`、`evals/v2/expectations.py`、`tests/conftest.py`、`tests/test_run_rag_eval.py`、`tests/test_v2_expectations.py`

**Interfaces:**
- Consumes: Task 1 的 `CoverageResult`、`load_reference_texts`、`judge_coverage`、`coverage_summary`、`render_coverage`、`get_coverage_judge`；`evals.rag_metrics.generation_summary`
- Produces:
  - `evals.v2.expectations.check_generation_expectations(gen: dict[str, dict[str, float]], cov: dict[tuple[str, str], dict]) -> list[tuple[int, str, bool, str]]`
  - `render_expectations(rows, title: str = "冻结前写定的预期") -> str`
  - `run_rag_eval.collect(..., coverage: bool = False)`；`Collected.coverage: list[CoverageResult]`
  - CLI：`--coverage`、`--save-generation PATH`

- [ ] **Step 1: 写失败的预期测试（追加到 `tests/test_v2_expectations.py`）**

```python
def gen(faith=0.95, refusal=0.05):
    return {"hybrid_rerank": {"faithfulness": faith, "false_refusal": refusal}}


def cov(e=0.60, others=(0.90, 0.85, 0.80)):
    buckets = ("A_policy", "B_model", "C_colloquial")
    out = {("hybrid_rerank", b): {"group_all": v} for b, v in zip(buckets, others)}
    out[("hybrid_rerank", "E_multi")] = {"group_all": e}
    return out


def test_generation_expectations_met():
    rows = ex.check_generation_expectations(gen(), cov())
    assert [(n, met) for n, _, met, _ in rows] == [(7, True), (8, True), (9, True)]


def test_generation_expectations_boundaries():
    rows = ex.check_generation_expectations(gen(faith=0.90, refusal=0.10), cov(e=0.80))
    assert [met for _, _, met, _ in rows] == [True, False, True]
    rows = ex.check_generation_expectations(gen(faith=0.899, refusal=0.101), cov())
    assert [met for _, _, met, _ in rows] == [False, True, False]


def test_generation_expectations_missing_data():
    rows = ex.check_generation_expectations({}, {})
    assert [(n, met, actual) for n, _, met, actual in rows] == [
        (7, False, "数据不足"), (8, False, "数据不足"), (9, False, "数据不足")]
    partial = cov()
    partial[("hybrid_rerank", "B_model")]["group_all"] = None
    assert ex.check_generation_expectations(gen(), partial)[1][3] == "数据不足"


def test_render_expectations_title():
    assert ex.render_expectations([], title="冻结前写定的生成预期").startswith("## 冻结前写定的生成预期")
    assert ex.render_expectations([]).startswith("## 冻结前写定的预期")
```

- [ ] **Step 2: 写失败的运行脚本测试（追加到 `tests/test_run_rag_eval.py`）**

```python
from evals import coverage as cv


def gen_patches(monkeypatch, *, refused_ids=()):
    async def understand(query):
        return PLAN

    async def generate(sample, plan, strategy, **kwargs):
        refused = sample.id in refused_ids
        return rm.GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                            True, refused, "答", [], None if refused else True, [], "")

    written = []

    async def write(results, judge_model):
        written.extend(results)
        return 0

    from types import SimpleNamespace
    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "get_chat_model", lambda: object())
    monkeypatch.setattr(rre, "get_faith_judge", lambda: object())
    monkeypatch.setattr(rre, "get_settings", lambda: SimpleNamespace(chat_model="m1"))
    monkeypatch.setattr(rre, "generate_one", generate)
    monkeypatch.setattr(rre, "write_faith_cases", write)
    return written


def v2_patches(monkeypatch, samples):
    monkeypatch.setattr(rre, "load_samples", lambda path=None: samples)
    monkeypatch.setattr(rre, "validate", lambda *a, **k: [])
    monkeypatch.setattr(rre, "check_manifest", lambda *a, **k: None)
    monkeypatch.setattr(rre, "sha256_file", lambda p: "abc123")


def coverage_judge(monkeypatch, fail_ids=()):
    calls, loaded = [], []

    async def load(keys):
        loaded.append(set(keys))
        return {key: "原文" for key in keys}

    def run(inputs):
        calls.append(inputs)
        if any(inputs["question"].startswith(i) for i in fail_ids):
            raise RuntimeError("x")
        k = inputs["references"].count("\n\n") + 1
        return {"parsed": CoverageVerdict(groups=[GroupCoverage(n=n, covered=n == 1, reason="r")
                                                  for n in range(1, k + 1)])}

    monkeypatch.setattr(rre, "load_reference_texts", load)
    monkeypatch.setattr(rre, "get_coverage_judge", lambda: RunnableLambda(run))
    return calls, loaded


async def test_v2_generation_does_not_write_faith_cases(offline_eval, monkeypatch):
    samples, _, _ = offline_eval
    v2_patches(monkeypatch, samples)
    written = gen_patches(monkeypatch)
    args = rre.build_parser().parse_args(["--set", "v2", "--stage", "generation", "--bucket", "B_model",
                                         "--limit", "1"])
    assert await rre.run_eval(args) == 0 and written == []


async def test_base_generation_still_writes_faith_cases(offline_eval, monkeypatch):
    written = gen_patches(monkeypatch)
    args = rre.build_parser().parse_args(["--stage", "generation", "--bucket", "B_model", "--limit", "1"])
    assert await rre.run_eval(args) == 0 and len(written) == 1


async def test_v2_coverage_report_and_expectations(offline_eval, monkeypatch):
    samples, _, report_dir = offline_eval
    v2_patches(monkeypatch, samples)
    gen_patches(monkeypatch, refused_ids={"E01"})
    calls, loaded = coverage_judge(monkeypatch)
    picked = [s for s in samples if s.id in ("B01", "D01", "E01", "E02")]
    monkeypatch.setattr(rre, "load_samples", lambda path=None: picked)
    args = rre.build_parser().parse_args(["--set", "v2", "--stage", "generation", "--coverage"])
    assert await rre.run_eval(args) == 0
    # D 不评；E01 拒答不调裁判。
    assert sorted(c["question"] for c in calls) == ["B01 q", "E02 q"]
    assert loaded == [{ITEM.section_path, "其他来源"}]
    md = next(report_dir.glob("rag_eval_v2_*.md")).read_text()
    assert "## 事实覆盖率" in md and "## 未覆盖个案" in md and "- E01 [hybrid_rerank] 拒答" in md
    assert "## 冻结前写定的生成预期" in md and "## 冻结前写定的预期\n" not in md
    assert "| hybrid_rerank | E_multi | 2 | 1 | 0 | 0.250 | 0.000 | 0.500 | 0.000 |" in md


async def test_coverage_judge_failure_exits_1(offline_eval, monkeypatch):
    samples, _, report_dir = offline_eval
    gen_patches(monkeypatch)
    coverage_judge(monkeypatch, fail_ids=("B01",))
    args = rre.build_parser().parse_args(["--stage", "generation", "--bucket", "B_model", "--limit", "1",
                                         "--coverage"])
    assert await rre.run_eval(args) == 1
    md = next(report_dir.glob("rag_eval_*.md")).read_text()
    assert "B01/hybrid_rerank：覆盖率裁判失败" in md and "## 冻结前写定的生成预期" not in md


async def test_coverage_ignored_for_retrieval_stage(offline_eval, monkeypatch):
    _, _, report_dir = offline_eval
    calls, loaded = coverage_judge(monkeypatch)

    async def understand(query):
        return PLAN

    async def retrieve(query, strategy, **kwargs):
        return Retrieval(PLAN, [ITEM], [ITEM])

    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "retrieve", retrieve)
    args = rre.build_parser().parse_args(["--stage", "retrieval", "--bucket", "B_model", "--limit", "1",
                                         "--coverage"])
    assert await rre.run_eval(args) == 0 and not calls and not loaded
    assert "## 事实覆盖率" not in next(report_dir.glob("*.md")).read_text()


async def test_params_line_unchanged_without_coverage(offline_eval, monkeypatch):
    _, _, report_dir = offline_eval
    gen_patches(monkeypatch)
    args = rre.build_parser().parse_args(["--stage", "generation", "--bucket", "B_model", "--limit", "1"])
    assert await rre.run_eval(args) == 0
    md = next(report_dir.glob("*.md")).read_text()
    assert '"coverage"' not in md and '"save_generation"' not in md and "## 事实覆盖率" not in md


async def test_save_generation_json(offline_eval, monkeypatch, tmp_path):
    samples, _, _ = offline_eval
    gen_patches(monkeypatch)
    coverage_judge(monkeypatch)
    path = tmp_path / "out" / "gen.json"
    picked = [s for s in samples if s.id in ("D01", "E01")]
    monkeypatch.setattr(rre, "load_samples", lambda: picked)
    args = rre.build_parser().parse_args(["--stage", "generation", "--coverage", "--no-write",
                                         "--save-generation", str(path)])
    assert await rre.run_eval(args) == 0
    rows = {r["sample_id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["results"]}
    assert rows["D01"]["coverage"] is None and rows["D01"]["answer"] == "答"
    assert rows["E01"]["coverage"] == {"keys": [ITEM.section_path, "其他来源"], "covered": [True, False],
                                       "reasons": ["r", "r"], "error": None}
```

在文件顶部 import 中加入 `CoverageVerdict, GroupCoverage`（来自 `app.schemas`）。

说明：
- `test_v2_coverage_report_and_expectations` 中 E_multi 行：E01 拒答（2 组都未覆盖），E02 覆盖第 1 组；全部口径组 1/4 = 0.250，全覆盖 0/2；已回答口径组 1/2，全覆盖 0/1。
- `offline_eval` 的 v2 运行要求 `validate` 和 `check_manifest` 打桩，由 `v2_patches` 完成。

- [ ] **Step 3: 运行测试，确认失败**

Run: `uv run pytest tests/test_v2_expectations.py tests/test_run_rag_eval.py -q`
Expected: 新测试 FAIL（`check_generation_expectations` 不存在；`--coverage` 参数无法识别）；已有测试 PASS

- [ ] **Step 4: 实现 `evals/v2/expectations.py` 的改动**

```python
def check_generation_expectations(
    gen: dict[str, dict[str, float]], cov: dict[tuple[str, str], dict],
) -> list[tuple[int, str, bool, str]]:
    """预期 7–9，只看 hybrid_rerank。"""
    rows = []
    g = gen.get("hybrid_rerank")
    desc7 = "hybrid_rerank 的 Faithfulness ≥ 0.90"
    desc8 = "E_multi 的组覆盖率（全部）是 4 个可回答桶中最低的"
    desc9 = "可回答桶误拒率 ≤ 0.10"
    rows.append((7, desc7, False, "数据不足") if g is None else
                (7, desc7, g["faithfulness"] >= 0.90, f"Faithfulness={g['faithfulness']:.3f}"))
    values = [cov.get(("hybrid_rerank", b), {}).get("group_all") for b in ANSWERABLE_V2]
    if any(v is None for v in values):
        rows.append((8, desc8, False, "数据不足"))
    else:
        rows.append((8, desc8, all(values[-1] < v for v in values[:-1]),
                     "；".join(f"{b}={v:.3f}" for b, v in zip(ANSWERABLE_V2, values))))
    rows.append((9, desc9, False, "数据不足") if g is None else
                (9, desc9, g["false_refusal"] <= 0.10, f"误拒率={g['false_refusal']:.3f}"))
    return rows
```

`render_expectations` 签名改为 `render_expectations(rows, title: str = "冻结前写定的预期")`，第一行用 `f"## {title}"`。`ANSWERABLE_V2` 的顺序是 A、B、C、E，E 在最后。

- [ ] **Step 5: 实现 `evals/run_rag_eval.py` 的改动**

1. import：

```python
from app.llm import get_chat_model, get_coverage_judge, get_faith_judge
from evals.coverage import (
    CoverageResult, coverage_summary, judge_coverage, load_reference_texts, render_coverage,
)
from evals.rag_metrics import (
    ANSWERABLE, GenResult, ThresholdRow, generation_summary, is_refusal, render_report,
    score_retrieval, summarize, threshold_sweep,
)
from evals.v2.expectations import (
    check_expectations, check_generation_expectations, render_expectations,
)
```

2. `Collected` 末尾加字段 `coverage: list[CoverageResult]`；`collect` 的 return 改为 `Collected(..., len(fallback_samples), coverage_results)`。

3. `collect` 签名加 `coverage: bool = False`。生成段改为：

```python
    results, coverage_results = [], []
    if do_generation:
        model, judge = get_chat_model(), get_faith_judge()
        texts, coverage_judge = {}, None
        if coverage:
            texts = await load_reference_texts({group[0] for s in samples if s.bucket in ANSWERABLE
                                                for group in s.relevant})
            coverage_judge = get_coverage_judge()
        today = date.today()

        async def generate(sample, strategy):
            async with semaphore:
                try:
                    result = await generate_one(sample, plans[sample.id], strategy,
                                                model=model, judge=judge, today=today)
                except Exception as exc:
                    logger.exception("生成评估失败：%s/%s", sample.id, strategy)
                    failures.append(f"{sample.id}/{strategy}：{type(exc).__name__}")
                    return None
                if result.retrieved and not result.refused and result.faithful is None:
                    failures.append(f"{sample.id}/{strategy}：裁判失败")
                if coverage_judge is not None and sample.bucket in ANSWERABLE:
                    judged = await judge_coverage(sample, strategy, result.answer, result.refused,
                                                  texts, coverage_judge)
                    if judged.error:
                        failures.append(f"{sample.id}/{strategy}：{judged.error}")
                    coverage_results.append(judged)
                return result
```

（其余 `generated = await asyncio.gather(...)`、`write_faith_cases` 部分不变。）

4. `run_eval`：
   - `collect(...)` 调用改 `write_cases=not args.no_write and not v2`，加 `coverage=args.coverage`。
   - 在 `if args.save_rankings:` 块之后加：

```python
        if args.save_generation and do_generation:
            by_key = {(c.sample_id, c.strategy): c for c in got.coverage}
            rows = []
            for r in sorted(results, key=lambda r: (r.strategy, r.sample_id)):
                c = by_key.get((r.sample_id, r.strategy))
                rows.append({**asdict(r), "coverage": None if c is None else {
                    "keys": c.keys, "covered": c.covered, "reasons": c.reasons, "error": c.error}})
            path = Path(args.save_generation)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"results": rows}, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            print(f"生成结果保存：{path}")
```

   - `params` 改为不写未启用的新参数：

```python
        hidden = {"coverage", "save_generation"}
        params = json.dumps({k: v for k, v in vars(args).items()
                             if (v2 or k != "eval_set") and not (k in hidden and not v)},
                            ensure_ascii=False, sort_keys=True)
```

   - 在检索预期（`if v2 and do_retrieval:`）之后加：

```python
        if do_generation and args.coverage:
            report += "\n\n" + render_coverage(got.coverage)
            if v2:
                report += "\n\n" + render_expectations(
                    check_generation_expectations(generation_summary(results), coverage_summary(got.coverage)),
                    title="冻结前写定的生成预期")
```

5. `build_parser` 加：

```python
    parser.add_argument("--coverage", action="store_true", help="生成段加评事实覆盖率")
    parser.add_argument("--save-generation", help="保存每题生成结果和覆盖判定（JSON）")
```

6. `tests/conftest.py` 的 `_block_faith_judge` 加 `monkeypatch.setattr(run_rag_eval, "get_coverage_judge", _blocked_factory("get_coverage_judge"))`。

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/test_v2_expectations.py tests/test_run_rag_eval.py tests/test_coverage.py tests/test_coverage_judge_eval.py -q`
Expected: 全部 PASS

- [ ] **Step 7: 跑全量**

Run: `uv run pytest -q`
Expected: 全部 PASS

- [ ] **Step 8: 提交（预期 7–9 随本提交入库，早于全量运行）**

```bash
git add evals/run_rag_eval.py evals/v2/expectations.py tests/conftest.py tests/test_run_rag_eval.py tests/test_v2_expectations.py
git commit -m "feat(ch12): --coverage and --save-generation; v2 skips faith_cases; expectations 7-9"
```

---

### Task 4（Claude）：全量运行与报告

**Files:**
- Create: `evals/reports/ch12/v2_newkb_generation.md`、`evals/reports/ch12/v2_newkb_generation.json`
- Modify: `evals/reports/ch12_comparison.md`、`dev-notes/ch12.md`、`CLAUDE.md`（常用命令加 `run_coverage_judge_eval.py`、`--coverage`）

**Interfaces:**
- Consumes: Task 2 验证通过的裁判；Task 3 的 CLI。

> 警告：全量运行约 1,700 次 DeepSeek 调用。运行前确认 Task 2 Step 7 退出码 0、Task 3 已提交，否则预期不算"运行前写定"。

- [ ] **Step 1: 确认服务状态**

Run: `docker compose ps --format '{{.Service}} {{.Health}}' && uv run python scripts/build_kb.py --check; echo exit=$?`
Expected: mysql、milvus healthy；`exit=0`

- [ ] **Step 2: 全量运行（后台）**

```bash
uv run python evals/run_rag_eval.py --set v2 --stage generation --gen-strategies hybrid_rerank \
  --coverage --concurrency 3 --save-generation evals/reports/ch12/v2_newkb_generation.json \
  > .superpowers/sdd/2026-10-11-ch12-fact-coverage/gen_run.log 2>&1; echo exit=$? >> .superpowers/sdd/2026-10-11-ch12-fact-coverage/gen_run.log
```

Expected: 报告头部"Query 改写退回原话"≤ 3 题；日志无 `Insufficient Balance`、`402`。退出码 1 时，先看"执行失败"一节：只有个别裁判失败时照实报告；改写退回超过 3 题时结果作废，查明原因后重跑。

- [ ] **Step 3: 复制报告**

把最新的 `evals/reports/rag_eval_v2_*.md` 复制为 `evals/reports/ch12/v2_newkb_generation.md`。

- [ ] **Step 4: E 桶未覆盖原因分类**

在 scratchpad 写一次性脚本，读 `v2_newkb_generation.json`，对 E 桶每个未覆盖组分类：拒答；回答引用的证据（`citations`，按 `source_key(section_path, question)` 换算）中没有该组任一来源键；证据中有但回答漏说；回答说错（裁判理由指出数值、条件或型号不一致）。生成段每题重新改写和检索，排序可能与检索评估的 `evals/reports/ch12/rankings_v2_newkb.json` 不同，以 `citations` 为准。分类口径写进报告。

- [ ] **Step 5: 更新 `evals/reports/ch12_comparison.md`**

末尾加"v2 生成评估"一节：生成表、事实覆盖率表、预期 7–9 对照、E 桶原因分类、噪声说明（单次运行；ch10 两次运行 Faithfulness 相差约 4 个百分点）。

- [ ] **Step 6: 更新 CLAUDE.md 常用命令与 dev-notes，提交并推送**

```bash
git add evals/reports/ch12/v2_newkb_generation.* evals/reports/ch12_comparison.md dev-notes/ch12.md CLAUDE.md
git commit -m "eval(ch12): v2 generation run with fact coverage"
git push
```
