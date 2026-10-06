import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date

from langchain_core.runnables import Runnable

from app.config import DEDUP_KB_MIN_SCORE, DEDUP_STAGING_MIN_SCORE, MINE_BATCH_SIZE, MINE_CONCURRENCY
from app.db.engine import get_sessionmaker
from app.db.models import Message
from app.knowledge.chunking import PATH_SEP
from app.knowledge.embeddings import get_embeddings
from app.knowledge.ingest import MINED_SOURCE
from app.knowledge.retrieval import search_by_vector
from app.knowledge.vectorize import vectorize_pending
from app.repositories import knowledge, messages, staging
from app.repositories.knowledge import NewChunk
from app.repositories.staging import NewStaging

logger = logging.getLogger(__name__)

DEDUP_KB_TOP_K = 5


def source_ref(conversation_id: int) -> str:
    return f"conversation:{conversation_id}"


def batch_no(day: date, seq: int) -> str:
    return f"{day:%Y%m%d}-{seq:02d}"


def format_transcript(rows: list[Message]) -> str | None:
    """只取用户和客服的正文。没有客服正文时返回 None。"""
    lines, has_reply = [], False
    for m in rows:
        if m.role == "user" and m.content:
            lines.append(f"用户：{m.content}")
        elif m.role == "assistant" and m.content:
            lines.append(f"客服：{m.content}")
            has_reply = True
    return "\n".join(lines) if has_reply else None


@dataclass
class ExtractStats:
    conversations: int = 0
    skipped: int = 0
    failed: int = 0
    pairs: int = 0
    batches: int = 0


async def _extract_one(
    cid: int, extractor: Runnable, sem: asyncio.Semaphore, stats: ExtractStats
) -> list[tuple[str, str]]:
    async with get_sessionmaker()() as s:
        text = format_transcript(await messages.list_for_conversation(s, cid))
    if text is None:
        stats.skipped += 1
        return []
    async with sem:
        try:
            result = await extractor.ainvoke({"transcript": text})
        except Exception:
            logger.exception("会话 %d 抽取失败", cid)
            stats.failed += 1
            return []
    parsed = result["parsed"]
    if parsed is None:
        logger.error(
            "会话 %d 抽取结果无法解析：raw=%r，parsing_error=%r", cid, result["raw"], result["parsing_error"]
        )
        stats.failed += 1
        return []
    return [(p.question.strip(), p.answer.strip()) for p in parsed.pairs if p.question.strip() and p.answer.strip()]


async def extract_day(
    day: date, extractor: Runnable, *, batch_size: int = MINE_BATCH_SIZE, concurrency: int = MINE_CONCURRENCY
) -> ExtractStats:
    """抽取指定日期的会话。一个会话一次 LLM 调用；一批的暂存行在一个事务中写入。"""
    stats = ExtractStats()
    async with get_sessionmaker()() as s:
        ids = await staging.unmined_conversation_ids(s, day)
        seq = await staging.max_batch_seq(s, day)
    stats.conversations = len(ids)
    sem = asyncio.Semaphore(concurrency)
    for start in range(0, len(ids), batch_size):
        batch_ids = ids[start:start + batch_size]
        results = await asyncio.gather(*(_extract_one(cid, extractor, sem, stats) for cid in batch_ids))
        pairs = [(cid, q, a) for cid, found in zip(batch_ids, results) for q, a in found]
        if not pairs:
            continue
        seq += 1
        bno = batch_no(day, seq)
        rows = [NewStaging(bno, source_ref(cid), q, a) for cid, q, a in pairs]
        async with get_sessionmaker()() as s:
            await staging.add_rows(s, rows)
            await s.commit()
        stats.pairs += len(rows)
        stats.batches += 1
    return stats


def format_candidates(cands: list[tuple[str, str]]) -> str:
    if not cands:
        return "（无）"
    return "\n".join(f"{i}. 问：{q}\n   答：{a}" for i, (q, a) in enumerate(cands, start=1))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@dataclass
class DedupStats:
    kept: int = 0
    discarded: int = 0
    failed: int = 0


async def dedup_pending(judge: Runnable) -> DedupStats:
    """逐行处理 extracted 暂存行。召回候选后裁定，丢弃重复项，写入新问答。"""
    stats = DedupStats()
    embeddings = get_embeddings()
    async with get_sessionmaker()() as s:
        rows = await staging.list_extracted(s)
    kept: list[tuple[list[float], str, str]] = []  # 本次保留项，还不在 Milvus 中。
    for row in rows:
        try:
            vec = await embeddings.aembed_query(row.question)
            kb = await search_by_vector(vec, limit=DEDUP_KB_TOP_K, min_score=DEDUP_KB_MIN_SCORE)
            cands = [(c.questions, c.answer) for c, _ in kb]
            cands += [(q, a) for v, q, a in kept if _cosine(v, vec) >= DEDUP_STAGING_MIN_SCORE]
            result = await judge.ainvoke({
                "question": row.question, "answer": row.answer, "candidates": format_candidates(cands),
            })
        except Exception:
            logger.exception("暂存行 %d 去重失败", row.id)
            stats.failed += 1
            continue
        verdict = result["parsed"]
        if verdict is None or (
            verdict.duplicate_of is not None and not 1 <= verdict.duplicate_of <= len(cands)
        ):
            logger.error("暂存行 %d 裁定结果无效：raw=%r", row.id, result["raw"])
            stats.failed += 1
            continue
        async with get_sessionmaker()() as s:
            if verdict.duplicate_of is not None:
                await staging.set_status(s, row.id, "discarded")
            else:
                await knowledge.insert_chunks(s, [NewChunk(
                    verdict.category, row.question, row.answer,
                    f"{MINED_SOURCE}{PATH_SEP}{verdict.category}", "mined", False,
                )])
                await staging.set_status(s, row.id, "kept")
            await s.commit()
        if verdict.duplicate_of is not None:
            stats.discarded += 1
        else:
            kept.append((vec, row.question, row.answer))
            stats.kept += 1
    return stats


@dataclass
class MiningStats:
    extract: ExtractStats
    dedup: DedupStats
    vectorized: int


async def run_mining(day: date, extractor: Runnable, judge: Runnable) -> MiningStats:
    """抽取、补齐已有向量、整体去重，再向量化新问答。"""
    extract = await extract_day(day, extractor)
    vectorized = await vectorize_pending()  # 补齐已有知识的向量，供去重召回。
    dedup = await dedup_pending(judge)
    vectorized += await vectorize_pending()
    return MiningStats(extract, dedup, vectorized)
