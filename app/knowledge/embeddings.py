from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.config import (
    EMBED_MAX_RETRIES,
    EMBED_MODEL,
    EMBED_TIMEOUT_SECONDS,
    VECTORIZE_BATCH_SIZE,
    Settings,
    get_settings,
)

_embeddings: Embeddings | None = None


def build_embeddings(settings: Settings) -> OpenAIEmbeddings:
    # 硅基流动不接受 token id。关闭长度检查，直接发送原文。
    return OpenAIEmbeddings(
        model=EMBED_MODEL,
        base_url=settings.embed_base_url,
        api_key=settings.embed_api_key,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
        chunk_size=VECTORIZE_BATCH_SIZE,
        max_retries=EMBED_MAX_RETRIES,
        timeout=EMBED_TIMEOUT_SECONDS,
    )


def get_embeddings() -> Embeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = build_embeddings(get_settings())
    return _embeddings


def set_embeddings(e: Embeddings | None) -> None:
    """替换嵌入对象。传 None 后，下次调用按配置创建对象。"""
    global _embeddings
    _embeddings = e
