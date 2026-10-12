# ch11 困难评估集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 合成一套 240 题的困难评估集（4 桶各 60 题），冻结后用现有四策略评估脚本跑检索和生成，与原题集并列写入 README。

**Architecture:** `evals/hard_set/` 是独立的合成包：`corpus` 从 MySQL 读知识块并按来源键合并，`seeds` 确定性地生成种子池，`prompts` 放出题和标注 Prompt，`filters` 做机械过滤，`labels` 做标注补全检查，`pipeline` 编排重试和补位，`expectations` 对照冻结前写定的预期。`scripts/synth_hard_set.py` 是命令行入口。`evals/run_rag_eval.py` 增加 `--set hard` 和 `--gen-repeat`。合成代码不导入任何检索模块。

**Tech Stack:** Python 3.12、SQLAlchemy async、LangChain（`ChatPromptTemplate`、`with_structured_output(method="json_mode")`、`RunnableLambda`）、pytest。

**Spec:** `docs/superpowers/specs/2026-10-10-ch11-hard-eval-set-design.md`

## Global Constraints

- 合成代码（`evals/hard_set/`、`scripts/synth_hard_set.py`）不导入 `app.knowledge.retrieval`、`app.knowledge.milvus`、`app.knowledge.rerank`、`app.knowledge.embeddings`（含传递导入）。
- 种子选择、过滤和标注都不看任何检索结果。
- 固定随机种子 `SEED = 20261010`。
- 4 个桶：`H1_sibling`、`H2_colloquial`、`H3_buried`、`H4_specific`，每桶 60 题；题号 `H1_01`–`H4_60`；`difficulty` 一律为 `hard`；每题恰好 1 组来源键，组内最多 3 个键。
- 同一桶内一个目标段落最多出 2 题。
- 出题和标注用 `app.llm.build_extract_model(get_settings())`（DeepSeek，关闭思考），`with_structured_output(..., method="json_mode", include_raw=True)`。重试只靠 `ChatOpenAI` 的 `max_retries`（SDK 指数回退），不自写重试循环；"同一种子最多 3 次尝试"是内容淘汰后的重新生成，不是故障重试。
- 合成并发默认 4，评估并发 3（高于 4 触发上游限流）。
- 测试不访问上游模型、Milvus、重排；LLM 用 `RunnableLambda` 替代。
- 原题集 `evals/rag_eval.jsonl` 和 `--set base` 的行为、报告格式不变。
- 代码由 Codex 编写（`codex exec ... -m gpt-6.1-sol -c model_reasoning_effort="high"`，命令见 CLAUDE.md）；额度用完时改派 Sonnet 子代理。Claude 检查 diff、跑测试、提交。

## Review Focus

1. **误覆盖冻结题集**：`evals/rag_eval_hard.jsonl` 已存在时，不带 `--force` 运行合成脚本必须退出码 1 且不改任何文件（Task 4 测试）。
2. **型号别名**：问题写成 `x3pro`、`X3pro`、`X3-Pro` 时，必须识别为 `X3 Pro`，不能被当成 `X3`（Task 2 测试）。
3. **同一来源键的多个分块**（`售后手册 > 维修 > 维修流程` 2 块、`退货政策 > 品类退换货规则` 4 块）：合并为 1 个候选，来源键组不重复（Task 1 测试）。
4. **LLM 返回异常**：`parsed` 为 None、抛异常、标注编号越界或为 0，都按一次失败尝试处理，不中断整批（Task 3、Task 4 测试）。
5. **桶与题集不匹配**：`--set hard --bucket A_policy` 或 `--set base --bucket H1_sibling` 必须退出码 1 并提示（Task 5 测试）。

---

### Task 1: 知识库视图与种子池（`corpus.py`、`seeds.py`）

**Files:**
- Create: `evals/hard_set/__init__.py`（空文件）
- Create: `evals/hard_set/corpus.py`
- Create: `evals/hard_set/seeds.py`
- Test: `tests/test_hard_set_seeds.py`

**Interfaces:**
- Produces:
  - `corpus.Chunk`（frozen dataclass）：`key: str`、`section_path: str`、`title: str`、`text: str`、`category: str`、`source: str`、`model: str | None`、`aspect: str | None`
  - `corpus.chunk_key(section_path: str, questions: str) -> str`
  - `corpus.build_chunks(rows: list[tuple[str, str, str]], lexicon: Lexicon) -> list[Chunk]`（rows 为按 id 排序的 `(section_path, questions, answer)`）
  - `async corpus.load_chunks(lexicon: Lexicon) -> list[Chunk]`
  - `corpus.DEFAULT_MODELS = ("X3", "T3", "L1", "C5")`
  - `seeds.SEED = 20261010`、`seeds.BUCKETS`、`seeds.Seed`（frozen dataclass：`bucket`、`target_key`、`slot`、`distractor_keys: tuple[str, ...]`）
  - `seeds.build_pools(chunks: list[Chunk], *, seed: int = SEED) -> dict[str, list[Seed]]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hard_set_seeds.py
from app.db.models import KnowledgeChunk
from app.knowledge.query import load_lexicon
from app.knowledge.retrieval import FAQ_TABLE_SOURCE, source_key
from evals.hard_set import corpus, seeds

LEX = load_lexicon()


def manual_rows():
    rows = []
    for category, models in (("蓝牙耳机", ("X3", "X3 Pro", "X5")), ("台灯", ("L1", "L1 Pro", "L2"))):
        for model in models:
            for aspect in ("参数", "续航", "保修", "退换规则", "常见故障"):
                rows.append((f"商品手册 > {category} > {model} {aspect}", f"{model} {aspect}",
                             f"{model} 的{aspect}说明，数值 {len(model)}。"))
    return rows


def general_rows():
    return [
        ("商品手册", "商品手册", "手册简介"),
        ("售后手册 > 维修 > 维修流程", "维修流程", "第一块"),
        ("售后手册 > 维修 > 维修流程", "维修流程", "第二块"),
        ("售后手册 > 维修 > 保修期", "保修期", "保修期从签收次日开始计算"),
        ("退货政策 > 退货条件 > 七天无理由退货", "七天无理由退货", "签收后 7 天内"),
        ("退货政策 > 退货条件 > 质量问题退货", "质量问题退货", "签收后 15 天内"),
        ("退货政策 > 品类退换货规则", "品类退换货规则", "表格一"),
        ("退货政策 > 品类退换货规则", "品类退换货规则", "表格二"),
        ("退货政策 > 特殊情况 > 价格保护", "价格保护", "签收后 7 天内降价可申请价格保护"),
        ("常见问答 > 退换货", "退货政策是什么？", "7 天无理由"),
        ("常见问答 > 售后维修", "商品坏了怎么保修？", "保修期内免费维修"),
        ("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "耳机充满电能用多久？", "约 6 小时"),
        ("商品FAQ > 台灯 > 台灯有几档亮度？", "台灯有几档亮度？", "5 档"),
    ]


def chunks():
    return corpus.build_chunks(manual_rows() + general_rows(), LEX)


def test_chunk_key_matches_production_rule():
    assert corpus.FAQ_TABLE_SOURCE == FAQ_TABLE_SOURCE
    for path, q in (("常见问答 > 运费", "运费怎么算？"), ("退货政策 > 退款 > 退款时间", "退款时间")):
        assert corpus.chunk_key(path, q) == source_key(path, q)


def test_build_chunks_merges_split_chunks_and_parses_models():
    by_key = {c.key: c for c in chunks()}
    assert by_key["售后手册 > 维修 > 维修流程"].text == "第一块\n第二块"
    assert by_key["退货政策 > 品类退换货规则"].text == "表格一\n表格二"
    assert len([c for c in chunks() if c.key == "售后手册 > 维修 > 维修流程"]) == 1
    pro = by_key["商品手册 > 蓝牙耳机 > X3 Pro 续航"]
    assert (pro.model, pro.aspect, pro.category, pro.source) == ("X3 Pro", "续航", "蓝牙耳机", "商品手册")
    faq = by_key["常见问答 > 退换货 > 退货政策是什么？"]
    assert (faq.title, faq.category, faq.model) == ("退货政策是什么？", "通用", None)
    assert by_key["商品FAQ > 台灯 > 台灯有几档亮度？"].category == "台灯"


def test_pools_are_deterministic_and_slot_ordered():
    a, b = seeds.build_pools(chunks()), seeds.build_pools(chunks())
    assert a == b and set(a) == set(seeds.BUCKETS)
    for pool in a.values():
        slots = [s.slot for s in pool]
        assert slots == sorted(slots)                     # 全部 slot 1 在 slot 2 之前
        per_target = {}
        for s in pool:
            per_target[s.target_key] = per_target.get(s.target_key, 0) + 1
        assert max(per_target.values()) <= 2
    assert seeds.build_pools(chunks(), seed=1) != a


def test_pool_contents_per_bucket():
    pools = seeds.build_pools(chunks())
    h1 = [s for s in pools["H1_sibling"] if s.target_key == "商品手册 > 蓝牙耳机 > X3 Pro 续航"][0]
    assert set(h1.distractor_keys) == {"商品手册 > 蓝牙耳机 > X3 续航", "商品手册 > 蓝牙耳机 > X5 续航"}
    h2_targets = {s.target_key for s in pools["H2_colloquial"]}
    assert "商品手册 > 蓝牙耳机 > X3 常见故障" in h2_targets
    assert "商品手册 > 蓝牙耳机 > X3 参数" not in h2_targets
    assert not h2_targets & {"商品手册", "退货政策 > 品类退换货规则"}
    h3_targets = {s.target_key for s in pools["H3_buried"]}
    assert "退货政策 > 特殊情况 > 价格保护" in h3_targets
    assert not any(k.startswith("商品手册") for k in h3_targets)
    h4 = {s.target_key: s for s in pools["H4_specific"]}
    assert set(h4["商品手册 > 蓝牙耳机 > X3 保修"].distractor_keys) == {
        "售后手册 > 维修 > 保修期", "常见问答 > 售后维修 > 商品坏了怎么保修？"}
    assert "退货政策 > 品类退换货规则" in h4["商品手册 > 台灯 > L2 退换规则"].distractor_keys
    assert h4["商品手册 > 蓝牙耳机 > X3 Pro 续航"].distractor_keys == (
        "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？",)
    assert "商品手册 > 蓝牙耳机 > X3 续航" not in h4          # 默认型号不出 FAQ 冲突题
    assert "商品手册 > 蓝牙耳机 > X5 常见故障" not in h4


def test_pool_round_robin_across_categories():
    pool = seeds.build_pools(chunks())["H1_sibling"]
    first_two = [s.target_key.split(" > ")[1] for s in pool[:2]]
    assert set(first_two) == {"蓝牙耳机", "台灯"}


async def test_load_chunks_reads_doc_sources_only(db):
    async with db() as s:
        s.add_all([
            KnowledgeChunk(category="商品手册 > 蓝牙耳机", questions="X3 续航", answer="6 小时",
                           section_path="商品手册 > 蓝牙耳机 > X3 续航", content_type="manual",
                           vectorize_status="done"),
            KnowledgeChunk(category="对话挖掘 > 发票", questions="能开专票吗？", answer="可以",
                           section_path="对话挖掘 > 发票", content_type="mined", vectorize_status="done"),
            KnowledgeChunk(category="飞轮补充", questions="q", answer="a",
                           section_path="飞轮补充 > x", content_type="flywheel", vectorize_status="done"),
        ])
        await s.commit()
    got = await corpus.load_chunks(LEX)
    assert [c.key for c in got] == ["商品手册 > 蓝牙耳机 > X3 续航"]
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_hard_set_seeds.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'evals.hard_set'`

- [ ] **Step 3: 实现 `corpus.py`**

```python
# evals/hard_set/corpus.py
"""困难评估集的知识库视图：按来源键合并分块，解析型号和方面。不导入检索模块。"""

from dataclasses import dataclass

from sqlalchemy import select

from app.config import GENERAL_CATEGORY
from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.chunking import PATH_SEP, product_category_of
from app.knowledge.query import Lexicon

# 与 app.knowledge.retrieval.FAQ_TABLE_SOURCE 一致，测试比对两者。
FAQ_TABLE_SOURCE = "常见问答"
DOC_CONTENT_TYPES = ("manual", "faq", "policy")
# 商品手册约定：商品FAQ 中未注明型号的参数对应这 4 个型号。
DEFAULT_MODELS = ("X3", "T3", "L1", "C5")


@dataclass(frozen=True)
class Chunk:
    key: str
    section_path: str
    title: str
    text: str
    category: str
    source: str
    model: str | None
    aspect: str | None


def chunk_key(section_path: str, questions: str) -> str:
    """与 app.knowledge.retrieval.source_key 同一规则。"""
    if section_path.startswith(FAQ_TABLE_SOURCE + PATH_SEP):
        return f"{section_path}{PATH_SEP}{questions}"
    return section_path


def _model_aspect(section_path: str, lexicon: Lexicon) -> tuple[str | None, str | None]:
    parts = section_path.split(PATH_SEP)
    if parts[0] != "商品手册" or len(parts) != 3:
        return None, None
    last = parts[2]
    for model in sorted(lexicon.model_categories, key=len, reverse=True):
        if last.startswith(model + " "):
            return model, last[len(model) + 1:].strip()
    return None, None


def build_chunks(rows: list[tuple[str, str, str]], lexicon: Lexicon) -> list[Chunk]:
    order: list[str] = []
    merged: dict[str, dict] = {}
    for section_path, questions, answer in rows:
        path = section_path or ""
        key = chunk_key(path, questions)
        if key not in merged:
            order.append(key)
            title = questions if path.startswith(FAQ_TABLE_SOURCE + PATH_SEP) else path.split(PATH_SEP)[-1]
            merged[key] = {"path": path, "title": title, "texts": []}
        merged[key]["texts"].append(answer)
    out = []
    for key in order:
        item = merged[key]
        model, aspect = _model_aspect(item["path"], lexicon)
        out.append(Chunk(key=key, section_path=item["path"], title=item["title"],
                         text="\n".join(item["texts"]),
                         category=product_category_of(item["path"]) or GENERAL_CATEGORY,
                         source=item["path"].split(PATH_SEP)[0], model=model, aspect=aspect))
    return out


async def load_chunks(lexicon: Lexicon) -> list[Chunk]:
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(KnowledgeChunk.section_path, KnowledgeChunk.questions, KnowledgeChunk.answer)
            .where(KnowledgeChunk.vectorize_status == "done",
                   KnowledgeChunk.content_type.in_(DOC_CONTENT_TYPES))
            .order_by(KnowledgeChunk.id)
        )).all()
    return build_chunks([tuple(r) for r in rows], lexicon)
```

- [ ] **Step 4: 实现 `seeds.py`**

```python
# evals/hard_set/seeds.py
"""确定性生成 4 个桶的种子池。只看知识库结构，不看检索结果。"""

from collections import defaultdict
from dataclasses import dataclass
import random

from app.config import GENERAL_CATEGORY
from evals.hard_set.corpus import DEFAULT_MODELS, Chunk

SEED = 20261010
BUCKETS = ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")
# 简介段和整张品类表不出题。
EXCLUDED_KEYS = frozenset({"商品手册", "售后手册", "退货政策", "退货政策 > 品类退换货规则"})
H3_SOURCES = ("退货政策", "售后手册", "常见问答", "商品FAQ")
WARRANTY_DISTRACTORS = ("售后手册 > 维修 > 保修期",)
RETURN_DISTRACTORS = ("退货政策 > 退货条件 > 七天无理由退货", "退货政策 > 退货条件 > 质量问题退货",
                      "退货政策 > 品类退换货规则")
POLICY_ASPECTS = ("保修", "退换规则")


@dataclass(frozen=True)
class Seed:
    bucket: str
    target_key: str
    slot: int
    distractor_keys: tuple[str, ...]


def _h4_distractors(target: Chunk, chunks: list[Chunk]) -> tuple[str, ...]:
    keys = {c.key for c in chunks}
    if target.aspect == "保修":
        extra = [c.key for c in chunks if c.key.startswith("常见问答 > 售后维修 > ")]
        return tuple(k for k in WARRANTY_DISTRACTORS if k in keys) + tuple(extra)
    if target.aspect == "退换规则":
        extra = [c.key for c in chunks if c.key.startswith("常见问答 > 退换货 > 退货政策")]
        return tuple(k for k in RETURN_DISTRACTORS if k in keys) + tuple(extra)
    return tuple(c.key for c in chunks if c.source == "商品FAQ" and c.category == target.category)


def _targets(bucket: str, chunks: list[Chunk]) -> list[tuple[Chunk, tuple[str, ...]]]:
    default_categories = {c.category for c in chunks if c.model in DEFAULT_MODELS}
    out = []
    for c in chunks:
        if c.key in EXCLUDED_KEYS:
            continue
        if bucket == "H1_sibling" and c.model:
            siblings = tuple(o.key for o in chunks if o.model and o.model != c.model
                             and o.category == c.category and o.aspect == c.aspect)
            if siblings:
                out.append((c, siblings))
        elif bucket == "H2_colloquial" and (
                (c.model and c.aspect == "常见故障") or c.source == "商品FAQ"
                or (c.category == GENERAL_CATEGORY and c.source != "商品手册")):
            out.append((c, ()))
        elif bucket == "H3_buried" and c.source in H3_SOURCES:
            out.append((c, ()))
        elif bucket == "H4_specific" and c.model and (
                c.aspect in POLICY_ASPECTS
                or (c.model not in DEFAULT_MODELS and c.category in default_categories
                    and c.aspect != "常见故障")):
            distractors = _h4_distractors(c, chunks)
            if distractors:
                out.append((c, distractors))
    return out


def _round_robin(items: list[tuple[Chunk, tuple[str, ...]]], rng_key: str) -> list[tuple[Chunk, tuple[str, ...]]]:
    groups = defaultdict(list)
    for item in items:
        groups[item[0].category].append(item)
    names = sorted(groups)
    for name in names:
        random.Random(f"{rng_key}:{name}").shuffle(groups[name])
    out = []
    while any(groups[n] for n in names):
        for name in names:
            if groups[name]:
                out.append(groups[name].pop(0))
    return out


def build_pools(chunks: list[Chunk], *, seed: int = SEED) -> dict[str, list[Seed]]:
    pools = {}
    for bucket in BUCKETS:
        ordered = _round_robin(_targets(bucket, chunks), f"{seed}:{bucket}")
        pools[bucket] = [Seed(bucket, c.key, slot, d) for slot in (1, 2) for c, d in ordered]
    return pools
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/test_hard_set_seeds.py -q`
Expected: PASS（6 passed）

- [ ] **Step 6: 提交**

```bash
git add evals/hard_set/__init__.py evals/hard_set/corpus.py evals/hard_set/seeds.py tests/test_hard_set_seeds.py
git commit -m "feat(ch11): hard set corpus view and deterministic seed pools"
```

---

### Task 2: 机械过滤（`filters.py`）

**Files:**
- Create: `evals/hard_set/filters.py`
- Test: `tests/test_hard_set_filters.py`

**Interfaces:**
- Consumes: `corpus.Chunk`、`app.knowledge.query.Lexicon`
- Produces:
  - `filters.models_in(text: str, lexicon: Lexicon) -> list[str]`（规范型号，按出现顺序去重）
  - `filters.bigram_overlap(query: str, text: str, lexicon: Lexicon) -> float`
  - `filters.check_query(bucket: str, query: str, target: Chunk, *, term: str | None, chunks: list[Chunk], lexicon: Lexicon, existing: set[str]) -> str | None`（返回淘汰原因；通过返回 None）
  - 常量 `MAX_QUERY_CHARS = 200`、`H3_MIN_CHARS = 60`、`H3_MAX_CHARS = 150`、`H2_BODY_MAX_OVERLAP = 0.15`、`TERM_MIN_CHARS = 2`、`TERM_MAX_CHARS = 8`、`TERM_MAX_KEYS = 3`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hard_set_filters.py
import pytest

from app.knowledge.query import load_lexicon
from evals.hard_set import filters as f
from evals.hard_set.corpus import Chunk

LEX = load_lexicon()


def chunk(key="商品手册 > 蓝牙耳机 > X3 Pro 续航", title="X3 Pro 续航", text="X3 Pro 关闭降噪单次续航约 8 小时。",
          category="蓝牙耳机", source="商品手册", model="X3 Pro", aspect="续航"):
    return Chunk(key, key, title, text, category, source, model, aspect)


def check(bucket, query, target=None, term=None, chunks=None, existing=()):
    target = target or chunk()
    return f.check_query(bucket, query, target, term=term, chunks=chunks or [target],
                         lexicon=LEX, existing=set(existing))


@pytest.mark.parametrize("text, expected", [
    ("x3pro 开降噪能用多久", ["X3 Pro"]),
    ("X3-Pro 和 X3 哪个好", ["X3 Pro", "X3"]),
    ("X3pro 续航", ["X3 Pro"]),
    ("耳机就左边响", []),
])
def test_models_in_uses_lexicon_aliases(text, expected):
    assert f.models_in(text, LEX) == expected


def test_bigram_overlap_strips_models_and_punctuation():
    assert f.bigram_overlap("X3 Pro 续航？", "X3 Pro 续航", LEX) == 1.0
    assert f.bigram_overlap("X3 Pro 能撑多久", "X3 Pro 续航", LEX) == 0.0
    assert f.bigram_overlap("", "续航", LEX) == 0.0


def test_common_rules():
    assert check("H1_sibling", " ") == "问题为空"
    assert check("H1_sibling", "X3 Pro" + "长" * 200) == "问题超过 200 字"
    assert check("H1_sibling", "X3 Pro 开降噪能撑多久", existing={"X3 Pro 开降噪能撑多久"}) == "问题重复"


@pytest.mark.parametrize("bucket", ["H1_sibling", "H4_specific"])
def test_model_rules(bucket):
    assert check(bucket, "x3pro 开降噪能撑多久") is None
    assert check(bucket, "这副耳机开降噪能撑多久") == "缺少目标型号"
    assert check(bucket, "X3 Pro 比 X5 能撑多久") == "含其他型号"
    assert check(bucket, "X3 开降噪能撑多久") == "缺少目标型号"


def test_h2_overlap_rules():
    fault = chunk(key="商品手册 > 蓝牙耳机 > X3 常见故障", title="X3 常见故障",
                  text="X3 只有一边有声音时，把两只耳机放回充电盒，盖上盖子等 10 秒。", model="X3", aspect="常见故障")
    assert check("H2_colloquial", "X3 戴着左边响右边哑了咋整", target=fault) is None
    assert check("H2_colloquial", "X3 常见的毛病有哪些", target=fault) == "与标题字面重合"
    assert check("H2_colloquial", "只有一边有声音时把两只耳机放回充电盒行不行", target=fault) == "与正文字面重合过高"


def test_h3_rules():
    target = chunk(key="退货政策 > 特殊情况 > 价格保护", title="价格保护",
                   text="签收后 7 天内降价的，可以申请价格保护。", category="通用", source="退货政策",
                   model=None, aspect=None)
    filler = "我上周在你们这买了好几样东西，有给家里老人的保温杯，也有给孩子挑的运动鞋，快递第二天就到了，包装也挺严实，"
    ok = filler + "就是想问下价格保护怎么弄。"
    assert 60 <= len(ok) <= 150
    assert check("H3_buried", ok, target=target, term="价格保护") is None
    assert check("H3_buried", "价格保护怎么弄", target=target, term="价格保护") == "长度不在 60–150 字"
    assert check("H3_buried", ok, target=target, term=None) == "术语长度无效"
    assert check("H3_buried", ok, target=target, term="很久以前") == "术语不在目标段落中"
    twice = filler + "价格保护怎么弄，价格保护要多久？"
    assert check("H3_buried", twice, target=target, term="价格保护") == "术语在问题中出现次数不是 1"
    others = [target] + [chunk(key=f"k{i}", text="价格保护说明", model=None) for i in range(3)]
    assert check("H3_buried", ok, target=target, term="价格保护", chunks=others) == "术语不稀有"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_hard_set_filters.py -q`
Expected: FAIL，`ImportError: cannot import name 'filters'`

- [ ] **Step 3: 实现 `filters.py`**

```python
# evals/hard_set/filters.py
"""困难评估集的机械过滤。纯函数，不访问外部服务，不看检索结果。"""

import re

from app.knowledge.query import Lexicon, normalize_models
from evals.hard_set.corpus import Chunk

MAX_QUERY_CHARS = 200
H3_MIN_CHARS = 60
H3_MAX_CHARS = 150
H2_BODY_MAX_OVERLAP = 0.15
TERM_MIN_CHARS = 2
TERM_MAX_CHARS = 8
TERM_MAX_KEYS = 3


def models_in(text: str, lexicon: Lexicon) -> list[str]:
    normalized = normalize_models(text, lexicon)
    canonical = lexicon.canonical
    found = []
    for m in lexicon._model_re.finditer(normalized):
        model = canonical[re.sub(r"[\s\-]", "", m.group(0)).lower()]
        if model not in found:
            found.append(model)
    return found


def _bigrams(text: str, lexicon: Lexicon) -> set[str]:
    stripped = lexicon._model_re.sub("", normalize_models(text, lexicon))
    chars = re.sub(r"[\W_]", "", stripped)
    return {chars[i:i + 2] for i in range(len(chars) - 1)}


def bigram_overlap(query: str, text: str, lexicon: Lexicon) -> float:
    q = _bigrams(query, lexicon)
    if not q:
        return 0.0
    return len(q & _bigrams(text, lexicon)) / len(q)


def check_query(bucket: str, query: str, target: Chunk, *, term: str | None,
                chunks: list[Chunk], lexicon: Lexicon, existing: set[str]) -> str | None:
    q = query.strip()
    if not q:
        return "问题为空"
    if len(q) > MAX_QUERY_CHARS:
        return "问题超过 200 字"
    if q in existing:
        return "问题重复"
    if bucket in ("H1_sibling", "H4_specific"):
        models = models_in(q, lexicon)
        if target.model not in models:
            return "缺少目标型号"
        if len(models) > 1:
            return "含其他型号"
    if bucket == "H2_colloquial":
        if bigram_overlap(q, target.title, lexicon) > 0:
            return "与标题字面重合"
        if bigram_overlap(q, target.text, lexicon) > H2_BODY_MAX_OVERLAP:
            return "与正文字面重合过高"
    if bucket == "H3_buried":
        if not H3_MIN_CHARS <= len(q) <= H3_MAX_CHARS:
            return "长度不在 60–150 字"
        if not term or not TERM_MIN_CHARS <= len(term) <= TERM_MAX_CHARS:
            return "术语长度无效"
        if term not in target.text and term not in target.title:
            return "术语不在目标段落中"
        if q.count(term) != 1:
            return "术语在问题中出现次数不是 1"
        if sum(term in c.text or term in c.title for c in chunks) > TERM_MAX_KEYS:
            return "术语不稀有"
    return None
```

注：`test_model_rules` 中 "X3 开降噪能撑多久" 返回 "缺少目标型号"，因为目标型号是 `X3 Pro`；规则先查缺失，再查多余。

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/test_hard_set_filters.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add evals/hard_set/filters.py tests/test_hard_set_filters.py
git commit -m "feat(ch11): mechanical filters for hard set queries"
```

---

### Task 3: Prompt 与标注补全（`prompts.py`、`labels.py`）

**Files:**
- Create: `evals/hard_set/prompts.py`
- Create: `evals/hard_set/labels.py`
- Test: `tests/test_hard_set_labels.py`

**Interfaces:**
- Consumes: `corpus.Chunk`、`seeds.Seed`、`app.knowledge.query.{Lexicon, normalize_models, model_category}`
- Produces:
  - `prompts.GeneratedQuery(BaseModel)`：`query: str`、`term: str | None = None`
  - `prompts.LabelAnswer(BaseModel)`：`answers: list[int]`
  - `prompts.GEN_PROMPTS: dict[str, ChatPromptTemplate]`（键为 4 个桶名，变量 `target_title`、`target_text`、`distractors`、`avoid`）
  - `prompts.LABEL_PROMPT: ChatPromptTemplate`（变量 `candidates`、`query`）
  - `prompts.prompt_sha256() -> dict[str, str]`（4 个桶 + `label`）
  - `labels.MAX_GROUP_KEYS = 3`
  - `labels.candidates_for(query: str, chunks: list[Chunk], lexicon: Lexicon) -> list[Chunk]`
  - `labels.render_candidates(cands: list[Chunk]) -> str`
  - `labels.answer_keys(numbers: list[int], cands: list[Chunk]) -> list[str] | None`（越界或为空返回 None）
  - `labels.check_labels(bucket: str, target: Chunk, keys: list[str], by_key: dict[str, Chunk]) -> str | None`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hard_set_labels.py
from langchain_core.prompts import ChatPromptTemplate

from app.knowledge.query import load_lexicon
from evals.hard_set import labels, prompts
from evals.hard_set.corpus import Chunk
from evals.hard_set.seeds import BUCKETS

LEX = load_lexicon()


def c(key, category, model=None, source="商品手册"):
    return Chunk(key, key, key.split(" > ")[-1], f"{key} 正文", category, source, model, None)


CHUNKS = [
    c("商品手册 > 蓝牙耳机 > X3 续航", "蓝牙耳机", "X3"),
    c("商品手册 > 蓝牙耳机 > X3 Pro 续航", "蓝牙耳机", "X3 Pro"),
    c("商品手册 > 台灯 > L1 参数", "台灯", "L1"),
    c("商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "蓝牙耳机", None, "商品FAQ"),
    c("售后手册 > 维修 > 保修期", "通用", None, "售后手册"),
]
BY_KEY = {x.key: x for x in CHUNKS}


def test_candidates_follow_production_filter():
    with_model = labels.candidates_for("x3pro 能用多久", CHUNKS, LEX)
    assert [x.key for x in with_model] == [
        "商品手册 > 蓝牙耳机 > X3 续航", "商品手册 > 蓝牙耳机 > X3 Pro 续航",
        "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "售后手册 > 维修 > 保修期"]
    assert labels.candidates_for("耳机就左边响", CHUNKS, LEX) == CHUNKS


def test_render_and_answer_keys():
    text = labels.render_candidates(CHUNKS[:2])
    assert text.startswith("[1] 商品手册 > 蓝牙耳机 > X3 续航\n") and "[2] 商品手册 > 蓝牙耳机 > X3 Pro 续航" in text
    assert labels.answer_keys([2, 1, 2], CHUNKS[:2]) == [
        "商品手册 > 蓝牙耳机 > X3 Pro 续航", "商品手册 > 蓝牙耳机 > X3 续航"]
    assert labels.answer_keys([3], CHUNKS[:2]) is None
    assert labels.answer_keys([0], CHUNKS[:2]) is None
    assert labels.answer_keys([], CHUNKS[:2]) is None


def test_check_labels_rules():
    pro, x3 = BY_KEY["商品手册 > 蓝牙耳机 > X3 Pro 续航"], BY_KEY["商品手册 > 蓝牙耳机 > X3 续航"]
    faq, general = "商品FAQ > 蓝牙耳机 > 耳机充满电能用多久？", "售后手册 > 维修 > 保修期"
    assert labels.check_labels("H1_sibling", pro, [x3.key], BY_KEY) == "目标段落不在答案中"
    assert labels.check_labels("H1_sibling", pro, [pro.key, x3.key], BY_KEY) == "同系列其他型号也能回答"
    assert labels.check_labels("H1_sibling", x3, [x3.key, faq], BY_KEY) is None
    assert labels.check_labels("H4_specific", pro, [pro.key, faq], BY_KEY) == "通用段落也能回答"
    assert labels.check_labels("H4_specific", pro, [pro.key], BY_KEY) is None
    assert labels.check_labels("H2_colloquial", pro, [pro.key, faq, general, x3.key], BY_KEY) == "来源键超过 3 个"
    assert labels.check_labels("H2_colloquial", pro, [pro.key, faq, general], BY_KEY) is None


def test_prompts_render_with_all_variables():
    assert set(prompts.GEN_PROMPTS) == set(BUCKETS)
    for bucket, prompt in prompts.GEN_PROMPTS.items():
        assert isinstance(prompt, ChatPromptTemplate)
        messages = prompt.format_messages(target_title="T", target_text="正文", distractors="（无）", avoid="（无）")
        joined = "\n".join(m.content for m in messages)
        assert "JSON" in joined and "正文" in joined, bucket
    label = prompts.LABEL_PROMPT.format_messages(candidates="[1] a\nb", query="问")
    assert "JSON" in label[0].content and "[1] a" in label[0].content and label[-1].content.endswith("问")
    hashes = prompts.prompt_sha256()
    assert set(hashes) == set(BUCKETS) | {"label"} and all(len(v) == 64 for v in hashes.values())
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_hard_set_labels.py -q`
Expected: FAIL，`ImportError`

- [ ] **Step 3: 实现 `prompts.py`**

注意：`ChatPromptTemplate` 中字面的 `{`、`}` 必须写成 `{{`、`}}`。

```python
# evals/hard_set/prompts.py
"""困难评估集的出题与标注 Prompt。"""

import hashlib

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


class GeneratedQuery(BaseModel):
    query: str = Field(description="顾客问题")
    term: str | None = Field(default=None, description="H3 用：问题中决定答案的术语")


class LabelAnswer(BaseModel):
    answers: list[int] = Field(description="能完整回答问题的候选编号")


_COMMON = """你在为电商售后客服的检索系统编写测试问题。只输出 JSON 对象，不输出其他文字。
目标段落：
标题：{target_title}
正文：{target_text}

不要和下列已有问题问同一件事：
{avoid}"""

_H1 = _COMMON + """

同系列其他型号的同名段落：
{distractors}

要求：
1. 问题必须写出目标段落的型号，不写其他型号。
2. 问的事实要在目标段落中有明确答案，并且在其他型号的段落中数值或说法不同。
3. 用顾客口吻，一句话，不超过 40 字。

示例 1：目标「X3 Pro 续航」→ {{"query": "X3 Pro 开着降噪大概能听几个小时？"}}
示例 2：目标「S10 Max 保修」→ {{"query": "S10 Max 的主机保几年？"}}

输出格式：{{"query": "..."}}"""

_H2 = _COMMON + """

要求：
1. 用顾客口吻描述遇到的现象或场景，让人读完就知道要找这个段落。
2. 不用标题中的词，尽量不用正文中的词，换成日常说法。
3. 段落属于某个型号时，可以写出型号。
4. 一到两句，不超过 60 字。

示例 1：目标「X3 常见故障」（只有一边有声音）→ {{"query": "X3 戴着只有左耳出声，右边一点动静没有，咋回事"}}
示例 2：目标「订单取消」→ {{"query": "刚付完款就后悔了，东西还没寄出来，这单还能撤回吗"}}

输出格式：{{"query": "..."}}"""

_H3 = _COMMON + """

要求：
1. 从目标段落中选 1 个能决定答案的术语（2–8 字，原文照抄，例如“价格保护”“上门取件”）。
2. 写一段 60–150 字的顾客留言：前面是与问题无关的闲聊（购物经历、家人、物流等），术语只在最后提问时出现 1 次。
3. 闲聊不涉及目标段落中的事实，不出现其他售后术语。

示例 1：{{"term": "价格保护", "query": "我上周给家里老人买了个保温杯，还给孩子挑了双运动鞋，快递第二天就到了，包装也挺严实，老人用着说挺好。今天刷到同款便宜了二十多块，想问问价格保护怎么申请？"}}
示例 2：{{"term": "上门取件", "query": "前阵子搬家，东西乱七八糟堆了一屋，最近才收拾完，发现扫地机有点毛病已经申请了维修。我腿脚不太方便，不想自己跑快递点，你们能上门取件吗？"}}

输出格式：{{"term": "...", "query": "..."}}"""

_H4 = _COMMON + """

看起来相关、但不能给出正确答案的通用段落：
{distractors}

要求：
1. 问题必须写出目标段落的型号，不写其他型号。
2. 用通用说法提问（例如“保修多久”“能退吗”“能用多久”），不写段落标题或章节名。
3. 答案只能从目标段落得到；上面的通用段落给不出这个答案，或者给出的答案不对。
4. 用顾客口吻，一句话，不超过 40 字。

示例 1：目标「X3 Pro 续航」，通用段落「耳机充满电能用多久？」（写的是 X3 的数值）→ {{"query": "X3 Pro 充满一次能用多久？"}}
示例 2：目标「X3 保修」，通用段落「保修期」（只说以详情页为准）→ {{"query": "X3 耳机进水了还能免费修吗？"}}

输出格式：{{"query": "..."}}"""

GEN_PROMPTS = {
    bucket: ChatPromptTemplate.from_messages([("system", text), ("human", "请输出 JSON。")])
    for bucket, text in (("H1_sibling", _H1), ("H2_colloquial", _H2), ("H3_buried", _H3), ("H4_specific", _H4))
}

_LABEL = """你在为检索测试集做标注。下面是知识库候选段落，每段以 [编号] 开头。
读完顾客问题后，找出所有能完整回答这个问题的段落。只回答了一部分、讲的是其他型号、或只说"以详情页为准"的段落不算。
只输出 JSON 对象：{{"answers": [编号, ...]}}；没有能回答的段落时输出 {{"answers": []}}。

候选段落：
{candidates}"""

LABEL_PROMPT = ChatPromptTemplate.from_messages([("system", _LABEL), ("human", "顾客问题：{query}")])


def prompt_sha256() -> dict[str, str]:
    texts = {"H1_sibling": _H1, "H2_colloquial": _H2, "H3_buried": _H3, "H4_specific": _H4, "label": _LABEL}
    return {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in texts.items()}
```

- [ ] **Step 4: 实现 `labels.py`**

```python
# evals/hard_set/labels.py
"""标注补全：候选范围等于生产检索的过滤范围，只依赖知识库结构。"""

from app.config import GENERAL_CATEGORY
from app.knowledge.query import Lexicon, model_category, normalize_models
from evals.hard_set.corpus import Chunk

MAX_GROUP_KEYS = 3


def candidates_for(query: str, chunks: list[Chunk], lexicon: Lexicon) -> list[Chunk]:
    # 与生产检索一致：问题中出现型号时，品类由第一个型号决定。
    category = model_category(normalize_models(query, lexicon), lexicon)
    if category is None:
        return list(chunks)
    return [c for c in chunks if c.category in (category, GENERAL_CATEGORY)]


def render_candidates(cands: list[Chunk]) -> str:
    return "\n\n".join(f"[{i}] {c.key}\n{c.text}" for i, c in enumerate(cands, 1))


def answer_keys(numbers: list[int], cands: list[Chunk]) -> list[str] | None:
    if not numbers or any(not 1 <= n <= len(cands) for n in numbers):
        return None
    return list(dict.fromkeys(cands[n - 1].key for n in numbers))


def check_labels(bucket: str, target: Chunk, keys: list[str], by_key: dict[str, Chunk]) -> str | None:
    if target.key not in keys:
        return "目标段落不在答案中"
    if len(keys) > MAX_GROUP_KEYS:
        return "来源键超过 3 个"
    if bucket == "H1_sibling" and any(by_key[k].model not in (None, target.model) for k in keys):
        return "同系列其他型号也能回答"
    if bucket == "H4_specific" and any(by_key[k].model is None for k in keys):
        return "通用段落也能回答"
    return None
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `uv run pytest tests/test_hard_set_labels.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add evals/hard_set/prompts.py evals/hard_set/labels.py tests/test_hard_set_labels.py
git commit -m "feat(ch11): generation and labeling prompts, label completion checks"
```

---

### Task 4: 合成编排与命令行（`pipeline.py`、`scripts/synth_hard_set.py`）

**Files:**
- Create: `evals/hard_set/pipeline.py`
- Create: `scripts/synth_hard_set.py`
- Modify: `evals/rag_eval_set.py`：第 13 行 `from app.knowledge.retrieval import source_key` 移进 `known_source_keys()` 函数体（避免合成脚本传递导入检索模块）；在 `SAMPLES_PATH` 下一行新增 `HARD_SAMPLES_PATH = SAMPLES_PATH.parent / "rag_eval_hard.jsonl"`
- Test: `tests/test_hard_set_pipeline.py`

**Interfaces:**
- Consumes: Task 1–3 的全部接口；`evals.rag_eval_set.{load_samples, validate}`。本任务在 `evals/rag_eval_set.py` 中新增 `HARD_SAMPLES_PATH`。`validate(..., eval_set="hard")` 由 Task 5 实现，本任务不测 `--check` 的校验部分。
- Produces:
  - `pipeline.HardSample`（frozen dataclass）：`id: str`、`bucket: str`、`query: str`、`keys: tuple[str, ...]`、`target_key: str`、`term: str | None`
  - `pipeline.BucketStats`（dataclass）：`seeds_tried: int`、`attempts: int`、`rejected: dict[str, int]`、`errors: int`、`replaced: list[str]`
  - `async pipeline.synthesize(pools, chunks, lexicon, *, generators: dict[str, Runnable], labeler: Runnable, per_bucket: int = 60, max_attempts: int = 3, concurrency: int = 4) -> tuple[list[HardSample], dict[str, BucketStats]]`
  - `pipeline.spotcheck_ids(samples: list[HardSample], seed: int) -> list[str]`
  - `scripts/synth_hard_set.py`：`build_parser()`、`async run(args) -> int`、`main() -> int`；输出 `evals/rag_eval_hard.jsonl`、`evals/rag_eval_hard.manifest.json`、`evals/rag_eval_hard.spotcheck.md`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hard_set_pipeline.py
import json
import subprocess
import sys

from langchain_core.runnables import RunnableLambda

from app.knowledge.query import load_lexicon
from evals.hard_set import pipeline
from evals.hard_set.corpus import Chunk
from evals.hard_set.filters import models_in
from evals.hard_set.prompts import GeneratedQuery, LabelAnswer
from evals.hard_set.seeds import Seed

LEX = load_lexicon()
FORBIDDEN = ("app.knowledge.retrieval", "app.knowledge.milvus", "app.knowledge.rerank", "app.knowledge.embeddings")


def mk(key, model):
    return Chunk(key, key, key.split(" > ")[-1], f"{model} 正文 {key}", "蓝牙耳机", "商品手册", model, "续航")


CHUNKS = [mk("商品手册 > 蓝牙耳机 > X3 续航", "X3"), mk("商品手册 > 蓝牙耳机 > X3 Pro 续航", "X3 Pro"),
          mk("商品手册 > 蓝牙耳机 > X5 续航", "X5")]


def pools(n_targets=3):
    seeds = [Seed("H1_sibling", c.key, 1, ()) for c in CHUNKS[:n_targets]]
    return {"H1_sibling": seeds, "H2_colloquial": [], "H3_buried": [], "H4_specific": []}


def gen_for(script):
    """script: target_title -> 依次返回的问题列表；值为 Exception 时抛出，None 时 parsed 为 None。"""
    calls = {}

    def run(inputs):
        title = inputs["target_title"]
        i = calls.get(title, 0)
        calls[title] = i + 1
        item = script[title][min(i, len(script[title]) - 1)]
        if isinstance(item, Exception):
            raise item
        return {"parsed": None if item is None else GeneratedQuery(query=item), "raw": "r"}
    return RunnableLambda(run), calls


def labeler_target_only():
    def run(inputs):
        lines = [l for l in inputs["candidates"].splitlines() if l.startswith("[")]
        model = models_in(inputs["query"], LEX)[0]
        for line in lines:
            if line.endswith(f"> {model} 续航"):
                return {"parsed": LabelAnswer(answers=[int(line[1:line.index("]")])]), "raw": "r"}
        return {"parsed": LabelAnswer(answers=[99]), "raw": "r"}
    return RunnableLambda(run)


async def synth(script, per_bucket, n_targets=3, labeler=None):
    gen, calls = gen_for(script)
    gens = {b: gen for b in ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")}
    samples, stats = await pipeline.synthesize(
        pools(n_targets), CHUNKS, LEX, generators=gens, labeler=labeler or labeler_target_only(),
        per_bucket={"H1_sibling": per_bucket, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0},
        concurrency=2)
    return samples, stats, calls


async def test_retries_then_accepts_and_assigns_ids():
    script = {"X3 续航": ["X3 续航能用多久"], "X3 Pro 续航": ["X5 和 X3 Pro 比", RuntimeError("boom"), "X3 Pro 能用多久"],
              "X5 续航": ["X5 能用多久"]}
    samples, stats, calls = await synth(script, 2)
    assert [(s.id, s.query) for s in samples] == [("H1_01", "X3 续航能用多久"), ("H1_02", "X3 Pro 能用多久")]
    assert samples[1].keys == ("商品手册 > 蓝牙耳机 > X3 Pro 续航",)
    assert calls["X3 Pro 续航"] == 3 and "X5 续航" not in calls
    st = stats["H1_sibling"]
    assert st.rejected == {"含其他型号": 1} and st.errors == 1 and st.attempts == 4


async def test_replaces_failed_seed_in_order():
    script = {"X3 续航": [None], "X3 Pro 续航": ["X3 Pro 能用多久"], "X5 续航": ["X5 能用多久"]}
    samples, stats, calls = await synth(script, 2)
    assert [s.target_key.split(" > ")[-1] for s in samples] == ["X3 Pro 续航", "X5 续航"]
    assert calls["X3 续航"] == 3 and stats["H1_sibling"].replaced == ["商品手册 > 蓝牙耳机 > X3 续航"]
    assert stats["H1_sibling"].rejected == {"解析失败": 3}


async def test_label_out_of_range_is_rejection():
    bad = RunnableLambda(lambda _: {"parsed": LabelAnswer(answers=[0]), "raw": "r"})
    script = {"X3 续航": ["X3 能用多久"], "X3 Pro 续航": ["X3 Pro 能用多久"], "X5 续航": ["X5 能用多久"]}
    samples, stats, _ = await synth(script, 1, labeler=bad)
    assert samples == [] and stats["H1_sibling"].rejected == {"标注编号无效": 9}


async def test_duplicate_query_in_same_batch_is_dropped():
    script = {"X3 续航": ["X3 能用多久"], "X3 Pro 续航": ["X3 Pro 能用多久"], "X5 续航": ["X5 能用多久"]}
    gen, _ = gen_for(script)
    seeds = [Seed("H1_sibling", CHUNKS[0].key, 1, ()), Seed("H1_sibling", CHUNKS[0].key, 2, ()),
             Seed("H1_sibling", CHUNKS[1].key, 1, ())]
    samples, stats = await pipeline.synthesize(
        {"H1_sibling": seeds, "H2_colloquial": [], "H3_buried": [], "H4_specific": []}, CHUNKS, LEX,
        generators={"H1_sibling": gen}, labeler=labeler_target_only(),
        per_bucket={"H1_sibling": 2, "H2_colloquial": 0, "H3_buried": 0, "H4_specific": 0})
    assert [s.query for s in samples] == ["X3 能用多久", "X3 Pro 能用多久"]
    assert stats["H1_sibling"].rejected.get("问题重复", 0) >= 1


def test_spotcheck_ids_deterministic():
    samples = [pipeline.HardSample(f"H{b}_{i:02d}", "x", "q", ("k",), "k", None)
               for b in range(1, 5) for i in range(1, 61)]
    ids = pipeline.spotcheck_ids(samples, 20261010)
    assert ids[:4] == ["H1_05", "H1_20", "H1_35", "H1_50"] and len(ids) == 20 and len(set(ids)) == 20
    assert ids == pipeline.spotcheck_ids(samples, 20261010)


def test_synthesis_code_does_not_import_retrieval_modules():
    code = ("import sys, importlib; importlib.import_module('scripts.synth_hard_set');"
            f"bad=[m for m in {FORBIDDEN!r} if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_cli_refuses_to_overwrite_frozen_set(tmp_path, monkeypatch, capsys):
    from scripts import synth_hard_set as cli
    frozen = tmp_path / "rag_eval_hard.jsonl"
    frozen.write_text("frozen\n", encoding="utf-8")
    monkeypatch.setattr(cli, "HARD_PATH", frozen)
    assert await cli.run(cli.build_parser().parse_args([])) == 1
    assert frozen.read_text(encoding="utf-8") == "frozen\n"
    assert "--force" in capsys.readouterr().out


async def test_cli_writes_nothing_when_bucket_short(tmp_path, monkeypatch):
    from scripts import synth_hard_set as cli
    monkeypatch.setattr(cli, "HARD_PATH", tmp_path / "rag_eval_hard.jsonl")
    monkeypatch.setattr(cli, "MANIFEST_PATH", tmp_path / "m.json")
    monkeypatch.setattr(cli, "SPOTCHECK_PATH", tmp_path / "s.md")

    async def fake_load(lexicon):
        return CHUNKS

    async def fake_synth(*a, **k):
        return [], {b: pipeline.BucketStats() for b in ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")}

    async def noop():
        return None

    monkeypatch.setattr(cli, "load_chunks", fake_load)
    monkeypatch.setattr(cli, "synthesize", fake_synth)
    monkeypatch.setattr(cli, "build_runnables", lambda: ({}, None))
    monkeypatch.setattr(cli, "dispose_engine", noop)
    assert await cli.run(cli.build_parser().parse_args([])) == 1
    assert list(tmp_path.iterdir()) == []


async def test_cli_check_detects_hash_mismatch(tmp_path, monkeypatch, capsys):
    from scripts import synth_hard_set as cli
    path, manifest = tmp_path / "rag_eval_hard.jsonl", tmp_path / "m.json"
    path.write_text(json.dumps({"id": "H1_01"}) + "\n", encoding="utf-8")
    manifest.write_text(json.dumps({"jsonl_sha256": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(cli, "HARD_PATH", path)
    monkeypatch.setattr(cli, "MANIFEST_PATH", manifest)
    assert cli.check_hash() == "SHA-256 与 manifest 不一致"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_hard_set_pipeline.py -q`
Expected: FAIL，`ImportError: cannot import name 'pipeline'`

- [ ] **Step 3: 实现 `pipeline.py`**

```python
# evals/hard_set/pipeline.py
"""编排：出题 → 机械过滤 → 标注补全；淘汰后同一种子重试，再失败按顺序换下一个种子。"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import logging
import random

from langchain_core.runnables import Runnable

from app.knowledge.query import Lexicon
from evals.hard_set.corpus import Chunk
from evals.hard_set.filters import check_query
from evals.hard_set.labels import answer_keys, candidates_for, check_labels, render_candidates
from evals.hard_set.seeds import BUCKETS, Seed

logger = logging.getLogger(__name__)
ID_PREFIX = {"H1_sibling": "H1", "H2_colloquial": "H2", "H3_buried": "H3", "H4_specific": "H4"}
SPOTCHECK_POSITIONS = (5, 20, 35, 50)
SPOTCHECK_RANDOM = 4


@dataclass(frozen=True)
class HardSample:
    id: str
    bucket: str
    query: str
    keys: tuple[str, ...]
    target_key: str
    term: str | None


@dataclass
class BucketStats:
    seeds_tried: int = 0
    attempts: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    replaced: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Accepted:
    query: str
    keys: tuple[str, ...]
    term: str | None


def _format_keys(keys, by_key) -> str:
    return "\n\n".join(f"{by_key[k].key}\n{by_key[k].text}" for k in keys if k in by_key) or "（无）"


async def _run_seed(seed: Seed, *, by_key, chunks, lexicon, generator, labeler, existing: set[str],
                    avoid: list[str], max_attempts: int, stats: BucketStats, rejected: Counter,
                    semaphore: asyncio.Semaphore) -> _Accepted | None:
    target = by_key[seed.target_key]
    inputs = {"target_title": target.title, "target_text": target.text,
              "distractors": _format_keys(seed.distractor_keys, by_key),
              "avoid": "\n".join(avoid) or "（无）"}
    for _ in range(max_attempts):
        stats.attempts += 1
        try:
            async with semaphore:
                got = await generator.ainvoke(inputs)
            parsed = got["parsed"]
            if parsed is None:
                rejected["解析失败"] += 1
                continue
            query = parsed.query.strip()
            reason = check_query(seed.bucket, query, target, term=parsed.term, chunks=chunks,
                                 lexicon=lexicon, existing=existing)
            if reason:
                rejected[reason] += 1
                continue
            cands = candidates_for(query, chunks, lexicon)
            async with semaphore:
                label = await labeler.ainvoke({"candidates": render_candidates(cands), "query": query})
            if label["parsed"] is None:
                rejected["标注解析失败"] += 1
                continue
            keys = answer_keys(label["parsed"].answers, cands)
            if keys is None:
                rejected["标注编号无效"] += 1
                continue
            reason = check_labels(seed.bucket, target, keys, by_key)
            if reason:
                rejected[reason] += 1
                continue
            return _Accepted(query, tuple(keys), parsed.term)
        except Exception:
            logger.exception("合成失败：%s %s", seed.bucket, seed.target_key)
            stats.errors += 1
    return None


async def _bucket(bucket: str, pool: list[Seed], need: int, *, by_key, chunks, lexicon, generator,
                  labeler, max_attempts, semaphore) -> tuple[list[tuple[int, Seed, _Accepted]], BucketStats]:
    stats, rejected = BucketStats(), Counter()
    accepted: list[tuple[int, Seed, _Accepted]] = []
    existing: set[str] = set()
    next_i = 0
    while len(accepted) < need and next_i < len(pool):
        batch = list(enumerate(pool[next_i:next_i + need - len(accepted)], start=next_i))
        next_i += len(batch)
        stats.seeds_tried += len(batch)
        snapshot = set(existing)
        results = await asyncio.gather(*(
            _run_seed(seed, by_key=by_key, chunks=chunks, lexicon=lexicon, generator=generator,
                      labeler=labeler, existing=snapshot,
                      avoid=[a.query for _, s, a in accepted if s.target_key == seed.target_key],
                      max_attempts=max_attempts, stats=stats, rejected=rejected, semaphore=semaphore)
            for _, seed in batch))
        for (index, seed), result in zip(batch, results):
            if result is None:
                stats.replaced.append(seed.target_key)
            elif result.query in existing:
                rejected["问题重复"] += 1
                stats.replaced.append(seed.target_key)
            else:
                existing.add(result.query)
                accepted.append((index, seed, result))
    stats.rejected = dict(rejected)
    accepted.sort(key=lambda item: item[0])
    return accepted, stats


async def synthesize(pools: dict[str, list[Seed]], chunks: list[Chunk], lexicon: Lexicon, *,
                     generators: dict[str, Runnable], labeler: Runnable,
                     per_bucket: int | dict[str, int] = 60, max_attempts: int = 3,
                     concurrency: int = 4) -> tuple[list[HardSample], dict[str, BucketStats]]:
    by_key = {c.key: c for c in chunks}
    semaphore = asyncio.Semaphore(concurrency)
    samples, all_stats = [], {}
    for bucket in BUCKETS:
        need = per_bucket[bucket] if isinstance(per_bucket, dict) else per_bucket
        if need == 0:
            all_stats[bucket] = BucketStats()
            continue
        accepted, stats = await _bucket(bucket, pools[bucket], need, by_key=by_key, chunks=chunks,
                                        lexicon=lexicon, generator=generators[bucket], labeler=labeler,
                                        max_attempts=max_attempts, semaphore=semaphore)
        all_stats[bucket] = stats
        samples.extend(HardSample(f"{ID_PREFIX[bucket]}_{n:02d}", bucket, a.query, a.keys, seed.target_key, a.term)
                       for n, (_, seed, a) in enumerate(accepted, 1))
    return samples, all_stats


def spotcheck_ids(samples: list[HardSample], seed: int) -> list[str]:
    ids = sorted(s.id for s in samples)
    fixed = [f"{ID_PREFIX[b]}_{p:02d}" for b in BUCKETS for p in SPOTCHECK_POSITIONS]
    fixed = [i for i in fixed if i in ids]
    rest = [i for i in ids if i not in fixed]
    return fixed + random.Random(seed).sample(rest, min(SPOTCHECK_RANDOM, len(rest)))
```

- [ ] **Step 4: 修改 `evals/rag_eval_set.py`，延迟导入 `source_key`**

删除第 13 行 `from app.knowledge.retrieval import source_key`，改为在 `known_source_keys()` 函数体第一行导入：

```python
async def known_source_keys() -> dict[str, str]:
    # 延迟导入：合成脚本导入本模块时不能传递导入检索模块。
    from app.knowledge.retrieval import source_key
    async with get_sessionmaker()() as s:
        ...  # 其余不变
```

- [ ] **Step 5: 实现 `scripts/synth_hard_set.py`**

```python
# scripts/synth_hard_set.py
"""合成困难评估集（真实上游 + MySQL）。

uv run python scripts/synth_hard_set.py            # 合成；题集已存在时需要 --force
uv run python scripts/synth_hard_set.py --check    # 只校验冻结文件：格式、题数、来源键、SHA-256
"""

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.engine import dispose_engine
from app.knowledge.query import load_lexicon
from app.llm import build_extract_model
from evals.hard_set.corpus import load_chunks
from evals.hard_set.labels import MAX_GROUP_KEYS
from evals.hard_set.pipeline import spotcheck_ids, synthesize
from evals.hard_set.prompts import GEN_PROMPTS, LABEL_PROMPT, GeneratedQuery, LabelAnswer, prompt_sha256
from evals.hard_set.seeds import BUCKETS, SEED, build_pools
from evals.rag_eval_set import HARD_SAMPLES_PATH, load_samples, validate

logger = logging.getLogger(__name__)
HARD_PATH = HARD_SAMPLES_PATH
MANIFEST_PATH = HARD_PATH.with_suffix(".manifest.json")
SPOTCHECK_PATH = HARD_PATH.with_suffix(".spotcheck.md")
PER_BUCKET = 60


def build_runnables():
    model = build_extract_model(get_settings())
    gens = {b: p | model.with_structured_output(GeneratedQuery, method="json_mode", include_raw=True)
            for b, p in GEN_PROMPTS.items()}
    labeler = LABEL_PROMPT | model.with_structured_output(LabelAnswer, method="json_mode", include_raw=True)
    return gens, labeler


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_hash() -> str | None:
    if not MANIFEST_PATH.exists():
        return "缺少 manifest"
    expected = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("jsonl_sha256")
    return None if expected == sha256_file(HARD_PATH) else "SHA-256 与 manifest 不一致"


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def _spotcheck_md(samples, by_key) -> str:
    wanted = spotcheck_ids(samples, SEED)
    by_id = {s.id: s for s in samples}
    lines = ["# 困难评估集人工抽查", "", "检查三点：像真实顾客的话；标注正确；属于本桶设计。", ""]
    for sid in wanted:
        s = by_id[sid]
        lines += [f"## {s.id}（{s.bucket}）", "", f"问题：{s.query}", ""]
        if s.term:
            lines += [f"术语：{s.term}", ""]
        lines += [f"- `{k}`：{by_key[k].text[:120]}" for k in s.keys] + [""]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    if args.check:
        return await _check()
    if HARD_PATH.exists() and not args.force:
        print(f"{HARD_PATH.name} 已存在（冻结题集）。确认要重新合成时加 --force。")
        return 1
    try:
        lexicon = load_lexicon()
        chunks = await load_chunks(lexicon)
        gens, labeler = build_runnables()
        samples, stats = await synthesize(build_pools(chunks), chunks, lexicon, generators=gens,
                                          labeler=labeler, per_bucket=PER_BUCKET,
                                          concurrency=args.concurrency)
    finally:
        await dispose_engine()
    counts = {b: sum(s.bucket == b for s in samples) for b in BUCKETS}
    for bucket in BUCKETS:
        st = stats[bucket]
        print(f"{bucket}：{counts[bucket]}/{PER_BUCKET}，种子 {st.seeds_tried}，尝试 {st.attempts}，"
              f"故障 {st.errors}，淘汰 {st.rejected}")
    if any(counts[b] < PER_BUCKET for b in BUCKETS):
        print("有桶不足 60 题，未写入文件。")
        return 1
    rows = [{"id": s.id, "bucket": s.bucket, "difficulty": "hard", "query": s.query,
             "relevant": [list(s.keys)], "target": s.target_key, "term": s.term} for s in samples]
    HARD_PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": get_settings().chat_model, "git_commit": _git_commit(), "seed": SEED,
        "max_group_keys": MAX_GROUP_KEYS, "prompt_sha256": prompt_sha256(),
        "stats": {b: {"accepted": counts[b], "seeds_tried": stats[b].seeds_tried,
                      "attempts": stats[b].attempts, "errors": stats[b].errors,
                      "rejected": stats[b].rejected, "replaced": stats[b].replaced} for b in BUCKETS},
        "jsonl_sha256": sha256_file(HARD_PATH),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SPOTCHECK_PATH.write_text(_spotcheck_md(samples, {c.key: c for c in chunks}), encoding="utf-8")
    print(f"写入 {HARD_PATH.name}、{MANIFEST_PATH.name}、{SPOTCHECK_PATH.name}")
    return 0


async def _check() -> int:
    try:
        keys = {c.key for c in await load_chunks(load_lexicon())}
    finally:
        await dispose_engine()
    errors = validate(load_samples(HARD_PATH), keys, full=True, eval_set="hard")
    hash_error = check_hash()
    if hash_error:
        errors.append(hash_error)
    for error in errors:
        print(error)
    if not errors:
        print("困难题集检查通过")
    return 1 if errors else 0


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("参数必须为正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合成困难评估集，会调用上游模型。")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的题集")
    parser.add_argument("--concurrency", type=_positive, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.ERROR)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
```

注：`_check()` 调用 `validate(..., eval_set="hard")`，该参数由 Task 5 加入。Task 4 合入后、Task 5 合入前，`--check` 会报 `TypeError`，这是预期的；Task 4 的测试不覆盖 `_check()`。

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/test_hard_set_pipeline.py tests/test_rag_eval_set.py tests/test_run_rag_eval.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add evals/hard_set/pipeline.py scripts/synth_hard_set.py evals/rag_eval_set.py tests/test_hard_set_pipeline.py
git commit -m "feat(ch11): hard set synthesis pipeline and CLI"
```

---

### Task 5: 评估脚本支持困难题集（`--set hard`、`--gen-repeat`、预期对照）

**Files:**
- Modify: `evals/rag_eval_set.py`（`HARD_BUCKET_SIZES`、`validate(..., eval_set=)`）
- Modify: `evals/rag_metrics.py`（桶常量、`GenResult.run`、`generation_summary` 按次平均、报告列出各次值）
- Create: `evals/hard_set/expectations.py`
- Modify: `evals/run_rag_eval.py`（`--set`、`--gen-repeat`、困难题集报告）
- Test: `tests/test_rag_eval_set.py`、`tests/test_rag_metrics.py`、`tests/test_run_rag_eval.py`、`tests/test_hard_set_expectations.py`

**Interfaces:**
- Consumes: `evals.rag_eval_set.HARD_SAMPLES_PATH`（Task 4 已加）
- Produces:
  - `rag_eval_set.HARD_BUCKET_SIZES = {"H1_sibling": 60, "H2_colloquial": 60, "H3_buried": 60, "H4_specific": 60}`
  - `rag_eval_set.validate(samples, known_keys, *, full=True, eval_set="base")`
  - `rag_metrics.HARD_BUCKETS`（4 个桶名元组），`ANSWERABLE` 增加这 4 个桶；`BUCKETS` 增加这 4 个桶
  - `rag_metrics.GenResult.run: int = 1`（新字段放在最后，带默认值，现有位置参数构造不变）
  - `expectations.check_expectations(by_bucket: dict[tuple[str, str], dict[str, float]]) -> list[tuple[str, bool, str]]`
  - `expectations.render_expectations(rows) -> str`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rag_eval_set.py 追加
def hard(id="H1_01", bucket="H1_sibling", relevant=(("k1",),), difficulty="hard"):
    return es.EvalSample(id, bucket, difficulty, "q", tuple(tuple(g) for g in relevant))


def test_validate_hard_set_rules():
    assert es.validate([hard()], {"k1"}, full=False, eval_set="hard") == []
    errors = es.validate([
        hard("H1_02", "H2_colloquial"),                     # 题号与桶不符
        hard("H3_01", "H3_buried", relevant=(("k1",), ("k2",))),  # 只能 1 组
        hard("H4_01", "H4_specific", relevant=(("a", "b", "c", "d"),)),  # 组内最多 3 个键
        hard("H2_01", "H2_colloquial", difficulty="easy"),  # 难度必须为 hard
        hard("A01", "A_policy"),                            # 不属于困难题集
    ], None, full=False, eval_set="hard")
    joined = "\n".join(errors)
    for word in ("H1_02", "H3_01", "H4_01", "H2_01", "A01"):
        assert word in joined
    full = es.validate([hard()], None, full=True, eval_set="hard")
    assert any("H2_colloquial" in e and "60" in e for e in full)


def test_validate_base_rejects_hard_buckets():
    assert es.validate([hard()], None, full=False) != []
```

```python
# tests/test_rag_metrics.py 追加
from evals import rag_metrics as rm


def gen(strategy, run, faithful, bucket="H1_sibling"):
    return rm.GenResult("H1_01", bucket, "hard", strategy, "q", True, False, "a", [], faithful, [], "r", run)


def test_hard_buckets_are_answerable():
    assert set(rm.HARD_BUCKETS) <= set(rm.ANSWERABLE) and set(rm.HARD_BUCKETS) <= set(rm.BUCKETS)


def test_generation_summary_averages_runs_and_report_lists_each_run():
    results = [gen("dense", 1, True), gen("dense", 1, True), gen("dense", 2, True), gen("dense", 2, False)]
    summary = rm.generation_summary(results)
    assert summary["dense"]["faithfulness"] == 0.75          # (1.0 + 0.5) / 2
    assert summary["dense"]["runs"] == [1.0, 0.5]
    md = rm.render_report(retrieval_by_bucket=None, retrieval_by_difficulty=None, post_threshold=None,
                          sweep=None, current_threshold=None, generation=results, failures=[])
    assert "| dense | 0.750 |" in md and "1.000 / 0.500" in md


def test_generation_summary_single_run_unchanged():
    summary = rm.generation_summary([gen("dense", 1, True), gen("dense", 1, False)])
    assert summary["dense"]["faithfulness"] == 0.5 and summary["dense"]["runs"] == [0.5]
```

```python
# tests/test_hard_set_expectations.py
from evals.hard_set import expectations as ex

S = ("dense", "bm25", "hybrid", "hybrid_rerank")


def table(values):
    """values: {(strategy, bucket): (R@1, R@5, MRR)}"""
    return {k: {"R@1": v[0], "R@3": v[0], "R@5": v[1], "R@10": v[1], "MRR": v[2], "n": 60}
            for k, v in values.items()}


def winning():
    v = {}
    for bucket in ("ALL", "H1_sibling", "H2_colloquial", "H3_buried", "H4_specific"):
        v[(("dense"), bucket)] = (0.6, 0.96, 0.7)
        v[("bm25", bucket)] = (0.5, 0.96, 0.6)
        v[("hybrid", bucket)] = (0.65, 0.97, 0.75)
        v[("hybrid_rerank", bucket)] = (0.8, 0.99, 0.88)
    v[("dense", "H3_buried")] = (0.4, 0.9, 0.5)
    v[("bm25", "H3_buried")] = (0.7, 0.95, 0.8)
    return v


def test_all_expectations_met():
    rows = ex.check_expectations(table(winning()))
    assert len(rows) == 6 and all(ok for _, ok, _ in rows)
    md = ex.render_expectations(rows)
    assert md.startswith("## 冻结前写定的预期") and md.count("符合") == 6


def test_expectation_failures_reported():
    v = winning()
    v[("hybrid", "ALL")] = (0.8, 0.99, 0.9)                 # 重排不再严格最高
    v[("bm25", "H2_colloquial")] = (0.9, 0.99, 0.95)       # BM25 不是最低
    for s in S:
        for b in ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific"):
            r1, _, mrr = v[(s, b)]
            v[(s, b)] = (r1, 0.9, mrr)                     # 没有桶四策略 R@5 都 ≥ 0.95
    rows = ex.check_expectations(table(v))
    failed = [i for i, (_, ok, _) in enumerate(rows, 1) if not ok]
    assert failed == [1, 3, 6]
    assert "不符合" in ex.render_expectations(rows)
```

```python
# tests/test_run_rag_eval.py 追加（复用文件中的 ITEM、PLAN、Retrieval、replace、rm、es、rre）
@pytest.fixture
def offline_hard(monkeypatch, tmp_path):
    samples = [EvalSample(f"{b[:2]}_{i:02d}", b, "hard", f"{b[:2]}_{i:02d} q", ((ITEM.section_path,),))
               for b in es.HARD_BUCKET_SIZES for i in range(1, 61)]
    loaded = []

    async def noop():
        return None

    async def keys():
        return {ITEM.section_path: "预览"}

    def load(path=None):
        loaded.append(path)
        return samples

    for name in ("ensure_collection", "close_milvus", "close_rerank", "dispose_engine"):
        monkeypatch.setattr(rre, name, noop)
    monkeypatch.setattr(rre, "known_source_keys", keys)
    monkeypatch.setattr(rre, "load_samples", load)
    monkeypatch.setattr(rre, "REPORTS_DIR", tmp_path)
    return samples, loaded, tmp_path


async def test_hard_set_retrieval_report(offline_hard, monkeypatch):
    _, loaded, report_dir = offline_hard

    async def understand(query):
        return PLAN

    async def retrieve(query, strategy, **kwargs):
        return Retrieval(PLAN, [ITEM], [])

    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "retrieve", retrieve)
    args = rre.build_parser().parse_args(["--set", "hard", "--stage", "retrieval", "--bucket", "H1_sibling",
                                         "--limit", "2"])
    assert await rre.run_eval(args) == 0
    assert loaded == [es.HARD_SAMPLES_PATH]
    md = next(report_dir.glob("*.md")).read_text()
    assert md.startswith("# RAG 评估报告（困难题集）")
    assert "| hybrid_rerank | H1_sibling | 1.000 |" in md
    assert "## 检索：策略 × 难度" not in md and "## 门槛扫描" not in md
    assert "## 冻结前写定的预期" in md


@pytest.mark.parametrize("options", [["--set", "hard", "--bucket", "A_policy"],
                                     ["--set", "base", "--bucket", "H1_sibling"]])
async def test_bucket_must_belong_to_set(offline_eval, options, capsys):
    assert await rre.run_eval(rre.build_parser().parse_args(options)) == 1
    assert "不属于题集" in capsys.readouterr().out


async def test_gen_repeat_runs_each_strategy_n_times_and_writes_first_run_only(offline_hard, monkeypatch):
    _, _, report_dir = offline_hard
    runs, written = [], []
    model, judge = object(), object()

    async def understand(query):
        return PLAN

    async def generate(sample, plan, strategy, **kwargs):
        runs.append(strategy)
        return rm.GenResult(sample.id, sample.bucket, sample.difficulty, strategy, sample.query,
                            True, False, "答", [], False, ["答"], "缺")

    async def write(results, judge_model):
        written.extend(results)
        return len(results)

    from types import SimpleNamespace
    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "get_chat_model", lambda: model)
    monkeypatch.setattr(rre, "get_faith_judge", lambda: judge)
    monkeypatch.setattr(rre, "get_settings", lambda: SimpleNamespace(chat_model="m1"))
    monkeypatch.setattr(rre, "generate_one", generate)
    monkeypatch.setattr(rre, "write_faith_cases", write)
    args = rre.build_parser().parse_args(["--set", "hard", "--stage", "generation", "--bucket", "H2_colloquial",
                                         "--limit", "1", "--gen-strategies", "all", "--gen-repeat", "2"])
    assert await rre.run_eval(args) == 0
    assert sorted(runs) == sorted(list(rre.STRATEGIES) * 2)
    assert len(written) == 4 and all(r.run == 1 for r in written)
    assert "0.000 / 0.000" in next(report_dir.glob("*.md")).read_text()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/test_rag_eval_set.py tests/test_rag_metrics.py tests/test_run_rag_eval.py tests/test_hard_set_expectations.py -q`
Expected: FAIL（`validate() got an unexpected keyword argument 'eval_set'`、`HARD_BUCKETS` 不存在等）

- [ ] **Step 3: 修改 `evals/rag_eval_set.py`**

```python
HARD_BUCKET_SIZES = {"H1_sibling": 60, "H2_colloquial": 60, "H3_buried": 60, "H4_specific": 60}
HARD_MAX_GROUP_KEYS = 3


def _validate_hard(samples, known_keys, full):
    errors, seen = [], set()
    for s in samples:
        if s.id in seen:
            errors.append(f"题号重复：{s.id}")
        seen.add(s.id)
        if s.bucket not in HARD_BUCKET_SIZES:
            errors.append(f"{s.id}：桶无效 {s.bucket}")
        elif not re.fullmatch(r"H[1-4]_\d{2}", s.id) or s.id[:2] != s.bucket[:2]:
            errors.append(f"题号与桶不符：{s.id}（{s.bucket}）")
        if s.difficulty != "hard":
            errors.append(f"{s.id}：难度必须为 hard")
        if not s.query.strip() or len(s.query) > 512:
            errors.append(f"{s.id}：问题为空或超过 512 字")
        if len(s.relevant) != 1:
            errors.append(f"{s.id}：必须恰好 1 组来源键")
        for group in s.relevant:
            if not group or len(set(group)) != len(group) or len(group) > HARD_MAX_GROUP_KEYS:
                errors.append(f"{s.id}：来源键组为空、重复或超过 {HARD_MAX_GROUP_KEYS} 个")
        if known_keys is not None:
            errors.extend(f"{s.id}：来源键不存在 {key}"
                          for key in dict.fromkeys(k for g in s.relevant for k in g) if key not in known_keys)
    if full:
        buckets = Counter(s.bucket for s in samples)
        for bucket, expected in HARD_BUCKET_SIZES.items():
            if buckets[bucket] != expected:
                errors.append(f"{bucket}：题数 {buckets[bucket]}，应为 {expected}")
    return errors
```

`validate` 签名改为 `validate(samples, known_keys, *, full=True, eval_set="base")`，函数体第一行：

```python
    if eval_set == "hard":
        return _validate_hard(samples, known_keys, full)
```

其余 base 逻辑不变。

- [ ] **Step 4: 修改 `evals/rag_metrics.py`**

```python
HARD_BUCKETS = ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")
BUCKETS = ("A_policy", "B_model", "C_colloquial", "D_unanswerable", "E_multi") + HARD_BUCKETS
ANSWERABLE = ("A_policy", "B_model", "C_colloquial", "E_multi") + HARD_BUCKETS
```

`GenResult` 末尾加字段 `run: int = 1`。

`generation_summary` 中 faithfulness 改为按次计算再平均：

```python
        per_run = []
        for run in sorted({r.run for r in items}):
            judged = [r for r in answerable if r.run == run and r.retrieved and not r.refused
                      and r.faithful is not None]
            per_run.append(sum(r.faithful is True for r in judged) / len(judged) if judged else 0.0)
        out[strategy] = {
            "faithfulness": mean(per_run) if per_run else 0.0,
            "runs": per_run,
            ...  # 其余字段不变
        }
```

`render_report` 生成表：当任一策略 `len(values["runs"]) > 1` 时，表头追加 `| 各次 Faithfulness |`，每行追加 `" / ".join(f"{x:.3f}" for x in values["runs"])`；只有 1 次时表格与现在完全一致。

- [ ] **Step 5: 实现 `evals/hard_set/expectations.py`**

```python
# evals/hard_set/expectations.py
"""spec 第 7 节：冻结前写定的预期，跑完后逐条对照。"""

STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
HARD = ("H1_sibling", "H2_colloquial", "H3_buried", "H4_specific")


def _v(t, s, b, m):
    return t[(s, b)][m]


def _fmt(t, b, metrics):
    return "；".join(f"{b} {m}：" + "、".join(f"{s} {_v(t, s, b, m):.3f}" for s in STRATEGIES) for m in metrics)


def _strict_max(t, b, m, s="hybrid_rerank"):
    return all(_v(t, s, b, m) > _v(t, o, b, m) for o in STRATEGIES if o != s)


def _strict_min(t, b, m, s):
    return all(_v(t, s, b, m) < _v(t, o, b, m) for o in STRATEGIES if o != s)


def check_expectations(t: dict) -> list[tuple[str, bool, str]]:
    r5_buckets = [b for b in HARD if all(_v(t, s, b, "R@5") >= 0.95 for s in STRATEGIES)]
    return [
        ("全部 240 题：hybrid_rerank 的 MRR 和 R@1 最高",
         _strict_max(t, "ALL", "MRR") and _strict_max(t, "ALL", "R@1"), _fmt(t, "ALL", ("R@1", "MRR"))),
        ("H1：hybrid_rerank 的 R@1 高于 dense 和 BM25",
         all(_v(t, "hybrid_rerank", "H1_sibling", "R@1") > _v(t, o, "H1_sibling", "R@1") for o in ("dense", "bm25")),
         _fmt(t, "H1_sibling", ("R@1",))),
        ("H2：BM25 的 R@1 和 MRR 最低",
         _strict_min(t, "H2_colloquial", "R@1", "bm25") and _strict_min(t, "H2_colloquial", "MRR", "bm25"),
         _fmt(t, "H2_colloquial", ("R@1", "MRR"))),
        ("H3：dense 的 R@1 和 MRR 低于 BM25",
         _v(t, "dense", "H3_buried", "R@1") < _v(t, "bm25", "H3_buried", "R@1")
         and _v(t, "dense", "H3_buried", "MRR") < _v(t, "bm25", "H3_buried", "MRR"),
         _fmt(t, "H3_buried", ("R@1", "MRR"))),
        ("H4：hybrid_rerank 的 R@1 最高", _strict_max(t, "H4_specific", "R@1"), _fmt(t, "H4_specific", ("R@1",))),
        ("至少 1 个桶中四种策略 R@5 都 ≥ 0.95", bool(r5_buckets), "、".join(r5_buckets) or "无"),
    ]


def render_expectations(rows) -> str:
    lines = ["## 冻结前写定的预期", "", "| # | 预期 | 结果 | 数值 |", "| --- | --- | --- | --- |"]
    lines += [f"| {i} | {text} | {'符合' if ok else '不符合'} | {detail} |"
              for i, (text, ok, detail) in enumerate(rows, 1)]
    return "\n".join(lines) + "\n"
```

注：表头列名是「结果」，不含「符合」二字，所以 `md.count("符合") == 6` 只统计 6 行结果单元格。

- [ ] **Step 6: 修改 `evals/run_rag_eval.py`**

1. 导入：`from evals.rag_eval_set import BUCKET_SIZES, HARD_BUCKET_SIZES, HARD_SAMPLES_PATH, EvalSample, known_source_keys, load_samples, validate`；`from evals.hard_set.expectations import check_expectations, render_expectations`。
2. `build_parser`：
   - `parser.add_argument("--set", dest="eval_set", choices=("base", "hard"), default="base")`
   - `parser.add_argument("--gen-repeat", type=_positive, default=1)`
   - `--bucket` 的 choices 改为 `tuple(BUCKET_SIZES) + tuple(HARD_BUCKET_SIZES)`。
3. `run_eval`：
   - `hard = args.eval_set == "hard"`；`sizes = HARD_BUCKET_SIZES if hard else BUCKET_SIZES`。
   - **在加载题集之前**检查桶：若 `args.bucket` 不为 None 且不在 `sizes`，打印 `f"桶 {args.bucket} 不属于题集 {args.eval_set}"` 并 `return 1`（仍走 finally 关闭资源）。原因：`test_bucket_must_belong_to_set` 用 base fixture，其 `load_samples` 不接受参数。
   - `all_samples = load_samples(HARD_SAMPLES_PATH) if hard else load_samples()`（base 仍无参调用，保持现有 fixture 可用）。
   - `validate(all_samples, set(keys), full=True, eval_set=args.eval_set)`。
   - `render_report(...)` 时：`retrieval_by_difficulty=None if hard else ...`，`sweep=None if hard else ...`。
   - 标题：`"# RAG 评估报告（困难题集）"` if hard else `"# RAG 评估报告"`；困难题集在「评估集题数」行后加一行 `题集文件：rag_eval_hard.jsonl，SHA-256 {hashlib.sha256(HARD_SAMPLES_PATH.read_bytes()).hexdigest()}`，文件不存在时（测试）写 `未找到`。
   - 困难题集且 `do_retrieval`：报告末尾追加 `render_expectations(check_expectations(summarize(scores, "bucket")))`。只跑部分桶时缺少的 `(strategy, bucket)` 键不能导致崩溃：`check_expectations` 调用包在 `try/except KeyError`，缺键时追加一行 `预期对照需要全部 4 个桶，本次跳过。`（测试 `--bucket H1_sibling --limit 2` 走这条路径，所以测试断言的是标题 `## 冻结前写定的预期` 存在——把跳过提示也放在该标题下）。
4. `collect` 增加参数 `gen_repeat: int = 1`：生成段对每个 `run in range(1, gen_repeat + 1)` 调 `generate_one`，结果用 `dataclasses.replace(result, run=run)`；失败信息带上 `run`（`f"{sample.id}/{strategy}#{run}：..."`，仅当 `gen_repeat > 1`，`gen_repeat == 1` 时保持原格式）。`write_faith_cases` 只传 `run == 1` 的结果。`run_eval` 调 `collect` 时传 `gen_repeat=args.gen_repeat`。

- [ ] **Step 7: 运行测试，确认通过**

Run: `uv run pytest tests/test_rag_eval_set.py tests/test_rag_metrics.py tests/test_run_rag_eval.py tests/test_hard_set_expectations.py tests/test_hard_set_pipeline.py -q`
Expected: PASS

- [ ] **Step 8: 全量测试**

Run: `uv run pytest -q`
Expected: 全部通过（基线 1,115 个 + 本章新增）

- [ ] **Step 9: 提交**

```bash
git add evals/rag_eval_set.py evals/rag_metrics.py evals/run_rag_eval.py evals/hard_set/expectations.py tests/
git commit -m "feat(ch11): --set hard, --gen-repeat and pre-registered expectations in RAG eval"
```

---

### Task 6: 真实合成与人工抽查（Claude 执行）

本任务不写代码，由 Claude 运行，用户审查。

- [ ] **Step 1: 确认环境**

Run: `docker compose ps` 和 `uv run python scripts/build_kb.py --check`
Expected: MySQL、Milvus healthy；`--check` 退出码 0。

- [ ] **Step 2: 合成**

Run: `uv run python scripts/synth_hard_set.py > /tmp/ch11-synth.log 2>&1; echo $?`
Expected: 退出码 0；日志中 4 个桶都是 `60/60`。如果有桶不足 60，停下来把各步淘汰数报告给用户，不放宽过滤规则。

- [ ] **Step 3: 校验**

Run: `uv run python scripts/synth_hard_set.py --check`
Expected: `困难题集检查通过`

- [ ] **Step 4: 人工抽查（用户）**

把 `evals/rag_eval_hard.spotcheck.md` 的 20 题发给用户审查。

- 如果用户认可：进入 Step 5。
- 如果用户指出问题：修改 Prompt 或过滤规则（交给 Codex，附带对应测试），用 `--force` 整批重新合成，再回到 Step 3。不手改单题。

- [ ] **Step 5: 冻结**

在跑任何检索之前提交：

```bash
git add evals/rag_eval_hard.jsonl evals/rag_eval_hard.manifest.json evals/rag_eval_hard.spotcheck.md
git commit -m "data(ch11): freeze hard evaluation set (240 questions)"
```

记录该 commit 号，写入 `dev-notes/ch11.md`。

---

### Task 7: 跑评估（Claude 执行）

- [ ] **Step 1: 确认冻结**

Run: `git log -1 --format=%H -- evals/rag_eval_hard.jsonl && uv run python scripts/synth_hard_set.py --check`
Expected: 输出 Task 6 Step 5 的 commit；检查通过。

- [ ] **Step 2: 全量评估**

注意：生成段 240 题 × 4 策略 × 2 次，每次含生成和裁判调用，预计 2–3 小时。并发不超过 3。

Run: `uv run python evals/run_rag_eval.py --set hard --stage all --gen-strategies all --gen-repeat 2 --concurrency 3 > /tmp/ch11-eval.log 2>&1; echo $?`
Expected: 退出码 0，`evals/reports/rag_eval_<时间>.md` 生成，标题为「RAG 评估报告（困难题集）」。如果退出码 1，查看报告的「执行失败」段；上游失败可以重跑整个命令，不能只重跑部分题后拼接。

- [ ] **Step 3: 对照预期**

读报告末尾「冻结前写定的预期」表，把 6 条结果原样写入 `dev-notes/ch11.md`。不符合的条目不改题、不重跑，只记录。

- [ ] **Step 4: 提交报告**

```bash
git add evals/reports/rag_eval_*.md
git commit -m "eval(ch11): hard set four-strategy report"
```

---

### Task 8: README、facts、CLAUDE.md 与收尾

**Files:**
- Modify: `README.md`、`README.zh-CN.md`（Claude 写）
- Modify: `web/src/welcome/facts.ts`（Codex 写；仅当欢迎页要显示困难题集数字时）
- Modify: `CLAUDE.md`（ch11 一行、新增命令）、`dev-notes/ch11.md`

- [ ] **Step 1: README 两份**

在「Measured, not claimed」节原表之后加：

1. 一张表：`Retrieval strategy (240 hard)` × `Recall@1 | Recall@5 | MRR | Faithfulness (mean of 2 runs)`，数字从 Task 7 报告逐个抄。
2. 一段说明（英文，中文版对应翻译）：4 类题各针对什么弱点；种子、过滤和标注不看检索结果；题集在跑检索前冻结（链接冻结 commit、manifest、spot-check）；预期在冻结前写定，报告中有逐条对照（写出符合几条）。
3. 决策记录 2 的 Evidence 补一句困难题集上的 R@1 和 MRR 对比。

- [ ] **Step 2: facts.ts（可选）**

如果用户要求欢迎页显示困难题集数字，交给 Codex：在 `FACTS` 中加字段并在欢迎页使用，跑 `npm --prefix web run typecheck && npm --prefix web run test`。用户没有要求时跳过，`recallAt1` 保持原题集的值。

- [ ] **Step 3: CLAUDE.md**

项目状态加 ch11 一行；常用命令加：

```bash
uv run python scripts/synth_hard_set.py              # 合成困难评估集（真实上游 + MySQL；题集已存在时需 --force；--check 只校验冻结文件和 SHA-256）
uv run python evals/run_rag_eval.py --set hard --stage all --gen-strategies all --gen-repeat 2 --concurrency 3   # 困难题集四策略评估
```

设计约束加一条：困难评估集冻结后不改题、不删题；合成代码不导入检索模块（`tests/test_hard_set_pipeline.py` 检查）。

- [ ] **Step 4: 全量验证**

Run: `uv run pytest -q && npm --prefix web run typecheck && npm --prefix web run test`
Expected: 全部通过。

- [ ] **Step 5: code review 与 finish**

按 superpowers:requesting-code-review 审查整个分支，处理结论后按 superpowers:finishing-a-development-branch 提 PR。dev-notes 每个阶段当场补记。
