"""置信度闸参数的网格搜索与交叉验证。纯函数，不访问外部服务。"""

import random
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass

from app.services.confidence import gate_passes
from evals.rag_metrics import ANSWERABLE

D_BUCKET = "D_unanswerable"
THRESHOLDS = tuple(i / 100 for i in range(0, 101))


@dataclass(frozen=True)
class SignalRow:
    sample_id: str
    bucket: str
    top1_relevant: bool
    scores: list[float]


@dataclass(frozen=True)
class Params:
    weights: tuple[float, float, float]
    effective_n: int
    threshold: float


@dataclass(frozen=True)
class Outcome:
    params: Params
    keep: float
    d_reject: float


def weight_grid(step: float = 0.1) -> list[tuple[float, float, float]]:
    n = round(1 / step)
    return [(round(a * step, 1), round(b * step, 1), round((n - a - b) * step, 1))
            for a in range(n + 1) for b in range(n + 1 - a)]


def evaluate(rows: list[SignalRow], params: Params) -> Outcome:
    answerable = [r for r in rows if r.bucket in ANSWERABLE]
    unanswerable = [r for r in rows if r.bucket == D_BUCKET]

    def passed(r):
        return gate_passes(r.scores, weights=params.weights, effective_n=params.effective_n,
                           threshold=params.threshold)[0]

    keep = sum(r.top1_relevant and passed(r) for r in answerable) / len(answerable) if answerable else 0.0
    d_reject = sum(not passed(r) for r in unanswerable) / len(unanswerable) if unanswerable else 0.0
    return Outcome(params, keep, d_reject)


def _key(o: Outcome):
    # D 拒答率最高；相同时保留率更高；再相同时取门槛低、N 小、w1 大的参数，结果可复现。
    return (-o.d_reject, -o.keep, o.params.threshold, o.params.effective_n, tuple(-w for w in o.params.weights))


def search(rows, *, min_keep: float = 0.95, ns=(2, 3, 4, 5), thresholds=THRESHOLDS) -> Outcome | None:
    answerable = [r for r in rows if r.bucket in ANSWERABLE]
    unanswerable = [r for r in rows if r.bucket == D_BUCKET]
    ns, thresholds = tuple(ns), tuple(thresholds)
    best = None
    for w in weight_grid():
        for n in ns:
            # 每组参数只计算一次置信分。二分计数保留 >= 门槛的边界语义。
            kept_scores, d_scores = [], []
            for r in answerable + unanswerable:
                eligible, conf = gate_passes(r.scores, weights=w, effective_n=n, threshold=float("-inf"))
                if eligible:
                    if r.bucket == D_BUCKET:
                        d_scores.append(conf.score)
                    elif r.top1_relevant:
                        kept_scores.append(conf.score)
            kept_scores.sort()
            d_scores.sort()
            for t in thresholds:
                keep = ((len(kept_scores) - bisect_left(kept_scores, t)) / len(answerable)
                        if answerable else 0.0)
                d_reject = ((len(unanswerable) - len(d_scores) + bisect_left(d_scores, t)) / len(unanswerable)
                            if unanswerable else 0.0)
                o = Outcome(Params(w, n, t), keep, d_reject)
                if o.keep >= min_keep and (best is None or _key(o) < _key(best)):
                    best = o
    return best


def stratified_folds(rows, k: int = 5, seed: int = 0) -> list[list[SignalRow]]:
    by_bucket = defaultdict(list)
    for r in rows:
        by_bucket[r.bucket].append(r)
    folds = [[] for _ in range(k)]
    rng = random.Random(seed)
    for bucket in sorted(by_bucket):
        items = sorted(by_bucket[bucket], key=lambda r: r.sample_id)
        rng.shuffle(items)
        for i, r in enumerate(items):
            folds[i % k].append(r)
    return folds


def cross_validate(rows, k: int = 5, seed: int = 0, min_keep: float = 0.95) -> list[tuple[Outcome, Outcome]]:
    folds = stratified_folds(rows, k, seed)
    out = []
    for i, test in enumerate(folds):
        train = [r for j, f in enumerate(folds) if j != i for r in f]
        best = search(train, min_keep=min_keep)
        if best is None:
            continue
        out.append((best, evaluate(test, best.params)))
    return out


def render_report(full: Outcome | None, folds, old: Outcome, n_rows: int) -> str:
    lines = ["# 置信度闸校准报告", "", f"样本数：{n_rows}；规则：可答题保留率 ≥ 95% 时 D 桶拒答率最高。", ""]
    lines += ["## 全量最优", ""]
    if full is None:
        lines.append("没有满足保留率约束的参数。")
    else:
        p = full.params
        lines.append(f"- GATE_WEIGHTS = {p.weights}\n- GATE_EFFECTIVE_N = {p.effective_n}\n"
                     f"- GATE_CONF_THRESHOLD = {p.threshold:.2f}\n- 保留率 {full.keep:.3f}，D 拒答率 {full.d_reject:.3f}")
    lines += ["", "## 旧规则（Top-1 ≥ 0.20）", "", f"- 保留率 {old.keep:.3f}，D 拒答率 {old.d_reject:.3f}", "",
              "## 5 折交叉验证", "", "| 折 | 参数 | 训练保留率 | 训练 D 拒答率 | 验证保留率 | 验证 D 拒答率 |",
              "|---|---|---|---|---|---|"]
    for i, (train, test) in enumerate(folds, 1):
        p = train.params
        lines.append(f"| {i} | {p.weights}/N={p.effective_n}/t={p.threshold:.2f} | {train.keep:.3f} | "
                     f"{train.d_reject:.3f} | {test.keep:.3f} | {test.d_reject:.3f} |")
    return "\n".join(lines)
