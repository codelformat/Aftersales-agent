import logging

from langchain_core.runnables import Runnable

from app.schemas import AfterSalesRequest

logger = logging.getLogger(__name__)


class ExtractionFailed(Exception):
    """上游失败，或输出无法解析为 AfterSalesRequest。"""


async def extract(text: str, extractor: Runnable) -> AfterSalesRequest:
    # 调用提取器，记录上游异常并转换为提取失败。
    try:
        result = await extractor.ainvoke({"text": text})
    except Exception as exc:
        logger.exception("上游提取失败")
        raise ExtractionFailed from exc

    # 返回成功解析的售后信息。
    parsed = result["parsed"]
    if parsed is not None:
        return parsed

    # 记录原始输出和解析错误。
    logger.error("提取解析失败：raw=%r，parsing_error=%r", result["raw"], result["parsing_error"])
    raise ExtractionFailed
