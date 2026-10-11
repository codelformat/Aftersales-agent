# ch12 扩充知识库与 v2 评估集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩充知识库（16 个形近型号、2 个新政策、12 条 FAQ），编写并冻结 300 题 v2 评估集，在新旧知识库上跑 3 套题集的检索评估并出对比报告。

**Architecture:** 检索与指标代码不动。评估侧新增 `--set v2`、v2 校验、冻结清单校验和事先写定的预期；数据侧由 spec 附录写定全部事实，Codex 按附录和出题清单编写，机械脚本检查一致性和 D 桶无答案依据。所有评估只跑检索阶段。

**Tech Stack:** Python 3.12（uv）、pytest、MySQL + Milvus（Docker Compose）、DeepSeek（Query 改写）、硅基流动（嵌入、重排）。

**Spec:** `docs/superpowers/specs/2026-10-11-ch12-harder-eval-set-design.md`

## Global Constraints

- 不改 `app/` 下任何文件；不改 `evals/rag_metrics.py`。
- 不改 `evals/rag_eval.jsonl`；不改 ch11 分支的任何文件（worktree 中复制 `lexicon.json` 不提交）。
- `--set` 默认 `base`；不带 `--set` 时，现有命令的行为、输出和报告文件名不变。
- v2 题集冻结后不改题、不删题、不改标注。
- 编写 v2 题集的执行者（Codex）禁止运行 `run_rag_eval.py --stage retrieval|generation|all` 和任何 Milvus 检索。
- 只跑检索阶段；不评 Faithfulness；不写 `faith_cases`（检索阶段本来不写）。
- 随机种子 `20261011`。
- Codex 调用命令照 CLAUDE.md（`gpt-6.1-sol`、`high`、两个 `-c` 沙箱参数、`< /dev/null`）。每次结束后查日志中的 quota、usage limit、429；额度用完时告诉用户并改派 Sonnet 子代理。
- docker 和 `build_kb.py` 由 Claude 执行。
- 每个任务完成后，Claude 当场在 `dev-notes/ch12.md` 追加一段（用户原话、产出、纠偏、翻车）。

## Review Focus

1. `--set v2` 在 manifest 不存在时跑检索：应打印"v2 题集未冻结"并退出码 1，不调用模型；`--check` 不受影响。（Task 1 测试）
2. `check_d_evidence.py` 的关键词含大小写差异或正则特殊字符（如 `x5 lite`、`(`）：按字面、忽略大小写匹配。（Task 3 测试）
3. `new_chunk_audit.py` 遇到 D 题（`relevant` 为空）、检索失败题（rankings 中缺该题）和 Top-5 中重复的来源键：D 题和缺失题跳过，重复键先去重再定位次。（Task 3 测试）
4. `make_targets.py` 同一输入同一种子输出逐字相同；来源键清单中混入 `mined` 或飞轮来源的键时不被抽中。（Task 3 测试）
5. `check_kb_v2.py` 在 git 中找不到基线提交 `cd7a110` 时：打印明确错误并退出码 1，不当作通过。（Task 2 测试）

---

### Task 0：旧知识库基线（Claude 执行，不写代码）

**Files:**
- Create: `evals/reports/ch12/`（运行产物目录）

- [ ] **Step 1：启动服务并确认知识库与 `main` 一致**

```bash
docker compose up -d --wait
uv run python scripts/build_kb.py --check
```
Expected：退出码 0。不为 0 时停下，报告用户，不 rebuild（会改变基线）。

- [ ] **Step 2：保存旧来源键清单**

```bash
mkdir -p evals/reports/ch12
uv run python evals/run_rag_eval.py --list-keys > evals/reports/ch12/keys_old.tsv
```

- [ ] **Step 3：旧 300 题基线**

```bash
uv run python evals/run_rag_eval.py --stage retrieval --concurrency 3 \
  --save-rankings evals/reports/ch12/rankings_base_oldkb.json
```
把生成的 `evals/reports/rag_eval_<时间>.md` 改名为 `evals/reports/ch12/base_oldkb.md`。退出码 1 时查看"执行失败"段：改写失败少于 3 题照常保留并在报告中注明；更多时停下报告用户。

- [ ] **Step 4：ch11 困难题集基线**

```bash
WT=/Users/harry/Aftersales-agent-ch11
git worktree add $WT ch11
cp .env $WT/.env
(cd $WT && uv run python evals/run_rag_eval.py --set hard --stage retrieval --concurrency 3 \
  --save-rankings /Users/harry/Aftersales-agent/evals/reports/ch12/rankings_hard_oldkb.json)
```
把 worktree 中生成的报告复制为 `evals/reports/ch12/hard_oldkb.md`。

- [ ] **Step 5：提交并记 dev-notes**

```bash
git add evals/reports/ch12 dev-notes/ch12.md
git commit -m "eval(ch12): baselines on the old knowledge base"
```

---

### Task 1：v2 校验、`--set v2`、冻结清单校验、预期（Codex）

**Files:**
- Modify: `evals/rag_eval_set.py`
- Modify: `evals/run_rag_eval.py`
- Create: `evals/v2/__init__.py`、`evals/v2/expectations.py`
- Test: `tests/test_rag_eval_set.py`、`tests/test_run_rag_eval.py`、`tests/test_v2_expectations.py`

**Interfaces:**
- Produces（`evals/rag_eval_set.py`）：
  ```python
  V2_SAMPLES_PATH = SAMPLES_PATH.parent / "rag_eval_v2.jsonl"
  V2_MANIFEST_PATH = SAMPLES_PATH.parent / "v2" / "manifest.json"
  V2_BUCKET_SIZES = {"A_policy": 40, "B_model": 50, "C_colloquial": 50, "D_unanswerable": 60, "E_multi": 100}
  V2_DIFFICULTY_SIZES = {"A_policy": (0, 20, 20), "B_model": (10, 20, 20), "C_colloquial": (10, 20, 20),
                         "D_unanswerable": (15, 20, 25), "E_multi": (20, 40, 40)}
  V2_MIN_THREE_GROUP_E = 50
  def validate(samples, known_keys, *, full: bool = True, eval_set: str = "base") -> list[str]
  def sha256_file(path: Path) -> str
  def check_manifest(samples_path: Path = V2_SAMPLES_PATH, manifest_path: Path = V2_MANIFEST_PATH) -> str | None
      # 返回 None 表示一致；否则返回中文错误（"v2 题集未冻结：缺少 manifest" 或 "v2 题集 SHA-256 与 manifest 不一致"）
  ```
- Produces（`evals/v2/expectations.py`）：
  ```python
  STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
  ANSWERABLE_V2 = ("A_policy", "B_model", "C_colloquial", "E_multi")
  def check_expectations(t: dict[tuple[str, str], dict[str, float]]) -> list[tuple[int, str, bool, str]]
  def render_expectations(rows: list[tuple[int, str, bool, str]]) -> str
  ```
  `t` 为 `evals.rag_metrics.summarize(scores, "bucket")` 的返回值，键 `(策略, 桶或 "ALL")`，值含 `R@1`、`R@5`、`MRR`。
- manifest 格式：`{"samples_sha256": "<hex>", "targets_sha256": "<hex>", "frozen_at": "<ISO 时间>", "commit_note": "..."}`。

- [ ] **Step 1：写失败测试（`tests/test_rag_eval_set.py` 追加）**

```python
def v2_full_set():
    rows = []
    sizes = es.V2_BUCKET_SIZES
    for bucket, diffs in es.V2_DIFFICULTY_SIZES.items():
        n = 0
        for difficulty, count in zip(("easy", "medium", "hard"), diffs):
            for _ in range(count):
                n += 1
                if bucket == "D_unanswerable":
                    rel = ()
                elif bucket == "E_multi":
                    rel = (("k1",), ("k2",), ("k3",)) if n <= 50 else (("k1",), ("k2",))
                else:
                    rel = (("k1",),)
                rows.append(sample(f"{bucket[0]}{n:03d}", bucket, difficulty, relevant=rel))
    assert len(rows) == sum(sizes.values()) == 300
    return rows


def test_validate_v2_full_set_ok():
    assert es.validate(v2_full_set(), {"k1", "k2", "k3"}, eval_set="v2") == []


def test_validate_v2_errors():
    rows = v2_full_set()
    rows[0] = sample("A01", "A_policy", "medium")                     # 两位题号
    rows[1] = sample("A002", "A_policy", "medium", relevant=(("k1",), ("k2",)))  # A 桶多组
    e_index = next(i for i, r in enumerate(rows) if r.id == "E001")
    rows[e_index] = sample("E001", "E_multi", "easy", relevant=(("k1",), ("k2",), ("k3",), ("k1",)))  # 4 组
    errors = "\n".join(es.validate(rows, {"k1", "k2", "k3"}, eval_set="v2"))
    assert "A01" in errors and "A002" in errors and "E001" in errors
    assert "3 组的题" in errors                                            # 3 组题降到 49 道


def test_validate_v2_rejects_unknown_set():
    with pytest.raises(ValueError):
        es.validate([], set(), eval_set="v3")


def test_validate_base_unchanged_for_three_digit_ids():
    # base 规则仍接受 2–3 位题号，计数仍按 BUCKET_SIZES
    assert es.validate([sample("A001")], {"k1"}, full=False) == []


def test_check_manifest(tmp_path):
    s = tmp_path / "v2.jsonl"
    s.write_text("x\n", encoding="utf-8")
    m = tmp_path / "manifest.json"
    assert "未冻结" in es.check_manifest(s, m)
    m.write_text(json.dumps({"samples_sha256": es.sha256_file(s)}), encoding="utf-8")
    assert es.check_manifest(s, m) is None
    s.write_text("y\n", encoding="utf-8")
    assert "不一致" in es.check_manifest(s, m)
```

- [ ] **Step 2：写失败测试（`tests/test_v2_expectations.py`）**

```python
from evals.v2 import expectations as ex


def table(rerank_r5=0.90, others_r5=0.85, others_mrr=0.80):
    t = {}
    for s in ex.STRATEGIES:
        for b in ("ALL", *ex.ANSWERABLE_V2):
            r5 = rerank_r5 if s == "hybrid_rerank" else others_r5
            if b == "E_multi":
                r5 -= 0.10
            mrr = 0.95 if s == "hybrid_rerank" else others_mrr
            r1 = 0.90 if s == "hybrid_rerank" else 0.80
            if b == "C_colloquial" and s == "bm25":
                mrr = 0.50
            t[(s, b)] = {"R@1": r1, "R@5": r5, "MRR": mrr}
    return t


def test_all_expectations_met():
    rows = ex.check_expectations(table())
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert all(r[2] for r in rows)


def test_expectation_2_bounds_inclusive():
    assert ex.check_expectations(table(rerank_r5=0.92))[1][2] is True
    assert ex.check_expectations(table(rerank_r5=0.921))[1][2] is False


def test_expectation_3_fails_when_other_mrr_reaches_090():
    assert ex.check_expectations(table(others_mrr=0.90))[2][2] is False


def test_render_marks_result():
    md = ex.render_expectations(ex.check_expectations(table(rerank_r5=0.95)))
    assert "## 冻结前写定的预期" in md and "不符合" in md and "符合" in md
```

- [ ] **Step 3：写失败测试（`tests/test_run_rag_eval.py` 追加，沿用 `offline_eval` fixture）**

```python
async def test_v2_retrieval_refuses_without_manifest(offline_eval, monkeypatch, tmp_path, capsys):
    samples, lifecycle, report_dir = offline_eval
    monkeypatch.setattr(rre, "load_samples", lambda path=None: samples)
    monkeypatch.setattr(rre, "validate", lambda *a, **k: [])
    monkeypatch.setattr(rre, "V2_MANIFEST_PATH", tmp_path / "missing.json")
    called = []
    monkeypatch.setattr(rre, "understand", lambda q: called.append(q))
    args = rre.build_parser().parse_args(["--set", "v2", "--stage", "retrieval"])
    assert await rre.run_eval(args) == 1
    assert "未冻结" in capsys.readouterr().out and not called
    assert not list(report_dir.glob("*.md"))


async def test_v2_check_works_without_manifest(offline_eval, monkeypatch, tmp_path, capsys):
    samples, _, _ = offline_eval
    seen = {}
    monkeypatch.setattr(rre, "load_samples", lambda path=None: seen.setdefault("path", path) and samples)
    monkeypatch.setattr(rre, "validate", lambda s, k, **kw: seen.setdefault("set", kw.get("eval_set")) and [])
    monkeypatch.setattr(rre, "V2_MANIFEST_PATH", tmp_path / "missing.json")
    assert await rre.run_eval(rre.build_parser().parse_args(["--set", "v2", "--check"])) == 0
    assert seen == {"path": rre.V2_SAMPLES_PATH, "set": "v2"}


async def test_v2_report_has_sha_and_expectations(offline_eval, monkeypatch, tmp_path):
    samples, _, report_dir = offline_eval
    monkeypatch.setattr(rre, "load_samples", lambda path=None: samples)
    monkeypatch.setattr(rre, "validate", lambda *a, **k: [])
    monkeypatch.setattr(rre, "check_manifest", lambda *a, **k: None)
    monkeypatch.setattr(rre, "sha256_file", lambda p: "abc123")

    async def understand(query):
        return PLAN

    async def retrieve(query, strategy, **kwargs):
        return Retrieval(PLAN, [ITEM], [])

    monkeypatch.setattr(rre, "understand", understand)
    monkeypatch.setattr(rre, "retrieve", retrieve)
    args = rre.build_parser().parse_args(["--set", "v2", "--stage", "retrieval", "--bucket", "B_model",
                                         "--limit", "1"])
    assert await rre.run_eval(args) == 0
    [report] = report_dir.glob("rag_eval_v2_*.md")
    md = report.read_text()
    assert md.startswith("# RAG 评估报告（v2 题集）") and "SHA-256 abc123" in md
    assert "## 冻结前写定的预期" in md
```

说明：`--bucket B_model --limit 1` 时只有 B 桶数据，`check_expectations` 读不到的键按"数据不足"处理：该条结果为不符合，数值列写"数据不足"，不抛异常。在 `tests/test_v2_expectations.py` 加一条：

```python
def test_missing_buckets_do_not_raise():
    rows = ex.check_expectations({("hybrid_rerank", "B_model"): {"R@1": 1.0, "R@5": 1.0, "MRR": 1.0}})
    assert len(rows) == 6 and not any(r[2] for r in rows) and "数据不足" in rows[0][3]
```

- [ ] **Step 4：运行，确认失败**

Run：`uv run pytest tests/test_rag_eval_set.py tests/test_v2_expectations.py tests/test_run_rag_eval.py -q`
Expected：新增用例 FAIL（缺少属性或参数），现有用例 PASS。

- [ ] **Step 5：实现**

- `rag_eval_set.validate`：`eval_set` 不是 `base`/`v2` 时 `raise ValueError`。v2 时：题号正则 `[A-E]\d{3}`；桶集合与计数用 `V2_*`；A、B、C 恰好 1 组（错误文案含题号）；E 为 2 或 3 组；`full=True` 时 E 桶 3 组题数 < `V2_MIN_THREE_GROUP_E` 报"E_multi：3 组的题 N 道，至少 50"。其余检查（重复题号、难度值、问题长度、D 为空、组内重复、来源键存在）与 base 共用一段代码。base 分支行为逐字不变。
- `sha256_file`：`hashlib.sha256(path.read_bytes()).hexdigest()`。
- `check_manifest`：manifest 不存在 → "v2 题集未冻结：缺少 manifest"；`samples_sha256` 不等 → "v2 题集 SHA-256 与 manifest 不一致"。
- `run_rag_eval.py`：
  - `build_parser` 增加 `parser.add_argument("--set", dest="eval_set", choices=("base", "v2"), default="base")`。
  - 从 `evals.rag_eval_set` 导入 `V2_SAMPLES_PATH`、`V2_MANIFEST_PATH`、`check_manifest`、`sha256_file`；从 `evals.v2.expectations` 导入两个函数。
  - `run_eval`：`v2 = args.eval_set == "v2"`；`all_samples = load_samples(V2_SAMPLES_PATH) if v2 else load_samples()`；`validate(..., full=True, eval_set=args.eval_set)`；`--check` 照旧返回；之后若 `v2` 且 `check_manifest()` 返回错误 → 打印并 `return 1`（在 `collect` 之前）。
  - v2 报告：标题 `# RAG 评估报告（v2 题集）`，数据行后加 `题集文件：rag_eval_v2.jsonl，SHA-256 {sha256_file(V2_SAMPLES_PATH)}`；跑检索时报告末尾追加 `render_expectations(check_expectations(summarize(scores, "bucket")))`；文件名 `rag_eval_v2_{时间}.md`。base 路径不变。
- `expectations.py`：6 条按 spec §2.7；比较用严格 `>`/`<`，第 2 条两端含；取值缺失时该条 `(编号, 说明, False, "数据不足")`。

- [ ] **Step 6：运行，确认通过**

Run：`uv run pytest tests/test_rag_eval_set.py tests/test_v2_expectations.py tests/test_run_rag_eval.py -q`
Expected：全部 PASS。

- [ ] **Step 7：Claude 审查 diff、跑全量 `uv run pytest -q`，提交**

```bash
git add evals/rag_eval_set.py evals/run_rag_eval.py evals/v2 tests/test_rag_eval_set.py tests/test_run_rag_eval.py tests/test_v2_expectations.py
git commit -m "feat(ch12): v2 eval-set validation, --set v2, manifest check, pre-registered expectations"
```

---

### Task 2：知识库检查脚本 `scripts/check_kb_v2.py`（Codex）

**Files:**
- Create: `scripts/check_kb_v2.py`
- Test: `tests/test_check_kb_v2.py`

**Interfaces:**
- Produces：
  ```python
  BASELINE_COMMIT = "cd7a110"
  NEW_MODELS: dict[str, str]          # 型号 → 品类，16 项，按 spec 1.1
  SECTION_NAMES: dict[str, tuple[str, ...]]   # 品类 → 5 个节名后缀
  FACTS: dict[tuple[str, str], tuple[str, ...]]  # (型号, 节名) → 必须出现的字面片段（数字加单位），按 spec 附录 A
  BANNED_WORDS: tuple[str, ...]       # spec 附录 D.2 + D.3
  INTRO_OLD = "本手册覆盖 8 个品类、24 个型号"; INTRO_NEW = "本手册覆盖 8 个品类、40 个型号"
  def parse_sections(markdown: str) -> dict[str, str]         # "### 标题" → 正文
  def check(docs: dict[str, str], baseline: dict[str, str], old_d_queries: list[str]) -> list[str]
      # docs/baseline：相对路径 → 文本；返回中文错误列表
  def load_baseline(paths: list[str]) -> dict[str, str]      # git show BASELINE_COMMIT:<path>；失败抛 RuntimeError
  def main() -> int
  ```
- `FACTS` 的字面片段示例（Codex 必须对 80 节逐节写全，片段取 spec 附录 A 的数字加单位）：
  ```python
  ("X3 Lite", "参数"): ("蓝牙 5.2", "3.8 克"),
  ("X3 Lite", "续航"): ("5 小时", "20 小时", "1.5 小时"),
  ("X3 Lite", "保修"): ("90 天",),
  ("S10 Pro", "容量"): ("350 毫升", "240 毫升"),
  ("L2 Pro", "材质"): ("48 厘米", "5V 3A", "600 流明"),
  ```

- [ ] **Step 1：写失败测试**

```python
import pytest

from scripts import check_kb_v2 as ck

BASE_MANUAL = "# 商品手册\n\n本手册覆盖 8 个品类、24 个型号的参数。\n\n## 蓝牙耳机\n\n### X3 参数\n\nX3 使用蓝牙 5.2。\n"


def manual_with(models_md: str, intro=ck.INTRO_NEW) -> str:
    return BASE_MANUAL.replace(ck.INTRO_OLD, intro) + models_md


def full_new_sections() -> str:
    parts = []
    for model, category in ck.NEW_MODELS.items():
        for name in ck.SECTION_NAMES[category]:
            facts = "，".join(ck.FACTS.get((model, name), ()))
            parts.append(f"### {model} {name}\n\n{model} {facts}。\n")
    return "\n".join(parts)


def run(docs_manual, old_d=("X7 的续航？",), extra=None):
    docs = {"knowledge/docs/manual/商品手册.md": docs_manual, **(extra or {})}
    baseline = {"knowledge/docs/manual/商品手册.md": BASE_MANUAL}
    return ck.check(docs, baseline, list(old_d))


def test_complete_kb_passes():
    assert run(manual_with(full_new_sections())) == []


def test_missing_section_and_wrong_value():
    md = full_new_sections().replace("### X3 Lite 保修", "### X3 Lite 保修期").replace("350 毫升", "300 毫升")
    errors = "\n".join(run(manual_with(md)))
    assert "X3 Lite 保修" in errors and "350 毫升" in errors


def test_new_model_name_in_old_d_query():
    errors = "\n".join(run(manual_with(full_new_sections()), old_d=("x3lite 防水吗",)))
    assert "X3 Lite" in errors


def test_banned_word_in_added_line():
    md = full_new_sections() + "\n### 补充\n\n支持分期付款。\n"
    assert any("分期" in e for e in run(manual_with(md)))


def test_modified_existing_line():
    md = manual_with(full_new_sections()).replace("X3 使用蓝牙 5.2。", "X3 使用蓝牙 5.3。")
    assert any("修改或删除" in e for e in run(md))


def test_intro_not_updated():
    assert any("40 个型号" in e for e in run(manual_with(full_new_sections(), intro=ck.INTRO_OLD)))


def test_missing_baseline_commit(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("bad object")
    monkeypatch.setattr(ck.subprocess, "run", fail)
    with pytest.raises(RuntimeError):
        ck.load_baseline(["knowledge/docs/manual/商品手册.md"])
```

- [ ] **Step 2：运行，确认失败**

Run：`uv run pytest tests/test_check_kb_v2.py -q` → FAIL（模块不存在）。

- [ ] **Step 3：实现**

- 节标题匹配：`### {型号} {节名}`，型号取 `NEW_MODELS` 的键（含空格）。每个新型号 5 节都必须存在；每节正文必须包含 `FACTS` 中该节的全部片段。
- 旧 D 题检查：对每个新型号 `m`，`re.sub(r"[\s\-]", "", m).lower()` 出现在 `re.sub(r"[\s\-]", "", q).lower()` 中即报错。
- 新增行：对每个文件，`difflib.ndiff(baseline.splitlines(), docs.splitlines())` 中以 `+ ` 开头的行；`- ` 开头的行除 `INTRO_OLD` 所在行外都报"修改或删除了已有行"。新增行包含 `BANNED_WORDS` 任一词即报错。`商品手册.md` 中必须包含 `INTRO_NEW`，否则报"开头应为 40 个型号"。
- `load_baseline`：`subprocess.run(["git", "show", f"{BASELINE_COMMIT}:{p}"], check=True, capture_output=True, text=True)`；基线中不存在的文件（新政策文档）视为空文本——用 `git cat-file -e` 先判断存在；git 本身失败（无此提交）时抛 `RuntimeError("找不到基线提交 cd7a110")`。
- `main`：docs 为 `knowledge/docs/**/*.md`；旧 D 题从 `evals/rag_eval.jsonl` 读 `bucket == "D_unanswerable"` 的 `query`；有错误逐行打印并返回 1，否则打印"知识库检查通过"返回 0。`RuntimeError` 打印后返回 1。

- [ ] **Step 4：运行，确认通过**

Run：`uv run pytest tests/test_check_kb_v2.py -q` → PASS。

- [ ] **Step 5：在未改动的知识库上运行，确认按预期报错**

Run：`uv run python scripts/check_kb_v2.py; echo $?`
Expected：退出码 1，报 16 个型号缺节和"开头应为 40 个型号"；没有"修改或删除"错误。

- [ ] **Step 6：Claude 逐节核对 `FACTS` 与 spec 附录 A，提交**

```bash
git add scripts/check_kb_v2.py tests/test_check_kb_v2.py
git commit -m "feat(ch12): knowledge base consistency check for the v2 expansion"
```

---

### Task 3：出题清单、D 桶依据检查、新增小节审计（Codex）

**Files:**
- Create: `evals/v2/make_targets.py`、`evals/v2/check_d_evidence.py`、`evals/v2/new_chunk_audit.py`
- Test: `tests/test_v2_tools.py`

**Interfaces:**
- Consumes：`evals.rag_eval_set.load_samples`、`SAMPLES_PATH`、`V2_SAMPLES_PATH`、`V2_BUCKET_SIZES`、`V2_DIFFICULTY_SIZES`；`evals.rag_metrics.dedupe`；`scripts.check_kb_v2.NEW_MODELS`；`app.knowledge.query.load_lexicon`（读型号 → 品类）。
- Produces：
  ```python
  # make_targets.py
  SEED = 20261011
  DOC_ROOTS = ("商品手册", "商品FAQ", "退货政策", "售后手册", "配送与签收政策", "预售与缺货政策", "常见问答")
  POLICY_ROOTS = ("退货政策", "售后手册", "配送与签收政策", "预售与缺货政策")
  def load_keys(path: Path) -> list[str]          # 每行 "键\t预览"，取键，去重保序
  def build_targets(keys: list[str], old_keys: list[str], model_categories: dict[str, str],
                    seed: int = SEED) -> list[dict]
  # 每行：{"id","bucket","difficulty","targets": [[key], ...],"distractor": str|None,"d_class": int|None}
  # CLI：--keys new.tsv --old-keys old.tsv --out evals/v2/targets.jsonl

  # check_d_evidence.py
  def count_hits(keyword: str, texts: list[str]) -> int   # 字面、忽略大小写
  def check(evidence: list[dict], d_ids: dict[str, set[str]], texts: list[str]) -> list[str]
  # CLI：默认读 evals/v2/d_evidence.jsonl、knowledge/docs/**/*.md、两套题集的 D 题号；打印每题关键词与命中数

  # new_chunk_audit.py
  def audit(rankings: dict, new_keys: set[str], strategies=None) -> list[dict]
  # 每行：{"strategy","id","bucket","query","new_key","new_rank","first_hit_rank": int|None}
  def render(rows: list[dict]) -> str              # Markdown 表
  # CLI：--rankings r.json --old-keys old.tsv --new-keys new.tsv [--out x.md]
  ```

`build_targets` 规则（spec §2.3）：
- 只使用首段在 `DOC_ROOTS` 中的键（首段 = `key.split(" > ")[0]`）。
- A（40）：池 = 首段在 `POLICY_ROOTS` 的键 + 以 `商品FAQ > 配送与签收 > ` 或 `商品FAQ > 预售与缺货 > ` 开头的键；`random.Random(seed)` 打乱后依次取，不够时重新打乱续取。
- B（50）：型号节键 = 首段 `商品手册` 且末段以某个型号加空格开头（按型号名长度降序匹配，避免 `X3` 误配 `X3 Lite`）。`model_categories` 中每个型号各 1 题（目标节在该型号的节中随机），其余题（真实数据为 10 题）从 `NEW_MODELS` 中随机选型号和节。干扰型号：目标是现有型号 → 同品类新增型号中随机 1 个；目标是新增型号 → 同品类其他 4 个中随机 1 个。
- C（50）：先放入全部新增 FAQ 键（`商品FAQ` 开头且不在 `old_keys` 中），其余从全部可用键中随机抽，不与本桶已选重复。
- E（100）：前 25 题为同品类 3 个型号的同一节（3 个型号中至少 1 个新增型号），25 题为 1 个型号节 + 2 个首段不同的 `POLICY_ROOTS` 键，50 题为首段不同的 2 个键。
- D（60）：`d_class` 1–4 各 15，`targets` 为空列表。
- 每桶的难度标签按 `V2_DIFFICULTY_SIZES` 生成列表后用同一 `Random` 打乱，依次赋给该桶各题。题号 `{桶首字母}{序号:03d}`。

`check` 规则：`d_ids` 为 `{"v2": {...}, "base": {...}}`；每个题号必须恰有 1 行依据；`keywords` 为 1–4 个非空字符串；任一关键词命中数 > 0 报错（含题号、关键词、命中数）；依据中出现不属于 D 题的题号也报错。

`audit` 规则：`rankings` 为 `--save-rankings` 的 JSON；跳过 `relevant` 为空的题和 `rankings[strategy]` 中缺失的题；Top-5 先 `dedupe`；`new_rank` 为第一个属于 `new_keys` 的键的位次（1 起）；`first_hit_rank` 为第一个命中任一组的位次，没有则 None；只输出 `new_rank` 存在且（`first_hit_rank` 为 None 或 `new_rank < first_hit_rank`）的题。

- [ ] **Step 1：写失败测试（`tests/test_v2_tools.py`）**

```python
import json
from collections import Counter

from evals.v2 import check_d_evidence as cd, make_targets as mt, new_chunk_audit as na

CATS = {"X3": "蓝牙耳机", "X3 Pro": "蓝牙耳机", "X5": "蓝牙耳机", "X3 Lite": "蓝牙耳机", "X5 Pro": "蓝牙耳机"}
SECTIONS = ("参数", "续航", "保修", "退换规则", "常见故障")


def fake_keys():
    keys = [f"商品手册 > 蓝牙耳机 > {m} {s}" for m in CATS for s in SECTIONS]
    keys += [f"退货政策 > 退货条件 > 条款{i}" for i in range(5)]
    keys += [f"配送与签收政策 > 发货 > 条款{i}" for i in range(3)]
    keys += [f"预售与缺货政策 > 预售 > 条款{i}" for i in range(3)]
    keys += ["商品FAQ > 蓝牙耳机 > 耳机怎么连手机？", "商品FAQ > 配送与签收 > 下单后多久能发货啊？",
             "飞轮 > 某问题", "mined > x"]
    return keys


def old_keys():
    return [k for k in fake_keys() if "X3 Lite" not in k and "X5 Pro" not in k and "配送" not in k
            and "预售" not in k]


def build(seed=mt.SEED):
    return mt.build_targets(fake_keys(), old_keys(), CATS, seed=seed)


def test_targets_counts_and_determinism():
    rows = build()
    assert rows == build()
    assert Counter(r["bucket"] for r in rows) == {"A_policy": 40, "B_model": 50, "C_colloquial": 50,
                                                  "E_multi": 100, "D_unanswerable": 60}
    diffs = Counter((r["bucket"], r["difficulty"]) for r in rows)
    assert diffs[("A_policy", "easy")] == 0 and diffs[("E_multi", "hard")] == 40
    assert sum(1 for r in rows if r["bucket"] == "E_multi" and len(r["targets"]) == 3) >= 50
    assert Counter(r["d_class"] for r in rows if r["bucket"] == "D_unanswerable") == {1: 15, 2: 15, 3: 15, 4: 15}


def test_targets_exclude_foreign_roots_and_cover_models():
    rows = build()
    used = {k for r in rows for g in r["targets"] for k in g}
    assert not any(k.startswith(("飞轮", "mined")) for k in used)
    def model_of(key):
        last = key.split(" > ")[-1]
        return next(m for m in sorted(CATS, key=len, reverse=True) if last.startswith(m + " "))

    b_rows = [r for r in rows if r["bucket"] == "B_model"]
    assert {model_of(r["targets"][0][0]) for r in b_rows} == set(CATS)
    for r in b_rows:
        assert r["distractor"] in CATS and r["distractor"] != model_of(r["targets"][0][0])


def test_c_bucket_includes_new_faq():
    rows = build()
    c_keys = {r["targets"][0][0] for r in rows if r["bucket"] == "C_colloquial"}
    assert "商品FAQ > 配送与签收 > 下单后多久能发货啊？" in c_keys


def test_count_hits_literal_case_insensitive():
    assert cd.count_hits("x5 lite", ["X5 Lite 的参数"]) == 1
    assert cd.count_hits("(", ["a(b"]) == 1 and cd.count_hits("毫安", ["容量很小"]) == 0


def test_d_evidence_check():
    texts = ["X3 使用蓝牙 5.2", "锂电池容量很小"]
    ev = [{"set": "v2", "id": "D001", "keywords": ["毫安"]},
          {"set": "base", "id": "D25", "keywords": ["电池容量"]}]
    errors = "\n".join(cd.check(ev, {"v2": {"D001", "D002"}, "base": {"D25"}}, texts))
    assert "D25" in errors and "电池容量" in errors and "D002" in errors and "D001：" not in errors


def test_audit_skips_d_and_missing_and_dedupes():
    rankings = {
        "rankings": {"hybrid_rerank": {"A01": ["new", "new", "old"], "A02": ["old", "new"], "D01": ["new"]}},
        "samples": {"A01": {"relevant": [["old"]], "bucket": "A_policy", "query": "q1"},
                    "A02": {"relevant": [["old"]], "bucket": "A_policy", "query": "q2"},
                    "A03": {"relevant": [["old"]], "bucket": "A_policy", "query": "q3"},
                    "D01": {"relevant": [], "bucket": "D_unanswerable", "query": "q4"}},
    }
    rows = na.audit(rankings, {"new"})
    assert [(r["id"], r["new_rank"], r["first_hit_rank"]) for r in rows] == [("A01", 1, 2)]
    assert "A01" in na.render(rows)
```

- [ ] **Step 2：运行，确认失败**

Run：`uv run pytest tests/test_v2_tools.py -q` → FAIL（模块不存在）。

- [ ] **Step 3：实现三个模块（规则见上）**

- [ ] **Step 4：运行，确认通过**

Run：`uv run pytest tests/test_v2_tools.py -q` → PASS。

- [ ] **Step 5：Claude 审查 diff，跑全量 `uv run pytest -q`，提交**

```bash
git add evals/v2 tests/test_v2_tools.py
git commit -m "feat(ch12): target list, D-bucket evidence check, new-chunk audit"
```

---

### Task 4：扩充知识库（Codex 写内容，Claude 建库）

**Files:**
- Modify: `knowledge/docs/manual/商品手册.md`、`knowledge/docs/faq/商品FAQ.md`、`knowledge/lexicon.json`
- Create: `knowledge/docs/policy/配送与签收政策.md`、`knowledge/docs/policy/预售与缺货政策.md`

- [ ] **Step 1：Codex 按 spec 编写**

任务描述要点（自包含）：
1. 新型号 80 节按 spec 附录 A 逐行写，句式照抄同品类"同 X"所指的现有小节，只替换型号名和列出的值；插入位置为各品类最后一个现有型号的 `常见故障` 节之后、下一个 `## ` 之前。
2. 两个政策文档按附录 B：一级标题为文件名（不含 `.md`），开头一段照附录，二级、三级标题照表。
3. FAQ 按附录 C：前 7 条插到对应品类 `## ` 段末尾；`## 配送与签收`、`## 预售与缺货` 放文件末尾。
4. `lexicon.json` 按 spec 1.4；修改开头一句按 spec 1.5。
5. 不改其他任何已有行。
6. 跑 `uv run python scripts/check_kb_v2.py`，必须退出码 0。
7. 不运行 `build_kb.py`、不运行任何检索评估。

- [ ] **Step 2：Claude 审查**

1. `uv run python scripts/check_kb_v2.py` → 退出码 0。
2. `git diff main -- knowledge/` 逐节对照附录 A、B、C；确认没有附录外的新事实（新句子中的每个数字都能在附录中找到）。
3. 抽读 5 个新型号与其"同 X"小节，确认句式一致。

- [ ] **Step 3：Claude 建库**

```bash
uv run python scripts/build_kb.py --rebuild
uv run python scripts/build_kb.py --check
uv run python evals/run_rag_eval.py --list-keys > evals/reports/ch12/keys_new.tsv
comm -13 <(cut -f1 evals/reports/ch12/keys_old.tsv | sort) <(cut -f1 evals/reports/ch12/keys_new.tsv | sort) | wc -l
uv run python evals/run_rag_eval.py --check
```
Expected：`--rebuild`、`--check` 退出码 0；新增来源键约 80 + 2 个政策文档的节数 + 12；旧 300 题 `--check` 仍通过（来源键只增不减）。

- [ ] **Step 4：跑全量测试，提交**

```bash
uv run pytest -q
git add knowledge evals/reports/ch12/keys_new.tsv
git commit -m "data(ch12): 16 look-alike models, delivery and presale policies, 12 FAQ entries"
```

---

### Task 5：编写 v2 题集与 D 桶依据（Codex 写，Claude 审）

**Files:**
- Create: `evals/v2/targets.jsonl`、`evals/rag_eval_v2.jsonl`、`evals/v2/d_evidence.jsonl`

- [ ] **Step 1：Claude 生成出题清单**

```bash
uv run python evals/v2/make_targets.py --keys evals/reports/ch12/keys_new.tsv \
  --old-keys evals/reports/ch12/keys_old.tsv --out evals/v2/targets.jsonl
```

- [ ] **Step 2：Codex 编写题集和依据**

任务描述包含 spec §2.2、§2.4 全文和 §2.5 规则，另写明：
1. 逐行按 `targets.jsonl` 写，题号、桶、难度照抄；每组以清单中的目标键为基础，按 §2.4 规则 2 补全等价键（在 `keys_new.tsv` 和 `knowledge/docs/` 中查找写了同一事实的节）。
2. E 桶的问题必须需要每一组的事实；2 组题可以加入第 3 组，但 3 组题不能删组。
3. `d_evidence.jsonl` 写 120 行（v2 D 题 60 + 旧 300 题 D 题 60），关键词规则照 §2.5。
4. 必须通过：`uv run python evals/run_rag_eval.py --set v2 --check` 和 `uv run python evals/v2/check_d_evidence.py`。
5. 禁止运行 `run_rag_eval.py --stage` 的任何取值和任何 Milvus 检索。

- [ ] **Step 3：Claude 审核**

1. 两个检查命令退出码 0。
2. 抽查可回答题：每桶按题号固定抽取（A 每 8 题取 1、B 每 10 取 1、C 每 10 取 1、E 每 6 取 1，E 中 3 组题不少于 12 道），共不少于 24 题。逐题核对：问题与目标事实对应；等价键完整；难度标签符合定义；B hard 含干扰型号和非规范写法。
3. 审核 120 道 D 题：关键词是否足以证明无答案；问题是否属于声明的小类。
4. 问题多于抽查题数的 10% 时，写明规则交给 Codex 整批修改，然后回到第 1 步重新审核；不手改单题。

- [ ] **Step 4：冻结**

```bash
python3 - <<'EOF'
import json, hashlib, datetime, pathlib
p = pathlib.Path("evals/rag_eval_v2.jsonl"); t = pathlib.Path("evals/v2/targets.jsonl")
pathlib.Path("evals/v2/manifest.json").write_text(json.dumps({
    "samples_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    "targets_sha256": hashlib.sha256(t.read_bytes()).hexdigest(),
    "frozen_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "commit_note": "冻结前未运行任何 v2 检索评估"}, ensure_ascii=False, indent=2) + "\n")
EOF
git add evals/rag_eval_v2.jsonl evals/v2/targets.jsonl evals/v2/d_evidence.jsonl evals/v2/manifest.json
git commit -m "data(ch12): freeze v2 evaluation set (300 questions)"
```

---

### Task 6：新知识库上的 3 次运行与审计（Claude）

- [ ] **Step 1：v2**

```bash
uv run python evals/run_rag_eval.py --set v2 --stage retrieval --concurrency 3 \
  --save-rankings evals/reports/ch12/rankings_v2_newkb.json
```
报告复制为 `evals/reports/ch12/v2_newkb.md`。

- [ ] **Step 2：旧 300 题**

```bash
uv run python evals/run_rag_eval.py --stage retrieval --concurrency 3 \
  --save-rankings evals/reports/ch12/rankings_base_newkb.json
```
报告复制为 `evals/reports/ch12/base_newkb.md`。

- [ ] **Step 3：ch11 困难题集**

```bash
WT=/Users/harry/Aftersales-agent-ch11
cp /Users/harry/Aftersales-agent/knowledge/lexicon.json $WT/knowledge/lexicon.json
(cd $WT && uv run python evals/run_rag_eval.py --set hard --stage retrieval --concurrency 3 \
  --save-rankings /Users/harry/Aftersales-agent/evals/reports/ch12/rankings_hard_newkb.json)
git -C $WT checkout knowledge/lexicon.json
```
报告复制为 `evals/reports/ch12/hard_newkb.md`。

- [ ] **Step 4：新增小节审计**

```bash
for s in base hard; do
  uv run python evals/v2/new_chunk_audit.py --rankings evals/reports/ch12/rankings_${s}_newkb.json \
    --old-keys evals/reports/ch12/keys_old.tsv --new-keys evals/reports/ch12/keys_new.tsv \
    --out evals/reports/ch12/audit_${s}.md
done
```
Claude 逐题判定"等价事实"或"干扰项"，判定写进 `audit_*.md` 的新增一列。

- [ ] **Step 5：提交**

```bash
git add evals/reports/ch12
git commit -m "eval(ch12): v2, base and hard sets on the expanded knowledge base"
```

---

### Task 7：汇总报告、code review、交付（Claude）

- [ ] **Step 1：写 `evals/reports/ch12_comparison.md`**（spec §4.4 表 1–7，数字只取自 `evals/reports/ch12/`）。
- [ ] **Step 2：更新 CLAUDE.md**：项目状态加 ch12 一行；常用命令加 `--set v2`、`check_kb_v2.py`、`make_targets.py`、`check_d_evidence.py`、`new_chunk_audit.py`；设计约束加"v2 题集冻结后不改"。
- [ ] **Step 3：code review**：用 superpowers:requesting-code-review 审查整个分支；按结论修改（代码改动交给 Codex）。
- [ ] **Step 4：全量测试**：`uv run pytest -q`，记录通过数。
- [ ] **Step 5：dev-notes 末段**：摘录表 1–4 结论；推送并提 PR（不合并）。

```bash
git push -u origin ch12
gh pr create --base main --head ch12 --title "ch12: expanded knowledge base and v2 evaluation set" --body-file <(...)
git worktree remove ../Aftersales-agent-ch11
```
