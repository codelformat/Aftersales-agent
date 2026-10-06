import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from langchain_core.runnables import Runnable

from app.config import MINE_BATCH_SIZE, MINE_CONCURRENCY
from app.db.engine import get_sessionmaker
from app.db.models import Message
from app.repositories import messages, staging
from app.repositories.staging import NewStaging

logger = logging.getLogger(__name__)


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
