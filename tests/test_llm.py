from pydantic import SecretStr

from app.config import Settings
from app.llm import build_chat_model, build_extract_model


def _settings(thinking):
    return Settings(
        _env_file=None,
        chat_base_url="https://example.test/v1",
        chat_model="m1",
        chat_api_key=SecretStr("k1"),
        chat_thinking=thinking,
        database_url="mysql+asyncmy://u:p@h:3307/aftersales",
        embed_api_key=SecretStr("e1"),
        milvus_uri="http://m:19530",
    )


def test_chat_model_sends_configured_thinking():
    m = build_chat_model(_settings("adaptive"))
    assert m.extra_body == {"thinking": {"type": "adaptive"}}


def test_extract_model_disables_thinking():
    m = build_extract_model(_settings("adaptive"))
    assert m.extra_body == {"thinking": {"type": "disabled"}}


def test_no_thinking_field_when_not_configured():
    s = _settings(None)
    assert not build_chat_model(s).extra_body
    assert not build_extract_model(s).extra_body


def test_common_parameters():
    m = build_chat_model(_settings(None))
    assert m.model_name == "m1"
    assert m.openai_api_base == "https://example.test/v1"
    assert m.request_timeout == 60
    assert m.max_retries == 1
