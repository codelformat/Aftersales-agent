import logging
from dataclasses import dataclass

from app.config import VECTORIZE_BATCH_SIZE
from app.db.engine import get_sessionmaker
from app.knowledge.chunking import knowledge_text
from app.knowledge.embeddings import get_embeddings
from app.knowledge.milvus import count_vectors, upsert_vectors
from app.repositories import knowledge

logger = logging.getLogger(__name__)


class SimulatedCrash(Exception):
    """故障注入：Milvus 已写入、MySQL 未回填时中断。只用于演示和测试。"""


async def vectorize_pending(
    *, batch_size: int = VECTORIZE_BATCH_SIZE, crash_after_batches: int | None = None
) -> int:
    """把 pending 行嵌入后 upsert 到 Milvus，再回填 MySQL。返回本次处理的行数。

    Milvus 主键等于 MySQL 主键。中断后重跑会覆盖已写入的向量，不产生重复。
    """
    sm = get_sessionmaker()
    embeddings = get_embeddings()
    after_id = processed = batches = 0
    while True:
        async with sm() as s:
            rows = await knowledge.next_pending(s, after_id, batch_size)
        if not rows:
            return processed
        vectors = await embeddings.aembed_documents(
            [knowledge_text(r.category, r.questions, r.answer) for r in rows]
        )
        await upsert_vectors([(r.id, v) for r, v in zip(rows, vectors)])
        if crash_after_batches is not None and batches == crash_after_batches:
            raise SimulatedCrash(f"第 {batches + 1} 批已写入 Milvus，未回填 MySQL")
        async with sm() as s:
            await knowledge.mark_done(s, [r.id for r in rows])
            await s.commit()
        processed += len(rows)
        batches += 1
        after_id = rows[-1].id
        logger.info("已向量化 %d 行", processed)


@dataclass(frozen=True)
class KbStatus:
    groups: list[tuple[str | None, str, int]]
    total: int
    pending: int
    done: int
    milvus: int


async def kb_status() -> KbStatus:
    async with get_sessionmaker()() as s:
        groups = await knowledge.status_counts(s)
    pending = sum(n for _, st, n in groups if st == "pending")
    done = sum(n for _, st, n in groups if st == "done")
    return KbStatus(groups, pending + done, pending, done, await count_vectors())


def format_status(st: KbStatus) -> str:
    lines = ["知识库状态："]
    lines += [f"  {ct or '-'} / {status}: {n}" for ct, status, n in st.groups]
    lines.append(
        f"  MySQL 合计 {st.total}（pending {st.pending}，done {st.done}）；Milvus 实体数 {st.milvus}"
    )
    return "\n".join(lines)
