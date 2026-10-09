import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.config import GENERAL_CATEGORY, PRODUCT_CATEGORIES
from app.db.engine import get_sessionmaker
from app.knowledge.chunking import PATH_SEP
from app.knowledge.ingest import FLYWHEEL_CONTENT_TYPE, FLYWHEEL_SOURCE
from app.knowledge.vectorize import vectorize_pending
from app.repositories import knowledge, low_confidence, review_queue

logger = logging.getLogger(__name__)
router = APIRouter()
EXTRA_QUESTIONS = 3
NOT_FOUND = {"code": "review_not_found", "message": "待审问题不存在"}
NOT_PENDING = {"code": "review_not_pending", "message": "该问题已审核"}
CATEGORIES = (*PRODUCT_CATEGORIES, GENERAL_CATEGORY)


class ReviewOut(BaseModel):
    id: int
    normalized_question: str
    ai_suggested_answer: str | None
    occurrence_count: int
    review_status: str
    approved_answer: str | None
    created_at: datetime
    updated_at: datetime


class SourceOut(BaseModel):
    id: int
    raw_question: str
    source: str
    reason: str | None
    created_at: datetime
    retrieved_chunks: list[dict[str, Any]] | None


class ReviewDetail(ReviewOut):
    sources: list[SourceOut]


class ApproveRequest(BaseModel):
    approved_answer: str
    product_category: str = GENERAL_CATEGORY

    @field_validator("approved_answer")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("核准答案不能为空")
        return v

    @field_validator("product_category")
    @classmethod
    def known_category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError("品类不在可选列表中")
        return v


def _out(row) -> dict:
    return {k: getattr(row, k) for k in ReviewOut.model_fields}


@router.get("/api/review-queue", response_model=list[ReviewOut])
async def list_reviews(status: Literal["待审", "通过", "驳回"] | None = None) -> list[dict]:
    async with get_sessionmaker()() as s:
        return [_out(r) for r in await review_queue.list_items(s, status)]


@router.get("/api/review-queue/{review_id}", response_model=ReviewDetail)
async def review_detail(review_id: int) -> dict:
    async with get_sessionmaker()() as s:
        row = await review_queue.get(s, review_id)
        if row is None:
            raise HTTPException(404, detail=NOT_FOUND)
        sources = await low_confidence.list_for_review(s, review_id)
    return {**_out(row), "sources": [{k: getattr(x, k) for k in SourceOut.model_fields} for x in sources]}


def _questions(normalized: str, raws: list[str]) -> str:
    extra = [q for q in dict.fromkeys(r.strip() for r in raws) if q and q != normalized][:EXTRA_QUESTIONS]
    return "\n".join([normalized, *extra])


@router.post("/api/review-queue/{review_id}/approve")
async def approve(review_id: int, req: ApproveRequest) -> dict:
    async with get_sessionmaker()() as s:
        row = await review_queue.get(s, review_id, for_update=True)
        if row is None:
            raise HTTPException(404, detail=NOT_FOUND)
        if row.review_status != "待审":
            raise HTTPException(409, detail=NOT_PENDING)
        raws = [x.raw_question for x in await low_confidence.list_for_review(s, review_id)]
        path = FLYWHEEL_SOURCE if req.product_category == GENERAL_CATEGORY else \
            f"{FLYWHEEL_SOURCE}{PATH_SEP}{req.product_category}"
        [chunk] = await knowledge.insert_chunks(s, [knowledge.NewChunk(
            req.product_category, _questions(row.normalized_question, raws), req.approved_answer,
            path, FLYWHEEL_CONTENT_TYPE, False)])
        await review_queue.set_status(s, review_id, "通过", approved_answer=req.approved_answer)
        await s.commit()
        chunk_id = chunk.id
    try:
        # 同步向量化，返回后同一个问题马上能检索到。
        vectorized = await vectorize_pending()
    except Exception:
        logger.exception("review_vectorize_failed review=%s chunk=%s", review_id, chunk_id)
        raise HTTPException(502, detail={"code": "vectorize_failed",
                                         "message": "已通过，向量化失败，请运行 build_kb.py 补齐"})
    logger.info("review approve review=%s chunk=%s vectorized=%s", review_id, chunk_id, vectorized)
    return {"chunk_id": chunk_id, "vectorized": vectorized}


@router.post("/api/review-queue/{review_id}/reject")
async def reject(review_id: int) -> dict:
    async with get_sessionmaker()() as s:
        row = await review_queue.get(s, review_id, for_update=True)
        if row is None:
            raise HTTPException(404, detail=NOT_FOUND)
        if row.review_status != "待审":
            raise HTTPException(409, detail=NOT_PENDING)
        await review_queue.set_status(s, review_id, "驳回")
        await s.commit()
    return {"id": review_id, "review_status": "驳回"}
