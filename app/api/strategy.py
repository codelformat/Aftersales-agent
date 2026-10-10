import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "web/public/snapshots/strategy-comparison.json"


@router.get("/api/strategy-comparison")
async def strategy_comparison():
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"code": "not_exported"})
