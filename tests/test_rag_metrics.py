import pytest

from app.prompts import REFUSAL_PREFIX
from evals import rag_metrics as rm


def test_is_refusal():
    assert rm.is_refusal("  " + REFUSAL_PREFIX + "建议转人工")
    assert not rm.is_refusal("可以退" + REFUSAL_PREFIX)


def test_dedupe_keeps_first():
    assert rm.dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_at_k_dedupes_by_source_key():
    ranked = ["x", "x", "a", "b"]          # 表格拆成两块，共用来源键 x
    assert rm.recall_at_k(ranked, ["a", "b"], 1) == 0.0
    assert rm.recall_at_k(ranked, ["a", "b"], 2) == 0.5
    assert rm.recall_at_k(ranked, ["a", "b"], 3) == 1.0


def test_reciprocal_rank():
    assert rm.reciprocal_rank(["x", "x", "a"], ["a"]) == 0.5
    assert rm.reciprocal_rank(["x"], ["a"]) == 0.0
    assert rm.reciprocal_rank([], ["a"]) == 0.0


def test_summarize_by_bucket_and_all():
    s = [
        rm.score_retrieval("A01", "A_policy", "easy", "bm25", ["a"], ["a"]),
        rm.score_retrieval("B01", "B_model", "hard", "bm25", ["x", "b"], ["b"]),
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


def gen(bucket, *, retrieved=True, refused=False, faithful=True, strategy="hybrid_rerank"):
    return rm.GenResult("X01", bucket, "easy", strategy, "q", retrieved, refused, "a", [], faithful, [], "")


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
