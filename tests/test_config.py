from app import config
from app.config import Settings


def _set_required(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CHAT_MODEL", "m1")
    monkeypatch.setenv("CHAT_API_KEY", "k1")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@h:3307/aftersales")


def test_reads_chat_variables(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("CHAT_THINKING", "adaptive")
    s = Settings(_env_file=None)
    assert s.chat_base_url == "https://example.test/v1"
    assert s.chat_model == "m1"
    assert s.chat_api_key.get_secret_value() == "k1"
    assert s.chat_thinking == "adaptive"


def test_thinking_is_optional(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("CHAT_THINKING", raising=False)
    assert Settings(_env_file=None).chat_thinking is None


def test_unknown_env_file_keys_are_ignored(monkeypatch, tmp_path):
    for k in ("CHAT_BASE_URL", "CHAT_MODEL", "CHAT_API_KEY", "CHAT_THINKING", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "CHAT_BASE_URL=https://example.test/v1\nCHAT_MODEL=m1\nCHAT_API_KEY=k1\n"
        "TOKEN_BUDGET=999\nDATABASE_URL=mysql+asyncmy://u:p@h:3307/aftersales\n"
    )
    s = Settings(_env_file=env)
    assert s.chat_model == "m1"
    assert not hasattr(s, "token_budget")


def test_constants():
    assert config.TOKEN_BUDGET == 2000
    assert config.CHARS_PER_TOKEN == 2.0
    assert config.SHOP_NAME == "示例商城"
    assert config.UPSTREAM_TIMEOUT_SECONDS == 60
    assert config.UPSTREAM_MAX_RETRIES == 1
    assert config.MAX_INPUT_CHARS == 2000


def test_empty_thinking_treated_as_unset(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("CHAT_THINKING", "")
    assert Settings(_env_file=None).chat_thinking is None


def test_test_database_url_replaces_db_name():
    from app.config import test_database_url

    url = "mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales?charset=utf8mb4"
    assert test_database_url(url) == (
        "mysql+asyncmy://aftersales:aftersales@127.0.0.1:3307/aftersales_test?charset=utf8mb4"
    )


def test_tool_constants():
    from app import config

    assert config.TOOL_TIMEOUT_SECONDS == 5
    assert config.TOOL_MAX_ATTEMPTS == 3
    assert config.TOOL_RETRY_BASE_DELAY == 0.2
    assert config.TOOL_RETRY_MAX_DELAY == 2.0
    assert config.TOOL_RESULT_MAX_CHARS == 1500
    assert config.FAQ_MAX_RESULTS == 3
