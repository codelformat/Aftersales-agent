import pytest

from app.prompts import REFUSAL_PREFIX
from evals import rag_metrics as rm


def test_is_refusal():
    assert rm.is_refusal("  " + REFUSAL_PREFIX + "建议转人工")
    assert not rm.is_refusal("可以退" + REFUSAL_PREFIX)
    assert rm.is_refusal(REFUSAL_PREFIX.rstrip("。") + "，建议转人工")


def test_dedupe_keeps_first():
    assert rm.dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_at_k_dedupes_by_source_key():
    ranked = ["x", "x", "a", "b"]          # 表格拆成两块，共用来源键 x
    groups = [["a"], ["b"]]
    assert rm.recall_at_k(ranked, groups, 1) == 0.0
    assert rm.recall_at_k(ranked, groups, 2) == 0.5
    assert rm.recall_at_k(ranked, groups, 3) == 1.0


def test_recall_equivalent_keys_count_as_one_group():
    assert rm.recall_at_k(["b"], [["a", "b"]], 1) == 1.0
    assert rm.recall_at_k(["a", "b"], [["a", "b"]], 2) == 1.0


def test_recall_each_group_needs_a_hit():
    groups = [["a"], ["c", "d"]]
    ranked = ["x", "d", "a"]
    assert rm.recall_at_k(ranked, groups, 1) == 0.0
    assert rm.recall_at_k(ranked, groups, 2) == 0.5
    assert rm.recall_at_k(ranked, groups, 3) == 1.0
    assert rm.recall_at_k(ranked, [], 3) == 0.0


def test_reciprocal_rank():
    assert rm.reciprocal_rank(["x", "x", "a"], [["a"]]) == 0.5
    assert rm.reciprocal_rank(["x"], [["a"]]) == 0.0
    assert rm.reciprocal_rank([], [["a"]]) == 0.0
    assert rm.reciprocal_rank(["x", "c", "a"], [["a"], ["c", "d"]]) == 0.5   # 任一组的首个命中


def test_summarize_by_bucket_and_all():
    s = [
        rm.score_retrieval("A01", "A_policy", "easy", "bm25", ["a"], [["a"]]),
        rm.score_retrieval("B01", "B_model", "hard", "bm25", ["x", "b"], [["b"]]),
    ]
    out = rm.summarize(s, "bucket")
    assert out[("bm25", "A_policy")]["MRR"] == 1.0
    assert out[("bm25", "B_model")]["R@1"] == 0.0 and out[("bm25", "B_model")]["R@3"] == 1.0
    assert out[("bm25", "ALL")]["MRR"] == pytest.approx(0.75) and out[("bm25", "ALL")]["n"] == 2
    assert rm.summarize(s, "difficulty")[("bm25", "hard")]["n"] == 1


def test_threshold_sweep_and_pick():
    rows = [
        rm.ThresholdRow("A_policy", True, 0.9),
        rm.ThresholdRow("A_policy", True, 0.4),
        rm.ThresholdRow("B_model", False, 0.95),   # 第 1 名不相关，任何门槛都不算保留
        rm.ThresholdRow("D_unanswerable", False, 0.2),
        rm.ThresholdRow("D_unanswerable", False, None),
    ]
    sweep = dict((t, (keep, refuse)) for t, keep, refuse in rm.threshold_sweep(rows, (0.1, 0.3, 0.5)))
    assert sweep[0.1] == (pytest.approx(2 / 3), 0.5)
    assert sweep[0.3] == (pytest.approx(2 / 3), 1.0)
    assert sweep[0.5] == (pytest.approx(1 / 3), 1.0)
    assert rm.pick_threshold([(0.1, 0.96, 0.2), (0.2, 0.95, 0.5), (0.3, 0.90, 0.8)]) == 0.2
    assert rm.pick_threshold([(0.1, 0.5, 0.2)]) is None
    assert len(rm.THRESHOLDS) == 16 and rm.THRESHOLDS[0] == 0.05 and rm.THRESHOLDS[-1] == 0.8


def gen(bucket, *, retrieved=True, refused=False, faithful=True, strategy="hybrid_rerank", run=1):
    return rm.GenResult("X01", bucket, "easy", strategy, "q", retrieved, refused, "a", [], faithful, [], "", run)


def test_generation_summary():
    out = rm.generation_summary([
        gen("A_policy"), gen("A_policy", faithful=False), gen("B_model", refused=True, faithful=None),
        gen("C_colloquial", retrieved=False, faithful=None),
        gen("D_unanswerable", refused=True, faithful=None), gen("D_unanswerable", faithful=False),
        gen("E_multi", faithful=None),   # 裁判失败
    ])["hybrid_rerank"]
    assert out["faithfulness"] == pytest.approx(1 / 2)     # 只算检索过、未拒答、裁判成功的 A/B/C/E
    assert out["false_refusal"] == pytest.approx(1 / 5)    # A/B/C/E 共 5 题，拒答 1 题
    assert out["d_refusal"] == pytest.approx(1 / 2)
    assert out["no_retrieval"] == 1 and out["judge_failed"] == 1


def test_render_report_sections():
    md = rm.render_report(
        retrieval_by_bucket={("bm25", "ALL"): {"R@1": 0.5, "R@3": 0.6, "R@5": 0.7, "R@10": 0.8, "MRR": 0.55, "n": 2}},
        retrieval_by_difficulty={("bm25", "easy"): {"R@1": 0.5, "R@3": 0.6, "R@5": 0.7, "R@10": 0.8, "MRR": 0.55, "n": 2}},
        post_threshold=None, sweep=[(0.3, 0.96, 0.8)], current_threshold=0.3,
        generation=None, failures=[],
    )
    assert "## 检索：策略 × 桶" in md and "## 检索：策略 × 难度" in md and "## 门槛扫描" in md
    assert "| bm25 | ALL | 0.500 |" in md
    assert "## 生成" not in md


def test_hard_buckets_are_answerable():
    assert set(rm.HARD_BUCKETS) <= set(rm.ANSWERABLE) and set(rm.HARD_BUCKETS) <= set(rm.BUCKETS)


def test_generation_summary_averages_runs_and_report_lists_each_run():
    results = [gen("H1_sibling", strategy="dense", run=1, faithful=True), gen("H1_sibling", strategy="dense", run=1, faithful=True), gen("H1_sibling", strategy="dense", run=2, faithful=True), gen("H1_sibling", strategy="dense", run=2, faithful=False)]
    summary = rm.generation_summary(results)
    assert summary["dense"]["faithfulness"] == 0.75          # (1.0 + 0.5) / 2
    assert summary["dense"]["runs"] == [1.0, 0.5]
    md = rm.render_report(retrieval_by_bucket=None, retrieval_by_difficulty=None, post_threshold=None,
                          sweep=None, current_threshold=None, generation=results, failures=[])
    assert "| dense | 0.750 |" in md and "1.000 / 0.500" in md


def test_generation_summary_single_run_unchanged():
    summary = rm.generation_summary([gen("H1_sibling", strategy="dense", run=1, faithful=True), gen("H1_sibling", strategy="dense", run=1, faithful=False)])
    assert summary["dense"]["faithfulness"] == 0.5 and summary["dense"]["runs"] == [0.5]


def test_generation_summary_weights_runs_equally_with_different_judged_counts():
    results = [gen("H1_sibling", strategy="dense", run=2, faithful=False),
               gen("H1_sibling", strategy="dense", run=1, faithful=True),
               gen("H1_sibling", strategy="dense", run=1, faithful=True),
               gen("H1_sibling", strategy="dense", run=2, faithful=None)]
    summary = rm.generation_summary(results)["dense"]
    assert summary["faithfulness"] == 0.5
    assert summary["runs"] == [1.0, 0.0]
    assert summary["judge_failed"] == 0.5


@pytest.mark.parametrize("metric, expected", [
    ("no_retrieval", 1.5), ("judge_failed", 1.5), ("answered", 2.0),
])
def test_generation_summary_averages_counts_per_run(metric, expected):
    results = [
        gen("A_policy", run=1, retrieved=False, faithful=None),
        gen("A_policy", run=1, faithful=None),
        gen("A_policy", run=2, retrieved=False, faithful=None),
        gen("A_policy", run=2, retrieved=False, faithful=None),
        gen("A_policy", run=2, faithful=None),
        gen("D_unanswerable", run=2, faithful=None),
        gen("A_policy", run=2),
    ]
    assert rm.generation_summary(results)["hybrid_rerank"][metric] == expected


@pytest.mark.parametrize("metric, expected", [
    ("false_refusal", 2 / 3), ("d_refusal", 0.5),
])
def test_generation_summary_weights_refusal_rates_equally_per_run(metric, expected):
    results = [
        gen("A_policy", run=1, refused=True),
        gen("D_unanswerable", run=1, refused=True),
        gen("A_policy", run=2, refused=True),
        gen("A_policy", run=2), gen("A_policy", run=2),
        gen("D_unanswerable", run=2), gen("D_unanswerable", run=2),
        gen("D_unanswerable", run=2),
    ]
    assert rm.generation_summary(results)["hybrid_rerank"][metric] == pytest.approx(expected)


def test_generation_repeated_report_labels_average_counts():
    results = [
        gen("A_policy", strategy="dense", run=1, retrieved=False),
        gen("A_policy", strategy="dense", run=1, faithful=None),
        gen("A_policy", strategy="dense", run=2, retrieved=False),
        gen("A_policy", strategy="dense", run=2, retrieved=False),
        gen("A_policy", strategy="dense", run=2, faithful=None),
    ]
    md = rm.render_report(retrieval_by_bucket=None, retrieval_by_difficulty=None,
                          post_threshold=None, sweep=None, current_threshold=None,
                          generation=results, failures=[])
    assert "| 未检索（每次平均） | 裁判失败（每次平均） |" in md
    assert "| dense | 0.000 | 0.000 | 0.000 | 1.5 | 1 | 0.000 / 0.000 |" in md


@pytest.mark.parametrize("bucket, faithful, detail", [
    ("H1_sibling", False, "未找到依据：；"),
    ("D_unanswerable", True, "D 误答：a"),
])
def test_generation_repeated_report_identifies_case_runs(bucket, faithful, detail):
    results = [gen(bucket, strategy="dense", run=1, faithful=faithful),
               gen(bucket, strategy="dense", run=2, faithful=faithful),
               gen(bucket, strategy="bm25", faithful=faithful)]
    md = rm.render_report(retrieval_by_bucket=None, retrieval_by_difficulty=None,
                          post_threshold=None, sweep=None, current_threshold=None,
                          generation=results, failures=[])
    assert f"- X01 [dense#1] q → {detail}" in md
    assert f"- X01 [dense#2] q → {detail}" in md
    assert f"- X01 [bm25#1] q → {detail}" in md


def test_generation_single_run_cases_keep_original_format():
    md = rm.render_report(retrieval_by_bucket=None, retrieval_by_difficulty=None,
                          post_threshold=None, sweep=None, current_threshold=None,
                          generation=[gen("H1_sibling", faithful=False),
                                      gen("D_unanswerable")], failures=[])
    assert md == (
        "## 生成\n\n"
        "| 策略 | Faithfulness | 误拒率 | D 正确拒答率 | 未检索 | 裁判失败 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| hybrid_rerank | 0.000 | 0.000 | 0.000 | 0 | 0 |\n\n"
        "## 编造个案与 D 误答\n\n"
        "- X01 [hybrid_rerank] q → 未找到依据：；\n"
        "- X01 [hybrid_rerank] q → D 误答：a\n"
    )


def test_generation_single_run_table_matches_original_exactly():
    md = rm.render_report(retrieval_by_bucket=None, retrieval_by_difficulty=None,
                          post_threshold=None, sweep=None, current_threshold=None,
                          generation=[gen("A_policy")], failures=[])
    assert md == (
        "## 生成\n\n"
        "| 策略 | Faithfulness | 误拒率 | D 正确拒答率 | 未检索 | 裁判失败 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| hybrid_rerank | 1.000 | 0.000 | 0.000 | 0 | 0 |\n\n"
        "## 编造个案与 D 误答\n\n无\n"
    )
