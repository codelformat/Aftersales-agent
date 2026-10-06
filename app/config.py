from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

TOKEN_BUDGET = 2000
# 实测 DeepSeek：55 个中文字符约 30 token。取 2.0 字符/token。
CHARS_PER_TOKEN = 2.0
SHOP_NAME = "示例商城"
UPSTREAM_TIMEOUT_SECONDS = 60
UPSTREAM_MAX_RETRIES = 1
MAX_INPUT_CHARS = 2000
TOOL_TIMEOUT_SECONDS = 5
TOOL_MAX_ATTEMPTS = 3
TOOL_RETRY_BASE_DELAY = 0.2
TOOL_RETRY_MAX_DELAY = 2.0
TOOL_RESULT_MAX_CHARS = 1500
FAQ_MAX_RESULTS = 3


class Settings(BaseSettings):
    # 忽略 .env 中未定义的变量。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    chat_base_url: str
    chat_model: str
    chat_api_key: SecretStr
    chat_thinking: str | None = None
    database_url: str


def test_database_url(url: str) -> str:
    """把库名替换为 aftersales_test，其余部分不变。"""
    return make_url(url).set(database="aftersales_test").render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
