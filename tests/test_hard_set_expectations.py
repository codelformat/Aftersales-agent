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
