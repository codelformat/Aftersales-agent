from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.db.engine import get_sessionmaker
from app.repositories import eval_runs

router = APIRouter()


class EvalRunOut(BaseModel):
    id: int
    triggered_by: str
    dataset_size: int
    metrics: dict[str, Any]
    created_at: datetime


@router.get("/api/eval-runs", response_model=list[EvalRunOut])
async def list_eval_runs(limit: int = Query(30, ge=1, le=200)) -> list[dict]:
    async with get_sessionmaker()() as s:
        rows = await eval_runs.list_recent(s, limit)
    return [{k: getattr(r, k) for k in EvalRunOut.model_fields} for r in rows]

