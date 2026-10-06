from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.config import (
    UPSTREAM_MAX_RETRIES,
    UPSTREAM_TIMEOUT_SECONDS,
    Settings,
    get_settings,
)
from app.prompts import extract_prompt
from app.schemas import AfterSalesRequest


def _build(settings: Settings, thinking: str | None) -> ChatOpenAI:
    kwargs = {}
    # 仅在配置 CHAT_THINKING 时发送 thinking。GPT 和 Ollama 不识别该字段。
    if settings.chat_thinking is not None and thinking is not None:
        kwargs["extra_body"] = {"thinking": {"type": thinking}}
    return ChatOpenAI(
        model=settings.chat_model,
        base_url=settings.chat_base_url,
        api_key=settings.chat_api_key,
        timeout=UPSTREAM_TIMEOUT_SECONDS,
        max_retries=UPSTREAM_MAX_RETRIES,
        **kwargs,
    )


def build_chat_model(settings: Settings) -> ChatOpenAI:
    return _build(settings, settings.chat_thinking)


def build_extract_model(settings: Settings) -> ChatOpenAI:
    # 强制 tool_choice 与 DeepSeek 思考模式冲突。DeepSeek 返回 400。
    # DeepSeek 默认开启思考。使用 DeepSeek 时必须设置 CHAT_THINKING。
    # 否则提取模型不发送 disabled，/extract 会失败。
    return _build(settings, "disabled")


@lru_cache
def get_chat_model() -> BaseChatModel:
    return build_chat_model(get_settings())


@lru_cache
def get_extractor() -> Runnable:
    model = build_extract_model(get_settings())
    return extract_prompt | model.with_structured_output(
        AfterSalesRequest, method="function_calling", include_raw=True
    )
