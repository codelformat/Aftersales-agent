from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.engine import get_sessionmaker
from app.repositories import knowledge

router = APIRouter()


class ChunkOut(BaseModel):
    id: int
    section_path: str | None
    content_type: str | None
    questions: str
    answer: str
    prev_chunk_id: int | None
    next_chunk_id: int | None


@router.get("/api/knowledge/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(chunk_id: int) -> ChunkOut:
    async with get_sessionmaker()() as s:
        row = await knowledge.get_done(s, chunk_id)
    if row is None:
        raise HTTPException(404, detail={"code": "chunk_not_found", "message": "知识块不存在"})
    return ChunkOut.model_validate(row, from_attributes=True)
