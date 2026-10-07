"""RAG 评估指标与 Markdown 报告。所有函数不访问外部服务。"""

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

from app.prompts import REFUSAL_PREFIX

BUCKETS = ("A_policy", "B_model", "C_colloquial", "D_unanswerable", "E_multi")
ANSWERABLE = ("A_policy", "B_model", "C_colloquial", "E_multi")
DIFFICULTIES = ("easy", "medium", "hard")
KS = (1, 3, 5, 10)
THRESHOLDS = tuple(i / 100 for i in range(5, 81, 5))


def is_refusal(answer: str) -> bool:
    return answer.lstrip().startswith(REFUSAL_PREFIX)


def dedupe(keys: list[str]) -> list[str]:
    return list(dict.fromkeys(keys))


def recall_at_k(ranked_keys: list[str], relevant: list[str], k: int) -> float:
    targets = set(relevant)
    hits = set(dedupe(ranked_keys)[:max(k, 0)]) & targets
    return len(hits) / len(targets) if targets else 0.0


def reciprocal_rank(ranked_keys: list[str], relevant: list[str]) -> float:
    targets = set(relevant)
    return next((1 / i for i, key in enumerate(dedupe(ranked_keys), 1) if key in targets), 0.0)


@dataclass(frozen=True)
class RetrievalScore:
    sample_id: str
    bucket: str
    difficulty: str
    strategy: str
    recall: dict[int, float]
    rr: float


def score_retrieval(sample_id, bucket, difficulty, strategy, ranked_keys, relevant) -> RetrievalScore:
    return RetrievalScore(sample_id, bucket, difficulty, strategy,
                          {k: recall_at_k(ranked_keys, relevant, k) for k in KS},
                          reciprocal_rank(ranked_keys, relevant))


def summarize(scores: list[RetrievalScore], group: str) -> dict[tuple[str, str], dict[str, float]]:
    if group not in ("bucket", "difficulty"):
        raise ValueError("分组必须为 bucket 或 difficulty")
    grouped = defaultdict(list)
    for score in scores:
        grouped[(score.strategy, getattr(score, group))].append(score)
        grouped[(score.strategy, "ALL")].append(score)
    return {key: {**{f"R@{k}": mean(s.recall[k] for s in items) for k in KS},
                  "MRR": mean(s.rr for s in items), "n": len(items)}
            for key, items in grouped.items()}


@dataclass(frozen=True)
class ThresholdRow:
    bucket: str
    top1_relevant: bool
    top_score: float | None


def threshold_sweep(rows: list[ThresholdRow], thresholds=THRESHOLDS) -> list[tuple[float, float, float]]:
    answerable = [r for r in rows if r.bucket in ANSWERABLE]
    unanswerable = [r for r in rows if r.bucket == "D_unanswerable"]
    return [(t,
             sum(r.top1_relevant and r.top_score is not None and r.top_score >= t
                 for r in answerable) / len(answerable) if answerable else 0.0,
             sum(r.top_score is None or r.top_score < t
                 for r in unanswerable) / len(unanswerable) if unanswerable else 0.0)
            for t in thresholds]


def pick_threshold(sweep, min_keep: float = 0.95) -> float | None:
    return max((t for t, keep, _ in sweep if keep >= min_keep), default=None)


@dataclass
class GenResult:
    sample_id: str
    bucket: str
    difficulty: str
    strategy: str
    query: str
    retrieved: bool
    refused: bool
    answer: str
    citations: list[dict]
    faithful: bool | None
    unsupported: list[str]
    reason: str


def generation_summary(results: list[GenResult]) -> dict[str, dict[str, float]]:
    grouped = defaultdict(list)
    for result in results:
        grouped[result.strategy].append(result)
    out = {}
    for strategy, items in grouped.items():
        answerable = [r for r in items if r.bucket in ANSWERABLE]
        unanswerable = [r for r in items if r.bucket == "D_unanswerable"]
        judged = [r for r in answerable if r.retrieved and not r.refused and r.faithful is not None]
        out[strategy] = {
            "faithfulness": sum(r.faithful is True for r in judged) / len(judged) if judged else 0.0,
            "false_refusal": sum(r.refused for r in answerable) / len(answerable) if answerable else 0.0,
            "d_refusal": sum(r.refused for r in unanswerable) / len(unanswerable) if unanswerable else 0.0,
            "no_retrieval": sum(not r.retrieved for r in items),
            "judge_failed": sum(r.retrieved and not r.refused and r.faithful is None for r in items),
            "answered": sum(r.retrieved and not r.refused for r in items),
        }
    return out


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_report(*, retrieval_by_bucket, retrieval_by_difficulty, post_threshold, sweep,
                  current_threshold, generation, failures: list[str]) -> str:
    sections = []
    for title, group, data in (
        ("检索：策略 × 桶", "桶", retrieval_by_bucket),
        ("检索：策略 × 难度", "难度", retrieval_by_difficulty),
        ("hybrid_rerank 门槛后", "桶", post_threshold),
    ):
        if data is None:
            continue
        lines = [f"## {title}", "", f"| 策略 | {group} | R@1 | R@3 | R@5 | R@10 | MRR | 题数 |",
                 "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for (strategy, name), values in data.items():
            numbers = " | ".join(f"{values[key]:.3f}" for key in ("R@1", "R@3", "R@5", "R@10", "MRR"))
            lines.append(f"| {strategy} | {name} | {numbers} | {int(values['n'])} |")
        sections.append("\n".join(lines))
    if sweep is not None:
        lines = ["## 门槛扫描", "", "| 门槛 | A/B/C/E Top-1 保留率 | D 门槛拒答率 |",
                 "| --- | --- | --- |"]
        lines.extend(f"| {t:.3f} | {keep:.3f} | {refuse:.3f} |" for t, keep, refuse in sweep)
        suggested = pick_threshold(sweep)
        suggestion = f"{suggested:.2f}" if suggested is not None else "无符合条件的门槛"
        current = f"{current_threshold:.2f}" if current_threshold is not None else "未提供"
        lines.extend(["", f"当前 RERANK_MIN_SCORE：{current}；按校准规则建议：{suggestion}"])
        sections.append("\n".join(lines))
    if generation is not None:
        lines = ["## 生成", "", "| 策略 | Faithfulness | 误拒率 | D 正确拒答率 | 未检索 | 裁判失败 |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for strategy, values in generation_summary(generation).items():
            lines.append(f"| {strategy} | {values['faithfulness']:.3f} | {values['false_refusal']:.3f} | "
                         f"{values['d_refusal']:.3f} | {values['no_retrieval']} | {values['judge_failed']} |")
        sections.append("\n".join(lines))
        cases = []
        for r in generation:
            if r.bucket == "D_unanswerable" and not r.refused:
                detail = f"D 误答：{r.answer}"
            elif r.bucket in ANSWERABLE and r.retrieved and not r.refused and r.faithful is False:
                detail = f"未找到依据：{'；'.join(r.unsupported)}；{r.reason}"
            else:
                continue
            cases.append(f"- {r.sample_id} [{r.strategy}] {_cell(r.query)} → {_cell(detail)}")
        sections.append("## 编造个案与 D 误答\n\n" + ("\n".join(cases) or "无"))
    if failures:
        sections.append("## 执行失败\n\n" + "\n".join(f"- {_cell(f)}" for f in failures))
    return "\n\n".join(sections) + "\n"
