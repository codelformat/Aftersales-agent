from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/", response_class=FileResponse, include_in_schema=False)
async def index() -> Path:
    return Path(__file__).resolve().parent.parent / "web" / "index.html"


@router.get("/admin/faith-cases", response_class=FileResponse, include_in_schema=False)
async def faith_cases_page() -> Path:
    return Path(__file__).resolve().parent.parent / "web" / "faith_cases.html"
