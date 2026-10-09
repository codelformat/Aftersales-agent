"""置信度闸的信号与分数。纯函数，线上闸和校准脚本共用。"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.config import GATE_CONF_THRESHOLD, GATE_EFFECTIVE_N, GATE_WEIGHTS, RERANK_MIN_SCORE


@dataclass(frozen=True)
class Confidence:
    top1: float
    effective: float
    margin: float
    score: float

    def to_dict(self) -> dict:
        return {k: round(getattr(self, k), 4) for k in ("top1", "effective", "margin", "score")}


def evidence_confidence(
    scores: Sequence[float], *, weights: tuple[float, float, float] = GATE_WEIGHTS,
    effective_n: int = GATE_EFFECTIVE_N, min_score: float = RERANK_MIN_SCORE,
) -> Confidence:
    ranked = sorted(scores, reverse=True)
    if not ranked:
        return Confidence(0.0, 0.0, 0.0, 0.0)
    top1 = ranked[0]
    top2 = ranked[1] if len(ranked) > 1 else 0.0
    effective = min(sum(s >= min_score for s in ranked) / effective_n, 1.0)
    margin = top1 - top2
    w1, w2, w3 = weights
    return Confidence(top1, effective, margin, w1 * top1 + w2 * effective + w3 * margin)


def gate_passes(
    scores: Sequence[float], *, weights: tuple[float, float, float] = GATE_WEIGHTS,
    effective_n: int = GATE_EFFECTIVE_N, threshold: float = GATE_CONF_THRESHOLD,
    min_score: float = RERANK_MIN_SCORE,
) -> tuple[bool, Confidence]:
    conf = evidence_confidence(scores, weights=weights, effective_n=effective_n, min_score=min_score)
    # 没有过检索门槛的证据时，Agent 拿不到任何证据。
    has_evidence = any(s >= min_score for s in scores)
    return has_evidence and conf.score >= threshold, conf
