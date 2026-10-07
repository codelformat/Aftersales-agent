from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeChunk

_SEP = " > "


@dataclass(frozen=True)
class NewChunk:
    category: str
    questions: str
    answer: str
    section_path: str
    content_type: str
    is_key_clause: bool


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _source_filter(root: str):
    return or_(
        KnowledgeChunk.section_path == root,
        KnowledgeChunk.section_path.like(f"{_escape_like(root + _SEP)}%", escape="\\"),
    )


async def has_source(s: AsyncSession, root: str) -> bool:
    return (await s.scalar(select(KnowledgeChunk.id).where(_source_filter(root)).limit(1))) is not None


async def ids_for_source(s: AsyncSession, root: str) -> list[int]:
    return list(await s.scalars(
        select(KnowledgeChunk.id).where(_source_filter(root)).order_by(KnowledgeChunk.id)
    ))


async def insert_chunks(s: AsyncSession, chunks: list[NewChunk]) -> list[KnowledgeChunk]:
    rows = [KnowledgeChunk(**vars(c)) for c in chunks]
    for row in rows:
        s.add(row)
        # 逐行刷新，保证 id 按文档顺序递增。
        await s.flush()
    return rows


async def link_chain(s: AsyncSession, ids: list[int]) -> None:
    for i, cid in enumerate(ids):
        await s.execute(update(KnowledgeChunk).where(KnowledgeChunk.id == cid).values(
            prev_chunk_id=ids[i - 1] if i > 0 else None,
            next_chunk_id=ids[i + 1] if i + 1 < len(ids) else None,
        ))


async def delete_ids(s: AsyncSession, ids: list[int]) -> None:
    if not ids:
        return
    # 自引用外键：先清指针，再删除。
    await s.execute(update(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)).values(
        prev_chunk_id=None, next_chunk_id=None,
    ))
    await s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))


async def next_pending(s: AsyncSession, after_id: int, limit: int) -> list[KnowledgeChunk]:
    return list(await s.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.vectorize_status == "pending", KnowledgeChunk.id > after_id)
        .order_by(KnowledgeChunk.id).limit(limit)
    ))


async def mark_done(s: AsyncSession, ids: list[int]) -> None:
    for cid in ids:
        await s.execute(update(KnowledgeChunk).where(KnowledgeChunk.id == cid).values(
            vector_id=str(cid), vectorize_status="done",
        ))


async def get_done_by_ids(s: AsyncSession, ids: list[int]) -> dict[int, KnowledgeChunk]:
    if not ids:
        return {}
    rows = await s.scalars(select(KnowledgeChunk).where(
        KnowledgeChunk.id.in_(ids), KnowledgeChunk.vectorize_status == "done",
    ))
    return {r.id: r for r in rows}


async def get_done(s: AsyncSession, chunk_id: int) -> KnowledgeChunk | None:
    return await s.scalar(select(KnowledgeChunk).where(
        KnowledgeChunk.id == chunk_id, KnowledgeChunk.vectorize_status == "done",
    ))


async def status_counts(s: AsyncSession) -> list[tuple[str | None, str, int]]:
    rows = await s.execute(
        select(KnowledgeChunk.content_type, KnowledgeChunk.vectorize_status, func.count())
        .group_by(KnowledgeChunk.content_type, KnowledgeChunk.vectorize_status)
        .order_by(KnowledgeChunk.content_type, KnowledgeChunk.vectorize_status)
    )
    return [(ct, st, int(n)) for ct, st, n in rows]


async def mark_pending_by_content_type(s: AsyncSession, content_type: str) -> int:
    result = await s.execute(
        update(KnowledgeChunk)
        .where(KnowledgeChunk.content_type == content_type)
        .values(vectorize_status="pending", vector_id=None)
    )
    return result.rowcount
