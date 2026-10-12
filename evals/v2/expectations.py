"""对照冻结前写定的检索和生成预期。"""

from decimal import Decimal

STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
ANSWERABLE_V2 = ("A_policy", "B_model", "C_colloquial", "E_multi")


def check_expectations(t: dict[tuple[str, str], dict[str, float]]) -> list[tuple[int, str, bool, str]]:
    rows = []
    others = STRATEGIES[:-1]

    def check(number, description, keys, predicate):
        try:
            values = [t[(strategy, bucket)][metric] for strategy, bucket, metric in keys]
        except KeyError:
            rows.append((number, description, False, "数据不足"))
            return
        actual = "；".join(f"{strategy}/{bucket} {metric}={value:.3f}"
                           for (strategy, bucket, metric), value in zip(keys, values))
        rows.append((number, description, bool(predicate(values)), actual))

    check(1, "hybrid_rerank 的 R@5 在 4 种策略中最高",
          [(s, "ALL", "R@5") for s in ("hybrid_rerank", *others)],
          lambda v: all(v[0] > x for x in v[1:]))
    check(2, "hybrid_rerank 的 R@5 在 0.88–0.92 之间（含两端）",
          [("hybrid_rerank", "ALL", "R@5")], lambda v: 0.88 <= v[0] <= 0.92)
    check(3, "dense、bm25、hybrid 的 R@5 和 MRR 都低于 0.90",
          [(s, "ALL", m) for s in others for m in ("R@5", "MRR")],
          lambda v: all(x < 0.90 for x in v))
    check(4, "每种策略的 E_multi R@5 都是 4 个可回答桶中最低的",
          [(s, b, "R@5") for s in STRATEGIES for b in ("E_multi", *ANSWERABLE_V2[:-1])],
          lambda v: all(v[i] < min(v[i + 1:i + 4]) for i in range(0, len(v), 4)))
    check(5, "B_model 中 hybrid_rerank 的 R@1 比 dense 至少高 0.05",
          [(s, "B_model", "R@1") for s in ("hybrid_rerank", "dense")],
          lambda v: Decimal(str(v[0])) - Decimal(str(v[1])) >= Decimal("0.05"))
    check(6, "C_colloquial 中 bm25 的 MRR 是 4 种策略中最低的",
          [(s, "C_colloquial", "MRR") for s in ("bm25", "dense", "hybrid", "hybrid_rerank")],
          lambda v: all(v[0] < x for x in v[1:]))
    return rows


def check_generation_expectations(
    gen: dict[str, dict[str, float]], cov: dict[tuple[str, str], dict],
) -> list[tuple[int, str, bool, str]]:
    """预期 7–9，只看 hybrid_rerank。"""
    rows = []
    g = gen.get("hybrid_rerank", {})
    faith, refusal = g.get("faithfulness"), g.get("false_refusal")
    desc7 = "hybrid_rerank 的 Faithfulness ≥ 0.90"
    desc8 = "E_multi 的组覆盖率（全部）是 4 个可回答桶中最低的"
    desc9 = "可回答桶误拒率 ≤ 0.10"
    rows.append((7, desc7, False, "数据不足") if faith is None else
                (7, desc7, faith >= 0.90, f"Faithfulness={faith:.3f}"))
    values = [cov.get(("hybrid_rerank", b), {}).get("group_all") for b in ANSWERABLE_V2]
    if any(v is None for v in values):
        rows.append((8, desc8, False, "数据不足"))
    else:
        rows.append((8, desc8, all(values[-1] < v for v in values[:-1]),
                     "；".join(f"{b}={v:.3f}" for b, v in zip(ANSWERABLE_V2, values))))
    rows.append((9, desc9, False, "数据不足") if refusal is None else
                (9, desc9, refusal <= 0.10, f"误拒率={refusal:.3f}"))
    return rows


def render_expectations(rows: list[tuple[int, str, bool, str]], title: str = "冻结前写定的预期") -> str:
    lines = [f"## {title}", "", "| 编号 | 预期 | 结果 | 实际值 |",
             "|---|---|---|---|"]
    lines.extend(f"| {number} | {description} | {'符合' if met else '不符合'} | {actual} |"
                 for number, description, met, actual in rows)
    return "\n".join(lines) + "\n"
