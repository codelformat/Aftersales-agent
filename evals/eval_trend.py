"""评估轮次的指标组装与趋势。纯函数。"""

from dataclasses import dataclass
from datetime import datetime

from app.config import EVAL_DROP_TOLERANCE, GATE_CONF_THRESHOLD
from evals.rag_metrics import generation_summary, summarize

STRATEGY = "hybrid_rerank"
METRIC_KEYS = ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "faithfulness",
               "false_refusal", "d_refusal")
LOWER_IS_BETTER = {"false_refusal"}


def build_metrics(scores, results, failures: int) -> dict:
    retrieval = summarize(scores, "bucket")[(STRATEGY, "ALL")]
    gen = generation_summary(results)[STRATEGY]
    return {
        "strategy": STRATEGY,
        **{f"recall_at_{k}": round(retrieval[f"R@{k}"], 4) for k in (1, 3, 5, 10)},
        "mrr": round(retrieval["MRR"], 4),
        "faithfulness": round(gen["faithfulness"], 4),
        "false_refusal": round(gen["false_refusal"], 4),
        "d_refusal": round(gen["d_refusal"], 4),
        "judge_failed": gen["judge_failed"],
        "failures": failures,
        "gate_conf_threshold": GATE_CONF_THRESHOLD,
    }


@dataclass(frozen=True)
class RunPoint:
    id: int
    created_at: datetime
    triggered_by: str
    dataset_size: int
    metrics: dict


@dataclass(frozen=True)
class Trend:
    points: list[RunPoint]
    deltas: list[dict[str, float | None]]
    dropped: list[str]


def trend(runs: list[RunPoint], last: int = 10, tolerance: float = EVAL_DROP_TOLERANCE) -> Trend:
    if not runs:
        return Trend([], [], [])
    size = runs[-1].dataset_size
    points = [r for r in runs if r.dataset_size == size][-last:]
    deltas = [{k: None for k in METRIC_KEYS}]
    for prev, cur in zip(points, points[1:]):
        deltas.append({k: (cur.metrics[k] - prev.metrics[k]) if k in cur.metrics and k in prev.metrics else None
                       for k in METRIC_KEYS})
    dropped = []
    if len(points) > 1:
        for k in METRIC_KEYS:
            d = deltas[-1][k]
            if d is not None and (d > tolerance if k in LOWER_IS_BETTER else d < -tolerance):
                dropped.append(k)
    return Trend(points, deltas, dropped)


def render_trend(t: Trend) -> str:
    if not t.points:
        return "eval_runs 中没有数据"
    head = "| 轮次 | 时间 | 触发 | 题数 | " + " | ".join(METRIC_KEYS) + " |"
    lines = [head, "|" + "---|" * (4 + len(METRIC_KEYS))]
    for p, d in zip(t.points, t.deltas):
        cells = []
        for k in METRIC_KEYS:
            v, dv = p.metrics.get(k), d[k]
            if v is None:
                cells.append("-")
                continue
            mark = ""
            if dv is not None:
                worse = dv > EVAL_DROP_TOLERANCE if k in LOWER_IS_BETTER else dv < -EVAL_DROP_TOLERANCE
                mark = f" ({dv:+.3f}{' ↓' if worse else ''})"
            cells.append(f"{v:.3f}{mark}")
        lines.append(f"| {p.id} | {p.created_at:%Y-%m-%d %H:%M} | {p.triggered_by} | {p.dataset_size} | "
                     + " | ".join(cells) + " |")
    lines.append("")
    lines.append("下滑指标：" + ("、".join(t.dropped) if t.dropped else "无"))
    return "\n".join(lines)

