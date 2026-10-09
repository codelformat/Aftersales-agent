import asyncio
from dataclasses import dataclass, replace

from app.config import EVIDENCE_TOP_N, FUSED_LIMIT, GENERAL_CATEGORY, MULTI_FUSED_LIMIT, RECALL_LEG_LIMIT, RERANK_MIN_SCORE
from app.db.engine import get_sessionmaker
from app.db.models import KnowledgeChunk
from app.knowledge import rerank as rerank_mod
from app.knowledge.chunking import PATH_SEP, knowledge_text
from app.knowledge.embeddings import get_embeddings
from app.knowledge.milvus import search_bm25, search_dense, search_hybrid, search_vectors
from app.knowledge.query import bm25_query, dense_query, get_lexicon, understand
from app.repositories import knowledge
from app.schemas import QueryPlan

STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
FAQ_TABLE_SOURCE = "常见问答"


@dataclass(frozen=True)
class EvidenceItem:
    chunk_id: int
    section_path: str
    question: str
    answer: str
    score: float


@dataclass(frozen=True)
class Retrieval:
    plan: QueryPlan
    ranked: list[EvidenceItem]
    evidence: list[EvidenceItem]


def build_filter(category: str | None, exclude_mined: bool) -> str:
    parts = []
    if category:
        # 通用政策也必须能召回。
        parts.append(f'product_category in ["{category}", "{GENERAL_CATEGORY}"]')
    if exclude_mined:
        parts.append('content_type != "mined"')
    return " and ".join(parts)


def interleave(items: list) -> list:
    """按排名交替放首尾：第 1 名放首位，第 2 名放末位，依次向中间填。"""
    head, tail = [], []
    for i, item in enumerate(items):
        (head if i % 2 == 0 else tail).append(item)
    return head + tail[::-1]


def source_key(section_path: str, questions: str) -> str:
    """评估集的来源键。常见问答同一分类下有多行，需要加上问题文本。"""
    if section_path.startswith(FAQ_TABLE_SOURCE + PATH_SEP):
        return f"{section_path}{PATH_SEP}{questions}"
    return section_path


async def _done_rows(ids: list[int]) -> dict[int, KnowledgeChunk]:
    async with get_sessionmaker()() as s:
        return await knowledge.get_done_by_ids(s, ids)


async def _rerank_hits(
    plan: QueryPlan, hits: list[tuple[int, float]], min_score: float, top_n: int
) -> Retrieval:
    lex = get_lexicon()
    rows = await _done_rows([i for i, _ in hits])
    # Milvus 有、MySQL 没有（或仍为 pending）的 id 跳过。
    hits = [(i, sc) for i, sc in hits if i in rows]
    items = [EvidenceItem(i, rows[i].section_path or "", rows[i].questions, rows[i].answer, sc) for i, sc in hits]
    docs = [knowledge_text(rows[i].category, rows[i].questions, rows[i].answer) for i, _ in hits]
    # 重排用替换了俗称的标准问法，不用追加了同义词的 BM25 查询。
    scored = await rerank_mod.rerank(dense_query(plan.standard_query, lex), docs, top_n)
    ranked = [replace(items[idx], score=score) for idx, score in scored]
    kept = [e for e in ranked if e.score >= min_score]
    return Retrieval(plan, ranked, interleave(kept))


async def retrieve(
    question: str,
    strategy: str = "hybrid_rerank",
    *,
    plan: QueryPlan | None = None,
    exclude_mined: bool = False,
    min_score: float = RERANK_MIN_SCORE,
    top_n: int = EVIDENCE_TOP_N,
) -> Retrieval:
    if strategy not in STRATEGIES:
        raise ValueError(f"未知的检索策略：{strategy}")
    plan = plan or await understand(question)
    lex = get_lexicon()
    flt = build_filter(plan.product_category, exclude_mined)
    text = bm25_query(plan.standard_query, lex)
    vector = None
    if strategy != "bm25":
        normalized_query = dense_query(plan.standard_query, lex)
        vector = await get_embeddings().aembed_query(normalized_query)
    if strategy == "dense":
        hits = await search_dense(vector, top_n, flt)
    elif strategy == "bm25":
        hits = await search_bm25(text, top_n, flt)
    else:
        limit = FUSED_LIMIT if strategy == "hybrid_rerank" else top_n
        hits = await search_hybrid(vector, text, leg_limit=RECALL_LEG_LIMIT, limit=limit, filter=flt)
    if strategy == "hybrid_rerank":
        return await _rerank_hits(plan, hits, min_score, top_n)
    rows = await _done_rows([i for i, _ in hits])
    # Milvus 有、MySQL 没有（或仍为 pending）的 id 跳过。
    hits = [(i, sc) for i, sc in hits if i in rows]
    items = [EvidenceItem(i, rows[i].section_path or "", rows[i].questions, rows[i].answer, sc) for i, sc in hits]
    ranked = items[:top_n]
    return Retrieval(plan, ranked, interleave(ranked))


async def retrieve_multi(
    queries: list[str], plan: QueryPlan, *, min_score: float = RERANK_MIN_SCORE, top_n: int = EVIDENCE_TOP_N
) -> Retrieval:
    """多条查询各自混合召回，按块合并后对标准问法只重排一次。"""
    if not queries:
        raise ValueError("查询列表为空")
    lex = get_lexicon()
    flt = build_filter(plan.product_category, False)
    emb = get_embeddings()
    vectors = await asyncio.gather(*(emb.aembed_query(dense_query(q, lex)) for q in queries))
    legs = await asyncio.gather(*(
        search_hybrid(v, bm25_query(q, lex), leg_limit=RECALL_LEG_LIMIT, limit=FUSED_LIMIT, filter=flt)
        for q, v in zip(queries, vectors)
    ))
    best: dict[int, tuple[int, float]] = {}
    for hits in legs:
        for rank, (chunk_id, score) in enumerate(hits):
            current = best.get(chunk_id)
            if current is None or (rank, -score) < (current[0], -current[1]):
                best[chunk_id] = (rank, score)
    merged = sorted(best.items(), key=lambda kv: (kv[1][0], -kv[1][1]))[:MULTI_FUSED_LIMIT]
    return await _rerank_hits(plan, [(i, sc) for i, (_, sc) in merged], min_score, top_n)


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
