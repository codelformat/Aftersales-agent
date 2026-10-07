from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.db.engine import get_sessionmaker
from app.db.models import FaithCase
from app.repositories import faith_cases
from app.services.grounding import parse_citations

router = APIRouter()


class FaithCaseOut(BaseModel):
    id: int
    eval_id: str
    bucket: str
    query: str
    strategy: str
    answer: str
    reason: str
    citations: list[dict[str, Any]] | None
    cited: list[int]
    judge_model: str | None
    status: str
    seen_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    resolution: str | None
    resolved_at: datetime | None


class ResolveRequest(BaseModel):
    status: Literal["已解决", "无需解决"]
    resolution: str = Field(max_length=300)

    @field_validator("resolution")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("处置说明不能为空")
        return v


def _out(row: FaithCase) -> FaithCaseOut:
    return FaithCaseOut.model_validate({
        **{k: getattr(row, k) for k in FaithCaseOut.model_fields if k != "cited"},
        "cited": parse_citations(row.answer),
    })


@router.get("/api/faith-cases", response_model=list[FaithCaseOut])
async def list_faith_cases(
    status: Literal["未解决", "已解决", "无需解决"] | None = None,
) -> list[FaithCaseOut]:
    async with get_sessionmaker()() as s:
        return [_out(r) for r in await faith_cases.list_cases(s, status)]


@router.post("/api/faith-cases/{case_id}/resolve", response_model=FaithCaseOut)
async def resolve_faith_case(case_id: int, req: ResolveRequest) -> FaithCaseOut:
    async with get_sessionmaker()() as s:
        row = await faith_cases.resolve_case(s, case_id, req.status, req.resolution)
        if row is None:
            raise HTTPException(404, detail={"code": "case_not_found", "message": "个案不存在"})
        await s.commit()
        await s.refresh(row)
        return _out(row)
