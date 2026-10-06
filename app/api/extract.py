from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.runnables import Runnable

from app.llm import get_extractor
from app.schemas import AfterSalesRequest, ExtractRequest
from app.services.extract import ExtractionFailed, extract

router = APIRouter()


@router.post("/extract", response_model=AfterSalesRequest)
async def extract_request(
    req: ExtractRequest,
    extractor: Annotated[Runnable, Depends(get_extractor)],
) -> AfterSalesRequest:
    try:
        return await extract(req.text, extractor)
    except ExtractionFailed:
        raise HTTPException(502, detail={"code": "extraction_failed", "message": "提取失败，请稍后重试"})
