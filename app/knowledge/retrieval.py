from app.config import FAQ_MAX_RESULTS, FAQ_MIN_SCORE
from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge.embeddings import get_embeddings
from app.knowledge.milvus import search_vectors
from app.repositories import knowledge


async def search_by_vector(
    vector: list[float], *, limit: int, min_score: float
) -> list[tuple[KnowledgeChunk, float]]:
    """Milvus 取 Top-K，丢弃低于阈值的结果，正文从 MySQL 读（只取 done 行），保持相似度顺序。"""
    hits = [(i, sc) for i, sc in await search_vectors(vector, limit) if sc >= min_score]
    if not hits:
        return []
    async with get_sessionmaker()() as s:
        rows = await knowledge.get_done_by_ids(s, [i for i, _ in hits])
    # Milvus 有、MySQL 没有（或仍为 pending）的 id 跳过。
    return [(rows[i], sc) for i, sc in hits if i in rows]


async def search_with_scores(
    keyword: str, *, limit: int = FAQ_MAX_RESULTS, min_score: float = FAQ_MIN_SCORE
) -> list[tuple[KnowledgeChunk, float]]:
    vector = await get_embeddings().aembed_query(keyword)
    return await search_by_vector(vector, limit=limit, min_score=min_score)


async def search_faq(keyword: str) -> list[dict]:
    return [
        {"question": r.questions, "answer": r.answer, "category": r.category}
        for r, _ in await search_with_scores(keyword)
    ]
