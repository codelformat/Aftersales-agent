"""事实覆盖率：参考原文、裁判调用、汇总和报告。"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
import logging

from langchain_core.runnables import Runnable
from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.retrieval import source_key
from app.schemas import CoverageVerdict
from evals.rag_eval_set import EvalSample
from evals.rag_metrics import ANSWERABLE, _cell

logger = logging.getLogger(__name__)
EXCLUDED_CONTENT_TYPES = ("mined", "flywheel")
BUCKET_ORDER = (*ANSWERABLE, "ALL")


@dataclass
class CoverageResult:
    sample_id: str
    bucket: str
    strategy: str
    keys: list[str]
    refused: bool
    covered: list[bool] | None
    reasons: list[str] = field(default_factory=list)
    error: str | None = None


async def load_reference_texts(keys: Iterable[str]) -> dict[str, str]:
    """一个来源键对应多个知识块时，按主键升序拼接。"""
    wanted = set(keys)
    parts = defaultdict(list)
    async with get_sessionmaker()() as s:
        rows = await s.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.vectorize_status == "done",
            KnowledgeChunk.content_type.notin_(EXCLUDED_CONTENT_TYPES),
        ).order_by(KnowledgeChunk.id))
        for row in rows:
            key = source_key(row.section_path or "", row.questions)
            if key in wanted:
                parts[key].append(row.answer)
    return {key: "\n".join(texts) for key, texts in parts.items()}


def format_references(keys: list[str], texts: dict[str, str]) -> str:
    return "\n\n".join(f"[{n}] {key}\n{texts[key]}" for n, key in enumerate(keys, 1))


def parse_verdict(parsed, k: int) -> tuple[list[bool], list[str]]:
    verdict = CoverageVerdict.model_validate(parsed)
    numbers = [g.n for g in verdict.groups]
    if sorted(numbers) != list(range(1, k + 1)):
        raise ValueError(f"覆盖率裁判编号无效：{numbers}")
    ordered = sorted(verdict.groups, key=lambda g: g.n)
    return [g.covered for g in ordered], [g.reason for g in ordered]


async def judge_coverage(sample: EvalSample, strategy: str, answer: str, refused: bool,
                         texts: dict[str, str], judge: Runnable) -> CoverageResult:
    """拒答时每组记未覆盖，不调裁判。参考原文取每组第 1 个来源键。"""
    keys = [group[0] for group in sample.relevant]
    result = CoverageResult(sample.id, sample.bucket, strategy, keys, refused, None)
    missing = [key for key in keys if key not in texts]
    if missing:
        result.error = f"参考原文缺失：{'、'.join(missing)}"
        return result
    if refused:
        result.covered, result.reasons = [False] * len(keys), ["拒答"] * len(keys)
        return result
    try:
        raw = await judge.ainvoke({"question": sample.query,
                                   "references": format_references(keys, texts), "answer": answer})
        if raw["parsed"] is None:
            raise ValueError("覆盖率裁判结果无效")
        result.covered, result.reasons = parse_verdict(raw["parsed"], len(keys))
    except Exception:
        logger.exception("覆盖率裁判失败：%s/%s", sample.id, strategy)
        result.error = "覆盖率裁判失败"
    return result


def _rates(items: list[CoverageResult]) -> tuple[float | None, float | None]:
    judged = [r for r in items if r.covered is not None]
    groups = sum(len(r.covered) for r in judged)
    return (sum(sum(r.covered) for r in judged) / groups if groups else None,
            sum(all(r.covered) for r in judged) / len(judged) if judged else None)


def coverage_summary(results: list[CoverageResult]) -> dict[tuple[str, str], dict]:
    """全部口径计入拒答题；已回答口径排除拒答题。裁判失败的题不进分母。"""
    grouped = defaultdict(list)
    for r in results:
        grouped[(r.strategy, r.bucket)].append(r)
        grouped[(r.strategy, "ALL")].append(r)
    out = {}
    for strategy, bucket in sorted(grouped, key=lambda k: (k[0], BUCKET_ORDER.index(k[1]))):
        items = grouped[(strategy, bucket)]
        group_all, full_all = _rates(items)
        group_answered, full_answered = _rates([r for r in items if not r.refused])
        out[(strategy, bucket)] = {
            "n": len(items), "refused": sum(r.refused for r in items),
            "failed": sum(r.covered is None for r in items),
            "group_all": group_all, "full_all": full_all,
            "group_answered": group_answered, "full_answered": full_answered,
        }
    return out


def _rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_coverage(results: list[CoverageResult]) -> str:
    lines = ["## 事实覆盖率", "",
             "| 策略 | 桶 | 题数 | 拒答 | 裁判失败 | 组覆盖率（全部） | 全覆盖率（全部） | 组覆盖率（已回答） | 全覆盖率（已回答） |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for (strategy, bucket), v in coverage_summary(results).items():
        lines.append(f"| {strategy} | {bucket} | {v['n']} | {v['refused']} | {v['failed']} | "
                     f"{_rate(v['group_all'])} | {_rate(v['full_all'])} | "
                     f"{_rate(v['group_answered'])} | {_rate(v['full_answered'])} |")
    cases = []
    for r in sorted(results, key=lambda r: (r.strategy, r.sample_id)):
        if r.covered is None:
            continue
        if r.refused:
            cases.append(f"- {r.sample_id} [{r.strategy}] 拒答")
            continue
        for n, (key, ok, reason) in enumerate(zip(r.keys, r.covered, r.reasons), 1):
            if not ok:
                cases.append(f"- {r.sample_id} [{r.strategy}] 组 {n} {_cell(key)} → {_cell(reason)}")
    return "\n".join(lines) + "\n\n## 未覆盖个案\n\n" + ("\n".join(cases) or "无") + "\n"
