import pytest

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
    assert ex.check_expectations(table(rerank_r5=0.88))[1][2] is True
    assert ex.check_expectations(table(rerank_r5=0.879))[1][2] is False
    assert ex.check_expectations(table(rerank_r5=0.92))[1][2] is True
    assert ex.check_expectations(table(rerank_r5=0.921))[1][2] is False


def test_expectation_3_fails_when_other_mrr_reaches_090():
    assert ex.check_expectations(table(others_mrr=0.90))[2][2] is False


def test_render_marks_result():
    md = ex.render_expectations(ex.check_expectations(table(rerank_r5=0.95)))
    assert "## 冻结前写定的预期" in md and "不符合" in md and "符合" in md


def test_missing_buckets_do_not_raise():
    rows = ex.check_expectations({("hybrid_rerank", "B_model"): {"R@1": 1.0, "R@5": 1.0, "MRR": 1.0}})
    assert len(rows) == 6 and not any(r[2] for r in rows) and "数据不足" in rows[0][3]


@pytest.mark.parametrize("index, key, metric, value", [
    (0, ("dense", "ALL"), "R@5", 0.90),
    (2, ("hybrid", "ALL"), "R@5", 0.90),
    (3, ("hybrid", "E_multi"), "R@5", 0.85),
    (4, ("hybrid_rerank", "B_model"), "R@1", 0.849),
    (5, ("dense", "C_colloquial"), "MRR", 0.50),
])
def test_strict_comparisons_and_minimum_gain(index, key, metric, value):
    t = table()
    t[key][metric] = value
    assert ex.check_expectations(t)[index][2] is False


def test_expectation_5_includes_exact_005_gain():
    t = table()
    t[("hybrid_rerank", "B_model")]["R@1"] = 0.85
    assert ex.check_expectations(t)[4][2] is True


def test_missing_metric_only_affects_dependent_expectations():
    t = table()
    del t[("hybrid", "ALL")]["MRR"]
    rows = ex.check_expectations(t)
    assert rows[2][2:] == (False, "数据不足")
    assert all(row[2] for row in rows if row[0] != 3)


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


@pytest.mark.parametrize("metrics, expected", [
    ({"faithfulness": 0.95}, [(7, True), (9, False)]),
    ({"false_refusal": 0.05}, [(7, False), (9, True)]),
    ({}, [(7, False), (9, False)]),
])
def test_generation_expectations_missing_metrics(metrics, expected):
    rows = ex.check_generation_expectations({"hybrid_rerank": metrics}, cov())
    assert [(n, met) for n, _, met, _ in rows if n in (7, 9)] == expected
    assert all(actual == "数据不足" for n, _, met, actual in rows if n in (7, 9) and not met)
