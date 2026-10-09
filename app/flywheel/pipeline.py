"""处理一条落池问题：标准化 → 召回待审候选 → 查重判定 → 累加或新建。"""

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from enum import Enum

from app.config import FLYWHEEL_LLM_TIMEOUT_SECONDS, REVIEW_DEDUP_MIN_SCORE, REVIEW_DEDUP_TOP_K
from app.db.engine import get_sessionmaker
from app.knowledge.embeddings import get_embeddings
from app.llm import get_question_normalizer, get_review_dedup_judge
from app.repositories import low_confidence, review_queue

logger = logging.getLogger(__name__)
# 待审问题的向量缓存，键为 (id, 问题文本)。问题文本不变时不重复嵌入。
_vectors: dict[tuple[int, str], list[float]] = {}


@dataclass(frozen=True)
class ProcessResult:
    review_id: int
    merged: bool
    candidates: int


class ProcessSkipReason(Enum):
    ALREADY_MATCHED = "already_matched"


SKIPPED = ProcessSkipReason.ALREADY_MATCHED


class StepFailed(Exception):
    def __init__(self, step: str):
        super().__init__(step)
        self.step = step


def clear_vector_cache() -> None:
    _vectors.clear()


def ensure_question_mark(question: str) -> str:
    """清理末尾问号、句号和空白，保证以中文问号结尾。"""
    question = re.sub(r"[?。.\s]+$", "", question)
    return question if question.endswith("？") else question + "？"


def format_chunks(chunks: list[dict] | None) -> str:
    if not chunks:
        return "（无）"
    return "\n\n".join(f"[{i}] {c['section_path']}（分数 {c['score']:.2f}）\n问：{c['question']}\n答：{c['answer']}"
                       for i, c in enumerate(chunks, 1))


def format_candidates(cands: list[tuple[int, str]]) -> str:
    return "\n".join(f"{i}. {q}" for i, (_, q) in enumerate(cands, 1))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def _call(runnable, inputs: dict, step: str):
    try:
        result = await asyncio.wait_for(runnable.ainvoke(inputs), FLYWHEEL_LLM_TIMEOUT_SECONDS)
    except Exception as exc:
        raise StepFailed(step) from exc
    if result["parsed"] is None:
        logger.error("flywheel_invalid step=%s raw=%r", step, result.get("raw"))
        raise StepFailed(step)
    return result["parsed"]


async def _candidates(question: str) -> tuple[list[float], list[tuple[int, str]]]:
    emb = get_embeddings()
    async with get_sessionmaker()() as s:
        pending = await review_queue.list_pending_questions(s)
    missing = [(i, q) for i, q in pending if (i, q) not in _vectors]
    vectors = await emb.aembed_documents([question, *(q for _, q in missing)])
    for key, v in zip(missing, vectors[1:]):
        _vectors[key] = v
    scored = [(_cosine(vectors[0], _vectors[(i, q)]), i, q) for i, q in pending]
    top = sorted((x for x in scored if x[0] >= REVIEW_DEDUP_MIN_SCORE), key=lambda x: (-x[0], x[1]))
    return vectors[0], [(i, q) for _, i, q in top[:REVIEW_DEDUP_TOP_K]]


async def process(lcq_id: int) -> ProcessResult | ProcessSkipReason | None:
    """成功返回 ProcessResult，已匹配返回 SKIPPED，失败返回 None。"""
    step = "load"
    try:
        async with get_sessionmaker()() as s:
            row = await low_confidence.get(s, lcq_id)
        if row is None:
            return None
        if row.matched_review_id is not None:
            logger.info("flywheel skip lcq=%s reason=already_matched", lcq_id)
            return SKIPPED
        step = "normalize"
        norm = await _call(get_question_normalizer(),
                           {"question": row.raw_question, "chunks": format_chunks(row.retrieved_chunks)}, step)
        norm.normalized_question = ensure_question_mark(norm.normalized_question)
        step = "candidates"
        try:
            _, cands = await _candidates(norm.normalized_question)
        except Exception as exc:
            raise StepFailed(step) from exc
        dup_id = None
        if cands:
            step = "judge"
            verdict = await _call(get_review_dedup_judge(),
                                  {"question": norm.normalized_question, "candidates": format_candidates(cands)},
                                  step)
            if verdict.duplicate_of is not None:
                if not 1 <= verdict.duplicate_of <= len(cands):
                    logger.error("flywheel_invalid step=judge duplicate_of=%s candidates=%s",
                                 verdict.duplicate_of, len(cands))
                    raise StepFailed(step)
                dup_id = cands[verdict.duplicate_of - 1][0]
        step = "save"
        async with get_sessionmaker()() as s:
            if dup_id is not None:
                review_id = dup_id
            else:
                # 外键要求先取得新行 ID；条件回填失败时整笔事务回滚。
                item = await review_queue.add(s, normalized_question=norm.normalized_question,
                                              suggested_answer=norm.suggested_answer)
                review_id = item.id
            if not await low_confidence.set_matched(s, lcq_id, review_id):
                await s.rollback()
                logger.info("flywheel skip lcq=%s reason=already_matched", lcq_id)
                return SKIPPED
            if dup_id is not None:
                await review_queue.increment(s, dup_id)
            await s.commit()
    except StepFailed as exc:
        logger.exception("flywheel_failed lcq=%s step=%s", lcq_id, exc.step)
        return None
    except Exception:
        logger.exception("flywheel_failed lcq=%s step=%s", lcq_id, step)
        return None
    logger.info("flywheel lcq=%s review=%s merged=%s candidates=%s", lcq_id, review_id, dup_id is not None,
                len(cands))
    return ProcessResult(review_id, dup_id is not None, len(cands))
