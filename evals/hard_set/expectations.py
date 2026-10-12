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
